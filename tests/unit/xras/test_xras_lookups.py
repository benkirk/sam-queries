"""The validate-only lookup memo (``sam.xras.lookups``) and the worklist's seeding rule."""

from types import SimpleNamespace

from sam.queries.xras_accounts import _seed_found
from sam.xras.lookups import batch_lookups, cached, seed


def _session():
    return SimpleNamespace(info={})


def _counting(value):
    calls = []

    def load():
        calls.append(1)
        return value
    return load, calls


def test_no_memo_is_a_plain_call_every_time():
    """Live posts open no memo, so every lookup still queries."""
    session = _session()
    load, calls = _counting('row')
    assert cached(session, ('k', 1), load) == 'row'
    assert cached(session, ('k', 1), load) == 'row'
    assert len(calls) == 2
    seed(session, [(('k', 2), 'seeded')])
    assert session.info == {}


def test_an_open_memo_loads_each_key_once_and_keeps_misses():
    session = _session()
    hit, hit_calls = _counting('row')
    miss, miss_calls = _counting(None)
    with batch_lookups(session):
        for _ in range(3):
            assert cached(session, ('k', 1), hit) == 'row'
            assert cached(session, ('k', 2), miss) is None
    assert (len(hit_calls), len(miss_calls)) == (1, 1)
    assert session.info == {}


def test_a_nested_open_joins_and_only_the_outer_one_closes():
    session = _session()
    with batch_lookups(session) as outer:
        with batch_lookups(session) as inner:
            assert inner is outer
        seed(session, [(('k', 1), 'seeded')])
        assert cached(session, ('k', 1), lambda: 'queried') == 'seeded'
    assert session.info == {}


def test_seeding_keeps_misses_only_when_every_found_key_was_wanted():
    session = _session()
    with batch_lookups(session) as memo:
        _seed_found(session, 'user', {'ann', 'bob'}, {'ann': 'Ann'})
        assert memo == {('user', 'ann'): 'Ann', ('user', 'bob'): None}

    with batch_lookups(session) as memo:
        # A row matched under the collation but not under its key: its misses are unsafe.
        _seed_found(session, 'user', {'josé', 'bob'}, {'jose': 'Jose'})
        assert memo == {}
