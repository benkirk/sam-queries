"""Click wiring for `sam-admin tasks` — modes, guards, envelopes, exit codes.

Modelled on `test_admin_xras_cli.py`. The distinctive things asserted here are
the two places this command deliberately departs from its siblings:

* it must work with **no SAM MySQL connection at all**, because its ledger is
  in `system_status` and a SAM outage must not stop status retention; and
* `--format json --run-due` is **allowed** despite being side-effecting, unlike
  `xras --recheck`. See `src/cli/README.md` § Exit Codes.
"""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from sqlalchemy.orm import Session

from cli.cmds.admin import cli
from scheduling.ledger import TaskLedger

pytestmark = pytest.mark.unit

NAME = 'cleanup_status_snapshots'


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def status_engine(app, status_session):
    from webapp.extensions import db
    return db.engines['system_status']


@pytest.fixture
def wired(status_engine, monkeypatch):
    """Point the command's status-session factory at the test SQLite bind.

    Patched at `system_status.session.create_status_engine`, the single place
    `TasksCommand` builds its engine. Nothing patches a SAM engine — these
    tests would fail if the command tried to open one, which is the point.

    Which is why every task declaring ``needs=('sam', ...)`` is switched off
    here. `--run-due` dispatches the whole registry, so any SAM-needing task
    that happens to be due would try to open the connection this file exists to
    prove is unnecessary. That used to be nobody: the two SAM tasks are monthly
    and weekly with graces well short of their periods, so both misfire on
    almost every run and record `skipped` without executing. `xras_notices` is
    hourly, and is due for most of the day.

    Derived from `needs` rather than named, so registering another SAM task
    does not silently turn this file red — and so what is asserted stays "the
    status task runs without SAM", not "these three names do not".
    """
    from scheduling.registry import TASKS
    monkeypatch.setenv('SAM_TASKS_DISABLED', ','.join(
        sorted(name for name, t in TASKS.items() if 'sam' in t.needs)))
    with patch('system_status.session.create_status_engine') as mk:
        mk.return_value = (status_engine, None)
        yield status_engine


@pytest.fixture
def ledger(status_engine):
    return TaskLedger(lambda: Session(status_engine))


def _json(result):
    return json.loads(result.output)


# --------------------------------------------------------------------- list

class TestListMode:

    def test_default_mode_is_list(self, runner, wired):
        result = runner.invoke(cli, ['--format', 'json', 'tasks'])
        assert result.exit_code == 0, result.output
        assert _json(result)['kind'] == 'task_list'

    def test_envelope_shape(self, runner, wired):
        payload = _json(runner.invoke(cli, ['--format', 'json', 'tasks', '--list']))
        assert set(payload) >= {'kind', 'now', 'count', 'disabled', 'tasks'}
        assert payload['count'] == len(payload['tasks'])

    def test_the_real_task_is_listed(self, runner, wired):
        payload = _json(runner.invoke(cli, ['--format', 'json', 'tasks']))
        names = [t['name'] for t in payload['tasks']]
        assert NAME in names

        entry = next(t for t in payload['tasks'] if t['name'] == NAME)
        assert entry['schedule'] == 'daily at 02:15 America/Denver'
        assert entry['needs'] == ['status']
        assert entry['enabled'] is True
        assert entry['last_run'] is None

    def test_next_occurrence_is_reported_for_display(self, runner, wired):
        payload = _json(runner.invoke(cli, ['--format', 'json', 'tasks']))
        entry = next(t for t in payload['tasks'] if t['name'] == NAME)
        assert entry['next_occurrence'] is not None

    def test_the_kill_switch_shows_up(self, runner, wired, monkeypatch):
        monkeypatch.setenv('SAM_TASKS_DISABLED', NAME)
        payload = _json(runner.invoke(cli, ['--format', 'json', 'tasks']))
        assert payload['disabled'] == [NAME]
        entry = next(t for t in payload['tasks'] if t['name'] == NAME)
        assert entry['enabled'] is False

    def test_last_run_appears_once_there_is_one(self, runner, wired, ledger):
        run_id = ledger.claim(NAME, '20260812T081500Z',
                              now=datetime(2026, 8, 12, 9, 0))
        ledger.finish(run_id, state='succeeded',
                      now=datetime(2026, 8, 12, 9, 1), duration_ms=1234)

        payload = _json(runner.invoke(cli, ['--format', 'json', 'tasks']))
        entry = next(t for t in payload['tasks'] if t['name'] == NAME)
        assert entry['last_run']['state'] == 'succeeded'
        assert entry['last_run']['duration_ms'] == 1234

    def test_rich_mode_renders(self, runner, wired):
        result = runner.invoke(cli, ['tasks', '--list'])
        assert result.exit_code == 0, result.output
        assert 'Scheduled tasks' in result.output


