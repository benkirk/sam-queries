"""The Remediations card's read-only dialogs: the modal GETs, the request, XRAS user and
opportunity detail modals. Every modal GET degrades with a 200 (see `test_xras_remediations.py`).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sam.integration.xras_api.base import XrasSourceUnavailable

# The shared fixtures and payload builders; `_cache` is the autouse cache reset.
from _remediations_support import (  # noqa: F401
    _cache,
    FRAGMENT,
    configured,
    armed,
    _payload,
    _publish,
    _detail_payload,
    _reader,
    _person,
)

# modal GETs

class TestModalGets:

    def test_the_merge_modal_renders_its_candidates(self, auth_client, armed,
                                                    monkeypatch):
        _reader(monkeypatch,
                person={'username': 'ghost-user-abcde', 'firstName': 'G',
                        'lastName': 'Host', 'email': 'g@example.invalid',
                        'organization': 'Example U', 'isReconciled': True},
                candidates=[{'username': 'ghost', 'firstName': 'G',
                             'lastName': 'Host', 'email': 'g@example.invalid',
                             'organization': 'Example U'}])
        body = auth_client.get(
            '/allocations/xras_merge_form/ghost-user-abcde').get_data(as_text=True)
        assert 'ghost' in body
        assert 'email matches exactly' in body
        # A single match is the confirmed target: no radio to click, the value
        # rides a hidden input, and the free-text override does not render.
        assert 'type="radio"' not in body
        assert 'type="hidden" name="candidate" value="ghost"' in body
        assert 'Or merge into a different XRAS username' not in body

    def test_two_candidates_leave_nothing_preselected(self, auth_client, armed,
                                                      monkeypatch):
        """WARNING: Measured: two live identities for one human, differing only by
        email and organization. A default would pick one, and merge deletes
        the other."""
        _reader(monkeypatch,
                person={'username': 'ghost-user-abcde', 'lastName': 'Host',
                        'email': 'g@example.invalid',
                        'organization': 'Example U'},
                candidates=[
                    {'username': 'ghost', 'lastName': 'Host',
                     'email': 'g@example.invalid', 'organization': 'Example U'},
                    {'username': 'ghosty', 'lastName': 'Host',
                     'email': 'other@example.invalid', 'organization': 'NCAR'}])
        body = auth_client.get(
            '/allocations/xras_merge_form/ghost-user-abcde').get_data(as_text=True)
        assert 'checked' not in body
        assert '2 candidates' in body

    def test_a_single_strong_match_is_the_confirmed_target(self, auth_client,
                                                           armed, monkeypatch):
        # Email resolves to exactly one identity -> rank 0 -> confirmed target,
        # no radio, no override (the one-click case the operator asked for).
        _reader(monkeypatch,
                person={'username': 'ghost-user-abcde', 'firstName': 'G',
                        'lastName': 'Host', 'email': 'g@example.invalid',
                        'organization': 'Example U'},
                candidates=[{'username': 'ghost', 'firstName': 'G',
                             'lastName': 'Host', 'email': 'g@example.invalid',
                             'organization': 'Example U'}])
        body = auth_client.get(
            '/allocations/xras_merge_form/ghost-user-abcde').get_data(as_text=True)
        assert 'Merging into' in body
        assert 'type="radio"' not in body
        assert 'type="hidden" name="candidate" value="ghost"' in body
        assert 'Or merge into a different XRAS username' not in body

    def test_a_single_weak_match_still_gets_a_radio(self, auth_client, armed,
                                                    monkeypatch):
        # A lone name-only candidate (different email and organization) is not
        # trustworthy enough to auto-confirm -> it stays a radio to pick, with
        # the override available.
        _reader(monkeypatch,
                person={'username': 'ghost-user-abcde', 'firstName': 'G',
                        'lastName': 'Host', 'email': 'kyle@example.invalid',
                        'organization': 'Rutgers'},
                candidates=[{'username': 'ghosty', 'firstName': 'A',
                             'lastName': 'Host', 'email': 'alan@nsf.example',
                             'organization': 'NSF'}])
        body = auth_client.get(
            '/allocations/xras_merge_form/ghost-user-abcde').get_data(as_text=True)
        assert 'name only' in body
        assert 'type="radio" name="candidate"' in body
        assert 'Or merge into a different XRAS username' in body
        assert 'Merging into' not in body

    def test_an_already_merged_placeholder_says_so(self, auth_client, armed,
                                                   monkeypatch):
        _reader(monkeypatch, person=None)
        response = auth_client.get(
            '/allocations/xras_merge_form/ghost-user-abcde')
        assert response.status_code == 200
        assert 'already been merged away' in response.get_data(as_text=True)

    def test_the_withdraw_modal_names_both_identities(self, auth_client, armed,
                                                      monkeypatch):
        _reader(monkeypatch)
        _publish(_payload())
        body = auth_client.get(
            '/allocations/xras_withdraw_form/EXAM0001/7').get_data(as_text=True)
        assert 'pi-user' in body, 'the impersonated user'
        assert 'benkirk' in body, 'the recorded operator'
        assert 'de-approves' in body

    def test_the_withdraw_modal_says_a_multi_action_request_stays_open(
            self, auth_client, armed, monkeypatch):
        payload = _payload()
        payload['actions'].append({'actionId': 8, 'actionType': 'New',
                                   'actionStatus': 'Approved'})
        _reader(monkeypatch, payload=payload)
        body = auth_client.get(
            '/allocations/xras_withdraw_form/EXAM0001/7').get_data(as_text=True)
        assert 'stay' in body and '2 actions' in body

    def test_a_placeholder_role_holder_is_flagged(self, auth_client, armed,
                                                  monkeypatch):
        """WARNING: The project lead is sometimes an unmerged placeholder — 2 of 27
        live rows the first time this card met production.

        XRAS authorizes the call (the placeholder really does hold the role),
        so this is a tell rather than a block: the operator is acting as a
        throwaway identity that a merge on this same card would delete.
        Silently preferring a different role-holder would change who the write
        is attributed to.
        """
        payload = _payload()
        # The placeholder holds PI, exactly as NCAR4262 does in production.
        payload['roles'] = [{'person': {'username': 'ghost-user-abcde',
                                        'firstName': 'G', 'lastName': 'Host',
                                        'isReconciled': True},
                             'roles': [{'roleId': 1, 'role': 'PI',
                                        'roleTypeId': 13}]}]
        _reader(monkeypatch, payload=payload)
        body = auth_client.get(
            '/allocations/xras_withdraw_form/EXAM0001/7').get_data(as_text=True)
        assert 'placeholder identity' in body

    def test_a_real_role_holder_is_not_flagged(self, auth_client, armed,
                                               monkeypatch):
        _reader(monkeypatch)
        body = auth_client.get(
            '/allocations/xras_withdraw_form/EXAM0001/7').get_data(as_text=True)
        assert 'placeholder identity' not in body

    def test_the_detail_modal_renders_the_live_roster(self, auth_client, armed,
                                                      monkeypatch):
        """The roster is inline in the detail modal now; the add-role select
        carries XRAS's display vocabulary."""
        _reader(monkeypatch)
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'ghost-user-abcde' in body
        assert 'Project Lead' in body, 'XRAS display vocabulary, not "PI"'

    @pytest.mark.parametrize('path', [
        '/allocations/xras_merge_form/ghost-user-abcde',
        '/allocations/xras_withdraw_form/EXAM0001/7',
        '/allocations/xras_resubmit_form/EXAM0001/7',
        '/allocations/xras_request_detail/EXAM0001',
        '/allocations/xras_user_detail/janebaldwin',
        '/allocations/xras_opportunity_detail/532220',
    ])
    def test_an_outage_degrades_with_a_200(self, auth_client, armed,
                                           monkeypatch, path):
        """WARNING: htmx will not swap a 4xx into an open modal — an error status
        renders as an empty modal indistinguishable from a broken button."""
        client = MagicMock()
        client.get_request_line.side_effect = XrasSourceUnavailable('down')
        client.get_request_family_by_number.side_effect = XrasSourceUnavailable('down')
        client.get_person.side_effect = XrasSourceUnavailable('down')
        client.get_opportunity.side_effect = XrasSourceUnavailable('down')
        monkeypatch.setattr(
            'sam.integration.xras_api.client.XrasApiClient.from_environment',
            classmethod(lambda cls, *a, **k: client))
        response = auth_client.get(path)
        assert response.status_code == 200
        assert 'not answering' in response.get_data(as_text=True)


