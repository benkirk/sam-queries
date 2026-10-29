"""``sam.xras.routing``: the policy table, the family read and the roster gate."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from sam.integration.xras_submission import XrasSubmission
from sam.xras.routing import (
    ROUTING_REASONS,
    XRAS_REQUEST_ROUTES,
    request_routing,
    route_for,
)


def _project(type_name, project_id=-1):
    kind = SimpleNamespace(allocation_type=type_name) if type_name else None
    return SimpleNamespace(allocation_type=kind, project_id=project_id)


def _line(request_id=10, *, deleted=False, roles=(('pi1', 13),), action_id=1):
    return {'requestId': request_id, 'requestNumber': 'UHSS0001', 'isDeleted': deleted,
            'actions': [{'actionId': action_id, 'actionType': 'New'}],
            'roles': [{'person': {'username': u},
                       'roles': [{'roleId': n, 'roleTypeId': t, 'role': 'PI'}]}
                      for n, (u, t) in enumerate(roles)]}


class TestThePolicy:

    @pytest.mark.parametrize('name', ['CHAP', 'Small', 'Small (No NSF award)', 'Data',
                                      'Classroom', 'NSC', 'External Project'])
    def test_the_xras_types(self, name):
        assert route_for(name) == 'xras'

    def test_wrap_goes_by_mail(self, session):
        d = request_routing(session, _project('WRAP'), [_line()], 'pi1', armed=True)
        assert (d.route, d.email, d.allowed) == ('email', 'wrap@uwyo.edu', False)

    @pytest.mark.parametrize('name', ['Divisional', 'Staff', 'CISL Reserve', 'Director Reserve'])
    def test_staff_types(self, session, name):
        d = request_routing(session, _project(name), [_line()], 'pi1', armed=True)
        assert (d.route, d.reason, d.detail) == ('staff', 'policy', None)

    def test_an_unlisted_type_routes_staff_and_says_why(self, session):
        d = request_routing(session, _project('LCAP'), [_line()], 'pi1', armed=True)
        assert d.route == 'staff' and 'LCAP has no XRAS route' in d.detail

    def test_every_route_names_a_real_type(self, session):
        from sam.accounting.allocations import AllocationType
        names = {n for (n,) in session.query(AllocationType.allocation_type)}
        assert set(XRAS_REQUEST_ROUTES) <= names


class TestTheGates:

    def test_lever_off(self, session):
        d = request_routing(session, _project('Small'), [_line()], 'pi1', armed=False)
        assert d.reason == 'lever_off' and d.message == ROUTING_REASONS['lever_off']

    def test_no_live_family(self, session):
        for family in ([], None, [_line(deleted=True)]):
            d = request_routing(session, _project('Small'), family, 'pi1', armed=True)
            assert d.reason == 'no_family'

    def test_not_on_the_roster(self, session):
        d = request_routing(session, _project('Small'), [_line()], 'someone', armed=True)
        assert d.reason == 'not_on_roster' and d.pi_username == 'pi1'

    def test_the_primary_line_decides(self, session):
        family = [_line(10, roles=(('old', 13),), action_id=1),
                  _line(11, roles=(('pi1', 13),), action_id=9)]
        d = request_routing(session, _project('Small'), family, 'pi1', armed=True)
        assert d.allowed and d.request_id == 11

    def test_an_open_submission_blocks_a_second(self, session):
        project = _project('Small', project_id=-4242)
        XrasSubmission.create(session, action_type='Extension', created_by='pi1',
                              xa_user='pi1', eula_sha='a' * 40,
                              eula_accepted_at=datetime.now(), payload={},
                              project_id=-4242)
        d = request_routing(session, project, [_line()], 'pi1', armed=True)
        assert d.reason == 'pending'


class TestTheOffers:
    """``action_offers``: one per picker action, disabled with its reason, never hidden."""

    @staticmethod
    def _allowed():
        from sam.xras.routing import RoutingDecision
        return RoutingDecision('xras', request_number='UHSS0001', request_id=10)

    def test_a_refused_decision_disables_every_offer_with_its_reason(self):
        from sam.xras.routing import PICKER_ACTION_TYPES, RoutingDecision, action_offers
        offers = action_offers(RoutingDecision('xras', reason='not_on_roster'), [],
                               lambda t: True)
        assert [o.action_type for o in offers] == list(PICKER_ACTION_TYPES)
        assert {o.reason for o in offers} == {ROUTING_REASONS['not_on_roster']}

    def test_an_unarmed_type_is_disabled_alone(self):
        from sam.xras.routing import action_offers
        offers = {o.action_type: o for o in action_offers(
            self._allowed(), [], lambda t: t == 'Extension')}
        assert offers['Extension'].enabled
        assert 'not enabled here yet' in offers['Supplement'].reason

    def test_a_renewal_needs_xras_to_offer_it(self):
        from sam.xras.routing import action_offers
        available = [{'opportunity_id': 532221, 'request_number': 'UHSS0001',
                      'action_type': 'Renewal', 'resource_ids': [530902]},
                     {'opportunity_id': 1, 'request_number': 'OTHER',
                      'action_type': 'Renewal'}]
        renewal = action_offers(self._allowed(), available, lambda t: True)[-1]
        assert renewal.enabled and (renewal.opportunity_id, renewal.resource_ids) == \
            (532221, (530902,))
        assert 'offers no renewal' in action_offers(
            self._allowed(), available[1:], lambda t: True)[-1].reason
        assert 'did not answer' in action_offers(
            self._allowed(), None, lambda t: True)[-1].reason
