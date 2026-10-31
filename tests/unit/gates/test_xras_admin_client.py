"""The outbound XRAS **write** client.

Mirrors ``tests/unit/xras/test_xras_api_client.py`` — canned payloads with invented
identities, transport through a mocked ``session.request``, no network — but
asserts the properties that are specific to writing and that the live probe
(``docs/xras/outgoing/XRAS_WRITE_PROBES.md``) had to establish:

* the lever is a **second**, independent, default-off switch;
* this is a **sibling** of the read client, so the GET-only pin on that class
  stays true and meaningful;
* a write gets **one** attempt, whatever happens;
* **a 200 is not success** — every verb re-reads, and the three-valued verdict
  distinguishes "did not happen" from "could not tell".

The last one is the whole point of the module. XRAS returns 200 for a merge
that changed nothing and for a submit whose body is ``null``, so a client that
trusted status codes would report irreversible operations as fine.
"""

from __future__ import annotations

import base64
import inspect
import json
from pathlib import Path
from _paths import REPO_ROOT
from unittest.mock import MagicMock

import pytest
import requests
import yaml

from sam.integration.xras_api import cache as xras_cache
from sam.integration.xras_api.admin_client import (
    PI_ROLE_TYPE_ID,
    ROLE_TYPES,
    XA_ADMIN_CONTEXT,
    XrasAdminClient,
    XrasWriteResult,
    role_type,
)
from sam.integration.xras_api.base import (
    XrasApiNotConfigured,
    XrasSourceUnavailable,
    XrasWriteNotConfigured,
    XrasWriteRejected,
)
from sam.integration.xras_api.client import XA_CONTEXT, XrasApiClient
from sam.integration.xras_api.config import (
    XrasApiConfig,
    parse_submit_action_types,
    xras_submit_configured,
    xras_write_configured,
)
from sam.integration.xras_api.vocabulary import (
    ACTION_TYPES,
    SUBMIT_ACTION_TYPES,
    action_type_id,
    action_type_name,
)


# canned payloads

def _person(username, reconciled=True):
    return {
        'username': username, 'firstName': 'Ada', 'middleName': None,
        'lastName': 'Invented', 'email': f'{username}@example.invalid',
        'phone': None, 'organization': 'Example University',
        'academicStatus': 'Graduate Student',
        'residenceCountry': 'United States', 'isReconciled': reconciled,
        'orcid': None, 'hasOrcidToken': False,
    }


def _reports_request(*, actions=(), roster=(), number='EXAM0001'):
    """The ``reports/request_numbers/<n>`` shape — note the NESTED roles."""
    return {
        'requestId': 900001, 'requestNumber': number,
        'requestStatus': 'Approved', 'requestType': 'New',
        'roles': [{'person': _person(u),
                   'roles': [{'roleId': rid, 'role': rname,
                              'roleTypeId': rtid, 'beginDate': '2026-01-01',
                              'endDate': None, 'isAccountToBeCreated': False}]}
                  for (u, rid, rname, rtid) in roster],
        'actions': [{'actionId': aid, 'actionType': 'Supplement',
                     'actionStatus': state} for (aid, state) in actions],
    }


_DEFAULT_ROSTER = (('pi-user', 1, 'PI', 13), ('am-user', 2, 'Allocation Manager', 14))


def _response(status, result=None, message=None, text='', raw=False):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.json.return_value = (result if raw
                              else {'message': message, 'result': result})
    return resp


class _FakeReader:
    """Stands in for the report-context read client used by verification.

    Each scripted item is one family read: a dict (a one-line family), a list
    of lines, None (nothing resolves) or an exception.
    """

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def _next(self):
        self.calls += 1
        if not self.payloads:
            raise AssertionError('reader called more times than scripted')
        payload = (self.payloads.pop(0) if len(self.payloads) > 1
                   else self.payloads[0])
        if isinstance(payload, Exception):
            raise payload
        return payload

    def get_request_family_by_number(self, request_number):
        payload = self._next()
        if payload is None:
            return []
        return list(payload) if isinstance(payload, list) else [payload]

    def get_request_line(self, request_number, *, request_id=None):
        from sam.queries.xras_requests import line_by_id, primary_line
        family = self.get_request_family_by_number(request_number)
        return (line_by_id(family, request_id) if request_id is not None
                else primary_line(family))

    def get_request_by_id(self, request_id):
        payload = self._next()
        return payload[0] if isinstance(payload, list) else payload


def _client(monkeypatch, responses, *, reader=None, **config_kwargs):
    config = XrasApiConfig(enabled=True, write_enabled=True,
                           api_key='not-a-real-key', **config_kwargs)
    client = XrasAdminClient(config, reader=reader or _FakeReader({}))
    monkeypatch.setattr(client.session, 'request',
                        MagicMock(side_effect=responses))
    # The retrying GET (and its backoff sleep) is inherited from the shared
    # transport in ``client``; the admin client's own writes never sleep.
    monkeypatch.setattr('sam.integration.xras_api.client.time.sleep',
                        lambda _s: None)
    return client


@pytest.fixture(autouse=True)
def _reset_xras_cache(monkeypatch):
    monkeypatch.delenv('CACHE_REDIS_URL', raising=False)
    xras_cache._CACHE.reset_for_tests()
    yield
    xras_cache._CACHE.reset_for_tests(disabled=False)


# the lever

class TestTheWriteLever:
    """Fail-closed, and independent of the read lever."""

    def test_writes_are_off_by_default(self, monkeypatch):
        for key in ('XRAS_OUTGOING_ENABLED', 'XRAS_WRITE_ENABLED',
                    'XRAS_API_KEY'):
            monkeypatch.delenv(key, raising=False)
        assert xras_write_configured() is False

    def test_reading_does_not_imply_writing(self, monkeypatch):
        """The production posture today: reads on, writes off."""
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        monkeypatch.delenv('XRAS_WRITE_ENABLED', raising=False)
        config = XrasApiConfig.from_environment()
        assert config.configured is True
        assert config.write_configured is False

    def test_writing_requires_reading_too(self):
        """There is no write-without-read mode — verification is a read."""
        config = XrasApiConfig(enabled=False, write_enabled=True, api_key='k')
        assert config.write_configured is False

    def test_all_three_conditions_are_needed(self):
        assert XrasApiConfig(enabled=True, write_enabled=True,
                             api_key='').write_configured is False
        assert XrasApiConfig(enabled=True, write_enabled=True,
                             api_key='k').write_configured is True

    def test_from_environment_refuses_when_unarmed(self, monkeypatch):
        monkeypatch.delenv('XRAS_WRITE_ENABLED', raising=False)
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        with pytest.raises(XrasWriteNotConfigured):
            XrasAdminClient.from_environment()

    def test_not_configured_degrades_through_the_read_path(self):
        """A caller that only handles 'could not ask' is already correct."""
        assert issubclass(XrasWriteNotConfigured, XrasApiNotConfigured)
        assert issubclass(XrasWriteNotConfigured, XrasSourceUnavailable)

    def test_the_summary_reports_the_lever_but_never_the_key(self):
        summary = XrasApiConfig(enabled=True, write_enabled=True,
                                api_key='super-secret-value').summary()
        assert summary['write_enabled'] is True
        assert summary['write_configured'] is True
        assert 'super-secret-value' not in repr(summary)


