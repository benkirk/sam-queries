"""``roles[]``, read twice, with different rules — and the defect that produces.

One array, two readings, and conflating them is the easiest way to get project
membership wrong:

============================  ============================================  =====================
reading                       filter                                        result
============================  ============================================  =====================
**role assignment**           ``roleType`` must equal ``PI`` or             project **lead** /
(``getUsernameByRoleType``)   ``Allocation Manager``, plus a date window     **admin**
**roster**                    ``roleType`` is **never examined** — date      **every** entry
(``getUsernames``)            window only                                    becomes a member
============================  ============================================  =====================

``ActionRoleName`` has exactly two constants, ``PI("PI")`` and
``ALLOCATION_MANAGER("Allocation Manager")`` — space-separated, case-sensitive, and a
*different vocabulary* from the ``Pi`` / ``CoPi`` / ``AllocationManager`` keys of
``GET /v1/requests/role/…``. So a ``Co-PI`` or a ``User`` is invisible to role
assignment but **is still added to the project**. ``new_ncar4232_failed.json`` carries
a ``User`` entry and is the corpus proof.

The end-date rule is **identical** on both readings. Only the begin-date rule differs,
and it differs asymmetrically:

.. code-block:: java

    // roster
    if (roleBeginDate.compareTo(actionDate) > 0) continue;        // strictly excluded

    // role assignment
    if (roleBeginDate > actionDate && currDate <= roleBeginDate && currDate <= actionDate)
        continue;                                                 // excluded only if ALSO future

The role-assignment rule is a triple conjunct, so a future-dated role is ignored *only
while the action itself is also still in the future*. Once the action's begin date has
passed, a future-dated role is **accepted** — while the roster still excludes it. That
is **legacy defect 3**: such a person becomes project lead of a project they have no
account on. Because role assignment's exclusion is a strict subset of the roster's, the
disagreement can only ever run one way, which :func:`role_assignment_disagreements`
relies on and a test asserts.

Both are ported separately and the disagreement is surfaced as a warning rather than
silently repaired — it is a real data problem, and hiding it would remove the only
evidence anyone has of it.

Dates are compared **as strings**, never parsed. Java uses lexicographic
``String.compareTo``, which is correct only because the wire is zero-padded
``yyyy-MM-dd``; Python's string comparison is identical over that alphabet. Parsing
would introduce a second failure mode (an unparseable date) that legacy does not have.

Verified against ``~/codes/sam`` at tag 2.0.3. See ``docs/xras/incoming/implemented/XRAS_SPRINT_C.md``
§ *The roster*.
"""

import logging
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sam.core.users import User

from . import errors as e
from .errors import ActionErrors
from .wire import get_field

logger = logging.getLogger(__name__)

__all__ = [
    'Roster',
    'PI_ROLE',
    'ALLOCATION_MANAGER_ROLE',
    'normalize_username',
    'roster_usernames',
    'role_candidates',
    'role_assignment_disagreements',
    'resolve_roster',
]

#: ``ActionRoleName.PI`` — matched with ``String.equals``, so case- and space-exact.
PI_ROLE = 'PI'

#: ``ActionRoleName.ALLOCATION_MANAGER``. Note the space: not ``AllocationManager``,
#: which is the spelling the *GET* side uses for the same concept.
ALLOCATION_MANAGER_ROLE = 'Allocation Manager'


