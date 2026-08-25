"""The XRAS Remediations card and its modal routes.

House convention for the HTTP tier: auth, validation, 404s and render smoke.
The write happy-paths are covered at the service layer
(``test_xras_remediation_service.py``), where they belong — a route test that
exercised a real write would either mock the whole world or touch production
XRAS.

Two properties here are specific to this card and worth stating:

* it is a **card, not a fourth tab** — three panes stay three;
* every modal GET degrades with a **200**, because htmx will not swap a 4xx
  into an already-open modal, so an error status renders as an empty modal
  indistinguishable from a broken button.
"""

from __future__ import annotations

import pathlib
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from sam.integration.xras_api import cache as xras_cache
from sam.integration.xras_api.base import XrasSourceUnavailable
from sam.queries.xras_requests import request_index_entry
from webapp.utils.rbac import Permission

pytestmark = pytest.mark.unit

FRAGMENT = '/allocations/xras_remediations'
# Most tests below publish UNCHECKED rows (no preflight), which the default
# pending-work queue excludes by definition, so they read the card with
# show_all=1. TestPendingWorkToggle covers the default.


@pytest.fixture
def view_only_client(auth_client, monkeypatch):
    """`benkirk` minus MANAGE_XRAS — the split has to be simulated.

    Same approach as the sibling card's fixture: patch `get_user_permissions`,
    which every gate in the request path reads through.
    """
    from webapp.utils import rbac

    real = rbac.get_user_permissions

    def limited(user, *args, **kwargs):
        return {p for p in real(user, *args, **kwargs)
                if p != Permission.MANAGE_XRAS}

    for module in (rbac,
                   __import__('webapp.dashboards.allocations.blueprint',
                              fromlist=['x']),
                   __import__('webapp.dashboards.allocations'
                              '.xras.remediation', fromlist=['x'])):
        if hasattr(module, 'get_user_permissions'):
            monkeypatch.setattr(module, 'get_user_permissions', limited)
    monkeypatch.setattr(rbac, 'get_user_permissions', limited)
    return auth_client


@pytest.fixture
def manage_not_admin_client(auth_client, monkeypatch):
    """`benkirk` with MANAGE_XRAS but WITHOUT ADMIN_XRAS.

    The Part C split: a full-editor operator must be refused the destructive
    lifecycle. `view_only_client` cannot test this — it removes only
    MANAGE_XRAS and so still holds ADMIN_XRAS.
    """
    from webapp.utils import rbac

    real = rbac.get_user_permissions

    def limited(user, *args, **kwargs):
        return {p for p in real(user, *args, **kwargs)
                if p != Permission.ADMIN_XRAS}

    for module in (rbac,
                   __import__('webapp.dashboards.allocations.blueprint',
                              fromlist=['x']),
                   __import__('webapp.dashboards.allocations'
                              '.xras.remediation', fromlist=['x'])):
        if hasattr(module, 'get_user_permissions'):
            monkeypatch.setattr(module, 'get_user_permissions', limited)
    monkeypatch.setattr(rbac, 'get_user_permissions', limited)
    return auth_client


@pytest.fixture(autouse=True)
def _cache(monkeypatch):
    monkeypatch.delenv('CACHE_REDIS_URL', raising=False)
    xras_cache._CACHE.reset_for_tests(disabled=False)
    yield
    xras_cache._CACHE.reset_for_tests(disabled=False)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
    monkeypatch.setenv('XRAS_API_KEY', 'k')


@pytest.fixture
def armed(configured, monkeypatch):
    monkeypatch.setenv('XRAS_WRITE_ENABLED', '1')


def _payload(number='EXAM0001', *, status='Approved', action_status='Approved',
             placeholder=True, reconciled=True, submit_date=None):
    roles = [{'person': {'username': 'pi-user', 'firstName': 'P',
                         'lastName': 'Eye', 'isReconciled': True},
              'roles': [{'roleId': 1, 'role': 'PI', 'roleTypeId': 13}]}]
    if placeholder:
        roles.append({'person': {'username': 'ghost-user-abcde',
                                 'firstName': 'G', 'lastName': 'Host',
                                 'isReconciled': reconciled},
                      'roles': [{'roleId': 2, 'role': 'User',
                                 'roleTypeId': 19}]})
    return {'requestId': 900001, 'requestNumber': number,
            'requestStatus': status, 'requestType': 'New',
            'opportunityId': 5, 'opportunity_name': 'Small Allocation',
            # Inside the shared window's default lookback unless a test says
            # otherwise — the window's behavior is its own set of tests below.
            'submitDate': submit_date or datetime.now().strftime('%Y-%m-%dT00:00:00Z'),
            'roles': roles,
            'actions': [{'actionId': 7, 'actionType': 'Supplement',
                         'actionStatus': action_status}]}


def _publish(*payloads, pending=True):
    xras_cache.store_requests_index({
        'generated_at': datetime.now(),
        'statuses': ['Approved', 'Submitted', 'Under Review'],
        'extra_statuses': {},
        'rows': [request_index_entry(p, pending_push=pending) for p in payloads]})


def _detail_payload(number='EXAM0001'):
    """A reports/request_numbers-shaped payload with the rich detail sections.

    Mirrors the live shape probed 2026-08-22: `resources[]` is a flat list, one
    entry per (resource × stage), each self-describing (`displayResourceName`,
    `resourceUnits`, `amount`, `type`).
    """
    payload = _payload(number)
    payload.update({
        'abstract': 'A study of atmospheric turbulence.',
        'title': 'Turbulence at scale',
        'shortTitle': 'Turbulence',
        'fos': [{'fosTypeId': 500003, 'fosNum': '1', 'fosName': 'Atmospheric Science',
                 'isPrimary': True}],
        'grants': [{'grantId': 1, 'grantNumber': 'AGS-123', 'title': 'A grant',
                    'piName': 'P Eye', 'beginDate': '2026-01-01'}],
        'publicURL': 'https://xras.example.invalid/req/EXAM0001',
    })
    payload['actions'][0]['resources'] = [
        {'resourceId': 530201, 'displayResourceName': 'Cheyenne',
         'resourceUnits': 'Core-hours', 'amount': '555.0', 'comments': None,
         'type': 'Requested'},
        {'resourceId': 530201, 'displayResourceName': 'Cheyenne',
         'resourceUnits': 'Core-hours', 'amount': '500.0', 'comments': None,
         'type': 'Approved'},
    ]
    payload['actions'][0]['allocationDates'] = [
        {'allocationDateId': 9, 'beginDate': '2026-01-01', 'endDate': '2026-12-31',
         'type': 'Requested'}]
    payload['actions'][0]['documents'] = [
        {'documentId': 1, 'documentType': 'Supp_Info', 'title': 'Award Letter',
         'filename': 'award.pdf', 'size': 44042}]
    return payload


def _reader(monkeypatch, payload=_payload(), person=None, candidates=(),
            person_roles=None, opportunity=None, fos_types=None):
    """Swap in a scripted read client for every live lookup."""
    client = MagicMock()
    client.get_request_by_number.return_value = payload
    # The detail modal fetches the whole family; derive it from the single-line
    # mock so a test's return_value/side_effect (not-found, outage) still applies.
    client.get_request_family_by_number.side_effect = (
        lambda n: [row] if (row := client.get_request_by_number(n)) else [])
    client.get_person.return_value = person
    client.search_people.return_value = list(candidates)
    # Default to an empty dict, not MagicMock's auto-child — the parser tests
    # its type and a MagicMock would read as "no such thing" only by accident.
    client.get_person_roles.return_value = ({} if person_roles is None
                                            else person_roles)
    client.get_opportunity.return_value = opportunity
    # Real list, not a MagicMock — fos_name_map iterates it (and swallows a
    # TypeError to an empty map, so the default keeps the request modal working
    # with fos ids, but a real list lets a test assert resolved names).
    client.get_fos_types.return_value = ([] if fos_types is None else fos_types)
    monkeypatch.setattr(
        'sam.integration.xras_api.client.XrasApiClient.from_environment',
        classmethod(lambda cls, *a, **k: client))
    return client


