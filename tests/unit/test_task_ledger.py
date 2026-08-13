"""The task_run ledger: claiming, reclaiming, and the portability boundary.

The concurrency tests here are the reason the ledger exists at all. Everything
else in the dispatcher assumes that exactly one process can own a
`(task_name, occurrence_key)` pair — if that is not true, a nightly prune can
run twice and an expiration mailer can email a PI twice.

The AST guard at the bottom is not decoration. CI runs SQLite and production
runs Postgres, so a stray `ON CONFLICT` would pass every test here and fail at
02:15 in a pod.
"""

import ast
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from scheduling.ledger import (
    LEASE_FACTOR,
    MIN_LEASE,
    TaskLedger,
    lease_for,
)
from system_status.models import TaskRun

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 12, 9, 7, 0)
KEY = '20260812T081500Z'
TASK = 'cleanup_status_snapshots'


@pytest.fixture
def status_engine(app, status_session):
    """The per-worker SQLite engine behind the `system_status` bind.

    Depends on `status_session` purely for its side effects: the app context
    and the per-test DELETE of every status table.
    """
    from webapp.extensions import db
    return db.engines['system_status']


@pytest.fixture
def ledger(status_engine):
    """A ledger with **genuinely independent** sessions, committing for real.

    Not a wrapper around the test's own session, and that is the whole point.
    `claim()` resolves a race by letting the loser's INSERT raise
    `IntegrityError` and rolling back — if both callers shared one session
    that rollback would discard the *winner's* row too, and the tests would
    assert the opposite of production behaviour. On Postgres it is worse
    still: the aborted transaction poisons the connection for everything
    after it, which is exactly why the real ledger takes a `session_factory`.

    Real sessions are affordable here because the status tier isolates with
    DELETE-per-test (see `status_session`), not SAVEPOINT, so committed rows
    are cleaned up by the next test rather than needing to be rolled back.
    """
    return TaskLedger(lambda: Session(status_engine))


@pytest.fixture
def rows(status_engine):
    """Read committed `task_run` rows through a fresh session.

    A separate session per call, because the ledger commits from its own and a
    long-lived reader would sit on a stale snapshot.
    """
    def _rows(**filters):
        with Session(status_engine) as s:
            q = s.query(TaskRun)
            if filters:
                q = q.filter_by(**filters)
            return q.order_by(TaskRun.task_run_id).all()
    return _rows


# ------------------------------------------------------------------- leases

class TestLease:

    def test_defaults_to_the_floor(self):
        assert lease_for(None) == MIN_LEASE

    def test_short_task_still_gets_the_floor(self):
        assert lease_for(timedelta(seconds=5)) == MIN_LEASE

    def test_long_task_scales(self):
        assert lease_for(timedelta(minutes=30)) == LEASE_FACTOR * timedelta(minutes=30)

    def test_floor_is_generous_on_purpose(self):
        """Stealing a live run is worse than waiting out a dead one."""
        assert MIN_LEASE >= timedelta(minutes=10)


# ------------------------------------------------------------ primitive A

class TestClaim:

    def test_first_claim_wins(self, ledger, rows):
        run_id = ledger.claim(TASK, KEY, now=NOW, runner_id='pod-a')
        assert run_id is not None

        row, = rows()
        assert row.task_run_id == run_id
        assert row.state == 'running'
        assert row.trigger_type == 'schedule'
        assert row.attempt == 1
        assert row.runner_id == 'pod-a'
        assert row.claimed_at == NOW and row.heartbeat_at == NOW
        assert row.finished_at is None

    def test_second_claim_loses_and_leaves_one_row(self, ledger, rows):
        """The whole design in one assertion."""
        first = ledger.claim(TASK, KEY, now=NOW, runner_id='pod-a')
        second = ledger.claim(TASK, KEY, now=NOW + timedelta(minutes=1),
                              runner_id='pod-b')

        assert first is not None
        assert second is None, 'two dispatchers must not both own a slot'
        assert len(rows(task_name=TASK, occurrence_key=KEY)) == 1

    def test_the_loser_does_not_disturb_the_winner(self, ledger, rows):
        first = ledger.claim(TASK, KEY, now=NOW, runner_id='pod-a')
        ledger.claim(TASK, KEY, now=NOW + timedelta(minutes=1), runner_id='pod-b')

        row, = rows()
        assert row.task_run_id == first
        assert row.runner_id == 'pod-a'
        assert row.attempt == 1

    def test_a_different_occurrence_is_a_different_slot(self, ledger):
        assert ledger.claim(TASK, KEY, now=NOW) is not None
        assert ledger.claim(TASK, '20260813T081500Z', now=NOW) is not None

    def test_a_different_task_is_a_different_slot(self, ledger):
        assert ledger.claim(TASK, KEY, now=NOW) is not None
        assert ledger.claim('some_other_task', KEY, now=NOW) is not None

    def test_manual_trigger_is_recorded(self, ledger, rows):
        run_id = ledger.claim(TASK, 'M20260812T090000Z', now=NOW,
                              trigger='manual')
        assert rows()[0].trigger_type == 'manual'


