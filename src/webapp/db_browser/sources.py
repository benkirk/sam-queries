"""What the browser can do with an inventoried engine, and one table of it per request.

``BrowseSource`` adds browsing to ``EngineSource``: read-only connections and the
per-process metadata cache (catalog, reflected tables, FK graph, ORM overlay).
``BrowsedTable`` is one table as this request's user may see it, so the hidden
set, which depends on ``current_user``, never enters the cache. Unknown source
keys and table names 404 here, so a route never builds SQL from a name
reflection has not produced.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from functools import cached_property
from typing import Dict, FrozenSet, List, Optional, Tuple

from flask import abort, current_app, url_for
from flask_login import current_user
from sqlalchemy import Column, Table

from dbbrowse import (DEFAULT_POLICY, FkEdge, FkGraph, MetadataCache, OrmOverlay, TableEntry,
                      column_kind, load_catalog, load_fk_graph, read_only_connection,
                      reflect_table)
from webapp.extensions import db
from webapp.utils.engine_inventory import EngineSource, engine_sources
from webapp.utils.rbac import Permission, has_permission

from .params import key_args

CACHE = MetadataCache(ttl_seconds=900)

_OVERLAY_BIND = {'sam': None, 'system_status': 'system_status'}
_NOTES = {'sam': 'Times are naive Mountain.', 'system_status': 'Times are naive UTC.'}

# Columns shown only to holders of a further permission: (table, column) -> Permission.
GATED_COLUMNS = {('xras_action_log', 'raw_payload'): Permission.MANAGE_XRAS}

UNSORTABLE_KINDS = frozenset({'binary', 'json', 'long'})


@dataclass(frozen=True)
class BrowseSource(EngineSource):

    @classmethod
    def of(cls, src: EngineSource) -> 'BrowseSource':
        return cls(**{f.name: getattr(src, f.name) for f in fields(EngineSource)})

    @property
    def note(self) -> Optional[str]:
        return _NOTES.get(self.family)

    def connect(self):
        return read_only_connection(
            self.engine, timeout_ms=current_app.config['DB_BROWSER_STATEMENT_TIMEOUT_MS'])

    def invalidate(self) -> None:
        CACHE.invalidate((self.key,))

    def catalog(self) -> Tuple[TableEntry, ...]:
        def load():
            with self.connect() as conn:
                return tuple(load_catalog(conn, self.schema))
        return CACHE.get_or_load((self.key, 'catalog'), load)

    def overlay(self) -> OrmOverlay:
        if self.family not in _OVERLAY_BIND:
            return OrmOverlay()

        def load():
            from sam.base import Base
            return OrmOverlay.from_registry(Base.registry, bind_key=_OVERLAY_BIND[self.family])
        return CACHE.get_or_load((self.key, 'overlay'), load)

    def fk_graph(self) -> FkGraph:
        def load():
            with self.connect() as conn:
                graph = load_fk_graph(conn, self.schema)
            return graph.merged(self.overlay().fk_edges)
        return CACHE.get_or_load((self.key, 'fks'), load)

    def entry(self, name: str) -> TableEntry:
        for entry in self.catalog():
            if entry.name == name:
                return entry
        abort(404)

    def table(self, name: str) -> Table:
        self.entry(name)

        def load():
            with self.connect() as conn:
                return reflect_table(conn, self.schema, name)
        return CACHE.get_or_load((self.key, 'table', name), load)

    def browse(self, name: str) -> 'BrowsedTable':
        return BrowsedTable.of(self, name)


def browse_sources() -> Dict[str, BrowseSource]:
    return {src.key: BrowseSource.of(src) for src in engine_sources(current_app, db)}


def get_source(key: str) -> BrowseSource:
    src = browse_sources().get(key)
    if src is None:
        abort(404)
    return src


@dataclass(frozen=True)
class BrowsedTable:
    src: BrowseSource
    table: Table
    entry: TableEntry
    pk: Tuple[str, ...]
    hidden: FrozenSet[str]

    @classmethod
    def of(cls, src: BrowseSource, name: str) -> 'BrowsedTable':
        table = src.table(name)
        return cls(src, table, src.entry(name), _primary_key(src, table), _hidden_columns(table))

    @property
    def name(self) -> str:
        return self.table.name

    @cached_property
    def shown(self) -> List[Column]:
        return [c for c in self.table.c if c.name not in self.hidden]

    @cached_property
    def shown_names(self) -> List[str]:
        return [c.name for c in self.shown]

    @cached_property
    def sortable(self) -> FrozenSet[str]:
        return frozenset(c.name for c in self.shown if column_kind(c) not in UNSORTABLE_KINDS)

    @cached_property
    def indexed(self) -> FrozenSet[str]:
        """Columns that lead an index (or the PK): sorting on them is cheap."""
        lead = {c.name for c in list(self.table.primary_key)[:1]}
        lead |= {list(ix.columns)[0].name for ix in self.table.indexes if len(ix.columns)}
        return frozenset(lead)

    @property
    def orm_class(self) -> Optional[str]:
        return self.src.overlay().classes.get(self.name)

    def column(self, name: str) -> Optional[Column]:
        """A shown column by name; None for unknown or hidden."""
        return self.table.c[name] if name in self.table.c and name not in self.hidden else None

    def url(self, **args) -> str:
        return url_for('db_browser.table', source=self.src.key, table=self.name, **args)

    def row_url(self, row: Dict[str, object]) -> Optional[str]:
        if not self.pk or any(row.get(c) is None for c in self.pk):
            return None
        return url_for('db_browser.row', source=self.src.key, table=self.name,
                       **key_args({c: row[c] for c in self.pk}))

    @cached_property
    def fk_edges(self) -> List[FkEdge]:
        """Outgoing FKs whose target table exists in this source."""
        names = {e.name for e in self.src.catalog()}
        return [e for e in self.src.fk_graph().outgoing.get(self.name, ())
                if e.ref_table in names]

    @cached_property
    def referencing_edges(self) -> List[FkEdge]:
        """Incoming FKs from tables that exist in this source."""
        names = {e.name for e in self.src.catalog()}
        return [e for e in self.src.fk_graph().incoming.get(self.name, ()) if e.table in names]

    def fk_links(self, row: Dict[str, object]) -> Dict[str, str]:
        """{column: url} for each outgoing FK whose values are all present in ``row``."""
        links = {}
        for edge in self.fk_edges:
            if any(row.get(c) is None for c in edge.columns):
                continue
            links.setdefault(edge.columns[0], url_for(
                'db_browser.row', source=self.src.key, table=edge.ref_table,
                **key_args(dict(zip(edge.ref_columns, (row[c] for c in edge.columns))))))
        return links


def _primary_key(src: BrowseSource, table: Table) -> Tuple[str, ...]:
    """Reflected PK, else the ORM's (views, PK-less tables); () when neither knows."""
    pk = tuple(c.name for c in table.primary_key)
    if pk:
        return pk
    orm_pk = src.overlay().primary_keys.get(table.name, ())
    return orm_pk if orm_pk and all(c in table.c for c in orm_pk) else ()


def _hidden_columns(table: Table) -> FrozenSet[str]:
    hidden = {c.name for c in table.c if DEFAULT_POLICY.is_redacted(table.name, c.name)}
    for (tname, cname), perm in GATED_COLUMNS.items():
        if tname == table.name and not has_permission(current_user, perm):
            hidden.add(cname)
    return frozenset(hidden)
