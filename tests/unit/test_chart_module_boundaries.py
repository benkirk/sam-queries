"""The chart package's module boundaries, enforced rather than documented.

Three modules — `links.py`, `series.py`, `jobs_metrics.py` — must import no
matplotlib. They are the ones a different rendering backend would reuse
verbatim: URL construction, band normalization, and plugin-envelope
arithmetic. Keeping them clean is the whole migration seam this refactor
bought instead of an abstract base layer.

It has to be a test rather than a convention, because an accidental
`import matplotlib` would never fail anything — it would just quietly weld the
seam shut.

Also pinned here: the package facade's public surface, and the absence of the
import cycle that the caching layer's lazy imports exist to avoid.
"""

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[2] / 'src' / 'webapp' / 'dashboards' / 'charts'

#: Modules that must stay renderer-agnostic.
BACKEND_FREE = ['links.py', 'series.py', 'jobs_metrics.py']

#: Every module in the package, for the general hygiene checks.
ALL_MODULES = sorted(p.name for p in PKG.glob('*.py'))


def _imports(path: Path) -> set:
    """Top-level module names imported anywhere in the file, including inside
    functions — a lazy import is still an import."""
    tree = ast.parse(path.read_text())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split('.')[0])
    return out


@pytest.mark.parametrize('name', BACKEND_FREE)
def test_backend_free_modules_import_no_matplotlib(name):
    imported = _imports(PKG / name)
    assert 'matplotlib' not in imported, (
        f'{name} imports matplotlib. It is one of the modules a different '
        f'rendering backend would reuse; keeping it clean is the point.')


@pytest.mark.parametrize('name', BACKEND_FREE)
def test_backend_free_modules_import_no_numpy(name):
    assert 'numpy' not in _imports(PKG / name)


def test_expected_package_layout():
    """A new module is a design decision, so it should be a visible diff."""
    assert ALL_MODULES == [
        '__init__.py', 'base.py', 'dualpanel.py', 'histogram.py',
        'jobs_metrics.py', 'layout.py', 'links.py', 'pace.py', 'pie.py',
        'series.py', 'stacked.py', 'theme.py',
    ]


def test_no_module_exceeds_the_readable_size():
    """The refactor's stated goal was breaking up a file that no longer fit in
    anyone's head. 2,011 lines became twelve modules; none should drift back."""
    oversized = {p.name: len(p.read_text().splitlines())
                 for p in PKG.glob('*.py')
                 if len(p.read_text().splitlines()) > 550}
    assert not oversized, f'modules growing back toward the old monolith: {oversized}'


def test_family_modules_do_not_import_each_other():
    """Families are siblings, not a chain.

    The one allowed edge is `stacked.py` -> `dualpanel.py` for the shared
    `_to_display_tz`. If a second edge appears, the shared thing belongs in
    `base.py` or `theme.py` instead.
    """
    families = ['pie.py', 'stacked.py', 'histogram.py', 'dualpanel.py', 'pace.py']
    stems = {f[:-3] for f in families}
    edges = []
    for f in families:
        tree = ast.parse((PKG / f).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split('.')
                if parts[:-1] == ['webapp', 'dashboards', 'charts'] and parts[-1] in stems:
                    edges.append((f, parts[-1]))
    assert edges == [('stacked.py', 'dualpanel')], f'unexpected family edges: {edges}'


def test_caching_package_stays_chart_free():
    """`base.py` imports `webapp.caching`; if that package ever imports a
    chart module at its top level the cycle closes and the whole app fails to
    import. The lazy imports in `caching/__init__.py` exist for this, but
    nothing enforced it beyond a comment.
    """
    caching_init = PKG.parents[1] / 'caching' / '__init__.py'
    tree = ast.parse(caching_init.read_text())
    top_level = set()
    for node in tree.body:                      # module scope only
        if isinstance(node, ast.Import):
            top_level.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module)
    offenders = [m for m in top_level if 'dashboards' in m or 'charts' in m]
    assert not offenders, (
        f'webapp.caching imports {offenders} at module scope, closing the '
        f'import cycle with webapp.dashboards.charts.')


class TestFacade:
    """The package facade's public surface."""

    def test_all_names_resolve(self):
        from webapp.dashboards import charts
        missing = [n for n in charts.__all__ if not hasattr(charts, n)]
        assert not missing, f'__all__ names a nonexistent attribute: {missing}'

    def test_every_generator_is_exported(self):
        from webapp.dashboards import charts
        generators = {n for n in dir(charts) if n.startswith('generate_')}
        assert generators <= set(charts.__all__), (
            f'generators missing from __all__: {generators - set(charts.__all__)}')

    def test_private_names_tests_import_still_resolve(self):
        """These moved onto classes or into sibling modules during the
        refactor; the facade keeps their historical spellings because tests
        and profiling scripts import them from here."""
        from webapp.dashboards import charts
        for name in ('_JOBS_METRIC_KEYS', '_jobs_bucket_segments',
                     '_jobs_metric_value', '_jobs_timeseries_series',
                     '_pie_cumulative_keep', '_pie_trim', '_bucket_segments',
                     '_pace_bands', '_pace_key_fields',
                     '_jobs_histogram_cache_key', '_jobs_timeseries_cache_key',
                     '_jobs_usage_pie_cache_key', '_pace_cache_key',
                     '_empty_state', '_fig_to_svg', '_to_display_tz',
                     '_project_modal_url', '_user_modal_url',
                     '_USAGE_METRIC_YLABELS', '_FONT_DIR'):
            assert hasattr(charts, name), f'facade dropped {name}'

    def test_import_side_effects_still_fire(self):
        """`import webapp.dashboards.charts` must still register the fonts and
        apply the rcParams — test_chart_fonts.py depends on it, and so does
        every chart's typography."""
        import matplotlib.pyplot as plt
        import webapp.dashboards.charts  # noqa: F401
        assert plt.rcParams['font.family'][0] == 'Poppins'