# access control

class TestAccessControl:

    def test_anonymous_is_redirected(self, client):
        assert client.get(FRAGMENT).status_code in (302, 401)

    def test_view_only_is_forbidden(self, view_only_client):
        """VIEW_XRAS is not enough — every control here writes to XRAS."""
        assert view_only_client.get(FRAGMENT).status_code == 403

    @pytest.mark.parametrize('path', [
        '/allocations/xras_merge_form/ghost-user-abcde',
        '/allocations/xras_withdraw_form/EXAM0001/7',
        '/allocations/xras_resubmit_form/EXAM0001/7',
        '/allocations/xras_request_detail/EXAM0001',
        '/allocations/xras_readiness_detail/EXAM0001',
        '/allocations/xras_user_detail/janebaldwin',
        '/allocations/xras_opportunity_detail/532220',
        '/allocations/xras_resource_form/EXAM0001/7/530201',
        '/allocations/xras_dates_form/EXAM0001/7',
        '/allocations/xras_attributes_form/EXAM0001',
        '/allocations/xras_action_fields_form/EXAM0001/7',
    ])
    def test_every_modal_is_gated(self, view_only_client, path):
        assert view_only_client.get(path).status_code == 403

    @pytest.mark.parametrize('path', [
        '/allocations/xras_merge/ghost-user-abcde',
        '/allocations/xras_withdraw/EXAM0001/7',
        '/allocations/xras_resubmit/EXAM0001/7',
        '/allocations/xras_role_add/EXAM0001',
        '/allocations/xras_role_remove/EXAM0001/2',
        '/allocations/xras_resource_edit/EXAM0001/7/530201',
        '/allocations/xras_resource_remove/EXAM0001/7/530201',
        '/allocations/xras_dates_edit/EXAM0001/7',
        '/allocations/xras_dates_remove/EXAM0001/7/9',
        '/allocations/xras_attributes_edit/EXAM0001',
        '/allocations/xras_action_fields_edit/EXAM0001/7',
        '/allocations/xras_recheck_request/EXAM0001',
        '/allocations/xras_recheck_visible',
    ])
    def test_every_write_is_gated(self, view_only_client, path):
        assert view_only_client.post(path).status_code == 403

    def test_the_page_shell_hides_the_card_from_a_view_only_operator(
            self, view_only_client):
        """Neither the container nor its facet form may render."""
        body = view_only_client.get('/allocations/xras').get_data(as_text=True)
        assert 'alloc-xras-remediations' not in body
        assert 'xras-remediation-filters' not in body

    def test_the_page_shell_carries_both_for_an_admin(self, auth_client):
        body = auth_client.get('/allocations/xras').get_data(as_text=True)
        assert 'alloc-xras-remediations' in body
        assert 'xras-remediation-filters' in body


class TestRecheckNow:
    """The request-scoped, never-writes re-check that patches the snapshot."""

    def test_it_patches_the_snapshot_with_fresh_verdicts(self, auth_client,
                                                          configured, monkeypatch):
        _publish(_payload('EXAM0001'))
        _reader(monkeypatch, payload=_detail_payload('EXAM0001'))
        resp = auth_client.post('/allocations/xras_recheck_request/EXAM0001')
        assert resp.status_code == 200
        assert 'refreshXrasTab' in resp.headers.get('HX-Trigger', '')
        entry = next(r for r in xras_cache.load_requests_index()['rows']
                     if r['request_number'] == 'EXAM0001')
        # A re-check writes nothing, so it must NOT stamp the "updated since the
        # sweep" marker — the flipped verdict is the only tell.
        assert entry['refreshed_at'] is None
        assert entry['actions'][0]['preflight'] is not None

    def test_it_degrades_200_when_xras_is_unreachable(self, auth_client,
                                                      configured, monkeypatch):
        client = _reader(monkeypatch)
        client.get_request_by_number.side_effect = XrasSourceUnavailable('down')
        resp = auth_client.post('/allocations/xras_recheck_request/EXAM0001')
        # 200, not 4xx: htmx will not swap a 4xx into the open modal.
        assert resp.status_code == 200
        assert 'could not be reached' in resp.get_data(as_text=True)

    def test_the_modal_renders_the_preflight_section_from_the_snapshot(
            self, auth_client, configured, monkeypatch):
        payload = _detail_payload('EXAM0001')
        verdict = {'status': 'failed', 'would_succeed': False,
                   'messages': ['PI ghost is not in database'], 'gaps': [],
                   'service': 'supplement', 'stage': 'Approved',
                   'action_status': 'Approved', 'request_status': 'Approved',
                   'push_state': 'pending', 'push_detail': None, 'resolved': None,
                   'checked_at': '2026-08-23T09:00:00'}
        xras_cache.store_requests_index({
            'generated_at': datetime.now(),
            'statuses': ['Approved'], 'extra_statuses': {},
            'rows': [request_index_entry(payload, pending_push=True,
                                         preflights={7: verdict})]})
        _reader(monkeypatch, payload=payload)
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'SAM pre-flight' in body
        assert 'PI ghost is not in database' in body


def _publish_one_verdict(status, *, messages=(), service='supplement'):
    """Publish EXAM0001 carrying a single-action verdict of the given status."""
    payload = _detail_payload('EXAM0001')
    verdict = {'status': status, 'would_succeed': status == 'rechecked',
               'messages': list(messages), 'gaps': [], 'service': service,
               'stage': 'Approved', 'action_status': 'Approved',
               'request_status': 'Approved', 'push_state': 'pending',
               'push_detail': None, 'resolved': None,
               'checked_at': '2026-08-23T09:00:00'}
    xras_cache.store_requests_index({
        'generated_at': datetime.now(), 'statuses': ['Approved'],
        'extra_statuses': {},
        'rows': [request_index_entry(payload, pending_push=True,
                                     preflights={7: verdict})]})


class TestReadinessModal:
    """The focused push-readiness modal a verdict badge opens — snapshot-only."""

    def test_it_renders_the_reasons_without_a_live_read(self, auth_client):
        # No _reader() is scripted: the modal reads the swept verdict straight
        # from the snapshot, so it needs no live XRAS call to explain a badge.
        _publish_one_verdict('failed', messages=['PI ghost is not in database'])
        body = auth_client.get(
            '/allocations/xras_readiness_detail/EXAM0001').get_data(as_text=True)
        assert 'SAM pre-flight' in body
        assert 'PI ghost is not in database' in body
        assert 'checked as' in body and 'supplement' in body

    def test_an_unswept_request_is_not_found(self, auth_client):
        xras_cache.store_requests_index({
            'generated_at': datetime.now(), 'statuses': [], 'extra_statuses': {},
            'rows': []})
        resp = auth_client.get('/allocations/xras_readiness_detail/NOPE0001')
        # 200, not 4xx: htmx will not swap a 4xx into an already-open modal.
        assert resp.status_code == 200
        assert 'SAM pre-flight' not in resp.get_data(as_text=True)


class TestReadinessBadgeWiring:
    """Card badges: a verdict opens the modal, incomplete is passive, a not-checked
    row posts a recheck."""

    def test_a_verdict_badge_links_to_the_readiness_modal(self, auth_client,
                                                          configured):
        _publish_one_verdict('failed', messages=['x'])
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'xras_readiness_detail/EXAM0001' in body
        assert 'would fail' in body

    def test_an_incomplete_badge_is_passive(self, auth_client, configured):
        _publish_one_verdict('incomplete')
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert '>incomplete</span>' in body
        # No re-check (it can't resolve it) and no modal link.
        assert 'xras_recheck_request/EXAM0001' not in body
        assert 'xras_readiness_detail/EXAM0001' not in body

    def test_a_not_checked_row_posts_the_inline_recheck(self, auth_client,
                                                        configured):
        # No preflight at all -> rollup None -> the checkable "not checked" state.
        _publish(_payload('EXAM0001'))
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'not checked' in body
        assert 'xras_recheck_request/EXAM0001' in body


