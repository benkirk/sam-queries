"""Selecting a handler on the pair ``(actionType, does the project exist)``.

The trap this file exists for: **``New`` does not imply a new project.**
``new_uwis0071_existing_ok.json`` is an ``actionType: 'New'`` whose ``requestNumber``
is the projcode of a project that already existed, and legacy routed it to *Update*.
A request token is projcode-*shaped* — ``NCAR4232`` and ``UCUB0166`` are the same eight
characters — so only the database can separate them.
:meth:`TestNewIsNotAlwaysAdd.test_the_same_payload_routes_two_ways` demonstrates that
directly, running one payload against a database with and without the project.

Five of the eight corpus projcodes are present in the obfuscated snapshot, so
:class:`TestTheCorpusDispatches` is a real oracle rather than a restatement of the
table.

See ``docs/plans/XRAS_SPRINT_C.md`` § *The dispatcher*.
"""

import json

import pytest

from sam.xras import dispatch
from sam.xras.dispatch import (
    ALL_ACTION_TYPES,
    SERVICES,
    DispatchResult,
    dispatch_action,
    parse_enabled_action_types,
    register,
    select_service,
)
from sam.xras.errors import ActionErrors, XrasActionRejected

from xras_helpers import FIXTURE_DIR, load_fixture

pytestmark = pytest.mark.unit


def act(action_type='Extension', request_number='NOSUCH9999', **extra):
    payload = {'actionType': action_type, 'requestNumber': request_number}
    payload.update(extra)
    return payload


@pytest.fixture
def clean_registry():
    """Isolate handler registration.

    ``_HANDLERS`` is module state populated at import time, so a test that registered
    into it would leak into every later test in the worker — including the route tests
    that assert the manual arm is still reachable.
    """
    saved = dict(dispatch._HANDLERS)
    dispatch._HANDLERS.clear()
    yield dispatch._HANDLERS
    dispatch._HANDLERS.clear()
    dispatch._HANDLERS.update(saved)


# ---------------------------------------------------------------------------
# The allowlist.
# ---------------------------------------------------------------------------


class TestParseEnabledActionTypes:

    def test_none_means_everything(self):
        """An unset config value must not disable the integration."""
        assert parse_enabled_action_types(None) == ALL_ACTION_TYPES

    @pytest.mark.parametrize('value', ['all', 'ALL', ' all ', ''])
    def test_all_and_blank_mean_everything(self, value):
        assert parse_enabled_action_types(value) == ALL_ACTION_TYPES

    @pytest.mark.parametrize('value', ['none', 'NONE'])
    def test_none_string_disables_everything(self, value):
        assert parse_enabled_action_types(value) == frozenset()

    def test_a_list_is_taken_literally(self):
        assert parse_enabled_action_types('Extension,Supplement') == {
            'Extension', 'Supplement'}

    def test_whitespace_and_empty_entries_are_tolerated(self):
        assert parse_enabled_action_types(' Extension , , Supplement ') == {
            'Extension', 'Supplement'}

    def test_both_adjust_spellings_fold_to_one(self):
        """The alias map already exists for the query layer; reused rather than
        duplicated (legacy defect 4)."""
        assert parse_enabled_action_types('Adjust') == {'Adjustment'}
        assert parse_enabled_action_types('Adjustment') == {'Adjustment'}

    def test_an_unknown_token_is_dropped_leaving_that_type_disabled(self, caplog):
        """Fails safe in the direction that matters: a typo parks actions for a human
        rather than enabling a handler nobody meant to enable."""
        with caplog.at_level('WARNING'):
            result = parse_enabled_action_types('Extention,Supplement')
        assert result == {'Supplement'}
        assert 'Extention' in caplog.text

    def test_an_all_unknown_list_disables_everything_rather_than_enabling_it(self):
        assert parse_enabled_action_types('nonsense') == frozenset()

    def test_every_known_type_round_trips(self):
        assert parse_enabled_action_types(','.join(ALL_ACTION_TYPES)) == ALL_ACTION_TYPES


# ---------------------------------------------------------------------------
# The selector.
# ---------------------------------------------------------------------------


