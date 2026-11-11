"""
Allocation query functions for SAM.

This module provides functions for retrieving allocation data including
allocation history, active allocations, and allocation summaries by
various criteria (type, resource, facility).

Functions:
    get_project_allocations: Get all allocations for a project
    get_active_allocation: Get currently active allocation for a project
    get_latest_allocation_for_project: Get most recent allocation
    get_allocation_history: Get transaction history for a project
    get_recent_allocation_transactions: Cross-project transaction query with
        flexible filters (date range, project, resource, facility, user, …)
    get_allocations_by_type: Get allocations of a specific type
    get_allocations_by_resource: Get allocations for a specific resource
    get_allocation_summary_by_facility: Get summary statistics by facility
    get_allocation_summary: Get flexible allocation summaries with grouping and filtering
"""

from datetime import datetime, timedelta
from typing import Any, List, Optional, Dict, Tuple, Union

from sqlalchemy import or_, func
from sqlalchemy.orm import Session, aliased, noload

from sam.dates import end_of_day
from sam.accounting.calculator import (anchor_sums, anchored_charges, get_charge_models_for_activity,
                                       usage_anchor)

from sam.core.users import User
from sam.projects.projects import Project
from sam.accounting.allocations import (
    Allocation,
    AllocationTransaction,
    AllocationTransactionType,
    AllocationType,
)
from sam.resources.resources import Resource, ResourceType
from sam.resources.facilities import Facility, Panel
from sam.accounting.accounts import Account
from sam.accounting.adjustments import ChargeAdjustment


# ============================================================================
# Allocation Queries
# ============================================================================

def get_project_allocations(session: Session, projcode: str, resource_name: str = None) -> List[Tuple[Allocation, str]]:
    """
    Get all allocations for a project, optionally filtered by resource.

    Args:
        session: SQLAlchemy session
        projcode: Project code
        resource_name: Optional resource name to filter

    Returns:
        List of (Allocation, resource_name) tuples
    """
    query = session.query(Allocation, Resource.resource_name)\
        .join(Account, Allocation.account_id == Account.account_id)\
        .join(Project, Account.project_id == Project.project_id)\
        .join(Resource, Account.resource_id == Resource.resource_id)\
        .filter(
            Project.projcode == projcode,
            Allocation.deleted == False
        )

    if resource_name:
        query = query.filter(Resource.resource_name == resource_name)

    return query.order_by(Resource.resource_name, Allocation.start_date.desc()).all()


def get_active_allocation(session: Session, projcode: str) -> Optional[Allocation]:
    """Get the current active allocation for a project."""
    now = datetime.now()
    return session.query(Allocation)\
        .join(Account, Allocation.account_id == Account.account_id)\
        .join(Project, Account.project_id == Project.project_id)\
        .filter(
            Project.projcode == projcode,
            Allocation.deleted == False,
            Allocation.start_date <= now,
            or_(
                Allocation.end_date.is_(None),
                Allocation.end_date >= now
            )
        )\
        .order_by(Allocation.allocation_id.desc())\
        .first()


def get_latest_allocation_for_project(session: Session, project_id: int) -> Optional[Allocation]:
    """
    Helper function to get the most recent allocation for a project.
    This is a simpler alternative when you just need one project's latest allocation.
    """
    return session.query(Allocation)\
        .join(Account, Allocation.account_id == Account.account_id)\
        .filter(
            Account.project_id == project_id,
            Allocation.deleted == False
        )\
        .order_by(Allocation.allocation_id.desc())\
        .first()


def latest_allocation_end_by_project(session: Session, project_ids) -> Dict[int, datetime]:
    """The latest allocation ``end_date`` on each project's accounts, in one statement."""
    if not project_ids:
        return {}
    rows = session.query(Account.project_id, func.max(Allocation.end_date))\
        .join(Allocation, Allocation.account_id == Account.account_id)\
        .filter(Account.project_id.in_(list(project_ids)))\
        .group_by(Account.project_id)
    return {pid: end for pid, end in rows if end is not None}


def get_allocation_history(
    session: Session,
    projcode: str
) -> List[Dict]:
    """Get complete allocation transaction history for a project."""
    results = session.query(
        AllocationTransaction,
        Allocation,
        User
    )\
        .join(Allocation, AllocationTransaction.allocation_id == Allocation.allocation_id)\
        .join(Account, Allocation.account_id == Account.account_id)\
        .join(Project, Account.project_id == Project.project_id)\
        .outerjoin(User, AllocationTransaction.user_id == User.user_id)\
        .filter(Project.projcode == projcode)\
        .order_by(AllocationTransaction.creation_time)\
        .all()

    history = []
    for txn, alloc, user in results:
        history.append({
            'transaction_date': txn.creation_time,
            'transaction_type': txn.transaction_type,
            'requested_amount': txn.requested_amount,
            'transaction_amount': txn.transaction_amount,
            'start_date': txn.alloc_start_date,
            'end_date': txn.alloc_end_date,
            'processed_by': user.full_name if user else None,
            'comment': txn.transaction_comment,
            'auth_at_panel': txn.auth_at_panel_mtg
        })

    return history


ALLOCATION_TRANSACTION_SORT_COLUMNS = {
    'creation_time': AllocationTransaction.creation_time,
    'transaction_type': AllocationTransaction.transaction_type,
    'transaction_amount': AllocationTransaction.transaction_amount,
    'projcode': Project.projcode,
    'resource_name': Resource.resource_name,
    # URL-facing sort key is the row-dict key ('facility'); maps to the
    # underlying SQL column ``Facility.facility_name``. Keep the key
    # consistent with the row-level identifier produced below.
    'facility': Facility.facility_name,
    'allocation_type': AllocationType.allocation_type,
    'username': User.username,
}


def apply_scope_filters(query, *scopes):
    """Each ``(column, value)`` as a filter: falsy or ``"TOTAL"`` adds none, a list is ``IN``, a scalar ``=``."""
    for column, value in scopes:
        if value and value != "TOTAL":
            query = query.filter(column.in_(value) if isinstance(value, list) else column == value)
    return query


