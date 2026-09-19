"""HTTP-layer tests for the allocate-down (residual sub-allocation) routes
under Admin > Edit Project > Allocations.

Scope (mirrors test_htmx_facility_resource_override.py): auth, permission,
404, validation re-render, and render smoke against committed snapshot data.
Happy-path DB writes and the full validation matrix are covered at the model
layer in test_allocate_residual.py; the per-node authorization rule is covered
in test_project_permissions.py::TestCanAllocateResidual.

Routes see only committed snapshot rows (Flask-SQLAlchemy's ``db.session`` is
a separate connection from the SAVEPOINT-isolated test ``session``), so these
tests select targets from the snapshot instead of building factory trees.

Endpoints tested:
    GET  /admin/htmx/allocate-down-form/<allocation_id>
    POST /admin/htmx/allocate-down/<allocation_id>
"""
import os

import pytest

from sam.accounting.allocations import Allocation
from sam.manage.allocations import get_carveout_frontier


pytestmark = pytest.mark.unit

_BOGUS = 99_999_999


def _form_url(alloc_id):
    return f'/admin/htmx/allocate-down-form/{alloc_id}'


def _post_url(alloc_id):
    return f'/admin/htmx/allocate-down/{alloc_id}'


def _dedicated_allocations(session, limit=500):
    """Committed snapshot allocations that are standalone and not deleted."""
    return (
        session.query(Allocation)
        .filter(
            Allocation.deleted == False,           # noqa: E712
            Allocation.parent_allocation_id.is_(None),
        )
        .limit(limit)
        .all()
    )


@pytest.fixture
def snapshot_dedicated_alloc(session):
    """Any dedicated snapshot allocation with a valid account/project."""
    for a in _dedicated_allocations(session, limit=50):
        if a.account and a.account.project:
            return a
    pytest.skip("snapshot has no dedicated allocations")


@pytest.fixture
def snapshot_inheriting_alloc(session):
    a = (
        session.query(Allocation)
        .filter(
            Allocation.deleted == False,           # noqa: E712
            Allocation.parent_allocation_id.isnot(None),
        )
        .first()
    )
    if a is None:
        pytest.skip("snapshot has no inheriting allocations")
    return a


@pytest.fixture
def snapshot_carve_parent(session):
    """A snapshot allocation whose direct frontier has carve-out children and
    a positive residual — the real allocate-down surface (e.g. CESM0002).

    Targeted SQL first (current dedicated allocations on active projects with
    active children), then the frontier filter in Python.
    """
    from sqlalchemy import text

    rows = session.execute(text("""
        SELECT al.allocation_id
        FROM project p
        JOIN account a ON a.project_id = p.project_id AND a.deleted IS FALSE
        JOIN allocation al ON al.account_id = a.account_id
            AND al.deleted IS FALSE
            AND al.parent_allocation_id IS NULL
            AND al.start_date <= NOW()
            AND (al.end_date IS NULL OR al.end_date >= NOW())
        WHERE p.active IS TRUE
          AND EXISTS (SELECT 1 FROM project c
                      WHERE c.parent_id = p.project_id AND c.active IS TRUE)
        LIMIT 200
    """)).fetchall()
    for (alloc_id,) in rows:
        a = session.get(Allocation, alloc_id)
        frontier = get_carveout_frontier(session, a)
        if frontier.carve_children and frontier.residual > 0:
            return a, frontier
    pytest.skip("snapshot has no carve-out parent with positive residual")


# ---------------------------------------------------------------------------
# GET form
# ---------------------------------------------------------------------------


class TestAllocateDownFormRoute:

    def test_unauthenticated_redirects_or_401(self, client):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        resp = client.get(_form_url(_BOGUS))
        assert resp.status_code in (302, 401)

    def test_non_steward_denied(self, non_admin_client, session, snapshot_dedicated_alloc):
        resp = non_admin_client.get(_form_url(snapshot_dedicated_alloc.allocation_id))
        # The snapshot non-admin user could coincidentally lead this project
        # or an ancestor; tolerate only the expected outcomes.
        assert resp.status_code in (200, 403)
        if resp.status_code == 200:
            pytest.skip("snapshot non-admin user happens to steward this allocation")

    def test_missing_allocation_404(self, auth_client):
        resp = auth_client.get(_form_url(_BOGUS))
        assert resp.status_code == 404

    def test_dedicated_allocation_renders(self, auth_client, snapshot_dedicated_alloc):
        resp = auth_client.get(_form_url(snapshot_dedicated_alloc.allocation_id))
        assert resp.status_code == 200
        # Either the form (carve parent) or an informational alert (nothing
        # to allocate / over-carved) — both are valid render paths.
        html = resp.get_data(as_text=True)
        assert 'alert' in html or 'Sub-project' in html

    def test_inheriting_allocation_renders_shared_notice(
            self, auth_client, snapshot_inheriting_alloc):
        resp = auth_client.get(_form_url(snapshot_inheriting_alloc.allocation_id))
        assert resp.status_code == 200
        assert 'shared allocation' in resp.get_data(as_text=True).lower()

    def test_carve_parent_renders_form(self, auth_client, snapshot_carve_parent):
        alloc, frontier = snapshot_carve_parent
        resp = auth_client.get(_form_url(alloc.allocation_id))
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Unallocated' in html
        assert 'Sub-project' in html
        assert 'name="target"' in html
        assert 'name="amount"' in html


# ---------------------------------------------------------------------------
# POST
# ---------------------------------------------------------------------------


class TestAllocateDownPostRoute:

    def test_unauthenticated_redirects_or_401(self, client):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip("Auth disabled in dev environment")
        resp = client.post(_post_url(_BOGUS), data={'target': 'alloc:1', 'amount': '1'})
        assert resp.status_code in (302, 401)

    def test_missing_allocation_404(self, auth_client):
        resp = auth_client.post(_post_url(_BOGUS),
                                data={'target': 'alloc:1', 'amount': '1'})
        assert resp.status_code == 404

    def test_invalid_form_re_renders_with_error(
            self, auth_client, snapshot_dedicated_alloc):
        resp = auth_client.post(
            _post_url(snapshot_dedicated_alloc.allocation_id),
            data={'amount': '1'},                   # missing target
        )
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers     # not a success response
        assert 'alert-danger' in resp.get_data(as_text=True)

    def test_forged_target_rejected_no_write(
            self, auth_client, session, snapshot_carve_parent):
        alloc, frontier = snapshot_carve_parent
        resp = auth_client.post(
            _post_url(alloc.allocation_id),
            data={'target': f'alloc:{_BOGUS}', 'amount': '1'},
        )
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers
        assert 'alert-danger' in resp.get_data(as_text=True)
        # Frontier unchanged — nothing was written.
        session.expire_all()
        after = get_carveout_frontier(session, alloc)
        assert after.carve_total == frontier.carve_total

    def test_overdraft_amount_rejected(self, auth_client, snapshot_carve_parent):
        alloc, frontier = snapshot_carve_parent
        target = frontier.carve_children[0]
        resp = auth_client.post(
            _post_url(alloc.allocation_id),
            data={'target': f'alloc:{target.allocation_id}',
                  'amount': str(frontier.residual + 1)},
        )
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers
        assert 'exceeds the unallocated residual' in resp.get_data(as_text=True)
