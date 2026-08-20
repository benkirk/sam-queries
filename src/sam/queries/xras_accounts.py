"""The XRAS account-creation worklist — who must exist in SAM before a handoff works.

The problem
-----------
``src/sam/xras/handlers/new.py:24-27`` records the measured causes of the legacy
70% failure rate, and the largest single one — **55%** — is unreconciled ARC
placeholder identities: a researcher XRAS names on a request who has no SAM
account. Account creation is manual, so the fix is not code that creates
accounts; it is a worklist telling an operator *who*, *why*, and *with what
detail*.

Two feeds, one classifier
-------------------------
::

    Feed A  xras_action_log.raw_payload   ──┐
            (inbound pushes, at push time)  │   normalized        classify vs
                                            ├── RosterRecord ──►  users  ──► rows
    Feed B  GET /v1/reports/requests        │   (feed-neutral)   absent/inactive
            (enumeration, ahead of push)  ──┘

The feed-agnostic seam is :class:`RosterRecord`. It is the one decision that
keeps the classifier, the card, the CLI and the eventual operator-notes table
single-sourced — Feed B reaches people Feed A structurally cannot (a brand-new
PI on a solo New request, connected to nobody SAM knows, *before* the push),
and neither feed should imply a second copy of the classification rules.

⚠️ Not exported from ``sam/queries/__init__.py``
------------------------------------------------
That module imports its submodules eagerly, so listing this one would drag
``requests`` and the cache layer into every ``from sam.queries import ...``.
Same reasoning as ``expiration_notices`` and ``xras_notices``; import it by
module path. For the same reason the default person lookup is resolved by a
**deferred import** inside :func:`enrich_worklist` rather than at module scope —
``src/scheduling/`` imports this module, and
``test_task_ledger.py::TestPortabilityBoundary`` walks what that drags in.

⚠️ Regime-proof by construction
--------------------------------
Classification is a check against the **current** state of ``users``, never
against the action's ``status``. Actions in the log may be ``received`` (under
capture-only) or ``processed``/``failed``/``manual`` (under live dispatch), and
the worklist must mean the same thing on both sides of that flip.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import (Any, Callable, Dict, Iterable, List, Mapping, Optional,
                    Sequence, Tuple)

from sqlalchemy.orm import Session

from sam.core.users import User
from sam.integration.xras import XrasActionLog
from sam.queries.xras_actions import XRAS_ACTION_STATUSES

logger = logging.getLogger(__name__)

#: ⚠️ **Every status, deliberately — do not narrow this to the "interesting"
#: ones.** An earlier version read only ``received``/``failed``/``manual`` on
#: the reasoning that a ``processed`` action had already succeeded, so its
#: roster must have resolved.
#:
#: That reasoning is wrong, and the local smoke measured it: seeding 41 real
#: payloads under live dispatch left 28 ``processed``, and excluding them hid
#: **5 usernames SAM cannot use — four inactive, one absent entirely, most of
#: them Allocation Managers.** An action processes fine while naming such a
#: person, because ``resolve_roster`` reports a missing or inactive *member*
#: as a warning rather than an error; the handler simply skips assigning them.
#: The account is still needed, and the operator still has to create it.
#:
#: Worse, the narrow list made the worklist **regime-dependent** — the exact
#: property § 1 of the design says it must not have. The same corpus produced
#: 9 rows under capture-only (all ``received``) and 4 under live dispatch.
#: Classification keys off the ``users`` table alone; this must not
#: reintroduce a status dependency behind it.
WORKLIST_STATUSES: Tuple[str, ...] = XRAS_ACTION_STATUSES

#: Which actions are worth running the dispatch pre-flight over. **This** is
#: the bounded-work knob the status list used to be conflated with: validating
#: a ``processed`` action re-runs a handler's whole assembly to learn something
#: already known, and ``rechecked``/``unmapped`` are not an action's own
#: outcome. Narrowing this costs provenance on those rows, never a
#: classification.
VALIDATE_STATUSES: Tuple[str, ...] = ('received', 'failed', 'manual')

#: ARC placeholder identities, e.g. ``placeholder34-user-00034``. XRAS mints one
#: for every unreconciled person, and it resolves fully through
#: ``GET /v1/people/<username>``, which is what makes the detail sheet possible.
PLACEHOLDER_USERNAME_RE = re.compile(r'^\S+-user-\S+$')

#: Wire role strings, spaced — the *inbound* vocabulary. Verified live: the
#: NCAR process defines exactly these three role types (13 PI / 14 Allocation
#: Manager / 19 User), so no co-PI can ever appear. Deliberately NOT the
#: ``Pi``/``CoPi``/``AllocationManager`` vocabulary SAM's own outbound GET side
#: uses (``webapp/api/xras/requests.py:33-37``); the two must not be conflated.
PI_ROLE = 'PI'
ALLOCATION_MANAGER_ROLE = 'Allocation Manager'
USER_ROLE = 'User'

#: Ranked for display: the strongest role a username holds leads the row.
_ROLE_ORDER = (PI_ROLE, ALLOCATION_MANAGER_ROLE, USER_ROLE)

#: PII. Assembled only for a viewer holding ``MANAGE_XRAS`` — see the card.
PERSON_FIELDS = ('firstName', 'middleName', 'lastName', 'email', 'phone',
                 'organization', 'academicStatus', 'residenceCountry',
                 'isReconciled', 'orcid')


CLASSIFICATION_ABSENT = 'absent'
CLASSIFICATION_INACTIVE = 'inactive'

#: What an operator has to *do*, which is not the same question as why the row
#: is here — the two remedies are different pieces of work.
REMEDIES = {CLASSIFICATION_ABSENT: 'create', CLASSIFICATION_INACTIVE: 'reactivate'}


@dataclass(frozen=True)
class ActionRef:
    """Provenance for one roster — feed-neutral.

    ``action_log_id`` is ``None`` for a Feed-B record: an enumerated request
    has no row in ``xras_action_log`` precisely because it has not been pushed
    yet, which is the whole point of the second feed.
    """

    action_log_id: Optional[int] = None
    request_number: Optional[str] = None
    action_type: Optional[str] = None
    status: Optional[str] = None
    received_time: Optional[datetime] = None
    #: When the request appeared in XRAS (``submitDate``), for a feed that has
    #: no arrival of its own. Feed A leaves it ``None`` and uses
    #: ``received_time`` — when XRAS pushed the action to us.
    #:
    #: These two are the SAME QUESTION per feed — "when did this show up?" —
    #: which is what lets one window control span both. Filtering Feed B on
    #: its period of performance instead was tried and is wrong: a pending
    #: request's allocation almost always ends a year out, so a one-sided
    #: window keeps every row at every width and the control looks dead. The
    #: period of performance belongs where it already is, bounding what the
    #: sweep collects.
    submit_date: Optional[str] = None
    source: str = 'action_log'
    #: ``dispatch_action(validate_only=True)``'s verdict. ``None`` = not run.
    would_succeed: Optional[bool] = None
    #: Verbatim, **display-only**. These are a byte-pinned wire contract
    #: (``sam/xras/errors.py``), not an interface — never parse them.
    reject_messages: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RosterRecord:
    """One action's roster, normalized. The seam both feeds construct."""

    ref: ActionRef
    usernames: Tuple[str, ...] = ()
    roles_by_username: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    #: ``isAccountToBeCreated`` — a **hint column only**. See
    #: :func:`classify_accounts` for why it is never the predicate.
    account_flag: Mapping[str, bool] = field(default_factory=dict)
    #: Inline person detail, when the feed carried it (Feed B always does).
    person_by_username: Mapping[str, dict] = field(default_factory=dict)


