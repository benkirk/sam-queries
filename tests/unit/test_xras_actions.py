"""XrasActionSchema against real production ``XRAS_post_action.json`` payloads.

The fixtures in ``tests/fixtures/xras/actions/`` are genuine legacy POST bodies,
scrubbed of PII by ``scripts/xras/scrub_payload.py``. They are the only evidence of
the wire contract that exists — ``actionJson`` is never logged at any level, and
legacy's sole record is an email attachment.

Every assertion below encodes a fact measured from those payloads, and each one
contradicted the shape inferred from the Java POJOs alone. If one of these fails after
a schema edit, the schema is wrong, not the test.

Corpus — the original 2x2 over New x Extension, plus the four that closed the
Supplement, Update and Adjustment gaps:

===============================  ==========  ==========================================
fixture                          actionType  why it is here
===============================  ==========  ==========================================
new_ncar4232_failed.json         New         55% failure mode: unreconciled ARC PI,
                                             same username under two roleTypes,
                                             ``grants: []``
new_ncar4253_ok.json             New         full success path; minted projcode UCIR0072
new_uwis0071_existing_ok.json    New         **the Update path** — New against a project
                                             that already exists, so ``requestNumber``
                                             is a projcode. Two ``PI`` roles separated
                                             only by date window, one human under two
                                             usernames with the organization changing,
                                             and the only ``isAccountToBeCreated: true``
extension_ufsu0023_failed.json   Extension   shrink rejected; null PI organization
extension_ucub0166_ok.json       Extension   success; null AM organization,
                                             all-null ``primaryFos``, ``'0.0'`` grant
supplement_ucub0182_ok.json      Supplement  non-empty ``resources``, unlike Extension;
                                             ``allocationType: 'Exploratory'``
supplement_ubrn0027_ok.json      Supplement  one human holding PI *and* Allocation
                                             Manager under one username;
                                             ``allocationType: 'Data Analysis'``
adjustment_uwis0064_manual.json  Adjustment  the spelling legacy never matches — its
                                             ``AdjustProjectActionService`` tests
                                             ``"Adjust"``, so this fell through to the
                                             manual-email fallback
===============================  ==========  ==========================================

Still unsampled, and therefore still unknown: ``Transfer``, ``Renewal``, ``Advance``,
and the co-PI ``roleType`` spelling.
"""

import json

import pytest
from marshmallow import ValidationError

from sam.schemas.forms import XrasActionSchema

from xras_helpers import FIXTURE_DIR, load_fixture


#: Every payload, by fixture stem — the corpus is small enough to parametrize whole.
ALL_FIXTURES = sorted(p.name for p in FIXTURE_DIR.glob('*.json'))


def load_schema(name):
    return XrasActionSchema().load(load_fixture(name))


def test_corpus_is_present():
    """Guard against a silently empty parametrization."""
    assert len(ALL_FIXTURES) == 41, ALL_FIXTURES


# ---------------------------------------------------------------------------
# Whole-corpus invariants
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('name', ALL_FIXTURES)
def test_every_real_payload_loads(name):
    """The headline assertion: the schema accepts real production bytes.

    ⚠️ ``'Date Adjustment'`` is on this list because the wire sends it, not because
    SAM services it — it has no serviceable and parks as ``manual``, exactly as it
    does in legacy. It was unknown until the 2026-08-11 forward; see
    ``XRAS_REIMPLEMENTATION.md`` § 1.4 on why the manual-fallback subject is the only
    record of the action types SAM does not service.
    """
    data = load_schema(name)
    assert data['actionType'] in ('New', 'Extension', 'Supplement', 'Adjustment',
                                  'Date Adjustment')
    assert data['requestNumber']


#: The only empty string in the corpus, pinned so a *second* one still fails this
#: test. ``grants[].subAwardNumber`` is declared by the schema and read by nothing
#: (grep it: the declaration is its only occurrence under ``src/``), so an empty
#: string here reaches no handler and decides nothing.
KNOWN_EMPTY_STRINGS = {
    'supplement_ucit0011_ok.json': ['grants[0].subAwardNumber'],
}


