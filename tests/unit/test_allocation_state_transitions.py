"""Tests for allocation tree-relationship operations in sam.manage.allocations.

These four functions mutate the parent_allocation_id graph that the
shared-pool / deep-tree allocation model depends on. They were previously
uncovered:

  - propagate_allocation_to_subprojects: parent → descendant fan-out
  - detach_allocation: child → standalone (clear parent link)
  - link_allocation_to_parent: standalone → child (re-link)
  - get_partitioned_descendant_sum: sum of non-inheriting descendants
    overlapping a target allocation's date range

Topologies are built from scratch via factories — far-future dates so
the tests can never collide with snapshot data.
"""
from datetime import datetime

import pytest

from sam import Allocation, Project
from sam.accounting.allocations import (
    AllocationTransaction,
    AllocationTransactionType,
)
from sam.manage.allocations import (
    detach_allocation,
    get_partitioned_descendant_sum,
    link_allocation_to_parent,
    propagate_allocation_to_subprojects,
)
from sam.manage.transaction import management_transaction

from factories import make_account, make_allocation, make_project, make_resource, make_user


pytestmark = pytest.mark.unit


# Far-future window so we never collide with snapshot allocations.
FAR_START = datetime(2099, 1, 1)
FAR_END = datetime(2099, 12, 31, 23, 59, 59)
ROOT_AMOUNT = 1_000_000.0


def _build_tree(session, *, depth_two=False):
    """Build a project tree + a single resource + per-project accounts.

    Returns (resource, root, children, grandchild_or_none, accounts_by_project_id).
    With depth_two=True the tree is root → c1, c2 with c1 → grandchild.
    Otherwise the tree is just root → c1, c2 (depth 1).
    """
    resource = make_resource(session)
    root = make_project(session)
    c1 = make_project(session, parent=root)
    c2 = make_project(session, parent=root)
    grandchild = make_project(session, parent=c1) if depth_two else None

    projects = [root, c1, c2] + ([grandchild] if grandchild else [])
    accounts = {p.project_id: make_account(session, project=p, resource=resource)
                for p in projects}
    session.flush()
    session.expire_all()

    # Re-fetch after expire so attribute access loads fresh state.
    root = session.get(Project, root.project_id)
    return resource, root, accounts


# ---------------------------------------------------------------------------
# propagate_allocation_to_subprojects
# ---------------------------------------------------------------------------


