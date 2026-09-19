"""``sam.queries.xras_requests`` — the one derivation two consumers share.

The sweep builds ~100 of these an hour; the post-write patch builds one. They
call this function, so what is worth pinning is the traps in the payload it
parses and the promises the card renders against.
"""

from __future__ import annotations

from datetime import date
import pytest

from sam.queries.xras_requests import (
    DRAFT_ACTION_STATUS,
    _preflight_rollup,
    actions_from_payload,
    latest_action_type,
    person_roles_from_payload,
    request_family,
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


class TestRoleWindows:
    """Role begin/end dates reach the modal roster and the PI resolution."""

    def test_undated_roles_are_active(self):
        assert [r['active'] for r in roster_from_payload(_payload())] == [
            True, True]

    def test_an_ended_role_stays_listed_but_reads_as_over(self):
        """Not a filter: removal keys on role_id, so the row must survive."""
        entry = _payload()['roles'][0]
        entry['roles'][1]['endDate'] = '2026-07-28'
        rows = roster_from_payload(_payload(roles=[entry]))
        assert [(r['role_id'], r['active']) for r in rows] == [
            (1, True), (9, False)]

    def test_resolve_pi_prefers_a_current_pi_over_an_ended_one(self):
        entry = _payload()['roles'][0]
        entry['roles'] = [dict(entry['roles'][0], endDate='2026-07-28')]
        successor = {'person': {'username': 'new-pi', 'firstName': 'Grace',
                                'lastName': 'Current'},
                     'roles': [{'roleId': 3, 'role': 'PI', 'roleTypeId': 13}]}
        rows = roster_from_payload(_payload(roles=[entry, successor]))
        assert resolve_pi(rows) == 'new-pi'

    def test_an_ended_pi_is_the_fail_open_fallback(self):
        """A request with only a historical lead still needs an XA-USER."""
        entry = _payload()['roles'][0]
        entry['roles'] = [dict(entry['roles'][0], endDate='2026-07-28')]
        rows = roster_from_payload(_payload(roles=[entry]))
        assert resolve_pi(rows) == 'pi-user'


class TestTheProjectAdmin:
    """The Allocation Manager (SAM: "Project Admin") — resolved by id like the
    PI, and often simply absent."""

    def _with_admin(self):
        payload = _payload()
        payload['roles'].append(
            {'person': {'username': 'admin-user', 'firstName': 'Al',
                        'lastName': 'Manager', 'isReconciled': True},
             'roles': [{'roleId': 2, 'role': 'Allocation Manager',
                        'roleTypeId': 14}]})
        return payload

    def test_it_resolves_the_allocation_manager_by_id(self):
        entry = request_index_entry(self._with_admin())
        assert entry['admin']['username'] == 'admin-user'
        assert entry['admin']['name']            # display name from the person

    def test_no_admin_on_the_request_is_none_not_a_guess(self):
        # The base payload names a PI and a User, never an Allocation Manager.
        assert request_index_entry(_payload())['admin'] == {
            'username': None, 'name': None}


class TestPersonRoles:
    """``person_roles_from_payload`` shapes the ``reports/username`` feed the
    XRAS User modal renders — grouped by role, keyed to the Request modal by
    number, and carrying no ``requestStatus`` because the feed has none."""

    def _payload(self, **over):
        payload = {
            'panels': [],
            'requestRoles': [
                {'roleName': 'Project Lead',
                 'requests': [
                     {'requestNumber': 'UCIR0072', 'requestId': 1446007,
                      'requestTitle': 'CLaSH', 'actionType': 'New',
                      'allocationType': 'Small',
                      'opportunity': 'Small Allocation (University)',
                      'beginDate': '2026-08-04', 'endDate': '2027-08-31',
                      'pi': 'Baldwin, Jane', 'piUsername': 'janebaldwin'}]},
                {'roleName': 'User',
                 'requests': [
                     {'requestNumber': 'NCAR0007', 'requestID': 42}]},
            ],
        }
        payload.update(over)
        return payload

    def test_groups_are_preserved_and_role_labelled(self):
        groups = person_roles_from_payload(self._payload())
        assert [g['role_name'] for g in groups] == ['Project Lead', 'User']
        assert groups[0]['projects'][0]['request_number'] == 'UCIR0072'

    def test_it_takes_either_spelling_of_the_id(self):
        # The feed spells it requestId in one place, requestID in another.
        groups = person_roles_from_payload(self._payload())
        assert groups[0]['projects'][0]['actions'][0]['request_id'] == 1446007
        assert groups[1]['projects'][0]['actions'][0]['request_id'] == 42

    def test_dates_are_parsed_for_fmt_date(self):
        from datetime import date
        action = person_roles_from_payload(
            self._payload())[0]['projects'][0]['actions'][0]
        assert action['begin_date'] == date(2026, 8, 4)
        assert action['end_date'] == date(2027, 8, 31)

    def test_actions_group_under_one_project_not_duplicated_rows(self):
        # The feed lists one entry per action; the same projcode must collapse to
        # one project carrying the action list, oldest action first.
        from datetime import date
        payload = self._payload(requestRoles=[{'roleName': 'User', 'requests': [
            {'requestNumber': 'UWAS0104', 'requestId': 1, 'requestTitle': 'SST',
             'actionType': 'New', 'beginDate': '2021-05-01',
             'endDate': '2023-05-31', 'updateDate': '2021-04-28'},
            {'requestNumber': 'UWAS0104', 'requestId': 2, 'requestTitle': 'SST',
             'actionType': 'Extension', 'beginDate': '2023-05-31',
             'endDate': '2024-05-31', 'updateDate': '2023-05-31'},
        ]}])
        projects = person_roles_from_payload(payload)[0]['projects']
        assert len(projects) == 1
        proj = projects[0]
        assert proj['request_number'] == 'UWAS0104'
        assert proj['title'] == 'SST'
        # Oldest-first (newest at the bottom).
        assert [a['action_type'] for a in proj['actions']] == ['New', 'Extension']
        # Project recency is its newest (last) action; each action keeps its own PoP.
        assert proj['activity_date'] == date(2023, 5, 31)
        assert proj['actions'][0]['begin_date'] == date(2021, 5, 1)

    def test_a_request_without_a_number_is_dropped(self):
        # The modal's only link key is the number; a row without one cannot
        # support the Request link, so it costs the row, not the panel.
        payload = self._payload(requestRoles=[
            {'roleName': 'User', 'requests': [
                {'requestId': 5},
                {'requestNumber': 'NCAR0009', 'requestId': 6}]}])
        projects = person_roles_from_payload(payload)[0]['projects']
        assert [p['request_number'] for p in projects] == ['NCAR0009']

    def test_a_group_with_no_usable_request_is_dropped(self):
        payload = self._payload(requestRoles=[
            {'roleName': 'User', 'requests': [{'requestId': 5}]}])
        assert person_roles_from_payload(payload) == []

    def test_a_malformed_payload_costs_the_panel_not_the_run(self):
        assert person_roles_from_payload('not a dict') == []
        assert person_roles_from_payload(None) == []
        assert person_roles_from_payload({}) == []


class TestNoPiiInTheSnapshot:
    """The payload has full person objects inline. The entry must not."""

    def test_the_roster_carries_flags_not_contact_details(self):
        row = roster_from_payload(_payload())[0]
        assert set(row) == {'role_id', 'role_type_id', 'role_type', 'username',
                            'name', 'active', 'placeholder', 'is_reconciled'}

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

    def test_activity_date_uses_the_latest_action_not_the_request(self):
        from datetime import date
        p = _payload(submitDate='2022-06-16T00:00:00Z')
        p['actions'] = [
            {'actionId': 1, 'actionType': 'New', 'actionStatus': 'Approved',
             'entryDate': '2022-06-16'},
            {'actionId': 2, 'actionType': 'Extension', 'actionStatus': 'Submitted',
             'entryDate': '2026-08-21'}]      # submitDate null, entryDate recent
        e = request_index_entry(p)
        assert e['activity_date'] == date(2026, 8, 21)   # the recent Extension
        assert e['submit_date'] == date(2022, 6, 16)     # request date unchanged


def _line(request_id, request_type, begin, actions, **over):
    payload = {
        'requestId': request_id, 'requestNumber': 'EXAM0001',
        'requestStatus': 'Approved', 'requestType': request_type,
        'beginDate': begin, 'endDate': '2024-12-31',
        'roles': [{'person': {'username': 'pi-user', 'firstName': 'Ada',
                              'lastName': 'Invented'},
                   'roles': [{'roleId': 1, 'role': 'PI', 'roleTypeId': 13}]}],
        'actions': actions,
    }
    payload.update(over)
    return payload


def _new_and_renewal():
    return [
        _line(111, 'New', '2020-01-01', [
            {'actionId': 1, 'actionType': 'New', 'actionStatus': 'Approved',
             'entryDate': '2020-01-01'},
            {'actionId': 2, 'actionType': 'Supplement', 'actionStatus': 'Approved',
             'entryDate': '2021-03-01'}]),
        _line(222, 'Renewal', '2022-05-01', [
            {'actionId': 3, 'actionType': 'Renewal', 'actionStatus': 'Approved',
             'entryDate': '2022-05-01'},
            {'actionId': 4, 'actionType': 'Extension', 'actionStatus': 'Submitted',
             'entryDate': '2024-12-23'}]),
    ]


class TestRequestFamily:
    """Grouping a project's request lines into one allocation-lifecycle tree."""

    def test_two_lines_group_into_one_family(self):
        fam = request_family(_new_and_renewal())
        assert fam['request_number'] == 'EXAM0001'
        assert [r['request_id'] for r in fam['requests']] == [111, 222]  # New first
        assert fam['new_request_id'] == 111

    def test_the_timeline_flattens_actions_date_ordered(self):
        from datetime import date
        fam = request_family(_new_and_renewal())
        assert [a['action_id'] for a in fam['timeline']] == [1, 2, 3, 4]
        assert fam['timeline'][-1]['entry_date'] == date(2024, 12, 23)
        # every timeline action carries its parent line id
        assert {a['request_id'] for a in fam['timeline']} == {111, 222}

    def test_activity_date_is_the_max_across_the_family(self):
        from datetime import date
        # The Extension is years after the New — the family's recency is the max.
        assert request_family(_new_and_renewal())['activity_date'] == date(2024, 12, 23)

    def test_span_is_earliest_begin_to_latest_end(self):
        from datetime import date
        fam = request_family(_new_and_renewal())
        assert fam['begin_date'] == date(2020, 1, 1)
        assert fam['end_date'] == date(2024, 12, 31)

    def test_new_renewal_comes_off_the_wire(self):
        fam = request_family(_new_and_renewal())
        by_id = {r['request_id']: r['request_type'] for r in fam['requests']}
        assert (by_id[111], by_id[222]) == ('New', 'Renewal')

    def test_earliest_begin_is_the_new_fallback(self):
        # No line claims New -> the earliest-begin line becomes it.
        lines = _new_and_renewal()
        for ln in lines:
            ln['requestType'] = 'Renewal'
        fam = request_family(lines)
        assert fam['new_request_id'] == 111       # the 2020 line
        assert fam['requests'][0]['request_id'] == 111

    def test_a_bare_dict_is_accepted_as_a_family_of_one(self):
        fam = request_family(_new_and_renewal()[0])
        assert [r['request_id'] for r in fam['requests']] == [111]
        assert fam['new_request_id'] == 111

    def test_a_malformed_input_is_none(self):
        assert request_family('not a list') is None
        assert request_family(None) is None
        assert request_family([]) is None
        assert request_family([{'requestNumber': None}]) is None  # unusable line


def _verdict(status, *, push_state='pending'):
    return {'status': status, 'push_state': push_state}


class TestPreflightRollup:
    """The worst PENDING verdict — applied actions must not poison the badge."""

    def test_it_is_none_when_nothing_is_checked(self):
        assert _preflight_rollup(None) is None
        assert _preflight_rollup({}) is None

    def test_worst_wins_among_pending(self):
        assert _preflight_rollup({1: _verdict('rechecked'),
                                  2: _verdict('failed')}) == 'failed'

    def test_it_ignores_already_applied_actions(self):
        # An old applied action that no longer validates must not poison a
        # request whose next push is fine.
        rollup = _preflight_rollup({1: _verdict('failed', push_state='applied_inferred'),
                                    2: _verdict('rechecked', push_state='unknown')})
        assert rollup == 'rechecked'

    def test_all_applied_falls_back_to_their_verdict_not_none(self):
        # Nothing pending, but the request has a known state — show it, not a
        # false "not checked" that invites a no-op re-check.
        assert _preflight_rollup({1: _verdict('rechecked',
                                              push_state='seen_in_log')}) == 'rechecked'

    def test_it_is_none_when_nothing_was_checked_at_all(self):
        assert _preflight_rollup({1: None}) is None


class TestLatestActionType:
    """The Type column's in-flight pick — what admin.xras.org names it."""

    def test_the_in_flight_action_wins_over_an_older_applied_one(self):
        actions = [{'action_id': 1, 'action_type': 'New', 'action_status': 'Approved'},
                   {'action_id': 2, 'action_type': 'Extension',
                    'action_status': 'Submitted'}]
        assert latest_action_type(actions) == 'Extension'

    def test_it_falls_back_to_the_newest_when_none_are_in_flight(self):
        actions = [{'action_id': 1, 'action_type': 'New', 'action_status': 'Approved'},
                   {'action_id': 5, 'action_type': 'Supplement',
                    'action_status': 'Approved'}]
        assert latest_action_type(actions) == 'Supplement'

    def test_it_is_none_when_no_action_carries_a_type(self):
        assert latest_action_type([]) is None
        assert latest_action_type([{'action_id': 1, 'action_status': 'Approved'}]) is None


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


class TestIsPendingWork:
    """The XRAS-admin "Recent submissions" population, from the snapshot alone."""

    @staticmethod
    def _entry(*actions):
        return {'request_number': 'EXAM0001', 'actions': list(actions)}

    @staticmethod
    def _action(status, push_state=None, *, entry_date=date(2026, 8, 25), checked=True,
                push_detail=None):
        return {'action_id': 1, 'action_status': status, 'entry_date': entry_date,
                'preflight': ({'status': 'rechecked', 'push_state': push_state,
                               'push_detail': push_detail}
                              if checked else None)}

    def test_a_push_that_did_not_land_is_still_pending(self):
        """NCAR4262: two failed posts in the log, still on XRAS admin's list."""
        from sam.queries.xras_requests import is_pending_work
        for status in ('failed', 'manual', 'received'):
            assert is_pending_work(self._entry(self._action(
                'Approved', 'seen_in_log', push_detail={'status': status, 'log_id': 9})))
        assert not is_pending_work(self._entry(self._action(
            'Approved', 'seen_in_log', push_detail={'status': 'processed', 'log_id': 9})))

    def test_an_in_flight_action_is_pending(self):
        from sam.queries.xras_requests import is_pending_work
        for status in ('Submitted', 'Under Review'):
            assert is_pending_work(self._entry(self._action(status, 'unknown')))

    def test_an_approved_new_with_no_sam_project_is_pending(self):
        from sam.queries.xras_requests import is_pending_work
        assert is_pending_work(self._entry(self._action('Approved', 'pending',
                                                        entry_date=date(2025, 1, 1))))

    def test_an_approved_action_after_the_repoint_with_no_log_row_is_pending(self):
        from sam.queries.xras_requests import XRAS_REPOINTED_ON, is_pending_work
        assert is_pending_work(self._entry(
            self._action('Approved', 'unknown', entry_date=XRAS_REPOINTED_ON)))

    def test_a_legacy_era_unknown_is_assumed_posted(self):
        from sam.queries.xras_requests import is_pending_work
        assert not is_pending_work(self._entry(
            self._action('Approved', 'unknown', entry_date=date(2026, 8, 23))))

    def test_a_posted_or_applied_action_is_not_pending(self):
        from sam.queries.xras_requests import is_pending_work
        for push_state in ('seen_in_log', 'applied_inferred'):
            assert not is_pending_work(self._entry(self._action('Approved', push_state)))

    def test_an_action_outside_the_sweep_window_is_not_recent_work(self):
        from sam.queries.xras_requests import is_pending_work
        assert not is_pending_work(self._entry(self._action('Submitted', checked=False)))

    def test_declined_and_empty_are_not_pending(self):
        from sam.queries.xras_requests import is_pending_work
        assert not is_pending_work(self._entry(self._action('Declined', 'unknown')))
        assert not is_pending_work(self._entry())
