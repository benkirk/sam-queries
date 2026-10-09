"""Load schemas for the ``POST /api/xras/v1/actions`` body.

These are **not** ``HtmxFormSchema`` subclasses, deliberately. That base is
``ImmutableMultiDict``-shaped: its ``_strip_empty_strings`` pre-load has a ``getlist``
branch for form posts and a plain-dict branch that is a *shallow* filter, so it will
not recurse into five nested arrays — and its empty-string dropping is data loss for a
JSON body rather than convenience. The right family is the plain-``marshmallow``
input schemas in :mod:`sam.schemas.charges` (``BaseChargeSummaryInputSchema``), with
``unknown = EXCLUDE`` set explicitly.

Written against **41** real production payloads (see ``tests/fixtures/xras/actions/``),
not against the Java POJOs alone. The tolerances below are measured, and each one is
load-bearing:

1. **Absent scalars arrive as JSON ``null``, essentially never ``""``.** XRAS always
   sends the key, so the Java ``private String x = ""`` initializers hardly ever fire
   on real traffic. Hence ``allow_none=True`` almost everywhere; ``load_default`` is
   the defensive belt.

   WARNING: At eight payloads this was absolute — ~400 scalar fields, not one empty string —
   and that measurement is what settled this schema on ``allow_none`` rather than
   empty-string handling. At 41 payloads and ~2,000 scalars there is exactly **one**:
   ``grants[].subAwardNumber`` in ``supplement_ucit0011_ok.json``, a field declared
   here and read by nothing. So the design conclusion stands and no field SAM *reads*
   has ever arrived ``""`` — but "never" was too strong, and those initializers
   evidently can fire. Pinned by
   ``tests/unit/xras/test_xras_actions.py::KNOWN_EMPTY_STRINGS``, which still fails on a
   second one.
2. **Ints arrive in String-declared fields.** ``awardPeriod`` is ``12`` and
   ``fos[].fosTypeId`` is ``500006``, both ``private String`` in Java — Jackson coerces
   silently, marshmallow will not. ``_Coerced*`` fields below accept either.
3. **``awardedAmount`` is a float-formatted string** (``'500000.0'``, ``'1.0'``,
   ``'0.0'``). ``int()`` raises on those, and ``'0.0'`` is a legitimate grant amount
   (a GRFP fellowship), so it must not be treated as missing.
4. **Unknown fields are on the wire.** ``requestGrantType``, ``opportunityQA`` and
   ``resources[].resourceQA`` are sent by XRAS and declared by no POJO, so legacy
   discards them. ``unknown = EXCLUDE`` reproduces that. ``opportunityQA`` carries the
   NWSC End User Agreement acknowledgement — including HTML in ``attributeSetName`` —
   which SAM currently throws away. It is non-empty on **all 16** ``New`` payloads and
   empty on all 25 others: the acknowledgement is collected once, when the request is
   created, not on every subsequent action. (Held at 3 payloads, still holds at 41 —
   strong enough now to state as a rule rather than an observation.)

   The 41-payload corpus added **no new undeclared field**: it is still exactly these
   three, which is the best evidence available that the wire is stable.
5. **The forgiving boolean is one field only** — ``roles[].isAccountToBeCreated``.
   Observed ``false`` in every sampled role but one, and ``true`` in that one
   (``new_uwis0071_existing_ok.json``, on the incoming NCAR username of a PI who
   changed institution mid-request). Never null and never a string, so the *coercion*
   is still defensive even though both values now occur. Do not generalize it.

WARNING: ``isReconciled`` and ``isAccountToBeCreated`` are **inert** in legacy: parsed and
never read by any business logic. Parse them — they are contract — but do not wire
them to behavior without deciding to. In particular ``isReconciled`` is XRAS's view
of *its own* reconciliation and arrives ``true`` even for the unreconciled ARC
placeholder identities that SAM cannot find, which is 55% of production failures. A
handler that trusted it would be wrong.
"""

from marshmallow import EXCLUDE, Schema, ValidationError, fields, validate
from sam.schemas.wire import CoercedStr, WireInput, opt_bool, opt_coerced_str, opt_int, opt_str

__all__ = [
    'XrasActionSchema',
    'XrasActionResourceSchema',
    'XrasActionRoleSchema',
    'XrasActionPersonSchema',
    'XrasActionFosSchema',
    'XrasActionPanelSchema',
    'XrasActionGrantSchema',
]

#: Truthy/falsey spellings legacy's one forgiving boolean accepts.
_TRUE_STRINGS = frozenset({'t', 'true', 'y', 'yes'})
_FALSE_STRINGS = frozenset({'f', 'false', 'n', 'no', ''})


