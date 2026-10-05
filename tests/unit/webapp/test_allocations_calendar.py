"""Allocations dashboard: the calendar view's geometry and its two htmx fragments."""

from datetime import datetime
from unittest.mock import patch

import pytest

from webapp.dashboards.allocations import blueprint
from webapp.dashboards.allocations.burn import (
    burn_class, burn_key, burn_through, pace_segments, project_ratios, recent_rate, runs_out,
)
from webapp.dashboards.allocations.calendar import (
    burn_cells, calendar_group_burn, calendar_groups, calendar_months, calendar_rows, calendar_window,
    group_burn,
)

AT = datetime(2026, 10, 3)
THROUGH = datetime(2026, 10, 4)   # the end of the AT day


def _row(projcode, start, end, amount=100.0, used=0.0, facility='UNIV', alloc_type='Small',
         allocation_id=None):
    return {'projcode': projcode, 'resource': 'Derecho', 'facility': facility,
            'allocation_type': alloc_type, 'start_date': start, 'end_date': end,
            'total_amount': amount, 'total_used': used, 'allocation_id': allocation_id}


class TestGeometry:
    def test_window_is_whole_months_around_the_date(self):
        assert calendar_window(AT) == (datetime(2025, 10, 1), datetime(2027, 11, 1))
        assert calendar_window(datetime(2026, 1, 15), past=1, future=0) == (
            datetime(2025, 12, 1), datetime(2026, 2, 1))

    def test_month_widths_follow_days_and_cover_the_window(self):
        start, end = calendar_window(AT)
        months = calendar_months(start, end)
        assert len(months) == 25
        assert sum(m['width'] for m in months) == pytest.approx(100.0)
        feb, mar = months[4], months[5]
        assert (feb['label'], mar['label']) == ('Feb', 'Mar')
        assert feb['width'] / mar['width'] == pytest.approx(28 / 31)
        assert [m['year'] for m in months if m['year']] == [2025, 2026, 2027]

    def test_bars_clip_to_the_window_and_carry_state(self):
        start, end = calendar_window(AT)
        rows = [_row('A', datetime(2024, 10, 1), datetime(2025, 12, 31), used=80.0),
                _row('A', datetime(2026, 1, 1), datetime(2026, 12, 31), used=50.0),
                _row('A', datetime(2027, 1, 1), datetime(2027, 12, 31)),
                _row('B', datetime(2020, 1, 1), datetime(2021, 1, 1))]   # outside: dropped
        (a,) = calendar_rows(rows, start, end, AT)
        past, current, future = a['bars']
        assert [b['state'] for b in a['bars']] == ['past', 'current', 'future']
        assert past['left'] == 0.0 and past['clip_left'] and not past['clip_right']
        assert future['clip_right'] and future['left'] + future['width'] == pytest.approx(100.0)
        assert future['fill'] == 0.0
        assert a['lanes'] == 1

    def test_fill_runs_along_the_whole_allocation(self):
        start, end = calendar_window(AT)
        (a,) = calendar_rows([_row('A', datetime(2026, 1, 1), datetime(2027, 1, 1), used=50.0)],
                             start, end, AT)
        assert a['bars'][0]['fill'] == pytest.approx(50.0)
        # 40% of a 30-month allocation reaches 2024-10, before the window opens,
        # so none of the visible bar is filled.
        (b,) = calendar_rows([_row('B', datetime(2023, 10, 1), datetime(2026, 4, 1), used=40.0)],
                             start, end, AT)
        assert b['bars'][0]['fill'] == 0.0 and b['bars'][0]['pct_used'] == pytest.approx(40.0)

    def test_over_use_and_open_ended(self):
        start, end = calendar_window(AT)
        rows = [_row('A', datetime(2026, 1, 1), datetime(2026, 12, 31), used=130.0),
                _row('B', datetime(2026, 1, 1), None, used=10.0)]
        a, b = calendar_rows(rows, start, end, AT)
        assert a['bars'][0]['over'] and a['bars'][0]['fill'] == pytest.approx(100.0)
        assert b['bars'][0]['clip_right'] and b['bars'][0]['state'] == 'current'

    def test_overlapping_allocations_stack_into_lanes(self):
        start, end = calendar_window(AT)
        rows = [_row('A', datetime(2026, 1, 1), datetime(2026, 12, 31)),
                _row('A', datetime(2026, 6, 1), datetime(2027, 5, 31))]
        (a,) = calendar_rows(rows, start, end, AT)
        assert a['lanes'] == 2 and sorted(b['lane'] for b in a['bars']) == [0, 1]

    def test_groups_follow_facility_order(self):
        rows = [_row('A', AT, AT, facility='UNIV', alloc_type='Small'),
                _row('A', AT, AT, facility='UNIV', alloc_type='Small'),
                _row('B', AT, AT, facility='UNIV', alloc_type='Large'),
                _row('C', AT, AT, facility='NCAR', alloc_type='NSC')]
        assert calendar_groups(rows, ['NCAR', 'UNIV']) == [('NCAR', ['NSC']), ('UNIV', ['Large', 'Small'])]


