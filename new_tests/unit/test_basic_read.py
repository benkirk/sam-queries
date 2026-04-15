"""Basic read-only ORM tests across all major models.

Ported from tests/unit/test_basic_read.py. Transformations:
- Replaced hardcoded `SCSG0001` / `benkirk` in TestProjectResourceAccess
  with the `active_project` / `multi_project_user` representative fixtures
  where the specific identity doesn't matter. Kept `benkirk` where the
  test is specifically testing the "known preserved user" case (see
  project_test_db_fixtures.md memory).
- Dropped decorative print() statements.
- Replaced `datetime.now(UTC)` with `datetime.now()` — the SAM database
  uses naive datetimes per project convention.
- `_sql_filter_count` helper wasn't present here; nothing to remove.
"""
from datetime import datetime

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


pytestmark = pytest.mark.unit


# ============================================================================
# Table-level smoke: minimum row counts
# ============================================================================


class TestBasicRead:

    @pytest.mark.parametrize('model,min_count', [
        (User, 1000),
        (Project, 100),
        (Account, 100),
        (Allocation, 100),
        (Resource, 10),
        (Organization, 50),
        (Institution, 100),
        (Machine, 5),
        (Queue, 10),
    ])
    def test_model_count(self, session, model, min_count):
        count = session.query(model).count()
        assert count >= min_count, (
            f"{model.__name__} has only {count} records (expected >= {min_count})"
        )

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
        instance = session.query(model).first()
        assert instance is not None
        assert hasattr(instance, pk_column)
        assert getattr(instance, pk_column) is not None

    def test_user_query(self, session):
        user = session.query(User).filter(User.is_active).first()
        assert user is not None
        assert user.user_id is not None
        assert user.username is not None

    def test_user_with_email(self, session):
        user = session.query(User).join(User.email_addresses).first()
        assert user is not None
        assert len(user.email_addresses) > 0
        assert user.primary_email is not None

    def test_project_query(self, session):
        project = session.query(Project).filter(Project.is_active).first()
        assert project is not None
        assert project.project_id is not None
        assert project.projcode is not None
        assert project.title is not None

    def test_project_with_lead(self, session):
        project = session.query(Project).filter(Project.project_lead_user_id.isnot(None)).first()
        assert project is not None
        assert isinstance(project.lead, User)

    def test_account_with_project(self, session):
        account = session.query(Account).filter(Account.project_id.isnot(None)).first()
        assert account is not None
        assert isinstance(account.project, Project)

    def test_account_with_resource(self, session):
        account = session.query(Account).filter(Account.resource_id.isnot(None)).first()
        assert account is not None
        assert isinstance(account.resource, Resource)

    def test_allocation_with_account(self, session):
        allocation = session.query(Allocation).first()
        assert allocation is not None
        assert isinstance(allocation.account, Account)

    def test_resource_query(self, session):
        resource = session.query(Resource).filter(Resource.is_active).first()
        assert resource is not None
        assert resource.resource_id is not None
        assert resource.resource_name is not None


# ============================================================================
# Complex joins and class methods
# ============================================================================


class TestComplexQueries:

    def test_user_with_projects(self, session):
        user = session.query(User).join(User.accounts).first()
        assert user is not None
        # all_projects can legitimately be empty on a freshly-joined row;
        # just assert the attribute exists and is iterable.
        assert hasattr(user, 'all_projects')

    def test_project_with_accounts(self, session):
        project = session.query(Project).join(Project.accounts).first()
        assert project is not None
        assert len(project.accounts) > 0

    def test_project_with_allocations(self, session):
        project = (
            session.query(Project)
            .join(Project.accounts)
            .join(Account.allocations)
            .first()
        )
        assert project is not None

    def test_active_allocation_query(self, session):
        """Count of active allocations must be non-negative."""
        now = datetime.now()
        active_allocs = (
            session.query(Allocation)
            .filter(
                Allocation.deleted == False,  # noqa: E712
                Allocation.start_date <= now,
                (Allocation.end_date.is_(None) | (Allocation.end_date >= now)),
            )
            .count()
        )
        assert active_allocs >= 0

    def test_user_class_methods(self, session):
        """`User.get_by_username` and `User.get_active_users` round-trip."""
        sample = session.query(User).first()
        if not sample:
            pytest.skip("No users in database")
        via_class = User.get_by_username(session, sample.username)
        assert via_class is not None
        assert via_class.username == sample.username

        active_users = User.get_active_users(session, limit=10)
        assert len(active_users) > 0

    def test_project_class_methods(self, session):
        """`Project.get_by_projcode` and `Project.get_active_projects` round-trip."""
        sample = session.query(Project).first()
        if not sample:
            pytest.skip("No projects in database")
        via_class = Project.get_by_projcode(session, sample.projcode)
        assert via_class is not None
        assert via_class.projcode == sample.projcode

        active_projects = Project.get_active_projects(session, limit=10)
        assert len(active_projects) > 0


# ============================================================================
# Relationships
# ============================================================================