@pytest.mark.parametrize('name', ALL_FIXTURES)
def test_no_empty_strings_in_the_corpus(name):
    """Absent scalars arrive as ``null``, essentially never ``""``.

    ⚠️ **Corrected by the 2026-08-11 forward.** At eight payloads this was absolute —
    ~400 scalar fields, not one empty string — and that measurement is what settled
    the schema on ``allow_none`` rather than empty-string handling. At 41 payloads and
    ~2,000 scalars there is exactly **one**, in a field nothing reads
    (:data:`KNOWN_EMPTY_STRINGS`).

    The design conclusion is unchanged and the emphasis is still right: ``null`` is
    the wire's way of saying absent, and no field SAM *reads* has ever arrived ``""``.
    But "never" was too strong, and the Java ``= ""`` initializers evidently can fire.
    Pinning the exception rather than deleting the test keeps the guard: a new empty
    string in a field that matters still turns this red.
    """
    empties = []

    def walk(node, path=''):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f'{path}.{k}' if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f'{path}[{i}]')
        elif node == '':
            empties.append(path)

    walk(load_fixture(name))
    assert empties == KNOWN_EMPTY_STRINGS.get(name, [])


@pytest.mark.parametrize('name', ALL_FIXTURES)
def test_undeclared_wire_fields_are_dropped(name):
    """``unknown = EXCLUDE`` is mandatory, not stylistic.

    ``requestGrantType``, ``opportunityQA`` and ``resources[].resourceQA`` are sent by
    XRAS and declared by no legacy POJO, so ``@JsonIgnoreProperties(ignoreUnknown)``
    discards them. Without EXCLUDE marshmallow would reject every real payload.
    """
    raw = load_fixture(name)
    # Present on the wire...
    assert 'requestGrantType' in raw
    assert 'opportunityQA' in raw
    # ...and gone after loading.
    data = XrasActionSchema().load(raw)
    assert 'requestGrantType' not in data
    assert 'opportunityQA' not in data
    for resource in data['resources']:
        assert 'resourceQA' not in resource


@pytest.mark.parametrize('name', ALL_FIXTURES)
def test_dates_are_zero_padded_iso_date_only(name):
    """Legacy compares dates with lexicographic ``String.compareTo``.

    That is correct *only* for zero-padded ISO-8601, so the format is load-bearing
    rather than cosmetic. Verified on every date field in the payload, not just the
    top-level pair.
    """
    import re
    iso = re.compile(r'^\d{4}-\d{2}-\d{2}$')
    data = load_schema(name)

    for field in ('actionBeginDate', 'actionEndDate', 'awardDate'):
        if data.get(field) is not None:
            assert iso.match(data[field]), (field, data[field])
    for role in data['roles']:
        for field in ('beginDate', 'endDate'):
            if role.get(field) is not None:
                assert iso.match(role[field]), (field, role[field])
    for grant in data['grants']:
        for field in ('beginDate', 'endDate'):
            if grant.get(field) is not None:
                assert iso.match(grant[field]), (field, grant[field])


@pytest.mark.parametrize('name', ALL_FIXTURES)
def test_request_type_is_not_action_type(name):
    """``requestType`` is useless for dispatch and must not be mistaken for the selector.

    At eight payloads the evidence was that ``requestType`` is a *constant*: every one
    carried ``'New'``, including both Extensions, both Supplements and the Adjustment.
    The 2026-08-11 forward supplies the stronger form — ``requestType`` **varies**
    (``'New'`` ×38, ``'Renewal'`` ×3) and still tracks the action type on nothing:

    ==========  ===============  =====================================
    requestType actionType       fixture
    ==========  ===============  =====================================
    Renewal     Extension        ``extension_unid0003_ok.json``
    Renewal     Supplement       ``supplement_uwku0002_ok.json``
    Renewal     Date Adjustment  ``date_adjustment_uwas0141_manual.json``
    ==========  ===============  =====================================

    ⚠️ Note the third row especially: a ``requestType: 'Renewal'`` exists whose
    ``actionType`` is **not** ``Renewal``. Legacy's selector routes ``Renewal`` to the
    Update handler — but it reads ``actionType``, so none of these three go there.
    Dispatching on ``requestType`` would send all three somewhere different.
    """
    data = load_schema(name)
    assert data['requestType'] in ('New', 'Renewal')
    # The whole point: where the two disagree, only actionType may select.
    if data['actionType'] != data['requestType']:
        assert data['requestType'] != data['actionType']


def test_request_type_renewal_never_implies_a_renewal_action():
    """No payload has ever carried ``actionType: 'Renewal'`` — only ``requestType``.

    So the Update handler's ``Renewal`` arm remains unsampled after 41 payloads, and
    ``tests/stress/scenarios.json::unsampled_renewal`` stays synthetic. Recorded here
    because the three ``requestType: 'Renewal'`` payloads look at a glance like the
    sample that would close it, and they are not.
    """
    renewals = {n for n in ALL_FIXTURES
                if load_fixture(n)['requestType'] == 'Renewal'}
    assert renewals == {
        'extension_unid0003_ok.json',
        'supplement_uwku0002_ok.json',
        'date_adjustment_uwas0141_manual.json',
    }, renewals
    assert not [n for n in ALL_FIXTURES
                if load_fixture(n)['actionType'] == 'Renewal']


