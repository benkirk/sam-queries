"""Allocation tree (master/child shared-allocation) tests — Phase 3 port.

Ported from tests/unit/test_allocation_tree.py. The legacy file pinned a
specific snapshot row (Allocation 6077 from NMMM0003) as the parent
fixture; obfuscation refreshes broke the test every cycle. The port
builds a fresh parent + children tree per test via factories.
"""
from datetime import datetime, timedelta

import pytest

from sam import Allocation
from sam.accounting.allocations import (
    AllocationTransaction,
    AllocationTransactionType,
    InheritingAllocationException,
)
from sam.manage.allocations import log_allocation_transaction, update_allocation

from factories import make_account, make_allocation, make_project, make_user

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures (function-scoped — each test gets its own savepoint-isolated tree)
# ---------------------------------------------------------------------------


@pytest.fixture
def parent_allocation(session):
    """Build a fresh parent (master) Allocation with 3 child allocations.

    The legacy fixture used Allocation 6077 from NMMM0003 (which had 11
    children at the time). We use 3 — enough to exercise tree iteration
    without making the build expensive.
    """
    account = make_account(session)
    parent = make_allocation(
        session,
        account=account,
        amount=300_000.0,
        start_date=datetime.now() - timedelta(days=30),
        end_date=datetime.now() + timedelta(days=365),
    )
    for _ in range(3):
        make_allocation(
            session,
            account=account,
            amount=100_000.0,
            start_date=parent.start_date,
            end_date=parent.end_date,
            parent=parent,
        )
    session.refresh(parent)
    return parent


@pytest.fixture
def child_allocation(parent_allocation):
    """Return the first child of `parent_allocation`."""
    return parent_allocation.children[0]


@pytest.fixture
def flat_root_allocation(session):
    """Build a fresh single-node Allocation (no children).

    The legacy fixture searched NRAL0002 for a leaf root allocation. A
    factory-built allocation with no children handles this case directly.
    """
    return make_allocation(
        session,
        amount=50_000.0,
        start_date=datetime.now() - timedelta(days=30),
        end_date=datetime.now() + timedelta(days=365),
    )


@pytest.fixture
def acting_user(session):
    """A fresh user to attribute audit-trail transactions to."""
    return make_user(session)


# ---------------------------------------------------------------------------
# is_inheriting
# ---------------------------------------------------------------------------


class TestIsInheriting:

    def test_root_allocation_not_inheriting(self, parent_allocation):
        assert parent_allocation.is_inheriting is False

    def test_child_allocation_is_inheriting(self, child_allocation):
        assert child_allocation.is_inheriting is True

    def test_flat_allocation_not_inheriting(self, flat_root_allocation):
        assert flat_root_allocation.is_inheriting is False

    def test_is_inheriting_matches_column(self, parent_allocation, child_allocation):
        assert parent_allocation.parent_allocation_id is None
        assert child_allocation.parent_allocation_id is not None


# ---------------------------------------------------------------------------
# _walk_tree
# ---------------------------------------------------------------------------


class TestWalkTree:

    def test_walk_visits_root(self, parent_allocation):
        visited: list[int] = []
        parent_allocation._walk_tree(lambda node: visited.append(node.allocation_id))
        assert parent_allocation.allocation_id in visited

    def test_walk_visits_all_children(self, parent_allocation):
        visited: list[int] = []
        parent_allocation._walk_tree(lambda node: visited.append(node.allocation_id))
        for child in parent_allocation.children:
            assert child.allocation_id in visited

    def test_walk_flat_allocation_visits_only_root(self, flat_root_allocation):
        visited: list[int] = []
        flat_root_allocation._walk_tree(lambda node: visited.append(node.allocation_id))
        assert visited == [flat_root_allocation.allocation_id]


# ---------------------------------------------------------------------------
# extend_allocation
# ---------------------------------------------------------------------------