class TestPropagateAllocationToSubprojects:
    """Cover the fan-out path + skip_existing arms."""

    def test_propagate_creates_child_allocations(self, session):
        resource, root, accounts = _build_tree(session, depth_two=True)
        user = make_user(session)

        root_alloc = make_allocation(
            session,
            account=accounts[root.project_id],
            amount=ROOT_AMOUNT,
            start_date=FAR_START,
            end_date=FAR_END,
        )
        descendants = root.get_descendants()

        with management_transaction(session):
            created, skipped = propagate_allocation_to_subprojects(
                session, root_alloc, descendants, user.user_id,
            )

        # All three descendants got a fresh allocation
        assert len(created) == 3
        assert skipped == []

        # Each new allocation inherits amount + dates from the parent
        for alloc in created:
            assert alloc.amount == ROOT_AMOUNT
            assert alloc.start_date == FAR_START
            assert alloc.end_date == FAR_END
            assert alloc.parent_allocation_id is not None

        # Audit log row exists for each, marked propagated=True
        for alloc in created:
            txn = session.query(AllocationTransaction).filter_by(
                allocation_id=alloc.allocation_id
            ).first()
            assert txn is not None
            assert txn.propagated is True

    def test_propagate_skips_descendants_with_existing_allocations(self, session):
        """skip_existing=True (default) → descendants with their own allocation
        are skipped, registered in alloc_map for grandchild linking."""
        resource, root, accounts = _build_tree(session, depth_two=True)
        user = make_user(session)

        # Pre-seed an allocation on c1 BEFORE propagation
        c1 = root.get_descendants()[0]
        existing = make_allocation(
            session,
            account=accounts[c1.project_id],
            amount=999.0,
            start_date=FAR_START,
            end_date=FAR_END,
        )

        root_alloc = make_allocation(
            session,
            account=accounts[root.project_id],
            amount=ROOT_AMOUNT,
            start_date=FAR_START,
            end_date=FAR_END,
        )

        with management_transaction(session):
            created, skipped = propagate_allocation_to_subprojects(
                session, root_alloc, root.get_descendants(), user.user_id,
            )

        # c1 was skipped (kept its 999.0 allocation), c2 + grandchild created
        assert len(skipped) == 1
        assert skipped[0].project_id == c1.project_id
        assert len(created) == 2
        # The pre-existing allocation is unchanged
        assert existing.amount == 999.0

    def test_propagate_skip_existing_false_raises(self, session):
        resource, root, accounts = _build_tree(session)
        user = make_user(session)

        c1 = root.get_descendants()[0]
        make_allocation(
            session,
            account=accounts[c1.project_id],
            amount=42.0,
            start_date=FAR_START,
            end_date=FAR_END,
        )

        root_alloc = make_allocation(
            session,
            account=accounts[root.project_id],
            amount=ROOT_AMOUNT,
            start_date=FAR_START,
            end_date=FAR_END,
        )

        with pytest.raises(ValueError, match="already has an allocation"):
            with management_transaction(session):
                propagate_allocation_to_subprojects(
                    session, root_alloc, root.get_descendants(), user.user_id,
                    skip_existing=False,
                )

    def test_propagate_skips_inactive_descendants(self, session):
        resource, root, accounts = _build_tree(session)
        user = make_user(session)

        # Deactivate c2
        c2 = root.get_descendants()[1]
        c2.active = False
        session.flush()

        root_alloc = make_allocation(
            session,
            account=accounts[root.project_id],
            amount=ROOT_AMOUNT,
            start_date=FAR_START,
            end_date=FAR_END,
        )

        with management_transaction(session):
            created, skipped = propagate_allocation_to_subprojects(
                session, root_alloc, root.get_descendants(), user.user_id,
            )

        # Only c1 got an allocation; c2 (inactive) was silently skipped
        assert len(created) == 1
        assert skipped == []


# ---------------------------------------------------------------------------
# detach_allocation
# ---------------------------------------------------------------------------


class TestDetachAllocation:

    def test_detach_clears_parent_link(self, session):
        resource, root, accounts = _build_tree(session)
        user = make_user(session)

        root_alloc = make_allocation(
            session, account=accounts[root.project_id],
            amount=ROOT_AMOUNT, start_date=FAR_START, end_date=FAR_END,
        )
        c1 = root.get_descendants()[0]
        child_alloc = make_allocation(
            session, account=accounts[c1.project_id],
            amount=ROOT_AMOUNT, start_date=FAR_START, end_date=FAR_END,
            parent=root_alloc,
        )
        assert child_alloc.parent_allocation_id == root_alloc.allocation_id

        with management_transaction(session):
            detached = detach_allocation(session, child_alloc.allocation_id, user.user_id)

        assert detached.parent_allocation_id is None

        # DETACH transaction logged
        txn = session.query(AllocationTransaction).filter_by(
            allocation_id=child_alloc.allocation_id,
            transaction_type=AllocationTransactionType.DETACH,
        ).first()
        assert txn is not None
        assert f"#{root_alloc.allocation_id}" in (txn.transaction_comment or "")

    def test_detach_nonexistent_raises(self, session):
        user = make_user(session)
        with pytest.raises(ValueError, match="not found or is not an inheriting"):
            with management_transaction(session):
                detach_allocation(session, 999_999_999, user.user_id)

    def test_detach_standalone_raises(self, session):
        """A non-inheriting allocation can't be detached — there's no parent."""
        resource, root, accounts = _build_tree(session)
        user = make_user(session)

        standalone = make_allocation(
            session, account=accounts[root.project_id],
            amount=ROOT_AMOUNT, start_date=FAR_START, end_date=FAR_END,
        )
        assert standalone.parent_allocation_id is None

        with pytest.raises(ValueError, match="not an inheriting"):
            with management_transaction(session):
                detach_allocation(session, standalone.allocation_id, user.user_id)