class TestSelectService:

    def test_new_against_an_absent_projcode_is_add(self, session):
        assert select_service(session, act('New', 'NOSUCH9999')) == 'add'

    def test_new_against_an_existing_project_is_update(self, session):
        from factories import make_project
        project = make_project(session)
        assert select_service(session, act('New', project.projcode)) == 'update'

    def test_renewal_against_an_existing_project_is_update(self, session):
        from factories import make_project
        project = make_project(session)
        assert select_service(session, act('Renewal', project.projcode)) == 'update'

    def test_renewal_against_an_absent_project_matches_nothing(self, session):
        """Unlike ``New``, a Renewal has nothing to create. Legacy falls off the end of
        the selector chain and emails a human."""
        assert select_service(session, act('Renewal', 'NOSUCH9999')) is None

    @pytest.mark.parametrize('action_type,expected', [
        ('Extension', 'extend'),
        ('Supplement', 'supplement'),
        ('Transfer', 'transfer'),
        ('Adjustment', 'adjust'),
        ('Adjust', 'adjust'),
    ])
    def test_the_project_scoped_services(self, session, action_type, expected):
        from factories import make_project
        project = make_project(session)
        assert select_service(session, act(action_type, project.projcode)) == expected

    @pytest.mark.parametrize('action_type',
                             ['Extension', 'Supplement', 'Transfer', 'Adjustment'])
    def test_they_all_require_an_existing_project(self, session, action_type):
        assert select_service(session, act(action_type, 'NOSUCH9999')) is None

    def test_advance_matches_nothing_in_either_direction(self, session):
        """A declared wire type with no legacy service — never sampled, and it would
        fall through to the manual email today."""
        from factories import make_project
        project = make_project(session)
        assert select_service(session, act('Advance', project.projcode)) is None
        assert select_service(session, act('Advance', 'NOSUCH9999')) is None

    def test_an_unknown_action_type_matches_nothing(self, session):
        assert select_service(session, act('Nonsense', 'NOSUCH9999')) is None

    @pytest.mark.parametrize('request_number', [None, '', '   '])
    def test_a_blank_request_number_cannot_resolve_a_project(self, session,
                                                             request_number):
        """``New`` still routes to Add — legacy mints the projcode itself — but nothing
        that needs an existing project can match."""
        assert select_service(session, act('New', request_number)) == 'add'
        assert select_service(session, act('Extension', request_number)) is None

    def test_the_projcode_lookup_is_case_insensitive(self, session):
        from factories import make_project
        project = make_project(session)
        assert select_service(
            session, act('Extension', project.projcode.lower())) == 'extend'

    def test_request_type_is_ignored(self, session):
        """All eight sampled payloads carry ``requestType: 'New'``, including both
        Extensions. A dispatcher that read it would route every one of them wrong."""
        from factories import make_project
        project = make_project(session)
        assert select_service(session, act(
            'Extension', project.projcode, requestType='New')) == 'extend'


class TestInactiveProjectsStillExist:
    """⚠️ The existence check must not filter on ``active``.

    XRAS-created projects arrive ``active = 0`` by design (``InactivateNewProject``) —
    the success email is the human trigger to activate them. An active-only check would
    route a re-posted New action to the **Add** handler and mint a second project for
    the same request.
    """

    def test_a_new_action_against_an_inactive_project_is_still_an_update(self, session):
        from factories import make_project
        project = make_project(session, active=False)
        assert not project.is_active
        assert select_service(session, act('New', project.projcode)) == 'update'

    def test_an_extension_against_an_inactive_project_still_extends(self, session):
        from factories import make_project
        project = make_project(session, active=False)
        assert select_service(session, act('Extension', project.projcode)) == 'extend'