class TestActionsColumnAndFacet:
    """The Type column (#2, between Request and Status) and its facet chip."""

    def _publish_types(self, *types):
        payloads = []
        for i, kind in enumerate(types):
            p = _payload('EXAM%04d' % i)
            p['actions'][0]['actionType'] = kind
            payloads.append(p)
        _publish(*payloads)

    def test_the_column_renders_the_type(self, auth_client, configured):
        self._publish_types('New')
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert '>Type<' in body                # the header
        assert 'New' in body                   # the row badge

    def test_the_facet_narrows_to_the_chosen_type(self, auth_client, configured):
        self._publish_types('New', 'Supplement')
        body = auth_client.get(
            FRAGMENT + '?show_all=1&action_type=New').get_data(as_text=True)
        assert 'EXAM0000' in body             # the New request survives
        assert 'EXAM0001' not in body         # the Supplement request is filtered

    def test_it_shows_only_the_latest_action_type(self, auth_client, configured):
        # A request with an old New and an in-flight Extension shows Extension —
        # what admin names it — not the whole history.
        p = _payload('EXAM0009')
        p['actions'] = [
            {'actionId': 1, 'actionType': 'Supplement', 'actionStatus': 'Approved'},
            {'actionId': 2, 'actionType': 'Extension', 'actionStatus': 'Submitted'}]
        _publish(p)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'Extension' in body
        assert 'Supplement' not in body       # neither the cell nor the facet


class TestSortableColumns:
    """Non-facet headers sort rows within each opportunity group, as form state."""

    def test_a_header_sorts_within_the_group_both_ways(self, auth_client, configured):
        # All three share the default opportunity -> one group.
        _publish(_payload('EXAM0003'), _payload('EXAM0001'), _payload('EXAM0002'))
        asc = auth_client.get(
            FRAGMENT + '?show_all=1&sort_by=request&sort_dir=asc').get_data(as_text=True)
        desc = auth_client.get(
            FRAGMENT + '?show_all=1&sort_by=request&sort_dir=desc').get_data(as_text=True)
        assert asc.index('EXAM0001') < asc.index('EXAM0003')
        assert desc.index('EXAM0003') < desc.index('EXAM0001')

    def test_headers_are_form_based_so_sort_survives_facet_clicks(self, auth_client,
                                                                  configured):
        _publish(_payload())
        body = auth_client.get(
            FRAGMENT + '?show_all=1&sort_by=request&sort_dir=asc').get_data(as_text=True)
        assert 'set-sort-submit' in body            # writes the form, not a URL
        assert 'data-sort-by="request"' in body

    def test_an_unknown_sort_column_is_ignored(self, auth_client, configured):
        _publish(_payload('EXAM0001'))
        assert auth_client.get(
            FRAGMENT + '?show_all=1&sort_by=bogus&sort_dir=asc').status_code == 200


class TestCheckAll:
    """Sent with show_all: an unchecked row is outside the pending-work queue.

    The header 'Check all' batch re-check over the not-checked rows in view."""

    def test_it_rechecks_the_not_checked_rows(self, auth_client, configured,
                                              monkeypatch):
        _publish(_payload('EXAM0001'))        # no preflight -> rollup None
        _reader(monkeypatch, payload=_detail_payload('EXAM0001'))
        resp = auth_client.post('/allocations/xras_recheck_visible', data={'show_all': '1'})
        assert resp.status_code == 200
        assert 'refreshXrasTab' in resp.headers.get('HX-Trigger', '')
        assert 'Checked 1 request' in resp.get_data(as_text=True)

    def test_nothing_to_check_when_all_are_verdicts(self, auth_client, configured):
        _publish_one_verdict('failed', messages=['x'])   # a real verdict, not None
        resp = auth_client.post('/allocations/xras_recheck_visible', data={'show_all': '1'})
        assert resp.status_code == 200
        assert 'Nothing to check' in resp.get_data(as_text=True)

    def test_it_degrades_200_when_xras_is_unreachable(self, auth_client, configured,
                                                      monkeypatch):
        _publish(_payload('EXAM0001'))
        client = _reader(monkeypatch)
        client.get_request_by_number.side_effect = XrasSourceUnavailable('down')
        resp = auth_client.post('/allocations/xras_recheck_visible', data={'show_all': '1'})
        assert resp.status_code == 200
        assert 'could not be reached' in resp.get_data(as_text=True)

    def test_it_honors_a_wide_window_passed_in_the_post_body(
            self, auth_client, configured, monkeypatch):
        # The row is out of the DEFAULT window but in a wide one. The card sends
        # the window in the POST BODY (hx-include), so the route must read
        # request.values, not request.args — else it silently reports "nothing to
        # check" over a wide-filter view full of not-checked rows.
        _publish(_payload('EXAM0001', submit_date='2015-01-01T00:00:00Z'))
        _reader(monkeypatch, payload=_detail_payload('EXAM0001'))
        # default window sees nothing
        assert 'Nothing to check' in auth_client.post(
            '/allocations/xras_recheck_visible').get_data(as_text=True)
        # a wide window in the body picks it up
        resp = auth_client.post(
            '/allocations/xras_recheck_visible',
            data={'show_all': '1', 'start_date': '2000-01-01', 'end_date': '2030-01-01'})
        assert 'Checked 1 request' in resp.get_data(as_text=True)


def _publish_with_verdict(payload, *, push_state, status='rechecked'):
    verdict = {'status': status, 'would_succeed': status == 'rechecked',
               'messages': [], 'gaps': [], 'service': 'supplement',
               'stage': 'Approved', 'action_status': 'Approved',
               'request_status': 'Approved', 'push_state': push_state,
               'push_detail': None, 'resolved': None,
               'checked_at': '2026-08-24T09:00:00'}
    xras_cache.store_requests_index({
        'generated_at': datetime.now(), 'statuses': ['Approved'],
        'extra_statuses': {},
        'rows': [request_index_entry(payload, pending_push=True,
                                     preflights={7: verdict})]})


class TestPendingWorkToggle:
    """Default is the pending-work queue (XRAS admin's Recent submissions);
    `show_all=1` restores everything, with the date filter."""

    def test_a_posted_request_is_hidden_by_default(self, auth_client, configured):
        _publish_with_verdict(_payload('EXAM0001'), push_state='seen_in_log')
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'No pending work' in body
        assert 'EXAM0001' not in body.split('id="xras-remediation-show-all"')[1]

    def test_show_everything_reveals_it(self, auth_client, configured):
        _publish_with_verdict(_payload('EXAM0001'), push_state='seen_in_log')
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'EXAM0001' in body
        assert 'checked' in body.split('id="xras-remediation-show-all"')[1].split('>')[0]

    def test_an_in_flight_request_is_pending_whatever_its_age(self, auth_client,
                                                              configured):
        # No date window in the queue: a 2015 submission still in review is work.
        _publish_with_verdict(_payload('EXAM0001', action_status='Submitted',
                                       submit_date='2015-01-01T00:00:00Z'),
                              push_state='unknown')
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert 'EXAM0001' in body and 'No pending work' not in body
        assert 'outside the date filter' not in body
        # ... and the date filter hides it once everything is shown.
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'outside the date filter' in body

    def test_the_header_counts_pending_and_the_rest(self, auth_client, configured):
        xras_cache.store_requests_index({
            'generated_at': datetime.now(), 'statuses': ['Approved'],
            'extra_statuses': {},
            'rows': [request_index_entry(_payload('EXAM0001', action_status='Submitted'),
                                         pending_push=True,
                                         preflights={7: {'status': 'incomplete',
                                                         'push_state': 'unknown'}}),
                     request_index_entry(_payload('EXAM0002'), pending_push=False,
                                         preflights={7: {'status': 'rechecked',
                                                         'push_state': 'seen_in_log'}})]})
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        assert '1 pending' in body
        assert '1 more with Show everything' in body

    def test_the_batch_recheck_honors_the_toggle(self, auth_client, configured,
                                                 monkeypatch):
        # An unchecked row is never pending work (no verdict = outside the sweep
        # window), so "Check all" only finds it under Show everything.
        _publish(_payload('EXAM0001'))
        _reader(monkeypatch, payload=_detail_payload('EXAM0001'))
        assert 'Nothing to check' in auth_client.post(
            '/allocations/xras_recheck_visible').get_data(as_text=True)
        resp = auth_client.post('/allocations/xras_recheck_visible',
                                data={'show_all': '1'})
        assert 'Checked 1 request' in resp.get_data(as_text=True)

    def test_the_switch_belongs_to_the_filter_form(self, auth_client, configured):
        _publish(_payload())
        body = auth_client.get(FRAGMENT).get_data(as_text=True)
        switch = body.split('id="xras-remediation-show-all"')[1].split('>')[0]
        assert 'name="show_all"' in switch and 'form="xras-remediation-filters"' in switch