class TestHelmWriteLever:
    """Two drift gates. Flipping either is a deliberate, reviewed act."""

    def _values(self):
        return yaml.safe_load((REPO_ROOT / 'helm' / 'values.yaml').read_text())

    def test_the_write_lever_is_armed_on_purpose(self):
        """Armed 2026-08-25 (PR #482). Disarming means editing this line too."""
        env = self._values()['webapp']['env']
        assert 'XRAS_WRITE_ENABLED' in env, \
            'the lever must be present and explicit, not merely absent'
        assert str(env['XRAS_WRITE_ENABLED']) == '1'

    def test_the_after_reply_rename_is_armed_on_purpose(self):
        """Armed 2026-10-14 after the backfill (XRAS_CLOSED_LOOP.md § 3.3). Disarming
        means editing this line too."""
        assert str(self._values()['webapp']['env']['XRAS_RENAME_ENABLED']) == '1'

    def test_auto_activation_is_armed_on_purpose(self):
        """XRAS_POST_AUTOMATION.md § 3 B, armed after the samuel-dev smoke (#828). Disarming
        means editing this line too."""
        assert str(self._values()['webapp']['env']['XRAS_AUTO_ACTIVATE']) == '1'

    def test_the_news_award_mail_is_armed_on_purpose(self):
        """XRAS_PHASE_C_AND_TICKETS.md § 5.4: the route mails the New after activating it.
        Disarming means editing this line too; xras_notices backstops it either way."""
        assert str(self._values()['webapp']['env']['XRAS_AUTO_NOTIFY']) == '1'

    def test_only_the_tasks_env_arms_task_writes(self):
        """`xras_rename` is the one production task that writes to XRAS.

        `cronjob-tasks.yaml` inherits nothing from `webapp.env`, so the lever reaches
        the task pod only through `tasks.env`, never the manifest itself. Disarming
        `xras_rename` is an edit to this test.
        """
        tasks_env = (self._values().get('tasks') or {}).get('env') or {}
        assert str(tasks_env.get('XRAS_WRITE_ENABLED')) == '1'
        assert 'xras_rename' not in tasks_env['SAM_TASKS_DISABLED'].split(',')

        manifest = (REPO_ROOT / 'helm' / 'templates'
                    / 'cronjob-tasks.yaml').read_text()
        body = '\n'.join(line for line in manifest.splitlines()
                         if not line.lstrip().startswith('#'))
        assert 'XRAS_WRITE_ENABLED' not in body, \
            'the task pod must never carry the XRAS write lever'


class TestDev:
    """samuel-dev runs both directions against XRAS's demo instance, never production."""

    def test_dev_targets_the_demo_instance_with_its_own_key_path(self):
        prod = yaml.safe_load((REPO_ROOT / 'helm' / 'values.yaml').read_text())['webapp']
        dev = yaml.safe_load((REPO_ROOT / 'helm' / 'values-dev.yaml').read_text())['webapp']
        env = dev['env']
        assert env['XRAS_API_BASE'] != prod['env']['XRAS_API_BASE']
        assert 'api.xras.org' not in env['XRAS_API_BASE']
        assert (env['XRAS_OUTGOING_ENABLED'], env['XRAS_WRITE_ENABLED'],
                env['XRAS_ACTIONS_CAPTURE_ONLY']) == ('1', '1', '0')
        assert dev['xrasApiCredentials']['secretPath'] != prod['xrasApiCredentials']['secretPath']

    def test_dev_arms_the_rename_task(self):
        """The one task write (`xras_rename`) runs on dev against the demo instance."""
        dev = yaml.safe_load((REPO_ROOT / 'helm' / 'values-dev.yaml').read_text())['tasks']['env']
        assert dev['XRAS_WRITE_ENABLED'] == '1'
        assert yaml.safe_load((REPO_ROOT / 'helm' / 'values-dev.yaml').read_text())[
            'webapp']['env']['XRAS_RENAME_ENABLED'] == '1'
        assert 'xras_rename' not in dev['SAM_TASKS_DISABLED'].split(',')

    def test_dev_auto_activates(self):
        dev = yaml.safe_load((REPO_ROOT / 'helm' / 'values-dev.yaml').read_text())
        assert dev['webapp']['env']['XRAS_AUTO_ACTIVATE'] == '1'


# structure

class TestItIsASiblingNotASubclass:
    """The GET-only pin on the read client must stay true and meaningful."""

    def test_the_admin_client_does_not_subclass_the_read_client(self):
        assert not issubclass(XrasAdminClient, XrasApiClient)

    def test_the_read_client_still_has_no_write_verb(self):
        for name in ('post', 'put', 'patch', 'delete', 'request'):
            assert not hasattr(XrasApiClient, name)
        source = inspect.getsource(XrasApiClient)
        for verb in ("'POST'", "'PUT'", "'PATCH'", "'DELETE'"):
            assert verb not in source

    def test_the_two_clients_live_in_different_contexts(self):
        """Neither context can serve the other's routes — measured, not styled."""
        assert XA_CONTEXT == 'report'
        assert XA_ADMIN_CONTEXT == 'submit'

    def test_the_session_carries_the_submit_context(self, monkeypatch):
        client = _client(monkeypatch, [])
        assert client.session.headers['XA-CONTEXT'] == 'submit'


class TestRoleTypes:
    """Three spellings per role, because the API uses all three."""

    def test_the_ncar_vocabulary_is_what_the_probe_measured(self):
        assert {(r.type_id, r.name, r.display) for r in ROLE_TYPES} == {
            (13, 'PI', 'Project Lead'),
            (14, 'Allocation Manager', 'Project Admin'),
            (19, 'User', 'User'),
        }

    def test_there_is_no_co_pi_in_the_ncar_process(self):
        assert not any('copi' in r.name.casefold() for r in ROLE_TYPES)

    def test_pi_is_the_impersonation_default(self):
        assert PI_ROLE_TYPE_ID == 13
        assert role_type(PI_ROLE_TYPE_ID).name == 'PI'

    @pytest.mark.parametrize('key', [19, '19', 'User', 'user'])
    def test_it_resolves_every_spelling(self, key):
        assert role_type(key).type_id == 19

    def test_an_unknown_role_type_is_loud(self):
        """Better a ValueError than an unrecognised value in a URL path."""
        with pytest.raises(ValueError):
            role_type('Reviewer')


# transport

class TestWritesGetExactlyOneAttempt:
    """A retried merge could delete a second person. Never retry a write."""

    def test_a_5xx_is_not_retried(self, monkeypatch):
        reader = _FakeReader(_reports_request(actions=((7, 'Incomplete'),)))
        client = _client(monkeypatch, [_response(503)], reader=reader)
        result = client.withdraw_action(1, 7, request_number='EXAM0001',
                                        xa_user='pi-user')
        assert client.session.request.call_count == 1
        assert result.write_error == 'HTTP 503'

    def test_a_transport_error_does_not_raise_and_still_verifies(self, monkeypatch):
        """Ambiguity is settled by the re-read, not by another attempt."""
        reader = _FakeReader(_reports_request(actions=((7, 'Incomplete'),)))
        client = _client(monkeypatch,
                         [requests.ConnectionError('socket died')], reader=reader)
        result = client.withdraw_action(1, 7, request_number='EXAM0001',
                                        xa_user='pi-user')
        assert client.session.request.call_count == 1
        assert 'socket died' in result.write_error
        # The write may well have landed — and here the re-read says it did.
        assert result.verified is True

    def test_a_4xx_raises_and_skips_verification(self, monkeypatch):
        """Nothing happened, so there is nothing to verify."""
        reader = _FakeReader(_reports_request(actions=((7, 'Approved'),)))
        client = _client(monkeypatch, [_response(401)], reader=reader)
        with pytest.raises(XrasWriteRejected) as caught:
            client.withdraw_action(1, 7, request_number='EXAM0001',
                                   xa_user='not-a-role-holder')
        assert caught.value.status == 401

    def test_a_400_carries_the_validation_errors(self, monkeypatch):
        client = _client(monkeypatch, [
            _response(400, {'validation': 'failed',
                            'errors': ['Title is a required field']},
                      message='validation failed'),
        ])
        with pytest.raises(XrasWriteRejected) as caught:
            client.submit_action(1, 7, request_number='EXAM0001',
                                 xa_user='pi-user', preflight=False)
        assert caught.value.errors == ['Title is a required field']


class TestImpersonation:
    """One rule covers every request-scoped write: XA-USER must hold a role."""

    def test_the_user_travels_per_call_not_on_the_session(self, monkeypatch):
        reader = _FakeReader(_reports_request(actions=((7, 'Incomplete'),)))
        client = _client(monkeypatch, [_response(200)], reader=reader)
        client.withdraw_action(1, 7, request_number='EXAM0001',
                               xa_user='pi-user')
        _, kwargs = client.session.request.call_args
        assert kwargs['headers'] == {'XA-USER': 'pi-user'}
        # The session default is untouched, so the next call cannot inherit it.
        assert client.session.headers['XA-USER'] != 'pi-user'

    def test_the_pi_is_resolvable_from_the_nested_roster(self, monkeypatch):
        reader = _FakeReader(_reports_request(roster=_DEFAULT_ROSTER))
        client = _client(monkeypatch, [], reader=reader)
        assert client.resolve_pi('EXAM0001') == 'pi-user'

    def test_the_roster_is_flattened_one_row_per_role(self, monkeypatch):
        """`role['roleType']` on the OUTER object is None — the nesting trap."""
        reader = _FakeReader(_reports_request(roster=_DEFAULT_ROSTER))
        client = _client(monkeypatch, [], reader=reader)
        rows = client.roster('EXAM0001')
        assert [(r['username'], r['role_id'], r['role_type_id']) for r in rows] \
            == [('pi-user', 1, 13), ('am-user', 2, 14)]


