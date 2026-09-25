"""What a database holds: table names and kinds, row estimates, reflected tables, FKs.

Every loader takes a Connection (normally a ``read_only_connection``) and an
optional schema. Results are cached per process by ``MetadataCache``; nothing
here runs at import or startup.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from sqlalchemy import MetaData, Table, inspect, text
from sqlalchemy.engine import Connection


@dataclass(frozen=True)
class TableEntry:
    name: str
    kind: str                     # 'table' | 'view'
    estimate: Optional[int]       # planner/statistics row estimate; None = unknown


@dataclass(frozen=True)
class FkEdge:
    table: str
    columns: Tuple[str, ...]
    ref_table: str
    ref_columns: Tuple[str, ...]
    origin: str = 'db'            # 'db' | 'orm' (declared by the ORM, absent from the DB)


@dataclass
class FkGraph:
    outgoing: Dict[str, List[FkEdge]] = field(default_factory=dict)
    incoming: Dict[str, List[FkEdge]] = field(default_factory=dict)

    @classmethod
    def from_edges(cls, edges) -> 'FkGraph':
        out, inc = defaultdict(list), defaultdict(list)
        for e in edges:
            out[e.table].append(e)
            inc[e.ref_table].append(e)
        return cls(dict(out), dict(inc))

    def merged(self, extra) -> 'FkGraph':
        """Add edges whose (table, columns) pair the graph does not already hold."""
        have = {(e.table, e.columns) for edges in self.outgoing.values() for e in edges}
        edges = [e for es in self.outgoing.values() for e in es]
        edges += [e for e in extra if (e.table, e.columns) not in have]
        return FkGraph.from_edges(edges)


_MYSQL_TABLES = text(
    "SELECT TABLE_NAME, TABLE_TYPE, TABLE_ROWS FROM information_schema.TABLES "
    "WHERE TABLE_SCHEMA = COALESCE(:schema, DATABASE())")

_PG_TABLES = text(
    "SELECT c.relname, c.relkind, c.reltuples FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = COALESCE(:schema, current_schema()) "
    "AND c.relkind IN ('r', 'p', 'v', 'm', 'f')")

_MYSQL_FKS = text(
    "SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, "
    "REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
    "WHERE TABLE_SCHEMA = COALESCE(:schema, DATABASE()) "
    "AND REFERENCED_TABLE_NAME IS NOT NULL "
    "ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION")


def load_catalog(conn: Connection, schema: Optional[str] = None) -> List[TableEntry]:
    """Every table and view, sorted case-insensitively, with a row estimate where cheap."""
    name = conn.dialect.name
    if name in ('mysql', 'mariadb'):
        entries = [TableEntry(t, 'view' if kind == 'VIEW' else 'table',
                              None if kind == 'VIEW' or rows is None else int(rows))
                   for t, kind, rows in conn.execute(_MYSQL_TABLES, {'schema': schema})]
    elif name == 'postgresql':
        # reltuples is -1 until the first ANALYZE; views have no statistics.
        entries = [TableEntry(t, 'view' if kind in ('v', 'm') else 'table',
                              int(rows) if kind not in ('v', 'm') and rows is not None
                              and rows >= 0 else None)
                   for t, kind, rows in conn.execute(_PG_TABLES, {'schema': schema})]
    else:
        insp = inspect(conn)
        entries = ([TableEntry(t, 'table', None) for t in insp.get_table_names(schema=schema)]
                   + [TableEntry(v, 'view', None) for v in insp.get_view_names(schema=schema)])
    return sorted(entries, key=lambda e: e.name.lower())


def reflect_table(conn: Connection, schema: Optional[str], name: str) -> Table:
    """Reflect one table without following its FKs into their targets."""
    return Table(name, MetaData(), schema=schema, autoload_with=conn, resolve_fks=False)


def load_fk_graph(conn: Connection, schema: Optional[str] = None) -> FkGraph:
    if conn.dialect.name in ('mysql', 'mariadb'):
        # One query; the Inspector would issue a SHOW CREATE TABLE per table.
        grouped: Dict[Tuple[str, str], list] = defaultdict(list)
        for t, cname, col, ref_t, ref_col in conn.execute(_MYSQL_FKS, {'schema': schema}):
            grouped[(t, cname)].append((col, ref_t, ref_col))
        edges = [FkEdge(t, tuple(c for c, _, _ in cols), cols[0][1],
                        tuple(r for _, _, r in cols))
                 for (t, _), cols in grouped.items()]
        return FkGraph.from_edges(edges)

    insp = inspect(conn)
    edges = []
    for (_, table), fks in insp.get_multi_foreign_keys(schema=schema).items():
        for fk in fks:
            if fk.get('referred_table'):
                edges.append(FkEdge(table, tuple(fk['constrained_columns']),
                                    fk['referred_table'], tuple(fk['referred_columns'])))
    return FkGraph.from_edges(edges)


class MetadataCache:
    """Thread-safe TTL memo keyed by tuples; a loader runs outside the lock."""

    def __init__(self, ttl_seconds: float = 900):
        self.ttl = ttl_seconds
        self._lock = threading.RLock()
        self._data: Dict[tuple, Tuple[float, object]] = {}

    def get_or_load(self, key: tuple, loader: Callable[[], object]):
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(key)
            if hit is not None and hit[0] > now:
                return hit[1]
        value = loader()
        with self._lock:
            self._data[key] = (now + self.ttl, value)
        return value

    def invalidate(self, prefix: tuple = ()) -> None:
        with self._lock:
            for key in [k for k in self._data if k[:len(prefix)] == prefix]:
                del self._data[key]
