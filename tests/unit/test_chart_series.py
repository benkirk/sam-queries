"""Tests for `charts/series.py` — the stacked charts' band normalization."""

import ast
from pathlib import Path

import pytest

from webapp.dashboards.charts import series as S
from webapp.dashboards.charts.theme import (
    UNITY_NCAR_GRAY_LIGHT, UNITY_STACK_10, UNITY_STACK_20,
)


class TestAdapters:
    """Three producer envelopes, one shape."""

    def test_label_series(self):
        out = S.from_label_series([{'label': 'alice', 'values': [1, 2]}])
        assert out == [S.Series('alice', [1, 2], 'alice')]

    def test_username_series(self):
        out = S.from_username_series([{'username': 'bob', 'values': [3]}])
        assert out == [S.Series('bob', [3], 'bob')]

    def test_pairs(self):
        out = S.from_pairs([('carol', [4, 5])])
        assert out == [S.Series('carol', [4, 5], 'carol')]

    @pytest.mark.parametrize('adapter,raw', [
        (S.from_label_series, None), (S.from_label_series, []),
        (S.from_username_series, None), (S.from_pairs, None),
    ])
    def test_empty_inputs(self, adapter, raw):
        assert adapter(raw) == []

    def test_missing_values_becomes_empty_list(self):
        assert S.from_label_series([{'label': 'x'}])[0].values == []


class TestLinkability:
    """The invariant: an artist is linked iff `link_key` is not None.

    This replaced eight scattered `label == 'Others'` comparisons and three
    `is None` checks across four charts.
    """

    def test_named_bands_are_linkable(self):
        assert S.from_label_series([{'label': 'alice', 'values': []}])[0].is_linkable

    @pytest.mark.parametrize('adapter,raw', [
        (S.from_label_series, [{'label': 'Others', 'values': []}]),
        (S.from_username_series, [{'username': 'Others', 'values': []}]),
        (S.from_pairs, [('Others', [])]),
    ])
    def test_others_is_inert_in_every_envelope(self, adapter, raw):
        band = adapter(raw)[0]
        assert not band.is_linkable
        assert band.link_key is None

    @pytest.mark.parametrize('label', [None, ''])
    def test_unnamed_bands_are_inert(self, label):
        assert not S.from_label_series([{'label': label, 'values': []}])[0].is_linkable

    def test_others_is_case_sensitive(self):
        """Producers emit the literal 'Others'. A lenient match would make a
        user actually called 'others' silently unclickable."""
        assert S.from_label_series([{'label': 'others', 'values': []}])[0].is_linkable


class TestAssignColors:
    def _bands(self, *labels):
        return S.from_label_series([{'label': l, 'values': []} for l in labels])

    def test_others_takes_the_neutral_colour(self):
        colors = S.assign_colors(self._bands('Others', 'a'), UNITY_STACK_10,
                                 UNITY_NCAR_GRAY_LIGHT)
        assert colors[0] == UNITY_NCAR_GRAY_LIGHT

    def test_others_does_not_advance_the_cursor(self):
        """A named band keeps its color whether or not a remainder exists —
        otherwise the same user changes color when the tail happens to be
        empty."""
        with_other = S.assign_colors(self._bands('Others', 'a', 'b'),
                                     UNITY_STACK_10, UNITY_NCAR_GRAY_LIGHT)
        without = S.assign_colors(self._bands('a', 'b'),
                                  UNITY_STACK_10, UNITY_NCAR_GRAY_LIGHT)
        assert with_other[1:] == without

    def test_forward_walk(self):
        colors = S.assign_colors(self._bands('a', 'b', 'c'), UNITY_STACK_10,
                                 UNITY_NCAR_GRAY_LIGHT)
        assert colors == list(UNITY_STACK_10[:3])

    def test_reverse_walk_gives_the_highest_rank_the_warmest_colour(self):
        """The user/proj stacked area's deliberate inversion.

        Its series arrive [Others, lowest-rank, …, highest-rank]. Walking the
        palette forward would hand the LOWEST-rank band gold — backwards from
        the pace chart, where the biggest band is gold.
        """
        colors = S.assign_colors(self._bands('Others', 'low', 'mid', 'high'),
                                 UNITY_STACK_20, UNITY_NCAR_GRAY_LIGHT,
                                 reverse=True)
        assert colors[0] == UNITY_NCAR_GRAY_LIGHT
        assert colors[-1] == UNITY_STACK_20[0]     # highest rank -> gold
        assert colors[1] == UNITY_STACK_20[2]      # lowest rank -> coolest

    def test_reverse_is_opt_in(self):
        fwd = S.assign_colors(self._bands('a', 'b'), UNITY_STACK_20, '#000')
        rev = S.assign_colors(self._bands('a', 'b'), UNITY_STACK_20, '#000',
                              reverse=True)
        assert fwd != rev

    def test_palette_wraps(self):
        bands = self._bands(*[f'u{i}' for i in range(13)])
        colors = S.assign_colors(bands, UNITY_STACK_10, '#000')
        assert colors[10] == UNITY_STACK_10[0]

    def test_no_bands(self):
        assert S.assign_colors([], UNITY_STACK_10, '#000') == []


def test_series_module_imports_no_matplotlib():
    """See the note in links.py — this seam is enforced, not aspirational."""
    tree = ast.parse(Path(S.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
    assert 'matplotlib' not in imported
    assert 'numpy' not in imported
