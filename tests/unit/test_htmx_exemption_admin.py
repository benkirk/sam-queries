"""HTTP-layer tests for the admin wallclock-exemption routes and the
Resources card listing.

Scope (mirrors tests/unit/test_htmx_create_adjustment.py): auth,
permission, validation, and render smoke — happy-path DB writes are
covered at the model layer in test_wallclock_exemptions.py.

Endpoints tested:
    GET  /admin/htmx/resources                    (listing renders)
    POST /admin/htmx/exemption-deactivate/<id>    (auth + 404 paths)
"""
import os

import pytest


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# GET /admin/htmx/resources — Wallclock Exemptions section renders
# ---------------------------------------------------------------------------


class TestResourcesCardExemptionsSection:

    def test_unauthenticated_redirects_or_401(self, client):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        resp = client.get('/admin/htmx/resources')
        assert resp.status_code in (302, 401)

    def test_non_admin_denied(self, non_admin_client):
        resp = non_admin_client.get('/admin/htmx/resources')
        assert resp.status_code == 403

    def test_admin_renders_exemptions_section(self, auth_client):
        """The new Wallclock Exemptions listing block is present in the
        Queues tab — heading, column headers, and chevron-icon class."""
        resp = auth_client.get('/admin/htmx/resources')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Wallclock Exemptions' in html
        assert 'exemption-res-collapse-icon' in html
        # Deactivate button wiring is emitted only when exemptions exist, so
        # don't assert on it here — the chevron class is present either way.


# ---------------------------------------------------------------------------
# POST /admin/htmx/exemption-deactivate/<id>
# ---------------------------------------------------------------------------


class TestExemptionDeactivateEndpoint:

    def test_unauthenticated_redirects_or_401(self, client):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        resp = client.post('/admin/htmx/exemption-deactivate/99999999')
        assert resp.status_code in (302, 401)

    def test_non_admin_denied(self, non_admin_client):
        resp = non_admin_client.post('/admin/htmx/exemption-deactivate/99999999')
        assert resp.status_code == 403

    def test_admin_nonexistent_returns_404(self, auth_client):
        """Missing exemption_id → htmx_not_found 404 with alert fragment."""
        resp = auth_client.post('/admin/htmx/exemption-deactivate/99999999')
        assert resp.status_code == 404
        assert b'Exemption not found' in resp.data
