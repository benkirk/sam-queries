"""The XRAS account-creation worklist -- who must exist in SAM before a handoff works.

The largest single cause of the legacy 70% failure rate, at **55%**, is
unreconciled ARC placeholder identities: a researcher XRAS names on a request
who has no SAM account. Account creation is manual, so the fix is not code that
creates accounts; it is a worklist saying who, why, and with what detail.

Two feeds, one classifier, joined at :class:`RosterRecord`: Feed A is
``xras_action_log.raw_payload`` at push time, Feed B is
``GET /v1/reports/requests`` ahead of the push. Feed B reaches people Feed A
structurally cannot -- a brand-new PI on a solo New request, connected to
nobody SAM knows -- and neither feed should imply a second copy of the rules.

WARNING: not exported from ``sam/queries/__init__.py``, which imports its
submodules eagerly -- listing this would drag ``requests`` and the cache layer
into every ``from sam.queries import ...``. Import it by module path. For the
same reason the default person lookup is a DEFERRED import inside
:func:`enrich_worklist`: ``src/scheduling/`` imports this module, and
``test_task_ledger.py::TestPortabilityBoundary`` walks what that drags in.

WARNING: classification checks the CURRENT state of ``users``, never the
action's ``status``. Actions may be ``received`` under capture-only or
``processed``/``failed``/``manual`` under live dispatch, and the worklist must
mean the same thing on both sides of that flip.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import (Any, Callable, Dict, Iterable, Iterator, List, Mapping,
                    Optional, Sequence, Tuple)

from sqlalchemy.orm import Session

from sam.core.users import User
from sam.integration.xras import XrasActionLog
from sam.projects.projects import Project
from sam.queries.xras_actions import XRAS_ACTION_STATUSES

logger = logging.getLogger(__name__)

#: WARNING: **every status, deliberately.** Narrowing to the "interesting" ones
#: -- ``received``/``failed``/``manual``, on the reasoning that a ``processed``
#: action must have resolved its roster -- is wrong, and the local smoke
#: measured it. Seeding 41 real payloads under live dispatch left 28
#: ``processed``, and excluding them hid **5 usernames SAM cannot use: four
#: inactive, one absent entirely, most of them Allocation Managers.** An action
#: processes fine while naming such a person, because ``resolve_roster`` reports
#: a missing or inactive member as a warning, not an error, and the handler
#: skips assigning them. The account is still needed.
#:
#: A narrow list also makes the worklist **regime-dependent**, the exact
#: property § 1 of the design forbids: the same corpus produced 9 rows under
#: capture-only and 4 under live dispatch. Classification keys off the ``users``
#: table alone and must stay that way.
WORKLIST_STATUSES: Tuple[str, ...] = XRAS_ACTION_STATUSES

#: Which actions are worth a dispatch pre-flight. **This** is the bounded-work
#: knob, distinct from the status list: validating a ``processed`` action re-runs
#: a handler's whole assembly to learn what is already known, and
#: ``rechecked``/``unmapped`` are not an action's own outcome. Narrowing this
#: costs provenance on those rows, never a classification.
VALIDATE_STATUSES: Tuple[str, ...] = ('received', 'failed', 'manual')

#: ARC placeholder identities, e.g. ``placeholder34-user-00034``. XRAS mints one
#: for every unreconciled person, and it resolves fully through
#: ``GET /v1/people/<username>``, which is what makes the detail sheet possible.
PLACEHOLDER_USERNAME_RE = re.compile(r'^\S+-user-\S+$')

#: Wire role strings, spaced -- the *inbound* vocabulary. Verified live: the
#: NCAR process defines exactly these three role types (13 PI / 14 Allocation
#: Manager / 19 User), so no co-PI can appear. Deliberately NOT the
#: ``Pi``/``CoPi``/``AllocationManager`` vocabulary SAM's outbound GET side uses.
PI_ROLE = 'PI'
ALLOCATION_MANAGER_ROLE = 'Allocation Manager'
USER_ROLE = 'User'

#: Ranked for display: the strongest role a username holds leads the row.
_ROLE_ORDER = (PI_ROLE, ALLOCATION_MANAGER_ROLE, USER_ROLE)

#: PII. Assembled only for a viewer holding ``MANAGE_XRAS`` — see the card.
PERSON_FIELDS = ('firstName', 'middleName', 'lastName', 'email', 'phone',
                 'organization', 'academicStatus', 'residenceCountry',
                 'isReconciled', 'orcid')

#: Provenance tags carried on a row's ``sources`` and each action's ``source``:
#: Feed A (a posted ``xras_action_log`` row) vs Feed B (the sweep's enumeration
#: of approved, not-yet-pushed requests). Rendered as badges on the Pending
#: Users card; a row may carry both.
SOURCE_ACTION_LOG = 'action_log'
SOURCE_REPORTS = 'reports'


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
    #: When the request appeared in XRAS (``submitDate``), for a feed with no
    #: arrival of its own. Feed A leaves it ``None`` and uses ``received_time``.
    #: The two are the SAME QUESTION per feed -- "when did this show up?" --
    #: which is what lets one window control span both. NOT the period of
    #: performance: a pending request's allocation almost always ends a year
    #: out, so a one-sided window on it keeps every row at every width and the
    #: control looks dead. That bounds what the sweep collects instead.
    submit_date: Optional[str] = None
    source: str = SOURCE_ACTION_LOG
    #: ``dispatch_action(validate_only=True)``'s verdict. ``None`` = not run.
    would_succeed: Optional[bool] = None
    #: The richer verdict behind ``would_succeed``: ``rechecked`` (would land) /
    #: ``failed`` (would 422) / ``manual`` (nothing would run — parked) /
    #: ``incomplete`` (preflight raised) / ``None`` (not run). ``would_succeed``
    #: is ``status == 'rechecked'``; ``manual`` is NOT success (its reason rides
    #: ``reject_messages``) — the trap Phase 0 fixed.
    preflight_status: Optional[str] = None
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


# Feed A: the inbound action log

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

        preflight_status, messages = None, ()
        if validate and row.status in VALIDATE_STATUSES:
            preflight_status, messages = _validate(session, action,
                                                   row.xras_action_log_id)

        records.append(RosterRecord(
            ref=ActionRef(action_log_id=row.xras_action_log_id,
                          request_number=row.request_number,
                          action_type=row.action_type,
                          status=row.status,
                          received_time=row.received_time,
                          source=SOURCE_ACTION_LOG,
                          would_succeed=_would_succeed(preflight_status),
                          preflight_status=preflight_status,
                          reject_messages=messages),
            usernames=usernames,
            roles_by_username={k: tuple(v) for k, v in roles.items()},
            account_flag=flags))
    return records


def _would_succeed(status: Optional[str]) -> Optional[bool]:
    """``rechecked`` is the only success; ``manual`` is not (Phase 0 trap)."""
    if status is None or status == 'incomplete':
        return None
    return status == 'rechecked'


def _validate(session: Session, action, action_log_id
              ) -> Tuple[Optional[str], Tuple[str, ...]]:
    """Run the dispatch pre-flight; return its ``(status, messages)`` verdict.

    Registers the handlers first (import side effect) — the CLI/sweep path
    imports none, so without this every dispatch parks as ``manual``. ``manual``
    is a real not-success answer here, its reason carried in ``messages``; only
    ``rechecked`` is success.
    """
    from sam.xras.dispatch import dispatch_action
    from sam.xras.errors import XrasActionRejected
    import sam.xras.handlers  # noqa: F401 — registers handlers by side effect

    try:
        result = dispatch_action(session, action, validate_only=True)
    except XrasActionRejected as exc:
        return 'failed', tuple(exc.messages)
    except Exception as exc:                     # noqa: BLE001
        # The worklist is a report. A handler blowing up on one action must
        # not take the whole card down — record "we could not tell".
        logger.warning('xras worklist: validate failed for action %s (%s)',
                       action_log_id, exc)
        return 'incomplete', ()
    if result.status == 'manual':
        return 'manual', (result.reason,) if result.reason else ()
    return 'rechecked', ()


# Feed B: the outbound enumeration

def iter_roster_entries(
        payload: dict) -> Iterator[Tuple[dict, List[dict]]]:
    """Walk the outbound reports ``roles[]`` once: yield ``(person, roles)``.

    The outgoing wire nests each ``roles[]`` entry as ``{person, roles[]}``,
    where the inner entries carry ``role`` (not ``roleType``) and the person is
    inline and complete — every ``/v1/people`` field including ``isReconciled``
    and ``residenceCountry``, which is why a Feed-B row never needs a person
    fetch. This is the ``roles[].roles[]`` flatten that the *modal* roster
    (:func:`sam.queries.xras_requests.roster_from_payload`) and the *accounts*
    aggregation (:func:`records_from_report_requests` below) both need; sharing
    the traversal keeps their two shapes from drifting on the same trap.

    ``person`` is always a dict (never ``None``); ``roles`` is filtered to dict
    entries. Username normalization and empty-username skipping stay with each
    caller — the modal roster and the accounts view do them differently, and
    only the traversal is shared.
    """
    for entry in payload.get('roles') or ():
        if not isinstance(entry, dict):
            continue
        person = entry.get('person')
        if not isinstance(person, dict):
            person = {}
        roles = [r for r in (entry.get('roles') or ()) if isinstance(r, dict)]
        yield person, roles


def role_in_window(role: dict, *, on: Optional[str] = None) -> bool:
    """Whether one ``roles[].roles[]`` entry is current on *on* (default today).

    The inbound roster (``sam/xras/roster.py``) windows every role on its
    dates against the action's begin date; these outbound worklist/roster
    reads answer "who holds the role NOW", so the reference date is today
    (naive-Mountain, the wire's calendar-date convention). Null dates are
    open ends.
    """
    today = on or datetime.now().strftime('%Y-%m-%d')
    begin = str(role.get('beginDate') or '')[:10]
    end = str(role.get('endDate') or '')[:10]
    if begin and begin > today:
        return False
    return not (end and end < today)


def _report_action_type(payload: dict) -> Optional[str]:
    """The request's representative ``actionType`` — never ``requestType``.

    ``requestType`` is ``New``/``Renewal`` on every row and does not select a
    handler (``schemas/forms/xras.py``); the dispatching type lives on each
    action. A request usually carries one action; take the first that names a
    type, falling back to ``requestType`` only when none do.
    """
    for action in payload.get('actions') or ():
        if isinstance(action, dict) and action.get('actionType'):
            return action.get('actionType')
    return payload.get('requestType')


def records_from_report_requests(payloads: Iterable[dict]) -> List[RosterRecord]:
    """Feed B — rosters from ``GET /v1/reports/requests`` rows.

    Built on the shared :func:`iter_roster_entries` traversal, aggregated to one
    record per request with a per-username role set (a Feed-B row never needs a
    person fetch — the person is inline).
    """
    records: List[RosterRecord] = []
    for payload in payloads or ():
        if not isinstance(payload, dict):
            continue

        usernames: List[str] = []
        roles: Dict[str, List[str]] = {}
        flags: Dict[str, bool] = {}
        people: Dict[str, dict] = {}

        for person, role_entries in iter_roster_entries(payload):
            username = str(person.get('username') or '').strip()
            if not username:
                continue
            current = [r for r in role_entries if role_in_window(r)]
            if role_entries and not current:
                # Every role this person held is dated out of range: the role
                # is over and the handoff does not need the account — the
                # window rule the inbound roster already applies.
                continue
            if username not in usernames:
                usernames.append(username)
                people[username] = person
            for role in current:
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
                          action_type=_report_action_type(payload),
                          status=payload.get('requestStatus'),
                          received_time=None,
                          submit_date=(str(payload.get('submitDate'))[:10]
                                       if payload.get('submitDate') else None),
                          source=SOURCE_REPORTS),
            usernames=tuple(usernames),
            roles_by_username={k: tuple(v) for k, v in roles.items()},
            account_flag=flags,
            person_by_username=people))
    return records


# the classifier

def _waiting_since(first_seen: Optional[datetime],
                   actions: Sequence[Dict[str, Any]]) -> Optional[date]:
    """The earliest date this person is known to have been blocking something.

    WARNING: **Neither feed alone answers this**, which is why it is derived here
    rather than read off a column. Feed A knows ``received_time`` — when XRAS
    pushed the action at us — and leaves ``submit_date`` null. Feed B is the
    exact inverse: a request that has not been pushed has no arrival, only the
    ``submitDate`` it got in XRAS. A merged row carries both.

    Returned as a **date**, not a datetime: the answer is rendered as a whole
    number of days waiting, and a spurious time-of-day on one feed and not the
    other would imply a precision the two sources do not share.

    Distinct from ``last_seen``, which is when we most recently *heard* about
    the person — on a freshly seeded log that is today for every row, which is
    exactly the reading that makes it useless as a queue order.
    """
    candidates = []
    if first_seen is not None:
        candidates.append(first_seen.date())
    for action in actions:
        raw = action.get('submit_date')
        if not raw:
            continue
        try:
            candidates.append(date.fromisoformat(str(raw)[:10]))
        except ValueError:
            # A feed that changes its date format must not take the card down;
            # the row simply has no age, and renders as such.
            continue
    return min(candidates) if candidates else None


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

    ``absent``    no ``users`` row at all            -> create the account
    ``inactive``  a row that fails ``User.is_active``  -> reactivate it

    A predicate that only checked existence would miss the second entirely —
    of 19 live roster usernames sampled during design, all 19 existed but
    **five were inactive**, a quarter of the real cases.

    WARNING: ``isAccountToBeCreated`` is **never** the predicate. XRAS sets it when
    the role is created and never clears it, so live data shows active,
    present SAM users still carrying ``true``. It rides along as a hint column
    and nothing more.
    """
    wanted = {u for record in records for u in record.usernames if u}
    if not wanted:
        return []

    # One query for the whole batch, never per username.
    #
    # WARNING: **keyed case-INSENSITIVELY, and this is not cosmetic.**
    # ``users.username`` is ``utf8mb3_general_ci`` with a UNIQUE index, so MySQL
    # treats ``Jsmith`` and ``jsmith`` as one account and the ``IN`` matches
    # either spelling. A case-sensitive dict underneath then MISSES the row it
    # was just handed and reports the username ``absent`` -- telling an operator
    # to create an account that already exists and is active. The local smoke
    # hit exactly that. No in-tree fixture can catch it: the anonymizer emits
    # lowercase ``user_<hex>``, so every scrubbed roster is already
    # case-matched, and ``roster.normalize_username`` does not fold case either
    # (it reproduces Java), so the wire spelling arrives untouched.
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
                    'waiting_since': None,
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
                # Filtered to PERSON_FIELDS as `enrich_worklist` does for Feed
                # A: a raw XRAS person carries fields we never declared
                # (`hasOrcidToken`, a duplicate `username`), and a Feed-B row is
                # PERSISTED into the cache, so it outlives its request.
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
                'preflight_status': record.ref.preflight_status,
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
        row['waiting_since'] = _waiting_since(row['first_seen'], row['actions'])
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
                         limit: Optional[int] = None,
                         pending_rows: Optional[Sequence[Dict[str, Any]]] = None
                         ) -> List[Dict[str, Any]]:
    """Feed A composed with the classifier, optionally unioned with Feed B.

    *pending_rows* is the worklist ``xras_sweep`` published — **injected, never
    fetched**, exactly as :func:`~sam.queries.xras_actions.audit_resource_mapping`
    takes *xras_keys*. This module stays free of cache and network knowledge;
    the caller decides whether it can reach the snapshot.

    ``None`` reproduces the Feed-A-only answer byte for byte, which is what a
    consumer with no Redis gets — and it must be able to tell that apart from
    "Feed B is empty", so a caller that *tried* should say so rather than
    reporting a smaller queue as if it were the whole one.

    WARNING: **Overlap is normal**, and this is a union, not a concatenation. The two
    feeds answer adjacent questions — Feed A is precisely the actions that have
    **posted**, Feed B is what XRAS has approved and *may or may not* have
    posted — so the same person legitimately appears in both. They are merged
    on the casefolded username (``users.username`` is ``utf8mb3_general_ci``,
    so two spellings are one account) with their provenance unioned.
    """
    rows = classify_accounts(session, records_from_action_log(
        session, statuses=statuses, since=since, until=until,
        validate=validate, limit=limit))
    if pending_rows is None:
        return rows
    return merge_worklists(rows, pending_rows)


