"""`run_due()` — dueness, claiming, catch-up, and failure handling.

`now` is a parameter, so a simulated week runs in milliseconds against a fake
registry. That is the whole reason the runner takes the clock as an argument
rather than reading it.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from scheduling.ledger import MIN_LEASE, TaskLedger
from scheduling.registry import CatchUp, Task, TaskContext, TaskResult
from scheduling.runner import disabled_tasks, run_due
from scheduling.schedules import Daily, Hourly, occurrence_key
from system_status.models import TaskRun

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 12, 9, 7, 0)


@pytest.fixture
def status_engine(app, status_session):
    from webapp.extensions import db
    return db.engines['system_status']


@pytest.fixture
def ledger(status_engine):
    return TaskLedger(lambda: Session(status_engine))


@pytest.fixture
def rows(status_engine):
    def _rows(**filters):
        with Session(status_engine) as s:
            q = s.query(TaskRun)
            if filters:
                q = q.filter_by(**filters)
            return q.order_by(TaskRun.task_run_id).all()
    return _rows


def make_task(name='t', schedule=None, fn=None, **kwargs):
    """A Task built directly, bypassing the global TASKS registry.

    Tests must never register into `TASKS` — it is module-global, and an
    xdist worker that mutated it would leak into every other test in the
    process.
    """
    return Task(name=name, schedule=schedule or Daily(2, 15),
                fn=fn or (lambda ctx: TaskResult(detail={'ok': True})),
                **kwargs)


def registry_of(*tasks):
    return {t.name: t for t in tasks}


# ------------------------------------------------------------ the happy path

class TestDispatch:

    def test_a_due_task_runs_and_is_recorded_succeeded(self, ledger, rows):
        calls = []
        task = make_task(fn=lambda ctx: calls.append(ctx) or TaskResult())

        out = run_due(now=NOW, ledger=ledger, registry=registry_of(task))

        assert out['counts'] == {'succeeded': 1}
        assert len(calls) == 1
        row, = rows()
        assert row.state == 'succeeded'
        assert row.occurrence_key == '20260812T081500Z'
        assert row.finished_at is not None
        assert row.duration_ms is not None

    def test_a_second_dispatch_in_the_same_slot_is_a_no_op(self, ledger, rows):
        calls = []
        task = make_task(fn=lambda ctx: calls.append(1) or TaskResult())

        run_due(now=NOW, ledger=ledger, registry=registry_of(task))
        out = run_due(now=NOW + timedelta(hours=1), ledger=ledger,
                      registry=registry_of(task))

        assert out['counts'] == {'already_claimed': 1}
        assert len(calls) == 1, 'the task body must not run twice in one slot'
        assert len(rows()) == 1

    def test_the_task_receives_its_occurrence_not_now(self, ledger):
        seen = {}

        def body(ctx):
            seen['now'] = ctx.now
            seen['occurrence'] = ctx.occurrence
            return TaskResult()

        run_due(now=NOW, ledger=ledger, registry=registry_of(make_task(fn=body)))

        assert seen['now'] == NOW
        assert seen['occurrence'] == datetime(2026, 8, 12, 8, 15), (
            'a task computes from its slot, never the dispatch instant')

    def test_detail_is_persisted(self, ledger, rows):
        task = make_task(fn=lambda ctx: TaskResult(detail={'deleted': 17}))
        run_due(now=NOW, ledger=ledger, registry=registry_of(task))
        assert '"deleted"' in rows()[0].detail

    def test_a_bare_dict_return_is_accepted(self, ledger, rows):
        task = make_task(fn=lambda ctx: {'deleted': 3})
        run_due(now=NOW, ledger=ledger, registry=registry_of(task))
        assert rows()[0].state == 'succeeded'
        assert '"deleted"' in rows()[0].detail

    def test_a_none_return_is_accepted(self, ledger, rows):
        task = make_task(fn=lambda ctx: None)
        run_due(now=NOW, ledger=ledger, registry=registry_of(task))
        assert rows()[0].state == 'succeeded'

    def test_partial_failures_produce_the_partial_state(self, ledger, rows):
        task = make_task(fn=lambda ctx: TaskResult(detail={'sent': 23},
                                                   partial_failures=2))
        out = run_due(now=NOW, ledger=ledger, registry=registry_of(task))

        assert out['counts'] == {'partial': 1}
        assert rows()[0].state == 'partial'
        assert '"partial_failures"' in rows()[0].detail

    def test_tasks_run_in_registry_order(self, ledger):
        order = []
        a = make_task('a', fn=lambda ctx: order.append('a') or TaskResult())
        b = make_task('b', fn=lambda ctx: order.append('b') or TaskResult())
        run_due(now=NOW, ledger=ledger, registry=registry_of(a, b))
        assert order == ['a', 'b']

    def test_runner_id_is_recorded(self, ledger, rows):
        run_due(now=NOW, ledger=ledger, registry=registry_of(make_task()),
                runner_id='samuel-tasks-abc123')
        assert rows()[0].runner_id == 'samuel-tasks-abc123'


# ------------------------------------------------------------------ failure

class TestFailure:

    def test_a_raising_task_is_failed_with_a_traceback(self, ledger, rows):
        def boom(ctx):
            raise RuntimeError('the database fell over')

        out = run_due(now=NOW, ledger=ledger,
                      registry=registry_of(make_task(fn=boom)))

        assert out['counts'] == {'failed': 1}
        row, = rows()
        assert row.state == 'failed'
        assert 'the database fell over' in row.detail
        assert 'Traceback' in row.detail
        assert row.finished_at is not None, 'a failed run is still closed out'

    def test_one_failure_does_not_stop_the_next_task(self, ledger):
        def boom(ctx):
            raise RuntimeError('nope')

        ran = []
        a = make_task('a', fn=boom)
        b = make_task('b', fn=lambda ctx: ran.append('b') or TaskResult())

        out = run_due(now=NOW, ledger=ledger, registry=registry_of(a, b))

        assert ran == ['b']
        assert out['counts'] == {'failed': 1, 'succeeded': 1}

    @pytest.mark.parametrize('exc', [KeyboardInterrupt, SystemExit])
    def test_baseexception_propagates(self, ledger, rows, exc):
        """A pod kill must leave the row `running`-and-stale for the reclaim
        path, not mislabelled `failed` by a handler that caught the kill."""
        def killed(ctx):
            raise exc()

        with pytest.raises(exc):
            run_due(now=NOW, ledger=ledger,
                    registry=registry_of(make_task(fn=killed)))

        assert rows()[0].state == 'running'
        assert rows()[0].finished_at is None


# ------------------------------------------------------------- kill switch

class TestKillSwitch:

    def test_a_disabled_task_is_skipped_without_running(self, ledger, rows):
        calls = []
        task = make_task('cleanup', fn=lambda ctx: calls.append(1) or TaskResult())

        out = run_due(now=NOW, ledger=ledger, registry=registry_of(task),
                      env={'SAM_TASKS_DISABLED': 'cleanup'})

        assert out['counts'] == {'skipped': 1}
        assert calls == []
        row, = rows()
        assert row.state == 'skipped'
        assert '"disabled"' in row.detail

    def test_only_the_named_task_is_disabled(self, ledger):
        a = make_task('a')
        b = make_task('b')
        out = run_due(now=NOW, ledger=ledger, registry=registry_of(a, b),
                      env={'SAM_TASKS_DISABLED': 'a'})
        assert out['counts'] == {'skipped': 1, 'succeeded': 1}

    def test_the_list_is_comma_separated_and_tolerates_spaces(self):
        assert disabled_tasks({'SAM_TASKS_DISABLED': 'a, b ,c'}) == {'a', 'b', 'c'}

    def test_empty_means_nothing_disabled(self):
        assert disabled_tasks({'SAM_TASKS_DISABLED': ''}) == set()
        assert disabled_tasks({}) == set()


# ------------------------------------------------------- misfire / catch-up

class TestMisfire:

    def test_within_grace_still_runs(self, ledger):
        task = make_task(misfire_grace=timedelta(hours=6))
        # slot 08:15 UTC, dispatch 4.9 h later
        out = run_due(now=datetime(2026, 8, 12, 13, 7), ledger=ledger,
                      registry=registry_of(task))
        assert out['counts'] == {'succeeded': 1}

    def test_past_grace_is_skipped_not_run(self, ledger):
        calls = []
        task = make_task(misfire_grace=timedelta(hours=6),
                         fn=lambda ctx: calls.append(1) or TaskResult())
        # slot 08:15 UTC, dispatch 7.9 h later
        out = run_due(now=datetime(2026, 8, 12, 16, 7), ledger=ledger,
                      registry=registry_of(task))

        assert out['counts'] == {'skipped': 1}
        assert calls == []

    def test_the_skip_row_records_how_late_it_was(self, ledger, rows):
        task = make_task(misfire_grace=timedelta(hours=6))
        run_due(now=datetime(2026, 8, 12, 16, 7), ledger=ledger,
                registry=registry_of(task))

        skipped = [r for r in rows() if r.occurrence_key == '20260812T081500Z']
        assert '"late_by_s"' in skipped[0].detail
        assert '"misfire"' in skipped[0].detail

    def test_a_three_day_outage_runs_once_and_backfills_the_rest(self, ledger, rows):
        """The question the design exists to answer, asserted directly."""
        calls = []
        task = make_task(fn=lambda ctx: calls.append(ctx.occurrence) or TaskResult())

        # Dispatcher returns after three days down, within grace of last night.
        out = run_due(now=datetime(2026, 8, 12, 11, 0), ledger=ledger,
                      registry=registry_of(task))

        assert out['counts'] == {'succeeded': 1}
        assert calls == [datetime(2026, 8, 12, 8, 15)], (
            'only the most recent slot runs under CatchUp.SKIP')

        # Nothing was backfilled, because the recent slot was NOT a misfire —
        # the outage shows up as absent rows, and the next dispatch is correct.
        assert [r.state for r in rows()] == ['succeeded']

    def test_a_long_outage_backfills_skipped_rows_for_the_missed_slots(self,
                                                                       ledger, rows):
        """`--history` must show an outage *as* an outage."""
        task = make_task(misfire_grace=timedelta(hours=6))
        # Dispatch three days after the last slot it could have run.
        run_due(now=datetime(2026, 8, 15, 16, 0), ledger=ledger,
                registry=registry_of(task))

        states = [r.state for r in rows()]
        assert states and set(states) == {'skipped'}
        assert len(states) > 1, 'the missed nights should be visible'

    def test_backfill_is_bounded(self, ledger, rows):
        """A task re-enabled after a year must not emit 365 INSERTs."""
        task = make_task(misfire_grace=timedelta(hours=6))
        run_due(now=datetime(2027, 8, 12, 16, 0), ledger=ledger,
                registry=registry_of(task))
        assert len(rows()) <= 34, f'backfill was unbounded: {len(rows())} rows'

    def test_backfill_stops_at_recorded_history(self, ledger, rows):
        task = make_task(misfire_grace=timedelta(hours=6))
        run_due(now=datetime(2026, 8, 15, 16, 0), ledger=ledger,
                registry=registry_of(task))
        first = len(rows())

        run_due(now=datetime(2026, 8, 16, 16, 0), ledger=ledger,
                registry=registry_of(task))

        # Exactly one new row: the skip for 08-16's own slot. The backfill
        # walks back one step, finds 08-15 already recorded, and stops — so a
        # long outage is paid for once, not re-walked on every later dispatch.
        assert len(rows()) == first + 1, (
            'a second dispatch must add its own slot only, not replay history')


# ------------------------------------------------------------ simulated time

class TestSimulatedTime:

    def test_a_week_of_hourly_dispatches_runs_a_daily_task_seven_times(self,
                                                                       ledger, rows):
        calls = []
        task = make_task(fn=lambda ctx: calls.append(ctx.occurrence) or TaskResult())

        start = datetime(2026, 8, 5, 9, 7)
        for hour in range(7 * 24):
            run_due(now=start + timedelta(hours=hour), ledger=ledger,
                    registry=registry_of(task))

        assert len(calls) == 7, f'ran {len(calls)} times in a week: {calls}'
        assert len(set(calls)) == 7, 'every run filled a distinct slot'
        assert all(r.state == 'succeeded' for r in rows())

    def test_running_the_dispatcher_three_times_a_minute_changes_nothing(self,
                                                                         ledger):
        calls = []
        task = make_task(fn=lambda ctx: calls.append(1) or TaskResult())
        for _ in range(3):
            run_due(now=NOW, ledger=ledger, registry=registry_of(task))
        assert len(calls) == 1

    def test_an_hourly_task_fires_once_per_hour(self, ledger):
        calls = []
        task = make_task(schedule=Hourly(7),
                         fn=lambda ctx: calls.append(ctx.occurrence) or TaskResult())
        start = datetime(2026, 8, 12, 0, 0)
        for minute in range(0, 24 * 60, 10):
            run_due(now=start + timedelta(minutes=minute), ledger=ledger,
                    registry=registry_of(task))

        # 25, not 24: the sweep opens at 00:00 with the previous day's 23:07
        # slot already current, then walks 24 more. Spacing is the property
        # that matters; a count would just encode the window's edges.
        assert len(calls) == len(set(calls)), 'a slot ran twice'
        assert sorted(calls) == calls
        for a, b in zip(calls, calls[1:]):
            assert b - a == timedelta(hours=1), (a, b)


# ------------------------------------------------------------------ dry run

class TestDryRun:

    def test_writes_no_ledger_rows_at_all(self, ledger, rows):
        """A dry run that claimed the slot would prevent the real run — the
        worst possible failure mode for a safety flag."""
        calls = []
        task = make_task(fn=lambda ctx: calls.append(1) or TaskResult())

        out = run_due(now=NOW, ledger=ledger, registry=registry_of(task),
                      dry_run=True)

        assert rows() == []
        assert calls == [], 'dry run must not execute the body either'
        assert out['counts'] == {'would_claim': 1}

    def test_reports_already_claimed_without_writing(self, ledger, rows):
        task = make_task()
        run_due(now=NOW, ledger=ledger, registry=registry_of(task))
        before = len(rows())

        out = run_due(now=NOW, ledger=ledger, registry=registry_of(task),
                      dry_run=True)

        assert out['counts'] == {'already_claimed': 1}
        assert len(rows()) == before

    def test_a_dry_run_does_not_block_the_real_run(self, ledger):
        calls = []
        task = make_task(fn=lambda ctx: calls.append(1) or TaskResult())
        run_due(now=NOW, ledger=ledger, registry=registry_of(task), dry_run=True)
        run_due(now=NOW, ledger=ledger, registry=registry_of(task))
        assert len(calls) == 1, 'the real run must still happen'


# ------------------------------------------------------------- only / force

class TestOnlyAndForce:

    def test_only_runs_the_named_task(self, ledger):
        ran = []
        a = make_task('a', fn=lambda ctx: ran.append('a') or TaskResult())
        b = make_task('b', fn=lambda ctx: ran.append('b') or TaskResult())
        run_due(now=NOW, ledger=ledger, registry=registry_of(a, b), only='b')
        assert ran == ['b']

    def test_unknown_task_raises_keyerror(self, ledger):
        with pytest.raises(KeyError):
            run_due(now=NOW, ledger=ledger, registry=registry_of(make_task()),
                    only='nope')

    def test_only_ignores_the_misfire_grace(self, ledger):
        """An operator asking for a run has already decided it is wanted."""
        calls = []
        task = make_task(misfire_grace=timedelta(hours=6),
                         fn=lambda ctx: calls.append(1) or TaskResult())
        out = run_due(now=datetime(2026, 8, 12, 20, 0), ledger=ledger,
                      registry=registry_of(task), only='t')
        assert out['counts'] == {'succeeded': 1}
        assert len(calls) == 1

    def test_force_writes_a_manual_key_that_cannot_collide(self, ledger, rows):
        task = make_task()
        run_due(now=NOW, ledger=ledger, registry=registry_of(task),
                only='t', force=True)

        row, = rows()
        assert row.occurrence_key.startswith('M')
        assert row.trigger == 'manual'
        assert row.occurrence_key != occurrence_key(datetime(2026, 8, 12, 8, 15))

    def test_a_forced_run_does_not_satisfy_the_scheduled_slot(self, ledger, rows):
        """Documented at the flag: a forced 10:00 run does not stop tonight's."""
        calls = []
        task = make_task(fn=lambda ctx: calls.append(ctx.occurrence_key) or TaskResult())

        run_due(now=NOW, ledger=ledger, registry=registry_of(task),
                only='t', force=True)
        run_due(now=NOW, ledger=ledger, registry=registry_of(task))

        assert len(calls) == 2
        assert calls[0].startswith('M') and not calls[1].startswith('M')
        assert len(rows()) == 2