def _apply_transaction_filters(
    query,
    *,
    transaction_id,
    projcode,
    resource_name,
    facility_name,
    allocation_type,
    transaction_types,
    start_date,
    end_date,
    user_id,
    username,
    include_deleted,
    include_propagated,
):
    """Apply the shared WHERE clauses. ``query`` must already have the standard
    JOINs (see ``_transactions_query`` / the get/count callers)."""
    if user_id is not None and username is not None:
        raise ValueError("Pass user_id OR username, not both")

    if transaction_id is not None:
        query = query.filter(
            AllocationTransaction.allocation_transaction_id == transaction_id
        )

    query = apply_scope_filters(
        query, (Project.projcode, projcode), (Resource.resource_name, resource_name),
        (Facility.facility_name, facility_name), (AllocationType.allocation_type, allocation_type))

    if transaction_types is not None:
        if isinstance(transaction_types, (list, tuple, set)):
            values = [str(t) for t in transaction_types]
            query = query.filter(AllocationTransaction.transaction_type.in_(values))
        else:
            query = query.filter(
                AllocationTransaction.transaction_type == str(transaction_types)
            )

    if start_date is not None:
        query = query.filter(AllocationTransaction.creation_time >= start_date)
    if end_date is not None:
        query = query.filter(AllocationTransaction.creation_time <= end_date)

    if user_id is not None:
        query = query.filter(AllocationTransaction.user_id == user_id)
    elif username is not None:
        query = query.filter(User.username == username)

    if not include_deleted:
        query = query.filter(
            Allocation.deleted == False,
            Account.deleted == False,
        )

    if not include_propagated:
        query = query.filter(AllocationTransaction.propagated == False)

    return query


def _join_transaction_query(query):
    """Apply the shared JOIN chain used by get/count variants."""
    return query.join(Allocation, AllocationTransaction.allocation_id == Allocation.allocation_id)\
                .join(Account, Allocation.account_id == Account.account_id)\
                .join(Project, Account.project_id == Project.project_id)\
                .join(Resource, Account.resource_id == Resource.resource_id)\
                .outerjoin(AllocationType, Project.allocation_type_id == AllocationType.allocation_type_id)\
                .outerjoin(Panel, AllocationType.panel_id == Panel.panel_id)\
                .outerjoin(Facility, Panel.facility_id == Facility.facility_id)\
                .outerjoin(User, AllocationTransaction.user_id == User.user_id)