# ---------------------------------------------------------------------------
# Type tolerances
# ---------------------------------------------------------------------------

def test_int_arrives_in_string_declared_fields():
    """``awardPeriod`` and ``fosTypeId`` are JSON numbers in String-declared fields.

    Jackson coerces silently; marshmallow raises "Not a valid string" without the
    ``_CoercedStr`` field. Both are ints in all four real payloads.
    """
    raw = load_fixture('new_ncar4253_ok.json')
    assert isinstance(raw['awardPeriod'], int)
    assert isinstance(raw['fos'][0]['fosTypeId'], int)

    data = XrasActionSchema().load(raw)
    assert data['awardPeriod'] == '12'
    assert data['fos'][0]['fosTypeId'] == '500032'


def test_coerced_str_rejects_booleans():
    """``bool`` is an ``int`` subclass — it must not silently become ``'True'``."""
    with pytest.raises(ValidationError) as exc:
        XrasActionSchema().load({'awardPeriod': True})
    assert 'awardPeriod' in exc.value.messages


def test_awarded_amount_is_a_float_formatted_string():
    """``'500000.0'`` — so ``int()`` would raise, and the value keeps its decimal point."""
    data = load_schema('new_ncar4232_failed.json')
    amounts = [r['awardedAmount'] for r in data['resources']]
    assert amounts == ['500000.0', '1.0', '5000.0', '1.0']
    with pytest.raises(ValueError):
        int(amounts[0])


def test_zero_is_a_legitimate_grant_amount():
    """A GRFP fellowship awards ``'0.0'``; it must not read as missing."""
    data = load_schema('extension_ucub0166_ok.json')
    grant = data['grants'][0]
    assert grant['awardedAmount'] == '0.0'
    assert grant['grantNumber'] == 'GRFP-2040434'
    # ...and awardedUnits is null in 2 of 3 observed grants.
    assert grant['awardedUnits'] is None


# ---------------------------------------------------------------------------
# Role vocabulary and identity handling
# ---------------------------------------------------------------------------

def test_role_type_vocabulary_is_space_separated():
    """``'Allocation Manager'``, not ``'AllocationManager'``.

    The camel-case spellings belong to ``GET /v1/requests/role/{role}/{username}``,
    a different vocabulary that must not be conflated with this one.
    """
    observed = set()
    for name in ALL_FIXTURES:
        observed.update(r['roleType'] for r in load_schema(name)['roles'])
    assert observed == {'PI', 'Allocation Manager', 'User'}


def test_same_username_can_hold_two_roles():
    """A PI who is also a ``User`` — so add-every-role consumers must dedupe."""
    roles = load_schema('new_ncar4232_failed.json')['roles']
    by_user = {}
    for role in roles:
        by_user.setdefault(role['username'], []).append(role['roleType'])
    duplicated = {u: rs for u, rs in by_user.items() if len(rs) > 1}
    assert len(duplicated) == 1
    (role_types,) = duplicated.values()
    assert sorted(role_types) == ['PI', 'User']
    # Distinct role ids, so they are genuinely two records not a repeated one.
    ids = [r['requestPeopleRoleId'] for r in roles if r['username'] in duplicated]
    assert len(set(ids)) == 2


def test_is_reconciled_is_true_even_for_an_identity_sam_cannot_find():
    """``isReconciled`` is XRAS's reconciliation state, not SAM's.

    ``gsaha-user-hv1bu`` (scrubbed to a ``placeholder*-user-*`` name) is the
    unreconciled ARC identity whose absence *caused* this payload to fail, and it
    still arrives ``isReconciled: true``. Any handler that trusted this flag would be
    wrong, which is why it stays inert.
    """
    roles = load_schema('new_ncar4232_failed.json')['roles']
    pi = next(r for r in roles if r['roleType'] == 'PI')
    assert '-user-' in pi['username'], pi['username']
    assert pi['person']['isReconciled'] is True