def is_placeholder(username: str) -> bool:
    """True for an ARC placeholder identity."""
    return bool(username) and bool(PLACEHOLDER_USERNAME_RE.match(username))


# ── Feed A: the inbound action log ──────────────────────────────────────

def _roster_from_action(action) -> Tuple[Tuple[str, ...],
                                         Dict[str, List[str]],
                                         Dict[str, bool]]:
    """Usernames and roles from a loaded action, via the structured helpers.

    Uses ``sam.xras.roster`` rather than parsing the error strings in
    ``sam/xras/errors.py``: those are a wire contract XRAS administrators read
    and grep, not an interface for us to scrape.

    The union of the roster and the two role-candidate lists is deliberate.
    ``role_candidates`` applies a *looser* begin-date rule than
    ``roster_usernames`` (that gap is legacy defect 3), so a PI can resolve
    while being absent from the roster — and the handoff still fails on a
    missing PI. Taking only the roster would miss exactly those.
    """
    from sam.xras.roster import (ALLOCATION_MANAGER_ROLE as _AM,
                                 PI_ROLE as _PI, normalize_username,
                                 role_candidates, roster_usernames)
    from sam.xras.wire import get_field

    members = roster_usernames(action)
    pis = role_candidates(action, _PI)
    admins = role_candidates(action, _AM)

    roles: Dict[str, List[str]] = {}
    for username in pis:
        roles.setdefault(username, []).append(PI_ROLE)
    for username in admins:
        roles.setdefault(username, []).append(ALLOCATION_MANAGER_ROLE)
    for username in members:
        roles.setdefault(username, [])

    ordered: List[str] = []
    for username in (*members, *pis, *admins):
        if username and username not in ordered:
            ordered.append(username)

    # Anyone named with no stronger role is a plain member.
    for username in ordered:
        if not roles.get(username):
            roles[username] = [USER_ROLE]

    flags: Dict[str, bool] = {}
    for role in get_field(action, 'roles') or ():
        username = normalize_username(get_field(role, 'username'))
        if username and get_field(role, 'isAccountToBeCreated'):
            flags[username] = True

    return tuple(ordered), roles, flags


