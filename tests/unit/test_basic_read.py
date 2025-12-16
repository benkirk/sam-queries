"""
Basic Read Operations Test

Tests querying existing data from all major ORM models.
This is a read-only test suite that doesn't modify the database.
"""

import pytest
from datetime import datetime, UTC

import sam
from sam import (
    User, Project, Account, Allocation, Resource, Organization,
    Institution, Machine, Queue, Contract, AreaOfInterest,
    EmailAddress, Phone, AccountUser, AllocationType
)


class TestBasicRead:
    """Test basic read operations on core models."""

    @pytest.mark.parametrize('model,min_count,model_name', [
        (User, 1000, 'users'),
        (Project, 100, 'projects'),
        (Account, 100, 'accounts'),
        (Allocation, 100, 'allocations'),
        (Resource, 10, 'resources'),
        (Organization, 50, 'organizations'),
        (Institution, 100, 'institutions'),
        (Machine, 5, 'machines'),
        (Queue, 10, 'queues'),
    ])
    def test_model_count(self, session, model, min_count, model_name):
        """Test that core models have expected record counts."""
        count = session.query(model).count()
        assert count >= min_count, f"{model.__name__} has only {count} records (expected >= {min_count})"
        print(f"✅ Found {count} {model_name}")

    @pytest.mark.parametrize('model,pk_column', [
        (User, 'user_id'),
        (Project, 'project_id'),
        (Account, 'account_id'),
        (Allocation, 'allocation_id'),
        (Resource, 'resource_id'),
        (Organization, 'organization_id'),
        (Institution, 'institution_id'),
        (Machine, 'machine_id'),
        (Queue, 'queue_id'),
    ])
    def test_model_primary_key(self, session, model, pk_column):
        """Test that models have valid primary keys."""
        instance = session.query(model).first()
        assert instance is not None, f"No records found for {model.__name__}"
        assert hasattr(instance, pk_column), f"{model.__name__} missing primary key column '{pk_column}'"
        assert getattr(instance, pk_column) is not None, f"{model.__name__}.{pk_column} is None"
        print(f"✅ {model.__name__}.{pk_column} = {getattr(instance, pk_column)}")

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
        project = session.query(Project).filter(Project.project_lead_user_id.isnot(None)).first()
        assert project is not None
        assert project.lead is not None
        assert isinstance(project.lead, User)
        print(f"✅ Project {project.projcode} lead: {project.lead.username}")

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
        print(f"✅ Account {account.account_id} → Resource {account.resource.resource_name}")

    def test_allocation_with_account(self, session):
        """Test allocation with account relationship."""
        allocation = session.query(Allocation).first()
        assert allocation is not None
        assert allocation.account is not None
        assert isinstance(allocation.account, Account)
        print(f"✅ Allocation {allocation.allocation_id} → Account {allocation.account.account_id}")

    def test_resource_query(self, session):
        """Test querying and accessing resource properties."""
        resource = session.query(Resource).filter(Resource.is_active == True).first()
        assert resource is not None
        assert resource.resource_id is not None
        assert resource.resource_name is not None
        print(f"✅ Resource: {resource.resource_name}")


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
                (Allocation.end_date.is_(None) | (Allocation.end_date >= now))
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

        print(f"✅ AccountUser relationships work: User={account_user.user.username}, Account={account_user.account.account_id}")

    def test_project_hierarchy(self, session):
        """Test project parent-child relationships."""
        child_project = session.query(Project).filter(Project.parent_id.isnot(None)).first()
        if not child_project:
            print("ℹ️  No child projects found (skipping hierarchy test)")
            return

        assert child_project.parent is not None
        assert isinstance(child_project.parent, Project)
        print(f"✅ Project hierarchy works: {child_project.projcode} → parent: {child_project.parent.projcode}")

    def test_allocation_parent_child(self, session):
        """Test allocation parent-child relationships."""
        child_allocation = session.query(Allocation).filter(Allocation.parent_allocation_id.isnot(None)).first()
        if not child_allocation:
            print("ℹ️  No child allocations found (skipping hierarchy test)")
            return

        assert child_allocation.parent is not None
        assert isinstance(child_allocation.parent, Allocation)
        print(f"✅ Allocation hierarchy works: {child_allocation.allocation_id} → parent: {child_allocation.parent.allocation_id}")


