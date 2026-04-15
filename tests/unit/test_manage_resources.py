"""Tests for Resource / ResourceType / Machine / Queue .update() methods.

Ported from tests/unit/test_manage_resources.py. Same pattern as the
other manage_* ports: `_get_X` helpers replaced with `any_X` fixtures,
post-rollback re-assertions dropped.
"""
from datetime import datetime, timedelta

import pytest

from sam.resources.machines import Queue


pytestmark = pytest.mark.unit


# ============================================================================
# Resource.update()
# ============================================================================


class TestUpdateResource:

    def test_update_description(self, session, any_resource):
        updated = any_resource.update(description="New description")
        assert updated.description == "New description"
        session.rollback()

    def test_clear_description(self, session, any_resource):
        any_resource.update(description="temporary")
        updated = any_resource.update(description="")
        assert updated.description is None
        session.rollback()

    def test_update_commission_date(self, session, any_resource):
        new_date = datetime(2020, 1, 1)
        updated = any_resource.update(commission_date=new_date)
        assert updated.commission_date == new_date
        session.rollback()

    def test_update_decommission_date(self, session, any_resource):
        commission = datetime(2018, 1, 1)
        decommission = datetime(2030, 12, 31)
        updated = any_resource.update(commission_date=commission, decommission_date=decommission)
        # normalize_end_date converts midnight to 23:59:59 per project convention
        assert updated.decommission_date == decommission.replace(hour=23, minute=59, second=59)
        session.rollback()

    def test_decommission_before_commission_raises(self, session, any_resource):
        commission = datetime(2025, 6, 1)
        bad_decommission = datetime(2025, 1, 1)
        with pytest.raises(ValueError, match="decommission_date must be after commission_date"):
            any_resource.update(commission_date=commission, decommission_date=bad_decommission)
        session.rollback()

    def test_update_charging_exempt(self, session, any_resource):
        original = any_resource.charging_exempt
        updated = any_resource.update(charging_exempt=not original)
        assert updated.charging_exempt == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session, any_resource):
        original_desc = any_resource.description
        updated = any_resource.update()
        assert updated.description == original_desc
        session.rollback()


# ============================================================================
# ResourceType.update()
# ============================================================================


class TestUpdateResourceType:

    def test_update_grace_period(self, session, any_resource_type):
        updated = any_resource_type.update(grace_period_days=30)
        assert updated.grace_period_days == 30
        session.rollback()

    def test_update_grace_period_zero(self, session, any_resource_type):
        updated = any_resource_type.update(grace_period_days=0)
        assert updated.grace_period_days == 0
        session.rollback()

    def test_negative_grace_period_raises(self, session, any_resource_type):
        with pytest.raises(ValueError, match="grace_period_days must be >= 0"):
            any_resource_type.update(grace_period_days=-1)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session, any_resource_type):
        original = any_resource_type.grace_period_days
        any_resource_type.update()
        assert any_resource_type.grace_period_days == original
        session.rollback()


# ============================================================================
# Machine.update()
# ============================================================================


class TestUpdateMachine:

    def test_update_description(self, session, any_machine):
        updated = any_machine.update(description="Updated desc")
        assert updated.description == "Updated desc"
        session.rollback()

    def test_clear_description(self, session, any_machine):
        any_machine.update(description="temp")
        updated = any_machine.update(description="")
        assert updated.description is None
        session.rollback()

    def test_update_cpus_per_node(self, session, any_machine):
        updated = any_machine.update(cpus_per_node=128)
        assert updated.cpus_per_node == 128
        session.rollback()

    def test_cpus_per_node_zero_raises(self, session, any_machine):
        with pytest.raises(ValueError, match="cpus_per_node must be a positive integer"):
            any_machine.update(cpus_per_node=0)
        session.rollback()

    def test_cpus_per_node_negative_raises(self, session, any_machine):
        with pytest.raises(ValueError, match="cpus_per_node must be a positive integer"):
            any_machine.update(cpus_per_node=-4)
        session.rollback()

    def test_update_commission_date(self, session, any_machine):
        new_date = datetime(2019, 3, 15)
        updated = any_machine.update(commission_date=new_date)
        assert updated.commission_date == new_date
        session.rollback()

    def test_update_decommission_date(self, session, any_machine):
        commission = datetime(2018, 1, 1)
        decommission = datetime(2028, 1, 1)
        updated = any_machine.update(commission_date=commission, decommission_date=decommission)
        assert updated.decommission_date == decommission.replace(hour=23, minute=59, second=59)
        session.rollback()

    def test_decommission_before_commission_raises(self, session, any_machine):
        with pytest.raises(ValueError, match="decommission_date must be after commission_date"):
            any_machine.update(
                commission_date=datetime(2025, 6, 1),
                decommission_date=datetime(2025, 1, 1),
            )
        session.rollback()

    def test_no_op_when_no_kwargs(self, session, any_machine):
        original_desc = any_machine.description
        any_machine.update()
        assert any_machine.description == original_desc
        session.rollback()


# ============================================================================
# Queue.update()
# ============================================================================


class TestUpdateQueue:

    def test_update_description(self, session, any_queue):
        updated = any_queue.update(description="New queue desc")
        assert updated.description == "New queue desc"
        session.rollback()

    def test_description_empty_string(self, session, any_queue):
        """Passing an empty string sets description to '' (NOT NULL column)."""
        updated = any_queue.update(description="")
        assert updated.description == ""
        session.rollback()

    def test_update_wall_clock_hours_limit(self, session, any_queue):
        updated = any_queue.update(wall_clock_hours_limit=24.0)
        assert updated.wall_clock_hours_limit == 24.0
        session.rollback()

    def test_wall_clock_zero_raises(self, session, any_queue):
        with pytest.raises(ValueError, match="wall_clock_hours_limit must be positive"):
            any_queue.update(wall_clock_hours_limit=0.0)
        session.rollback()

    def test_wall_clock_negative_raises(self, session, any_queue):
        with pytest.raises(ValueError, match="wall_clock_hours_limit must be positive"):
            any_queue.update(wall_clock_hours_limit=-12.0)
        session.rollback()

    def test_update_end_date(self, session, any_queue):
        future_end = datetime.now() + timedelta(days=3650)
        updated = any_queue.update(end_date=future_end)
        assert updated.end_date == future_end
        session.rollback()

    def test_end_date_before_start_raises(self, session):
        """Specifically need a queue with a start_date set."""
        q = session.query(Queue).filter(Queue.start_date.isnot(None)).first()
        if not q:
            pytest.skip("No queues with a start_date in database")
        bad_end = q.start_date - timedelta(days=1)
        with pytest.raises(ValueError, match="end_date must be after start_date"):
            q.update(end_date=bad_end)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session, any_queue):
        original = any_queue.description
        any_queue.update()
        assert any_queue.description == original
        session.rollback()