# verification: the reason this module exists

class TestAOneHundredIsNotSuccess:

    def test_withdraw_is_verified_by_the_action_state(self, monkeypatch):
        reader = _FakeReader(_reports_request(actions=((7, 'Approved'),)),
                             _reports_request(actions=((7, 'Incomplete'),)))
        client = _client(monkeypatch, [_response(200)], reader=reader)
        result = client.withdraw_action(1, 7, request_number='EXAM0001',
                                        xa_user='pi-user')
        assert (result.before, result.after) == ('Approved', 'Incomplete')
        assert result.verified is True
        assert result.status == 'verified'

    def test_a_200_that_changed_nothing_is_not_verified(self, monkeypatch):
        """The isReconciled lesson, generalized: green and inert."""
        reader = _FakeReader(_reports_request(actions=((7, 'Approved'),)))
        client = _client(monkeypatch, [_response(200)], reader=reader)
        result = client.withdraw_action(1, 7, request_number='EXAM0001',
                                        xa_user='pi-user')
        assert result.http_status == 200
        assert result.verified is False
        assert result.succeeded is False

    def test_an_unreadable_verify_is_unknown_not_failed(self, monkeypatch):
        """`None` and `False` mean different things to an operator."""
        reader = _FakeReader(XrasSourceUnavailable('XRAS is down'))
        client = _client(monkeypatch, [_response(200)], reader=reader)
        result = client.withdraw_action(1, 7, request_number='EXAM0001',
                                        xa_user='pi-user')
        assert result.verified is None
        assert result.status == 'unverified'
        assert 'verify read failed' in result.verify_detail

    def test_an_unreadable_before_state_does_not_block_the_write(self, monkeypatch):
        """The before-state is audit context, not a precondition.

        A flaky reports endpoint must not deny the operator the control that
        fixes a stuck action — the after-read is what settles the outcome.
        """
        reader = _FakeReader(XrasSourceUnavailable('reports flaked'),
                             _reports_request(actions=((7, 'Incomplete'),)))
        client = _client(monkeypatch, [_response(200)], reader=reader)
        result = client.withdraw_action(1, 7, request_number='EXAM0001',
                                        xa_user='pi-user')
        assert client.session.request.call_count == 1
        assert result.before is None
        assert result.verified is True
        assert 'before-state unreadable' in result.verify_detail

    def test_resubmit_expects_anything_but_incomplete(self, monkeypatch):
        """XRAS lands a re-submit in `Under Review`, not `Submitted`."""
        reader = _FakeReader(_reports_request(actions=((7, 'Incomplete'),)),
                             _reports_request(actions=((7, 'Under Review'),)))
        client = _client(monkeypatch, [_response(200)], reader=reader)
        result = client.submit_action(1, 7, request_number='EXAM0001',
                                      xa_user='pi-user', preflight=False)
        assert result.after == 'Under Review'
        assert result.verified is True

    def test_the_null_submit_body_does_not_defeat_verification(self, monkeypatch):
        """The 200 body is `null` where the docs promise the request object."""
        reader = _FakeReader(_reports_request(actions=((7, 'Incomplete'),)),
                             _reports_request(actions=((7, 'Under Review'),)))
        client = _client(monkeypatch, [_response(200, None)], reader=reader)
        assert client.submit_action(1, 7, request_number='EXAM0001',
                                    xa_user='pi-user',
                                    preflight=False).verified is True


class TestMerge:
    """Destructive, user-agnostic, and fail-closed on both endpoints."""

    def test_a_self_merge_is_refused_before_any_read(self, monkeypatch):
        """XRAS matches usernames case-insensitively, so a case-variant of
        the source is the same identity — and both fail-closed resolves would
        pass (it exists), sending XRAS a self-merge with unknown effect."""
        client = _client(monkeypatch, [])
        with pytest.raises(XrasWriteRejected):
            client.merge_person('placeholder-user-x', 'Placeholder-USER-X')
        assert client.session.request.call_count == 0, \
            'refused before any capture or write'

    def test_it_refuses_when_the_target_does_not_resolve(self, monkeypatch):
        """A typo would MINT an identity — the API creates on merge."""
        client = _client(monkeypatch, [
            _response(200, _person('placeholder-user-x')),  # source
            _response(404),                                 # target: absent
        ])
        with pytest.raises(XrasWriteRejected):
            client.merge_person('placeholder-user-x', 'typoed-target')
        assert not any(call[0][0] != 'GET'
                       for call in client.session.request.call_args_list), \
            'no merge may be attempted against an unresolvable target'

    def test_it_refuses_when_the_source_is_already_gone(self, monkeypatch):
        client = _client(monkeypatch, [
            _response(404),                          # source: already merged
            _response(200, _person('real-target')),  # target
        ])
        with pytest.raises(XrasWriteRejected):
            client.merge_person('placeholder-user-x', 'real-target')

    def test_a_verified_merge_captures_both_sheets_first(self, monkeypatch):
        """residenceCountry is not copied by the merge, so capture it before."""
        client = _client(monkeypatch, [
            _response(200, _person('placeholder-user-x')),  # before: source
            _response(200, _person('real-target')),         # before: target
            _response(200),                                 # the merge
            _response(404),                                 # after: source gone
            _response(200, _person('real-target')),         # after: target kept
        ])
        result = client.merge_person('placeholder-user-x', 'real-target')
        assert result.before['source']['residenceCountry'] == 'United States'
        assert result.verified is True
        assert result.xa_user is None, 'merge is user-agnostic'

    def test_a_merge_whose_source_survives_is_not_verified(self, monkeypatch):
        client = _client(monkeypatch, [
            _response(200, _person('placeholder-user-x')),
            _response(200, _person('real-target')),
            _response(200),
            _response(200, _person('placeholder-user-x')),  # still there!
            _response(200, _person('real-target')),
        ])
        assert client.merge_person('placeholder-user-x',
                                   'real-target').verified is False


class TestRoles:

    def test_add_sends_the_role_NAME_never_the_id(self, monkeypatch):
        """`/v1/requests/.../roles/19/...` is a 400; the string is required."""
        reader = _FakeReader(
            _reports_request(roster=_DEFAULT_ROSTER),
            _reports_request(roster=_DEFAULT_ROSTER + (('new-user', 3, 'User', 19),)))
        client = _client(monkeypatch, [_response(200, {'roleId': 3})],
                         reader=reader)
        result = client.add_role(1, 19, 'new-user', request_number='EXAM0001',
                                 xa_user='pi-user')
        called_path = client.session.request.call_args[0][1]
        assert called_path.endswith('/roles/User/new-user')
        assert '/roles/19/' not in called_path
        assert result.extra['role_id'] == 3
        assert result.verified is True

    def test_add_sends_no_person_parameters_ever(self, monkeypatch):
        """They would create the person — with isReconciled defaulting TRUE."""
        reader = _FakeReader(_reports_request(roster=_DEFAULT_ROSTER))
        client = _client(monkeypatch, [_response(200, {'roleId': 3})],
                         reader=reader)
        client.add_role(1, 'User', 'new-user', request_number='EXAM0001',
                        xa_user='pi-user')
        _, kwargs = client.session.request.call_args
        assert kwargs['params'] is None

    def test_remove_is_keyed_on_role_id_not_username(self, monkeypatch):
        """One person can hold two roles; remove exactly the one named."""
        reader = _FakeReader(
            _reports_request(roster=_DEFAULT_ROSTER + (('u', 3, 'User', 19),)),
            _reports_request(roster=_DEFAULT_ROSTER))
        client = _client(monkeypatch, [_response(200)], reader=reader)
        result = client.remove_role(1, 3, request_number='EXAM0001',
                                    xa_user='pi-user')
        assert client.session.request.call_args[0][1].endswith('/roles/3')
        assert result.verified is True

    def test_a_role_that_survives_removal_is_not_verified(self, monkeypatch):
        roster = _DEFAULT_ROSTER + (('u', 3, 'User', 19),)
        reader = _FakeReader(_reports_request(roster=roster))
        client = _client(monkeypatch, [_response(200)], reader=reader)
        assert client.remove_role(1, 3, request_number='EXAM0001',
                                  xa_user='pi-user').verified is False


