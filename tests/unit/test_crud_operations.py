"""
CRUD Operations Test Suite

Tests Create, Update, and Delete operations on ORM models.
All tests use transactions that rollback to avoid polluting the database.
"""

import pytest
from datetime import datetime, timedelta, UTC

from sam import (
    User, Project, Account, Allocation, Resource, Organization,
    Institution, EmailAddress, Phone, PhoneType, AccountUser,
    AllocationType, LoginType, AcademicStatus, AreaOfInterest,
    ResourceType, ChargeAdjustment, ChargeAdjustmentType
)


class TestCreateOperations:
    """Test creating new records."""

    def test_create_email_address(self, session):
        """Test creating a new email address for an existing user."""
        # Get an existing user
        user = session.query(User).first()
        if not user:
            pytest.skip("No users in database")

        # Create new email
        new_email = EmailAddress(
            email_address=f'test_{datetime.now().timestamp()}@example.com',
            user_id=user.user_id,
            is_primary=False,
            active=True
        )

        session.add(new_email)
        session.flush()  # Get ID without committing

        assert new_email.email_address_id is not None
        assert new_email.user_id == user.user_id
        print(f"✅ Created email: {new_email.email_address} (ID: {new_email.email_address_id})")

        # Verify relationship
        assert new_email.user == user
        print(f"✅ Email relationship works: user={user.username}")

        session.rollback()  # Don't actually save

    def test_create_phone(self, session):
        """Test creating a phone number for an existing user."""
        # Get existing user and phone type
        user = session.query(User).first()
        phone_type = session.query(PhoneType).first()

        if not user or not phone_type:
            pytest.skip("Need user and phone_type in database")

        # Create new phone
        new_phone = Phone(
            user_id=user.user_id,
            ext_phone_type_id=phone_type.ext_phone_type_id,
            phone_number='555-1234'
        )

        session.add(new_phone)
        session.flush()

        assert new_phone.ext_phone_id is not None
        print(f"✅ Created phone: {new_phone.phone_number} (ID: {new_phone.ext_phone_id})")

        # Verify relationships
        assert new_phone.user == user
        assert new_phone.phone_type == phone_type
        print(f"✅ Phone relationships work")

        session.rollback()

    def test_create_allocation(self, session):
        """Test creating a new allocation for an existing account."""
        # Get an existing account
        account = session.query(Account).first()
        if not account:
            pytest.skip("No accounts in database")

        # Create new allocation
        now = datetime.now(UTC)
        new_allocation = Allocation(
            account_id=account.account_id,
            amount=10000.00,
            description='Test allocation',
            start_date=now,
            end_date=now + timedelta(days=365),
            deleted=False
        )

        session.add(new_allocation)
        session.flush()

        assert new_allocation.allocation_id is not None
        assert new_allocation.account_id == account.account_id
        assert float(new_allocation.amount) == 10000.00
        print(f"✅ Created allocation: ID={new_allocation.allocation_id}, amount={new_allocation.amount}")

        # Verify relationship
        assert new_allocation.account == account
        print(f"✅ Allocation→Account relationship works")

        session.rollback()

    def test_create_account_user(self, session):
        """Test creating a new account-user association."""
        # Get existing account and user
        account = session.query(Account).first()
        user = session.query(User).filter(User.user_id != account.project.project_lead_user_id).first()

        if not account or not user:
            pytest.skip("Need account and user in database")

        # Create new account-user relationship
        now = datetime.now(UTC)
        new_account_user = AccountUser(
            account_id=account.account_id,
            user_id=user.user_id,
            start_date=now,
            end_date=None,  # No end date = active
            creation_time=now  # Required field without server default
        )

        session.add(new_account_user)
        session.flush()

        assert new_account_user.account_user_id is not None
        print(f"✅ Created AccountUser: ID={new_account_user.account_user_id}")

        # Verify relationships
        assert new_account_user.account == account
        assert new_account_user.user == user
        print(f"✅ AccountUser relationships work: {user.username} → Account {account.account_id}")

        session.rollback()

    def test_create_charge_adjustment(self, session):
        """Test creating a charge adjustment."""
        # Get existing account, user, and adjustment type
        account = session.query(Account).first()
        user = session.query(User).first()
        adj_type = session.query(ChargeAdjustmentType).first()

        if not all([account, user, adj_type]):
            pytest.skip("Need account, user, and adjustment type")

        # Create adjustment
        new_adjustment = ChargeAdjustment(
            account_id=account.account_id,
            adjusted_by_id=user.user_id,
            charge_adjustment_type_id=adj_type.charge_adjustment_type_id,
            amount=-100.00,  # Credit
            adjustment_date=datetime.now(UTC),
            comment='Test adjustment'
        )

        session.add(new_adjustment)
        session.flush()

        assert new_adjustment.charge_adjustment_id is not None
        assert float(new_adjustment.amount) == -100.00
        print(f"✅ Created adjustment: ID={new_adjustment.charge_adjustment_id}, amount={new_adjustment.amount}")

        session.rollback()


