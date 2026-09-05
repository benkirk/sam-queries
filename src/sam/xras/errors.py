"""The 422 body: XRAS's error vocabulary, reproduced byte for byte.

Why this is a module and not a handful of f-strings
---------------------------------------------------
XRAS administrators read these strings directly, in their "Accounting Service Posts"
panel, and act on them. They are a **wire contract**, not diagnostics — the same
standing as the JSON field names. Legacy has been emitting them for years and the
people fixing failed requests have learned them.

So every message is a named builder with its legacy emitter cited at ``file:line``,
and the punctuation is reproduced exactly. Three of them are typos in the Java that
we keep on purpose:

- ``pi_not_active`` ends with a **colon and a space** and then nothing.
- ``manager_not_in_database`` does too.
- ``manager_not_active`` ends with a **bare space**, no colon.
- ``could_not_convert_amount`` has **two spaces** before ``to float``.

Reproduce, don't tidy. A cleaned-up string is a contract change nobody asked for,
and it would break any grep an operator has saved. Every one of these is pinned by
a test asserting the exact bytes, so a well-meant cleanup fails loudly.

WARNING: ``docs/xras/incoming/XRAS_REIMPLEMENTATION.md`` § 3.4 lists these too, and **it is wrong
in seven places** — a double space dropped, two strings collapsed into one, four
missing entirely. It was written from the POJOs before anyone read the emitters.
``docs/xras/incoming/implemented/XRAS_SPRINT_C.md`` § *The error vocabulary* carries the verified table
and cites this module as the implementation. Trust these two; not § 3.4.

Accumulation, and why a set
---------------------------
Legacy assembles the entire command list first, reporting every problem it finds
into a ``LinkedHashSet`` on ``ProcessingAction``, then raises **once** with the
whole list (``AbstractServiceableProjectActionService.addOrUpdate``). Nothing is
written unless assembly was clean. That is what lets an operator fix a request in
one pass instead of five, and it is the behavior `ActionErrors` exists to provide.

A ``LinkedHashSet`` is insertion-ordered **and deduplicating**, and both halves
matter:

- *Ordered*, because the operator reads the list top to bottom and the order tracks
  the order of assembly.
- *Deduplicating*, because a factory can report the same message more than once for
  structural reasons. Three resources each missing ``awardedAmount`` produce **one**
  ``Awarded amount missing``, not three. ``AddAllocationToProjectActionCommandsFactory``
  even calls ``getResourceName`` twice per resource, so an unmapped key reports twice
  and collapses to one line.

A Python ``list`` would diverge on every multi-resource failure — which is most of
them. Hence ``dict.fromkeys`` semantics.
"""

from typing import Iterable, List, Optional


class XrasActionRejected(Exception):
    """Assembly found problems; nothing was written.

    Carries the accumulated, ordered, deduplicated messages. The route turns this
    into a 422 whose body is the list — see ``webapp/api/xras/actions.py``.

    This is deliberately *not* raised from inside ``management_transaction``: the
    contract is assemble -> check -> execute, so a rejection happens before any
    transaction is opened. A handler that raises this mid-write has a bug.
    """

    def __init__(self, messages: Iterable[str], *, resolved: Optional[dict] = None):
        self.messages: List[str] = list(messages)
        # What assembly resolved before it rejected — the preflight board reads
        # it (unresolved grants, series). The handler sets it; the bag cannot.
        self.resolved: Optional[dict] = resolved
        super().__init__('\n'.join(self.messages))