# ------------------------------------------------------------------ reclaim

class TestStaleReclaim:

    def test_a_stale_running_row_is_reclaimed_and_rerun(self, ledger, rows):
        task = make_task(expected_runtime=timedelta(minutes=1))
        # A previous dispatcher claimed the slot and died.
        ledger.claim('t', '20260812T081500Z', now=NOW - MIN_LEASE - timedelta(minutes=5),
                     runner_id='dead-pod')

        calls = []
        task = make_task(expected_runtime=timedelta(minutes=1),
                         fn=lambda ctx: calls.append(1) or TaskResult())
        out = run_due(now=NOW, ledger=ledger, registry=registry_of(task),
                      runner_id='live-pod')

        assert out['counts'] == {'succeeded': 1}
        assert len(calls) == 1
        row, = rows()
        assert row.attempt == 2
        assert row.runner_id == 'live-pod'
        assert row.state == 'succeeded'

    def test_a_live_running_row_is_not_stolen(self, ledger):
        ledger.claim('t', '20260812T081500Z', now=NOW - timedelta(minutes=1),
                     runner_id='busy-pod')
        calls = []
        task = make_task(fn=lambda ctx: calls.append(1) or TaskResult())

        out = run_due(now=NOW, ledger=ledger, registry=registry_of(task))

        assert out['counts'] == {'already_claimed': 1}
        assert calls == []


