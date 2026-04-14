"""
Extend allocation logic — push ``end_date`` forward on a project tree's
existing allocations without creating new rows.

Mirrors Renew's tree-awareness (handles both inheriting and standalone
sub-project topologies) but mutates the existing allocations in place
and logs ``AllocationTransactionType.EXTENSION`` instead of creating new
rows under ``RENEW``. Use Renew when you want a fresh allocation period;
use Extend when you want a grace-period push on the current grant.

Skips silently when:
  - a resource has no root-project source active at ``source_active_at``
  - a source allocation is open-ended (``end_date IS NULL``)
  - a source allocation already ends on/after the requested new date
    (would be a no-op or a shortening — use Edit Allocation for that)
"""

from datetime import datetime
from typing import List

from sqlalchemy.orm import Session

from sam.accounting.allocations import (
    Allocation,
    AllocationTransactionType,
)
from sam.projects.projects import Project
from sam.manage.allocations import (
    log_allocation_transaction,
    validate_allocation_dates,
)
from sam.manage.renew import find_source_alloc_at


__all__ = [
    'extend_project_allocations',
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