def records_from_action_log(session: Session, *,
                            statuses: Sequence[str] = WORKLIST_STATUSES,
                            since: Optional[datetime] = None,
                            until: Optional[datetime] = None,
                            validate: bool = True,
                            limit: Optional[int] = None
                            ) -> List[RosterRecord]:
    """Feed A — rosters from ``xras_action_log.raw_payload``.

    Parses through ``XrasActionSchema`` (``sam.schemas.forms``), the same path
    the webapp's ``_parse_action`` takes, with no webapp import.

    With *validate* on, each action **in :data:`VALIDATE_STATUSES`**
    additionally goes through ``dispatch_action(..., validate_only=True)``. That path structurally cannot
    write — ``management_transaction`` only opens in ``handlers/base.py``, past
    the seam — and its verdict is recorded as **provenance**, never as the
    classifier: it also catches non-account failures (an unresolvable mnemonic,
    a resource key with no mapping) which the card shows as "this action would
    fail for other reasons too".
    """
    from marshmallow import ValidationError

    from sam.schemas.forms import XrasActionSchema

    query = session.query(XrasActionLog)
    if statuses:
        query = query.filter(XrasActionLog.status.in_(tuple(statuses)))
    if since is not None:
        query = query.filter(XrasActionLog.received_time >= since)
    if until is not None:
        query = query.filter(XrasActionLog.received_time <= until)
    query = query.order_by(XrasActionLog.received_time.desc(),
                           XrasActionLog.xras_action_log_id.desc())
    if limit:
        query = query.limit(limit)

    schema = XrasActionSchema()
    records: List[RosterRecord] = []
    for row in query.all():
        try:
            action = schema.load(json.loads(row.raw_payload or '{}'))
        except (ValueError, ValidationError) as exc:
            # A body that would not parse has no roster to offer. It is
            # already visible on the action-log card as its own failure;
            # re-reporting it here would just be noise.
            logger.debug('xras worklist: action %s did not parse (%s)',
                         row.xras_action_log_id, exc)
            continue

        usernames, roles, flags = _roster_from_action(action)
        if not usernames:
            continue

        would_succeed, messages = None, ()
        if validate and row.status in VALIDATE_STATUSES:
            would_succeed, messages = _validate(session, action,
                                                row.xras_action_log_id)

        records.append(RosterRecord(
            ref=ActionRef(action_log_id=row.xras_action_log_id,
                          request_number=row.request_number,
                          action_type=row.action_type,
                          status=row.status,
                          received_time=row.received_time,
                          source='action_log',
                          would_succeed=would_succeed,
                          reject_messages=messages),
            usernames=usernames,
            roles_by_username={k: tuple(v) for k, v in roles.items()},
            account_flag=flags))
    return records