def test_is_account_to_be_created_is_observed_both_ways():
    """Never null and never a string — but no longer always ``false``.

    The four-payload corpus saw only ``false``, which made the coercion look purely
    defensive. UWIS0071 carries the first ``true``: the PI changed institution
    mid-request, and the incoming NCAR username has no account yet. Both values are
    real, so a handler must not assume either.
    """
    observed = set()
    for name in ALL_FIXTURES:
        for role in load_schema(name)['roles']:
            value = role['isAccountToBeCreated']
            assert value is True or value is False, (name, value)
            observed.add(value)
    assert observed == {True, False}

    truths = [r for r in load_schema('new_uwis0071_existing_ok.json')['roles']
              if r['isAccountToBeCreated']]
    assert len(truths) == 1
    assert truths[0]['roleType'] == 'PI'
    assert truths[0]['person']['organization'] == 'NCAR/EDECD'


@pytest.mark.parametrize('value,expected', [
    (None, False),
    (0, False), (1, True), (2, True),
    ('true', True), ('T', True), ('yes', True), ('Y', True),
    ('false', False), ('n', False), ('no', False), ('', False),
    (True, True), (False, False),
])
def test_forgiving_boolean_coercion(value, expected):
    """Legacy's coercion table, reproduced for the one field that uses it."""
    data = XrasActionSchema().load({'roles': [{'isAccountToBeCreated': value}]})
    assert data['roles'][0]['isAccountToBeCreated'] is expected


def test_forgiving_boolean_rejects_nonsense():
    with pytest.raises(ValidationError):
        XrasActionSchema().load({'roles': [{'isAccountToBeCreated': 'maybe'}]})


# ---------------------------------------------------------------------------
# The organization field — 24% of production failures live here
# ---------------------------------------------------------------------------

def test_null_organization_on_the_lead_vs_on_the_manager():
    """A null organization is fatal on the PI and harmless on the Allocation Manager.

    The corpus contains exactly one of each, which is what makes this testable:
    UFSU0023's PI has none and the action failed with
    ``Could not determine Mnemonic code for internal PI via organization``;
    UCUB0166's Allocation Manager has none and the action succeeded.
    """
    failed = load_schema('extension_ufsu0023_failed.json')['roles']
    pi = next(r for r in failed if r['roleType'] == 'PI')
    assert pi['person']['organization'] is None

    ok = load_schema('extension_ucub0166_ok.json')['roles']
    ok_pi = next(r for r in ok if r['roleType'] == 'PI')
    ok_am = next(r for r in ok if r['roleType'] == 'Allocation Manager')
    assert ok_pi['person']['organization'] == 'UNIVERSITY OF COLORADO AT BOULDER'
    assert ok_am['person']['organization'] is None


def test_organization_is_free_text_not_an_institution_key():
    """Inconsistent case and appended role suffixes — the mnemonic lookup's real problem."""
    orgs = set()
    for name in ALL_FIXTURES:
        for role in load_schema(name)['roles']:
            org = role['person']['organization']
            if org:
                orgs.add(org)
    assert 'NORTH CAROLINA STATE UNIVERSITY' in orgs           # shouting
    assert 'Fluid Numerics LLC' in orgs                        # title case, a company
    # ...and one carries a role suffix appended to the institution name.
    assert any(' - Incoming Graduate Student' in o for o in orgs)


# ---------------------------------------------------------------------------
# Nested-array shapes that change handler design
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('name', [
    'extension_ufsu0023_failed.json',
    'extension_ucub0166_ok.json',
])
def test_extension_carries_no_resources(name):
    """Empty on the success as well as the failure, so it is not an error artifact.

    An Extension handler therefore cannot derive its targets from the payload — its
    only input is ``actionEndDate`` against the project's existing allocations.
    """
    assert load_schema(name)['resources'] == []


@pytest.mark.parametrize('name', [
    'new_ncar4232_failed.json',
    'new_ncar4253_ok.json',
])
def test_new_carries_resources(name):
    assert len(load_schema(name)['resources']) == 4


@pytest.mark.parametrize('name,count', [
    ('supplement_ucub0182_ok.json', 2),
    ('supplement_ubrn0027_ok.json', 3),
])
def test_supplement_carries_resources_unlike_extension(name, count):
    """Supplement is the mirror image of Extension, and that drives handler design.

    Extension's array is empty, so its only input is ``actionEndDate``. Supplement's
    is populated and *is* the input. Legacy passes each ``awardedAmount`` straight
    into ``command.supplementAmount(...)``, so it is the **increment, not the new
    total** — and a resource with no existing allocation gets one created rather than
    supplemented.
    """
    resources = load_schema(name)['resources']
    assert len(resources) == count
    for resource in resources:
        assert resource['resourceRepositoryKey'] is not None
        # Float-formatted strings here too, so Decimal conversion is the handler's job.
        assert float(resource['awardedAmount']) > 0