class ActionErrors:
    """Insertion-ordered, deduplicating accumulator for assembly errors.

    Mirrors legacy's ``ProcessingAction`` error set. Report freely — including the
    same message twice from different code paths — and let the container collapse
    duplicates.

    >>> errs = ActionErrors()
    >>> errs.report(awarded_amount_missing())
    >>> errs.report(awarded_amount_missing())      # same resource shape, second resource
    >>> errs.report(missing_title())
    >>> list(errs)
    ['Awarded amount missing', 'Missing title']
    >>> bool(errs)
    True
    """

    __slots__ = ('_messages',)

    def __init__(self, messages: Optional[Iterable[str]] = None):
        # dict preserves insertion order and gives set semantics on the key.
        self._messages: dict = {}
        for message in messages or ():
            self.report(message)

    def report(self, message: str) -> None:
        """Record one problem. Repeats of an identical message are collapsed."""
        self._messages[message] = None

    def extend(self, messages: Iterable[str]) -> None:
        for message in messages:
            self.report(message)

    def raise_if_any(self) -> None:
        """The ``throwExceptionIfErrors`` moment — call once, between assemble and execute."""
        if self._messages:
            raise XrasActionRejected(self._messages.keys())

    def __iter__(self):
        return iter(self._messages)

    def __len__(self) -> int:
        return len(self._messages)

    def __bool__(self) -> bool:
        return bool(self._messages)

    def __repr__(self) -> str:
        return f'ActionErrors({list(self._messages)!r})'


# ---------------------------------------------------------------------------
# The vocabulary.
#
# Java paths below are relative to
# ~/codes/sam/src/main/java/edu/ucar/cisl/sam/, at tag 2.0.3 — which the plan
# certifies is byte-identical to the deployed code over these packages.
# ---------------------------------------------------------------------------

# -- Project fields and identity -- action/command/ProjectActionCommandFactoryBase


def missing_title() -> str:
    """`ProjectActionCommandFactoryBase:28` — blank ``requestTitle``."""
    return 'Missing title'


def missing_pi_role() -> str:
    """`ProjectActionCommandFactoryBase:39` — lowercase "pi", as in the source."""
    return 'Missing pi role'


def pi_not_in_database(username: str) -> str:
    """`ProjectActionCommandFactoryBase:43` — no trailing punctuation, unlike its
    Allocation Manager counterpart."""
    return f'PI {username} is not in database'


def pi_not_active(username: str) -> str:
    """`ProjectActionCommandFactoryBase:45` — WARNING: trailing colon-space, nothing after it."""
    return f'PI {username} is not an active user: '


def manager_not_in_database(username: str) -> str:
    """`ProjectActionCommandFactoryBase:58` — WARNING: trailing colon-space. The PI variant
    above has none; the inconsistency is legacy's."""
    return f'Allocation Manager {username} is not in database: '


def manager_not_active(username: str) -> str:
    """`ProjectActionCommandFactoryBase:60` — WARNING: trailing space, no colon, and
    "is not active" rather than the PI variant's "is not an active user"."""
    return f'Allocation Manager {username} is not active '


# -- Roster -- action/command/AddUserToProjectActionCommandsFactory


def ambiguous_role(role_type: str, usernames: Iterable[str]) -> str:
    """**Not a legacy string** — legacy resolves this case by coin flip instead.

    ``getUsernameByRoleType`` (`XrasAction:267`) returns the **first** entry surviving
    the date filter and discards the rest, so when a payload names two current PIs it
    is *array order* that decides who leads the project. That is legacy defect 1.

    We filter on the same date window and reject only when more than one still
    survives. Rejecting costs an operator one round trip; guessing wrong assigns a
    project to the wrong principal investigator and nobody finds out. The candidates
    are named because the fix is upstream, in XRAS.
    """
    listed = ', '.join(usernames)
    return f'Multiple {role_type} roles are in range for this action: {listed}'


def username_missing(username: str) -> str:
    """`AddUserToProjectActionCommandsFactory:55`."""
    return f'Username {username} is missing'


def username_inactive(username: str) -> str:
    """`AddUserToProjectActionCommandsFactory:57`."""
    return f'Username {username} is inactive'


# -- Resources -- two variants, and the difference is not cosmetic


def no_resource_for_key(key: str) -> str:
    """`ProjectAllocationActionCommandsFactoryBase:38` — the **allocation** path,
    reporting ``resource.getKey()``."""
    return f'No resource found in SAM corresponding to key {key}'


def no_resource_for_name(resource_name: str) -> str:
    """`AddUserToProjectActionCommandsFactory:81` — the **roster** path, reporting
    ``resource.getResourceName()``. Same failure, different wording and different
    input; both can fire for one action."""
    return f'No resource found in SAM corresponding to name {resource_name}'


# -- Amounts -- action/command/ProjectAllocationActionCommandsFactoryBase