class TestValidatePreflight:
    """The verdict is per-impersonated-user, so it gates per call."""

    def test_a_failing_preflight_blocks_the_submit(self, monkeypatch):
        client = _client(monkeypatch, [
            _response(200, {'validation': 'failed',
                            'errors': ['A PI CV is required for each PI']}),
        ])
        with pytest.raises(XrasWriteRejected) as caught:
            client.submit_action(1, 7, request_number='EXAM0001',
                                 xa_user='am-user')
        assert caught.value.errors == ['A PI CV is required for each PI']
        assert client.session.request.call_count == 1, 'no submit was attempted'

    def test_the_preflight_is_evaluated_as_the_impersonated_user(self, monkeypatch):
        client = _client(monkeypatch, [
            _response(200, {'validation': 'successful', 'errors': []}),
        ])
        client.validate_action(1, 7, xa_user='pi-user')
        _, kwargs = client.session.request.call_args
        assert kwargs['headers'] == {'XA-USER': 'pi-user'}

    def test_preflight_can_be_overridden_deliberately(self, monkeypatch):
        reader = _FakeReader(_reports_request(actions=((7, 'Incomplete'),)),
                             _reports_request(actions=((7, 'Under Review'),)))
        client = _client(monkeypatch, [_response(200)], reader=reader)
        client.submit_action(1, 7, request_number='EXAM0001',
                             xa_user='pi-user', preflight=False)
        assert client.session.request.call_count == 1  # no validate call


def _reports_with(*, action_id=7, resources=(), dates=(), number='EXAM0001'):
    """A reports payload carrying one action's ``resources[]``/``allocationDates[]``.

    Resource rows are self-describing, exactly as the live feed returns them
    (probed 2026-08-22): ``resourceId`` + ``type`` (stage) + ``amount``.
    """
    return {
        'requestId': 900001, 'requestNumber': number,
        'requestStatus': 'Approved', 'requestType': 'New',
        'roles': [{'person': _person('pi-user'),
                   'roles': [{'roleId': 1, 'role': 'PI', 'roleTypeId': 13}]}],
        'actions': [{'actionId': action_id, 'actionType': 'New',
                     'actionStatus': 'Approved',
                     'resources': [dict(r) for r in resources],
                     'allocationDates': [dict(d) for d in dates]}],
    }


class TestResourceAndDateVerbs:
    """The request editor's client verbs — query params, per-call context,
    single attempt, verify-by-reread against the targeted stage."""

    def test_update_amount_puts_query_params_and_verifies(self, monkeypatch):
        before = _reports_with(resources=[
            {'resourceId': 530201, 'type': 'Requested', 'amount': '555.0'}])
        after = _reports_with(resources=[
            {'resourceId': 530201, 'type': 'Requested', 'amount': '556'}])
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(before, after))

        result = client.update_resource_amount(
            900001, 7, 530201, '556', request_number='EXAM0001',
            xa_user='pi-user')

        assert result.verified is True
        method, url = client.session.request.call_args[0]
        _, kwargs = client.session.request.call_args
        assert method == 'PUT'
        assert url.endswith('/actions/7/resources/530201')
        assert kwargs['params'] == {'amount': '556', 'comments': ''}
        # submit context is the session default, so no per-call override header.
        assert kwargs['headers'] == {'XA-USER': 'pi-user'}

    def test_an_unchanged_amount_reads_back_unverified(self, monkeypatch):
        same = _reports_with(resources=[
            {'resourceId': 530201, 'type': 'Requested', 'amount': '555.0'}])
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(same, same))
        result = client.update_resource_amount(
            900001, 7, 530201, '999', request_number='EXAM0001',
            xa_user='pi-user')
        assert result.verified is False

    def test_admin_context_is_sent_per_call_and_verifies_the_award(
            self, monkeypatch):
        before = _reports_with(resources=[
            {'resourceId': 530201, 'type': 'Approved', 'amount': '10'}])
        after = _reports_with(resources=[
            {'resourceId': 530201, 'type': 'Approved', 'amount': '20'}])
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(before, after))

        result = client.update_resource_amount(
            900001, 7, 530201, '20', request_number='EXAM0001',
            xa_user='pi-user', context='admin')

        _, kwargs = client.session.request.call_args
        assert kwargs['headers'] == {'XA-USER': 'pi-user', 'XA-CONTEXT': 'admin'}
        assert result.verified is True  # verified against the Approved stage

    def test_remove_resource_deletes_and_verifies_the_line_is_gone(
            self, monkeypatch):
        before = _reports_with(resources=[
            {'resourceId': 530201, 'type': 'Requested', 'amount': '555'}])
        after = _reports_with(resources=[])
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(before, after))
        result = client.remove_resource(
            900001, 7, 530201, request_number='EXAM0001', xa_user='pi-user')
        assert result.verified is True
        assert client.session.request.call_args[0][0] == 'DELETE'

    def test_a_5xx_write_is_not_retried(self, monkeypatch):
        before = _reports_with(resources=[])
        client = _client(monkeypatch, [_response(503)],
                         reader=_FakeReader(before, before))
        client.update_resource_amount(900001, 7, 530201, '1',
                                      request_number='EXAM0001',
                                      xa_user='pi-user')
        assert client.session.request.call_count == 1

    def test_a_4xx_write_raises_and_carries_errors(self, monkeypatch):
        client = _client(monkeypatch, [
            _response(400, {'errors': ['Budget exceeds the opportunity limit']})],
            reader=_FakeReader(_reports_with(resources=[])))
        with pytest.raises(XrasWriteRejected) as caught:
            client.update_resource_amount(900001, 7, 530201, '1',
                                          request_number='EXAM0001',
                                          xa_user='pi-user')
        assert caught.value.errors == ['Budget exceeds the opportunity limit']

    def test_set_dates_posts_query_params_and_returns_the_new_id(
            self, monkeypatch):
        before = _reports_with(dates=[])
        after = _reports_with(dates=[
            {'allocationDateId': 9, 'beginDate': '2026-01-01',
             'endDate': '2026-12-31', 'type': 'Requested'}])
        client = _client(monkeypatch,
                         [_response(200, {'allocationDateId': 9})],
                         reader=_FakeReader(before, after))
        import datetime as dt
        result = client.set_action_dates(
            900001, 7, dt.date(2026, 1, 1), dt.date(2026, 12, 31),
            request_number='EXAM0001', xa_user='pi-user')
        method, url = client.session.request.call_args[0]
        _, kwargs = client.session.request.call_args
        assert method == 'POST'
        assert url.endswith('/actions/7/allocation_dates')
        assert kwargs['params'] == {'beginDate': '2026-01-01',
                                    'endDate': '2026-12-31'}
        assert result.verified is True
        assert result.extra['allocation_date_id'] == 9

    def test_update_dates_puts_to_the_id_and_verifies(self, monkeypatch):
        before = _reports_with(dates=[
            {'allocationDateId': 9, 'beginDate': '2026-01-01',
             'endDate': '2026-12-31', 'type': 'Requested'}])
        after = _reports_with(dates=[
            {'allocationDateId': 9, 'beginDate': '2026-02-01',
             'endDate': '2026-11-30', 'type': 'Requested'}])
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(before, after))
        import datetime as dt
        result = client.update_action_dates(
            900001, 7, 9, dt.date(2026, 2, 1), dt.date(2026, 11, 30),
            request_number='EXAM0001', xa_user='pi-user')
        assert client.session.request.call_args[0][1].endswith(
            '/allocation_dates/9')
        assert result.verified is True

    def test_remove_dates_deletes_the_id_and_verifies_gone(self, monkeypatch):
        before = _reports_with(dates=[
            {'allocationDateId': 9, 'beginDate': '2026-01-01',
             'endDate': '2026-12-31', 'type': 'Requested'}])
        after = _reports_with(dates=[])
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(before, after))
        result = client.remove_action_dates(
            900001, 7, 9, request_number='EXAM0001', xa_user='pi-user')
        assert client.session.request.call_args[0][0] == 'DELETE'
        assert result.verified is True


