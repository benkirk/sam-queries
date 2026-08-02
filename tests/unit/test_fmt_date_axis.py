"""The chart date-axis vocabulary in ``sam.fmt``.

`date_str` stays ISO — a table column wants `2026-07-26`, sortable and
unambiguous. A chart axis wants the opposite, and this is that opposite:
the tick carries what changes, a second line carries the context, and the
context is drawn only where it changes.

These tests pin the vocabulary itself. `test_chart_fingerprints` pins what
each chart does with it; this pins what "it" is, at every span the app
actually renders — 6 hours (`?hours=6` on the status dashboard) through
several years (a long allocation history).
"""

from datetime import datetime, timedelta

import pytest

from sam import fmt

pytestmark = pytest.mark.unit


def _ticks(span, n, start=datetime(2026, 7, 26)):
    return fmt._label_ticks([start + span / n * i for i in range(n + 1)])


def _lines(label):
    return label.split('\n')


class TestVocabulary:
    """One case per band, at a span the app really renders."""

    def test_hours_show_clock_times(self):
        # `?hours=6` on the status dashboard. Before this existed every tick
        # read `07-26 00`, `07-26 01`, ... — the date on all seven.
        got = _ticks(timedelta(hours=6), 6)
        assert _lines(got[0]) == ['00:00', 'Jul 26']
        assert got[1:] == ['01:00', '02:00', '03:00', '04:00', '05:00', '06:00']

    def test_days_show_month_and_day(self):
        got = _ticks(timedelta(days=7), 7)
        assert _lines(got[0]) == ['Jul 26', '2026']
        assert got[1:4] == ['Jul 27', 'Jul 28', 'Jul 29']

    def test_months_show_month_name(self):
        got = _ticks(timedelta(days=365), 6)
        assert _lines(got[0]) == ['Jul', '2026']
        assert got[1] == 'Sep'

    def test_multi_year_shows_years(self):
        got = _ticks(timedelta(days=365 * 8), 8)
        assert all('\n' not in g for g in got), 'years need no context line'
        assert got[0] == '2026' and got[-1] == '2034'


class TestContextAppearsOnlyWhenItChanges:
    """The whole point: the repeated part is drawn once."""

    def test_year_appears_once_within_one_year(self):
        got = _ticks(timedelta(days=30), 6)
        assert sum('2026' in g for g in got) == 1
        assert _lines(got[0])[1] == '2026'

    def test_year_reappears_at_the_boundary(self):
        # The pace chart is always a window centred on today, so it crosses a
        # year boundary for half of every year.
        got = fmt._label_ticks(
            [datetime(2026, 10, 1) + timedelta(days=61 * i) for i in range(6)])
        years = [i for i, g in enumerate(got) if '\n' in g]
        assert len(years) == 2, f'expected first tick + Jan, got {got}'
        assert _lines(got[years[0]])[1] == '2026'
        assert _lines(got[years[1]])[1] == '2027'

    def test_date_reappears_at_midnight_on_an_hours_axis(self):
        got = _ticks(timedelta(hours=36), 12)
        dated = [g for g in got if '\n' in g]
        assert len(dated) == 2, f'expected first tick + next midnight: {got}'
        assert _lines(dated[1])[1] == 'Jul 27'

    def test_first_tick_always_carries_context(self):
        """An axis is never left without its date, whatever the span."""
        for span in (timedelta(hours=6), timedelta(days=7),
                     timedelta(days=90), timedelta(days=365)):
            got = _ticks(span, 6)
            assert '\n' in got[0], f'{span} left the first tick bare: {got}'


class TestBandSelection:
    """The band comes from the ACTUAL tick spacing, not the data's span.

    They usually agree, but the locator has the last word on where ticks land
    — and a formatter that guessed from the span would mislabel whenever they
    diverged.
    """

    def test_single_tick_falls_back_to_day_grain(self):
        got = fmt._label_ticks([datetime(2026, 7, 26)])
        assert _lines(got[0]) == ['Jul 26', '2026']

    def test_empty_is_empty(self):
        assert fmt._label_ticks([]) == []

    def test_month_spaced_ticks_get_month_grain_regardless_of_count(self):
        got = fmt._label_ticks(
            [datetime(2026, 1, 1) + timedelta(days=31 * i) for i in range(4)])
        assert got[1] == 'Feb'


