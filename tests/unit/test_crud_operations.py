"""CRUD smoke tests against the ORM — Phase 3 port.

Ported from tests/unit/test_crud_operations.py. The legacy file is mostly
"can I instantiate / mutate / delete this model" smoke checks, useful as
schema-drift detection but with two structural problems:

  1. Every test grabbed the first row from the snapshot via `.query().first()`,
     so xdist workers all touched the same rows and the tests were sensitive
     to whatever the snapshot happened to contain.
  2. Two tests (`test_rollback_prevents_persistence`, `test_flush_vs_commit`)
     opened a second session via the legacy `fixtures.test_config` helpers
     to verify that uncommitted changes never reach disk. Under the
     SAVEPOINT-isolated `session` fixture our entire test architecture
     already enforces this — those tests are now tautological and have
     been dropped.

The port builds fresh isolated graphs per test via factories. A handful
of small-table lookups (PhoneType, ChargeAdjustmentType) still go through
the snapshot since those are reference data.
"""
from datetime import datetime, timedelta

import pytest

from sam.accounting.accounts import Account, AccountUser
from sam.accounting.adjustments import ChargeAdjustment, ChargeAdjustmentType
from sam.accounting.allocations import Allocation
from sam.core.users import EmailAddress, Phone, PhoneType

from factories import make_account, make_allocation, make_user

pytestmark = pytest.mark.unit


class TestCreateOperations:
    """Test creating new records via direct ORM constructors."""

    def test_create_email_address(self, session):
        user = make_user(session)
        new_email = EmailAddress(
            email_address=f"{user.username}@example.test",
            user_id=user.user_id,
            is_primary=False,
            active=True,
        )
        session.add(new_email)
        session.flush()

        assert new_email.email_address_id is not None
        assert new_email.user_id == user.user_id
        assert new_email.user == user

    def test_create_phone(self, session):
        phone_type = session.query(PhoneType).first()
        if phone_type is None:
            pytest.skip("No PhoneType reference rows in snapshot")
        user = make_user(session)

        new_phone = Phone(
            user_id=user.user_id,
            ext_phone_type_id=phone_type.ext_phone_type_id,
            phone_number="555-1234",
        )
        session.add(new_phone)
        session.flush()

        assert new_phone.ext_phone_id is not None
        assert new_phone.user == user
        assert new_phone.phone_type == phone_type

    def test_create_allocation(self, session):
        account = make_account(session)
        now = datetime.now()
        allocation = Allocation(
            account_id=account.account_id,
            amount=10_000.0,
            description="Test allocation",
            start_date=now,
            end_date=now + timedelta(days=365),
            deleted=False,
        )
        session.add(allocation)
        session.flush()

        assert allocation.allocation_id is not None
        assert allocation.account_id == account.account_id
        assert allocation.amount == 10_000.0
        assert allocation.account == account

    def test_create_account_user(self, session):
        account = make_account(session)
        user = make_user(session)  # not the project lead — fresh row
        now = datetime.now()
        au = AccountUser(
            account_id=account.account_id,
            user_id=user.user_id,
            start_date=now,
            end_date=None,
            creation_time=now,
        )
        session.add(au)
        session.flush()

        assert au.account_user_id is not None
        assert au.account == account
        assert au.user == user

    def test_create_charge_adjustment(self, session):
        adj_type = session.query(ChargeAdjustmentType).first()
        if adj_type is None:
            pytest.skip("No ChargeAdjustmentType reference rows in snapshot")
        account = make_account(session)
        adjuster = make_user(session)

        adjustment = ChargeAdjustment(
            account_id=account.account_id,
            adjusted_by_id=adjuster.user_id,
            charge_adjustment_type_id=adj_type.charge_adjustment_type_id,
            amount=-100.0,
            adjustment_date=datetime.now(),
            comment="Test adjustment",
        )
        session.add(adjustment)
        session.flush()

        assert adjustment.charge_adjustment_id is not None
        assert adjustment.amount == -100.0


class TestUpdateOperations:
    """Test updating existing records."""

    def test_update_email_active_status(self, session):
        user = make_user(session)
        email = EmailAddress(
            email_address=f"{user.username}@example.test",
            user_id=user.user_id,
            is_primary=False,
            active=True,
        )
        session.add(email)
        session.flush()

        email.active = False
        session.flush()
        assert email.active is False

    def test_update_allocation_amount(self, session):
        allocation = make_allocation(session, amount=10_000.0)
        allocation.amount = 15_000.0
        session.flush()
        assert allocation.amount == 15_000.0

    def test_update_allocation_dates(self, session):
        allocation = make_allocation(session)
        new_start = datetime(2099, 1, 1)
        new_end = datetime(2099, 7, 1)  # midnight — will be normalized
        allocation.start_date = new_start
        allocation.end_date = new_end
        session.flush()
        assert allocation.start_date == new_start
        # `normalize_end_date` only fires when end_date is exactly midnight,
        # rolling it forward to 23:59:59 of the same day.
        assert allocation.end_date == new_end.replace(hour=23, minute=59, second=59)

    def test_update_user_name(self, session):
        user = make_user(session)
        user.nickname = "Test Nickname"
        session.flush()
        assert user.nickname == "Test Nickname"

    def test_update_account_user_end_date(self, session):
        """Setting end_date deactivates a previously open-ended membership."""
        account = make_account(session)
        member = make_user(session)
        au = AccountUser(
            account_id=account.account_id,
            user_id=member.user_id,
            start_date=datetime.now(),
            end_date=None,
            creation_time=datetime.now(),
        )
        session.add(au)
        session.flush()
        assert au.end_date is None

        new_end = datetime.now()
        au.end_date = new_end
        session.flush()
        assert au.end_date is not None


