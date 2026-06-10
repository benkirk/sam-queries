"""
Route-authorization hardening tests (PRODUCTION_IMPROVEMENTS item 2;
PR295 P0-4, P1-4/5/6/7).

Three access tiers exercised against the formerly-open routes:
  - outsider: active snapshot user, no permissions, unaffiliated with the
    target project → 403 everywhere
  - lead: lead of the target project (steward override, no system perms)
    → reads + member-management succeed
  - auth_client (benkirk): full permissions via USER_PERMISSION_OVERRIDES
    → unaffected

Project/user pairs are derived from the snapshot so the tests survive
obfuscated-snapshot refreshes.
"""

import pytest

from sam import Allocation, User


def _affiliated(user, project):
    """Mirror access_control._user_can_access_project (direct only)."""
    ids = {p.project_id for p in user.active_projects()}
    ids.update(p.project_id for p in user.led_projects)
    ids.update(p.project_id for p in user.admin_projects)
    return project.project_id in ids


def _login(client, user):
    with client.session_transaction() as s:
        s['_user_id'] = str(user.user_id)
        s['_fresh'] = True


@pytest.fixture
def outsider(session):
    """Active snapshot user with no permissions (not benkirk)."""
    user = (session.query(User)
            .filter(User.is_active, User.username != 'benkirk')
            .order_by(User.user_id)
            .first())
    assert user is not None, "snapshot has no active non-benkirk users"
    return user


@pytest.fixture
def lead_project(session, outsider):
    """(lead_user, project): an active project led by a non-benkirk user
    the outsider is NOT affiliated with."""
    leads = (session.query(User)
             .filter(User.is_active,
                     User.username != 'benkirk',
                     User.user_id != outsider.user_id,
                     User.led_projects.any())
             .order_by(User.user_id)
             .limit(50))
    for lead in leads:
        for project in lead.led_projects:
            if project.active and not _affiliated(outsider, project):
                return lead, project
    pytest.skip("snapshot has no suitable lead/project pair")


@pytest.fixture
def target_allocation(session, lead_project):
    """A non-deleted allocation under the lead's project (so the lead can
    read it via steward override and the outsider cannot)."""
    _, project = lead_project
    alloc = (session.query(Allocation)
             .filter(Allocation.deleted == False)  # noqa: E712
             .join(Allocation.account)
             .filter_by(project_id=project.project_id)
             .order_by(Allocation.allocation_id)
             .first())
    if alloc is None:
        pytest.skip("lead project has no allocations in snapshot")
    return alloc


class TestOutsiderGets403:

    @pytest.fixture(autouse=True)
    def _as_outsider(self, client, outsider):
        _login(client, outsider)
        self.client = client

    def test_allocation_get(self, target_allocation):
        resp = self.client.get(f'/api/v1/allocations/{target_allocation.allocation_id}')
        assert resp.status_code == 403
        assert 'error' in resp.get_json()

    def test_users_search(self):
        assert self.client.get('/api/v1/users/search?q=ab').status_code == 403

    def test_charges(self, lead_project):
        _, project = lead_project
        assert self.client.get(
            f'/api/v1/projects/{project.projcode}/charges').status_code == 403

    def test_charges_summary(self, lead_project):
        _, project = lead_project
        assert self.client.get(
            f'/api/v1/projects/{project.projcode}/charges/summary').status_code == 403

    def test_rolling_section(self, lead_project):
        _, project = lead_project
        assert self.client.get(
            f'/user/htmx/rolling-section/{project.projcode}/Derecho').status_code == 403

    def test_threshold_form(self, lead_project):
        _, project = lead_project
        assert self.client.get(
            f'/user/htmx/threshold-form/{project.projcode}/Derecho/30').status_code == 403

    def test_threshold_save(self, lead_project):
        _, project = lead_project
        assert self.client.post(
            f'/user/htmx/threshold/{project.projcode}/Derecho/30',
            data={'threshold_pct': '150'}).status_code == 403

    def test_members_fragment(self, lead_project):
        _, project = lead_project
        assert self.client.get(
            f'/project-members/{project.projcode}').status_code == 403

    def test_member_add_form(self, lead_project):
        _, project = lead_project
        assert self.client.get(
            f'/project-members/{project.projcode}/add-form').status_code == 403

    def test_member_add(self, lead_project):
        _, project = lead_project
        assert self.client.post(
            f'/project-members/{project.projcode}/add',
            data={'username': 'benkirk'}).status_code == 403

    def test_member_remove(self, lead_project):
        _, project = lead_project
        assert self.client.delete(
            f'/project-members/{project.projcode}/benkirk').status_code == 403

    def test_change_admin_htmx(self, lead_project):
        _, project = lead_project
        assert self.client.put(
            f'/project-members/{project.projcode}/admin',
            data={'admin_username': ''}).status_code == 403

    def test_change_admin_api(self, lead_project):
        _, project = lead_project
        resp = self.client.put(
            f'/api/v1/projects/{project.projcode}/admin',
            json={'admin_username': ''})
        assert resp.status_code == 403
        assert 'error' in resp.get_json()

    def test_htmx_403_is_friendly_fragment(self, lead_project):
        _, project = lead_project
        resp = self.client.get(
            f'/project-members/{project.projcode}',
            headers={'HX-Request': 'true'})
        assert resp.status_code == 403
        assert 'alert-danger' in resp.get_data(as_text=True)


class TestLeadStewardAccess:
    """Lead of the project (no system permissions) keeps working."""

    @pytest.fixture(autouse=True)
    def _as_lead(self, client, lead_project):
        lead, project = lead_project
        _login(client, lead)
        self.client = client
        self.project = project

    def test_charges_member_access(self):
        """Members/stewards can now see their own charges [P1-4]."""
        resp = self.client.get(f'/api/v1/projects/{self.project.projcode}/charges')
        assert resp.status_code == 200

    def test_allocation_get_steward(self, target_allocation):
        resp = self.client.get(f'/api/v1/allocations/{target_allocation.allocation_id}')
        assert resp.status_code == 200

    def test_rolling_section(self):
        resp = self.client.get(
            f'/user/htmx/rolling-section/{self.project.projcode}/Derecho')
        assert resp.status_code == 200

    def test_members_fragment(self):
        assert self.client.get(
            f'/project-members/{self.project.projcode}').status_code == 200

    def test_member_add_form(self):
        assert self.client.get(
            f'/project-members/{self.project.projcode}/add-form').status_code == 200

    def test_change_admin_form_access(self):
        """Lead passes the can_change_admin gate (failure here would be 403;
        a 404 'user not found' means the gate was passed)."""
        resp = self.client.put(
            f'/project-members/{self.project.projcode}/admin',
            data={'admin_username': 'no_such_user_xyz'})
        assert resp.status_code == 404


class TestPrivilegedAccessPreserved:

    def test_users_search(self, auth_client):
        resp = auth_client.get('/api/v1/users/search?q=ben')
        assert resp.status_code == 200

    def test_charges(self, auth_client, lead_project):
        _, project = lead_project
        assert auth_client.get(
            f'/api/v1/projects/{project.projcode}/charges').status_code == 200

    def test_members_fragment(self, auth_client, lead_project):
        _, project = lead_project
        assert auth_client.get(
            f'/project-members/{project.projcode}').status_code == 200

    def test_rolling_section(self, auth_client, lead_project):
        _, project = lead_project
        assert auth_client.get(
            f'/user/htmx/rolling-section/{project.projcode}/Derecho').status_code == 200
