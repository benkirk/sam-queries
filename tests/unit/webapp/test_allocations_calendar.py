"""Allocations dashboard: the calendar view's geometry and its two htmx fragments."""

from datetime import datetime
from unittest.mock import patch

import pytest

from webapp.dashboards.allocations import blueprint
from webapp.dashboards.allocations.calendar import (
    burn_cells, burn_class, calendar_groups, calendar_months, calendar_rows, calendar_window,
    group_burn,
)

AT = datetime(2026, 10, 3)


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


_ROWS = [_row('UNIV0001', datetime(2026, 1, 1), datetime(2026, 12, 31), used=40.0),
         _row('UNIV0002', datetime(2025, 1, 1), datetime(2025, 12, 31), used=90.0, alloc_type='Large'),
         _row('NCAR0001', datetime(2026, 4, 1), datetime(2027, 3, 31), facility='NCAR', alloc_type='NSC')]


class TestBurn:
    # 365 days: Jan 2026 through Dec 2026, so a 31-day month's even share is 31/365 of it.
    YEAR = (datetime(2026, 1, 1), datetime(2027, 1, 1))

    def test_classes_split_at_the_edges(self):
        assert [burn_class(r) for r in (0, 0.24, 0.25, 0.74, 0.75, 1.24, 1.25, 1.99, 2, 9)] == \
            [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]

    def test_cells_follow_months_and_stop_at_the_as_of_day(self):
        start, end = calendar_window(AT)
        row = _row('A', *self.YEAR, amount=365.0, allocation_id=7)
        cells = burn_cells(row, {202601: 31.0, 202602: 56.0, 202610: 3.0}, start, end, AT)
        assert len(cells) == 10                       # Jan..Oct, nothing after Oct 3
        jan, feb = cells[0], cells[1]
        assert jan['ratio'] == pytest.approx(1.0) and jan['cls'] == 2
        assert feb['ratio'] == pytest.approx(2.0) and feb['cls'] == 4   # 56 over a 28-day share
        assert cells[2]['charges'] == 0.0 and cells[2]['cls'] == 0
        assert jan['left'] == 0.0 and feb['left'] == pytest.approx(31 / 365 * 100)
        oct_ = cells[-1]                              # Oct 1 to the end of Oct 3: ~3 days
        assert oct_['ratio'] == pytest.approx(1.0, rel=1e-4)
        assert oct_['width'] == pytest.approx(3 / 365 * 100, rel=1e-4)

    def test_cells_are_in_percent_of_the_clipped_bar(self):
        start, end = calendar_window(AT)               # opens 2025-10-01
        row = _row('A', datetime(2025, 7, 1), datetime(2026, 7, 1), amount=365.0, allocation_id=1)
        cells = burn_cells(row, {202510: 31.0}, start, end, AT)
        assert cells[0]['month'] == datetime(2025, 10, 1) and cells[0]['left'] == 0.0
        assert sum(c['width'] for c in cells) == pytest.approx(100.0)
        assert cells[0]['ratio'] == pytest.approx(1.0)

    def test_no_cells_without_an_even_pace(self):
        start, end = calendar_window(AT)
        for row in (_row('A', datetime(2026, 1, 1), None, allocation_id=1),
                    _row('A', *self.YEAR, amount=0.0, allocation_id=1),
                    _row('A', *self.YEAR)):           # no allocation_id: a stale cached row
            assert burn_cells(row, {}, start, end, AT) is None
        future = _row('A', datetime(2026, 11, 1), datetime(2027, 11, 1), allocation_id=1)
        assert burn_cells(future, {}, start, end, AT) == []

    def test_rows_carry_burn_only_when_asked(self):
        start, end = calendar_window(AT)
        rows = [_row('A', *self.YEAR, amount=365.0, allocation_id=7)]
        assert 'burn' not in calendar_rows(rows, start, end, AT)[0]['bars'][0]
        (a,) = calendar_rows(rows, start, end, AT, burns={7: {202601: 31.0}})
        assert a['bars'][0]['burn'][0]['cls'] == 2

    def test_group_burn_sums_charges_over_shares(self):
        start, end = calendar_window(AT)
        rows = [_row('A', *self.YEAR, amount=365.0, allocation_id=1),
                _row('B', *self.YEAR, amount=730.0, allocation_id=2),
                _row('C', datetime(2026, 1, 1), None, allocation_id=3)]   # open-ended: ignored
        cells = group_burn(rows, {1: {202603: 31.0}, 2: {202603: 31.0}, 3: {202603: 999.0}},
                           start, end, AT)
        mar = next(c for c in cells if c['month'] == datetime(2026, 3, 1))
        assert mar['charges'] == 62.0 and mar['ratio'] == pytest.approx(62 / 93)
        assert cells[0]['month'] == datetime(2026, 1, 1)
        assert cells[-1]['month'] == datetime(2026, 10, 1)
        assert mar['left'] == pytest.approx((datetime(2026, 3, 1) - start) / (end - start) * 100)


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
    assert body.count('facilities=UNIV') == 2


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
