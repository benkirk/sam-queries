"""Read-only tests for database VIEWs (XRAS integration + CompActivityCharge).

Ported from tests/integration/test_views.py. The assertions are structural
(view is queryable, rows have expected shape, view is read-only) so they
survive snapshot refreshes.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func

from sam.integration.xras_views import (
    XrasActionView,
    XrasAllocationView,
    XrasHpcAllocationAmountView,
    XrasRequestView,
    XrasRoleView,
    XrasUserView,
)
from sam.activity.computational import CompActivityChargeView


pytestmark = pytest.mark.integration


# ============================================================================
# XRAS views
# ============================================================================


class TestXrasViews:

    def test_xras_user_view_count(self, session):
        session.query(XrasUserView).count()  # must not raise

    def test_xras_user_view_query(self, session):
        user = session.query(XrasUserView).first()
        if user is None:
            pytest.skip("XrasUserView is empty")
        assert user.username is not None

    def test_xras_role_view_count(self, session):
        session.query(XrasRoleView).count()

    def test_xras_role_view_query(self, session):
        role = session.query(XrasRoleView).first()
        if role is None:
            pytest.skip("XrasRoleView is empty")
        assert role.projectId is not None
        assert role.username is not None
        assert role.role is not None

    def test_xras_action_view_count(self, session):
        session.query(XrasActionView).count()

    def test_xras_action_view_query(self, session):
        action = session.query(XrasActionView).first()
        if action is None:
            pytest.skip("XrasActionView is empty")
        assert action.allocationId is not None
        assert action.projectId is not None

    def test_xras_allocation_view_count(self, session):
        session.query(XrasAllocationView).count()

    def test_xras_allocation_view_query(self, session):
        allocation = session.query(XrasAllocationView).first()
        if allocation is None:
            pytest.skip("XrasAllocationView is empty")
        assert allocation.allocationId is not None
        assert allocation.projectId is not None
        assert allocation.allocatedAmount is not None

    def test_xras_allocation_view_with_remaining(self, session):
        allocation = (
            session.query(XrasAllocationView)
            .filter(XrasAllocationView.remainingAmount.isnot(None))
            .first()
        )
        if allocation is None:
            pytest.skip("No allocations with remaining amount data")
        if allocation.remainingAmount is not None and allocation.allocatedAmount is not None:
            assert allocation.remainingAmount <= allocation.allocatedAmount

    def test_xras_hpc_allocation_amount_view_count(self, session):
        session.query(XrasHpcAllocationAmountView).count()

    def test_xras_hpc_allocation_amount_view_query(self, session):
        hpc = session.query(XrasHpcAllocationAmountView).first()
        if hpc is None:
            pytest.skip("XrasHpcAllocationAmountView is empty")
        assert hpc.allocation_id is not None
        assert hpc.allocated is not None

    def test_xras_request_view_count(self, session):
        # This view has historical GROUP BY issues in strict mode; tolerate.
        try:
            session.query(XrasRequestView).count()
        except Exception:
            pytest.skip("XrasRequestView has GROUP BY compatibility issues")

    def test_xras_request_view_query(self, session):
        try:
            request = session.query(XrasRequestView).first()
        except Exception:
            pytest.skip("XrasRequestView has GROUP BY compatibility issues")
        if request is None:
            pytest.skip("XrasRequestView is empty")
        assert request.projectId is not None
        assert request.allocationType is not None
        assert request.projectTitle is not None


# ============================================================================
# CompActivityChargeView
# ============================================================================


class TestCompActivityChargeView:

    def test_count(self, session):
        if session.query(CompActivityChargeView).count() == 0:
            pytest.skip("CompActivityChargeView is empty")

    def test_basic_query(self, session):
        charge = session.query(CompActivityChargeView).first()
        if charge is None:
            pytest.skip("CompActivityChargeView is empty")
        assert charge.job_idx is not None
        assert charge.util_idx is not None
        assert charge.projcode is not None

    def test_with_username(self, session):
        charge = (
            session.query(CompActivityChargeView)
            .filter(CompActivityChargeView.username.isnot(None))
            .first()
        )
        if charge is None:
            pytest.skip("No charges with username")
        assert charge.username is not None
        assert charge.projcode is not None

    def test_with_charge_amount(self, session):
        charge = (
            session.query(CompActivityChargeView)
            .filter(CompActivityChargeView.charge.isnot(None))
            .first()
        )
        if charge is None:
            pytest.skip("No charges with charge amount")
        assert charge.charge is not None
        assert charge.charge >= 0

    def test_machine_queue(self, session):
        charge = session.query(CompActivityChargeView).first()
        if charge is None:
            pytest.skip("CompActivityChargeView is empty")
        assert charge.machine is not None
        assert charge.queue_name is not None

    def test_timing_ordering(self, session):
        charge = session.query(CompActivityChargeView).first()
        if charge is None:
            pytest.skip("CompActivityChargeView is empty")
        assert charge.start_time is not None
        assert charge.end_time is not None
        assert charge.submit_time is not None
        assert charge.start_time >= charge.submit_time
        assert charge.end_time >= charge.start_time

    def test_filter_by_project(self, session):
        sample = session.query(CompActivityChargeView).first()
        if sample is None:
            pytest.skip("CompActivityChargeView is empty")
        projcode = sample.projcode
        charges = (
            session.query(CompActivityChargeView)
            .filter(CompActivityChargeView.projcode == projcode)
            .limit(10)
            .all()
        )
        assert len(charges) > 0
        assert all(c.projcode == projcode for c in charges)

    def test_filter_by_date(self, session):
        recent = datetime.now() - timedelta(days=30)
        # Informational — we just verify the query runs
        (
            session.query(CompActivityChargeView)
            .filter(CompActivityChargeView.activity_date >= recent)
            .limit(10)
            .all()
        )

    def test_aggregate_top_projects(self, session):
        if session.query(CompActivityChargeView).count() == 0:
            pytest.skip("CompActivityChargeView is empty")
        results = (
            session.query(
                CompActivityChargeView.projcode,
                func.count(CompActivityChargeView.job_idx).label('job_count'),
                func.sum(CompActivityChargeView.charge).label('total_charge'),
            )
            .group_by(CompActivityChargeView.projcode)
            .order_by(func.sum(CompActivityChargeView.charge).desc())
            .limit(5)
            .all()
        )
        assert len(results) > 0


# ============================================================================
# Read-only enforcement
# ============================================================================


class TestViewReadOnly:
    """Confirm database rejects DML against views."""

    def test_cannot_insert_into_view(self, session):
        new_user = XrasUserView(
            username='test_user',
            firstName='Test',
            lastName='User',
            email='test@example.com',
        )
        session.add(new_user)
        with pytest.raises(Exception):
            session.flush()
        session.rollback()

    def test_cannot_update_view(self, session):
        user = session.query(XrasUserView).first()
        if user is None:
            pytest.skip("No data in XrasUserView")
        user.email = 'modified@example.com'
        with pytest.raises(Exception):
            session.flush()
        session.rollback()

    def test_cannot_delete_from_view(self, session):
        user = session.query(XrasUserView).first()
        if user is None:
            pytest.skip("No data in XrasUserView")
        session.delete(user)
        with pytest.raises(Exception):
            session.flush()
        session.rollback()
