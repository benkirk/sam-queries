"""Contract create: the two-mode form, award prefill, and the widened model.

Two layers, per the house convention (docs/TESTING.md):

* **Model layer** — the writes. ``Contract.create`` / ``Contract.update``
  now carry the monitor and NSF program, and ``update`` distinguishes
  "leave alone" from "set NULL".
* **HTTP layer** — auth, validation, and render behaviour of the bespoke
  create routes plus the award-lookup endpoint. Route handlers use
  Flask-SQLAlchemy's ``db.session``, which only sees committed snapshot
  rows, so happy-path creates are not exercised here.

No network: ``resolve_award`` is stubbed at its module.
"""

import os
from datetime import datetime
from unittest.mock import patch

import pytest
from marshmallow import ValidationError

from sam.integration.awards.base import (
    AwardRecord, AwardSourceUnavailable, PersonRef,
)
from sam.projects.contracts import Contract, NSFProgram, UNCHANGED
from sam.schemas.forms.orgs import CreateContractForm, EditContractForm
from factories.core import make_user
from factories.projects import make_contract, make_contract_source

pytestmark = pytest.mark.unit


def _award(**overrides):
    defaults = dict(
        provenance='NSF Awards API',
        contract_number='AGS-1852977',
        title='The Management and Operation of NCAR',
        start_date=datetime(2018, 10, 1).date(),
        end_date=datetime(2028, 9, 30).date(),
        url='https://www.nsf.gov/awardsearch/show-award?AWD_ID=1852977',
        program_name='NCAR-Nat Center Atmosph Resear',
        pi=PersonRef(name='Eric Barron', email='barron@ucar.edu'),
        monitor=PersonRef(name='Carrie E. Black', email='cblack@nsf.gov'),
        unavailable_fields=frozenset(),
    )
    defaults.update(overrides)
    return AwardRecord(**defaults)


# ── Schema ────────────────────────────────────────────────────────────────

class TestCreateContractFormSchema:

    def _base_form(self, **extra):
        form = {
            'contract_number': 'AGS-1852977',
            'title': 'A contract',
            'start_date': '2018-10-01',
            'contract_source_id': '1',
            'principal_investigator_user_id': '42',
        }
        form.update(extra)
        return form

    def test_mode_defaults_to_manual(self):
        data = CreateContractForm().load(self._base_form())
        assert data['contract_mode'] == 'manual'

    def test_lookup_mode_round_trips(self):
        data = CreateContractForm().load(
            self._base_form(contract_mode='lookup'))
        assert data['contract_mode'] == 'lookup'

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValidationError) as exc:
            CreateContractForm().load(self._base_form(contract_mode='magic'))
        assert 'contract_mode' in exc.value.messages

    def test_both_modes_accept_the_same_fields(self):
        """The modes are presentational — neither changes what is required."""
        extra = {'contract_monitor_user_id': '7', 'nsf_program_id': '3'}
        manual = CreateContractForm().load(self._base_form(**extra))
        lookup = CreateContractForm().load(
            self._base_form(contract_mode='lookup', **extra))
        manual.pop('contract_mode'), lookup.pop('contract_mode')
        assert manual == lookup

    def test_monitor_and_program_are_optional(self):
        data = CreateContractForm().load(self._base_form())
        assert data['contract_monitor_user_id'] is None
        assert data['nsf_program_id'] is None

    def test_empty_strings_fall_back_to_none(self):
        """A cleared FK picker posts '' — _strip_empty_strings drops it."""
        data = CreateContractForm().load(
            self._base_form(contract_monitor_user_id='', nsf_program_id=''))
        assert data['contract_monitor_user_id'] is None
        assert data['nsf_program_id'] is None


class TestEditContractFormSchema:

    def test_carries_monitor_and_program(self):
        data = EditContractForm().load({
            'title': 'T', 'start_date': '2020-01-01',
            'contract_monitor_user_id': '9', 'nsf_program_id': '4',
        })
        assert data['contract_monitor_user_id'] == 9
        assert data['nsf_program_id'] == 4


# ── Model ─────────────────────────────────────────────────────────────────

