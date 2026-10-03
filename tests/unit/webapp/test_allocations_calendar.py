"""Allocations dashboard: the calendar view's geometry and its two htmx fragments."""

from datetime import datetime
from unittest.mock import patch

import pytest

from webapp.dashboards.allocations import blueprint
from webapp.dashboards.allocations.calendar import (
    calendar_groups, calendar_months, calendar_rows, calendar_window,
)

AT = datetime(2026, 10, 3)


def _row(projcode, start, end, amount=100.0, used=0.0, facility='UNIV', alloc_type='Small'):
    return {'projcode': projcode, 'resource': 'Derecho', 'facility': facility,
            'allocation_type': alloc_type, 'start_date': start, 'end_date': end,
            'total_amount': amount, 'total_used': used}


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
