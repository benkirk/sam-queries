"""Tests for the operator User/Resource Access remediation functions:
grant_user_resource_access, revoke_user_resource_access, and
reconcile_project_access (sam.manage).

Each test builds a fresh isolated graph (Layer-2 factories) so it does not
depend on snapshot membership:
  - make_project()                     → fresh Project + fresh lead User
  - make_account(project=, resource=)  → Account.create() seeds the lead
  - make_allocation(account=)          → currently-active allocation so the
                                         resource counts as "active" for
                                         get_user_inaccessible_resources()
  - make_user()                        → fresh User, unambiguously off-project
"""
from datetime import datetime, timedelta

import pytest

from sam.accounting.accounts import AccountUser
from sam.manage import (
    add_user_to_project,
    grant_user_resource_access,
    reconcile_project_access,
    revoke_user_resource_access,
)

from factories import make_account, make_allocation, make_project, make_user
from factories import make_resource

pytestmark = pytest.mark.unit


def _membership_rows(session, account_id, user_id):
    return session.query(AccountUser).filter(
        AccountUser.account_id == account_id,
        AccountUser.user_id == user_id,
    ).all()


class TestGrantUserResourceAccess:
    def test_grant_adds_single_membership(self, session):
        project = make_project(session)
        account = make_account(session, project=project)
        user = make_user(session)

        grant_user_resource_access(
            session, project.project_id, user.user_id, account.resource_id
        )

        rows = _membership_rows(session, account.account_id, user.user_id)
        assert len(rows) == 1
        assert rows[0].end_date is None

    def test_grant_is_idempotent(self, session):
        project = make_project(session)
        account = make_account(session, project=project)
        user = make_user(session)

        grant_user_resource_access(
            session, project.project_id, user.user_id, account.resource_id
        )
        grant_user_resource_access(
            session, project.project_id, user.user_id, account.resource_id
        )

        rows = _membership_rows(session, account.account_id, user.user_id)
        assert len(rows) == 1

    def test_grant_raises_when_no_account_for_resource(self, session):
        project = make_project(session)
        make_account(session, project=project)
        user = make_user(session)
        # A resource the project has no account for.
        orphan_resource = make_resource(session)

        with pytest.raises(ValueError, match="no account for resource"):
            grant_user_resource_access(
                session, project.project_id, user.user_id,
                orphan_resource.resource_id,
            )


class TestRevokeUserResourceAccess:
    def test_revoke_removes_membership(self, session):
        project = make_project(session)
        account = make_account(session, project=project)
        user = make_user(session)
        grant_user_resource_access(
            session, project.project_id, user.user_id, account.resource_id
        )
        assert _membership_rows(session, account.account_id, user.user_id)

        revoke_user_resource_access(
            session, project.project_id, user.user_id, account.resource_id
        )

        assert _membership_rows(session, account.account_id, user.user_id) == []

    def test_revoke_lead_raises(self, session):
        project = make_project(session)
        account = make_account(session, project=project)
        lead_id = project.project_lead_user_id

        with pytest.raises(ValueError, match="lead"):
            revoke_user_resource_access(
                session, project.project_id, lead_id, account.resource_id
            )