def merge_worklists(primary: Sequence[Dict[str, Any]],
                    secondary: Sequence[Dict[str, Any]]
                    ) -> List[Dict[str, Any]]:
    """Union two already-classified worklists on the casefolded username.

    Both sides were classified against the same ``users`` table by the same
    :func:`classify_accounts`, so this merges answers rather than recomputing
    one. Where a username is in both, *primary* wins the scalar fields: it is
    the live classification, while *secondary* is as old as the last sweep.

    What is unioned rather than overwritten: ``roles``, ``actions`` and
    ``sources`` — a person can be a PI on a posted action and a User on a
    pending one, and losing either would misreport why they are blocking.
    ``waiting_since`` takes the **earlier** of the two, because the question is
    how long they have been waiting, not which feed noticed first.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    for row in list(primary) + list(secondary):
        key = (row.get('username') or '').casefold()
        if not key:
            continue
        seen = merged.get(key)
        if seen is None:
            merged[key] = dict(row)
            merged[key]['roles'] = tuple(row.get('roles') or ())
            # Copy each action dict, not just the list: the primary rows are the
            # cached snapshot's own objects, and `stamp_project_existence` writes
            # `action['is_project']` in place -- without the copy the in-process
            # cache adapter would carry that mutation into the next render.
            merged[key]['actions'] = [dict(a) for a in (row.get('actions') or [])]
            merged[key]['sources'] = list(row.get('sources') or [])
            continue
        seen['roles'] = _sorted_roles((*seen['roles'], *(row.get('roles') or ())))
        seen['actions'] = list(seen['actions']) + [
            dict(a) for a in (row.get('actions') or [])]
        seen['sources'] = sorted(set(seen['sources']) | set(row.get('sources') or []))
        for field_name in ('first_seen', 'waiting_since'):
            other = row.get(field_name)
            if other is not None and (seen.get(field_name) is None
                                      or other < seen[field_name]):
                seen[field_name] = other
        if row.get('last_seen') is not None and (
                seen.get('last_seen') is None
                or row['last_seen'] > seen['last_seen']):
            seen['last_seen'] = row['last_seen']
        # Person detail and the XRAS identity flag ride whichever feed carried
        # them; Feed B always does, Feed A only after an --enrich pass.
        for field_name in ('person', 'is_reconciled', 'latest_action_log_id'):
            if seen.get(field_name) is None and row.get(field_name) is not None:
                seen[field_name] = row[field_name]

    # Received-push rows lead: a Feed-A row is the more urgent flavor -- a push
    # already arrived and is blocked -- so it sorts ahead of the absent-before-
    # inactive-then-username order the CLI and card both inherit.
    return sorted(merged.values(),
                  key=lambda r: (SOURCE_ACTION_LOG not in (r.get('sources') or ()),
                                 r['classification'] != CLASSIFICATION_ABSENT,
                                 r['username']))


@dataclass(frozen=True)
class PendingFeed:
    """The sweep's Feed B as last published, plus WHY it may be empty.

    ``checked`` is whether we could look at all; ``reason`` names the degraded
    state so the one consumer with a place to show it can. ``rows`` is always a
    list -- empty on any degraded path -- so a caller can inject it unguarded.
    """

    rows: List[Dict[str, Any]] = field(default_factory=list)
    checked: bool = False
    #: ``unconfigured`` / ``no_snapshot`` / ``unreadable`` / ``None`` (read OK).
    reason: Optional[str] = None
    snapshot: Optional[Dict[str, Any]] = None


def load_pending_worklist_rows() -> PendingFeed:
    """Read Feed B from the cache, degrading rather than raising.

    The three failure modes are distinct facts, kept apart the way
    ``live_checked`` is on the mapping audit: an unconfigured deployment, a
    configured one with no sweep yet, and an unreadable backend must render
    different notes -- an empty ``rows`` alone cannot tell them apart. Cache and
    API imports are DEFERRED (this module's rule -- see the module docstring).
    """
    from sam.integration.xras_api import xras_api_configured

    if not xras_api_configured():
        return PendingFeed(reason='unconfigured')
    try:
        from sam.integration.xras_api.cache import load_pending_worklist
        snapshot = load_pending_worklist()
    except Exception as exc:                     # noqa: BLE001
        # The cache backend is infrastructure, not a contract -- a laptop with
        # no CACHE_REDIS_URL raises from the adapter stack rather than returning
        # empty, and that must not take the report down.
        logger.warning('xras worklist: pending feed unreadable (%s)', exc)
        return PendingFeed(reason='unreadable')
    if snapshot is None:
        return PendingFeed(reason='no_snapshot')
    return PendingFeed(rows=list(snapshot.get('rows') or []), checked=True,
                       snapshot=snapshot)


# enrichment

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

    WARNING: **``isReconciled`` is NOT a closure signal**, despite what the design
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
        'received_push': sum(
            1 for r in rows if SOURCE_ACTION_LOG in (r.get('sources') or ())),
        'pending_request': sum(
            1 for r in rows if SOURCE_REPORTS in (r.get('sources') or ())),
        # How long the oldest row has been blocking something, in days. The
        # single number that says whether this queue is being worked — a count
        # alone reads the same on a healthy day and a neglected one.
        'oldest_days': max(
            (r['waiting_days'] for r in rows
             if r.get('waiting_days') is not None), default=None),
    }


def waiting_days(row: Dict[str, Any], *, today: Optional[date] = None
                 ) -> Optional[int]:
    """Whole days since :func:`_waiting_since`, or ``None`` if undatable.

    *today* is injectable because a test that read the wall clock would pass or
    fail depending on the day it ran — the same reason
    ``sam/queries/xras_accounts.py``'s window predicate takes its boundary.
    """
    since = row.get('waiting_since')
    if since is None:
        return None
    # WARNING: clamped at zero. `received_time` is naive-Mountain from the app
    # clock, so a process in another zone stamps rows that read as the future --
    # a container with no TZ set runs six hours ahead of the data it writes. A
    # negative age is never a fact about the queue, only about the clock. The
    # cause is fixed where it belongs (compose sets TZ); this keeps the column
    # honest if it recurs.
    return max(0, ((today or date.today()) - since).days)


def stamp_project_existence(session: Session,
                            rows: Sequence[Dict[str, Any]]) -> None:
    """Stamp ``is_project`` onto every action, in place. **One query.**

    A ``request_number`` is a projcode for Extension/Supplement/Adjustment and
    a request token for New — and nothing in the row distinguishes them, since
    the two are the same shape. The only way to know is to ask whether a
    project by that name exists. Measured on a seeded stack: **30 of 41**
    distinct numbers resolve, including four ``New`` actions, which is the
    New-whose-projcode-already-exists case ``dispatch.select_service`` routes
    to ``update``.

    Applied by the CALLER, like :func:`stamp_waiting_days` and
    :func:`enrich_worklist`, and for a sharper reason than either: this must
    **not** go inside :func:`classify_accounts`, which Feed B also runs — from
    the sweep, into a cache. A flag computed at sweep time and read an hour
    later is a claim about the database that nothing rechecked. Feed B has no
    use for it anyway: its cohort is `numbers - known`, so every row there is
    by construction a number SAM has no project for.

    Two states, not the three ``_annotate_project_existence`` gives the action
    log. There, a projcode that fails to resolve means an action *already ran*
    naming a project SAM does not have — worth an operator's attention, hence
    the warning branch. Here the action has **not** completed; that is why the
    account is missing. A number with no project yet is the expected case, so
    there is no third state to draw.
    """
    actions = [a for row in rows for a in (row.get('actions') or ())]
    for action in actions:
        action['is_project'] = False

    codes = {a['request_number'] for a in actions if a.get('request_number')}
    if not codes:
        return

    known = {c for (c,) in session.query(Project.projcode)
             .filter(Project.projcode.in_(sorted(codes))).all()}
    for action in actions:
        action['is_project'] = action.get('request_number') in known


def stamp_waiting_days(rows: Sequence[Dict[str, Any]], *,
                       today: Optional[date] = None) -> None:
    """Stamp ``waiting_days`` onto each row, in place.

    Separate from classification and applied by the caller, for the same reason
    :func:`enrich_worklist` is: it is the only field whose value depends on
    *when you asked*, and a cached row carrying a stale age would be worse than
    one carrying none.
    """
    when = today or date.today()
    for row in rows:
        # WARNING: backfill rather than assume. A Feed-B row is read back from a
        # snapshot the *task* published, and the task can run older code than
        # the reader -- guaranteed mid-deploy, and a cached snapshot outlives a
        # rollback. A row with no `waiting_since` is version skew, not a row
        # with no age, so recompute it from the provenance every row carries.
        if row.get('waiting_since') is None:
            row['waiting_since'] = _waiting_since(row.get('first_seen'),
                                                  row.get('actions') or ())
        row['waiting_days'] = waiting_days(row, today=when)
