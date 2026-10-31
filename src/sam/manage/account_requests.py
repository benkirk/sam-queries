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
    OPEN_STATES,
    PURPOSE_ENROLLMENT,
    PURPOSE_REACTIVATION,
    PURPOSE_STANDALONE,
    PURPOSE_SUBMISSION,
    AccountRequest,
    AccountRequestEvent,
    EventEnrollment,
)
from sam.core.users import User
from sam.queries.account_requests import (queue_requests, requests_by_xras_username,
                                          resolve_requests)
from sam.queries.users import users_by_username_ci
from sam.queries.xras_actions import project_ids_for_actions
from sam.queries.xras_accounts import (CLASSIFICATION_ABSENT, CLASSIFICATION_INACTIVE,
                                       sam_merge_targets)

from . import add_user_to_project


OUTCOME_ADDED = 'added'          # SAM already knew the address: member now
OUTCOME_QUEUED = 'queued'        # a new row for NUSD
OUTCOME_DUPLICATE = 'duplicate'  # an open row for this address + project exists
OUTCOME_AMBIGUOUS = 'ambiguous'  # two active holders: invite_user refuses


def _open_request_for(session: Session, email: str,
                      project_id: Optional[int]) -> Optional[AccountRequest]:
    query = session.query(AccountRequest).filter(
        AccountRequest.email == email.strip().lower(),
        AccountRequest.is_open,
        AccountRequest.user_id.is_(None))
    if project_id is not None:
        query = query.filter(AccountRequest.project_id == project_id)
    return query.first()