_ROWS = [_row('UNIV0001', datetime(2026, 1, 1), datetime(2026, 12, 31), used=40.0, allocation_id=1),
         _row('UNIV0002', datetime(2025, 1, 1), datetime(2025, 12, 31), used=90.0, alloc_type='Large',
              allocation_id=2),
         _row('NCAR0001', datetime(2026, 4, 1), datetime(2027, 3, 31), facility='NCAR', alloc_type='NSC',
              allocation_id=3)]


class TestBurn:
    # 365 days: Jan 2026 through Dec 2026, so a 31-day month's even share is 31/365 of it.
    YEAR = (datetime(2026, 1, 1), datetime(2027, 1, 1))

    def test_classes_split_at_the_edges(self):
        assert [burn_class(r) for r in (0, 0.24, 0.25, 0.74, 0.75, 1.24, 1.25, 1.99, 2, 9)] == \
            [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]

    def test_cells_follow_months_and_stop_at_the_as_of_day(self):
        start, end = calendar_window(AT)
        row = _row('A', *self.YEAR, amount=365.0, allocation_id=7)
        cells = burn_cells(row, {202601: 31.0, 202602: 56.0, 202610: 3.0}, start, end, THROUGH)
        assert len(cells) == 10                       # Jan..Oct, nothing after Oct 3
        jan, feb = cells[0], cells[1]
        assert jan['ratio'] == pytest.approx(1.0) and jan['cls'] == 2
        assert feb['ratio'] == pytest.approx(2.0) and feb['cls'] == 4   # 56 over a 28-day share
        assert cells[2]['charges'] == 0.0 and cells[2]['cls'] == 0
        assert jan['left'] == 0.0 and feb['left'] == pytest.approx(31 / 365 * 100)
        oct_ = cells[-1]                              # Oct 1 to the end of Oct 3: ~3 days
        assert oct_['ratio'] == pytest.approx(1.0)
        assert oct_['width'] == pytest.approx(3 / 365 * 100)

    def test_cells_are_in_percent_of_the_clipped_bar(self):
        start, end = calendar_window(AT)               # opens 2025-10-01
        row = _row('A', datetime(2025, 7, 1), datetime(2026, 7, 1), amount=365.0, allocation_id=1)
        cells = burn_cells(row, {202510: 31.0}, start, end, THROUGH)
        assert cells[0]['month'] == datetime(2025, 10, 1) and cells[0]['left'] == 0.0
        assert sum(c['width'] for c in cells) == pytest.approx(100.0)
        assert cells[0]['ratio'] == pytest.approx(1.0)

    def test_no_cells_without_an_even_pace(self):
        start, end = calendar_window(AT)
        for row in (_row('A', datetime(2026, 1, 1), None, allocation_id=1),
                    _row('A', *self.YEAR, amount=0.0, allocation_id=1),
                    _row('A', *self.YEAR)):           # no allocation_id: a stale cached row
            assert burn_cells(row, {}, start, end, THROUGH) is None
        future = _row('A', datetime(2026, 11, 1), datetime(2027, 11, 1), allocation_id=1)
        assert burn_cells(future, {}, start, end, THROUGH) == []

    def test_rows_carry_burn_only_when_asked(self):
        start, end = calendar_window(AT)
        rows = [_row('A', *self.YEAR, amount=365.0, allocation_id=7)]
        assert 'burn' not in calendar_rows(rows, start, end, AT)[0]['bars'][0]
        (a,) = calendar_rows(rows, start, end, AT, burns={7: {202601: 31.0}}, through=THROUGH)
        assert a['bars'][0]['burn'][0]['cls'] == 2

    def test_group_burn_sums_charges_over_shares(self):
        start, end = calendar_window(AT)
        rows = [_row('A', *self.YEAR, amount=365.0, allocation_id=1),
                _row('B', *self.YEAR, amount=730.0, allocation_id=2),
                _row('C', datetime(2026, 1, 1), None, allocation_id=3)]   # open-ended: ignored
        cells = group_burn(rows, {1: {202603: 31.0}, 2: {202603: 31.0}, 3: {202603: 999.0}},
                           start, end, THROUGH)
        mar = next(c for c in cells if c['month'] == datetime(2026, 3, 1))
        assert mar['charges'] == 62.0 and mar['ratio'] == pytest.approx(62 / 93)
        assert cells[0]['month'] == datetime(2026, 1, 1)
        assert cells[-1]['month'] == datetime(2026, 10, 1)
        assert mar['left'] == pytest.approx((datetime(2026, 3, 1) - start) / (end - start) * 100)

    def test_group_burn_per_facility_and_type(self):
        start, end = calendar_window(AT)
        rows = [_row('A', *self.YEAR, amount=365.0, allocation_id=1),
                _row('B', *self.YEAR, amount=365.0, alloc_type='Large', allocation_id=2)]
        strips = calendar_group_burn(rows, calendar_groups(rows, ['UNIV']), {1: {202601: 31.0}},
                                     start, end, THROUGH)
        assert set(strips) == {'UNIV', ('UNIV', 'Small'), ('UNIV', 'Large')}
        assert strips[('UNIV', 'Small')][0]['ratio'] == pytest.approx(1.0)
        assert strips['UNIV'][0]['ratio'] == pytest.approx(0.5)

    def test_group_burn_ignores_a_start_later_in_the_as_of_month(self):
        start, end = calendar_window(AT)
        rows = [_row('A', *self.YEAR, amount=365.0, allocation_id=1)]
        later = _row('B', datetime(2026, 10, 20), datetime(2027, 10, 20), amount=36500.0, allocation_id=2)
        burns = {1: {202610: 3.0}}
        alone = group_burn(rows, burns, start, end, THROUGH)
        assert alone[-1]['month'] == datetime(2026, 10, 1) and alone[-1]['ratio'] == pytest.approx(1.0)
        assert group_burn(rows + [later], burns, start, end, THROUGH) == alone

    def test_shading_stops_at_todays_midnight(self):
        assert burn_through(AT, today=datetime(2026, 12, 1)) == THROUGH
        # As of today: today's charges land tomorrow, so its share would read low.
        assert burn_through(AT, today=AT) == AT
        assert burn_through(datetime(2027, 1, 1), today=AT) == AT

    def test_key_labels_follow_the_edges(self):
        assert [label for _, label in burn_key()] == [
            '<0.25×', '0.25–0.75×', '0.75–1.25×', '1.25–2×', '≥2×']



