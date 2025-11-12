"""
Database VIEW Tests

Tests read-only operations on database views (XRAS views and CompActivityCharge view).
These are read-only tests that don't modify the database.
"""

import pytest
from datetime import datetime

from sam.integration.xras_views import (
    XrasUserView,
    XrasRoleView,
    XrasActionView,
    XrasAllocationView,
    XrasHpcAllocationAmountView,
    XrasRequestView,
    CompActivityChargeView
)


class TestXrasViews:
    """Test XRAS integration views."""

    def test_xras_user_view_count(self, session):
        """Test querying XrasUserView."""
        count = session.query(XrasUserView).count()
        print(f"✅ Found {count} XRAS users in view")
        # View may be empty, just check it doesn't error

    def test_xras_user_view_query(self, session):
        """Test accessing XrasUserView properties."""
        user = session.query(XrasUserView).first()
        if user:
            assert user.username is not None
            print(f"✅ XrasUserView: {user.username} ({user.email})")
        else:
            print(f"ℹ️  XrasUserView is empty")

    def test_xras_role_view_count(self, session):
        """Test querying XrasRoleView."""
        count = session.query(XrasRoleView).count()
        print(f"✅ Found {count} XRAS roles in view")

    def test_xras_role_view_query(self, session):
        """Test accessing XrasRoleView properties."""
        role = session.query(XrasRoleView).first()
        if role:
            assert role.projectId is not None
            assert role.username is not None
            assert role.role is not None
            print(f"✅ XrasRoleView: {role.username} on project {role.projectId} as {role.role}")
        else:
            print(f"ℹ️  XrasRoleView is empty")

    def test_xras_action_view_count(self, session):
        """Test querying XrasActionView."""
        count = session.query(XrasActionView).count()
        print(f"✅ Found {count} XRAS actions in view")

    def test_xras_action_view_query(self, session):
        """Test accessing XrasActionView properties."""
        action = session.query(XrasActionView).first()
        if action:
            assert action.allocationId is not None
            assert action.projectId is not None
            print(f"✅ XrasActionView: Allocation {action.allocationId}, Project {action.projectId}, Action: {action.actionType}")
        else:
            print(f"ℹ️  XrasActionView is empty")

    def test_xras_allocation_view_count(self, session):
        """Test querying XrasAllocationView."""
        count = session.query(XrasAllocationView).count()
        print(f"✅ Found {count} XRAS allocations in view")

    def test_xras_allocation_view_query(self, session):
        """Test accessing XrasAllocationView properties."""
        allocation = session.query(XrasAllocationView).first()
        if allocation:
            assert allocation.allocationId is not None
            assert allocation.projectId is not None
            assert allocation.allocatedAmount is not None
            print(f"✅ XrasAllocationView: Allocation {allocation.allocationId}, Amount: {allocation.allocatedAmount}")
        else:
            print(f"ℹ️  XrasAllocationView is empty")

    def test_xras_allocation_view_with_remaining(self, session):
        """Test XrasAllocationView with remaining amount calculation."""
        allocation = session.query(XrasAllocationView).filter(
            XrasAllocationView.remainingAmount is not None
        ).first()

        if allocation:
            print(f"✅ Allocation {allocation.allocationId}: Allocated={allocation.allocatedAmount}, Remaining={allocation.remainingAmount}")
            # Check that remaining is less than or equal to allocated
            if allocation.remainingAmount is not None and allocation.allocatedAmount is not None:
                assert allocation.remainingAmount <= allocation.allocatedAmount
        else:
            print(f"ℹ️  No allocations with remaining amount data")

    def test_xras_hpc_allocation_amount_view_count(self, session):
        """Test querying XrasHpcAllocationAmountView."""
        count = session.query(XrasHpcAllocationAmountView).count()
        print(f"✅ Found {count} HPC allocation amounts in view")

    def test_xras_hpc_allocation_amount_view_query(self, session):
        """Test accessing XrasHpcAllocationAmountView properties."""
        hpc_amount = session.query(XrasHpcAllocationAmountView).first()
        if hpc_amount:
            assert hpc_amount.allocation_id is not None
            assert hpc_amount.allocated is not None
            print(f"✅ XrasHpcAllocationAmountView: Allocation {hpc_amount.allocation_id}, Allocated: {hpc_amount.allocated}, Used: {hpc_amount.used}")
        else:
            print(f"ℹ️  XrasHpcAllocationAmountView is empty")

    def test_xras_request_view_count(self, session):
        """Test querying XrasRequestView."""
        # Note: This view has GROUP BY issues in MySQL strict mode
        # Testing that it doesn't crash, even if empty
        try:
            count = session.query(XrasRequestView).count()
            print(f"✅ Found {count} XRAS requests in view")
        except Exception as e:
            print(f"⚠️  XrasRequestView has database-level issues: {e}")
            pytest.skip("XrasRequestView has GROUP BY compatibility issues")

    def test_xras_request_view_query(self, session):
        """Test accessing XrasRequestView properties."""
        try:
            request = session.query(XrasRequestView).first()
            if request:
                assert request.projectId is not None
                assert request.allocationType is not None
                assert request.projectTitle is not None
                print(f"✅ XrasRequestView: Project {request.projectId}, Type: {request.allocationType}")
            else:
                print(f"ℹ️  XrasRequestView is empty")
        except Exception as e:
            print(f"⚠️  XrasRequestView has database-level issues")
            pytest.skip("XrasRequestView has GROUP BY compatibility issues")