class TestMnemonicUnblockStrip:
    """The § 2.2 ranking strip on the Remediations card — points at Admin to fix."""

    def _publish_mnemonic_failure(self, pi_username):
        from sam.xras.errors import mnemonic_internal_failed
        payload = _payload('EXAM0001')
        payload['roles'][0]['person']['username'] = pi_username
        verdict = {'status': 'failed', 'would_succeed': False,
                   'messages': [mnemonic_internal_failed()], 'gaps': [],
                   'service': 'add', 'stage': 'Approved', 'action_status': 'Approved',
                   'request_status': 'Approved', 'push_state': 'pending',
                   'push_detail': None, 'resolved': None,
                   'checked_at': '2026-08-23T09:00:00'}
        xras_cache.store_requests_index({
            'generated_at': datetime.now(), 'statuses': ['Approved'],
            'extra_statuses': {},
            'rows': [request_index_entry(payload, pending_push=True,
                                         preflights={7: verdict})]})

    def test_an_unknown_pi_renders_the_no_affiliation_line(self, auth_client,
                                                           configured):
        # A PI SAM does not know can't resolve an org -> the 'unresolved' bucket,
        # which the strip surfaces as the no-affiliation line. Deterministic
        # without committing users.
        self._publish_mnemonic_failure('ghost-user-strip-xyz')
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'no current affiliation' in body

    def test_view_only_is_still_forbidden(self, view_only_client, configured):
        self._publish_mnemonic_failure('ghost-user-strip-xyz')
        assert view_only_client.get(FRAGMENT).status_code == 403


class TestContractUnblockStrip:
    """The contract-blockers strip — the mnemonic strip's shape, linking to Admin -> Contracts."""

    def _publish_contract_failure(self, grants):
        payload = _payload('EXAM0001')
        verdict = {'status': 'failed', 'would_succeed': False,
                   'messages': ['x'], 'gaps': [],
                   'service': 'add', 'stage': 'Approved', 'action_status': 'Approved',
                   'request_status': 'Approved', 'push_state': 'pending',
                   'push_detail': None,
                   'resolved': {'unresolved_grants': list(grants)},
                   'checked_at': '2026-08-23T09:00:00'}
        xras_cache.store_requests_index({
            'generated_at': datetime.now(), 'statuses': ['Approved'],
            'extra_statuses': {},
            'rows': [request_index_entry(payload, pending_push=True,
                                         preflights={7: verdict})]})

    @staticmethod
    def _grant(number, **over):
        grant = {'number': number, 'core': number, 'reason': 'missing',
                 'candidates': [], 'agency': None, 'title': 'Seeded Title',
                 'pi_name': 'P. Eye', 'begin_date': '2026-01-01',
                 'end_date': '2027-12-31', 'is_pending': False}
        grant.update(over)
        return grant

    def test_a_reference_links_to_a_seeded_manual_form(self, auth_client, configured):
        self._publish_contract_failure([self._grant('ISS 25-643')])
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'id="xras-contract-strip"' in body
        assert 'ISS 25-643' in body
        assert '/admin/contracts?' in body and 'create=ISS' in body
        assert 'mode=manual' in body and 'title=Seeded' in body
        assert 'start_date=2026-01-01' in body and 'end_date=2027-12-31' in body

    def test_an_nsf_award_links_to_lookup_mode(self, auth_client, configured):
        self._publish_contract_failure([self._grant(
            '9980401', agency='National Science Foundation')])
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'create=9980401' in body and 'mode=lookup' in body

    def test_a_tie_renders_as_a_variant_with_no_create_link(self, auth_client,
                                                            configured, monkeypatch):
        # The route's db.session cannot see rows made in the test SAVEPOINT, so
        # the live re-check is stubbed; the tie itself is covered in
        # test_xras_contract_report.py.
        monkeypatch.setattr('sam.queries.xras_contract_report._recheck',
                            lambda session, number, core: 'ambiguous')
        self._publish_contract_failure([self._grant(
            'NSF-9980402', core='9980402', reason='ambiguous',
            candidates=['9980402', 'PLR-9980402'])])
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'NSF-9980402' in body and 'spelling variant' in body
        assert 'create=NSF-9980402' not in body

    def test_no_grants_means_no_strip(self, auth_client, configured):
        self._publish_contract_failure([])
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'xras-contract-strip' not in body

    def test_view_only_is_still_forbidden(self, view_only_client, configured):
        self._publish_contract_failure([self._grant('ISS 25-643')])
        assert view_only_client.get(FRAGMENT).status_code == 403


class TestItIsACardNotATab:

    def test_the_worklist_still_has_exactly_two_tabs(self, auth_client):
        body = auth_client.get('/allocations/xras').get_data(as_text=True)
        pane = body.split('id="xrasWorklistTabs"')[1].split('</ul>')[0]
        assert pane.count('data-bs-toggle="tab"') == 2

    def test_the_card_sits_outside_the_tab_content(self, auth_client):
        body = auth_client.get('/allocations/xras').get_data(as_text=True)
        assert body.index('alloc-xras-remediations') \
            > body.index('alloc-xras-accounts')


# the collapse affordance

class TestEveryExpandableRowShowsAChevron:
    """Every expandable table needs a visible chevron.

    A `cursor-pointer` row announces itself only to someone already hovering
    it, so a table without the chevron has no visible affordance at all. The
    chevron is `.collapse-icon` and nothing else: components.css rotates it
    off the `aria-expanded` Bootstrap writes onto the trigger, which works for
    a toggle on the `<tr>` and for one on a `<td>`, and survives an htmx swap
    with no registration anywhere.
    """

    PARTIALS = (pathlib.Path(__file__).resolve().parents[2] / 'src' / 'webapp'
                / 'templates' / 'dashboards' / 'allocations' / 'partials')

    @pytest.mark.parametrize('name', [
        'xras_activity_card.html',
        'xras_accounts_card.html',
        'xras_remediations_card.html',
        'xras_table.html',
    ])
    def test_each_expandable_table_offers_one(self, name):
        assert 'collapse-icon' in (self.PARTIALS / name).read_text()

    @pytest.mark.parametrize('name', [
        'xras_activity_card.html', 'xras_table.html'])
    def test_none_still_uses_the_js_driven_marker(self, name):
        """`.xras-collapse-icon` rotated via an admin-cards.js registration
        that no longer exists. The class alone renders a chevron that never
        moves — a silent, plausible-looking regression."""
        assert 'xras-collapse-icon' not in (self.PARTIALS / name).read_text()


# facet / control parity

