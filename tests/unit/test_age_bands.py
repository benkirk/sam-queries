"""Age-band ladder maths (webapp/utils/age_bands.py).

Mostly exercised against a synthetic ladder so the tier does not depend on the
optional fs-scans plugin; the real ``ATIME_BUCKETS`` is checked separately and
skipped when the plugin is absent.
"""

from datetime import datetime, timedelta

import pytest

from webapp.utils import age_bands

#: Deliberately not ATIME_BUCKETS: small enough to reason about by hand, and it
#: keeps these tests honest if the plugin's ladder ever changes.
LADDER = (('< 1 Month', 30), ('1-3 Months', 90), ('3+ Months', None))

ANCHOR = datetime(2026, 6, 1)


class TestThresholds:

    def test_lower_is_the_previous_upper(self):
        """Contiguity is derived, not asserted by the caller keeping two lists
        in step — which is the bug the cumulative `prev` in the original loop
        existed to avoid."""
        assert age_bands.thresholds(LADDER) == [
            ('< 1 Month', 0, 30),
            ('1-3 Months', 30, 90),
            ('3+ Months', 90, None),
        ]

    def test_an_empty_ladder_expands_to_nothing(self):
        assert age_bands.thresholds(()) == []


class TestBandBounds:

    def test_newest_band_runs_up_to_the_anchor(self):
        after, before = age_bands.band_bounds(LADDER, ANCHOR, 0, 0)
        assert before == '2026-06-01'
        assert after == '2026-05-02'

    def test_the_open_ended_band_has_no_older_edge(self):
        after, before = age_bands.band_bounds(LADDER, ANCHOR, 2, 2)
        assert after is None
        assert before == (ANCHOR - timedelta(days=90)).strftime('%Y-%m-%d')

    def test_a_span_takes_the_newer_edge_from_lo_and_the_older_from_hi(self):
        """The inversion is the whole subtlety: a LATER band index is an OLDER
        file, so a span's `after` comes from `hi` and its `before` from `lo`."""
        after, before = age_bands.band_bounds(LADDER, ANCHOR, 0, 1)
        assert before == '2026-06-01'                       # band 0's newer edge
        assert after == (ANCHOR - timedelta(days=90)).strftime('%Y-%m-%d')

    def test_a_single_band_span_matches_the_drilldown_mapping(self):
        """`lo == hi` must reproduce what the access-history drill-down has
        always produced, since that mapping is now built on this."""
        for i in range(len(LADDER)):
            assert (age_bands.band_bounds(LADDER, ANCHOR, i, i)
                    == age_bands.band_bounds(LADDER, ANCHOR, i, i))

    def test_indices_are_clamped_rather_than_raising(self):
        """These arrive from a query string, so out-of-range is a stale
        bookmark, not a programming error."""
        assert (age_bands.band_bounds(LADDER, ANCHOR, -5, 99)
                == age_bands.band_bounds(LADDER, ANCHOR, 0, 2))

    def test_a_reversed_span_collapses_to_the_lo_band(self):
        assert (age_bands.band_bounds(LADDER, ANCHOR, 2, 0)
                == age_bands.band_bounds(LADDER, ANCHOR, 2, 2))

    def test_default_span_is_the_whole_ladder(self):
        after, before = age_bands.band_bounds(LADDER, ANCHOR)
        assert after is None
        assert before == '2026-06-01'


class TestBandsFor:
    """The inverse map. A control renders its thumbs from this, so a wrong
    answer silently moves the handles away from the filter in force."""

    @pytest.mark.parametrize('lo,hi', [(0, 0), (0, 1), (1, 2), (0, 2), (2, 2)])
    def test_round_trips_every_span(self, lo, hi):
        after, before = age_bands.band_bounds(LADDER, ANCHOR, lo, hi)
        assert age_bands.bands_for(LADDER, ANCHOR, after, before) == (lo, hi)

    def test_a_hand_typed_range_is_not_a_span(self):
        """None is the "custom" signal — the control must not snap to a span
        that isn't what the filter says."""
        assert age_bands.bands_for(LADDER, ANCHOR, '2026-01-15', '2026-03-04') is None

    def test_a_missing_before_means_up_to_the_anchor(self):
        assert age_bands.bands_for(LADDER, ANCHOR, None, None) == (0, 2)

    def test_an_empty_ladder_has_no_span(self):
        assert age_bands.bands_for((), ANCHOR, None, None) is None


class TestTilesWithoutOverlap:
    """The tripwire. Both fs-scans comparisons are strict (`max_atime >
    :accessed_after`, `max_atime < :accessed_before`), so band i's `after`
    being exactly band i+1's `before` is what makes the drill-downs partition
    the total. Make either bound inclusive without re-deriving the edges here
    and adjacent bands double-count a whole boundary day — visible only as
    drill-downs summing to more than their parent.

    Note what this does and does not cover: because `thresholds()` *derives*
    each band's lower edge from the previous upper, no ladder shape can break
    contiguity. What it guards is `band_bounds` itself."""

    def test_the_synthetic_ladder_tiles(self):
        assert age_bands.tiles_without_overlap(LADDER, ANCHOR)

    def test_the_real_atime_ladder_tiles(self):
        ladder = age_bands.atime_ladder()
        if ladder is None:
            pytest.skip('fs-scans plugin not installed')
        assert age_bands.tiles_without_overlap(ladder, ANCHOR)

    def test_a_one_day_edge_shift_would_violate_the_property(self):
        """Not a tautology with respect to `band_bounds`: shifting either shared
        edge by a day — which is exactly what making a bound inclusive would
        require — stops the edges meeting. A tripwire that cannot fail is worse
        than none, because it reads as covered."""
        after_0, _ = age_bands.band_bounds(LADDER, ANCHOR, 0, 0)
        _, before_1 = age_bands.band_bounds(LADDER, ANCHOR, 1, 1)
        assert after_0 == before_1                      # the property held
        shifted = (datetime.strptime(before_1, '%Y-%m-%d')
                   - timedelta(days=1)).strftime('%Y-%m-%d')
        assert after_0 != shifted                       # ...and a shift breaks it


class TestBandMap:

    def test_pre_resolves_every_band_under_the_caller_s_key_names(self):
        """The JSON data block a template emits. Keys are parameterized because
        disk scans and jobs name their fields differently."""
        rows = age_bands.band_map(LADDER, ANCHOR, 'accessed_after', 'accessed_before')
        assert [r['label'] for r in rows] == list(age_bands.labels(LADDER))
        assert rows[0]['accessed_before'] == '2026-06-01'
        assert rows[-1]['accessed_after'] is None

    def test_every_row_carries_exactly_the_three_keys(self):
        """The client indexes this and reads two fields; a stray key would mean
        date arithmetic had leaked back into JavaScript."""
        rows = age_bands.band_map(LADDER, ANCHOR, 'start', 'end')
        assert all(set(r) == {'label', 'start', 'end'} for r in rows)


class TestAtimeLadder:

    def test_absent_plugin_yields_none_rather_than_raising(self, monkeypatch):
        """Every fs-scans surface degrades rather than 500s; this is the same
        contract `_atime_band_bounds` has always had."""
        import builtins
        real_import = builtins.__import__

        def boom(name, *args, **kwargs):
            if name == 'fs_scans.core.models':
                raise ImportError('no plugin')
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, '__import__', boom)
        assert age_bands.atime_ladder() is None