@dataclass(frozen=True)
class Roster:
    """Everything ``roles[]`` yields, resolved and validated once.

    ``pi_username`` is ``None`` when the action names no current PI **or** names more
    than one — in both cases an error has been reported and the handler must not
    proceed. ``admin_username`` is ``None`` when the action names no Allocation
    Manager, which is *not* an error: legacy guards its validation on
    ``adminUsername != null`` and several real payloads have none.

    ``warnings`` are not errors. They record situations the action survives but a
    human should look at — today only the defect-3 disagreement.

    **The resolved rows come with it.** ``pi`` / ``admin`` / ``members`` are the
    ``User`` objects :func:`_validate_user` already fetched while validating. The New
    and Update handlers used to re-look-up every one of them from the usernames — two
    byte-identical seven-line blocks — which doubled the query count for a roster:
    ten members cost twenty ``SELECT``s where ten would do.

    ⚠️ ``members`` is positionally aligned with ``member_usernames`` and **may contain
    ``None``** where a username matched no row. That is deliberate: a missing member
    has already been reported, so ``raise_if_any()`` stops the action before anything
    iterates it, and preserving the hole keeps the handlers' existing
    ``if member is not None`` guard meaningful rather than silently shortening the
    list and changing what a future reader thinks it is looking at.
    """

    pi_username: Optional[str]
    admin_username: Optional[str]
    member_usernames: Tuple[str, ...]
    warnings: Tuple[str, ...] = ()
    pi: Optional[User] = None
    admin: Optional[User] = None
    members: Tuple[Optional[User], ...] = ()


def _wire_str(value) -> str:
    """A wire string as Jackson would have produced it.

    ⚠️ **The one divergence in this module, and it is systematic rather than local.**
    Every string field on ``XrasRole`` is declared ``= ""``, so Java distinguishes an
    *absent* key (``""``) from an explicit JSON ``null``. marshmallow's
    ``load_default=None`` gives ``None`` for both, and the distinction is not
    recoverable here.

    We resolve it the same way :mod:`sam.xras.extractors` does: **absent is treated as
    JSON null**. That is what the observed wire actually sends — ``endDate`` is JSON
    ``null`` in all eight corpus payloads, never absent and never ``""`` — and it is
    the safer branch on the field where it matters. An ``endDate`` of ``""`` compares
    *less than* any real date, so Java would skip the role entirely; a role with no end
    date is plainly meant to be current.

    ``username`` and ``roleType`` are the exception, and deliberately: they keep the
    ``""`` reading, because ``Username  is missing`` (with its double space) is the
    exact bytes legacy emits for a blank username, and swallowing it would hide a role
    entry that has no person attached.
    """
    return '' if value is None else str(value)


def normalize_username(value) -> str:
    """``StringUtil.normalize`` — NFD-decompose, then keep only whitespace and ASCII.

    Legacy runs every username from ``roles[]`` through this before using it as a
    lookup key, so ``José`` becomes ``Jose``. Reproduced because it is the function
    that decides *which row* gets looked up: skipping it would turn a resolvable user
    into ``Username %s is missing``.

    ⚠️ Java's ``Character.isWhitespace`` and Python's ``str.isspace`` disagree on a
    handful of code points (U+00A0 most notably, whitespace to Python but not to Java).
    A username containing a non-breaking space is pathological enough that matching
    Java exactly here would cost more than it buys.
    """
    text = _wire_str(value).strip()
    decomposed = unicodedata.normalize('NFD', text)
    return ''.join(c for c in decomposed
                   if c.isspace() or '\u0020' <= c <= '\u007f')


def _today() -> str:
    """``DateUtil.getDateAsString(DateUtil.NOW())``.

    Naive local time, per the house convention — SAM/MySQL is naive-Mountain and every
    date on this wire is a calendar date, not an instant.
    """
    return datetime.now().strftime('%Y-%m-%d')


def _end_date_in_range(role, action_date: str) -> bool:
    """``roleEndDateInRange`` — identical on both readings, which § 3.5's snippet
    omits in a way that invites the wrong conclusion."""
    end_date = get_field(role, 'endDate')
    if end_date is None:
        return True
    return not (_wire_str(end_date) < action_date)


def roster_usernames(action) -> Tuple[str, ...]:
    """``getUsernames()`` — **every** role entry inside the date window, ``roleType``
    ignored entirely.

    Deduplicated, which legacy does not do: one human holding two roles appears twice
    in its list. That is harmless downstream (``Account.assign`` is idempotent) but it
    would double every ``Username %s is missing`` before the accumulator collapsed it,
    and it makes the count meaningless to anyone reading it. Order is preserved.

    ⚠️ ``AddUserToProjectActionCommandsFactory.create()`` fans this list out **per
    ``resources[]`` entry**. With ``resources: []`` — *both* Extensions in the corpus —
    it produces **zero** add-user commands even though the roster is non-empty. The
    roster is computed and validated regardless; whether anything is done with it is
    the handler's decision, not this function's.
    """
    action_date = _wire_str(get_field(action, 'actionBeginDate'))
    names: List[str] = []
    for role in get_field(action, 'roles') or ():
        begin_date = _wire_str(get_field(role, 'beginDate'))
        if begin_date > action_date:
            continue
        if not _end_date_in_range(role, action_date):
            continue
        username = normalize_username(get_field(role, 'username'))
        if username not in names:
            names.append(username)
    return tuple(names)