def _validate(session: Session, action, action_log_id
              ) -> Tuple[Optional[bool], Tuple[str, ...]]:
    """Run the dispatch pre-flight, catching its rejection."""
    from sam.xras.dispatch import dispatch_action
    from sam.xras.errors import XrasActionRejected

    try:
        dispatch_action(session, action, validate_only=True)
        return True, ()
    except XrasActionRejected as exc:
        return False, tuple(exc.messages)
    except Exception as exc:                     # noqa: BLE001
        # The worklist is a report. A handler blowing up on one action must
        # not take the whole card down — record "we could not tell".
        logger.warning('xras worklist: validate failed for action %s (%s)',
                       action_log_id, exc)
        return None, ()


# ── Feed B: the outbound enumeration ────────────────────────────────────

def records_from_report_requests(payloads: Iterable[dict]) -> List[RosterRecord]:
    """Feed B — rosters from ``GET /v1/reports/requests`` rows.

    The outgoing wire nests differently from the incoming one:
    ``roles[]`` entries are ``{person, roles[]}``, where the inner entries
    carry ``role`` (not ``roleType``). Verified live, along with the fact that
    the person object is **inline and complete** — every ``/v1/people`` field
    including ``isReconciled`` and ``residenceCountry`` — which is why a
    Feed-B row never needs a person fetch.
    """
    records: List[RosterRecord] = []
    for payload in payloads or ():
        if not isinstance(payload, dict):
            continue

        usernames: List[str] = []
        roles: Dict[str, List[str]] = {}
        flags: Dict[str, bool] = {}
        people: Dict[str, dict] = {}

        for entry in payload.get('roles') or ():
            if not isinstance(entry, dict):
                continue
            person = entry.get('person') if isinstance(entry.get('person'), dict) else {}
            username = str(person.get('username') or '').strip()
            if not username:
                continue
            if username not in usernames:
                usernames.append(username)
                people[username] = person
            for role in entry.get('roles') or ():
                if not isinstance(role, dict):
                    continue
                name = str(role.get('role') or '').strip()
                if name and name not in roles.setdefault(username, []):
                    roles[username].append(name)
                if role.get('isAccountToBeCreated'):
                    flags[username] = True
            if not roles.get(username):
                roles[username] = [USER_ROLE]

        if not usernames:
            continue

        records.append(RosterRecord(
            ref=ActionRef(action_log_id=None,
                          request_number=payload.get('requestNumber'),
                          action_type=payload.get('requestType'),
                          status=payload.get('requestStatus'),
                          received_time=None,
                          submit_date=(str(payload.get('submitDate'))[:10]
                                       if payload.get('submitDate') else None),
                          source='reports'),
            usernames=tuple(usernames),
            roles_by_username={k: tuple(v) for k, v in roles.items()},
            account_flag=flags,
            person_by_username=people))
    return records


# ── the classifier ──────────────────────────────────────────────────────

def _sorted_roles(names: Iterable[str]) -> Tuple[str, ...]:
    """Strongest first, then anything unrecognised, alphabetically."""
    unique = set(names)
    ranked = [r for r in _ROLE_ORDER if r in unique]
    return tuple(ranked + sorted(unique - set(_ROLE_ORDER)))


