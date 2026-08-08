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
from pathlib import Path

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

pytestmark = pytest.mark.unit

FIXTURE_DIR = Path(__file__).parent.parent / 'fixtures' / 'xras' / 'actions'


def load_fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text())


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
        """One payload, one database, two answers — the only variable is whether the
        project row exists. UWIS0071 is the production case: legacy emitted its
        "Existing XRAS project updated" subject for an ``actionType: 'New'``."""
        from factories import make_project
        data = load_fixture('new_uwis0071_existing_ok.json')
        assert data['actionType'] == 'New'

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
    """Five of the eight projcodes are in the obfuscated snapshot, so these are real."""

    #: fixture → expected service, given the snapshot's contents.
    EXPECTED = {
        'extension_ucub0166_ok.json': 'extend',        # UCUB0166 present
        'extension_ufsu0023_failed.json': 'extend',    # UFSU0023 present
        'supplement_ubrn0027_ok.json': 'supplement',   # UBRN0027 present
        'supplement_ucub0182_ok.json': 'supplement',   # UCUB0182 present
        'adjustment_uwis0064_manual.json': 'adjust',   # UWIS0064 present — see below
        'new_ncar4232_failed.json': 'add',             # request token, no project
        'new_ncar4253_ok.json': 'add',                 # request token, no project
        'new_uwis0071_existing_ok.json': 'add',        # absent HERE; 'update' in prod
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


# ---------------------------------------------------------------------------
# dispatch_action.
# ---------------------------------------------------------------------------


class TestRegistration:

    def test_an_unknown_service_name_raises(self, clean_registry):
        with pytest.raises(ValueError, match='unknown XRAS service'):
            register('nonsense', lambda s, a: None)

    def test_registering_twice_raises(self, clean_registry):
        register('extend', lambda s, a: None)
        with pytest.raises(ValueError, match='already registered'):
            register('extend', lambda s, a: None)

    def test_every_service_name_is_registrable(self, clean_registry):
        for service in SERVICES:
            register(service, lambda s, a: None)
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
        register('extend', lambda s, a: DispatchResult(
            status='processed', service='extend', projcode=project.projcode))

        result = dispatch_action(session, act('Extension', project.projcode))
        assert result.status == 'processed'
        assert result.projcode == project.projcode

    def test_a_disabled_type_parks_as_manual_without_running_the_handler(
            self, session, clean_registry):
        from factories import make_project
        project = make_project(session)
        ran = []
        register('extend', lambda s, a: ran.append(1) or DispatchResult(
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
        register('extend', lambda s, a: DispatchResult(status='processed',
                                                       service='extend'))
        assert dispatch_action(session, act('Extension', project.projcode),
                               enabled=None).status == 'processed'

    def test_disabling_new_disables_both_add_and_update(self, session, clean_registry):
        """The stated consequence of keying on action type rather than handler. Both
        halves of the ``New`` decision go quiet together, which is what "stop
        processing New actions" means."""
        from factories import make_project
        project = make_project(session)
        register('add', lambda s, a: DispatchResult(status='processed', service='add'))
        register('update', lambda s, a: DispatchResult(status='processed',
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
        register('adjust', lambda s, a: DispatchResult(status='processed',
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

        def rejecting_handler(s, a):
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

        def handler(s, a):
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
    BUILT = {'extend', 'supplement'}

    def test_the_inventory_is_current(self):
        import sam.xras.handlers  # noqa: F401  — registers on import
        assert dispatch.registered_services() == self.BUILT

    @pytest.mark.parametrize('name', sorted(p.name for p in FIXTURE_DIR.glob('*.json')))
    def test_a_payload_whose_service_is_unbuilt_still_parks_as_manual(
            self, session, clean_registry, name):
        """With the registry emptied, every corpus payload takes the manual arm — and
        names the handler it wanted, which is the distinction that saves triage time."""
        result = dispatch_action(session, load_fixture(name))
        assert result.status == 'manual'
        assert result.service is not None
        assert 'no handler is registered' in result.reason
