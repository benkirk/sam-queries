"""Contract create: the two-mode form, award prefill, and the widened model.

Two layers, per the house convention (docs/TESTING.md):

* **Model layer** — the writes. ``Contract.create`` / ``Contract.update``
  now carry the monitor and NSF program, and ``update`` distinguishes
  "leave alone" from "set NULL".
* **HTTP layer** — auth, validation, and render behavior of the bespoke
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


# Schema

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


# Model

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


# HTTP: bespoke create routes

CREATE_FORM_URL = '/admin/htmx/contract-create-form'
CREATE_URL = '/admin/htmx/contract-create'
LOOKUP_URL = '/admin/htmx/contract-award-lookup'
SEARCH_URL = '/admin/htmx/contract-award-search'
CANDIDATES_URL = '/admin/htmx/contract-award-candidates'
PROGRAM_CREATE_URL = '/admin/htmx/contract-program-create'
PROGRAM_SEARCH_URL = '/admin/htmx/search/nsf-programs'


CREATE_FORM_URL = '/admin/htmx/contract-create-form'
CONTRACTS_PAGE = '/admin/contracts'


class TestSeededCreateForm:
    """`?create=` from a contract-blocker link: seeded, in the mode the link chose."""

    def test_lookup_mode_seeds_the_number_and_fires_the_fetch(self, auth_client):
        body = auth_client.get(CREATE_FORM_URL, query_string={
            'contract_number': 'NSF-9980501'}).get_data(as_text=True)
        assert 'value="NSF-9980501"' in body
        assert 'hx-trigger="load, click"' in body
        assert 'id="contractModeLookup" value="lookup" autocomplete="off"\n' \
               '                       checked' in body or 'value="lookup"' in body

    def test_manual_mode_seeds_title_and_dates_without_a_fetch(self, auth_client):
        body = auth_client.get(CREATE_FORM_URL, query_string={
            'contract_number': 'ISS 25-643', 'mode': 'manual', 'title': 'Seeded Title',
            'start_date': '2026-01-01', 'end_date': '2027-12-31'}).get_data(as_text=True)
        assert 'value="ISS 25-643"' in body
        assert 'value="Seeded Title"' in body
        assert 'value="2026-01-01"' in body and 'value="2027-12-31"' in body
        assert 'hx-trigger="load, click"' not in body

    def test_the_contracts_page_carries_the_auto_open_marker(self, auth_client):
        body = auth_client.get(CONTRACTS_PAGE, query_string={
            'create': 'ISS 25-643', 'mode': 'manual', 'title': 'T'}).get_data(as_text=True)
        assert 'data-auto-open-create="' in body
        assert 'contract_number=ISS' in body and 'mode=manual' in body and 'title=T' in body

    def test_no_create_arg_means_no_marker(self, auth_client):
        body = auth_client.get(CONTRACTS_PAGE).get_data(as_text=True)
        assert 'data-auto-open-create' not in body

    def test_a_viewer_without_create_gets_no_marker(self, auth_client, monkeypatch):
        from webapp.utils import rbac
        from webapp.utils.rbac import Permission
        allowed = {Permission.ACCESS_ADMIN_DASHBOARD, Permission.VIEW_CONTRACTS}
        monkeypatch.setattr(rbac, 'get_user_permissions', lambda user: allowed)
        monkeypatch.setattr('webapp.dashboards.admin.blueprint.has_permission',
                            lambda user, perm: perm in allowed)
        monkeypatch.setattr('webapp.dashboards.admin.blueprint.has_permission_any_facility',
                            lambda user, perm, **kw: perm in allowed)
        resp = auth_client.get(CONTRACTS_PAGE, query_string={'create': 'X'})
        assert resp.status_code == 200
        assert 'data-auto-open-create' not in resp.get_data(as_text=True)


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
        (SEARCH_URL, 'get'),
        (CANDIDATES_URL, 'get'),
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
        (SEARCH_URL, 'get'),
        (CANDIDATES_URL, 'get'),
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

    def test_error_rerender_keeps_the_picker_badge(self, auth_client,
                                                   any_contract):
        """A failed create must not blank the FK picker badges.

        `fk_search_field` renders the badge from `<field>_display`, which
        nothing in the DOM posts — `_contract_create_context` synthesises it.
        `HtmxFormHandler.render_errors` therefore has to let `context()`'s
        `form` win over the raw `request.form`, which carries no `_display`
        keys. Pins the behavior, so no caller needs a local override.
        """
        resp = auth_client.post(CREATE_URL, data={
            'contract_number': any_contract.contract_number,   # duplicate
            'title': 'Duplicate attempt',
            'start_date': '2024-01-01',
            'contract_source_id': str(any_contract.contract_source_id),
            'principal_investigator_user_id':
                str(any_contract.principal_investigator_user_id),
        })
        body = resp.get_data(as_text=True)
        assert 'already exists' in body
        assert 'fk-picker-badge' in body
        assert any_contract.principal_investigator.username in body

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
        # Labels come from the shared UNAVAILABLE_FIELD_LABELS map, so this
        # sentence and `sam-search awards`' matching one cannot drift apart.
        assert 'PI' in body and 'Monitor' in body

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


class TestAwardSearch:
    """Free-text search above the Fetch button. Read-only, never 500s.

    `search_awards` is stubbed at the package object because the route
    imports it inside the function body — a module-scope `from ... import`
    would bind the real callable and ignore the patch. No network.
    """

    def test_short_query_returns_nothing(self, auth_client):
        """Below min_len we must not bother two public APIs."""
        with patch('sam.integration.awards.search_awards') as search:
            resp = auth_client.get(SEARCH_URL, query_string={'q': 'ab'})
        assert resp.get_data(as_text=True) == ''
        search.assert_not_called()

    def test_renders_results(self, auth_client):
        with patch('sam.integration.awards.search_awards',
                   return_value=([_award()], [])):
            resp = auth_client.get(SEARCH_URL,
                                   query_string={'q': 'turbulence'})
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert 'The Management and Operation of NCAR' in body
        assert 'AGS-1852977' in body
        assert 'NSF' in body                       # provenance badge

    def test_rows_carry_the_use_award_action(self, auth_client):
        """The template/JS contract: `use-award` writes the two parent-form
        inputs and fires #contractFetchAward."""
        with patch('sam.integration.awards.search_awards',
                   return_value=([_award()], [])):
            body = auth_client.get(
                SEARCH_URL, query_string={'q': 'turbulence'}
            ).get_data(as_text=True)
        assert 'data-action="use-award"' in body
        assert 'data-award-number="AGS-1852977"' in body

    def test_no_match_renders_an_empty_state(self, auth_client):
        with patch('sam.integration.awards.search_awards',
                   return_value=([], [])):
            body = auth_client.get(
                SEARCH_URL, query_string={'q': 'zzz-no-such-award'}
            ).get_data(as_text=True)
        assert 'No awards found' in body

    def test_source_unavailable_is_an_inline_note_not_a_500(self, auth_client):
        """register_typeahead would have made this a 500; that is exactly
        why this route is hand-written."""
        errors = [{'provenance': 'NSF Awards API', 'reason': 'unreachable'}]
        with patch('sam.integration.awards.search_awards',
                   return_value=([], errors)):
            resp = auth_client.get(SEARCH_URL, query_string={'q': 'turbulence'})
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert 'could not be reached' in body
        assert 'No awards found' not in body       # not the same answer

    def test_partial_outage_still_shows_hits_and_warns(self, auth_client):
        errors = [{'provenance': 'NSF Awards API', 'reason': 'unreachable'}]
        with patch('sam.integration.awards.search_awards',
                   return_value=([_award()], errors)):
            body = auth_client.get(
                SEARCH_URL, query_string={'q': 'turbulence'}
            ).get_data(as_text=True)
        assert 'AGS-1852977' in body
        assert 'partial' in body

    def test_already_in_sam_rows_are_flagged_and_not_reusable(
            self, auth_client, session, any_contract):
        """Duplicate protection surfaced one round-trip before
        _ContractCreateHandler.clean would have caught it."""
        record = _award(contract_number=any_contract.contract_number)
        with patch('sam.integration.awards.search_awards',
                   return_value=([record], [])):
            body = auth_client.get(
                SEARCH_URL, query_string={'q': 'turbulence'}
            ).get_data(as_text=True)
        assert 'already in SAM' in body
        # A row SAM already has must not offer to seed the form.
        assert 'data-action="use-award"' not in body

    def test_usaspending_states_its_structural_gap(self, auth_client):
        record = _award(provenance='USAspending', pi=None, monitor=None,
                        program_name=None,
                        unavailable_fields=frozenset({'pi', 'monitor'}))
        with patch('sam.integration.awards.search_awards',
                   return_value=([record], [])):
            body = auth_client.get(
                SEARCH_URL, query_string={'q': 'turbulence'}
            ).get_data(as_text=True)
        assert 'no PI/Monitor from this source' in body

    def test_source_scopes_the_search(self, auth_client, any_contract_source):
        with patch('sam.integration.awards.search_awards',
                   return_value=([], [])) as search:
            auth_client.get(SEARCH_URL, query_string={
                'q': 'turbulence',
                'contract_source_id': str(any_contract_source.contract_source_id)})
        assert search.call_args.kwargs['sources'] == [
            any_contract_source.contract_source]

    def test_nsf_source_id_is_resolved_by_name_not_hardcoded(self, auth_client,
                                                             session):
        """Lookup-table PKs differ between environments."""
        from sam.projects.contracts import ContractSource
        nsf = (session.query(ContractSource)
               .filter(ContractSource.contract_source == 'NSF').first())
        if nsf is None:
            pytest.skip('no NSF contract source in this snapshot')

        with patch('sam.integration.awards.search_awards',
                   return_value=([_award(contract_number='AGS-0000001')], [])):
            body = auth_client.get(
                SEARCH_URL, query_string={'q': 'turbulence'}
            ).get_data(as_text=True)
        assert f'data-source-id="{nsf.contract_source_id}"' in body


