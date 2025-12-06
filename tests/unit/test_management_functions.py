"""
Unit tests for project management functions.

Tests the ORM-level management functions in sam.manage module
for adding/removing project members and changing admin roles.

These tests use the 'session' fixture which automatically rolls back
all changes, preventing test data from persisting.
"""

import pytest
from datetime import datetime, timedelta
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

from sam.manage import add_user_to_project, remove_user_from_project, change_project_admin
from sam.projects.projects import Project
from sam.accounting.accounts import Account, AccountUser
from sam.core.users import User


class TestAddUserToProject:
    """Tests for add_user_to_project() function."""

    def test_raises_error_for_nonexistent_project(self, session):
        """Raises ValueError if project has no accounts."""
        # Use a project ID that doesn't exist
        with pytest.raises(ValueError, match="No accounts found"):
            add_user_to_project(session, project_id=999999, user_id=1)

    def test_adds_user_to_project_accounts(self, session, test_project, test_user):
        """Adds AccountUser records for each project account."""
        # Find a user who is NOT currently on the project
        member_ids = {
            au.user_id
            for account in test_project.accounts
            for au in account.users  # 'users' is the relationship name
        }
        member_ids.add(test_project.project_lead_user_id)

        # Find another user not in this set
        other_user = session.query(User).filter(
            ~User.user_id.in_(member_ids),
            User.username.isnot(None)
        ).first()

        if not other_user:
            pytest.skip("No available user found to add to project")

        # Count accounts before
        account_count = len([a for a in test_project.accounts if not a.deleted])

        # Add the user
        add_user_to_project(session, test_project.project_id, other_user.user_id)

        # Verify AccountUser records were created
        new_memberships = session.query(AccountUser).filter(
            AccountUser.user_id == other_user.user_id,
            AccountUser.account_id.in_([a.account_id for a in test_project.accounts])
        ).all()

        assert len(new_memberships) == account_count

    def test_start_date_defaults_to_now(self, session, test_project):
        """Start date defaults to current datetime if not provided."""
        # Find a user not on the project
        member_ids = {
            au.user_id
            for account in test_project.accounts
            for au in account.users  # 'users' is the relationship name
        }
        member_ids.add(test_project.project_lead_user_id)

        other_user = session.query(User).filter(
            ~User.user_id.in_(member_ids),
            User.username.isnot(None)
        ).first()

        if not other_user:
            pytest.skip("No available user found to add")

        before = datetime.now()
        add_user_to_project(session, test_project.project_id, other_user.user_id)
        after = datetime.now()

        # Check start_date
        membership = session.query(AccountUser).filter(
            AccountUser.user_id == other_user.user_id,
            AccountUser.account_id.in_([a.account_id for a in test_project.accounts])
        ).first()

        assert membership is not None
        # Allow 2 second tolerance for database/test timing differences
        tolerance = timedelta(seconds=2)
        assert before - tolerance <= membership.start_date <= after + tolerance

    def test_custom_dates_are_used(self, session, test_project):
        """Custom start_date and end_date are applied."""
        member_ids = {
            au.user_id
            for account in test_project.accounts
            for au in account.users  # 'users' is the relationship name
        }
        member_ids.add(test_project.project_lead_user_id)

        other_user = session.query(User).filter(
            ~User.user_id.in_(member_ids),
            User.username.isnot(None)
        ).first()

        if not other_user:
            pytest.skip("No available user found to add")

        start = datetime(2100, 1, 1)
        end = datetime(2100, 12, 31)

        add_user_to_project(
            session,
            test_project.project_id,
            other_user.user_id,
            start_date=start,
            end_date=end
        )

        membership = session.query(AccountUser).filter(
            AccountUser.user_id == other_user.user_id,
            AccountUser.account_id.in_([a.account_id for a in test_project.accounts])
        ).first()

        assert membership.start_date == start
        assert membership.end_date == end

    def test_skips_existing_member(self, session, test_project, test_user):
        """Does not raise error if user is already a member, just skips."""
        # test_user (benkirk) is already on SCSG0001
        # This should not raise an error, just skip existing memberships
        add_user_to_project(session, test_project.project_id, test_user.user_id)

        # If we get here without error, the test passes