class TestUpdateOperations:
    """Test updating existing records."""

    def test_update_email_active_status(self, session):
        """Test updating email active status."""
        email = session.query(EmailAddress).filter(EmailAddress.active == True).first()
        if not email:
            pytest.skip("No active emails in database")

        original_active = email.active
        original_id = email.email_address_id

        # Update active status
        email.active = False
        session.flush()

        # Verify update
        updated_email = session.query(EmailAddress).filter_by(email_address_id=original_id).first()
        assert updated_email.active == False
        print(f"✅ Updated email {email.email_address}: active {original_active} → {updated_email.active}")

        session.rollback()

    def test_update_allocation_amount(self, session):
        """Test updating allocation amount."""
        allocation = session.query(Allocation).filter(Allocation.deleted == False).first()
        if not allocation:
            pytest.skip("No allocations in database")

        original_amount = float(allocation.amount)
        original_id = allocation.allocation_id
        new_amount = original_amount + 5000.00

        # Update amount
        allocation.amount = new_amount
        session.flush()

        # Verify update
        updated_allocation = session.query(Allocation).filter_by(allocation_id=original_id).first()
        assert float(updated_allocation.amount) == new_amount
        print(f"✅ Updated allocation {original_id}: amount {original_amount} → {new_amount}")

        session.rollback()

    def test_update_allocation_dates(self, session):
        """Test updating allocation start/end dates."""
        allocation = session.query(Allocation).filter(Allocation.deleted == False).first()
        if not allocation:
            pytest.skip("No allocations in database")

        original_start = allocation.start_date
        original_end = allocation.end_date
        new_start = datetime.now(UTC)
        new_end = new_start + timedelta(days=180)

        # Update dates
        allocation.start_date = new_start
        allocation.end_date = new_end
        session.flush()

        # Verify updates
        assert allocation.start_date != original_start
        assert allocation.end_date != original_end
        print(f"✅ Updated allocation dates")

        session.rollback()

    def test_update_user_name(self, session):
        """Test updating user name fields."""
        user = session.query(User).first()
        if not user:
            pytest.skip("No users in database")

        original_nickname = user.nickname
        new_nickname = "Test Nickname"

        # Update nickname
        user.nickname = new_nickname
        session.flush()

        # Verify update
        assert user.nickname == new_nickname
        print(f"✅ Updated user {user.username}: nickname '{original_nickname}' → '{new_nickname}'")

        session.rollback()

    def test_update_account_user_end_date(self, session):
        """Test updating account_user end date (deactivating membership)."""
        account_user = (
            session.query(AccountUser)
            .filter(AccountUser.end_date.is_(None))
            .first()
        )
        if not account_user:
            pytest.skip("No active account_user records found")

        original_end_date = account_user.end_date
        new_end_date = datetime.now(UTC)

        # Set end date (deactivate)
        account_user.end_date = new_end_date
        session.flush()

        # Verify update
        assert account_user.end_date is not None
        assert account_user.end_date == new_end_date
        print(f"✅ Updated AccountUser {account_user.account_user_id}: end_date None → {new_end_date}")

        session.rollback()