def test_adjustment_is_the_spelling_on_the_wire():
    """``'Adjustment'``, not ``'Adjust'`` — and that difference is a live legacy defect.

    ``AdjustProjectActionService.isServiceable`` tests ``equals("Adjust")``, which no
    payload can satisfy, so the handler has never fired and every Adjustment falls
    through to the manual-email fallback. SAM treats the two as synonyms instead; see
    ``sam.queries.xras_actions.XRAS_ACTION_TYPE_ALIASES``.
    """
    data = load_schema('adjustment_uwis0064_manual.json')
    assert data['actionType'] == 'Adjustment'
    assert data['requestNumber'] == 'UWIS0064'


def test_new_action_can_name_an_existing_project():
    """``actionType: 'New'`` does **not** imply a request token — this is the Update path.

    Legacy dispatches on the pair ``(actionType, does the project exist)``:
    ``AddProjectActionService`` takes ``New`` when the projcode does not exist,
    ``UpdateProjectActionService`` takes ``New`` or ``Renewal`` when it does. So
    ``New`` alone cannot tell a handler which one it is, and ``requestNumber`` is a
    projcode here rather than an ``NCAR####`` token — which is why the resolver has
    to ask the database.
    """
    data = load_schema('new_uwis0071_existing_ok.json')
    assert data['actionType'] == 'New'
    assert data['requestNumber'] == 'UWIS0071'
    assert not data['requestNumber'].startswith('NCAR')


def test_role_type_is_not_unique_and_only_dates_separate_the_duplicates():
    """Two open-ended ``PI`` entries would be ambiguous; the date window disambiguates.

    UWIS0071's PI changed institution mid-request, so the payload carries the old
    username on a *closed* window and the new one on an open window. A pick-first
    resolver — legacy's ``getUsernameByRoleType()`` — has no basis for its choice, and
    the two entries disagree about ``organization``, which is the mnemonic extractor's
    input. This is the measured case behind that defect.
    """
    roles = load_schema('new_uwis0071_existing_ok.json')['roles']
    pis = [r for r in roles if r['roleType'] == 'PI']
    assert len(pis) == 2

    closed = [r for r in pis if r['endDate'] is not None]
    open_ = [r for r in pis if r['endDate'] is None]
    assert len(closed) == 1 and len(open_) == 1

    # Non-overlapping, contiguous windows — the closed one ends the day before.
    assert closed[0]['endDate'] < open_[0]['beginDate']
    # Two usernames, and the organization differs between them.
    assert closed[0]['username'] != open_[0]['username']
    assert closed[0]['person']['organization'] != open_[0]['person']['organization']
    assert open_[0]['person']['organization'] == 'NCAR/EDECD'


def test_one_person_can_hold_pi_and_manager_under_one_username():
    """UBRN0027's PI *is* its Allocation Manager, same username, distinct role ids.

    Distinct from ``test_same_username_can_hold_two_roles`` (a PI who is also a
    ``User``): this is the two *lead* roles collapsing onto one human, so a handler
    resolving "the PI" and "the manager" separately gets the same person twice.
    """
    roles = load_schema('supplement_ubrn0027_ok.json')['roles']
    assert {r['roleType'] for r in roles} == {'PI', 'Allocation Manager'}
    assert len({r['username'] for r in roles}) == 1
    assert len({r['requestPeopleRoleId'] for r in roles}) == 2


def test_opportunity_qa_is_populated_only_on_new_actions():
    """The End User Agreement acknowledgement is collected once, at request creation.

    Non-empty on **every** ``New`` payload and empty on every other action type. It
    is undeclared by any POJO and dropped by ``EXCLUDE``, so SAM throws the
    acknowledgement away — recorded here because that is a product decision, not an
    accident.

    Held at 3 payloads and still holds at 41 (16 New, 25 not), which is now a strong
    enough correlation to state as a rule rather than an observation.
    """
    by_type = {}
    for name in ALL_FIXTURES:
        raw = load_fixture(name)
        by_type.setdefault(raw['actionType'], []).append(len(raw['opportunityQA']))

    assert all(n > 0 for n in by_type['New']), by_type
    assert len(by_type['New']) == 16
    for action_type in ('Extension', 'Supplement', 'Adjustment', 'Date Adjustment'):
        assert by_type[action_type] == [0] * len(by_type[action_type]), by_type

    # HTML in the wire text, which is one more reason not to render it blindly.
    qa = load_fixture('new_uwis0071_existing_ok.json')['opportunityQA'][0]
    assert '<a href=' in qa['attributeSetName']


