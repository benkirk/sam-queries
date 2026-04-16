"""
Renew allocation logic — clone a project's allocations into a new time period.

Renewal is tree-aware and handles both topology styles found in SAM:

  1. **Inheriting (shared) tree** — e.g. NMMM0003: the root project has a
     non-inheriting allocation, and sub-projects have child allocations
     linked via ``parent_allocation_id``. All nodes share the same amount.

  2. **Standalone (divergent) tree** — e.g. CESM0002: the root project and
     each sub-project each have their OWN non-inheriting allocation, with
     potentially different amounts.

A single tree can mix both styles across resources, so renewal walks
every descendant project and renews whichever source allocation that
project had at ``source_active_at``, preserving each row's own amount
and its original parent-link style.

The source snapshot is determined by the caller-supplied ``source_active_at``
date, which mirrors the "Active At" filter on the Admin > Edit Project >
Allocations tab: renew renews what the admin sees.
"""

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from sam.accounting.allocations import (
    Allocation,
    AllocationTransactionType,
)
from sam.fmt import round_to_sig_figs
from sam.projects.projects import Project
from sam.manage.allocations import (
    create_allocation,
    date_ranges_overlap,
    log_allocation_transaction,
    validate_allocation_dates,
)


__all__ = [
    'find_source_allocations_at',
    'find_renewable_descendants',
    'renew_project_allocations',
]


def find_source_alloc_at(
    project: Project,
    resource_id: int,
    check_date: datetime,
) -> Optional[Allocation]:
    """Return the single non-deleted allocation for ``project`` + ``resource_id``
    that was active at ``check_date``, or None.

    If multiple are active (rare — overlapping allocations), the one with the
    latest ``start_date`` wins so renewal anchors on the most-recent grant.
    """
    matches: List[Allocation] = []
    for account in project.accounts:
        if account.resource_id != resource_id:
            continue
        for alloc in account.allocations:
            if alloc.deleted:
                continue
            if alloc.is_active_at(check_date):
                matches.append(alloc)
    if not matches:
        return None
    matches.sort(key=lambda a: a.start_date, reverse=True)
    return matches[0]


def _account_has_overlapping_alloc(
    project: Project,
    resource_id: int,
    new_start: datetime,
    new_end: datetime,
) -> bool:
    """True if ``project`` already has a non-deleted allocation for
    ``resource_id`` whose date range overlaps [new_start, new_end].

    Used to avoid creating a duplicate when renew is clicked twice or when
    the target period already exists.
    """
    class _Range:
        def __init__(self, s, e):
            self.start_date = s
            self.end_date = e

    target = _Range(new_start, new_end)
    for account in project.accounts:
        if account.resource_id != resource_id:
            continue
        for alloc in account.allocations:
            if alloc.deleted:
                continue
            if date_ranges_overlap(alloc, target):
                return True
    return False


def find_source_allocations_at(
    session: Session,
    root_project: Project,
    source_active_at: datetime,
) -> List[Allocation]:
    """Return the non-inheriting allocations on ``root_project`` that are
    active at ``source_active_at``.

    Shared by Renew and Extend — one row per resource. Inheriting (child)
    allocations are excluded at the root level because both flows operate
    from the tree root; sub-project rows are discovered by walking
    descendants inside the caller.
    """
    candidates: List[Allocation] = []
    for account in root_project.accounts:
        for alloc in account.allocations:
            if alloc.is_inheriting:
                continue
            if alloc.is_active_at(source_active_at):
                candidates.append(alloc)
    return candidates


def find_renewable_descendants(
    root_project: Project,
    resource_id: int,
    check_date: datetime,
) -> List[Project]:
    """Return descendant projects (DFS pre-order) that had any non-deleted
    allocation for ``resource_id`` active at ``check_date`` — inheriting or
    standalone. These are the projects renew will create new rows on.
    """
    return [
        d for d in root_project.get_descendants()
        if d.active and find_source_alloc_at(d, resource_id, check_date) is not None
    ]


