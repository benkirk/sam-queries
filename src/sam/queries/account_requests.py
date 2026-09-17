"""Read side of the HPC account-request queue. Derives; never writes.

Whether a request is fulfilled is a fact about ``users``, re-derived on every
read from the row's email through :func:`sam.queries.xras_accounts.sam_merge_targets`
(the one email-to-user derivation, ``ambiguous`` honored). Stamping the row and
acting on it belong to :mod:`sam.manage.account_requests`.

WARNING: NOT exported from ``sam/queries/__init__.py``. This module also holds
the ``sam.notify`` message builders for the family, and that file imports its
submodules eagerly, so listing it would put ``sam.notify.base`` into every
``from sam.queries import ...``. Import by full path; the gate is
``tests/unit/test_notify_import_graph.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import or_
from sqlalchemy.orm import Session

from sam.core.account_requests import CREATED_BY_SELF, AccountRequest, AccountRequestEvent
from sam.core.users import User
from sam.notify import Message, Recipient
from sam.projects.projects import Project

from .xras_accounts import sam_merge_targets

#: kind -> subject. In Python, not the template: it is also the searchable
#: ``notification_log.subject`` column.
ACCOUNT_KIND_SUBJECTS = {
    'account_queue_summary': 'NCAR HPC account requests: {total} waiting, {new} new',
    'account_verify': 'Verify your email address for your NCAR HPC account request',
}


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


def group_by_event(rows: Sequence[AccountRequest],
                   events: Dict[int, AccountRequestEvent]) -> List[Dict[str, Any]]:
    """Event groups nearest deadline first, then the ungrouped rows oldest first.

    Each group is ``{'event': AccountRequestEvent | None, 'rows': [...]}``. A
    row naming an event that no longer exists is ungrouped rather than lost.
    """
    grouped: Dict[Optional[int], List[AccountRequest]] = {}
    for row in rows:
        key = row.event_id if row.event_id in events else None
        grouped.setdefault(key, []).append(row)
    groups = [{'event': events[key], 'rows': members}
              for key, members in grouped.items() if key is not None]
    groups.sort(key=lambda g: (g['event'].accounts_needed_by,
                               g['event'].event_code))
    if None in grouped:
        loose = sorted(grouped[None], key=lambda r: r.creation_time)
        groups.append({'event': None, 'rows': loose})
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
                    AccountRequest.state.in_(('submitted', 'claimed')))
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


# -- message builders ----------------------------------------------------------
#
# Both consumers of each kind (the admin button and the task; the public form
# and any resend) must mint the same dedup key, so each is built in one place.

def queue_summary_context(session: Session, rows: Sequence[AccountRequest], *,
                          occurrence: datetime, queue_url: str = '') -> Dict[str, Any]:
    """The digest payload: two flat lists, event groups first then the rest."""
    events = events_for(session, rows)
    groups = group_by_event(rows, events)
    sponsor_ids = sorted({r.sponsor_user_id for r in rows if r.sponsor_user_id})
    sponsors = {u.user_id: u for u in
                session.query(User).filter(User.user_id.in_(sponsor_ids)).all()} \
        if sponsor_ids else {}
    project_ids = sorted({r.project_id for r in rows if r.project_id}
                         | {e.project_id for e in events.values()})
    projects = dict(session.query(Project.project_id, Project.projcode)
                    .filter(Project.project_id.in_(project_ids)).all()) \
        if project_ids else {}
    today = occurrence.date()

    def _row(r: AccountRequest, event_code: str) -> Dict[str, Any]:
        sponsor = sponsors.get(r.sponsor_user_id)
        note = (r.comment or r.purpose_note or '').strip().splitlines()
        return {
            'name': r.display_name,
            'email': r.email,
            'organization': r.organization or '',
            'desired_username': r.desired_username or '',
            'purpose': r.purpose,
            'project_code': projects.get(r.project_id, '') if r.project_id else '',
            'event_code': event_code,
            'sponsor': sponsor.display_name if sponsor else '',
            'waiting_days': waiting_days(r, today=today),
            'state': r.state,
            'assignee': r.assignee or '',
            'note': note[0] if note else '',
        }

    out_events, out_rows = [], []
    for group in groups:
        event = group['event']
        code = event.event_code if event else ''
        if event:
            out_events.append({
                'event_code': code,
                'event_name': event.name,
                'project_code': projects.get(event.project_id, ''),
                'deadline': event.accounts_needed_by.isoformat(),
                'count': len(group['rows']),
            })
        out_rows.extend(_row(r, code) for r in group['rows'])
    by_purpose: Dict[str, int] = {}
    for r in rows:
        by_purpose[r.purpose] = by_purpose.get(r.purpose, 0) + 1
    return {
        'occurrence': occurrence.isoformat(timespec='seconds'),
        'total': len(rows),
        'new_count': sum(1 for r in rows if r.requested_at is None),
        'waiting_count': sum(1 for r in rows if r.requested_at is not None),
        'oldest_days': max((waiting_days(r, today=today) for r in rows), default=0),
        'by_purpose': [{'purpose': p, 'count': by_purpose[p]} for p in sorted(by_purpose)],
        'events': out_events,
        'rows': out_rows,
        'queue_url': queue_url,
    }


def build_queue_summary(session: Session, rows: Sequence[AccountRequest], *,
                        recipient: str, occurrence: datetime, requested_by: str,
                        queue_url: str = '') -> Message:
    """One digest to the configured NUSD address, keyed on the day so the
    button and the weekly task cannot both send it."""
    context = queue_summary_context(session, rows, occurrence=occurrence,
                                    queue_url=queue_url)
    address = recipient.strip()
    return Message(
        kind='account_queue_summary',
        recipient=Recipient(address, name='NUSD', role='operator'),
        subject=ACCOUNT_KIND_SUBJECTS['account_queue_summary'].format(
            total=context['total'], new=context['new_count']),
        context=context,
        dedup_key=f'account_queue_summary:{occurrence.date().isoformat()}:{address}',
        requested_by=requested_by,
    )


def build_verify_message(row: AccountRequest, *, verify_url: str, code: str,
                         expires_hours: int, event_name: Optional[str] = None,
                         requested_by: str = CREATED_BY_SELF) -> Message:
    """The verification mail. Its context carries NOTHING the visitor typed
    -- not even a name -- so SAM cannot be used as a relay with a UCAR
    return address. The key changes with every issue, so a resend is not
    suppressed by the last one."""
    issued = (row.verify_expires_at.isoformat(timespec='seconds')
              if row.verify_expires_at else 'unissued')
    return Message(
        kind='account_verify',
        recipient=Recipient(row.email, role='user'),
        subject=ACCOUNT_KIND_SUBJECTS['account_verify'],
        context={
            'verify_url': verify_url,
            'code': code,
            'expires_hours': expires_hours,
            'event_name': event_name or '',
        },
        entity=('account_request', row.account_request_id),
        dedup_key=f'account_verify:{row.account_request_id}:{issued}',
        requested_by=requested_by,
    )
