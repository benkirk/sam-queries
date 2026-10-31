"""Read side of the HPC account-request queue. Derives; never writes.

Whether a request is fulfilled is a fact about ``users``, re-derived on every
read from the row's email through :func:`sam.queries.xras_accounts.sam_merge_targets`
(the one email-to-user derivation, ``ambiguous`` honored). Stamping the row and
acting on it belong to :mod:`sam.manage.account_requests`.
The ``account`` family's message builders are in
:mod:`sam.queries.account_notices`, which imports ``sam.notify`` and is
therefore never exported from ``sam/queries/__init__.py``; this module is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload, lazyload

from sam.core.account_requests import (
    CREATED_BY_SELF,
    CREATED_BY_SWEEP,
    OPEN_STATES,
    PURPOSE_REACTIVATION,
    PURPOSE_SUBMISSION,
    AccountRequest,
    AccountRequestEvent,
    EventEnrollment,
)
from sam.accounting.allocations import AllocationType
from sam.core.users import User
from sam.integration.tickets.queries import ACCOUNT_REQUEST, tickets_for
from sam.projects.projects import Project
from sam.queries.projects import project_codes
from sam.queries.users import users_by_username_ci
from sam.resources.facilities import Panel

from .xras_accounts import sam_merge_targets

@dataclass(frozen=True)
class Resolution:
    """What the mirror says about one request's email.

    ``hint`` is a casefolded hit on ``desired_username`` -- shown to the
    operator, never acted on: a stranger may already hold that name.
    """
    username: Optional[str] = None
    user_id: Optional[int] = None
    active: bool = False
    ambiguous: bool = False
    hint: Optional[str] = None

    @property
    def ready(self) -> bool:
        return self.active and not self.ambiguous and self.user_id is not None


#: Where a row came from, for the card's Origin facet and the digest.
ORIGIN_SELF, ORIGIN_SPONSOR, ORIGIN_SWEEP = 'self', 'sponsor', 'sweep'
ORIGIN_LABELS = {ORIGIN_SELF: 'Public form', ORIGIN_SPONSOR: 'Invited',
                 ORIGIN_SWEEP: 'XRAS roster'}


def origin_of(row: AccountRequest) -> str:
    if row.created_by == CREATED_BY_SELF:
        return ORIGIN_SELF
    if row.created_by == CREATED_BY_SWEEP:
        return ORIGIN_SWEEP
    return ORIGIN_SPONSOR


def queue_requests(session: Session) -> List[AccountRequest]:
    """The rows NUSD works: open, verified, not held, and not yet fulfilled -- plus
    fulfilled rows whose enrollment failed, which need a retry."""
    return (session.query(AccountRequest)
            .filter(AccountRequest.is_open, ~AccountRequest.is_held,
                    or_(AccountRequest.user_id.is_(None),
                        AccountRequest.fulfill_error.isnot(None)))
            .order_by(AccountRequest.creation_time)
            .all())


def visitors_to_merge(session: Session) -> List[tuple]:
    """``(account_request_id, placeholder, username)`` for each fulfilled ``/request``
    registration whose placeholder no merge has closed yet."""
    rows = (session.query(AccountRequest.account_request_id, AccountRequest.xras_username,
                          User.username)
            .join(User, User.user_id == AccountRequest.user_id)
            .filter(AccountRequest.purpose == PURPOSE_SUBMISSION,
                    AccountRequest.created_by == CREATED_BY_SELF,
                    AccountRequest.state.in_(OPEN_STATES), AccountRequest.merged_at.is_(None),
                    AccountRequest.xras_username.isnot(None))
            .order_by(AccountRequest.account_request_id).all())
    return [tuple(r) for r in rows]


def all_requests(session: Session) -> List[AccountRequest]:
    """Every row, newest first -- the "show everything" view."""
    return (session.query(AccountRequest)
            .order_by(AccountRequest.creation_time.desc())
            .all())


def resolve_requests(session: Session,
                     rows: Sequence[AccountRequest]) -> Dict[int, Resolution]:
    """``account_request_id -> Resolution`` for every row the mirror knows.

    Two batched queries: the email derivation, then the ``users`` rows the
    resolved usernames, the ``desired_username`` hints and each reactivation's
    ``xras_username`` name. A reactivation resolves by that username, not by
    email. Rows with no hit are absent from the result.
    """
    if not rows:
        return {}
    targets = sam_merge_targets(session, [row.email for row in rows])
    wanted = {t['username'] for t in targets.values() if t.get('username')}
    hints = {row.desired_username.casefold() for row in rows if row.desired_username}
    named = {row.xras_username for row in rows
             if row.purpose == PURPOSE_REACTIVATION and row.xras_username}
    users = users_by_username_ci(session, wanted | hints | named)

    resolutions: Dict[int, Resolution] = {}
    for row in rows:
        target = targets.get(row.email)
        hint = None
        if row.desired_username:
            hit = users.get(row.desired_username.casefold())
            hint = hit.username if hit else None
        named_user = (users.get(row.xras_username.casefold())
                      if row.purpose == PURPOSE_REACTIVATION and row.xras_username else None)
        if named_user is not None:
            resolutions[row.account_request_id] = Resolution(
                username=named_user.username, user_id=named_user.user_id,
                active=bool(named_user.is_active), hint=hint)
            continue
        if target is None:
            if hint:
                resolutions[row.account_request_id] = Resolution(hint=hint)
            continue
        if target.get('ambiguous'):
            resolutions[row.account_request_id] = Resolution(ambiguous=True, hint=hint)
            continue
        user = users.get(target['username'].casefold())
        resolutions[row.account_request_id] = Resolution(
            username=target['username'],
            user_id=user.user_id if user else None,
            active=bool(target['active']),
            hint=hint,
        )
    return resolutions


def readiness_of(row: AccountRequest, resolution: Optional[Resolution]) -> str:
    """One word for the card: fulfilled | failed | ready | inactive | ambiguous | open."""
    if row.fulfill_error:
        return 'failed'
    if row.is_fulfilled:
        return 'fulfilled'
    if resolution is None:
        return 'open'
    if resolution.ambiguous:
        return 'ambiguous'
    if resolution.ready:
        return 'ready'
    if resolution.username:
        return 'inactive'
    return 'open'


def waiting_days(row: AccountRequest, *, today: Optional[date] = None) -> int:
    """Days since the request was made. Clamped at zero if a clock is off."""
    since = row.creation_time.date()
    return max(0, ((today or date.today()) - since).days)


def events_for(session: Session,
               rows: Iterable[AccountRequest]) -> Dict[int, AccountRequestEvent]:
    """``event_id -> event`` for every event the rows reference, one query."""
    ids = sorted({row.event_id for row in rows if row.event_id})
    if not ids:
        return {}
    return {e.account_request_event_id: e for e in
            session.query(AccountRequestEvent)
            .filter(AccountRequestEvent.account_request_event_id.in_(ids)).all()}


def event_sponsors(session: Session,
                   events: Iterable[AccountRequestEvent]) -> Dict[int, User]:
    """``user_id -> User`` for every extra sponsor the events name, one query."""
    ids = sorted({e.extra_sponsor_user_id for e in events if e.extra_sponsor_user_id})
    if not ids:
        return {}
    return {u.user_id: u for u in
            session.query(User).filter(User.user_id.in_(ids)).all()}


def request_views(session: Session, rows: Sequence[AccountRequest], *,
                  resolutions: Dict[int, Resolution],
                  events: Dict[int, AccountRequestEvent],
                  today: Optional[date] = None) -> List[Dict[str, Any]]:
    """One plain dict per row: what the card, the digest and the tab read.

    Three batched lookups (sponsors, project codes, tickets) on top of the
    row. Order is the input order. Keys: ``id row first_name last_name name
    email state purpose origin readiness resolution event event_code deadline
    project_code event_project_code sponsor waiting_days verified tickets``.
    """
    sponsor_ids = sorted({r.sponsor_user_id for r in rows if r.sponsor_user_id})
    sponsors = ({u.user_id: u for u in session.query(User)
                 .filter(User.user_id.in_(sponsor_ids)).all()}
                if sponsor_ids else {})
    projects = project_codes(session, {r.project_id for r in rows}
                             | {e.project_id for e in events.values()})
    today = today or date.today()
    tickets = ticket_views(session, [r.account_request_id for r in rows])
    views = []
    for r in rows:
        event = events.get(r.event_id) if r.event_id else None
        views.append({
            'id': r.account_request_id,
            'row': r,
            'first_name': r.first_name, 'last_name': r.last_name,
            'name': r.display_name, 'email': r.email,
            'state': r.state, 'purpose': r.purpose,
            'origin': origin_of(r),
            'readiness': readiness_of(r, resolutions.get(r.account_request_id)),
            'resolution': resolutions.get(r.account_request_id),
            'event': event,
            'event_code': event.event_code if event else '',
            'deadline': event.accounts_needed_by if event else None,
            'project_code': projects.get(r.project_id, '') if r.project_id else '',
            'event_project_code': projects.get(event.project_id, '') if event else '',
            'sponsor': sponsors.get(r.sponsor_user_id),
            'waiting_days': waiting_days(r, today=today),
            'verified': r.is_verified,
            'tickets': tickets.get(r.account_request_id, []),
        })
    return views


def ticket_views(session: Session, request_ids: Iterable[int]
                 ) -> Dict[int, List[Dict[str, Any]]]:
    """``{request_id: [{key url status closed_at origin}, ...]}``, oldest first.

    The URL comes from the provider each row names, so a link survives
    ``TICKET_PROVIDER`` going back to mail; a retired provider gives ``''``.
    """
    found = tickets_for(session, ACCOUNT_REQUEST, request_ids)
    if not found:
        return {}
    # By path and lazily: the registry imports requests (tickets/__init__.py).
    from sam.integration.tickets.registry import provider_for_stored
    providers = {name: provider_for_stored(name)
                 for name in {t.provider for ts in found.values() for t in ts}}
    return {rid: [{'key': t.ticket_key,
                   'url': providers[t.provider].browse_url(t.ticket_key)
                          if providers[t.provider] else '',
                   'status': t.status or '', 'closed_at': t.closed_at,
                   'origin': t.origin} for t in ts]
            for rid, ts in found.items()}


def enrollees_for_event(session: Session, event_id: int) -> List[Dict[str, Any]]:
    """One dict per enrolled user, newest first. Keys: ``user enrolled_at source``.

    The event-enrollment ledger (:class:`EventEnrollment`) is the single source
    of truth, so this includes people no ``account_request`` row ever named --
    the existing-user invite and the signed-in self-enroll.
    """
    rows = (session.query(EventEnrollment)
            .filter(EventEnrollment.event_id == event_id)
            .order_by(EventEnrollment.enrolled_at.desc()).all())
    if not rows:
        return []
    users = {u.user_id: u for u in session.query(User)
             .filter(User.user_id.in_({r.user_id for r in rows})).all()}
    return [{'user': users.get(r.user_id), 'enrolled_at': r.enrolled_at,
             'source': r.source} for r in rows]


def events_for_user(session: Session, user_id: int) -> List[Dict[str, Any]]:
    """One dict per event the user enrolled in, newest first. Keys:
    ``enrollment event project_code enrolled_at source``. A row whose event is
    gone is skipped, not lost."""
    rows = (session.query(EventEnrollment)
            .filter(EventEnrollment.user_id == user_id)
            .order_by(EventEnrollment.enrolled_at.desc()).all())
    if not rows:
        return []
    events = {e.account_request_event_id: e for e in
              session.query(AccountRequestEvent).filter(
                  AccountRequestEvent.account_request_event_id.in_(
                      {r.event_id for r in rows})).all()}
    projects = project_codes(session, (e.project_id for e in events.values()))
    out = []
    for r in rows:
        event = events.get(r.event_id)
        if event is None:
            continue
        out.append({'enrollment': r, 'event': event,
                    'project_code': projects.get(event.project_id, ''),
                    'enrolled_at': r.enrolled_at, 'source': r.source})
    return out


def user_has_enrollments(session: Session, user_id: int) -> bool:
    """Cheap EXISTS for gating the My Events tab."""
    return session.query(EventEnrollment.enrollment_id).filter(
        EventEnrollment.user_id == user_id).first() is not None


def all_events(session: Session, *,
               facility_names: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """Every event across projects, soonest deadline first within active. Keys:
    ``event project_code project_active facility sponsor enrolled``; four
    queries whatever the row count. ``facility_names`` (None = no restriction)
    keeps only events whose project sits in one of them."""
    events = (session.query(AccountRequestEvent)
              .order_by(AccountRequestEvent.active.desc(),
                        AccountRequestEvent.accounts_needed_by,
                        AccountRequestEvent.event_code).all())
    if not events:
        return []
    project_ids = sorted({e.project_id for e in events})
    projects = {p.project_id: p for p in (
        session.query(Project)
        .options(lazyload('*'),
                 joinedload(Project.allocation_type)
                 .joinedload(AllocationType.panel).joinedload(Panel.facility))
        .filter(Project.project_id.in_(project_ids)).all())}
    sponsors = event_sponsors(session, events)
    counts = dict(session.query(EventEnrollment.event_id, func.count())
                  .group_by(EventEnrollment.event_id).all())
    allowed = None if facility_names is None else set(facility_names)
    out = []
    for e in events:
        project = projects.get(e.project_id)
        facility = project.facility_name if project else None
        if allowed is not None and facility not in allowed:
            continue
        out.append({'event': e,
                    'project_code': project.projcode if project else '',
                    'project_active': bool(project and project.is_active),
                    'facility': facility,
                    'sponsor': sponsors.get(e.extra_sponsor_user_id),
                    'enrolled': counts.get(e.account_request_event_id, 0)})
    return out


def upcoming_listed_events(session: Session, *,
                           now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Listed events whose form is open and whose deadline has not passed,
    soonest first, as plain dicts (the caller caches them). Keys: ``event_id
    event_code name instructions project_code accounts_needed_by closes_at
    invite_only``.

    ``now`` is naive-Mountain like the columns it is compared to, NOT the status
    page's naive-UTC clock. The window mirrors AccountRequestEvent.is_open_at.
    """
    now = now or datetime.now()
    E = AccountRequestEvent
    # Inner join: an event on a retired (or vanished) project is not advertised.
    rows = (session.query(E, Project.projcode)
            .join(Project, Project.project_id == E.project_id)
            .filter(E.is_active, E.listed, Project.is_active,
                    or_(E.opens_at.is_(None), E.opens_at <= now),
                    or_(E.closes_at.is_(None), E.closes_at > now),
                    E.accounts_needed_by >= now.date())
            .order_by(E.accounts_needed_by, E.event_code).all())
    return [{'event_id': e.account_request_event_id, 'event_code': e.event_code,
             'name': e.name, 'instructions': e.instructions,
             'project_code': projcode or '',
             'accounts_needed_by': e.accounts_needed_by,
             'closes_at': e.closes_at,
             'invite_only': bool(e.invite_only)} for e, projcode in rows]


