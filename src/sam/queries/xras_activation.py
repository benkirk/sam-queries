"""XRAS operator state — a *different* table from the action log.

Split out of :mod:`sam.queries.xras_actions`, which had fused two concerns over two
tables with no shared helpers across the seam. The forms layer already splits exactly
this way (``sam/schemas/forms/xras.py`` and ``xras_activation.py``); this brings the
query layer in line.

**What lives here.** Everything about ``xras_activation_event`` and the derived
operator state on top of it: what happened recently, what an operator has done about
each outcome, who would be notified, and the provenance link back to the action that
prompted it.

The centerpiece is :func:`get_xras_activity`, which is keyed on the **action**. It
replaced a project-keyed worklist filtered on ``~Project.is_active``; see its
docstring for the three operator-visible bugs that key caused.

**The one thing shared across the seam** is the ``projcode_result`` OR
``request_number`` join, imported from :mod:`~sam.queries.xras_actions` as
:func:`~sam.queries.xras_actions.action_names_project` together with the
``_LATEST_ACTION_ORDER`` tie-break. WARNING: Both must stay imported rather than re-spelled
here — that duplication is precisely what let the pending card and the provenance
stamp name different actions for one project.

Everything is re-exported through :mod:`sam.queries`, so call sites are unaffected by
the split.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from sam.integration.xras import XrasActionLog, XrasActivationEvent
from sam.projects.projects import Project

from .notifications import get_recent_notifications
from .xras_actions import _LATEST_ACTION_ORDER, action_names_project

__all__ = [
    'ACTIVITY_TAGS',
    'ATTENTION_RECENT_DAYS',
    'XRAS_SERVICE_KINDS',
    'activity_tags',
    'needs_attention',
    'get_xras_activity',
    'xras_dedup_key',
    'parse_xras_dedup_key',
    'get_latest_xras_action_id',
    'get_xras_activation_events',
    'get_xras_pending_recipients',
]


#: ``xras_action_log.service`` -> the notification kind that reports it.
#:
#: The dispatcher's vocabulary is :data:`sam.xras.dispatch.SERVICES`; this maps
#: the subset that produces something a PI should be told about. One is
#: deliberately absent:
#:
#: * ``transfer`` — parks as ``manual`` by design and never completes, so there
#:   is no outcome to report.
#:
#: ``adjust`` was absent for the same reason until the Round 2 smoke, on the
#: grounds that an Adjustment can be a **reduction**. It notifies now; the
#: reduction case is handled in the template's wording rather than by staying
#: silent, because a PI whose allocation shrank is precisely who needs telling.
#:
#: A row whose service is not here still appears on the activity table as
#: history; it simply has no Notify button. Adding a kind is this dict plus
#: :data:`sam.notify.kinds.NOTIFICATION_KINDS` plus :data:`XRAS_KIND_SUBJECTS`
#: plus the two template files; ``tests/unit/test_xras_taxonomy_parity.py`` fails
#: if any of those layers is left behind.
XRAS_SERVICE_KINDS: Mapping[str, str] = {
    'add': 'xras_activation',
    'update': 'xras_update',
    'extend': 'xras_extension',
    'supplement': 'xras_supplement',
    'adjust': 'xras_adjustment',
}

#: Statuses whose ledger row means the recipient was actually reached.
#: Mirrors :data:`sam.notify.ledger.SUPPRESSING_STATUSES` — deliberately, since
#: "we told them" and "do not tell them again" must be the same predicate.
_DELIVERED_STATUSES = frozenset({'sent', 'redirected'})


def xras_dedup_key(kind: str, projcode: str, action_id: Optional[int],
                   address: str) -> str:
    """The one place an XRAS notification key is spelled.

    ``{kind}:{projcode}:{action_id}:{address}``. Keyed on the **action**, not
    the project, which is what lets a Supplement be notified after a New
    without either suppressing the other.

    ``action_id`` may be ``None`` — a project no action names is still a
    stable key, it just says so.
    """
    return f'{kind}:{projcode}:{action_id}:{address}'


def parse_xras_dedup_key(
    key: Optional[str],
) -> Optional[Tuple[str, str, Optional[int], str]]:
    """Read a key back, or ``None`` if it is not one of ours.

    The inverse of :func:`xras_dedup_key`, and the **only** reader — the
    activity table correlates ``notification_log`` rows to actions through
    this rather than through a foreign key, because ``notification_log`` is
    deliberately generic (no FKs, ``entity_type``/``entity_id``) and an
    XRAS-shaped column on it would be the wrong kind of coupling.

    That correlation is cheap: ``notification_log_projcode`` serves the
    ``IN`` fetch and the parse happens in Python over rows already in hand.

    Returns ``(kind, projcode, action_id, address)``. An expiration key, a
    malformed one, or ``None`` all return ``None`` rather than raising —
    callers are rendering a table, not validating input.
    """
    parts = (key or '').split(':', 3)
    if len(parts) != 4:
        return None
    kind, projcode, raw_action, address = parts
    if kind not in set(XRAS_SERVICE_KINDS.values()):
        return None
    try:
        action_id: Optional[int] = int(raw_action)
    except ValueError:
        action_id = None
    return kind, projcode, action_id, address


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


def get_xras_activity(
    session: Session,
    *,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    statuses: Sequence[str] = ('processed',),
) -> List[Dict[str, Any]]:
    """Recent XRAS outcomes — one row per **action**, not per project.

    This replaces the project-keyed pending worklist, and the change of key is
    the whole point. A project-keyed row folds every action into one and is
    filtered on ``~Project.is_active``, which has three consequences an
    operator actually hit:

    * activating a project **erased** its own Notify button, so activating
      before notifying left no way back;
    * a Supplement or Extension against a live project was invisible — it
      adjusted real allocations and appeared nowhere;
    * a second action had to mark the first notification "stale", because one
      row cannot represent two things having happened.

    Keyed on the action, all three dissolve. Nothing leaves the table because
    someone did their job, and ``notified_stale`` stops existing as a concept:
    a new action is a new row.

    **Scope.** Successfully processed actions only, by default. A failure or a
    ``manual`` park needs an operator to fix something, not to mail anyone,
    and it is already on the action-log table below with its own filters.

    **Dismissal no longer hides.** It clears the call to action and marks the
    row; the history stays. A table whose purpose is "what did we tell people"
    cannot have rows that vanish.

    Args:
        session: the session to query.
        since: lower bound on ``received_time``. ``None`` means all time —
            the attention queue, which is state rather than a window.
        until: upper bound, for a custom range.
        statuses: which ``xras_action_log.status`` values qualify.

    Returns:
        Newest first. Each row carries the action (``action_log_id``,
        ``action_type``, ``service``, ``received_time``, ``status``), its
        project (``projcode``, ``project_id``, ``title``, ``project_active``),
        the derived operator state (``dismissed`` and friends,
        ``comment_count``, ``is_latest_action``), the notification rollup
        (``kind``, ``notifiable``, ``notifications``, ``notified``,
        ``notified_time``, ``notified_age``, ``delivered_count``,
        ``failed_count``, ``suppressed_count``) and ``tags`` — see
        :func:`activity_tags`.
    """
    query = (
        session.query(XrasActionLog, Project)
        .join(Project, action_names_project(Project.projcode))
    )
    if statuses:
        query = query.filter(XrasActionLog.status.in_(list(statuses)))
    if since is not None:
        query = query.filter(XrasActionLog.received_time >= since)
    if until is not None:
        query = query.filter(XrasActionLog.received_time <= until)

    window = query.order_by(*_LATEST_ACTION_ORDER).all()
    if not window:
        return []

    projcodes = sorted({project.projcode for _action, project in window})

    # Which action is newest for each project — over ALL time, not just the
    # window, or a narrow window would promote an old action to "latest" and
    # put an Activate button on the wrong row. Same ordering as
    # `get_latest_xras_action_id`, so the two cannot disagree on a tie.
    latest_action_id: Dict[str, int] = {}
    for action, project in (
        session.query(XrasActionLog, Project)
        .join(Project, action_names_project(Project.projcode))
        .filter(Project.projcode.in_(projcodes))
        .order_by(*_LATEST_ACTION_ORDER)
        .all()
    ):
        latest_action_id.setdefault(project.projcode, action.xras_action_log_id)

    state = _activation_state(
        session, sorted({project.project_id for _a, project in window}))

    # One indexed fetch for every notification about these projects, bucketed
    # in Python by the action id embedded in the dedup key. `limit=None` is
    # safe here precisely because the projcode IN list already bounds it.
    by_action: Dict[Tuple[str, int], List[Any]] = {}
    for row in get_recent_notifications(session, projcodes=projcodes,
                                        limit=None):
        parsed = parse_xras_dedup_key(row.dedup_key)
        if parsed is None:
            continue
        _kind, parsed_projcode, action_id, _address = parsed
        if action_id is None:
            continue
        by_action.setdefault((parsed_projcode, action_id), []).append(row)

    now = datetime.now()
    rows: List[Dict[str, Any]] = []
    for action, project in window:
        events = state.get(project.project_id, {})
        dismissed_ev = events.get('dismissed')
        restored_ev = events.get('restored')

        # A dismissal is superseded by whichever came later: a fresh XRAS
        # action (new information) or an explicit Restore. Unchanged from the
        # worklist rule — only its *effect* changed, from hiding to marking.
        supersedes = action.received_time
        if restored_ev is not None and restored_ev.creation_time > supersedes:
            supersedes = restored_ev.creation_time
        is_dismissed = (dismissed_ev is not None
                        and dismissed_ev.creation_time > supersedes)

        notifications = by_action.get(
            (project.projcode, action.xras_action_log_id), [])
        delivered = [n for n in notifications
                     if n.status in _DELIVERED_STATUSES]
        newest_delivery = delivered[0] if delivered else None

        row: Dict[str, Any] = {
            'action_log_id': action.xras_action_log_id,
            'action_type': action.action_type,
            'service': action.service,
            'received_time': action.received_time,
            'status': action.status,
            'projcode': project.projcode,
            'project_id': project.project_id,
            'title': project.title,
            'project_active': bool(project.is_active),
            'is_latest_action': (latest_action_id.get(project.projcode)
                                 == action.xras_action_log_id),
            'kind': XRAS_SERVICE_KINDS.get(action.service or ''),
            'dismissed': is_dismissed,
            'dismissed_time': dismissed_ev.creation_time if is_dismissed else None,
            'dismissed_by': dismissed_ev.created_by if is_dismissed else None,
            'dismissed_reason': dismissed_ev.comment if is_dismissed else None,
            'comment_count': events.get('comment_count', 0),
            'notifications': notifications,
            'notified': bool(delivered),
            'notified_time': newest_delivery.creation_time if newest_delivery else None,
            # A timedelta, because ``fmt_ago`` takes an elapsed delta. Putting
            # ``datetime.now()`` in Jinja would make it untestable.
            'notified_age': ((now - newest_delivery.creation_time)
                             if newest_delivery else None),
            'delivered_count': len(delivered),
            'failed_count': sum(1 for n in notifications if n.status == 'failed'),
            'suppressed_count': sum(1 for n in notifications
                                    if n.status == 'suppressed'),
        }
        row['notifiable'] = row['kind'] is not None
        row['needs_activation'] = (not row['project_active']
                                   and row['is_latest_action']
                                   and not is_dismissed)
        row['tags'] = activity_tags(row)
        rows.append(row)

    return rows


#: The chip vocabulary, in display order. Declared rather than derived so a
#: value with no rows still renders — an absent chip reads as "not measured",
#: which is a different claim from "none".
ACTIVITY_TAGS: Tuple[str, ...] = (
    'needs_activation', 'not_notified', 'notified', 'failed', 'dismissed',
)


def activity_tags(row: Mapping[str, Any]) -> List[str]:
    """The chip tags one activity row carries.

    A **list**, not a single state, because the useful questions overlap: a
    row can need activation *and* not have been notified, and an operator
    filtering on "not notified" must still find it. Collapsing these into one
    enum would make that row reachable from only one chip.
    """
    tags: List[str] = []
    if row.get('needs_activation'):
        tags.append('needs_activation')
    if row.get('notified'):
        tags.append('notified')
    elif row.get('notifiable'):
        tags.append('not_notified')
    if row.get('failed_count'):
        tags.append('failed')
    if row.get('dismissed'):
        tags.append('dismissed')
    return tags


#: Days a row stays in the attention queue on recency alone, so a fresh post
#: is seen once even when nothing about it needs a click.
ATTENTION_RECENT_DAYS = 3


def needs_attention(row: Mapping[str, Any], *, now: datetime,
                    recent_days: int = ATTENTION_RECENT_DAYS) -> bool:
    """Needs a human, or too recent to have been looked at. Dismissed is never in.

    ``now`` is injected, unlike :func:`get_xras_activity`'s own clock, so the
    boundary is testable without patching. A dateless row is never "recent" —
    the same guard as the ``xras_notices`` task.
    """
    if row.get('dismissed'):
        return False
    if row.get('needs_activation'):
        return True
    if row.get('notifiable') and not row.get('notified'):
        return True
    received = row.get('received_time')
    return received is not None and received >= now - timedelta(days=recent_days)


def notify_only_project_ids(rows: Iterable[Mapping[str, Any]]) -> List[int]:
    """Projects whose queue rows only await a notification, none needing activation.

    Selected per PROJECT, not per action row: a dismissal is project-scoped and
    supersedes the latest action, and `needs_activation` rides only on that
    latest action — so a project holding an older notify-only action AND a latest
    needs-activation action must be excluded, or dismissing it would suppress the
    activation too. A project qualifies iff it has a live notify-only row
    (notifiable, not notified, not dismissed) and no row needs activation.
    """
    state: dict[int, dict[str, bool]] = {}
    for row in rows:
        s = state.setdefault(row['project_id'],
                             {'notify_only': False, 'needs_activation': False})
        if row.get('needs_activation'):
            s['needs_activation'] = True
        if (row.get('notifiable') and not row.get('notified')
                and not row.get('needs_activation') and not row.get('dismissed')):
            s['notify_only'] = True
    return sorted(pid for pid, s in state.items()
                  if s['notify_only'] and not s['needs_activation'])


def get_latest_xras_action_id(
    session: Session,
    project_id: int,
) -> Optional[int]:
    """The most recent ``xras_action_log`` row naming *project_id*, or None.

    Provenance for ``xras_activation_event.xras_action_log_id``. Lives here rather
    than in a route because it shares :func:`action_names_project` and
    :data:`_LATEST_ACTION_ORDER` with :func:`get_xras_activity` — the table
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

    WARNING: Rows carry ``notified_to`` — project lead and admin contact details. The
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

    WARNING: **Deliberately not folded into** :func:`get_xras_activity`.
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
                # `display_name` first: it is (nickname or first) + last, so a
                # PI greeted by mail gets "Ben Kirk" rather than the
                # middle-name-carrying "Benjamin Shelton Kirk" that `full_name`
                # produces. Everywhere else in the UI honors the nickname; a
                # notice addressed to someone should read the way their
                # colleagues address them.
                #
                # The rest of the chain only fires when `display_name` is
                # EMPTY, which needs both (nickname or first) and last to be
                # null — i.e. a user with no usable name, who falls through to
                # `username`. `full_name` sits between them for the one row
                # shape that has a middle name and nothing else; it is close to
                # theoretical, and kept because it costs one `or`.
                'name': user.display_name or user.full_name or user.username,
                'email': email,
                'role': role,
            })
        recipients[project.project_id] = people
    return recipients
