"""Tests for sam.manage user/admin functions — Phase 3 port.

Ported from tests/unit/test_management_functions.py. The legacy file used
`test_user`/`test_project` (benkirk + SCSG0001 from snapshot) plus
hand-rolled "find a non-member" queries against the snapshot. Each of
those couplings was a fragility — snapshot refreshes change membership,
which silently broke "find a user not on this project" lookups.

The port builds a fresh isolated graph for every test:
  - `make_project()` → fresh Project with a fresh lead User
  - `make_account(project=...)` → fresh Account; Account.create() auto-
    propagates the project lead as the first AccountUser
  - `make_user()` → fresh User who is unambiguously NOT on the project
"""
import pytest
from datetime import datetime, timedelta

from sam.accounting.accounts import Account, AccountUser
from sam.manage import add_user_to_project, change_project_admin, remove_user_from_project

from factories import make_account, make_project, make_user

pytestmark = pytest.mark.unit


def _project_with_account(session):
    """Build a fresh project that already has one Account on it.

    `make_account` auto-builds a project if not given one — but we want
    the same project bound to both the returned `project` and the
    `account.project_id` here, so we build the project first.
    """
    project = make_project(session)
    account = make_account(session, project=project)
    return project, account


class TestAddUserToProject:
    """Tests for add_user_to_project()."""

    def test_raises_error_for_nonexistent_project(self, session):
        """Raises ValueError if project has no accounts."""
        # Build a user so the FK is valid even though the project ID won't be.
        user = make_user(session)
        with pytest.raises(ValueError, match="No accounts found"):
            add_user_to_project(session, project_id=999_999_999, user_id=user.user_id)

    def test_adds_user_to_project_accounts(self, session):
        """Adds AccountUser records for each project account."""
        project, _ = _project_with_account(session)
        # Add a second account so we can verify multi-account propagation.
        make_account(session, project=project)
        new_user = make_user(session)

        active_account_count = len([a for a in project.accounts if not a.deleted])
        assert active_account_count == 2

        add_user_to_project(session, project.project_id, new_user.user_id)

        new_memberships = session.query(AccountUser).filter(
            AccountUser.user_id == new_user.user_id,
            AccountUser.account_id.in_([a.account_id for a in project.accounts]),
        ).all()
        assert len(new_memberships) == active_account_count

    def test_start_date_defaults_to_now(self, session):
        """Start date defaults to current datetime if not provided."""
        project, _ = _project_with_account(session)
        new_user = make_user(session)

        before = datetime.now()
        add_user_to_project(session, project.project_id, new_user.user_id)
        after = datetime.now()

        membership = session.query(AccountUser).filter(
            AccountUser.user_id == new_user.user_id,
            AccountUser.account_id.in_([a.account_id for a in project.accounts]),
        ).first()
        assert membership is not None
        tolerance = timedelta(seconds=2)
        assert before - tolerance <= membership.start_date <= after + tolerance

    def test_custom_dates_are_used(self, session):
        """Custom start_date and end_date are applied (end_date normalized)."""
        project, _ = _project_with_account(session)
        new_user = make_user(session)

        start = datetime(2100, 1, 1)
        end = datetime(2100, 12, 31)
        add_user_to_project(
            session,
            project.project_id,
            new_user.user_id,
            start_date=start,
            end_date=end,
        )

        membership = session.query(AccountUser).filter(
            AccountUser.user_id == new_user.user_id,
            AccountUser.account_id.in_([a.account_id for a in project.accounts]),
        ).first()
        assert membership.start_date == start
        # normalize_end_date converts midnight to 23:59:59 per project convention
        assert membership.end_date == end.replace(hour=23, minute=59, second=59)

    def test_skips_existing_member(self, session):
        """Calling twice does not raise — the second call is a no-op."""
        project, account = _project_with_account(session)
        # The project lead is already a member (Account.create() propagated).
        lead_id = project.project_lead_user_id

        # Calling again should silently skip rather than raise.
        add_user_to_project(session, project.project_id, lead_id)

        # Still exactly one AccountUser row for (account, lead).
        count = session.query(AccountUser).filter_by(
            account_id=account.account_id, user_id=lead_id
        ).count()
        assert count == 1


class TestRemoveUserFromProject:
    """Tests for remove_user_from_project()."""

    def test_raises_error_for_nonexistent_project(self, session):
        with pytest.raises(ValueError, match="not found"):
            remove_user_from_project(session, project_id=999_999_999, user_id=1)

    def test_cannot_remove_project_lead(self, session):
        project, _ = _project_with_account(session)
        with pytest.raises(ValueError, match="Cannot remove the project lead"):
            remove_user_from_project(session, project.project_id, project.project_lead_user_id)

    def test_removes_user_from_all_accounts(self, session):
        project, _ = _project_with_account(session)
        make_account(session, project=project)  # second account on same project
        member = make_user(session)
        add_user_to_project(session, project.project_id, member.user_id)

        account_ids = [a.account_id for a in project.accounts]
        before = session.query(AccountUser).filter(
            AccountUser.account_id.in_(account_ids),
            AccountUser.user_id == member.user_id,
        ).count()
        assert before == len(account_ids) > 0

        remove_user_from_project(session, project.project_id, member.user_id)

        after = session.query(AccountUser).filter(
            AccountUser.account_id.in_(account_ids),
            AccountUser.user_id == member.user_id,
        ).count()
        assert after == 0

    def test_clears_admin_role_when_removing_admin(self, session):
        """Removing the admin user from the project clears project_admin_user_id."""
        project, _ = _project_with_account(session)
        admin = make_user(session)
        add_user_to_project(session, project.project_id, admin.user_id)
        change_project_admin(session, project.project_id, admin.user_id)

        session.refresh(project)
        assert project.project_admin_user_id == admin.user_id
        # Sanity: admin must not be the lead, otherwise remove would error.
        assert project.project_admin_user_id != project.project_lead_user_id

        remove_user_from_project(session, project.project_id, admin.user_id)

        session.refresh(project)
        assert project.project_admin_user_id is None


class TestChangeProjectAdmin:
    """Tests for change_project_admin()."""

    def test_raises_error_for_nonexistent_project(self, session):
        with pytest.raises(ValueError, match="not found"):
            change_project_admin(session, project_id=999_999_999, new_admin_user_id=1)

    def test_sets_admin_to_project_member(self, session):
        project, _ = _project_with_account(session)
        member = make_user(session)
        add_user_to_project(session, project.project_id, member.user_id)

        change_project_admin(session, project.project_id, member.user_id)

        session.refresh(project)
        assert project.project_admin_user_id == member.user_id

    def test_allows_lead_to_be_admin(self, session):
        """Project lead is always a valid admin choice (even though redundant)."""
        project, _ = _project_with_account(session)
        change_project_admin(session, project.project_id, project.project_lead_user_id)
        session.refresh(project)
        assert project.project_admin_user_id == project.project_lead_user_id

    def test_clears_admin_with_none(self, session):
        project, _ = _project_with_account(session)
        admin = make_user(session)
        add_user_to_project(session, project.project_id, admin.user_id)
        change_project_admin(session, project.project_id, admin.user_id)
        session.refresh(project)
        assert project.project_admin_user_id == admin.user_id

        change_project_admin(session, project.project_id, None)
        session.refresh(project)
        assert project.project_admin_user_id is None

    def test_raises_error_for_non_member(self, session):
        """Raises ValueError if proposed admin is not a project member."""
        project, _ = _project_with_account(session)
        non_member = make_user(session)  # never added to the project

        with pytest.raises(ValueError, match="must be a project member"):
            change_project_admin(session, project.project_id, non_member.user_id)