class TestRunOut:
    # 365 days; from Oct 1 the 90-day look-back opens Jul 3, so July counts 29 of its 31 days.
    YEAR = (datetime(2026, 1, 1), datetime(2027, 1, 1))
    OCT = datetime(2026, 10, 1)
    TWO_A_DAY = {202607: 62.0, 202608: 62.0, 202609: 60.0}

    def _row(self, used, **kw):
        return _row('A', *kw.pop('span', self.YEAR), amount=365.0, used=used, allocation_id=7, **kw)

    def test_recent_rate_counts_a_partial_month_pro_rata(self):
        assert recent_rate(self._row(0), self.TWO_A_DAY, self.OCT) == pytest.approx(2.0)
        assert recent_rate(self._row(0), {202607: 31.0}, self.OCT) == pytest.approx(29 / 90)

    def test_recent_rate_counts_the_current_month_through_the_day(self):
        assert recent_rate(self._row(0), {202610: 28.0}, datetime(2026, 10, 15)) == pytest.approx(28 / 90)

    def test_recent_rate_starts_at_the_start_date(self):
        row = self._row(0, span=(datetime(2026, 9, 1), datetime(2027, 9, 1)))
        assert recent_rate(row, {202609: 60.0}, self.OCT) == pytest.approx(2.0)
        assert recent_rate(row, {}, datetime(2026, 9, 1)) == 0.0

    def test_date_is_the_balance_at_the_recent_rate(self):
        # 120 left at 2/day: 60 days, Nov 30, which is 32 days before the end.
        assert runs_out(self._row(245.0), self.TWO_A_DAY, self.OCT) == datetime(2026, 11, 30)

    def test_none_within_the_margin_or_without_a_rate_or_balance(self):
        assert runs_out(self._row(225.0), self.TWO_A_DAY, self.OCT) is None   # Dec 10: 22 days left
        assert runs_out(self._row(245.0), {}, self.OCT) is None
        assert runs_out(self._row(365.0), self.TWO_A_DAY, self.OCT) is None
        assert runs_out(self._row(400.0), self.TWO_A_DAY, self.OCT) is None
        assert runs_out(self._row(0.0), {202609: 1e-9}, self.OCT) is None      # past datetime.max

    def test_none_unless_current_and_burnable(self):
        assert runs_out(self._row(245.0, span=(datetime(2026, 1, 1), None)), self.TWO_A_DAY, self.OCT) is None
        assert runs_out(self._row(245.0, span=(datetime(2025, 1, 1), datetime(2026, 9, 1))),
                        self.TWO_A_DAY, self.OCT) is None
        assert runs_out(self._row(0.0, span=(datetime(2026, 11, 1), datetime(2027, 11, 1))),
                        self.TWO_A_DAY, self.OCT) is None

    def test_bar_places_the_mark_in_percent_of_itself(self):
        start, end = calendar_window(AT)
        (p,) = calendar_rows([self._row(245.0)], start, end, AT, burns={7: self.TWO_A_DAY}, through=self.OCT)
        bar = p['bars'][0]
        assert bar['runs_out'] == datetime(2026, 11, 30)
        assert bar['runs_out_left'] == pytest.approx(333 / 365 * 100)
        (p,) = calendar_rows([self._row(225.0)], start, end, AT, burns={7: self.TWO_A_DAY}, through=self.OCT)
        assert 'runs_out' not in p['bars'][0]