def test_allocation_type_vocabulary_does_not_match_sams_table():
    """Observed spellings are XRAS's own, and only one of the five names a SAM row.

    ⚠️ **Corrected in Sprint C.** This test used to say the field was "inert on the
    action-post path" and read only on the GET side. It is not: it is the first input
    to the eleven-strategy allocation-type chain, and the strategies read it three
    different ways — as an exact key (``ACCESSStrategy``), as an equality test
    (``LargeStrategy``), and as free text (``ExternalStrategy``). Sprint A wrote that
    claim from the POJOs, before anyone read ``AllocationTypeIdExtractor``.

    What survives is the assertion, and the trap underneath it. Of the five observed
    spellings only ``Small`` names a SAM ``allocation_type`` row — where it is **not
    unique**, appearing under both ``UNIV USS`` and ``UW``. The other four resolve by
    falling *through* to ``opportunityName``. So a handler that mapped this field
    directly would mis-file four types outright and coin-flip the fifth.

    The chain, its order, and the pair each corpus payload resolves to are in
    ``tests/unit/test_xras_extractors.py`` — where six of the eight are checked
    against the allocation type the real project carries in production.
    """
    observed = {load_schema(n)['allocationType'] for n in ALL_FIXTURES}
    assert observed == {'Small', 'Large', 'Educational', 'Exploratory', 'Data Analysis'}


def test_educational_allocation_has_no_grants():
    """``grants: []`` must not be an error — no grant means no ProjectContract."""
    data = load_schema('new_ncar4232_failed.json')
    assert data['allocationType'] == 'Educational'
    assert data['grants'] == []


def test_primary_fos_can_be_an_all_null_object():
    """``grants[].primaryFos`` is present-but-empty in 2 of 3 observed grants.

    Not absent, and not populated — every member null. So the nested FoS schema must
    allow a null ``fosTypeId`` even though it is an int elsewhere.
    """
    empty = load_schema('extension_ucub0166_ok.json')['grants'][0]['primaryFos']
    assert empty == {'fosTypeId': None, 'fosNum': None,
                     'fosName': None, 'fosAbbr': None, 'isPrimary': None}

    populated = load_schema('extension_ufsu0023_failed.json')['grants'][0]['primaryFos']
    assert populated['fosTypeId'] == '500031'
    assert populated['fosNum'] == '29'


def test_primary_panel_is_not_necessarily_first():
    """UFSU0023's primary panel is the second of two."""
    panels = load_schema('extension_ufsu0023_failed.json')['panels']
    assert len(panels) == 2
    assert panels[0]['isPrimary'] is False
    assert panels[1]['isPrimary'] is True
    assert panels[1]['abbr'] == 'CHAP'


# ---------------------------------------------------------------------------
# The AOI lookup key
# ---------------------------------------------------------------------------

def test_primary_fos_num_is_the_aoi_lookup_key():
    """``AreaOfInterestExtractor`` reads the primary entry's ``fosNum``, not ``fosTypeId``."""
    schema = XrasActionSchema()
    data = load_schema('extension_ufsu0023_failed.json')
    # Four entries, primary first here — but selection must be by flag, not position.
    assert len(data['fos']) == 4
    assert schema.primary_fos_num(data) == '29'

    data = load_schema('extension_ucub0166_ok.json')
    assert schema.primary_fos_num(data) == '12'


def test_primary_fos_num_is_none_when_there_are_no_fos_entries():
    """Legacy raises "No FieldOfScience (fos) objects"; the caller owns that message."""
    schema = XrasActionSchema()
    assert schema.primary_fos_num(schema.load({})) is None


# ---------------------------------------------------------------------------
# Defaults for a body that omits everything
# ---------------------------------------------------------------------------

def test_empty_body_loads_to_defaults():
    """A ``{}`` body must not raise — the route needs to audit it, then report errors.

    Malformed *JSON* is a 400; structurally-valid-but-empty JSON is a 422 carrying the
    accumulated validator messages, so the schema must get out of the way here.
    """
    data = XrasActionSchema().load({})
    assert data['actionType'] is None
    assert data['requestNumber'] is None
    for array in ('resources', 'roles', 'fos', 'panels', 'grants'):
        assert data[array] == []