# ------------------------------------------------------------------ history

class TestHistoryMode:

    def test_empty_history_is_success_not_not_found(self, runner, wired):
        result = runner.invoke(cli, ['--format', 'json', 'tasks', '--history'])
        assert result.exit_code == 0, result.output
        payload = _json(result)
        assert payload['kind'] == 'task_history'
        assert payload['runs'] == []

    def test_lists_runs_newest_first(self, runner, wired, ledger):
        for day in (10, 11, 12):
            rid = ledger.claim(NAME, f'202608{day}T081500Z',
                               now=datetime(2026, 8, day, 9, 0))
            ledger.finish(rid, state='succeeded', now=datetime(2026, 8, day, 9, 1))

        payload = _json(runner.invoke(
            cli, ['--format', 'json', 'tasks', '--history']))
        assert [r['occurrence'] for r in payload['runs']] == [
            '20260812T081500Z', '20260811T081500Z', '20260810T081500Z']

    def test_limit_is_honoured(self, runner, wired, ledger):
        for day in (10, 11, 12):
            ledger.claim(NAME, f'202608{day}T081500Z',
                         now=datetime(2026, 8, day, 9, 0))
        payload = _json(runner.invoke(
            cli, ['--format', 'json', 'tasks', '--history', '--limit', '2']))
        assert payload['count'] == 2

    def test_unknown_task_is_exit_1(self, runner, wired):
        result = runner.invoke(
            cli, ['--format', 'json', 'tasks', '--history', '--task', 'nope'])
        assert result.exit_code == 1
        payload = _json(result)
        assert payload['error'] == 'not_found'
        assert payload['task'] == 'nope'


# ----------------------------------------------------------------- dispatch

class TestDispatch:

    def test_dry_run_writes_no_rows(self, runner, wired, ledger):
        result = runner.invoke(
            cli, ['--format', 'json', 'tasks', '--run-due', '--dry-run'])
        assert result.exit_code == 0, result.output
        payload = _json(result)
        assert payload['kind'] == 'task_dispatch'
        assert payload['dry_run'] is True
        assert ledger.history() == [], 'a dry run must claim nothing'

    def test_run_one_task_by_name(self, runner, wired, ledger):
        result = runner.invoke(
            cli, ['--format', 'json', 'tasks', '--run', NAME])
        assert result.exit_code == 0, result.output
        payload = _json(result)
        assert payload['results'][0]['outcome'] == 'succeeded'
        assert len(ledger.history()) == 1

    def test_force_uses_a_manual_key(self, runner, wired, ledger):
        runner.invoke(cli, ['--format', 'json', 'tasks', '--run', NAME, '--force'])
        # NB: ledger rows carry the column names; the builder is what renames
        # them to `task`/`occurrence` for the wire.
        run, = ledger.history()
        assert run['occurrence_key'].startswith('M')
        assert run['trigger'] == 'manual'

    def test_unknown_task_is_exit_1(self, runner, wired):
        result = runner.invoke(cli, ['--format', 'json', 'tasks', '--run', 'nope'])
        assert result.exit_code == 1
        assert _json(result)['error'] == 'not_found'

    def test_a_failing_task_exits_2(self, runner, wired):
        """A nonzero exit is what marks the k8s Job Failed."""
        # Patched where it is *used*: cleanup_status.py does
        # `from system_status.retention import cleanup_old_data` at import, so
        # patching the source module would rebind a name nobody reads.
        with patch('scheduling.tasks.cleanup_status.cleanup_old_data',
                   side_effect=RuntimeError('boom')):
            result = runner.invoke(
                cli, ['--format', 'json', 'tasks', '--run', NAME])
        assert result.exit_code == 2, result.output
        assert _json(result)['results'][0]['outcome'] == 'failed'

    def test_rich_dispatch_renders(self, runner, wired):
        result = runner.invoke(cli, ['tasks', '--run-due', '--dry-run'])
        assert result.exit_code == 0, result.output


