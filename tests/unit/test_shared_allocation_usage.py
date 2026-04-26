"""Tree-aware usage for shared (inheriting) allocations.

When an allocation tree is shared across a project hierarchy
(parent → child → grandchild), child projects must surface the
*root* tree's full usage as `used`/`remaining`/`percent_used`, plus
their own contribution as `self_used`/`self_percent_used`. The UI
needs both to render a two-tone progress bar.
"""
from datetime import date, datetime, timedelta

import pytest

from sam.summaries.comp_summaries import CompChargeSummary

from factories import (
    make_account,
    make_allocation,
    make_project,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def shared_tree(session, hpc_resource):
    """Three-deep project tree sharing one allocation pool.

        root  (allocation root, amount=1000)
         └── mid (inheriting child)
              └── leaf (inheriting grandchild)

    Each project has its own Account on the HPC resource. Charges live
    only on the leaf (200) and the mid project (300); root has none —
    so the tree total is 500 against an allocation of 1000.
    """
    root_p = make_project(session)
    mid_p = make_project(session, parent=root_p)
    leaf_p = make_project(session, parent=mid_p)

    root_acct = make_account(session, project=root_p, resource=hpc_resource)
    mid_acct = make_account(session, project=mid_p, resource=hpc_resource)
    leaf_acct = make_account(session, project=leaf_p, resource=hpc_resource)

    start = datetime.now() - timedelta(days=30)
    end = datetime.now() + timedelta(days=365)

    root_alloc = make_allocation(
        session, account=root_acct, amount=1000.0,
        start_date=start, end_date=end,
    )
    mid_alloc = make_allocation(
        session, account=mid_acct, amount=1000.0,
        start_date=start, end_date=end, parent=root_alloc,
    )
    leaf_alloc = make_allocation(
        session, account=leaf_acct, amount=1000.0,
        start_date=start, end_date=end, parent=mid_alloc,
    )

    today = date.today()
    session.add(CompChargeSummary(
        account_id=mid_acct.account_id, activity_date=today, charges=300.0,
        machine='test-mach', queue='test-queue',
    ))
    session.add(CompChargeSummary(
        account_id=leaf_acct.account_id, activity_date=today, charges=200.0,
        machine='test-mach', queue='test-queue',
    ))
    session.flush()

    # NestedSetMixin._ns_place_in_tree() updates ancestor tree_right via
    # raw SQL when descendants are added — refresh the in-memory rows so
    # subsequent get_subtree_charges() queries see the current coords.
    session.refresh(root_p)
    session.refresh(mid_p)

    return {
        'root': root_p, 'mid': mid_p, 'leaf': leaf_p,
        'resource': hpc_resource,
        'root_alloc': root_alloc, 'mid_alloc': mid_alloc, 'leaf_alloc': leaf_alloc,
    }


class TestAllocationRoot:
    def test_root_of_root_is_self(self, shared_tree):
        assert shared_tree['root_alloc'].root is shared_tree['root_alloc']

    def test_root_of_grandchild_walks_to_top(self, shared_tree):
        assert shared_tree['leaf_alloc'].root is shared_tree['root_alloc']


class TestSharedAllocationUsage:
    """Verify get_detailed_allocation_usage() returns tree-truth on
    inheriting allocations and self contribution alongside it.
    """

    def test_root_sees_full_tree_usage(self, shared_tree):
        usage = shared_tree['root'].get_detailed_allocation_usage(
            include_adjustments=True,
        )
        rname = shared_tree['resource'].resource_name
        row = usage[rname]
        assert row['is_inheriting'] is False
        assert row['used'] == pytest.approx(500.0)
        assert row['remaining'] == pytest.approx(500.0)
        # Non-inheriting: no self_* fields surfaced.
        assert 'self_used' not in row

    def test_leaf_used_reflects_root_tree(self, shared_tree):
        usage = shared_tree['leaf'].get_detailed_allocation_usage(
            include_adjustments=True,
        )
        rname = shared_tree['resource'].resource_name
        row = usage[rname]
        assert row['is_inheriting'] is True
        # `used` is the full tree consumption — same number the root sees.
        assert row['used'] == pytest.approx(500.0)
        assert row['remaining'] == pytest.approx(500.0)
        # `self_used` is just leaf's own subtree (it's a leaf → just itself).
        assert row['self_used'] == pytest.approx(200.0)
        assert row['root_projcode'] == shared_tree['root'].projcode

    def test_mid_self_used_includes_descendants(self, shared_tree):
        """`self_used` is the project's OWN subtree contribution — for a
        non-leaf, that includes its descendants.
        """
        usage = shared_tree['mid'].get_detailed_allocation_usage(
            include_adjustments=True,
        )
        rname = shared_tree['resource'].resource_name
        row = usage[rname]
        assert row['is_inheriting'] is True
        # Mid's subtree = mid (300) + leaf (200) = 500
        assert row['self_used'] == pytest.approx(500.0)
        # `used` (the root tree) is also 500 here because the root
        # itself has no charges; matches the parent's view.
        assert row['used'] == pytest.approx(500.0)

    def test_self_percent_used_present_only_when_inheriting(self, shared_tree):
        rname = shared_tree['resource'].resource_name
        leaf_row = shared_tree['leaf'].get_detailed_allocation_usage(
            include_adjustments=True,
        )[rname]
        root_row = shared_tree['root'].get_detailed_allocation_usage(
            include_adjustments=True,
        )[rname]
        assert leaf_row['self_percent_used'] == pytest.approx(20.0)
        assert 'self_percent_used' not in root_row
