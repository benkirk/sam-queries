"""
CliRunner tests for `sam-admin xras`.

Unlike `sam-admin cache`, this command reads the database directly, so the group
fixture wires the CLI's session to the test session rather than mocking it away.
Rows come from the Layer-2 factory path via `_record` where a committed row is
needed, and from an empty table otherwise — the envelope shape must be correct in
both cases.

The `--recheck` path is deliberately NOT exercised end-to-end here: it builds a
full Flask app to get an application context (see `XrasCommand._replay` for why
that is the right call rather than a second write path). Its behavior is covered
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
        payload = json.loads(result.stdout)
        assert payload['kind'] == 'xras_action_list'
        assert set(payload) >= {'kind', 'count', 'filters', 'limit', 'actions'}
        assert payload['count'] == len(payload['actions'])

    def test_filters_are_echoed_into_the_envelope(self, runner, cli_session):
        """A payload that reports counts without their scope is not reproducible."""
        result = runner.invoke(cli, [
            '--format', 'json', 'xras',
            '--status', 'failed', '--type', 'New', '--request', 'NCAR4232'])
        assert result.exit_code == 0
        filters = json.loads(result.stdout)['filters']
        assert filters['status'] == ['failed']
        assert filters['action_type'] == ['New']
        assert filters['request_number'] == 'NCAR4232'

    def test_repeatable_status_option(self, runner, cli_session):
        result = runner.invoke(cli, [
            '--format', 'json', 'xras',
            '--status', 'failed', '--status', 'received'])
        assert result.exit_code == 0
        assert json.loads(result.stdout)['filters']['status'] == [
            'failed', 'received']

    def test_an_invalid_status_is_rejected_by_click(self, runner, cli_session):
        result = runner.invoke(cli, ['xras', '--status', 'nonsense'])
        assert result.exit_code != 0

    def test_last_window_is_applied(self, runner, cli_session):
        result = runner.invoke(cli, ['--format', 'json', 'xras', '--last', '7d'])
        assert result.exit_code == 0
        assert json.loads(result.stdout)['filters']['start_date'] is not None

    def test_no_window_means_all_time(self, runner, cli_session):
        """A CLI invocation is explicit by nature; a hidden default window would
        make --summary quietly wrong."""
        result = runner.invoke(cli, ['--format', 'json', 'xras'])
        assert json.loads(result.stdout)['filters']['start_date'] is None


class TestReadinessMode:
    def test_empty_board_exits_zero(self, runner, cli_session, monkeypatch):
        monkeypatch.setattr('sam.integration.xras_api.cache.load_requests_index',
                            lambda: None)
        result = runner.invoke(cli, ['--format', 'json', 'xras', '--readiness'])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload['kind'] == 'xras_readiness'
        assert payload['total'] == 0

    def test_the_board_sorts_red_before_green(self, runner, cli_session,
                                              monkeypatch):
        snapshot = {'generated_at': '2026-08-23', 'rows': [
            {'request_number': 'GREEN0001', 'preflight_rollup': 'rechecked',
             'status': 'Approved', 'opportunity_name': 'Large',
             'pi': {'username': 'q'}, 'pending_push': False,
             'actions': [{'action_id': 2, 'preflight': {'status': 'rechecked',
                                                        'messages': []}}]},
            {'request_number': 'RED0001', 'preflight_rollup': 'failed',
             'status': 'Approved', 'opportunity_name': 'Small',
             'pi': {'username': 'p'}, 'pending_push': True,
             'actions': [{'action_id': 1, 'preflight': {
                 'status': 'failed', 'messages': ['PI x is not in database']}}]},
        ]}
        monkeypatch.setattr('sam.integration.xras_api.cache.load_requests_index',
                            lambda: snapshot)
        result = runner.invoke(cli, ['--format', 'json', 'xras', '--readiness'])
        assert result.exit_code == 0
        rows = json.loads(result.stdout)['requests']
        assert [r['request_number'] for r in rows] == ['RED0001', 'GREEN0001']
        assert rows[0]['messages'] == ['PI x is not in database']


class TestMnemonicReportMode:
    def test_empty_board_exits_zero(self, runner, cli_session, monkeypatch):
        monkeypatch.setattr('sam.integration.xras_api.cache.load_requests_index',
                            lambda: None)
        result = runner.invoke(cli, ['--format', 'json', 'xras', '--mnemonic-report'])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload['kind'] == 'xras_mnemonic_report'
        assert payload['targets'] == []

    def test_it_ranks_orgs_from_the_snapshot(self, runner, cli_session,
                                             monkeypatch):
        from factories import make_organization, make_user, make_user_organization
        from sam.xras.errors import mnemonic_internal_failed

        for name, username in [('Top Org', 'pi-top'), ('Lesser Org', 'pi-less')]:
            user = make_user(cli_session, username=username)
            org = make_organization(cli_session, name=name)
            make_user_organization(cli_session, user=user, organization=org)
        cli_session.flush()

        def _entry(num, pi):
            return {'request_number': num, 'preflight_rollup': 'failed',
                    'pi': {'username': pi}, 'opportunity_name': 'O',
                    'actions': [{'action_id': 1, 'preflight': {
                        'status': 'failed', 'messages': [mnemonic_internal_failed()]}}]}
        snapshot = {'generated_at': '2026-08-23', 'rows': [
            _entry('NCAR0001', 'pi-top'), _entry('NCAR0002', 'pi-top'),
            _entry('NCAR0003', 'pi-less')]}
        monkeypatch.setattr('sam.integration.xras_api.cache.load_requests_index',
                            lambda: snapshot)
        result = runner.invoke(cli, ['--format', 'json', 'xras', '--mnemonic-report'])
        assert result.exit_code == 0
        targets = json.loads(result.stdout)['targets']
        assert [t['name'] for t in targets] == ['Top Org', 'Lesser Org']
        assert targets[0]['unblock_count'] == 2


class TestIdentityReportMode:

    @staticmethod
    def _feed(monkeypatch, rows):
        from sam.queries.xras_accounts import PendingFeed
        monkeypatch.setattr('sam.queries.xras_accounts.load_pending_worklist_rows',
                            lambda: PendingFeed(rows=rows, checked=True))

    def test_no_snapshot_is_an_empty_report_not_an_error(self, runner, cli_session,
                                                         monkeypatch):
        from sam.queries.xras_accounts import PendingFeed
        monkeypatch.setattr('sam.queries.xras_accounts.load_pending_worklist_rows',
                            lambda: PendingFeed(reason='no_snapshot'))
        result = runner.invoke(cli, ['--format', 'json', 'xras', '--identity-report'])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload['kind'] == 'xras_identity_report'
        # Feed A may hold rows other tests committed; none is a merge target.
        assert payload['targets'] == []

    def test_a_placeholder_sam_holds_is_a_target(self, runner, cli_session,
                                                 monkeypatch):
        from factories import make_email_address, make_user
        mail = make_email_address(session=cli_session, user=make_user(cli_session))
        self._feed(monkeypatch, [{
            'username': 'ghost-user-cli1', 'classification': 'absent',
            'remedy': 'create', 'placeholder': True, 'is_reconciled': False,
            'roles': ('PI',), 'sources': ['reports'], 'waiting_since': None,
            'person': {'email': mail.email_address},
            'actions': [{'action_log_id': None, 'request_number': 'NCAR0777',
                         'action_type': 'New', 'status': 'Approved',
                         'received_time': None, 'submit_date': '2026-08-20',
                         'source': 'reports', 'would_succeed': None,
                         'preflight_status': None, 'reject_messages': []}]}])
        result = runner.invoke(cli, ['--format', 'json', 'xras', '--identity-report'])
        assert result.exit_code == 0, result.output
        targets = json.loads(result.stdout)['targets']
        assert [t['username'] for t in targets] == ['ghost-user-cli1']
        assert targets[0]['target_username'] == mail.user.username
        assert targets[0]['sample'] == ['NCAR0777']

    def test_rich_mode_renders(self, runner, cli_session, monkeypatch):
        from sam.queries.xras_accounts import PendingFeed
        monkeypatch.setattr('sam.queries.xras_accounts.load_pending_worklist_rows',
                            lambda: PendingFeed(reason='no_snapshot'))
        result = runner.invoke(cli, ['xras', '--identity-report'])
        assert result.exit_code == 0, result.output


class TestContractReportMode:

    def test_no_snapshot_is_an_empty_report_not_an_error(self, runner, cli_session,
                                                         monkeypatch):
        monkeypatch.setattr('sam.integration.xras_api.cache.load_requests_index',
                            lambda: None)
        result = runner.invoke(cli, ['--format', 'json', 'xras', '--contract-report'])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload['kind'] == 'xras_contract_report'
        assert payload['targets'] == [] and payload['variants'] == []

    def test_it_ranks_numbers_from_the_snapshot(self, runner, cli_session,
                                                monkeypatch):
        def _entry(num, number):
            return {'request_number': num, 'preflight_rollup': 'failed',
                    'pi': {'username': 'pi-x'}, 'activity_date': '2026-08-20',
                    'actions': [{'action_id': 1, 'preflight': {
                        'status': 'failed', 'messages': ['x'],
                        'resolved': {'unresolved_grants': [
                            {'number': number, 'core': number, 'reason': 'missing',
                             'candidates': [], 'agency': 'NSF', 'title': 'T'}]}}}]}
        snapshot = {'generated_at': '2026-08-24', 'rows': [
            _entry('NCAR0001', '9980101'), _entry('NCAR0002', '9980101'),
            _entry('NCAR0003', 'ISS 25-643')]}
        monkeypatch.setattr('sam.integration.xras_api.cache.load_requests_index',
                            lambda: snapshot)
        result = runner.invoke(cli, ['--format', 'json', 'xras', '--contract-report'])
        assert result.exit_code == 0, result.output
        targets = json.loads(result.stdout)['targets']
        assert [t['number'] for t in targets] == ['9980101', 'ISS 25-643']
        assert targets[0]['unblock_count'] == 2
        assert targets[0]['suggested_source'] == 'NSF'

    def test_rich_mode_renders(self, runner, cli_session, monkeypatch):
        monkeypatch.setattr('sam.integration.xras_api.cache.load_requests_index',
                            lambda: None)
        result = runner.invoke(cli, ['xras', '--contract-report'])
        assert result.exit_code == 0, result.output
        assert 'No failing push cites a contract' in result.output


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
        payload = json.loads(result.stdout)
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
        payload = json.loads(result.stdout)
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
        assert json.loads(result.stdout)['error'] == 'json_unsupported_for_writes'

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


# the account worklist, person lookup, and the two-sided mapping audit

class TestAccountsMode:
    """``--accounts``: who must exist in SAM before a handoff works."""

    def test_an_empty_worklist_exits_zero(self, runner, cli_session):
        """Nobody blocked is a successful report, not a miss. A gate that
        treated it as one would fail every healthy day."""
        result = runner.invoke(cli, ['xras', '--accounts'])
        assert result.exit_code == EXIT_SUCCESS

    def test_the_json_envelope_carries_the_expected_kind(self, runner, cli_session):
        # WARNING: `result.stdout`, not `result.output` — the latter merges stderr,
        # and the whole point of the split below is that a degradation notice
        # must not land inside the envelope.
        result = runner.invoke(cli, ['--format', 'json', 'xras', '--accounts'])
        assert result.exit_code == EXIT_SUCCESS
        payload = json.loads(result.stdout)
        assert payload['kind'] == 'xras_accounts'
        assert set(payload['counts']) >= {'total', 'absent', 'inactive',
                                          'placeholder', 'oldest_days'}
        assert payload['enriched'] is False
        assert payload['enrichment'] is None
        # "Feed B was empty" and "we could not read Feed B" are different
        # facts, and only the second means this count is a subset.
        assert payload['pending_checked'] is False

    def test_a_degradation_notice_never_lands_inside_the_json(
            self, runner, cli_session, monkeypatch):
        """WARNING: Regression: `ctx.console` is **stdout**.

        Every "could not reach X, reporting the local half" notice on this
        command used to print there, which put prose ahead of the envelope and
        broke `sam-admin --format json xras ... | jq` for exactly the runs an
        operator most needs to pipe. They belong on stderr; the envelope
        already carries the machine-readable form of the same fact.
        """
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        monkeypatch.setattr(
            'sam.integration.xras_api.cache.load_pending_worklist',
            lambda: (_ for _ in ()).throw(RuntimeError('no redis here')))

        result = runner.invoke(cli, ['--format', 'json', 'xras', '--accounts'])

        json.loads(result.stdout)                    # parses, i.e. clean
        assert 'no redis here' not in result.stdout
        assert json.loads(result.stdout)['pending_checked'] is False

    def test_the_worklist_unions_both_feeds(self, runner, cli_session,
                                            monkeypatch):
        """The gap this closes: `--accounts` reported 0 in production while the
        dashboard showed a real queue, because it only ever read the action log
        and the card reads the sweep's published snapshot.

        WARNING: Overlap between the feeds is normal — Feed A is precisely what has
        POSTED, Feed B what XRAS approved and may or may not have sent — so
        this is a union on the casefolded username, not a concatenation.
        """
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        monkeypatch.setattr(
            'sam.integration.xras_api.cache.load_pending_worklist',
            lambda: {'rows': [{'username': 'ghostpendingonly',
                               'classification': 'absent', 'remedy': 'create',
                               'placeholder': False, 'roles': ('PI',),
                               'actions': [], 'sources': ['reports'],
                               'is_account_to_be_created': False,
                               'first_seen': None, 'last_seen': None,
                               'person': None, 'is_reconciled': None,
                               'latest_action_log_id': None}]})

        result = runner.invoke(cli, ['--format', 'json', 'xras', '--accounts'])
        payload = json.loads(result.stdout)

        assert payload['pending_checked'] is True
        names = [a['username'] for a in payload['accounts']]
        assert 'ghostpendingonly' in names

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
        payload = json.loads(result.stdout)
        assert payload == {'kind': 'xras_person', 'username': 'nobody',
                           'found': False, 'person': None}


class TestFamilyMode:
    """The request-family tree probe — same three-outcome model as --person."""

    def _configure(self, monkeypatch, lines):
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        client = MagicMock()
        client.get_request_family_by_number.return_value = lines
        monkeypatch.setattr(
            'sam.integration.xras_api.XrasApiClient.from_environment',
            classmethod(lambda cls, *a, **k: client))

    def _lines(self):
        return [
            {'requestId': 111, 'requestNumber': 'UCUB0089', 'requestType': 'New',
             'beginDate': '2020-01-01', 'endDate': '2024-12-31',
             'actions': [{'actionId': 1, 'actionType': 'New',
                          'actionStatus': 'Approved', 'entryDate': '2020-01-01'}]},
            {'requestId': 222, 'requestNumber': 'UCUB0089', 'requestType': 'Renewal',
             'beginDate': '2022-05-01', 'endDate': '2024-12-31',
             'actions': [{'actionId': 4, 'actionType': 'Extension',
                          'actionStatus': 'Submitted', 'entryDate': '2024-12-23'}]},
        ]

    def test_a_found_family_exits_zero(self, runner, cli_session, monkeypatch):
        self._configure(monkeypatch, self._lines())
        result = runner.invoke(cli, ['xras', '--family', 'UCUB0089'])
        assert result.exit_code == EXIT_SUCCESS

    def test_an_unknown_projcode_exits_not_found(self, runner, cli_session,
                                                 monkeypatch):
        self._configure(monkeypatch, [])
        result = runner.invoke(cli, ['xras', '--family', 'NOSUCH0001'])
        assert result.exit_code == EXIT_NOT_FOUND

    def test_an_outage_exits_error_not_not_found(self, runner, cli_session,
                                                 monkeypatch):
        from sam.integration.xras_api.base import XrasSourceUnavailable

        self._configure(monkeypatch, [])
        client = MagicMock()
        client.get_request_family_by_number.side_effect = \
            XrasSourceUnavailable('down')
        monkeypatch.setattr(
            'sam.integration.xras_api.XrasApiClient.from_environment',
            classmethod(lambda cls, *a, **k: client))
        result = runner.invoke(cli, ['xras', '--family', 'UCUB0089'])
        assert result.exit_code == EXIT_ERROR

    def test_unconfigured_exits_error(self, runner, cli_session, monkeypatch):
        monkeypatch.delenv('XRAS_OUTGOING_ENABLED', raising=False)
        monkeypatch.delenv('XRAS_API_KEY', raising=False)
        result = runner.invoke(cli, ['xras', '--family', 'UCUB0089'])
        assert result.exit_code == EXIT_ERROR

    def test_the_json_envelope_carries_the_tree(self, runner, cli_session,
                                                monkeypatch):
        self._configure(monkeypatch, self._lines())
        result = runner.invoke(cli, ['--format', 'json', 'xras',
                                     '--family', 'UCUB0089'])
        payload = json.loads(result.stdout)
        assert payload['kind'] == 'xras_request_family'
        assert payload['found'] is True
        assert payload['family']['new_request_id'] == 111
        # timeline flattens both lines' actions, date-ordered, ISO strings
        assert [a['action_id'] for a in payload['family']['timeline']] == [1, 4]
        assert payload['family']['activity_date'] == '2024-12-23'


class TestTwoSidedMappingAudit:
    """The gap that made the pre-cutover gate one-sided."""

    def test_unconfigured_reproduces_the_local_only_report(self, runner,
                                                           cli_session,
                                                           monkeypatch):
        monkeypatch.delenv('XRAS_OUTGOING_ENABLED', raising=False)
        monkeypatch.delenv('XRAS_API_KEY', raising=False)
        result = runner.invoke(cli, ['--format', 'json', 'xras',
                                     '--validate-mapping'])
        payload = json.loads(result.stdout)
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
        payload = json.loads(result.stdout)
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


class TestOpportunityAudit:
    """``--validate-opportunities`` — the map whose failure mode is silent.

    Every other XRAS mapping gap 422s the action. An unmapped ``opportunityId``
    falls through to the free-text ladder, which cannot name any facility-4
    allocation type — so a Wyoming request resolves to a UNIV panel, the join
    *succeeds*, and the project is created with a UNIV projcode. Nothing fails.
    That is why this command exists and why its exit code is so narrow.
    """

    #: One open-opportunity payload, in the shape ``GET /v1/opportunities``
    #: returns: the type id lives under ``allocationTypeInfo`` and the panel is
    #: the one flagged ``isPrimary``, never ``panels[0]``.
    @staticmethod
    def _payload(opportunity_id, *, type_id, panel_id, name, alloc_type=None):
        return {
            'opportunityId': opportunity_id,
            'opportunityName': name,
            'allocationType': alloc_type,
            'allocationTypeInfo': {'allocationTypeId': type_id},
            'panels': [{'panelId': panel_id, 'isPrimary': True}],
        }

    def _run(self, runner, payloads, monkeypatch, *, fmt_json=True):
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        client = MagicMock()
        client.get_open_opportunities.return_value = payloads
        monkeypatch.setattr(
            'sam.integration.xras_api.XrasApiClient.from_environment',
            classmethod(lambda cls, *a, **k: client))
        args = ['xras', '--validate-opportunities']
        return runner.invoke(cli, (['--format', 'json'] + args) if fmt_json
                             else args)

    # -- the degraded halves ------------------------------------------------

    def test_unconfigured_reports_the_local_half_and_says_so(self, runner,
                                                             cli_session,
                                                             monkeypatch):
        """Fail-closed, exactly as ``--validate-mapping`` degrades.

        ``live_checked`` False is the whole point: "nothing is unmapped out
        there" and "we never asked" must not render as the same report.
        """
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '0')
        monkeypatch.delenv('XRAS_API_KEY', raising=False)
        result = runner.invoke(cli, ['--format', 'json', 'xras',
                                     '--validate-opportunities'])
        payload = json.loads(result.stdout)
        assert payload['kind'] == 'xras_opportunity_mapping'
        assert payload['live_checked'] is False
        assert payload['live_id_count'] is None
        assert payload['unmapped_ids'] == []
        assert payload['proposal'] == {'agree': [], 'review': [],
                                       'unknown_pair': []}
        assert result.exit_code == EXIT_SUCCESS

    def test_an_unreachable_api_warns_and_degrades(self, runner, cli_session,
                                                   monkeypatch):
        """The local half is still worth reporting, and it is still exit 0."""
        from sam.integration.xras_api.base import XrasSourceUnavailable

        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        client = MagicMock()
        client.get_open_opportunities.side_effect = XrasSourceUnavailable('down')
        monkeypatch.setattr(
            'sam.integration.xras_api.XrasApiClient.from_environment',
            classmethod(lambda cls, *a, **k: client))
        result = runner.invoke(cli, ['xras', '--validate-opportunities'])
        assert result.exit_code == EXIT_SUCCESS
        assert 'local half only' in result.output

    # -- the exit-code contract, which is the easy thing to get wrong -------

    def test_an_unmapped_opportunity_is_not_a_failure(self, runner, cli_session,
                                                      monkeypatch):
        """With an EMPTY table every opportunity is unmapped and ingestion is
        completely healthy — the ladder resolves them exactly as it did before
        the table existed. A gate keyed on this would fail forever, which is
        the mistake ``--validate-mapping`` already made once and corrected."""
        result = self._run(runner, [self._payload(
            9_100_001, type_id=500024, panel_id=500021,
            name='Small Allocation (University)', alloc_type='Small')],
            monkeypatch)
        payload = json.loads(result.stdout)
        assert 9_100_001 in payload['unmapped_ids']
        assert result.exit_code == EXIT_SUCCESS

    def test_a_dangling_row_is_the_one_failing_state(self, runner, cli_session,
                                                     monkeypatch, session):
        """A mapping row whose allocation type has no panel. The ingest-side
        lookup must treat it as a miss and fall through *silently*, so nothing
        else would ever surface it."""
        from factories import make_allocation_type, make_xras_opportunity_mapping

        alloc_type = make_allocation_type(session)
        alloc_type.panel_id = None
        session.flush()
        row = make_xras_opportunity_mapping(session, allocation_type=alloc_type)

        result = self._run(runner, [], monkeypatch)
        payload = json.loads(result.stdout)
        assert row.opportunity_id in payload['dangling_ids']
        assert result.exit_code == EXIT_NOT_FOUND

    # -- the agree rule -----------------------------------------------------

    def test_an_agreed_pair_is_reported_as_mappable(self, runner, cli_session,
                                                    monkeypatch):
        """Both derivations produce ``CHAP``/``CHAP``, so the sweep would write
        it and there is nothing for a human to decide."""
        result = self._run(runner, [self._payload(
            9_100_002, type_id=500023, panel_id=500022,
            name='Large Allocation (University) - Fall 2026',
            alloc_type='Large')], monkeypatch)
        payload = json.loads(result.stdout)
        agreed = {e['opportunity_id']: e for e in payload['proposal']['agree']}
        assert 9_100_002 in agreed
        assert agreed[9_100_002]['pair'] == ['CHAP', 'CHAP']
        assert result.exit_code == EXIT_SUCCESS

    def test_a_disagreement_is_withheld_and_reported_with_both_answers(
            self, runner, cli_session, monkeypatch):
        """The known live case: XRAS files the unsponsored family under
        ``Educational`` — the same type id as Classroom — while SAM means
        ``Small (No NSF award)``. It changes the answer, so a human decides.

        WARNING: Reported with **both** derivations, so the row explains itself
        without a second query. That is what makes it actionable rather than
        merely alarming.
        """
        result = self._run(runner, [self._payload(
            9_100_003, type_id=500026, panel_id=500021,
            name='University small request - unsponsored',
            alloc_type='Exploratory')], monkeypatch)
        payload = json.loads(result.stdout)
        review = {e['opportunity_id']: e for e in payload['proposal']['review']}
        assert 9_100_003 in review
        assert review[9_100_003]['xras'] != review[9_100_003]['ladder']
        assert payload['proposal']['agree'] == []
        # A withheld row is the rule WORKING. Two pairs sit here permanently by
        # design, so exiting non-zero would train an operator to ignore the one
        # bucket that matters.
        assert result.exit_code == EXIT_SUCCESS

    def test_an_unknown_pair_is_reported_never_guessed(self, runner, cli_session,
                                                       monkeypatch):
        """A genuinely new allocation product. Adding it is a one-line edit to
        ``sam/xras/opportunity_types.py`` — a code review, not a silent write."""
        result = self._run(runner, [self._payload(
            9_100_004, type_id=999_001, panel_id=999_002,
            name='Wyoming Small Allocation', alloc_type='Small')], monkeypatch)
        payload = json.loads(result.stdout)
        unknown = [e['opportunity_id']
                   for e in payload['proposal']['unknown_pair']]
        assert 9_100_004 in unknown
        assert payload['proposal']['agree'] == []
        assert result.exit_code == EXIT_SUCCESS

    # -- the scoping rule ---------------------------------------------------

    def test_the_proposal_covers_only_unmapped_opportunities(
            self, runner, cli_session, monkeypatch, session):
        """WARNING: The rule that keeps the ``review`` bucket meaningful.

        Two rows in production are ``source='manual'`` *because* the two
        derivations disagree and a human settled it. Run the proposal over
        everything and those reappear in ``review`` on every invocation, which
        is precisely how an operator learns to ignore the bucket. So the
        proposal sees the unmapped subset only — the same scoping
        ``_map_new_opportunities`` uses in ``xras_sweep``.
        """
        from factories import make_allocation_type, make_xras_opportunity_mapping

        row = make_xras_opportunity_mapping(
            session, allocation_type=make_allocation_type(session),
            opportunity_name='already decided by a human')

        result = self._run(runner, [self._payload(
            row.opportunity_id, type_id=500026, panel_id=500021,
            name='already decided by a human',
            alloc_type='Exploratory')], monkeypatch)
        payload = json.loads(result.stdout)

        assert row.opportunity_id in payload['mapped_ids']
        assert row.opportunity_id not in payload['unmapped_ids']
        seen = [e['opportunity_id']
                for bucket in payload['proposal'].values() for e in bucket]
        assert row.opportunity_id not in seen

    # -- rendering ----------------------------------------------------------

    def test_rich_mode_renders_all_three_buckets(self, runner, cli_session,
                                                 monkeypatch):
        result = self._run(runner, [
            self._payload(9_100_005, type_id=500023, panel_id=500022,
                          name='agrees', alloc_type='Large'),
            self._payload(9_100_006, type_id=500026, panel_id=500021,
                          name='disagrees', alloc_type='Exploratory'),
            self._payload(9_100_007, type_id=999_001, panel_id=999_002,
                          name='unknown to the constant', alloc_type='Small'),
        ], monkeypatch, fmt_json=False)
        assert result.exit_code == EXIT_SUCCESS
        assert 'Would be mapped automatically' in result.output
        assert 'the two derivations disagree' in result.output
        assert 'a new allocation product' in result.output


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

    def test_a_ready_row_names_its_merge_target(self):
        out = self._render(self._payload(
            remedy='merge', merge_target={'username': 'realname', 'active': True}))
        assert 'merge into realname' in out

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


class TestVocabularyAudit:
    """The hand-verified constants get a live re-verification path."""

    LIVE_ROLES = [
        {'roleTypeId': 13, 'roleType': 'PI',
         'displayRoleType': 'Project Lead', 'isActive': True},
        {'roleTypeId': 14, 'roleType': 'Allocation Manager',
         'displayRoleType': 'Project Admin', 'isActive': True},
        {'roleTypeId': 19, 'roleType': 'User',
         'displayRoleType': 'User', 'isActive': True},
    ]
    LIVE_PANELS = [
        {'panelId': 500021, 'panelName': 'CISL Resource Support',
         'panelAbbr': 'CISL RSD', 'isActive': True},
        {'panelId': 500022, 'panelName': 'CISL HPC Allocation Panel',
         'panelAbbr': 'CHAP', 'isActive': True},
        {'panelId': 500032, 'panelName': 'External reviewers for CHAP',
         'panelAbbr': 'CHAP External', 'isActive': True},
        {'panelId': 500045, 'panelName': 'NSC Allocation Panel',
         'panelAbbr': 'NSC-AP', 'isActive': True},
        {'panelId': 500046, 'panelName': 'Admin Panel',
         'panelAbbr': 'admin', 'isActive': True},
    ]

    def _stub_live(self, monkeypatch, roles, panels):
        from sam.integration.xras_api.client import XrasApiClient

        stub = MagicMock()
        stub.get_role_types.return_value = roles
        stub.get_panels.return_value = panels
        monkeypatch.setenv('XRAS_OUTGOING_ENABLED', '1')
        monkeypatch.setenv('XRAS_API_KEY', 'k')
        monkeypatch.setattr(XrasApiClient, 'from_environment',
                            staticmethod(lambda: stub))

    def test_agreement_reports_no_drift_and_exits_zero(self, runner,
                                                       cli_session,
                                                       monkeypatch):
        self._stub_live(monkeypatch, self.LIVE_ROLES, self.LIVE_PANELS)
        result = runner.invoke(cli, ['--format', 'json', 'xras',
                                     '--validate-vocabulary'])
        payload = json.loads(result.stdout)
        assert payload['drift'] == [] and payload['unresolved'] == []
        # The DB half: both legacy names resolve to real allocation_type rows.
        resolved = {r['name'] for r in payload['panel_authorized']['types']}
        assert resolved == {'CHAP', 'CSL'}
        assert result.exit_code == EXIT_SUCCESS

    def test_a_renamed_live_role_type_is_drift(self, runner, cli_session,
                                               monkeypatch):
        roles = [dict(self.LIVE_ROLES[0], roleType='Principal Investigator'),
                 *self.LIVE_ROLES[1:]]
        self._stub_live(monkeypatch, roles, self.LIVE_PANELS)
        result = runner.invoke(cli, ['--format', 'json', 'xras',
                                     '--validate-vocabulary'])
        payload = json.loads(result.stdout)
        assert any('roleTypeId 13' in d for d in payload['drift'])
        assert result.exit_code == EXIT_NOT_FOUND

    def test_a_missing_declared_panel_is_drift(self, runner, cli_session,
                                               monkeypatch):
        self._stub_live(monkeypatch, self.LIVE_ROLES, self.LIVE_PANELS[1:])
        result = runner.invoke(cli, ['--format', 'json', 'xras',
                                     '--validate-vocabulary'])
        payload = json.loads(result.stdout)
        assert any('500021' in d for d in payload['drift'])
        assert result.exit_code == EXIT_NOT_FOUND

    def test_unconfigured_still_runs_the_db_half(self, runner, cli_session,
                                                 monkeypatch):
        monkeypatch.delenv('XRAS_OUTGOING_ENABLED', raising=False)
        monkeypatch.delenv('XRAS_API_KEY', raising=False)
        result = runner.invoke(cli, ['--format', 'json', 'xras',
                                     '--validate-vocabulary'])
        payload = json.loads(result.stdout)
        assert payload['role_types']['live_checked'] is False
        assert payload['panels']['live_checked'] is False
        assert {r['name'] for r in payload['panel_authorized']['types']} == {
            'CHAP', 'CSL'}
        assert result.exit_code == EXIT_SUCCESS

    def test_an_unresolvable_authorized_type_fails(self, cli_session,
                                                   monkeypatch):
        from sam.queries.xras_actions import audit_vocabulary
        import sam.xras.handlers._allocations as alloc

        monkeypatch.setattr(alloc, 'PANEL_AUTHORISED_TYPES',
                            frozenset({'CHAP', 'NOSUCH'}))
        report = audit_vocabulary(cli_session)
        assert report['unresolved'] == ['NOSUCH']

    def test_an_extra_live_panel_is_informational_not_drift(self, runner,
                                                            cli_session,
                                                            monkeypatch):
        panels = [*self.LIVE_PANELS,
                  {'panelId': 500099, 'panelName': 'Brand New Panel',
                   'panelAbbr': 'BNP', 'isActive': True}]
        self._stub_live(monkeypatch, self.LIVE_ROLES, panels)
        result = runner.invoke(cli, ['--format', 'json', 'xras',
                                     '--validate-vocabulary'])
        payload = json.loads(result.stdout)
        assert payload['drift'] == []
        assert payload['panels']['extra_live'] == ['500099 (Brand New Panel)']
        assert result.exit_code == EXIT_SUCCESS