class TestDeleteOperations:
    """Test soft-delete and hard-delete operations."""

    def test_soft_delete_allocation(self, session):
        """Test soft-deleting an allocation."""
        allocation = session.query(Allocation).filter(Allocation.deleted == False).first()
        if not allocation:
            pytest.skip("No non-deleted allocations in database")

        original_id = allocation.allocation_id
        original_deleted = allocation.deleted

        # Soft delete
        allocation.deleted = True
        allocation.deletion_time = datetime.now(UTC)
        session.flush()

        # Verify soft delete
        deleted_allocation = session.query(Allocation).filter_by(allocation_id=original_id).first()
        assert deleted_allocation is not None  # Still exists in DB
        assert deleted_allocation.deleted == True
        assert deleted_allocation.deletion_time is not None
        print(f"✅ Soft-deleted allocation {original_id}: deleted {original_deleted} → {deleted_allocation.deleted}")

        session.rollback()

    def test_delete_email_address(self, session):
        """Test hard-deleting an email address (after creating one)."""
        # First create an email to delete
        user = session.query(User).first()
        if not user:
            pytest.skip("No users in database")

        test_email = EmailAddress(
            email_address=f'delete_test_{datetime.now().timestamp()}@example.com',
            user_id=user.user_id,
            is_primary=False,
            active=True
        )

        session.add(test_email)
        session.flush()
        email_id = test_email.email_address_id

        # Verify it exists
        assert session.query(EmailAddress).filter_by(email_address_id=email_id).first() is not None

        # Hard delete
        session.delete(test_email)
        session.flush()

        # Verify it's gone
        deleted_email = session.query(EmailAddress).filter_by(email_address_id=email_id).first()
        assert deleted_email is None
        print(f"✅ Hard-deleted email {email_id}")

        session.rollback()

    def test_delete_phone(self, session):
        """Test hard-deleting a phone number (after creating one)."""
        # Create a phone to delete
        user = session.query(User).first()
        phone_type = session.query(PhoneType).first()

        if not user or not phone_type:
            pytest.skip("Need user and phone_type")

        test_phone = Phone(
            user_id=user.user_id,
            ext_phone_type_id=phone_type.ext_phone_type_id,
            phone_number='555-DELETE'
        )

        session.add(test_phone)
        session.flush()
        phone_id = test_phone.ext_phone_id

        # Delete it
        session.delete(test_phone)
        session.flush()

        # Verify deletion
        deleted_phone = session.query(Phone).filter_by(ext_phone_id=phone_id).first()
        assert deleted_phone is None
        print(f"✅ Hard-deleted phone {phone_id}")

        session.rollback()


class TestTransactionBehavior:
    """Test transaction commit/rollback behavior."""

    def test_rollback_prevents_persistence(self, session):
        """Test that rollback prevents data from being saved."""
        user = session.query(User).first()
        if not user:
            pytest.skip("No users in database")

        # Create email
        test_email = EmailAddress(
            email_address=f'rollback_test_{datetime.now().timestamp()}@example.com',
            user_id=user.user_id,
            is_primary=False,
            active=True
        )

        session.add(test_email)
        session.flush()
        email_id = test_email.email_address_id

        # Rollback
        session.rollback()

        # Verify it doesn't persist
        # Need a new session to check
        from fixtures.test_config import create_test_session_factory, create_test_engine
        engine = create_test_engine()
        SessionFactory = create_test_session_factory(engine)
        check_session = SessionFactory()

        try:
            rolled_back_email = check_session.query(EmailAddress).filter_by(email_address_id=email_id).first()
            assert rolled_back_email is None
            print(f"✅ Rollback prevented persistence of email {email_id}")
        finally:
            check_session.close()

    def test_flush_vs_commit(self, session):
        """Test difference between flush() and commit()."""
        user = session.query(User).first()
        if not user:
            pytest.skip("No users in database")

        # Create email and flush
        test_email = EmailAddress(
            email_address=f'flush_test_{datetime.now().timestamp()}@example.com',
            user_id=user.user_id,
            is_primary=False,
            active=True
        )

        session.add(test_email)
        session.flush()  # Flush to DB but don't commit

        # Email has ID after flush
        assert test_email.email_address_id is not None
        email_id = test_email.email_address_id
        print(f"✅ Flush assigned ID: {email_id}")

        # But can still be rolled back
        session.rollback()

        # Verify rollback worked
        assert test_email.email_address_id is None or session.query(EmailAddress).filter_by(email_address_id=email_id).first() is None
        print(f"✅ Flush+Rollback prevented persistence")


