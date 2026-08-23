"""The ``/api/xras/*`` catch-all — turning an unmapped path into a record.

``POST /v1/roles/…`` went unported for a whole build and nothing in the running system
could have said so: an unmapped path produced Werkzeug's default HTML 404 and left no log
line, no row, nothing on the dashboard. Only an audit of the deployed ``ROOT.war`` found
it. These tests pin the change that makes the *next* one visible.
"""

import re

import pytest

from xras_audit import action_log  # noqa: F401  — shared with tests/stress/
from xras_helpers import (  # noqa: F401  — pytest resolves fixtures by name
    reset_db_key_cache,
    xras_auth as _auth,
    xras_client,
    xras_keys,
)


class TestItRecords:

    def test_an_unmapped_path_is_a_404_envelope(self, xras_client, action_log):
        resp = xras_client.get('/api/xras/v1/usage/by_month/2026', headers=_auth())
        assert resp.status_code == 404
        assert resp.data == (
            b'{"message":"no route for GET /api/xras/v1/usage/by_month/2026",'
            b'"result":null}')

    def test_it_mints_a_row_with_the_unmapped_status(self, xras_client, action_log):
        """``unmapped`` is its own status on purpose.

        Not ``manual``, which is the four-cause parking cohort triage week filters on;
        not ``failed``, which would inflate the failure rate the dashboard reports for
        something that never claimed to be supported.
        """
        xras_client.delete('/api/xras/v1/roles/ABC1234/pi/someone', headers=_auth())

        row = action_log.one()
        assert row['status'] == 'unmapped'
        assert row['http_status'] == 404
        assert row['outcome_reason'] == 'DELETE /api/xras/v1/roles/ABC1234/pi/someone'
        assert row['action_type'] is None
        assert row['service'] is None
        assert row['processed_time'] is not None

    def test_the_row_keeps_the_query_string_and_the_body(self, xras_client, action_log):
        """``raw_payload`` is the only place either survives — and it is NOT NULL."""
        xras_client.post('/api/xras/v1/users/sync?force=1',
                         data='{"who":"someone"}',
                         content_type='application/json', headers=_auth())

        payload = action_log.one()['raw_payload']
        assert 'POST /api/xras/v1/users/sync?force=1' in payload
        assert '{"who":"someone"}' in payload

    def test_the_bare_prefix_lands_here_too(self, xras_client, action_log):
        """``<path:>`` will not match an empty segment, hence the second rule."""
        resp = xras_client.get('/api/xras/v1/', headers=_auth())
        assert resp.status_code == 404
        assert action_log.one()['status'] == 'unmapped'

    @pytest.mark.parametrize('method', ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
    def test_every_verb_is_recorded(self, xras_client, action_log, method):
        resp = xras_client.open('/api/xras/v1/nothing/here', method=method,
                                headers=_auth())
        assert resp.status_code == 404
        assert action_log.one()['outcome_reason'].startswith(method)

    def test_a_wrong_verb_on_a_mapped_path_is_recorded_not_405(
            self, xras_client, action_log):
        """Legacy's ``/v1/roles`` is ``@PostMapping``, so a DELETE 404s there silently.

        The ACCESS spec documents ``DELETE /v1/roles/…`` for revocations that legacy
        never implemented. If XRAS ever starts sending one, a row records it, rather
        than someone asking why a co-PI is still on a project.
        """
        resp = xras_client.delete('/api/xras/v1/roles/ABC1234/pi/someone',
                                  headers=_auth())
        assert resp.status_code == 404
        assert action_log.one()['status'] == 'unmapped'


class TestItIsBehindAuth:

    def test_unauthenticated_gets_401_and_mints_nothing(self, xras_client, action_log):
        """Without this, every internet scanner probing ``/api/xras/v1/wp-admin``
        becomes rows — noise in the table, and unbounded write amplification from an
        unauthenticated endpoint. The question this feature answers is "did *XRAS* call
        something new", and only an authenticated caller can.
        """
        resp = xras_client.get('/api/xras/v1/wp-admin')
        assert resp.status_code == 401
        assert len(resp.data) == 41
        assert action_log.rows() == []

    def test_a_credential_without_role_xras_mints_nothing(self, xras_client, action_log):
        resp = xras_client.get('/api/xras/v1/wp-admin', headers=_auth('nobody'))
        assert resp.status_code == 403
        assert action_log.rows() == []


class TestItShadowsNothing:

    def test_no_mapped_rule_is_shadowed(self, app):
        """Every other rule on the blueprint still resolves to itself.

        Werkzeug orders rules by specificity rather than registration, and the ``path``
        converter weights lowest — so this should hold by construction. It is asserted
        anyway because the failure mode is the entire XRAS surface quietly 404ing, and
        because "should hold by construction" is exactly the kind of claim that stops
        being true after a Werkzeug upgrade.
        """
        catch_all = 'api_xras.unmapped_path'
        adapter = app.url_map.bind('localhost')

        rules = [r for r in app.url_map.iter_rules()
                 if r.endpoint.startswith('api_xras.') and r.endpoint != catch_all]
        assert rules, 'no XRAS rules found — the blueprint did not register'

        for rule in rules:
            method = next(iter(rule.methods - {'HEAD', 'OPTIONS'}))
            # Substitute a plausible value per converter: int segments need digits.
            path = re.sub(r'<int:[^>]+>', '1', rule.rule)
            path = re.sub(r'<[^>]+>', 'sample', path)

            matched, _ = adapter.match(path, method=method)
            assert matched == rule.endpoint, (
                f'{method} {path} resolved to {matched}, not {rule.endpoint}')