class TestRenameRequest:
    """The rename verifies by requestId: by number, the old one stops resolving."""

    def test_puts_the_new_number_and_verifies_by_id(self, monkeypatch):
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(_reports_request(number='UXYZ0001')))
        result = client.rename_request(900001, old_number='NCAR0001',
                                       new_number='UXYZ0001', xa_user='pi-user')
        assert result.verified is True and result.operation == 'rename_request'
        method, url = client.session.request.call_args[0]
        _, kwargs = client.session.request.call_args
        assert method == 'PUT' and url.endswith('/requests/900001/attributes')
        assert kwargs['params'] == {'requestNumber': 'UXYZ0001'}
        assert kwargs['headers'] == {'XA-USER': 'pi-user'}
        assert result.before == {'requestNumber': 'NCAR0001'}
        assert result.after == {'requestNumber': 'UXYZ0001'}

    def test_a_number_that_did_not_change_is_unverified(self, monkeypatch):
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(_reports_request(number='NCAR0001')))
        result = client.rename_request(900001, old_number='NCAR0001',
                                       new_number='UXYZ0001', xa_user='pi-user')
        assert result.verified is False and result.status == 'unverified'

    def test_a_failed_verify_read_is_unknown_not_failed(self, monkeypatch):
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(XrasSourceUnavailable('down')))
        result = client.rename_request(900001, old_number='NCAR0001',
                                       new_number='UXYZ0001', xa_user='pi-user')
        assert result.verified is None and 'verify read failed' in result.verify_detail

    def test_a_refusal_raises(self, monkeypatch):
        client = _client(monkeypatch, [_response(401, message='no role')])
        with pytest.raises(XrasWriteRejected):
            client.rename_request(900001, old_number='NCAR0001',
                                  new_number='UXYZ0001', xa_user='nobody')


class TestMetadataVerbs:
    """The B2a text editors — PUT + params + verify-by-reread against the
    reports read-back (only fields the feed echoes are editable)."""

    def _attr_payload(self, **top):
        p = _reports_with(resources=[])
        p.update(top)
        return p

    def test_update_attributes_puts_params_and_verifies(self, monkeypatch):
        before = self._attr_payload(title='Old', shortTitle=None, abstract='A')
        after = self._attr_payload(title='New', shortTitle='S', abstract='A')
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(before, after))
        result = client.update_request_attributes(
            900001, request_number='EXAM0001', xa_user='pi-user',
            title='New', shortTitle='S', abstract='A')
        assert result.verified is True
        method, url = client.session.request.call_args[0]
        _, kwargs = client.session.request.call_args
        assert method == 'PUT'
        assert url.endswith('/requests/900001/attributes')
        assert kwargs['params'] == {'title': 'New', 'shortTitle': 'S',
                                    'abstract': 'A'}

    def test_a_field_that_does_not_read_back_is_unverified(self, monkeypatch):
        before = self._attr_payload(title='Old')
        after = self._attr_payload(title='Old')      # unchanged
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(before, after))
        result = client.update_request_attributes(
            900001, request_number='EXAM0001', xa_user='pi-user', title='New')
        assert result.verified is False

    def test_clearing_a_field_sends_empty_and_verifies(self, monkeypatch):
        before = self._attr_payload(shortTitle='S')
        after = self._attr_payload(shortTitle=None)   # None reads as cleared
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(before, after))
        result = client.update_request_attributes(
            900001, request_number='EXAM0001', xa_user='pi-user', shortTitle='')
        _, kwargs = client.session.request.call_args
        assert kwargs['params'] == {'shortTitle': ''}
        assert result.verified is True

    def test_a_boolean_reads_back_as_the_text_sent(self, monkeypatch):
        before = self._attr_payload(isSupportedByGrants=None)
        after = self._attr_payload(isSupportedByGrants=True)
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(before, after))
        result = client.update_request_attributes(
            900001, request_number='EXAM0001', xa_user='pi-user', isSupportedByGrants='true')
        assert result.verified is True

    def test_keywords_read_back_without_the_spaces_xras_strips(self, monkeypatch):
        before = self._attr_payload(keywords=None)
        after = self._attr_payload(keywords='sam,xras,proof')
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(before, after))
        result = client.update_request_attributes(
            900001, request_number='EXAM0001', xa_user='pi-user', keywords='sam, xras, proof')
        assert result.verified is True

    def test_update_action_puts_to_the_action_and_verifies(self, monkeypatch):
        before = _reports_with(resources=[])
        before['actions'][0]['userComments'] = 'old'
        after = _reports_with(resources=[])
        after['actions'][0]['userComments'] = 'new'
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(before, after))
        result = client.update_action(
            900001, 7, request_number='EXAM0001', xa_user='pi-user',
            userComments='new')
        assert result.verified is True
        method, url = client.session.request.call_args[0]
        _, kwargs = client.session.request.call_args
        assert method == 'PUT'
        assert url.endswith('/requests/900001/actions/7')
        assert kwargs['params'] == {'userComments': 'new'}


class TestDestructiveVerbs:
    """Part C — irreversible, not live-probed, fail-visible. Single attempt,
    verify-by-reread."""

    def test_delete_verifies_the_request_is_gone(self, monkeypatch):
        before = _reports_with(resources=[])
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(before, None))  # after: gone
        result = client.delete_request(900001, request_number='EXAM0001',
                                       xa_user='pi-user')
        assert result.verified is True
        assert client.session.request.call_args[0][0] == 'DELETE'
        assert client.session.request.call_args[0][1].endswith('/requests/900001')

    def test_delete_that_still_resolves_is_unverified(self, monkeypatch):
        before = _reports_with(resources=[])
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(before, before))  # still there
        result = client.delete_request(900001, request_number='EXAM0001',
                                       xa_user='pi-user')
        assert result.verified is False

    def test_renew_verifies_a_new_request_id(self, monkeypatch):
        client = _client(monkeypatch,
                         [_response(200, {'requestId': 900002})],
                         reader=_FakeReader(_reports_with(resources=[])))
        result = client.renew_request(900001, request_number='EXAM0001',
                                      xa_user='pi-user')
        assert result.verified is True
        assert result.extra['renewal_request_id'] == 900002
        assert client.session.request.call_args[0][1].endswith(
            '/requests/900001/renew')

    def test_add_action_verifies_the_new_action_appears(self, monkeypatch):
        before = _reports_with(resources=[])          # action 7
        after = _reports_with(resources=[])
        after['actions'].append({'actionId': 8, 'actionType': 'Supplement',
                                 'actionStatus': 'Incomplete'})
        client = _client(monkeypatch, [_response(200, {'actionId': 8})],
                         reader=_FakeReader(before, after))
        result = client.add_action(900001, 'Supplement',
                                   request_number='EXAM0001', xa_user='pi-user')
        assert result.verified is True
        method, url = client.session.request.call_args[0]
        _, kwargs = client.session.request.call_args
        assert method == 'POST'
        assert url.endswith('/requests/900001/actions')
        assert kwargs['params'] == {'actionType': 'Supplement'}

    def test_a_4xx_on_delete_raises(self, monkeypatch):
        client = _client(monkeypatch, [_response(401)],
                         reader=_FakeReader(_reports_with(resources=[])))
        with pytest.raises(XrasWriteRejected):
            client.delete_request(900001, request_number='EXAM0001',
                                  xa_user='pi-user')


class TestTheResultRecord:
    """What the audit row is built from."""

    def test_the_status_vocabulary_matches_the_audit_table(self):
        assert XrasWriteResult('op', 'POST', '/p', verified=True).status == 'verified'
        assert XrasWriteResult('op', 'POST', '/p', verified=False).status == 'unverified'
        assert XrasWriteResult('op', 'POST', '/p', verified=None).status == 'unverified'
        assert XrasWriteResult('op', 'POST', '/p', verified=False,
                               write_error='boom').status == 'error'

    def test_it_is_frozen_so_an_audit_row_cannot_be_edited_after_the_fact(self):
        result = XrasWriteResult('op', 'POST', '/p')
        with pytest.raises(Exception):
            result.verified = True


