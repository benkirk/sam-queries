"""Factories for resource-domain entities: ResourceType, Resource, Machine, Queue."""
from datetime import datetime
from typing import Optional

from sam.resources.machines import Machine, Queue
from sam.resources.resources import Resource, ResourceType

from ._seq import next_seq


def make_resource_type(
    session,
    *,
    resource_type: Optional[str] = None,
) -> ResourceType:
    """Build and flush a fresh ResourceType row."""
    if resource_type is None:
        resource_type = next_seq("RT")
    return ResourceType.create(session, resource_type=resource_type)


def make_resource(
    session,
    *,
    resource_type: Optional[ResourceType] = None,
    resource_name: Optional[str] = None,
    commission_date: Optional[datetime] = None,
) -> Resource:
    """Build and flush a fresh Resource row, auto-building a ResourceType if needed."""
    if resource_type is None:
        resource_type = make_resource_type(session)
    if resource_name is None:
        resource_name = next_seq("RES")

    return Resource.create(
        session,
        resource_name=resource_name,
        resource_type_id=resource_type.resource_type_id,
        commission_date=commission_date,
    )


def make_machine(
    session,
    *,
    resource: Optional[Resource] = None,
    name: Optional[str] = None,
    cpus_per_node: Optional[int] = None,
) -> Machine:
    """Build and flush a fresh Machine row, auto-building a Resource if needed."""
    if resource is None:
        resource = make_resource(session)
    if name is None:
        name = next_seq("mach")

    return Machine.create(
        session,
        name=name,
        resource_id=resource.resource_id,
        cpus_per_node=cpus_per_node,
    )


def make_queue(
    session,
    *,
    resource: Optional[Resource] = None,
    queue_name: Optional[str] = None,
    description: Optional[str] = None,
    wall_clock_hours_limit: Optional[float] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> Queue:
    """Build and flush a fresh Queue row, auto-building a Resource if needed.

    `Queue.description` is NOT NULL — the factory supplies a default. There
    is no `Queue.create()` classmethod (only `update()`), so we instantiate
    the model directly.
    """
    if resource is None:
        resource = make_resource(session)
    if queue_name is None:
        queue_name = next_seq("q")
    if description is None:
        description = "test queue"

    queue = Queue(
        resource_id=resource.resource_id,
        queue_name=queue_name,
        description=description,
        wall_clock_hours_limit=wall_clock_hours_limit,
        start_date=start_date,
        end_date=end_date,
    )
    session.add(queue)
    session.flush()
    return queue
