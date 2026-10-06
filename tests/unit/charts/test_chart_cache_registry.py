"""The chart caches: names, count, and registration order.

Three things are pinned here, all of which the refactor could break silently:

- **Names.** They are Redis key prefixes (``redis_chart.py``:
  ``f'chart:{name}:'``), so renaming one orphans its warm entries rather than
  failing. ``test_redis_cache.py`` also names several directly.
- **Count.** One cache per generator: every generator is ``chart_view``-bound.
- **Order.** ``chart_cached`` appends to ``caching._chart_caches`` at
  decoration time, so the order of the bindings in ``charts/__init__.py`` is
  the order rows appear on the admin Caching card. Nothing enforces that but
  import order, which a refactor reshuffles for free.
"""

import pytest

from webapp.caching import caching
from webapp.dashboards import charts  # noqa: F401  — import registers the caches

#: Exact expected order, matching the binding order in charts/__init__.py.
EXPECTED = [
    'usage_timeseries',
    'usage_timeseries_stacked',
    'disk_usage_stacked_area',
    'user_proj_stacked_area',
    'distribution_histogram',
    'nodetype_history',
    'queue_history',
    'disk_entity_pie_chart',
    'user_usage_pie_chart',
    'jobs_histogram',
    'jobs_timeseries',
    'jobs_usage_pie_chart',
    'pace_chart',
    'fair_share_sunburst',
    'allocation_sunburst',
    'jobs_facility_sunburst',
    'panel_sunburst',
]


def _registered_names():
    return [c.name for c in caching._chart_caches]


def test_cache_names_and_order():
    assert _registered_names() == EXPECTED


def test_cache_count():
    assert len(caching._chart_caches) == 17


def test_no_duplicate_cache_names():
    """Two charts sharing a name would silently serve each other's SVGs —
    with Redis, across pods."""
    names = _registered_names()
    assert len(names) == len(set(names))


def test_every_cached_generator_has_a_cache():
    """One cache per cached generator, and no strays."""
    generators = [n for n in dir(charts) if n.startswith('generate_')]
    assert all(hasattr(getattr(charts, n), 'chart_class') for n in generators)
    assert len(generators) == len(_registered_names()) == 17


@pytest.mark.parametrize('name', EXPECTED)
def test_cache_exposes_the_introspection_api(name):
    """`cache_clear` / `cache_info` / `cache_bytes` are attributes of the
    public callables, relied on by profile_allocations.py and the
    allocations performance test."""
    cache = next(c for c in caching._chart_caches if c.name == name)
    assert hasattr(cache, 'info')
    assert hasattr(cache, 'clear')


def test_public_callables_carry_the_cache_helpers():
    for gen in (charts.generate_pace_chart_matplotlib,
                charts.generate_nodetype_history_matplotlib):
        for attr in ('cache_clear', 'cache_info', 'cache_bytes'):
            assert hasattr(gen, attr), f'{attr} missing'


def test_adapters_include_every_chart_cache():
    names = {c.name for c in caching.adapters() if c.name in set(EXPECTED)}
    assert names == set(EXPECTED)
