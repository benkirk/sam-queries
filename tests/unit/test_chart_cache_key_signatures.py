"""Every chart's cache-key function must accept the generator's call shape.

A key function is invoked with the *view's own arguments*
(``webapp/caching/chart.py``: ``key = _key(*args, **kwargs)``), so it sees
exactly what the caller passed — not what the generator's defaults would fill
in. Two consequences, both of which had already gone wrong:

1. A parameter the generator defaults but the key function does not raises
   ``TypeError`` before the chart body ever runs. ``generate_user_usage_pie_chart``
   was one ``metric=`` away from a 500 on every call that omitted it.

2. A parameter defaulted *differently* in the two places is a silent cache
   aliasing bug: ``_pace_cache_key`` defaulted ``top_n=15`` while
   ``generate_pace_chart_matplotlib`` defaults ``20``, so an explicit
   ``top_n=15`` call and an omitted-``top_n`` call hash identically while
   rendering different charts. With Redis the cache is shared across workers
   and pods, so that aliasing would be global.

Both are invisible in normal operation — nothing fails, the wrong SVG is just
served — which is why they get a structural test rather than a comment.
"""

import inspect

import pytest

from webapp.dashboards import charts

#: ``(generator_name, key_fn_name)`` for every chart that supplies a key_fn.
#: The four charts using the decorator's default key_fn are covered by
#: ``test_default_key_fn_charts_take_one_argument`` below instead.
KEYED_CHARTS = [
    ('generate_usage_timeseries_matplotlib', '_usage_timeseries_cache_key'),
    ('generate_usage_timeseries_stacked_by_user', '_usage_stacked_cache_key'),
    ('generate_disk_usage_stacked_area', '_disk_usage_stacked_area_cache_key'),
    ('generate_user_proj_stacked_area', '_user_proj_stacked_area_cache_key'),
    ('generate_distribution_histogram', '_distribution_cache_key'),
    ('generate_disk_entity_pie_chart', '_disk_entity_pie_cache_key'),
    ('generate_user_usage_pie_chart', '_user_usage_pie_cache_key'),
    ('generate_jobs_histogram', '_jobs_histogram_cache_key'),
    ('generate_jobs_timeseries_stacked', '_jobs_timeseries_cache_key'),
    ('generate_jobs_usage_pie_chart', '_jobs_usage_pie_cache_key'),
    ('generate_pace_chart_matplotlib', '_pace_cache_key'),
]

#: Charts that use the decorator's default ``key_fn``, which hashes only
#: ``args[0]``. Safe only while they take exactly one meaningful argument.
DEFAULT_KEY_CHARTS = [
    'generate_nodetype_history_matplotlib',
    'generate_queue_history_matplotlib',
    'generate_facility_pie_chart_matplotlib',
    'generate_allocation_type_pie_chart_matplotlib',
]


def _params(fn):
    """Parameters of the *undecorated* function.

    ``chart_cached`` wraps with ``functools.wraps``, so ``signature()``
    follows ``__wrapped__`` back to the original — which is what we want.
    """
    return inspect.signature(fn).parameters


@pytest.mark.parametrize('gen_name,key_name', KEYED_CHARTS)
def test_cache_key_accepts_generator_signature(gen_name, key_name):
    gen = getattr(charts, gen_name)
    key = getattr(charts, key_name)

    gen_params = _params(gen)
    key_params = _params(key)

    missing = [n for n in gen_params if n not in key_params]
    assert not missing, (
        f'{key_name} is missing {missing}, which {gen_name} accepts. '
        f'The key function is called with the caller\'s arguments, so a '
        f'parameter it does not accept raises TypeError before the chart runs.')


@pytest.mark.parametrize('gen_name,key_name', KEYED_CHARTS)
def test_cache_key_defaults_match_generator(gen_name, key_name):
    """Mismatched defaults alias two different renderings onto one key."""
    gen_params = _params(getattr(charts, gen_name))
    key_params = _params(getattr(charts, key_name))

    drift = {
        name: (p.default, key_params[name].default)
        for name, p in gen_params.items()
        if p.default is not inspect.Parameter.empty
        and name in key_params
        and key_params[name].default is not inspect.Parameter.empty
        and key_params[name].default != p.default
    }
    assert not drift, (
        f'{key_name} defaults differ from {gen_name}: {drift}. '
        f'An omitted argument hashes with the key function\'s default but '
        f'renders with the generator\'s — two charts, one cache entry.')


@pytest.mark.parametrize('gen_name', DEFAULT_KEY_CHARTS)
def test_default_key_fn_charts_take_one_argument(gen_name):
    """The decorator's default key_fn hashes only ``args[0]``.

    These four charts are correct today because they take a single argument.
    Adding a second one — a ``layout=``/``theme=`` render axis, say — would be
    silently dropped from the key and the first-rendered variant served to
    everyone. If this fails, that chart needs an explicit ``key_fn``.
    """
    params = _params(getattr(charts, gen_name))
    assert len(params) == 1, (
        f'{gen_name} now takes {list(params)}, but uses the default key_fn '
        f'which hashes only args[0]. Give it an explicit key_fn.')


def test_user_usage_pie_renders_without_explicit_metric(app):
    """Regression: this raised TypeError in the key function."""
    with app.test_request_context('/'):
        out = charts.generate_user_usage_pie_chart(
            [{'username': 'alice', 'charges': 10.0, 'jobs': 2, 'core_hours': 5.0}])
    assert '<svg' in out


def test_pace_explicit_default_top_n_matches_omitted(app):
    """Regression: explicit top_n=20 and omitted top_n must hash the same."""
    from datetime import datetime

    allocs = [{'projcode': 'P0001',
               'start_date': datetime(2026, 1, 1), 'end_date': datetime(2026, 12, 31),
               'total_amount': 1000.0, 'total_used': 400.0}]
    now = datetime(2026, 6, 1)
    assert (charts._pace_cache_key(allocs, now)
            == charts._pace_cache_key(allocs, now, 180, 20))
