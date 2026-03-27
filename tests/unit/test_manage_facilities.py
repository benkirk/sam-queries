"""
Tests for Facility, Panel, PanelSession, and AllocationType ORM update() methods.
"""

import pytest
from datetime import datetime, timedelta

from sam.resources.facilities import Facility, Panel, PanelSession
from sam.accounting.allocations import AllocationType


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
# Facility.update()
# ---------------------------------------------------------------------------

class TestUpdateFacility:

    def test_update_description(self, session):
        """update() sets a new description."""
        f = _get_facility(session)
        original = f.description

        updated = f.update(description="New description")

        assert updated.description == "New description"
        session.rollback()
        assert f.description == original

    def test_description_empty_string(self, session):
        """Passing an empty string sets description to '' (NOT NULL column)."""
        f = _get_facility(session)

        updated = f.update(description="")

        assert updated.description == ""
        session.rollback()

    def test_update_fair_share_percentage(self, session):
        """update() sets a valid fair_share_percentage."""
        f = _get_facility(session)

        updated = f.update(fair_share_percentage=33.33)

        assert abs(updated.fair_share_percentage - 33.33) < 0.001
        session.rollback()

    def test_fair_share_zero_valid(self, session):
        """fair_share_percentage=0 is valid."""
        f = _get_facility(session)

        updated = f.update(fair_share_percentage=0.0)

        assert updated.fair_share_percentage == 0.0
        session.rollback()

    def test_fair_share_hundred_valid(self, session):
        """fair_share_percentage=100 is valid."""
        f = _get_facility(session)

        updated = f.update(fair_share_percentage=100.0)

        assert updated.fair_share_percentage == 100.0
        session.rollback()

    def test_fair_share_out_of_range_raises(self, session):
        """update() raises ValueError when fair_share_percentage > 100."""
        f = _get_facility(session)

        with pytest.raises(ValueError, match="fair_share_percentage must be between 0 and 100"):
            f.update(fair_share_percentage=101.0)
        session.rollback()

    def test_fair_share_negative_raises(self, session):
        """update() raises ValueError for negative fair_share_percentage."""
        f = _get_facility(session)

        with pytest.raises(ValueError, match="fair_share_percentage must be between 0 and 100"):
            f.update(fair_share_percentage=-1.0)
        session.rollback()

    def test_toggle_active(self, session):
        """update() can toggle the active flag."""
        f = _get_facility(session)
        original = f.active

        updated = f.update(active=not original)

        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update() with no keyword args is a no-op."""
        f = _get_facility(session)
        original = f.description

        f.update()

        assert f.description == original
        session.rollback()


# ---------------------------------------------------------------------------
# Panel.update()
# ---------------------------------------------------------------------------

class TestUpdatePanel:

    def test_update_description(self, session):
        """update() sets a new description."""
        p = _get_panel(session)
        original = p.description

        updated = p.update(description="Updated desc")

        assert updated.description == "Updated desc"
        session.rollback()
        assert p.description == original

    def test_clear_description(self, session):
        """Passing an empty string clears description to None (nullable column)."""
        p = _get_panel(session)

        p.update(description="temp")
        updated = p.update(description="")

        assert updated.description is None
        session.rollback()

    def test_toggle_active(self, session):
        """update() can toggle the active flag."""
        p = _get_panel(session)
        original = p.active

        updated = p.update(active=not original)

        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update() with no keyword args is a no-op."""
        p = _get_panel(session)
        original = p.description

        p.update()

        assert p.description == original
        session.rollback()


# ---------------------------------------------------------------------------
# PanelSession.update()
# ---------------------------------------------------------------------------