class TestContractCreate:

    def test_persists_monitor_and_program(self, session):
        pi = make_user(session)
        monitor = make_user(session)
        source = make_contract_source(session)
        program = NSFProgram.create(session, nsf_program_name='TEST PROGRAM X')

        contract = Contract.create(
            session,
            contract_number='TEST-0000001',
            title='Widened create',
            start_date=datetime(2024, 1, 1),
            contract_source_id=source.contract_source_id,
            principal_investigator_user_id=pi.user_id,
            contract_monitor_user_id=monitor.user_id,
            nsf_program_id=program.nsf_program_id,
        )

        assert contract.contract_monitor_user_id == monitor.user_id
        assert contract.nsf_program_id == program.nsf_program_id
        assert contract.contract_monitor.user_id == monitor.user_id
        assert contract.nsf_program.nsf_program_name == 'TEST PROGRAM X'

    def test_both_stay_optional(self, session):
        pi = make_user(session)
        source = make_contract_source(session)
        contract = Contract.create(
            session,
            contract_number='TEST-0000002',
            title='No monitor',
            start_date=datetime(2024, 1, 1),
            contract_source_id=source.contract_source_id,
            principal_investigator_user_id=pi.user_id,
        )
        assert contract.contract_monitor_user_id is None
        assert contract.nsf_program_id is None


class TestContractUpdate:

    def test_sets_monitor_and_program(self, session):
        contract = make_contract(session)
        monitor = make_user(session)
        program = NSFProgram.create(session, nsf_program_name='TEST PROGRAM Y')

        contract.update(contract_monitor_user_id=monitor.user_id,
                        nsf_program_id=program.nsf_program_id)

        assert contract.contract_monitor_user_id == monitor.user_id
        assert contract.nsf_program_id == program.nsf_program_id

    def test_explicit_none_clears(self, session):
        """A cleared picker posts nothing, which must mean "unset"."""
        contract = make_contract(session)
        monitor = make_user(session)
        contract.update(contract_monitor_user_id=monitor.user_id)

        contract.update(contract_monitor_user_id=None, nsf_program_id=None)
        assert contract.contract_monitor_user_id is None

    def test_omitting_them_leaves_them_alone(self, session):
        """htmx_contract_delete calls update(end_date=...) — it must not wipe."""
        contract = make_contract(session)
        monitor = make_user(session)
        contract.update(contract_monitor_user_id=monitor.user_id)

        contract.update(end_date=datetime(2030, 1, 1))
        assert contract.contract_monitor_user_id == monitor.user_id

    def test_sentinel_is_not_a_plausible_value(self):
        assert UNCHANGED is not None
        assert repr(UNCHANGED) == 'UNCHANGED'


# ── HTTP: bespoke create routes ───────────────────────────────────────────

CREATE_FORM_URL = '/admin/htmx/contract-create-form'
CREATE_URL = '/admin/htmx/contract-create'
LOOKUP_URL = '/admin/htmx/contract-award-lookup'
PROGRAM_CREATE_URL = '/admin/htmx/contract-program-create'
PROGRAM_SEARCH_URL = '/admin/htmx/search/nsf-programs'


class TestNsfProgramTypeahead:
    """~240 programs is a list you search, not one you scroll."""

    def test_short_query_returns_nothing(self, auth_client):
        resp = auth_client.get(PROGRAM_SEARCH_URL, query_string={'q': 'a'})
        assert resp.get_data(as_text=True) == ''

    def test_matches_are_fk_picker_rows(self, auth_client, any_nsf_program):
        resp = auth_client.get(PROGRAM_SEARCH_URL, query_string={
            'q': any_nsf_program.nsf_program_name[:6]})
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        # fk-picker.js bails out unless both data attributes are present.
        assert 'data-fk-id=' in body and 'data-fk-label=' in body

    def test_no_match_renders_an_empty_state(self, auth_client):
        resp = auth_client.get(PROGRAM_SEARCH_URL,
                               query_string={'q': 'zzzz-no-such-program'})
        assert 'No NSF programs found' in resp.get_data(as_text=True)

    def test_search_is_active_only(self, auth_client, session):
        """No checkbox behind this picker, so the param never arrives (§10)."""
        inactive = NSFProgram.create(session,
                                     nsf_program_name='ZZ INACTIVE PROBE')
        inactive.update(active=False)
        session.commit()
        try:
            resp = auth_client.get(PROGRAM_SEARCH_URL,
                                   query_string={'q': 'ZZ INACTIVE PROBE'})
            assert 'ZZ INACTIVE PROBE' not in resp.get_data(as_text=True)
        finally:
            session.delete(inactive)
            session.commit()

    def test_non_admin_forbidden(self, non_admin_client):
        assert non_admin_client.get(PROGRAM_SEARCH_URL).status_code == 403


