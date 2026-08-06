"""XrasActionSchema against real production ``XRAS_post_action.json`` payloads.

The fixtures in ``tests/fixtures/xras/actions/`` are genuine legacy POST bodies,
scrubbed of PII by ``scripts/xras/scrub_payload.py``. They are the only evidence of
the wire contract that exists — ``actionJson`` is never logged at any level, and
legacy's sole record is an email attachment.

Every assertion below encodes a fact measured from those payloads, and each one
contradicted the shape inferred from the Java POJOs alone. If one of these fails after
a schema edit, the schema is wrong, not the test.

Corpus (2x2 over the two handlers that matter):

===============================  ==========  ==========================================
fixture                          actionType  why it is here
===============================  ==========  ==========================================
new_ncar4232_failed.json         New         55% failure mode: unreconciled ARC PI,
                                             same username under two roleTypes,
                                             ``grants: []``
new_ncar4253_ok.json             New         full success path; minted projcode UCIR0072
extension_ufsu0023_failed.json   Extension   shrink rejected; null PI organization
extension_ucub0166_ok.json       Extension   success; null AM organization,
                                             all-null ``primaryFos``, ``'0.0'`` grant
===============================  ==========  ==========================================
"""

import json
from pathlib import Path

import pytest
from marshmallow import ValidationError

from sam.schemas.forms import XrasActionSchema

FIXTURE_DIR = Path(__file__).parent.parent / 'fixtures' / 'xras' / 'actions'

#: Every payload, by fixture stem — the corpus is small enough to parametrize whole.
ALL_FIXTURES = sorted(p.name for p in FIXTURE_DIR.glob('*.json'))


def load_fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text())


def load_schema(name):
    return XrasActionSchema().load(load_fixture(name))


def test_corpus_is_present():
    """Guard against a silently empty parametrization."""
    assert len(ALL_FIXTURES) == 4, ALL_FIXTURES


# ---------------------------------------------------------------------------
# Whole-corpus invariants
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('name', ALL_FIXTURES)
def test_every_real_payload_loads(name):
    """The headline assertion: the schema accepts real production bytes."""
    data = load_schema(name)
    assert data['actionType'] in ('New', 'Extension')
    assert data['requestNumber']


@pytest.mark.parametrize('name', ALL_FIXTURES)
def test_no_empty_strings_in_the_corpus(name):
    """Absent scalars arrive as ``null``, never ``""``.

    This is the measured fact that inverts the doc's tolerance emphasis: across four
    payloads and ~200 scalar fields there is not one empty string, so the Java
    ``= ""`` field initialisers never fire on real traffic and ``allow_none`` — not
    empty-string handling — is what the schema actually needs.
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
    assert empties == []


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

    All four payloads carry ``requestType: 'New'`` — including both Extensions.
    """
    data = load_schema(name)
    assert data['requestType'] == 'New'


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


def test_is_account_to_be_created_is_false_everywhere_observed():
    """Never null, never a string, never true across all 9 sampled roles."""
    for name in ALL_FIXTURES:
        for role in load_schema(name)['roles']:
            assert role['isAccountToBeCreated'] is False


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
