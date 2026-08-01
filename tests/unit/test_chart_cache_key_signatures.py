"""Every chart's cache key must cover everything its rendering depends on.

A key function is invoked with the *view's own arguments*
(``webapp/caching/chart.py``: ``key = _key(*args, **kwargs)``), so it sees
exactly what the caller passed — not what the generator's defaults would fill
in. Three failure modes, all silent, all of which had already happened or were
one edit away:

1. A parameter the generator defaults but the key function does not raises
   ``TypeError`` before the chart body ever runs. ``generate_user_usage_pie_chart``
   was one ``metric=`` away from a 500 on every call that omitted it.

2. A parameter defaulted *differently* in the two places aliases two
   renderings onto one entry. ``_pace_cache_key`` defaulted ``top_n=15``
   while its generator defaults ``20``.

3. **A parameter the key ignores entirely.** Both cache implementations
   default ``key_fn`` to ``lambda *args, **kwargs: content_hash(args[0])`` —
   every argument but the first positional one is dropped. This is the trap
   the ``layout``/``theme`` render axes would have fallen into; ``chart_view``
   closes it structurally by composing them into the key itself.

Charts migrate to ``chart_view`` over the course of this refactor, so the
tests below split on whether a chart has been migrated yet rather than
hardcoding one list.
"""

import inspect
from datetime import datetime

import pytest

from webapp.dashboards import charts

#: ``(generator_name, key_fn_name)`` for charts still bound as plain
#: functions with a hand-written key. Shrinks to empty as the refactor lands.
KEYED_CHARTS = [
]

#: Charts still using the decorator's default ``key_fn``. Safe only while they
#: take exactly one meaningful argument. Empty once every chart is migrated —
#: `chart_view` always supplies an explicit key.
DEFAULT_KEY_CHARTS = []

#: Generators that accept the render axes without being ``chart_view``-bound
#: because they *delegate* to one that is, so the axes do reach a cache key —
#: just not their own. Exempt from `test_unmigrated_charts_do_not_accept_render_axes`,
#: and required by `test_delegating_facades_forward_the_axes` to prove the
#: forwarding actually happens rather than being asserted here.
DELEGATING_FACADES = {
    'generate_jobs_user_pie_chart': 'generate_jobs_usage_pie_chart',
}


def _public_charts():
    return {n: getattr(charts, n) for n in dir(charts) if n.startswith('generate_')}


def _migrated():
    """Charts bound through ``chart_view`` — they carry ``chart_class``."""
    return {n: fn.chart_class for n, fn in _public_charts().items()
            if hasattr(fn, 'chart_class')}


def _params(fn):
    """Parameters of the undecorated function (``functools.wraps`` sets
    ``__wrapped__``, and ``chart_view`` sets an explicit ``__signature__``)."""
    return inspect.signature(fn).parameters


def _pending(name):
    return [(n, k) for n, k in KEYED_CHARTS if n == name]


# --------------------------------------------------------------------------
# Charts not yet migrated
# --------------------------------------------------------------------------

@pytest.mark.parametrize('gen_name,key_name', KEYED_CHARTS)
def test_cache_key_accepts_generator_signature(gen_name, key_name):
    gen_params = _params(getattr(charts, gen_name))
    key_params = _params(getattr(charts, key_name))
    missing = [n for n in gen_params if n not in key_params]
    assert not missing, (
        f'{key_name} is missing {missing}, which {gen_name} accepts. The key '
        f'function is called with the caller\'s arguments, so a parameter it '
        f'does not accept raises TypeError before the chart runs.')


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
        f'{key_name} defaults differ from {gen_name}: {drift}. An omitted '
        f'argument hashes with the key function\'s default but renders with '
        f'the generator\'s — two charts, one cache entry.')


@pytest.mark.parametrize('gen_name', DEFAULT_KEY_CHARTS)
def test_default_key_fn_charts_take_one_argument(gen_name):
    """The decorator's default key_fn hashes only ``args[0]``.

    Correct today only because these take a single argument. Adding a second
    — a render axis, say — would be silently dropped from the key and the
    first-rendered variant served to everyone. If this fails, that chart needs
    an explicit ``key_fn`` (which migrating it to ``chart_view`` provides).
    """
    params = _params(getattr(charts, gen_name))
    assert len(params) == 1, (
        f'{gen_name} now takes {list(params)}, but uses the default key_fn '
        f'which hashes only args[0]. Give it an explicit key_fn.')


