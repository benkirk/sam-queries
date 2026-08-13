"""`system_status.queries.task_runs` — the card counts and the facet rollups.

Uses `status_session` rather than `session`: `task_run` lives on the
`system_status` bind, which is a per-worker SQLite tempfile with per-test
DELETE isolation. Rows are built by a module-private helper rather than a
`tests/factories/` builder, because that package targets the SAM bind only —
the convention every other status-tier test module follows (see
`test_status_retention.py`, `test_user_proj_queues_timeseries.py`).
"""

import itertools
from datetime import datetime, timedelta

import pytest

from system_status.models.task_run import TaskRun
from system_status.queries.task_runs import (
    CARD_STATES,
    count_recent_task_runs,
    count_stale_running,
    facet_task_runs,
    get_recent_task_runs,
    last_dispatch,
    observed_task_names,
    summarize_task_runs,
)
from system_status.timeutil import utcnow_naive

#: True when local wall-clock and UTC coincide, which makes the naive-UTC vs
#: naive-local distinction unobservable. The discriminating tests below skip
#: there rather than passing vacuously.
_TZ_IS_UTC = abs((datetime.now() - utcnow_naive()).total_seconds()) < 60

#: Keeps `(task_name, occurrence_key)` unique when two rows land in the same
#: second — that UNIQUE constraint is the ledger's mutual-exclusion lock, so
#: colliding on it in a fixture is a test bug, not a schema complaint.
#:
#: A plain module counter is safe here, unlike in `tests/factories/`: the
#: `system_status` bind is a per-worker SQLite tempfile, so xdist workers
#: cannot see each other's rows.
_KEY_SEQ = itertools.count(1)


def _make_task_run(session, *, task_name='cleanup_status_snapshots',
                   state='succeeded', trigger_type='schedule',
                   age=None, claimed_at=None, heartbeat_age=None,
                   occurrence_key=None,
                   runner_id='samuel-tasks-00000000-aaaaa', attempt=1,
                   duration_ms=1234, detail=None):
    """One ledger row. `age` is measured back from **naive UTC**.

    `heartbeat_age` defaults to `age`, which is what a finished run looks
    like; pass it explicitly to build a stale `running` row.
    """
    now = utcnow_naive()
    if claimed_at is None:
        claimed_at = now - (age or timedelta(0))
    heartbeat_at = now - heartbeat_age if heartbeat_age is not None \
        else claimed_at
    row = TaskRun(
        task_name=task_name,
        occurrence_key=occurrence_key or (
            f"{claimed_at.strftime('%Y%m%dT%H%M')}{next(_KEY_SEQ):04d}Z"),
        state=state,
        trigger_type=trigger_type,
        attempt=attempt,
        claimed_at=claimed_at,
        heartbeat_at=heartbeat_at,
        finished_at=None if state == 'running' else claimed_at,
        duration_ms=None if state == 'running' else duration_ms,
        runner_id=runner_id,
        detail=detail,
    )
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def runs(status_session):
    """Five rows inside the default 24h window, deliberately lopsided."""
    mk = _make_task_run
    mk(status_session, state='succeeded', age=timedelta(hours=1))
    mk(status_session, state='succeeded', age=timedelta(hours=2))
    mk(status_session, state='failed', age=timedelta(hours=3))
    mk(status_session, state='skipped', trigger_type='manual',
       task_name='other_task', age=timedelta(hours=4))
    mk(status_session, state='partial', trigger_type='manual',
       task_name='other_task', age=timedelta(hours=5))
    status_session.flush()
    return status_session