class TestMplDateTicks:

    def test_returns_a_locator_and_formatter(self):
        from matplotlib.dates import AutoDateLocator
        from matplotlib.ticker import Formatter
        loc, fmtr = fmt.mpl_date_ticks(max_ticks=5)
        assert isinstance(loc, AutoDateLocator)
        assert isinstance(fmtr, Formatter)

    def test_max_ticks_reaches_the_locator(self):
        assert (fmt.mpl_date_ticks(max_ticks=5)[0].maxticks
                != fmt.mpl_date_ticks(max_ticks=12)[0].maxticks)

    def test_formatter_labels_a_whole_row(self):
        from matplotlib.dates import date2num
        _loc, fmtr = fmt.mpl_date_ticks()
        vals = [date2num(datetime(2026, 7, 26) + timedelta(days=i))
                for i in range(4)]
        assert fmtr.format_ticks(vals) == ['Jul 26\n2026', 'Jul 27',
                                           'Jul 28', 'Jul 29']

    def test_single_value_call_is_self_describing(self):
        """The `__call__` path has no neighbours, so nothing may be
        suppressed — it feeds cursor readouts, not the axis."""
        from matplotlib.dates import date2num
        _loc, fmtr = fmt.mpl_date_ticks()
        assert fmtr(date2num(datetime(2026, 7, 26, 14, 30))) == '2026-07-26 14:30'

    def test_module_stays_matplotlib_free_at_import(self):
        """`sam.fmt` is imported by every CLI invocation; pulling matplotlib
        in at module scope would tax `sam-search` for a webapp feature."""
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(fmt))
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = [a.name for n in top if isinstance(n, ast.Import) for a in n.names]
        names += [n.module or '' for n in top if isinstance(n, ast.ImportFrom)]
        assert not [n for n in names if 'matplotlib' in n], names


class TestCompactDateLabels:
    """For charts whose x axis is categorical — the jobs timeline groups
    server-side, so it plots band indices against period strings."""

    def test_day_grain_matches_the_date_axis(self):
        assert fmt.compact_date_labels(
            ['2026-07-26', '2026-07-27', '2026-07-28']
        ) == ['Jul 26\n2026', 'Jul 27', 'Jul 28']

    def test_month_grain(self):
        assert fmt.compact_date_labels(
            ['2026-11', '2026-12', '2027-01']
        ) == ['Nov\n2026', 'Dec', 'Jan\n2027']

    def test_year_grain(self):
        assert fmt.compact_date_labels(['2025', '2026']) == ['2025', '2026']

    def test_unparsable_grain_degrades_to_the_raw_strings(self):
        """Week and quarter grains, or anything the plugin someday spells
        differently. A half-converted axis is worse than a consistent ISO
        one — and a chart must not raise on a label it does not recognise."""
        raw = ['2026-W12', '2026-W13']
        assert fmt.compact_date_labels(raw) == raw

    def test_one_bad_label_leaves_all_of_them_alone(self):
        raw = ['2026-07-26', 'unknown', '2026-07-28']
        assert fmt.compact_date_labels(raw) == raw

    def test_empty(self):
        assert fmt.compact_date_labels([]) == []


class TestParsePeriodLabel:

    @pytest.mark.parametrize('raw,expected', [
        ('2026-07-26', datetime(2026, 7, 26)),
        ('2026-07', datetime(2026, 7, 1)),
        ('2026', datetime(2026, 1, 1)),
    ])
    def test_grains(self, raw, expected):
        assert fmt.parse_period_label(raw) == expected

    @pytest.mark.parametrize('raw', ['2026-W12', '', 'nope', None, '2026-13-01'])
    def test_rejects_the_rest(self, raw):
        assert fmt.parse_period_label(raw) is None


def test_date_str_is_untouched():
    """This pass is a charting change. Tables keep ISO — sortable, and the
    convention every export and API response already uses."""
    assert fmt.date_str(datetime(2026, 7, 26)) == '2026-07-26'
    assert fmt.date_str(datetime(2026, 7, 26), fmt='%b %Y') == 'Jul 2026'
