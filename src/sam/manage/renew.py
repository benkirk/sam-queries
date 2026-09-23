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

Each resource is walked from its *anchors* (``find_renew_anchors``): the root
when it holds the resource, else the topmost sub-projects that do. Without
the fallback a sub-project-only resource was skipped as ``no_source``.

The source snapshot is determined by the caller-supplied ``source_active_at``
date, which mirrors the "Active At" filter on the Admin > Edit Project >
Allocations tab: renew renews what the admin sees.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from sam.accounting.allocations import (
    Allocation,
    AllocationTransactionType,
)
from sam.fmt import round_to_sig_figs
from sam.projects.projects import Project
from sam.manage.allocations import (
    date_ranges_overlap,
    log_allocation_transaction,
    update_allocation,
    validate_allocation_dates,
)


__all__ = [
    'find_source_allocations_at',
    'find_renewable_descendants',
    'find_renew_anchors',
    'find_child_only_resources',
    'renew_project_allocations',
    'analyze_renew_preconditions',
    'analyze_renew_overlap',
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


def _find_overlapping_allocs(
    project: Project,
    resource_id: int,
    new_start: datetime,
    new_end: datetime,
) -> List[Allocation]:
    """Return non-deleted allocations on ``project`` for ``resource_id``
    whose date range overlaps [new_start, new_end].
    """
    class _Range:
        def __init__(self, s, e):
            self.start_date = s
            self.end_date = e

    target = _Range(new_start, new_end)
    hits: List[Allocation] = []
    for account in project.accounts:
        if account.resource_id != resource_id:
            continue
        for alloc in account.allocations:
            if alloc.deleted:
                continue
            if date_ranges_overlap(alloc, target):
                hits.append(alloc)
    return hits


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
    return bool(_find_overlapping_allocs(project, resource_id, new_start, new_end))


def _truncate_overlapping_allocs(
    session: Session,
    project: Project,
    resource_id: int,
    new_start: datetime,
    new_end: datetime,
    user_id: int,
) -> List[Allocation]:
    """Clear the way for a renewed allocation covering [new_start, new_end].

    An overlap that **starts before** ``new_start`` (the FY-crossing case) is
    **truncated**: its ``end_date`` is pulled back to ``new_start - 1s`` via
    ``update_allocation`` (an EDIT->ADJUSTMENT carrying ``transaction_amount``
    0 — a replay no-op), so coverage hands off to the new row with no gap and
    the old row survives. This replaces the old delete-and-recreate behavior,
    which left the account with no current allocation between now and the new
    start (fstree ``Waiting``); see docs/plans/FY27_PROD_RENEW_HANDOFF.md.

    An overlap that starts on/after ``new_start`` sits fully inside the new
    window and cannot be truncated (``end < start``); it is genuinely
    superseded, so it is soft-deleted — the double-click-same-period case.
    Inheriting children of that fully-contained case ARE soft-deleted here (a
    soft-delete does not cascade). In the truncate branch they are instead
    skipped: ``update_allocation`` refuses an inheriting child, and the
    master's truncation already cascades the child's ``end_date``.

    Returns the list of touched (truncated or soft-deleted) allocations.
    """
    touched: List[Allocation] = []
    handoff_end = new_start - timedelta(seconds=1)
    period = (
        f"{new_start.strftime('%Y-%m-%d')} → {new_end.strftime('%Y-%m-%d')}"
    )
    for alloc in _find_overlapping_allocs(project, resource_id, new_start, new_end):
        if alloc.start_date < new_start:
            if alloc.is_inheriting:
                continue
            update_allocation(
                session,
                alloc.allocation_id,
                user_id,
                end_date=handoff_end,
                comment=f"Truncated by renew to hand off to {period}",
            )
        else:
            alloc.deleted = True
            log_allocation_transaction(
                session,
                alloc,
                user_id,
                AllocationTransactionType.DELETE,
                comment=f"Superseded by renew on {period}",
                old_values={},
            )
        touched.append(alloc)
    return touched


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


def analyze_renew_preconditions(
    session: Session,
    *,
    root_project_id: int,
    source_active_at: datetime,
    new_start: datetime,
    new_end: datetime,
    resource_ids: List[int],
) -> Dict[int, str]:
    """Classify each requested resource for a Renew request WITHOUT mutating.

    Returns a dict mapping resource_id -> one of:
      'ok'        — renew will create new allocations for this resource
      'no_source' — no project in the tree has a non-inheriting allocation
                    active at ``source_active_at`` (renew would skip it)
      'overlap'   — every anchor already has a non-deleted allocation whose
                    date range overlaps [new_start, new_end] (idempotent
                    skip — usually indicates renew was applied previously)

    Callers use this to produce accurate user-facing messages instead of
    the old catch-all "nothing was renewed" string, and to decide whether
    to prompt for the ``replace_existing`` override.
    """
    root = session.get(Project, root_project_id)
    if root is None:
        raise ValueError(f"Project {root_project_id} not found")

    result: Dict[int, str] = {}
    for rid in resource_ids:
        anchors = find_renew_anchors(root, rid, source_active_at)
        if not anchors:
            result[rid] = 'no_source'
        elif all(_account_has_overlapping_alloc(p, rid, new_start, new_end)
                 for p, _ in anchors):
            result[rid] = 'overlap'
        else:
            result[rid] = 'ok'
    return result


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


def _is_ancestor(ancestor: Project, node: Project) -> bool:
    return ancestor.tree_left < node.tree_left and node.tree_right < ancestor.tree_right


def find_renew_anchors(
    root_project: Project,
    resource_id: int,
    check_date: datetime,
) -> List[Tuple[Project, Allocation]]:
    """Return the ``(project, source)`` pairs a Renew/Extend of ``resource_id``
    walks from: the root when it has a non-inheriting source at
    ``check_date``, else the topmost active sub-projects that do.
    """
    src = find_source_alloc_at(root_project, resource_id, check_date)
    if src is not None and not src.is_inheriting:
        return [(root_project, src)]
    anchors: List[Tuple[Project, Allocation]] = []
    for d in root_project.get_descendants():
        if not d.active or any(_is_ancestor(a, d) for a, _ in anchors):
            continue
        src = find_source_alloc_at(d, resource_id, check_date)
        if src is not None and not src.is_inheriting:
            anchors.append((d, src))
    return anchors


def find_child_only_resources(
    root_project: Project,
    check_date: datetime,
) -> Dict[int, List[Tuple[Project, Allocation]]]:
    """Map resource_id -> anchors for each resource the root has no source
    for but some sub-project does (the resources a root-only walk misses)."""
    root_rids = {
        a.account.resource_id
        for a in find_source_allocations_at(None, root_project, check_date)
    }
    candidate_rids = sorted({
        acc.resource_id
        for d in root_project.get_descendants() if d.active
        for acc in d.accounts
        if acc.resource_id not in root_rids
    })
    result: Dict[int, List[Tuple[Project, Allocation]]] = {}
    for rid in candidate_rids:
        anchors = find_renew_anchors(root_project, rid, check_date)
        if anchors:
            result[rid] = anchors
    return result


def analyze_renew_overlap(
    session: Session,
    *,
    root_project_id: int,
    source_active_at: datetime,
    new_start: datetime,
    new_end: datetime,
    resource_ids: List[int],
) -> Dict[str, object]:
    """Census the allocations across the whole tree that a renew into
    [new_start, new_end] would supersede, and whether truncating them
    preserves coverage.

    Drives the Renew modal's contextual "Truncate existing" control. Mirrors
    renew's own gating: a resource counts only when it has anchors
    (``find_renew_anchors``), and overlaps are counted only on the anchors and
    the projects under them renew would create rows on.

    Classifies each overlap three ways against ``[new_start, new_end]``:
      - starts **before** new_start, ends on/before new_end → cleanly
        truncatable (hand-off, no loss)
      - starts **before** new_start, ends **past** new_end (or open-ended) →
        *shrinking*: truncating would drop the tail
      - starts **at/after** new_start → *collision*: an allocation already
        occupies the target window (the period was likely already renewed by
        someone else), so replacing it is a soft-delete + recreate that loses
        that row's later changes

    Returns a dict:
      ``count``      — overlapping allocations renew would supersede (tree-wide)
      ``preserving`` — True iff ``count > 0`` and every overlap is cleanly
                       truncatable (no collisions, none shrinking); only then
                       does the control default ON
      ``collisions`` — ``[{resource_name, start_date, end_date, created}]`` for
                       resources already covered in the target window
      ``shrinking``  — ``[{resource_name, end_date}]`` (``end_date`` None means
                       open-ended) for resources whose coverage runs past
                       ``new_end``
    """
    root = session.get(Project, root_project_id)
    if root is None:
        raise ValueError(f"Project {root_project_id} not found")

    count = 0
    worst_end: Dict[str, Optional[datetime]] = {}
    collided: Dict[str, Dict[str, object]] = {}
    for rid in resource_ids:
        projects = [
            p
            for anchor, _ in find_renew_anchors(root, rid, source_active_at)
            for p in [anchor] + find_renewable_descendants(anchor, rid, source_active_at)
        ]
        for proj in projects:
            for alloc in _find_overlapping_allocs(proj, rid, new_start, new_end):
                count += 1
                name = alloc.account.resource.resource_name
                if alloc.start_date >= new_start:
                    # Already occupies the target window — the period was likely
                    # renewed already. Keep the most-recently-created collider.
                    prev = collided.get(name)
                    if prev is None or (
                        alloc.creation_time is not None
                        and (prev['created'] is None
                             or alloc.creation_time > prev['created'])
                    ):
                        collided[name] = {
                            'start_date': alloc.start_date,
                            'end_date': alloc.end_date,
                            'created': alloc.creation_time,
                        }
                elif alloc.end_date is None or alloc.end_date > new_end:
                    # Front overlap running past the new end: truncating drops
                    # the tail [new_end + 1 .. old_end].
                    if name not in worst_end:
                        worst_end[name] = alloc.end_date
                    elif worst_end[name] is not None:
                        worst_end[name] = (
                            None if alloc.end_date is None
                            else max(worst_end[name], alloc.end_date)
                        )
                # else: front overlap ending on/before new_end — clean truncate.

    collisions = [
        {'resource_name': name, **info} for name, info in sorted(collided.items())
    ]
    shrinking = [
        {'resource_name': name, 'end_date': end}
        for name, end in sorted(worst_end.items())
    ]
    return {
        'count': count,
        'preserving': count > 0 and not collisions and not shrinking,
        'collisions': collisions,
        'shrinking': shrinking,
    }


def _renew_subtree(
    session: Session,
    anchor: Project,
    source: Allocation,
    resource_id: int,
    *,
    source_active_at: datetime,
    new_start: datetime,
    new_end: datetime,
    user_id: int,
    scale: float,
    replace_existing: bool,
    touched: Optional[List[Allocation]],
) -> Optional[Allocation]:
    """Renew one anchor's allocation and every descendant source under it.

    Returns the new anchor allocation, or None when the anchor already
    overlaps the new period and ``replace_existing`` is off.
    """
    if _account_has_overlapping_alloc(anchor, resource_id, new_start, new_end):
        if not replace_existing:
            # Already renewed — nothing to do for this subtree.
            return None
        # Superseded rows are NOT added to ``touched`` — that list drives
        # the "renewed" notice, which should name only the new rows.
        _truncate_overlapping_allocs(
            session, anchor, resource_id, new_start, new_end, user_id,
        )

    # Scale + round to SAM_SIG_FIGS (allocations are human-defined at
    # ~3 sig figs). Guarded so scale=1.0 renewals stay byte-identical
    # to pre-scale behavior.
    scaled_amount = source.amount * scale
    if scale != 1.0:
        scaled_amount = round_to_sig_figs(scaled_amount)

    # One row via the model classmethod plus exactly ONE RENEW transaction;
    # manage.create_allocation() would add a second (CREATE) audit row.
    new_anchor = Allocation.create(
        session,
        project_id=anchor.project_id,
        resource_id=resource_id,
        amount=scaled_amount,
        start_date=new_start,
        end_date=new_end,
        description=source.description,
        allow_zero=True,  # mirror a 0-amount source (e.g. a 0 reserve)
    )
    log_allocation_transaction(
        session,
        new_anchor,
        user_id,
        AllocationTransactionType.RENEW,
        comment=(
            f"Renewed from allocation #{source.allocation_id} "
            f"({source.start_date.strftime('%Y-%m-%d')} → "
            f"{source.end_date.strftime('%Y-%m-%d') if source.end_date else 'open'})"
            + (f" — scaled ×{scale:g}" if scale != 1.0 else "")
        ),
        old_values={},
    )
    if touched is not None:
        touched.append(new_anchor)

    # project_id -> new allocation_id (for re-wiring inheriting children).
    alloc_map: Dict[int, int] = {anchor.project_id: new_anchor.allocation_id}

    for descendant in anchor.get_descendants():
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
            if not replace_existing:
                continue
            _truncate_overlapping_allocs(
                session, descendant, resource_id, new_start, new_end, user_id,
            )

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
            allow_zero=True,  # mirror a 0-amount source (e.g. a 0 reserve)
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
        if touched is not None:
            touched.append(new_child)

    return new_anchor


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
    replace_existing: bool = False,
    touched: Optional[List[Allocation]] = None,
) -> List[Allocation]:
    """Clone a project tree's active-at-a-date allocations into a new period.

    Per resource, for each anchor (``find_renew_anchors``: the root, or the
    topmost sub-projects when only they hold the resource):
      1. Create a new anchor allocation with the same amount/description and
         log a ``RENEW`` transaction referencing the source.
      2. Walk the anchor's descendants in DFS pre-order. For each descendant
         project that had a source allocation for this resource at
         ``source_active_at``:
           - **Inheriting source**: create a new inheriting allocation
             linked to the renewed immediate project-parent (preserves
             the shared-pool topology that existed at the source date).
           - **Standalone source**: create a new standalone allocation on
             that project with the *child's own* source amount.
         Each child mutation is logged as ``RENEW`` (``propagated=True``
         when inheriting).
      3. Skip any target account that already has an overlapping
         non-deleted allocation in [new_start, new_end] — renew is
         idempotent on double-click; an overlapping anchor skips its whole
         subtree. When ``replace_existing=True`` the overlapping
         allocations are cleared BEFORE the new row is created (see
         ``_truncate_overlapping_allocs``): a crossing allocation that
         starts before ``new_start`` is *truncated* to the handoff boundary
         so coverage is contiguous, while one fully inside the new window is
         soft-deleted.

    Runs inside the caller's ``management_transaction()`` — does NOT commit.

    Returns the newly-created anchor allocations (one per renewed anchor).
    ``touched``, when given, collects every created allocation, anchors and
    descendants alike.
    """
    validate_allocation_dates(new_start, new_end)

    root_project = session.get(Project, root_project_id)
    if root_project is None:
        raise ValueError(f"Project {root_project_id} not found")

    scales = scales or {}
    created: List[Allocation] = []
    for resource_id in set(resource_ids):
        for anchor, source in find_renew_anchors(
            root_project, resource_id, source_active_at
        ):
            new_anchor = _renew_subtree(
                session, anchor, source, resource_id,
                source_active_at=source_active_at,
                new_start=new_start,
                new_end=new_end,
                user_id=user_id,
                scale=scales.get(resource_id, 1.0),
                replace_existing=replace_existing,
                touched=touched,
            )
            if new_anchor is not None:
                created.append(new_anchor)
    return created