def awarded_amount_missing() -> str:
    """`ProjectAllocationActionCommandsFactoryBase:55` — blank ``awardedAmount``.

    In legacy this message is usually **lost**: the supplement/adjust/update paths
    then unbox a null ``Float`` in ``getTransactionAmount(...) > 0``, throwing an NPE
    before ``throwExceptionIfErrors`` runs, so the operator receives a bare
    ``NullPointerException`` instead. We keep the diagnostic — a declared divergence.
    """
    return 'Awarded amount missing'


def could_not_convert_amount(amount: str) -> str:
    """`ProjectAllocationActionCommandsFactoryBase:66` — WARNING: **two spaces** before
    ``to float``. The source concatenates ``"\\""`` and ``"\\"  to float"``."""
    return f'Could not convert awarded amount "{amount}"  to float'


# -- Dates -- one builder each, because legacy has one string each


def missing_date(which: str) -> str:
    """`ProjectAllocationActionCommandsFactoryBase:85` via ``validateDate(name, …)``.

    ``which`` is ``'begin'`` or ``'end'``. § 3.4 renders this as a single
    "Missing begin/end date for allocation(s)"; there is no such string — there are
    two, and an operator greps for the one they got.
    """
    return f'Missing {which} date for allocation(s)'


def could_not_convert_date(which: str) -> str:
    """`ProjectAllocationActionCommandsFactoryBase:91`. Absent from § 3.4 entirely."""
    return f'Could not convert {which} date for allocation(s)'


# -- End-date validation -- two different validators, two different strings


def extension_end_date_before_existing(existing_end: str) -> str:
    """`ExtendProjectAllocationActionCommandsFactory:42` — the **Extension** path.

    Interpolates a ``yyyy-MM-dd`` date, and reads "date **is** before". This is the
    string UFSU0023 actually returned in production, which makes it the regression
    oracle for the Extension handler.
    """
    return f'Action end date is before existing allocation end date ({existing_end})'


def update_end_date_before_existing(resource_name: str) -> str:
    """`UpdateProjectAllocationActionCommandsFactory:52` — the **Update** path.

    Interpolates a *resource name*, and omits the "is". Do not unify these two: the
    difference tells an operator which path rejected the action.
    """
    return f'Action end date before existing allocation end date for {resource_name}'


def allocation_end_before_commission(end_date: str, resource_name: str) -> str:
    """`DefaultAddAllocationToProjectCommand:63` — an ``IllegalStateException``, so in
    legacy it is **not** observer-reported and becomes a 500 with no diagnostic.

    WARNING: Reproduced with its typo: **no space** before the parenthesis in
    ``resource(%s)``. Legacy never shows this string to an XRAS admin — it escapes as
    an exception — so reproducing the bytes buys nothing on the wire. It is kept
    anyway, because the moment it *does* reach someone it should read the way the
    source says it reads, and inventing a nicer string here would make this the one
    message that cannot be traced back to a line of Java.

    The Add handler reports it instead of raising, which is the divergence: same
    refusal, one an operator can act on.
    """
    return (f'End date of allocation ({end_date}) must be after commission date '
            f'of resource({resource_name}).')


def adjustment_would_go_negative(resource_name: str, current: float,
                                 amount: float) -> str:
    """**Not a legacy string** — legacy has no guard here at all.

    ``Allocation.verifyValidateState()`` checks only the end date, so a negative
    adjustment larger than the allocation would drive ``amount`` below zero and every
    downstream ``remaining = allocated − used`` becomes nonsense. Nothing has ever hit
    it because ``AdjustProjectActionService`` has never serviced an action (defect 4,
    plus a copy-pasted ``> 0`` gate that drops the negatives an adjustment is *for*).

    This port makes that handler live, so the guard arrives with it. It can only
    reject, never corrupt — and a rejected Adjustment goes to a human, which is where
    100% of them go today.

    WARNING: ``,.2f`` rather than ``g``, and the difference is not cosmetic: ``g`` carries six
    significant digits, so an adjustment of **-1,000,001** against an allocation of
    1,000,000 rendered as ``-1e+06`` — a message stating that a number *equal* to the
    balance would take it below zero. The operator has to be able to see which number is
    which. Not ``sam.fmt`` either: that compacts above 100,000 (``68.6M``), which is
    right for a dashboard and wrong for a value someone must reconcile against a wire
    payload.
    """
    return (f'Adjustment of {amount:,.2f} for {resource_name} would take the '
            f'allocation below zero (currently {current:,.2f})')


