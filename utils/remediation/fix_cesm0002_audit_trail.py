#!/usr/bin/env python3
"""Backfill the CESM0002 allocation_transaction audit trail.

Repairs the legacy-fstree mismatches introduced on 2026-04-23 when the
new "Extend Project Tree" flow + a follow-up amount edit wrote audit
rows that the legacy SAM replay path could not reproduce. See
``docs/plans/FIX_TREE_EXTENSION_bugs.md`` for the full context. This
script is the **Stream A** remediation: append-only corrective
``ADJUSTMENT`` rows that bring legacy replay back into agreement with
``allocation.amount``. Stream B (commits B1/B2/B3) prevents recurrence
in the source code.

Strategy
--------
For every allocation under the target project tree whose
``allocation_transaction`` history contains a malformed
``ADJUSTMENT`` row written before B1 landed (the row's
``transaction_amount`` stores the post-edit total instead of the
delta), append a single corrective ``ADJUSTMENT`` row whose
``transaction_amount`` is the negative of the pre-edit total. Replay
then reproduces ``allocation.amount`` to within float-precision noise.

The fingerprint we look for:

    transaction_type = 'ADJUSTMENT'
    transaction_comment LIKE 'Amount: % → %'
    |transaction_amount - allocation.amount| < tolerance

Phases
------
1. **discover**     — list candidate rows (read-only).
2. **replay-check** — Python port of legacy ``DateBoundedAllocationAmount``
                       confirms the row needs repair (replayed != amount)
                       and computes the correction.
3. **dry-run**      — print proposed ``INSERT`` statements with
                       before/after replay sums (read-only).
4. **apply**        — gated on ``--confirm`` AND ``--projcode`` allowlist;
                       wraps INSERTs in a single transaction that
                       auto-rollbacks on post-apply replay mismatch.
5. **verify**       — re-runs replay-check; mismatches must be zero.

Rollback
--------
Every corrective row gets a uniform ``[REMEDIATION YYYY-MM-DD]``
comment prefix. To undo::

    DELETE FROM allocation_transaction
    WHERE transaction_comment LIKE '[REMEDIATION YYYY-MM-DD]%';

Usage
-----
::

    # Discover only (read-only):
    python -m utils.remediation.fix_cesm0002_audit_trail --projcode CESM0002

    # Add --dry-run to also show proposed corrections:
    python -m utils.remediation.fix_cesm0002_audit_trail --projcode CESM0002 --dry-run

    # Apply (requires explicit --confirm):
    python -m utils.remediation.fix_cesm0002_audit_trail \\
        --projcode CESM0002 --apply --confirm \\
        --user-id <admin_user_id>

Environment
-----------
Reads DB credentials via the standard ``sam.session`` module
(``SAM_DB_USERNAME`` / ``SAM_DB_PASSWORD`` / ``SAM_DB_SERVER`` /
``SAM_DB_NAME``), so ``source etc/config_env.sh`` first.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

# Make the project src/ importable when invoked as a script
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', '..', 'src'))

from sqlalchemy.orm import Session  # noqa: E402

from sam.accounting.allocations import (  # noqa: E402
    Allocation,
    AllocationTransaction,
    LEGACY_TRANSACTION_TYPES,
    replay_amount,
)
from sam.projects.projects import Project  # noqa: E402
from sam.session import create_sam_engine  # noqa: E402


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------

#: ``transaction_comment`` pattern for an EDIT-as-ADJUSTMENT row that
#: stored the post-edit total instead of a delta. Pre-B1 ``update_allocation``
#: emitted exactly this comment shape via ``log_allocation_transaction``.
AMOUNT_DELTA_RE = re.compile(
    r"^Amount:\s*([\d.eE+\-]+)\s*→\s*([\d.eE+\-]+)"
)

#: Tolerance (in resource units) for "transaction_amount equals
#: allocation.amount". MySQL ``FLOAT(15,2)`` carries ~7 significant
#: figures; for sums in the 10^8 range, the noise floor is ~10^1.
DEFAULT_TOLERANCE = 1.0

#: Tolerance for post-correction replay matching ``allocation.amount``.
#: Allow a bit more headroom since multiple float-noise additions
#: accumulate across the full audit history.
REPLAY_TOLERANCE = 100.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class BogusEditRow:
    """One bogus pre-B1 EDIT-as-ADJUSTMENT row + the proposed correction."""
    allocation_transaction_id: int
    allocation_id: int
    projcode: str
    resource_name: str
    bogus_amount: float
    intended_old: float        # the X in "Amount: X → Y"
    intended_new: float        # the Y in "Amount: X → Y"
    intended_delta: float      # Y - X (what should have been written)
    correction_amount: float   # signed delta to append (= -X)
    alloc_start_date: datetime
    alloc_end_date: Optional[datetime]
    creation_time: datetime


@dataclass
class AllocationFinding:
    """Per-allocation findings after discover + replay-check."""
    allocation_id: int
    projcode: str
    resource_name: str
    stored_amount: float
    pre_replay: float
    bogus_rows: List[BogusEditRow] = field(default_factory=list)
    duplicate_new_count: int = 0
    post_replay: Optional[float] = None  # populated after correction(s) are simulated

    @property
    def needs_repair(self) -> bool:
        return abs(self.pre_replay - self.stored_amount) > REPLAY_TOLERANCE

    @property
    def repaired_ok(self) -> bool:
        if self.post_replay is None:
            return False
        return abs(self.post_replay - self.stored_amount) <= REPLAY_TOLERANCE


# ---------------------------------------------------------------------------
# Discovery + replay
# ---------------------------------------------------------------------------


def _project_tree_ids(session: Session, projcode: str) -> List[int]:
    """Return ``[root.project_id, *all_descendants.project_id]`` for projcode."""
    root = Project.get_by_projcode(session, projcode)
    if root is None:
        raise SystemExit(f"Project not found: {projcode!r}")
    ids = [root.project_id]
    ids.extend(d.project_id for d in root.get_descendants())
    return ids


def _allocations_under_tree(session: Session, projcode: str) -> List[Allocation]:
    """All non-deleted Allocations for any project in the projcode's tree."""
    from sam.accounting.accounts import Account
    project_ids = _project_tree_ids(session, projcode)
    return (
        session.query(Allocation)
        .join(Account, Account.account_id == Allocation.account_id)
        .filter(
            Account.project_id.in_(project_ids),
            Allocation.deleted == False,  # noqa: E712
        )
        .all()
    )


