"""
CliRunner tests for `sam-admin xras`.

Unlike `sam-admin cache`, this command reads the database directly, so the group
fixture wires the CLI's session to the test session rather than mocking it away.
Rows come from the Layer-2 factory path via `_record` where a committed row is
needed, and from an empty table otherwise — the envelope shape must be correct in
both cases.

The `--recheck` path is deliberately NOT exercised end-to-end here: it builds a
full Flask app to get an application context (see `XrasCommand._replay` for why
that is the right call rather than a second write path). Its behaviour is covered
at `tests/api/test_xras_access.py::TestReplay`; what is tested here is the
option plumbing around it.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli.cmds.admin import cli
from cli.core.utils import EXIT_ERROR, EXIT_NOT_FOUND, EXIT_SUCCESS


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def cli_session(session):
    """Point the CLI group's session at the test session."""
    with patch('sam.session.create_sam_engine') as mock_engine, \
         patch('cli.core.context.Session') as mock_session_cls:
        mock_engine.return_value = (MagicMock(), None)
        mock_session_cls.return_value = session
        yield session


class TestListMode:
    def test_default_lists_actions(self, runner, cli_session):
        result = runner.invoke(cli, ['xras'])
        assert result.exit_code == 0

    def test_json_envelope_shape(self, runner, cli_session):
        result = runner.invoke(cli, ['--format', 'json', 'xras'])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload['kind'] == 'xras_action_list'
        assert set(payload) >= {'kind', 'count', 'filters', 'limit', 'actions'}
        assert payload['count'] == len(payload['actions'])

    def test_filters_are_echoed_into_the_envelope(self, runner, cli_session):
        """A payload that reports counts without their scope is not reproducible."""
        result = runner.invoke(cli, [
            '--format', 'json', 'xras',
            '--status', 'failed', '--type', 'New', '--request', 'NCAR4232'])
        assert result.exit_code == 0
        filters = json.loads(result.output)['filters']
        assert filters['status'] == ['failed']
        assert filters['action_type'] == ['New']
        assert filters['request_number'] == 'NCAR4232'

    def test_repeatable_status_option(self, runner, cli_session):
        result = runner.invoke(cli, [
            '--format', 'json', 'xras',
            '--status', 'failed', '--status', 'received'])
        assert result.exit_code == 0
        assert json.loads(result.output)['filters']['status'] == [
            'failed', 'received']

    def test_an_invalid_status_is_rejected_by_click(self, runner, cli_session):
        result = runner.invoke(cli, ['xras', '--status', 'nonsense'])
        assert result.exit_code != 0

    def test_last_window_is_applied(self, runner, cli_session):
        result = runner.invoke(cli, ['--format', 'json', 'xras', '--last', '7d'])
        assert result.exit_code == 0
        assert json.loads(result.output)['filters']['start_date'] is not None

    def test_no_window_means_all_time(self, runner, cli_session):
        """A CLI invocation is explicit by nature; a hidden default window would
        make --summary quietly wrong."""
        result = runner.invoke(cli, ['--format', 'json', 'xras'])
        assert json.loads(result.output)['filters']['start_date'] is None


class TestSummaryMode:
    def test_summary_json_lists_every_status_including_zero(self, runner,
                                                            cli_session):
        """An absent bucket reads as "not measured" rather than "none".

        ``>=`` rather than ``==``: the five are a floor, not a ceiling. An
        out-of-vocabulary status must survive to the envelope — see
        ``test_an_unknown_status_survives_into_the_summary``.
        """
        result = runner.invoke(cli, ['--format', 'json', 'xras', '--summary'])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload['kind'] == 'xras_action_summary'
        assert set(payload['by_status']) >= {
            'received', 'processed', 'manual', 'failed', 'rechecked'}

    def test_summary_total_matches_the_status_buckets(self, runner, cli_session):
        payload = json.loads(
            runner.invoke(cli, ['--format', 'json', 'xras', '--summary']).output)
        assert payload['total'] == sum(payload['by_status'].values())

    def test_an_unknown_status_survives_into_the_summary(self, runner, cli_session):
        """A status outside the vocabulary is a bug to surface, not a row to hide.

        ``summarize_xras_actions`` goes out of its way to keep it — *"A status
        outside the vocabulary would be a bug, not a filter miss — surface it rather
        than dropping it on the floor"* — and this builder used to re-derive the
        dict from ``XRAS_ACTION_STATUSES``, silently undoing that. ``total`` counted
        the row either way, so the envelope reported a total that did not reconcile
        with the sum of its own buckets, on the surface built for triage.
        """
        from datetime import datetime

        from sam.integration.xras import XrasActionLog

        cli_session.add(XrasActionLog(
            received_time=datetime.now(), remote_actor='xras',
            status='pending', raw_payload='{}', action_type='Extension'))
        cli_session.flush()

        payload = json.loads(
            runner.invoke(cli, ['--format', 'json', 'xras', '--summary']).output)

        assert payload['by_status'].get('pending') == 1
        assert payload['total'] == sum(payload['by_status'].values())

    def test_summary_renders_in_rich_mode(self, runner, cli_session):
        result = runner.invoke(cli, ['xras', '--summary'])
        assert result.exit_code == 0
        assert 'Status' in result.output