class TestCreateRouteAuth:
    """The hand-written routes must gate exactly as the generated ones did."""

    @pytest.mark.parametrize('url,method', [
        (CREATE_FORM_URL, 'get'),
        (CREATE_URL, 'post'),
        (LOOKUP_URL, 'get'),
        (PROGRAM_CREATE_URL, 'post'),
    ])
    def test_unauthenticated_rejected(self, client, url, method):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip('Auth disabled in dev environment')
        resp = getattr(client, method)(url)
        assert resp.status_code in (302, 401)

    @pytest.mark.parametrize('url,method', [
        (CREATE_FORM_URL, 'get'),
        (CREATE_URL, 'post'),
        (LOOKUP_URL, 'get'),
        (PROGRAM_CREATE_URL, 'post'),
    ])
    def test_non_admin_forbidden(self, non_admin_client, url, method):
        assert getattr(non_admin_client, method)(url).status_code == 403


class TestCreateForm:

    def test_renders_both_modes(self, auth_client):
        body = auth_client.get(CREATE_FORM_URL).get_data(as_text=True)
        assert 'contract_mode' in body
        assert 'contractModeManual' in body
        assert 'contractModeLookup' in body

    def test_renders_the_new_fields(self, auth_client):
        body = auth_client.get(CREATE_FORM_URL).get_data(as_text=True)
        assert 'contract_monitor_user_id' in body
        assert 'nsf_program_id' in body

    def test_invalid_post_rerenders_without_success_trigger(self, auth_client):
        resp = auth_client.post(CREATE_URL, data={})
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers

    def test_error_rerender_returns_to_the_submitted_mode(self, auth_client):
        resp = auth_client.post(CREATE_URL, data={'contract_mode': 'lookup'})
        body = resp.get_data(as_text=True)
        lookup_radio = body.split('id="contractModeLookup"')[1][:200]
        assert 'checked' in lookup_radio

    def test_duplicate_contract_number_is_a_form_error(self, auth_client,
                                                       any_contract):
        resp = auth_client.post(CREATE_URL, data={
            'contract_number': any_contract.contract_number,
            'title': 'Duplicate attempt',
            'start_date': '2024-01-01',
            'contract_source_id': str(any_contract.contract_source_id),
            'principal_investigator_user_id':
                str(any_contract.principal_investigator_user_id),
        })
        assert resp.status_code == 200
        assert 'already exists' in resp.get_data(as_text=True)
        assert 'HX-Trigger' not in resp.headers

    def test_unknown_monitor_is_rejected_before_the_write(self, auth_client,
                                                          any_contract_source):
        resp = auth_client.post(CREATE_URL, data={
            'contract_number': 'TEST-NO-SUCH-USER',
            'title': 'Bad FK',
            'start_date': '2024-01-01',
            'contract_source_id': str(any_contract_source.contract_source_id),
            'principal_investigator_user_id': '99999999',
            'contract_monitor_user_id': '99999998',
        })
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'principal investigator does not exist' in body
        assert 'contract monitor does not exist' in body


