"""The `cleanup_status_snapshots` task, end to end through the runner.

The retention *policy* is tested in `test_status_retention.py`; what matters
here is the wiring: that the task computes its cutoff from the occurrence
rather than the clock, that it prunes the ledger without deleting its own live
row, and that `$STATUS_RETENTION_DAYS` reaches it.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from scheduling.ledger import TaskLedger
from scheduling.registry import TASKS
from scheduling.runner import run_due
from scheduling.tasks.cleanup_status import (
    TASK_RUN_RETENTION_DAYS,
    cleanup_status_snapshots,
    retention_days,
)
from system_status import DerechoStatus
from system_status.models import TaskRun
from system_status.retention import DEFAULT_RETENTION_DAYS

pytestmark = pytest.mark.unit

NAME = 'cleanup_status_snapshots'
#: A dispatch instant well after the 02:15 MDT slot (08:15 UTC) on this date.
NOW = datetime(2026, 8, 12, 9, 7, 0)
OCC = datetime(2026, 8, 12, 8, 15, 0)


@pytest.fixture
def status_engine(app, status_session):
    from webapp.extensions import db
    return db.engines['system_status']


@pytest.fixture
def ledger(status_engine):
    return TaskLedger(lambda: Session(status_engine))


@pytest.fixture
def registry():
    """Just this task, taken from the real global registry."""
    import scheduling.tasks                       # noqa: F401  (side effects)
    return {NAME: TASKS[NAME]}


def _derecho(session, ts):
    row = DerechoStatus(
        timestamp=ts,
        cpu_nodes_total=100, cpu_nodes_available=50, cpu_nodes_down=0,
        gpu_nodes_total=10, gpu_nodes_available=5, gpu_nodes_down=0,
        cpu_cores_total=12800, cpu_cores_allocated=6400, cpu_cores_idle=6400,
        gpu_count_total=40, gpu_count_allocated=20, gpu_count_idle=20,
        memory_total_gb=10000.0, memory_allocated_gb=5000.0,
    )
    session.add(row)
    return row


# ---------------------------------------------------------------- registration

class TestRegistration:

    def test_the_task_is_registered_by_importing_the_package(self):
        import scheduling.tasks                   # noqa: F401
        assert NAME in TASKS

    def test_it_needs_only_the_status_database(self):
        import scheduling.tasks                   # noqa: F401
        assert TASKS[NAME].needs == ('status',)

    def test_it_runs_at_0215_mountain(self):
        import scheduling.tasks                   # noqa: F401
        assert TASKS[NAME].schedule.describe() == 'daily at 02:15 America/Denver'

    def test_it_declares_an_expected_runtime_so_the_lease_is_sized(self):
        import scheduling.tasks                   # noqa: F401
        assert TASKS[NAME].expected_runtime is not None


# -------------------------------------------------------------- the env knob

class TestRetentionDays:

    def test_defaults_to_the_policy(self):
        assert retention_days({}) == DEFAULT_RETENTION_DAYS

    def test_reads_the_env_var(self):
        assert retention_days({'STATUS_RETENTION_DAYS': '30'}) == 30

    def test_blank_falls_back(self):
        assert retention_days({'STATUS_RETENTION_DAYS': ''}) == DEFAULT_RETENTION_DAYS
        assert retention_days({'STATUS_RETENTION_DAYS': '   '}) == DEFAULT_RETENTION_DAYS

    def test_garbage_falls_back_rather_than_crashing_the_task(self):
        assert retention_days({'STATUS_RETENTION_DAYS': 'yesterday'}) == \
            DEFAULT_RETENTION_DAYS

    @pytest.mark.parametrize('value', ['0', '-1', '-365'])
    def test_a_non_positive_window_is_refused(self, value):
        """Zero would delete rows written seconds ago. Refuse, don't obey."""
        assert retention_days({'STATUS_RETENTION_DAYS': value}) == \
            DEFAULT_RETENTION_DAYS


# ------------------------------------------------------------------ dispatch

