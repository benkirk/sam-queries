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