class TestTheWindowIsUtc:
    """TRAP 1 — `notification_log` is naive-Mountain, `task_run` is naive-UTC.

    Copying the notifications module's `datetime.now()` verbatim would shift
    every count here by 6-7 hours.
    """

    def test_the_window_start_derives_from_utcnow(self, status_session):
        summary = summarize_task_runs(status_session, window_hours=24)
        expected = utcnow_naive() - timedelta(hours=24)
        drift = abs((summary['window_start'] - expected).total_seconds())
        assert drift < 60, (
            f'window_start is {summary["window_start"]}, expected ~{expected} '
            f'— it is being computed from the local clock, not utcnow_naive')

    @pytest.mark.skipif(_TZ_IS_UTC,
                        reason='local time == UTC here, so the bug is '
                               'unobservable; the assertion above still holds')
    def test_a_row_just_outside_the_utc_window_is_excluded(self,
                                                           status_session):
        """The discriminating case.

        A row 26h old is outside a correct 24h UTC window. Under the bug the
        cutoff sits ~6-7h further back, so it would be counted.
        """
        _make_task_run(status_session, state='failed',
                       age=timedelta(hours=26))
        _make_task_run(status_session, state='succeeded',
                       age=timedelta(hours=1))

        summary = summarize_task_runs(status_session, window_hours=24)
        assert summary['failed'] == 0, \
            'a 26h-old row landed inside a 24h window — the window is being ' \
            'computed from local time, which sits hours behind UTC'
        assert summary['succeeded'] == 1


class TestSummarize:

    def test_every_card_state_is_present_and_zeroed(self, status_session):
        summary = summarize_task_runs(status_session)
        for state in CARD_STATES:
            assert summary[state] == 0, f'{state} missing from the summary'

    def test_counts_by_state(self, runs):
        summary = summarize_task_runs(runs)
        assert summary['succeeded'] == 2
        assert summary['failed'] == 1
        assert summary['skipped'] == 1
        assert summary['partial'] == 1
        assert summary['total'] == 5

    def test_running_is_not_a_card_row(self):
        """`running` is deliberately absent — a run in flight is not news.
        A run in flight past its lease is, and that is stale_running."""
        assert 'running' not in CARD_STATES

    def test_the_window_excludes_older_rows(self, status_session):
        _make_task_run(status_session, state='failed', age=timedelta(days=3))
        summary = summarize_task_runs(status_session, window_hours=24)
        assert summary['failed'] == 0
        assert summary['total'] == 0

    def test_window_hours_is_echoed(self, status_session):
        assert summarize_task_runs(
            status_session, window_hours=6)['window_hours'] == 6


class TestStaleRunning:

    def test_a_fresh_running_row_is_not_stale(self, status_session):
        _make_task_run(status_session, state='running',
                       heartbeat_age=timedelta(seconds=30))
        assert count_stale_running(status_session) == 0

    def test_a_running_row_past_the_lease_is_stale(self, status_session):
        _make_task_run(status_session, state='running',
                       heartbeat_age=timedelta(hours=2))
        assert count_stale_running(status_session) == 1

    def test_a_finished_row_is_never_stale(self, status_session):
        _make_task_run(status_session, state='failed',
                       age=timedelta(days=5))
        assert count_stale_running(status_session) == 0

    def test_it_is_deliberately_not_windowed(self, status_session):
        """A run stuck three days ago matters more than one stuck an hour
        ago; windowing would let the oldest breakage age off the card."""
        _make_task_run(status_session, state='running',
                       age=timedelta(days=30),
                       heartbeat_age=timedelta(days=30))
        assert count_stale_running(status_session) == 1
        # ...and the summary carries it even though the window is 24h.
        assert summarize_task_runs(
            status_session, window_hours=24)['stale_running'] == 1

    def test_the_default_lease_comes_from_the_dispatcher(self, status_session):
        """The card and the reclaim rule must agree on what 'stale' means."""
        from scheduling.ledger import MIN_LEASE

        just_inside = MIN_LEASE - timedelta(seconds=60)
        _make_task_run(status_session, state='running',
                       heartbeat_age=just_inside)
        assert count_stale_running(status_session) == 0

        _make_task_run(status_session, state='running',
                       heartbeat_age=MIN_LEASE + timedelta(seconds=60))
        assert count_stale_running(status_session) == 1


