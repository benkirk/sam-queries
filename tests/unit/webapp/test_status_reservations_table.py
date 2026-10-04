"""The shared reservations table (status/fragments/reservations.html) and ResourceReservation.is_active."""

from datetime import datetime, timedelta

from flask import render_template_string
from sqlalchemy import select

from system_status.models.outages import ResourceReservation
from system_status.timeutil import utcnow_naive

MACRO = ("{% from 'dashboards/status/fragments/reservations.html' import reservation_table %}"
         "{{ reservation_table(rows, show_system=show_system) }}")


def _row(name, start, end, **kw):
    return ResourceReservation(reservation_name=name, start_time=start, end_time=end, **kw)


def _render(app, rows, show_system=False):
    with app.test_request_context():
        return render_template_string(MACRO, rows=rows, show_system=show_system)


def test_same_day_window_prints_the_date_once(app):
    # 14:00-18:00 UTC is 08:00-12:00 MDT on the same day.
    html = _render(app, [_row('latch', datetime(2030, 7, 8, 14), datetime(2030, 7, 8, 18), node_count=36)])
    assert 'Mon Jul 8' in html and html.count('Jul 8') == 1
    assert '08:00&ndash;12:00 M' in html


def test_cross_day_window_prints_both_dates(app):
    html = _render(app, [_row('drain', datetime(2030, 7, 5, 16), datetime(2030, 7, 8, 16))])
    assert 'Fri Jul 5' in html and 'Mon Jul 8' in html


def test_partition_shows_only_when_not_the_default(app):
    html = _render(app, [_row('a', datetime(2030, 1, 1), datetime(2030, 1, 2), partition='pbs-default'),
                         _row('b', datetime(2030, 1, 1), datetime(2030, 1, 2), partition='gpu-a100')])
    assert 'partition gpu-a100' in html and 'pbs-default' not in html


def test_system_column_and_empty_state(app):
    assert '<th class="col-shrink">System</th>' in _render(app, [_row('a', datetime(2030, 1, 1), datetime(2030, 1, 2))],
                                                           show_system=True)
    empty = _render(app, [])
    assert 'Maintenance &amp; Reservations' in empty and 'No upcoming reservations.' in empty


def test_is_active_marks_a_reservation_holding_nodes_now(app):
    now = utcnow_naive()
    held = _row('held', now - timedelta(hours=1), now + timedelta(hours=1))
    later = _row('later', now + timedelta(hours=1), now + timedelta(hours=2))
    assert held.is_active and not later.is_active
    html = _render(app, [held, later])
    assert html.count('in progress') == 1


def test_is_active_expression_bounds_both_ends():
    sql = str(select(ResourceReservation.reservation_id).where(ResourceReservation.is_active))
    assert 'start_time <=' in sql and 'end_time >=' in sql