def get_recent_allocation_transactions(
    session: Session,
    *,
    transaction_id: Optional[int] = None,
    projcode: Optional[Union[str, List[str]]] = None,
    resource_name: Optional[Union[str, List[str]]] = None,
    facility_name: Optional[Union[str, List[str]]] = None,
    allocation_type: Optional[Union[str, List[str]]] = None,
    transaction_types: Optional[Union[str, List[str]]] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    include_deleted: bool = False,
    include_propagated: bool = True,
    sort_by: Optional[str] = None,
    sort_dir: str = "desc",
    offset: int = 0,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Query allocation transactions across projects/resources with flexible filters.

    Filter conventions follow get_allocation_summary: each scope argument accepts
    a scalar, a list, None (no filter), or the literal "TOTAL" (also a no-op).
    Dates filter on ``AllocationTransaction.creation_time``. Results are returned
    as denormalized dicts suitable for table/report rendering.

    Args:
        projcode: project code(s) to include.
        resource_name: resource name(s) to include.
        facility_name: facility name(s) to include (joined via Project ->
            AllocationType -> Panel -> Facility).
        allocation_type: allocation type name(s) to include.
        transaction_types: one or more legacy DB strings (``"NEW"``,
            ``"ADJUSTMENT"``, ``"SUPPLEMENT"``, ``"EXTENSION"``,
            ``"TRANSFER"``). The Python-side intent enum
            (``AllocationTransactionType.EDIT`` etc.) is translated to
            legacy strings on write; intent-aware filtering should use
            ``sam.accounting.allocations.intent_filter`` instead.
        start_date, end_date: inclusive bounds on ``creation_time``.
        user_id: filter by responsible-user FK. Mutually exclusive with ``username``.
        username: filter by responsible-user username (joined via User).
        include_deleted: when False (default), hide transactions whose Allocation
            or Account is soft-deleted.
        include_propagated: when False, hide cascaded child-allocation transactions
            (those written when a parent allocation edit fanned out).
        sort_by: column name to sort by. Must be a key in
            ``ALLOCATION_TRANSACTION_SORT_COLUMNS``. Defaults to ``creation_time``.
        sort_dir: ``"asc"`` or ``"desc"``. Default ``"desc"``.
        offset: zero-based row offset for pagination.
        limit: optional row cap.

    Returns:
        A list of dicts, one per transaction, with the keys:

        - ``transaction_id``, ``allocation_id``, ``transaction_type``,
          ``creation_time``
        - ``transaction_amount``, ``requested_amount``
        - ``alloc_start_date``, ``alloc_end_date``
        - ``transaction_comment``, ``auth_at_panel_mtg``, ``propagated``
        - ``projcode``, ``project_id``
        - ``resource_name``, ``resource_id``
        - ``facility_name``, ``allocation_type``
        - ``user_id``, ``username``, ``user_display_name`` (all ``None`` when
          the transaction has no responsible user)
    """
    if sort_by is not None and sort_by not in ALLOCATION_TRANSACTION_SORT_COLUMNS:
        raise ValueError(
            f"Unknown sort_by={sort_by!r}; allowed: "
            f"{sorted(ALLOCATION_TRANSACTION_SORT_COLUMNS)}"
        )
    if sort_dir not in ("asc", "desc"):
        raise ValueError(f"sort_dir must be 'asc' or 'desc', got {sort_dir!r}")

    sort_col = ALLOCATION_TRANSACTION_SORT_COLUMNS[sort_by] if sort_by \
        else AllocationTransaction.creation_time

    query = _join_transaction_query(session.query(
        AllocationTransaction,
        Project,
        Resource,
        AllocationType.allocation_type.label('allocation_type_name'),
        Facility.facility_name.label('facility_name_val'),
        User,
    ))
    query = _apply_transaction_filters(
        query,
        transaction_id=transaction_id,
        projcode=projcode, resource_name=resource_name, facility_name=facility_name,
        allocation_type=allocation_type, transaction_types=transaction_types,
        start_date=start_date, end_date=end_date,
        user_id=user_id, username=username,
        include_deleted=include_deleted, include_propagated=include_propagated,
    )

    order = sort_col.asc() if sort_dir == "asc" else sort_col.desc()
    query = query.order_by(
        order,
        AllocationTransaction.allocation_transaction_id.desc(),
    )

    if offset:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)

    rows = query.all()

    return [
        {
            'transaction_id': txn.allocation_transaction_id,
            'allocation_id': txn.allocation_id,
            'transaction_type': txn.transaction_type,
            'creation_time': txn.creation_time,
            'transaction_amount': txn.transaction_amount,
            'requested_amount': txn.requested_amount,
            'alloc_start_date': txn.alloc_start_date,
            'alloc_end_date': txn.alloc_end_date,
            'transaction_comment': txn.transaction_comment,
            'auth_at_panel_mtg': txn.auth_at_panel_mtg,
            'propagated': txn.propagated,
            'projcode': project.projcode,
            'project_id': project.project_id,
            'resource_name': resource.resource_name,
            'resource_id': resource.resource_id,
            'facility': fac_name,
            'allocation_type': at_name,
            'user_id': user.user_id if user is not None else None,
            'username': user.username if user is not None else None,
            'user_display_name': user.display_name if user is not None else None,
        }
        for txn, project, resource, at_name, fac_name, user in rows
    ]


def count_recent_allocation_transactions(
    session: Session,
    *,
    transaction_id: Optional[int] = None,
    projcode: Optional[Union[str, List[str]]] = None,
    resource_name: Optional[Union[str, List[str]]] = None,
    facility_name: Optional[Union[str, List[str]]] = None,
    allocation_type: Optional[Union[str, List[str]]] = None,
    transaction_types: Optional[Union[str, List[str]]] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    include_deleted: bool = False,
    include_propagated: bool = True,
) -> int:
    """Return the total number of allocation transactions matching the filters.

    Shares filter semantics with :func:`get_recent_allocation_transactions`;
    intended for pagination ("page 2 of 47") and summary displays.
    """
    query = _join_transaction_query(
        session.query(func.count(AllocationTransaction.allocation_transaction_id))
    )
    query = _apply_transaction_filters(
        query,
        transaction_id=transaction_id,
        projcode=projcode, resource_name=resource_name, facility_name=facility_name,
        allocation_type=allocation_type, transaction_types=transaction_types,
        start_date=start_date, end_date=end_date,
        user_id=user_id, username=username,
        include_deleted=include_deleted, include_propagated=include_propagated,
    )
    return query.scalar() or 0


def get_allocations_by_type(
    session: Session,
    allocation_type: str,
    active_only: bool = True
) -> List[Tuple[Project, Allocation]]:
    """Get all allocations of a specific type."""
    now = datetime.now()

    query = session.query(Project, Allocation)\
        .join(Account, Project.project_id == Account.project_id)\
        .join(Allocation, Account.account_id == Allocation.account_id)\
        .join(AllocationType, Project.allocation_type_id == AllocationType.allocation_type_id)\
        .filter(
            AllocationType.allocation_type == allocation_type,
            Allocation.deleted == False
        )

    if active_only:
        query = query.filter(
            Allocation.start_date <= now,
            or_(
                Allocation.end_date.is_(None),
                Allocation.end_date >= now
            )
        )

    return query.all()

def get_allocations_by_resource(
    session: Session,
    resource_name: str,
    active_only: bool = True
) -> List[Tuple[Project, Allocation]]:
    """
    Get all allocations for a specific resource.

    Args:
        session: SQLAlchemy session
        resource_name: Name of the resource (e.g., 'Derecho', 'GLADE', 'Campaign')
        active_only: If True, only return currently active allocations

    Returns:
        List of (Project, Allocation) tuples for the specified resource

    Examples:
        >>> # Get all active Derecho allocations
        >>> results = get_allocations_by_resource(session, 'Derecho')
        >>> for project, allocation in results:
        ...     print(f"{project.projcode}: {allocation.amount}")

        >>> # Get all GLADE allocations including expired ones
        >>> results = get_allocations_by_resource(session, 'GLADE', active_only=False)
    """
    now = datetime.now()

    # Build the query joining through the account table to reach resource
    query = session.query(Project, Allocation)\
        .join(Account, Project.project_id == Account.project_id)\
        .join(Allocation, Account.account_id == Allocation.account_id)\
        .join(Resource, Account.resource_id == Resource.resource_id)\
        .filter(
            Resource.resource_name == resource_name,
            Allocation.deleted == False
        )

    # Optionally filter to only currently active allocations
    if active_only:
        query = query.filter(
            Allocation.start_date <= now,
            or_(
                Allocation.end_date.is_(None),
                Allocation.end_date >= now
            )
        )

    return query.all()

def get_allocation_summary_by_facility(
    session: Session,
    start_date: datetime,
    end_date: datetime
) -> List[Dict]:
    """Get allocation summary statistics by facility."""
    results = session.query(
        Facility.facility_name,
        AllocationType.allocation_type,
        func.count(Allocation.allocation_id).label('num_allocations'),
        func.sum(Allocation.amount).label('total_amount'),
        func.avg(Allocation.amount).label('avg_amount')
    )\
        .join(Panel, Facility.facility_id == Panel.facility_id)\
        .join(AllocationType, Panel.panel_id == AllocationType.panel_id)\
        .join(Project, AllocationType.allocation_type_id == Project.allocation_type_id)\
        .join(Account, Project.project_id == Account.project_id)\
        .join(Allocation, Account.account_id == Allocation.account_id)\
        .filter(
            Allocation.deleted == False,
            Allocation.start_date.between(start_date, end_date)
        )\
        .group_by(Facility.facility_name, AllocationType.allocation_type)\
        .all()

    return [
        {
            'facility': r[0],
            'allocation_type': r[1],
            'count': r[2],
            'total_amount': float(r[3]) if r[3] else 0,
            'avg_amount': float(r[4]) if r[4] else 0
        }
        for r in results
    ]


def _annual_rate(amount: float, start: datetime, end: Optional[datetime],
                 now: datetime) -> Tuple[int, float]:
    """``(duration_days, amount per 365 days)`` for one allocation."""
    if end is None:
        # Open-ended: assume it ends a year from now. No +1, this is an approximation.
        days = (now + timedelta(days=365) - start).days
    else:
        days = (end - start).days + 1   # end_date is inclusive
    return days, (amount / days * 365 if days > 0 else 0.0)


def get_allocation_summary(
    session: Session,
    resource_name: Optional[Union[str, List[str]]] = None,
    facility_name: Optional[Union[str, List[str]]] = None,
    allocation_type: Optional[Union[str, List[str]]] = None,
    projcode: Optional[Union[str, List[str]]] = None,
    active_only: bool = True,
    active_at: Optional[datetime] = None,
    root_only: bool = False,
) -> List[Dict]:
    """
    Allocation amounts and annual rates, grouped by every dimension not given as "TOTAL".

    Each dimension takes None (group by it), "TOTAL" (sum across it), a string or a list
    (filter to those values, still grouped). ``active_at`` defaults to now under
    ``active_only``. ``root_only`` keeps allocations with no parent allocation on projects
    with no parent project, so a shared pool or a subdivided award counts once, at its root.

    Rows carry the grouped fields plus count, total_amount, avg_amount, start_date (earliest),
    end_date (latest), annualized_rate (the sum of each allocation's own rate),
    duration_days (single-allocation rows only), is_open_ended (any member open-ended,
    rated as ending a year from now) and open_ended_count. Rows grouped by project also
    carry is_root and parent_projcode / project_root_projcode (the project tree), and
    is_inheriting (every allocation in the row draws on a parent allocation).
    """
    ParentProject, RootProject = aliased(Project), aliased(Project)
    query = session.query(
        Resource.resource_name, Facility.facility_name, AllocationType.allocation_type,
        Project.projcode, Project.parent_id, ParentProject.projcode, RootProject.projcode,
        Allocation.amount, Allocation.start_date, Allocation.end_date,
        Allocation.parent_allocation_id,
    ).join(Account, Allocation.account_id == Account.account_id)\
     .join(Project, Account.project_id == Project.project_id)\
     .join(Resource, Account.resource_id == Resource.resource_id)\
     .join(AllocationType, Project.allocation_type_id == AllocationType.allocation_type_id)\
     .join(Panel, AllocationType.panel_id == Panel.panel_id)\
     .join(Facility, Panel.facility_id == Facility.facility_id)\
     .outerjoin(ParentProject, ParentProject.project_id == Project.parent_id)\
     .outerjoin(RootProject, RootProject.project_id == Project.tree_root)\
     .filter(
         Allocation.deleted == False,
         Resource.is_active  # Exclude decommissioned resources
     )

    query = apply_scope_filters(
        query, (Resource.resource_name, resource_name), (Facility.facility_name, facility_name),
        (AllocationType.allocation_type, allocation_type), (Project.projcode, projcode))

    if active_only:
        check_date = active_at if active_at is not None else datetime.now()
        query = query.filter(
            Allocation.start_date <= check_date,
            or_(
                Allocation.end_date.is_(None),
                Allocation.end_date >= check_date
            )
        )

    # Two trees double-count: allocation trees (parent_allocation_id, a shared pool) and
    # project trees (Project.parent_id, a subdivided award whose children carve the root).
    if root_only:
        query = query.filter(
            Allocation.parent_allocation_id.is_(None),
            Project.parent_id.is_(None),
        )

    now = datetime.now()
    dims = (('resource', resource_name), ('facility', facility_name),
            ('allocation_type', allocation_type), ('projcode', projcode))
    groups: Dict[tuple, Dict] = {}
    for (res, fac, atype, pc, parent_id, parent_pc, root_pc,
         amount, start, end, parent_alloc_id) in query:
        values = {'resource': res, 'facility': fac, 'allocation_type': atype, 'projcode': pc}
        key = tuple(values[name] for name, dim in dims if dim != "TOTAL")
        item = groups.get(key)
        if item is None:
            item = {name: values[name] for name, dim in dims if dim != "TOTAL"}
            if projcode != "TOTAL":
                item.update(is_root=parent_id is None, parent_projcode=parent_pc,
                            project_root_projcode=root_pc or pc, is_inheriting=True)
            item.update(count=0, total_amount=0.0, start_date=None, end_date=None,
                        annualized_rate=0.0, open_ended_count=0, _days=None)
            groups[key] = item
        amount = float(amount or 0.0)
        days, rate = _annual_rate(amount, start, end, now)
        item['count'] += 1
        item['total_amount'] += amount
        item['annualized_rate'] += rate
        item['_days'] = days
        if start is not None and (item['start_date'] is None or start < item['start_date']):
            item['start_date'] = start
        if end is None:
            item['open_ended_count'] += 1
        elif item['end_date'] is None or end > item['end_date']:
            item['end_date'] = end
        if 'is_inheriting' in item and parent_alloc_id is None:
            item['is_inheriting'] = False

    output = []
    for key in sorted(groups):
        item = groups[key]
        days = item.pop('_days')
        item['avg_amount'] = item['total_amount'] / item['count']
        item['duration_days'] = days if item['count'] == 1 else None
        item['is_open_ended'] = item['open_ended_count'] > 0
        output.append(item)
    return output


def _fetch_all_allocations(
    session: Session,
    resource_name: Optional[Union[str, List[str]]],
    facility_name: Optional[Union[str, List[str]]],
    allocation_type: Optional[Union[str, List[str]]],
    projcode: Optional[Union[str, List[str]]],
    active_only: bool,
    check_date: datetime,
    root_only: bool = False,
    overlaps: Optional[Tuple[datetime, datetime]] = None,
) -> List[tuple]:
    """
    Fetch all active allocations matching the given filters in a single query.

    ``overlaps=(start, end)`` keeps allocations whose dates intersect that window;
    pair it with ``active_only=False``.

    Returns list of (Allocation, resource_name, resource_type, activity_type,
    facility_name, allocation_type_name, projcode, Project, Account) tuples.
    Account.users selectin is suppressed via lazyload — the caller never needs it.
    The facility, allocation_type, and projcode columns are fetched as explicit scalars
    to avoid triggering lazy-loads when building grouping keys.
    """
    query = session.query(
        Allocation,
        Resource.resource_name,
        ResourceType.resource_type,
        Resource.activity_type,
        Facility.facility_name,
        AllocationType.allocation_type,
        Project.projcode,
        Project,
        Account
    ).join(Account, Allocation.account_id == Account.account_id)\
     .join(Project, Account.project_id == Project.project_id)\
     .join(Resource, Account.resource_id == Resource.resource_id)\
     .join(ResourceType, Resource.resource_type_id == ResourceType.resource_type_id)\
     .join(AllocationType, Project.allocation_type_id == AllocationType.allocation_type_id)\
     .join(Panel, AllocationType.panel_id == Panel.panel_id)\
     .join(Facility, Panel.facility_id == Facility.facility_id)\
     .filter(
         Allocation.deleted == False,
         Resource.is_active,
     )\
     .options(noload(Account.users))  # prevent selectin — account.users never accessed here

    # Apply shared filters (same semantics as get_allocation_summary)
    query = apply_scope_filters(
        query, (Resource.resource_name, resource_name), (Facility.facility_name, facility_name),
        (AllocationType.allocation_type, allocation_type), (Project.projcode, projcode))

    if active_only:
        query = query.filter(
            Allocation.start_date <= check_date,
            or_(
                Allocation.end_date.is_(None),
                Allocation.end_date >= check_date
            )
        )

    if overlaps is not None:
        window_start, window_end = overlaps
        query = query.filter(
            Allocation.start_date <= window_end,
            or_(
                Allocation.end_date.is_(None),
                Allocation.end_date >= window_start
            )
        )

    if root_only:
        query = query.filter(
            Allocation.parent_allocation_id.is_(None),  # root-level allocation
            Project.parent_id.is_(None),                # root or standalone project
        )

    return query.all()


def get_allocation_usage_rows(
    session: Session,
    *,
    resource_name: Union[str, List[str]],
    window_start: datetime,
    window_end: datetime,
    as_of: datetime,
    include_adjustments: bool = True,
) -> List[Dict]:
    """One row per root allocation overlapping the window, with usage as of ``as_of``.

    Unlike get_allocation_summary_with_usage() nothing is grouped, so a project's
    ended and current allocations stay separate rows (the pace chart draws each).
    Charges count through the end of the ``as_of`` day; an allocation starting
    after it reads 0. ``total_used`` is lifetime usage. Disk occupancy is not substituted: callers
    wanting the disk capacity figure use get_allocation_summary_with_usage().
    """
    as_of_end = end_of_day(as_of)
    rows = _fetch_all_allocations(
        session, resource_name, None, None, None,
        active_only=False, check_date=as_of_end, root_only=True,
        overlaps=(window_start, window_end),
    )

    anchors = []
    for alloc, _rn, res_type, activity_type, _fn, _atn, _pc, project, account in rows:
        if alloc.start_date > as_of_end:
            continue
        end = min(alloc.end_date, as_of_end) if alloc.end_date else as_of_end
        anchors.append(usage_anchor(alloc.allocation_id, project, account, activity_type,
                                    alloc.start_date, end, res_type))

    def _used(key):
        c = charges.get(key)
        if c is None:
            return 0.0
        return sum(c['charges_by_type'].values()) + (c['adjustment'] if include_adjustments else 0.0)

    charges = anchored_charges(session, anchors, include_adjustments=include_adjustments)

    out = []
    for alloc, res_name, _rt, _act, fac_name, at_name, projcode, _proj, _acct in rows:
        out.append({
            'projcode': projcode,
            'resource': res_name,
            'facility': fac_name,
            'allocation_type': at_name,
            'allocation_id': alloc.allocation_id,
            'start_date': alloc.start_date,
            'end_date': alloc.end_date,
            'total_amount': float(alloc.amount or 0.0),
            'total_used': _used(alloc.allocation_id),
        })
    return out


def get_allocation_burn(
    session: Session,
    *,
    resource_name: Union[str, List[str]],
    window_start: datetime,
    window_end: datetime,
    as_of: datetime,
    include_adjustments: bool = True,
) -> Dict[int, Dict[int, float]]:
    """``{allocation_id: {yyyymm: charges}}`` over get_allocation_usage_rows()'s allocations, each
    within its own dates, the window and the end of the ``as_of`` day; empty months are absent.
    Per-anchor dates ride in the anchors table: per path, one query per charge model + adjustments."""
    as_of_end = end_of_day(as_of)
    rows = _fetch_all_allocations(
        session, resource_name, None, None, None,
        active_only=False, check_date=as_of_end, root_only=True,
        overlaps=(window_start, window_end),
    )
    paths = {'subtree': {}, 'account': {}}   # path -> activity_type -> [info]
    for alloc, _rn, _rt, activity_type, _fn, _atn, _pc, project, account in rows:
        lo = max(alloc.start_date, window_start)
        hi = min(alloc.end_date or as_of_end, as_of_end, window_end)
        if hi < lo:
            continue
        info, subtree = usage_anchor(alloc.allocation_id, project, account, activity_type, lo, hi)
        paths['subtree' if subtree else 'account'].setdefault(activity_type, []).append(info)

    out: Dict[int, Dict[int, float]] = {}

    def _add(sums):
        for key, ym, amount in sums:
            cells = out.setdefault(key, {})
            cells[ym] = cells.get(ym, 0.0) + amount

    for path, by_activity in paths.items():
        subtree = path == 'subtree'
        for activity_type, infos in by_activity.items():
            for model in get_charge_models_for_activity(activity_type).values():
                _add(anchor_sums(session, infos, subtree=subtree, table=model.__tablename__,
                                 value='charges', date_col='activity_date', by_month=True))
        if include_adjustments:
            infos = [i for group in by_activity.values() for i in group]
            _add(anchor_sums(session, infos, subtree=subtree, table='charge_adjustment',
                             value='amount', date_col='adjustment_date', by_month=True))
    return out


def _group_allocations_by_summary_key(
    allocations: List[tuple],
    resource_name,
    facility_name,
    allocation_type,
    projcode,
) -> Dict[tuple, List[tuple]]:
    """
    Group a flat list of allocation tuples by the same dimensions used in get_allocation_summary().

    Key components follow the same "group by" logic: a dimension is included in the key
    when it is NOT "TOTAL".  The inner-loop charge methods only need (Allocation, res_name,
    res_type, Project, Account), so each bucket stores those 5-tuples.
    """
    grouped: Dict[tuple, List[tuple]] = {}
    for row in allocations:
        alloc, res_name, res_type, _activity, fac_name, at_name, proj_code, project, account = row

        key_parts = []
        if resource_name != "TOTAL":
            key_parts.append(res_name)
        if facility_name != "TOTAL":
            key_parts.append(fac_name)
        if allocation_type != "TOTAL":
            key_parts.append(at_name)
        if projcode != "TOTAL":
            key_parts.append(proj_code)

        key = tuple(key_parts)
        # Store only the 5-tuple the charge loop needs
        grouped.setdefault(key, []).append((alloc, res_name, res_type, project, account))

    return grouped


def _pool_root(alloc: Allocation) -> Optional[Project]:
    """The project owning a shared allocation's pool, or None when it cannot anchor a subtree."""
    account = alloc.root.account
    project = account.project if account else None
    return project if project is not None and project.has_tree_coordinates() else None


def _summary_item_key(item: Dict, resource_name, facility_name, allocation_type, projcode) -> tuple:
    """Build the grouping key for a summary item, matching _group_allocations_by_summary_key."""
    key_parts = []
    if resource_name != "TOTAL":
        key_parts.append(item.get('resource'))
    if facility_name != "TOTAL":
        key_parts.append(item.get('facility'))
    if allocation_type != "TOTAL":
        key_parts.append(item.get('allocation_type'))
    if projcode != "TOTAL":
        key_parts.append(item.get('projcode'))
    return tuple(key_parts)


def _aggregate_usage_to_total(per_project_usage: List[Dict]) -> List[Dict]:
    """
    Derive projcode="TOTAL" rows from a projcode=None usage list.

    Groups per-project usage rows by (resource, facility, allocation_type) and
    sums all numeric fields. Eliminates the need for a separate
    cached_allocation_usage(projcode="TOTAL") database round-trip.

    Args:
        per_project_usage: Result from get_allocation_summary_with_usage(projcode=None)

    Returns:
        List of dicts with same keys as projcode="TOTAL" result, minus 'projcode'.
    """
    groups: Dict[tuple, Dict] = {}

    for row in per_project_usage:
        key = (row.get('resource'), row.get('facility'), row.get('allocation_type'))

        if key not in groups:
            groups[key] = {
                'resource': row.get('resource'),
                'facility': row.get('facility'),
                'allocation_type': row.get('allocation_type'),
                'count': 0,
                'total_amount': 0.0,
                'avg_amount': None,        # not meaningful across projects
                'start_date': None,        # not meaningful across projects
                'end_date': None,          # not meaningful across projects
                'duration_days': None,
                'annualized_rate': 0.0,
                'is_open_ended': False,
                'total_used': 0.0,
                'total_allocated': 0.0,
                'percent_used': 0.0,
                'charges_by_type': {},
            }

        g = groups[key]
        g['count'] += row.get('count', 0)
        g['total_amount'] += row.get('total_amount', 0.0)
        g['annualized_rate'] += row.get('annualized_rate') or 0.0
        g['is_open_ended'] = g['is_open_ended'] or row.get('is_open_ended', False)
        g['total_used'] += row.get('total_used', 0.0)
        g['total_allocated'] += row.get('total_allocated', 0.0)
        for charge_key, amount in row.get('charges_by_type', {}).items():
            g['charges_by_type'][charge_key] = g['charges_by_type'].get(charge_key, 0.0) + amount

    result = list(groups.values())
    for item in result:
        total_alloc = item['total_allocated']
        item['percent_used'] = (item['total_used'] / total_alloc * 100) if total_alloc > 0 else 0.0

    return result


def _read_model_rows(session, all_allocations, active_at, include_adjustments,
                     resource_name):
    """Read-model rows covering every allocation, or None for the live path.

    Live when the gate refuses, when ``include_adjustments`` is off (a row's
    pool figure bakes the root's adjustments in), when any allocation lacks
    a row, or when resources are merged into a TOTAL row that holds an
    inheriting DISK allocation (only there is its pool figure, which the row
    stores as the snapshot override, observable). Lazy import:
    ``allocation_state`` imports the dashboard builder this module's callers
    share.
    """
    if not include_adjustments or not all_allocations:
        return None
    from sam.queries.allocation_state import fresh_state
    # `_fetch_all_allocations` rows: (alloc, res_name, res_type, activity_type,
    # facility, alloc_type, projcode, project, account).
    resource_ids = {t[8].resource_id for t in all_allocations}
    project_ids = {t[7].project_id for t in all_allocations}
    rows = fresh_state(session, resource_ids=resource_ids, project_ids=project_ids,
                       as_of=active_at).rows
    if rows is None:
        return None
    for t in all_allocations:
        alloc, res_type = t[0], t[2]
        if alloc.allocation_id not in rows:
            return None
        if resource_name == "TOTAL" and res_type == 'DISK' and alloc.is_inheriting:
            return None
    return rows


def get_allocation_summary_with_usage(
    session: Session,
    resource_name: Optional[Union[str, List[str]]] = None,
    facility_name: Optional[Union[str, List[str]]] = None,
    allocation_type: Optional[Union[str, List[str]]] = None,
    projcode: Optional[Union[str, List[str]]] = None,
    active_only: bool = True,
    active_at: Optional[datetime] = None,
    include_adjustments: bool = True,
    _summary: Optional[List[Dict]] = None,
    root_only: bool = False,
) -> List[Dict]:
    """
    Get allocation summary statistics with usage information.

    This extends get_allocation_summary() by calculating actual usage for each group.
    For aggregated results (count > 1), sums charges across all allocations in the group.

    Args:
        Same as get_allocation_summary(), plus:
        include_adjustments: If True (default), include manual charge adjustments in
            total_used. When False, only raw charge summaries are included and
            charges_by_type will never contain an 'adjustments' key.
        _summary: Optional pre-computed result of get_allocation_summary() with the same
            parameters. When provided, the internal get_allocation_summary() call is
            skipped. Callers that already have the summary can pass it here to avoid a
            redundant database round-trip.

    Returns:
        List of dicts with same keys as get_allocation_summary() plus:
        - total_used: Total charges across all allocations in this group
        - total_allocated: Total allocation amounts in this group
        - percent_used: (total_used / total_allocated) * 100
        - charges_by_type: Breakdown of charges by type (comp, dav, disk, archive,
          and optionally adjustments when include_adjustments=True)
        - a shared-pool row (every allocation inheriting from one pool root) instead
          reports the pool's use as total_used, its own as self_used/self_percent_used,
          and the pool's project as root_projcode; is_inheriting is True only there

    Example:
        >>> results = get_allocation_summary_with_usage(session, resource_name="Derecho")
        >>> for r in results:
        ...     print(f"{r['facility']}: {r['percent_used']:.1f}% used")
    """
    # Use pre-computed summary when available (avoids a redundant DB round-trip)
    summary = _summary if _summary is not None else get_allocation_summary(
        session, resource_name, facility_name, allocation_type, projcode,
        active_only, active_at, root_only=root_only
    )

    if not summary:
        return summary

    check_date = active_at if active_at is not None else datetime.now()
    # Usage is as of check_date: charges after that day never count, as in get_allocation_usage_rows.
    as_of_end = end_of_day(check_date)

    # Fetch ALL matching allocations in a single query, then group in Python.
    # This replaces the previous per-summary-row query loop (N+1 problem).
    all_allocations = _fetch_all_allocations(
        session, resource_name, facility_name, allocation_type, projcode,
        active_only, check_date, root_only=root_only
    )
    # account_id -> activity_type: the resource's single authoritative charge
    # table (see accounting/calculator.py). Cheap map off the already-fetched
    # scalar, so charge routing needs no extra query per allocation.
    account_activity = {
        _acct.account_id: _act
        for (_a, _rn, _rt, _act, _fn, _atn, _pc, _proj, _acct) in all_allocations
    }
    alloc_by_key = _group_allocations_by_summary_key(
        all_allocations, resource_name, facility_name, allocation_type, projcode
    )

    # One `usage_anchor` per allocation: the kernel sums them in one query per charge model.
    anchors = []
    for alloc_list in alloc_by_key.values():
        for alloc, res_name, res_type, project, account in alloc_list:
            end_date = min(alloc.end_date, as_of_end) if alloc.end_date else as_of_end
            anchors.append(usage_anchor(alloc.allocation_id, project, account,
                                        account_activity.get(alloc.account_id),
                                        alloc.start_date, end_date, res_type))

    # Read-model short-circuit (docs/plans/implemented/READ_MODEL.md): rows the hourly task
    # wrote stand in for the batched rollups, the root anchors and the disk
    # snapshot walk when the gate says the scope is fresh.
    state = _read_model_rows(session, all_allocations, active_at, include_adjustments,
                             resource_name)

    # Tree-aware augmentation: for inheriting allocations, the per-allocation
    # subtree charge does NOT match what users see in the project card —
    # there `used` reflects the ROOT allocation's full subtree (the actual
    # shared pool consumption). Collect anchors for the root projects so
    # we can batch a parallel set of subtree queries keyed by ('root',
    # allocation_id). Cheap when the row set is fully non-inheriting (no
    # extra entries appended).
    root_projcode_by_alloc_id: Dict[int, str] = {}
    for alloc_list in alloc_by_key.values():
        if state is not None:
            break
        for alloc, res_name, res_type, project, account in alloc_list:
            if not alloc.is_inheriting:
                continue
            root_project = _pool_root(alloc)
            if root_project is None:
                continue
            root_projcode_by_alloc_id[alloc.allocation_id] = root_project.projcode
            root_info, _ = usage_anchor(
                ('root', alloc.allocation_id), root_project, account,
                account_activity.get(alloc.account_id), alloc.start_date,
                min(alloc.end_date, as_of_end) if alloc.end_date else as_of_end, res_type)
            anchors.append((root_info, True))

    # Batch compute all charges in O(charge_models × date_groups) SQL queries
    all_charges: Dict[Any, Dict] = {}
    if state is not None:
        for alloc, res_name, res_type, project, account in (
                t for group in alloc_by_key.values() for t in group):
            row = state[alloc.allocation_id]
            all_charges[alloc.allocation_id] = {'charges_by_type': dict(row.charges_by_type),
                                                'adjustment': row.adjustments}
            if alloc.is_inheriting and row.root_projcode is not None:
                all_charges[('root', alloc.allocation_id)] = {
                    'charges_by_type': {'pool': row.used}, 'adjustment': 0.0}
                root_projcode_by_alloc_id[alloc.allocation_id] = row.root_projcode
    else:
        all_charges.update(anchored_charges(session, anchors, include_adjustments=include_adjustments))

    # Bulk-resolve disk capacity for every all-disk row at once. The
    # `get_subtree_disk_capacity` walk is expensive (per-account snapshot
    # lookups) and the allocation summary may contain hundreds of disk
    # rows — fanning out per-row drove route query counts above 4000.
    # Collect the (project, resource_name) pairs we'll need and resolve
    # them in a single fixed-size set of queries.
    disk_capacity_pairs: List[Tuple['Project', str]] = []
    pool_root_by_pair: Dict[Tuple[int, str], int] = {}
    for alloc_list in alloc_by_key.values():
        if not alloc_list:
            continue
        if not all(rt == 'DISK' for _, _, rt, _, _ in alloc_list):
            continue
        unique_projects = {p.project_id: p for _, _, _, p, _ in alloc_list}
        unique_resources = {res for _, res, _, _, _ in alloc_list}
        if len(unique_projects) == 1 and len(unique_resources) == 1:
            only_project = next(iter(unique_projects.values()))
            only_resource = next(iter(unique_resources))
            disk_capacity_pairs.append((only_project, only_resource))
            # A shared row's pool is its root's subtree capacity (the dashboard builder's rule).
            roots = {_pool_root(alloc) for alloc, *_ in alloc_list if alloc.is_inheriting}
            if len(roots) == 1 and None not in roots:
                root = roots.pop()
                disk_capacity_pairs.append((root, only_resource))
                pool_root_by_pair[(only_project.project_id, only_resource)] = root.project_id
    from sam.queries.disk_usage import _EMPTY_CAP
    pool_caps: Dict[Tuple[int, str], Dict[str, Any]] = {}
    if state is not None:
        disk_caps = {}
        for alloc, res_name, res_type, project, account in (
                t for group in alloc_by_key.values() for t in group):
            row = state[alloc.allocation_id]
            if res_type == 'DISK' and row.activity_date is not None:
                own = row.self_used if row.is_inheriting else row.used
                disk_caps[(project.project_id, res_name)] = {
                    'used_tib': own, 'activity_date': row.activity_date}
                if row.is_inheriting:
                    pool_caps[(project.project_id, res_name)] = {
                        'used_tib': row.used, 'activity_date': row.activity_date}
    elif disk_capacity_pairs:
        from sam.queries.disk_usage import bulk_get_subtree_disk_capacity
        disk_caps = bulk_get_subtree_disk_capacity(session, disk_capacity_pairs)
        for (pid, res), root_id in pool_root_by_pair.items():
            pool_caps[(pid, res)] = disk_caps.get((root_id, res), _EMPTY_CAP)
    else:
        disk_caps = {}

    # Enrich each summary item with pre-computed usage data
    for item in summary:
        key = _summary_item_key(item, resource_name, facility_name, allocation_type, projcode)
        item_allocations = alloc_by_key.get(key, [])

        self_used = 0.0
        tree_used = 0.0
        charges_by_type_total: Dict[str, float] = {}

        # Track inheriting state across the group: only surface tree-aware
        # fields if EVERY allocation in this row is inheriting AND all
        # share the same root project (e.g. one row per child project on
        # one shared resource — the common case in the projects-tab).
        all_inheriting = bool(item_allocations)
        root_projcodes: set = set()

        for alloc, res_name, res_type, project, account in item_allocations:
            charge_result = all_charges.get(alloc.allocation_id, {'charges_by_type': {}, 'adjustment': 0.0})

            for charge_type, amount in charge_result['charges_by_type'].items():
                charges_by_type_total[charge_type] = charges_by_type_total.get(charge_type, 0.0) + amount
                self_used += amount

            if include_adjustments:
                adjustment_amount = charge_result['adjustment']
                self_used += adjustment_amount
                if adjustment_amount != 0:
                    charges_by_type_total['adjustments'] = (
                        charges_by_type_total.get('adjustments', 0.0) + adjustment_amount
                    )

            if alloc.is_inheriting:
                root_data = all_charges.get(('root', alloc.allocation_id),
                                            {'charges_by_type': {}, 'adjustment': 0.0})
                tree_used += sum(root_data['charges_by_type'].values())
                if include_adjustments:
                    tree_used += root_data['adjustment']
                root_code = root_projcode_by_alloc_id.get(alloc.allocation_id)
                if root_code is not None:
                    root_projcodes.add(root_code)
            else:
                all_inheriting = False
                # Non-inheriting allocation contributes equally to self/tree.
                tree_used += sum(charge_result['charges_by_type'].values())
                if include_adjustments:
                    tree_used += charge_result['adjustment']

        # If this row represents a single shared pool (all inheriting,
        # one common root), use the tree total as `total_used` so the
        # bar reflects pool consumption, and expose `self_used`/
        # `self_percent_used` for the two-tone overlay. Otherwise fall
        # back to the legacy per-allocation sum.
        item['total_allocated'] = item['total_amount']  # Alias for clarity
        if all_inheriting and len(root_projcodes) == 1:
            item['total_used'] = tree_used
            item['percent_used'] = (tree_used / item['total_allocated'] * 100) if item['total_allocated'] > 0 else 0
            item['self_used'] = self_used
            item['self_percent_used'] = (self_used / item['total_allocated'] * 100) if item['total_allocated'] > 0 else 0
            item['is_inheriting'] = True
            item['root_projcode'] = next(iter(root_projcodes))
        else:
            item['total_used'] = self_used
            item['percent_used'] = (self_used / item['total_allocated'] * 100) if item['total_allocated'] > 0 else 0
            if item.get('is_inheriting'):
                item['is_inheriting'] = False   # no single resolvable pool root, so no pool fields
        item['charges_by_type'] = charges_by_type_total

        # Disk capacity override: when every allocation in this row is on
        # a disk resource, replace the cumulative TiB-year total_used
        # with the point-in-time TiB occupancy of the project subtree
        # (so parents like NMMM0003, whose children hold the bytes,
        # don't read 0%). For single-project rows (the common case
        # under projcode-grouping), `get_subtree_disk_capacity` walks
        # `project.get_descendants()` on the row's resource and sums
        # snapshot bytes. Multi-project rows fall back to summing
        # per-account snapshots (legacy behavior) — capacity-aware
        # de-duplication across overlapping subtrees in TOTAL-style
        # aggregates is a known limitation.
        all_disk = bool(item_allocations) and all(
            rt == 'DISK' for _, _, rt, _, _ in item_allocations
        )
        if all_disk:
            unique_projects = {p.project_id: p for _, _, _, p, _ in item_allocations}
            unique_resources = {res for _, res, _, _, _ in item_allocations}
            if len(unique_projects) == 1 and len(unique_resources) == 1:
                only_project = next(iter(unique_projects.values()))
                only_resource = next(iter(unique_resources))
                cap = disk_caps.get(
                    (only_project.project_id, only_resource), _EMPTY_CAP,
                )
                own_used = cap['used_tib']
                pool = pool_caps.get((only_project.project_id, only_resource)) if item.get('is_inheriting') else None
                capacity_used = (pool or cap)['used_tib']
                activity_date = (pool or cap)['activity_date']
            else:
                own_used = None
                capacity_used = 0.0
                activity_date = None
                for _, _, _, _, account in item_allocations:
                    snap = account.current_disk_usage()
                    if snap is None:
                        continue
                    capacity_used += snap.used_tib
                    if activity_date is None or snap.activity_date > activity_date:
                        activity_date = snap.activity_date
            item['total_used'] = capacity_used
            item['percent_used'] = (
                (capacity_used / item['total_allocated'] * 100)
                if item['total_allocated'] > 0 else 0
            )
            if 'self_used' in item:
                item['self_used'] = capacity_used if own_used is None else own_used
                item['self_percent_used'] = (
                    (item['self_used'] / item['total_allocated'] * 100)
                    if item['total_allocated'] > 0 else 0
                )
            item['activity_date'] = activity_date

    return summary
