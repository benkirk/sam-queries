"""Wallclock-exemption query functions.

``get_wallclock_exemption_data()`` reproduces the legacy Java
``GET /api/protected/admin/ssg/wallClockExemption``. The tree is
resource -> queue -> user limit, consumed by the PBS scheduler to grant a user
a raised wallclock limit on one queue for a bounded window.

Legacy keeps only exemptions whose own date window contains today and applies
NO active filter on the resource, queue, or user. Reproduced exactly with the
``WallclockExemption.is_active`` hybrid. See CLAUDE.md section 5.

Response shape: ``docs/apis/SYSTEMS_INTEGRATION_APIs.md``.
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
