"""
Resource management functions.

Administrative operations for updating Resources, ResourceTypes, Machines,
and Queues. These are write operations that modify the database, as opposed
to read-only query functions in sam.queries.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from sam.resources.resources import Resource, ResourceType
from sam.resources.machines import Machine, Queue


def update_resource(
    session: Session,
    resource_id: int,
    *,
    description: Optional[str] = None,
    commission_date: Optional[datetime] = None,
    decommission_date: Optional[datetime] = None,
    charging_exempt: Optional[bool] = None,
) -> Resource:
    """
    Update an existing Resource record.

    NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

    Args:
        session: SQLAlchemy session
        resource_id: ID of the resource to update
        description: New description (pass empty string to clear)
        commission_date: New commission date (required if resource lacks one)
        decommission_date: New decommission date; None leaves unchanged, pass
                           a sentinel if you want to clear it
        charging_exempt: Whether the resource is exempt from charging

    Returns:
        The updated Resource object

    Raises:
        ValueError: If resource not found or validation fails
    """
    resource = session.get(Resource, resource_id)
    if not resource:
        raise ValueError(f"Resource {resource_id} not found")

    if commission_date is not None:
        resource.commission_date = commission_date

    if decommission_date is not None:
        if resource.commission_date and decommission_date <= resource.commission_date:
            raise ValueError("decommission_date must be after commission_date")
        resource.decommission_date = decommission_date

    if description is not None:
        resource.description = description if description.strip() else None

    if charging_exempt is not None:
        resource.charging_exempt = charging_exempt

    session.flush()
    return resource


def update_resource_type(
    session: Session,
    resource_type_id: int,
    *,
    grace_period_days: Optional[int] = None,
) -> ResourceType:
    """
    Update an existing ResourceType record.

    NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

    Args:
        session: SQLAlchemy session
        resource_type_id: ID of the resource type to update
        grace_period_days: Number of grace-period days (>= 0)

    Returns:
        The updated ResourceType object

    Raises:
        ValueError: If resource type not found or validation fails
    """
    resource_type = session.get(ResourceType, resource_type_id)
    if not resource_type:
        raise ValueError(f"ResourceType {resource_type_id} not found")

    if grace_period_days is not None:
        if grace_period_days < 0:
            raise ValueError("grace_period_days must be >= 0")
        resource_type.grace_period_days = grace_period_days

    session.flush()
    return resource_type


def update_machine(
    session: Session,
    machine_id: int,
    *,
    description: Optional[str] = None,
    cpus_per_node: Optional[int] = None,
    commission_date: Optional[datetime] = None,
    decommission_date: Optional[datetime] = None,
) -> Machine:
    """
    Update an existing Machine record.

    NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

    Args:
        session: SQLAlchemy session
        machine_id: ID of the machine to update
        description: New description (pass empty string to clear)
        cpus_per_node: Number of CPUs per node (must be positive)
        commission_date: New commission date
        decommission_date: New decommission date

    Returns:
        The updated Machine object

    Raises:
        ValueError: If machine not found or validation fails
    """
    machine = session.get(Machine, machine_id)
    if not machine:
        raise ValueError(f"Machine {machine_id} not found")

    if description is not None:
        machine.description = description if description.strip() else None

    if cpus_per_node is not None:
        if cpus_per_node <= 0:
            raise ValueError("cpus_per_node must be a positive integer")
        machine.cpus_per_node = cpus_per_node

    if commission_date is not None:
        machine.commission_date = commission_date

    if decommission_date is not None:
        effective_commission = commission_date or machine.commission_date
        if effective_commission and decommission_date <= effective_commission:
            raise ValueError("decommission_date must be after commission_date")
        machine.decommission_date = decommission_date

    session.flush()
    return machine


def update_queue(
    session: Session,
    queue_id: int,
    *,
    description: Optional[str] = None,
    wall_clock_hours_limit: Optional[float] = None,
    end_date: Optional[datetime] = None,
) -> Queue:
    """
    Update an existing Queue record.

    NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

    Args:
        session: SQLAlchemy session
        queue_id: ID of the queue to update
        description: New description (pass empty string to clear)
        wall_clock_hours_limit: New default wallclock limit in hours (must be positive)
        end_date: New end date; must be after start_date if provided

    Returns:
        The updated Queue object

    Raises:
        ValueError: If queue not found or validation fails
    """
    queue = session.get(Queue, queue_id)
    if not queue:
        raise ValueError(f"Queue {queue_id} not found")

    if description is not None:
        # description is NOT NULL in the schema — store empty string rather than None
        queue.description = description.strip()

    if wall_clock_hours_limit is not None:
        if wall_clock_hours_limit <= 0:
            raise ValueError("wall_clock_hours_limit must be positive")
        queue.wall_clock_hours_limit = wall_clock_hours_limit

    if end_date is not None:
        if queue.start_date and end_date <= queue.start_date:
            raise ValueError("end_date must be after start_date")
        queue.end_date = end_date

    session.flush()
    return queue