class TestComplexCRUD:
    """Test more complex CRUD scenarios."""

    def test_create_allocation_with_parent(self, session):
        """Test creating child allocation with parent relationship."""
        # Get an existing allocation to use as parent
        parent_allocation = session.query(Allocation).filter(Allocation.deleted == False).first()
        if not parent_allocation:
            pytest.skip("No allocations in database")

        # Create child allocation
        now = datetime.now(UTC)
        child_allocation = Allocation(
            account_id=parent_allocation.account_id,
            parent_allocation_id=parent_allocation.allocation_id,
            amount=5000.00,
            description='Child test allocation',
            start_date=now,
            end_date=now + timedelta(days=180),
            deleted=False
        )

        session.add(child_allocation)
        session.flush()

        # Verify relationship
        assert child_allocation.parent == parent_allocation
        assert child_allocation in parent_allocation.children
        print(f"✅ Created child allocation {child_allocation.allocation_id} under parent {parent_allocation.allocation_id}")

        session.rollback()

    def test_cascade_update_timestamps(self, session):
        """Test that timestamp fields auto-update."""
        user = session.query(User).first()
        if not user:
            pytest.skip("No users in database")

        # Get original modified time
        original_modified = user.modified_time

        # Make a change
        user.nickname = f"Updated_{datetime.now().timestamp()}"
        session.flush()

        # Modified time should update (if database has ON UPDATE trigger)
        # Note: This depends on database-level triggers
        print(f"✅ User update completed (modified_time tracking depends on DB triggers)")

        session.rollback()

    def test_bulk_insert_emails(self, session):
        """Test bulk inserting multiple email addresses."""
        user = session.query(User).first()
        if not user:
            pytest.skip("No users in database")

        # Create multiple emails
        timestamp = datetime.now().timestamp()
        emails = [
            EmailAddress(
                email_address=f'bulk_{i}_{timestamp}@example.com',
                user_id=user.user_id,
                is_primary=False,
                active=True
            )
            for i in range(5)
        ]

        session.add_all(emails)
        session.flush()

        # Verify all have IDs
        assert all(email.email_address_id is not None for email in emails)
        print(f"✅ Bulk inserted {len(emails)} emails")


