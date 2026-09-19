"""Unit tests for sam.queries.tree_audit.

Each test builds an isolated tree on its own freshly-made HPC resource
(Layer-2 factories) and audits filtered to that resource, so assertions are
exact counts rather than "did my violation appear among the snapshot's".
"""
from datetime import datetime, timedelta

import pytest

from sam.queries.tree_audit import audit_allocation_trees, audit_allocation_dates
from tests.factories.projects import make_project, make_account, make_allocation
from tests.factories.resources import make_resource

pytestmark = pytest.mark.unit


@pytest.fixture
def hpc_type(session):
    """The real 'HPC' ResourceType — the audit only looks at HPC/DAV."""
    from sam import ResourceType
    rt = session.query(ResourceType).filter_by(resource_type='HPC').first()
    assert rt is not None, "snapshot has no 'HPC' resource type"
    return rt


@pytest.fixture
def resource(session, hpc_type):
    """A fresh configurable HPC resource, invisible to every other test."""
    return make_resource(session, resource_type=hpc_type)


def _alloc(session, resource, project, amount, parent_alloc=None):
    """Give `project` a current allocation of `amount` on `resource`."""
    account = make_account(session, project=project, resource=resource)
    return make_allocation(session, account=account, amount=amount,
                           parent=parent_alloc)


# ============================================================================
# The tree invariant
# ============================================================================


class TestCarveOutTrees:
    """Children holding distinct amounts consume the parent's allocation."""

    def test_covered_children_are_not_flagged(self, session, resource):
        parent = make_project(session)
        _alloc(session, resource, parent, 1_000_000)
        for amount in (400_000, 400_000):
            _alloc(session, resource, make_project(session, parent=parent), amount)

        assert audit_allocation_trees(session, resource.resource_name) == []

    def test_children_exceeding_parent_are_flagged(self, session, resource):
        parent = make_project(session)
        _alloc(session, resource, parent, 1_000_000)
        for amount in (600_000, 600_000):
            _alloc(session, resource, make_project(session, parent=parent), amount)

        violations = audit_allocation_trees(session, resource.resource_name)

        assert len(violations) == 1
        v = violations[0]
        assert v['parent_projcode'] == parent.projcode
        assert v['resource_name'] == resource.resource_name
        assert v['parent_amount'] == 1_000_000
        assert v['carve_total'] == 1_200_000
        assert v['deficit'] == 200_000
        assert len(v['carve_children']) == 2
        assert v['pool_children'] == []

    def test_exactly_covered_is_not_flagged(self, session, resource):
        """The invariant is 'covers', so equality is fine — a tree that spends
        its parent's allocation to the last AU is not an error."""
        parent = make_project(session)
        _alloc(session, resource, parent, 1_000_000)
        _alloc(session, resource, make_project(session, parent=parent), 1_000_000 - 1)
        # (1_000_000 would be read as a pool member, not a carve-out)

        assert audit_allocation_trees(session, resource.resource_name) == []


class TestSharedPools:
    """Pool members carry the pool's full amount and must not count against it."""

    def test_equal_amount_children_are_pool_members(self, session, resource):
        """The fallback signal: unlinked pools are common (in production only
        3 of NMMM0003's 15 members are linked), so equal amounts must be read
        as sharing, not as a 15x over-subscription."""
        parent = make_project(session)
        _alloc(session, resource, parent, 1_000_000)
        for _ in range(3):
            _alloc(session, resource, make_project(session, parent=parent), 1_000_000)

        assert audit_allocation_trees(session, resource.resource_name) == []

    def test_linked_children_are_pool_members_regardless_of_amount(
            self, session, resource):
        """Linkage is authoritative: parent_allocation_id says 'shares the
        pool' even when the amounts disagree."""
        parent = make_project(session)
        parent_alloc = _alloc(session, resource, parent, 1_000_000)
        _alloc(session, resource, make_project(session, parent=parent),
               999_999, parent_alloc=parent_alloc)

        assert audit_allocation_trees(session, resource.resource_name) == []

    def test_pool_and_carve_children_coexist(self, session, resource):
        """A node can mix conventions; only the carve-outs count."""
        parent = make_project(session)
        _alloc(session, resource, parent, 1_000_000)
        _alloc(session, resource, make_project(session, parent=parent), 1_000_000)
        _alloc(session, resource, make_project(session, parent=parent), 1_200_000)

        violations = audit_allocation_trees(session, resource.resource_name)

        assert len(violations) == 1
        v = violations[0]
        assert v['deficit'] == 200_000
        assert len(v['pool_children']) == 1
        assert len(v['carve_children']) == 1


class TestAuditScope:

    def test_violations_are_per_resource(self, session, hpc_type):
        """A tree can be a pool on one resource and subdivided on another, so
        the invariant is judged per (parent, resource)."""
        res_a = make_resource(session, resource_type=hpc_type)
        res_b = make_resource(session, resource_type=hpc_type)
        parent = make_project(session)
        child = make_project(session, parent=parent)

        _alloc(session, res_a, parent, 1_000_000)
        _alloc(session, res_a, child, 1_200_000)      # violation on A
        _alloc(session, res_b, parent, 1_000_000)
        _alloc(session, res_b, child, 500_000)        # fine on B

        assert len(audit_allocation_trees(session, res_a.resource_name)) == 1
        assert audit_allocation_trees(session, res_b.resource_name) == []

    def test_deficits_sort_worst_first(self, session, hpc_type):
        res = make_resource(session, resource_type=hpc_type)
        for parent_amt, child_amt in ((1_000_000, 1_100_000),
                                      (1_000_000, 3_000_000)):
            parent = make_project(session)
            _alloc(session, res, parent, parent_amt)
            _alloc(session, res, make_project(session, parent=parent), child_amt)

        deficits = [v['deficit'] for v in audit_allocation_trees(session,
                                                                 res.resource_name)]
        assert deficits == sorted(deficits, reverse=True)

    def test_childless_projects_are_never_flagged(self, session, resource):
        _alloc(session, resource, make_project(session), 1_000_000)
        assert audit_allocation_trees(session, resource.resource_name) == []


# ============================================================================
# Allocation date windows
# ============================================================================


class TestDateAudit:

    def test_impossible_start_year_is_flagged(self, session, resource):
        """A mistyped year (production had 0006-05-08) yields a ~737,000-day
        window, which wrecks any burn-rate consumer."""
        project = make_project(session)
        account = make_account(session, project=project, resource=resource)
        make_allocation(session, account=account, amount=10_000,
                        start_date=datetime(6, 5, 8),
                        end_date=datetime.now() + timedelta(days=365))

        bad = audit_allocation_dates(session, resource.resource_name)

        assert len(bad) == 1
        assert bad[0]['projcode'] == project.projcode
        assert bad[0]['start_date'].year == 6

    def test_normal_window_is_not_flagged(self, session, resource):
        _alloc(session, resource, make_project(session), 1_000_000)
        assert audit_allocation_dates(session, resource.resource_name) == []

    def test_long_windows_are_not_flagged(self, session, resource):
        """Regression: multi-year allocations are routine (~160 current ones
        run 5-10 years), so length alone must never be an error."""
        project = make_project(session)
        account = make_account(session, project=project, resource=resource)
        make_allocation(session, account=account, amount=1_000_000,
                        start_date=datetime.now() - timedelta(days=365 * 4),
                        end_date=datetime.now() + timedelta(days=365 * 5))

        assert audit_allocation_dates(session, resource.resource_name) == []
