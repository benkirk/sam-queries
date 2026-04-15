"""Tests for the `management_transaction` context manager — Phase 3 port.

Ported from tests/unit/test_transaction_context.py. The legacy file uses
`session_commit` for the multi-operation test to actually persist data and
verify the commit. Under our SAVEPOINT-isolated `session` fixture, a
`session.commit()` inside `management_transaction` is a SAVEPOINT release,
not a real commit — but subsequent queries on the same session still see
the released data until the outer fixture transaction rolls back at
teardown. So we can verify the same semantics without needing a separate
committing fixture.

Project + Account dependencies come from the `active_project` representative
fixture (Layer 1). Fresh User rows for membership manipulation come from
`make_user` (Layer 2). Composing both layers is the intended pattern —
the rule against blending only applies to a single helper that secretly
falls back from one layer to the other.
"""
import pytest
from datetime import datetime

from sam import Account, AccountUser
from sam.manage import add_user_to_project, change_project_admin
from sam.manage.transaction import management_transaction

from factories import make_user

pytestmark = pytest.mark.unit


class TestManagementTransaction:
    """Test management_transaction context manager."""

    def _account_for(self, session, project):
        """Return any non-deleted Account on the given project."""
        return session.query(Account).filter(
            Account.project_id == project.project_id,
            Account.deleted == False,  # noqa: E712 — SQLAlchemy column comparison
        ).first()

    def test_commits_on_success(self, session, active_project):
        """Context manager commits (SAVEPOINT-releases) on successful execution."""
        account = self._account_for(session, active_project)
        if account is None:
            pytest.skip("active_project has no accounts")
        user = make_user(session)

        with management_transaction(session):
            membership = AccountUser(
                account_id=account.account_id,
                user_id=user.user_id,
                start_date=datetime.now(),
            )
            session.add(membership)
            session.flush()

        # After commit() (SAVEPOINT release) the object stays in the
        # identity map — `in session` is True.
        assert membership in session

    def test_rolls_back_on_exception(self, session, active_project):
        """Context manager rolls back when the body raises."""
        account = self._account_for(session, active_project)
        if account is None:
            pytest.skip("active_project has no accounts")
        user = make_user(session)

        initial_count = session.query(AccountUser).count()

        with pytest.raises(ValueError, match="Test error"):
            with management_transaction(session):
                membership = AccountUser(
                    account_id=account.account_id,
                    user_id=user.user_id,
                    start_date=datetime.now(),
                )
                session.add(membership)
                session.flush()
                raise ValueError("Test error")

        # SAVEPOINT rollback drops the new AccountUser.
        assert session.query(AccountUser).count() == initial_count

    def test_propagates_exceptions(self, session):
        """Exceptions raised inside the context manager propagate out unchanged."""
        with pytest.raises(RuntimeError, match="Test exception"):
            with management_transaction(session):
                raise RuntimeError("Test exception")

    def test_multiple_operations(self, session, active_project):
        """Multiple management ops inside one context commit atomically."""
        account = self._account_for(session, active_project)
        if account is None:
            pytest.skip("active_project has no accounts")

        user1 = make_user(session)
        user2 = make_user(session)

        with management_transaction(session):
            add_user_to_project(session, active_project.project_id, user1.user_id)
            add_user_to_project(session, active_project.project_id, user2.user_id)
            change_project_admin(session, active_project.project_id, user1.user_id)

        # After SAVEPOINT release, the in-session state reflects all three ops.
        session.refresh(active_project)
        assert active_project.project_admin_user_id == user1.user_id
        # Both users now show up on at least one of the project's accounts.
        member_ids = {
            au.user_id for au in session.query(AccountUser)
            .join(Account)
            .filter(Account.project_id == active_project.project_id)
            .all()
        }
        assert user1.user_id in member_ids
        assert user2.user_id in member_ids