def invite_outcomes(session: Session, project_id: Optional[int],
                    emails: Iterable[str]) -> Dict[str, Tuple[str, Optional[str]]]:
    """``{email: (outcome, username)}``: what :func:`invite_user` would do, read-only.

    ``username`` is the active holder for ``added`` and the inactive holder
    (named in the queued row's comment) for ``queued``. Two batched queries.
    """
    addresses = list(dict.fromkeys(e.strip().lower() for e in emails if e))
    if not addresses:
        return {}
    targets = sam_merge_targets(session, addresses)
    query = session.query(AccountRequest.email).filter(
        AccountRequest.email.in_(addresses), AccountRequest.is_open,
        AccountRequest.user_id.is_(None))
    if project_id is not None:
        query = query.filter(AccountRequest.project_id == project_id)
    waiting = {email.lower() for (email,) in query.all()}
    out: Dict[str, Tuple[str, Optional[str]]] = {}
    for address in addresses:
        target = targets.get(address)
        if target and target.get('ambiguous'):
            out[address] = (OUTCOME_AMBIGUOUS, None)
        elif target and target['active']:
            out[address] = (OUTCOME_ADDED, target['username'])
        elif address in waiting:
            out[address] = (OUTCOME_DUPLICATE, None)
        else:
            out[address] = (OUTCOME_QUEUED, target['username'] if target else None)
    return out


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
    outcome, username = invite_outcomes(session, project_id, [address])[address]
    if outcome == OUTCOME_AMBIGUOUS:
        raise ValueError(
            f'{address} belongs to more than one active SAM user; add the '
            f'member by username instead')
    if outcome == OUTCOME_ADDED:
        user = User.get_by_username(session, username)
        if event is not None:
            enroll_user_in_event(session, event=event, user=user, source=source,
                                 by=sponsor.username, clock=clock)
        else:
            add_user_to_project(session, project_id, user.user_id)
        return OUTCOME_ADDED, user
    if outcome == OUTCOME_DUPLICATE:
        return OUTCOME_DUPLICATE, _open_request_for(session, address, project_id)
    comment = note
    if username:
        held = f'SAM holds this address on inactive account {username}'
        comment = f'{note}\n{held}' if note else held
    row = AccountRequest.create(
        session,
        email=address, first_name=first_name, last_name=last_name,
        purpose=PURPOSE_ENROLLMENT, project_id=project_id,
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


#: What the sweep files for each worklist classification.
_PURPOSE_BY_CLASSIFICATION = {CLASSIFICATION_ABSENT: PURPOSE_SUBMISSION,
                              CLASSIFICATION_INACTIVE: PURPOSE_REACTIVATION}


def _blocked_purposes(req: AccountRequest) -> Tuple[str, ...]:
    """What *req* keeps the sweep from filing again for its username.

    An open row is the work already, whatever its purpose: a submission whose
    account arrives inactive must not gain a reactivation beside it. A
    dismissal or rejection survives the next sweep for its own purpose only.
    A fulfilled row blocks nothing: the person may lapse and be filed again.
    """
    if req.is_fulfilled:
        return ()
    if req.state in ('dismissed', 'rejected'):
        return (req.purpose,)
    return tuple(_PURPOSE_BY_CLASSIFICATION.values())


def upsert_sweep_requests(session: Session, worklist_rows: Sequence[Dict[str, Any]],
                          *, clock=None) -> Dict[str, Any]:
    """Give every absent or inactive worklist member a row. Idempotent.

    ``absent`` files a ``submission``, ``inactive`` a ``reactivation``. Keyed
    on (XRAS username, purpose): an open row blocks either purpose, a dismissed
    or rejected one its own (``_blocked_purposes``). An absent person's email
    comes from XRAS; a row without one cannot be matched later, so it is
    skipped and counted rather than created blind. An inactive person's detail
    comes from their SAM row. An absent person whose email one active SAM user
    holds files nothing (``already_in_sam``): reconcile would fulfill the row on
    its next pass, and a fulfilled row blocks nothing, so the sweep would file it
    again every run.
    """
    counts: Dict[str, Any] = {'created': 0, 'existing': 0, 'skipped': 0,
                              'already_in_sam': 0, 'projects_attached': 0, 'by_purpose': {}}
    wanted = [r for r in worklist_rows
              if r.get('classification') in _PURPOSE_BY_CLASSIFICATION and r.get('username')]
    if not wanted:
        return counts
    existing = requests_by_xras_username(session, (r['username'] for r in wanted))
    known = {(key, purpose) for key, reqs in existing.items()
             for req in reqs for purpose in _blocked_purposes(req)}
    sam_users = users_by_username_ci(
        session, (r['username'] for r in wanted
                  if r['classification'] == CLASSIFICATION_INACTIVE))
    projects = _projects_by_row(session, wanted)
    held = sam_merge_targets(session, (
        (r.get('person') or {}).get('email') or '' for r in wanted
        if r['classification'] == CLASSIFICATION_ABSENT))
    for row in wanted:
        purpose = _PURPOSE_BY_CLASSIFICATION[row['classification']]
        if row['classification'] == CLASSIFICATION_ABSENT:
            email = ((row.get('person') or {}).get('email') or '').strip().lower()
            if (held.get(email) or {}).get('active'):
                counts['already_in_sam'] += 1
                continue
        key = AccountRequest.xras_key(row['username'])
        project_id = projects[id(row)]
        if (key, purpose) in known:
            counts['existing'] += 1
            # The project lands after the row: attach it once the handoff has, to
            # any open row (it blocked this purpose too; see _blocked_purposes).
            for req in existing.get(key, ()):
                if (project_id and req.project_id is None
                        and req.is_open and not req.is_fulfilled):
                    req.project_id = project_id
                    counts['projects_attached'] += 1
            continue
        if purpose == PURPOSE_REACTIVATION:
            fields = _person_from_user(sam_users.get(row['username'].casefold()))
        else:
            fields = _person_from_xras(row.get('person') or {})
        if fields is None:
            counts['skipped'] += 1
            continue
        AccountRequest.create(
            session, **fields, purpose=purpose, project_id=project_id,
            xras_username=row['username'],
            created_by=CREATED_BY_SWEEP, verified_by=CREATED_BY_SWEEP,
            clock=clock,
        )
        known.add((key, purpose))
        counts['created'] += 1
        counts['by_purpose'][purpose] = counts['by_purpose'].get(purpose, 0) + 1
    if counts['projects_attached']:
        session.flush()
    return counts


def _projects_by_row(session: Session, rows: Sequence[Dict[str, Any]]) -> Dict[int, Optional[int]]:
    """``{id(row): project_id}``: the first of a row's actions that names a project."""
    actions = [(id(row), a) for row in rows for a in row.get('actions') or ()]
    found = project_ids_for_actions(session, [a for _, a in actions])
    out: Dict[int, Optional[int]] = {id(row): None for row in rows}
    for (key, _), project_id in zip(actions, found):
        if out[key] is None:
            out[key] = project_id
    return out


def _person_from_xras(person: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    email = (person.get('email') or '').strip().lower()
    first = (person.get('firstName') or '').strip()
    last = (person.get('lastName') or '').strip()
    if not email or not first or not last:
        return None
    return {'email': email, 'first_name': first, 'last_name': last,
            'middle_name': person.get('middleName'),
            'organization': person.get('organization'),
            'academic_status': person.get('academicStatus'),
            'residence_country': person.get('residenceCountry'),
            'orcid': person.get('orcid'), 'phone': person.get('phone')}


def _person_from_user(user: Optional[User]) -> Optional[Dict[str, Any]]:
    email = ((user.primary_email or '') if user else '').strip().lower()
    if not email or not user.first_name or not user.last_name:
        return None
    return {'email': email, 'first_name': user.first_name, 'last_name': user.last_name,
            'middle_name': user.middle_name}


def refused_visitor_registration(session: Session, request_id: Optional[int],
                                 messages: Sequence[str]) -> Optional[AccountRequest]:
    """The ``/request`` registration whose New a refused post refused for its SAM-minted PI.

    That refusal is the approval: XRAS posts only an approved request, and the PI has
    no account yet. ``None`` for any other refusal. Reads only.
    """
    from sam.integration.xras_submission import XrasSubmission
    from sam.xras.errors import pi_not_in_database
    if request_id is None:
        return None
    for sub in session.query(XrasSubmission).filter(
            XrasSubmission.request_id == request_id, XrasSubmission.action_type == 'New',
            XrasSubmission.registration_id.isnot(None)):
        if pi_not_in_database(sub.xa_user) in messages:
            row = session.get(AccountRequest, sub.registration_id)
            if row is not None and row.state in OPEN_STATES:
                return row
    return None


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
        if row.purpose == PURPOSE_ENROLLMENT and row.project_id:
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
                     purpose: Optional[str] = None, **person) -> AccountRequest:
    """A self-registration: ``created_by='self'``, unverified, and therefore
    invisible to the queue until the mailed link or code confirms the address.
    An event makes it an ``enrollment`` on the event's project; ``purpose='submission'``
    is a visitor filing a New (``/request``)."""
    return AccountRequest.create(
        session,
        email=email, first_name=first_name, last_name=last_name,
        purpose=purpose or (PURPOSE_ENROLLMENT if event else PURPOSE_STANDALONE),
        project_id=event.project_id if event else None,
        event_id=event.account_request_event_id if event else None,
        created_by=CREATED_BY_SELF, verified_by=None, clock=clock, **person,
    )
