"""
Tests for facility management functions:
  update_facility, update_panel, update_panel_session, update_allocation_type
"""

import pytest
from datetime import datetime, timedelta

from sam.resources.facilities import Facility, Panel, PanelSession
from sam.accounting.allocations import AllocationType
from sam.manage.facilities import (
    update_facility,
    update_panel,
    update_panel_session,
    update_allocation_type,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_facility(session) -> Facility:
    f = session.query(Facility).first()
    if not f:
        pytest.skip("No facilities in database")
    return f


def _get_panel(session) -> Panel:
    p = session.query(Panel).first()
    if not p:
        pytest.skip("No panels in database")
    return p


def _get_panel_session(session) -> PanelSession:
    ps = session.query(PanelSession).first()
    if not ps:
        pytest.skip("No panel sessions in database")
    return ps


def _get_allocation_type(session) -> AllocationType:
    at = session.query(AllocationType).first()
    if not at:
        pytest.skip("No allocation types in database")
    return at


# ---------------------------------------------------------------------------
# update_facility
# ---------------------------------------------------------------------------

class TestUpdateFacility:

    def test_update_description(self, session):
        """update_facility sets a new description."""
        f = _get_facility(session)
        original = f.description

        updated = update_facility(session, f.facility_id, description="New description")

        assert updated.description == "New description"
        session.rollback()
        assert f.description == original

    def test_description_empty_string(self, session):
        """Passing an empty string sets description to '' (NOT NULL column)."""
        f = _get_facility(session)

        updated = update_facility(session, f.facility_id, description="")

        assert updated.description == ""
        session.rollback()

    def test_update_fair_share_percentage(self, session):
        """update_facility sets a valid fair_share_percentage."""
        f = _get_facility(session)

        updated = update_facility(session, f.facility_id, fair_share_percentage=33.33)

        assert abs(updated.fair_share_percentage - 33.33) < 0.001
        session.rollback()

    def test_fair_share_zero_valid(self, session):
        """fair_share_percentage=0 is valid."""
        f = _get_facility(session)

        updated = update_facility(session, f.facility_id, fair_share_percentage=0.0)

        assert updated.fair_share_percentage == 0.0
        session.rollback()

    def test_fair_share_hundred_valid(self, session):
        """fair_share_percentage=100 is valid."""
        f = _get_facility(session)

        updated = update_facility(session, f.facility_id, fair_share_percentage=100.0)

        assert updated.fair_share_percentage == 100.0
        session.rollback()

    def test_fair_share_out_of_range_raises(self, session):
        """update_facility raises ValueError when fair_share_percentage > 100."""
        f = _get_facility(session)

        with pytest.raises(ValueError, match="fair_share_percentage must be between 0 and 100"):
            update_facility(session, f.facility_id, fair_share_percentage=101.0)
        session.rollback()

    def test_fair_share_negative_raises(self, session):
        """update_facility raises ValueError for negative fair_share_percentage."""
        f = _get_facility(session)

        with pytest.raises(ValueError, match="fair_share_percentage must be between 0 and 100"):
            update_facility(session, f.facility_id, fair_share_percentage=-1.0)
        session.rollback()

    def test_toggle_active(self, session):
        """update_facility can toggle the active flag."""
        f = _get_facility(session)
        original = f.active

        updated = update_facility(session, f.facility_id, active=not original)

        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update_facility with no keyword args is a no-op."""
        f = _get_facility(session)
        original = f.description

        update_facility(session, f.facility_id)

        assert f.description == original
        session.rollback()

    def test_not_found_raises(self, session):
        """update_facility raises ValueError for a nonexistent facility_id."""
        with pytest.raises(ValueError, match="not found"):
            update_facility(session, facility_id=999999999, description="x")


# ---------------------------------------------------------------------------
# update_panel
# ---------------------------------------------------------------------------

class TestUpdatePanel:

    def test_update_description(self, session):
        """update_panel sets a new description."""
        p = _get_panel(session)
        original = p.description

        updated = update_panel(session, p.panel_id, description="Updated desc")

        assert updated.description == "Updated desc"
        session.rollback()
        assert p.description == original

    def test_clear_description(self, session):
        """Passing an empty string clears description to None (nullable column)."""
        p = _get_panel(session)

        update_panel(session, p.panel_id, description="temp")
        updated = update_panel(session, p.panel_id, description="")

        assert updated.description is None
        session.rollback()

    def test_toggle_active(self, session):
        """update_panel can toggle the active flag."""
        p = _get_panel(session)
        original = p.active

        updated = update_panel(session, p.panel_id, active=not original)

        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update_panel with no keyword args is a no-op."""
        p = _get_panel(session)
        original = p.description

        update_panel(session, p.panel_id)

        assert p.description == original
        session.rollback()

    def test_not_found_raises(self, session):
        """update_panel raises ValueError for a nonexistent panel_id."""
        with pytest.raises(ValueError, match="not found"):
            update_panel(session, panel_id=999999999, description="x")


# ---------------------------------------------------------------------------
# update_panel_session
# ---------------------------------------------------------------------------

class TestUpdatePanelSession:

    def test_update_description(self, session):
        """update_panel_session sets a new description."""
        ps = _get_panel_session(session)
        original = ps.description

        updated = update_panel_session(session, ps.panel_session_id, description="New desc")

        assert updated.description == "New desc"
        session.rollback()
        assert ps.description == original

    def test_clear_description(self, session):
        """Passing empty string clears description to None."""
        ps = _get_panel_session(session)

        update_panel_session(session, ps.panel_session_id, description="temp")
        updated = update_panel_session(session, ps.panel_session_id, description="")

        assert updated.description is None
        session.rollback()

    def test_update_start_date(self, session):
        """update_panel_session sets a new start date."""
        ps = _get_panel_session(session)
        new_start = datetime(2024, 1, 1)

        updated = update_panel_session(session, ps.panel_session_id, start_date=new_start)

        assert updated.start_date == new_start
        session.rollback()

    def test_update_end_date(self, session):
        """update_panel_session sets a valid end date after start date."""
        ps = _get_panel_session(session)
        # Use a far-future date guaranteed to be after any start_date
        future_end = datetime.now() + timedelta(days=3650)

        updated = update_panel_session(session, ps.panel_session_id, end_date=future_end)

        assert updated.end_date == future_end
        session.rollback()

    def test_end_date_before_start_raises(self, session):
        """update_panel_session raises ValueError when end_date <= start_date."""
        ps = session.query(PanelSession).filter(PanelSession.start_date.isnot(None)).first()
        if not ps:
            pytest.skip("No panel sessions with a start_date in database")

        bad_end = ps.start_date - timedelta(days=1)

        with pytest.raises(ValueError, match="end_date must be after start_date"):
            update_panel_session(session, ps.panel_session_id, end_date=bad_end)
        session.rollback()

    def test_update_panel_meeting_date(self, session):
        """update_panel_session sets a panel_meeting_date without constraint."""
        ps = _get_panel_session(session)
        meeting = datetime(2025, 6, 15)

        updated = update_panel_session(session, ps.panel_session_id, panel_meeting_date=meeting)

        assert updated.panel_meeting_date == meeting
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update_panel_session with no keyword args is a no-op."""
        ps = _get_panel_session(session)
        original = ps.description

        update_panel_session(session, ps.panel_session_id)

        assert ps.description == original
        session.rollback()

    def test_not_found_raises(self, session):
        """update_panel_session raises ValueError for a nonexistent ID."""
        with pytest.raises(ValueError, match="not found"):
            update_panel_session(session, panel_session_id=999999999, description="x")


# ---------------------------------------------------------------------------
# update_allocation_type
# ---------------------------------------------------------------------------

class TestUpdateAllocationType:

    def test_update_default_allocation_amount(self, session):
        """update_allocation_type sets a new default_allocation_amount."""
        at = _get_allocation_type(session)

        updated = update_allocation_type(session, at.allocation_type_id, default_allocation_amount=500000.0)

        assert updated.default_allocation_amount == 500000.0
        session.rollback()

    def test_default_amount_zero_valid(self, session):
        """default_allocation_amount=0 is valid."""
        at = _get_allocation_type(session)

        updated = update_allocation_type(session, at.allocation_type_id, default_allocation_amount=0.0)

        assert updated.default_allocation_amount == 0.0
        session.rollback()

    def test_default_amount_negative_raises(self, session):
        """update_allocation_type raises ValueError for negative default_allocation_amount."""
        at = _get_allocation_type(session)

        with pytest.raises(ValueError, match="default_allocation_amount must be >= 0"):
            update_allocation_type(session, at.allocation_type_id, default_allocation_amount=-1.0)
        session.rollback()

    def test_update_fair_share_percentage(self, session):
        """update_allocation_type sets a valid fair_share_percentage."""
        at = _get_allocation_type(session)

        updated = update_allocation_type(session, at.allocation_type_id, fair_share_percentage=25.0)

        assert updated.fair_share_percentage == 25.0
        session.rollback()

    def test_fair_share_out_of_range_raises(self, session):
        """update_allocation_type raises ValueError for fair_share_percentage > 100."""
        at = _get_allocation_type(session)

        with pytest.raises(ValueError, match="fair_share_percentage must be between 0 and 100"):
            update_allocation_type(session, at.allocation_type_id, fair_share_percentage=150.0)
        session.rollback()

    def test_fair_share_negative_raises(self, session):
        """update_allocation_type raises ValueError for negative fair_share_percentage."""
        at = _get_allocation_type(session)

        with pytest.raises(ValueError, match="fair_share_percentage must be between 0 and 100"):
            update_allocation_type(session, at.allocation_type_id, fair_share_percentage=-5.0)
        session.rollback()

    def test_toggle_active(self, session):
        """update_allocation_type can toggle the active flag."""
        at = _get_allocation_type(session)
        original = at.active

        updated = update_allocation_type(session, at.allocation_type_id, active=not original)

        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update_allocation_type with no keyword args is a no-op."""
        at = _get_allocation_type(session)
        original = at.default_allocation_amount

        update_allocation_type(session, at.allocation_type_id)

        assert at.default_allocation_amount == original
        session.rollback()

    def test_not_found_raises(self, session):
        """update_allocation_type raises ValueError for a nonexistent ID."""
        with pytest.raises(ValueError, match="not found"):
            update_allocation_type(session, allocation_type_id=999999999, active=True)