class TestNewIsNotAlwaysAdd:
    """Trap 2, demonstrated rather than asserted from the table."""

    def test_the_same_payload_routes_two_ways(self, session):
        """One payload, one database, two answers — the only variable is whether
        the project row exists. UWIS0071 is the production case: legacy emitted
        its "Existing XRAS project updated" subject for an ``actionType: 'New'``.

        ⚠️ The projcode is **rewritten to one this database cannot contain**,
        rather than used as the fixture ships it. This test used to assert
        ``'add'`` first, which silently required UWIS0071 to be ABSENT from the
        snapshot — a fact about a 20 MB blob, asserted nowhere. The 2026-08-10
        refresh brought the project in and the test failed for a reason that
        had nothing to do with dispatch. What is under test is the *rule*, so
        the rule is what the fixture should supply.
        """
        from factories import make_project
        from factories._seq import next_seq
        data = dict(load_fixture('new_uwis0071_existing_ok.json'))
        assert data['actionType'] == 'New'

        # No truncation: `projcode` is varchar(30), and clipping next_seq's
        # worker-namespaced counter to 8 chars would map UDSP00001 and
        # UDSP00002 onto the same code — a collision under xdist.
        data['requestNumber'] = next_seq('UDSP')
        assert select_service(session, data) == 'add'
        make_project(session, projcode=data['requestNumber'])
        assert select_service(session, data) == 'update'

    def test_a_request_token_is_projcode_shaped(self):
        """Why no prefix or shape rule can work: ``NCAR4232`` (a request token) and
        ``UCUB0166`` (a projcode) are the same four-letters-four-digits shape."""
        token = load_fixture('new_ncar4232_failed.json')['requestNumber']
        projcode = load_fixture('extension_ucub0166_ok.json')['requestNumber']
        assert len(token) == len(projcode) == 8
        assert token[:4].isalpha() and token[4:].isdigit()
        assert projcode[:4].isalpha() and projcode[4:].isdigit()


class TestTheCorpusDispatches:
    """Every projcode in the corpus is in the obfuscated snapshot, so these are real.

    The only fixtures whose ``requestNumber`` is absent are the eleven ``NCAR####``
    request tokens, and they are absent *because* they are tokens — a New that had not
    yet minted a projcode when it was captured. So the selector is exercised against
    real ``exists`` answers on 30 of 41, and against a real "no project" on the other
    eleven. Nothing here is a fixture pretending.
    """

    #: fixture → expected service, given the snapshot's contents.
    #:
    #: ⚠️ ``None`` means **no service matches** — the manual-fallback arm, not an
    #: unfinished entry. All four are ``Date Adjustment``, an ``actionType`` with no
    #: serviceable in legacy either; see :class:`TestDateAdjustmentParks`.
    EXPECTED = {
        'adjustment_ucsu0146_manual.json': 'adjust',       # UCSU0146 present
        'adjustment_ucub0160_manual.json': 'adjust',       # UCUB0160 present
        'adjustment_uwis0064_manual.json': 'adjust',       # UWIS0064 present — see below
        'date_adjustment_uazn0052_manual.json': None,      # no serviceable
        'date_adjustment_ucor0097_manual.json': None,      # no serviceable
        'date_adjustment_ucub0155_manual.json': None,      # no serviceable
        'date_adjustment_uwas0141_manual.json': None,      # no serviceable
        'extension_ucbk0034_ok.json': 'extend',            # UCBK0034 present
        'extension_ucsd0048_ok.json': 'extend',            # UCSD0048 present
        'extension_ucsd0073_ok.json': 'extend',            # UCSD0073 present
        'extension_ucub0166_ok.json': 'extend',            # UCUB0166 present
        'extension_ufsu0023_failed.json': 'extend',        # UFSU0023 present
        'extension_ugmu0052_ok.json': 'extend',            # UGMU0052 present
        'extension_uiuc0073_ok.json': 'extend',            # UIUC0073 present
        'extension_unid0003_ok.json': 'extend',            # UNID0003 present
        'extension_uwho0019_ok.json': 'extend',            # UWHO0019 present
        'new_ncar4214_ok.json': 'add',                     # request token, no project
        'new_ncar4218_ok.json': 'add',                     # request token, no project
        'new_ncar4223_ok.json': 'add',                     # request token, no project
        'new_ncar4227_failed.json': 'add',                 # request token, no project
        'new_ncar4228_failed.json': 'add',                 # request token, no project
        'new_ncar4229_ok.json': 'add',                     # request token, no project
        'new_ncar4232_failed.json': 'add',                 # request token, no project
        'new_ncar4236_failed.json': 'add',                 # request token — see below
        'new_ncar4246_ok.json': 'add',                     # request token, no project
        'new_ncar4250_ok.json': 'add',                     # request token, no project
        'new_ncar4253_ok.json': 'add',                     # request token, no project
        # The five below are the shape that makes `New` ambiguous: actionType 'New'
        # carrying a *projcode*, which legacy routes to Update and reports as
        # "Existing XRAS project updated". Only the database can tell them from the
        # eleven above — a request token is projcode-shaped.
        'new_uchi0020_ok.json': 'update',                  # UCHI0020 present — see below
        'new_uida0008_ok.json': 'update',                  # UIDA0008 present
        'new_ummm0016_failed.json': 'update',              # UMMM0016 present
        'new_umsb0003_ok.json': 'update',                  # UMSB0003 present
        'new_uwis0071_existing_ok.json': 'update',         # UWIS0071 present
        'supplement_uahv0010_ok.json': 'supplement',       # UAHV0010 present
        'supplement_ubrn0027_ok.json': 'supplement',       # UBRN0027 present
        'supplement_ucit0011_ok.json': 'supplement',       # UCIT0011 present
        'supplement_ucla0076_ok.json': 'supplement',       # UCLA0076 present
        'supplement_ucla0080_ok.json': 'supplement',       # UCLA0080 present
        'supplement_ucsu0114_ok.json': 'supplement',       # UCSU0114 present
        'supplement_ucub0182_ok.json': 'supplement',       # UCUB0182 present
        'supplement_ugit0044_ok.json': 'supplement',       # UGIT0044 present
        'supplement_uwku0002_ok.json': 'supplement',       # UWKU0002 present
    }

    def test_the_corpus_is_complete(self):
        on_disk = sorted(p.name for p in FIXTURE_DIR.glob('*.json'))
        assert on_disk == sorted(self.EXPECTED)

    @pytest.mark.parametrize('name', sorted(EXPECTED))
    def test_each_payload_selects_its_service(self, session, name):
        assert select_service(session, load_fixture(name)) == self.EXPECTED[name]

    def test_the_adjustment_reaches_a_service_legacy_never_could(self, session):
        """Legacy defect 4: XRAS sends ``Adjustment``, ``AdjustProjectActionService``
        compares ``Adjust``, so this payload has only ever produced a manual email.
        Accepting both spellings is what makes the handler reachable at all."""
        data = load_fixture('adjustment_uwis0064_manual.json')
        assert data['actionType'] == 'Adjustment'
        assert select_service(session, data) == 'adjust'