class TestProjectResourceAccess:
    """Test project methods for determining user resource access."""

    def test_get_user_inaccessible_resources_method_exists(self, session):
        """Test that the get_user_inaccessible_resources method exists on Project."""
        project = session.query(Project).filter(Project.active == True).first()
        assert hasattr(project, 'get_user_inaccessible_resources')
        assert callable(getattr(project, 'get_user_inaccessible_resources'))
        print("✅ Project.get_user_inaccessible_resources() method exists")

    def test_get_user_inaccessible_resources_returns_set(self, session):
        """Test that get_user_inaccessible_resources returns a set."""
        # Get a project with active allocations
        project = session.query(Project).filter(
            Project.active == True,
            Project.projcode == 'SCSG0001'
        ).first()

        if not project:
            pytest.skip("SCSG0001 project not found")

        # Get a user on the project
        user = User.get_by_username(session, 'benkirk')
        if not user:
            pytest.skip("benkirk user not found")

        result = project.get_user_inaccessible_resources(user)
        assert isinstance(result, set)
        print(f"✅ get_user_inaccessible_resources returns set (size: {len(result)})")

    def test_get_user_inaccessible_resources_with_full_access(self, session):
        """Test user with full access to all project resources."""
        # Use SCSG0001 as test project
        project = session.query(Project).filter(
            Project.active == True,
            Project.projcode == 'SCSG0001'
        ).first()

        if not project:
            pytest.skip("SCSG0001 project not found")

        # Get project lead (should have full access)
        if not project.lead:
            pytest.skip("Project has no lead")

        inaccessible = project.get_user_inaccessible_resources(project.lead)

        # Lead should typically have access to all resources
        # (This might fail if the lead genuinely has restricted access)
        print(f"✅ Project lead has {len(inaccessible)} inaccessible resources")
        assert isinstance(inaccessible, set)

    def test_get_user_inaccessible_resources_with_no_allocations(self, session):
        """Test project with no active allocations returns empty set."""
        # Find a project with no active allocations
        all_projects = session.query(Project).filter(Project.active == True).limit(100).all()

        project_with_no_allocs = None
        for proj in all_projects:
            allocs = proj.get_all_allocations_by_resource()
            if not allocs:
                project_with_no_allocs = proj
                break

        if not project_with_no_allocs:
            pytest.skip("No active project without allocations found")

        # Get any user
        user = session.query(User).filter(User.active == True).first()
        if not user:
            pytest.skip("No active user found")

        inaccessible = project_with_no_allocs.get_user_inaccessible_resources(user)

        # Should return empty set when project has no active allocations
        assert inaccessible == set()
        print(f"✅ Project with no allocations returns empty set for any user")

    def test_get_user_inaccessible_resources_sorted_output(self, session):
        """Test that inaccessible resources can be sorted alphabetically."""
        project = session.query(Project).filter(
            Project.active == True,
            Project.projcode == 'SCSG0001'
        ).first()

        if not project:
            pytest.skip("SCSG0001 project not found")

        user = User.get_by_username(session, 'benkirk')
        if not user:
            pytest.skip("benkirk user not found")

        inaccessible = project.get_user_inaccessible_resources(user)

        # Verify we can sort the results
        sorted_resources = sorted(inaccessible)
        assert isinstance(sorted_resources, list)
        print(f"✅ Can sort inaccessible resources: {sorted_resources}")

    def test_get_user_inaccessible_resources_with_multiple_resources(self, session):
        """Test project with multiple resources."""
        # Find a project with multiple resources
        all_projects = session.query(Project).filter(Project.active == True).limit(100).all()

        project_with_multiple = None
        for proj in all_projects:
            allocs = proj.get_all_allocations_by_resource()
            if len(allocs) >= 2:
                project_with_multiple = proj
                break

        if not project_with_multiple:
            pytest.skip("No project with multiple resources found")

        # Get a user on the project
        users = project_with_multiple.users
        if not users:
            pytest.skip("Project has no users")

        user = users[0]
        inaccessible = project_with_multiple.get_user_inaccessible_resources(user)

        # Should return a set (might be empty if user has full access)
        assert isinstance(inaccessible, set)

        # Get all project resources for comparison
        all_resources = set(project_with_multiple.get_all_allocations_by_resource().keys())

        # Inaccessible should be subset of all resources
        assert inaccessible.issubset(all_resources)

        print(f"✅ User has access to {len(all_resources) - len(inaccessible)} of {len(all_resources)} resources")
        if inaccessible:
            print(f"   Inaccessible: {sorted(inaccessible)}")