class _ForgivingBool(fields.Field):
    """Legacy's ``BooleanUtil``-style coercion, for ``isAccountToBeCreated`` only.

    ``None`` -> ``False``; any integer -> ``!= 0``; ``t/true/y/yes`` -> ``True``;
    ``f/false/n/no/''`` -> ``False``; anything else is an error.
    """

    def deserialize(self, value, attr=None, data=None, **kwargs):
        """Intercept ``None`` ahead of marshmallow's short-circuit.

        ``Field.deserialize`` returns ``None`` immediately when ``allow_none`` is set,
        never reaching ``_deserialize`` — which would give ``null -> None`` instead of
        legacy's ``null -> False``. ``missing_`` is not ``None``, so an absent key still
        falls through to ``load_default``.
        """
        if value is None:
            return False
        return super().deserialize(value, attr, data, **kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in _TRUE_STRINGS:
                return True
            if lowered in _FALSE_STRINGS:
                return False
        raise ValidationError('Not a valid boolean.')


class XrasActionFosSchema(WireInput):
    """A field-of-science entry.

    Used in two places with different shapes, which is why every field is optional:
    ``action.fos[]`` carries ``isPrimary``, while ``grants[].primaryFos`` does not —
    and ``primaryFos`` arrives as an **all-null object** in 2 of 3 observed grants
    (present, but with every member null), so ``fosTypeId`` must be nullable too.

    ``fosNum`` is the AOI lookup key, not ``fosTypeId``: legacy's
    ``AreaOfInterestExtractor`` reads the primary entry's ``fosNum`` and tries
    ``Integer.decode`` first, falling back to a string lookup.
    """

    fosTypeId = opt_coerced_str()
    fosNum = opt_coerced_str()
    fosName = opt_str()
    fosAbbr = opt_str()
    isPrimary = opt_bool()


class XrasActionPersonSchema(WireInput):
    """``roles[].person`` — the requester's identity as XRAS knows it.

    ``organization`` is **free text** and may be ``null``. Observed values span
    ``'UNIVERSITY OF CALIFORNIA AT IRVINE'``, ``'Fluid Numerics LLC'`` and
    ``'North Carolina State University - Incoming Graduate Student'`` — inconsistent
    case, with an appended role suffix in the last. It is the input to the mnemonic
    extractor, whose failures are 24% of production traffic; a null here on the
    **lead** is fatal, while a null on the Allocation Manager is harmless (observed
    both ways, one of each, in the sampled payloads).
    """

    firstName = opt_str()
    middleName = opt_str()
    lastName = opt_str()
    email = opt_str()
    phone = opt_str()
    organization = opt_str()
    academicStatus = opt_str()
    #: Inert in legacy, and ``true`` even for identities SAM cannot find. See module docstring.
    isReconciled = opt_bool()


class XrasActionRoleSchema(WireInput):
    """One ``roles[]`` entry.

    ``roleType`` observed values are ``'PI'``, ``'Allocation Manager'`` and ``'User'``
    — **space separated, not camel case**. These are *not* the ``Pi`` / ``CoPi`` /
    ``AllocationManager`` keys that ``GET /v1/requests/role/{role}/{username}`` maps;
    the two vocabularies are distinct and must not be conflated.

    **The co-PI question is settled** (2026-08-19, superseding the hedge that
    stood here). ``GET /v1/types/roles`` on the live NCAR process returns exactly
    three role types — 13 ``PI``, 14 ``Allocation Manager``, 19 ``User`` — so
    **no co-PI can ever appear on this wire**, and its "unknown spelling" was
    never going to be observed. The generic XRAS product does define ``CoPI`` at
    ``roleTypeId`` 1, but those ids are per-process, which is why NCAR's are
    13/14/19. Confirmed on live data: zero co-PIs across 64 sampled role
    entries, and across 101 role entries in 41 captured fixtures. See
    ``docs/xras/outgoing/XRAS_OUTGOING_QUERIES.md`` § 3.4.

    The field is still **not validated against an enum**, but now for a
    different and better reason: an unrecognised value must still parse and
    still land in the audit row, because a silently-rejected body is far worse
    than an unhandled role. (It also means ``CoPi``'s structurally-empty branch
    in ``webapp/api/xras/requests.py`` is provably empty, not merely unobserved.)

    The same ``username`` can appear under two roles in one payload (observed: a PI
    who is also a ``User``, with distinct ``requestPeopleRoleId``), so consumers that
    add every role to the accounts must dedupe.

    WARNING: **``roleType`` is not unique within a payload, and only the date window
    separates the duplicates.** ``new_uwis0071_existing_ok.json`` carries *two* ``PI``
    entries for the same human under two usernames — one closed
    (``beginDate`` 2026-07-27, ``endDate`` 2026-08-04) and one open
    (``beginDate`` 2026-08-05, ``endDate`` null) — because the PI changed institution
    mid-request. ``person.organization`` differs between them
    (``'UNIVERSITY OF WISCONSIN AT MADISON'`` vs ``'NCAR/EDECD'``), so the mnemonic
    extractor gets a different input depending on which is chosen, and array order is
    not the answer: the open entry happens to be first here, but nothing guarantees
    that. **A resolver must filter on the date window, not pick the first match** —
    this payload is the measured case that legacy's pick-first
    ``getUsernameByRoleType()`` resolves wrongly (defect 1 in
    ``docs/xras/incoming/XRAS_REIMPLEMENTATION.md`` § 9).
    """

    requestPeopleRoleId = opt_int()
    roleType = opt_str()
    username = opt_str()
    beginDate = opt_str()
    endDate = opt_str()
    #: The one forgiving boolean (tolerance 5). Inert in legacy.
    isAccountToBeCreated = _ForgivingBool(load_default=False, allow_none=True)
    person = fields.Nested(XrasActionPersonSchema, load_default=None, allow_none=True)


class XrasActionResourceSchema(WireInput):
    """One ``resources[]`` entry.

    ``resourceRepositoryKey`` joins ``xras_resource_repository_key_resource``.
    ``awardedAmount`` is a float-formatted string (tolerance 3) and is kept as a
    string here — converting to ``Decimal`` is the handler's job, since the error
    message for an unparseable amount belongs in the accumulated 422 list rather
      than in schema validation.

    Note this array is **empty on Extension actions** — observed on both the success
    and the failure — so an Extension handler cannot derive its target resources from
    the payload. Its only input is ``actionEndDate`` against existing allocations.
    **Supplement is the opposite**: both sampled Supplements carry a populated array
    (2 and 3 entries), and it is the handler's whole input.

    On Supplement, ``awardedAmount`` is the **increment, not the new total** — legacy's
    ``SupplementProjectAllocationActionCommandsFactory`` passes
    ``getTransactionAmount(resource)`` straight into ``command.supplementAmount(...)``,
    where ``getTransactionAmount`` is ``Float.valueOf(awardedAmount)``. When the
    project has no allocation for that resource yet, legacy **creates** one instead of
    supplementing, dating it from today to the latest contract (else allocation) end
    date. Beware the path that reaches that decision: legacy tests
    ``getTransactionAmount(resource) > 0`` on a ``Float`` that is ``null`` whenever
    ``awardedAmount`` is blank, which unboxes to an NPE. Do not reproduce — a missing
    amount belongs in the accumulated 422 list.
    """

    actionResourceId = opt_int()
    resourceRepositoryKey = opt_int()
    awardedAmount = opt_coerced_str()
    comments = opt_str()


class XrasActionPanelSchema(WireInput):
    """One ``panels[]`` entry. ``isPrimary`` is not necessarily index 0."""

    type = opt_str()
    name = opt_str()
    abbr = opt_str()
    isPrimary = opt_bool()


class XrasActionGrantSchema(WireInput):
    """One ``grants[]`` entry — the funding award behind the request.

    ``grantNumber`` is an NSF-style award number (``'EAR-2425607'``,
    ``'OCE-2123632'``, ``'GRFP-2040434'``) matched against SAM's ``contract`` table,
    whose text columns are ``utf8mb3_bin`` and therefore case-sensitive — that lookup
    must use ``ilike``, not ``LIKE``.

    ``awardedAmount`` may legitimately be ``'0.0'`` and ``awardedUnits`` is null in 2
    of 3 observed grants, so neither may be treated as "missing". The array itself is
    empty for Educational/Classroom allocations, which must not be an error.
    """

    fundingAgency = opt_str()
    grantNumber = opt_str()
    programOfficerName = opt_str()
    programOfficerEmail = opt_str()
    piName = opt_str()
    title = opt_str()
    beginDate = opt_str()
    endDate = opt_str()
    awardedAmount = opt_coerced_str()
    awardedUnits = opt_str()
    percentageAward = opt_coerced_str()
    subAwardNumber = opt_str()
    primaryFos = fields.Nested(XrasActionFosSchema, load_default=None, allow_none=True)
    isPending = opt_bool()


class XrasActionSchema(WireInput):
    """The ``POST /api/xras/v1/actions`` body.

    ``requestNumber`` is the **projcode** for an action against an existing project
    (Extension, Supplement, Adjustment) and a request token (``NCAR####`` at this
    site) for a New action that mints one — confirmed by legacy's
    ``formatSuccessSubject``, which picks its "Existing XRAS project updated" wording
    precisely when ``requestNumber.equals(projcode)``. So the action selector resolves
    the project by treating this value as a projcode.

    WARNING: **``New`` does not imply a request token.** ``new_uwis0071_existing_ok.json``
    is an ``actionType: 'New'`` whose ``requestNumber`` is the projcode ``UWIS0071``
    of a project that already existed — legacy emitted the "Existing XRAS project
    updated" subject for it. Only the database can tell the two apart.

    ``requestType`` is **not** ``actionType`` and is useless for dispatch. At eight
    payloads the evidence was that it is a *constant* — every one carried ``'New'``,
    including both Extensions, both Supplements and the Adjustment. At 41 the stronger
    form holds: it **varies** (``'New'`` ×38, ``'Renewal'`` ×3) and still tracks the
    action type on nothing. ``extension_unid0003_ok``, ``supplement_uwku0002_ok`` and
    ``date_adjustment_uwas0141_manual`` all carry ``requestType: 'Renewal'`` while
    their ``actionType`` is Extension / Supplement / Date Adjustment respectively.

    WARNING: Note the third especially: legacy routes ``actionType: 'Renewal'`` to the Update
    handler, but none of these three go there — the selector reads ``actionType``.
    Dispatching on ``requestType`` would send all three somewhere different.

    Only ``actionType`` selects a handler — and even that is not enough on its own.
    Legacy dispatches on the **pair** ``(actionType, does the project exist)``:

    =====================================  ===============================================
    service                                selector
    =====================================  ===============================================
    ``AddProjectActionService``            ``New`` and **not** ``exists(projcode)``
    ``UpdateProjectActionService``         (``New`` or ``Renewal``) and ``exists``
    ``ExtendProjectActionService``         ``Extension`` and ``exists``
    ``SupplementProjectActionService``     ``Supplement`` and ``exists``
    ``TransferAllocationActionService``    ``Transfer`` and ``exists``
    ``AdjustProjectActionService``         ``Adjust`` and ``exists`` — **unreachable**
    =====================================  ===============================================

    So "Update" is a *handler*, never an ``actionType``; and the last row never fires
    because XRAS sends ``Adjustment`` while legacy compares against ``Adjust``. See
    ``sam.queries.xras_actions.XRAS_ACTION_TYPE_ALIASES``.

    ``allocationType`` (``Small``, ``Large``, ``Educational``, ``Exploratory``,
    ``Data Analysis`` observed) is **inert on this path**, like ``isReconciled``:
    legacy reads it only on the GET side (``RequestDTO`` / ``Request`` /
    ``RequestFactory``). Its vocabulary does not match SAM's ``allocation_type``
    table, where ``Small`` is not even unique, so do not build a mapping from it
    without deciding to.

    Dates are zero-padded ISO-8601 date-only strings (``'2026-07-28'``) in every date
    field. They are loaded as strings rather than ``fields.Date`` on purpose: legacy
    compares them with lexicographic ``String.compareTo``, which is correct for this
    format, and a malformed date must surface in the accumulated 422 error list rather
    than as a schema-level rejection of the whole body.
    """

    actionId = opt_int()
    actionType = opt_str()
    actionBeginDate = opt_str()
    actionEndDate = opt_str()

    requestId = opt_int()
    requestNumber = opt_str(validate=validate.Length(max=30))
    requestType = opt_str()
    requestAbstract = opt_str()
    requestTitle = opt_str()
    requestShortTitle = opt_str()

    opportunityId = opt_int()
    opportunityType = opt_str()
    opportunityName = opt_str()

    allocationType = opt_str()
    awardDate = opt_str()
    awardPeriod = opt_coerced_str()

    resources = fields.List(fields.Nested(XrasActionResourceSchema), load_default=list)
    roles = fields.List(fields.Nested(XrasActionRoleSchema), load_default=list)
    fos = fields.List(fields.Nested(XrasActionFosSchema), load_default=list)
    panels = fields.List(fields.Nested(XrasActionPanelSchema), load_default=list)
    grants = fields.List(fields.Nested(XrasActionGrantSchema), load_default=list)

    def primary_fos_num(self, data):
        """Return the primary ``fos[].fosNum``, or ``None``.

        This is the AOI lookup key (``AreaOfInterestExtractor``). ``isPrimary`` is not
        reliably index 0, and legacy raises ``"No FieldOfScience (fos) objects"`` when
        the array is empty — the caller owns that message, so this returns ``None``.
        """
        for entry in data.get('fos') or []:
            if entry.get('isPrimary'):
                return entry.get('fosNum')
        return None