class TestPaceSegments:
    OCT = datetime(2026, 10, 1)
    YEAR = (datetime(2026, 1, 1), datetime(2027, 1, 1))     # 365 days, even 1/day per 365
    TWO_A_DAY = {202607: 62.0, 202608: 62.0, 202609: 60.0}  # ratio 2x on an amount of 365

    def _row(self, used=0.0, span=None, projcode='A', allocation_id=7, amount=365.0):
        return _row(projcode, *(span or self.YEAR), amount=amount, used=used, allocation_id=allocation_id)

    def _pace(self, rows, burns):
        return {r['allocation_id']: r['pace'] for r in pace_segments(rows, burns, self.OCT, self.OCT)}

    def test_past_cells_are_charges_over_days_and_stop_at_the_split(self):
        (p,) = self._pace([self._row(184.0)], {7: self.TWO_A_DAY}).values()
        assert p['past'][6] == (datetime(2026, 7, 1), datetime(2026, 8, 1), pytest.approx(2.0))
        assert p['past'][-1][1] == self.OCT and len(p['past']) == 9
        assert p['recent'] == pytest.approx(2.0)

    def test_projection_runs_until_the_balance_is_spent(self):
        (p,) = self._pace([self._row(245.0)], {7: self.TWO_A_DAY}).values()
        assert p['projected'] == [(self.OCT, datetime(2026, 11, 30), pytest.approx(2.0))]
        assert p['committed'] == [(self.OCT, datetime(2027, 1, 1), pytest.approx(120 / 92))]

    def test_a_renewal_inherits_its_projects_pace(self):
        old = self._row(184.0, span=(datetime(2025, 10, 1), self.OCT))
        new = self._row(0.0, span=(self.OCT, datetime(2027, 10, 1)), allocation_id=8, amount=730.0)
        assert project_ratios([old, new], {7: self.TWO_A_DAY}, self.OCT)['A'] == pytest.approx(2.0, rel=0.01)
        p = self._pace([old, new], {7: self.TWO_A_DAY})
        assert p[8]['projected'][0][2] == pytest.approx(4.0, rel=0.01)     # 2x its 2/day even rate
        assert not p[7]['projected'] and not p[7]['committed']

    def test_recent_adds_up_to_the_projects_rate_across_a_renewal(self):
        # 2/day throughout; the old allocation ended Aug 31, inside the look-back.
        old = self._row(365.0, span=(datetime(2025, 9, 1), datetime(2026, 9, 1)))
        new = self._row(60.0, span=(datetime(2026, 9, 1), datetime(2027, 9, 1)), allocation_id=8)
        p = self._pace([old, new], {7: {202607: 62.0, 202608: 62.0}, 8: {202609: 60.0}})
        assert p[7]['recent'] + p[8]['recent'] == pytest.approx(2.0)
        assert p[8]['recent'] == pytest.approx(60 / 90)

    def test_no_history_projects_the_even_rate(self):
        later = self._row(span=(datetime(2026, 10, 20), datetime(2027, 10, 20)))
        (p,) = self._pace([later], {}).values()
        assert p['past'] == [] and p['recent'] == 0.0
        assert p['projected'] == [(datetime(2026, 10, 20), datetime(2027, 10, 20), pytest.approx(1.0))]

    def test_no_projection_without_a_balance(self):
        (p,) = self._pace([self._row(400.0)], {7: self.TWO_A_DAY}).values()
        assert p['projected'] == [] and p['committed'] == []

    def test_disk_rows_use_the_lifetime_average(self):
        (p,) = self._pace([self._row(273.0)], None).values()
        assert p['past'] == [(datetime(2026, 1, 1), self.OCT, pytest.approx(1.0))]
        assert p['projected'][0][2] == pytest.approx(1.0)

    def test_open_ended_rows_are_skipped(self):
        assert pace_segments([self._row(span=(datetime(2026, 1, 1), None))], {}, self.OCT, self.OCT) == []