def renew_project_allocations(
    session: Session,
    *,
    root_project_id: int,
    source_active_at: datetime,
    new_start: datetime,
    new_end: datetime,
    resource_ids: List[int],
    user_id: int,
    scales: Optional[Dict[int, float]] = None,
) -> List[Allocation]:
    """Clone a project tree's active-at-a-date allocations into a new period.

    Per resource:
      1. Locate the root-project non-inheriting allocation active at
         ``source_active_at``. Skip the resource if the root has none.
      2. Create a new root allocation with the same amount/description and
         log a ``RENEW`` transaction referencing the source.
      3. Walk descendants in DFS pre-order. For each descendant project
         that had a source allocation for this resource at
         ``source_active_at``:
           - **Inheriting source**: create a new inheriting allocation
             linked to the renewed immediate project-parent (preserves
             the shared-pool topology that existed at the source date).
           - **Standalone source**: create a new standalone allocation on
             that project with the *child's own* source amount.
         Each child mutation is logged as ``RENEW`` (``propagated=True``
         when inheriting).
      4. Skip any target account that already has an overlapping
         non-deleted allocation in [new_start, new_end] — renew is
         idempotent on double-click.

    Runs inside the caller's ``management_transaction()`` — does NOT commit.

    Returns the list of newly-created root allocations (one per renewed
    resource).
    """
    validate_allocation_dates(new_start, new_end)

    root_project = session.get(Project, root_project_id)
    if root_project is None:
        raise ValueError(f"Project {root_project_id} not found")

    all_descendants = root_project.get_descendants()
    requested = set(resource_ids)
    scales = scales or {}
    created_roots: List[Allocation] = []

    for resource_id in requested:
        scale = scales.get(resource_id, 1.0)

        source_root = find_source_alloc_at(
            root_project, resource_id, source_active_at
        )
        if source_root is None or source_root.is_inheriting:
            continue

        if _account_has_overlapping_alloc(
            root_project, resource_id, new_start, new_end
        ):
            # Already renewed — nothing to do for this resource.
            continue

        # Scale + round to SAM_SIG_FIGS (allocations are human-defined at
        # ~3 sig figs). Guarded so scale=1.0 renewals stay byte-identical
        # to pre-scale behaviour.
        scaled_root_amount = source_root.amount * scale
        if scale != 1.0:
            scaled_root_amount = round_to_sig_figs(scaled_root_amount)

        new_root = create_allocation(
            session,
            project_id=root_project_id,
            resource_id=resource_id,
            amount=scaled_root_amount,
            start_date=new_start,
            end_date=new_end,
            description=source_root.description,
            user_id=user_id,
        )
        log_allocation_transaction(
            session,
            new_root,
            user_id,
            AllocationTransactionType.RENEW,
            comment=(
                f"Renewed from allocation #{source_root.allocation_id} "
                f"({source_root.start_date.strftime('%Y-%m-%d')} → "
                f"{source_root.end_date.strftime('%Y-%m-%d') if source_root.end_date else 'open'})"
                + (f" — scaled ×{scale:g}" if scale != 1.0 else "")
            ),
            old_values={},
        )
        created_roots.append(new_root)

        # project_id → new allocation_id (for re-wiring inheriting children).
        alloc_map: Dict[int, int] = {
            root_project.project_id: new_root.allocation_id,
        }

        for descendant in all_descendants:
            if not descendant.active:
                continue

            source_child = find_source_alloc_at(
                descendant, resource_id, source_active_at
            )
            if source_child is None:
                continue

            if _account_has_overlapping_alloc(
                descendant, resource_id, new_start, new_end
            ):
                continue

            if source_child.is_inheriting:
                new_parent_id = alloc_map.get(descendant.parent_id)
                propagated = new_parent_id is not None
            else:
                new_parent_id = None
                propagated = False

            scaled_child_amount = source_child.amount * scale
            if scale != 1.0:
                scaled_child_amount = round_to_sig_figs(scaled_child_amount)

            new_child = Allocation.create(
                session,
                project_id=descendant.project_id,
                resource_id=resource_id,
                amount=scaled_child_amount,
                start_date=new_start,
                end_date=new_end,
                description=source_child.description,
                parent_allocation_id=new_parent_id,
            )
            log_allocation_transaction(
                session,
                new_child,
                user_id,
                AllocationTransactionType.RENEW,
                comment=(
                    f"Renewed from allocation #{source_child.allocation_id}"
                    + (f" — scaled ×{scale:g}" if scale != 1.0 else "")
                ),
                old_values={},
                propagated=propagated,
            )
            alloc_map[descendant.project_id] = new_child.allocation_id

    return created_roots