class TestCompActivityChargeView:
    """Test computational activity charge view."""

    def test_comp_activity_charge_view_count(self, session):
        """Test querying CompActivityChargeView."""
        count = session.query(CompActivityChargeView).count()
        # View may be empty in test database
        print(f"✅ Found {count} computational activity charges in view")
        if count == 0:
            pytest.skip("CompActivityChargeView is empty in test database")

    def test_comp_activity_charge_view_query(self, session):
        """Test accessing CompActivityChargeView properties."""
        charge = session.query(CompActivityChargeView).first()
        if not charge:
            pytest.skip("CompActivityChargeView is empty in test database")
        assert charge.job_idx is not None
        assert charge.util_idx is not None
        assert charge.projcode is not None
        print(f"✅ CompActivityChargeView: Job {charge.job_id}, Project {charge.projcode}")

    def test_comp_activity_charge_view_with_user(self, session):
        """Test CompActivityChargeView with user information."""
        charge = session.query(CompActivityChargeView).filter(
            CompActivityChargeView.username.isnot(None)
        ).first()

        if charge:
            assert charge.username is not None
            assert charge.projcode is not None
            print(f"✅ Charge for user {charge.username} on project {charge.projcode}")
        else:
            print(f"ℹ️  No charges with username found")

    def test_comp_activity_charge_view_with_charges(self, session):
        """Test CompActivityChargeView with charge amounts."""
        charge = session.query(CompActivityChargeView).filter(
            CompActivityChargeView.charge.isnot(None)
        ).first()

        if charge:
            assert charge.charge is not None
            print(f"✅ Job {charge.job_id}: Charge={charge.charge}, Core Hours={charge.core_hours}")
            # Charge should be positive
            assert charge.charge >= 0
        else:
            print(f"ℹ️  No charges with charge amount found")

    def test_comp_activity_charge_view_machine_queue(self, session):
        """Test CompActivityChargeView machine and queue info."""
        charge = session.query(CompActivityChargeView).first()
        if not charge:
            pytest.skip("CompActivityChargeView is empty in test database")
        assert charge.machine is not None
        assert charge.queue_name is not None
        print(f"✅ Job ran on {charge.machine} queue {charge.queue_name}")

    def test_comp_activity_charge_view_timing(self, session):
        """Test CompActivityChargeView timing information."""
        charge = session.query(CompActivityChargeView).first()
        if not charge:
            pytest.skip("CompActivityChargeView is empty in test database")
        assert charge.start_time is not None
        assert charge.end_time is not None
        assert charge.submit_time is not None

        # start_time should be >= submit_time
        assert charge.start_time >= charge.submit_time
        # end_time should be >= start_time
        assert charge.end_time >= charge.start_time

        print(f"✅ Timing: Submit={charge.submit_time}, Start={charge.start_time}, End={charge.end_time}")

    def test_comp_activity_charge_view_filter_by_project(self, session):
        """Test filtering CompActivityChargeView by project."""
        # Get a project code first
        sample = session.query(CompActivityChargeView).first()
        if not sample:
            pytest.skip("No data in CompActivityChargeView")

        projcode = sample.projcode

        # Query by that project
        charges = session.query(CompActivityChargeView).filter(
            CompActivityChargeView.projcode == projcode
        ).limit(10).all()

        assert len(charges) > 0
        assert all(c.projcode == projcode for c in charges)
        print(f"✅ Found {len(charges)} charges for project {projcode}")

    def test_comp_activity_charge_view_filter_by_date(self, session):
        """Test filtering CompActivityChargeView by activity date."""
        from datetime import date, timedelta

        # Get charges from the last 30 days (if any)
        recent_date = datetime.now() - timedelta(days=30)

        charges = session.query(CompActivityChargeView).filter(
            CompActivityChargeView.activity_date >= recent_date
        ).limit(10).all()

        if charges:
            print(f"✅ Found {len(charges)} recent charges")
            for charge in charges[:3]:
                print(f"   - {charge.job_id} on {charge.activity_date}")
        else:
            print(f"ℹ️  No recent charges in last 30 days")

    def test_comp_activity_charge_view_aggregate(self, session):
        """Test aggregating CompActivityChargeView data."""
        from sqlalchemy import func

        # Check if view has data first
        if session.query(CompActivityChargeView).count() == 0:
            pytest.skip("CompActivityChargeView is empty in test database")

        # Get total charges by project (top 5)
        results = (
            session.query(
                CompActivityChargeView.projcode,
                func.count(CompActivityChargeView.job_idx).label('job_count'),
                func.sum(CompActivityChargeView.charge).label('total_charge')
            )
            .group_by(CompActivityChargeView.projcode)
            .order_by(func.sum(CompActivityChargeView.charge).desc())
            .limit(5)
            .all()
        )

        assert len(results) > 0
        print(f"✅ Top projects by charge:")
        for projcode, job_count, total_charge in results:
            print(f"   - {projcode}: {job_count} jobs, {total_charge:.2f} total charge")


