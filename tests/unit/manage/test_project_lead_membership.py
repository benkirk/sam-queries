"""The lead and admin are live members: ensure_members, Project.update seeding,
change_project_admin, the admin revoke guard, and reconcile's return value."""
from datetime import datetime, timedelta

import pytest

from sam.accounting.accounts import AccountUser
from sam.manage import (
    change_project_admin,
    reconcile_project_access,
    revoke_user_resource_access,
)

from factories import make_account, make_allocation, make_project, make_user


def _rows(session, account, user):
    return session.query(AccountUser).filter_by(
        account_id=account.account_id, user_id=user.user_id).all()


def _live(session, account, user):
    now = datetime.now()
    return [au for au in _rows(session, account, user)
            if au.end_date is None or au.end_date > now]


def _expire(session, account, user):
    for au in _rows(session, account, user):
        au.end_date = datetime.now() - timedelta(days=1)
    session.flush()


@pytest.fixture
def two_accounts(session):
    project = make_project(session)
    a1 = make_account(session, project=project)
    a2 = make_account(session, project=project)
    make_allocation(session, account=a1)
    make_allocation(session, account=a2)
    session.expire_all()
    return project, a1, a2


class TestEnsureMembers:
    def test_adds_a_row_on_each_live_account(self, session, two_accounts):
        project, a1, a2 = two_accounts
        user = make_user(session)
        added = project.ensure_members(user.user_id)
        assert len(added) == 2
        assert len(_live(session, a1, user)) == 1
        assert len(_live(session, a2, user)) == 1
        assert all(au.end_date is None and au.start_date.microsecond == 0 for au in added)

    def test_skips_an_existing_live_row(self, session, two_accounts):
        project, a1, a2 = two_accounts
        assert project.ensure_members(project.project_lead_user_id) == []
        assert len(_rows(session, a1, project.lead)) == 1

    def test_readds_after_an_expired_row(self, session, two_accounts):
        project, a1, a2 = two_accounts
        _expire(session, a1, project.lead)
        added = project.ensure_members(project.project_lead_user_id)
        assert [au.account_id for au in added] == [a1.account_id]
        assert len(_live(session, a1, project.lead)) == 1

    def test_ignores_a_deleted_account(self, session, two_accounts):
        project, a1, a2 = two_accounts
        a2.deleted = True
        session.flush()
        user = make_user(session)
        added = project.ensure_members(user.user_id)
        assert [au.account_id for au in added] == [a1.account_id]
        assert _rows(session, a2, user) == []


@pytest.mark.parametrize('field', ['project_lead_user_id', 'project_admin_user_id'])
class TestUpdateSeedsLeadAndAdmin:
    def test_seeds_the_new_user(self, session, two_accounts, field):
        project, a1, a2 = two_accounts
        user = make_user(session)
        project.update(**{field: user.user_id})
        assert len(_live(session, a1, user)) == 1
        assert len(_live(session, a2, user)) == 1

    def test_old_holder_keeps_their_rows(self, session, two_accounts, field):
        project, a1, a2 = two_accounts
        old = make_user(session)
        project.update(**{field: old.user_id})
        project.update(**{field: make_user(session).user_id})
        assert len(_live(session, a1, old)) == 1
        assert len(_live(session, a2, old)) == 1

    def test_unchanged_value_writes_nothing(self, session, two_accounts, field):
        project, a1, a2 = two_accounts
        user = make_user(session)
        project.update(**{field: user.user_id})
        _expire(session, a1, user)
        project.update(**{field: user.user_id})
        assert _live(session, a1, user) == []


class TestChangeProjectAdmin:
    def test_former_member_with_only_expired_rows_gets_live_rows(self, session, two_accounts):
        project, a1, a2 = two_accounts
        user = make_user(session)
        project.ensure_members(user.user_id)
        _expire(session, a1, user)
        _expire(session, a2, user)
        change_project_admin(session, project.project_id, user.user_id)
        assert project.project_admin_user_id == user.user_id
        assert len(_live(session, a1, user)) == 1
        assert len(_live(session, a2, user)) == 1

    def test_clearing_to_none(self, session, two_accounts):
        project, *_ = two_accounts
        user = make_user(session)
        change_project_admin(session, project.project_id, project.project_lead_user_id)
        project.update(project_admin_user_id=user.user_id)
        change_project_admin(session, project.project_id, None)
        assert project.project_admin_user_id is None


def test_revoke_refuses_the_admin(session, two_accounts):
    project, a1, _ = two_accounts
    admin = make_user(session)
    project.update(project_admin_user_id=admin.user_id)
    with pytest.raises(ValueError, match="project admin"):
        revoke_user_resource_access(session, project.project_id, admin.user_id, a1.resource_id)
    assert len(_live(session, a1, admin)) == 1


def test_reconcile_returns_the_rows_it_added(session, two_accounts):
    project, a1, a2 = two_accounts
    _expire(session, a2, project.lead)
    added = reconcile_project_access(session, project.project_id)
    assert [(au.account_id, au.user_id) for au in added] == [
        (a2.account_id, project.project_lead_user_id)]
    assert reconcile_project_access(session, project.project_id) == []


class TestActiveResourcesOnly:
    """Decommissioned resources are left alone; ``account.deleted`` is never set in practice."""

    def _retire(self, session, account):
        account.resource.decommission_date = datetime.now() - timedelta(days=30)
        session.flush()
        session.expire_all()

    def test_ensure_members_skips_a_decommissioned_resource(self, session, two_accounts):
        project, a1, a2 = two_accounts
        self._retire(session, a2)
        user = make_user(session)
        added = project.ensure_members(user.user_id)
        assert [au.account_id for au in added] == [a1.account_id]
        assert _rows(session, a2, user) == []

    def test_reconcile_skips_a_decommissioned_resource(self, session, two_accounts):
        project, a1, a2 = two_accounts
        _expire(session, a1, project.lead)
        _expire(session, a2, project.lead)
        self._retire(session, a2)
        added = reconcile_project_access(session, project.project_id)
        assert [au.account_id for au in added] == [a1.account_id]

    def test_lead_admin_only_leaves_other_members_alone(self, session, two_accounts):
        project, a1, a2 = two_accounts
        member = make_user(session)
        project.ensure_members(member.user_id)
        _expire(session, a2, member)
        _expire(session, a2, project.lead)
        added = reconcile_project_access(session, project.project_id, lead_admin_only=True)
        assert [(au.account_id, au.user_id) for au in added] == [
            (a2.account_id, project.project_lead_user_id)]


@pytest.mark.parametrize('lead_admin_only', [False, True])
def test_reconcile_skips_an_inactive_user(session, two_accounts, lead_admin_only):
    project, a1, a2 = two_accounts
    retired = make_user(session, active=False)
    project.update(project_admin_user_id=retired.user_id)  # an explicit assignment still seeds
    _expire(session, a1, retired)
    _expire(session, a1, project.lead)
    added = reconcile_project_access(session, project.project_id, lead_admin_only=lead_admin_only)
    assert [(au.account_id, au.user_id) for au in added] == [
        (a1.account_id, project.project_lead_user_id)]
    assert _live(session, a1, retired) == []
