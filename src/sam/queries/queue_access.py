"""
Queue query functions for SAM.

Provides get_queue_data() which reproduces the output of the legacy Java
``GET /api/protected/admin/ssg/queue`` (and ``/queue/{resource}``) endpoints.

The data is organized as a two-level tree:
  queues -> resource -> queue

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

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_
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


def get_queue_cleanup_candidates(
    session: Session,
    resource_id: int,
    *,
    days: int = 90,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Find active queues on a resource that have seen no charging activity
    in the last ``days`` days — candidates for expiry.

    Usage comes from ``comp_charge_summary``, the only live charge-summary
    table (``dav_charge_summary`` and ``hpc_charge_summary`` stopped receiving
    rows in 2023 / 2024). Its ``queue_id`` FK is populated on effectively every
    row, and ``fk_comp_charge_summary_queue`` indexes the join.

    Two exclusions keep obvious non-candidates off the list:

    * **Pattern rows** — names containing ``*`` (``M*``, ``S*``, ``R*``) are
      templates holding defaults for reservation queues, not real queues. They
      can never accrue charges, and expiring one silently changes those defaults.
    * **Grace period** — a queue whose ``start_date`` falls inside the window
      has not existed long enough to be "unused for N days", so a freshly
      created queue never appears.

    Note what this cannot see: **routing queues never accrue charges**, because
    jobs charge to the execution queue they route into. Such a queue is live but
    indistinguishable from a dead one by usage alone. The ``ever_charged`` flag
    is the available signal — a queue that has *never* been charged may well be
    a routing queue, whereas one that was charged and then went quiet is far
    more likely genuinely dead. Callers should surface that distinction rather
    than treating every candidate the same; the admin UI pre-selects only
    ``preselected`` rows for that reason — and additionally cross-checks
    candidates against the system_status PBS snapshots/roster (see
    ``_annotate_pbs_activity`` in the admin resources routes), which is
    deliberately kept out of this SAM-only query.

    Args:
        session:     SQLAlchemy session.
        resource_id: Resource whose queues to examine.
        days:        Inactivity window in days (default 90).
        now:         Override "now" (testing); defaults to ``datetime.now()``.

    Returns:
        List of dicts sorted by queue name, each with:
          ``queue``        — the Queue instance
          ``last_charged`` — date of its most recent charge, or None if never
          ``ever_charged`` — bool
          ``preselected``  — bool; True for charged-then-stale queues only
    """
    from sam.summaries.comp_summaries import CompChargeSummary

    if now is None:
        now = datetime.now()
    cutoff = now - timedelta(days=days)

    rows = (
        session.query(
            Queue,
            func.max(CompChargeSummary.activity_date).label('last_charged'),
        )
        .outerjoin(CompChargeSummary, CompChargeSummary.queue_id == Queue.queue_id)
        .filter(Queue.resource_id == resource_id)
        .filter(Queue.is_active)
        .filter(~Queue.queue_name.contains('*'))
        # NULL start_date means "active from the beginning" (see Queue.is_active),
        # so such a queue is past any grace period.
        .filter(or_(Queue.start_date.is_(None), Queue.start_date < cutoff))
        .group_by(Queue.queue_id)
        .order_by(Queue.queue_name)
        .all()
    )

    candidates = []
    for queue, last_charged in rows:
        if last_charged is not None and last_charged >= cutoff.date():
            continue        # charged inside the window — still in use
        candidates.append({
            'queue':        queue,
            'last_charged': last_charged,
            'ever_charged': last_charged is not None,
            'preselected':  last_charged is not None,
        })

    return candidates