class TestLastDispatch:

    def test_none_when_nothing_has_ever_run(self, status_session):
        assert last_dispatch(status_session) is None

    def test_it_is_the_newest_claim_across_all_tasks(self, runs):
        newest = last_dispatch(runs)
        assert (utcnow_naive() - newest) < timedelta(hours=1, minutes=5)

    def test_it_is_unwindowed(self, status_session):
        """Otherwise a long-dead dispatcher reads as 'never ran'."""
        _make_task_run(status_session, age=timedelta(days=90))
        assert last_dispatch(status_session) is not None

    def test_the_summary_carries_the_age_as_a_timedelta(self, status_session):
        """`fmt_ago` takes a timedelta, and the subtraction must happen
        against utcnow_naive — a template differencing this naive-UTC column
        against the local clock would report an hourly dispatcher as hours
        stale."""
        _make_task_run(status_session, age=timedelta(hours=2))
        age = summarize_task_runs(status_session)['last_dispatch_age']
        assert isinstance(age, timedelta)
        assert timedelta(hours=1, minutes=55) < age < timedelta(hours=2, minutes=5)

    def test_the_age_is_none_when_nothing_has_run(self, status_session):
        assert summarize_task_runs(status_session)['last_dispatch_age'] is None


class TestFacetsExcludeTheirOwnDimension:

    def test_the_state_facet_ignores_the_state_filter(self, runs):
        facet = facet_task_runs(runs, 'state', states=['succeeded'])
        assert facet['succeeded'] == 2
        assert facet['failed'] == 1, \
            'picking "succeeded" must not zero the other state chips'

    def test_the_state_facet_still_honours_other_filters(self, runs):
        facet = facet_task_runs(runs, 'state', states=['succeeded'],
                                task_names=['other_task'])
        assert facet.get('succeeded') is None
        assert facet == {'partial': 1, 'skipped': 1}

    def test_the_task_name_facet_ignores_its_own_filter(self, runs):
        facet = facet_task_runs(runs, 'task_name',
                                task_names=['other_task'])
        assert facet == {'cleanup_status_snapshots': 3, 'other_task': 2}

    def test_the_trigger_facet_ignores_its_own_filter(self, runs):
        facet = facet_task_runs(runs, 'trigger_type', triggers=['manual'])
        assert facet == {'manual': 2, 'schedule': 3}

    def test_an_unknown_dimension_raises_with_the_vocabulary(self,
                                                             status_session):
        with pytest.raises(ValueError,
                           match='task_name, state, trigger_type'):
            facet_task_runs(status_session, 'runner_id')


class TestListing:

    def test_rows_come_back_newest_first(self, runs):
        rows = get_recent_task_runs(runs)
        claims = [r.claimed_at for r in rows]
        assert claims == sorted(claims, reverse=True)

    def test_filters_narrow_the_listing(self, runs):
        rows = get_recent_task_runs(runs, states=['succeeded'])
        assert len(rows) == 2
        assert {r.state for r in rows} == {'succeeded'}

    def test_search_matches_task_name(self, runs):
        assert len(get_recent_task_runs(runs, search='other')) == 2

    def test_search_matches_runner_id(self, status_session):
        _make_task_run(status_session, runner_id='samuel-tasks-9999-zzzzz')
        assert len(get_recent_task_runs(status_session, search='zzzzz')) == 1

    def test_count_and_listing_agree(self, runs):
        filters = {'states': ['succeeded', 'failed']}
        assert count_recent_task_runs(runs, **filters) == \
            len(get_recent_task_runs(runs, limit=None, **filters))

    def test_limit_and_offset_are_disjoint(self, runs):
        first = get_recent_task_runs(runs, limit=2, offset=0)
        second = get_recent_task_runs(runs, limit=2, offset=2)
        assert not {r.task_run_id for r in first} & \
            {r.task_run_id for r in second}


class TestObservedTaskNames:

    def test_it_reads_the_table_not_the_registry(self, runs):
        """A task deleted from the registry still has history worth
        filtering to."""
        assert observed_task_names(runs) == ['cleanup_status_snapshots',
                                             'other_task']

    def test_empty_when_nothing_has_run(self, status_session):
        assert observed_task_names(status_session) == []