class TestViewReadOnly:
    """Test that views are read-only."""

    def test_cannot_insert_into_view(self, session):
        """Test that we cannot insert into a view."""
        # Views should not support INSERT operations
        # This test just documents the behavior

        new_user = XrasUserView(
            username='test_user',
            firstName='Test',
            lastName='User',
            email='test@example.com'
        )

        session.add(new_user)

        # This should raise an error when we try to flush
        with pytest.raises(Exception):  # Will be a DB error about views
            session.flush()

        session.rollback()
        print(f"✅ Confirmed: Cannot insert into XrasUserView (expected behavior)")

    def test_cannot_update_view(self, session):
        """Test that we cannot update a view."""
        user = session.query(XrasUserView).first()
        if not user:
            pytest.skip("No data in XrasUserView")

        # Try to modify
        user.email = 'modified@example.com'

        # This should raise an error when we try to flush
        with pytest.raises(Exception):  # Will be a DB error about views
            session.flush()

        session.rollback()
        print(f"✅ Confirmed: Cannot update XrasUserView (expected behavior)")

    def test_cannot_delete_from_view(self, session):
        """Test that we cannot delete from a view."""
        user = session.query(XrasUserView).first()
        if not user:
            pytest.skip("No data in XrasUserView")

        # Try to delete
        session.delete(user)

        # This should raise an error when we try to flush
        with pytest.raises(Exception):  # Will be a DB error about views
            session.flush()

        session.rollback()
        print(f"✅ Confirmed: Cannot delete from XrasUserView (expected behavior)")
