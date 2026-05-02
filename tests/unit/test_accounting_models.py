"""Tests for accounting ORM models: Contract and AllocationTransactionType.

Ported from tests/unit/test_accounting_models.py. Dropped decorative
print statements. Contract.is_active hybrid property is the main thing
under test — both Python and SQL filter paths.
"""
from datetime import datetime, timedelta

import pytest

from sam import AllocationTransactionType, Contract
from sam.accounting.allocations import (
    AllocationTransaction,
    LEGACY_TRANSACTION_TYPES,
    LEGACY_TYPE_MAP,
    parse_intent,
    replay_amount,
)
from sam.manage.allocations import (
    log_allocation_transaction,
    update_allocation,
)

from factories import make_allocation, make_user


pytestmark = pytest.mark.unit


class TestContractModel:

    def test_contract_query(self, session):
        contract = session.query(Contract).first()
        if not contract:
            pytest.skip("No contracts in database")
        assert contract.contract_number is not None
        assert contract.title is not None
        assert contract.principal_investigator is not None
        assert contract.start_date is not None

    def test_contract_is_active_property(self, session):
        """Contract.is_active Python-side matches is_active_at() helper."""
        contract = session.query(Contract).first()
        if not contract:
            pytest.skip("No contracts in database")
        assert isinstance(contract.is_active, bool)
        assert contract.is_active == contract.is_active_at()

    def test_contract_is_active_sql_filter(self, session):
        """Contract.is_active works in SQL filter context, and active+inactive = total."""
        active = session.query(Contract).filter(Contract.is_active).all()
        inactive = session.query(Contract).filter(~Contract.is_active).all()
        total = session.query(Contract).count()
        assert len(active) + len(inactive) == total
        for c in active:
            assert c.is_active is True


class TestAllocationTransactionType:

    def test_allocation_transaction_type_enum(self):
        """AllocationTransactionType is a StrEnum with both new + legacy values."""
        assert AllocationTransactionType.CREATE == "CREATE"
        assert isinstance(AllocationTransactionType.CREATE, str)
        expected_new = {"CREATE", "EDIT", "EXPIRE", "DELETE", "DETACH", "LINK", "RENEW"}
        expected_legacy = {"NEW", "ADJUSTMENT", "SUPPLEMENT", "EXTENSION", "TRANSFER"}
        expected = expected_new | expected_legacy
        actual = {m.value for m in AllocationTransactionType}
        assert actual == expected

    def test_legacy_type_map_covers_all_intents(self):
        """LEGACY_TYPE_MAP must have an entry for every enum value."""
        for member in AllocationTransactionType:
            assert member in LEGACY_TYPE_MAP, f"{member} missing from LEGACY_TYPE_MAP"

    def test_legacy_type_map_outputs_only_legacy_strings(self):
        """Every db_type in the map must be in LEGACY_TRANSACTION_TYPES."""
        for member, (db_type, _tag) in LEGACY_TYPE_MAP.items():
            assert db_type in LEGACY_TRANSACTION_TYPES, (
                f"{member} maps to {db_type!r} which is not a legacy DB string"
            )


# ---------------------------------------------------------------------------
# B3 invariant: log_allocation_transaction only ever writes legacy strings
# to allocation_transaction.transaction_type, regardless of which Python
# intent the caller passes. This is the property that lets us coexist with
# legacy SAM's Java enum validator without throwing on writes.
# ---------------------------------------------------------------------------


class TestLogAllocationTransactionEmitsLegacyStrings:

    @pytest.fixture
    def fresh_alloc(self, session):
        return make_allocation(
            session,
            amount=1000.0,
            start_date=datetime.now() - timedelta(days=30),
            end_date=datetime.now() + timedelta(days=365),
        )

    @pytest.fixture
    def acting_user(self, session):
        return make_user(session)

    @pytest.mark.parametrize("intent", list(AllocationTransactionType))
    def test_every_intent_stores_a_legacy_string(
        self, session, fresh_alloc, acting_user, intent
    ):
        """For every enum value, log_allocation_transaction emits a row
        whose transaction_type is one of the five legacy strings."""
        txn = log_allocation_transaction(
            session, fresh_alloc, acting_user.user_id, intent,
        )
        assert txn.transaction_type in LEGACY_TRANSACTION_TYPES, (
            f"intent={intent} stored as {txn.transaction_type!r}, "
            f"expected one of {sorted(LEGACY_TRANSACTION_TYPES)}"
        )

    @pytest.mark.parametrize("intent,expected_db,expected_tag", [
        (AllocationTransactionType.CREATE,    "NEW",        None),
        (AllocationTransactionType.RENEW,     "NEW",        "RENEW"),
        (AllocationTransactionType.EDIT,      "ADJUSTMENT", None),
        (AllocationTransactionType.EXPIRE,    "EXTENSION",  None),
        (AllocationTransactionType.DELETE,    "ADJUSTMENT", "DELETE"),
        (AllocationTransactionType.DETACH,    "ADJUSTMENT", "DETACH"),
        (AllocationTransactionType.LINK,      "ADJUSTMENT", "LINK"),
        (AllocationTransactionType.TRANSFER,  "TRANSFER",   None),
        (AllocationTransactionType.EXTENSION, "EXTENSION",  None),
    ])
    def test_specific_mapping(
        self, session, fresh_alloc, acting_user, intent, expected_db, expected_tag
    ):
        txn = log_allocation_transaction(
            session, fresh_alloc, acting_user.user_id, intent,
            comment="hello",
        )
        assert txn.transaction_type == expected_db
        if expected_tag is None:
            assert not (txn.transaction_comment or '').startswith('[')
        else:
            assert (txn.transaction_comment or '').startswith(f"[{expected_tag}]")

    @pytest.mark.parametrize("intent", [
        AllocationTransactionType.DELETE,
        AllocationTransactionType.DETACH,
        AllocationTransactionType.LINK,
    ])
    def test_no_amount_change_intents_write_zero_delta(
        self, session, fresh_alloc, acting_user, intent
    ):
        """DELETE/DETACH/LINK map to ADJUSTMENT but must not move the
        running amount under legacy replay — write 0 so addAmount is a no-op."""
        txn = log_allocation_transaction(
            session, fresh_alloc, acting_user.user_id, intent,
        )
        assert txn.transaction_amount == 0.0


