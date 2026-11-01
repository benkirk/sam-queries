"""``sam.xras.submission_preflight``: the review step's reads and the never-writes verdict."""

from datetime import date, timedelta

import pytest

from sam.manage.xras_submission import ActionRequest, ResourceAmount
from sam.xras.submission_preflight import (
    draft_action,
    extension_floor,
    limit_refusals,
    line_resources,
    new_request_line,
    new_request_sam_state,
    preflight_draft,
)


def _line(**kw):
    line = {'requestId': 1449367, 'requestNumber': 'UHSS0001', 'requestType': 'New',
            'requestStatus': 'Approved', 'opportunityId': 532220,
            'roles': [{'person': {'username': 'benkirk'},
                       'roles': [{'roleId': 1, 'roleTypeId': 13, 'role': 'PI'}]}],
            'actions': []}
    line.update(kw)
    return line


class TestTheLine:

    def test_resources_once_each_from_live_actions(self):
        res = lambda rid, name: {'resourceId': rid, 'resourceName': name, 'type': 'Approved'}
        line = _line(actions=[
            {'actionId': 1, 'resources': [res(2, 'Derecho'), res(3, 'Casper')]},
            {'actionId': 2, 'resources': [res(2, 'Derecho')]},
            {'actionId': 3, 'isDeleted': True, 'resources': [res(9, 'Gone')]}])
        assert [(r['resource_id'], r['name']) for r in line_resources(line)] == \
            [(3, 'Casper'), (2, 'Derecho')]

    def test_the_draft_action_is_reports_shaped(self):
        draft = ActionRequest(action_type='Supplement', resources=(ResourceAmount(2, 5.0),),
                              dates=(None, date(2028, 1, 1)))
        action = draft_action(draft)
        assert action['actionId'] is None and action['resources'][0]['type'] == 'Requested'
        assert action['allocationDates'][0]['endDate'] == '2028-01-01'


class TestTheLimits:

    def test_a_refusal_names_the_resource(self, session):
        from sam.accounting.allocation_type_limits import AllocationTypeResourceLimit
        from sam.accounting.allocations import AllocationType
        from sam.resources.resources import Resource
        kind = (session.query(AllocationType).filter(AllocationType.allocation_type ==
                                                     'Education').first())
        derecho = session.query(Resource).filter(Resource.resource_name == 'Derecho').one()
        if kind is None:
            pytest.skip('no Education type')
        AllocationTypeResourceLimit.create(session, allocation_type_id=kind.allocation_type_id,
                                           resource_id=derecho.resource_id, created_by='t',
                                           max_amount=100)
        assert 'limit' in limit_refusals(session, 'Education', {'Derecho': 101})['Derecho']
        assert limit_refusals(session, 'Education', {'Derecho': 99}) == {}
        assert limit_refusals(session, None, {'Derecho': 10 ** 9}) == {}


class TestTheVerdict:

    @pytest.fixture
    def uhss0001(self, session):
        from sam.projects.projects import Project
        project = Project.get_by_projcode(session, 'UHSS0001')
        if project is None or extension_floor(project) is None:
            pytest.skip('UHSS0001 has no extendable allocation in this snapshot')
        return project

    def test_an_extension_before_the_floor_fails_with_the_handlers_words(self, session,
                                                                         uhss0001):
        floor = extension_floor(uhss0001)
        verdict = preflight_draft(session, _line(), ActionRequest(
            action_type='Extension', request_number='UHSS0001',
            dates=(None, floor - timedelta(days=30))))
        assert verdict.status == 'failed'
        assert 'before existing allocation end date' in verdict.messages[0]

    def test_an_extension_after_it_would_land(self, session, uhss0001):
        verdict = preflight_draft(session, _line(), ActionRequest(
            action_type='Extension', request_number='UHSS0001',
            dates=(None, extension_floor(uhss0001) + timedelta(days=365))))
        assert verdict.status == 'rechecked' and verdict.service == 'extend'


_EXPLORATORY = {'opportunity_id': 532221, 'allocation_type': 'Exploratory',
                'opportunity_name': 'Exploratory Allocation (University)'}


