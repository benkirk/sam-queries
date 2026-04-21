"""Tests for the Exchange Allocation flow:

- ``ExchangeAllocationForm`` (sam.schemas.forms.user): input shape validation.
- ``exchange_allocations()`` (sam.manage.allocations): move amount between
  two dedicated allocations on the same resource, preserving the combined
  total and writing paired TRANSFER audit rows.
"""
from datetime import datetime, timedelta

import pytest
from marshmallow import ValidationError

from sam import Allocation
from sam.accounting.allocations import (
    AllocationTransaction,
    AllocationTransactionType,
    InheritingAllocationException,
)
from sam.manage.allocations import exchange_allocations
from sam.schemas.forms import ExchangeAllocationForm

from factories import (
    make_account,
    make_allocation,
    make_project,
    make_resource,
    make_user,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Form schema
# ---------------------------------------------------------------------------


class TestExchangeAllocationForm:

    def _valid(self):
        return {'from_allocation_id': '1', 'to_allocation_id': '2', 'amount': '100'}

    def test_coerces_types(self):
        data = ExchangeAllocationForm().load(self._valid())
        assert data['from_allocation_id'] == 1
        assert data['to_allocation_id'] == 2
        assert data['amount'] == 100.0

    def test_missing_from_rejected(self):
        payload = self._valid()
        del payload['from_allocation_id']
        with pytest.raises(ValidationError) as ei:
            ExchangeAllocationForm().load(payload)
        assert 'from_allocation_id' in ei.value.messages

    def test_missing_amount_rejected(self):
        payload = self._valid()
        del payload['amount']
        with pytest.raises(ValidationError) as ei:
            ExchangeAllocationForm().load(payload)
        assert 'amount' in ei.value.messages

    def test_zero_amount_rejected(self):
        payload = self._valid()
        payload['amount'] = '0'
        with pytest.raises(ValidationError) as ei:
            ExchangeAllocationForm().load(payload)
        assert 'amount' in ei.value.messages

    def test_negative_amount_rejected(self):
        payload = self._valid()
        payload['amount'] = '-10'
        with pytest.raises(ValidationError) as ei:
            ExchangeAllocationForm().load(payload)
        assert 'amount' in ei.value.messages

    def test_same_from_and_to_rejected(self):
        payload = self._valid()
        payload['to_allocation_id'] = payload['from_allocation_id']
        with pytest.raises(ValidationError) as ei:
            ExchangeAllocationForm().load(payload)
        assert 'to_allocation_id' in ei.value.messages

    def test_unknown_fields_dropped(self):
        payload = self._valid()
        payload['csrf_token'] = 'abc'
        data = ExchangeAllocationForm().load(payload)
        assert 'csrf_token' not in data


# ---------------------------------------------------------------------------
# exchange_allocations — happy path + error cases
# ---------------------------------------------------------------------------


@pytest.fixture
def acting_user(session):
    return make_user(session)


@pytest.fixture
def exchange_pair(session):
    """Two dedicated allocations on the same resource, same project tree.

    Returns (from_alloc, to_alloc, resource) where:
      - parent project + child project share a tree (tree_root).
      - Each has its own Account on `resource` and its own dedicated
        (non-inheriting) Allocation on that account.
    """
    resource = make_resource(session)
    parent_project = make_project(session)
    child_project = make_project(session, parent=parent_project)

    parent_account = make_account(
        session, project=parent_project, resource=resource
    )
    child_account = make_account(
        session, project=child_project, resource=resource
    )

    start = datetime.now() - timedelta(days=30)
    end = datetime.now() + timedelta(days=365)

    from_alloc = make_allocation(
        session, account=parent_account,
        amount=1_000_000.0, start_date=start, end_date=end,
    )
    to_alloc = make_allocation(
        session, account=child_account,
        amount=200_000.0, start_date=start, end_date=end,
    )
    return from_alloc, to_alloc, resource


class TestExchangeAllocationsHappyPath:

    def test_conserves_combined_total(self, session, exchange_pair, acting_user):
        from_alloc, to_alloc, _ = exchange_pair
        total_before = from_alloc.amount + to_alloc.amount

        exchange_allocations(
            session,
            from_allocation_id=from_alloc.allocation_id,
            to_allocation_id=to_alloc.allocation_id,
            amount=150_000.0,
            user_id=acting_user.user_id,
        )

        session.refresh(from_alloc)
        session.refresh(to_alloc)
        assert from_alloc.amount + to_alloc.amount == total_before

    def test_applies_debit_and_credit(self, session, exchange_pair, acting_user):
        from_alloc, to_alloc, _ = exchange_pair
        original_from = from_alloc.amount
        original_to = to_alloc.amount

        exchange_allocations(
            session,
            from_allocation_id=from_alloc.allocation_id,
            to_allocation_id=to_alloc.allocation_id,
            amount=150_000.0,
            user_id=acting_user.user_id,
        )

        session.refresh(from_alloc)
        session.refresh(to_alloc)
        assert from_alloc.amount == original_from - 150_000.0
        assert to_alloc.amount == original_to + 150_000.0

    def test_writes_paired_transfer_rows(self, session, exchange_pair, acting_user):
        from_alloc, to_alloc, _ = exchange_pair

        exchange_allocations(
            session,
            from_allocation_id=from_alloc.allocation_id,
            to_allocation_id=to_alloc.allocation_id,
            amount=150_000.0,
            user_id=acting_user.user_id,
        )

        from_txns = (
            session.query(AllocationTransaction)
            .filter_by(
                allocation_id=from_alloc.allocation_id,
                transaction_type=AllocationTransactionType.TRANSFER,
            )
            .all()
        )
        to_txns = (
            session.query(AllocationTransaction)
            .filter_by(
                allocation_id=to_alloc.allocation_id,
                transaction_type=AllocationTransactionType.TRANSFER,
            )
            .all()
        )
        assert len(from_txns) == 1
        assert len(to_txns) == 1
        # Signed transaction_amount so TRANSFER rows are greppable by direction.
        assert from_txns[0].transaction_amount == -150_000.0
        assert to_txns[0].transaction_amount == 150_000.0

    def test_dates_unchanged(self, session, exchange_pair, acting_user):
        from_alloc, to_alloc, _ = exchange_pair
        # Refresh first — MySQL DATETIME truncates microseconds on write,
        # so the in-session Python values (set by the factory) differ
        # from what comes back after the exchange's flush/refresh.
        session.refresh(from_alloc)
        session.refresh(to_alloc)
        from_start, from_end = from_alloc.start_date, from_alloc.end_date
        to_start, to_end = to_alloc.start_date, to_alloc.end_date

        exchange_allocations(
            session,
            from_allocation_id=from_alloc.allocation_id,
            to_allocation_id=to_alloc.allocation_id,
            amount=50_000.0,
            user_id=acting_user.user_id,
        )

        session.refresh(from_alloc)
        session.refresh(to_alloc)
        assert from_alloc.start_date == from_start
        assert from_alloc.end_date == from_end
        assert to_alloc.start_date == to_start
        assert to_alloc.end_date == to_end


class TestExchangeAllocationsErrors:

    def test_rejects_same_from_and_to(self, session, exchange_pair, acting_user):
        from_alloc, _, _ = exchange_pair
        with pytest.raises(ValueError):
            exchange_allocations(
                session,
                from_allocation_id=from_alloc.allocation_id,
                to_allocation_id=from_alloc.allocation_id,
                amount=100.0,
                user_id=acting_user.user_id,
            )

    def test_rejects_non_positive_amount(self, session, exchange_pair, acting_user):
        from_alloc, to_alloc, _ = exchange_pair
        with pytest.raises(ValueError):
            exchange_allocations(
                session,
                from_allocation_id=from_alloc.allocation_id,
                to_allocation_id=to_alloc.allocation_id,
                amount=0.0,
                user_id=acting_user.user_id,
            )

    def test_rejects_amount_exceeding_from(self, session, exchange_pair, acting_user):
        from_alloc, to_alloc, _ = exchange_pair
        with pytest.raises(ValueError):
            exchange_allocations(
                session,
                from_allocation_id=from_alloc.allocation_id,
                to_allocation_id=to_alloc.allocation_id,
                amount=from_alloc.amount + 1.0,
                user_id=acting_user.user_id,
            )

    def test_rejects_inheriting_from(self, session, acting_user):
        resource = make_resource(session)
        account = make_account(session, resource=resource)
        parent = make_allocation(session, account=account, amount=1_000.0)
        child = make_allocation(
            session, account=account, amount=500.0, parent=parent
        )
        other_account = make_account(session, resource=resource)
        sibling = make_allocation(session, account=other_account, amount=1_000.0)

        with pytest.raises(InheritingAllocationException):
            exchange_allocations(
                session,
                from_allocation_id=child.allocation_id,
                to_allocation_id=sibling.allocation_id,
                amount=100.0,
                user_id=acting_user.user_id,
            )

    def test_rejects_different_resources(self, session, acting_user):
        r1 = make_resource(session)
        r2 = make_resource(session)
        alloc1 = make_allocation(
            session, account=make_account(session, resource=r1), amount=1_000.0
        )
        alloc2 = make_allocation(
            session, account=make_account(session, resource=r2), amount=1_000.0
        )
        with pytest.raises(ValueError):
            exchange_allocations(
                session,
                from_allocation_id=alloc1.allocation_id,
                to_allocation_id=alloc2.allocation_id,
                amount=100.0,
                user_id=acting_user.user_id,
            )

    def test_rejects_missing_allocation(self, session, exchange_pair, acting_user):
        _, to_alloc, _ = exchange_pair
        with pytest.raises(ValueError):
            exchange_allocations(
                session,
                from_allocation_id=999_999_999,
                to_allocation_id=to_alloc.allocation_id,
                amount=100.0,
                user_id=acting_user.user_id,
            )