class TestParseIntent:

    @pytest.fixture
    def acting_user(self, session):
        return make_user(session)

    @pytest.mark.parametrize("intent", [
        AllocationTransactionType.CREATE,
        AllocationTransactionType.RENEW,
        AllocationTransactionType.EDIT,
        AllocationTransactionType.DELETE,
        AllocationTransactionType.DETACH,
        AllocationTransactionType.LINK,
        AllocationTransactionType.TRANSFER,
        AllocationTransactionType.EXTENSION,
    ])
    def test_round_trip(self, session, acting_user, intent):
        """A row written with intent X should parse back to intent X.

        EDIT round-trips because EDIT is the canonical Python-side name
        for an untagged ADJUSTMENT row (DB has no native EDIT type).
        """
        alloc = make_allocation(
            session, amount=500.0,
            start_date=datetime.now(),
            end_date=datetime.now() + timedelta(days=30),
        )
        txn = log_allocation_transaction(
            session, alloc, acting_user.user_id, intent, comment="hello",
        )
        # ADJUSTMENT and EDIT both map to ADJUSTMENT untagged — collapses
        # to canonical EDIT on read.
        if intent == AllocationTransactionType.ADJUSTMENT:
            assert parse_intent(txn) == AllocationTransactionType.EDIT
        else:
            assert parse_intent(txn) == intent

    def test_legacy_pre_b3_row_parses(self, session):
        """Pre-B3 rows in prod (untagged ADJUSTMENT, NEW, etc.) must
        still parse to a sensible intent for display purposes."""
        alloc = make_allocation(session)
        # Simulate a row written by legacy SAM Java code (no [TAG] prefix)
        legacy = AllocationTransaction(
            allocation_id=alloc.allocation_id,
            transaction_type="NEW",
            transaction_amount=alloc.amount,
            transaction_comment="for year one",
            propagated=False,
        )
        session.add(legacy)
        session.flush()
        assert parse_intent(legacy) == AllocationTransactionType.CREATE


class TestReplayAmount:
    """Python port of legacy SAM's DateBoundedAllocationAmount replay.
    Used by Stream A backfill to prove corrective rows before INSERT."""

    @pytest.fixture
    def acting_user(self, session):
        return make_user(session)

    def test_single_new_row(self, session, acting_user):
        alloc = make_allocation(session, amount=1000.0)
        # Reset transactions: write a clean NEW.
        session.query(AllocationTransaction).filter_by(
            allocation_id=alloc.allocation_id,
        ).delete()
        log_allocation_transaction(
            session, alloc, acting_user.user_id, AllocationTransactionType.CREATE,
        )
        txns = session.query(AllocationTransaction).filter_by(
            allocation_id=alloc.allocation_id,
        ).all()
        assert replay_amount(txns) == pytest.approx(alloc.amount)

    def test_new_then_edit_replays_correctly(self, session, acting_user):
        alloc = make_allocation(session, amount=1000.0)
        session.query(AllocationTransaction).filter_by(
            allocation_id=alloc.allocation_id,
        ).delete()
        log_allocation_transaction(
            session, alloc, acting_user.user_id, AllocationTransactionType.CREATE,
        )
        update_allocation(
            session, alloc.allocation_id, acting_user.user_id, amount=1500.0,
        )
        txns = session.query(AllocationTransaction).filter_by(
            allocation_id=alloc.allocation_id,
        ).all()
        # NEW(1000) + ADJUSTMENT(+500) = 1500
        assert replay_amount(txns) == pytest.approx(1500.0)
        session.refresh(alloc)
        assert replay_amount(txns) == pytest.approx(alloc.amount)

    def test_cesm0002_bug_fingerprint(self, session, acting_user):
        """The CESM0002 production scenario, replicated in-memory:
        NEW 461, NEW 461 (duplicate), ADJUSTMENT 465 (POST-EDIT TOTAL,
        bug). Pre-fix replay would give 926. Post-fix, B1 ensures the
        EDIT row stores the +4 delta, so replay gives 465."""
        alloc = make_allocation(session, amount=461.0)
        session.query(AllocationTransaction).filter_by(
            allocation_id=alloc.allocation_id,
        ).delete()

        # Two NEW rows (mirroring B2-pre bug — kept here as a regression test
        # against legacy replay's idempotent-NEW semantics).
        for _ in range(2):
            log_allocation_transaction(
                session, alloc, acting_user.user_id,
                AllocationTransactionType.CREATE,
            )
        # Now an EDIT bumping amount by +4 (the real-world correction).
        update_allocation(
            session, alloc.allocation_id, acting_user.user_id, amount=465.0,
        )

        txns = session.query(AllocationTransaction).filter_by(
            allocation_id=alloc.allocation_id,
        ).order_by(AllocationTransaction.allocation_transaction_id.asc()).all()
        replayed = replay_amount(txns)
        # B1 fix: EDIT row carries +4 delta, not 465; legacy replay matches stored amount.
        assert replayed == pytest.approx(465.0)
        assert replayed != pytest.approx(926.0)
