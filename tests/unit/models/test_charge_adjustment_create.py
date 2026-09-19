"""Tests for ChargeAdjustment.create() and ChargeAdjustment.supported_types().

Exercises the Sign Enforcement pattern documented in
legacy_sam/doc/data_structures/charge_adjustments.md §2: the user supplies a
positive amount and the server multiplies by +1 (Debit / Reservation) or -1
(Credit / Refund) at write time so the stored amount is always the correct sign.

Each test builds a minimal Account via Layer 2 factories so two xdist workers
can't bleed state through the SAVEPOINT rollback.
"""
from datetime import datetime

import pytest

from sam.accounting.adjustments import ChargeAdjustment, ChargeAdjustmentType
from factories import make_account, make_user


pytestmark = pytest.mark.unit


def _type_by_name(session, name):
    t = (session.query(ChargeAdjustmentType)
                .filter(ChargeAdjustmentType.type == name)
                .first())
    if t is None:
        pytest.skip(f"No ChargeAdjustmentType row named {name!r} in test DB")
    return t


class TestSupportedTypes:
    """ChargeAdjustment.supported_types(session) returns the v1 set in order."""

    def test_returns_four_types_in_order(self, session):
        rows = ChargeAdjustment.supported_types(session)
        names = [r.type for r in rows]
        # Order matches _SIGN_BY_TYPE insertion order: Refund first (most-used
        # per legacy docs), then Credit, Debit, Reservation.
        assert names == ['Refund', 'Credit', 'Debit', 'Reservation']

    def test_excludes_storage_types(self, session):
        names = {r.type for r in ChargeAdjustment.supported_types(session)}
        assert 'Storage-Credit' not in names
        assert 'Storage-Debit' not in names


class TestCreateSignByType:
    """create() applies sign by type name regardless of input sign."""

    @pytest.mark.parametrize("type_name,expected_sign", [
        ('Refund', -1),
        ('Credit', -1),
        ('Debit', +1),
        ('Reservation', +1),
    ])
    def test_sign_applied(self, session, type_name, expected_sign):
        account = make_account(session)
        actor = make_user(session)
        adj_type = _type_by_name(session, type_name)

        adj = ChargeAdjustment.create(
            session,
            account_id=account.account_id,
            charge_adjustment_type_id=adj_type.charge_adjustment_type_id,
            amount=100.0,
            adjusted_by_id=actor.user_id,
        )

        assert adj.charge_adjustment_id is not None
        assert adj.amount == expected_sign * 100.0
        assert adj.account_id == account.account_id
        assert adj.adjusted_by_id == actor.user_id
        assert adj.comment is None

    def test_records_adjustment_date_near_now(self, session):
        account = make_account(session)
        actor = make_user(session)
        refund = _type_by_name(session, 'Refund')
        before = datetime.now()

        adj = ChargeAdjustment.create(
            session,
            account_id=account.account_id,
            charge_adjustment_type_id=refund.charge_adjustment_type_id,
            amount=50.0,
            adjusted_by_id=actor.user_id,
        )

        after = datetime.now()
        assert before <= adj.adjustment_date <= after

    def test_stores_comment(self, session):
        account = make_account(session)
        actor = make_user(session)
        credit = _type_by_name(session, 'Credit')

        adj = ChargeAdjustment.create(
            session,
            account_id=account.account_id,
            charge_adjustment_type_id=credit.charge_adjustment_type_id,
            amount=7.5,
            adjusted_by_id=actor.user_id,
            comment='ticket #1234',
        )

        assert adj.comment == 'ticket #1234'

    def test_blank_comment_stored_as_none(self, session):
        """create() normalizes empty/whitespace comment to NULL."""
        account = make_account(session)
        actor = make_user(session)
        refund = _type_by_name(session, 'Refund')

        adj = ChargeAdjustment.create(
            session,
            account_id=account.account_id,
            charge_adjustment_type_id=refund.charge_adjustment_type_id,
            amount=1.0,
            adjusted_by_id=actor.user_id,
            comment='',
        )

        assert adj.comment is None


class TestCreateValidation:
    """create() raises ValueError on bad inputs — defense in depth around the
    form schema (which also enforces positive amounts)."""

    def test_zero_amount_rejected(self, session):
        account = make_account(session)
        actor = make_user(session)
        refund = _type_by_name(session, 'Refund')

        with pytest.raises(ValueError, match="positive"):
            ChargeAdjustment.create(
                session,
                account_id=account.account_id,
                charge_adjustment_type_id=refund.charge_adjustment_type_id,
                amount=0,
                adjusted_by_id=actor.user_id,
            )

    def test_negative_amount_rejected(self, session):
        account = make_account(session)
        actor = make_user(session)
        refund = _type_by_name(session, 'Refund')

        with pytest.raises(ValueError, match="positive"):
            ChargeAdjustment.create(
                session,
                account_id=account.account_id,
                charge_adjustment_type_id=refund.charge_adjustment_type_id,
                amount=-5.0,
                adjusted_by_id=actor.user_id,
            )

    def test_unknown_type_id_rejected(self, session):
        account = make_account(session)
        actor = make_user(session)

        with pytest.raises(ValueError, match="not found"):
            ChargeAdjustment.create(
                session,
                account_id=account.account_id,
                charge_adjustment_type_id=99_999_999,
                amount=1.0,
                adjusted_by_id=actor.user_id,
            )

    def test_unsupported_type_name_rejected(self, session):
        """A type that exists in the DB but is not in _SIGN_BY_TYPE must be
        rejected — this is what guards disk/archive types from slipping into
        the compute-only v1 UI."""
        storage_credit = (
            session.query(ChargeAdjustmentType)
                   .filter(ChargeAdjustmentType.type == 'Storage-Credit')
                   .first()
        )
        if storage_credit is None:
            pytest.skip("No 'Storage-Credit' row in the reference data")
        account = make_account(session)
        actor = make_user(session)

        with pytest.raises(ValueError, match="not supported"):
            ChargeAdjustment.create(
                session,
                account_id=account.account_id,
                charge_adjustment_type_id=storage_credit.charge_adjustment_type_id,
                amount=1.0,
                adjusted_by_id=actor.user_id,
            )