def test_unmigrated_charts_do_not_accept_render_axes():
    """A chart that takes ``layout``/``theme`` without going through
    ``chart_view`` has an unkeyed render axis — the aliasing bug this whole
    module exists to prevent."""
    for name, fn in _public_charts().items():
        if hasattr(fn, 'chart_class') or name in DELEGATING_FACADES:
            continue
        params = set(_params(fn))
        assert not (params & {'layout', 'theme'}), (
            f'{name} accepts a render axis but is not bound via chart_view, '
            f'so the axis is missing from its cache key.')


@pytest.mark.parametrize('facade,delegate', sorted(DELEGATING_FACADES.items()))
def test_delegating_facades_forward_the_axes(facade, delegate, app):
    """The exemption above is only sound if the facade really does pass the
    axes down to the bound chart that owns the cache key.

    Asserted by rendering rather than by reading the source: a facade that
    accepted ``layout=`` and dropped it on the floor would satisfy every
    signature check in this module while quietly serving desktop SVGs to
    phones — the same class of silent aliasing bug the module header lists.
    """
    fn = getattr(charts, facade)
    assert facade in _public_charts()
    assert hasattr(getattr(charts, delegate), 'chart_class'), (
        f'{delegate} is not chart_view-bound, so {facade} has no keyed home')

    params = _params(fn)
    assert params['layout'].default == 'desktop'
    assert params['theme'].default == 'light'

    # Reuse the fingerprint suite's payload rather than inventing one: the
    # empty-state short-circuit would make a malformed hand-rolled dict pass
    # this test for the wrong reason.
    from chart_samples import CASES
    sample = next(((a, k) for _id, f, a, k in CASES
                   if f is fn and not _id.endswith('.empty')), None)
    assert sample is not None, f'{facade} has no non-empty case in chart_samples'
    args, kwargs = sample

    with app.test_request_context('/'):
        desktop = fn(*args, **kwargs)
        mobile = fn(*args, **kwargs, layout='mobile')
    assert '<svg' in desktop and '<svg' in mobile
    assert desktop != mobile, (
        f'{facade} accepts layout= but renders identically — it is not '
        f'forwarding the axis to {delegate}')


# --------------------------------------------------------------------------
# Charts migrated to `chart_view`
# --------------------------------------------------------------------------

def test_at_least_one_chart_is_migrated():
    """Guards the tests below from silently covering nothing."""
    assert _migrated(), 'no charts bound via chart_view'


def test_migrated_charts_expose_the_render_axes():
    for name, cls in _migrated().items():
        params = _params(getattr(charts, name))
        assert 'layout' in params and 'theme' in params, (
            f'{name} ({cls.__name__}) should expose layout/theme')
        assert params['layout'].default == 'desktop'
        assert params['theme'].default == 'light'


def test_migrated_charts_keep_their_signature_introspectable():
    """``chart_view`` returns a ``*args`` closure; without the explicit
    ``__signature__`` the facade would be undocumented to every reader and
    tool."""
    for name, cls in _migrated().items():
        init = [p for p in inspect.signature(cls.__init__).parameters if p != 'self']
        params = list(_params(getattr(charts, name)))
        assert params[:len(init)] == init, f'{name} lost its argument names'


def test_migrated_cache_key_accepts_the_constructor_signature():
    for name, cls in _migrated().items():
        init = [p for p in inspect.signature(cls.__init__).parameters if p != 'self']
        key = _params(cls.cache_key)
        missing = [p for p in init if p not in key]
        assert not missing, f'{cls.__name__}.cache_key is missing {missing}'


def test_migrated_charts_declare_cache_config():
    for name, cls in _migrated().items():
        assert cls.cache_name, f'{cls.__name__} has no cache_name'
        assert cls.cache_maxsize, f'{cls.__name__} has no cache_maxsize'
        assert cls.LAYOUTS and {'desktop', 'mobile', 'tablet'} <= set(cls.LAYOUTS)


