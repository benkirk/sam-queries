"""The Facility -> Panel cascade serves every form that uses it."""
import pytest

from webapp.utils.rbac import Permission

URL = '/admin/htmx/panels-for-facility'


@pytest.fixture
def facility_id(session):
    from sam.resources.facilities import Facility, Panel
    row = (session.query(Facility.facility_id)
           .join(Panel, Panel.facility_id == Facility.facility_id)
           .filter(Facility.is_active, Panel.is_active).first())
    if row is None:
        pytest.skip('snapshot has no facility with an active panel')
    return row[0]


def _holding(monkeypatch, *perms):
    from webapp.utils import rbac
    allowed = {Permission.ACCESS_ADMIN_DASHBOARD, *perms}
    monkeypatch.setattr(rbac, 'get_user_permissions', lambda user: allowed)


@pytest.mark.parametrize('perm', [Permission.CREATE_FACILITIES, Permission.CREATE_PROJECTS])
def test_either_create_permission_fills_the_panel_select(auth_client, monkeypatch,
                                                         facility_id, perm):
    _holding(monkeypatch, perm)
    resp = auth_client.get(URL, query_string={'facility_id': facility_id})
    assert resp.status_code == 200
    assert '<option' in resp.get_data(as_text=True)


def test_a_viewer_is_still_refused(auth_client, monkeypatch, facility_id):
    _holding(monkeypatch, Permission.VIEW_PROJECTS)
    assert auth_client.get(URL, query_string={'facility_id': facility_id}).status_code == 403
