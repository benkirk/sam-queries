"""
Tests for WallclockExemption.create() and .update() class/instance methods.
"""

import pytest
from datetime import datetime

from sam import Queue, User, WallclockExemption


class TestWallclockExemption:
    """Tests for WallclockExemption.create() and .update()."""

    def _get_user_and_queue(self, session):
        """Return a (user, queue) pair suitable for exemption tests."""
        queue = session.query(Queue).first()
        if not queue:
            pytest.skip("No queues in database")
        user = session.query(User).first()
        if not user:
            pytest.skip("No users in database")
        return user, queue

    # ------------------------------------------------------------------
    # WallclockExemption.create()
    # ------------------------------------------------------------------

    def test_create_basic(self, session):
        """create() returns a persisted exemption."""
        user, queue = self._get_user_and_queue(session)
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
        session.rollback()

    def test_create_with_comment(self, session):
        """create() stores an optional comment."""
        user, queue = self._get_user_and_queue(session)
        start = datetime(2025, 1, 1)
        end = datetime(2025, 6, 1)

        ex = WallclockExemption.create(
            session, user.user_id, queue.queue_id, start, end, 24.0,
            comment="Special research run"
        )

        assert ex.comment == "Special research run"
        session.rollback()

    def test_create_invalid_dates(self, session):
        """create() raises ValueError when end <= start."""
        user, queue = self._get_user_and_queue(session)
        start = datetime(2025, 6, 1)
        end = datetime(2025, 1, 1)   # before start

        with pytest.raises(ValueError, match="end_date must be after start_date"):
            WallclockExemption.create(
                session, user.user_id, queue.queue_id, start, end, 48.0
            )

    def test_create_invalid_limit(self, session):
        """create() raises ValueError when time_limit_hours <= 0."""
        user, queue = self._get_user_and_queue(session)
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
        user, queue = self._get_user_and_queue(session)
        start = datetime(2025, 1, 1)
        end = datetime(2025, 6, 1)
        ex = WallclockExemption.create(
            session, user.user_id, queue.queue_id, start, end, 48.0
        )

        new_end = datetime(2025, 12, 31)
        updated = ex.update(end_date=new_end)

        assert updated.end_date == new_end
        assert updated.time_limit_hours == 48.0   # unchanged
        session.rollback()

    def test_update_limit_and_comment(self, session):
        """update() updates limit and comment independently."""
        user, queue = self._get_user_and_queue(session)
        start = datetime(2025, 1, 1)
        end = datetime(2025, 6, 1)
        ex = WallclockExemption.create(
            session, user.user_id, queue.queue_id, start, end, 48.0,
            comment="original"
        )

        updated = ex.update(time_limit_hours=72.0, comment="updated reason")

        assert updated.time_limit_hours == 72.0
        assert updated.comment == "updated reason"
        assert updated.end_date == end   # unchanged
        session.rollback()

    def test_update_clear_comment(self, session):
        """Passing an empty string for comment clears it to None."""
        user, queue = self._get_user_and_queue(session)
        start = datetime(2025, 1, 1)
        end = datetime(2025, 6, 1)
        ex = WallclockExemption.create(
            session, user.user_id, queue.queue_id, start, end, 48.0,
            comment="to be cleared"
        )

        updated = ex.update(comment="")

        assert updated.comment is None
        session.rollback()

    def test_update_invalid_end_date(self, session):
        """update() raises ValueError when end <= start."""
        user, queue = self._get_user_and_queue(session)
        start = datetime(2025, 6, 1)
        end = datetime(2025, 12, 1)
        ex = WallclockExemption.create(
            session, user.user_id, queue.queue_id, start, end, 48.0
        )

        with pytest.raises(ValueError, match="end_date must be after start_date"):
            ex.update(end_date=datetime(2025, 1, 1))   # before start
        session.rollback()