class TestGetMembersAccessStatus:
    """Tests for Project.get_members_access_status — the shared detector that
    backs the member-list warning, the CLI, and the operator access grid."""

    def _project_two_active_resources(self, session):
        project = make_project(session)
        r1 = make_resource(session)
        r2 = make_resource(session)
        acct1 = make_account(session, project=project, resource=r1)
        acct2 = make_account(session, project=project, resource=r2)
        make_allocation(session, account=acct1)
        make_allocation(session, account=acct2)
        return project, r1, r2, acct1, acct2

    def _add_member(self, session, project):
        member = make_user(session)
        past = datetime.now() - timedelta(days=1)
        add_user_to_project(
            session, project.project_id, member.user_id, start_date=past
        )
        session.expire_all()
        return member

    def _row_for(self, status, user_id):
        return next(r for r in status['members'] if r['user'].user_id == user_id)

    def test_full_access_member(self, session):
        project, r1, r2, _, _ = self._project_two_active_resources(session)
        member = self._add_member(session, project)

        row = self._row_for(project.get_members_access_status(), member.user_id)
        assert row['status'] == 'full'
        assert row['missing'] == []
        assert row['has'] == {r1.resource_name, r2.resource_name}
        assert row['is_lead'] is False

    def test_partial_access_carries_resource_id(self, session):
        project, r1, r2, _, acct2 = self._project_two_active_resources(session)
        member = self._add_member(session, project)

        # Drop the member's access to r2 only (out-of-band partial error).
        bad = session.query(AccountUser).filter_by(
            account_id=acct2.account_id, user_id=member.user_id
        ).one()
        session.delete(bad)
        session.flush()
        session.expire_all()

        row = self._row_for(project.get_members_access_status(), member.user_id)
        assert row['status'] == 'partial'
        assert [m['resource_name'] for m in row['missing']] == [r2.resource_name]
        # The grant loop needs a usable resource_id.
        assert row['missing'][0]['resource_id'] == acct2.resource_id

    def test_no_access_member(self, session):
        # jpereira's real shape: the member still belongs to the project (via
        # a resource with no active allocation) but has lost access to every
        # active-allocation resource. r1 has an active allocation, r2 does not.
        project = make_project(session)
        r1 = make_resource(session)
        r2 = make_resource(session)
        acct1 = make_account(session, project=project, resource=r1)
        make_account(session, project=project, resource=r2)
        make_allocation(session, account=acct1)  # only r1 is active
        member = self._add_member(session, project)

        # Drop the member's r1 (active) access; keep r2 so they remain a member.
        bad = session.query(AccountUser).filter_by(
            account_id=acct1.account_id, user_id=member.user_id
        ).one()
        session.delete(bad)
        session.flush()
        session.expire_all()

        row = self._row_for(project.get_members_access_status(), member.user_id)
        assert row['status'] == 'none'
        assert row['has'] == set()
        assert {m['resource_name'] for m in row['missing']} == {r1.resource_name}

    def test_lead_is_flagged(self, session):
        project, *_ = self._project_two_active_resources(session)
        row = self._row_for(
            project.get_members_access_status(), project.project_lead_user_id
        )
        assert row['is_lead'] is True

    def test_active_only_denominator(self, session):
        # r2's account has no active allocation → excluded when active_only,
        # included when active_only=False.
        project = make_project(session)
        r1 = make_resource(session)
        r2 = make_resource(session)
        make_allocation(session, account=make_account(session, project=project, resource=r1))
        make_account(session, project=project, resource=r2)  # no allocation
        session.expire_all()

        cols_active = {
            c['resource_name']
            for c in project.get_members_access_status(active_only=True)['columns']
        }
        cols_all = {
            c['resource_name']
            for c in project.get_members_access_status(active_only=False)['columns']
        }
        assert r1.resource_name in cols_active
        assert r2.resource_name not in cols_active
        assert {r1.resource_name, r2.resource_name} <= cols_all

    def test_grant_over_missing_restores_full(self, session):
        """End-to-end: the exact loop the grant route runs makes a member whole."""
        project, r1, r2, _, acct2 = self._project_two_active_resources(session)
        member = self._add_member(session, project)
        bad = session.query(AccountUser).filter_by(
            account_id=acct2.account_id, user_id=member.user_id
        ).one()
        session.delete(bad)
        session.flush()
        session.expire_all()

        status = project.get_members_access_status()
        missing = self._row_for(status, member.user_id)['missing']
        for gap in missing:
            grant_user_resource_access(
                session, project.project_id, member.user_id, gap['resource_id']
            )
        session.expire_all()

        row = self._row_for(project.get_members_access_status(), member.user_id)
        assert row['status'] == 'full'
        assert row['missing'] == []


class TestReconcileProjectAccess:
    def test_reconcile_fills_partial_access(self, session):
        project = make_project(session)
        r1 = make_resource(session)
        r2 = make_resource(session)
        acct1 = make_account(session, project=project, resource=r1)
        acct2 = make_account(session, project=project, resource=r2)
        # Active allocations so both resources count as "active".
        make_allocation(session, account=acct1)
        make_allocation(session, account=acct2)

        member = make_user(session)
        # Backdate so is_active is independent of same-second DATETIME rounding.
        past = datetime.now() - timedelta(days=1)
        add_user_to_project(
            session, project.project_id, member.user_id, start_date=past
        )

        # Simulate an out-of-band partial-access error: drop the member's
        # access to r2 only.
        bad = session.query(AccountUser).filter_by(
            account_id=acct2.account_id, user_id=member.user_id
        ).one()
        session.delete(bad)
        session.flush()
        session.expire_all()

        assert project.get_user_inaccessible_resources(member) == {r2.resource_name}

        reconcile_project_access(session, project.project_id)

        # Reconcile re-links the member to r2's account (deterministic).
        assert session.query(AccountUser).filter_by(
            account_id=acct2.account_id, user_id=member.user_id
        ).count() == 1

        # And the grid view agrees: no inaccessible resources remain.
        session.expire_all()
        assert project.get_user_inaccessible_resources(member) == set()
