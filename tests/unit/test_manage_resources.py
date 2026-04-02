"""
Tests for Resource, ResourceType, Machine, and Queue ORM update() methods.
"""

import pytest
from datetime import datetime, timedelta

from sam.resources.resources import Resource, ResourceType
from sam.resources.machines import Machine, Queue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_resource(session) -> Resource:
    r = session.query(Resource).first()
    if not r:
        pytest.skip("No resources in database")
    return r


def _get_resource_type(session) -> ResourceType:
    rt = session.query(ResourceType).first()
    if not rt:
        pytest.skip("No resource types in database")
    return rt


def _get_machine(session) -> Machine:
    m = session.query(Machine).first()
    if not m:
        pytest.skip("No machines in database")
    return m


def _get_queue(session) -> Queue:
    q = session.query(Queue).first()
    if not q:
        pytest.skip("No queues in database")
    return q


# ---------------------------------------------------------------------------
# Resource.update()
# ---------------------------------------------------------------------------

class TestUpdateResource:

    def test_update_description(self, session):
        """update() sets a new description."""
        r = _get_resource(session)
        original = r.description

        updated = r.update(description="New description")

        assert updated.description == "New description"
        session.rollback()
        assert r.description == original

    def test_clear_description(self, session):
        """Passing an empty string clears description to None."""
        r = _get_resource(session)

        r.update(description="temporary")
        updated = r.update(description="")

        assert updated.description is None
        session.rollback()

    def test_update_commission_date(self, session):
        """update() sets a new commission date."""
        r = _get_resource(session)
        new_date = datetime(2020, 1, 1)

        updated = r.update(commission_date=new_date)

        assert updated.commission_date == new_date
        session.rollback()

    def test_update_decommission_date(self, session):
        """update() sets a decommission date after commission date."""
        r = _get_resource(session)
        commission = datetime(2018, 1, 1)
        decommission = datetime(2030, 12, 31)

        updated = r.update(commission_date=commission, decommission_date=decommission)

        # normalize_end_date converts midnight to 23:59:59 per project convention
        assert updated.decommission_date == decommission.replace(hour=23, minute=59, second=59)
        session.rollback()

    def test_decommission_before_commission_raises(self, session):
        """update() raises ValueError when decommission <= commission."""
        r = _get_resource(session)
        commission = datetime(2025, 6, 1)
        bad_decommission = datetime(2025, 1, 1)

        with pytest.raises(ValueError, match="decommission_date must be after commission_date"):
            r.update(commission_date=commission, decommission_date=bad_decommission)
        session.rollback()

    def test_update_charging_exempt(self, session):
        """update() can toggle charging_exempt."""
        r = _get_resource(session)
        original = r.charging_exempt

        updated = r.update(charging_exempt=not original)

        assert updated.charging_exempt == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update() with no keyword args leaves the record unchanged."""
        r = _get_resource(session)
        original_desc = r.description

        updated = r.update()

        assert updated.description == original_desc
        session.rollback()


# ---------------------------------------------------------------------------
# ResourceType.update()
# ---------------------------------------------------------------------------

class TestUpdateResourceType:

    def test_update_grace_period(self, session):
        """update() sets a new grace_period_days."""
        rt = _get_resource_type(session)
        original = rt.grace_period_days

        updated = rt.update(grace_period_days=30)

        assert updated.grace_period_days == 30
        session.rollback()
        assert rt.grace_period_days == original

    def test_update_grace_period_zero(self, session):
        """grace_period_days=0 is valid."""
        rt = _get_resource_type(session)

        updated = rt.update(grace_period_days=0)

        assert updated.grace_period_days == 0
        session.rollback()

    def test_negative_grace_period_raises(self, session):
        """update() raises ValueError for negative grace_period_days."""
        rt = _get_resource_type(session)

        with pytest.raises(ValueError, match="grace_period_days must be >= 0"):
            rt.update(grace_period_days=-1)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update() with no keyword args is a no-op."""
        rt = _get_resource_type(session)
        original = rt.grace_period_days

        rt.update()

        assert rt.grace_period_days == original
        session.rollback()


# ---------------------------------------------------------------------------
# Machine.update()
# ---------------------------------------------------------------------------