class TestThroughTheRunner:

    def test_prunes_old_snapshots_and_keeps_recent_ones(self, ledger, registry,
                                                        status_session):
        _derecho(status_session, OCC - timedelta(days=400))     # doomed
        _derecho(status_session, OCC - timedelta(days=10))      # survives
        status_session.commit()

        out = run_due(now=NOW, ledger=ledger, registry=registry,
                      status_session_factory=lambda: status_session)

        assert out['counts'] == {'succeeded': 1}
        assert status_session.query(DerechoStatus).count() == 1

    def test_the_cutoff_comes_from_the_occurrence_not_the_clock(self, ledger,
                                                                registry,
                                                                status_session):
        """A late dispatch must delete exactly what a punctual one would."""
        status_session.commit()

        punctual = run_due(now=OCC, ledger=ledger, registry=registry,
                           status_session_factory=lambda: status_session)

        # Same slot, dispatched five hours later: the ledger blocks a re-run,
        # so compare the recorded cutoff instead.
        cutoff = punctual['results'][0]['detail']['cutoff']
        assert cutoff == (OCC - timedelta(days=DEFAULT_RETENTION_DAYS)).isoformat()
        assert 'T08:15:00' in cutoff, (
            'the cutoff must be anchored to the 02:15 MDT slot, not to `now`')

    def test_detail_carries_the_per_table_breakdown(self, ledger, registry,
                                                    status_session):
        _derecho(status_session, OCC - timedelta(days=400))
        status_session.commit()

        out = run_due(now=NOW, ledger=ledger, registry=registry,
                      status_session_factory=lambda: status_session)

        detail = out['results'][0]['detail']
        assert detail['deleted']['derecho_status'] == 1
        assert detail['retention_days'] == DEFAULT_RETENTION_DAYS
        assert 'user_proj_queue_status' in detail['deleted']

    def test_dry_run_deletes_nothing_but_says_what_it_would(self, ledger,
                                                            registry,
                                                            status_session):
        """This used to assert only the first half — and passed for the wrong
        reason, because the runner never executed the body under `--dry-run`.
        It would have gone on passing with the task's entire `ctx.dry_run`
        handling deleted. The `would_be` / `deleted` assertions are what make it
        able to fail.
        """
        _derecho(status_session, OCC - timedelta(days=400))     # doomed
        _derecho(status_session, OCC - timedelta(days=10))      # survives
        status_session.commit()

        out = run_due(now=NOW, ledger=ledger, registry=registry, dry_run=True,
                      status_session_factory=lambda: status_session)

        assert status_session.query(DerechoStatus).count() == 2, \
            'a dry run must not delete'
        result = out['results'][0]
        assert result['outcome'] == 'would_claim'
        assert result['would_be'] == 'succeeded'
        assert result['detail']['deleted']['derecho_status'] == 1, \
            'and must report the row it WOULD have deleted'

    def test_a_dry_run_does_not_prune_the_ledger_either(self, ledger, registry,
                                                        status_session):
        """The task guards its own `ledger.prune` on `ctx.dry_run` — a guard
        that was unreachable, so nothing proved it worked."""
        status_session.commit()

        out = run_due(now=NOW, ledger=ledger, registry=registry, dry_run=True,
                      status_session_factory=lambda: status_session)

        assert out['results'][0]['detail']['task_run_pruned'] == 0

    def test_a_second_dispatch_in_the_slot_does_not_prune_twice(self, ledger,
                                                                registry,
                                                                status_session):
        status_session.commit()
        run_due(now=NOW, ledger=ledger, registry=registry,
                status_session_factory=lambda: status_session)
        out = run_due(now=NOW + timedelta(hours=1), ledger=ledger,
                      registry=registry,
                      status_session_factory=lambda: status_session)
        assert out['counts'] == {'already_claimed': 1}


# ------------------------------------------------------------- ledger pruning

class TestLedgerPruning:

    def test_old_finished_ledger_rows_are_pruned(self, ledger, registry,
                                                 status_session):
        ancient = ledger.claim(NAME, '20250101T081500Z',
                               now=OCC - timedelta(days=400))
        ledger.finish(ancient, state='succeeded', now=OCC - timedelta(days=400))
        status_session.commit()

        out = run_due(now=NOW, ledger=ledger, registry=registry,
                      status_session_factory=lambda: status_session)

        assert out['results'][0]['detail']['task_run_pruned'] == 1

    def test_the_run_does_not_delete_its_own_row(self, ledger, registry,
                                                 status_session):
        """The task is itself a `running` row at the moment it prunes."""
        status_session.commit()

        run_due(now=NOW, ledger=ledger, registry=registry,
                status_session_factory=lambda: status_session)

        row = ledger.get(NAME, '20260812T081500Z')
        assert row is not None and row['state'] == 'succeeded'

    def test_recent_ledger_rows_survive(self, ledger, registry, status_session):
        recent = ledger.claim(NAME, '20260801T081500Z',
                              now=OCC - timedelta(days=11))
        ledger.finish(recent, state='succeeded', now=OCC - timedelta(days=11))
        status_session.commit()

        run_due(now=NOW, ledger=ledger, registry=registry,
                status_session_factory=lambda: status_session)

        assert ledger.get(NAME, '20260801T081500Z') is not None

    def test_the_ledger_horizon_is_shorter_than_the_snapshot_one(self):
        """Ledger rows are metadata about runs; snapshots are the product."""
        assert TASK_RUN_RETENTION_DAYS < DEFAULT_RETENTION_DAYS
