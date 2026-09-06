#!/usr/bin/env python3
"""Relink standalone divisional child allocations onto their parent pool.

One-shot FY-renewal remediation. Detects the mixed-sharing state (a child
project holding its OWN standalone allocation of the SAME amount/window as its
immediate parent project's allocation, instead of inheriting the parent's
shared pool) and relinks each child via
``sam.manage.allocations.link_allocation_to_parent`` — the exact write the
webdev "re-link to parent" UI action emits.

Dry-run by default; ``--commit`` writes inside one management_transaction.

    source etc/config_env.sh
    python scripts/repair/relink_shared_allocations.py                 # dry-run, FY26 compute
    python scripts/repair/relink_shared_allocations.py --parent NEOL0001
    python scripts/repair/relink_shared_allocations.py --commit --username benkirk

Applied to prod 2026-09-06: 92 FY26 compute relinks across NASP0001 / NEOL0001 /
NMMM0003 / NRAL0002; post-run predicate census empty. Re-run next FY (widen
--fy-end / --resources).
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / 'src'))

from rich.console import Console          # noqa: E402
from rich.table import Table              # noqa: E402
from rich import box                      # noqa: E402
from sqlalchemy import select, and_       # noqa: E402
from sqlalchemy.orm import Session, aliased  # noqa: E402

from sam.session import create_sam_engine          # noqa: E402
from sam.core.users import User                    # noqa: E402
from sam.projects.projects import Project          # noqa: E402
from sam.accounting.accounts import Account        # noqa: E402
from sam.accounting.allocations import Allocation  # noqa: E402
from sam.resources.resources import Resource       # noqa: E402
from sam.manage import management_transaction      # noqa: E402
from sam.manage.allocations import link_allocation_to_parent  # noqa: E402

console = Console()

DEFAULT_RESOURCES = ['Casper', 'Casper GPU', 'Derecho', 'Derecho GPU']


def _month_window(fy_end: datetime):
    """Return [first-of-month, first-of-next-month) bounding fy_end's month."""
    lo = fy_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    hi = (lo.replace(year=lo.year + 1, month=1) if lo.month == 12
          else lo.replace(month=lo.month + 1))
    return lo, hi


def _resolve_resources(session, tokens):
    """Map a list of resource names or ids to (id, name) pairs."""
    out = []
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        col = Resource.resource_id if tok.isdigit() else Resource.resource_name
        r = session.scalars(select(Resource).where(col == (int(tok) if tok.isdigit() else tok))).first()
        if r is None:
            raise SystemExit(f"Unknown resource: {tok!r}")
        out.append((r.resource_id, r.resource_name))
    return out


def find_candidates(session, resource_ids, fy_lo, fy_hi, parent_projcode=None):
    """Return (pairs, ambiguous) for the relink predicate.

    pairs: list of dicts child->parent that are safe to link.
    ambiguous: child_ids whose parent has >1 matching standalone alloc (skipped).
    """
    Child, CA, CP = aliased(Allocation), aliased(Account), aliased(Project)
    Par, PA, PP = aliased(Allocation), aliased(Account), aliased(Project)
    R = aliased(Resource)

    stmt = (
        select(Child.allocation_id, Par.allocation_id, PP.projcode, CP.projcode,
               R.resource_name, Child.amount)
        .join(CA, Child.account_id == CA.account_id)
        .join(CP, CA.project_id == CP.project_id)
        .join(PP, CP.parent_id == PP.project_id)
        .join(PA, and_(PA.project_id == PP.project_id, PA.resource_id == CA.resource_id))
        .join(Par, Par.account_id == PA.account_id)
        .join(R, R.resource_id == CA.resource_id)
        .where(
            Child.deleted.is_(False), Child.parent_allocation_id.is_(None),
            Par.deleted.is_(False), Par.parent_allocation_id.is_(None),
            Child.amount == Par.amount,
            Child.start_date == Par.start_date,
            Child.end_date == Par.end_date,
            CA.resource_id.in_(resource_ids),
            Child.end_date >= fy_lo, Child.end_date < fy_hi,
        )
    )
    if parent_projcode:
        stmt = stmt.where(PP.projcode == parent_projcode)

    rows = session.execute(stmt).all()

    # A child appearing with >1 distinct parent alloc = ambiguous target; skip it.
    by_child = {}
    for child_id, parent_id, pp, cp, res, amt in rows:
        by_child.setdefault(child_id, []).append(
            dict(child_id=child_id, parent_id=parent_id, parent_proj=pp,
                 child_proj=cp, resource=res, amount=float(amt)))
    pairs, ambiguous = [], []
    for child_id, cands in by_child.items():
        if len({c['parent_id'] for c in cands}) > 1:
            ambiguous.append(child_id)
        else:
            pairs.append(cands[0])
    pairs.sort(key=lambda p: (p['parent_proj'], p['resource'], p['child_proj']))
    return pairs, ambiguous