def all_end_dates_null_or_past(projcode: str) -> str:
    """`SupplementProjectAllocationActionCommandsFactory:73`, duplicated verbatim at
    `AdjustProjectAllocationActionCommandsFactory:72`. Square brackets, no period."""
    return f'All contract and allocation end dates are null or past for project [{projcode}]'


# -- Contracts -- action/domain/model/ContractNumberExtractor


def cannot_find_contract(grant_number: str, core_number: str) -> str:
    """`ContractNumberExtractor:21` — grant number first, then the ≥6-digit core the
    regex extracted from it. Both in escaped double quotes."""
    return f'Cannot find contract for grant number "{grant_number}" ("{core_number}")'


def ambiguous_contract(grant_number: str, core_number: str,
                       candidates: Iterable[str]) -> str:
    """**Not a legacy string** — legacy has none, because it crashes here instead.

    ``getContractEndingIn`` is a suffix match closed with Hibernate's
    ``uniqueResult()``, which raises ``NonUniqueResultException`` when two contracts
    end in the same core number. Production holds three such pairs (``1049089`` /
    ``PLR-1049089``, ``OPP-1744587`` / ``PLR-1744587``, ``2146709`` / ``AGS-2146709``),
    and the exception is not an ``AttributeExtractionException``, so it escapes the
    observer and becomes a 500 with no diagnostic at all.

    Adding a string to the vocabulary is a contract change, so it is worth being clear
    about what this one is *not*: it never replaces a message legacy emits, and it can
    only appear where legacy emitted nothing. The candidates are named because the fix
    is a data fix and the operator cannot make it without knowing which rows collided.
    """
    listed = ', '.join(candidates)
    return (f'Ambiguous contract for grant number "{grant_number}" ("{core_number}"): '
            f'matches {listed}')


# -- Mnemonic -- action/domain/model/mnemoniccode/MnemonicCodeExtractor


#: The legacy sentences, kept verbatim as prefixes: the playbook, the parity oracle
#: and `xras_mnemonic_report._mnemonic_family` all key on them. Detail goes after
#: a colon and is never parsed.
MNEMONIC_EXTERNAL_PREFIX = 'Could not determine Mnemonic code for external PI via institution'
MNEMONIC_INTERNAL_PREFIX = 'Could not determine Mnemonic code for internal PI via organization'


def _alternatives_clause(alternatives) -> str:
    """``; also current: "X" -> ABC, "Y" (no mnemonic link)`` for up to three others."""
    parts = []
    for name, code in list(alternatives or ())[:3]:
        parts.append(f'"{name}" -> {code}' if code else f'"{name}" (no mnemonic link)')
    return f'; also current: {", ".join(parts)}' if parts else ''


def mnemonic_external_failed(username=None, institution=None, city=None,
                             alternatives=()) -> str:
    """`MnemonicCodeExtractor:39`, plus which institution and any other current one."""
    if not institution:
        return MNEMONIC_EXTERNAL_PREFIX
    where = f'"{institution}"' + (f' ({city})' if city else '')
    return (f'{MNEMONIC_EXTERNAL_PREFIX}: {username or "the PI"}\'s current institution '
            f'{where} has no mnemonic link{_alternatives_clause(alternatives)}')


def mnemonic_internal_failed(username=None, organization=None, lab=None) -> str:
    """`MnemonicCodeExtractor:47` — **24% of legacy's XRAS failures** carry this one.

    The cause is data, not code: ``user_organization`` has been frozen since
    2026-07-09, and the soft link is an exact casefolded match of
    ``mnemonic_code.description`` to the organization name
    (``MnemonicCode.build_lookup``), which 153 of 171 active organizations cannot
    satisfy (legacy's ``code LIKE '%name%'`` measured 150). Surfacing it as a
    reviewable 422 is the point; fixing the data is not this sprint's job.
    """
    if not organization:
        return MNEMONIC_INTERNAL_PREFIX
    where = f'"{organization}"' + (f' (lab "{lab}")' if lab and lab != organization else '')
    return (f'{MNEMONIC_INTERNAL_PREFIX}: {username or "the PI"}\'s organization '
            f'{where} has no mnemonic link')


