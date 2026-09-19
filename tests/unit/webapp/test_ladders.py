"""Value-ladder maths — the numeric counterpart to test_age_bands.py.

Pure functions over constants, so nothing here needs a session or an app.
"""

import pytest

from webapp.utils import ladders

# A miniature half-open ladder in the shape of fs-scans' SIZE_BUCKETS, floor
# at 0 and an open top. Small enough to enumerate every span by hand.
LADDER = (
    ('0 - 1 KiB', 0, 1024),
    ('1 - 10 KiB', 1024, 10240),
    ('10 - 100 KiB', 10240, 102400),
    ('100 KiB+', 102400, None),
)


class TestLabels:
    def test_labels_are_the_display_vocabulary(self):
        assert ladders.labels(LADDER) == [
            '0 - 1 KiB', '1 - 10 KiB', '10 - 100 KiB', '100 KiB+']

    def test_an_empty_ladder_has_no_labels(self):
        assert ladders.labels(()) == []


class TestSpanBounds:
    def test_a_single_band_is_its_own_edges(self):
        assert ladders.span_bounds(LADDER, 1, 1) == (1024, 10240)

    def test_a_span_takes_the_outer_edges(self):
        assert ladders.span_bounds(LADDER, 1, 2) == (1024, 102400)

    def test_reaching_the_top_band_drops_the_upper_bound(self):
        # None means "no upper bound", not "zero" — callers omit it from the
        # query string rather than submitting it.
        assert ladders.span_bounds(LADDER, 2, 3) == (10240, None)

    def test_the_default_span_is_the_whole_ladder(self):
        assert ladders.span_bounds(LADDER) == (0, None)

    def test_the_floor_is_zero_not_absent(self):
        # The distinction the whole module turns on: band 0's lower edge is a
        # real 0, and a caller that treats it as falsy submits no bound at all.
        lo, _hi = ladders.span_bounds(LADDER, 0, 0)
        assert lo == 0 and lo is not None

    @pytest.mark.parametrize('lo, hi', [(-5, 99), (99, -5), (2, 0)])
    def test_out_of_range_indices_clamp_rather_than_raise(self, lo, hi):
        # These arrive from a range input a viewer can hand-edit; the house
        # rule for viewer-editable input is degrade, never 400.
        assert ladders.span_bounds(LADDER, lo, hi) is not None

    def test_an_empty_ladder_yields_no_bounds(self):
        assert ladders.span_bounds(()) == (None, None)


class TestSpanFor:
    @pytest.mark.parametrize('lo', range(len(LADDER)))
    @pytest.mark.parametrize('hi', range(len(LADDER)))
    def test_every_span_round_trips(self, lo, hi):
        if lo > hi:
            pytest.skip('not a span')
        lo_v, hi_v = ladders.span_bounds(LADDER, lo, hi)
        assert ladders.span_for(LADDER, lo_v, hi_v) == (lo, hi)

    def test_a_hand_typed_range_is_custom(self):
        # 5000 is inside band 1, not an edge of anything.
        assert ladders.span_for(LADDER, 5000, 102400) is None

    def test_one_typed_edge_is_enough_to_be_custom(self):
        assert ladders.span_for(LADDER, 1024, 99999) is None

    def test_absent_bounds_mean_the_whole_ladder(self):
        assert ladders.span_for(LADDER, None, None) == (0, len(LADDER) - 1)

    def test_absent_upper_selects_the_open_band(self):
        assert ladders.span_for(LADDER, 10240, None) == (2, 3)

    def test_zero_is_an_edge_not_an_absent_bound(self):
        # Both spellings land on band 0 here, but only because band 0's lo IS
        # 0. Pinning it because `if not lo_value` would conflate them, and on
        # a ladder with a non-zero floor that would be wrong.
        assert ladders.span_for(LADDER, 0, 1024) == (0, 0)
        assert ladders.span_for(LADDER, None, 1024) == (0, 0)

    def test_an_inverted_range_is_custom(self):
        assert ladders.span_for(LADDER, 102400, 1024) is None

    def test_an_empty_ladder_has_no_span(self):
        assert ladders.span_for((), None, None) is None


class TestBandMap:
    def test_rows_are_keyed_by_the_callers_field_names(self):
        # The handler in actions.js indexes each band by the field's own
        # `name`, which is what lets one handler serve every vocabulary.
        rows = ladders.band_map(LADDER, 'min_avg_size', 'max_avg_size')
        assert rows[0] == {'label': '0 - 1 KiB',
                           'min_avg_size': 0, 'max_avg_size': 1024}

    def test_the_open_band_carries_a_null_upper(self):
        rows = ladders.band_map(LADDER, 'lo', 'hi')
        assert rows[-1]['hi'] is None

    def test_order_is_ladder_order(self):
        rows = ladders.band_map(LADDER, 'lo', 'hi')
        assert [r['label'] for r in rows] == list(ladders.labels(LADDER))


class TestPluginAccessors:
    def test_size_ladder_matches_the_plugin_constant(self):
        ladder = ladders.size_ladder()
        if ladder is None:
            pytest.skip('fs_scans plugin not installed')
        from fs_scans.core.models import SIZE_BUCKETS
        assert list(ladder) == list(SIZE_BUCKETS)

    def test_size_ladder_is_a_well_formed_ladder(self):
        ladder = ladders.size_ladder()
        if ladder is None:
            pytest.skip('fs_scans plugin not installed')
        assert ladder[-1][2] is None, 'top band must be open-ended'
        assert ladders.span_for(ladder, *ladders.span_bounds(ladder)) == \
            (0, len(ladder) - 1)

    def test_machine_ladder_degrades_on_an_unknown_dimension(self):
        # ValueError from the plugin must not reach the panel: every caller
        # falls back to a bare min/max pair on None.
        assert ladders.machine_ladder('derecho', 'not_a_dimension') is None

    def test_machine_ladder_is_right_sized_per_machine(self):
        casper = ladders.machine_ladder('casper', 'nodes')
        derecho = ladders.machine_ladder('derecho', 'nodes')
        if casper is None or derecho is None:
            pytest.skip('job_history histogram_buckets not available')
        # The reason the ladders are plugin-supplied rather than hardcoded.
        assert len(casper) < len(derecho)