class TestUpdateMachine:

    def test_update_description(self, session):
        """update() sets a new description."""
        m = _get_machine(session)
        original = m.description

        updated = m.update(description="Updated desc")

        assert updated.description == "Updated desc"
        session.rollback()
        assert m.description == original

    def test_clear_description(self, session):
        """Passing an empty string clears description to None."""
        m = _get_machine(session)

        m.update(description="temp")
        updated = m.update(description="")

        assert updated.description is None
        session.rollback()

    def test_update_cpus_per_node(self, session):
        """update() sets a valid cpus_per_node."""
        m = _get_machine(session)

        updated = m.update(cpus_per_node=128)

        assert updated.cpus_per_node == 128
        session.rollback()

    def test_cpus_per_node_zero_raises(self, session):
        """update() raises ValueError for cpus_per_node <= 0."""
        m = _get_machine(session)

        with pytest.raises(ValueError, match="cpus_per_node must be a positive integer"):
            m.update(cpus_per_node=0)
        session.rollback()

    def test_cpus_per_node_negative_raises(self, session):
        """update() raises ValueError for negative cpus_per_node."""
        m = _get_machine(session)

        with pytest.raises(ValueError, match="cpus_per_node must be a positive integer"):
            m.update(cpus_per_node=-4)
        session.rollback()

    def test_update_commission_date(self, session):
        """update() sets a new commission date."""
        m = _get_machine(session)
        new_date = datetime(2019, 3, 15)

        updated = m.update(commission_date=new_date)

        assert updated.commission_date == new_date
        session.rollback()

    def test_update_decommission_date(self, session):
        """update() sets a decommission date after commission date."""
        m = _get_machine(session)
        commission = datetime(2018, 1, 1)
        decommission = datetime(2028, 1, 1)

        updated = m.update(commission_date=commission, decommission_date=decommission)

        # normalize_end_date converts midnight to 23:59:59 per project convention
        assert updated.decommission_date == decommission.replace(hour=23, minute=59, second=59)
        session.rollback()

    def test_decommission_before_commission_raises(self, session):
        """update() raises ValueError when decommission <= commission."""
        m = _get_machine(session)

        with pytest.raises(ValueError, match="decommission_date must be after commission_date"):
            m.update(
                commission_date=datetime(2025, 6, 1),
                decommission_date=datetime(2025, 1, 1),
            )
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update() with no keyword args is a no-op."""
        m = _get_machine(session)
        original_desc = m.description

        m.update()

        assert m.description == original_desc
        session.rollback()


# ---------------------------------------------------------------------------
# Queue.update()
# ---------------------------------------------------------------------------

class TestUpdateQueue:

    def test_update_description(self, session):
        """update() sets a new description."""
        q = _get_queue(session)
        original = q.description

        updated = q.update(description="New queue desc")

        assert updated.description == "New queue desc"
        session.rollback()
        assert q.description == original

    def test_description_empty_string(self, session):
        """Passing an empty string sets description to '' (NOT NULL column)."""
        q = _get_queue(session)

        updated = q.update(description="")

        assert updated.description == ""
        session.rollback()

    def test_update_wall_clock_hours_limit(self, session):
        """update() sets a new wall_clock_hours_limit."""
        q = _get_queue(session)

        updated = q.update(wall_clock_hours_limit=24.0)

        assert updated.wall_clock_hours_limit == 24.0
        session.rollback()

    def test_wall_clock_zero_raises(self, session):
        """update() raises ValueError when wall_clock_hours_limit <= 0."""
        q = _get_queue(session)

        with pytest.raises(ValueError, match="wall_clock_hours_limit must be positive"):
            q.update(wall_clock_hours_limit=0.0)
        session.rollback()

    def test_wall_clock_negative_raises(self, session):
        """update() raises ValueError for negative wall_clock_hours_limit."""
        q = _get_queue(session)

        with pytest.raises(ValueError, match="wall_clock_hours_limit must be positive"):
            q.update(wall_clock_hours_limit=-12.0)
        session.rollback()

    def test_update_end_date(self, session):
        """update() sets a valid end date after start_date."""
        q = _get_queue(session)
        # Use a far-future date guaranteed to be after any start_date
        future_end = datetime.now() + timedelta(days=3650)

        updated = q.update(end_date=future_end)

        assert updated.end_date == future_end
        session.rollback()

    def test_end_date_before_start_raises(self, session):
        """update() raises ValueError when end_date <= start_date."""
        q = session.query(Queue).filter(Queue.start_date.isnot(None)).first()
        if not q:
            pytest.skip("No queues with a start_date in database")

        bad_end = q.start_date - timedelta(days=1)

        with pytest.raises(ValueError, match="end_date must be after start_date"):
            q.update(end_date=bad_end)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update() with no keyword args is a no-op."""
        q = _get_queue(session)
        original = q.description

        q.update()

        assert q.description == original
        session.rollback()