class TestDeleteOperations:
    """Test soft-delete and hard-delete operations."""

    def test_soft_delete_allocation(self, session):
        allocation = make_allocation(session)
        assert allocation.deleted is False

        allocation.deleted = True
        allocation.deletion_time = datetime.now()
        session.flush()

        # Still in DB, just marked deleted.
        assert session.get(Allocation, allocation.allocation_id) is not None
        assert allocation.deleted is True
        assert allocation.deletion_time is not None
        assert allocation.is_active is False

    def test_delete_email_address(self, session):
        user = make_user(session)
        email = EmailAddress(
            email_address=f"delete-{user.username}@example.test",
            user_id=user.user_id,
            is_primary=False,
            active=True,
        )
        session.add(email)
        session.flush()
        email_id = email.email_address_id
        assert email_id is not None

        session.delete(email)
        session.flush()
        assert session.get(EmailAddress, email_id) is None

    def test_delete_phone(self, session):
        phone_type = session.query(PhoneType).first()
        if phone_type is None:
            pytest.skip("No PhoneType reference rows in snapshot")
        user = make_user(session)
        phone = Phone(
            user_id=user.user_id,
            ext_phone_type_id=phone_type.ext_phone_type_id,
            phone_number="555-DELETE",
        )
        session.add(phone)
        session.flush()
        phone_id = phone.ext_phone_id

        session.delete(phone)
        session.flush()
        assert session.get(Phone, phone_id) is None


class TestComplexCRUD:

    def test_create_allocation_with_parent(self, session):
        account = make_account(session)
        parent = make_allocation(session, account=account, amount=100_000.0)
        child = make_allocation(session, account=account, amount=10_000.0, parent=parent)

        assert child.parent == parent
        assert child in parent.children
        assert child.parent_allocation_id == parent.allocation_id

    def test_bulk_insert_emails(self, session):
        user = make_user(session)
        emails = [
            EmailAddress(
                email_address=f"bulk-{i}-{user.username}@example.test",
                user_id=user.user_id,
                is_primary=False,
                active=True,
            )
            for i in range(5)
        ]
        session.add_all(emails)
        session.flush()
        assert all(e.email_address_id is not None for e in emails)


class TestAccountCreatePropagation:
    """`Account.create()` enforces the lead/admin/sibling propagation invariant.

    The factory `make_account` calls `Account.create()` under the hood, so
    these tests pin the propagation contract directly.
    """

    def test_propagates_lead_and_admin(self, session):
        from factories import make_project, make_resource
        project = make_project(session)
        # Promote a fresh user to admin via the management function.
        from sam.manage import add_user_to_project, change_project_admin
        first_account = make_account(session, project=project)  # propagates lead
        admin = make_user(session)
        add_user_to_project(session, project.project_id, admin.user_id)
        change_project_admin(session, project.project_id, admin.user_id)

        # Now create a SECOND account on the project — both lead AND admin
        # should be propagated onto the new account.
        new_resource = make_resource(session)
        before = datetime.now()
        new_account = Account.create(
            session,
            project_id=project.project_id,
            resource_id=new_resource.resource_id,
        )

        new_aus = session.query(AccountUser).filter_by(
            account_id=new_account.account_id
        ).all()
        propagated_ids = {au.user_id for au in new_aus}

        assert project.project_lead_user_id in propagated_ids
        assert admin.user_id in propagated_ids
        for au in new_aus:
            assert au.end_date is None
            assert au.start_date >= before - timedelta(seconds=5)

    def test_propagates_active_sibling_members(self, session):
        from factories import make_project, make_resource
        from sam.manage import add_user_to_project
        project = make_project(session)
        first_account = make_account(session, project=project)
        sibling_member = make_user(session)
        add_user_to_project(session, project.project_id, sibling_member.user_id)

        new_resource = make_resource(session)
        new_account = Account.create(
            session,
            project_id=project.project_id,
            resource_id=new_resource.resource_id,
        )

        propagated_ids = {
            au.user_id
            for au in session.query(AccountUser).filter_by(
                account_id=new_account.account_id
            ).all()
        }
        assert sibling_member.user_id in propagated_ids

    def test_skips_inactive_sibling_members(self, session):
        """AccountUsers with end_date set are NOT propagated to new accounts."""
        from factories import make_project, make_resource
        from sam.manage import add_user_to_project
        project = make_project(session)
        first_account = make_account(session, project=project)
        expired_member = make_user(session)
        add_user_to_project(session, project.project_id, expired_member.user_id)

        # Expire the sibling membership on every account on the project.
        session.query(AccountUser).filter(
            AccountUser.user_id == expired_member.user_id,
        ).update({AccountUser.end_date: datetime.now() - timedelta(days=1)})
        session.flush()

        new_resource = make_resource(session)
        new_account = Account.create(
            session,
            project_id=project.project_id,
            resource_id=new_resource.resource_id,
        )

        propagated_ids = {
            au.user_id
            for au in session.query(AccountUser).filter_by(
                account_id=new_account.account_id
            ).all()
        }
        assert expired_member.user_id not in propagated_ids
        # Lead is still propagated unconditionally.
        assert project.project_lead_user_id in propagated_ids

    def test_raises_on_missing_project(self, session):
        from sam.resources.resources import Resource
        resource = session.query(Resource).first()
        assert resource is not None

        with pytest.raises(ValueError, match="Project .* not found"):
            Account.create(
                session,
                project_id=999_999_999,
                resource_id=resource.resource_id,
            )
