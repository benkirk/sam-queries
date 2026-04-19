"""HTTP-layer tests for the Create Charge Adjustment HTMX routes.

Scope follows the pattern from tests/api/test_member_management.py: this
file exercises **auth, validation, and error re-render paths only**.
Successful-write behavior (sign enforcement, adjusted_by, adjustment_date)
is covered at the model layer in tests/unit/test_charge_adjustment_create.py.
Writing a happy-path HTTP test would require full SAVEPOINT bridging
between Flask-SQLAlchemy's db.session and the test session fixture, which
this suite deliberately avoids.

Endpoints tested:
    GET  /allocations/htmx/create_adjustment_form
    GET  /allocations/htmx/resources_for_project
    POST /allocations/htmx/create_adjustment
"""
import os

import pytest


pytestmark = pytest.mark.unit


@pytest.fixture
def scsg0001_project_id(session):
    """SCSG0001's project_id from the test snapshot.

    The snapshot preserves benkirk's real project so tests that need a
    guaranteed-existing project can use it without factory overhead.
    """
    from sam.projects.projects import Project
    p = Project.get_by_projcode(session, 'SCSG0001')
    assert p is not None, "SCSG0001 must exist in the test snapshot"
    return p.project_id


# ---------------------------------------------------------------------------
# GET /htmx/create_adjustment_form
# ---------------------------------------------------------------------------


class TestCreateAdjustmentFormEndpoint:

    def test_unauthenticated_redirects_or_401(self, client):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        resp = client.get('/allocations/htmx/create_adjustment_form')
        assert resp.status_code in (302, 401)

    def test_non_admin_denied(self, non_admin_client):
        """User without EDIT_ALLOCATIONS gets 403 from @require_permission."""
        resp = non_admin_client.get('/allocations/htmx/create_adjustment_form')
        assert resp.status_code == 403

    def test_admin_renders_four_type_options(self, auth_client):
        """Admin sees a <select> containing exactly Refund / Credit / Debit /
        Reservation — the four names keyed by _SIGN_BY_TYPE."""
        resp = auth_client.get('/allocations/htmx/create_adjustment_form')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        # Type names appear in <option> labels (and also in the .adj-intent
        # boilerplate divs) — presence is enough; exact count is fragile.
        for name in ('Refund', 'Credit', 'Debit', 'Reservation'):
            assert name in html
        # Storage-* types must not be offered.
        assert 'Storage-Credit' not in html
        assert 'Storage-Debit' not in html


# ---------------------------------------------------------------------------
# GET /htmx/resources_for_project
# ---------------------------------------------------------------------------


class TestResourcesForProjectEndpoint:

    def test_unauthenticated_redirects_or_401(self, client, scsg0001_project_id):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        resp = client.get(
            f'/allocations/htmx/resources_for_project?project_id={scsg0001_project_id}'
        )
        assert resp.status_code in (302, 401)

    def test_non_admin_denied(self, non_admin_client, scsg0001_project_id):
        resp = non_admin_client.get(
            f'/allocations/htmx/resources_for_project?project_id={scsg0001_project_id}'
        )
        assert resp.status_code == 403

    def test_missing_project_id_returns_placeholder(self, auth_client):
        resp = auth_client.get('/allocations/htmx/resources_for_project')
        assert resp.status_code == 200
        assert 'Select a project first' in resp.get_data(as_text=True)

    def test_non_int_project_id_returns_placeholder(self, auth_client):
        resp = auth_client.get(
            '/allocations/htmx/resources_for_project?project_id=not-a-number'
        )
        assert resp.status_code == 200
        assert 'Select a project first' in resp.get_data(as_text=True)

    def test_unknown_project_id_returns_placeholder(self, auth_client):
        resp = auth_client.get(
            '/allocations/htmx/resources_for_project?project_id=999999999'
        )
        assert resp.status_code == 200
        assert 'Unknown project' in resp.get_data(as_text=True)

    def test_known_project_returns_hpc_dav_options(self, auth_client, scsg0001_project_id):
        """SCSG0001 has active HPC+DAV accounts in the snapshot — the
        endpoint should render <option> tags for compute resources."""
        resp = auth_client.get(
            f'/allocations/htmx/resources_for_project?project_id={scsg0001_project_id}'
        )
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert '<option' in html
        # Placeholder not suppressed — the user still needs to pick one.
        assert 'Select a resource' in html


# ---------------------------------------------------------------------------
# POST /htmx/create_adjustment
# ---------------------------------------------------------------------------


def _valid_form_data(project_id):
    return {
        'project_id': str(project_id),
        'resource_id': '7',
        'charge_adjustment_type_id': '3',   # Refund
        'amount': '100',
        'comment': 'test',
    }


class TestCreateAdjustmentPost:

    def test_unauthenticated_redirects_or_401(self, client, scsg0001_project_id):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        resp = client.post(
            '/allocations/htmx/create_adjustment',
            data=_valid_form_data(scsg0001_project_id),
        )
        assert resp.status_code in (302, 401)

    def test_non_admin_denied(self, non_admin_client, scsg0001_project_id):
        resp = non_admin_client.post(
            '/allocations/htmx/create_adjustment',
            data=_valid_form_data(scsg0001_project_id),
        )
        assert resp.status_code == 403

    def test_missing_required_field_rerenders_form_with_error(
        self, auth_client, scsg0001_project_id,
    ):
        """Marshmallow validation error → handle_htmx_form_post re-renders
        the form fragment with the error list. No DB commit."""
        bad = _valid_form_data(scsg0001_project_id)
        del bad['amount']
        resp = auth_client.post(
            '/allocations/htmx/create_adjustment', data=bad,
        )
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        # Error panel is inside the re-rendered form fragment.
        assert 'alert-danger' in html
        assert 'Amount' in html

    def test_negative_amount_rerenders_form_with_error(
        self, auth_client, scsg0001_project_id,
    ):
        """Schema's Range(min=0, min_inclusive=False) rejects negative input
        — the error comes back as a re-rendered form fragment."""
        bad = _valid_form_data(scsg0001_project_id)
        bad['amount'] = '-5'
        resp = auth_client.post(
            '/allocations/htmx/create_adjustment', data=bad,
        )
        assert resp.status_code == 200
        assert 'alert-danger' in resp.get_data(as_text=True)

    def test_unknown_project_id_rerenders_form_with_error(self, auth_client):
        """ValueError raised inside do_action (FK existence check) →
        management_transaction auto-rolls back, handle_htmx_form_post
        catches the exception and re-renders with the prefixed message."""
        bad = _valid_form_data(project_id=999_999_999)
        resp = auth_client.post(
            '/allocations/htmx/create_adjustment', data=bad,
        )
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'alert-danger' in html
        assert 'Error creating adjustment' in html

    def test_unsupported_type_id_rerenders_form_with_error(
        self, auth_client, scsg0001_project_id,
    ):
        """Type ID 5 (Storage-Credit) passes the schema's shape check but
        ChargeAdjustment.create() rejects it because 'Storage-Credit' is
        not in _SIGN_BY_TYPE. The route surfaces that ValueError as a
        form error. The error could also surface as an FK lookup failure
        if the (project, resource) → Account join fails first — either
        way the response carries the "Error creating adjustment" prefix."""
        bad = _valid_form_data(scsg0001_project_id)
        bad['charge_adjustment_type_id'] = '5'
        resp = auth_client.post(
            '/allocations/htmx/create_adjustment', data=bad,
        )
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'alert-danger' in html
        assert 'Error creating adjustment' in html