class TestDateAdjustmentParks:
    """``Date Adjustment`` — an ``actionType`` nobody knew existed until 2026-08-11.

    It reached the corpus through the manual-fallback subject
    (``New XRAS post action (Date Adjustment request for UAZN0052)``), which
    ``XRAS_REIMPLEMENTATION.md`` § 1.4 identifies as *the only record of the action
    types SAM does not service*. Four samples arrived in one forward, so it is not
    rare.

    **Parking is the correct behaviour and matches legacy exactly.** Legacy has no
    ``DateAdjustProjectActionService``; every one of these four produced a
    manual-fallback email and a bare 200. Servicing it would be new behaviour
    introduced under a cutover with no observation window.
    """

    NAMES = sorted(p.name for p in FIXTURE_DIR.glob('date_adjustment_*.json'))

    def test_all_four_are_present(self):
        assert len(self.NAMES) == 4, self.NAMES

    def test_it_is_declared_but_deliberately_unserviced(self, session):
        """Listed in the vocabulary, absent from the selector — and that pairing is
        the point, not an oversight half-finished.

        It is in ``XRAS_ACTION_TYPES`` so the XRAS tab offers it as a filter chip
        before the first row exists. It has no ``select_service`` arm because legacy
        has no serviceable for it, and inventing one would be new behaviour under a
        cutover with no observation window.

        If a future change adds a dispatch arm, this test fails and asks for the
        decision to be made deliberately. Read
        ``sam.queries.xras_actions.XRAS_ACTION_TYPES``' note first: routing it to
        ``extend`` is one line and wrong twice over.
        """
        from sam.queries.xras_actions import XRAS_ACTION_TYPES
        assert 'Date Adjustment' in XRAS_ACTION_TYPES
        for name in self.NAMES:
            assert select_service(session, load_fixture(name)) is None

    @pytest.mark.parametrize('name', NAMES)
    def test_the_wire_really_sends_this_action_type(self, name):
        """From the bytes, not from the subject line that first revealed it."""
        assert load_fixture(name)['actionType'] == 'Date Adjustment'

    @pytest.mark.parametrize('name', NAMES)
    def test_no_service_matches(self, session, name):
        assert select_service(session, load_fixture(name)) is None

    @pytest.mark.parametrize('name', NAMES)
    def test_it_parks_with_a_reason(self, session, name):
        result = dispatch_action(session, load_fixture(name))
        assert result.status == 'manual'
        assert result.service is None
        assert 'no service matches' in result.reason

    @pytest.mark.parametrize('name', NAMES)
    def test_it_is_extension_shaped(self, name):
        """Why a handler is even conceivable: it carries dates and **no resources**.

        That is the Extension signature — ``ExtensionHandler`` reads only
        ``actionEndDate`` and ignores ``resources[]`` — so the obvious implementation
        is to route it to ``extend``.

        ⚠️ Recorded as a *shape* observation, not a recommendation. It also carries an
        ``actionBeginDate``, which Extension ignores entirely, and no sample tells us
        whether XRAS expects the begin date to move. Deciding that needs ACCESS, not
        inference from four payloads.
        """
        data = load_fixture(name)
        assert data['resources'] == []
        assert data['actionBeginDate'] and data['actionEndDate']


