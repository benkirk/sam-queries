"""
Allocation Tree (Shared Allocations) Tests

Tests for the hierarchical allocation / master-only-write semantics:
- is_inheriting property
- _walk_tree recursive traversal
- extend_allocation domain method (cascades end_date across tree)
- update_allocation guard (blocks direct child mutation)
- update_allocation cascade (propagates amount/date changes to children)
- propagated flag set correctly on cascaded transactions
"""

import pytest
from datetime import datetime, timedelta

from sam import Allocation
from sam.accounting.allocations import (
    AllocationTransaction, AllocationTransactionType,
    InheritingAllocationException,
)
from sam.manage.allocations import log_allocation_transaction, update_allocation


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def nmmm_parent_allocation(session):
    """
    Return a known root (master) allocation from NMMM0003 that has children.
    Allocation 6077 has 11 child allocations across child projects.
    """
    alloc = session.get(Allocation, 6077)
    if alloc is None:
        pytest.skip("Allocation 6077 not found in database")
    if not alloc.children:
        pytest.skip("Allocation 6077 has no children")
    return alloc


@pytest.fixture
def nmmm_child_allocation(session, nmmm_parent_allocation):
    """Return the first child of the NMMM parent allocation."""
    return nmmm_parent_allocation.children[0]


@pytest.fixture
def flat_root_allocation(session):
    """
    Return a root allocation from NRAL0002 (flat project, no children).
    Used to verify non-tree allocations work correctly.
    """
    from sam import Project
    project = Project.get_by_projcode(session, 'NRAL0002')
    if not project:
        pytest.skip("NRAL0002 not found")
    for account in project.accounts:
        for alloc in account.allocations:
            if not alloc.is_inheriting and not alloc.children:
                return alloc
    pytest.skip("No flat root allocation found in NRAL0002")


# ---------------------------------------------------------------------------
# is_inheriting
# ---------------------------------------------------------------------------

class TestIsInheriting:

    def test_root_allocation_not_inheriting(self, nmmm_parent_allocation):
        assert nmmm_parent_allocation.is_inheriting is False

    def test_child_allocation_is_inheriting(self, nmmm_child_allocation):
        assert nmmm_child_allocation.is_inheriting is True

    def test_flat_allocation_not_inheriting(self, flat_root_allocation):
        assert flat_root_allocation.is_inheriting is False

    def test_is_inheriting_matches_column(self, nmmm_parent_allocation, nmmm_child_allocation):
        assert nmmm_parent_allocation.parent_allocation_id is None
        assert nmmm_child_allocation.parent_allocation_id is not None


# ---------------------------------------------------------------------------
# _walk_tree
# ---------------------------------------------------------------------------

class TestWalkTree:

    def test_walk_visits_root(self, nmmm_parent_allocation):
        visited = []
        nmmm_parent_allocation._walk_tree(lambda node: visited.append(node.allocation_id))
        assert nmmm_parent_allocation.allocation_id in visited

    def test_walk_visits_all_children(self, nmmm_parent_allocation):
        visited = []
        nmmm_parent_allocation._walk_tree(lambda node: visited.append(node.allocation_id))
        for child in nmmm_parent_allocation.children:
            assert child.allocation_id in visited

    def test_walk_flat_allocation_visits_only_root(self, flat_root_allocation):
        visited = []
        flat_root_allocation._walk_tree(lambda node: visited.append(node.allocation_id))
        assert visited == [flat_root_allocation.allocation_id]


# ---------------------------------------------------------------------------
# extend_allocation
# ---------------------------------------------------------------------------

class TestExtendAllocation:

    def test_raises_on_child(self, nmmm_child_allocation):
        new_end = nmmm_child_allocation.end_date + timedelta(days=30)
        with pytest.raises(InheritingAllocationException):
            nmmm_child_allocation.extend_allocation(new_end, user_id=1)

    def test_raises_if_end_before_start(self, nmmm_parent_allocation):
        bad_end = nmmm_parent_allocation.start_date - timedelta(days=1)
        with pytest.raises(ValueError):
            nmmm_parent_allocation.extend_allocation(bad_end, user_id=1)

    def test_extends_root_end_date(self, session, nmmm_parent_allocation):
        original_end = nmmm_parent_allocation.end_date
        new_end = (original_end or nmmm_parent_allocation.start_date) + timedelta(days=90)

        nmmm_parent_allocation.extend_allocation(new_end, user_id=1)

        assert nmmm_parent_allocation.end_date == new_end
        session.rollback()

    def test_extends_all_children_end_date(self, session, nmmm_parent_allocation):
        child_ids = [c.allocation_id for c in nmmm_parent_allocation.children]
        original_end = nmmm_parent_allocation.end_date
        new_end = (original_end or nmmm_parent_allocation.start_date) + timedelta(days=90)

        nmmm_parent_allocation.extend_allocation(new_end, user_id=1)

        for child_id in child_ids:
            child = session.get(Allocation, child_id)
            assert child.end_date == new_end, f"Child {child_id} end_date not updated"
        session.rollback()

    def test_root_transaction_not_propagated(self, session, nmmm_parent_allocation):
        original_end = nmmm_parent_allocation.end_date
        new_end = (original_end or nmmm_parent_allocation.start_date) + timedelta(days=90)

        nmmm_parent_allocation.extend_allocation(new_end, user_id=1)
        session.flush()

        # Find the EXTENSION transaction for the root allocation
        txn = (
            session.query(AllocationTransaction)
            .filter_by(
                allocation_id=nmmm_parent_allocation.allocation_id,
                transaction_type=AllocationTransactionType.EXTENSION,
            )
            .order_by(AllocationTransaction.allocation_transaction_id.desc())
            .first()
        )
        assert txn is not None
        assert txn.propagated is False
        session.rollback()

    def test_child_transactions_are_propagated(self, session, nmmm_parent_allocation):
        original_end = nmmm_parent_allocation.end_date
        new_end = (original_end or nmmm_parent_allocation.start_date) + timedelta(days=90)
        child = nmmm_parent_allocation.children[0]

        nmmm_parent_allocation.extend_allocation(new_end, user_id=1)
        session.flush()

        txn = (
            session.query(AllocationTransaction)
            .filter_by(
                allocation_id=child.allocation_id,
                transaction_type=AllocationTransactionType.EXTENSION,
            )
            .order_by(AllocationTransaction.allocation_transaction_id.desc())
            .first()
        )
        assert txn is not None
        assert txn.propagated is True
        session.rollback()


