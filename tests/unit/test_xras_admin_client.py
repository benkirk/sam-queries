"""The outbound XRAS **write** client.

Mirrors ``tests/unit/test_xras_api_client.py`` — canned payloads with invented
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

import inspect
from pathlib import Path
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
from sam.integration.xras_api.config import XrasApiConfig, xras_write_configured

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── canned payloads ─────────────────────────────────────────────────────

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
    """Stands in for the report-context read client used by verification."""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def get_request_by_number(self, request_number):
        self.calls += 1
        if not self.payloads:
            raise AssertionError('reader called more times than scripted')
        payload = (self.payloads.pop(0) if len(self.payloads) > 1
                   else self.payloads[0])
        if isinstance(payload, Exception):
            raise payload
        return payload


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


# ── the lever ───────────────────────────────────────────────────────────

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


class TestHelmDoesNotArmWrites:
    """Two drift gates. Flipping either is a deliberate, reviewed act."""

    def _values(self):
        return yaml.safe_load((REPO_ROOT / 'helm' / 'values.yaml').read_text())

    def test_the_write_lever_ships_off(self):
        env = self._values()['webapp']['env']
        assert 'XRAS_WRITE_ENABLED' in env, \
            'the lever must be present and visibly off, not merely absent'
        assert str(env['XRAS_WRITE_ENABLED']) == '0'

    def test_the_tasks_env_never_arms_writes(self):
        """No scheduled task may write to XRAS.

        `cronjob-tasks.yaml` inherits nothing from `webapp.env` — it hand-lists
        what it needs — so the guarantee is that neither the tasks env block
        nor that manifest ever mentions the lever.
        """
        tasks_env = (self._values().get('tasks') or {}).get('env') or {}
        assert 'XRAS_WRITE_ENABLED' not in tasks_env

        manifest = (REPO_ROOT / 'helm' / 'templates'
                    / 'cronjob-tasks.yaml').read_text()
        body = '\n'.join(line for line in manifest.splitlines()
                         if not line.lstrip().startswith('#'))
        assert 'XRAS_WRITE_ENABLED' not in body, \
            'the task pod must never carry the XRAS write lever'


# ── structure ───────────────────────────────────────────────────────────

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


# ── transport ───────────────────────────────────────────────────────────

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


# ── verification: the reason this module exists ─────────────────────────

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
        """The isReconciled lesson, generalised: green and inert."""
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