@pytest.fixture
def captured():
    seen = {}
    with patch.object(blueprint, 'cached_allocation_usage_rows',
                      side_effect=lambda *a, **kw: seen.update(query=kw) or [dict(r) for r in _ROWS]):
        yield seen


def test_skeleton_lists_groups_and_lazy_row_urls(auth_client, captured):
    body = auth_client.get('/allocations/htmx/calendar/Derecho?active_at=2026-10-03').get_data(as_text=True)
    assert (captured['query']['window_start'], captured['query']['window_end']) == (
        datetime(2025, 10, 1), datetime(2027, 11, 1))
    assert captured['query']['as_of'] == AT
    assert body.count('/allocations/htmx/calendar/Derecho/rows?') == 3
    assert body.index('NCAR') < body.index('UNIV')
    assert 'data-cal-focus=' in body and 'class="cal-now"' in body


def test_skeleton_facility_filter_narrows_and_is_forwarded(auth_client, captured):
    body = auth_client.get('/allocations/htmx/calendar/Derecho?facilities=UNIV').get_data(as_text=True)
    assert 'NCAR' not in body
    assert body.count('facilities=UNIV') == 4   # two rows URLs, two Used | Burn pills


def test_rows_render_one_type_group(auth_client, captured):
    body = auth_client.get('/allocations/htmx/calendar/Derecho/rows?facility=UNIV&allocation_type=Small'
                           '&active_at=2026-10-03').get_data(as_text=True)
    assert 'UNIV0001' in body and 'UNIV0002' not in body
    assert 'cal-bar cal-current' in body
    assert 'data-fs="' in body


def test_storage_rows_draw_spans_without_fills(auth_client, captured, monkeypatch):
    monkeypatch.setattr(blueprint, 'get_resource_types', lambda session: {'Derecho': 'DISK'})
    body = auth_client.get('/allocations/htmx/calendar/Derecho/rows?facility=UNIV&allocation_type=Small'
                           '&active_at=2026-10-03').get_data(as_text=True)
    assert '--fill: 0.00%' in body and 'cal-bar-label' not in body
    assert '100 allocated' in body


@pytest.fixture
def burn():
    """Burn queries captured, with "today" pinned well after AT so AT reads as a past day."""
    seen = {}
    with patch.object(blueprint, 'cached_allocation_burn',
                      side_effect=lambda *a, **kw: seen.update(query=kw) or {1: {202603: 50.0}}), \
            patch.object(blueprint, '_burn_through', lambda at: burn_through(at, datetime(2030, 1, 1))):
        yield seen


def test_used_mode_offers_the_toggle_and_never_queries_burn(auth_client, captured, burn):
    body = auth_client.get('/allocations/htmx/calendar/Derecho?active_at=2026-10-03&mode=used'
                           ).get_data(as_text=True)
    assert 'aria-label="Calendar shading"' in body and 'mode=burn' in body
    assert 'cal-burn' not in body and 'cal-strip' not in body and 'burn-key' not in body
    assert 'query' not in burn


def test_burn_mode_draws_group_strips_and_forwards_the_mode(auth_client, captured, burn):
    body = auth_client.get('/allocations/htmx/calendar/Derecho?active_at=2026-10-03&mode=burn'
                           '&facilities=UNIV').get_data(as_text=True)
    assert burn['query']['as_of'] == AT and burn['query']['window_start'] == datetime(2025, 10, 1)
    assert 'alloc-calendar cal-burn' in body and 'burn-key' in body and 'burn-runout-swatch' in body
    assert body.count('class="cal-strip"') == 3          # UNIV and its two types
    assert body.count('mode=burn&amp;') + body.count('mode=burn"') >= 3
    assert 'Mar 2026: 50 charged' in body