class TestExtendAllocation:

    def test_raises_on_child(self, child_allocation, acting_user):
        new_end = child_allocation.end_date + timedelta(days=30)
        with pytest.raises(InheritingAllocationException):
            child_allocation.extend_allocation(new_end, user_id=acting_user.user_id)

    def test_raises_if_end_before_start(self, parent_allocation, acting_user):
        bad_end = parent_allocation.start_date - timedelta(days=1)
        with pytest.raises(ValueError):
            parent_allocation.extend_allocation(bad_end, user_id=acting_user.user_id)

    def test_extends_root_end_date(self, parent_allocation, acting_user):
        new_end = parent_allocation.end_date + timedelta(days=90)
        parent_allocation.extend_allocation(new_end, user_id=acting_user.user_id)
        assert parent_allocation.end_date == new_end

    def test_extends_all_children_end_date(self, session, parent_allocation, acting_user):
        child_ids = [c.allocation_id for c in parent_allocation.children]
        new_end = parent_allocation.end_date + timedelta(days=90)

        parent_allocation.extend_allocation(new_end, user_id=acting_user.user_id)

        for child_id in child_ids:
            child = session.get(Allocation, child_id)
            assert child.end_date == new_end, f"Child {child_id} end_date not updated"

    def test_root_transaction_not_propagated(self, session, parent_allocation, acting_user):
        new_end = parent_allocation.end_date + timedelta(days=90)
        parent_allocation.extend_allocation(new_end, user_id=acting_user.user_id)
        session.flush()

        txn = (
            session.query(AllocationTransaction)
            .filter_by(
                allocation_id=parent_allocation.allocation_id,
                transaction_type=AllocationTransactionType.EXTENSION,
            )
            .order_by(AllocationTransaction.allocation_transaction_id.desc())
            .first()
        )
        assert txn is not None
        assert txn.propagated is False

    def test_child_transactions_are_propagated(self, session, parent_allocation, acting_user):
        new_end = parent_allocation.end_date + timedelta(days=90)
        child = parent_allocation.children[0]
        parent_allocation.extend_allocation(new_end, user_id=acting_user.user_id)
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


# ---------------------------------------------------------------------------
# update_allocation guard
# ---------------------------------------------------------------------------


class TestUpdateAllocationGuard:

    def test_raises_on_child(self, session, child_allocation, acting_user):
        with pytest.raises(InheritingAllocationException):
            update_allocation(
                session,
                allocation_id=child_allocation.allocation_id,
                user_id=acting_user.user_id,
                amount=child_allocation.amount + 1,
            )

    def test_allows_root_update(self, session, flat_root_allocation, acting_user):
        new_amount = flat_root_allocation.amount + 1.0
        result = update_allocation(
            session,
            allocation_id=flat_root_allocation.allocation_id,
            user_id=acting_user.user_id,
            amount=new_amount,
        )
        assert result.amount == new_amount


# ---------------------------------------------------------------------------
# update_allocation cascade
# ---------------------------------------------------------------------------


class TestUpdateAllocationCascade:

    def test_amount_cascades_to_all_children(self, session, parent_allocation, acting_user):
        child_ids = [c.allocation_id for c in parent_allocation.children]
        new_amount = parent_allocation.amount + 100_000.0

        update_allocation(
            session,
            allocation_id=parent_allocation.allocation_id,
            user_id=acting_user.user_id,
            amount=new_amount,
        )

        for child_id in child_ids:
            child = session.get(Allocation, child_id)
            assert child.amount == new_amount, f"Child {child_id} amount not cascaded"

    def test_description_not_cascaded(self, session, parent_allocation, acting_user):
        original_child_desc = parent_allocation.children[0].description

        update_allocation(
            session,
            allocation_id=parent_allocation.allocation_id,
            user_id=acting_user.user_id,
            description="Updated parent description",
        )

        child = session.get(Allocation, parent_allocation.children[0].allocation_id)
        assert child.description == original_child_desc

    def test_child_transactions_marked_propagated(self, session, parent_allocation, acting_user):
        child = parent_allocation.children[0]
        new_amount = parent_allocation.amount + 100_000.0

        update_allocation(
            session,
            allocation_id=parent_allocation.allocation_id,
            user_id=acting_user.user_id,
            amount=new_amount,
        )
        session.flush()

        txn = (
            session.query(AllocationTransaction)
            .filter_by(
                allocation_id=child.allocation_id,
                transaction_type=AllocationTransactionType.EDIT,
            )
            .order_by(AllocationTransaction.allocation_transaction_id.desc())
            .first()
        )
        assert txn is not None
        assert txn.propagated is True

    def test_no_cascade_for_flat_allocation(self, session, flat_root_allocation, acting_user):
        new_amount = flat_root_allocation.amount + 1.0
        result = update_allocation(
            session,
            allocation_id=flat_root_allocation.allocation_id,
            user_id=acting_user.user_id,
            amount=new_amount,
        )
        assert result.amount == new_amount


# ---------------------------------------------------------------------------
# log_allocation_transaction propagated flag
# ---------------------------------------------------------------------------


class TestLogAllocationTransactionPropagated:

    def test_propagated_false_by_default(self, session, flat_root_allocation, acting_user):
        txn = log_allocation_transaction(
            session,
            flat_root_allocation,
            user_id=acting_user.user_id,
            transaction_type=AllocationTransactionType.EDIT,
        )
        assert txn.propagated is False

    def test_propagated_true_when_set(self, session, flat_root_allocation, acting_user):
        txn = log_allocation_transaction(
            session,
            flat_root_allocation,
            user_id=acting_user.user_id,
            transaction_type=AllocationTransactionType.EDIT,
            propagated=True,
        )
        assert txn.propagated is True
