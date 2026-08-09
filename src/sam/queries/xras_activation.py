"""The XRAS pending-activation worklist — a *different* table from the action log.

Split out of :mod:`sam.queries.xras_actions`, which had fused two concerns over two
tables with no shared helpers across the seam. The forms layer already splits exactly
this way (``sam/schemas/forms/xras.py`` and ``xras_activation.py``); this brings the
query layer in line.

**What lives here.** Everything about ``xras_activation_event`` and the derived
operator state on top of it: which XRAS-touched projects are still inactive, what an
operator has done about each, who would be notified, and the provenance link back to
the action that prompted it.

**The one thing shared across the seam** is the ``projcode_result`` OR
``request_number`` join, imported from :mod:`~sam.queries.xras_actions` as
:func:`~sam.queries.xras_actions.action_names_project` together with the
``_LATEST_ACTION_ORDER`` tie-break. ⚠️ Both must stay imported rather than re-spelled
here — that duplication is precisely what let the pending card and the provenance
stamp name different actions for one project.

Everything is re-exported through :mod:`sam.queries`, so call sites are unaffected by
the split.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func

from sam.integration.xras import XrasActionLog, XrasActivationEvent
from sam.projects.projects import Project

from .xras_actions import _LATEST_ACTION_ORDER, action_names_project

__all__ = [
    'get_xras_pending_activation',
    'count_xras_dismissed_pending',
    'get_latest_xras_action_id',
    'get_xras_activation_events',
    'get_xras_pending_recipients',
]


def _activation_state(
    session: Session,
    project_ids: List[int],
) -> Dict[int, Dict[str, Any]]:
    """Latest event of each type, plus the comment count, per project.

    One grouped query over ``xras_activation_event`` for the whole page, joined
    in memory by the caller — not N+1, and not a correlated subquery per row.
    The ``(project_id, creation_time)`` index serves it.

    Ties on ``creation_time`` break by id descending: the column is DATETIME with
    one-second resolution, and two events a second apart in the same click burst
    are entirely possible.
    """
    if not project_ids:
        return {}

    rows = (
        session.query(XrasActivationEvent)
        .filter(XrasActivationEvent.project_id.in_(project_ids))
        .order_by(XrasActivationEvent.creation_time.asc(),
                  XrasActivationEvent.xras_activation_event_id.asc())
        .all()
    )

    state: Dict[int, Dict[str, Any]] = {}
    for event in rows:
        # Ascending order means a later row simply overwrites an earlier one, so
        # what survives per key is the latest.
        per_project = state.setdefault(event.project_id, {'comment_count': 0})
        if event.event_type == 'comment':
            per_project['comment_count'] += 1
        else:
            per_project[event.event_type] = event
    return state


def get_xras_pending_activation(
    session: Session,
    *,
    limit: Optional[int] = None,
    include_dismissed: bool = False,
) -> List[Dict[str, Any]]:
    """Projects an XRAS action touched that are still inactive.

    XRAS projects arrive ``active = 0`` **by design** and a human activates them.
    Legacy's trigger for that human is its success email; SAM has no mailer, so
    this view is the stand-in — which is what keeps SMTP deferred rather than a
    prerequisite for the ``POST /actions`` cutover.

    **Identification, and its limit.** There is no marker on ``Project`` saying
    "XRAS created this" — nothing in ``sam/projects/`` records provenance, and
    ``XrasActionView`` is an *outbound* reporting view derived from allocations,
    not a record of inbound posts. So a project qualifies here iff some
    ``xras_action_log`` row names it, via either:

    - ``projcode_result`` — the New path, where SAM minted the projcode; or
    - ``request_number`` — Extension/Supplement/Update, where XRAS sends the
      projcode *as* the request number.

    The consequence is worth stating plainly rather than discovering later: **this
    card sees only projects this table knows about.** It renders empty today
    (capture mode has created nothing) and grows exactly as the log grows. It does
    **not** retro-discover the 23 historical XRAS projects legacy created, and an
    empty card must not be read as "nothing pending" until SAM has been the system
    of record for a while.

    **Derived operator state.** Rows also carry the current state of the worklist,
    computed from ``xras_activation_event`` rather than stored. Nothing here is a
    boolean column; every state is a timestamp compared against ``received_time``,
    which is already the most recent action naming the project::

        hidden from the card  iff  latest('dismissed')
                                       > MAX(received_time, latest('restored'))
        "marked notified"     iff  latest('notified')  > received_time

    That single rule is both the anti-spam mechanism and the re-open mechanism: a
    dismissed project **reappears** when a new Extension arrives (new information
    — the operator should look again), while a notified one stays quiet until
    something actually changes, and goes stale the moment it does. A stored
    boolean gets both wrong. See ``sam.integration.xras.XrasActivationEvent``.

    Args:
        session: the session to query.
        limit: display cap, applied after sorting, in Python.
        include_dismissed: when True, hidden rows are returned too, flagged
            ``dismissed=True``. The card's "Show dismissed" toggle needs this —
            without it a dismissal is unrecoverable until a new action arrives,
            which may be never.

    Returns:
        A list of dicts with ``projcode``, ``project_id``, ``title``,
        ``action_log_id``, ``action_type``, ``received_time``, ``status`` —
        one per project, carrying its most recent XRAS action — plus the derived
        ``dismissed``, ``dismissed_time``, ``dismissed_by``, ``notified_time``,
        ``notified_by``, ``notified_stale`` and ``comment_count``.
    """
    # One OR-join, ordered newest-first, keeping the first row seen per project.
    #
    # ⚠️ This was two per-column queries merged in Python, and the merge compared
    # only `received_time` — so on a same-second tie whichever column was iterated
    # first won, regardless of id, while `get_latest_xras_action_id` broke the same
    # tie on id. The two could name different actions for one project. Both now go
    # through `action_names_project` and `_LATEST_ACTION_ORDER`; do not re-split
    # this into per-column passes.
    #
    # The old form justified itself as keeping "which column matched" available.
    # Nothing ever read it — the dict below carries no such key — so the OR-join
    # gives up nothing and costs one query instead of two.
    rows = (
        session.query(XrasActionLog, Project)
        .join(Project, action_names_project(Project.projcode))
        .filter(~Project.is_active)
        .order_by(*_LATEST_ACTION_ORDER)
        .all()
    )

    candidates: Dict[str, Dict[str, Any]] = {}
    for action, project in rows:
        # First wins: the rows arrive newest-first, so this keeps the most recent
        # action per project — an Extension that followed a New is the one an
        # operator should be looking at.
        candidates.setdefault(project.projcode, {
            'projcode': project.projcode,
            'project_id': project.project_id,
            'title': project.title,
            'action_log_id': action.xras_action_log_id,
            'action_type': action.action_type,
            'received_time': action.received_time,
            'status': action.status,
        })

    state = _activation_state(session, [c['project_id'] for c in candidates.values()])

    pending: List[Dict[str, Any]] = []
    for row in candidates.values():
        events = state.get(row['project_id'], {})
        dismissed_ev = events.get('dismissed')
        restored_ev = events.get('restored')
        notified_ev = events.get('notified')

        # A dismissal is superseded by whichever came later: a fresh XRAS action
        # (new information) or an explicit Restore (the operator undoing it).
        supersedes = row['received_time']
        if restored_ev is not None and restored_ev.creation_time > supersedes:
            supersedes = restored_ev.creation_time
        is_dismissed = (dismissed_ev is not None
                        and dismissed_ev.creation_time > supersedes)

        if is_dismissed and not include_dismissed:
            continue

        # Ages are computed here, as timedeltas, because ``fmt_ago`` takes an
        # elapsed delta rather than a timestamp. Doing the subtraction in the
        # template would put `datetime.now()` in Jinja, where it is untestable.
        now = datetime.now()
        row.update({
            'dismissed': is_dismissed,
            'dismissed_time': dismissed_ev.creation_time if is_dismissed else None,
            'dismissed_by': dismissed_ev.created_by if is_dismissed else None,
            'dismissed_reason': dismissed_ev.comment if is_dismissed else None,
            'notified_time': notified_ev.creation_time if notified_ev else None,
            'notified_age': (now - notified_ev.creation_time) if notified_ev else None,
            'notified_by': notified_ev.created_by if notified_ev else None,
            # Stale means "we told them, then the situation changed" — the button
            # becomes "Mark notified again" rather than staying quiet.
            'notified_stale': (notified_ev is not None
                               and notified_ev.creation_time <= row['received_time']),
            'comment_count': events.get('comment_count', 0),
        })
        pending.append(row)

    pending.sort(key=lambda r: r['received_time'], reverse=True)
    return pending[:limit] if limit is not None else pending


def count_xras_dismissed_pending(session: Session) -> int:
    """How many pending rows are currently hidden by a dismissal.

    Feeds the card's empty state and its "Show dismissed" toggle. Without a
    truthful count, "no rows" reads as "all clear" when it may mean "all
    dismissed" — the same honesty problem the empty-state copy already solves for
    capture mode.

    ⚠️ This runs the **whole** pending pipeline. A caller that also needs the rows
    should call :func:`get_xras_pending_activation` once with
    ``include_dismissed=True`` and count the ``dismissed`` flag itself, as
    ``xras_pending_fragment`` does — calling both doubles the work for a number
    already present in the rows. This exists for callers that want only the count.
    """
    return sum(1 for row in get_xras_pending_activation(
        session, include_dismissed=True) if row['dismissed'])


def get_latest_xras_action_id(
    session: Session,
    project_id: int,
) -> Optional[int]:
    """The most recent ``xras_action_log`` row naming *project_id*, or None.

    Provenance for ``xras_activation_event.xras_action_log_id``. Lives here rather
    than in a route because it shares :func:`action_names_project` and
    :data:`_LATEST_ACTION_ORDER` with :func:`get_xras_pending_activation` — the card
    that says *why* a project is pending and the stamp recording what the operator
    did about it must never name different actions.
    """
    project = session.get(Project, project_id)
    if project is None:
        return None

    row = (
        session.query(XrasActionLog)
        .filter(action_names_project(project.projcode))
        .order_by(*_LATEST_ACTION_ORDER)
        .first()
    )
    return row.xras_action_log_id if row else None


def get_xras_activation_events(
    session: Session,
    project_id: int,
) -> List[Dict[str, Any]]:
    """The append-only operator timeline for one project, newest first.

    ⚠️ Rows carry ``notified_to`` — project lead and admin contact details. The
    caller must gate this on ``Permission.MANAGE_XRAS``, the same authority that
    gates the raw payload, not on ``VIEW_XRAS``.
    """
    rows = (
        session.query(XrasActivationEvent)
        .filter(XrasActivationEvent.project_id == project_id)
        .order_by(XrasActivationEvent.creation_time.desc(),
                  XrasActivationEvent.xras_activation_event_id.desc())
        .all()
    )
    now = datetime.now()
    return [{
        'event_id': r.xras_activation_event_id,
        'event_type': r.event_type,
        'comment': r.comment,
        'notified_to': r.notified_to,
        'action_log_id': r.xras_action_log_id,
        'created_by': r.created_by,
        'creation_time': r.creation_time,
        # ``fmt_ago`` takes an elapsed timedelta, not a timestamp.
        'age': now - r.creation_time,
    } for r in rows]


def get_xras_pending_recipients(
    session: Session,
    project_ids: List[int],
) -> Dict[int, List[Dict[str, str]]]:
    """Lead and admin contact details for the notify handoff, per project.

    ⚠️ **Deliberately not folded into** :func:`get_xras_pending_activation`.
    Doing so would push contact PII into every caller of that function, including
    the ``VIEW_XRAS`` render of the card. Keeping it separate lets the route ask
    for addresses only when the viewer holds ``MANAGE_XRAS``, so a view-source
    cannot leak what the page chose not to draw — the same route-level (not
    template-level) gate the raw-payload panel uses.

    Returns:
        ``{project_id: [{'name': ..., 'email': ..., 'role': 'lead'|'admin'}]}``.
        A project with neither on file maps to an empty list; the caller decides
        whether that blocks anything (it does not — an operator may have reached
        them out of band).
    """
    if not project_ids:
        return {}

    projects = (
        session.query(Project)
        .filter(Project.project_id.in_(project_ids))
        .all()
    )

    recipients: Dict[int, List[Dict[str, str]]] = {}
    for project in projects:
        people = []
        seen = set()
        for role, user in (('lead', project.lead), ('admin', project.admin)):
            if user is None:
                continue
            email = user.primary_email
            if not email or email in seen:
                continue
            seen.add(email)
            people.append({
                'name': user.full_name or user.username,
                'email': email,
                'role': role,
            })
        recipients[project.project_id] = people
    return recipients
