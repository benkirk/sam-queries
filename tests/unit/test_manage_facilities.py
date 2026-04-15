"""Tests for Facility / Panel / PanelSession / AllocationType .update() methods.

Ported from tests/unit/test_manage_facilities.py. The legacy file uses
fetch-from-snapshot → mutate-in-place → rollback semantics. Under the
SAVEPOINT-isolated session fixture in new_tests/conftest.py, any
mid-test session.rollback() becomes a SAVEPOINT release and the outer
transaction is still intact for the teardown rollback — so these tests
are safe despite calling .update() on real rows.

Transformations:
- `_get_X(session)` helpers replaced by `any_X` fixtures from conftest.
- Post-rollback re-assertions dropped — the in-session object is detached
  after rollback so the assertion is either a tautology or wrong.
- Mid-test `session.rollback()` calls kept: they trigger the SAVEPOINT
  restart pattern cleanly.
"""
from datetime import datetime, timedelta

import pytest

from sam.accounting.allocations import AllocationType
from sam.resources.facilities import PanelSession


pytestmark = pytest.mark.unit


# ============================================================================
# Facility.update()
# ============================================================================


class TestUpdateFacility:

    def test_update_description(self, session, any_facility):
        updated = any_facility.update(description="New description")
        assert updated.description == "New description"
        session.rollback()

    def test_description_empty_string(self, session, any_facility):
        """Passing an empty string sets description to '' (NOT NULL column)."""
        updated = any_facility.update(description="")
        assert updated.description == ""
        session.rollback()

    def test_update_fair_share_percentage(self, session, any_facility):
        updated = any_facility.update(fair_share_percentage=33.33)
        assert abs(updated.fair_share_percentage - 33.33) < 0.001
        session.rollback()

    def test_fair_share_zero_valid(self, session, any_facility):
        updated = any_facility.update(fair_share_percentage=0.0)
        assert updated.fair_share_percentage == 0.0
        session.rollback()

    def test_fair_share_hundred_valid(self, session, any_facility):
        updated = any_facility.update(fair_share_percentage=100.0)
        assert updated.fair_share_percentage == 100.0
        session.rollback()

    def test_fair_share_out_of_range_raises(self, session, any_facility):
        with pytest.raises(ValueError, match="fair_share_percentage must be between 0 and 100"):
            any_facility.update(fair_share_percentage=101.0)
        session.rollback()

    def test_fair_share_negative_raises(self, session, any_facility):
        with pytest.raises(ValueError, match="fair_share_percentage must be between 0 and 100"):
            any_facility.update(fair_share_percentage=-1.0)
        session.rollback()

    def test_toggle_active(self, session, any_facility):
        original = any_facility.active
        updated = any_facility.update(active=not original)
        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session, any_facility):
        original = any_facility.description
        any_facility.update()
        assert any_facility.description == original
        session.rollback()


# ============================================================================
# Panel.update()
# ============================================================================


class TestUpdatePanel:

    def test_update_description(self, session, any_panel):
        updated = any_panel.update(description="Updated desc")
        assert updated.description == "Updated desc"
        session.rollback()

    def test_clear_description(self, session, any_panel):
        """Passing an empty string clears description to None (nullable column)."""
        any_panel.update(description="temp")
        updated = any_panel.update(description="")
        assert updated.description is None
        session.rollback()

    def test_toggle_active(self, session, any_panel):
        original = any_panel.active
        updated = any_panel.update(active=not original)
        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session, any_panel):
        original = any_panel.description
        any_panel.update()
        assert any_panel.description == original
        session.rollback()


# ============================================================================
# PanelSession.update()
# ============================================================================


class TestUpdatePanelSession:

    def test_update_description(self, session, any_panel_session):
        updated = any_panel_session.update(description="New desc")
        assert updated.description == "New desc"
        session.rollback()

    def test_clear_description(self, session, any_panel_session):
        any_panel_session.update(description="temp")
        updated = any_panel_session.update(description="")
        assert updated.description is None
        session.rollback()

    def test_update_start_date(self, session, any_panel_session):
        new_start = datetime(2024, 1, 1)
        updated = any_panel_session.update(start_date=new_start)
        assert updated.start_date == new_start
        session.rollback()

    def test_update_end_date(self, session, any_panel_session):
        """Setting a valid end_date (far future, unambiguously after start)."""
        future_end = datetime.now() + timedelta(days=3650)
        updated = any_panel_session.update(end_date=future_end)
        assert updated.end_date == future_end
        session.rollback()

    def test_end_date_before_start_raises(self, session):
        """Specifically need a PanelSession with a start_date set."""
        ps = session.query(PanelSession).filter(PanelSession.start_date.isnot(None)).first()
        if not ps:
            pytest.skip("No panel sessions with a start_date in database")
        bad_end = ps.start_date - timedelta(days=1)
        with pytest.raises(ValueError, match="end_date must be after start_date"):
            ps.update(end_date=bad_end)
        session.rollback()

    def test_update_panel_meeting_date(self, session, any_panel_session):
        meeting = datetime(2025, 6, 15)
        updated = any_panel_session.update(panel_meeting_date=meeting)
        assert updated.panel_meeting_date == meeting
        session.rollback()

    def test_no_op_when_no_kwargs(self, session, any_panel_session):
        original = any_panel_session.description
        any_panel_session.update()
        assert any_panel_session.description == original
        session.rollback()


# ============================================================================
# AllocationType.update()
# ============================================================================


class TestUpdateAllocationType:

    def test_update_default_allocation_amount(self, session, any_allocation_type):
        updated = any_allocation_type.update(default_allocation_amount=500_000.0)
        assert updated.default_allocation_amount == 500_000.0
        session.rollback()

    def test_default_amount_zero_valid(self, session, any_allocation_type):
        updated = any_allocation_type.update(default_allocation_amount=0.0)
        assert updated.default_allocation_amount == 0.0
        session.rollback()

    def test_default_amount_negative_raises(self, session, any_allocation_type):
        with pytest.raises(ValueError, match="default_allocation_amount must be >= 0"):
            any_allocation_type.update(default_allocation_amount=-1.0)
        session.rollback()

    def test_update_fair_share_percentage(self, session, any_allocation_type):
        updated = any_allocation_type.update(fair_share_percentage=25.0)
        assert updated.fair_share_percentage == 25.0
        session.rollback()

    def test_fair_share_out_of_range_raises(self, session, any_allocation_type):
        with pytest.raises(ValueError, match="fair_share_percentage must be between 0 and 100"):
            any_allocation_type.update(fair_share_percentage=150.0)
        session.rollback()

    def test_fair_share_negative_raises(self, session, any_allocation_type):
        with pytest.raises(ValueError, match="fair_share_percentage must be between 0 and 100"):
            any_allocation_type.update(fair_share_percentage=-5.0)
        session.rollback()

    def test_toggle_active(self, session, any_allocation_type):
        original = any_allocation_type.active
        updated = any_allocation_type.update(active=not original)
        assert updated.active == (not original)
        session.rollback()

    def test_no_op_when_no_kwargs(self, session, any_allocation_type):
        original = any_allocation_type.default_allocation_amount
        any_allocation_type.update()
        assert any_allocation_type.default_allocation_amount == original
        session.rollback()