def role_candidates(action, role_type: str, *, today: Optional[str] = None) -> Tuple[str, ...]:
    """Every entry ``getUsernameByRoleType`` would consider for *role_type*, in order.

    Legacy returns the **first** survivor and discards the rest — that is *legacy
    defect 1*, and it means which human leads a project is decided by array order.
    This returns them all so :func:`resolve_roster` can reject an ambiguous action
    rather than coin-flip it.

    The begin-date rule is the triple conjunct described in the module docstring: a
    future-dated role is excluded **only while the action is also still in the
    future**, which makes this function's result depend on the current date. *today*
    is injectable for exactly that reason — a test that let it float would pass or fail
    depending on when it ran.
    """
    current_date = today or _today()
    action_date = _wire_str(get_field(action, 'actionBeginDate'))

    candidates: List[str] = []
    for role in get_field(action, 'roles') or ():
        # Exact string inequality, reproducing Java's String.equals: no case
        # folding, no alias table. Settled 2026-08-19 by probing
        # `GET /v1/types/roles`: the NCAR process defines exactly THREE role
        # types — 13 PI, 14 Allocation Manager, 19 User — so `roleType` has a
        # closed three-value vocabulary and no co-PI can ever appear on this
        # wire. (The generic XRAS product does define CoPI at roleTypeId 1;
        # those ids are per-process, which is why NCAR's are 13/14/19.)
        # Confirmed again on live data: zero co-PIs across 64 sampled role
        # entries. See docs/xras/outgoing/XRAS_OUTGOING_QUERIES.md § 3.4.
        if _wire_str(get_field(role, 'roleType')) != role_type:
            continue
        begin_date = _wire_str(get_field(role, 'beginDate'))
        if (begin_date > action_date
                and current_date <= begin_date
                and current_date <= action_date):
            continue
        if not _end_date_in_range(role, action_date):
            continue
        candidates.append(normalize_username(get_field(role, 'username')))
    return tuple(candidates)


def role_assignment_disagreements(action, *, today: Optional[str] = None) -> Tuple[str, ...]:
    """Usernames role assignment accepts but the roster excludes — **legacy defect 3**.

    Such a person becomes project lead or admin while having no account on the
    project. The disagreement can only run this way: role assignment's begin-date
    exclusion is the roster's, conjoined with two further conditions, so it excludes a
    strict subset. The end-date rule is identical on both. ``test_the_disagreement_is
    _one_directional`` asserts that rather than trusting the argument.

    ``adjustment_uwis0064_manual.json`` is the live case — every role begins in 2025
    against a 2021 action date, so the roster is empty while both role assignments
    resolve.
    """
    members = set(roster_usernames(action))
    assigned = set(role_candidates(action, PI_ROLE, today=today))
    assigned |= set(role_candidates(action, ALLOCATION_MANAGER_ROLE, today=today))
    return tuple(sorted(assigned - members))


def _user_resolver(session):
    """A memoised ``username -> User | None`` lookup for one ``resolve_roster`` call.

    One person routinely occupies several roles in one action — PI, Allocation
    Manager and roster member is the ordinary shape, not an edge case — and each
    occurrence must be *reported* separately, in legacy's vocabulary and order. Only
    the **lookup** is redundant, so only the lookup is cached.

    Scoped to a single call rather than module-level: it must not outlive the session
    it queried, and an action is short.
    """
    cache: Dict[str, Optional[User]] = {}

    def resolve(username: str) -> Optional[User]:
        if username not in cache:
            cache[username] = User.get_by_username(session, username)
        return cache[username]

    return resolve


