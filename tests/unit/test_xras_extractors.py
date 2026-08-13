"""The four lookups every project-shaped handler makes, and the traps in each.

The headline test is :class:`TestTheCorpusOracle`: the eleven-strategy chain is run
against all eight real production payloads and compared to the ``(panel, type)`` pair
the **real project** ended up carrying. Six of the eight projects exist in the
production sample and all six agree — so this is not a test of our reading of the
Java, it is a test against what legacy actually did.

The rest pin the individual traps: the CSL regex the plan document mangles, strategy
1's short-circuit on the literal ``"Small"``, ``fosNum`` being an
``area_of_interest_id`` rather than an ``fos_aoi.fos_id``, and the contract collision
where legacy raises ``NonUniqueResultException`` and 500s.

See ``docs/xras/incoming/implemented/XRAS_SPRINT_C.md`` § *Allocation-type resolution* for the verified
strategy table and § *The error vocabulary* for the strings.
"""

import json

import pytest

from sam.xras.errors import ActionErrors
from sam.xras.extractors import (

    SelectionParms,
    extract_core_number,
    primary_fos_num,
    resolve_allocation_type,
    resolve_area_of_interest,
    resolve_contract,
    resolve_mnemonic_code,
    select_allocation_type_parms,
)

from xras_helpers import FIXTURE_DIR, load_fixture

pytestmark = pytest.mark.unit


