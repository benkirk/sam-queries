"""API endpoint tests for /api/v1/allocations/<id> — HTTP-layer-only subset.

Scope note: GET tests use snapshot data (read-only). PUT tests cover only
the failure paths (validation errors, 404, auth) because the success path
commits via `management_transaction(db.session)` and there's no SAVEPOINT
bridging between the raw test `session` fixture and Flask-SQLAlchemy's
`db.session` — a successful PUT would mutate the snapshot for real.

The `update_allocation` service function itself is exercised at the
service layer by tests/unit/test_management_functions.py.
"""
import os

import pytest

from sam import Allocation


pytestmark = pytest.mark.api


@pytest.fixture
def snapshot_allocation_id(session):
    """ID of any non-deleted allocation in the snapshot.

    Used for GET happy-path and PUT auth/validation tests where we need
    a real ID that Flask's db.session can resolve. Tests must NOT issue
    a successful PUT against this row — see the module docstring.
    """
    alloc = (
        session.query(Allocation)
        .filter(Allocation.deleted == False)  # noqa: E712 — SQL bool, not Python identity
        .order_by(Allocation.allocation_id)
        .first()
    )
    if alloc is None:
        pytest.skip("snapshot has no non-deleted allocations")
    return alloc.allocation_id


class TestGetAllocation:
    """GET /api/v1/allocations/<id>."""

    def test_get_allocation_success(self, auth_client, snapshot_allocation_id):
        response = auth_client.get(f'/api/v1/allocations/{snapshot_allocation_id}')
        assert response.status_code == 200

        data = response.get_json()
        # AllocationWithUsageSchema fields — lock the contract that the
        # CLI/balance UI depends on.
        assert data['allocation_id'] == snapshot_allocation_id
        for field in ('allocated', 'used', 'remaining', 'percent_used',
                      'charges_by_type'):
            assert field in data, f"missing {field!r} in serialized allocation"

    def test_get_allocation_not_found(self, auth_client):
        # 999_999_999 is well above any allocation_id in the snapshot.
        response = auth_client.get('/api/v1/allocations/999999999')
        assert response.status_code == 404
        assert 'error' in response.get_json()

    def test_get_allocation_unauthenticated(self, client):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        response = client.get('/api/v1/allocations/1')
        assert response.status_code in (302, 401)


class TestUpdateAllocationFailures:
    """PUT /api/v1/allocations/<id> — failure-mode subset only.

    The success path is covered at the service layer; see module docstring.
    """

    def test_put_unauthenticated(self, client):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        response = client.put('/api/v1/allocations/1', json={'amount': 100.0})
        assert response.status_code in (302, 401)

    def test_put_not_found(self, auth_client):
        response = auth_client.put(
            '/api/v1/allocations/999999999',
            json={'amount': 100.0},
        )
        # require_allocation_permission resolves the allocation first;
        # missing → 404.
        assert response.status_code == 404

    def test_put_empty_body(self, auth_client, snapshot_allocation_id):
        """Missing JSON body → 400 ('Request body must be JSON')."""
        # Send an empty content-type=application/json body. Flask returns
        # data=None, route returns 400 before hitting the schema.
        response = auth_client.put(
            f'/api/v1/allocations/{snapshot_allocation_id}',
            data='',
            content_type='application/json',
        )
        assert response.status_code == 400
        assert 'error' in response.get_json()

    def test_put_invalid_amount(self, auth_client, snapshot_allocation_id):
        """Negative amount fails Range(min=0, min_inclusive=False) → 400."""
        response = auth_client.put(
            f'/api/v1/allocations/{snapshot_allocation_id}',
            json={'amount': -50.0},
        )
        assert response.status_code == 400
        assert 'error' in response.get_json()

    def test_put_no_recognized_fields(self, auth_client, snapshot_allocation_id):
        """A JSON body whose keys are all unknown to the schema is dropped
        by HtmxFormSchema (unknown=EXCLUDE), leaving updates empty → 400."""
        response = auth_client.put(
            f'/api/v1/allocations/{snapshot_allocation_id}',
            json={'unrecognized_field': 'whatever'},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