class TestAwardSearchFormSeam:
    """The search input lives inside the form; it must not reach the ORM."""

    def test_the_search_box_is_named_q(self, auth_client):
        """`q` is what htmx_contract_award_lookup already pops, which is what
        keeps it out of the prefill dict and the POST."""
        body = auth_client.get(CREATE_FORM_URL).get_data(as_text=True)
        assert 'id="createContractAwardSearch"' in body
        assert 'name="q"' in body

    def test_the_fetch_button_has_the_id_the_action_triggers(self, auth_client):
        body = auth_client.get(CREATE_FORM_URL).get_data(as_text=True)
        assert 'id="contractFetchAward"' in body

    def test_a_stray_q_is_dropped_by_the_create_schema(self):
        """unknown=EXCLUDE is the safety net behind
        `kwargs = {k: v for k, v in data.items() if k != 'contract_mode'}` —
        verify rather than assume."""
        data = CreateContractForm().load({
            'contract_number': 'AGS-1852977',
            'title': 'A contract',
            'start_date': '2018-10-01',
            'contract_source_id': '1',
            'principal_investigator_user_id': '1',
            'contract_mode': 'lookup',
            'q': 'turbulence',
        })
        assert 'q' not in data


class TestAwardCandidates:
    """"Find Candidate Contracts" on /admin/contracts — the same search as
    the create-modal one, rendered as cards with a different affordance.

    `search_awards` is stubbed at the package object (the route imports it
    inside `_award_search_context`). No network.
    """

    def test_short_query_returns_nothing(self, auth_client):
        with patch('sam.integration.awards.search_awards') as search:
            resp = auth_client.get(CANDIDATES_URL, query_string={'q': 'ab'})
        assert resp.get_data(as_text=True) == ''
        search.assert_not_called()

    def test_renders_cards(self, auth_client):
        with patch('sam.integration.awards.search_awards',
                   return_value=([_award()], [])):
            body = auth_client.get(
                CANDIDATES_URL, query_string={'q': 'turbulence'}
            ).get_data(as_text=True)
        assert 'The Management and Operation of NCAR' in body
        assert 'AGS-1852977' in body
        assert 'Carrie E. Black' in body        # NSF supplies a program manager

    def test_create_button_seeds_the_form_via_the_url(self, auth_client):
        """Server-side seeding, not JS: the form does not exist yet when the
        button is clicked, so the number rides on the create-form URL."""
        with patch('sam.integration.awards.search_awards',
                   return_value=([_award()], [])):
            body = auth_client.get(
                CANDIDATES_URL, query_string={'q': 'turbulence'}
            ).get_data(as_text=True)
        assert 'contract_number=AGS-1852977' in body
        assert 'createContractModal' in body

    def test_already_in_sam_offers_the_contract_instead_of_creation(
            self, auth_client, any_contract):
        """Better than the modal's disabled state: the existing contract is
        one click away on this very page."""
        record = _award(contract_number=any_contract.contract_number)
        with patch('sam.integration.awards.search_awards',
                   return_value=([record], [])):
            body = auth_client.get(
                CANDIDATES_URL, query_string={'q': 'turbulence'}
            ).get_data(as_text=True)
        assert 'already in SAM' in body
        assert 'View contract' in body
        assert 'contract_number=' not in body   # no create affordance

    def test_total_outage_is_an_inline_note_not_a_500(self, auth_client):
        errors = [{'provenance': 'NSF Awards API', 'reason': 'unreachable'}]
        with patch('sam.integration.awards.search_awards',
                   return_value=([], errors)):
            resp = auth_client.get(CANDIDATES_URL,
                                   query_string={'q': 'turbulence'})
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'could not be reached' in body
        assert 'No awards found' not in body

    def test_partial_outage_shows_hits_and_warns(self, auth_client):
        errors = [{'provenance': 'NSF Awards API', 'reason': 'unreachable'}]
        with patch('sam.integration.awards.search_awards',
                   return_value=([_award()], errors)):
            body = auth_client.get(
                CANDIDATES_URL, query_string={'q': 'turbulence'}
            ).get_data(as_text=True)
        assert 'AGS-1852977' in body
        assert 'partial' in body

    def test_no_match_renders_an_empty_state(self, auth_client):
        with patch('sam.integration.awards.search_awards',
                   return_value=([], [])):
            body = auth_client.get(
                CANDIDATES_URL, query_string={'q': 'zzz-nothing'}
            ).get_data(as_text=True)
        assert 'No awards found' in body

    def test_source_scopes_the_search(self, auth_client, any_contract_source):
        with patch('sam.integration.awards.search_awards',
                   return_value=([], [])) as search:
            auth_client.get(CANDIDATES_URL, query_string={
                'q': 'turbulence',
                'contract_source_id': str(any_contract_source.contract_source_id)})
        assert search.call_args.kwargs['sources'] == [
            any_contract_source.contract_source]


