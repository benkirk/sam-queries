"""Write side of the HPC account-request queue.

Everything that creates or advances a request lives here; the routes and
tasks call these inside ``management_transaction``. The read side is
:mod:`sam.queries.account_requests`. Design: docs/plans/implemented/ACCOUNT_REGISTRATION.md.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from sam.core.account_requests import (
    CREATED_BY_SELF,
    CREATED_BY_SWEEP,
    AccountRequest,
    AccountRequestEvent,
    EventEnrollment,
)
from sam.core.users import User
from sam.projects.projects import Project
from sam.queries.account_requests import queue_requests, resolve_requests
from sam.queries.xras_accounts import CLASSIFICATION_ABSENT, sam_merge_targets

from . import add_user_to_project


OUTCOME_ADDED = 'added'          # SAM already knew the address: member now
OUTCOME_QUEUED = 'queued'        # a new row for NUSD
OUTCOME_DUPLICATE = 'duplicate'  # an open row for this address + project exists


def _open_request_for(session: Session, email: str,
                      project_id: Optional[int]) -> Optional[AccountRequest]:
    query = session.query(AccountRequest).filter(
        AccountRequest.email == email.strip().lower(),
        AccountRequest.is_open,
        AccountRequest.user_id.is_(None))
    if project_id is not None:
        query = query.filter(AccountRequest.project_id == project_id)
    return query.first()


def enroll_user_in_event(session: Session, *, event: AccountRequestEvent,
                         user: User, source: str, by: str, clock=None
                         ) -> EventEnrollment:
    """Add a known user to an event's project and record the enrollment.

    The membership write and the ledger row are kept together so they cannot
    drift; both are idempotent for a live member. Raises ``ValueError`` from
    ``add_user_to_project`` when the project has no accounts.
    """
    add_user_to_project(session, event.project_id, user.user_id)
    return EventEnrollment.upsert(session, event=event, user=user,
                                  source=source, by=by, clock=clock)


def invite_user(session: Session, *, project_id: int, sponsor: User, email: str,
                first_name: str, last_name: str, note: Optional[str] = None,
                event: Optional[AccountRequestEvent] = None, source: str = 'invite',
                clock=None, **person) -> Tuple[str, Any]:
    """Invite one person onto a project; returns ``(outcome, User | AccountRequest)``.

    An address SAM already holds on an active account skips the queue and
    becomes a membership at once (recorded as an event enrollment when an event
    is given). An inactive holder is queued with the account named in the
    comment -- reactivation is NUSD's. Two active holders is a ``ValueError``:
    the sponsor should pick the user by name.
    """
    address = email.strip().lower()
    target = sam_merge_targets(session, [address]).get(address)
    if target and target.get('ambiguous'):
        raise ValueError(
            f'{address} belongs to more than one active SAM user; add the '
            f'member by username instead')
    if target and target['active']:
        user = User.get_by_username(session, target['username'])
        if event is not None:
            enroll_user_in_event(session, event=event, user=user, source=source,
                                 by=sponsor.username, clock=clock)
        else:
            add_user_to_project(session, project_id, user.user_id)
        return OUTCOME_ADDED, user
    existing = _open_request_for(session, address, project_id)
    if existing is not None:
        return OUTCOME_DUPLICATE, existing
    comment = note
    if target:
        held = f'SAM holds this address on inactive account {target["username"]}'
        comment = f'{note}\n{held}' if note else held
    row = AccountRequest.create(
        session,
        email=address, first_name=first_name, last_name=last_name,
        purpose='enrollment', project_id=project_id,
        sponsor_user_id=sponsor.user_id,
        event_id=event.account_request_event_id if event else None,
        comment=comment, created_by=sponsor.username,
        verified_by=sponsor.username, clock=clock, **person,
    )
    return OUTCOME_QUEUED, row


#: ``First Last <email>``, ``Last, First <email>``, or ``First Last email``.
_ANGLE_RE = re.compile(r'^(?P<name>.*?)\s*<(?P<email>[^<>\s]+@[^<>\s]+)>\s*$')
_TRAILING_RE = re.compile(r'^(?P<name>.+?)\s+(?P<email>[^\s<>]+@[^\s<>]+)\s*$')


def _split_name(name: str) -> Tuple[str, str]:
    """``Last, First`` or ``First [Middle] Last``; a lone word is a last name."""
    name = name.strip().strip('"')
    if ',' in name:
        last, first = (p.strip() for p in name.split(',', 1))
        return first, last
    parts = name.split()
    if len(parts) == 1:
        return '', parts[0]
    return ' '.join(parts[:-1]), parts[-1]


def parse_roster(text: str) -> Tuple[List[Dict[str, str]], List[str]]:
    """One person per line; ``#`` and blank lines ignored.

    Returns ``(entries, errors)`` where an error names its line. A bare
    address is an error -- a request needs a name, and guessing one from
    the local part would put a guess in front of NUSD.
    """
    entries: List[Dict[str, str]] = []
    errors: List[str] = []
    seen: Dict[str, int] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        match = _ANGLE_RE.match(line) or _TRAILING_RE.match(line)
        if not match:
            errors.append(f'line {number}: expected "Name <email>"')
            continue
        first, last = _split_name(match.group('name'))
        if not first or not last:
            errors.append(f'line {number}: a first and last name are required')
            continue
        email = match.group('email').lower()
        if email in seen:
            errors.append(f'line {number}: duplicate of line {seen[email]}')
            continue
        seen[email] = number
        entries.append({'email': email, 'first_name': first, 'last_name': last})
    return entries, errors


def paste_roster(session: Session, *, event: AccountRequestEvent, sponsor: User,
                 entries: Sequence[Dict[str, str]], clock=None) -> Dict[str, list]:
    """Invite every parsed entry under an event; returns outcomes by name."""
    outcomes: Dict[str, list] = {OUTCOME_ADDED: [], OUTCOME_QUEUED: [],
                                 OUTCOME_DUPLICATE: [], 'error': []}
    for entry in entries:
        try:
            outcome, _ = invite_user(
                session, project_id=event.project_id, sponsor=sponsor,
                event=event, source='roster', clock=clock, **entry)
        except ValueError as exc:
            outcomes['error'].append((entry['email'], str(exc)))
            continue
        outcomes[outcome].append(entry['email'])
    return outcomes


def upsert_sweep_requests(session: Session, worklist_rows: Sequence[Dict[str, Any]],
                          *, clock=None) -> Dict[str, int]:
    """Give every absent roster member a ``submission`` row. Idempotent.

    Keyed on the XRAS username, casefolded. An existing row in any state is
    left alone -- a dismissal must survive the next sweep. ``inactive`` rows
    are not taken: reactivation is a purpose this queue does not have yet.
    A row without a person email cannot be matched later, so it is skipped
    and counted rather than created blind.
    """
    counts = {'created': 0, 'existing': 0, 'skipped': 0}
    absent = [r for r in worklist_rows
              if r.get('classification') == CLASSIFICATION_ABSENT and r.get('username')]
    if not absent:
        return counts
    keys = sorted({r['username'].lower() for r in absent})
    known = {req.xras_username for req in
             session.query(AccountRequest.xras_username)
             .filter(AccountRequest.xras_username.in_(keys)).all()}
    numbers = sorted({a.get('request_number') for r in absent
                      for a in r.get('actions', ()) if a.get('request_number')})
    projects = {}
    if numbers:
        projects = dict(session.query(Project.projcode, Project.project_id)
                        .filter(Project.projcode.in_(numbers)).all())
    for row in absent:
        key = row['username'].lower()
        if key in known:
            counts['existing'] += 1
            continue
        person = row.get('person') or {}
        email = (person.get('email') or '').strip().lower()
        first = (person.get('firstName') or '').strip()
        last = (person.get('lastName') or '').strip()
        if not email or not first or not last:
            counts['skipped'] += 1
            continue
        project_id = next((projects[a['request_number']]
                           for a in row.get('actions', ())
                           if a.get('request_number') in projects), None)
        AccountRequest.create(
            session,
            email=email, first_name=first, last_name=last,
            middle_name=person.get('middleName'),
            organization=person.get('organization'),
            academic_status=person.get('academicStatus'),
            residence_country=person.get('residenceCountry'),
            orcid=person.get('orcid'), phone=person.get('phone'),
            purpose='submission', project_id=project_id,
            xras_username=row['username'],
            created_by=CREATED_BY_SWEEP, verified_by=CREATED_BY_SWEEP,
            clock=clock,
        )
        known.add(key)
        counts['created'] += 1
    return counts


def reconcile_account_requests(session: Session, *, clock=None,
                               purge_unverified_days: Optional[int] = None
                               ) -> Dict[str, int]:
    """Act on what the mirror now says. The ONE place fulfillment is stamped.

    For each queue row whose email resolves to exactly one active user:
    stamp ``user_id``/``upid``, and for an ``enrollment`` add the membership
    inside a savepoint so one project without accounts cannot fail the pass.
    A failed enrollment is recorded on the row, which then stays in the queue
    with the reason and is retried next run (``add_user_to_project`` is
    idempotent for a live member). Unverified public rows older than the
    horizon are purged. Never raises past a row.
    """
    now = clock or datetime.now()
    counts = {'checked': 0, 'fulfilled': 0, 'enrolled': 0, 'enroll_failed': 0,
              'ambiguous': 0, 'purged': 0}
    rows = queue_requests(session)
    counts['checked'] = len(rows)
    resolutions = resolve_requests(session, rows)
    for row in rows:
        resolution = resolutions.get(row.account_request_id)
        if resolution is None:
            continue
        if resolution.ambiguous:
            counts['ambiguous'] += 1
            continue
        if not resolution.ready:
            continue
        user = session.get(User, resolution.user_id)
        if row.user_id is None:
            row.fulfill(user, when=now)
            counts['fulfilled'] += 1
        if row.purpose == 'enrollment' and row.project_id:
            try:
                with session.begin_nested():
                    event = (session.get(AccountRequestEvent, row.event_id)
                             if row.event_id else None)
                    if event is not None:
                        enroll_user_in_event(session, event=event, user=user,
                                             source='reconcile', by=row.created_by)
                    else:
                        add_user_to_project(session, row.project_id, user.user_id)
            except ValueError as exc:
                row.record_fulfill_error(str(exc))
                counts['enroll_failed'] += 1
                continue
            if row.fulfill_error:
                row.record_fulfill_error(None)
            counts['enrolled'] += 1
    if purge_unverified_days:
        cutoff = now - timedelta(days=purge_unverified_days)
        stale = (session.query(AccountRequest)
                 .filter(AccountRequest.created_by == CREATED_BY_SELF,
                         AccountRequest.verified_at.is_(None),
                         AccountRequest.creation_time < cutoff).all())
        for row in stale:
            session.delete(row)
        counts['purged'] = len(stale)
        session.flush()
    return counts


def register_request(session: Session, *, email: str, first_name: str, last_name: str,
                     event: Optional[AccountRequestEvent] = None, clock=None,
                     **person) -> AccountRequest:
    """A self-registration: ``created_by='self'``, unverified, and therefore
    invisible to the queue until the mailed link or code confirms the address.
    An event makes it an ``enrollment`` on the event's project."""
    return AccountRequest.create(
        session,
        email=email, first_name=first_name, last_name=last_name,
        purpose='enrollment' if event else 'standalone',
        project_id=event.project_id if event else None,
        event_id=event.account_request_event_id if event else None,
        created_by=CREATED_BY_SELF, verified_by=None, clock=clock, **person,
    )