class TestRemoveUserFromProject:
    """Tests for remove_user_from_project() function."""

    def test_raises_error_for_nonexistent_project(self, session):
        """Raises ValueError if project doesn't exist."""
        with pytest.raises(ValueError, match="not found"):
            remove_user_from_project(session, project_id=999999, user_id=1)

    def test_cannot_remove_project_lead(self, session, test_project):
        """Raises ValueError when trying to remove project lead."""
        lead_id = test_project.project_lead_user_id

        with pytest.raises(ValueError, match="Cannot remove the project lead"):
            remove_user_from_project(session, test_project.project_id, lead_id)

    def test_removes_user_from_all_accounts(self, session, test_project):
        """Removes AccountUser records from all project accounts."""
        # First find a member who is NOT the lead
        lead_id = test_project.project_lead_user_id
        account_ids = [a.account_id for a in test_project.accounts]

        existing_member = session.query(AccountUser).filter(
            AccountUser.account_id.in_(account_ids),
            AccountUser.user_id != lead_id
        ).first()

        if not existing_member:
            pytest.skip("No non-lead member found to remove")

        user_id = existing_member.user_id

        # Count memberships before
        memberships_before = session.query(AccountUser).filter(
            AccountUser.account_id.in_(account_ids),
            AccountUser.user_id == user_id
        ).count()

        assert memberships_before > 0

        # Remove the user
        remove_user_from_project(session, test_project.project_id, user_id)

        # Verify all memberships are gone
        memberships_after = session.query(AccountUser).filter(
            AccountUser.account_id.in_(account_ids),
            AccountUser.user_id == user_id
        ).count()

        assert memberships_after == 0

    def test_clears_admin_role_when_removing_admin(self, session):
        """Clears project_admin_user_id when removing the admin."""
        # Find a project that has an admin set who is NOT also the lead
        project = session.query(Project).filter(
            Project.project_admin_user_id.isnot(None),
            Project.project_admin_user_id != Project.project_lead_user_id
        ).first()

        if not project:
            # Create the scenario: set an admin on test project
            project = Project.get_by_projcode(session, 'SCSG0001')
            if not project:
                pytest.skip("Test project not found")

            # Find a member who can be admin (must NOT be the lead)
            account_ids = [a.account_id for a in project.accounts]
            member = session.query(AccountUser).filter(
                AccountUser.account_id.in_(account_ids),
                AccountUser.user_id != project.project_lead_user_id
            ).first()

            if not member:
                pytest.skip("No member available to make admin")

            project.project_admin_user_id = member.user_id
            session.flush()

        admin_id = project.project_admin_user_id

        # Sanity check: admin should not be the lead
        assert admin_id != project.project_lead_user_id, "Test setup error: admin is also the lead"

        # Remove the admin from the project
        remove_user_from_project(session, project.project_id, admin_id)

        # Verify admin role was cleared
        session.refresh(project)
        assert project.project_admin_user_id is None


class TestChangeProjectAdmin:
    """Tests for change_project_admin() function."""

    def test_raises_error_for_nonexistent_project(self, session):
        """Raises ValueError if project doesn't exist."""
        with pytest.raises(ValueError, match="not found"):
            change_project_admin(session, project_id=999999, new_admin_user_id=1)

    def test_sets_admin_to_project_member(self, session, test_project):
        """Sets admin to a user who is a project member."""
        # Find a member who is not the lead
        account_ids = [a.account_id for a in test_project.accounts]
        member = session.query(AccountUser).filter(
            AccountUser.account_id.in_(account_ids),
            AccountUser.user_id != test_project.project_lead_user_id
        ).first()

        if not member:
            pytest.skip("No non-lead member found")

        # Change admin
        change_project_admin(session, test_project.project_id, member.user_id)

        # Verify
        session.refresh(test_project)
        assert test_project.project_admin_user_id == member.user_id

    def test_allows_lead_to_be_admin(self, session, test_project):
        """Project lead can be set as admin (even though redundant)."""
        lead_id = test_project.project_lead_user_id

        # This should work - lead is always a valid choice
        change_project_admin(session, test_project.project_id, lead_id)

        session.refresh(test_project)
        assert test_project.project_admin_user_id == lead_id

    def test_clears_admin_with_none(self, session, test_project):
        """Setting admin to None clears the admin role."""
        # First set an admin if not already set
        if not test_project.project_admin_user_id:
            account_ids = [a.account_id for a in test_project.accounts]
            member = session.query(AccountUser).filter(
                AccountUser.account_id.in_(account_ids),
                AccountUser.user_id != test_project.project_lead_user_id
            ).first()

            if member:
                test_project.project_admin_user_id = member.user_id
                session.flush()

        # Clear admin
        change_project_admin(session, test_project.project_id, None)

        session.refresh(test_project)
        assert test_project.project_admin_user_id is None

    def test_raises_error_for_non_member(self, session, test_project):
        """Raises ValueError if user is not a project member."""
        # Find a user who is NOT a member
        account_ids = [a.account_id for a in test_project.accounts]
        member_ids = session.query(AccountUser.user_id).filter(
            AccountUser.account_id.in_(account_ids)
        ).distinct().all()
        member_ids = {u[0] for u in member_ids}
        member_ids.add(test_project.project_lead_user_id)

        non_member = session.query(User).filter(
            ~User.user_id.in_(member_ids),
            User.username.isnot(None)
        ).first()

        if not non_member:
            pytest.skip("No non-member user found")

        with pytest.raises(ValueError, match="must be a project member"):
            change_project_admin(session, test_project.project_id, non_member.user_id)
