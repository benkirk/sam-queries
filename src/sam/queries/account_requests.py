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

from sqlalchemy import or_
from sqlalchemy.orm import Session

from sam.core.account_requests import (
    CREATED_BY_SELF,
    CREATED_BY_SWEEP,
    OPEN_STATES,
    AccountRequest,
    AccountRequestEvent,
)
from sam.core.users import User
from sam.projects.projects import Project
from sam.projects.projects import Project

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


def origin_of(row: AccountRequest) -> str:
    if row.created_by == CREATED_BY_SELF:
        return ORIGIN_SELF
    if row.created_by == CREATED_BY_SWEEP:
        return ORIGIN_SWEEP
    return ORIGIN_SPONSOR


def queue_requests(session: Session) -> List[AccountRequest]:
    """The rows NUSD works: open, verified, and not yet fulfilled -- plus
    fulfilled rows whose enrollment failed, which need a retry."""
    return (session.query(AccountRequest)
            .filter(AccountRequest.is_open,
                    or_(AccountRequest.user_id.is_(None),
                        AccountRequest.fulfill_error.isnot(None)))
            .order_by(AccountRequest.creation_time)
            .all())


def all_requests(session: Session) -> List[AccountRequest]:
    """Every row, newest first -- the "show everything" view."""
    return (session.query(AccountRequest)
            .order_by(AccountRequest.creation_time.desc())
            .all())


def resolve_requests(session: Session,
                     rows: Sequence[AccountRequest]) -> Dict[int, Resolution]:
    """``account_request_id -> Resolution`` for every row the mirror knows.

    Two batched queries: the email derivation, then the ``users`` rows the
    resolved usernames and the ``desired_username`` hints name. Rows with no
    hit are absent from the result.
    """
    if not rows:
        return {}
    targets = sam_merge_targets(session, [row.email for row in rows])
    wanted = {t['username'] for t in targets.values() if t.get('username')}
    hints = {row.desired_username.casefold() for row in rows if row.desired_username}
    lookup = wanted | hints
    users: Dict[str, User] = {}
    if lookup:
        # users.username is case-insensitive at the DB, so IN matches either
        # spelling; the dict must be casefolded to find what came back.
        users = {u.username.casefold(): u for u in
                 session.query(User).filter(User.username.in_(sorted(lookup))).all()}

    resolutions: Dict[int, Resolution] = {}
    for row in rows:
        target = targets.get(row.email)
        hint = None
        if row.desired_username:
            hit = users.get(row.desired_username.casefold())
            hint = hit.username if hit else None
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

    Two batched lookups (sponsors, project codes) on top of the row. Order
    is the input order. Keys: ``id row first_name last_name name email
    state purpose origin readiness resolution event event_code deadline
    project_code event_project_code sponsor waiting_days verified``.
    """
    sponsor_ids = sorted({r.sponsor_user_id for r in rows if r.sponsor_user_id})
    sponsors = ({u.user_id: u for u in session.query(User)
                 .filter(User.user_id.in_(sponsor_ids)).all()}
                if sponsor_ids else {})
    project_ids = sorted({r.project_id for r in rows if r.project_id}
                         | {e.project_id for e in events.values()})
    projects = (dict(session.query(Project.project_id, Project.projcode)
                     .filter(Project.project_id.in_(project_ids)).all())
                if project_ids else {})
    today = today or date.today()
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
        })
    return views


def group_by_event(views: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Event groups nearest deadline first, then the loose views last, each
    in input order. A view whose event is gone (``event`` None) is loose,
    not lost. Each group is ``{'event', 'rows', 'project_code'}``.
    """
    by_event: Dict[int, List[Dict[str, Any]]] = {}
    loose: List[Dict[str, Any]] = []
    for v in views:
        (by_event.setdefault(v['event'].account_request_event_id, [])
         if v['event'] else loose).append(v)
    groups = [{'event': members[0]['event'], 'rows': members,
               'project_code': members[0]['event_project_code']}
              for members in by_event.values()]
    groups.sort(key=lambda g: (g['event'].accounts_needed_by, g['event'].event_code))
    if loose:
        groups.append({'event': None, 'rows': loose, 'project_code': ''})
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


def stamp_account_requests(session: Session,
                           worklist_rows: Sequence[Dict[str, Any]]) -> None:
    """Point each Pending Users row at its ``account_request``, in place.

    Matched on the XRAS username the sweep stored in ``xras_username``,
    casefolded on both sides for the same reason ``classify_accounts`` is.
    Sets ``row['account_request']`` to a small dict or ``None``; every state
    is reported so the card can offer reopen on a dismissed row.
    """
    wanted = sorted({(r.get('username') or '').lower()
                     for r in worklist_rows if r.get('username')})
    if not wanted:
        return
    found = {}
    for req in (session.query(AccountRequest)
                .filter(AccountRequest.xras_username.in_(wanted))
                .order_by(AccountRequest.creation_time).all()):
        found[req.xras_username] = req
    for row in worklist_rows:
        req = found.get((row.get('username') or '').lower())
        row['account_request'] = ({
            'id': req.account_request_id,
            'state': req.state,
            'assignee': req.assignee,
            'requested_at': req.requested_at,
            'fulfilled': req.is_fulfilled,
        } if req else None)