class TestOneActionIdSpansAFailureAndItsRetry:
    """``actionId`` 388865 arrives twice, with different bodies and different outcomes.

    Captured in the same forward: first as ``requestNumber: 'NCAR4236'``, which legacy
    answered *"Failed to add or update XRAS project"*; then as
    ``requestNumber: 'UCHI0020'``, which legacy answered *"Existing XRAS project
    updated"*. Same action id, two posts, two services.

    Two consequences worth pinning:

    1. **``xras_action_log.action_id`` is not an identity key.** It exists to answer
       "have I seen this action before?" (``XRAS_STRESS_AND_SCHEMA.md`` § *Verdicts*),
       and the honest answer here is "yes, but not with this body". Any triage that
       treats a repeated ``action_id`` as a duplicate post would collapse a failure
       and its retry into one row.
    2. **A repeat is a person, not a machine.** ACCESS answered the retry question on
       2026-08-11 (Steven Peckins, XRAS): *"POSTs are not automatically retried. They
       are triggered by a human — a user in xras_admin pushes a button."* So this pair
       is one admin pushing the button, seeing it fail, and pushing it again once the
       project existed — not a broker retry loop.

       ⚠️ An earlier version of this docstring called it *"the only direct evidence we
       hold on broker retry behaviour"*. That was written before Steve's reply and is
       wrong in the direction that mattered: it implied an automatic retry, which is
       the loop the runbook feared. There is no such loop. The observation itself
       stands — same id, two bodies, two services.
    """

    PAIR = ('new_ncar4236_failed.json', 'new_uchi0020_ok.json')

    def test_both_carry_the_same_action_id(self):
        first, second = (load_fixture(n) for n in self.PAIR)
        assert first['actionId'] == second['actionId'] == 388865
        assert first['requestNumber'] == 'NCAR4236'
        assert second['requestNumber'] == 'UCHI0020'

    def test_the_bodies_differ(self):
        first, second = (load_fixture(n) for n in self.PAIR)
        assert first != second

    def test_they_select_different_services(self, session):
        """The retry is not a re-post of the same work: the token became a projcode,
        so the same action id routes to Add on one attempt and Update on the other."""
        first, second = (load_fixture(n) for n in self.PAIR)
        assert select_service(session, first) == 'add'
        assert select_service(session, second) == 'update'


# ---------------------------------------------------------------------------
# dispatch_action.
# ---------------------------------------------------------------------------