# ------------------------------------------------------------ primitive B

class TestReclaim:

    def test_a_fresh_run_is_not_reclaimable(self, ledger):
        ledger.claim(TASK, KEY, now=NOW, runner_id='pod-a')
        stolen = ledger.reclaim_stale(TASK, KEY, now=NOW + timedelta(minutes=1),
                                      lease=MIN_LEASE, runner_id='pod-b')
        assert stolen is None, 'a heartbeating run must never be stolen'

    def test_a_stale_run_is_reclaimed_and_attempt_increments(self, ledger, rows):
        run_id = ledger.claim(TASK, KEY, now=NOW, runner_id='pod-a')
        later = NOW + MIN_LEASE + timedelta(minutes=1)

        stolen = ledger.reclaim_stale(TASK, KEY, now=later, lease=MIN_LEASE,
                                      runner_id='pod-b')

        assert stolen == run_id, 'reclaim must reuse the row, not add one'
        row, = rows()
        assert row.attempt == 2
        assert row.runner_id == 'pod-b'
        assert row.claimed_at == later and row.heartbeat_at == later
        assert row.state == 'running'

    def test_reclaim_clears_a_stale_finished_at(self, ledger, rows, status_engine):
        """`finished_at=None` in the reclaim is defensive, and stays tested.

        No current path leaves a `running` row with `finished_at` set, so this
        forces the state directly. It guards the retry hook in § 4.5, which
        would let primitive B also match `state='failed'` — at which point an
        inherited `finished_at` would make a live run look already over.
        """
        run_id = ledger.claim(TASK, KEY, now=NOW)
        with Session(status_engine) as s:
            s.get(TaskRun, run_id).finished_at = NOW
            s.commit()

        ledger.reclaim_stale(TASK, KEY, now=NOW + MIN_LEASE + timedelta(minutes=1),
                             lease=MIN_LEASE)

        assert rows()[0].finished_at is None

    def test_a_heartbeat_defends_against_reclaim(self, ledger):
        run_id = ledger.claim(TASK, KEY, now=NOW, runner_id='pod-a')
        later = NOW + MIN_LEASE + timedelta(minutes=1)
        ledger.heartbeat(run_id, now=later)

        stolen = ledger.reclaim_stale(TASK, KEY, now=later + timedelta(minutes=1),
                                      lease=MIN_LEASE, runner_id='pod-b')
        assert stolen is None

    def test_a_finished_run_is_not_reclaimable(self, ledger):
        run_id = ledger.claim(TASK, KEY, now=NOW)
        ledger.finish(run_id, state='succeeded', now=NOW + timedelta(seconds=5))

        stolen = ledger.reclaim_stale(TASK, KEY, now=NOW + timedelta(days=1),
                                      lease=MIN_LEASE)
        assert stolen is None, 'only `running` rows are reclaimable'

    def test_reclaim_of_an_unknown_slot_is_none(self, ledger):
        assert ledger.reclaim_stale(TASK, KEY, now=NOW, lease=MIN_LEASE) is None


# ---------------------------------------------------------------- lifecycle

