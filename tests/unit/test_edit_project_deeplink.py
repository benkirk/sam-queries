"""Shareable ?tab= / ?active_at= deep-link on /admin/project/<projcode>/edit.

Auth/render smoke per house convention — these gate the server-driven tab
channel and the point-in-time picker seed, not any write path.
"""
from datetime import datetime

from webapp.dashboards.admin.projects_routes import _grace_window_end


def _alloc(bar_state, end_date, allocation_id=1):
    return {'allocation_id': allocation_id, 'bar_state': bar_state, 'end_date': end_date}


class TestGraceWindowEnd:
    """The grace-window banner fires only when every shown allocation expired."""

    def test_all_expired_returns_latest_end(self):
        end_a = datetime(2026, 9, 30)
        end_b = datetime(2026, 6, 30)
        assert _grace_window_end([_alloc('expired', end_b), _alloc('expired', end_a)]) == end_a

    def test_any_active_returns_none(self):
        assert _grace_window_end([
            _alloc('expired', datetime(2026, 9, 30)),
            _alloc('active', datetime(2027, 9, 30)),
        ]) is None

    def test_open_ended_returns_none(self):
        assert _grace_window_end([_alloc('open-ended', None)]) is None

    def test_empty_returns_none(self):
        assert _grace_window_end([]) is None
        assert _grace_window_end([{'bar_state': 'expired'}]) is None  # no allocation_id

    def test_expired_without_end_date_returns_none(self):
        assert _grace_window_end([_alloc('expired', None)]) is None


def _edit(auth_client, projcode, query=''):
    resp = auth_client.get(f'/admin/project/{projcode}/edit{query}')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_default_lands_on_details(auth_client, active_project):
    html = _edit(auth_client, active_project.projcode)
    assert 'class="tab-pane fade show active" id="tab-details"' in html
    assert 'class="tab-pane fade" id="tab-allocations"' in html


def test_tab_allocations_activates_allocations(auth_client, active_project):
    html = _edit(auth_client, active_project.projcode, '?tab=allocations')
    assert 'class="tab-pane fade show active" id="tab-allocations"' in html
    assert 'class="tab-pane fade" id="tab-details"' in html
    # A server-active lazy tab must fire on load, not shown.bs.tab.
    assert 'hx-trigger="load once, shown.bs.tab once"' in html


def test_active_at_seeds_picker_and_implies_allocations(auth_client, active_project):
    html = _edit(auth_client, active_project.projcode, '?active_at=2026-10-01')
    assert 'class="tab-pane fade show active" id="tab-allocations"' in html
    # Picker (operator) and the initial tree fetch both carry the date.
    assert 'value="2026-10-01"' in html
    assert 'active_at=2026-10-01' in html


def test_bogus_values_fall_back_cleanly(auth_client, active_project):
    # Unknown tab with no active_at -> details; never a 500.
    html = _edit(auth_client, active_project.projcode, '?tab=zzz')
    assert 'class="tab-pane fade show active" id="tab-details"' in html
    # Unparseable active_at is present, so it still implies allocations and
    # seeds today rather than raising.
    html = _edit(auth_client, active_project.projcode, '?active_at=not-a-date')
    assert 'class="tab-pane fade show active" id="tab-allocations"' in html