class TestVerifiesReadTheTargetedLine:
    """A projcode is a FAMILY; writes target one line and verifies must read it."""

    @staticmethod
    def _family():
        # XRAS order: descending requestId. The primary line (highest actionId)
        # is the LAST one, so family[0] would be wrong on every read below.
        new = dict(_reports_request(actions=((30, 'Approved'),), number='FAM0001',
                                    roster=(('renewal-pi', 7, 'PI', 13),)),
                   requestId=900003, title='renewal')
        mid = dict(_reports_request(actions=((20, 'Approved'),), number='FAM0001'),
                   requestId=900002, title='middle')
        old = dict(_reports_request(actions=((40, 'Approved'),), number='FAM0001',
                                    roster=(('original-pi', 1, 'PI', 13),)),
                   requestId=900001, title='original')
        return [new, mid, old]

    def test_actions_span_every_line(self, monkeypatch):
        client = _client(monkeypatch, [], reader=_FakeReader(self._family()))
        assert {a['actionId'] for a in client._actions('FAM0001')} == {20, 30, 40}
        assert client.action_status('FAM0001', 30) == 'Approved'

    def test_the_roster_is_the_targeted_line_else_the_primary(self, monkeypatch):
        client = _client(monkeypatch, [], reader=_FakeReader(self._family()))
        assert client.resolve_pi('FAM0001', 900003) == 'renewal-pi'
        assert client.resolve_pi('FAM0001') == 'original-pi', 'primary = highest actionId'

    def test_deleting_one_line_verifies_against_the_family(self, monkeypatch):
        family = self._family()
        after = [ln for ln in family if ln['requestId'] != 900002]
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(family, after))
        result = client.delete_request(900002, request_number='FAM0001',
                                       xa_user='pi-user')
        assert result.verified is True
        assert 'line no longer resolves' in result.verify_detail

    def test_a_surviving_line_is_not_a_failed_delete_of_a_sibling(self, monkeypatch):
        family = self._family()
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(family, family))
        result = client.delete_request(900002, request_number='FAM0001',
                                       xa_user='pi-user')
        assert result.verified is False

    def test_attribute_verify_reads_the_targeted_line(self, monkeypatch):
        family = self._family()
        changed = [dict(ln, title='renamed') if ln['requestId'] == 900001 else ln
                   for ln in family]
        client = _client(monkeypatch, [_response(200)],
                         reader=_FakeReader(family, changed))
        result = client.update_request_attributes(
            900001, request_number='FAM0001', xa_user='pi-user', title='renamed')
        assert result.verified is True
        assert result.before == {'title': 'original'}

    def test_the_first_element_read_is_gone(self):
        import pathlib
        src = REPO_ROOT / 'src'
        hits = [p for p in src.rglob('*.py') if 'get_request_by_number' in p.read_text()]
        assert hits == [], hits


# the submit lever (XRAS_SUBMISSION_SPRINT1.md § 3)


class TestTheSubmitLever:
    """A fourth lever, off by default, that only means anything on top of the write lever."""

    def test_off_by_default(self, monkeypatch):
        for key in ('XRAS_SUBMIT_ENABLED', 'XRAS_SUBMIT_ACTION_TYPES'):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_WRITE_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        assert xras_submit_configured() is False

    def test_it_needs_the_write_lever(self):
        assert XrasApiConfig(enabled=True, write_enabled=False, submit_enabled=True,
                             api_key='k').submit_configured is False
        assert XrasApiConfig(enabled=True, write_enabled=True, submit_enabled=True,
                             api_key='k').submit_configured is True

    @pytest.mark.parametrize('value, expected', [
        (None, set()), ('', set()), ('  ', set()),            # forgotten means nothing files
        ('all', set(SUBMIT_ACTION_TYPES)), ('none', set()),
        ('Extension,Supplement', {'Extension', 'Supplement'}),
        ('extension, Extention ,new', {'Extension', 'New'}),   # the typo is dropped
    ])
    def test_the_type_list_parses_fail_safe(self, value, expected):
        assert parse_submit_action_types(value) == expected

    def test_a_type_off_the_list_is_not_armed(self, monkeypatch):
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_WRITE_ENABLED', '1')
        monkeypatch.setenv('XRAS_SUBMIT_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        monkeypatch.setenv('XRAS_SUBMIT_ACTION_TYPES', 'Extension,Supplement')
        config = XrasApiConfig.from_environment()
        assert config.submit_allows('Extension') and not config.submit_allows('New')

    def test_the_summary_reports_both(self):
        summary = XrasApiConfig(enabled=True, write_enabled=True, submit_enabled=True,
                                api_key='k',
                                submit_action_types=frozenset({'Supplement'})).summary()
        assert summary['submit_enabled'] is True and summary['submit_configured'] is True
        assert summary['submit_action_types'] == ['Supplement']


class TestHelmSubmitLever:
    """Prod ships dark and narrowed, dev armed, and neither CronJob ever carries it."""

    def _values(self, name):
        return yaml.safe_load((REPO_ROOT / 'helm' / name).read_text())

    def test_prod_ships_it_dark_and_narrowed(self):
        env = self._values('values.yaml')['webapp']['env']
        assert str(env['XRAS_SUBMIT_ENABLED']) == '0'
        assert parse_submit_action_types(env['XRAS_SUBMIT_ACTION_TYPES']) == {
            'Extension', 'Supplement'}

    def test_dev_arms_it(self):
        assert self._values('values-dev.yaml')['webapp']['env']['XRAS_SUBMIT_ENABLED'] == '1'

    def test_no_task_pod_carries_it(self):
        for name in ('values.yaml', 'values-dev.yaml'):
            tasks_env = (self._values(name).get('tasks') or {}).get('env') or {}
            assert 'XRAS_SUBMIT_ENABLED' not in tasks_env, name
        manifest = (REPO_ROOT / 'helm' / 'templates' / 'cronjob-tasks.yaml').read_text()
        assert 'XRAS_SUBMIT' not in manifest


class TestActionAndRequestTypes:
    """The ids ``/v1/types/all`` answered on 2026-10-27; the rule-book route takes them."""

    def test_the_ids_the_design_names(self):
        assert {k: ACTION_TYPES[k] for k in ('Supplement', 'Extension', 'Renewal', 'New',
                                             'Adjustment', 'Date Adjustment')} == {
            'Supplement': 500020, 'Extension': 500017, 'Renewal': 500021, 'New': 500019,
            'Adjustment': 500168, 'Date Adjustment': 500334}

    def test_every_submit_type_has_an_id(self):
        assert all(t in ACTION_TYPES for t in SUBMIT_ACTION_TYPES)

    @pytest.mark.parametrize('key', ['extension', 'Extension', 500017, '500017'])
    def test_it_resolves_any_spelling(self, key):
        assert action_type_name(key) == 'Extension' and action_type_id(key) == 500017

    def test_an_unknown_type_is_loud(self):
        with pytest.raises(ValueError):
            action_type_id('Extention')


# submission verbs

_FIXTURES = REPO_ROOT / 'tests' / 'fixtures' / 'xras' / 'outgoing'


def _draft(**changes):
    """The captured create response for Small 532220 (request 1439704, action 373018)."""
    body = json.loads((_FIXTURES / 'create_request_small_532220.json').read_text())['result']
    body.update(changes)
    return body


def _draft_action(draft, **changes):
    draft['actions'][0].update(changes)
    return draft


def _calls(client):
    return [(c[0][0], c[0][1], c[1]) for c in client.session.request.call_args_list]


class TestDraftReads:

    def test_get_draft_reads_by_id_as_the_user(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, _draft())])
        assert client.get_draft(1439704, xa_user='pi-user')['requestId'] == 1439704
        method, url, kwargs = _calls(client)[0]
        assert (method, url.split('/v1')[1]) == ('GET', '/requests/1439704')
        assert kwargs['headers']['XA-USER'] == 'pi-user'

    def test_a_line_without_a_role_is_a_401(self, monkeypatch):
        client = _client(monkeypatch, [_response(401)])
        with pytest.raises(XrasWriteRejected) as exc:
            client.get_draft(1439704, xa_user='someone-else')
        assert exc.value.status == 401

    def test_required_documents_status(self, monkeypatch):
        rule = {'documentTypeId': 22, 'documentType': 'Advisor_Letter',
                'hardPageLimit': None, 'requirementMet': False}
        client = _client(monkeypatch, [_response(200, [rule]), _response(200, [])])
        assert client.required_documents_status(1, 2, xa_user='pi-user') == [rule]
        assert client.required_documents_status(1, 3, xa_user='pi-user') == []
        assert _calls(client)[0][1].endswith('/requests/1/actions/2/required_documents_status')

    def test_available_action_requests_flattens_the_opportunity_rules(self, monkeypatch):
        offer = {'requestId': 1449367, 'requestNumber': 'UHSS0001', 'actionType': 'Renewal',
                 'actionTypeId': 500021, 'resourceIdsAvailableForAction': [530902]}
        opportunities = [{'opportunityId': 532220, 'rules': {'availableActionRequests': []}},
                         {'opportunityId': 532221, 'opportunityName': 'Exploratory',
                          'allocationTypeInfo': {'allocationTypeId': 500847,
                                                 'allocationType': 'Exploratory'},
                          'defaultAllocationAwardPeriod': 12,
                          'attributeSets': [{'attributeSetName': 'the NWSC End User Agreement',
                                             'attributes': [{'opportunityAttributeId': 539638,
                                                             'isRequired': True}]}],
                          'resources': [{'resourceId': 530902,
                                         'displayResourceName': 'NSF NCAR Derecho',
                                         'resourceUnits': 'Core-hours'}],
                          'rules': {'availableActionRequests': [offer]}}]
        client = _client(monkeypatch, [_response(200, opportunities)])
        assert client.available_action_requests('pi-user') == [{
            'opportunity_id': 532221, 'opportunity_name': 'Exploratory',
            'allocation_type_id': 500847, 'allocation_type': 'Exploratory', 'award_months': 12,
            'eua_attribute_id': 539638,
            'request_id': 1449367, 'request_number': 'UHSS0001', 'action_type': 'Renewal',
            'resource_ids': [530902], 'resources': [{'resource_id': 530902,
                                                     'name': 'NSF NCAR Derecho',
                                                     'units': 'Core-hours'}]}]
        method, url, kwargs = _calls(client)[0]
        assert url.endswith('/v1/opportunities') and kwargs['headers']['XA-USER'] == 'pi-user'

    def test_new_request_opportunities_carry_the_users_rules(self, monkeypatch):
        opportunities = [
            {'opportunityId': 532221, 'opportunityName': 'Exploratory',
             'allocationTypeInfo': {'allocationTypeId': 500847, 'allocationType': 'Exploratory'},
             'resources': [{'resourceId': 530902, 'displayResourceName': 'NSF NCAR Derecho',
                            'resourceUnits': 'Core-hours'}],
             'rules': {'canSubmitNewRequest': True,
                       'resourceIdsAvailableForNewRequest': [530902]}},
            {'opportunityId': 532220, 'opportunityName': 'Small',
             'rules': {'canSubmitNewRequest': False,
                       'noAvailableActionsReason': 'Closed to you'}}]
        client = _client(monkeypatch, [_response(200, opportunities)])
        exploratory, small = client.new_request_opportunities('pi-user')
        assert (small['can_submit'], small['reason'], small['resources']) == \
            (False, 'Closed to you', [])
        assert exploratory['can_submit'] and exploratory['resources'] == [
            {'resource_id': 530902, 'name': 'NSF NCAR Derecho', 'units': 'Core-hours'}]
        assert exploratory['allocation_type_id'] == 500847

    def test_required_fields_is_a_read_client_call_keyed_by_ids(self, monkeypatch):
        reader = XrasApiClient(XrasApiConfig(enabled=True, api_key='k'))
        monkeypatch.setattr(reader.session, 'request', MagicMock(
            return_value=_response(200, {'requiredFields': {'request': {}}})))
        assert reader.required_fields(500024, 'Extension') == {
            'requiredFields': {'request': {}}}
        url = reader.session.request.call_args[0][1]
        assert url.endswith('/v1/allocation_types/500024/action_types/500017/required_fields')