def classify_accounts(session: Session,
                      records: Sequence[RosterRecord]) -> List[Dict[str, Any]]:
    """Turn rosters into worklist rows. **The core.**

    Two classes, because they block a handoff identically but need different
    work:

    ``absent``    no ``users`` row at all            → create the account
    ``inactive``  a row that fails ``User.is_active``  → reactivate it

    A predicate that only checked existence would miss the second entirely —
    of 19 live roster usernames sampled during design, all 19 existed but
    **five were inactive**, a quarter of the real cases.

    ⚠️ ``isAccountToBeCreated`` is **never** the predicate. XRAS sets it when
    the role is created and never clears it, so live data shows active,
    present SAM users still carrying ``true``. It rides along as a hint column
    and nothing more.
    """
    wanted = {u for record in records for u in record.usernames if u}
    if not wanted:
        return []

    # One query for the whole batch, never per username.
    #
    # ⚠️ **Keyed case-INSENSITIVELY, and this is not cosmetic.**
    # ``users.username`` is ``utf8mb3_general_ci`` with a UNIQUE index, so
    # MySQL considers ``Jsmith`` and ``jsmith`` the same account and the
    # ``IN`` above matches either spelling. A case-sensitive Python dict
    # underneath that then MISSES the row it was just handed, and the
    # username is reported ``absent`` — telling an operator to create an
    # account that already exists and is active.
    #
    # The local smoke hit exactly this: XRAS sent ``Jsmith``, SAM holds
    # ``jsmith`` active, and the card said "Create". No in-tree fixture could
    # have caught it — the anonymizer emits lowercase ``user_<hex>`` for
    # every username, so every scrubbed roster is already case-matched.
    # ``roster.normalize_username`` deliberately does not fold case either
    # (it reproduces Java), so the wire spelling reaches here untouched.
    existing = {u.username.casefold(): u for u in
                session.query(User).filter(User.username.in_(sorted(wanted))).all()}

    rows: Dict[str, Dict[str, Any]] = {}
    for record in records:
        for username in record.usernames:
            if not username:
                continue
            key = username.casefold()
            user = existing.get(key)
            if user is not None and user.is_active:
                continue                    # nothing to do — the common case
            classification = (CLASSIFICATION_ABSENT if user is None
                              else CLASSIFICATION_INACTIVE)

            # Grouped on the same case-insensitive key, so two spellings of
            # one account are one row of work rather than two.
            row = rows.get(key)
            if row is None:
                row = rows[key] = {
                    # The wire spelling, kept as XRAS sent it — for an absent
                    # account it is the only spelling there is, and for a
                    # present one the mismatch is itself worth seeing.
                    'username': username,
                    'classification': classification,
                    'remedy': REMEDIES[classification],
                    'placeholder': is_placeholder(username),
                    'roles': (),
                    'is_account_to_be_created': False,
                    'actions': [],
                    'latest_action_log_id': None,
                    'first_seen': None,
                    'last_seen': None,
                    'person': None,
                    'is_reconciled': None,
                    'sources': set(),
                }

            row['roles'] = _sorted_roles(
                (*row['roles'], *record.roles_by_username.get(username, ())))
            row['is_account_to_be_created'] = (
                row['is_account_to_be_created']
                or bool(record.account_flag.get(username)))
            row['sources'].add(record.ref.source)

            person = record.person_by_username.get(username)
            if person and row['person'] is None:
                # Filtered to PERSON_FIELDS, exactly as `enrich_worklist` does
                # for Feed A. Two reasons beyond consistency: a raw XRAS person
                # carries fields we never declared (`hasOrcidToken`, a
                # duplicate `username`), and a Feed-B row is PERSISTED — the
                # sweep publishes it into the cache — so whatever lands here
                # outlives the request that made it.
                row['person'] = {k: person.get(k) for k in PERSON_FIELDS
                                 if k in person}
                if 'isReconciled' in person:
                    row['is_reconciled'] = bool(person['isReconciled'])

            row['actions'].append({
                'action_log_id': record.ref.action_log_id,
                'request_number': record.ref.request_number,
                'action_type': record.ref.action_type,
                'status': record.ref.status,
                'received_time': record.ref.received_time,
                'submit_date': record.ref.submit_date,
                'source': record.ref.source,
                'would_succeed': record.ref.would_succeed,
                'reject_messages': list(record.ref.reject_messages),
            })

    for row in rows.values():
        # Newest first; a Feed-B record has no timestamp, so it sorts last
        # rather than pretending to be old.
        row['actions'].sort(
            key=lambda a: (a['received_time'] is not None, a['received_time'],
                           a['action_log_id'] or 0),
            reverse=True)
        stamps = [a['received_time'] for a in row['actions'] if a['received_time']]
        row['first_seen'] = min(stamps) if stamps else None
        row['last_seen'] = max(stamps) if stamps else None
        # Deliberately the future notes-table FK target.
        row['latest_action_log_id'] = next(
            (a['action_log_id'] for a in row['actions'] if a['action_log_id']), None)
        row['sources'] = sorted(row['sources'])

    return sorted(rows.values(),
                  key=lambda r: (r['classification'] != CLASSIFICATION_ABSENT,
                                 r['username']))