# ---------------------------------------------------------------------------
# link_allocation_to_parent
# ---------------------------------------------------------------------------


class TestLinkAllocationToParent:
    """Cover the validation arms + happy path of re-linking a standalone
    child allocation to its immediate-parent project's allocation."""

    def test_link_success_mirrors_parent_fields(self, session):
        resource, root, accounts = _build_tree(session)
        user = make_user(session)

        root_alloc = make_allocation(
            session, account=accounts[root.project_id],
            amount=ROOT_AMOUNT, start_date=FAR_START, end_date=FAR_END,
        )
        c1 = root.get_descendants()[0]
        # Standalone child allocation with DIFFERENT amount/dates
        child_alloc = make_allocation(
            session, account=accounts[c1.project_id],
            amount=42.0,
            start_date=datetime(2098, 1, 1),
            end_date=datetime(2098, 6, 30, 23, 59, 59),
        )

        with management_transaction(session):
            linked = link_allocation_to_parent(
                session, child_alloc.allocation_id, root_alloc.allocation_id,
                user.user_id,
            )

        # Child now points at parent and mirrors parent's fields
        assert linked.parent_allocation_id == root_alloc.allocation_id
        assert linked.amount == ROOT_AMOUNT
        assert linked.start_date == FAR_START
        assert linked.end_date == FAR_END

        txn = session.query(AllocationTransaction).filter_by(
            allocation_id=linked.allocation_id,
            transaction_type=AllocationTransactionType.LINK,
        ).first()
        assert txn is not None

    def test_link_already_inheriting_raises(self, session):
        resource, root, accounts = _build_tree(session)
        user = make_user(session)

        root_alloc = make_allocation(
            session, account=accounts[root.project_id],
            amount=ROOT_AMOUNT, start_date=FAR_START, end_date=FAR_END,
        )
        c1 = root.get_descendants()[0]
        already_linked = make_allocation(
            session, account=accounts[c1.project_id],
            amount=ROOT_AMOUNT, start_date=FAR_START, end_date=FAR_END,
            parent=root_alloc,
        )

        with pytest.raises(ValueError, match="already inheriting"):
            with management_transaction(session):
                link_allocation_to_parent(
                    session, already_linked.allocation_id,
                    root_alloc.allocation_id, user.user_id,
                )

    def test_link_unknown_child_raises(self, session):
        resource, root, accounts = _build_tree(session)
        user = make_user(session)
        root_alloc = make_allocation(
            session, account=accounts[root.project_id],
            amount=ROOT_AMOUNT, start_date=FAR_START, end_date=FAR_END,
        )
        with pytest.raises(ValueError, match="Allocation .* not found"):
            with management_transaction(session):
                link_allocation_to_parent(
                    session, 999_999_999, root_alloc.allocation_id, user.user_id,
                )

    def test_link_unknown_parent_raises(self, session):
        resource, root, accounts = _build_tree(session)
        user = make_user(session)
        c1 = root.get_descendants()[0]
        child_alloc = make_allocation(
            session, account=accounts[c1.project_id],
            amount=42.0, start_date=FAR_START, end_date=FAR_END,
        )
        with pytest.raises(ValueError, match="Parent allocation .* not found"):
            with management_transaction(session):
                link_allocation_to_parent(
                    session, child_alloc.allocation_id, 999_999_999, user.user_id,
                )

    def test_link_resource_mismatch_raises(self, session):
        """Child allocation on resource A cannot link to parent on resource B."""
        resource, root, accounts = _build_tree(session)
        other_resource = make_resource(session)
        user = make_user(session)

        # Account on root for OTHER resource
        from factories import make_account as _make_account
        other_root_account = _make_account(session, project=root, resource=other_resource)
        root_alloc_other_resource = make_allocation(
            session, account=other_root_account,
            amount=ROOT_AMOUNT, start_date=FAR_START, end_date=FAR_END,
        )

        c1 = root.get_descendants()[0]
        child_alloc_main = make_allocation(
            session, account=accounts[c1.project_id],
            amount=42.0, start_date=FAR_START, end_date=FAR_END,
        )

        with pytest.raises(ValueError, match="different resources"):
            with management_transaction(session):
                link_allocation_to_parent(
                    session, child_alloc_main.allocation_id,
                    root_alloc_other_resource.allocation_id, user.user_id,
                )

    def test_link_non_immediate_parent_raises(self, session):
        """Deep-tree links must point at the IMMEDIATE project parent —
        a grandchild cannot link directly to the root's allocation."""
        resource, root, accounts = _build_tree(session, depth_two=True)
        user = make_user(session)

        root_alloc = make_allocation(
            session, account=accounts[root.project_id],
            amount=ROOT_AMOUNT, start_date=FAR_START, end_date=FAR_END,
        )
        # Find the grandchild — depth_two=True puts it last
        grandchild = root.get_descendants()[-1]
        gc_alloc = make_allocation(
            session, account=accounts[grandchild.project_id],
            amount=42.0, start_date=FAR_START, end_date=FAR_END,
        )

        with pytest.raises(ValueError, match="not the immediate parent"):
            with management_transaction(session):
                link_allocation_to_parent(
                    session, gc_alloc.allocation_id, root_alloc.allocation_id,
                    user.user_id,
                )


