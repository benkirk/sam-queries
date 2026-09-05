"""Turning wire fields into SAM rows: allocation type, area of interest,
mnemonic, contract.

Four independent lookups every project-shaped handler needs, each a place where
a plausible reading of the payload gives the wrong answer.

Three shared contracts:

**They report; they do not raise.** Legacy funnels every extraction failure
into one observer, so an unresolvable mnemonic and a missing title arrive in
the SAME 422. Every function takes an
:class:`~sam.xras.errors.ActionErrors`, reports into it, and returns ``None``.

**They return ORM rows, not ids or names.** Legacy returns an id or a string
because its downstream commands re-resolve them; ours have none, so the row is
cheaper and harder to misuse -- and the allocation-type row carries the panel,
which ``getAuthAtPanelMeeting()`` needs.

**They are pure where they can be.** :func:`select_allocation_type_parms` takes
no session: the eleven-strategy chain is string matching, and the database is
consulted only to turn the pair into a row. That is what lets the strategy
order be tested against the corpus without a database.

Two traps would silently produce wrong data:

1. ``fosNum`` is an ``area_of_interest_id``, NOT an ``fos_aoi.fos_id``.
2. A ``(panel, type)`` pair must be resolved to an id AT RUNTIME -- ``Small``
   and ``Education`` each name two different ``allocation_type`` rows.

Verified against ``~/codes/sam`` at tag 2.0.3. See
``docs/xras/incoming/implemented/XRAS_SPRINT_C.md``.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from sam.accounting.allocations import AllocationType
from sam.core.organizations import MnemonicCode, Organization
from sam.integration.xras import XrasOpportunityAllocationType, lookup_request_override
from sam.core.users import User
from sam.projects.areas import AreaOfInterest
from sam.projects.contracts import Contract
from sam.resources.facilities import Panel

from . import errors as e
from .errors import ActionErrors
from .wire import get_field

__all__ = [
    'SelectionParms',
    'select_allocation_type_parms',
    'select_allocation_type_mapped',
    'resolve_allocation_type',
    'resolve_area_of_interest',
    'resolve_mnemonic_code',
    'extract_core_number',
    'resolve_contract',
]


# ---------------------------------------------------------------------------
# Allocation type — eleven strategies, first non-null wins.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SelectionParms:
    """The ``(panel, type)`` pair a strategy resolves to.

    Java's ``SelectionParms``. ``__str__`` is not reproduced here — the operator-facing
    rendering lives in :func:`sam.xras.errors.no_allocation_type_for_pair`, which is
    where every other wire string lives.
    """

    panel: str
    allocation_type: str


#: ``AllocationType`` enum, verbatim, in declaration order.
#:
#: WARNING: **Not** in *strategy* order — the chain order is :data:`_STRATEGIES` below, and
#: the two differ (``NSC`` is 6th here, 2nd there). This mapping exists only to give
#: :func:`_lookup_by_type_name` its keys, exactly as Java's ``NAME_MAP`` does.
_ALLOCATION_TYPES: Dict[str, SelectionParms] = {
    'EXPLORE_ACCESS': SelectionParms('ACCESS', 'Explore ACCESS'),
    'DISCOVER_ACCESS': SelectionParms('ACCESS', 'Discover ACCESS'),
    'EXTERNAL': SelectionParms('External Projects', 'External Project'),
    'CSL': SelectionParms('CSLAP', 'CSL'),
    'LARGE': SelectionParms('CHAP', 'CHAP'),
    'NSC': SelectionParms('NCAR-ARP', 'NSC'),
    'SMALL_NON_NSF': SelectionParms('UNIV USS', 'Small (No NSF award)'),
    'SMALL_NSF': SelectionParms('UNIV USS', 'Small'),
    'CLASSROOM': SelectionParms('UNIV USS', 'Classroom'),
    'DATA_ANALYSIS': SelectionParms('UNIV USS', 'Data'),
    'ASDUNIV': SelectionParms('ASD-CHAP', 'ASD-UNIV'),
    'ASDNCAR': SelectionParms('ASD-NCAR', 'ASD-NCAR'),
}

#: ``AllocationType.NAME_MAP`` — keyed on the **SAM type name**, which is why a wire
#: ``allocationType`` of ``'Small'`` resolves and ``'Large'`` does not. See
#: :func:`_access_strategy`.
_BY_TYPE_NAME: Dict[str, SelectionParms] = {
    parms.allocation_type: parms for parms in _ALLOCATION_TYPES.values()
}

#: ``CSLStrategy.CSLPREFIX``. The empty left branch of the alternation is deliberate
#: and load-bearing: "CSL alone, **or** CSL followed by a non-word character and
#: anything". WARNING: ``XRAS_REIMPLEMENTATION.md`` § 3.2 renders this with a backslash
#: before the pipe, which reads as a literal ``|`` and matches nothing real —
#: transcribing the doc rather than the source breaks CSL detection outright.
#: Java's ``Matcher.matches()`` is a full match, hence ``fullmatch`` at the call site.
_CSL_PATTERN = re.compile(r'\s*CSL(|[\W].*)')

#: ``ExternalStrategy.EXTERNAL_PATTERN`` — also full-match, also applied to three
#: different fields.
_EXTERNAL_PATTERN = re.compile(r'(.* )?External( .*)?')


def _clean(value: Optional[str]) -> Optional[str]:
    """Normalize a wire string to "a value" or ``None``.

    WARNING: **A declared divergence, and the only one in this module that changes which
    branch runs.** Jackson gives ``XrasAction.allocationType`` a default of ``""``,
    so Java can tell an *absent* key (``""``) from an explicit JSON ``null`` (Java
    ``null``) — and it behaves differently on each: ``""`` takes the exact-lookup
    branch of ``ACCESSStrategy``, which can only miss, while ``null`` takes the
    ``opportunityName`` branch that detects Discover/Explore ACCESS.

    marshmallow gives both ``None`` (``load_default=None``), so the distinction is not
    recoverable here. We take the ``null`` behavior for both, which is the strictly
    more capable one: the ``""`` path Java would have taken always resolves to nothing
    and falls straight through. The only payloads affected are ACCESS-instance ones
    that omit the key entirely, where legacy fails to resolve a type at all.
    """
    if value is None:
        return None
    value = value.strip()
    return value or None


def _lookup_by_type_name(name: Optional[str]) -> Optional[SelectionParms]:
    """``AllocationType.lookup`` — exact, case-sensitive, on the **SAM type name**."""
    return _BY_TYPE_NAME.get(name) if name else None


def _access_strategy(action) -> Optional[SelectionParms]:
    """`ACCESSStrategy` — strategy 1, and the one that short-circuits the other ten.

    WARNING: When the payload carries an ``allocationType`` this is an **exact lookup by SAM
    type name**, not an ACCESS test. So a wire ``allocationType: 'Small'`` resolves
    here, to ``('UNIV USS', 'Small')``, and strategies 2–11 never run. ``'Large'`` does
    **not** — the ``LARGE`` member's type name is ``'CHAP'`` — so it falls through to
    ``LargeStrategy`` at position 5, which is what actually resolves it. ``'Educational'``,
    ``'Exploratory'`` and ``'Data Analysis'`` all miss here too and are resolved further
    down by ``opportunityName``.

    Both arms are live across the corpus: of 41 payloads, 12 short-circuit here on
    ``'Small'`` and the other 29 fall through (14 ``'Exploratory'``, 6 ``'Large'``,
    5 ``'Data Analysis'``, 4 ``'Educational'``). § 3.2's "may return null and fall
    through" covers only half of it.

    WARNING: The corpus reaches **5 of the 11 strategies**, and growing it 8 -> 41 did not
    move that number at all — so it is a measurement, not a small sample. The other six
    see no traffic at this site and are pinned only by unit tests. See
    ``tests/unit/test_xras_extractors.py::test_five_distinct_strategies_are_exercised``.
    """
    allocation_type = _clean(get_field(action, 'allocationType'))
    if allocation_type is not None:
        return _lookup_by_type_name(allocation_type)

    opportunity = _clean(get_field(action, 'opportunityName'))
    if opportunity is None:
        return None
    lowered = opportunity.lower()
    if 'discover' in lowered:
        return _ALLOCATION_TYPES['DISCOVER_ACCESS']
    if 'explore' in lowered or lowered == 'staff allocations':
        return _ALLOCATION_TYPES['EXPLORE_ACCESS']
    return None


def _nsc_strategy(action) -> Optional[SelectionParms]:
    """`NSCStrategy` — the only strategy keyed on a prefix of ``opportunityName``
    that is also an ``NCAR ``-prefixed name, so it is also the one whose payloads
    take the mnemonic *lab* path. See :func:`resolve_mnemonic_code`."""
    opportunity = _clean(get_field(action, 'opportunityName'))
    if opportunity and opportunity.startswith('NCAR - NSC Allocation Request'):
        return _ALLOCATION_TYPES['NSC']
    return None


def _external_strategy(action) -> Optional[SelectionParms]:
    """`ExternalStrategy` — the only strategy that tests **three** fields, and the
    only one that reads ``allocationType`` as free text rather than as a key."""
    for field in ('requestTitle', 'opportunityName', 'allocationType'):
        value = _clean(get_field(action, field))
        if value and _EXTERNAL_PATTERN.fullmatch(value):
            return _ALLOCATION_TYPES['EXTERNAL']
    return None


def _csl_strategy(action) -> Optional[SelectionParms]:
    """`CSLStrategy` — ``requestTitle`` only. See :data:`_CSL_PATTERN` for the regex
    the plan document mangles."""
    title = _clean(get_field(action, 'requestTitle'))
    if title and _CSL_PATTERN.fullmatch(title):
        return _ALLOCATION_TYPES['CSL']
    return None


def _large_strategy(action) -> Optional[SelectionParms]:
    """`LargeStrategy` — where ``allocationType: 'Large'`` actually resolves, having
    missed the exact lookup in strategy 1.

    WARNING: Java dereferences both fields unguarded here and would ``NullPointerException``
    on an explicit JSON ``null``; the POJO defaults of ``""`` are all that keep it
    standing. We guard, because our schema admits ``None``.
    """
    allocation_type = _clean(get_field(action, 'allocationType'))
    opportunity = _clean(get_field(action, 'opportunityName'))
    if allocation_type == 'Large' or (opportunity and 'Large Allocation' in opportunity):
        return _ALLOCATION_TYPES['LARGE']
    return None


def _opportunity_contains(action, *markers: str) -> bool:
    """Case-**sensitive** substring test on ``opportunityName``, as Java's
    ``String.contains``. Five strategies share this shape."""
    opportunity = _clean(get_field(action, 'opportunityName'))
    if not opportunity:
        return False
    return any(marker in opportunity for marker in markers)


def _small_non_nsf_strategy(action) -> Optional[SelectionParms]:
    """`SmallNonNSFStrategy` — note ``'unsponsored'`` is lowercase in the source while
    its two neighbors are title-cased, and the test is case-sensitive."""
    if _opportunity_contains(action, 'no NSF award', 'unsponsored', 'Exploratory Allocation'):
        return _ALLOCATION_TYPES['SMALL_NON_NSF']
    return None


def _small_nsf_strategy(action) -> Optional[SelectionParms]:
    """`SmallNSFStrategy`. Ordered **after** the non-NSF variant, so an opportunity
    naming both markers is non-NSF."""
    if _opportunity_contains(action, 'w/ NSF', 'with NSF', 'Small Allocation'):
        return _ALLOCATION_TYPES['SMALL_NSF']
    return None


def _classroom_strategy(action) -> Optional[SelectionParms]:
    """`ClassroomStrategy`."""
    if _opportunity_contains(action, 'Classroom/Training', 'Classroom Allocation'):
        return _ALLOCATION_TYPES['CLASSROOM']
    return None


def _data_analysis_strategy(action) -> Optional[SelectionParms]:
    """`DataAnalysisStrategy`."""
    if _opportunity_contains(action, 'Data Analysis Allocation'):
        return _ALLOCATION_TYPES['DATA_ANALYSIS']
    return None


def _asd_univ_strategy(action) -> Optional[SelectionParms]:
    """`ASDUNIVStrategy` — lowercased prefix test, unlike the case-sensitive
    ``contains`` strategies above."""
    opportunity = _clean(get_field(action, 'opportunityName'))
    if opportunity and opportunity.lower().startswith('univ - asd opportunity'):
        return _ALLOCATION_TYPES['ASDUNIV']
    return None


def _asd_ncar_strategy(action) -> Optional[SelectionParms]:
    """`ASDNCARStrategy`."""
    opportunity = _clean(get_field(action, 'opportunityName'))
    if opportunity and opportunity.lower().startswith('ncar - asd opportunity'):
        return _ALLOCATION_TYPES['ASDNCAR']
    return None


#: ``AllocationTypeIdExtractor.SELECTION_PARM_STRATEGY``, in order. First non-``None``
#: wins (``FirstSuccessfulStrategy``). The order is the behavior — ``SmallNonNSF``
#: before ``SmallNSF``, and ``ACCESS`` first because its exact-lookup branch is what
#: lets a payload name its type outright.
_STRATEGIES = (
    _access_strategy,
    _nsc_strategy,
    _external_strategy,
    _csl_strategy,
    _large_strategy,
    _small_non_nsf_strategy,
    _small_nsf_strategy,
    _classroom_strategy,
    _data_analysis_strategy,
    _asd_univ_strategy,
    _asd_ncar_strategy,
)


def select_allocation_type_parms(action) -> Optional[SelectionParms]:
    """Run the eleven-strategy chain. Pure — no session, no error reporting.

    Returns the ``(panel, type)`` pair, or ``None`` if every strategy declined.
    :func:`resolve_allocation_type` is the version that talks to the database and
    reports; this one exists so the chain order can be tested against the corpus
    without one.
    """
    for strategy in _STRATEGIES:
        parms = strategy(action)
        if parms is not None:
            return parms
    return None


def select_allocation_type_mapped(session, action) -> Optional[SelectionParms]:
    """The ``(panel, type)`` pair, preferring the ``opportunityId`` map.

    A map hit yields the pair implied by the FK'd ``allocation_type`` row. A miss
    falls straight through to :func:`select_allocation_type_parms`, so **an empty
    table reproduces the ladder exactly** — which is the whole safety property:
    this adds fidelity when the map is populated and changes nothing when it is
    not.

    This is the one session-taking pre-step the strategy chain is not allowed to
    contain. The chain is pure and sessionless by construction (see the module
    docstring); a database read belongs here.

    WARNING: **Both consumers must use this, not the pure form.**
    :func:`resolve_allocation_type` sets ``project.allocation_type_id``, but
    ``handlers/_allocations.auth_at_panel_meeting`` independently re-derives the
    pair to set ``auth_at_panel_mtg`` on **allocation_transaction** rows. Wiring
    only one of them would let a project's type come from the map while its
    transactions' panel-authorization flag came from the ladder — inconsistent
    rows, written, silently.

    Three ways to miss, all of them falling through rather than raising: no
    ``opportunityId`` on the wire (the field is optional), no row for it, or a
    row whose ``allocation_type`` has no panel. That last one is real —
    ``allocation_type.panel_id`` is **nullable**, so the ``.panel.panel_name``
    traversal would otherwise raise ``AttributeError`` mid-dispatch.
    """
    opportunity_id = get_field(action, 'opportunityId')
    if opportunity_id is not None:
        # `opportunityId` is `_opt_int()`, so it arrives as an int or None —
        # deliberately not run through `_clean`, which is str-only.
        row = (session.query(XrasOpportunityAllocationType)
               .filter(XrasOpportunityAllocationType.opportunity_id == opportunity_id)
               .first())
        panel = getattr(getattr(row, 'allocation_type', None), 'panel', None)
        if panel is not None:
            return SelectionParms(panel.panel_name,
                                  row.allocation_type.allocation_type)

    return select_allocation_type_parms(action)


def resolve_allocation_type(session, action, errs: ActionErrors) -> Optional[AllocationType]:
    """The ``allocation_type`` row this action's project belongs to.

    WARNING: **Resolved by the ``(panel, type)`` pair, never by type name alone.** ``Small``
    names two rows (``UNIV USS`` id 8 and ``UW`` id 3) and so does ``Education``
    (``UNIV USS`` id 9, inactive, and ``UW`` id 18). A name-keyed lookup would put
    university-panel projects on the Wyoming panel roughly at random.

    The pair is resolved to an id at runtime rather than pinned in a constant, per the
    house rule against hardcoding lookup-table PKs. Legacy's ``findByPanelAndType``
    applies no ``active`` filter and neither does this — all twelve pairs the strategy
    chain can produce are active today, and filtering would turn a data change into a
    silent behavior change.

    The pair comes from :func:`select_allocation_type_mapped`, which prefers the
    ``opportunityId`` map and falls back to the ladder — so with an empty table this
    function behaves exactly as it did before the map existed.

    Reports ``Unable to determine allocation type from action data`` when no strategy
    matched, or ``No AllocationType for SelectionParms{…}`` when one did but the pair
    names no row. A map hit cannot reach the second message: the pair is read back off
    a real ``allocation_type`` row, so the join that follows is guaranteed to find it.
    """
    parms = select_allocation_type_mapped(session, action)
    if parms is None:
        errs.report(e.allocation_type_undetermined())
        return None

    row = (session.query(AllocationType)
           .join(Panel, AllocationType.panel_id == Panel.panel_id)
           .filter(Panel.panel_name == parms.panel)
           .filter(AllocationType.allocation_type == parms.allocation_type)
           .first())
    if row is None:
        errs.report(e.no_allocation_type_for_pair(parms.panel, parms.allocation_type))
        return None
    return row


# ---------------------------------------------------------------------------
# Area of interest.
# ---------------------------------------------------------------------------


def primary_fos_num(action, *, warnings: Optional[list] = None) -> Optional[str]:
    """``XrasAction.getPfosNumber()`` — the primary ``fos[]`` entry's ``fosNum``.

    Falls back to the **first** entry when no entry is flagged primary, which is
    legacy's second loop verbatim (`XrasAction:302-311`); ``isPrimary`` is not reliably
    at index 0. Returns ``None`` for an empty array — the caller owns the message.

    When several entries exist and none is flagged, array order is deciding the
    project's research area — recorded into *warnings* rather than silently.
    """
    entries = get_field(action, 'fos') or []
    for entry in entries:
        if get_field(entry, 'isPrimary'):
            return get_field(entry, 'fosNum')
    for entry in entries:
        fos = get_field(entry, 'fosNum')
        if warnings is not None and len(entries) > 1:
            warnings.append(
                f'No fos[] entry is flagged primary; research area taken from '
                f'the first of {len(entries)} (fosNum {fos})')
        return fos
    return None


def resolve_area_of_interest(session, action, errs: ActionErrors,
                             *, warnings: Optional[list] = None
                             ) -> Optional[AreaOfInterest]:
    """The ``area_of_interest`` row named by the primary field of science.

    WARNING: **``fosNum`` is an ``area_of_interest_id``, not an ``fos_aoi.fos_id``.** Legacy
    calls ``areaOfInterestRepository.findOne(fosInt)``, which is a Spring Data
    *primary-key* lookup — the ``fos_aoi`` mapping table is not on this path at all.
    It cannot be: its ``fos_id`` values are five-digit AMIE/XSEDE codes (``10202``,
    ``10501``, …) while XRAS sends ``1``–``40``, the ``area_of_interest`` id space.
    Confirmed against production — every corpus payload's primary ``fosNum`` equals the
    ``area_of_interest_id`` its real project carries. Reading this through ``fos_aoi``
    would file every XRAS project under the wrong research area, silently.

    Non-numeric ``fosNum`` falls back to a lookup by name, mirroring the
    ``NumberFormatException`` arm — ``Integer.decode`` also accepts ``0x``/``0``
    prefixed forms, which ``int(…, 0)`` reproduces closely enough that no real payload
    can tell them apart.

    WARNING: **That fallback is by name, and the names are not byte-equal.** At eight payloads
    the ``fosName`` XRAS sends was the SAM ``area_of_interest`` string *verbatim*; at 41
    it is 90 exact, 2 differing in one letter's case (``fosNum`` 39 —
    ``'Ecological studies'`` here, ``'Ecological Studies'`` on the wire), 0 differing in
    substance. Harmless on the **id** path above, which is what every real payload
    takes. It is the name fallback that would bite: ``area_of_interest`` is
    ``utf8mb3_bin``, so the comparison is case-**sensitive** and a wire spelling that
    differs only in case finds nothing. Pinned by
    ``tests/unit/test_xras_extractors.py::KNOWN_FOS_CASE_DIFFERENCES``.
    """
    fos = primary_fos_num(action, warnings=warnings)
    if fos is None:
        errs.report(e.no_fos_objects())
        return None

    row = None
    try:
        row = session.get(AreaOfInterest, int(str(fos).strip(), 0))
    except (TypeError, ValueError):
        row = (session.query(AreaOfInterest)
               .filter(AreaOfInterest.area_of_interest == fos)
               .first())

    if row is None:
        errs.report(e.aoi_not_in_database(str(fos)))
        return None
    return row


# ---------------------------------------------------------------------------
# Mnemonic code — 24% of legacy's XRAS failures land here.
# ---------------------------------------------------------------------------


def _best_organization(user: User) -> Optional[Organization]:
    """``User.getBestOrganization()`` — the first *current* ``user_organization``.

    "First" is DB order in both implementations; there is no tie-break, and a user
    with two concurrent organizations gets whichever the row order hands over. The
    admin lead-hint route (``dashboards/admin/projects_routes.py``) calls this directly,
    so the mnemonic it suggests to an operator and the one XRAS resolves are the same
    value by construction.
    """
    return next((uo.organization for uo in user.organizations if uo.is_active), None)


def _best_institution(user: User):
    """``User.getBestInstitution()`` — the first current ``user_institution``."""
    return next((ui.institution for ui in user.institutions if ui.is_active), None)


def _pi_wire_organization(action, pi_username: Optional[str]) -> Optional[str]:
    """The PI role's free-text ``person.organization`` from the wire, or ``None``.

    Beyond parity: legacy resolved the mnemonic off the DB user and never read this.
    Null-safe at every hop (roles/person/org each may be absent).
    """
    roles = get_field(action, 'roles') or ()
    pi_role = (next((r for r in roles if get_field(r, 'username') == pi_username), None)
               or next((r for r in roles if get_field(r, 'roleType') == 'PI'), None))
    person = get_field(pi_role, 'person') if pi_role is not None else None
    return _clean(get_field(person, 'organization')) if person is not None else None


def _select_institution(user: User, wire_org: Optional[str], lookup: dict):
    """The current institution to mint the mnemonic from.

    ``getBestInstitution`` took the first current row with no tie-break, assuming one
    current institution (``HashSet`` "should only have best"); a PI with several got an
    arbitrary one. When the wire names an institution (``person.organization``) that
    *uniquely* matches a current row and *resolves* to a code, prefer it — gated on
    resolving so the tie-break can only turn a failure into a success, never the reverse.
    """
    first = _best_institution(user)
    if not wire_org:
        return first
    key = wire_org.casefold()
    matched = [ui.institution for ui in user.institutions
               if ui.is_active and ui.institution is not None
               and (ui.institution.name or '').strip().casefold() == key]
    if len(matched) == 1 and MnemonicCode.resolve_for_institution(matched[0], lookup):
        return matched[0]
    return first


def _organization_parentage(org: Optional[Organization]) -> List[Organization]:
    """The org and its ancestors, deepest first, root last.

    ``DefaultUserAffiliationQuery`` walks ``getParentOrg()`` to the top. Delegates to
    ``Organization.ancestry`` (one cycle-guarded walker shared with the display side).
    """
    return org.ancestry() if org is not None else []


def _lab_level_organization(parentage: List[Organization]) -> Optional[Organization]:
    """``UserLabStrategy.getBestOrgAtLabLevelOrHigher()``.

    The list runs deepest -> root, so index ``len - 3`` is the level-3 org — "lab
    level" at NCAR. A user shallower than that gets their own organization.
    """
    if not parentage:
        return None
    if len(parentage) <= 3:
        return parentage[0]
    return parentage[len(parentage) - 3]


#: "the caller did not resolve the PI", as distinct from "the caller resolved it and
#: there is no such user" — which is ``None`` and must still report.
_UNRESOLVED = object()


def resolve_mnemonic_code(session, action, errs: ActionErrors, *,
                          pi_username: Optional[str],
                          pi=_UNRESOLVED) -> Optional[MnemonicCode]:
    """The three-letter code the new project's projcode will be minted from.

    ``pi_username`` is passed rather than read off the action because resolving it is
    the roster's job (``sam.xras.roster``) and this module must not depend on that
    one — legacy reads ``action.getPiUsername()`` here, which is the same value.

    ``pi`` is the already-resolved ``User`` row, when the caller has one. The roster
    fetched it moments earlier to validate it, so the handler passes it through rather
    than paying for the same ``SELECT`` twice. Omitting it makes this look the PI up
    itself, which is what the extractor tests do and why the parameter is optional
    rather than required.

    WARNING: ``None`` is a **resolved** answer meaning "no such user", and still reports. The
    sentinel is what distinguishes it from "not looked up yet"; a plain
    ``pi=None`` default would silently turn every existing caller into the
    no-such-user arm.

    Three routes, in legacy's order:

    1. ``opportunityName`` starts with ``'NCAR '`` -> the **lab** strategy: walk the
       PI's organization parentage to level 3 and match that. Note this catches the
       NSC opportunity prefix (``'NCAR - NSC Allocation Request'``) as well as the
       NCAR ASD one.
    2. The PI has an institution -> match ``"Name, City"``, then ``"Name"``. With
       several current institutions, the wire's ``person.organization`` breaks the tie
       (``_select_institution``) — beyond parity; legacy read only the DB user.
    3. Otherwise -> match the PI's organization name.

    ``MnemonicCode.build_lookup`` + ``resolve_for_*`` are the existing ports of
    ``UserInstitutionStrategy`` / ``UserOrganizationStrategy``; this reuses them so the
    code XRAS picks and the one the admin create-project form suggests cannot drift.

    **Declared divergence.** Legacy's lab route returns ``null`` *without reporting* —
    ``UserLabStrategy`` has no error arm, unlike the other two — so an NCAR-opportunity
    PI whose lab has no soft link yields a project with no mnemonic, and the failure
    surfaces later and less legibly. We report
    ``Could not determine Mnemonic code for internal PI via organization``, which is
    both true and the string an operator already knows: a lab is an organization, and a
    projcode cannot be minted without a code.

    WARNING: ``ProjectActionCommandFactoryBase:110`` short-circuits on
    ``action.getMnemonicCode()`` before calling any of this. On the XRAS path that is
    dead: ``XrasAction.getMnemonicCode()`` is a hardcoded ``return null``. It is the
    AMIE actions, which share the base class, that supply one. Nothing to port.
    """
    # Operator escape hatch: a per-request mnemonic override short-circuits all
    # resolution below (including the no-affiliation misses). Ignored if the
    # picked code has since been retired — falls through to normal resolution.
    override = lookup_request_override(session, get_field(action, 'requestId'), 'mnemonic')
    if override is not None:
        row = session.get(MnemonicCode, override.mnemonic_code_id)
        if row is not None and row.is_active:
            return row

    if not pi_username:
        errs.report(e.no_affiliation_for_pi(pi_username or ''))
        return None

    user = (User.get_by_username(session, pi_username)
            if pi is _UNRESOLVED else pi)
    if user is None:
        errs.report(e.no_affiliation_for_pi(pi_username))
        return None

    lookup = MnemonicCode.build_lookup(session)
    opportunity = _clean(get_field(action, 'opportunityName')) or ''

    # Declared divergence: legacy falls through to the internal-PI string here.
    if _best_institution(user) is None and _best_organization(user) is None:
        errs.report(e.no_current_affiliation_for_pi(pi_username))
        return None

    if opportunity.startswith('NCAR '):
        org = _best_organization(user)
        lab = _lab_level_organization(_organization_parentage(org))
        code = MnemonicCode.resolve_for_organization(lab, lookup) if lab else None
        if code is None:
            # Divergence, argued above: legacy returns null here in silence.
            errs.report(e.mnemonic_internal_failed(
                pi_username, org.name if org else None, lab.name if lab else None))
            return None
        return _mnemonic_row(session, code)

    institution = _select_institution(
        user, _pi_wire_organization(action, pi_username), lookup)
    if institution is not None:
        code = MnemonicCode.resolve_for_institution(institution, lookup)
        if code is None:
            # Name the other current rows too: the fallback takes the first, and a
            # stale one can shadow the right one (kheyblom, 2026-08-27). When the wire
            # named a resolvable institution, `_select_institution` already preferred it.
            others = [ui.institution for ui in user.institutions
                      if ui.is_active and ui.institution is not None
                      and ui.institution is not institution]
            errs.report(e.mnemonic_external_failed(
                pi_username, institution.name, getattr(institution, 'city', None),
                [(o.name, MnemonicCode.resolve_for_institution(o, lookup)) for o in others]))
            return None
        return _mnemonic_row(session, code)

    org = _best_organization(user)
    code = MnemonicCode.resolve_for_organization(org, lookup) if org else None
    if code is None:
        errs.report(e.mnemonic_internal_failed(pi_username, org.name if org else None))
        return None
    return _mnemonic_row(session, code)


def _mnemonic_row(session, code: str) -> Optional[MnemonicCode]:
    """The ``mnemonic_code`` row for a resolved code.

    ``build_lookup`` yields codes, but callers need the id to mint a projcode
    (``next_projcode``). ``code`` is uniquely indexed, so this is a scalar getter.
    """
    return (session.query(MnemonicCode)
            .filter(MnemonicCode.code == code)
            .filter(MnemonicCode.is_active)
            .first())


# ---------------------------------------------------------------------------
# Contract.
# ---------------------------------------------------------------------------

#: ``ContractNumberExtractor.CORE_NUMBER_EXTRACTOR_PATTERN``. Group 2 is the core: the
#: **last** run of six or more digits, with any non-digit tail allowed after it. The
#: leading ``(.*[^0-9])?`` is greedy, so ``'NSF-1234567890'`` yields ``'1234567890'``
#: and not a six-digit prefix of it.
_CORE_NUMBER_PATTERN = re.compile(r'^(.*[^0-9])?([0-9]{6,})[^0-9]*$')


def has_core_number(grant_number: Optional[str]) -> bool:
    """Whether the number carries a ≥6-digit core — the shape of an award number."""
    return _CORE_NUMBER_PATTERN.match(grant_number or '') is not None


def extract_core_number(grant_number: str) -> str:
    """The ≥6-digit core of an award number, or the input trimmed.

    ``'NSF-2146709'`` -> ``'2146709'``; ``'AGS-2524858'`` -> ``'2524858'``;
    ``'USDA Prime Award No. 2013-67003-20652'`` -> ``'20652'`` is *not* what happens —
    ``20652`` is five digits, so the pattern's last ≥6-digit run is ``67003``… also
    five. That string matches nothing and comes back trimmed, whole. Pure string work,
    no database.
    """
    match = _CORE_NUMBER_PATTERN.match(grant_number or '')
    if match:
        return match.group(2)
    return (grant_number or '').strip()


#: Escape the three characters that mean something to ``LIKE``. The backslash goes
#: first — translating it after ``%`` would double-escape the backslashes just added.
#: ``str.translate`` sidesteps that by doing every mapping in one pass.
_LIKE_ESCAPE = str.maketrans({'\\': r'\\', '%': r'\%', '_': r'\_'})

#: A tie is already an operator-facing error, and the message names every candidate.
#: Two is the largest real tie in production (``1049089`` / ``PLR-1049089``), so this
#: is far above any legitimate result while still bounding what a wildcard could drag
#: into ``xras_action_log.error_messages``.
_MAX_CONTRACT_CANDIDATES = 25


def contract_candidates(session, core: str) -> List[Contract]:
    """Every contract whose number ends in *core* — legacy's suffix match, escaped and capped.

    Shared by the handler and the contract-blockers report so both ask the
    same question of the table.
    """
    # `core` is the payload's `grantNumber` verbatim whenever the ≥6-digit pattern
    # misses, so `%` and `_` off the wire would otherwise reach LIKE as wildcards: a
    # `grantNumber` of `%` matches every contract in the table, and every match is
    # then named in `ambiguous_contract` and stored in `error_messages`. Never an
    # injection — SQLAlchemy binds the parameter — but a third-party broker should
    # not get to choose how many rows this returns. Escaped and capped.
    suffix = core.translate(_LIKE_ESCAPE)
    return (session.query(Contract)
            .filter(Contract.contract_number.ilike(f'%{suffix}', escape='\\'))
            .order_by(Contract.contract_id)
            .limit(_MAX_CONTRACT_CANDIDATES)
            .all())


def resolve_contract(session, grant_number: Optional[str],
                     errs: ActionErrors, *,
                     unresolved: Optional[List[dict]] = None) -> Optional[Contract]:
    """The SAM ``contract`` row behind one ``grants[]`` entry.

    Legacy extracts the core number and runs a single suffix match —
    ``contract_number ILIKE '%<core>'``, ``uniqueResult()``. Two things about that are
    worth knowing before reading the code below.

    **The column is case-sensitive.** ``contract.contract_number`` is ``utf8mb3_bin``,
    so a plain ``LIKE`` undercounts badly; every comparison here uses ``ilike``.

    **``uniqueResult()`` throws on a tie, and ties exist.** Production holds
    ``1049089`` *and* ``PLR-1049089``; ``OPP-1744587`` *and* ``PLR-1744587``;
    ``2146709`` *and* ``AGS-2146709``. A grant citing any of those cores makes legacy
    raise ``NonUniqueResultException`` — which is not an ``AttributeExtractionException``,
    so it escapes the observer entirely and becomes a 500 with no diagnostic.

    **Declared divergence, in three steps:**

    1. Exact match on the **full grant number** first, via ``Contract.get_by_number``
       (whitespace-insensitive, since operators have stored ``'OCE- 1419584'``). This
       is strictly better than legacy and can never be wrong: if the payload names a
       contract SAM holds verbatim, that is the contract.
    2. Otherwise the core-number suffix match. Exactly one row -> that row.
    3. A tie -> report it, naming the candidates, instead of raising. The operator gets
       an actionable 422 where legacy gave them a 500 and an email about a
       ``NonUniqueResultException``.

    Steps 1 and 3 are the divergence; step 2 is legacy. Nothing here can resolve to a
    row legacy would have rejected — only to one it would have crashed on.

    *unresolved*, when given, receives one entry per reported failure —
    ``{number, core, reason: missing|ambiguous, candidates}`` — the structured
    channel the contract-blockers report reads instead of parsing the 422 string.
    """
    grant_number = (grant_number or '').strip()
    if not grant_number:
        errs.report(e.cannot_find_contract('', ''))
        return None

    exact = Contract.get_by_number(session, grant_number)
    if exact is not None:
        return exact

    core = extract_core_number(grant_number)
    candidates = contract_candidates(session, core)

    if not candidates:
        errs.report(e.cannot_find_contract(grant_number, core))
        if unresolved is not None:
            unresolved.append({'number': grant_number, 'core': core,
                               'reason': 'missing', 'candidates': []})
        return None
    if len(candidates) == 1:
        return candidates[0]

    names = [c.contract_number for c in candidates]
    errs.report(e.ambiguous_contract(grant_number, core, names))
    if unresolved is not None:
        unresolved.append({'number': grant_number, 'core': core,
                           'reason': 'ambiguous', 'candidates': names})
    return None