class TestFacetParity:
    """A chip whose field has no control in the form filters nothing."""

    def test_every_facet_field_has_a_hidden_control(self, auth_client):
        body = auth_client.get('/allocations/xras').get_data(as_text=True)
        form = body.split('id="xras-remediation-filters"')[1].split('</form>')[0]
        for field in ('status', 'action_type', 'opportunity', 'push',
                      'readiness', 'request_number'):
            assert f'name="{field}"' in form, \
                f'{field} chips would be silently inert'

    def test_the_search_box_is_bound_to_that_form(self, auth_client, armed):
        """It renders inside the card and belongs to the form outside it.

        `form=` is what makes a chip click carry the search term: the chip
        submits `#xras-remediation-filters`, and htmx serializes a form through
        `form.elements` / `FormData`, both of which include form-associated
        controls wherever they sit in the document. Drop the attribute and
        every chip silently resets the search.
        """
        _publish(_payload())
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        box = body.split('id="xras-remediation-search"')[1].split('>')[0]
        assert 'name="search"' in box
        assert 'form="xras-remediation-filters"' in box


# the four empty states

class TestTheFourEmptyStates:
    """Collapsing any two of these would mislead an operator."""

    def test_unconfigured(self, auth_client, monkeypatch):
        monkeypatch.delenv('XRAS_OUTGOING_ENABLED', raising=False)
        monkeypatch.delenv('XRAS_API_KEY', raising=False)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'not configured' in body

    def test_no_sweep_at_all(self, auth_client, configured):
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'No sweep has published yet' in body

    def test_a_worklist_without_an_index(self, auth_client, configured):
        """The hour after deploy, and the independent-failure case the two
        cache keys exist for. A lie if reported as 'nothing to remediate'."""
        xras_cache.store_pending_worklist({'rows': [], 'counts': {}})
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'did not publish this list' in body

    def test_published_and_empty(self, auth_client, configured):
        xras_cache.store_requests_index({'generated_at': datetime.now(),
                                          'rows': []})
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'Nothing to remediate' in body

    def test_the_four_bodies_are_distinct(self, auth_client, configured,
                                          monkeypatch):
        seen = set()
        monkeypatch.delenv('XRAS_OUTGOING_ENABLED', raising=False)
        seen.add(auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True))
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        seen.add(auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True))
        xras_cache.store_pending_worklist({'rows': []})
        seen.add(auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True))
        xras_cache.store_requests_index({'generated_at': datetime.now(),
                                          'rows': []})
        seen.add(auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True))
        assert len(seen) == 4


# rendering

