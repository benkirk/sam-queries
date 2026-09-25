"""The browsable sources and their per-process metadata cache.

A source is one engine + schema: each fs_scans collection is its own source.
Unknown source keys and table names 404 here, so a route never builds SQL from
a name reflection has not produced.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional, Tuple

from flask import abort, current_app
from flask_login import current_user
from sqlalchemy import Table
from sqlalchemy.engine import Engine

from dbbrowse import (DEFAULT_POLICY, FkGraph, MetadataCache, OrmOverlay, TableEntry,
                      column_kind, load_catalog, load_fk_graph, read_only_connection,
                      reflect_table)
from webapp.extensions import db
from webapp.utils.engine_inventory import engine_sources
from webapp.utils.rbac import Permission, has_permission

CACHE = MetadataCache(ttl_seconds=900)

_OVERLAY_BIND = {'sam': None, 'system_status': 'system_status'}
_NOTES = {'sam': 'Times are naive Mountain.', 'system_status': 'Times are naive UTC.'}

# Columns shown only to holders of a further permission: (table, column) -> Permission.
GATED_COLUMNS = {('xras_action_log', 'raw_payload'): Permission.MANAGE_XRAS}

UNSORTABLE_KINDS = frozenset({'binary', 'json', 'long'})


@dataclass(frozen=True)
class BrowseSource:
    key: str
    label: str
    family: str
    engine: Engine
    schema: Optional[str] = None

    @property
    def note(self) -> Optional[str]:
        return _NOTES.get(self.family)


def browse_sources() -> Dict[str, BrowseSource]:
    out = {}
    for src in engine_sources(current_app, db):
        if src.family != 'fs_scans':
            out[src.key] = BrowseSource(src.key, src.label, src.family, src.engine)
            continue
        for collection, engine in src.engines.items():
            key = f'{src.key}.{collection}'
            schema = collection if engine.dialect.name == 'postgresql' else None
            out[key] = BrowseSource(key, f'{src.label} / {collection}', 'fs_scans',
                                    engine, schema)
    return out


def get_source(key: str) -> BrowseSource:
    src = browse_sources().get(key)
    if src is None:
        abort(404)
    return src


def connect(src: BrowseSource):
    return read_only_connection(
        src.engine, timeout_ms=current_app.config['DB_BROWSER_STATEMENT_TIMEOUT_MS'])


def catalog(src: BrowseSource) -> Tuple[TableEntry, ...]:
    def load():
        with connect(src) as conn:
            return tuple(load_catalog(conn, src.schema))
    return CACHE.get_or_load((src.key, 'catalog'), load)


def overlay(src: BrowseSource) -> OrmOverlay:
    if src.family not in _OVERLAY_BIND:
        return OrmOverlay()

    def load():
        from sam.base import Base
        return OrmOverlay.from_registry(Base.registry, bind_key=_OVERLAY_BIND[src.family])
    return CACHE.get_or_load((src.key, 'overlay'), load)


def fk_graph(src: BrowseSource) -> FkGraph:
    def load():
        with connect(src) as conn:
            graph = load_fk_graph(conn, src.schema)
        return graph.merged(overlay(src).fk_edges)
    return CACHE.get_or_load((src.key, 'fks'), load)


def table_entry(src: BrowseSource, name: str) -> TableEntry:
    for entry in catalog(src):
        if entry.name == name:
            return entry
    abort(404)


def get_table(src: BrowseSource, name: str) -> Table:
    table_entry(src, name)

    def load():
        with connect(src) as conn:
            return reflect_table(conn, src.schema, name)
    return CACHE.get_or_load((src.key, 'table', name), load)


def primary_key(src: BrowseSource, table: Table) -> Tuple[str, ...]:
    """Reflected PK, else the ORM's (views, PK-less tables); () when neither knows."""
    pk = tuple(c.name for c in table.primary_key)
    if pk:
        return pk
    orm_pk = overlay(src).primary_keys.get(table.name, ())
    return orm_pk if orm_pk and all(c in table.c for c in orm_pk) else ()


def hidden_columns(table: Table) -> FrozenSet[str]:
    hidden = {c.name for c in table.c if DEFAULT_POLICY.is_redacted(table.name, c.name)}
    for (tname, cname), perm in GATED_COLUMNS.items():
        if tname == table.name and not has_permission(current_user, perm):
            hidden.add(cname)
    return frozenset(hidden)


def sortable(table: Table, hidden: FrozenSet[str]) -> FrozenSet[str]:
    return frozenset(c.name for c in table.c
                     if c.name not in hidden and column_kind(c) not in UNSORTABLE_KINDS)


def indexed_columns(table: Table) -> FrozenSet[str]:
    """Columns that lead an index (or the PK): sorting on them is cheap."""
    lead = {c.name for c in list(table.primary_key)[:1]}
    lead |= {list(ix.columns)[0].name for ix in table.indexes if len(ix.columns)}
    return frozenset(lead)
