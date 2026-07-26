"""
Data-quality audits for project allocation trees.

SAM runs two project-tree conventions, and downstream consumers (notably the
PBS fairshare tree built from ``sam.queries.fstree_access``) rely on them being
kept consistent:

  * **shared pool** — every member of the tree carries an allocation row at the
    *full* pool amount, ideally linked via ``allocation.parent_allocation_id``;
  * **subdivided award** — children carve distinct amounts out of the root's
    total.

Both share one invariant: **a parent's allocation covers every child that is
not sharing its pool.**  Hold that, and a tree root's raw ``amount`` is always
its subtree total, which is what lets consumers weight a tree against its peers
using raw amounts and the hierarchy alone — no tree classification needed.
Break it, and the root is silently under-weighted against its peers.

Nothing enforces the invariant at write time (allocations are entered per
project), so it drifts as awards are made.  ``audit_allocation_trees()`` is the
check; ``sam-admin project --audit-trees`` is the operator-facing front end.

Note this module *does* classify pool-vs-carve children — that judgement lives
here, in :func:`is_pool_member`, the single classification site.  It is shared
with :func:`sam.manage.allocations.get_carveout_frontier` (the per-node
residual/frontier decomposition behind the admin "allocate down" workflow);
keep the two consumers in lock-step by changing only that function.
"""

from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session


# Current allocations on configurable HPC/DAV resources, one row per
# (active project, resource).  Verified against production data: no
# (project, resource) carries more than one current allocation, so joining
# this CTE to itself cannot double-count.
_CTE_CURRENT_ALLOCATIONS = """
    WITH cur AS (
        SELECT p.project_id,
               p.projcode,
               p.parent_id,
               r.resource_name,
               al.allocation_id,
               al.parent_allocation_id,
               al.amount
        FROM project p
        JOIN account        a  ON (a.project_id       = p.project_id
                                    AND a.deleted      IS FALSE)
        JOIN resources      r  ON (r.resource_id      = a.resource_id
                                    AND r.configurable IS TRUE)
        JOIN resource_type  rt ON (rt.resource_type_id = r.resource_type_id
                                    AND rt.resource_type IN ('HPC', 'DAV'))
        JOIN allocation     al ON (al.account_id      = a.account_id
                                    AND al.deleted     IS FALSE
                                    AND al.start_date <= NOW()
                                    AND (al.end_date IS NULL OR al.end_date >= NOW()))
        WHERE p.active IS TRUE
    )
"""

# One row per (parent, child, resource) pair, flagging how the child relates to
# its parent's allocation.
#
# `linked` guards the NULL: `parent_allocation_id = par.allocation_id` alone
# would yield NULL (not FALSE) for unlinked children and poison the OR.
_SQL_TREE_PAIRS = text(_CTE_CURRENT_ALLOCATIONS + """
    SELECT par.projcode        AS parent_projcode,
           par.resource_name   AS resource_name,
           par.amount          AS parent_amount,
           ch.projcode         AS child_projcode,
           ch.amount           AS child_amount,
           (ch.parent_allocation_id IS NOT NULL
             AND ch.parent_allocation_id = par.allocation_id) AS linked,
           (ch.amount = par.amount)                           AS equal_amount
    FROM cur par
    JOIN cur ch ON (ch.parent_id     = par.project_id
                     AND ch.resource_name = par.resource_name)
    WHERE (:resource IS NULL OR par.resource_name = :resource)
    ORDER BY par.resource_name, par.projcode, ch.projcode
""")

# Impossible allocation windows — not merely unusual ones.  Long windows are
# NOT an error: ~160 current allocations legitimately run 5-10 years (UNIV
# awards with extended end dates), so only physically impossible values are
# flagged.
#
# Scoped to configurable HPC/DAV resources — the population the fairshare tree
# is built from, and the only one where a bogus window changes a burn rate.
# Decommissioned machines (configurable IS FALSE) carry long-expired rows with
# their own inconsistencies that nobody will fix; including them would leave
# the audit permanently red and therefore ignored.
#
# Deliberately NOT restricted to *current* allocations: a current allocation
# cannot have end < start (current implies start <= NOW() <= end), so that
# check would be dead code under a current-only filter.
_SQL_BAD_DATES = text("""
    SELECT p.projcode        AS projcode,
           r.resource_name   AS resource_name,
           al.allocation_id  AS allocation_id,
           al.start_date     AS start_date,
           al.end_date       AS end_date,
           al.amount         AS amount
    FROM allocation al
    JOIN account       a  ON (a.account_id        = al.account_id
                               AND a.deleted      IS FALSE)
    JOIN project       p  ON (p.project_id        = a.project_id
                               AND p.active       IS TRUE)
    JOIN resources     r  ON (r.resource_id       = a.resource_id
                               AND r.configurable IS TRUE)
    JOIN resource_type rt ON (rt.resource_type_id = r.resource_type_id
                               AND rt.resource_type IN ('HPC', 'DAV'))
    WHERE al.deleted IS FALSE
      AND (:resource IS NULL OR r.resource_name = :resource)
      AND (YEAR(al.start_date) < 1990
           OR YEAR(al.end_date) > 2100
           OR (al.end_date IS NOT NULL AND al.end_date < al.start_date))
    ORDER BY p.projcode, r.resource_name
""")