def test_burn_on_today_counts_complete_days_only(auth_client, captured, burn):
    # Today's charges accumulate hourly; at month granularity the partial day waits for midnight.
    with patch.object(blueprint, '_burn_through', lambda at: burn_through(at, AT)):
        body = auth_client.get('/allocations/htmx/calendar/Derecho?active_at=2026-10-03&mode=burn'
                               ).get_data(as_text=True)
    assert burn['query']['as_of'] == datetime(2026, 10, 2)
    assert 'Charges through 2026-10-02, the last complete day.' in body


def test_burn_rows_carry_cells_instead_of_the_fill_label(auth_client, captured, burn):
    body = auth_client.get('/allocations/htmx/calendar/Derecho/rows?facility=UNIV&allocation_type=Small'
                           '&active_at=2026-10-03&mode=burn').get_data(as_text=True)
    assert 'burn-cell burn-' in body and 'cal-bar-label' not in body


def test_runout_mark_renders_in_burn_mode_only(auth_client, captured):
    # UNIV0001: 60 left on Dec 31; ~1.9/day over the look-back runs out early in November.
    hot = {1: {202607: 60.0, 202608: 60.0, 202609: 60.0}}
    url = ('/allocations/htmx/calendar/Derecho/rows?facility=UNIV&allocation_type=Small'
           '&active_at=2026-10-03')
    with patch.object(blueprint, 'cached_allocation_burn', return_value=hot):
        burn_body = auth_client.get(url + '&mode=burn').get_data(as_text=True)
        used_body = auth_client.get(url + '&mode=used').get_data(as_text=True)
    assert 'class="burn-runout"' in burn_body and 'runs out about 2026-11-' in burn_body
    assert 'burn-runout' not in used_body and 'runs out' not in used_body


def test_storage_offers_no_burn(auth_client, captured, burn, monkeypatch):
    monkeypatch.setattr(blueprint, 'get_resource_types', lambda session: {'Derecho': 'DISK'})
    body = auth_client.get('/allocations/htmx/calendar/Derecho?mode=burn').get_data(as_text=True)
    assert 'Calendar shading' not in body and 'cal-burn' not in body
    assert 'query' not in burn


@pytest.mark.parametrize('query', ['', '?mode=heat'])
def test_burn_is_the_default(auth_client, captured, burn, query):
    body = auth_client.get('/allocations/htmx/calendar/Derecho' + query).get_data(as_text=True)
    assert 'alloc-calendar cal-burn' in body and 'query' in burn
    burn_button = body[:body.index('>Burn</button>')].rsplit('<button', 1)[1]
    assert 'aria-pressed="true"' in burn_button, 'the toggle shows Burn as the selected mode'


def test_bad_active_at_falls_back_silently(auth_client, captured):
    resp = auth_client.get('/allocations/htmx/calendar/Derecho?active_at=nope')
    assert resp.status_code == 200
    assert captured['query']['as_of'].date() == datetime.now().date()


def test_unauthenticated_redirects(client):
    assert client.get('/allocations/htmx/calendar/Derecho').status_code == 302


def test_page_offers_table_and_calendar_views(auth_client):
    body = auth_client.get('/allocations/projects').get_data(as_text=True)
    assert 'id="alloc-table-view-derecho"' in body
    assert '/allocations/htmx/calendar/Derecho?' in body


@pytest.fixture
def scoped_to_univ(monkeypatch):
    from webapp.utils import rbac
    from webapp.utils.rbac import Permission
    monkeypatch.setattr(rbac, 'USER_PERMISSION_OVERRIDES', {})
    monkeypatch.setattr(rbac, 'GROUP_PERMISSIONS', {})
    monkeypatch.setattr(rbac, 'USER_FACILITY_PERMISSIONS', {'benkirk': {'UNIV': {Permission.VIEW_PROJECTS}}})


def test_scoped_user_sees_only_their_facility(auth_client, captured, scoped_to_univ):
    body = auth_client.get('/allocations/htmx/calendar/Derecho').get_data(as_text=True)
    assert 'UNIV' in body and 'NCAR' not in body


def test_scoped_user_cannot_open_another_facility_rows(auth_client, captured, scoped_to_univ):
    url = '/allocations/htmx/calendar/Derecho/rows?facility=NCAR&allocation_type=NSC'
    assert auth_client.get(url).status_code == 403