class TestDetailMode:
    def test_missing_action_is_exit_1_not_found(self, runner, cli_session):
        result = runner.invoke(cli, ['xras', '--show', '999999999'])
        assert result.exit_code == 1

    def test_missing_action_json_carries_the_not_found_marker(self, runner,
                                                             cli_session):
        result = runner.invoke(
            cli, ['--format', 'json', 'xras', '--show', '999999999'])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload['kind'] == 'xras_action'
        assert payload['error'] == 'not_found'
        assert payload['action_log_id'] == 999999999

    def test_payload_flag_requires_show(self, runner, cli_session):
        """--payload alone would silently do nothing; say so instead."""
        result = runner.invoke(cli, ['xras', '--payload'])
        assert result.exit_code == 2
        assert '--payload requires --show' in result.output


class TestWriteGuards:
    def test_json_format_is_rejected_for_recheck(self, runner, cli_session):
        """Writes have no JSON contract — a machine-readable success receipt
        invites scripting a write loop nobody reviewed."""
        result = runner.invoke(cli, ['--format', 'json', 'xras', '--recheck', '1'])
        assert result.exit_code == 2
        assert json.loads(result.output)['error'] == 'json_unsupported_for_writes'

    def test_the_guard_fires_before_any_app_is_built(self, runner, cli_session):
        """If it did not, the rejection would cost a full Flask app construction."""
        with patch('webapp.run.create_app') as create_app:
            result = runner.invoke(
                cli, ['--format', 'json', 'xras', '--recheck', '1'])
        assert result.exit_code == 2
        create_app.assert_not_called()


class TestHelp:
    def test_help_documents_every_mode(self, runner):
        result = runner.invoke(cli, ['xras', '--help'])
        assert result.exit_code == 0
        for flag in ('--show', '--summary', '--recheck', '--status', '--last'):
            assert flag in result.output

    def test_help_states_that_recheck_applies_nothing(self, runner):
        """The most surprising thing about --recheck, so it belongs in --help."""
        result = runner.invoke(cli, ['xras', '--help'])
        assert 'APPLIES NOTHING' in result.output
        assert 'Applies nothing' in result.output


class TestWindowParsing:
    @pytest.mark.parametrize('value,expected_days', [
        ('7d', 7), ('30', 30), ('2w', 14),
        # Round up: '12h' means "today", and truncating to 0 days would mean
        # "no window at all" — the opposite of what was asked.
        ('12h', 1), ('36h', 2), ('1h', 1),
    ])
    def test_parse_days(self, value, expected_days):
        from cli.xras.commands import XrasCommand
        assert XrasCommand._parse_days(value) == expected_days


# ── the account worklist, person lookup, and the two-sided mapping audit ──

class TestAccountsMode:
    """``--accounts``: who must exist in SAM before a handoff works."""

    def test_an_empty_worklist_exits_zero(self, runner, cli_session):
        """Nobody blocked is a successful report, not a miss. A gate that
        treated it as one would fail every healthy day."""
        result = runner.invoke(cli, ['xras', '--accounts'])
        assert result.exit_code == EXIT_SUCCESS

    def test_the_json_envelope_carries_the_expected_kind(self, runner, cli_session):
        result = runner.invoke(cli, ['--format', 'json', 'xras', '--accounts'])
        assert result.exit_code == EXIT_SUCCESS
        payload = json.loads(result.output)
        assert payload['kind'] == 'xras_accounts'
        assert set(payload['counts']) >= {'total', 'absent', 'inactive',
                                          'placeholder'}
        assert payload['enriched'] is False
        assert payload['enrichment'] is None

    def test_enrich_without_the_api_is_an_error(self, runner, cli_session,
                                                monkeypatch):
        monkeypatch.delenv('XRAS_OUTGOING_ENABLED', raising=False)
        monkeypatch.delenv('XRAS_API_KEY', raising=False)
        result = runner.invoke(cli, ['xras', '--accounts', '--enrich'])
        assert result.exit_code == EXIT_ERROR

    def test_enrich_requires_accounts(self, runner, cli_session):
        result = runner.invoke(cli, ['xras', '--enrich'])
        assert result.exit_code == EXIT_ERROR
        assert '--enrich requires --accounts' in result.output