class TestCreateRequest:

    def test_it_posts_params_and_verifies_by_the_new_id(self, monkeypatch):
        created = _draft()
        client = _client(monkeypatch, [_response(200, created), _response(200, _draft())])
        result = client.create_request(532220, xa_user='pi-user')
        assert result.verified is True
        assert (result.extra['request_id'], result.extra['action_id']) == (1439704, 373018)
        assert result.extra['response'] == created
        method, url, kwargs = _calls(client)[0]
        assert (method, kwargs['params']) == (
            'POST', {'opportunityId': 532220, 'requestType': 'New'})
        assert _calls(client)[1][1].endswith('/requests/1439704')
        assert 'roles' not in (result.after or {})     # no person block in the audit row

    def test_a_5xx_with_no_id_is_unknown_not_failed(self, monkeypatch):
        client = _client(monkeypatch, [_response(502)])
        result = client.create_request(532220, xa_user='pi-user')
        assert result.verified is None and result.write_error == 'HTTP 502'
        assert client.session.request.call_count == 1          # one attempt, no reread

    def test_an_unreadable_draft_is_unknown(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, _draft()), _response(401)])
        result = client.create_request(532220, xa_user='pi-user')
        assert result.verified is None and 'verify read failed' in result.verify_detail


class TestAbortVerbs:

    def test_delete_action_verifies_the_soft_delete(self, monkeypatch):
        after = _draft_action(_draft(), isDeleted=True)
        client = _client(monkeypatch, [_response(200), _response(200, after)])
        result = client.delete_action(1439704, 373018, xa_user='pi-user')
        assert result.verified is True and result.operation == 'delete_action'
        assert _calls(client)[0][:2] == (
            'DELETE', client._url('/v1/requests/1439704/actions/373018'))

    def test_an_action_still_live_is_not_verified(self, monkeypatch):
        client = _client(monkeypatch, [_response(200), _response(200, _draft())])
        assert client.delete_action(1439704, 373018, xa_user='pi-user').verified is False

    def test_a_draft_delete_verifies_by_the_by_id_401(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, _draft()), _response(200),
                                       _response(401)])
        result = client.delete_request(1439704, request_number=None, xa_user='pi-user')
        assert result.verified is True and '401' in result.verify_detail
        assert result.before['requestId'] == 1439704
        assert [c[0] for c in _calls(client)] == ['GET', 'DELETE', 'GET']

    def test_a_draft_that_still_reads_live_is_not_deleted(self, monkeypatch):
        client = _client(monkeypatch, [_response(200, _draft()), _response(200),
                                       _response(200, _draft())])
        assert client.delete_request(1439704, request_number=None,
                                     xa_user='pi-user').verified is False