# ---------------------------------------------------------------------------
# get_partitioned_descendant_sum
# ---------------------------------------------------------------------------


class TestGetPartitionedDescendantSum:

    def test_sum_returns_zero_when_no_children(self, session):
        """A leaf project has no descendants — sum is 0."""
        resource = make_resource(session)
        leaf = make_project(session)
        account = make_account(session, project=leaf, resource=resource)
        alloc = make_allocation(
            session, account=account,
            amount=ROOT_AMOUNT, start_date=FAR_START, end_date=FAR_END,
        )
        assert get_partitioned_descendant_sum(session, alloc) == 0.0

    def test_sum_returns_zero_when_descendants_inherit(self, session):
        """If all descendants are inheriting (parent_allocation_id set),
        nothing is "partitioned" — sum is 0."""
        resource, root, accounts = _build_tree(session)
        root_alloc = make_allocation(
            session, account=accounts[root.project_id],
            amount=ROOT_AMOUNT, start_date=FAR_START, end_date=FAR_END,
        )
        # All children point at root_alloc
        for d in root.get_descendants():
            make_allocation(
                session, account=accounts[d.project_id],
                amount=ROOT_AMOUNT, start_date=FAR_START, end_date=FAR_END,
                parent=root_alloc,
            )
        assert get_partitioned_descendant_sum(session, root_alloc) == 0.0

    def test_sum_aggregates_standalone_descendants(self, session):
        """Standalone descendant allocations on the same resource within an
        overlapping date range are summed."""
        resource, root, accounts = _build_tree(session)
        root_alloc = make_allocation(
            session, account=accounts[root.project_id],
            amount=ROOT_AMOUNT, start_date=FAR_START, end_date=FAR_END,
        )
        descendants = root.get_descendants()
        make_allocation(
            session, account=accounts[descendants[0].project_id],
            amount=300.0, start_date=FAR_START, end_date=FAR_END,
        )
        make_allocation(
            session, account=accounts[descendants[1].project_id],
            amount=700.0, start_date=FAR_START, end_date=FAR_END,
        )

        assert get_partitioned_descendant_sum(session, root_alloc) == 1000.0

    def test_sum_excludes_non_overlapping_dates(self, session):
        """Descendant allocation in a different fiscal year does NOT count."""
        resource, root, accounts = _build_tree(session)
        root_alloc = make_allocation(
            session, account=accounts[root.project_id],
            amount=ROOT_AMOUNT, start_date=FAR_START, end_date=FAR_END,
        )
        c1 = root.get_descendants()[0]
        # Different year — does not overlap FAR_START..FAR_END
        make_allocation(
            session, account=accounts[c1.project_id],
            amount=12345.0,
            start_date=datetime(2098, 1, 1),
            end_date=datetime(2098, 12, 31, 23, 59, 59),
        )

        assert get_partitioned_descendant_sum(session, root_alloc) == 0.0