class TestFinish:

    @pytest.mark.parametrize('state', ['succeeded', 'partial', 'failed'])
    def test_records_the_outcome(self, ledger, rows, state):
        run_id = ledger.claim(TASK, KEY, now=NOW)
        end = NOW + timedelta(seconds=42)

        ledger.finish(run_id, state=state, now=end, duration_ms=42000,
                      detail={'deleted': {'derecho_status': 3}})

        row, = rows()
        assert row.state == state
        assert row.finished_at == end
        assert row.duration_ms == 42000
        assert '"deleted"' in row.detail

    def test_detail_round_trips_through_get(self, ledger):
        run_id = ledger.claim(TASK, KEY, now=NOW)
        ledger.finish(run_id, state='succeeded', now=NOW,
                      detail={'deleted': {'queue_status': 7}, 'pruned': 2})

        got = ledger.get(TASK, KEY)
        assert got['detail'] == {'deleted': {'queue_status': 7}, 'pruned': 2}

    def test_unserializable_detail_still_writes_a_row(self, ledger):
        """Losing the detail's shape beats losing the record of the run."""
        run_id = ledger.claim(TASK, KEY, now=NOW)
        ledger.finish(run_id, state='succeeded', now=NOW,
                      detail={'when': datetime(2026, 1, 1)})
        assert ledger.get(TASK, KEY)['state'] == 'succeeded'

    def test_oversized_detail_is_truncated_not_raised(self, ledger):
        run_id = ledger.claim(TASK, KEY, now=NOW)
        ledger.finish(run_id, state='failed', now=NOW,
                      detail={'traceback': 'x' * 200_000})
        assert ledger.get(TASK, KEY)['state'] == 'failed'


class TestRecordSkip:

    def test_writes_a_terminal_row(self, ledger, rows):
        ledger.record_skip(TASK, KEY, now=NOW,
                           detail={'reason': 'misfire', 'late_by_s': 28800})
        row, = rows()
        assert row.state == 'skipped'
        assert row.finished_at == NOW
        assert row.duration_ms == 0

    def test_a_skip_cannot_displace_a_real_run(self, ledger, rows):
        ledger.claim(TASK, KEY, now=NOW, runner_id='pod-a')
        assert ledger.record_skip(TASK, KEY, now=NOW) is None
        row, = rows()
        assert row.state == 'running'

    def test_a_real_run_cannot_displace_a_skip(self, ledger):
        """Symmetry: the slot is the slot, whoever got there first."""
        ledger.record_skip(TASK, KEY, now=NOW, detail={'reason': 'disabled'})
        assert ledger.claim(TASK, KEY, now=NOW) is None


# -------------------------------------------------------------------- reads

class TestReads:

    def test_get_returns_none_for_an_unknown_slot(self, ledger):
        assert ledger.get(TASK, KEY) is None

    def test_get_returns_a_plain_dict_not_an_orm_row(self, ledger):
        ledger.claim(TASK, KEY, now=NOW)
        got = ledger.get(TASK, KEY)
        assert isinstance(got, dict)
        assert got['task_name'] == TASK and got['occurrence_key'] == KEY

    def test_latest_picks_the_newest_claim(self, ledger):
        ledger.claim(TASK, '20260810T081500Z', now=NOW - timedelta(days=2))
        ledger.claim(TASK, '20260812T081500Z', now=NOW)
        ledger.claim(TASK, '20260811T081500Z', now=NOW - timedelta(days=1))

        assert ledger.latest(TASK)['occurrence_key'] == '20260812T081500Z'

    def test_latest_is_none_for_an_unknown_task(self, ledger):
        assert ledger.latest('never_registered') is None

    def test_history_is_newest_first_and_honours_limit(self, ledger):
        for i in range(5):
            ledger.claim(TASK, f'2026081{i}T081500Z', now=NOW + timedelta(days=i))

        rows = ledger.history(limit=3)
        assert len(rows) == 3
        assert [r['occurrence_key'] for r in rows] == [
            '20260814T081500Z', '20260813T081500Z', '20260812T081500Z']

    def test_history_filters_by_task(self, ledger):
        ledger.claim(TASK, KEY, now=NOW)
        ledger.claim('other_task', KEY, now=NOW)

        rows = ledger.history(task_name=TASK)
        assert len(rows) == 1 and rows[0]['task_name'] == TASK

    def test_stale_running_finds_only_expired_rows(self, ledger):
        ledger.claim(TASK, KEY, now=NOW - timedelta(hours=3))          # stale
        ledger.claim(TASK, '20260812T091500Z', now=NOW)                # fresh
        done = ledger.claim(TASK, '20260812T101500Z', now=NOW - timedelta(hours=3))
        ledger.finish(done, state='succeeded', now=NOW)                # terminal

        stale = ledger.stale_running(now=NOW, lease=MIN_LEASE)
        assert [r['occurrence_key'] for r in stale] == [KEY]