class TestTheNew:

    def _draft(self, **kw):
        return ActionRequest(action_type='New', opportunity_id=532221, title='T',
                             abstract='Regional runs.', resources=(ResourceAmount(530902, 1e3),),
                             dates=(date(2026, 12, 1), date(2027, 11, 30)), **kw)

    def test_the_line_carries_the_roster_sam_will_add_and_the_grant_by_name(self):
        draft = self._draft(admin_username='adm1',
                            grants=({'grantNumber': '2345678', 'fundingAgencyId': 1},),
                            roster=({'email': 'u@x.edu', 'username': 'usr1'},
                                    {'email': 'new@x.edu', 'username': None}))
        line = new_request_line(draft, pi_username='pi1', opportunity=_EXPLORATORY,
                                fos={'fosTypeId': 500003, 'fosNum': '1'},
                                agency_names={1: 'National Science Foundation', 14: 'NASA'})
        assert (line['requestNumber'], line['requestType'], line['opportunityId']) == \
            (None, 'New', 532221)
        assert [(r['person']['username'], r['roles'][0]['role']) for r in line['roles']] == \
            [('pi1', 'PI'), ('adm1', 'Allocation Manager'), ('usr1', 'User')]
        assert line['grants'] == [{'grantNumber': '2345678',
                                   'fundingAgency': 'National Science Foundation'}]
        assert line['fos'] == [{'fosTypeId': 500003, 'fosNum': '1', 'isPrimary': True}]

    def test_another_agencys_grant_keeps_its_name(self):
        draft = self._draft(grants=({'grantNumber': '80NSSC21K1305', 'fundingAgencyId': 14},))
        line = new_request_line(draft, pi_username='pi1', opportunity=_EXPLORATORY, fos=None,
                                agency_names={1: 'National Science Foundation', 14: 'NASA'})
        assert line['grants'][0]['fundingAgency'] == 'NASA'

    def test_a_grant_with_dates_still_synthesizes(self):
        from sam.xras.preflight import synthesize_action
        draft = self._draft(grants=({'grantNumber': '2441755', 'fundingAgencyId': 1,
                                     'beginDate': date(2025, 9, 1),
                                     'endDate': date(2030, 8, 31)},))
        line = new_request_line(draft, pi_username='pi1', opportunity=_EXPLORATORY,
                                fos={'fosTypeId': 500003, 'fosNum': '1'})
        assert line['grants'][0]['beginDate'] == '2025-09-01'
        out = synthesize_action(line, draft_action(draft), resource_keys={530902: 1},
                                opportunities=None)
        assert 'schema_invalid' not in out.gaps and out.action is not None

    def test_the_ladder_readout_and_the_verdict_speak_the_same_words(self, session):
        from sam.core.users import User
        from sam.integration.xras import XrasResourceRepositoryKeyResource
        pi = User.get_by_username(session, 'benkirk')
        key = next((k.resource_repository_key for k in session.query(
            XrasResourceRepositoryKeyResource) if k.resource
            and k.resource.resource_name == 'Derecho'), None)
        if pi is None or key is None:
            pytest.skip('no benkirk or no Derecho key mapping in this snapshot')
        state = new_request_sam_state(session, _EXPLORATORY, pi)
        assert state['allocation_type'] is not None
        draft = self._draft()
        verdict = preflight_draft(
            session, new_request_line(draft, pi_username='benkirk', opportunity=_EXPLORATORY,
                                      fos={'fosTypeId': 500003, 'fosNum': '1'}),
            draft, resource_keys={530902: key},
            opportunities={532221: {'allocationType': 'Exploratory'}})
        assert verdict.service == 'add'
        if state['mnemonic'] is None:
            assert set(state['mnemonic_errors']) <= set(verdict.messages)
        else:
            assert verdict.status == 'rechecked'


class TestTheDocumentCheck:

    def test_a_pdf_passes_and_anything_else_is_refused(self):
        from sam.xras.submission_preflight import document_refusal
        assert document_refusal('a.pdf', b'%PDF-1.7 body') is None
        assert 'not a PDF' in document_refusal('a.txt', b'hello')
        assert 'empty' in document_refusal('a.pdf', b'')

    def test_the_size_and_page_hooks_are_dark_until_set(self, monkeypatch):
        from sam.xras.submission_preflight import document_refusal
        pdf = b'%PDF-1.7 ' + b'<< /Type /Page >> ' * 3 + b'<< /Type /Pages >>'
        assert document_refusal('a.pdf', pdf) is None
        monkeypatch.setenv('SUBMISSION_DOCUMENT_MAX_PAGES', '2')
        assert 'has 3 pages' in document_refusal('a.pdf', pdf)
        monkeypatch.setenv('SUBMISSION_DOCUMENT_MAX_PAGES', '')
        monkeypatch.setenv('SUBMISSION_DOCUMENT_MAX_BYTES', '10')
        assert 'larger than' in document_refusal('a.pdf', pdf)


class TestTheVisitorsCodeLetters:
    """A visitor has no SAM user: the institution they typed stands in (XRAS_SUBMISSION_SPRINT2.md 1c)."""

    def test_a_coded_institution_named_exactly_gives_its_code(self, session):
        from factories.core import make_institution, make_mnemonic_code
        from sam.xras.submission_preflight import institution_mnemonic
        inst = make_institution(session, name='Probe Institute of Visitors')
        code = make_mnemonic_code(session, description=inst.name)
        found = institution_mnemonic(session, '  PROBE INSTITUTE OF VISITORS ')
        assert found is not None and found.code == code.code

    @pytest.mark.parametrize('name', ['', None, 'No Such Institution Anywhere', '%'])
    def test_anything_else_gives_none(self, session, name):
        from sam.xras.submission_preflight import institution_mnemonic
        assert institution_mnemonic(session, name) is None