class TestRegistration:

    def test_an_unknown_service_name_raises(self, clean_registry):
        with pytest.raises(ValueError, match='unknown XRAS service'):
            register('nonsense', lambda s, a, *, validate_only=False: None)

    def test_registering_twice_raises(self, clean_registry):
        register('extend', lambda s, a, *, validate_only=False: None)
        with pytest.raises(ValueError, match='already registered'):
            register('extend', lambda s, a, *, validate_only=False: None)

    def test_every_service_name_is_registrable(self, clean_registry):
        for service in SERVICES:
            register(service, lambda s, a, *, validate_only=False: None)
        assert set(clean_registry) == set(SERVICES)


class TestDispatchAction:

    def test_no_matching_service_parks_as_manual_with_a_reason(self, session,
                                                               clean_registry):
        result = dispatch_action(session, act('Advance', 'NOSUCH9999'))
        assert result.status == 'manual'
        assert result.service is None
        assert 'no service matches' in result.reason

    def test_an_unregistered_service_parks_as_manual_but_names_itself(self, session,
                                                                     clean_registry):
        """The distinction that saves triage time: "nothing matched" and "the handler
        is not built yet" look identical in the audit table otherwise."""
        from factories import make_project
        project = make_project(session)
        result = dispatch_action(session, act('Extension', project.projcode))
        assert result.status == 'manual'
        assert result.service == 'extend'
        assert 'no handler is registered' in result.reason

    def test_a_registered_handler_runs(self, session, clean_registry):
        from factories import make_project
        project = make_project(session)
        register('extend', lambda s, a, *, validate_only=False: DispatchResult(
            status='processed', service='extend', projcode=project.projcode))

        result = dispatch_action(session, act('Extension', project.projcode))
        assert result.status == 'processed'
        assert result.projcode == project.projcode

    def test_a_disabled_type_parks_as_manual_without_running_the_handler(
            self, session, clean_registry):
        from factories import make_project
        project = make_project(session)
        ran = []
        register('extend', lambda s, a, *, validate_only=False: ran.append(1) or DispatchResult(
            status='processed', service='extend'))

        result = dispatch_action(session, act('Extension', project.projcode),
                                 enabled=frozenset({'Supplement'}))
        assert result.status == 'manual'
        assert result.service == 'extend'
        assert 'disabled by XRAS_ACTIONS_ENABLED' in result.reason
        assert ran == [], 'the handler must not run for a disabled type'

    def test_enabled_none_means_everything_is_allowed(self, session, clean_registry):
        from factories import make_project
        project = make_project(session)
        register('extend', lambda s, a, *, validate_only=False: DispatchResult(status='processed',
                                                       service='extend'))
        assert dispatch_action(session, act('Extension', project.projcode),
                               enabled=None).status == 'processed'

    def test_disabling_new_disables_both_add_and_update(self, session, clean_registry):
        """The stated consequence of keying on action type rather than handler. Both
        halves of the ``New`` decision go quiet together, which is what "stop
        processing New actions" means."""
        from factories import make_project
        project = make_project(session)
        register('add', lambda s, a, *, validate_only=False: DispatchResult(status='processed', service='add'))
        register('update', lambda s, a, *, validate_only=False: DispatchResult(status='processed',
                                                       service='update'))
        enabled = ALL_ACTION_TYPES - {'New'}

        assert dispatch_action(session, act('New', 'NOSUCH9999'),
                               enabled=enabled).status == 'manual'
        assert dispatch_action(session, act('New', project.projcode),
                               enabled=enabled).status == 'manual'

    def test_disabling_adjustment_by_either_spelling_disables_both(self, session,
                                                                  clean_registry):
        from factories import make_project
        project = make_project(session)
        register('adjust', lambda s, a, *, validate_only=False: DispatchResult(status='processed',
                                                       service='adjust'))
        enabled = parse_enabled_action_types('Adjust')

        for spelling in ('Adjust', 'Adjustment'):
            assert dispatch_action(session, act(spelling, project.projcode),
                                   enabled=enabled).status == 'processed'
        assert dispatch_action(session, act('Adjustment', project.projcode),
                               enabled=frozenset()).status == 'manual'

    def test_a_rejection_propagates_rather_than_becoming_a_result(self, session,
                                                                  clean_registry):
        """422 does not come back as a ``DispatchResult``. Assemble → check once →
        execute means a rejection happens before any transaction opens, so it is an
        exception and the route maps it."""
        from factories import make_project
        project = make_project(session)

        def rejecting_handler(s, a, *, validate_only=False):
            errs = ActionErrors()
            errs.report('Missing title')
            errs.raise_if_any()

        register('extend', rejecting_handler)
        with pytest.raises(XrasActionRejected) as exc:
            dispatch_action(session, act('Extension', project.projcode))
        assert exc.value.messages == ['Missing title']

    def test_the_handler_receives_the_session_and_the_action(self, session,
                                                             clean_registry):
        from factories import make_project
        project = make_project(session)
        seen = {}

        def handler(s, a, *, validate_only=False):
            seen['session'] = s
            seen['action'] = a
            return DispatchResult(status='processed', service='extend')

        register('extend', handler)
        payload = act('Extension', project.projcode)
        dispatch_action(session, payload)
        assert seen['session'] is session
        assert seen['action'] is payload


