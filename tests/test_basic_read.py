"""
Basic Read Operations Test

Tests querying existing data from all major ORM models.
This is a read-only test suite that doesn't modify the database.
"""

from datetime import UTC, datetime

import pytest
from sam import (
    Account,
    AccountUser,
    Allocation,
    Institution,
    Machine,
    Organization,
    Project,
    Queue,
    Resource,
    User,
)


class TestBasicRead:
    """Test basic read operations on core models."""

    def test_user_count(self, session):
        """Test that we can query users."""
        user_count = session.query(User).count()
        assert user_count > 0, "Expected to find users in database"
        print(f"✅ Found {user_count} users")

    def test_user_query(self, session):
        """Test querying and accessing user properties."""
        user = session.query(User).filter(User.active == True).first()
        assert user is not None, "Expected to find at least one active user"
        assert user.user_id is not None
        assert user.username is not None
        print(f"✅ User: {user.username} ({user.display_name})")

    def test_user_with_email(self, session):
        """Test user with email relationship."""
        user = session.query(User).join(User.email_addresses).first()
        assert user is not None
        assert len(user.email_addresses) > 0
        assert user.primary_email is not None
        print(f"✅ User {user.username} has email: {user.primary_email}")

    def test_project_count(self, session):
        """Test that we can query projects."""
        project_count = session.query(Project).count()
        assert project_count > 0, "Expected to find projects in database"
        print(f"✅ Found {project_count} projects")

    def test_project_query(self, session):
        """Test querying and accessing project properties."""
        project = session.query(Project).filter(Project.active == True).first()
        assert project is not None, "Expected to find at least one active project"
        assert project.project_id is not None
        assert project.projcode is not None
        assert project.title is not None
        print(f"✅ Project: {project.projcode} - {project.title[:50]}")

    def test_project_with_lead(self, session):
        """Test project with user relationship (lead)."""
        project = (
            session.query(Project)
            .filter(Project.project_lead_user_id.isnot(None))
            .first()
        )
        assert project is not None
        assert project.lead is not None
        assert isinstance(project.lead, User)
        print(f"✅ Project {project.projcode} lead: {project.lead.username}")

    def test_account_count(self, session):
        """Test that we can query accounts."""
        account_count = session.query(Account).count()
        assert account_count > 0, "Expected to find accounts in database"
        print(f"✅ Found {account_count} accounts")

    def test_account_with_project(self, session):
        """Test account with project relationship."""
        account = session.query(Account).filter(Account.project_id.isnot(None)).first()
        assert account is not None
        assert account.project is not None
        assert isinstance(account.project, Project)
        print(f"✅ Account {account.account_id} → Project {account.project.projcode}")

    def test_account_with_resource(self, session):
        """Test account with resource relationship."""
        account = session.query(Account).filter(Account.resource_id.isnot(None)).first()
        assert account is not None
        assert account.resource is not None
        assert isinstance(account.resource, Resource)
        print(
            f"✅ Account {account.account_id} → Resource {account.resource.resource_name}"
        )

    def test_allocation_count(self, session):
        """Test that we can query allocations."""
        allocation_count = session.query(Allocation).count()
        assert allocation_count > 0, "Expected to find allocations in database"
        print(f"✅ Found {allocation_count} allocations")

    def test_allocation_with_account(self, session):
        """Test allocation with account relationship."""
        allocation = session.query(Allocation).first()
        assert allocation is not None
        assert allocation.account is not None
        assert isinstance(allocation.account, Account)
        print(
            f"✅ Allocation {allocation.allocation_id} → Account {allocation.account.account_id}"
        )

    def test_resource_count(self, session):
        """Test that we can query resources."""
        resource_count = session.query(Resource).count()
        assert resource_count > 0, "Expected to find resources in database"
        print(f"✅ Found {resource_count} resources")

    def test_resource_query(self, session):
        """Test querying and accessing resource properties."""
        resource = session.query(Resource).filter(Resource.is_active == True).first()
        assert resource is not None
        assert resource.resource_id is not None
        assert resource.resource_name is not None
        print(f"✅ Resource: {resource.resource_name}")

    def test_organization_count(self, session):
        """Test that we can query organizations."""
        org_count = session.query(Organization).count()
        assert org_count > 0, "Expected to find organizations in database"
        print(f"✅ Found {org_count} organizations")

    def test_institution_count(self, session):
        """Test that we can query institutions."""
        inst_count = session.query(Institution).count()
        assert inst_count > 0, "Expected to find institutions in database"
        print(f"✅ Found {inst_count} institutions")

    def test_machine_count(self, session):
        """Test that we can query machines."""
        machine_count = session.query(Machine).count()
        assert machine_count > 0, "Expected to find machines in database"
        print(f"✅ Found {machine_count} machines")

    def test_queue_count(self, session):
        """Test that we can query queues."""
        queue_count = session.query(Queue).count()
        assert queue_count > 0, "Expected to find queues in database"
        print(f"✅ Found {queue_count} queues")