def get_account_worklist(session: Session, *,
                         statuses: Sequence[str] = WORKLIST_STATUSES,
                         since: Optional[datetime] = None,
                         until: Optional[datetime] = None,
                         validate: bool = True,
                         limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Feed A composed with the classifier — what the card and CLI call."""
    return classify_accounts(session, records_from_action_log(
        session, statuses=statuses, since=since, until=until,
        validate=validate, limit=limit))


# ── enrichment ──────────────────────────────────────────────────────────

def enrich_worklist(rows: Sequence[Dict[str, Any]], *,
                    person_lookup: Optional[Callable[[str], Optional[dict]]] = None,
                    max_lookups: int = 25) -> Dict[str, Any]:
    """Attach XRAS person detail and the ``isReconciled`` closure signal.

    Separate from classification and **injected**, so the query layer stays
    fully offline-capable: an unconfigured deployment renders the same
    worklist, minus person columns.

    A single ``XrasSourceUnavailable`` marks the whole batch ``unavailable``
    and leaves ``person`` as ``None`` rather than raising — the card degrades
    to counts and usernames instead of returning 500. *max_lookups* bounds a
    cold-cache render.

    ⚠️ **``isReconciled`` is NOT a closure signal**, despite what the design
    document originally claimed. The local smoke measured **9 of 9** worklist
    rows reconciled in XRAS while every one still had no usable SAM account —
    reconciliation is XRAS linking a placeholder to a real identity (often by
    merging it), which says nothing about whether SAM has a row.

    What it *is* worth showing: whether XRAS knows who the person really is. A
    **reconciled** placeholder is the easy case — there is a real detail sheet
    behind it to create the account from. An **unreconciled** one is the hard
    case, because XRAS cannot say who they are either.

    The real closure signal needs no polling: classification is a current-state
    check, so a row leaves this list the moment its ``users`` row exists and is
    active. That is what closes an item, and it is already free.

    Returns a report dict; *rows* are mutated in place, which is what the
    caller wants to render.
    """
    from sam.integration.xras_api.base import XrasSourceUnavailable

    if person_lookup is None:
        # Deferred: importing this at module scope would put ``requests`` and
        # the cache layer into every consumer's graph, including the task's.
        from sam.integration.xras_api.people import get_person as person_lookup

    report = {'looked_up': 0, 'found': 0, 'reconciled': 0, 'unavailable': False,
              'budget_exhausted': False, 'error': None}

    for row in rows:
        if row.get('person') is not None:
            # Feed B carried it inline — no round trip needed.
            continue
        if report['looked_up'] >= max_lookups:
            report['budget_exhausted'] = True
            break
        try:
            person = person_lookup(row['username'])
        except XrasSourceUnavailable as exc:
            report['unavailable'] = True
            report['error'] = str(exc)
            break
        report['looked_up'] += 1
        if person is None:
            continue
        report['found'] += 1
        row['person'] = {k: person.get(k) for k in PERSON_FIELDS if k in person}
        if 'isReconciled' in person:
            row['is_reconciled'] = bool(person['isReconciled'])
            if row['is_reconciled']:
                report['reconciled'] += 1
    return report


def worklist_counts(rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Facet counts for the card's chips and the task's ledger detail."""
    return {
        'total': len(rows),
        CLASSIFICATION_ABSENT: sum(
            1 for r in rows if r['classification'] == CLASSIFICATION_ABSENT),
        CLASSIFICATION_INACTIVE: sum(
            1 for r in rows if r['classification'] == CLASSIFICATION_INACTIVE),
        'placeholder': sum(1 for r in rows if r['placeholder']),
        'reconciled': sum(1 for r in rows if r.get('is_reconciled') is True),
    }
