"""Catalog, reflection, FK graph and ORM overlay against the real SAM schema."""
import pytest
from sqlalchemy import create_engine, text

from dbbrowse import (FkEdge, FkGraph, MetadataCache, OrmOverlay, load_catalog,
                      load_fk_graph, read_only_connection, reflect_table)


@pytest.fixture(scope='module')
def sam_meta(engine):
    with read_only_connection(engine) as conn:
        yield {'catalog': load_catalog(conn), 'fks': load_fk_graph(conn)}


def _overlay():
    from sam.base import Base
    return OrmOverlay.from_registry(Base.registry, bind_key=None)


def test_catalog_covers_every_orm_table(sam_meta):
    assert set(_overlay().classes) <= {e.name for e in sam_meta['catalog']}


@pytest.mark.mysql_only   # the Postgres copy is built from the ORM, so has no unmapped tables
def test_catalog_lists_unmapped_tables(sam_meta):
    assert 'schema_version' in {e.name for e in sam_meta['catalog']}


def test_catalog_marks_orm_views_as_views(sam_meta):
    from sam.base import Base
    orm_views = {m.persist_selectable.name for m in Base.registry.mappers
                 if m.persist_selectable.info.get('is_view')
                 and getattr(m.class_, '__bind_key__', None) is None}
    kinds = {e.name: e.kind for e in sam_meta['catalog']}
    assert orm_views and all(kinds[v] == 'view' for v in orm_views)


def test_catalog_is_sorted_case_insensitively(sam_meta):
    names = [e.name for e in sam_meta['catalog']]
    assert names == sorted(names, key=str.lower)


def test_fk_graph_both_directions(sam_meta):
    fks = sam_meta['fks']
    into_users = {(e.table, e.columns) for e in fks.incoming['users']}
    assert ('account_user', ('user_id',)) in into_users
    out_of = {e.ref_table for e in fks.outgoing['account_user']}
    assert {'users', 'account'} <= out_of


def test_reflect_does_not_follow_fks(engine):
    with read_only_connection(engine) as conn:
        table = reflect_table(conn, None, 'account_user')
    assert set(table.metadata.tables) == {'account_user'}
    assert [c.name for c in table.primary_key] == ['account_user_id']


def test_overlay_supplies_view_pk_and_class_names():
    overlay = _overlay()
    assert overlay.classes['users'] == 'User'
    assert overlay.primary_keys['users'] == ('user_id',)
    assert overlay.primary_keys['comp_activity_charge']   # a view: reflection has no PK
    assert all(e.origin == 'orm' for e in overlay.fk_edges)


def test_overlay_is_scoped_to_its_bind():
    from sam.base import Base
    status = OrmOverlay.from_registry(Base.registry, bind_key='system_status')
    assert 'users' not in status.classes
    assert set(status.classes).isdisjoint(_overlay().classes)


def test_merged_graph_adds_only_missing_edges():
    db = FkGraph.from_edges([FkEdge('a', ('b_id',), 'b', ('id',))])
    merged = db.merged([FkEdge('a', ('b_id',), 'b', ('id',), 'orm'),
                        FkEdge('a', ('c_id',), 'c', ('id',), 'orm')])
    assert [(e.columns, e.origin) for e in merged.outgoing['a']] == [
        (('b_id',), 'db'), (('c_id',), 'orm')]
    assert [e.table for e in merged.incoming['c']] == ['a']


def test_sqlite_catalog_has_no_estimates(tmp_path):
    eng = create_engine(f'sqlite:///{tmp_path}/t.db')
    with eng.begin() as conn:
        conn.execute(text('CREATE TABLE Beta (id INTEGER PRIMARY KEY)'))
        conn.execute(text('CREATE TABLE alpha (id INTEGER PRIMARY KEY, '
                          'beta_id INTEGER REFERENCES Beta(id))'))
        conn.execute(text('CREATE VIEW v AS SELECT id FROM alpha'))
    with read_only_connection(eng) as conn:
        cat = load_catalog(conn)
        fks = load_fk_graph(conn)
    eng.dispose()
    assert [(e.name, e.kind, e.estimate) for e in cat] == [
        ('alpha', 'table', None), ('Beta', 'table', None), ('v', 'view', None)]
    assert fks.incoming['Beta'][0].columns == ('beta_id',)


def test_metadata_cache_ttl_and_prefix_invalidation():
    cache = MetadataCache(ttl_seconds=60)
    calls = []

    def loader():
        calls.append(1)
        return len(calls)

    assert cache.get_or_load(('sam', 'catalog'), loader) == 1
    assert cache.get_or_load(('sam', 'catalog'), loader) == 1
    cache.get_or_load(('status', 'catalog'), loader)
    cache.invalidate(('sam',))
    assert cache.get_or_load(('sam', 'catalog'), loader) == 3
    assert cache.get_or_load(('status', 'catalog'), loader) == 2

    expired = MetadataCache(ttl_seconds=-1)
    expired.get_or_load(('k',), loader)
    expired.get_or_load(('k',), loader)
    assert len(calls) == 5