# -------------------------------------------------------------------- prune

class TestPrune:

    def test_removes_old_finished_rows(self, ledger, rows):
        old = ledger.claim(TASK, '20250101T081500Z', now=NOW - timedelta(days=400))
        ledger.finish(old, state='succeeded', now=NOW - timedelta(days=400))
        recent = ledger.claim(TASK, KEY, now=NOW)
        ledger.finish(recent, state='succeeded', now=NOW)

        deleted = ledger.prune(older_than=NOW - timedelta(days=180))

        assert deleted == 1
        assert len(rows()) == 1

    def test_never_deletes_a_running_row(self, ledger, rows):
        """The pruning task is itself a running row at the moment it prunes."""
        ledger.claim(TASK, KEY, now=NOW - timedelta(days=400))

        deleted = ledger.prune(older_than=NOW - timedelta(days=180))

        assert deleted == 0
        assert len(rows()) == 1

    def test_a_task_pruning_during_its_own_run_survives(self, ledger, rows):
        """The realistic scenario, end to end."""
        live = ledger.claim(TASK, KEY, now=NOW)
        ancient = ledger.claim(TASK, '20250101T081500Z',
                               now=NOW - timedelta(days=400))
        ledger.finish(ancient, state='succeeded', now=NOW - timedelta(days=400))

        ledger.prune(older_than=NOW - timedelta(days=180))

        assert [r.task_run_id for r in rows()] == [live]


# ------------------------------------------------------- portability guard

PKG = Path(__file__).resolve().parents[2] / 'src' / 'scheduling'

#: SQL that works on one of our three backends and not the others. CI is
#: SQLite, production is Postgres; this list is the gap between them.
#:
#: `RETURNING` is deliberately absent: it is an ordinary English word
#: ("a callable returning a new Session") and matching it as a bare substring
#: reports prose as a portability bug. A guard that cries wolf gets deleted.
FORBIDDEN_SQL = [
    'FOR UPDATE', 'SKIP LOCKED', 'GET_LOCK', 'pg_advisory',
    'ON CONFLICT', 'INSERT IGNORE', 'ON DUPLICATE KEY',
]


def _imports(path: Path) -> set:
    """Top-level module names imported anywhere, including inside functions."""
    tree = ast.parse(path.read_text())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split('.')[0])
    return out


class TestPortabilityBoundary:

    @pytest.mark.parametrize('fragment', FORBIDDEN_SQL)
    def test_ledger_uses_no_dialect_specific_sql(self, fragment):
        source = (PKG / 'ledger.py').read_text()
        # Strip the module docstring: it *names* these constructs to explain
        # why they are absent, and that prose must not trip its own guard.
        body = source.split('"""', 2)[-1]
        assert fragment.lower() not in body.lower(), (
            f'ledger.py mentions {fragment!r}. CI runs SQLite and production '
            f'runs Postgres — dialect-specific SQL passes every test here and '
            f'fails in the cluster.')

    def test_schedules_imports_no_sqlalchemy(self):
        imported = _imports(PKG / 'schedules.py')
        assert 'sqlalchemy' not in imported, (
            'schedules.py must stay pure: it is the one module a different '
            'persistence layer would reuse verbatim.')

    def test_schedules_imports_no_config_or_orm(self):
        imported = _imports(PKG / 'schedules.py')
        for banned in ('sqlalchemy', 'system_status', 'sam', 'config', 'flask',
                       'click'):
            assert banned not in imported, f'schedules.py imports {banned}'

    def test_schedules_is_stdlib_only(self):
        """Positive form of the rule, so a new dependency has to be deliberate."""
        allowed = {'__future__', 'dataclasses', 'datetime', 'typing', 'zoneinfo'}
        assert _imports(PKG / 'schedules.py') <= allowed

    def test_no_module_in_the_package_imports_click_or_flask(self):
        """A future daemon must be able to import the runner without the CLI."""
        for module in sorted(PKG.rglob('*.py')):
            imported = _imports(module)
            for banned in ('click', 'flask', 'rich', 'kubernetes'):
                assert banned not in imported, (
                    f'{module.relative_to(PKG)} imports {banned}; '
                    f'src/scheduling must stay presentation-free')