def _print_plan(pairs, ambiguous, resources):
    t = Table(box=box.SIMPLE_HEAD, title='FY relink plan')
    for c in ('parent tree', 'resource', 'child proj', 'child alloc',
              '→ parent alloc', 'amount'):
        t.add_column(c)
    for p in pairs:
        t.add_row(p['parent_proj'], p['resource'], p['child_proj'],
                  str(p['child_id']), str(p['parent_id']), f"{p['amount']:,.2f}")
    console.print(t)

    counts = {}
    for p in pairs:
        counts[p['parent_proj']] = counts.get(p['parent_proj'], 0) + 1
    summary = ', '.join(f"{k}={v}" for k, v in sorted(counts.items()))
    console.print(f"[bold]{len(pairs)}[/bold] relinks across "
                  f"{len(counts)} tree(s): {summary or '(none)'}")
    console.print(f"resources in scope: "
                  f"{', '.join(n for _, n in resources)}")
    if ambiguous:
        console.print(f"[yellow]SKIPPED {len(ambiguous)} ambiguous child(ren) "
                      f"(>1 candidate parent): {sorted(ambiguous)}[/yellow]")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--fy-end', default='2026-09-30',
                    help='Allocation end-of-year date (YYYY-MM-DD); its month '
                         'bounds the scope. Default 2026-09-30 (FY26).')
    ap.add_argument('--resources', default=','.join(DEFAULT_RESOURCES),
                    help='Comma list of resource names or ids. '
                         'Default: Casper,Casper GPU,Derecho,Derecho GPU.')
    ap.add_argument('--parent', default=None,
                    help='Limit to one parent-tree projcode (e.g. NEOL0001).')
    ap.add_argument('--username', default=None,
                    help='Acting user for the audit trail (required with --commit).')
    ap.add_argument('--user-id', type=int, default=None,
                    help='Acting user id (alternative to --username).')
    ap.add_argument('--commit', action='store_true',
                    help='Write the relinks. Without it, dry-run only.')
    args = ap.parse_args()

    fy_end = datetime.strptime(args.fy_end, '%Y-%m-%d')
    fy_lo, fy_hi = _month_window(fy_end)

    engine, _ = create_sam_engine()
    session = Session(engine)

    resources = _resolve_resources(session, args.resources.split(','))
    resource_ids = [rid for rid, _ in resources]

    pairs, ambiguous = find_candidates(
        session, resource_ids, fy_lo, fy_hi, parent_projcode=args.parent)
    _print_plan(pairs, ambiguous, resources)

    if not args.commit:
        console.print("\n[cyan]DRY RUN[/cyan] — no changes written. "
                      "Re-run with --commit to apply.")
        return

    if not pairs:
        console.print("Nothing to do.")
        return

    user_id = args.user_id
    if user_id is None:
        if not args.username:
            raise SystemExit("--commit requires --username or --user-id")
        user = User.get_by_username(session, args.username)
        if user is None:
            raise SystemExit(f"Unknown username: {args.username!r}")
        user_id = user.user_id

    linked, skipped = 0, []
    with management_transaction(session):
        for p in pairs:
            try:
                link_allocation_to_parent(
                    session, p['child_id'], p['parent_id'], user_id)
                linked += 1
            except ValueError as e:
                skipped.append((p['child_id'], str(e)))

    console.print(f"\n[green]Linked {linked}[/green] allocation(s).")
    for cid, msg in skipped:
        console.print(f"[yellow]skipped {cid}: {msg}[/yellow]")


if __name__ == '__main__':
    main()