# -------------------------------------------------------------------- guards

class TestFlagGuards:

    @pytest.mark.parametrize('flags', [
        ['--list', '--run-due'],
        ['--list', '--history'],
        ['--run-due', '--history'],
        ['--run-due', '--run', NAME],
    ])
    def test_modes_are_mutually_exclusive(self, runner, wired, flags):
        result = runner.invoke(cli, ['tasks', *flags])
        assert result.exit_code == 2
        assert 'mutually exclusive' in result.output

    def test_dry_run_requires_a_dispatch_mode(self, runner, wired):
        result = runner.invoke(cli, ['tasks', '--dry-run'])
        assert result.exit_code == 2
        assert '--dry-run requires' in result.output

    def test_force_requires_run(self, runner, wired):
        result = runner.invoke(cli, ['tasks', '--run-due', '--force'])
        assert result.exit_code == 2
        assert '--force requires --run' in result.output

    def test_task_filter_requires_history(self, runner, wired):
        result = runner.invoke(cli, ['tasks', '--task', NAME])
        assert result.exit_code == 2
        assert 'require --history' in result.output

    @pytest.mark.parametrize('flags', [
        ['--occurrence', '2026-11-23T09:00'],
        ['--run-due', '--occurrence', '2026-11-23T09:00'],
        ['--run', NAME, '--occurrence', '2026-11-23T09:00'],   # no --force
    ])
    def test_occurrence_requires_run_and_force(self, runner, wired, flags):
        """Without --force the key is a real scheduled occurrence, so a replay
        would claim the slot it was only meant to rehearse."""
        result = runner.invoke(cli, ['tasks', *flags])
        assert result.exit_code == 2
        assert '--occurrence requires --run and --force' in result.output


class TestOccurrenceReplay:
    """`--run <task> --force --occurrence <iso>` — rehearse a future slot.

    A task computes everything from `ctx.occurrence`, so this is how you ask
    "what would the Monday five weeks out have sent?" without waiting five
    weeks or temporarily editing a window constant. Phase V of the expiration
    rollout is the reason it exists.
    """

    def test_the_task_runs_at_the_given_occurrence(self, runner, wired, ledger):
        result = runner.invoke(cli, ['--format', 'json', 'tasks',
                                     '--run', NAME, '--force',
                                     '--occurrence', '2026-11-23T09:00'])
        assert result.exit_code == 0
        run, = _json(result)['results']
        assert run['occurrence'] == '2026-11-23T09:00:00'

    def test_the_ledger_row_carries_a_manual_key(self, runner, wired, ledger):
        runner.invoke(cli, ['--format', 'json', 'tasks', '--run', NAME,
                            '--force', '--occurrence', '2026-11-23T09:00'])
        row = ledger.history(limit=1)[0]
        assert row['occurrence_key'] == 'M20261123T090000Z'
        assert row['trigger'] == 'manual'

    @pytest.mark.parametrize('raw,expected', [
        ('2026-11-23T09:00',        '2026-11-23T09:00:00'),
        ('2026-11-23 09:00:00',     '2026-11-23T09:00:00'),
        ('2026-11-23T09:00:00Z',    '2026-11-23T09:00:00'),
        # An offset is converted and dropped: occurrence keys are naive UTC,
        # so keeping the wall time would key the row wrong AND select the
        # wrong window.
        ('2026-11-23T02:00:00-07:00', '2026-11-23T09:00:00'),
    ])
    def test_iso_forms_land_on_naive_utc(self, runner, wired, raw, expected):
        result = runner.invoke(cli, ['--format', 'json', 'tasks', '--run',
                                     NAME, '--force', '--occurrence', raw])
        assert result.exit_code == 0
        assert _json(result)['results'][0]['occurrence'] == expected

    def test_an_unparseable_occurrence_is_an_error_not_a_silent_now(
            self, runner, wired, ledger):
        """Falling back to the wall clock would run the task against a window
        the operator did not ask for and report success."""
        before = len(ledger.history(limit=50))
        result = runner.invoke(cli, ['--format', 'json', 'tasks', '--run',
                                     NAME, '--force',
                                     '--occurrence', 'next monday'])
        assert result.exit_code == 2
        assert _json(result)['error'] == 'bad_occurrence'
        assert len(ledger.history(limit=50)) == before, 'nothing ran'

    def test_the_rich_path_explains_the_expected_format(self, runner, wired):
        result = runner.invoke(cli, ['tasks', '--run', NAME, '--force',
                                     '--occurrence', 'next monday'])
        assert result.exit_code == 2
        assert 'ISO-8601' in result.output


