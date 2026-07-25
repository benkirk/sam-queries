"""Tests for the carve-out frontier decomposition:

- ``is_pool_member()`` (sam.queries.tree_audit): THE single pool-vs-carve
  classification rule, shared between the audit and the frontier helper.
- ``get_carveout_frontier()`` (sam.manage.allocations): per-node direct-frontier
  decomposition of a parent allocation into carve-outs, pool members, and open
  (uncovered) branches, plus the unallocated residual.

Semantics under test come from hpc-scheduling-tools/docs/FAIRSHARE_TREE.md
(leaf_weight = amount − Σ carve-out children; residual clamped ≥ 0; per
resource) and sam/queries/tree_audit.py (pool = linked OR equal-amount).
"""
from datetime import datetime, timedelta

import pytest

from sam.manage.allocations import get_carveout_frontier
from sam.queries.tree_audit import is_pool_member

from factories import (
    make_account,
    make_allocation,
    make_project,
    make_resource,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# is_pool_member — the classification primitive
# ---------------------------------------------------------------------------


class TestIsPoolMember:

    def test_linked_is_pool_regardless_of_amount(self):
        assert is_pool_member(linked=True, child_amount=1.0, parent_amount=999.0)

    def test_equal_amount_fallback_is_pool(self):
        assert is_pool_member(linked=False, child_amount=500.0, parent_amount=500.0)

    def test_distinct_amount_unlinked_is_carve(self):
        assert not is_pool_member(linked=False, child_amount=499.0, parent_amount=500.0)

    def test_exact_equality_no_tolerance(self):
        # One unit off is a carve-out — mirrors the fairshare tree's exact test.
        assert not is_pool_member(linked=False, child_amount=730.0, parent_amount=729.0)


# ---------------------------------------------------------------------------
# get_carveout_frontier
# ---------------------------------------------------------------------------


@pytest.fixture
def resource(session):
    return make_resource(session)


def _alloc(session, project, resource, amount, *, parent=None,
           start_date=None, end_date=None):
    """Standalone (or linked) allocation on `project` × `resource`."""
    account = make_account(session, project=project, resource=resource)
    return make_allocation(
        session, account=account, amount=amount, parent=parent,
        start_date=start_date, end_date=end_date,
    )


class TestFrontierClassification:

    def test_childless_project_is_empty_frontier(self, session, resource):
        parent = make_project(session)
        pa = _alloc(session, parent, resource, 1_000_000.0)
        f = get_carveout_frontier(session, pa)
        assert f.carve_children == []
        assert f.pool_children == []
        assert f.open_projects == []
        assert f.carve_total == 0.0
        assert f.residual == 1_000_000.0

    def test_linked_children_are_pool(self, session, resource):
        parent = make_project(session)
        pa = _alloc(session, parent, resource, 1_000_000.0)
        for _ in range(2):
            child = make_project(session, parent=parent)
            _alloc(session, child, resource, 1_000_000.0, parent=pa)
        f = get_carveout_frontier(session, pa)
        assert len(f.pool_children) == 2
        assert f.carve_children == []
        assert f.carve_total == 0.0
        assert f.residual == 1_000_000.0

    def test_equal_amount_unlinked_child_is_pool(self, session, resource):
        parent = make_project(session)
        pa = _alloc(session, parent, resource, 1_000_000.0)
        child = make_project(session, parent=parent)
        _alloc(session, child, resource, 1_000_000.0)   # never linked
        f = get_carveout_frontier(session, pa)
        assert len(f.pool_children) == 1
        assert f.carve_children == []
        assert f.carve_total == 0.0

    def test_simple_carve_children(self, session, resource):
        parent = make_project(session)
        pa = _alloc(session, parent, resource, 1_000_000.0)
        c1 = make_project(session, parent=parent)
        c2 = make_project(session, parent=parent)
        _alloc(session, c1, resource, 400_000.0)
        _alloc(session, c2, resource, 250_000.0)
        f = get_carveout_frontier(session, pa)
        assert len(f.carve_children) == 2
        assert f.carve_total == 650_000.0
        assert f.raw_residual == 350_000.0
        assert f.residual == 350_000.0

    def test_mixed_pool_and_carve_children(self, session, resource):
        """A single node can mix conventions (test_tree_audit precedent)."""
        parent = make_project(session)
        pa = _alloc(session, parent, resource, 1_000_000.0)
        pool_child = make_project(session, parent=parent)
        carve_child = make_project(session, parent=parent)
        _alloc(session, pool_child, resource, 1_000_000.0, parent=pa)
        _alloc(session, carve_child, resource, 300_000.0)
        f = get_carveout_frontier(session, pa)
        assert len(f.pool_children) == 1
        assert len(f.carve_children) == 1
        assert f.carve_total == 300_000.0
        assert f.residual == 700_000.0


class TestFrontierNesting:

    def test_grandchild_is_frontier_when_child_uncovered(self, session, resource):
        """An intermediate project without an allocation is skipped over; its
        allocated descendants carve directly out of the grandparent. The
        intermediate is NOT an open branch (partially covered)."""
        root = make_project(session)
        ra = _alloc(session, root, resource, 1_000_000.0)
        mid = make_project(session, parent=root)          # no allocation
        gc = make_project(session, parent=mid)
        _alloc(session, gc, resource, 200_000.0)
        sibling = make_project(session, parent=root)
        _alloc(session, sibling, resource, 300_000.0)

        f = get_carveout_frontier(session, ra)
        assert f.carve_total == 500_000.0
        assert f.residual == 500_000.0
        assert f.open_projects == []   # mid is covered via gc

    def test_frontier_stops_at_allocated_child(self, session, resource):
        """A grandchild under an allocated child draws from the child, not the
        root — it must not be double-counted at the root level."""
        root = make_project(session)
        ra = _alloc(session, root, resource, 1_000_000.0)
        child = make_project(session, parent=root)
        _alloc(session, child, resource, 400_000.0)
        gc = make_project(session, parent=child)
        _alloc(session, gc, resource, 100_000.0)

        f = get_carveout_frontier(session, ra)
        assert f.carve_total == 400_000.0   # child only, not child + gc
        assert f.residual == 600_000.0

    def test_uncovered_branch_is_open_project(self, session, resource):
        root = make_project(session)
        ra = _alloc(session, root, resource, 1_000_000.0)
        bare = make_project(session, parent=root)
        make_project(session, parent=bare)   # deeper, also bare

        f = get_carveout_frontier(session, ra)
        assert [p.project_id for p in f.open_projects] == [bare.project_id]

    def test_inactive_child_branch_skipped(self, session, resource):
        root = make_project(session)
        ra = _alloc(session, root, resource, 1_000_000.0)
        inactive = make_project(session, parent=root, active=False)
        _alloc(session, inactive, resource, 400_000.0)

        f = get_carveout_frontier(session, ra)
        assert f.carve_children == []
        assert f.carve_total == 0.0
        assert f.open_projects == []   # inactive branches are ignored entirely


class TestFrontierScoping:

    def test_per_resource_divergence(self, session):
        """Subdivided on res1, shared pool on res2 — residual is per resource."""
        res1 = make_resource(session)
        res2 = make_resource(session)
        root = make_project(session)
        ra1 = _alloc(session, root, res1, 1_000_000.0)
        ra2 = _alloc(session, root, res2, 500_000.0)
        child = make_project(session, parent=root)
        _alloc(session, child, res1, 400_000.0)               # carve on res1
        _alloc(session, child, res2, 500_000.0, parent=ra2)   # pool on res2

        f1 = get_carveout_frontier(session, ra1)
        f2 = get_carveout_frontier(session, ra2)
        assert f1.carve_total == 400_000.0 and f1.residual == 600_000.0
        assert f2.carve_total == 0.0 and f2.residual == 500_000.0

    def test_date_disjoint_allocations_excluded(self, session, resource):
        """A descendant allocation in a disjoint fiscal year is unrelated —
        the branch counts as open, not carved."""
        now = datetime.now()
        root = make_project(session)
        ra = _alloc(session, root, resource, 1_000_000.0,
                    start_date=now - timedelta(days=30),
                    end_date=now + timedelta(days=335))
        child = make_project(session, parent=root)
        _alloc(session, child, resource, 400_000.0,
               start_date=now - timedelta(days=800),
               end_date=now - timedelta(days=436))

        f = get_carveout_frontier(session, ra)
        assert f.carve_children == []
        assert [p.project_id for p in f.open_projects] == [child.project_id]

    def test_multiple_overlapping_rows_all_count(self, session, resource):
        """Two sequential-year child rows inside a multi-year parent window
        both count (documented behavior, matches get_partitioned_descendant_sum)."""
        now = datetime.now()
        root = make_project(session)
        ra = _alloc(session, root, resource, 1_000_000.0,
                    start_date=now - timedelta(days=400),
                    end_date=now + timedelta(days=400))
        child = make_project(session, parent=root)
        account = make_account(session, project=child, resource=resource)
        make_allocation(session, account=account, amount=100_000.0,
                        start_date=now - timedelta(days=395),
                        end_date=now - timedelta(days=30))
        make_allocation(session, account=account, amount=150_000.0,
                        start_date=now - timedelta(days=29),
                        end_date=now + timedelta(days=335))

        f = get_carveout_frontier(session, ra)
        assert len(f.carve_children) == 2
        assert f.carve_total == 250_000.0

    def test_over_carved_clamps_residual(self, session, resource):
        root = make_project(session)
        ra = _alloc(session, root, resource, 1_000_000.0)
        c1 = make_project(session, parent=root)
        c2 = make_project(session, parent=root)
        _alloc(session, c1, resource, 700_000.0)
        _alloc(session, c2, resource, 500_000.0)

        f = get_carveout_frontier(session, ra)
        assert f.carve_total == 1_200_000.0
        assert f.raw_residual == -200_000.0
        assert f.residual == 0.0
