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
from sqlalchemy.orm import Session, noload

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

    if projcode and projcode != "TOTAL":
        if isinstance(projcode, list):
            query = query.filter(Project.projcode.in_(projcode))
        else:
            query = query.filter(Project.projcode == projcode)

    if resource_name and resource_name != "TOTAL":
        if isinstance(resource_name, list):
            query = query.filter(Resource.resource_name.in_(resource_name))
        else:
            query = query.filter(Resource.resource_name == resource_name)

    if facility_name and facility_name != "TOTAL":
        if isinstance(facility_name, list):
            query = query.filter(Facility.facility_name.in_(facility_name))
        else:
            query = query.filter(Facility.facility_name == facility_name)

    if allocation_type and allocation_type != "TOTAL":
        if isinstance(allocation_type, list):
            query = query.filter(AllocationType.allocation_type.in_(allocation_type))
        else:
            query = query.filter(AllocationType.allocation_type == allocation_type)

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
        facility_name: facility name(s) to include (joined via Project →
            AllocationType → Panel → Facility).
        allocation_type: allocation type name(s) to include.
        transaction_types: one or more ``AllocationTransactionType`` values (enum
            members or raw strings such as ``"EDIT"``).
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
    Get allocation summary statistics with flexible grouping.

    Args:
        resource_name: None=group by resource, "TOTAL"=sum across, string=filter to one, list=filter to multiple
        facility_name: None=group by facility, "TOTAL"=sum across, string=filter to one, list=filter to multiple
        allocation_type: None=group by type, "TOTAL"=sum across, string=filter to one, list=filter to multiple
        projcode: None=group by project, "TOTAL"=sum across, string=filter to one, list=filter to multiple
        active_only: If True, filter to allocations active at specified date
        active_at: Date to check for active status. If None and active_only=True, uses datetime.now()
                   Useful for historical queries like "what was allocated on 2024-06-15?"
        root_only: If True, exclude inheriting (child) allocations — only count allocations
                   where parent_allocation_id IS NULL. Use for dashboard totals/summaries to
                   avoid double-counting shared-allocation trees where the root amount equals
                   the sum of all child amounts.

    Returns:
        List of dicts with keys depending on grouping:
        - Grouped fields (resource, facility, allocation_type, projcode) included when not "TOTAL"
        - count: Number of allocations
        - total_amount: Sum of allocation amounts
        - avg_amount: Average allocation amount
        - start_date: Earliest start date (datetime or None)
        - end_date: Latest end date (datetime or None)
        - duration_days: Number of days in allocation period (None if count > 1)
        - annualized_rate: Amount per year (None if count > 1)
        - is_open_ended: True if allocation has no end_date (False if count > 1)

    Examples:
        # All active Derecho allocations grouped by facility and type
        >>> get_allocation_summary(session, resource_name="Derecho")

        # Total amount allocated to Exploratory projects on Casper (sum across all projects)
        >>> get_allocation_summary(session, resource_name="Casper GPU",
        ...                        allocation_type="Exploratory", projcode="TOTAL")

        # Multiple resources
        >>> get_allocation_summary(session, resource_name=["Derecho", "Casper"],
        ...                        allocation_type=["Small", "Classroom"], projcode="TOTAL")

        # What allocations were active 6 months ago?
        >>> from datetime import datetime, timedelta
        >>> past_date = datetime.now() - timedelta(days=180)
        >>> get_allocation_summary(session, active_at=past_date)
    """
    # Determine what fields to group by and what to filter
    group_by_fields = []
    select_fields = []

    # Resource handling
    if resource_name != "TOTAL":
        select_fields.append(Resource.resource_name)
        # Group by resource if: None (all), single value, or list (multiple)
        if resource_name is None or isinstance(resource_name, (str, list)):
            group_by_fields.append(Resource.resource_name)

    # Facility handling
    if facility_name != "TOTAL":
        select_fields.append(Facility.facility_name)
        # Group by facility if: None (all), single value, or list (multiple)
        if facility_name is None or isinstance(facility_name, (str, list)):
            group_by_fields.append(Facility.facility_name)

    # Allocation type handling
    if allocation_type != "TOTAL":
        select_fields.append(AllocationType.allocation_type)
        # Group by type if: None (all), single value, or list (multiple)
        if allocation_type is None or isinstance(allocation_type, (str, list)):
            group_by_fields.append(AllocationType.allocation_type)

    # Project code handling
    if projcode != "TOTAL":
        select_fields.append(Project.projcode)
        select_fields.append(Project.parent_id)  # Expose tree position; is_root = (parent_id is None)
        # Group by project if: None (all), single value, or list (multiple)
        if projcode is None or isinstance(projcode, (str, list)):
            group_by_fields.append(Project.projcode)
            group_by_fields.append(Project.parent_id)  # Functionally dependent on projcode; required for strict-mode SQL

    # Add aggregation fields
    select_fields.extend([
        func.count(Allocation.allocation_id).label('count'),
        func.sum(Allocation.amount).label('total_amount'),
        func.avg(Allocation.amount).label('avg_amount'),
        func.min(Allocation.start_date).label('start_date'),
        func.max(Allocation.end_date).label('end_date')
    ])

    # Build the query
    query = session.query(*select_fields)\
        .join(Account, Allocation.account_id == Account.account_id)\
        .join(Project, Account.project_id == Project.project_id)\
        .join(Resource, Account.resource_id == Resource.resource_id)\
        .join(AllocationType, Project.allocation_type_id == AllocationType.allocation_type_id)\
        .join(Panel, AllocationType.panel_id == Panel.panel_id)\
        .join(Facility, Panel.facility_id == Facility.facility_id)\
        .filter(
            Allocation.deleted == False,
            Resource.is_active  # Exclude decommissioned resources
        )

    # Apply specific filters
    if resource_name and resource_name != "TOTAL":
        if isinstance(resource_name, list):
            query = query.filter(Resource.resource_name.in_(resource_name))
        else:
            query = query.filter(Resource.resource_name == resource_name)

    if facility_name and facility_name != "TOTAL":
        if isinstance(facility_name, list):
            query = query.filter(Facility.facility_name.in_(facility_name))
        else:
            query = query.filter(Facility.facility_name == facility_name)

    if allocation_type and allocation_type != "TOTAL":
        if isinstance(allocation_type, list):
            query = query.filter(AllocationType.allocation_type.in_(allocation_type))
        else:
            query = query.filter(AllocationType.allocation_type == allocation_type)

    if projcode and projcode != "TOTAL":
        if isinstance(projcode, list):
            query = query.filter(Project.projcode.in_(projcode))
        else:
            query = query.filter(Project.projcode == projcode)

    # Apply active_only filter
    if active_only:
        check_date = active_at if active_at is not None else datetime.now()
        query = query.filter(
            Allocation.start_date <= check_date,
            or_(
                Allocation.end_date.is_(None),
                Allocation.end_date >= check_date
            )
        )

    # Exclude child allocations and child projects when computing summaries/totals to
    # avoid double-counting trees where the root amount == sum of child amounts.
    # Two independent tree structures can cause double-counting:
    #   1. Allocation-level trees: parent_allocation_id links one allocation to another
    #   2. Project-level trees: Project.parent_id links child projects to a parent project
    #      (e.g., CESM0002 → CESM0002_sub1, CESM0002_sub2 each with their own allocations)
    if root_only:
        query = query.filter(
            Allocation.parent_allocation_id.is_(None),  # root-level allocation
            Project.parent_id.is_(None),                # root or standalone project
        )

    # Group by appropriate fields
    if group_by_fields:
        query = query.group_by(*group_by_fields)

    results = query.all()

    # Build return list
    output = []
    for row in results:
        item = {}
        idx = 0

        # Add grouped/filtered fields
        if resource_name != "TOTAL":
            item['resource'] = row[idx]
            idx += 1

        if facility_name != "TOTAL":
            item['facility'] = row[idx]
            idx += 1

        if allocation_type != "TOTAL":
            item['allocation_type'] = row[idx]
            idx += 1

        if projcode != "TOTAL":
            item['projcode'] = row[idx]
            idx += 1
            item['is_root'] = row[idx] is None  # parent_id is None → root or standalone project
            idx += 1

        # Add aggregations
        item['count'] = row[idx]
        item['total_amount'] = float(row[idx + 1]) if row[idx + 1] else 0.0
        item['avg_amount'] = float(row[idx + 2]) if row[idx + 2] else 0.0
        item['start_date'] = row[idx + 3]  # datetime object or None
        item['end_date'] = row[idx + 4]    # datetime object or None

        # Calculate annualized rate for single allocations only
        if item['count'] == 1:
            start = item['start_date']
            end = item['end_date']
            is_open_ended = (end is None)

            if is_open_ended:
                # For open-ended allocations, assume end date is 365 days from now.
                # No +1 here — this is an approximation, not an inclusive date range.
                assumed_end = datetime.now() + timedelta(days=365)
                duration_days = (assumed_end - start).days
            else:
                # +1: end_date is inclusive (both start and end days count)
                duration_days = (end - start).days + 1

            # Avoid division by zero; actual/365 annualization convention
            if duration_days > 0:
                annualized_rate = (item['total_amount'] / duration_days) * 365
            else:
                annualized_rate = 0.0

            item['duration_days'] = duration_days
            item['annualized_rate'] = annualized_rate
            item['is_open_ended'] = is_open_ended
        else:
            # Aggregated results - no rate calculation
            item['duration_days'] = None
            item['annualized_rate'] = None
            item['is_open_ended'] = False

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
) -> List[tuple]:
    """
    Fetch all active allocations matching the given filters in a single query.

    Returns list of (Allocation, resource_name, resource_type, facility_name,
    allocation_type_name, projcode, Project, Account) tuples.
    Account.users selectin is suppressed via lazyload — the caller never needs it.
    The facility, allocation_type, and projcode columns are fetched as explicit scalars
    to avoid triggering lazy-loads when building grouping keys.
    """
    query = session.query(
        Allocation,
        Resource.resource_name,
        ResourceType.resource_type,
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
    if resource_name and resource_name != "TOTAL":
        if isinstance(resource_name, list):
            query = query.filter(Resource.resource_name.in_(resource_name))
        else:
            query = query.filter(Resource.resource_name == resource_name)

    if facility_name and facility_name != "TOTAL":
        if isinstance(facility_name, list):
            query = query.filter(Facility.facility_name.in_(facility_name))
        else:
            query = query.filter(Facility.facility_name == facility_name)

    if allocation_type and allocation_type != "TOTAL":
        if isinstance(allocation_type, list):
            query = query.filter(AllocationType.allocation_type.in_(allocation_type))
        else:
            query = query.filter(AllocationType.allocation_type == allocation_type)

    if projcode and projcode != "TOTAL":
        if isinstance(projcode, list):
            query = query.filter(Project.projcode.in_(projcode))
        else:
            query = query.filter(Project.projcode == projcode)

    if active_only:
        query = query.filter(
            Allocation.start_date <= check_date,
            or_(
                Allocation.end_date.is_(None),
                Allocation.end_date >= check_date
            )
        )

    if root_only:
        query = query.filter(
            Allocation.parent_allocation_id.is_(None),  # root-level allocation
            Project.parent_id.is_(None),                # root or standalone project
        )

    return query.all()


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
        alloc, res_name, res_type, fac_name, at_name, proj_code, project, account = row

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
                'annualized_rate': None,
                'is_open_ended': False,
                'total_used': 0.0,
                'total_allocated': 0.0,
                'percent_used': 0.0,
                'charges_by_type': {},
            }

        g = groups[key]
        g['count'] += row.get('count', 0)
        g['total_amount'] += row.get('total_amount', 0.0)
        g['total_used'] += row.get('total_used', 0.0)
        g['total_allocated'] += row.get('total_allocated', 0.0)
        for charge_key, amount in row.get('charges_by_type', {}).items():
            g['charges_by_type'][charge_key] = g['charges_by_type'].get(charge_key, 0.0) + amount

    result = list(groups.values())
    for item in result:
        total_alloc = item['total_allocated']
        item['percent_used'] = (item['total_used'] / total_alloc * 100) if total_alloc > 0 else 0.0

    return result


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

    # Fetch ALL matching allocations in a single query, then group in Python.
    # This replaces the previous per-summary-row query loop (N+1 problem).
    all_allocations = _fetch_all_allocations(
        session, resource_name, facility_name, allocation_type, projcode,
        active_only, check_date, root_only=root_only
    )
    alloc_by_key = _group_allocations_by_summary_key(
        all_allocations, resource_name, facility_name, allocation_type, projcode
    )

    # Collect all allocation infos for batch charge computation.
    # This replaces per-allocation project.get_subtree_charges() / get_charges_by_resource_type()
    # calls (N scalar queries) with one query per charge model covering all allocations at once.
    subtree_infos: List[Dict[str, Any]] = []
    account_infos: List[Dict[str, Any]] = []

    for alloc_list in alloc_by_key.values():
        for alloc, res_name, res_type, project, account in alloc_list:
            end_date = alloc.end_date if alloc.end_date else check_date
            is_tree_valid = bool(project.tree_root and project.tree_left and project.tree_right)
            info = {
                'key': alloc.allocation_id,
                'resource_type': res_type,
                'resource_id': account.resource_id,
                'account_id': alloc.account_id,
                'tree_root': project.tree_root,
                'tree_left': project.tree_left,
                'tree_right': project.tree_right,
                'start_date': alloc.start_date,
                'end_date': end_date,
            }
            # Leaf nodes (no children) have no descendants — their subtree query is
            # identical to a direct account_id query. Route them to the faster account
            # path; only genuine non-leaf projects need the CTE subtree approach.
            if is_tree_valid and not project.is_leaf():
                subtree_infos.append(info)
            else:
                account_infos.append(info)

    # Tree-aware augmentation: for inheriting allocations, the per-allocation
    # subtree charge does NOT match what users see in the project card —
    # there `used` reflects the ROOT allocation's full subtree (the actual
    # shared pool consumption). Collect anchors for the root projects so
    # we can batch a parallel set of subtree queries keyed by ('root',
    # allocation_id). Cheap when the row set is fully non-inheriting (no
    # extra entries appended).
    root_anchor_by_alloc_id: Dict[int, 'Project'] = {}
    for alloc_list in alloc_by_key.values():
        for alloc, res_name, res_type, project, account in alloc_list:
            if not alloc.is_inheriting:
                continue
            root_alloc = alloc.root
            root_account = root_alloc.account
            root_project = root_account.project if root_account else None
            if root_project is None:
                continue
            if not (root_project.tree_root and root_project.tree_left and root_project.tree_right):
                continue
            root_anchor_by_alloc_id[alloc.allocation_id] = root_project
            subtree_infos.append({
                'key':           ('root', alloc.allocation_id),
                'resource_type': res_type,
                'resource_id':   account.resource_id,
                'account_id':    alloc.account_id,
                'tree_root':     root_project.tree_root,
                'tree_left':     root_project.tree_left,
                'tree_right':    root_project.tree_right,
                'start_date':    alloc.start_date,
                'end_date':      alloc.end_date if alloc.end_date else check_date,
            })

    # Batch compute all charges in O(charge_models × date_groups) SQL queries
    all_charges: Dict[Any, Dict] = {}
    if subtree_infos:
        all_charges.update(Project.batch_get_subtree_charges(session, subtree_infos, include_adjustments))
    if account_infos:
        all_charges.update(Project.batch_get_account_charges(session, account_infos, include_adjustments))

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
                root_proj = root_anchor_by_alloc_id.get(alloc.allocation_id)
                if root_proj is not None:
                    root_projcodes.add(root_proj.projcode)
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
        item['charges_by_type'] = charges_by_type_total

        # Disk capacity override: when every allocation in this row is on
        # a disk resource, replace the cumulative TiB-year total_used
        # with the point-in-time TiB occupancy sum from
        # Account.current_disk_usage(). The progress bar in the UI then
        # reads "TiB used / TiB allocated", which is what users mean by
        # "% used" for storage. Mixed-resource rows (rare) keep the
        # cumulative number.
        all_disk = bool(item_allocations) and all(
            rt == 'DISK' for _, _, rt, _, _ in item_allocations
        )
        if all_disk:
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
                item['self_used'] = capacity_used
                item['self_percent_used'] = item['percent_used']
            item['activity_date'] = activity_date

    return summary
