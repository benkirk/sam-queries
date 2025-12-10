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
    get_allocations_by_type: Get allocations of a specific type
    get_allocations_by_resource: Get allocations for a specific resource
    get_allocation_summary_by_facility: Get summary statistics by facility
    get_allocation_summary: Get flexible allocation summaries with grouping and filtering
"""

from datetime import datetime
from typing import List, Optional, Dict, Tuple, Union

from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from sam.core.users import User
from sam.projects.projects import Project
from sam.accounting.allocations import Allocation, AllocationTransaction, AllocationType
from sam.resources.resources import Resource, ResourceType
from sam.resources.facilities import Facility, Panel
from sam.accounting.accounts import Account
from sam.accounting.adjustments import ChargeAdjustment
from sam.summaries.comp_summaries import CompChargeSummary
from sam.summaries.dav_summaries import DavChargeSummary
from sam.summaries.disk_summaries import DiskChargeSummary
from sam.summaries.archive_summaries import ArchiveChargeSummary


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
    active_at: Optional[datetime] = None
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

    Returns:
        List of dicts with keys depending on grouping:
        - Grouped fields (resource, facility, allocation_type, projcode) included when not "TOTAL"
        - count: Number of allocations
        - total_amount: Sum of allocation amounts
        - avg_amount: Average allocation amount
        - start_date: Earliest start date (datetime or None)
        - end_date: Latest end date (datetime or None)

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
        # Group by project if: None (all), single value, or list (multiple)
        if projcode is None or isinstance(projcode, (str, list)):
            group_by_fields.append(Project.projcode)

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
        .filter(Allocation.deleted == False)

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

        # Add aggregations
        item['count'] = row[idx]
        item['total_amount'] = float(row[idx + 1]) if row[idx + 1] else 0.0
        item['avg_amount'] = float(row[idx + 2]) if row[idx + 2] else 0.0
        item['start_date'] = row[idx + 3]  # datetime object or None
        item['end_date'] = row[idx + 4]    # datetime object or None

        output.append(item)

    return output


def get_allocation_summary_with_usage(
    session: Session,
    resource_name: Optional[Union[str, List[str]]] = None,
    facility_name: Optional[Union[str, List[str]]] = None,
    allocation_type: Optional[Union[str, List[str]]] = None,
    projcode: Optional[Union[str, List[str]]] = None,
    active_only: bool = True,
    active_at: Optional[datetime] = None
) -> List[Dict]:
    """
    Get allocation summary statistics with usage information.

    This extends get_allocation_summary() by calculating actual usage for each group.
    For aggregated results (count > 1), sums charges across all allocations in the group.

    Args:
        Same as get_allocation_summary()

    Returns:
        List of dicts with same keys as get_allocation_summary() plus:
        - total_used: Total charges across all allocations in this group
        - total_allocated: Total allocation amounts in this group
        - percent_used: (total_used / total_allocated) * 100
        - charges_by_type: Breakdown of charges by type (comp, dav, disk, archive)

    Example:
        >>> results = get_allocation_summary_with_usage(session, resource_name="Derecho")
        >>> for r in results:
        ...     print(f"{r['facility']}: {r['percent_used']:.1f}% used")
    """
    # First get the basic summary
    summary = get_allocation_summary(
        session, resource_name, facility_name, allocation_type, projcode,
        active_only, active_at
    )

    check_date = active_at if active_at is not None else datetime.now()

    # Now enrich with usage data
    for item in summary:
        # Build query to find matching allocations
        query = session.query(
            Allocation,
            Resource.resource_name,
            ResourceType.resource_type,
            Project,
            Account
        ).join(Account, Allocation.account_id == Account.account_id)\
         .join(Project, Account.project_id == Project.project_id)\
         .join(Resource, Account.resource_id == Resource.resource_id)\
         .join(ResourceType, Resource.resource_type_id == ResourceType.resource_type_id)\
         .join(AllocationType, Project.allocation_type_id == AllocationType.allocation_type_id)\
         .join(Panel, AllocationType.panel_id == Panel.panel_id)\
         .join(Facility, Panel.facility_id == Facility.facility_id)\
         .filter(Allocation.deleted == False)

        # Apply filters based on what's in this summary item
        if 'resource' in item:
            query = query.filter(Resource.resource_name == item['resource'])
        if 'facility' in item:
            query = query.filter(Facility.facility_name == item['facility'])
        if 'allocation_type' in item:
            query = query.filter(AllocationType.allocation_type == item['allocation_type'])
        if 'projcode' in item:
            query = query.filter(Project.projcode == item['projcode'])

        # Apply active_only filter
        if active_only:
            query = query.filter(
                Allocation.start_date <= check_date,
                or_(
                    Allocation.end_date.is_(None),
                    Allocation.end_date >= check_date
                )
            )

        allocations = query.all()

        # Calculate total usage across all allocations in this group
        total_used = 0.0
        charges_by_type_total = {}

        for alloc, res_name, res_type, project, account in allocations:
            # Use the end_date from allocation, or current date if None
            end_date = alloc.end_date if alloc.end_date else check_date
            start_date = alloc.start_date

            # Check if we should use hierarchical charging
            # Mimic logic from Project.get_detailed_allocation_usage
            is_tree_valid = bool(project.tree_root and project.tree_left and project.tree_right)
            use_hierarchy = is_tree_valid  # Always default to hierarchical if structure exists

            # Calculate charges
            if use_hierarchy:
                charges = project.get_subtree_charges(account.resource_id,
                                                      res_type,
                                                      start_date,
                                                      end_date)
            else:
                charges = project.get_charges_by_resource_type(alloc.account_id,
                                                               res_type,
                                                               start_date,
                                                               end_date)

            # Calculate adjustments
            if use_hierarchy:
                adjustment_amount = project.get_subtree_adjustments(account.resource_id,
                                                                    start_date,
                                                                    end_date)
            else:
                adjustment_amount = project.get_adjustments(alloc.account_id,
                                                            start_date,
                                                            end_date)

            # Accumulate charges
            for charge_type, amount in charges.items():
                charges_by_type_total[charge_type] = charges_by_type_total.get(charge_type, 0.0) + amount
                total_used += amount

            # Add adjustments to total_used (they affect the balance)
            total_used += adjustment_amount
            if adjustment_amount != 0:
                charges_by_type_total['adjustments'] = charges_by_type_total.get('adjustments', 0.0) + adjustment_amount

        # Add usage fields to item
        item['total_used'] = total_used
        item['total_allocated'] = item['total_amount']  # Alias for clarity
        item['percent_used'] = (total_used / item['total_allocated'] * 100) if item['total_allocated'] > 0 else 0
        item['charges_by_type'] = charges_by_type_total

    return summary
