"""HTTP-layer tests for the per-resource facility fair-share override routes
under Admin > Resources.

Scope (mirrors test_htmx_exemption_admin.py): auth, permission, validation,
404, and render smoke. Happy-path DB writes are covered at the model layer in
test_facility_resource.py and end-to-end in
test_fstree_queries.py::TestFacilityResourceOverride.

Endpoints tested:
    GET    /admin/htmx/facility-resource-edit-form/<res>/<fac>
    POST   /admin/htmx/facility-resource-edit/<res>/<fac>
    DELETE /admin/htmx/facility-resource-unset/<res>/<fac>
"""
import os

import pytest


pytestmark = pytest.mark.unit


_BOGUS = 99999999


def _edit_form_url(res, fac):
    return f'/admin/htmx/facility-resource-edit-form/{res}/{fac}'


def _edit_url(res, fac):
    return f'/admin/htmx/facility-resource-edit/{res}/{fac}'


def _unset_url(res, fac):
    return f'/admin/htmx/facility-resource-unset/{res}/{fac}'


# ---------------------------------------------------------------------------
# Resources card renders the override table (chevron + modal wiring)
# ---------------------------------------------------------------------------


class TestResourcesCardOverrideSection:

    def test_admin_renders_override_wiring(self, auth_client):
        resp = auth_client.get('/admin/htmx/resources')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'facility-resource-collapse-icon' in html
        assert 'editFacilityResourceModal' in html

    def test_admin_renders_totals_footer(self, auth_client):
        """Each HPC/DAV override table has a Total footer row summing the shares."""
        resp = auth_client.get('/admin/htmx/resources')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert '<tfoot' in html
        assert 'Total' in html


# ---------------------------------------------------------------------------
# GET edit-form
# ---------------------------------------------------------------------------


class TestEditFormRoute:

    def test_unauthenticated_redirects_or_401(self, client):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        resp = client.get(_edit_form_url(_BOGUS, _BOGUS))
        assert resp.status_code in (302, 401)

    def test_non_admin_denied(self, non_admin_client):
        resp = non_admin_client.get(_edit_form_url(_BOGUS, _BOGUS))
        assert resp.status_code == 403

    def test_missing_pair_renders_not_found(self, auth_client):
        resp = auth_client.get(_edit_form_url(_BOGUS, _BOGUS))
        assert b'not found' in resp.data.lower()

    def test_valid_pair_renders_form(self, auth_client, hpc_resource, any_facility):
        resp = auth_client.get(
            _edit_form_url(hpc_resource.resource_id, any_facility.facility_id)
        )
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Override Fair Share' in html
        assert any_facility.facility_name in html


# ---------------------------------------------------------------------------
# POST edit (set / upsert)
# ---------------------------------------------------------------------------


class TestEditRoute:

    def test_unauthenticated_redirects_or_401(self, client):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        resp = client.post(_edit_url(_BOGUS, _BOGUS), data={'fair_share_percentage': '10'})
        assert resp.status_code in (302, 401)

    def test_non_admin_denied(self, non_admin_client):
        resp = non_admin_client.post(_edit_url(_BOGUS, _BOGUS), data={'fair_share_percentage': '10'})
        assert resp.status_code == 403

    def test_missing_pair_returns_404(self, auth_client):
        resp = auth_client.post(_edit_url(_BOGUS, _BOGUS), data={'fair_share_percentage': '10'})
        assert resp.status_code == 404

    def test_missing_value_re_renders_with_error(self, auth_client, hpc_resource, any_facility):
        resp = auth_client.post(
            _edit_url(hpc_resource.resource_id, any_facility.facility_id), data={}
        )
        assert resp.status_code == 200
        assert b'Override Fair Share' in resp.data  # form re-rendered

    def test_out_of_range_re_renders_with_error(self, auth_client, hpc_resource, any_facility):
        resp = auth_client.post(
            _edit_url(hpc_resource.resource_id, any_facility.facility_id),
            data={'fair_share_percentage': '150'},
        )
        assert resp.status_code == 200
        assert b'Override Fair Share' in resp.data


# ---------------------------------------------------------------------------
# DELETE unset
# ---------------------------------------------------------------------------


class TestUnsetRoute:

    def test_unauthenticated_redirects_or_401(self, client):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        resp = client.delete(_unset_url(_BOGUS, _BOGUS))
        assert resp.status_code in (302, 401)

    def test_non_admin_denied(self, non_admin_client):
        resp = non_admin_client.delete(_unset_url(_BOGUS, _BOGUS))
        assert resp.status_code == 403

    def test_unset_absent_pair_is_ok(self, auth_client, hpc_resource, any_facility):
        # No override exists for this (resource, facility) in the snapshot's
        # default state -> clear_override is a no-op but the route still
        # succeeds and fires reloadResourcesCard.
        resp = auth_client.delete(
            _unset_url(hpc_resource.resource_id, any_facility.facility_id)
        )
        assert resp.status_code == 200