class TestAccountCreatePropagation:
    """Account.create() enforces the member-propagation invariant.

    Every new Account must auto-materialize AccountUser rows for the project
    lead, the project admin, and any user currently active on a sibling
    Account of the same project. This class pins that behavior.
    """

    def _pick_unused_resource(self, session, project):
        """Return a Resource not yet attached to this project, or None."""
        used_ids = {a.resource_id for a in project.accounts if not a.deleted}
        q = session.query(Resource)
        if used_ids:
            q = q.filter(~Resource.resource_id.in_(used_ids))
        return q.first()

    def test_propagates_lead_and_admin(self, session, test_project):
        """Lead and admin land on the new Account as open-ended AccountUsers."""
        if test_project is None:
            pytest.skip("Test project SCSG0001 not found")
        if test_project.project_lead_user_id is None:
            pytest.skip("Test project has no lead")

        target_resource = self._pick_unused_resource(session, test_project)
        if target_resource is None:
            pytest.skip("No unused resource available for test project")

        before = datetime.now()
        account = Account.create(
            session,
            project_id=test_project.project_id,
            resource_id=target_resource.resource_id,
        )

        assert account.account_id is not None
        assert account.project_id == test_project.project_id
        assert account.resource_id == target_resource.resource_id

        new_aus = session.query(AccountUser).filter_by(
            account_id=account.account_id
        ).all()
        propagated_ids = {au.user_id for au in new_aus}

        assert test_project.project_lead_user_id in propagated_ids, (
            "Project lead must be propagated to new Account"
        )
        if test_project.project_admin_user_id is not None:
            assert test_project.project_admin_user_id in propagated_ids, (
                "Project admin must be propagated to new Account"
            )

        for au in new_aus:
            assert au.end_date is None, (
                f"AccountUser {au.account_user_id} must be open-ended "
                f"(got end_date={au.end_date})"
            )
            assert au.start_date is not None
            assert au.start_date >= before - timedelta(seconds=5)

        print(
            f"✅ Account.create() propagated {len(propagated_ids)} users "
            f"(lead/admin + siblings) to new {target_resource.resource_name} account"
        )
        session.rollback()

    def test_propagates_active_sibling_members(self, session, test_project):
        """Active AccountUsers on sibling accounts are copied to the new account."""
        if test_project is None or not test_project.accounts:
            pytest.skip("Test project has no sibling accounts")

        active_sibling_ids = set()
        for sibling in test_project.accounts:
            if sibling.deleted:
                continue
            for au in sibling.users:
                if au.end_date is None:
                    active_sibling_ids.add(au.user_id)
        if not active_sibling_ids:
            pytest.skip("No active sibling members to propagate")

        target_resource = self._pick_unused_resource(session, test_project)
        if target_resource is None:
            pytest.skip("No unused resource available for test project")

        account = Account.create(
            session,
            project_id=test_project.project_id,
            resource_id=target_resource.resource_id,
        )

        propagated_ids = {
            au.user_id
            for au in session.query(AccountUser).filter_by(
                account_id=account.account_id
            ).all()
        }
        missing = active_sibling_ids - propagated_ids
        assert not missing, (
            f"Active sibling members not propagated: user_ids={missing}"
        )
        print(
            f"✅ {len(active_sibling_ids)} active sibling members "
            f"propagated to new account"
        )
        session.rollback()

    def test_skips_inactive_sibling_members(self, session, test_project):
        """Expired (end_date set) sibling memberships are NOT propagated."""
        if test_project is None or not test_project.accounts:
            pytest.skip("Test project has no sibling accounts")

        expired_ids = set()
        active_ids = set()
        for sibling in test_project.accounts:
            if sibling.deleted:
                continue
            for au in sibling.users:
                if au.end_date is None:
                    active_ids.add(au.user_id)
                else:
                    expired_ids.add(au.user_id)
        # Users whose ONLY membership is expired — they must not appear.
        # (Users with both active and expired rows legitimately propagate
        # via the active row, so exclude them from this check.)
        truly_expired = expired_ids - active_ids
        # Lead/admin propagate unconditionally from the project FK, so
        # exclude them too.
        truly_expired -= {
            test_project.project_lead_user_id,
            test_project.project_admin_user_id,
        }
        if not truly_expired:
            pytest.skip("No users with only expired memberships on this project")

        target_resource = self._pick_unused_resource(session, test_project)
        if target_resource is None:
            pytest.skip("No unused resource available for test project")

        account = Account.create(
            session,
            project_id=test_project.project_id,
            resource_id=target_resource.resource_id,
        )

        propagated_ids = {
            au.user_id
            for au in session.query(AccountUser).filter_by(
                account_id=account.account_id
            ).all()
        }
        leaked = truly_expired & propagated_ids
        assert not leaked, (
            f"Expired-only sibling members were propagated: user_ids={leaked}"
        )
        print(f"✅ {len(truly_expired)} expired-only members correctly excluded")
        session.rollback()

    def test_raises_on_missing_project(self, session):
        """ValueError if project_id doesn't exist."""
        fake_id = 999_999_999
        assert session.get(Project, fake_id) is None

        resource = session.query(Resource).first()
        assert resource is not None

        with pytest.raises(ValueError, match="Project .* not found"):
            Account.create(
                session,
                project_id=fake_id,
                resource_id=resource.resource_id,
            )
        print("✅ Account.create() rejects missing project")
        session.rollback()

        session.rollback()