# ---------------------------------------------------------------------------
# update_allocation guard
# ---------------------------------------------------------------------------

class TestUpdateAllocationGuard:

    def test_raises_on_child(self, session, nmmm_child_allocation):
        with pytest.raises(InheritingAllocationException):
            update_allocation(
                session,
                allocation_id=nmmm_child_allocation.allocation_id,
                user_id=1,
                amount=nmmm_child_allocation.amount + 1,
            )
        session.rollback()

    def test_allows_root_update(self, session, flat_root_allocation):
        new_amount = flat_root_allocation.amount + 1.0
        result = update_allocation(
            session,
            allocation_id=flat_root_allocation.allocation_id,
            user_id=1,
            amount=new_amount,
        )
        assert result.amount == new_amount
        session.rollback()


# ---------------------------------------------------------------------------
# update_allocation cascade
# ---------------------------------------------------------------------------

class TestUpdateAllocationCascade:

    def test_amount_cascades_to_all_children(self, session, nmmm_parent_allocation):
        child_ids = [c.allocation_id for c in nmmm_parent_allocation.children]
        new_amount = nmmm_parent_allocation.amount + 100_000.0

        update_allocation(
            session,
            allocation_id=nmmm_parent_allocation.allocation_id,
            user_id=1,
            amount=new_amount,
        )

        for child_id in child_ids:
            child = session.get(Allocation, child_id)
            assert child.amount == new_amount, f"Child {child_id} amount not cascaded"
        session.rollback()

    def test_description_not_cascaded(self, session, nmmm_parent_allocation):
        original_child_desc = nmmm_parent_allocation.children[0].description

        update_allocation(
            session,
            allocation_id=nmmm_parent_allocation.allocation_id,
            user_id=1,
            description="Updated parent description",
        )

        # Child description must be unchanged
        child = session.get(Allocation, nmmm_parent_allocation.children[0].allocation_id)
        assert child.description == original_child_desc
        session.rollback()

    def test_child_transactions_marked_propagated(self, session, nmmm_parent_allocation):
        child = nmmm_parent_allocation.children[0]
        new_amount = nmmm_parent_allocation.amount + 100_000.0

        update_allocation(
            session,
            allocation_id=nmmm_parent_allocation.allocation_id,
            user_id=1,
            amount=new_amount,
        )
        session.flush()

        txn = (
            session.query(AllocationTransaction)
            .filter_by(allocation_id=child.allocation_id, transaction_type=AllocationTransactionType.EDIT)
            .order_by(AllocationTransaction.allocation_transaction_id.desc())
            .first()
        )
        assert txn is not None
        assert txn.propagated is True
        session.rollback()

    def test_no_cascade_for_flat_allocation(self, session, flat_root_allocation):
        """Flat allocations with no children should update without error."""
        new_amount = flat_root_allocation.amount + 1.0
        result = update_allocation(
            session,
            allocation_id=flat_root_allocation.allocation_id,
            user_id=1,
            amount=new_amount,
        )
        assert result.amount == new_amount
        session.rollback()


# ---------------------------------------------------------------------------
# log_allocation_transaction propagated flag
# ---------------------------------------------------------------------------

class TestLogAllocationTransactionPropagated:

    def test_propagated_false_by_default(self, session, flat_root_allocation):
        txn = log_allocation_transaction(
            session, flat_root_allocation, user_id=1,
            transaction_type=AllocationTransactionType.EDIT,
        )
        assert txn.propagated is False
        session.rollback()

    def test_propagated_true_when_set(self, session, flat_root_allocation):
        txn = log_allocation_transaction(
            session, flat_root_allocation, user_id=1,
            transaction_type=AllocationTransactionType.EDIT,
            propagated=True,
        )
        assert txn.propagated is True
        session.rollback()
