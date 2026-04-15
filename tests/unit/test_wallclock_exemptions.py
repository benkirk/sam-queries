"""Tests for WallclockExemption.create() and .update() — Phase 3 port.

Ported from tests/unit/test_wallclock_exemptions.py. The legacy file used
`session.query(User|Queue).first()` to grab whatever rows the snapshot
happened to have. Under SAVEPOINT isolation we instead build a fresh User
+ Queue per test via the Layer 2 factories — every row gets rolled back
at teardown, so two tests in the same xdist worker can't bleed state.
"""
from datetime import datetime

import pytest

from sam import WallclockExemption

from factories import make_queue, make_user, make_wallclock_exemption

pytestmark = pytest.mark.unit


class TestWallclockExemption:
    """Tests for WallclockExemption.create() and .update()."""

    # ------------------------------------------------------------------
    # WallclockExemption.create()
    # ------------------------------------------------------------------

    def test_create_basic(self, session):
        """create() returns a persisted exemption."""
        user = make_user(session)
        queue = make_queue(session)
        start = datetime(2025, 1, 1)
        end = datetime(2025, 6, 1)

        ex = WallclockExemption.create(
            session, user.user_id, queue.queue_id, start, end, 48.0
        )

        assert ex.wallclock_exemption_id is not None
        assert ex.user_id == user.user_id
        assert ex.queue_id == queue.queue_id
        assert ex.time_limit_hours == 48.0
        assert ex.comment is None

    def test_create_with_comment(self, session):
        """create() stores an optional comment."""
        user = make_user(session)
        queue = make_queue(session)
        start = datetime(2025, 1, 1)
        end = datetime(2025, 6, 1)

        ex = WallclockExemption.create(
            session, user.user_id, queue.queue_id, start, end, 24.0,
            comment="Special research run",
        )

        assert ex.comment == "Special research run"

    def test_create_invalid_dates(self, session):
        """create() raises ValueError when end <= start."""
        user = make_user(session)
        queue = make_queue(session)
        start = datetime(2025, 6, 1)
        end = datetime(2025, 1, 1)   # before start

        with pytest.raises(ValueError, match="end_date must be after start_date"):
            WallclockExemption.create(
                session, user.user_id, queue.queue_id, start, end, 48.0
            )

    def test_create_invalid_limit(self, session):
        """create() raises ValueError when time_limit_hours <= 0."""
        user = make_user(session)
        queue = make_queue(session)
        start = datetime(2025, 1, 1)
        end = datetime(2025, 6, 1)

        with pytest.raises(ValueError, match="time_limit_hours must be positive"):
            WallclockExemption.create(
                session, user.user_id, queue.queue_id, start, end, -1.0
            )

    # ------------------------------------------------------------------
    # WallclockExemption.update()
    # ------------------------------------------------------------------

    def test_update_end_date(self, session):
        """update() can extend the end date."""
        start = datetime(2025, 1, 1)
        end = datetime(2025, 6, 1)
        ex = make_wallclock_exemption(
            session, start_date=start, end_date=end, time_limit_hours=48.0
        )

        new_end = datetime(2025, 12, 31)
        updated = ex.update(end_date=new_end)

        # normalize_end_date converts midnight to 23:59:59 per project convention
        assert updated.end_date == new_end.replace(hour=23, minute=59, second=59)
        assert updated.time_limit_hours == 48.0   # unchanged

    def test_update_limit_and_comment(self, session):
        """update() updates limit and comment independently."""
        start = datetime(2025, 1, 1)
        end = datetime(2025, 6, 1)
        ex = make_wallclock_exemption(
            session,
            start_date=start,
            end_date=end,
            time_limit_hours=48.0,
            comment="original",
        )

        updated = ex.update(time_limit_hours=72.0, comment="updated reason")

        assert updated.time_limit_hours == 72.0
        assert updated.comment == "updated reason"
        # end_date unchanged but normalized to 23:59:59 by the @validates hook
        assert updated.end_date == end.replace(hour=23, minute=59, second=59)

    def test_update_clear_comment(self, session):
        """Passing an empty string for comment clears it to None."""
        ex = make_wallclock_exemption(session, comment="to be cleared")

        updated = ex.update(comment="")

        assert updated.comment is None

    def test_update_invalid_end_date(self, session):
        """update() raises ValueError when end <= start."""
        start = datetime(2025, 6, 1)
        end = datetime(2025, 12, 1)
        ex = make_wallclock_exemption(
            session, start_date=start, end_date=end, time_limit_hours=48.0
        )

        with pytest.raises(ValueError, match="end_date must be after start_date"):
            ex.update(end_date=datetime(2025, 1, 1))   # before start