class TestComplexQueries:
    """Test more complex queries and joins."""

    def test_user_with_projects(self, session):
        """Test getting users with their projects."""
        user = session.query(User).join(User.accounts).first()
        assert user is not None
        projects = user.all_projects
        if projects:
            print(f"✅ User {user.username} has {len(projects)} project(s)")
        else:
            print(f"ℹ️  User {user.username} has no projects")

    def test_project_with_accounts(self, session):
        """Test project with multiple accounts."""
        project = session.query(Project).join(Project.accounts).first()
        assert project is not None
        assert len(project.accounts) > 0
        print(f"✅ Project {project.projcode} has {len(project.accounts)} account(s)")

    def test_project_with_allocations(self, session):
        """Test getting project allocations through accounts."""
        project = (
            session.query(Project)
            .join(Project.accounts)
            .join(Account.allocations)
            .first()
        )
        assert project is not None

        # Get allocations
        allocations = []
        for account in project.accounts:
            allocations.extend(account.allocations)

        if allocations:
            print(f"✅ Project {project.projcode} has {len(allocations)} allocation(s)")
        else:
            print(f"ℹ️  Project {project.projcode} has no allocations")

    def test_active_allocation_query(self, session):
        """Test querying active allocations."""
        now = datetime.now(UTC)
        active_allocs = (
            session.query(Allocation)
            .filter(
                Allocation.deleted == False,
                Allocation.start_date <= now,
                (Allocation.end_date.is_(None) | (Allocation.end_date >= now)),
            )
            .count()
        )
        print(f"✅ Found {active_allocs} active allocations")

    def test_user_search_methods(self, session):
        """Test User class search methods."""
        # Get a real username first
        sample_user = session.query(User).first()
        if not sample_user:
            pytest.skip("No users in database")

        # Test get_by_username
        user = User.get_by_username(session, sample_user.username)
        assert user is not None
        assert user.username == sample_user.username
        print(f"✅ User.get_by_username() works: {user.username}")

        # Test get_active_users
        active_users = User.get_active_users(session, limit=10)
        assert len(active_users) > 0
        print(f"✅ User.get_active_users() found {len(active_users)} users")

    def test_project_search_methods(self, session):
        """Test Project class search methods."""
        # Get a real project code first
        sample_project = session.query(Project).first()
        if not sample_project:
            pytest.skip("No projects in database")

        # Test get_by_projcode
        project = Project.get_by_projcode(session, sample_project.projcode)
        assert project is not None
        assert project.projcode == sample_project.projcode
        print(f"✅ Project.get_by_projcode() works: {project.projcode}")

        # Test get_active_projects
        active_projects = Project.get_active_projects(session, limit=10)
        assert len(active_projects) > 0
        print(f"✅ Project.get_active_projects() found {len(active_projects)} projects")


class TestRelationships:
    """Test ORM relationships work correctly."""

    def test_account_user_bidirectional(self, session):
        """Test bidirectional relationship between Account and User."""
        account_user = session.query(AccountUser).first()
        if not account_user:
            pytest.skip("No account_user records found")

        # Forward relationship
        assert account_user.user is not None
        assert isinstance(account_user.user, User)

        # Reverse relationship
        assert account_user.account is not None
        assert isinstance(account_user.account, Account)

        print(
            f"✅ AccountUser relationships work: User={account_user.user.username}, Account={account_user.account.account_id}"
        )

    def test_project_hierarchy(self, session):
        """Test project parent-child relationships."""
        child_project = (
            session.query(Project).filter(Project.parent_id.isnot(None)).first()
        )
        if not child_project:
            print("ℹ️  No child projects found (skipping hierarchy test)")
            return

        assert child_project.parent is not None
        assert isinstance(child_project.parent, Project)
        print(
            f"✅ Project hierarchy works: {child_project.projcode} → parent: {child_project.parent.projcode}"
        )

    def test_allocation_parent_child(self, session):
        """Test allocation parent-child relationships."""
        child_allocation = (
            session.query(Allocation)
            .filter(Allocation.parent_allocation_id.isnot(None))
            .first()
        )
        if not child_allocation:
            print("ℹ️  No child allocations found (skipping hierarchy test)")
            return

        assert child_allocation.parent is not None
        assert isinstance(child_allocation.parent, Allocation)
        print(
            f"✅ Allocation hierarchy works: {child_allocation.allocation_id} → parent: {child_allocation.parent.allocation_id}"
        )