class TestPopulateVerbs:

    def test_upload_sends_base64_json_and_keeps_the_bytes_out(self, monkeypatch):
        pdf = b'%PDF-1.4 rendered'
        after = _draft_action(_draft(), documents=[{'documentId': 304037}])
        client = _client(monkeypatch, [_response(200, {'documentId': 304037}),
                                       _response(200, after)])
        result = client.upload_document(1439704, 373018, document_type='Advisor_Letter',
                                        filename='letter.pdf', content=pdf,
                                        xa_user='pi-user')
        assert result.verified is True
        method, url, kwargs = _calls(client)[0]
        assert method == 'POST' and url.endswith('/actions/373018/documents')
        assert kwargs['json'] == {'documentType': 'Advisor_Letter', 'filename': 'letter.pdf',
                                  'title': 'letter.pdf',
                                  'document': base64.b64encode(pdf).decode()}
        assert base64.b64encode(pdf).decode() not in repr(result)
        assert result.extra['bytes'] == len(pdf)

    def test_a_non_pdf_is_refused_by_xras(self, monkeypatch):
        client = _client(monkeypatch, [_response(400, message='document is not a PDF file')])
        with pytest.raises(XrasWriteRejected, match='not a PDF'):
            client.upload_document(1, 2, document_type='Main_Doc', filename='x.txt',
                                   content=b'text', xa_user='pi-user')

    def test_opportunity_attributes_go_as_the_bulk_json_body(self, monkeypatch):
        after = _draft_action(_draft(), opportunityAttributes=[
            {'opportunityAttributeId': 539637, 'attributeValue': None}])
        client = _client(monkeypatch, [_response(200), _response(200, after)])
        result = client.set_opportunity_attributes(
            1439704, 373018, [{'opportunityAttributeId': 539637}], xa_user='pi-user')
        assert result.verified is True and result.operation == 'set_opp_attributes'
        assert _calls(client)[0][2]['json'] == {'opportunityAttributes': [
            {'opportunityAttributeId': 539637, 'attributeValue': None}]}

    def test_a_missing_attribute_is_not_verified(self, monkeypatch):
        client = _client(monkeypatch, [_response(200), _response(200, _draft())])
        assert client.set_opportunity_attributes(
            1, 373018, [{'opportunityAttributeId': 539637}],
            xa_user='pi-user').verified is False

    _SIX = dict(piName='Ada Invented', title='An award', beginDate='2026-01-01',
                endDate='2028-12-31', programOfficerName='Officer',
                programOfficerEmail='officer@example.invalid')

    def test_add_grant_posts_params_and_reads_the_grant_back(self, monkeypatch):
        grant = {'grantId': 174568, 'grantNumber': '2345678',
                 **self._SIX, 'beginDate': '2026-01-01T00:00:00Z'}
        client = _client(monkeypatch, [_response(200, {'grantId': 174568}),
                                       _response(200, _draft(grants=[grant]))])
        result = client.add_grant(1439704, xa_user='pi-user', grantNumber='2345678',
                                  fundingAgencyId=1, **self._SIX)
        assert result.verified is True and result.extra['grant_id'] == 174568
        assert 'fundingAgencyId' in result.verify_detail      # named, not silently passed
        method, url, kwargs = _calls(client)[0]
        assert method == 'POST' and url.endswith('/requests/1439704/grants')
        assert kwargs['params']['programOfficerEmail'] == 'officer@example.invalid'

    def test_a_grant_field_that_reads_back_different_is_not_verified(self, monkeypatch):
        grant = {'grantId': 174568, 'grantNumber': '2345678', 'piName': None}
        client = _client(monkeypatch, [_response(200),
                                       _response(200, _draft(grants=[grant]))])
        result = client.update_grant(1439704, 174568, xa_user='pi-user',
                                     piName='Ada Invented')
        assert result.verified is False
        assert _calls(client)[0][:2] == ('PUT', client._url('/v1/requests/1439704/grants/174568'))

    def test_set_fos_sends_is_primary_and_verifies(self, monkeypatch):
        after = _draft(fos=[{'fosTypeId': 500003, 'isPrimary': True}])
        client = _client(monkeypatch, [_response(200), _response(200, after)])
        result = client.set_fos(1439704, 500003, xa_user='pi-user')
        assert result.verified is True
        assert _calls(client)[0][2]['params'] == {'isPrimary': 'true'}

    def test_every_new_operation_fits_the_audit_column(self):
        names = ('create_request', 'delete_action', 'upload_document',
                 'set_opp_attributes', 'add_grant', 'update_grant', 'set_fos')
        source = inspect.getsource(XrasAdminClient)
        assert all(f"'{n}'" in source and len(n) <= 24 for n in names)


class TestExtendedVerbs:

    def test_add_action_sends_comments_and_verifies_them(self, monkeypatch):
        before = _reports_with(resources=[])
        after = _reports_with(resources=[])
        after['actions'].append({'actionId': 8, 'actionType': 'Extension',
                                 'actionStatus': 'Incomplete', 'userComments': 'why '})
        client = _client(monkeypatch, [_response(200, {'actionId': 8})],
                         reader=_FakeReader(before, after))
        result = client.add_action(900001, 'Extension', request_number='EXAM0001',
                                   xa_user='pi-user', user_comments='why')
        assert result.verified is True
        assert _calls(client)[0][2]['params'] == {'actionType': 'Extension',
                                                  'userComments': 'why'}

    def test_comments_that_did_not_stick_are_not_verified(self, monkeypatch):
        after = _reports_with(resources=[])
        after['actions'].append({'actionId': 8, 'actionType': 'Extension',
                                 'actionStatus': 'Incomplete', 'userComments': None})
        client = _client(monkeypatch, [_response(200, {'actionId': 8})],
                         reader=_FakeReader(_reports_with(resources=[]), after))
        assert client.add_action(900001, 'Extension', request_number='EXAM0001',
                                 xa_user='pi-user', user_comments='why').verified is False

    def test_renew_carries_the_opportunity_and_the_new_lines_action(self, monkeypatch):
        renewed = _draft(requestId=1439800, requestType='Renewal')
        client = _client(monkeypatch, [_response(200, renewed)],
                         reader=_FakeReader(_reports_with(resources=[])))
        result = client.renew_request(900001, request_number='EXAM0001',
                                      xa_user='pi-user', opportunity_id=532221)
        assert result.verified is True and result.extra['action_id'] == 373018
        assert _calls(client)[0][2]['params'] == {'opportunityId': 532221}


class TestReadingADraft:
    """``reading_draft``: the reused editor verbs verify a line SAM minted by id, as the PI."""

    def test_a_resource_write_verifies_by_id_not_by_number(self, monkeypatch):
        reader = _FakeReader(AssertionError('the reports family must not be read'))
        before = _draft()
        after = _draft()
        after['actions'][0]['resources'] = [{'resourceId': 530902, 'type': 'Requested',
                                            'amount': 1000}]
        client = _client(monkeypatch, [_response(200, before), _response(200, {}),
                                       _response(200, after)], reader=reader)
        with client.reading_draft(1439704, 'pi-user'):
            result = client.update_resource_amount(
                1439704, 373018, 530902, 1000, request_number='UHSS0002', xa_user='pi-user')
        assert result.verified is True
        reads = [c for c in _calls(client) if c[0] == 'GET']
        assert [c[1].split('/v1')[1] for c in reads] == ['/requests/1439704'] * 2
        assert all(c[2]['headers']['XA-USER'] == 'pi-user' for c in reads)

    def test_the_scope_ends_with_the_block(self, monkeypatch):
        client = _client(monkeypatch, [])
        with client.reading_draft(1, 'pi-user'):
            assert client._draft_scope == (1, 'pi-user')
        assert client._draft_scope is None


class TestTheRuleBook:

    @staticmethod
    def _reader(fields=None, documents=None, fail=False):
        from sam.integration.xras_api.base import XrasSourceUnavailable
        reader = MagicMock()
        if fail:
            reader.required_fields.side_effect = XrasSourceUnavailable('down')
        reader.required_fields.return_value = fields
        reader.get_document_types.return_value = documents
        return reader

    def test_the_required_fields_and_documents_for_the_pair(self):
        from sam.integration.xras_api.rule_book import rule_book
        fields = {'requiredFields': {'request': {'abstract': {'required': True},
                                                 'grants': {'required': True},
                                                 'keywords': {'required': False}}}}
        documents = [{'documentType': 'Main_Doc', 'displayDocumentType': 'Main Document',
                      'allocationActionTypes': [{'allocationTypeId': 500023, 'actionTypes': [
                          {'actionType': 'Renewal', 'hardPageLimit': 10, 'isRequired': True}]}]},
                     {'documentType': 'Advisor_Letter', 'allocationActionTypes': [
                         {'allocationTypeId': 500847, 'actionTypes': [
                             {'actionType': 'Renewal', 'isRequired': True}]}]}]
        book = rule_book(self._reader(fields, documents), 500023, 'Renewal')
        assert book.required == ('abstract', 'grants') and book.wants_grants
        assert [(d.code, d.hard_page_limit) for d in book.documents] == [('Main_Doc', 10)]

    def test_an_outage_falls_back_by_type_name(self):
        from sam.integration.xras_api.rule_book import rule_book
        book = rule_book(self._reader(fail=True), 500847, 'Renewal',
                         allocation_type_name='Exploratory')
        assert book.source == 'fallback' and not book.wants_grants
        assert [d.code for d in book.documents] == ['Advisor_Letter']

    def test_an_unknown_type_with_no_answer_names_nothing(self):
        from sam.integration.xras_api.rule_book import rule_book
        book = rule_book(self._reader(fail=True), 1, 'Renewal', allocation_type_name='Nope')
        assert (book.source, book.required, book.documents) == ('none', (), ())