class TestWhichHandlersExist:
    """Importing ``sam.xras.handlers`` is the only wiring, so this is the inventory.

    Update the expected set as each handler lands. It is asserted rather than left
    implicit because a handler that silently failed to register would route live
    traffic to the manual fallback while every test calling it directly still passed —
    a ``manual`` row looks plausible, which is exactly what makes it dangerous.
    """

    #: Services with a handler today. Grows one entry per commit through the sprint.
    BUILT = {'extend', 'supplement', 'adjust', 'add', 'update', 'transfer'}

    def test_the_inventory_is_current(self):
        import sam.xras.handlers  # noqa: F401  — registers on import
        assert dispatch.registered_services() == self.BUILT

    @pytest.mark.parametrize('name', sorted(p.name for p in FIXTURE_DIR.glob('*.json')))
    def test_a_payload_whose_service_is_unbuilt_still_parks_as_manual(
            self, session, clean_registry, name):
        """With the registry emptied, every corpus payload takes the manual arm.

        Two distinguishable reasons, and the distinction is what saves triage time:

        * **selected a service, no handler** — names the handler it wanted, which is
          the case this test was written for (a handler that failed to register).
        * **selected nothing** — no serviceable matches the ``actionType`` at all.
          Emptying the registry cannot cause this, so it is a property of the payload:
          the four ``Date Adjustment`` fixtures park this way with a full registry too.
        """
        result = dispatch_action(session, load_fixture(name))
        assert result.status == 'manual'
        if result.service is None:
            assert 'no service matches' in result.reason
        else:
            assert 'no handler is registered' in result.reason


class TestTransferIsDeliberatelyManual:
    """The sixth service is registered but does not apply anything.

    Registering a handler that returns ``manual`` is not the same as leaving the
    service unbuilt: the reason lands on the audit row, so an operator triaging the
    parked action learns it was recognised and intentionally deferred rather than
    guessing whether something is broken.
    """

    def test_it_is_registered(self):
        import sam.xras.handlers  # noqa: F401
        assert 'transfer' in dispatch.registered_services()

    def test_it_parks_the_action_and_says_why(self, session):
        import sam.xras.handlers  # noqa: F401
        from factories import make_project
        project = make_project(session)

        result = dispatch_action(session, act('Transfer', project.projcode))
        assert result.status == 'manual'
        assert result.service == 'transfer'
        assert 'deliberately not serviced' in result.reason
        assert 'Legacy SAM does service it' in result.reason

    def test_the_reason_distinguishes_it_from_an_unbuilt_handler(self, session,
                                                                 clean_registry):
        """An unregistered service says "no handler is registered"; this one explains
        a decision. The two must not read the same."""
        from factories import make_project
        project = make_project(session)
        unbuilt = dispatch_action(session, act('Transfer', project.projcode))
        assert 'no handler is registered' in unbuilt.reason

    def test_the_transfer_vocabulary_is_complete_even_though_it_is_unbuilt(self):
        """All five strings are implemented and pinned, so a future implementation
        starts from verified bytes rather than re-reading the Java."""
        from sam.xras import errors as e
        assert e.transfer_one_source_only()
        assert e.transfer_requires_source()
        assert e.transfer_requires_destination()
        assert e.transfer_source_has_no_allocation('P', 'R')
        assert e.transfer_credit_exceeds_debit(1.0, 2.0)