def enrolled_event_ids(session: Session, user_id: int) -> set:
    return {eid for (eid,) in session.query(EventEnrollment.event_id)
            .filter(EventEnrollment.user_id == user_id).all()}


#: ``group_by_event`` kinds, and the headings the card and digest print for them.
GROUP_EVENT, GROUP_PROJECT, GROUP_OTHER = 'event', 'project', 'other'
GROUP_XRAS_PENDING, GROUP_XRAS_FULFILLMENT = 'xras_pending', 'xras_fulfillment'
GROUP_LABELS = {GROUP_PROJECT: 'Project Invitations',
                GROUP_XRAS_PENDING: 'XRAS Approved Pending Projects',
                GROUP_XRAS_FULFILLMENT: 'XRAS Fulfillment',
                GROUP_OTHER: 'Other requests'}


def group_by_event(views: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Event groups nearest deadline first, then the event-less views: the
    sweep's rows whose project does not exist yet, the sweep's rows whose
    project does (the account is what is missing), one group per project (by
    projcode), the rest project-less last, each in input order. A view whose
    event is gone is event-less, not lost. Each group is
    ``{'kind', 'label', 'key', 'event', 'rows', 'project_code'}``; ``key`` is
    unique per group and safe in a DOM id.
    """
    by_event: Dict[int, List[Dict[str, Any]]] = {}
    loose: Dict[str, List[Dict[str, Any]]] = {}
    xras: Dict[str, List[Dict[str, Any]]] = {GROUP_XRAS_PENDING: [], GROUP_XRAS_FULFILLMENT: []}
    for v in views:
        if v['event']:
            by_event.setdefault(v['event'].account_request_event_id, []).append(v)
        elif v['origin'] == ORIGIN_SWEEP:
            xras[GROUP_XRAS_FULFILLMENT if v['project_code'] else GROUP_XRAS_PENDING].append(v)
        else:
            loose.setdefault(v['project_code'] or '', []).append(v)
    groups = [{'kind': GROUP_EVENT, 'label': members[0]['event'].name,
               'key': f"event-{members[0]['event'].account_request_event_id}",
               'event': members[0]['event'], 'rows': members,
               'project_code': members[0]['event_project_code']}
              for members in by_event.values()]
    groups.sort(key=lambda g: (g['event'].accounts_needed_by, g['event'].event_code))
    for kind, members in xras.items():
        if members:
            groups.append({'kind': kind, 'label': GROUP_LABELS[kind], 'key': kind,
                           'event': None, 'rows': members, 'project_code': ''})
    for code in sorted(loose, key=lambda c: (c == '', c)):
        kind = GROUP_PROJECT if code else GROUP_OTHER
        groups.append({'kind': kind, 'label': GROUP_LABELS[kind],
                       'key': f'{kind}-{code}' if code else kind,
                       'event': None, 'rows': loose[code], 'project_code': code})
    return groups


def queue_counts(rows: Sequence[AccountRequest],
                 resolutions: Dict[int, Resolution]) -> Dict[str, int]:
    """The header badges: what is open, claimed, ready to reconcile, failed."""
    counts = {'open': 0, 'claimed': 0, 'ready': 0, 'failed': 0}
    for row in rows:
        if row.state == 'claimed':
            counts['claimed'] += 1
        else:
            counts['open'] += 1
        readiness = readiness_of(row, resolutions.get(row.account_request_id))
        if readiness == 'ready':
            counts['ready'] += 1
        elif readiness == 'failed':
            counts['failed'] += 1
    return counts


def unverified_count(session: Session) -> int:
    """Rows the public form created that never verified -- not in the queue."""
    return (session.query(AccountRequest)
            .filter(AccountRequest.verified_at.is_(None),
                    AccountRequest.state.in_(OPEN_STATES))
            .count())


def requests_by_xras_username(session: Session, usernames: Iterable[Optional[str]]
                              ) -> Dict[str, List[AccountRequest]]:
    """``{xras_key: [AccountRequest, ...]}`` oldest first, in one query."""
    keys = sorted({k for k in map(AccountRequest.xras_key, usernames) if k})
    if not keys:
        return {}
    found: Dict[str, List[AccountRequest]] = {}
    for req in (session.query(AccountRequest)
                .filter(AccountRequest.xras_username.in_(keys))
                .order_by(AccountRequest.creation_time).all()):
        found.setdefault(req.xras_username, []).append(req)
    return found


def current_request(requests: Sequence[AccountRequest]) -> Optional[AccountRequest]:
    """The row a person's queue state is: the newest open one, else the newest."""
    live = [r for r in requests if r.state in OPEN_STATES and not r.is_fulfilled]
    return (live or list(requests) or [None])[-1]


def stamp_account_requests(session: Session,
                           worklist_rows: Sequence[Dict[str, Any]]) -> None:
    """Point each Pending Users row at its ``account_request``, in place.

    Matched on the XRAS username the sweep stored in ``xras_username``,
    casefolded on both sides for the same reason ``classify_accounts`` is.
    Sets ``row['account_request']`` to a small dict or ``None``; every state
    is reported so the card can offer reopen on a dismissed row. A person with
    several rows (a submission, later a reactivation) shows the open one.
    """
    if not any(r.get('username') for r in worklist_rows):
        return
    found = requests_by_xras_username(session, (r.get('username') for r in worklist_rows))
    for row in worklist_rows:
        req = current_request(found.get(AccountRequest.xras_key(row.get('username')), ()))
        row['account_request'] = ({
            'id': req.account_request_id,
            'state': req.state,
            'assignee': req.assignee,
            'requested_at': req.requested_at,
            'held': req.is_held,
            'fulfilled': req.is_fulfilled,
            'creation_time': req.creation_time,
        } if req else None)
