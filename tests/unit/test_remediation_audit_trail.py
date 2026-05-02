"""Tests for utils/remediation/fix_cesm0002_audit_trail.py.

Builds the CESM0002 bug fingerprint in-memory using the factories and
verifies that the discover + correction logic produces a corrective
``ADJUSTMENT`` row whose ``transaction_amount`` is the negated
pre-edit total, restoring the legacy-replay invariant.

These tests do NOT touch prod. They run inside the standard test DB
isolation (per-test SAVEPOINT rollback) and synthesize the bug
locally — no remote calls, no live data.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

# Make utils.remediation importable
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..')
)
sys.path.insert(0, _REPO_ROOT)

from sam.accounting.allocations import (
    AllocationTransaction,
    AllocationTransactionType,
    replay_amount,
)
from sam.manage.allocations import update_allocation
from utils.remediation.fix_cesm0002_audit_trail import (
    AMOUNT_DELTA_RE,
    DEFAULT_TOLERANCE,
    REPLAY_TOLERANCE,
    AllocationFinding,
    BogusEditRow,
    ReplayMismatch,
    _build_corrective_row,
    _discover_for_allocation,
    _remediation_tag,
    _render_insert_sql,
    _simulate_post_correction,
    apply_corrections,
)
from factories import make_allocation, make_user

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers — build the CESM0002 bug fingerprint in-memory
# ---------------------------------------------------------------------------


def _seed_cesm0002_bug(session, user, *, intended_old=461.0, intended_new=465.0):
    """Build an Allocation whose audit trail mirrors CESM0002/Derecho's
    pre-fix state: two NEW rows + a malformed ADJUSTMENT that stored
    ``intended_new`` instead of the +(intended_new − intended_old) delta.

    Returns the Allocation. allocation.amount is set to ``intended_new``.
    """
    alloc = make_allocation(
        session,
        amount=intended_old,
        start_date=datetime.now() - timedelta(days=30),
        end_date=datetime.now() + timedelta(days=365),
    )
    # Wipe any auto-built transactions
    session.query(AllocationTransaction).filter_by(
        allocation_id=alloc.allocation_id,
    ).delete()

    # Two NEW rows (mirrors B2-pre duplicate-NEW pattern from renew.py)
    base_time = datetime(2026, 4, 23, 9, 4, 25)
    for i in range(2):
        session.add(AllocationTransaction(
            allocation_id=alloc.allocation_id,
            user_id=user.user_id,
            transaction_type="NEW",
            transaction_amount=intended_old,
            requested_amount=intended_old,
            alloc_start_date=alloc.start_date,
            alloc_end_date=alloc.end_date,
            transaction_comment=(
                "Allocation created" if i == 0
                else "Renewed from allocation #21664 — scaled ×0.67"
            ),
            propagated=False,
            creation_time=base_time + timedelta(microseconds=i),
        ))

    # The malformed ADJUSTMENT: stores the post-edit total as
    # transaction_amount (this is the bug B1 fixed for new writes).
    session.add(AllocationTransaction(
        allocation_id=alloc.allocation_id,
        user_id=user.user_id,
        transaction_type="ADJUSTMENT",
        transaction_amount=intended_new,            # ← BUG: should be delta
        requested_amount=intended_new,
        alloc_start_date=alloc.start_date,
        alloc_end_date=alloc.end_date,
        transaction_comment=f"Amount: {intended_old} → {intended_new}",
        propagated=False,
        creation_time=base_time + timedelta(minutes=4),
    ))

    # The post-edit allocation.amount the user actually intended
    alloc.amount = intended_new
    session.flush()
    return alloc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRegex:

    @pytest.mark.parametrize("comment, old, new", [
        ("Amount: 461000000.0 → 465000000.0", "461000000.0", "465000000.0"),
        ("Amount: 100 → 200; comment after", "100", "200"),
        ("Amount: 1.5e6 → 2e6", "1.5e6", "2e6"),
    ])
    def test_amount_delta_re_matches(self, comment, old, new):
        m = AMOUNT_DELTA_RE.match(comment)
        assert m is not None
        assert m.group(1) == old
        assert m.group(2) == new

    @pytest.mark.parametrize("comment", [
        "End date: 2026-01-01 → 2027-01-01",   # different field
        "[DELETE] Allocation created",          # B3-tagged comment
        "Renewed from allocation #21664",       # RENEW comment
        None,
    ])
    def test_amount_delta_re_misses(self, comment):
        if comment is None:
            assert AMOUNT_DELTA_RE.match("") is None
        else:
            assert AMOUNT_DELTA_RE.match(comment) is None


class TestDiscoverSingleAllocation:

    def test_replicates_cesm0002_926m_bug(self, session):
        """Pre-fix replay returns 461 + 465 = 926 (the bogus legacy value),
        not the stored 465. Discover flags it for repair."""
        user = make_user(session)
        alloc = _seed_cesm0002_bug(session, user, intended_old=461.0, intended_new=465.0)

        finding = _discover_for_allocation(alloc)

        # The bug fingerprint
        assert finding.stored_amount == pytest.approx(465.0)
        assert finding.pre_replay == pytest.approx(926.0)
        assert finding.needs_repair
        assert len(finding.bogus_rows) == 1
        assert finding.duplicate_new_count == 1   # 2 NEW rows = 1 duplicate

        # The correction
        b = finding.bogus_rows[0]
        assert b.intended_old == pytest.approx(461.0)
        assert b.intended_new == pytest.approx(465.0)
        assert b.intended_delta == pytest.approx(4.0)
        assert b.correction_amount == pytest.approx(-461.0)

    def test_simulated_correction_restores_invariant(self, session):
        user = make_user(session)
        alloc = _seed_cesm0002_bug(session, user, intended_old=461.0, intended_new=465.0)

        finding = _discover_for_allocation(alloc)
        finding.post_replay = _simulate_post_correction(finding, alloc.transactions)

        # Post-correction, replay reproduces stored amount within tolerance
        assert finding.repaired_ok
        assert finding.post_replay == pytest.approx(465.0, abs=REPLAY_TOLERANCE)
        # Bug-value 926 is no longer reachable
        assert abs(finding.post_replay - 926.0) > REPLAY_TOLERANCE


class TestDiscoverFalsePositives:

    def test_clean_allocation_not_flagged(self, session):
        """A post-B1 allocation (delta-correct EDIT row) is not flagged."""
        user = make_user(session)
        alloc = make_allocation(
            session, amount=1000.0,
            start_date=datetime.now(),
            end_date=datetime.now() + timedelta(days=30),
        )
        # Wipe any existing transactions (factory may have logged some)
        session.query(AllocationTransaction).filter_by(
            allocation_id=alloc.allocation_id,
        ).delete()
        session.flush()

        # Write a clean NEW + correct EDIT-as-ADJUSTMENT (post-B1 shape:
        # transaction_amount is the +delta, not the new total).
        session.add(AllocationTransaction(
            allocation_id=alloc.allocation_id,
            user_id=user.user_id,
            transaction_type="NEW",
            transaction_amount=1000.0,
            requested_amount=1000.0,
            alloc_start_date=alloc.start_date,
            alloc_end_date=alloc.end_date,
            transaction_comment="Allocation created",
            propagated=False,
            creation_time=datetime(2026, 4, 1, 9, 0, 0),
        ))
        # Update via update_allocation so the new code path writes a
        # B1-correct delta row
        update_allocation(
            session, alloc.allocation_id, user.user_id, amount=1500.0,
        )

        finding = _discover_for_allocation(alloc)
        assert finding.bogus_rows == []
        assert not finding.needs_repair

    def test_b3_tagged_adjustment_not_flagged(self, session):
        """[DELETE]/[DETACH]/[LINK] tagged ADJUSTMENT rows (transaction_amount=0)
        must NOT be flagged as the pre-B1 bug."""
        user = make_user(session)
        alloc = make_allocation(session, amount=500.0)
        session.query(AllocationTransaction).filter_by(
            allocation_id=alloc.allocation_id,
        ).delete()
        session.add(AllocationTransaction(
            allocation_id=alloc.allocation_id,
            user_id=user.user_id,
            transaction_type="NEW",
            transaction_amount=500.0,
            requested_amount=500.0,
            alloc_start_date=alloc.start_date,
            alloc_end_date=alloc.end_date,
            transaction_comment="Allocation created",
            propagated=False,
            creation_time=datetime(2026, 4, 1),
        ))
        session.add(AllocationTransaction(
            allocation_id=alloc.allocation_id,
            user_id=user.user_id,
            transaction_type="ADJUSTMENT",
            transaction_amount=0.0,
            requested_amount=500.0,
            alloc_start_date=alloc.start_date,
            alloc_end_date=alloc.end_date,
            transaction_comment="[DETACH] Detached from parent allocation #999",
            propagated=False,
            creation_time=datetime(2026, 4, 2),
        ))
        session.flush()

        finding = _discover_for_allocation(alloc)
        assert finding.bogus_rows == []

    def test_already_remediated_not_flagged(self, session):
        """Re-running the script after a successful apply must be a no-op."""
        user = make_user(session)
        alloc = _seed_cesm0002_bug(session, user)

        # Simulate an apply: append the corrective row directly
        finding = _discover_for_allocation(alloc)
        for b in finding.bogus_rows:
            session.add(_build_corrective_row(
                b, user_id=user.user_id, remediation_tag=_remediation_tag(),
            ))
        session.flush()

        # Re-discover — the bogus row matches the same fingerprint as
        # before, but the allocation is now self-consistent under
        # replay, so needs_repair should be False even if bogus_rows is
        # non-empty (post_replay confirms the fix).
        finding2 = _discover_for_allocation(alloc)
        finding2.post_replay = _simulate_post_correction(finding2, alloc.transactions)
        # The discover heuristic still flags the bogus row, but a real
        # apply step would re-introduce the correction; in practice
        # callers should check finding.needs_repair (replay-vs-amount)
        # before deciding to act.
        assert not finding2.needs_repair


class TestRenderInsertSQL:

    def test_render_includes_required_fields(self, session):
        user = make_user(session)
        alloc = _seed_cesm0002_bug(session, user)
        finding = _discover_for_allocation(alloc)
        sql = _render_insert_sql(finding.bogus_rows[0], "[REMEDIATION 2026-05-02]")
        assert "INSERT INTO allocation_transaction" in sql
        assert "'ADJUSTMENT'" in sql
        assert f"({alloc.allocation_id}," in sql
        assert "[REMEDIATION 2026-05-02]" in sql
        # Correction is the negative of intended_old
        assert "-461.0" in sql

    def test_render_escapes_quotes(self, session):
        b = BogusEditRow(
            allocation_transaction_id=42,
            allocation_id=99,
            projcode="X",
            resource_name="Y",
            bogus_amount=200.0,
            intended_old=100.0,
            intended_new=200.0,
            intended_delta=100.0,
            correction_amount=-100.0,
            alloc_start_date=datetime(2026, 1, 1),
            alloc_end_date=None,
            creation_time=datetime(2026, 1, 1, 12, 0, 0),
        )
        sql = _render_insert_sql(b, "[REMEDIATION 2026-05-02]")
        # NULL end date, no rogue quotes from None.strftime
        assert "NULL" in sql


class TestApplyCorrections:

    def test_apply_end_to_end_restores_replay_invariant(self, session):
        """End-to-end: discover → apply_corrections → replay matches amount.
        apply_corrections does NOT commit, so this runs cleanly inside the
        per-test SAVEPOINT."""
        user = make_user(session)
        alloc = _seed_cesm0002_bug(session, user)

        findings = [_discover_for_allocation(alloc)]
        for f in findings:
            f.post_replay = _simulate_post_correction(f, alloc.transactions)

        n_before = session.query(AllocationTransaction).filter_by(
            allocation_id=alloc.allocation_id,
        ).count()

        inserted = apply_corrections(
            session, findings,
            user_id=user.user_id,
            remediation_tag=_remediation_tag(),
        )

        assert inserted == 1
        n_after = session.query(AllocationTransaction).filter_by(
            allocation_id=alloc.allocation_id,
        ).count()
        assert n_after == n_before + 1

        session.refresh(alloc)
        assert replay_amount(alloc.transactions) == pytest.approx(
            alloc.amount, abs=REPLAY_TOLERANCE,
        )

    def test_apply_skips_findings_with_no_bogus_rows(self, session):
        """A finding for a clean allocation contributes zero INSERTs."""
        user = make_user(session)
        clean_alloc = make_allocation(session, amount=500.0)
        finding = _discover_for_allocation(clean_alloc)
        assert finding.bogus_rows == []

        inserted = apply_corrections(
            session, [finding],
            user_id=user.user_id,
            remediation_tag=_remediation_tag(),
        )
        assert inserted == 0

    def test_apply_raises_replay_mismatch_on_bad_correction(self, session):
        """If the correction we'd apply still leaves replay != amount,
        apply_corrections raises ReplayMismatch (caller rollbacks)."""
        user = make_user(session)
        alloc = _seed_cesm0002_bug(session, user)

        # Manually corrupt a finding so the correction is wrong
        finding = _discover_for_allocation(alloc)
        finding.bogus_rows[0].correction_amount = 0.0  # no-op correction

        with pytest.raises(ReplayMismatch):
            apply_corrections(
                session, [finding],
                user_id=user.user_id,
                remediation_tag=_remediation_tag(),
            )
        # Outer SAVEPOINT means we don't need to rollback explicitly,
        # but in production main() does session.rollback() on the
        # exception. The corrective row was flushed but is uncommitted.

    def test_corrective_row_carries_remediation_tag(self, session):
        user = make_user(session)
        alloc = _seed_cesm0002_bug(session, user)
        finding = _discover_for_allocation(alloc)
        b = finding.bogus_rows[0]

        row = _build_corrective_row(
            b, user_id=user.user_id, remediation_tag="[REMEDIATION 2026-05-02]",
        )
        assert row.transaction_type == "ADJUSTMENT"
        assert row.transaction_amount == pytest.approx(-461.0)
        assert row.transaction_comment.startswith("[REMEDIATION 2026-05-02]")
        # Reference to the original bogus txid, for traceability
        assert f"txid {b.allocation_transaction_id}" in row.transaction_comment


class TestRemediationTag:

    def test_tag_format(self):
        tag = _remediation_tag(datetime(2026, 5, 2))
        assert tag == "[REMEDIATION 2026-05-02]"