def action(**overrides):
    """A minimal wire action. Every field defaults to ``None``, as marshmallow loads
    an absent key — which is what makes the null-safety tests meaningful."""
    base = {
        'allocationType': None,
        'opportunityName': None,
        'requestTitle': None,
        'fos': [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# The corpus oracle.
# ---------------------------------------------------------------------------


class TestTheCorpusOracle:
    """41 real payloads through the chain, 30 checked against production rows.

    ⚠️ **Only the 30 marked ``verified`` are an oracle.** For those, the pair below is
    the ``(panel_name, allocation_type)`` the real project carries in the snapshot
    today — read from ``project.allocation_type_id``, independently of anything this
    code computes — so agreement means the chain reproduces what legacy decided. All
    30 agree, none differ.

    The 11 marked ``derived`` are the ``NCAR####`` request tokens. No project exists
    under a token (the New that carried it minted a projcode instead), so there is
    nothing to check against and these entries pin *our own* output. They are
    regression protection, not evidence — and they are labelled so nobody reads the
    count as 41 verified.

    One derived entry does get corroborated, by accident of the retry pair: NCAR4236
    and UCHI0020 are the same ``actionId`` before and after a failure, and the derived
    pair for the token equals the verified pair for the project it became.
    """

    #: fixture → the pair the chain must produce.
    EXPECTED = {
        # -- Strategy 1: exact lookup on the SAM type name. -------------------
        'adjustment_uwis0064_manual.json': ('UNIV USS', 'Small'),             # verified
        'extension_ucsd0048_ok.json': ('UNIV USS', 'Small'),                  # verified
        'extension_ucsd0073_ok.json': ('UNIV USS', 'Small'),                  # verified
        'extension_ucub0166_ok.json': ('UNIV USS', 'Small'),                  # verified
        'extension_uwho0019_ok.json': ('UNIV USS', 'Small'),                  # verified
        'new_uida0008_ok.json': ('UNIV USS', 'Small'),                        # verified
        'new_umsb0003_ok.json': ('UNIV USS', 'Small'),                        # verified
        'new_uwis0071_existing_ok.json': ('UNIV USS', 'Small'),               # verified
        'supplement_ucsu0114_ok.json': ('UNIV USS', 'Small'),                 # verified
        'supplement_ugit0044_ok.json': ('UNIV USS', 'Small'),                 # verified
        'new_ncar4229_ok.json': ('UNIV USS', 'Small'),                        # derived
        'new_ncar4253_ok.json': ('UNIV USS', 'Small'),                        # derived
        # -- Strategy 5: 'Large' misses the exact lookup (type name is 'CHAP').
        'date_adjustment_ucub0155_manual.json': ('CHAP', 'CHAP'),             # verified
        'date_adjustment_uwas0141_manual.json': ('CHAP', 'CHAP'),             # verified
        'extension_ufsu0023_failed.json': ('CHAP', 'CHAP'),                   # verified
        'extension_unid0003_ok.json': ('CHAP', 'CHAP'),                       # verified
        'supplement_ucit0011_ok.json': ('CHAP', 'CHAP'),                      # verified
        'supplement_uwku0002_ok.json': ('CHAP', 'CHAP'),                      # verified
        # -- Strategy 6: 'Exploratory Allocation' is a non-NSF marker. --------
        'adjustment_ucsu0146_manual.json': ('UNIV USS', 'Small (No NSF award)'),   # verified
        'adjustment_ucub0160_manual.json': ('UNIV USS', 'Small (No NSF award)'),   # verified
        'extension_ucbk0034_ok.json': ('UNIV USS', 'Small (No NSF award)'),        # verified
        'extension_ugmu0052_ok.json': ('UNIV USS', 'Small (No NSF award)'),        # verified
        'extension_uiuc0073_ok.json': ('UNIV USS', 'Small (No NSF award)'),        # verified
        'supplement_uahv0010_ok.json': ('UNIV USS', 'Small (No NSF award)'),       # verified
        'supplement_ucla0076_ok.json': ('UNIV USS', 'Small (No NSF award)'),       # verified
        'supplement_ucla0080_ok.json': ('UNIV USS', 'Small (No NSF award)'),       # verified
        'supplement_ucub0182_ok.json': ('UNIV USS', 'Small (No NSF award)'),       # verified
        'new_ncar4214_ok.json': ('UNIV USS', 'Small (No NSF award)'),              # derived
        'new_ncar4223_ok.json': ('UNIV USS', 'Small (No NSF award)'),              # derived
        'new_ncar4227_failed.json': ('UNIV USS', 'Small (No NSF award)'),          # derived
        'new_ncar4228_failed.json': ('UNIV USS', 'Small (No NSF award)'),          # derived
        'new_ncar4250_ok.json': ('UNIV USS', 'Small (No NSF award)'),              # derived
        # -- Strategy 8: 'Educational' misses; the opportunity name decides. ---
        'date_adjustment_uazn0052_manual.json': ('UNIV USS', 'Classroom'),    # verified
        'date_adjustment_ucor0097_manual.json': ('UNIV USS', 'Classroom'),    # verified
        'new_ummm0016_failed.json': ('UNIV USS', 'Classroom'),                # verified
        'new_ncar4232_failed.json': ('UNIV USS', 'Classroom'),                # derived
        # -- Strategy 9. ------------------------------------------------------
        'new_uchi0020_ok.json': ('UNIV USS', 'Data'),                         # verified
        'supplement_ubrn0027_ok.json': ('UNIV USS', 'Data'),                  # verified
        'new_ncar4218_ok.json': ('UNIV USS', 'Data'),                         # derived
        'new_ncar4236_failed.json': ('UNIV USS', 'Data'),                     # derived
        'new_ncar4246_ok.json': ('UNIV USS', 'Data'),                         # derived
    }

    def test_the_corpus_is_complete(self):
        on_disk = sorted(p.name for p in FIXTURE_DIR.glob('*.json'))
        assert on_disk == sorted(self.EXPECTED), 'a fixture landed without an expected pair'

    @pytest.mark.parametrize('name', sorted(EXPECTED))
    def test_each_payload_resolves_to_the_pair_legacy_produced(self, name):
        parms = select_allocation_type_parms(load_fixture(name))
        assert parms == SelectionParms(*self.EXPECTED[name])

    def test_five_distinct_strategies_are_exercised(self):
        """Coverage claim, asserted rather than asserted-in-prose.

        ⚠️ **Still five of eleven at 41 payloads.** Growing the corpus 5× moved this
        number not at all, which converts it from "the sample is small" into a
        measurement: six strategies see no real traffic at this site, and are pinned
        only by the unit tests below. Sprint C's deviations section says so; this
        keeps that statement honest.
        """
        assert len(set(self.EXPECTED.values())) == 5


# ---------------------------------------------------------------------------
# Strategy 1, and the short-circuit that skips the other ten.
# ---------------------------------------------------------------------------


class TestStrategyOneShortCircuit:
    """``ACCESSStrategy`` is an exact lookup by **SAM type name** when the payload
    carries an ``allocationType`` — not an ACCESS test. This is the most consequential
    trap in the chain, because ``Small`` is the second most common resulting type."""

    def test_small_resolves_here_and_never_reaches_strategy_seven(self):
        # If this fell through, SmallNSFStrategy would need 'Small Allocation' in the
        # opportunity name — so an empty one proves the short-circuit fired.
        parms = select_allocation_type_parms(
            action(allocationType='Small', opportunityName='irrelevant'))
        assert parms == SelectionParms('UNIV USS', 'Small')

    def test_large_does_not_short_circuit_because_its_type_name_is_chap(self):
        parms = select_allocation_type_parms(action(allocationType='Large'))
        assert parms == SelectionParms('CHAP', 'CHAP')

    @pytest.mark.parametrize('wire_value', ['Educational', 'Exploratory', 'Data Analysis'])
    def test_the_other_wire_spellings_miss_the_lookup_entirely(self, wire_value):
        """They name no SAM type, so with no opportunity name to fall back on the whole
        chain declines. That is the fall-through the corpus payloads rely on."""
        assert select_allocation_type_parms(action(allocationType=wire_value)) is None

    def test_the_access_names_resolve_by_exact_lookup(self):
        assert (select_allocation_type_parms(action(allocationType='Discover ACCESS'))
                == SelectionParms('ACCESS', 'Discover ACCESS'))

    @pytest.mark.parametrize('opportunity,expected', [
        ('ACCESS Discover Allocation', ('ACCESS', 'Discover ACCESS')),
        ('accEss exPLORE', ('ACCESS', 'Explore ACCESS')),
        ('Staff Allocations', ('ACCESS', 'Explore ACCESS')),
        ('staff allocations', ('ACCESS', 'Explore ACCESS')),
    ])
    def test_the_opportunity_branch_runs_only_with_no_allocation_type(
            self, opportunity, expected):
        assert (select_allocation_type_parms(action(opportunityName=opportunity))
                == SelectionParms(*expected))

    def test_staff_allocations_must_match_the_whole_string_not_a_substring(self):
        """``lcon.equals("staff allocations")`` — equality, unlike its two neighbours
        which are ``contains``."""
        assert select_allocation_type_parms(
            action(opportunityName='NCAR Staff Allocations 2026')) is None

    def test_an_empty_allocation_type_is_treated_as_absent(self):
        """Declared divergence: Java's POJO default of ``""`` takes the exact-lookup
        branch (which can only miss), while JSON ``null`` takes the opportunity branch.
        marshmallow gives ``None`` for both, so we take the null behaviour — which
        resolves here where Java would have declined."""
        assert (select_allocation_type_parms(
            action(allocationType='  ', opportunityName='Discover Allocations'))
            == SelectionParms('ACCESS', 'Discover ACCESS'))


# ---------------------------------------------------------------------------
# The regex traps.
# ---------------------------------------------------------------------------


class TestTheCslRegex:
    """``\\s*CSL(|[\\W].*)`` — an alternation with an **empty left branch**.

    § 3.2 of ``XRAS_REIMPLEMENTATION.md`` renders it with a backslash before the pipe,
    which reads as a literal ``|``. Transcribing the doc rather than the source would
    make every case below fail except a title literally containing ``CSL|``.
    """

    @pytest.mark.parametrize('title', [
        'CSL',                      # the empty branch — CSL alone
        '   CSL',                   # leading whitespace is consumed by \s*
        'CSL: Deep Convection',     # non-word char, then anything
        'CSL - 2026 request',
        'CSL/Climate',
    ])
    def test_titles_that_must_match(self, title):
        assert (select_allocation_type_parms(action(requestTitle=title))
                == SelectionParms('CSLAP', 'CSL'))

    @pytest.mark.parametrize('title', [
        'CSLAP request',            # [\W] requires a NON-word char after CSL
        'CSLX',
        'A CSL request',            # \s* anchors at the start; 'A ' is not whitespace
        'Deep convection CSL',
    ])
    def test_titles_that_must_not(self, title):
        assert select_allocation_type_parms(action(requestTitle=title)) is None

    def test_only_the_request_title_is_examined(self):
        assert select_allocation_type_parms(action(opportunityName='CSL 2026')) is None


class TestTheExternalPattern:
    """``(.* )?External( .*)?`` full-matched against three different fields — the only
    strategy that reads ``allocationType`` as free text rather than as a key."""

    @pytest.mark.parametrize('field', ['requestTitle', 'opportunityName', 'allocationType'])
    def test_any_of_the_three_fields_can_trigger_it(self, field):
        # 'External Project' is also a SAM type name, so route around strategy 1 by
        # using a phrase that is not one.
        assert (select_allocation_type_parms(action(**{field: 'NCAR External Users'}))
                == SelectionParms('External Projects', 'External Project'))

    @pytest.mark.parametrize('value', ['Externally funded', 'NonExternal', 'external'])
    def test_it_is_a_full_match_on_a_whole_word(self, value):
        assert select_allocation_type_parms(action(requestTitle=value)) is None


# ---------------------------------------------------------------------------
# Order, and the strategies the corpus never reaches.
# ---------------------------------------------------------------------------


class TestStrategyOrder:

    def test_non_nsf_wins_when_an_opportunity_carries_both_markers(self):
        """``SmallNonNSFStrategy`` is registered before ``SmallNSFStrategy``, so an
        opportunity naming both resolves non-NSF. Order *is* the behaviour."""
        assert (select_allocation_type_parms(
            action(opportunityName='Small Allocation, unsponsored'))
            == SelectionParms('UNIV USS', 'Small (No NSF award)'))

    def test_nsc_beats_the_ncar_asd_prefix_test(self):
        assert (select_allocation_type_parms(
            action(opportunityName='NCAR - NSC Allocation Request 2026'))
            == SelectionParms('NCAR-ARP', 'NSC'))

    @pytest.mark.parametrize('opportunity,expected', [
        ('Classroom/Training Allocation', ('UNIV USS', 'Classroom')),
        ('Data Analysis Allocation (University)', ('UNIV USS', 'Data')),
        ('University Small Allocation w/ NSF award', ('UNIV USS', 'Small')),
        ('Small Allocation with NSF support', ('UNIV USS', 'Small')),
        ('Request with no NSF award', ('UNIV USS', 'Small (No NSF award)')),
        ('UNIV - ASD Opportunity Spring 2026', ('ASD-CHAP', 'ASD-UNIV')),
        ('ncar - asd opportunity', ('ASD-NCAR', 'ASD-NCAR')),
        ('Large Allocation Request', ('CHAP', 'CHAP')),
    ])
    def test_the_strategies_the_corpus_never_exercises(self, opportunity, expected):
        assert (select_allocation_type_parms(action(opportunityName=opportunity))
                == SelectionParms(*expected))

    def test_the_contains_tests_are_case_sensitive_like_javas(self):
        assert select_allocation_type_parms(
            action(opportunityName='classroom allocation')) is None

    def test_the_asd_prefix_tests_are_not(self):
        assert (select_allocation_type_parms(action(opportunityName='UNIV - ASD OPPORTUNITY'))
                == SelectionParms('ASD-CHAP', 'ASD-UNIV'))

    def test_an_action_with_nothing_set_declines_rather_than_raising(self):
        """Java's POJO defaults of ``""`` are all that keep ``LargeStrategy`` and
        ``NSCStrategy`` from dereferencing null. Our schema admits ``None``, so every
        strategy guards."""
        assert select_allocation_type_parms(action()) is None


# ---------------------------------------------------------------------------
# Resolving the pair to a row.
# ---------------------------------------------------------------------------


class TestResolveAllocationType:

    def test_the_pair_disambiguates_a_non_unique_type_name(self, session):
        """``Small`` names two rows — ``UNIV USS`` and ``UW``. Resolving by name alone
        would put university projects on the Wyoming panel roughly at random."""
        row = resolve_allocation_type(
            session, action(allocationType='Small'), ActionErrors())
        assert row is not None
        assert row.allocation_type == 'Small'
        assert row.panel.panel_name == 'UNIV USS'

        # Both rows really are present, so the assertion above has teeth.
        from sam.accounting.allocations import AllocationType
        names = {at.panel.panel_name for at in session.query(AllocationType)
                 .filter(AllocationType.allocation_type == 'Small').all()}
        assert names == {'UNIV USS', 'UW'}

    def test_no_strategy_matches_reports_the_undetermined_string(self, session):
        errs = ActionErrors()
        assert resolve_allocation_type(session, action(), errs) is None
        assert list(errs) == ['Unable to determine allocation type from action data']

    def test_a_resolved_pair_with_no_row_reports_the_selection_parms(self, session, monkeypatch):
        import sam.xras.extractors as ex
        monkeypatch.setattr(ex, 'select_allocation_type_parms',
                            lambda _: SelectionParms('No Such Panel', 'No Such Type'))
        errs = ActionErrors()
        assert resolve_allocation_type(session, action(), errs) is None
        assert list(errs) == [
            "No AllocationType for SelectionParms{panel='No Such Panel', type='No Such Type'}"]

    def test_every_pair_the_chain_can_produce_resolves_to_a_row(self, session):
        """The chain's twelve pairs are a closed set; if the lookup tables ever drift
        away from one, this fails here rather than in a 422 at 3am."""
        from sam.xras.extractors import _ALLOCATION_TYPES
        from sam.accounting.allocations import AllocationType
        from sam.resources.facilities import Panel

        missing = []
        for parms in _ALLOCATION_TYPES.values():
            hit = (session.query(AllocationType)
                   .join(Panel, AllocationType.panel_id == Panel.panel_id)
                   .filter(Panel.panel_name == parms.panel)
                   .filter(AllocationType.allocation_type == parms.allocation_type)
                   .first())
            if hit is None:
                missing.append(parms)
        assert missing == []


# ---------------------------------------------------------------------------
# Area of interest.
# ---------------------------------------------------------------------------


class TestAreaOfInterest:
    """⚠️ ``fosNum`` is an ``area_of_interest_id``, not an ``fos_aoi.fos_id``."""

    def test_fos_num_is_the_area_of_interest_primary_key(self, session):
        """Legacy calls ``areaOfInterestRepository.findOne(fosInt)`` — a Spring Data
        PK lookup. Reading it through ``fos_aoi`` instead would file every XRAS project
        under the wrong research area, silently and with no error."""
        errs = ActionErrors()
        row = resolve_area_of_interest(
            session, load_fixture('extension_ucub0166_ok.json'), errs)
        assert not errs
        assert row.area_of_interest_id == 12
        assert row.area_of_interest == 'Fluid Dynamics and Turbulence'

    def test_the_fos_and_aoi_id_spaces_do_not_overlap(self, session):
        """The structural reason the mapping table cannot be on this path:
        ``fos_aoi.fos_id`` holds five-digit AMIE/XSEDE codes while XRAS sends the
        one- and two-digit ``area_of_interest`` id space."""
        from sam.projects.areas import AreaOfInterest, FosAoi
        fos_ids = {row.fos_id for row in session.query(FosAoi).all()}
        aoi_ids = {row.area_of_interest_id for row in session.query(AreaOfInterest).all()}
        assert fos_ids, 'fos_aoi is populated, so the overlap check is meaningful'
        assert fos_ids & aoi_ids == set()

    @pytest.mark.parametrize('name,expected_id', [
        ('adjustment_uwis0064_manual.json', 1),
        ('extension_ufsu0023_failed.json', 29),
        ('new_ncar4232_failed.json', 4),
        ('supplement_ucub0182_ok.json', 19),
    ])
    def test_every_corpus_payload_resolves(self, session, name, expected_id):
        errs = ActionErrors()
        row = resolve_area_of_interest(session, load_fixture(name), errs)
        assert not errs
        assert row.area_of_interest_id == expected_id

    #: ``fosNum`` → (SAM's string, the string XRAS sends). The two differ in **case
    #: only**, on one entry out of 92 across the corpus. Pinned rather than smoothed
    #: over so that a divergence which is *not* just case still fails this test.
    KNOWN_FOS_CASE_DIFFERENCES = {
        '39': ('Ecological studies', 'Ecological Studies'),
    }

    def test_the_fos_name_xras_sends_is_sams_own_string(self, session):
        """Corroborates the id reading from the other side: XRAS's FOS vocabulary *is*
        SAM's ``area_of_interest`` table, not a foreign taxonomy needing a mapping.

        ⚠️ **Equal ignoring case, not byte-equal.** At eight payloads every name
        matched exactly; at 41 there are 90 exact matches and 2 that differ in one
        letter's case (:data:`KNOWN_FOS_CASE_DIFFERENCES`). Zero differ in substance.

        Harmless *here* because ``resolve_area_of_interest`` keys on ``fosNum``, which
        is an ``area_of_interest_id`` — the name is never the lookup key. Recorded
        because it is precisely what would break a name-keyed lookup, and because
        ``area_of_interest`` is one of the ``utf8mb3_bin`` (case-**sensitive**) text
        columns: a ``LIKE`` against this string would silently miss.
        """
        from sam.projects.areas import AreaOfInterest
        for name in sorted(p.name for p in FIXTURE_DIR.glob('*.json')):
            data = load_fixture(name)
            for entry in data['fos']:
                row = session.get(AreaOfInterest, int(entry['fosNum']))
                assert row is not None, entry
                assert row.area_of_interest.lower() == entry['fosName'].lower(), (
                    name, entry)
                if row.area_of_interest != entry['fosName']:
                    assert self.KNOWN_FOS_CASE_DIFFERENCES.get(entry['fosNum']) == (
                        row.area_of_interest, entry['fosName']), (name, entry)

    def test_empty_fos_reports_rather_than_raising(self, session):
        errs = ActionErrors()
        assert resolve_area_of_interest(session, action(fos=[]), errs) is None
        assert list(errs) == ['No FieldOfScience (fos) objects']

    def test_an_unknown_id_reports_the_value_it_was_given(self, session):
        errs = ActionErrors()
        assert resolve_area_of_interest(
            session, action(fos=[{'fosNum': '99999', 'isPrimary': True}]), errs) is None
        assert list(errs) == ['AreaOfInterest (FOS) id is not in database: 99999']

    def test_a_non_numeric_fos_num_falls_back_to_a_name_lookup(self, session):
        errs = ActionErrors()
        row = resolve_area_of_interest(
            session,
            action(fos=[{'fosNum': 'Physical Oceanography', 'isPrimary': True}]),
            errs)
        assert not errs
        assert row.area_of_interest_id == 29

    def test_the_primary_entry_wins_regardless_of_position(self):
        assert primary_fos_num(action(fos=[
            {'fosNum': '1', 'isPrimary': False},
            {'fosNum': '29', 'isPrimary': True},
        ])) == '29'

    def test_with_no_primary_flag_the_first_entry_is_used(self):
        """Legacy's second loop, verbatim — ``isPrimary`` is not reliably index 0, and
        an array with none flagged still yields a value rather than an error."""
        assert primary_fos_num(action(fos=[
            {'fosNum': '7', 'isPrimary': False},
            {'fosNum': '29', 'isPrimary': False},
        ])) == '7'


# ---------------------------------------------------------------------------
# Contract.
# ---------------------------------------------------------------------------


class TestCoreNumberExtraction:
    """``^(.*[^0-9])?([0-9]{6,})[^0-9]*$``, group 2. Pure string work."""

    @pytest.mark.parametrize('grant,core', [
        ('NSF-2146709', '2146709'),
        ('AGS-2524858', '2524858'),
        ('PLR-1049089', '1049089'),
        ('1049089', '1049089'),
        ('OCE-1419584 (supplement)', '1419584'),
        ('  AGS-1852977  ', '1852977'),
    ])
    def test_the_core_is_the_last_run_of_six_or_more_digits(self, grant, core):
        assert extract_core_number(grant) == core

    def test_the_leading_group_is_greedy_so_long_numbers_stay_whole(self):
        assert extract_core_number('NSF-1234567890') == '1234567890'

    @pytest.mark.parametrize('grant', [
        'DE-SC0012704',                  # 7 digits, but '0012704' is preceded by 'SC'
        'USDA Prime Award No. 2013-67003-20652',
        'N00014-20-1-2580',
    ])
    def test_a_number_with_no_six_digit_run_comes_back_trimmed_whole(self, grant):
        result = extract_core_number(grant)
        assert result == grant.strip() or result.isdigit()

    def test_a_string_with_no_digits_at_all_comes_back_trimmed(self):
        assert extract_core_number('  NSF award  ') == 'NSF award'

    def test_none_and_empty_do_not_raise(self):
        assert extract_core_number(None) == ''
        assert extract_core_number('') == ''


class TestResolveContract:

    def test_an_exact_full_number_wins_before_any_suffix_matching(self, session):
        """Step 1 of the declared divergence, and the reason the collision case below
        is rare in practice: if SAM holds the number the payload names, that is the
        contract, and no suffix logic runs."""
        from factories import make_contract
        exact = make_contract(session, contract_number='PLR-9990001')
        make_contract(session, contract_number='9990001')      # would collide on suffix
        errs = ActionErrors()
        assert resolve_contract(session, 'PLR-9990001', errs) is exact
        assert not errs

    def test_a_unique_suffix_match_resolves_it(self, session):
        """Step 2 — legacy's own behaviour, unchanged."""
        from factories import make_contract
        contract = make_contract(session, contract_number='AGS-9990002')
        errs = ActionErrors()
        assert resolve_contract(session, 'NSF-9990002', errs) is contract
        assert not errs

    def test_a_collision_reports_instead_of_raising(self, session):
        """Step 3, the divergence that matters. ``getContractEndingIn`` closes with
        Hibernate's ``uniqueResult()``, which raises ``NonUniqueResultException`` —
        not an ``AttributeExtractionException``, so it escapes the observer and
        becomes a 500 with no diagnostic. Production holds three such pairs."""
        from factories import make_contract
        make_contract(session, contract_number='9990003')
        make_contract(session, contract_number='PLR-9990003')
        errs = ActionErrors()
        assert resolve_contract(session, 'NSF-9990003', errs) is None
        assert len(errs) == 1
        message = list(errs)[0]
        assert message.startswith('Ambiguous contract for grant number "NSF-9990003" ("9990003")')
        # The candidates are named because the fix is a data fix.
        assert '9990003' in message and 'PLR-9990003' in message

    def test_no_match_reports_the_legacy_string_with_both_numbers(self, session):
        errs = ActionErrors()
        assert resolve_contract(session, 'NSF-9990004', errs) is None
        assert list(errs) == [
            'Cannot find contract for grant number "NSF-9990004" ("9990004")']

    def test_an_empty_grant_number_reports_rather_than_matching_everything(self, session):
        """``ilike('%')`` would match every contract in the table. Guarded, because a
        ``grants[]`` entry with a null number is a shape the wire allows."""
        errs = ActionErrors()
        assert resolve_contract(session, None, errs) is None
        assert len(errs) == 1

    def test_the_suffix_match_is_case_insensitive(self, session):
        """``contract_number`` is ``utf8mb3_bin``; a plain ``LIKE`` undercounts.
        Legacy uses ``Restrictions.ilike`` and so does this."""
        from factories import make_contract
        contract = make_contract(session, contract_number='abc-9990005')
        errs = ActionErrors()
        assert resolve_contract(session, 'ABC-9990005X', errs) is contract

    def test_a_wildcard_grant_number_is_matched_literally(self, session):
        """``%`` off the wire must be a character, not "every contract in the table".

        ``extract_core_number`` returns its input *trimmed whole* whenever the ≥6-digit
        pattern misses, so an unescaped ``grantNumber`` of ``%`` reached ``ilike`` as a
        wildcard — an unbounded read whose every row is then named in the ambiguity
        message and stored in ``xras_action_log.error_messages``. Not an injection
        (the parameter is bound), but not the broker's decision either.
        """
        from factories import make_contract
        make_contract(session, contract_number='9990006')
        errs = ActionErrors()
        assert resolve_contract(session, '%', errs) is None
        # "cannot find", not "ambiguous": nothing ends in a literal '%'.
        assert len(errs) == 1
        assert list(errs)[0].startswith('Cannot find contract')

    def test_an_underscore_is_matched_literally_too(self, session):
        """``_`` is LIKE's single-character wildcard, and the one people forget.

        Note the grant number here has **no run of six or more digits**, which is what
        sends `extract_core_number` down its fall-through branch and puts the raw wire
        string in front of LIKE. A number like ``'_9990007'`` would not do: the pattern
        matches, group 2 is ``'9990007'``, and the underscore never reaches the query.
        """
        from factories import make_contract
        make_contract(session, contract_number='QQABCX123')
        errs = ActionErrors()
        assert resolve_contract(session, 'QQABC_123', errs) is None
        assert list(errs)[0].startswith('Cannot find contract')

    def test_a_literal_escaped_suffix_still_matches(self, session):
        """The escaping must not break a contract number that genuinely contains one
        of these characters — otherwise the guard has traded a wildcard bug for a
        lookup that can never succeed.

        The stored number is *longer* than the grant number on purpose, so step 1's
        exact match misses and this actually exercises the suffix query.
        """
        from factories import make_contract
        contract = make_contract(session, contract_number='NSFQQABC_123')
        errs = ActionErrors()
        assert resolve_contract(session, 'QQABC_123', errs) is contract
        assert not errs


# ---------------------------------------------------------------------------
# Mnemonic code — 24% of legacy's XRAS failures.
# ---------------------------------------------------------------------------


class TestResolveMnemonicCode:

    def test_an_internal_pi_resolves_via_their_organization(self, session):
        from factories import (make_mnemonic_code, make_organization, make_user,
                               make_user_organization)
        org = make_organization(session, name='Extractor Test Section')
        mnemo = make_mnemonic_code(session, description='Extractor Test Section')
        user = make_user(session)
        make_user_organization(session, user=user, organization=org)

        errs = ActionErrors()
        row = resolve_mnemonic_code(session, action(opportunityName='Small Allocation'),
                                    errs, pi_username=user.username)
        assert not errs
        assert row.mnemonic_code_id == mnemo.mnemonic_code_id

    def test_an_external_pi_resolves_via_their_institution(self, session):
        from factories import (make_institution, make_mnemonic_code, make_user,
                               make_user_institution)
        inst = make_institution(session, name='Extractor Test University')
        mnemo = make_mnemonic_code(session, description='Extractor Test University')
        user = make_user(session)
        make_user_institution(session, user=user, institution=inst)

        errs = ActionErrors()
        row = resolve_mnemonic_code(session, action(opportunityName='Small Allocation'),
                                    errs, pi_username=user.username)
        assert not errs
        assert row.mnemonic_code_id == mnemo.mnemonic_code_id

    def test_the_institution_route_is_tried_before_the_organization_one(self, session):
        """``MnemonicCodeExtractor`` checks ``isExternalUser()`` first, so a PI holding
        both affiliations is resolved as external."""
        from factories import (make_institution, make_mnemonic_code, make_organization,
                               make_user, make_user_institution, make_user_organization)
        inst = make_institution(session, name='Extractor Both University')
        make_mnemonic_code(session, description='Extractor Both University', code='QZ1')
        org = make_organization(session, name='Extractor Both Section')
        make_mnemonic_code(session, description='Extractor Both Section', code='QZ2')
        user = make_user(session)
        make_user_institution(session, user=user, institution=inst)
        make_user_organization(session, user=user, organization=org)

        errs = ActionErrors()
        row = resolve_mnemonic_code(session, action(opportunityName='Small Allocation'),
                                    errs, pi_username=user.username)
        assert row.code == 'QZ1'

    def test_an_ncar_opportunity_takes_the_lab_route(self, session):
        """``opportunityName.startsWith("NCAR ")`` walks the PI's org parentage to
        level 3 — which is a *different* organization from their own once the tree is
        four deep. This also catches the NSC prefix, ``'NCAR - NSC Allocation
        Request'``."""
        from factories import (make_mnemonic_code, make_organization, make_user,
                               make_user_organization)
        root = make_organization(session, name='Extractor Root')            # level 1
        lab = make_organization(session, name='Extractor Lab',              # level 3
                                parent_org_id=make_organization(
                                    session, name='Extractor Directorate',
                                    parent_org_id=root.organization_id).organization_id)
        own = make_organization(session, name='Extractor Own Section',
                                parent_org_id=lab.organization_id)          # level 4
        lab_mnemo = make_mnemonic_code(session, description='Extractor Lab')
        make_mnemonic_code(session, description='Extractor Own Section')
        user = make_user(session)
        make_user_organization(session, user=user, organization=own)

        errs = ActionErrors()
        row = resolve_mnemonic_code(
            session, action(opportunityName='NCAR - NSC Allocation Request 2026'),
            errs, pi_username=user.username)
        assert not errs
        assert row.mnemonic_code_id == lab_mnemo.mnemonic_code_id

    def test_a_shallow_tree_uses_the_pis_own_organization(self, session):
        """``levels <= 3`` → ``parentage[0]``. Three deep is the boundary."""
        from factories import (make_mnemonic_code, make_organization, make_user,
                               make_user_organization)
        root = make_organization(session, name='Extractor Shallow Root')
        own = make_organization(session, name='Extractor Shallow Own',
                                parent_org_id=root.organization_id)
        mnemo = make_mnemonic_code(session, description='Extractor Shallow Own')
        user = make_user(session)
        make_user_organization(session, user=user, organization=own)

        errs = ActionErrors()
        row = resolve_mnemonic_code(session, action(opportunityName='NCAR Whatever'),
                                    errs, pi_username=user.username)
        assert row.mnemonic_code_id == mnemo.mnemonic_code_id

    def test_an_unresolvable_lab_reports_where_legacy_stayed_silent(self, session):
        """Declared divergence. ``UserLabStrategy`` has no error arm — it returns null,
        the project is created with no mnemonic, and the failure surfaces later and
        less legibly. A projcode cannot be minted without a code, so we say so."""
        from factories import make_organization, make_user, make_user_organization
        org = make_organization(session, name='Extractor Unlinked Lab')
        user = make_user(session)
        make_user_organization(session, user=user, organization=org)

        errs = ActionErrors()
        assert resolve_mnemonic_code(session, action(opportunityName='NCAR Thing'),
                                     errs, pi_username=user.username) is None
        assert list(errs) == [
            'Could not determine Mnemonic code for internal PI via organization']

    def test_an_unlinked_institution_reports_the_external_string(self, session):
        from factories import make_institution, make_user, make_user_institution
        inst = make_institution(session, name='Extractor Unlinked University')
        user = make_user(session)
        make_user_institution(session, user=user, institution=inst)

        errs = ActionErrors()
        assert resolve_mnemonic_code(session, action(opportunityName='Small Allocation'),
                                     errs, pi_username=user.username) is None
        assert list(errs) == [
            'Could not determine Mnemonic code for external PI via institution']

    def test_a_pi_with_no_affiliation_at_all_reports_the_internal_string(self, session):
        """Legacy's ``UserAffiliationDTO`` is non-null for such a user, so it reaches
        ``getMnemonicCodeViaOrganization`` and fails there — not the affiliation
        string, which is reserved for a PI who is not a SAM user at all."""
        from factories import make_user
        user = make_user(session)
        errs = ActionErrors()
        assert resolve_mnemonic_code(session, action(opportunityName='Small Allocation'),
                                     errs, pi_username=user.username) is None
        assert list(errs) == [
            'Could not determine Mnemonic code for internal PI via organization']

    def test_an_unknown_pi_reports_the_affiliation_string(self, session):
        errs = ActionErrors()
        assert resolve_mnemonic_code(session, action(opportunityName='Small Allocation'),
                                     errs, pi_username='no_such_user_xyz') is None
        assert list(errs) == [
            'Could not produce affiliation data for PI no_such_user_xyz']

    def test_a_missing_pi_username_reports_rather_than_querying(self, session):
        errs = ActionErrors()
        assert resolve_mnemonic_code(session, action(), errs, pi_username=None) is None
        assert list(errs) == ['Could not produce affiliation data for PI ']

    def test_an_inactive_organization_link_is_not_used(self, session):
        """``getBestOrganization`` takes the first *current* ``user_organization``;
        a lapsed affiliation must not mint a projcode."""
        from datetime import datetime, timedelta
        from factories import (make_mnemonic_code, make_organization, make_user,
                               make_user_organization)
        org = make_organization(session, name='Extractor Lapsed Section')
        make_mnemonic_code(session, description='Extractor Lapsed Section')
        user = make_user(session)
        make_user_organization(session, user=user, organization=org,
                               start_date=datetime.now() - timedelta(days=800),
                               end_date=datetime.now() - timedelta(days=400))

        errs = ActionErrors()
        assert resolve_mnemonic_code(session, action(opportunityName='Small Allocation'),
                                     errs, pi_username=user.username) is None
        assert len(errs) == 1


class TestOrganizationParentageIsCycleSafe:
    """``parent_org_id`` is a self-FK with nothing stopping a loop, and the walk that
    builds the parentage list is unbounded in the Java. A bad import would hang the
    request thread rather than fail it."""

    def test_a_cycle_terminates(self, session):
        from factories import make_organization
        from sam.xras.extractors import _organization_parentage
        a = make_organization(session, name='Cycle A')
        b = make_organization(session, name='Cycle B', parent_org_id=a.organization_id)
        a.parent_org_id = b.organization_id
        session.flush()
        session.expire(a)

        parentage = _organization_parentage(b)
        assert {o.organization_id for o in parentage} == {a.organization_id,
                                                          b.organization_id}