def _validate_user(lookup, username: str, errs: ActionErrors,
                   missing, inactive) -> Optional[User]:
    """Report *username* against the users table, and **return the row it fetched**.

    ⚠️ ``User.is_active`` is ``active AND NOT locked``; Java's ``isActive()`` returns
    ``active`` alone. The house rule (CLAUDE.md § 5) is to use the hybrid, and the
    divergence is unobservable: production has **zero** locked users out of 28,371. A
    locked account is one somebody has deliberately stopped; leading a new project with
    it would be wrong even if legacy allowed it.

    ⚠️ **An inactive user is returned, not dropped.** It reported an error, so
    ``raise_if_any()`` will stop the action before anything reads the row — but the
    handlers used to re-fetch unconditionally and would have got it, so returning it
    keeps this a pure de-duplication of queries rather than a behaviour change.

    ⚠️ **Reporting is per occurrence, deliberately.** Called three times for one
    person in three roles, this reports three times — the strings differ per role
    (``PI %s is not in database`` vs ``Allocation Manager %s is not in database:␣`` vs
    ``Username %s is missing``) and ``ActionErrors`` deduplicates identical ones
    anyway. It is the query that is shared, via *lookup*, not the diagnosis.
    """
    user = lookup(username)
    if user is None:
        errs.report(missing(username))
    elif not user.is_active:
        errs.report(inactive(username))
    return user


def resolve_roster(session, action, errs: ActionErrors, *,
                   today: Optional[str] = None) -> Roster:
    """Both readings, validated, in legacy's reporting order.

    Order matters only because the operator reads the 422 top to bottom;
    ``ProjectActionCommandFactoryBase`` validates the lead, then the admin, and
    ``AddUserToProjectActionCommandsFactory`` walks the roster separately. Reproduced
    here as PI → Allocation Manager → members.

    Reports, in the vocabulary legacy uses:

    - ``Missing pi role`` when no current PI role exists
    - ``PI %s is not in database`` / ``PI %s is not an active user:␣``
    - ``Allocation Manager %s is not in database:␣`` / ``…is not active␣``
    - ``Username %s is missing`` / ``Username %s is inactive`` per roster member

    and one string legacy has no equivalent for — see
    :func:`sam.xras.errors.ambiguous_role`.

    **A missing Allocation Manager is not an error.** Legacy guards the whole check on
    ``adminUsername != null``, and real payloads arrive without one. A missing PI is.
    """
    lookup = _user_resolver(session)
    warnings = role_assignment_disagreements(action, today=today)
    for username in warnings:
        logger.warning(
            'XRAS role/roster disagreement: %s is assigned a role but is excluded '
            'from the project roster (legacy defect 3) — action %s',
            username, get_field(action, 'actionId'))

    pi_candidates = role_candidates(action, PI_ROLE, today=today)
    pi_username: Optional[str] = None
    pi: Optional[User] = None
    if not pi_candidates:
        errs.report(e.missing_pi_role())
    elif len(pi_candidates) > 1:
        errs.report(e.ambiguous_role(PI_ROLE, pi_candidates))
    else:
        pi_username = pi_candidates[0]
        pi = _validate_user(lookup, pi_username, errs,
                            e.pi_not_in_database, e.pi_not_active)

    admin_candidates = role_candidates(action, ALLOCATION_MANAGER_ROLE, today=today)
    admin_username: Optional[str] = None
    admin: Optional[User] = None
    if len(admin_candidates) > 1:
        errs.report(e.ambiguous_role(ALLOCATION_MANAGER_ROLE, admin_candidates))
    elif admin_candidates:
        admin_username = admin_candidates[0]
        admin = _validate_user(lookup, admin_username, errs,
                               e.manager_not_in_database, e.manager_not_active)

    members = roster_usernames(action)
    member_rows = tuple(
        _validate_user(lookup, username, errs,
                       e.username_missing, e.username_inactive)
        for username in members
    )

    return Roster(
        pi_username=pi_username,
        admin_username=admin_username,
        member_usernames=members,
        warnings=warnings,
        pi=pi,
        admin=admin,
        members=member_rows,
    )