class TestUpdatePanelSession:

    def test_update_description(self, session):
        """update() sets a new description."""
        ps = _get_panel_session(session)
        original = ps.description

        updated = ps.update(description="New desc")

        assert updated.description == "New desc"
        session.rollback()
        assert ps.description == original

    def test_clear_description(self, session):
        """Passing empty string clears description to None."""
        ps = _get_panel_session(session)

        ps.update(description="temp")
        updated = ps.update(description="")

        assert updated.description is None
        session.rollback()

    def test_update_start_date(self, session):
        """update() sets a new start date."""
        ps = _get_panel_session(session)
        new_start = datetime(2024, 1, 1)

        updated = ps.update(start_date=new_start)

        assert updated.start_date == new_start
        session.rollback()

    def test_update_end_date(self, session):
        """update() sets a valid end date after start date."""
        ps = _get_panel_session(session)
        # Use a far-future date guaranteed to be after any start_date
        future_end = datetime.now() + timedelta(days=3650)

        updated = ps.update(end_date=future_end)

        assert updated.end_date == future_end
        session.rollback()

    def test_end_date_before_start_raises(self, session):
        """update() raises ValueError when end_date <= start_date."""
        ps = session.query(PanelSession).filter(PanelSession.start_date.isnot(None)).first()
        if not ps:
            pytest.skip("No panel sessions with a start_date in database")

        bad_end = ps.start_date - timedelta(days=1)

        with pytest.raises(ValueError, match="end_date must be after start_date"):
            ps.update(end_date=bad_end)
        session.rollback()

    def test_update_panel_meeting_date(self, session):
        """update() sets a panel_meeting_date without constraint."""
        ps = _get_panel_session(session)
        meeting = datetime(2025, 6, 15)

        updated = ps.update(panel_meeting_date=meeting)

        assert updated.panel_meeting_date == meeting
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update() with no keyword args is a no-op."""
        ps = _get_panel_session(session)
        original = ps.description

        ps.update()

        assert ps.description == original
        session.rollback()


# ---------------------------------------------------------------------------
# AllocationType.update()
# ---------------------------------------------------------------------------

class TestUpdateAllocationType:

    def test_update_default_allocation_amount(self, session):
        """update() sets a new default_allocation_amount."""
        at = _get_allocation_type(session)

        updated = at.update(default_allocation_amount=500000.0)

        assert updated.default_allocation_amount == 500000.0
        session.rollback()

    def test_default_amount_zero_valid(self, session):
        """default_allocation_amount=0 is valid."""
        at = _get_allocation_type(session)

        updated = at.update(default_allocation_amount=0.0)

        assert updated.default_allocation_amount == 0.0
        session.rollback()

    def test_default_amount_negative_raises(self, session):
        """update() raises ValueError for negative default_allocation_amount."""
        at = _get_allocation_type(session)

        with pytest.raises(ValueError, match="default_allocation_amount must be >= 0"):
            at.update(default_allocation_amount=-1.0)
        session.rollback()

    def test_update_fair_share_percentage(self, session):
        """update() sets a valid fair_share_percentage."""
        at = _get_allocation_type(session)

        updated = at.update(fair_share_percentage=25.0)

        assert updated.fair_share_percentage == 25.0
        session.rollback()

    def test_fair_share_out_of_range_raises(self, session):
        """update() raises ValueError for fair_share_percentage > 100."""
        at = _get_allocation_type(session)

        with pytest.raises(ValueError, match="fair_share_percentage must be between 0 and 100"):
            at.update(fair_share_percentage=150.0)
        session.rollback()

    def test_fair_share_negative_raises(self, session):
        """update() raises ValueError for negative fair_share_percentage."""
        at = _get_allocation_type(session)

        with pytest.raises(ValueError, match="fair_share_percentage must be between 0 and 100"):
            at.update(fair_share_percentage=-5.0)
        session.rollback()

    def test_toggle_active(self, session):
        """update() can toggle the active flag."""
        at = _get_allocation_type(session)
        original = at.active

        updated = at.update(active=not original)

        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session):
        """Calling update() with no keyword args is a no-op."""
        at = _get_allocation_type(session)
        original = at.default_allocation_amount

        at.update()

        assert at.default_allocation_amount == original
        session.rollback()
