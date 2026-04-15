"""
API endpoint tests for Charge/Balance endpoints — HTTP-layer-only subset.

Scope note (Phase 4d): This file is the read-path + failure-mode subset of
the legacy tests/api/test_charge_endpoints.py. The successful POST tests
(create/update comp/disk/archive charge summaries) are intentionally
dropped because they are fully covered at the service layer by Phase 3's
test_manage_summaries (53 tests against the same upsert functions, under
SAVEPOINT isolation). Keeping them here would require full Flask-SQLAlchemy
SAVEPOINT bridging, which the raw `session` fixture cannot provide without
patching `webapp.extensions.db`.

What this file DOES cover (HTTP-layer concerns that Phase 3 doesn't):
  - GET /api/v1/projects/<projcode>/charges/summary — real-time balance reads
  - GET /api/v1/projects/<projcode>/charges — charge detail (may be 404 if not impl)
  - Schema validation (400) — route wiring + marshmallow error envelope
  - FK lookup failure (422) — management_transaction rollback path
  - Auth redirect on unauthenticated POST

None of the tests below produce successful commits: the GET tests are pure
reads, the validation-error tests fail before any DB touch, and the FK
lookup tests rely on `management_transaction` rolling back the nested
upsert call before any row is persisted.
"""

import json
import pytest


class TestProjectChargesSummary:
    """Test GET /api/v1/projects/<projcode>/charges/summary endpoint."""

    def test_charges_summary_success(self, auth_client):
        """Test successful charges summary retrieval."""
        response = auth_client.get('/api/v1/projects/CESM0002/charges/summary')
        assert response.status_code == 200

        data = response.get_json()
        assert 'projcode' in data
        assert data['projcode'] == 'CESM0002'

        # Response structure may be 'allocations', 'resources', or 'summary'
        assert 'allocations' in data or 'resources' in data or 'summary' in data

    def test_charges_summary_allocation_structure(self, auth_client):
        """Test allocation summary includes usage fields."""
        response = auth_client.get('/api/v1/projects/CESM0002/charges/summary')
        assert response.status_code == 200

        data = response.get_json()

        # Handle both 'allocations' and 'resources' response formats
        if 'allocations' in data and len(data['allocations']) > 0:
            alloc = data['allocations'][0]
        elif 'resources' in data and len(data['resources']) > 0:
            # Resources dict format: get first resource
            resource_name = list(data['resources'].keys())[0]
            alloc = data['resources'][resource_name]
        else:
            pytest.skip("No allocation or resource data to test")

        # Core balance fields
        assert 'allocated' in alloc
        assert 'used' in alloc
        assert 'remaining' in alloc
        assert 'percent_used' in alloc

        # Breakdown by charge type
        assert 'charges_by_type' in alloc
        charges = alloc['charges_by_type']
        # At least one charge type should be present
        assert isinstance(charges, dict)

    def test_charges_summary_with_adjustments(self, auth_client):
        """Test charges summary includes adjustments when requested."""
        response = auth_client.get(
            '/api/v1/projects/CESM0002/charges/summary?include_adjustments=true'
        )
        assert response.status_code == 200

        data = response.get_json()

        # Handle both response formats
        if 'allocations' in data and len(data['allocations']) > 0:
            alloc = data['allocations'][0]
            # Adjustments field should be present (may be empty list or number)
            assert 'adjustments' in alloc or 'adjustment_total' in alloc
        elif 'resources' in data and len(data['resources']) > 0:
            # For resources format, adjustments should be a number
            resource_name = list(data['resources'].keys())[0]
            assert 'adjustments' in data['resources'][resource_name]

    def test_charges_summary_without_adjustments(self, auth_client):
        """Test charges summary excludes adjustments when requested."""
        response = auth_client.get(
            '/api/v1/projects/CESM0002/charges/summary?include_adjustments=false'
        )
        assert response.status_code == 200

    def test_charges_summary_not_found(self, auth_client):
        """Test 404 for non-existent project."""
        response = auth_client.get('/api/v1/projects/INVALID999/charges/summary')
        assert response.status_code == 404

        data = response.get_json()
        assert 'error' in data

    def test_charges_summary_project_no_allocations(self, auth_client):
        """Test response for project with no allocations."""
        # Find a project with no allocations or use known test project
        response = auth_client.get('/api/v1/projects/UMIN0005/charges/summary')

        # Should return 200 with empty allocations (not 404)
        assert response.status_code in [200, 404]

        if response.status_code == 200:
            data = response.get_json()
            if 'allocations' in data:
                assert len(data['allocations']) == 0


