"""
Queue query functions for SAM.

Provides get_queue_data() which reproduces the output of the legacy Java
``GET /api/protected/admin/ssg/queue`` (and ``/queue/{resource}``) endpoints.

The data is organized as a two-level tree:
  queues → resource → queue

and is consumed by the PBS batch scheduler / systems-integration tooling to
configure per-queue wallclock limits and class-of-service ids.

Legacy semantics (QueueServiceImpl.getQueues / DefaultQueueQuery.findAllActive)
------------------------------------------------------------------------------
The legacy query returns queues whose ``end_date`` is in the future (or NULL)
AND whose parent resource is active on the current date. It does NOT check the
queue's own ``start_date``.

Here we filter with the idiomatic ``Queue.is_active`` hybrid (which additionally
requires ``start_date <= now``) intersected with ``Resource.is_active``. The
extra start_date bound is a deliberate, negligible tightening — future-dated
queues are vanishingly rare and the parity harness absorbs any such row with a
count tolerance. See CLAUDE.md §5 (universal ``is_active`` interface).

Response format::

    {
        "name": "queues",
        "resources": [
            {
                "resourceName": "Derecho",
                "queues": [
                    {
                        "queueName": "main",
                        "wallClockHoursLimit": 12.0,
                        "startDate": "2023-01-01T00:00:00",
                        "endDate": null,
                        "cosId": 5
                    }
                ]
            }
        ]
    }
"""

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session, joinedload

from sam import Queue, Resource


def _iso(value) -> Optional[str]:
    """Render a datetime as an ISO-8601 string, passing through None."""
    return value.isoformat() if value is not None else None


def get_queue_data(
    session: Session,
    resource_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build the active-queue tree grouped by resource.

    Reproduces the legacy ``GET /api/protected/admin/ssg/queue`` output.

    Args:
        session:       SQLAlchemy session.
        resource_name: Optional resource name filter (e.g. ``"Derecho"``).
                       When given, only queues on that resource are returned.

    Returns:
        Dict with ``name`` (``"queues"``) and ``resources`` keys. ``resources``
        is a list of ``{resourceName, queues}`` entries sorted by resource name;
        each queue carries ``queueName``, ``wallClockHoursLimit``, ``startDate``,
        ``endDate`` (ISO strings or null), and ``cosId``. Queues are sorted by
        name within each resource.
    """
    query = (
        session.query(Queue)
        .join(Resource, Queue.resource_id == Resource.resource_id)
        .options(joinedload(Queue.resource))
        .filter(Queue.is_active)
        .filter(Resource.is_active)
    )
    if resource_name is not None:
        query = query.filter(Resource.resource_name == resource_name)

    by_resource: Dict[str, List[Dict[str, Any]]] = {}
    for queue in query.all():
        rname = queue.resource.resource_name
        by_resource.setdefault(rname, []).append({
            'queueName':           queue.queue_name,
            'wallClockHoursLimit': queue.wall_clock_hours_limit,
            'startDate':           _iso(queue.start_date),
            'endDate':             _iso(queue.end_date),
            'cosId':               queue.cos_id,
        })

    resources = [
        {
            'resourceName': rname,
            'queues': sorted(queues, key=lambda q: q['queueName']),
        }
        for rname, queues in sorted(by_resource.items())
    ]

    return {
        'name': 'queues',
        'resources': resources,
    }