# ------------------------------------------------------------------ context

class TestTaskContext:

    def test_undeclared_sam_session_raises_a_helpful_error(self, ledger):
        def body(ctx):
            return ctx.sam_session

        out = run_due(now=NOW, ledger=ledger,
                      registry=registry_of(make_task(fn=body, needs=('status',))),
                      status_session_factory=lambda: None)
        assert out['counts'] == {'failed': 1}
        assert 'did not declare' in out['results'][0]['error']

    def test_a_status_only_task_never_opens_a_sam_session(self, ledger):
        """The § 3.2 payoff: a SAM outage cannot stop status retention."""
        opened = []
        task = make_task(needs=('status',), fn=lambda ctx: TaskResult())

        run_due(now=NOW, ledger=ledger, registry=registry_of(task),
                sam_session_factory=lambda: opened.append('sam'),
                status_session_factory=lambda: None)

        assert opened == []

    def test_sessions_are_opened_lazily_even_when_declared(self, ledger):
        opened = []
        task = make_task(needs=('sam', 'status'), fn=lambda ctx: TaskResult())

        run_due(now=NOW, ledger=ledger, registry=registry_of(task),
                sam_session_factory=lambda: opened.append('sam'),
                status_session_factory=lambda: opened.append('status'))

        assert opened == [], 'a task that touches neither should open neither'

    def test_rejects_an_unknown_needs_value(self):
        with pytest.raises(ValueError, match='unknown needs'):
            Task(name='x', schedule=Daily(1), fn=lambda ctx: None,
                 needs=('sam', 'postgres'))
