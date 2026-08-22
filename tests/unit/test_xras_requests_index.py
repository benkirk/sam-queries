"""``sam.queries.xras_requests`` — the one derivation two consumers share.

The sweep builds ~100 of these an hour; the post-write patch builds one. They
call this function, so what is worth pinning is the traps in the payload it
parses and the promises the card renders against.
"""

from __future__ import annotations

import pytest

from sam.queries.xras_requests import (
    DRAFT_ACTION_STATUS,
    actions_from_payload,
    request_index_entry,
    resolve_pi,
    roster_from_payload,
)

pytestmark = pytest.mark.unit


def _payload(**over):
    payload = {
        'requestId': 900001, 'requestNumber': 'EXAM0001',
        'requestStatus': 'Approved', 'requestType': 'New',
        'opportunityId': 5, 'opportunity_name': 'Small Allocation',
        'submitDate': '2026-01-01T00:00:00Z',
        'roles': [{'person': {'username': 'pi-user', 'firstName': 'Ada',
                              'lastName': 'Invented', 'isReconciled': True,
                              'email': 'ada@example.invalid',
                              'phone': '555', 'residenceCountry': 'Canada'},
                   'roles': [{'roleId': 1, 'role': 'PI', 'roleTypeId': 13},
                             {'roleId': 9, 'role': 'User', 'roleTypeId': 19}]}],
        'actions': [{'actionId': 7, 'actionType': 'Supplement',
                     'actionStatus': 'Approved'}],
    }
    payload.update(over)
    return payload


class TestTheNestingTrap:
    """`roles[]` entries are `{person, roles[]}` and spell the role `role`."""

    def test_one_row_per_role_not_per_person(self):
        """Two roles for one person must stay two rows — remove needs the id."""
        rows = roster_from_payload(_payload())
        assert [(r['username'], r['role_id'], r['role_type']) for r in rows] == [
            ('pi-user', 1, 'PI'), ('pi-user', 9, 'User')]

    def test_the_outer_object_has_no_role_type(self):
        """Reading `roleType` off the outer entry returns None — silently."""
        entry = _payload()['roles'][0]
        assert entry.get('roleType') is None
        assert roster_from_payload(_payload())[0]['role_type'] == 'PI'

    def test_the_pi_is_resolved_by_id_not_by_name(self):
        """Role *names* are process vocabulary; the id is the stable key."""
        rows = roster_from_payload(_payload())
        assert resolve_pi(rows) == 'pi-user'

    def test_no_pi_on_the_roster_is_none_not_a_guess(self):
        rows = [r for r in roster_from_payload(_payload())
                if r['role_type_id'] != 13]
        assert resolve_pi(rows) is None


class TestNoPiiInTheSnapshot:
    """The payload has full person objects inline. The entry must not."""

    def test_the_roster_carries_flags_not_contact_details(self):
        row = roster_from_payload(_payload())[0]
        assert set(row) == {'role_id', 'role_type_id', 'role_type', 'username',
                            'name', 'placeholder', 'is_reconciled'}

    def test_no_email_or_country_reaches_the_entry(self):
        blob = repr(request_index_entry(_payload()))
        assert 'ada@example.invalid' not in blob
        assert 'Canada' not in blob


class TestTheOffers:
    """Snapshot-derived, because `rules{allowedOperations}` is 401 for us."""

    def test_an_approved_action_offers_withdraw(self):
        action = actions_from_payload(_payload())[0]
        assert (action['can_withdraw'], action['can_resubmit']) == (True, False)

    def test_a_drafted_action_offers_resubmit(self):
        payload = _payload(actions=[{'actionId': 7, 'actionType': 'Supplement',
                                     'actionStatus': DRAFT_ACTION_STATUS}])
        action = actions_from_payload(payload)[0]
        assert (action['can_withdraw'], action['can_resubmit']) == (False, True)

    def test_a_terminal_action_offers_nothing(self):
        payload = _payload(actions=[{'actionId': 7, 'actionType': 'New',
                                     'actionStatus': 'Rejected'}])
        action = actions_from_payload(payload)[0]
        assert not action['can_withdraw'] and not action['can_resubmit']

    def test_under_review_is_withdrawable(self):
        """A re-submit lands here, so it must not read as terminal."""
        payload = _payload(actions=[{'actionId': 7, 'actionType': 'Supplement',
                                     'actionStatus': 'Under Review'}])
        assert actions_from_payload(payload)[0]['can_withdraw'] is True


class TestTheEntry:

    def test_the_opportunity_name_is_read_snake_case_first(self):
        """The reports feed spells it `opportunity_name`; the action wire does not."""
        assert request_index_entry(_payload())['opportunity_name'] \
            == 'Small Allocation'

    def test_the_camel_case_spelling_is_accepted_as_a_fallback(self):
        payload = _payload(opportunity_name=None, opportunityName='Camel')
        assert request_index_entry(payload)['opportunity_name'] == 'Camel'

    def test_the_stuck_placeholder_conjunction_is_precomputed(self):
        payload = _payload(roles=[
            {'person': {'username': 'ghost-user-abcde', 'isReconciled': True},
             'roles': [{'roleId': 2, 'role': 'User', 'roleTypeId': 19}]}])
        assert request_index_entry(payload)['has_stuck_placeholder'] is True

    def test_an_unreconciled_placeholder_is_the_healthy_path(self):
        """That row means 'create the account' and must not be flagged stuck."""
        payload = _payload(roles=[
            {'person': {'username': 'ghost-user-abcde', 'isReconciled': False},
             'roles': [{'roleId': 2, 'role': 'User', 'roleTypeId': 19}]}])
        assert request_index_entry(payload)['has_stuck_placeholder'] is False

    @pytest.mark.parametrize('missing', ['requestId', 'requestNumber'])
    def test_a_payload_missing_either_identifier_is_dropped(self, missing):
        """Writes key on the id, reads key on the number — both are needed."""
        assert request_index_entry(_payload(**{missing: None})) is None

    def test_a_malformed_payload_costs_its_row_not_the_run(self):
        assert request_index_entry('not a dict') is None
        assert request_index_entry(None) is None

    def test_pending_push_is_passed_in_not_derived(self):
        """The sweep resolves the whole set at once; a patch resolves one."""
        assert request_index_entry(_payload())['pending_push'] is False
        assert request_index_entry(_payload(), pending_push=True)['pending_push'] \
            is True

    def test_refreshed_at_defaults_absent_so_the_tell_means_something(self):
        assert request_index_entry(_payload())['refreshed_at'] is None


class TestAMalformedActionCostsItsRowNotTheCard:
    """Every offer routes through ``url_for(..., action_id=<int>)``, so an
    action carrying ``actionId: None`` cannot support a single button — and
    letting it through was a ``BuildError`` that 500'd the whole fragment."""

    def test_an_action_without_an_id_is_dropped(self):
        payload = _payload(actions=[
            {'actionId': None, 'actionType': 'New', 'actionStatus': 'Approved'},
            {'actionType': 'New', 'actionStatus': 'Approved'},
            {'actionId': 7, 'actionType': 'Supplement',
             'actionStatus': 'Approved'},
        ])
        rows = actions_from_payload(payload)
        assert [r['action_id'] for r in rows] == [7]

    def test_the_request_itself_survives_its_malformed_action(self):
        payload = _payload(actions=[{'actionStatus': 'Approved'}])
        entry = request_index_entry(payload)
        assert entry is not None
        assert entry['actions'] == []