# ------------------------------------------------- the two deliberate quirks

class TestJsonWriteCarveOut:
    """`--format json --run-due` is allowed, unlike `xras --recheck`.

    The `json_unsupported_for_writes` rule exists to stop someone accidentally
    writing while scripting a *report*. Here the side effect IS the command,
    and JSON on stdout is exactly what a log-scraped CronJob should emit.
    """

    def test_json_plus_run_due_is_not_rejected(self, runner, wired):
        result = runner.invoke(cli, ['--format', 'json', 'tasks', '--run-due'])
        assert result.exit_code == 0, result.output
        assert 'json_unsupported_for_writes' not in result.output

    def test_json_plus_run_is_not_rejected(self, runner, wired):
        result = runner.invoke(cli, ['--format', 'json', 'tasks', '--run', NAME])
        assert result.exit_code == 0, result.output
        assert 'json_unsupported_for_writes' not in result.output

    def test_stdout_is_pure_json(self, runner, wired):
        """The CronJob's output is log-scraped; one stray line breaks it."""
        result = runner.invoke(cli, ['--format', 'json', 'tasks', '--run-due'])
        json.loads(result.output)       # raises if anything else was printed


class TestNoSamConnection:
    """The § 3.2 payoff, asserted rather than assumed."""

    def test_listing_never_opens_a_sam_session(self, runner, wired):
        with patch('sam.session.create_sam_engine') as mk:
            result = runner.invoke(cli, ['--format', 'json', 'tasks', '--list'])
        assert result.exit_code == 0, result.output
        mk.assert_not_called()

    def test_dispatching_a_status_only_task_never_opens_a_sam_session(self, runner,
                                                                      wired):
        with patch('sam.session.create_sam_engine') as mk:
            result = runner.invoke(cli, ['--format', 'json', 'tasks', '--run-due'])
        assert result.exit_code == 0, result.output
        mk.assert_not_called(), (
            'a SAM outage must not be able to stop system_status retention')


# ---------------------------------------------------------------------- help

class TestHelp:

    def test_every_mode_flag_is_documented(self, runner):
        out = runner.invoke(cli, ['tasks', '--help']).output
        for flag in ('--list', '--run-due', '--run', '--history', '--task',
                     '--limit', '--dry-run', '--force'):
            assert flag in out, flag

    def test_help_states_the_surprising_force_semantics(self, runner):
        """A forced run does not satisfy the scheduled slot — say so."""
        out = ' '.join(runner.invoke(cli, ['tasks', '--help']).output.split())
        assert 'does NOT satisfy the scheduled slot' in out

    def test_tasks_appears_in_the_admin_help(self, runner):
        assert 'tasks' in runner.invoke(cli, ['--help']).output