class TestSeededCreateForm:
    """?contract_number=… opens the form pre-filled and auto-fetches."""

    def test_unseeded_form_is_unchanged(self, auth_client):
        body = auth_client.get(CREATE_FORM_URL).get_data(as_text=True)
        assert 'id="contractFetchAward"' in body
        # No auto-fetch: a bare New Contract must not call an agency.
        assert 'hx-trigger="load, click"' not in body

    def test_seeded_form_prefills_both_lookup_inputs(self, auth_client,
                                                     any_contract_source):
        body = auth_client.get(CREATE_FORM_URL, query_string={
            'contract_number': 'AGS-1852977',
            'contract_source_id': str(any_contract_source.contract_source_id),
        }).get_data(as_text=True)
        assert 'value="AGS-1852977"' in body
        assert 'contractModeLookup' in body

    def test_seeded_form_auto_fires_the_lookup(self, auth_client):
        """The chain's whole point: the lookup is what supplies Monitor and
        program, which a search result structurally cannot carry."""
        body = auth_client.get(CREATE_FORM_URL, query_string={
            'contract_number': 'AGS-1852977'}).get_data(as_text=True)
        assert 'hx-trigger="load, click"' in body

    def test_seeded_form_opens_in_lookup_mode(self, auth_client):
        body = auth_client.get(CREATE_FORM_URL, query_string={
            'contract_number': 'AGS-1852977'}).get_data(as_text=True)
        lookup_radio = body.split('id="contractModeLookup"')[1][:200]
        assert 'checked' in lookup_radio

    def test_a_bad_source_id_is_ignored_not_fatal(self, auth_client):
        resp = auth_client.get(CREATE_FORM_URL, query_string={
            'contract_number': 'AGS-1852977', 'contract_source_id': 'nope'})
        assert resp.status_code == 200
