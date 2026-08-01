"""Tests for the structured chart drill scheme (`charts/links.py`).

The href strings these produce are parsed by `svg-chart-links.js`, so the
format is a cross-language contract. Python-side tests can't execute the JS,
but they can pin the exact strings the JS is written against — which is what
the literals below are for. If you change the scheme, change both.
"""

import ast
from pathlib import Path

import pytest

from webapp.dashboards.charts import links

JS = Path(__file__).resolve().parents[2] / 'src' / 'webapp' / 'static' / 'js' / 'svg-chart-links.js'


class TestEncode:
    def test_shape(self):
        assert links.encode('row', 'data-owner-uid', 1234) == '#sam/row/data-owner-uid/1234'

    def test_percent_encodes_separators(self):
        """A value containing a slash must not read as an extra segment.

        Projcodes and usernames are user data; a raw slash would silently
        shift every following segment by one.
        """
        assert links.encode('user', 'a/b') == '#sam/user/a%2Fb'

    @pytest.mark.parametrize('raw', ['a b', 'a#b', 'a?b', 'a%b', 'a&b', 'ü'])
    def test_percent_encodes_url_metacharacters(self, raw):
        out = links.encode('user', raw)
        tail = out.split('/', 2)[2]
        assert tail != raw
        from urllib.parse import unquote
        assert unquote(tail) == raw

    def test_accepts_non_string_segments(self):
        assert links.encode('row', 'data-jh-bucket', 0) == '#sam/row/data-jh-bucket/0'


class TestDrillTargets:
    @pytest.mark.parametrize('target,value,expect', [
        (links.DAY, '2026-03-01', '#sam/day/2026-03-01'),
        (links.USAGE_USER, 'alice', '#sam/user/alice'),
        (links.AH_BUCKET, 3, '#sam/row/data-ah-bucket/3'),
        (links.JH_BUCKET, 0, '#sam/row/data-jh-bucket/0'),
        (links.JT_PERIOD, 7, '#sam/row/data-jt-period/7'),
        (links.DISK_OWNER, 1000, '#sam/row/data-owner-uid/1000'),
        (links.DISK_GROUP, 500, '#sam/row/data-group-gid/500'),
        (links.JOB_USER, 'bob', '#sam/row/data-job-user/bob'),
        (links.JOB_PROJECT, 'SCSG0001', '#sam/row/data-job-project/SCSG0001'),
    ])
    def test_urls(self, target, value, expect):
        assert target.url(value) == expect

    def test_row_drills_are_constructible_ad_hoc(self):
        """The payoff of the scheme: a new drill-down chart is one attribute
        name declared at the chart, with no JavaScript change at all."""
        assert links.RowDrill('data-anything').url('x') == '#sam/row/data-anything/x'

    def test_targets_are_frozen(self):
        with pytest.raises(Exception):
            links.JOB_USER.attr = 'data-nope'


class TestModalRoute:
    def test_resolves_a_real_url(self, app):
        with app.test_request_context('/'):
            url = links.PROJECT_MODAL.url('SCSG0001')
        assert url.startswith('/') and 'SCSG0001' in url
        assert not url.startswith('#')

    def test_needs_no_app_context_at_import(self):
        """`url_for` must resolve lazily inside `url()`. Eager resolution at
        class-definition time would break importing the module at all."""
        assert isinstance(links.USER_MODAL, links.ModalRoute)

    def test_modal_urls_are_matched_by_the_js_table(self, app):
        js = JS.read_text()
        with app.test_request_context('/'):
            proj = links.PROJECT_MODAL.url('SCSG0001')
            user = links.USER_MODAL.url('alice')
        assert '/user/project-details-modal/' in js
        assert '/admin/user/' in js
        assert proj.startswith('/user/project-details-modal/')
        assert user.startswith('/admin/user/')


class TestJavaScriptContract:
    """Pin the pieces of the scheme the JS hardcodes."""

    def test_js_scheme_matches_python(self):
        assert f"var SCHEME = '{links.SCHEME}/';" in JS.read_text()

    def test_js_dispatches_every_action_python_emits(self):
        js = JS.read_text()
        for action in ('row', 'day', 'user'):
            assert f"case '{action}':" in js, f'JS does not handle action {action!r}'

    def test_js_no_longer_carries_a_prefix_table(self):
        """The point of the change: the attribute travels in the href, so
        there is no cross-language table left to drift."""
        js = JS.read_text()
        assert 'ROW_SENTINELS' not in js
        for dead in ('#ah-bar-', '#jh-bar-', '#jt-bar-', '#day-bar-',
                     '#usage-user-', '#disk-ent-', '#job-user-', '#job-proj-'):
            assert dead not in js, f'legacy sentinel {dead!r} still in the JS'


def test_links_module_imports_no_matplotlib():
    """`links.py` and `series.py` are the two modules a different rendering
    backend could reuse. Keeping them matplotlib-free is what makes that seam
    real rather than aspirational — enforced by AST scan rather than by
    convention, because an accidental import would never fail anything.
    """
    src = Path(links.__file__).read_text()
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
    assert 'matplotlib' not in imported
    assert 'numpy' not in imported