def _discover_for_allocation(
    allocation: Allocation,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> AllocationFinding:
    """Walk one allocation's history; flag bogus EDIT-as-ADJUSTMENT rows."""
    txns = sorted(
        allocation.transactions,
        key=lambda t: (t.creation_time, t.allocation_transaction_id or 0),
    )

    finding = AllocationFinding(
        allocation_id=allocation.allocation_id,
        projcode=(allocation.account.project.projcode
                  if allocation.account and allocation.account.project else "?"),
        resource_name=(allocation.account.resource.resource_name
                       if allocation.account and allocation.account.resource else "?"),
        stored_amount=float(allocation.amount),
        pre_replay=replay_amount(txns),
    )

    new_count = sum(1 for t in txns if t.transaction_type == "NEW")
    finding.duplicate_new_count = max(0, new_count - 1)

    for t in txns:
        if t.transaction_type != "ADJUSTMENT":
            continue
        comment = t.transaction_comment or ""
        # Skip B3-tagged rows ([DELETE], [DETACH], [LINK]) — those are
        # intentional zero-deltas, not the pre-B1 bug fingerprint.
        if comment.startswith("["):
            continue
        # Skip already-applied remediation rows (defensive — they live
        # under [REMEDIATION ...] which starts with [, so the line
        # above already filters them, but keep the explicit check for
        # safety).
        if "[REMEDIATION" in comment:
            continue
        m = AMOUNT_DELTA_RE.match(comment)
        if not m:
            continue
        intended_old = float(m.group(1))
        intended_new = float(m.group(2))
        # Bug fingerprint: stored amount equals the post-edit total
        # (intended_new), not the delta.
        if abs(float(t.transaction_amount or 0.0) - intended_new) > tolerance:
            continue

        finding.bogus_rows.append(BogusEditRow(
            allocation_transaction_id=t.allocation_transaction_id,
            allocation_id=allocation.allocation_id,
            projcode=finding.projcode,
            resource_name=finding.resource_name,
            bogus_amount=float(t.transaction_amount or 0.0),
            intended_old=intended_old,
            intended_new=intended_new,
            intended_delta=intended_new - intended_old,
            correction_amount=-intended_old,
            alloc_start_date=allocation.start_date,
            alloc_end_date=allocation.end_date,
            creation_time=t.creation_time,
        ))

    return finding


def _simulate_post_correction(
    finding: AllocationFinding,
    txns: list,
) -> float:
    """Replay txns + the proposed corrective rows and return the result.

    ``txns`` is the stored history; we synthesize one extra
    ``AllocationTransaction``-shaped object per ``BogusEditRow`` and
    pass everything through ``replay_amount``. Pure in-memory; no DB
    writes.
    """
    synthetic = []
    for b in finding.bogus_rows:
        synthetic.append(_FakeTxn(
            transaction_type="ADJUSTMENT",
            transaction_amount=b.correction_amount,
            creation_time=datetime.now(),  # later than all stored rows
            allocation_transaction_id=10**9 + len(synthetic),
        ))
    return replay_amount(list(txns) + synthetic)


@dataclass
class _FakeTxn:
    transaction_type: str
    transaction_amount: float
    creation_time: datetime
    allocation_transaction_id: int


def discover(
    session: Session,
    projcode: str,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> List[AllocationFinding]:
    """Return one ``AllocationFinding`` per allocation under the tree.

    Findings with ``needs_repair == False`` and no ``bogus_rows`` mean
    the allocation is already self-consistent and no corrective row is
    needed.
    """
    findings = []
    for alloc in _allocations_under_tree(session, projcode):
        f = _discover_for_allocation(alloc, tolerance=tolerance)
        # Simulate the proposed correction so we can show before/after
        # replay sums during dry-run.
        if f.bogus_rows:
            f.post_replay = _simulate_post_correction(f, alloc.transactions)
        findings.append(f)
    return findings


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _print_discover(findings: List[AllocationFinding]) -> None:
    needs = [f for f in findings if f.bogus_rows]
    print(f"\n=== Discover: {len(findings)} allocations under tree, "
          f"{len(needs)} need repair ===\n")
    if not needs:
        print("  ✓ Audit trail already self-consistent — no work to do.")
        return
    for f in needs:
        delta_obs = f.pre_replay - f.stored_amount
        print(f"  Allocation {f.allocation_id} ({f.projcode}/{f.resource_name})")
        print(f"    stored amount = {f.stored_amount:>15,.2f}")
        print(f"    legacy replay = {f.pre_replay:>15,.2f}  (off by {delta_obs:+,.2f})")
        if f.duplicate_new_count:
            print(f"    duplicate NEW rows: {f.duplicate_new_count}")
        for b in f.bogus_rows:
            print(f"    └ bogus txid={b.allocation_transaction_id}: "
                  f"\"Amount: {b.intended_old:,} → {b.intended_new:,}\" "
                  f"stored {b.bogus_amount:,.2f} (intended delta {b.intended_delta:+,.2f})")


def _print_dry_run(findings: List[AllocationFinding], remediation_tag: str) -> None:
    needs = [f for f in findings if f.bogus_rows]
    print(f"\n=== Dry-run: proposed INSERT statements ===\n")
    if not needs:
        print("  (nothing to insert)")
        return
    for f in needs:
        print(f"  -- Allocation {f.allocation_id} ({f.projcode}/{f.resource_name})")
        print(f"  -- pre-replay  = {f.pre_replay:,.2f}")
        print(f"  -- stored      = {f.stored_amount:,.2f}")
        print(f"  -- post-replay = {f.post_replay:,.2f}  "
              + ("✓" if f.repaired_ok else "✗ MANUAL REVIEW"))
        for b in f.bogus_rows:
            print(_render_insert_sql(b, remediation_tag))
        print()


def _render_insert_sql(b: BogusEditRow, remediation_tag: str) -> str:
    """Render a copy-pasteable INSERT for one corrective ADJUSTMENT row."""
    end_lit = (f"'{b.alloc_end_date.strftime('%Y-%m-%d %H:%M:%S')}'"
               if b.alloc_end_date else "NULL")
    comment = _correction_comment(b, remediation_tag).replace("'", "''")
    return (
        f"  INSERT INTO allocation_transaction\n"
        f"    (allocation_id, user_id, transaction_type,\n"
        f"     transaction_amount, requested_amount,\n"
        f"     alloc_start_date, alloc_end_date,\n"
        f"     transaction_comment, propagated, creation_time)\n"
        f"  VALUES\n"
        f"    ({b.allocation_id}, :user_id, 'ADJUSTMENT',\n"
        f"     {b.correction_amount}, {b.correction_amount},\n"
        f"     '{b.alloc_start_date.strftime('%Y-%m-%d %H:%M:%S')}', {end_lit},\n"
        f"     '{comment}', 0, NOW());"
    )


# ---------------------------------------------------------------------------
# Apply (gated)
# ---------------------------------------------------------------------------


def _correction_comment(b: BogusEditRow, remediation_tag: str) -> str:
    """The standard remediation comment, formatted for human readability."""
    return (
        f"{remediation_tag} correct double-counted EDIT-as-ADJUSTMENT "
        f"(txid {b.allocation_transaction_id} stored post-edit total "
        f"{b.intended_new:,.2f} instead of delta {b.intended_delta:+,.2f})"
    )


def _build_corrective_row(
    b: BogusEditRow,
    *,
    user_id: int,
    remediation_tag: str,
) -> AllocationTransaction:
    return AllocationTransaction(
        allocation_id=b.allocation_id,
        user_id=user_id,
        transaction_type="ADJUSTMENT",
        transaction_amount=b.correction_amount,
        requested_amount=b.correction_amount,
        alloc_start_date=b.alloc_start_date,
        alloc_end_date=b.alloc_end_date,
        transaction_comment=_correction_comment(b, remediation_tag),
        propagated=False,
    )


class ReplayMismatch(RuntimeError):
    """Post-apply replay didn't match allocation.amount within tolerance."""


def apply_corrections(
    session: Session,
    findings: List[AllocationFinding],
    *,
    user_id: int,
    remediation_tag: str,
) -> int:
    """Insert corrective rows and verify the post-apply replay invariant.

    Flushes (does NOT commit) the corrective rows, then for every
    affected allocation re-runs ``replay_amount`` over its full
    history and confirms ``replayed ≈ allocation.amount`` within
    ``REPLAY_TOLERANCE``. Raises ``ReplayMismatch`` on any deviation
    so the caller can ``session.rollback()`` cleanly.

    Commit is the caller's responsibility — that lets tests exercise
    this function inside a SAVEPOINT and lets ``main()`` explicitly
    confirm before committing to prod.

    Returns the number of corrective rows successfully flushed.
    """
    needs = [f for f in findings if f.bogus_rows]
    inserted = 0
    for f in needs:
        for b in f.bogus_rows:
            session.add(_build_corrective_row(
                b, user_id=user_id, remediation_tag=remediation_tag,
            ))
            inserted += 1
    session.flush()

    # Verify replay matches stored amount for every allocation we touched
    for f in needs:
        alloc = session.get(Allocation, f.allocation_id)
        session.refresh(alloc)
        replayed = replay_amount(alloc.transactions)
        if abs(replayed - alloc.amount) > REPLAY_TOLERANCE:
            raise ReplayMismatch(
                f"Post-apply replay mismatch on allocation {f.allocation_id}: "
                f"replayed={replayed:,.2f} != amount={alloc.amount:,.2f} "
                f"(diff={replayed - alloc.amount:+,.2f})."
            )

    return inserted


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


#: Allowlist of projcodes this script is permitted to mutate. Add
#: explicitly here AND require ``--projcode <code>`` on the CLI;
#: belt-and-suspenders so a typo can't wander outside the intended
#: blast radius.
PROJCODE_ALLOWLIST = frozenset({"CESM0002"})


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__.split('\n\n')[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        '--projcode', required=True,
        help='Root project code to remediate (must be in PROJCODE_ALLOWLIST).',
    )
    p.add_argument(
        '--dry-run', action='store_true',
        help='Print proposed INSERT statements (no DB writes).',
    )
    p.add_argument(
        '--apply', action='store_true',
        help='Insert corrective rows. Requires --confirm.',
    )
    p.add_argument(
        '--confirm', action='store_true',
        help='Required alongside --apply to actually write to the DB.',
    )
    p.add_argument(
        '--user-id', type=int, default=None,
        help='User FK for the corrective rows. Required with --apply.',
    )
    p.add_argument(
        '--tolerance', type=float, default=DEFAULT_TOLERANCE,
        help=f'Match-tolerance for stored-total fingerprint. Default {DEFAULT_TOLERANCE}.',
    )
    return p.parse_args()


def _remediation_tag(today: Optional[datetime] = None) -> str:
    today = today or datetime.now()
    return f"[REMEDIATION {today.strftime('%Y-%m-%d')}]"


def main() -> int:
    args = _parse_args()

    if args.projcode not in PROJCODE_ALLOWLIST:
        print(
            f"ERROR: projcode {args.projcode!r} is not in PROJCODE_ALLOWLIST. "
            f"Add it explicitly to {__file__} if you really mean to remediate it.",
            file=sys.stderr,
        )
        return 2
    if args.apply and not args.confirm:
        print("ERROR: --apply requires --confirm", file=sys.stderr)
        return 2
    if args.apply and args.user_id is None:
        print("ERROR: --apply requires --user-id <admin_user_id>", file=sys.stderr)
        return 2

    engine, SessionLocal = create_sam_engine()
    print(f"Connected to {engine.url.host}/{engine.url.database}")

    tag = _remediation_tag()
    with SessionLocal() as session:
        findings = discover(session, args.projcode, tolerance=args.tolerance)
        _print_discover(findings)

        if args.dry_run or args.apply:
            _print_dry_run(findings, tag)

        if args.apply:
            print(f"\n=== Applying corrections (tag={tag}, user_id={args.user_id}) ===")
            try:
                inserted = apply_corrections(
                    session, findings,
                    user_id=args.user_id,
                    remediation_tag=tag,
                )
            except ReplayMismatch as exc:
                print(f"  ✗ {exc} — rolling back, no rows committed.",
                      file=sys.stderr)
                session.rollback()
                return 1

            session.commit()
            print(f"  ✓ Committed {inserted} corrective row(s)")

            # Re-discover for verification
            print("\n=== Verification: re-running discover ===")
            session.expire_all()
            verify_findings = discover(session, args.projcode, tolerance=args.tolerance)
            still_broken = [f for f in verify_findings if f.needs_repair]
            if still_broken:
                print(f"  ✗ {len(still_broken)} allocation(s) still report mismatches",
                      file=sys.stderr)
                return 1
            print("  ✓ All allocations under tree now self-consistent")

    return 0


if __name__ == '__main__':
    sys.exit(main())