def _cases_by_chart():
    """One representative fingerprint case per migrated chart.

    Reuses `chart_samples.CASES` rather than inventing a second set of
    payloads — a chart is only really exercised by data of its own shape, and
    keeping one source means a new chart cannot be half-covered.
    """
    from chart_samples import CASES
    out = {}
    for _id, fn, args, kwargs in CASES:
        if not hasattr(fn, 'chart_class') or _id.endswith('.empty'):
            continue
        out.setdefault(fn, (args, kwargs))
    return out


class TestRenderAxesAreKeyed:
    """The point of `chart_view`: layout and theme reach the cache key.

    Without this, the first-rendered variant is served to every viewer — and
    with Redis the cache is shared across workers *and* pods, so the aliasing
    would be global rather than per-process.
    """

    SAMPLE = [{'timestamp': datetime(2026, 3, 1, h), 'nodes_available': 10 - h,
               'nodes_down': h, 'nodes_allocated': 5,
               'utilization_percent': 50.0} for h in range(4)]

    def _keys(self, cls, **axes):
        from webapp.caching.chart import content_hash
        return content_hash([cls.cache_key(self.SAMPLE),
                             axes.get('layout', 'desktop'),
                             axes.get('theme', 'light')])

    def test_layout_changes_the_key(self):
        from webapp.dashboards.charts.dualpanel import NodetypeHistoryChart as C
        assert self._keys(C) != self._keys(C, layout='mobile')
        assert self._keys(C) != self._keys(C, layout='tablet')

    def test_theme_changes_the_key(self):
        from webapp.dashboards.charts.dualpanel import NodetypeHistoryChart as C
        assert self._keys(C) != self._keys(C, theme='dark')

    LAYOUTS = ('desktop', 'mobile', 'tablet')
    THEMES = ('light', 'dark')

    def test_axes_are_independent(self):
        from webapp.dashboards.charts.dualpanel import NodetypeHistoryChart as C
        seen = {self._keys(C, layout=l, theme=t)
                for l in self.LAYOUTS for t in self.THEMES}
        assert len(seen) == len(self.LAYOUTS) * len(self.THEMES), (
            'the two axes are not independent in the key')

    def test_every_migrated_chart_renders_under_every_combination(self, app):
        """Both axes must actually render, not merely be accepted.

        A vocabulary the cache distinguishes but the renderer cannot draw is
        worse than no axis at all — it caches an exception path.
        """
        cases = _cases_by_chart()
        assert cases, 'no migrated chart has a sample case'
        for fn, (args, kwargs) in cases.items():
            for lay in self.LAYOUTS:
                for thm in self.THEMES:
                    with app.test_request_context('/'):
                        out = fn(*args, **kwargs, layout=lay, theme=thm)
                    assert '<svg' in out, (
                        f'{fn.chart_class.__name__} failed at {lay}/{thm}')

    def test_unknown_axis_values_fall_back_silently(self, app):
        """Lenient like the route-level selector parsers — a stale
        localStorage replay must not 500 an htmx fragment."""
        for fn, (args, kwargs) in _cases_by_chart().items():
            with app.test_request_context('/'):
                out = fn(*args, **kwargs, layout='sideways', theme='sepia')
            assert '<svg' in out


# --------------------------------------------------------------------------
# Regressions
# --------------------------------------------------------------------------

def test_user_usage_pie_renders_without_explicit_metric(app):
    """Regression: this raised TypeError in the key function."""
    with app.test_request_context('/'):
        out = charts.generate_user_usage_pie_chart(
            [{'username': 'alice', 'charges': 10.0, 'jobs': 2, 'core_hours': 5.0}])
    assert '<svg' in out


def test_pace_explicit_default_top_n_matches_omitted():
    """Regression: explicit top_n=20 and omitted top_n must hash the same."""
    allocs = [{'projcode': 'P0001',
               'start_date': datetime(2026, 1, 1), 'end_date': datetime(2026, 12, 31),
               'total_amount': 1000.0, 'total_used': 400.0}]
    now = datetime(2026, 6, 1)
    assert (charts._pace_cache_key(allocs, now)
            == charts._pace_cache_key(allocs, now, 180, 20))