class TestPersonMode:
    """The three-outcome model reaches the exit code intact."""

    def _configure(self, monkeypatch):
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')

    def test_a_found_person_exits_zero(self, runner, cli_session, monkeypatch):
        self._configure(monkeypatch)
        monkeypatch.setattr('sam.integration.xras_api.get_person',
                            lambda u: {'username': u, 'firstName': 'Ada',
                                       'lastName': 'Invented',
                                       'isReconciled': False})
        result = runner.invoke(cli, ['xras', '--person', 'ghost-user-1'])
        assert result.exit_code == EXIT_SUCCESS

    def test_an_unknown_username_exits_not_found(self, runner, cli_session,
                                                 monkeypatch):
        self._configure(monkeypatch)
        monkeypatch.setattr('sam.integration.xras_api.get_person',
                            lambda u: None)
        result = runner.invoke(cli, ['xras', '--person', 'nobody'])
        assert result.exit_code == EXIT_NOT_FOUND

    def test_an_outage_exits_error_not_not_found(self, runner, cli_session,
                                                 monkeypatch):
        """Collapsing these would make "XRAS is down" indistinguishable from
        "this person does not exist" — opposite conclusions for an operator."""
        from sam.integration.xras_api.base import XrasSourceUnavailable

        self._configure(monkeypatch)

        def boom(_u):
            raise XrasSourceUnavailable('down')

        monkeypatch.setattr('sam.integration.xras_api.get_person', boom)
        result = runner.invoke(cli, ['xras', '--person', 'anyone'])
        assert result.exit_code == EXIT_ERROR

    def test_unconfigured_exits_error(self, runner, cli_session, monkeypatch):
        monkeypatch.delenv('XRAS_OUTGOING_ENABLED', raising=False)
        monkeypatch.delenv('XRAS_API_KEY', raising=False)
        result = runner.invoke(cli, ['xras', '--person', 'anyone'])
        assert result.exit_code == EXIT_ERROR

    def test_the_json_envelope_carries_the_expected_kind(self, runner, cli_session,
                                                         monkeypatch):
        self._configure(monkeypatch)
        monkeypatch.setattr('sam.integration.xras_api.get_person',
                            lambda u: None)
        result = runner.invoke(cli, ['--format', 'json', 'xras',
                                     '--person', 'nobody'])
        payload = json.loads(result.output)
        assert payload == {'kind': 'xras_person', 'username': 'nobody',
                           'found': False, 'person': None}


class TestTwoSidedMappingAudit:
    """The gap that made the pre-cutover gate one-sided."""

    def test_unconfigured_reproduces_the_local_only_report(self, runner,
                                                           cli_session,
                                                           monkeypatch):
        monkeypatch.delenv('XRAS_OUTGOING_ENABLED', raising=False)
        monkeypatch.delenv('XRAS_API_KEY', raising=False)
        result = runner.invoke(cli, ['--format', 'json', 'xras',
                                     '--validate-mapping'])
        payload = json.loads(result.output)
        # `live_checked` False distinguishes "XRAS sends nothing SAM lacks"
        # from "we never asked" — the report must not imply the stronger claim.
        assert payload['live_checked'] is False
        assert payload['xras_only_keys'] == []
        assert payload['live_key_count'] is None

    def test_a_key_xras_sends_that_sam_lacks_fails_the_gate(self, runner,
                                                            cli_session,
                                                            monkeypatch):
        """The runtime failure this makes visible ahead of time:
        ``No resource found in SAM corresponding to key %s``."""
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        monkeypatch.setattr(
            'sam.integration.xras_api.resource_repository_keys',
            lambda: [999999999])
        result = runner.invoke(cli, ['--format', 'json', 'xras',
                                     '--validate-mapping'])
        payload = json.loads(result.output)
        assert payload['live_checked'] is True
        assert 999999999 in payload['xras_only_keys']
        assert result.exit_code == EXIT_NOT_FOUND

    def test_an_unreachable_api_warns_and_degrades(self, runner, cli_session,
                                                   monkeypatch):
        """The local half is still worth reporting."""
        from sam.integration.xras_api.base import XrasSourceUnavailable

        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')

        def boom():
            raise XrasSourceUnavailable('down')

        monkeypatch.setattr(
            'sam.integration.xras_api.resource_repository_keys', boom)
        result = runner.invoke(cli, ['xras', '--validate-mapping'])
        assert result.exit_code == EXIT_SUCCESS

    def test_the_live_catalog_self_verifies(self, runner, cli_session,
                                            monkeypatch):
        """Feeding back exactly the keys SAM already holds must report zero
        gaps — the shape of the real 13/13 result against production."""
        from sam.queries.xras_actions import audit_resource_mapping

        from sam.integration.xras import XrasResourceRepositoryKeyResource

        local = audit_resource_mapping(cli_session)
        known = [r.resource_repository_key for r in
                 cli_session.query(XrasResourceRepositoryKeyResource).all()]

        report = audit_resource_mapping(cli_session, xras_keys=known)
        assert report['xras_only_keys'] == []
        assert report['live_checked'] is True
        assert report['live_key_count'] == len(set(known))
        # The local half is untouched by the new argument.
        assert report['unmapped_active'] == local['unmapped_active']
        assert report['dangling_keys'] == local['dangling_keys']