class TestAwardLookup:
    """Prefill only — this endpoint never writes, and never destroys input."""

    def _args(self, **extra):
        args = {'contract_number': 'AGS-1852977', 'contract_source_id': '1'}
        args.update(extra)
        return args

    def test_blank_number_is_a_no_swap(self, auth_client):
        resp = auth_client.get(LOOKUP_URL, query_string={'contract_number': ''})
        assert resp.status_code == 204

    def test_prefills_from_the_record(self, auth_client):
        with patch('sam.integration.awards.resolve_award',
                   return_value=_award()):
            resp = auth_client.get(LOOKUP_URL, query_string=self._args())
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert 'The Management and Operation of NCAR' in body
        assert '2018-10-01' in body and '2028-09-30' in body
        assert 'show-award?AWD_ID=1852977' in body
        assert 'NSF Awards API' in body      # provenance is shown

    def test_not_found_keeps_what_was_typed(self, auth_client):
        with patch('sam.integration.awards.resolve_award', return_value=None):
            resp = auth_client.get(LOOKUP_URL, query_string=self._args(
                title='Half-typed title'))
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert 'No award matching' in body
        assert 'Half-typed title' in body    # nothing wiped

    def test_source_unavailable_is_distinct_from_not_found(self, auth_client):
        with patch('sam.integration.awards.resolve_award',
                   side_effect=AwardSourceUnavailable('down')):
            resp = auth_client.get(LOOKUP_URL, query_string=self._args(
                title='Half-typed title'))
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert 'could not be reached' in body
        assert 'No award matching' not in body
        assert 'Half-typed title' in body

    def test_unresolved_person_becomes_a_hint_not_a_selection(self, auth_client):
        record = _award(pi=PersonRef(name='Nobody Atall',
                                     email='nobody@example.test'),
                        monitor=None)
        with patch('sam.integration.awards.resolve_award', return_value=record):
            resp = auth_client.get(LOOKUP_URL, query_string=self._args())
        body = resp.get_data(as_text=True)
        assert 'Nobody Atall' in body
        assert 'no matching SAM user' in body
        assert 'search-suggested-person' in body

    def test_unresolved_person_clears_a_stale_pick(self, auth_client):
        """Refetching a different award must not keep the previous PI.

        "Never destroy input" governs a *failed* lookup. Here the record
        named someone we could not map, so leaving an earlier award's pick
        sitting next to "no matching SAM user" would be actively wrong.
        """
        record = _award(pi=PersonRef(name='Nobody Atall',
                                     email='nobody@example.test'),
                        monitor=None)
        with patch('sam.integration.awards.resolve_award', return_value=record):
            resp = auth_client.get(LOOKUP_URL, query_string=self._args(
                principal_investigator_user_id='4242'))
        body = resp.get_data(as_text=True)
        assert 'value="4242"' not in body
        assert 'no matching SAM user' in body

    def test_provider_without_an_opinion_leaves_the_field_alone(
            self, auth_client, any_nsf_program):
        """USAspending has no people at all — that must not clear a pick."""
        record = _award(provenance='USAspending', pi=None, monitor=None,
                        contract_number=None, program_name=None,
                        unavailable_fields=frozenset({'pi', 'monitor'}))
        with patch('sam.integration.awards.resolve_award', return_value=record):
            resp = auth_client.get(LOOKUP_URL, query_string=self._args(
                contract_source_id='2',
                nsf_program_id=str(any_nsf_program.nsf_program_id)))
        body = resp.get_data(as_text=True)
        assert f'value="{any_nsf_program.nsf_program_id}"' in body

    def test_unknown_program_offers_create_and_select(self, auth_client):
        record = _award(program_name='A PROGRAM THAT IS NOT IN SAM AT ALL')
        with patch('sam.integration.awards.resolve_award', return_value=record):
            resp = auth_client.get(LOOKUP_URL, query_string=self._args())
        body = resp.get_data(as_text=True)
        assert 'A PROGRAM THAT IS NOT IN SAM AT ALL' in body
        assert 'create and select it' in body

    def test_known_program_is_preselected(self, auth_client, any_nsf_program):
        """Matched case-insensitively, and the picker gets its badge label."""
        record = _award(program_name=any_nsf_program.nsf_program_name.lower())
        with patch('sam.integration.awards.resolve_award', return_value=record):
            resp = auth_client.get(LOOKUP_URL, query_string=self._args())
        body = resp.get_data(as_text=True)
        assert 'create and select it' not in body
        assert f'value="{any_nsf_program.nsf_program_id}"' in body
        assert any_nsf_program.nsf_program_name in body

    def test_unavailable_fields_are_stated_not_left_blank(self, auth_client):
        record = _award(provenance='USAspending', pi=None, monitor=None,
                        contract_number=None, program_name=None,
                        unavailable_fields=frozenset({'pi', 'monitor'}))
        with patch('sam.integration.awards.resolve_award', return_value=record):
            resp = auth_client.get(LOOKUP_URL, query_string=self._args(
                contract_source_id='2'))
        body = resp.get_data(as_text=True)
        assert 'cannot supply' in body
        assert 'the PI' in body and 'the Monitor' in body

    def test_program_create_and_select_returns_a_populated_picker(
            self, auth_client, any_nsf_program):
        """Accepting the hint must leave the picker showing the program.

        The button posts an existing name here, so this exercises the
        already-exists branch without writing a row.
        """
        resp = auth_client.post(PROGRAM_CREATE_URL, data={
            'nsf_program_name': any_nsf_program.nsf_program_name.lower(),
        })
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert f'value="{any_nsf_program.nsf_program_id}"' in body
        assert any_nsf_program.nsf_program_name in body   # badge label
        assert 'create and select it' not in body         # hint is resolved

    def test_resolved_person_keeps_its_badge_label(self, auth_client):
        """fk_search_field renders the badge from <field>_display, which
        nothing in the DOM posts — the route must synthesise it."""
        from sam.core.users import User
        record = _award(pi=PersonRef(name='Nobody', email='nobody@example.test'),
                        monitor=None)
        with patch('sam.integration.awards.resolve_award', return_value=record), \
             patch('sam.integration.awards.resolve_person') as resolve:
            resolve.return_value = User(user_id=1, username='someone',
                                        first_name='Some', last_name='One')
            resp = auth_client.get(LOOKUP_URL, query_string=self._args())
        body = resp.get_data(as_text=True)
        assert 'fk-picker-badge' in body
        assert 'someone' in body
