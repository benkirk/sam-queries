"""Membership removal is a soft delete: rows are end-dated, never-started rows
are deleted, ended rows are history, and every live-path reader drops the
member (docs/plans/ACCOUNT_USER_SOFT_DELETE.md)."""
from datetime import datetime, timedelta

import pytest

import sam.manage as manage_mod
from sam.accounting.accounts import AccountUser
from sam.manage import (
    add_user_to_project,
    change_project_admin,
    remove_user_from_project,
    revoke_user_resource_access,
)
from sam.queries.users import get_users_on_project

from factories import make_account, make_allocation, make_project, make_user


def _rows(session, account, user):
    return session.query(AccountUser).filter_by(
        account_id=account.account_id, user_id=user.user_id
    ).order_by(AccountUser.account_user_id).all()


@pytest.fixture
def two_accounts(session):
    project = make_project(session)
    a1 = make_account(session, project=project)
    a2 = make_account(session, project=project)
    make_allocation(session, account=a1)
    make_allocation(session, account=a2)
    session.expire_all()
    return project, a1, a2


def _frozen_clock(monkeypatch, at: datetime):
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return at
    monkeypatch.setattr(manage_mod, 'datetime', _Frozen)


class TestRemoveUserFromProject:
    def test_started_rows_are_end_dated_just_before_now(self, session, two_accounts):
        project, a1, a2 = two_accounts
        user = make_user(session)
        add_user_to_project(session, project.project_id, user.user_id,
                            start_date=datetime.now() - timedelta(days=2))

        before = datetime.now()
        remove_user_from_project(session, project.project_id, user.user_id)
        after = datetime.now()

        for account in (a1, a2):
            rows = _rows(session, account, user)
            assert len(rows) == 1
            end = rows[0].end_date
            assert end.microsecond == 0
            assert before - timedelta(seconds=2) <= end < after
            assert not rows[0].is_active

    def test_never_started_row_is_deleted(self, session, two_accounts):
        project, a1, _ = two_accounts
        user = make_user(session)
        add_user_to_project(session, project.project_id, user.user_id,
                            start_date=datetime.now() + timedelta(days=1))
        assert len(_rows(session, a1, user)) == 1

        remove_user_from_project(session, project.project_id, user.user_id)

        assert _rows(session, a1, user) == []

    def test_already_ended_rows_are_untouched(self, session, two_accounts):
        project, a1, _ = two_accounts
        user = make_user(session)
        old_end = datetime.now().replace(microsecond=0) - timedelta(days=30)
        add_user_to_project(session, project.project_id, user.user_id,
                            start_date=old_end - timedelta(days=300), end_date=old_end)
        add_user_to_project(session, project.project_id, user.user_id,
                            start_date=datetime.now() - timedelta(days=1))
        assert len(_rows(session, a1, user)) == 2

        remove_user_from_project(session, project.project_id, user.user_id)

        rows = _rows(session, a1, user)
        assert len(rows) == 2
        assert rows[0].end_date == old_end
        assert rows[1].end_date > old_end

    def test_readd_after_removal_opens_a_fresh_row(self, session, two_accounts):
        project, a1, _ = two_accounts
        user = make_user(session)
        add_user_to_project(session, project.project_id, user.user_id,
                            start_date=datetime.now() - timedelta(days=1))
        remove_user_from_project(session, project.project_id, user.user_id)

        add_user_to_project(session, project.project_id, user.user_id)

        rows = _rows(session, a1, user)
        assert len(rows) == 2
        assert rows[0].end_date is not None
        assert rows[1].end_date is None

    def test_midnight_edge_ends_on_the_previous_day(self, session, two_accounts, monkeypatch):
        project, a1, _ = two_accounts
        user = make_user(session)
        add_user_to_project(session, project.project_id, user.user_id,
                            start_date=datetime(2020, 1, 1, 12, 0, 0))

        _frozen_clock(monkeypatch, datetime(2030, 6, 15, 0, 0, 0, 400_000))
        remove_user_from_project(session, project.project_id, user.user_id)

        assert _rows(session, a1, user)[0].end_date == datetime(2030, 6, 14, 23, 59, 59)

    def test_second_after_midnight_does_not_round_to_end_of_today(self, session, two_accounts, monkeypatch):
        project, a1, _ = two_accounts
        user = make_user(session)
        add_user_to_project(session, project.project_id, user.user_id,
                            start_date=datetime(2020, 1, 1, 12, 0, 0))

        # floor(now) - 1 s lands exactly on midnight, which normalize_end_date
        # would turn into 23:59:59 of the SAME day; the primitive steps back again.
        _frozen_clock(monkeypatch, datetime(2030, 6, 15, 0, 0, 1, 400_000))
        remove_user_from_project(session, project.project_id, user.user_id)

        assert _rows(session, a1, user)[0].end_date == datetime(2030, 6, 14, 23, 59, 59)

    def test_removed_member_is_a_former_project(self, session, two_accounts):
        project, *_ = two_accounts
        user = make_user(session)
        add_user_to_project(session, project.project_id, user.user_id,
                            start_date=datetime.now() - timedelta(days=1))
        remove_user_from_project(session, project.project_id, user.user_id)
        session.expire_all()

        assert user.former_projects()['ended'] == [project]
        assert project not in user.active_projects()

    def test_re_render_sources_drop_the_member(self, session, two_accounts):
        """What the members table and the access grid read after the commit."""
        project, *_ = two_accounts
        user = make_user(session)
        add_user_to_project(session, project.project_id, user.user_id,
                            start_date=datetime.now() - timedelta(days=1))
        session.expire_all()
        assert user.username in {u['username'] for u in get_users_on_project(session, project.projcode)}
        assert user in {r['user'] for r in project.get_members_access_status()['members']}

        remove_user_from_project(session, project.project_id, user.user_id)
        session.expire_all()

        assert user.username not in {u['username'] for u in get_users_on_project(session, project.projcode)}
        assert user not in {r['user'] for r in project.get_members_access_status()['members']}


class TestRevokeUserResourceAccess:
    def test_revoke_ends_only_that_accounts_row(self, session, two_accounts):
        project, a1, a2 = two_accounts
        user = make_user(session)
        add_user_to_project(session, project.project_id, user.user_id,
                            start_date=datetime.now() - timedelta(days=1))

        revoke_user_resource_access(session, project.project_id, user.user_id, a1.resource_id)

        (r1,) = _rows(session, a1, user)
        (r2,) = _rows(session, a2, user)
        assert r1.end_date is not None and not r1.is_active
        assert r2.end_date is None and r2.is_active


class TestChangeProjectAdmin:
    def test_ex_member_with_only_ended_rows_is_refused(self, session, two_accounts):
        project, *_ = two_accounts
        user = make_user(session)
        add_user_to_project(session, project.project_id, user.user_id,
                            start_date=datetime.now() - timedelta(days=1))
        remove_user_from_project(session, project.project_id, user.user_id)

        with pytest.raises(ValueError, match="must be a project member"):
            change_project_admin(session, project.project_id, user.user_id)
        assert project.project_admin_user_id != user.user_id

    def test_unended_member_is_accepted(self, session, two_accounts):
        project, *_ = two_accounts
        user = make_user(session)
        add_user_to_project(session, project.project_id, user.user_id)

        change_project_admin(session, project.project_id, user.user_id)

        assert project.project_admin_user_id == user.user_id