# the read-only detail modal (Part A)

class TestRequestDetailModal:
    """The read-only detail modal, and the surface the editors hang off.

    Its data source is the live `reports/request_numbers` payload, which is
    self-describing — resources carry their own name, units and stage — so the
    modal renders those directly with no resource-key mapping.
    """

    def test_a_permitted_operator_gets_200(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        response = auth_client.get('/allocations/xras_request_detail/EXAM0001')
        assert response.status_code == 200

    def test_it_pivots_resources_into_a_stage_matrix(self, auth_client, armed,
                                                     monkeypatch):
        """One row per resource, one column per stage present — a resource that
        was requested and then awarded is a SINGLE row, not repeated down three
        stage lists."""
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'EXAM0001' in body
        # The stage labels are the column headers that make requested-vs-awarded
        # legible; the payload's Cheyenne has a Requested and an Approved line.
        assert 'Requested' in body and 'Approved' in body
        # Rendered as a matrix — a "Resource" column header — with the
        # self-describing name + units straight from the payload.
        assert '>Resource</th>' in body
        assert 'Cheyenne' in body
        assert 'Core-hours' in body
        # Both the requested (555) and awarded (500) amounts for that one
        # resource are present (a single pivoted row, a cell per stage).
        assert '555' in body and '500' in body

    def test_it_renders_the_rich_sections(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'A study of atmospheric turbulence.' in body   # abstract
        assert 'Atmospheric Science' in body                  # FoS
        assert 'AGS-123' in body                              # grant
        assert 'award.pdf' in body                            # document

    def test_documents_on_an_older_action_show_above_the_actions(
            self, auth_client, armed, monkeypatch):
        """They sit on the original New, collapsed once a later action exists."""
        payload = _detail_payload()
        first = payload['actions'][0]
        payload['actions'].append({**first, 'actionId': first['actionId'] + 1,
                                   'actionType': 'Extension', 'documents': [],
                                   'resources': [], 'allocationDates': []})
        _reader(monkeypatch, payload=payload)
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'Supporting information' in body, 'Supp_Info gets its label'
        assert body.index('Award Letter') < body.index('xras-action-head')

    def test_a_non_integer_size_does_not_break_the_modal(
            self, auth_client, armed, monkeypatch):
        payload = _detail_payload()
        payload['actions'][0]['documents'][0]['size'] = '44042'
        _reader(monkeypatch, payload=payload)
        response = auth_client.get('/allocations/xras_request_detail/EXAM0001')
        assert response.status_code == 200 and 'Award Letter' in response.get_data(as_text=True)

    def test_it_carries_the_oob_title(self, auth_client, armed, monkeypatch):
        """The shared modal shell reads the title from an OOB swap."""
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'id="xrasModalLabel"' in body
        assert 'hx-swap-oob="true"' in body

    def test_it_carries_the_shared_write_buttons(self, auth_client, armed,
                                                 monkeypatch):
        """Roster + actions strip: Withdraw on an Approved action, and the
        roster editor is inline in the modal (add + remove a role) rather than
        a separate Roles… view."""
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'Withdraw…' in body
        # The inline roster editor: an add-role form and a per-role Remove.
        assert 'Add username (SAM or XRAS)' in body
        assert '/allocations/xras_role_remove/EXAM0001/' in body
        # No separate Roles… entry point, and no Details… link back to itself.
        assert 'Roles…' not in body
        assert 'Details…' not in body

    def test_the_actions_list_spans_all_request_lines(self, auth_client, armed,
                                                      monkeypatch):
        """A multi-line project shows ONE actions list across every request line;
        only the globally-most-recent action (in the primary line) is withdrawable."""
        client = _reader(monkeypatch, payload=_detail_payload())
        new_line = _detail_payload()
        new_line['requestId'] = 111
        new_line['requestType'] = 'New'          # its action is id 7 (Supplement)
        renewal = _detail_payload()
        renewal.update({'requestId': 222, 'requestType': 'Renewal',
                        'beginDate': '2027-01-01'})
        renewal['actions'] = [{'actionId': 88, 'actionType': 'Renewal',
                               'actionStatus': 'Approved', 'entryDate': '2027-01-02'}]
        client.get_request_family_by_number.side_effect = None
        client.get_request_family_by_number.return_value = [new_line, renewal]
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        # both lines' actions are in one collapsible list
        assert 'xras-action-7' in body and 'xras-action-88' in body
        assert '2 requests in this project' in body
        # only the latest (88, the renewal) is withdrawable
        assert body.count('Withdraw…') == 1

    def test_a_single_line_request_still_renders_its_actions(self, auth_client,
                                                             armed, monkeypatch):
        """One request line -> the same one-list rendering, its action editable."""
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'xras-action-7' in body       # the lone action
        assert body.count('Withdraw…') == 1  # it is the latest → editable

    def test_the_card_row_links_the_request_to_the_detail_modal(
            self, auth_client, armed):
        """The Request number is the single entry point into the detail modal
        now — the separate "Details…" link is gone."""
        _publish(_payload('EXAM0001'))
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert '/allocations/xras_request_detail/EXAM0001' in body
        assert 'Details…' not in body


# the read-only XRAS User detail modal

_PERSON_ROLES = {
    'panels': [],
    'requestRoles': [
        {'roleName': 'Project Lead',
         'requests': [{'requestNumber': 'UCIR0072', 'requestId': 1446007,
                       'requestTitle': 'CLaSH', 'actionType': 'New',
                       'allocationType': 'Small',
                       'opportunity': 'Small Allocation (University)',
                       'beginDate': '2026-08-04', 'endDate': '2027-08-31',
                       'pi': 'Baldwin, Jane', 'piUsername': 'janebaldwin'}]}],
}


class TestUserDetailModal:
    """The XRAS User modal — the person-side analogue of the request detail,
    reached from any roster username and the Accounts-Needed identity column."""

    def test_a_permitted_operator_gets_200(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, person=_person(), person_roles=_PERSON_ROLES)
        response = auth_client.get('/allocations/xras_user_detail/janebaldwin')
        assert response.status_code == 200

    def test_it_renders_the_person_sheet(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, person=_person(), person_roles=_PERSON_ROLES)
        body = auth_client.get(
            '/allocations/xras_user_detail/janebaldwin').get_data(as_text=True)
        assert 'Jane' in body and 'Baldwin' in body
        assert 'jane.baldwin@uci.edu' in body

    def test_it_carries_the_oob_title(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, person=_person())
        body = auth_client.get(
            '/allocations/xras_user_detail/janebaldwin').get_data(as_text=True)
        assert 'id="xrasModalLabel"' in body
        assert 'hx-swap-oob="true"' in body

    def test_it_lists_cross_request_roles_linked_to_the_request_modal(
            self, auth_client, armed, configured, monkeypatch):
        _reader(monkeypatch, person=_person(), person_roles=_PERSON_ROLES)
        body = auth_client.get(
            '/allocations/xras_user_detail/janebaldwin').get_data(as_text=True)
        assert 'Project Lead' in body          # the role-group label
        assert 'UCIR0072' in body              # the request number
        # Each request keys into the Request modal by number, no toggle.
        assert '/allocations/xras_request_detail/UCIR0072' in body

    def test_an_unknown_user_degrades_with_a_200(self, auth_client, armed,
                                                 monkeypatch):
        """A merged-away placeholder 404s at get_person; the modal must say so
        with a 200, not an empty body htmx cannot swap."""
        _reader(monkeypatch, person=None)
        response = auth_client.get(
            '/allocations/xras_user_detail/gone-user-abcde')
        assert response.status_code == 200
        assert 'no account named' in response.get_data(as_text=True)

    def test_a_back_link_appears_only_when_a_request_is_named(
            self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, person=_person())
        with_req = auth_client.get(
            '/allocations/xras_user_detail/janebaldwin?request_number=EXAM0001'
        ).get_data(as_text=True)
        assert '/allocations/xras_request_detail/EXAM0001' in with_req
        assert 'Back to request EXAM0001' in with_req

        without = auth_client.get(
            '/allocations/xras_user_detail/janebaldwin').get_data(as_text=True)
        assert 'Back to request' not in without

    def test_a_stuck_placeholder_offers_the_merge(self, auth_client, armed,
                                                  monkeypatch):
        """placeholder + reconciled is the contradiction the merge fixes."""
        _reader(monkeypatch,
                person=_person(username='ghost-user-abcde', isReconciled=True))
        body = auth_client.get(
            '/allocations/xras_user_detail/ghost-user-abcde').get_data(as_text=True)
        assert '/allocations/xras_merge_form/ghost-user-abcde' in body

    def test_a_real_user_offers_no_merge(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, person=_person())   # not a placeholder username
        body = auth_client.get(
            '/allocations/xras_user_detail/janebaldwin').get_data(as_text=True)
        assert 'xras_merge_form' not in body

    def test_the_roster_username_links_to_the_user_modal(
            self, auth_client, armed, monkeypatch):
        """The request detail's roster now opens the XRAS User modal per name,
        carrying the request number so the user modal can offer a way back."""
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert '/allocations/xras_user_detail/' in body
        assert 'request_number=EXAM0001' in body


# the read-only opportunity detail modal + FoS name resolution

_OPPORTUNITY = {
    'opportunityId': 532220, 'opportunityName': 'Small Allocation (University)',
    'displayOpportunityName': 'Small Allocation (University)',
    'opportunityType': 'Continuous', 'allocationType': 'Small',
    'allocationTypeInfo': {'allocationType': 'Small',
                           'description': 'Up to 400,000 core-hours for NSF-'
                                          'funded university researchers.'},
    'defaultAllocationAwardPeriod': 12, 'announcementDate': '2022-08-01',
    'opportunityStates': ['Reviews Visible'],
}


class TestOpportunityModal:
    """The opportunity detail modal — reached from the group header and the
    request modal header."""

    def test_a_permitted_operator_gets_200(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, opportunity=_OPPORTUNITY)
        response = auth_client.get('/allocations/xras_opportunity_detail/532220')
        assert response.status_code == 200

    def test_it_renders_the_allocation_description_and_facts(
            self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, opportunity=_OPPORTUNITY)
        body = auth_client.get(
            '/allocations/xras_opportunity_detail/532220').get_data(as_text=True)
        assert 'Small Allocation (University)' in body
        assert 'Up to 400,000 core-hours' in body       # the description
        assert '12 months' in body                       # award period
        assert 'Reviews Visible' in body                 # state

    def test_it_carries_the_oob_title(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, opportunity=_OPPORTUNITY)
        body = auth_client.get(
            '/allocations/xras_opportunity_detail/532220').get_data(as_text=True)
        assert 'id="xrasModalLabel"' in body
        assert 'hx-swap-oob="true"' in body

    def test_an_unknown_opportunity_degrades_with_a_200(self, auth_client, armed,
                                                        monkeypatch):
        _reader(monkeypatch, opportunity=None)
        response = auth_client.get('/allocations/xras_opportunity_detail/999999')
        assert response.status_code == 200
        assert 'no opportunity' in response.get_data(as_text=True)

    def test_a_back_link_appears_only_when_a_request_is_named(
            self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, opportunity=_OPPORTUNITY)
        with_req = auth_client.get(
            '/allocations/xras_opportunity_detail/532220?request_number=EXAM0001'
        ).get_data(as_text=True)
        assert '/allocations/xras_request_detail/EXAM0001' in with_req
        without = auth_client.get(
            '/allocations/xras_opportunity_detail/532220').get_data(as_text=True)
        assert 'Back to request' not in without

    def test_the_request_modal_header_links_the_opportunity(
            self, auth_client, armed, configured, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert '/allocations/xras_opportunity_detail/' in body

    def test_the_group_header_links_the_opportunity_when_configured(
            self, auth_client, configured):
        _publish(_payload('EXAM0001'))     # opportunityId 5
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert '/allocations/xras_opportunity_detail/5' in body

    def test_the_group_header_offers_no_modal_without_the_outbound_api(
            self, auth_client, monkeypatch):
        """The whole card gates on `configured`, so with outgoing off it renders
        its not-configured state and offers no opportunity modal at all."""
        monkeypatch.delenv('XRAS_OUTGOING_ENABLED', raising=False)
        monkeypatch.delenv('XRAS_API_KEY', raising=False)
        _publish(_payload('EXAM0001'))
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'xras_opportunity_detail' not in body


class TestFosNameResolution:
    """A reports payload spells fos as ids only, so the modal resolves them via
    the cached FoS catalog rather than rendering 'FoS 30'."""

    def test_the_request_modal_resolves_fos_ids_to_names(
            self, auth_client, armed, monkeypatch):
        payload = _detail_payload()
        payload['fos'] = [{'fosTypeId': 500032, 'fosNum': '30', 'isPrimary': True}]
        _reader(monkeypatch, payload=payload,
                fos_types=[{'fosTypeId': 500032, 'fosName': 'Regional Climate'}])
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'Regional Climate' in body
        assert 'FoS 30' not in body

    def test_it_falls_back_to_the_id_when_the_catalog_is_unavailable(
            self, auth_client, armed, monkeypatch):
        payload = _detail_payload()
        payload['fos'] = [{'fosTypeId': 500032, 'fosNum': '30', 'isPrimary': True}]
        # Default _reader get_fos_types is [] -> empty map -> id fallback.
        _reader(monkeypatch, payload=payload)
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'FoS 30' in body
