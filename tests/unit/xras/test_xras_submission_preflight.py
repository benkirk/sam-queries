"""``sam.xras.submission_preflight``: the review step's reads and the never-writes verdict."""

from datetime import date, timedelta

import pytest

from sam.manage.xras_submission import ActionRequest, ResourceAmount
from sam.xras.submission_preflight import (
    draft_action,
    extension_floor,
    limit_refusals,
    line_resources,
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