def is_pool_member(*, linked: bool, child_amount: float, parent_amount: float) -> bool:
    """
    THE pool-vs-carve classification rule — single site, shared.

    A child allocation is a **pool member** of its parent's allocation when it
    is linked via ``parent_allocation_id`` (the authoritative signal) OR its
    amount equals the parent's — the fallback for pools that were never
    formally linked, which are common.  Every other child is a **carve-out**
    and consumes part of the parent's amount.

    Exact float equality is deliberate: it mirrors the SQL fallback
    (``ch.amount = par.amount``) and the fairshare tree's carve test, where a
    child off by even one unit is a carve-out.

    Consumed by :func:`audit_allocation_trees` below and by
    :func:`sam.manage.allocations.get_carveout_frontier` — keep them in
    lock-step by changing only this function.
    """
    return linked or child_amount == parent_amount


def audit_allocation_trees(
    session: Session,
    resource_name: Optional[str] = None,
) -> List[Dict]:
    """
    Find parents whose allocation fails to cover their carve-out children.

    A child is a **pool member** (and so does not count against its parent) when
    its allocation is linked into the parent's (``parent_allocation_id``, the
    authoritative signal) *or* its amount equals the parent's — the fallback for
    pools that were never formally linked, which are common.  Every other child
    is a **carve-out** and consumes part of the parent's amount.

    Args:
        session:       SQLAlchemy session.
        resource_name: Optional resource filter (e.g. ``"Derecho"``).
                       ``None`` audits all HPC/DAV resources.

    Returns:
        One dict per violating (parent, resource), worst deficit first::

            {
                'parent_projcode': 'NCGD0006',
                'resource_name':   'Derecho',
                'parent_amount':   52000000.0,
                'carve_total':     54000000.0,
                'deficit':          2000000.0,   # always > 0
                'carve_children':  [{'projcode': ..., 'amount': ...}, ...],
                'pool_children':   [{'projcode': ..., 'amount': ...,
                                     'linked': True}, ...],
            }

        Empty list means the invariant holds everywhere.
    """
    rows = session.execute(_SQL_TREE_PAIRS, {'resource': resource_name}).fetchall()

    groups: Dict[tuple, Dict] = {}
    for row in rows:
        key = (row.parent_projcode, row.resource_name)
        group = groups.setdefault(key, {
            'parent_projcode': row.parent_projcode,
            'resource_name':   row.resource_name,
            'parent_amount':   float(row.parent_amount),
            'carve_total':     0.0,
            'carve_children':  [],
            'pool_children':   [],
        })

        child_amount = float(row.child_amount)
        if is_pool_member(linked=bool(row.linked),
                          child_amount=child_amount,
                          parent_amount=float(row.parent_amount)):
            group['pool_children'].append({
                'projcode': row.child_projcode,
                'amount':   child_amount,
                'linked':   bool(row.linked),
            })
        else:
            group['carve_children'].append({
                'projcode': row.child_projcode,
                'amount':   child_amount,
            })
            group['carve_total'] += child_amount

    violations = []
    for group in groups.values():
        deficit = group['carve_total'] - group['parent_amount']
        if deficit > 0:
            violations.append({**group, 'deficit': deficit})

    violations.sort(key=lambda v: -v['deficit'])
    return violations


def audit_allocation_dates(
    session: Session,
    resource_name: Optional[str] = None,
) -> List[Dict]:
    """
    Find allocations with impossible date windows.

    Flags only values that cannot be real — a start year before 1990, an end
    year after 2100, or an end that precedes its start (typically a mistyped
    year, e.g. ``0006-05-08``).  Long-but-plausible windows are **not** flagged:
    multi-year allocations are routine, and ~160 current ones legitimately run
    5-10 years.

    This matters to burn-rate consumers, which divide an amount by the window
    length; a bogus year yields a nonsense rate.  Scoped to configurable
    HPC/DAV resources for that reason — see the query comment.

    Args:
        session:       SQLAlchemy session.
        resource_name: Optional resource filter.  ``None`` audits all
                       configurable HPC/DAV resources.

    Returns:
        One dict per offending allocation with ``projcode``, ``resource_name``,
        ``allocation_id``, ``start_date``, ``end_date``, ``amount``.
    """
    rows = session.execute(_SQL_BAD_DATES, {'resource': resource_name}).fetchall()
    return [{
        'projcode':      row.projcode,
        'resource_name': row.resource_name,
        'allocation_id': row.allocation_id,
        'start_date':    row.start_date,
        'end_date':      row.end_date,
        'amount':        float(row.amount) if row.amount is not None else None,
    } for row in rows]