#: Stable substrings of the two no-affiliation messages below. A PI with no
#: resolvable affiliation is mnemonic-unresolvable (no projcode can be minted),
#: so ``row_blockers`` treats these as the mnemonic blocker a per-request code
#: override fixes — even though they carry no MNEMONIC_*_PREFIX (they precede the
#: org/institution resolution the prefixes name). Guarded by
#: ``test_xras_row_blockers`` against the real builders below.
NO_AFFILIATION_MARKERS = (
    'has no current institution or organization',
    'Could not produce affiliation data for PI',
)


def no_affiliation_for_pi(username: str) -> str:
    """`MnemonicCodeExtractor:56`."""
    return f'Could not produce affiliation data for PI {username}'


def no_current_affiliation_for_pi(username: str) -> str:
    """Not a legacy string -- a declared divergence.

    Legacy reaches ``getMnemonicCodeViaOrganization`` for a SAM user with no
    current institution and no current organization and reports the internal-PI
    string, which misleads on an external PI whose affiliation rows the
    upstream sync end-dated (NCAR4262, 2026-08-25). Name the real gap.
    """
    return f'PI {username} has no current institution or organization in SAM'


# -- Area of interest -- action/domain/model/AreaOfInterestExtractor


def no_fos_objects() -> str:
    """`AreaOfInterestExtractor:14` — fires on ``fos: []``. Absent from § 3.4."""
    return 'No FieldOfScience (fos) objects'


def aoi_not_in_database(fos: str) -> str:
    """`AreaOfInterestExtractor:25`."""
    return f'AreaOfInterest (FOS) id is not in database: {fos}'


# -- Allocation type -- action/domain/model/allocationtype/AllocationTypeIdExtractor


def allocation_type_undetermined() -> str:
    """`AllocationTypeIdExtractor:31` — all eleven strategies returned null."""
    return 'Unable to determine allocation type from action data'


def no_allocation_type_for_pair(panel: str, allocation_type: str) -> str:
    """`AllocationTypeIdExtractor:37` — a strategy resolved a ``(panel, type)`` pair
    but no ``allocation_type`` row matches it. Absent from § 3.4.

    The odd rendering is Java's ``SelectionParms.toString()``; reproduced because the
    operator sees it.
    """
    return (f"No AllocationType for SelectionParms{{panel='{panel}', "
            f"type='{allocation_type}'}}")


# -- Transfer -- action/command/TransferProjectAllocationActionCommandsFactory
#
# Transfer routes to the manual-fallback path in this port (zero production traffic,
# no sample payload, and `exchange_allocations` does not fit its 1->N clamping
# semantics). These are defined so the vocabulary is complete and so a future
# Transfer handler starts from the verified strings rather than re-reading the Java.


def transfer_one_source_only() -> str:
    """`TransferProjectAllocationActionCommandsFactory:57`."""
    return 'Transfer supports only one source (negative amount)'


def transfer_requires_source() -> str:
    """`TransferProjectAllocationActionCommandsFactory:67` — absent from § 3.4; a
    *third* arity string distinct from the two either side of it."""
    return 'Transfer requires one source resource (negative amount)'


def transfer_requires_destination() -> str:
    """`TransferProjectAllocationActionCommandsFactory:71`."""
    return 'Transfer requires at least one destination resource (positive amount)'


def transfer_source_has_no_allocation(projcode: str, resource_name: str) -> str:
    """`TransferProjectAllocationActionCommandsFactory:93`."""
    return f'Transfer source project:resource ({projcode}:{resource_name}) has no allocation'


def transfer_credit_exceeds_debit(credit: float, debit: float) -> str:
    """`TransferProjectAllocationActionCommandsFactory:102`.

    WARNING: Java's ``%f`` is **six decimal places**, not Python's ``float`` repr —
    ``1000.000000``, not ``1000.0``. Formatted explicitly here for that reason.
    """
    return (f'Transfer destination credit ({credit:f}) exceeds '
            f'source allowed debit ({debit:f})')
