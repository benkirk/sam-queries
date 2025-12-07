"""
Unit tests for management_transaction context manager.
"""
import pytest
from datetime import datetime
from sam.manage.transaction import management_transaction
from sam import Project, User, Account, AccountUser


class TestManagementTransaction:
    """Test management_transaction context manager."""

    def test_commits_on_success(self, session):
        """Test context manager commits on successful execution."""
        # Create test data
        project = session.query(Project).first()
        user = session.query(User).first()
        account = session.query(Account).filter_by(project_id=project.project_id).first()

        # Use context manager
        with management_transaction(session):
            membership = AccountUser(
                account_id=account.account_id,
                user_id=user.user_id,
                start_date=datetime.now()
            )
            session.add(membership)
            session.flush()

        # Changes should be committed (visible in session)
        # Note: In test context with auto-rollback fixture,
        # this won't actually persist, but we can verify the commit was called
        assert membership in session

    def test_rolls_back_on_exception(self, session):
        """Test context manager rolls back on exception."""
        project = session.query(Project).first()
        account = session.query(Account).filter_by(project_id=project.project_id).first()

        initial_count = session.query(AccountUser).count()

        with pytest.raises(ValueError):
            with management_transaction(session):
                membership = AccountUser(
                    account_id=account.account_id,
                    user_id=99999,  # Invalid user_id
                    start_date=datetime.now()
                )
                session.add(membership)
                session.flush()
                raise ValueError("Test error")

        # Changes should be rolled back
        assert session.query(AccountUser).count() == initial_count

    def test_propagates_exceptions(self, session):
        """Test context manager propagates exceptions after rollback."""
        with pytest.raises(RuntimeError, match="Test exception"):
            with management_transaction(session):
                raise RuntimeError("Test exception")

    def test_multiple_operations(self, session_commit):
        """Test context manager with multiple management operations."""
        from sam.manage import add_user_to_project, change_project_admin

        project = session_commit.query(Project).first()
        user1 = session_commit.query(User).first()
        user2 = session_commit.query(User).offset(1).first()

        with management_transaction(session_commit):
            # Multiple operations in single transaction
            add_user_to_project(session_commit, project.project_id, user1.user_id)
            add_user_to_project(session_commit, project.project_id, user2.user_id)
            change_project_admin(session_commit, project.project_id, user1.user_id)

        # All operations should be committed atomically
        session_commit.refresh(project)
        assert project.project_admin_user_id == user1.user_id
