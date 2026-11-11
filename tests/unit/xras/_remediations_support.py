"""The Remediations card tests' shared fixtures and payload builders.

Imported by the four `test_xras_remediations*.py` files; `_cache` is their autouse cache reset.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from sam.integration.xras_api import cache as xras_cache
from sam.queries.xras_requests import request_index_entry
from webapp.utils.rbac import Permission

FRAGMENT = '/allocations/xras_remediations'


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
             placeholder=True, reconciled=True, submit_date=None, email=None):
    roles = [{'person': {'username': 'pi-user', 'firstName': 'P',
                         'lastName': 'Eye', 'isReconciled': True},
              'roles': [{'roleId': 1, 'role': 'PI', 'roleTypeId': 13}]}]
    if placeholder:
        ghost = {'username': 'ghost-user-abcde', 'firstName': 'G',
                 'lastName': 'Host', 'isReconciled': reconciled}
        if email:
            ghost['email'] = email
        roles.append({'person': ghost,
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
         'allocationDateType': 'Requested'}]
    payload['actions'][0]['documents'] = [
        {'documentId': 1, 'documentType': 'Supp_Info', 'title': 'Award Letter',
         'filename': 'award.pdf', 'size': 44042}]
    return payload


def _reader(monkeypatch, payload=_payload(), person=None, candidates=(),
            person_roles=None, opportunity=None, fos_types=None, people=None):
    """Swap in a scripted read client for every live lookup.

    *people* maps username -> sheet for the direct `get_person` probes the
    merge finder and the handler's target check make; unknown names fall
    back to *person*.
    """
    client = MagicMock()
    client.get_request_line.return_value = payload
    # The detail modal fetches the whole family; derive it from the single-line
    # mock so a test's return_value/side_effect (not-found, outage) still applies.
    client.get_request_family_by_number.side_effect = (
        lambda n: [row] if (row := client.get_request_line(n)) else [])
    client.get_person.return_value = person
    if people is not None:
        client.get_person.side_effect = lambda u: people.get(u, person)
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


def _posted(push_state):
    """A real ``seen_in_log`` stamp always carries the row's status; the shorthand is a landed one."""
    return {'status': 'processed', 'log_id': 7} if push_state == 'seen_in_log' else None


def _publish_with_verdict(payload, *, push_state, status='rechecked'):
    verdict = {'status': status, 'would_succeed': status == 'rechecked',
               'messages': [], 'gaps': [], 'service': 'supplement',
               'stage': 'Approved', 'action_status': 'Approved',
               'request_status': 'Approved', 'push_state': push_state,
               'push_detail': _posted(push_state), 'resolved': None,
               'checked_at': '2026-08-24T09:00:00'}
    xras_cache.store_requests_index({
        'generated_at': datetime.now(), 'statuses': ['Approved'],
        'extra_statuses': {},
        'rows': [request_index_entry(payload, pending_push=True,
                                     preflights={7: verdict})]})


def _person(**over):
    person = {'username': 'janebaldwin', 'firstName': 'Jane',
              'middleName': None, 'lastName': 'Baldwin',
              'email': 'jane.baldwin@uci.edu', 'phone': '555',
              'organization': 'UC IRVINE', 'academicStatus': 'Faculty',
              'residenceCountry': 'United States', 'isReconciled': True,
              'orcid': None, 'hasOrcidToken': False}
    person.update(over)
    return person
