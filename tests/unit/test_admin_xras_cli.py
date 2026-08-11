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


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def cli_session(session):
    """Point the CLI group's session at the test session."""
    with patch('sam.session.create_sam_engine') as mock_engine, \
         patch('cli.cmds.admin.Session') as mock_session_cls:
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
