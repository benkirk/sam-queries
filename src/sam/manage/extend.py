"""
Extend allocation logic — push ``end_date`` forward on existing allocations
without creating new rows.

Two entry points with deliberately different scopes, and picking the wrong one
is the kind of mistake that shows up as a silent no-op:

* :func:`extend_project_allocations` — **project-tree** scoped, driven by a list
  of resource ids, and *forgiving*: it skips anything it cannot extend. That is
  right for the operator-facing Extend Allocation flow, where a partial extension
  beats an error dialog.
* :func:`extend_account_allocation` — **single-allocation** scoped and *strict*:
  a shrink or a null end date raises. That is what the XRAS integration needs,
  because legacy errors where the operator flow shrugs, and because legacy walks
  accounts rather than resources.

Both mirror Renew's tree-awareness but mutate existing allocations in place and
log ``AllocationTransactionType.EXTENSION`` instead of creating new rows under
``RENEW``. Use Renew when you want a fresh allocation period; use Extend when
you want a grace-period push on the current grant.

``extend_project_allocations`` skips silently when:
  - a resource has no root-project source active at ``source_active_at``
  - a source allocation is open-ended (``end_date IS NULL``)
  - a source allocation already ends on/after the requested new date
    (would be a no-op or a shortening — use Edit Allocation for that)

``extend_account_allocation`` raises on the middle two and skips only the exact
no-op. See its docstring for why that is a separate function rather than a flag.
"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from sam.accounting.allocations import (
    Allocation,
    AllocationTransactionType,
)
from sam.projects.projects import Project
from sam.manage.allocations import (
    detach_allocation,
    log_allocation_transaction,
    log_integration_transaction,
    validate_allocation_dates,
)
from sam.manage.renew import find_source_alloc_at


__all__ = [
    'extend_project_allocations',
    'extend_account_allocation',
]


def extend_project_allocations(
    session: Session,
    *,
    root_project_id: int,
    source_active_at: datetime,
    new_end: datetime,
    resource_ids: List[int],
    user_id: int,
) -> List[Allocation]:
    """Push ``end_date`` forward on every source allocation in the project
    tree for the selected resources.

    Walks every descendant project (not just inheriting allocation children)
    so both NMMM-style shared trees and CESM-style divergent trees are
    updated in lock-step.

    Runs inside the caller's ``management_transaction()`` — does NOT commit.

    Returns the list of root allocations actually updated (one per resource
    that had a real extension applied).
    """
    root_project = session.get(Project, root_project_id)
    if root_project is None:
        raise ValueError(f"Project {root_project_id} not found")

    all_descendants = root_project.get_descendants()
    requested = set(resource_ids)
    updated_roots: List[Allocation] = []

    for resource_id in requested:
        source_root = find_source_alloc_at(
            root_project, resource_id, source_active_at
        )
        if source_root is None or source_root.is_inheriting:
            continue
        if source_root.end_date is None:
            continue
        if source_root.end_date >= new_end:
            continue

        validate_allocation_dates(source_root.start_date, new_end)

        old_root_end = source_root.end_date
        source_root.end_date = new_end
        log_allocation_transaction(
            session,
            source_root,
            user_id,
            AllocationTransactionType.EXTENSION,
            comment=(
                f"End date extended "
                f"{old_root_end.strftime('%Y-%m-%d')} → "
                f"{new_end.strftime('%Y-%m-%d')}"
            ),
            propagated=False,
        )
        updated_roots.append(source_root)

        for descendant in all_descendants:
            if not descendant.active:
                continue

            source_child = find_source_alloc_at(
                descendant, resource_id, source_active_at
            )
            if source_child is None:
                continue
            if source_child.end_date is None:
                continue
            if source_child.end_date >= new_end:
                continue

            old_child_end = source_child.end_date
            source_child.end_date = new_end
            log_allocation_transaction(
                session,
                source_child,
                user_id,
                AllocationTransactionType.EXTENSION,
                comment=(
                    f"End date extended "
                    f"{old_child_end.strftime('%Y-%m-%d')} → "
                    f"{new_end.strftime('%Y-%m-%d')}"
                ),
                propagated=True,
            )

    session.flush()
    return updated_roots


def extend_account_allocation(
    session: Session,
    allocation: Allocation,
    *,
    new_end: datetime,
    comment: str,
    user_id: Optional[int] = None,
) -> List[Allocation]:
    """Push one allocation and its child subtree out to ``new_end``. Strict.

    The XRAS/legacy-faithful extend. Three things distinguish it from its
    neighbors in this module and on the model, and each one is load-bearing:

    **1. It is strict where the operator flow is forgiving.**
    ``extend_project_allocations`` skips a source whose ``end_date`` is null or
    already at/after the target — a shrink there is a no-op with no complaint.
    Legacy raises ``IllegalArgumentException`` on both
    (``DefaultExtendAllocationCommand.doValidateNewEndDate``), and the XRAS
    handler must surface that as a 422 an XRAS admin can act on. Loosening the
    existing caller to share this code would silently turn its skips into
    errors, so this is a second function rather than a flag.

    **2. It writes legacy's row shape, which is not the model method's.**
    ``Allocation.extend_allocation`` sets ``transaction_amount``,
    ``alloc_start_date`` and ``propagated`` on every EXTENSION row. Production
    disagrees: **all 1,553** ``XrasAction Extension Request`` rows carry
    ``transaction_amount``, ``requested_amount`` and ``alloc_start_date`` NULL,
    ``propagated`` false *even on child nodes*, and ``user_id`` NULL. Only
    ``alloc_end_date`` is set. ``replay_amount`` ignores ``transaction_amount``
    on EXTENSION entirely, so the replay invariant holds either way — this is
    about the audit trail reading the same before and after cutover, not about
    arithmetic.

    **3. A node already at ``new_end`` is skipped, not rewritten.**
    Legacy's ``doExtend`` returns early on an equal end date, so a re-posted
    Extension writes nothing rather than a run of no-op rows.

    ⚠️ **Detaching writes an audit row here; in legacy it does not.**
    ``Allocation.disinherit()`` severs ``parentAllocation`` in memory and leaves
    no trace — production has **zero** DETACH rows, against 2,390 currently
    inheriting allocations. SAM's audit trail is the product, so an inheriting
    allocation is detached through :func:`~sam.manage.allocations.detach_allocation`
    and gets its DETACH row. Declared divergence.

    Runs inside the caller's ``management_transaction()`` — does NOT commit.

    Args:
        session: SQLAlchemy session.
        allocation: the allocation to extend. Its whole child subtree comes too.
        new_end: the new end date. Normalized to 23:59:59 by the column
            validator, so callers may pass either convention.
        comment: ``transaction_comment`` for every row written. The XRAS caller
            passes ``'XrasAction Extension Request'`` — see the handler for why
            that Java class name is reproduced rather than cleaned up.
        user_id: ``None`` for an integration actor, which is the XRAS case.
            See :func:`~sam.manage.allocations.log_allocation_transaction`.

    Returns:
        The nodes actually modified, root first. Empty when every node was
        already at ``new_end``.

    Raises:
        ValueError: if any node in the subtree has a null ``end_date`` or an
            ``end_date`` after ``new_end``. Checked over the **whole subtree
            before anything is written**, matching legacy's
            ``validateNewEndDate`` → ``extend`` ordering: one bad descendant
            aborts the extension rather than half-applying it.
    """
    subtree: List[Allocation] = []
    allocation._walk_tree(lambda node: subtree.append(node))

    # Validate everything first. Legacy walks the tree twice for the same
    # reason: a partially-extended tree is worse than an unextended one.
    for node in subtree:
        if node.end_date is None:
            raise ValueError(
                f"Allocation {node.allocation_id} has no end date and cannot be "
                f"extended"
            )
        if new_end < node.end_date:
            raise ValueError(
                f"Allocation {node.allocation_id} ends {node.end_date:%Y-%m-%d}, "
                f"after the requested {new_end:%Y-%m-%d}"
            )

    if allocation.is_inheriting:
        detach_allocation(session, allocation.allocation_id, user_id)

    modified: List[Allocation] = []
    for node in subtree:
        if node.end_date == new_end:
            continue                      # legacy's early return in doExtend
        node.end_date = new_end
        # `log_integration_transaction` rather than `log_allocation_transaction`: the
        # latter snapshots the allocation's state into every row, and for EXTENSION the
        # table's convention is that only `alloc_end_date` is meaningful. See that
        # function for the measurement (20,603 of 20,618 production rows).
        log_integration_transaction(
            session, node, AllocationTransactionType.EXTENSION,
            comment=comment,
            alloc_end_date=new_end,
        )
        modified.append(node)

    session.flush()
    return modified