class TestProjectChargesDetail:
    """Test GET /api/v1/projects/<projcode>/charges endpoint (if it exists)."""

    def test_charges_detail_success(self, auth_client):
        """Test successful charges detail retrieval."""
        response = auth_client.get('/api/v1/projects/CESM0002/charges')

        # Skip if endpoint doesn't exist
        if response.status_code == 404:
            pytest.skip("Charges detail endpoint not implemented")

        assert response.status_code == 200

        data = response.get_json()
        assert 'projcode' in data
        assert 'charges' in data or 'charge_history' in data

    def test_charges_detail_date_filtering(self, auth_client):
        """Test date range filtering."""
        from datetime import datetime, timedelta

        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        response = auth_client.get(
            f'/api/v1/projects/CESM0002/charges?'
            f'start_date={start_date.date()}&end_date={end_date.date()}'
        )

        # Skip if endpoint doesn't exist
        if response.status_code == 404:
            pytest.skip("Charges detail endpoint not implemented")

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# POST charge summary endpoints — failure-mode subset
# ---------------------------------------------------------------------------
#
# Only the validation-error and FK-lookup-failure paths are kept from the
# legacy file. The successful POST tests (create/update on comp/disk/archive)
# are covered by Phase 3's test_manage_summaries.py at the service layer.
#
# `comp_post_body` builds a valid payload by reading snapshot data (benkirk,
# SCSG0001, Derecho, machine, queue) through the raw `session` fixture.
# It does NOT write anything — the tests below mutate the payload to trigger
# specific failure paths in `_handle_charge_summary_post`:
#   - 400: schema validation error (fails before management_transaction)
#   - 422: ValueError inside management_transaction → rollback → response
# Either path leaves the DB unchanged, so SAVEPOINT bridging is unnecessary.

@pytest.fixture
def comp_post_body(session):
    """Build a valid POST body for comp charge summary from snapshot data."""
    from sam.core.users import User
    from sam.projects.projects import Project
    from sam.resources.resources import Resource
    from sam.resources.machines import Machine, Queue
    from sam.accounting.accounts import Account

    user = User.get_by_username(session, 'benkirk')
    project = Project.get_by_projcode(session, 'SCSG0001')
    resource = Resource.get_by_name(session, 'Derecho')
    account = Account.get_by_project_and_resource(
        session, project.project_id, resource.resource_id
    )
    if not account:
        pytest.skip("No Derecho account for SCSG0001")

    machine = session.query(Machine).filter_by(
        resource_id=resource.resource_id
    ).first()
    queue = session.query(Queue).filter_by(
        resource_id=resource.resource_id
    ).first()

    return {
        'activity_date': '2099-01-15',
        'act_username': user.username,
        'act_projcode': project.projcode,
        'act_unix_uid': user.unix_uid,
        'resource_name': 'Derecho',
        'machine_name': machine.name,
        'queue_name': queue.queue_name,
        'num_jobs': 10,
        'core_hours': 1234.5,
        'charges': 987.65,
    }


class TestPostCompChargeSummaryFailures:
    """Test POST /api/v1/charge-summaries/comp — failure modes only."""

    def test_post_comp_validation_error(self, auth_client):
        """Missing required field -> 400 (fails before DB touch)."""
        response = auth_client.post(
            '/api/v1/charge-summaries/comp',
            data=json.dumps({'act_username': 'benkirk'}),
            content_type='application/json',
        )
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data

    def test_post_comp_unknown_user(self, auth_client, comp_post_body):
        """Unknown user -> 422 via management_transaction rollback."""
        body = dict(comp_post_body, act_username='nobody_xyz', act_unix_uid=999999999)
        response = auth_client.post(
            '/api/v1/charge-summaries/comp',
            data=json.dumps(body),
            content_type='application/json',
        )
        assert response.status_code == 422

    def test_post_comp_queue_missing_no_flag(self, auth_client, comp_post_body):
        """Unknown queue without autocreate flag -> 422 with hint."""
        body = dict(comp_post_body, queue_name='fake_queue_xyz')
        response = auth_client.post(
            '/api/v1/charge-summaries/comp',
            data=json.dumps(body),
            content_type='application/json',
        )
        assert response.status_code == 422
        assert 'create_queue_if_missing' in response.get_json()['error']


class TestPostDiskChargeSummaryFailures:
    """Test POST /api/v1/charge-summaries/disk — failure modes only."""

    def test_post_disk_validation_error(self, auth_client):
        """Missing required field -> 400."""
        response = auth_client.post(
            '/api/v1/charge-summaries/disk',
            data=json.dumps({'act_username': 'benkirk'}),
            content_type='application/json',
        )
        assert response.status_code == 400


class TestPostChargeSummaryAuth:
    """Test authentication/authorization for POST endpoints."""

    def test_post_unauthenticated(self, client):
        """No login -> 401 or 302 redirect."""
        response = client.post(
            '/api/v1/charge-summaries/comp',
            data=json.dumps({'activity_date': '2025-01-15'}),
            content_type='application/json',
        )
        assert response.status_code in [401, 302]
