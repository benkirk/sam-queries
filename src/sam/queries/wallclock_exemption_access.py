"""
Wallclock-exemption query functions for SAM.

Provides get_wallclock_exemption_data() which reproduces the output of the
legacy Java ``GET /api/protected/admin/ssg/wallClockExemption`` endpoint.

The data is organized as a three-level tree:
  exemptions -> resource -> queue -> user limit

and is consumed by the PBS batch scheduler to grant individual users a raised
wallclock limit on a specific queue for a bounded time window.

Legacy semantics (WallClockExemptionServiceImpl + activeWallclockExemptions SQL)
--------------------------------------------------------------------------------
The legacy named query joins ``resources -> queue -> wallclock_exemption -> users``
and keeps only exemptions whose date window contains today
(``DATE(start_date) <= CURDATE() AND DATE(end_date) >= CURDATE()``). It applies
NO active filter on the resource, queue, or user — only the exemption's own
window. We reproduce that exactly using the idiomatic ``WallclockExemption.is_active``
hybrid (``start_date <= now <= end_date``). See CLAUDE.md §5.

Response format::

    {
        "name": "exemptions",
        "resources": [
            {
                "resourceName": "Derecho",
                "queues": [
                    {
                        "queueName": "main",
                        "limits": [
                            {"username": "benkirk", "wallClockLimit": 48.0}
                        ]
                    }
                ]
            }
        ]
    }
"""

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session, joinedload

from sam import Queue, Resource, User, WallclockExemption


def get_wallclock_exemption_data(
    session: Session,
    resource_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build the active-exemption tree grouped by resource -> queue -> user.

    Reproduces the legacy ``GET /api/protected/admin/ssg/wallClockExemption``
    output.

    Args:
        session:       SQLAlchemy session.
        resource_name: Optional resource name filter (e.g. ``"Derecho"``).
                       When given, only exemptions on queues of that resource
                       are returned. (The legacy endpoint has no per-resource
                       variant; this arg powers the symmetric new route.)

    Returns:
        Dict with ``name`` (``"exemptions"``) and ``resources`` keys.
        ``resources`` is a list of ``{resourceName, queues}`` entries sorted by
        resource name; each queue is ``{queueName, limits}`` with ``limits`` a
        list of ``{username, wallClockLimit}`` sorted by username.
    """
    query = (
        session.query(WallclockExemption)
        .join(Queue, WallclockExemption.queue_id == Queue.queue_id)
        .join(Resource, Queue.resource_id == Resource.resource_id)
        .join(User, WallclockExemption.user_id == User.user_id)
        .options(
            joinedload(WallclockExemption.queue).joinedload(Queue.resource),
            joinedload(WallclockExemption.user),
        )
        .filter(WallclockExemption.is_active)
    )
    if resource_name is not None:
        query = query.filter(Resource.resource_name == resource_name)

    # resource_name -> queue_name -> [ {username, wallClockLimit} ]
    tree: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for exemption in query.all():
        queue = exemption.queue
        rname = queue.resource.resource_name
        qname = queue.queue_name
        tree.setdefault(rname, {}).setdefault(qname, []).append({
            'username':      exemption.user.username,
            'wallClockLimit': exemption.time_limit_hours,
        })

    resources = [
        {
            'resourceName': rname,
            'queues': [
                {
                    'queueName': qname,
                    'limits': sorted(limits, key=lambda l: l['username']),
                }
                for qname, limits in sorted(queues.items())
            ],
        }
        for rname, queues in sorted(tree.items())
    ]

    return {
        'name': 'exemptions',
        'resources': resources,
    }