class TestRendering:

    # The roster/action offers moved from the card's per-request expansion into
    # the detail modal (the card row now just links to it), so these behaviors
    # are asserted against the modal body — fed by a live read (`_reader`).
    def test_the_modal_renders_the_offers(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'EXAM0001' in body
        assert 'Withdraw…' in body
        assert 'Resolve identity (merge)…' in body

    def test_the_lever_off_disables_rather_than_hides(self, auth_client,
                                                      configured, monkeypatch):
        """A control that vanishes teaches nobody that a switch exists."""
        monkeypatch.delenv('XRAS_WRITE_ENABLED', raising=False)
        _reader(monkeypatch, payload=_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'Withdraw…' in body
        assert 'XRAS_WRITE_ENABLED' in body
        assert 'disabled' in body

    def test_a_drafted_action_offers_resubmit_instead(self, auth_client, armed,
                                                       monkeypatch):
        _reader(monkeypatch, payload=_payload(action_status='Incomplete'))
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'Re-submit…' in body
        assert 'Withdraw…' not in body

    def test_an_unreconciled_placeholder_offers_no_merge(self, auth_client,
                                                         armed, monkeypatch):
        """That row means 'create the account' — the healthy path."""
        _reader(monkeypatch, payload=_payload(reconciled=False))
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'Resolve identity' not in body
        assert 'needs an account' in body

    def test_the_window_says_what_it_hid(self, auth_client, armed):
        """WARNING: On this card the hidden rows skew URGENT — never hide silently."""
        _publish(_payload(), _payload('EXAM0002', submit_date='2015-01-01T00:00:00Z'))
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'outside the date filter' in body

    def test_a_card_emptied_by_the_window_says_so_rather_than_nothing_to_do(
            self, auth_client, armed):
        """The default lookback hides stale rows, which are the point here."""
        _publish(_payload(submit_date='2015-01-01T00:00:00Z'))
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'the current filters hide all of them' in body
        assert 'Nothing to remediate' not in body

    def test_a_row_with_no_submit_date_survives_the_window(self, auth_client,
                                                            armed):
        """Missing information is not evidence of age."""
        payload = _payload()
        payload['submitDate'] = None
        _publish(payload)
        body = auth_client.get(
            FRAGMENT + '?show_all=1&start_date=2026-08-01').get_data(as_text=True)
        assert 'EXAM0001' in body

    def test_a_patched_row_is_marked_as_newer_than_the_sweep(self, auth_client,
                                                             armed):
        xras_cache.store_requests_index({
            'generated_at': datetime.now(), 'rows': [
                request_index_entry(_payload(), refreshed_at=datetime.now())]})
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'newer than the sweep' in body

    def test_status_chips_filter(self, auth_client, armed):
        _publish(_payload('EXAM0001'), _payload('EXAM0002', status='Submitted'))
        body = auth_client.get(
            FRAGMENT + '?show_all=1&status=Submitted').get_data(as_text=True)
        assert 'EXAM0002' in body and 'EXAM0001' not in body

    def test_the_type_column_shows_the_in_flight_action_not_a_count(
            self, auth_client, armed):
        """The card shows the request's current action type, not how many actions
        it has — multiplicity now lives in the request modal."""
        payload = _payload()
        payload['actions'].append({'actionId': 8, 'actionType': 'Extension',
                                   'actionStatus': 'Approved'})
        _publish(payload)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'Extension' in body          # the latest (highest-id) action type
        assert '2 actions' not in body      # the old count note is gone

    def test_the_project_admin_column_shows_the_allocation_manager(
            self, auth_client, armed):
        """SAM's "Project Admin" is the XRAS Allocation Manager (roleTypeId 14),
        shown when the request names one."""
        payload = _payload()
        payload['roles'].append(
            {'person': {'username': 'am-user', 'firstName': 'Al',
                        'lastName': 'Manager', 'isReconciled': True},
             'roles': [{'roleId': 3, 'role': 'Allocation Manager',
                        'roleTypeId': 14}]})
        _publish(payload)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'Project Admin' in body      # the column header
        assert 'Al Manager' in body         # the resolved admin

    def test_the_project_admin_column_is_blank_without_one(self, auth_client,
                                                           armed):
        """The base payload names no Allocation Manager — the cell is a dash,
        and the header still renders."""
        _publish(_payload())
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'Project Admin' in body


# the project link

class TestTheSamBadgeLinksWhenTheProjectExists:
    """`pending_push` is not a display choice — it is the sweep's set
    difference of `requestNumber` against `Project.projcode`. So the badge
    already asserts "a SAM project exists"; the link is that same claim, made
    clickable, and costs no query.

    Two states, not the action log's three. The flag is only as fresh as the
    last sweep and the post-write patch copies it rather than re-deriving
    (`_still_pending` defaults to True), so a stale row can withhold a link it
    could have offered but can never offer one to a project that is not there.
    """

    def test_a_pushed_request_links_to_its_project(self, auth_client, armed):
        _publish(_payload('EXAM0001'), pending=False)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert '/user/project-details-modal/EXAM0001' in body
        assert 'data-bs-target="#projectDetailsModal"' in body
        assert 'hx-target="#projectDetailsModalBody"' in body

    def test_a_pending_request_is_a_plain_badge(self, auth_client, armed):
        """WARNING: The direction that matters. `pending_push` is exactly "SAM has
        no project for this number" — the reason most rows are on this card at
        all. A link would 404 the modal on the majority of the table."""
        _publish(_payload('EXAM0001'), pending=True)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'no project' in body
        assert 'projectDetailsModal' not in body

    def test_the_request_column_opens_the_detail_modal(self, auth_client, armed):
        """The request number IS the XRAS request, so it opens the read-only
        detail modal — even when a SAM project by that name exists (the SAM
        cell keeps the project link). It is no longer a collapse trigger."""
        _publish(_payload('EXAM0001'), pending=False)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert '/allocations/xras_request_detail/EXAM0001' in body
        assert 'data-bs-target="#auditDetailsModal"' in body
        # The SAM cell still links the found project — Request vs Result.
        assert '/user/project-details-modal/EXAM0001' in body


class TestTheOpportunityGroupCollapses:
    """The per-request expansion was folded into the detail modal (the Request
    cell opens it). The opportunity group is the collapsible layer now, default
    open: its header row carries the toggle — it holds no link, so a row-level
    toggle is safe — and the member rows live in a sibling `collapse show`
    tbody keyed on the numeric opportunity id."""

    def test_the_group_header_is_the_toggle_default_open(self, auth_client,
                                                         armed):
        _publish(_payload('EXAM0001'))
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'data-bs-target="#xopp-5"' in body
        assert 'aria-expanded="true"' in body
        assert '<tbody id="xopp-5" class="collapse show">' in body

    def test_the_per_request_expansion_is_gone(self, auth_client, armed):
        """No `#xrem-<id>` collapse rows and no row include — the request row is
        a single line that opens the modal, so the roster/actions expansion no
        longer ships in the card (it lives in the modal)."""
        _publish(_payload('EXAM0001'))
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert '#xrem-' not in body

    def test_the_group_id_is_numeric_not_the_free_text_number(
            self, auth_client, armed):
        """Submitted numbers can be free text with spaces ('New University
        Large Request - Fall 2017 …' is live) — an unmatchable id. The
        collapsible layer is keyed on the numeric opportunity id, never the
        request number."""
        payload = _payload('New University Large Request - Fall 2017 Zhong',
                           status='Submitted')
        _publish(payload)
        body = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'data-bs-target="#xopp-5"' in body
        assert 'id="xopp-New' not in body


# the search box

class TestSearch:
    """One box over the fields an operator arrives holding.

    The request number IS the projcode — the sweep resolves a handoff by
    ``Project.projcode == requestNumber`` — so there is no second identifier
    to search for. The other two are people, and one of them is invisible
    until a row is expanded.
    """

    def test_it_matches_the_request_number(self, auth_client, armed):
        _publish(_payload('EXAM0001'), _payload('EXAM0002'))
        body = auth_client.get(
            FRAGMENT + '?show_all=1&search=exam0002').get_data(as_text=True)
        assert 'EXAM0002' in body and 'EXAM0001' not in body

    def test_it_matches_the_project_lead_by_display_name(self, auth_client,
                                                          armed):
        """The column shows a name, so a name is what gets typed."""
        _publish(_payload('EXAM0001'), _payload('EXAM0002'))
        body = auth_client.get(FRAGMENT + '?show_all=1&search=Eye').get_data(as_text=True)
        assert 'EXAM0001' in body and 'EXAM0002' in body

    def test_it_matches_a_roster_member_the_row_does_not_show(
            self, auth_client, armed):
        """WARNING: The reason a request is on this card is usually one person on
        its roster, and that person is not rendered until the row is expanded.
        A search that only saw the summary row would miss every one of them."""
        _publish(_payload('EXAM0001'),
                 _payload('EXAM0002', placeholder=False))
        body = auth_client.get(
            FRAGMENT + '?show_all=1&search=ghost-user').get_data(as_text=True)
        assert 'EXAM0001' in body and 'EXAM0002' not in body

    def test_a_search_that_matches_nothing_keeps_its_own_box(self, auth_client,
                                                              armed):
        """Otherwise the only way out of a typo is a page reload."""
        _publish(_payload())
        body = auth_client.get(
            FRAGMENT + '?show_all=1&search=nothingmatchesthis').get_data(as_text=True)
        assert 'the current filters hide all of them' in body
        assert 'id="xras-remediation-search"' in body
        assert 'value="nothingmatchesthis"' in body

    def test_the_chips_survive_emptying_the_card(self, auth_client, armed):
        """The copy says "clear the search and chips"; both must be there to
        clear. Rendering them only alongside rows makes them vanish at exactly
        the moment they are needed."""
        _publish(_payload('EXAM0001'), _payload('EXAM0002', status='Submitted'))
        body = auth_client.get(
            FRAGMENT + '?show_all=1&status=Submitted&search=EXAM0001').get_data(as_text=True)
        assert 'the current filters hide all of them' in body
        assert 'facet-chip' in body

    def test_the_date_badge_counts_only_what_the_date_hid(self, auth_client,
                                                          armed):
        """WARNING: Measured against `total` it would grow with every keystroke and
        blame the window for the search."""
        _publish(_payload('EXAM0001'),
                 _payload('EXAM0002', submit_date='2015-01-01T00:00:00Z'))
        plain = auth_client.get(FRAGMENT, query_string={'show_all': '1'}).get_data(as_text=True)
        assert '1 outside the date filter' in plain

        searched = auth_client.get(
            FRAGMENT + '?show_all=1&search=zzz').get_data(as_text=True)
        assert '1 outside the date filter' in searched


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
        client.get_request_by_number.side_effect = XrasSourceUnavailable('down')
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

    def test_it_carries_the_oob_title(self, auth_client, armed, monkeypatch):
        """The shared modal shell reads the title from an OOB swap."""
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'id="auditDetailsModalTitle"' in body
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
        assert 'Add XRAS username' in body
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

def _person(**over):
    person = {'username': 'janebaldwin', 'firstName': 'Jane',
              'middleName': None, 'lastName': 'Baldwin',
              'email': 'jane.baldwin@uci.edu', 'phone': '555',
              'organization': 'UC IRVINE', 'academicStatus': 'Faculty',
              'residenceCountry': 'United States', 'isReconciled': True,
              'orcid': None, 'hasOrcidToken': False}
    person.update(over)
    return person


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
        assert 'id="auditDetailsModalTitle"' in body
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
        assert 'id="auditDetailsModalTitle"' in body
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


# the request editor (Part B)

class TestTheEditors:
    """The amount/date editors: forms render, validation bites, the lever and
    the admin-context ceiling both refuse. Write happy-paths live at the
    service layer (test_xras_remediation_service.py), per the house rule."""

    def test_the_amount_form_renders(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_resource_form/EXAM0001/7/530201'
            '?stage=Requested').get_data(as_text=True)
        assert 'Edit amount — EXAM0001' in body
        assert 'Cheyenne' in body
        assert 'name="amount"' in body

    def test_the_dates_form_renders(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_dates_form/EXAM0001/7').get_data(as_text=True)
        assert 'allocation dates — EXAM0001' in body
        assert 'name="begin_date"' in body

    def test_the_detail_modal_offers_editors_on_requested_rows(
            self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'xras_resource_form/EXAM0001/7/530201' in body   # Edit…
        assert 'xras_resource_remove/EXAM0001/7/530201' in body  # Remove
        assert 'xras_dates_form/EXAM0001/7' in body             # Set dates…

    def test_the_award_editor_is_locked_without_the_elevated_key(
            self, auth_client, armed, monkeypatch):
        """Phase 0.5: our key cannot write the Approved stage, so the award
        editor renders disabled with a reason rather than firing a 401."""
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'Edit award' in body
        assert 'elevated XRAS key' in body
        # and it is NOT a live link to the Approved editor
        assert 'stage=Approved' not in body

    def test_an_invalid_amount_is_rejected(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.post(
            '/allocations/xras_resource_edit/EXAM0001/7/530201',
            data={'amount': '-5', 'stage': 'Requested'}).get_data(as_text=True)
        assert 'zero or more' in body

    def test_a_bad_date_range_is_rejected(self, auth_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.post(
            '/allocations/xras_dates_edit/EXAM0001/7',
            data={'begin_date': '2026-12-31',
                  'end_date': '2026-01-01'}).get_data(as_text=True)
        assert 'must not precede' in body

    def test_editing_the_award_is_refused_at_the_post_too(
            self, auth_client, armed, monkeypatch):
        """Belt and braces: the button is locked AND the POST refuses."""
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.post(
            '/allocations/xras_resource_edit/EXAM0001/7/530201',
            data={'amount': '20', 'stage': 'Approved'}).get_data(as_text=True)
        assert 'elevated XRAS key' in body

    def test_the_lever_off_refuses_an_amount_edit(self, auth_client, configured,
                                                  monkeypatch):
        """Writes off: the service refuses even a well-formed edit."""
        monkeypatch.delenv('XRAS_WRITE_ENABLED', raising=False)
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.post(
            '/allocations/xras_resource_edit/EXAM0001/7/530201',
            data={'amount': '20', 'stage': 'Requested'}).get_data(as_text=True)
        assert 'switched off' in body

    # the B2a text editors

    def test_the_attributes_form_renders_prefilled(self, auth_client, armed,
                                                   monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_attributes_form/EXAM0001').get_data(as_text=True)
        assert 'Edit attributes — EXAM0001' in body
        assert 'value="Turbulence at scale"' in body   # prefilled title
        assert 'name="abstract"' in body

    def test_the_action_fields_form_renders(self, auth_client, armed,
                                            monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_action_fields_form/EXAM0001/7').get_data(as_text=True)
        assert 'Edit action 7 — EXAM0001' in body
        assert 'name="user_comments"' in body

    def test_the_detail_modal_offers_the_text_editors(self, auth_client, armed,
                                                      monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'xras_attributes_form/EXAM0001' in body
        assert 'xras_action_fields_form/EXAM0001/7' in body

    def test_attributes_with_no_title_is_rejected(self, auth_client, armed,
                                                  monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.post(
            '/allocations/xras_attributes_edit/EXAM0001',
            data={'title': '   ', 'abstract': 'x'}).get_data(as_text=True)
        assert 'required' in body.lower()

    def test_the_lever_off_refuses_an_attributes_edit(self, auth_client,
                                                      configured, monkeypatch):
        monkeypatch.delenv('XRAS_WRITE_ENABLED', raising=False)
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.post(
            '/allocations/xras_attributes_edit/EXAM0001',
            data={'title': 'A new title'}).get_data(as_text=True)
        assert 'switched off' in body


# the destructive lifecycle (Part C, ADMIN_XRAS)

class TestPartCIsAdminOnly:
    """The destructive verbs ride ABOVE MANAGE_XRAS: a full-editor operator is
    refused them, and the buttons render only for an ADMIN_XRAS holder."""

    DESTRUCTIVE = [
        ('get', '/allocations/xras_add_action_form/EXAM0001'),
        ('post', '/allocations/xras_request_delete/EXAM0001'),
        ('post', '/allocations/xras_request_renew/EXAM0001'),
        ('post', '/allocations/xras_add_action/EXAM0001'),
    ]

    @pytest.mark.parametrize('method,path', DESTRUCTIVE)
    def test_a_manage_only_operator_is_forbidden(self, manage_not_admin_client,
                                                 method, path):
        resp = getattr(manage_not_admin_client, method)(path)
        assert resp.status_code == 403

    def test_the_danger_zone_renders_for_an_admin(self, auth_client, armed,
                                                  monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'xras_request_delete/EXAM0001' in body
        assert 'Destructive' in body

    def test_the_danger_zone_is_hidden_from_a_manage_only_operator(
            self, manage_not_admin_client, armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = manage_not_admin_client.get(
            '/allocations/xras_request_detail/EXAM0001').get_data(as_text=True)
        assert 'xras_request_delete' not in body
        assert 'Destructive' not in body

    def test_the_add_action_form_renders_the_type_picker(self, auth_client,
                                                        armed, monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.get(
            '/allocations/xras_add_action_form/EXAM0001').get_data(as_text=True)
        assert 'Add action — EXAM0001' in body
        assert 'name="action_type"' in body
        assert 'Supplement' in body

    def test_an_unknown_action_type_is_rejected(self, auth_client, armed,
                                               monkeypatch):
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.post(
            '/allocations/xras_add_action/EXAM0001',
            data={'action_type': 'Nonsense'}).get_data(as_text=True)
        assert 'Must be one of' in body

    def test_the_lever_off_refuses_a_delete(self, auth_client, configured,
                                           monkeypatch):
        """Belt and braces: the button is disabled AND the POST refuses."""
        monkeypatch.delenv('XRAS_WRITE_ENABLED', raising=False)
        _reader(monkeypatch, payload=_detail_payload())
        body = auth_client.post(
            '/allocations/xras_request_delete/EXAM0001').get_data(as_text=True)
        assert 'switched off' in body

    def test_delete_loops_the_whole_tree(self, auth_client, armed, monkeypatch):
        """A multi-line project deletes EVERY request line, not just lines[0]."""
        from types import SimpleNamespace

        client = _reader(monkeypatch, payload=_detail_payload())
        new_line = _detail_payload(); new_line['requestId'] = 111
        renewal = _detail_payload(); renewal['requestId'] = 222
        renewal['actions'] = [{'actionId': 88, 'actionType': 'Renewal',
                               'actionStatus': 'Approved', 'entryDate': '2027-01-02'}]
        client.get_request_family_by_number.side_effect = None
        client.get_request_family_by_number.return_value = [new_line, renewal]

        calls = []

        def fake_delete(session, *, request_number, request_id, pi_username, operator):
            calls.append(request_id)
            return SimpleNamespace(status='verified',
                                   result=SimpleNamespace(errors=None))

        monkeypatch.setattr(
            'webapp.dashboards.allocations.xras.remediation.remediation.delete_request',
            fake_delete)
        body = auth_client.post(
            '/allocations/xras_request_delete/EXAM0001').get_data(as_text=True)
        assert sorted(calls) == [111, 222]     # both lines deleted
        assert '2 request' in body             # the confirm/result names the scope


# POST validation

class TestPostValidation:

    def test_a_merge_with_no_target_is_rejected(self, auth_client, armed,
                                                monkeypatch):
        _reader(monkeypatch, person={'username': 'ghost-user-abcde',
                                     'lastName': 'Host'})
        body = auth_client.post(
            '/allocations/xras_merge/ghost-user-abcde',
            data={}).get_data(as_text=True)
        assert 'Select the real XRAS identity' in body

    def test_a_merge_into_an_unresolvable_target_is_refused(
            self, auth_client, armed, monkeypatch):
        """WARNING: The API CREATES an unknown target rather than failing."""
        client = _reader(monkeypatch)
        client.get_person.return_value = None
        body = auth_client.post('/allocations/xras_merge/ghost-user-abcde',
                                data={'target_username': 'typo'}
                                ).get_data(as_text=True)
        assert 'would CREATE it' in body

    def test_a_merge_into_itself_is_refused(self, auth_client, armed,
                                            monkeypatch):
        _reader(monkeypatch, person={'username': 'ghost-user-abcde'})
        body = auth_client.post('/allocations/xras_merge/ghost-user-abcde',
                                data={'target_username': 'ghost-user-abcde'}
                                ).get_data(as_text=True)
        assert 'different account' in body

    def test_a_withdrawal_without_a_reason_is_rejected(self, auth_client, armed,
                                                       monkeypatch):
        _reader(monkeypatch)
        body = auth_client.post('/allocations/xras_withdraw/EXAM0001/7',
                                data={'comment': '   '}).get_data(as_text=True)
        assert 'required' in body.lower()

    def test_adding_an_unknown_username_is_refused(self, auth_client, armed,
                                                   monkeypatch):
        """It would create a new XRAS identity, reconciled by default."""
        client = _reader(monkeypatch)
        client.get_person.return_value = None
        body = auth_client.post('/allocations/xras_role_add/EXAM0001',
                                data={'username': 'nobody',
                                      'role_type': 'User'}).get_data(as_text=True)
        assert 'can create a new XRAS identity' in body

    def test_adding_a_role_someone_already_holds_is_refused(
            self, auth_client, armed, monkeypatch):
        client = _reader(monkeypatch)
        client.get_person.return_value = {'username': 'ghost-user-abcde'}
        body = auth_client.post('/allocations/xras_role_add/EXAM0001',
                                data={'username': 'ghost-user-abcde',
                                      'role_type': 'User'}).get_data(as_text=True)
        assert 'already holds' in body

    def test_an_unknown_role_type_is_refused(self, auth_client, armed,
                                             monkeypatch):
        _reader(monkeypatch)
        body = auth_client.post('/allocations/xras_role_add/EXAM0001',
                                data={'username': 'someone',
                                      'role_type': 'Reviewer'}
                                ).get_data(as_text=True)
        assert 'Must be one of' in body

    def test_removing_a_role_that_is_gone_says_so(self, auth_client, armed,
                                                  monkeypatch):
        _reader(monkeypatch)
        body = auth_client.post('/allocations/xras_role_remove/EXAM0001/999'
                                ).get_data(as_text=True)
        assert 'no longer on the roster' in body

    def test_the_lever_off_refuses_the_write_at_the_service(
            self, auth_client, configured, monkeypatch):
        """Belt and braces: the button is disabled AND the POST refuses."""
        monkeypatch.delenv('XRAS_WRITE_ENABLED', raising=False)
        _reader(monkeypatch)
        body = auth_client.post('/allocations/xras_resubmit/EXAM0001/7'
                                ).get_data(as_text=True)
        assert 'switched off' in body


# review fixes (PR #460 follow-up)

class TestThePreflightDegradesHonestly:
    """The resubmit modal's preflight has three non-happy paths, and each
    must render — not 500, and not masquerade as a different failure."""

    def _admin(self, monkeypatch, **script):
        client = MagicMock(**script)
        monkeypatch.setattr(
            'sam.integration.xras_api.admin_client'
            '.XrasAdminClient.from_environment',
            classmethod(lambda cls, *a, **k: client))
        return client

    def test_a_role_less_roster_renders_the_unvalidated_note(
            self, auth_client, armed, monkeypatch):
        """No xa_user means no preflight ran — the guard skips seeding, and
        an unseeded key was jinja2.Undefined, for which `is none` is False
        and the next attribute access a 500 with an empty modal."""
        payload = _payload()
        payload['roles'] = []
        _reader(monkeypatch, payload=payload)
        response = auth_client.get('/allocations/xras_resubmit_form/EXAM0001/7')
        assert response.status_code == 200
        assert 'could not be asked to pre-check' in response.get_data(as_text=True)

    def test_a_preflight_401_renders_the_refusal_not_an_outage(
            self, auth_client, armed, monkeypatch):
        """WARNING: XrasWriteRejected subclasses XrasSourceUnavailable, so the
        outage catch used to swallow it — telling the operator to 'retry
        later' about a refusal a retry can never fix."""
        from sam.integration.xras_api.base import XrasWriteRejected

        _reader(monkeypatch)
        admin = self._admin(monkeypatch)
        admin.validate_action.side_effect = XrasWriteRejected(
            'pi-user holds no role on this request', status=401)

        body = auth_client.get(
            '/allocations/xras_resubmit_form/EXAM0001/7').get_data(as_text=True)
        assert 'does not validate' in body
        assert 'holds no role' in body
        assert 'not answering' not in body


class TestAPostDuringAnOutageDegradesInline:
    """exception_map wraps perform() only; the live reads in clean() needed
    their own wrap or an outage there was a 500 htmx never swaps."""

    def test_a_withdraw_post_renders_the_outage_inline(self, auth_client,
                                                       armed, monkeypatch):
        client = _reader(monkeypatch)
        client.get_request_by_number.side_effect = XrasSourceUnavailable('down')
        response = auth_client.post('/allocations/xras_withdraw/EXAM0001/7',
                                    data={'comment': 'stale award'})
        assert response.status_code == 200
        assert 'could not be reached' in response.get_data(as_text=True)

    def test_a_role_add_post_renders_the_outage_inline(self, auth_client,
                                                       armed, monkeypatch):
        client = _reader(monkeypatch)
        client.get_request_by_number.side_effect = XrasSourceUnavailable('down')
        response = auth_client.post('/allocations/xras_role_add/EXAM0001',
                                    data={'username': 'somebody',
                                          'role_type': 'User'})
        assert response.status_code == 200
        assert 'could not be reached' in response.get_data(as_text=True)


class TestARejectionCarriesXrasReasons:
    """A 400 carries XRAS's own errors[] — the most actionable thing about a
    rejection — and they must reach the operator, not only the audit row."""

    def test_the_reasons_render_in_the_error_panel(self, auth_client, armed,
                                                   monkeypatch):
        from sam.integration.xras_api.base import XrasWriteRejected
        from sam.manage import xras_remediation as remediation
        from sam.manage.xras_remediation import RemediationOutcome

        _reader(monkeypatch)
        rejection = XrasWriteRejected(
            'XRAS validation failed for action 7 as pi-user', status=400,
            errors=['Budget exceeds the opportunity limit',
                    'End date precedes the begin date'])
        monkeypatch.setattr(
            remediation, 'withdraw_action',
            lambda *a, **k: RemediationOutcome(5, status='rejected',
                                               error=str(rejection),
                                               result=rejection))

        body = auth_client.post('/allocations/xras_withdraw/EXAM0001/7',
                                data={'comment': 'why'}).get_data(as_text=True)
        assert 'XRAS refused this' in body
        assert 'Budget exceeds the opportunity limit' in body
        assert 'End date precedes the begin date' in body


class TestTheMergeOverrideIsReachable:
    """`required` on the candidate radios let native constraint validation
    block the free-text override — the documented path for 'none of these is
    right' — whenever at least one candidate rendered."""

    def _form(self, auth_client, monkeypatch):
        _reader(monkeypatch,
                person={'username': 'ghost-user-abcde', 'firstName': 'G',
                        'lastName': 'Host', 'email': 'g@example.invalid'},
                candidates=[{'username': 'ghost', 'firstName': 'G',
                             'lastName': 'Host',
                             'email': 'g@example.invalid',
                             'organization': 'Example U'}])
        return auth_client.get(
            '/allocations/xras_merge_form/ghost-user-abcde'
        ).get_data(as_text=True)

    def test_the_radios_are_not_marked_required(self, auth_client, armed,
                                                monkeypatch):
        import re
        body = self._form(auth_client, monkeypatch)
        radios = re.findall(r'<input[^>]*name="candidate"[^>]*>', body)
        assert radios, 'the candidate radios must still render'
        assert all('required' not in tag for tag in radios)

    def test_no_radio_is_preselected(self, auth_client, armed, monkeypatch):
        """The exactly-one guarantee moved to the schema; the no-default
        guarantee stays in the template."""
        import re
        body = self._form(auth_client, monkeypatch)
        radios = re.findall(r'<input[^>]*name="candidate"[^>]*>', body)
        assert all('checked' not in tag for tag in radios)


class TestSelfMergeIsRefusedCaseInsensitively:
    """XRAS matches usernames case-insensitively, so a case-variant of the
    source is the same identity and would be a self-merge."""

    def test_a_case_variant_target_is_refused(self, auth_client, armed,
                                              monkeypatch):
        _reader(monkeypatch, person={'username': 'ghost-user-abcde'})
        body = auth_client.post('/allocations/xras_merge/ghost-user-abcde',
                                data={'target_username': 'Ghost-USER-Abcde'}
                                ).get_data(as_text=True)
        assert 'different account' in body