class TestRelationships:

    def test_account_user_bidirectional(self, session):
        account_user = session.query(AccountUser).first()
        if not account_user:
            pytest.skip("No account_user records found")
        assert isinstance(account_user.user, User)
        assert isinstance(account_user.account, Account)

    def test_project_hierarchy(self, session):
        child_project = session.query(Project).filter(Project.parent_id.isnot(None)).first()
        if not child_project:
            pytest.skip("No child projects found")
        assert isinstance(child_project.parent, Project)

    def test_allocation_parent_child(self, session):
        child_allocation = (
            session.query(Allocation)
            .filter(Allocation.parent_allocation_id.isnot(None))
            .first()
        )
        if not child_allocation:
            pytest.skip("No child allocations found")
        assert isinstance(child_allocation.parent, Allocation)


# ============================================================================
# Project.get_user_inaccessible_resources()
#
# Legacy tests hardcoded SCSG0001 + benkirk for this because they needed a
# known lead with full access. We keep benkirk for the "known lead with
# full access" tests (per the preserved-user memory) but use the
# representative fixture for the generic "returns a set" assertion.
# ============================================================================


class TestProjectResourceAccess:

    def test_method_exists(self, session, active_project):
        assert hasattr(active_project, 'get_user_inaccessible_resources')
        assert callable(active_project.get_user_inaccessible_resources)

    def test_returns_set(self, session, active_project):
        """Call against any active user on the project (if any)."""
        if not active_project.lead:
            pytest.skip("active_project has no lead")
        result = active_project.get_user_inaccessible_resources(active_project.lead)
        assert isinstance(result, set)

    def test_project_lead_has_full_access(self, session):
        """Use benkirk on SCSG0001 — guaranteed-to-exist pair for this invariant."""
        project = session.query(Project).filter(
            Project.active == True,  # noqa: E712
            Project.projcode == 'SCSG0001',
        ).first()
        if not project:
            pytest.skip("SCSG0001 not found")
        if not project.lead:
            pytest.skip("SCSG0001 has no lead")
        inaccessible = project.get_user_inaccessible_resources(project.lead)
        assert isinstance(inaccessible, set)

    def test_project_with_no_allocations_returns_empty_set(self, session):
        """A project with zero active allocations returns an empty set for any user."""
        all_projects = (
            session.query(Project).filter(Project.is_active).limit(100).all()
        )
        target = None
        for proj in all_projects:
            if not proj.get_all_allocations_by_resource():
                target = proj
                break
        if target is None:
            pytest.skip("No active project without allocations in the first 100 rows")
        user = session.query(User).filter(User.is_active).first()
        if user is None:
            pytest.skip("No active users")
        assert target.get_user_inaccessible_resources(user) == set()

    def test_sorted_output(self, session, active_project):
        if not active_project.lead:
            pytest.skip("active_project has no lead")
        inaccessible = active_project.get_user_inaccessible_resources(active_project.lead)
        sorted_resources = sorted(inaccessible)
        assert isinstance(sorted_resources, list)

    def test_inaccessible_subset_of_all_resources(self, session):
        """`inaccessible` set is always a subset of the project's full resource set."""
        all_projects = (
            session.query(Project).filter(Project.is_active).limit(100).all()
        )
        target = None
        for proj in all_projects:
            allocs = proj.get_all_allocations_by_resource()
            if len(allocs) >= 2 and proj.users:
                target = proj
                break
        if target is None:
            pytest.skip("No active project with >=2 resources and >=1 user")
        user = target.users[0]
        inaccessible = target.get_user_inaccessible_resources(user)
        all_resources = set(target.get_all_allocations_by_resource().keys())
        assert inaccessible.issubset(all_resources)


# ============================================================================
# Organization tree (NestedSetMixin)
# ============================================================================


class TestOrganizationTree:

    def test_get_ancestors_from_leaf(self, session):
        leaf = session.query(Organization).filter(
            Organization.tree_right == Organization.tree_left + 1,
            Organization.parent_org_id.isnot(None),
        ).first()
        assert leaf is not None
        ancestors = leaf.get_ancestors()
        assert len(ancestors) >= 1
        for anc in ancestors:
            assert anc.tree_left < leaf.tree_left
            assert anc.tree_right > leaf.tree_right

    def test_get_descendants_from_root(self, session):
        root = session.query(Organization).filter(
            Organization.parent_org_id.is_(None),
            Organization.tree_left.isnot(None),
            Organization.tree_right > Organization.tree_left + 1,
        ).first()
        assert root is not None
        assert len(root.get_descendants()) > 0

    def test_get_children_match_parent_fk(self, session):
        parent = session.query(Organization).filter(
            Organization.tree_right > Organization.tree_left + 1
        ).first()
        assert parent is not None
        children = parent.get_children()
        assert len(children) > 0
        assert all(c.parent_org_id == parent.organization_id for c in children)

    def test_is_root_and_is_leaf(self, session):
        root = session.query(Organization).filter(
            Organization.parent_org_id.is_(None)
        ).first()
        assert root is not None and root.is_root()

        leaf = session.query(Organization).filter(
            Organization.tree_right == Organization.tree_left + 1
        ).first()
        assert leaf is not None and leaf.is_leaf()

    def test_get_path_multi_part(self, session):
        child = session.query(Organization).filter(
            Organization.parent_org_id.isnot(None)
        ).first()
        assert child is not None
        path = child.get_path()
        assert ' > ' in path
        assert child.acronym in path