class TestWorklistRendering:
    """The rich renderers on a populated worklist.

    The empty-list cases above never reach the table-building code, so a
    formatting mistake there would ship green.
    """

    @staticmethod
    def _payload(**overrides):
        row = {
            'username': 'ghost-user-1', 'classification': 'absent',
            'remedy': 'create', 'placeholder': True, 'roles': ['PI'],
            'is_account_to_be_created': True, 'is_reconciled': False,
            'first_seen': None, 'last_seen': None,
            'latest_action_log_id': 7, 'sources': ['action_log'],
            'person': None,
            'actions': [{'action_log_id': 7, 'request_number': 'NCAR0001',
                         'action_type': 'New', 'status': 'received',
                         'received_time': None, 'source': 'action_log',
                         'would_succeed': False,
                         'reject_messages': ['Username x is missing']}],
        }
        row.update(overrides)
        return {'kind': 'xras_accounts',
                'counts': {'total': 1, 'absent': 1, 'inactive': 0,
                           'placeholder': 1, 'reconciled': 0},
                'enriched': False, 'enrichment': None, 'accounts': [row]}

    def _render(self, payload):
        from rich.console import Console

        from cli.xras.display import display_account_worklist

        console = Console(record=True, width=200)
        ctx = MagicMock()
        ctx.console = console
        display_account_worklist(ctx, payload)
        return console.export_text()

    def test_an_absent_placeholder_renders(self):
        out = self._render(self._payload())
        assert 'ghost-user-1' in out
        assert 'create' in out and 'placeholder' in out
        assert 'NCAR0001' in out

    def test_an_inactive_row_says_reactivate(self):
        out = self._render(self._payload(classification='inactive',
                                         remedy='reactivate',
                                         placeholder=False))
        assert 'reactivate' in out

    def test_identity_state_renders_in_all_three_states(self):
        """XRAS-side identity, not progress — and deliberately not worded as
        "reconciled"/"unreconciled" next to a placeholder count, which is a
        different fact entirely (see the card's header-badge test)."""
        assert 'unidentified' in self._render(self._payload(is_reconciled=False))
        assert 'identified' in self._render(self._payload(is_reconciled=True))
        # None means "XRAS was not asked" — distinct from a definite answer.
        self._render(self._payload(is_reconciled=None))

    def test_the_summary_line_calls_placeholders_placeholders(self):
        out = self._render(self._payload())
        assert 'placeholder identities' in out
        assert 'unreconciled ARC' not in out

    def test_an_outage_is_reported_under_the_table(self):
        payload = self._payload()
        payload['enriched'] = True
        payload['enrichment'] = {'looked_up': 0, 'found': 0, 'closed': 0,
                                 'unavailable': True, 'budget_exhausted': False,
                                 'error': 'down'}
        out = self._render(payload)
        assert 'unavailable' in out
        assert 'complete' in out          # the worklist itself still is

    def test_the_person_renderer_covers_found_and_missing(self):
        from rich.console import Console

        from cli.xras.display import display_person

        for payload, expected in (
                ({'kind': 'xras_person', 'username': 'u', 'found': False,
                  'person': None}, 'no user'),
                ({'kind': 'xras_person', 'username': 'u', 'found': True,
                  'person': {'firstName': 'Ada', 'lastName': 'Invented',
                             'email': 'ada@example.invalid',
                             'residenceCountry': 'Kiribati',
                             'isReconciled': False}}, 'Kiribati')):
            console = Console(record=True, width=200)
            ctx = MagicMock()
            ctx.console = console
            display_person(ctx, payload)
            assert expected in console.export_text()
