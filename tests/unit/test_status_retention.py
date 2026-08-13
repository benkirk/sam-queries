"""The `system_status` retention policy — what it prunes, and what it must not.

Most of these tests exist because ``docs/plans/implemented/SCHEDULED_TASKS.md``
§ 3.1 turned ``scripts/cleanup_status_data.py`` from a thing to port into a set
of decisions to make. Each decision that changed behaviour gets a test here, so
the agenda leaves evidence rather than a changelog entry:

* the cutoff is honoured exactly, and is naive UTC (the script used local time);
* curated tables are untouched (the script pruned them);
* spans are pruned on their own row, not inherited from a parent's CASCADE;
* 365 is the default everywhere a consumer could disagree.
"""

from datetime import datetime, timedelta

import pytest

from system_status import (
    CasperNodeTypeStatus,
    CasperStatus,
    DerechoStatus,
    FilesystemStatus,
    JupyterHubStatus,
    LoginNodeStatus,
    QueueStatus,
    ResourceReservation,
    SystemOutage,
    System,
    UserProjQueueStatus,
)
from system_status import retention
from system_status.retention import (
    DEFAULT_RETENTION_DAYS,
    SNAPSHOT_TABLES,
    cleanup_old_data,
)

pytestmark = pytest.mark.unit


#: A fixed "now" so nothing here depends on the wall clock.
NOW = datetime(2026, 8, 12, 3, 0, 0)
CUTOFF = NOW - timedelta(days=365)


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


def _span(session, parent, *, first_seen, last_seen, user='u1', project='P0001'):
    """A UserProjQueueStatus span. ``timestamp`` IS first_seen."""
    row = UserProjQueueStatus(
        timestamp=first_seen,
        last_seen=last_seen,
        system_name='derecho', queue_name='main',
        username=user, project_code=project,
        running_jobs=1, cores_allocated=128,
    )
    row.derecho_status = parent
    session.add(row)
    return row


def _system(session):
    sysrow = session.query(System).filter_by(name='derecho').one_or_none()
    if sysrow is None:
        sysrow = System(name='derecho')
        session.add(sysrow)
        session.flush()
    return sysrow


# ---------------------------------------------------------------- the cutoff

class TestCutoff:

    def test_cutoff_is_honoured_exactly(self, status_session):
        """One second either side of the cutoff decides the row's fate."""
        _derecho(status_session, CUTOFF - timedelta(seconds=1))   # doomed
        _derecho(status_session, CUTOFF)                          # survives (not <)
        _derecho(status_session, CUTOFF + timedelta(seconds=1))   # survives
        status_session.flush()

        counts = cleanup_old_data(cutoff=CUTOFF, session=status_session)

        assert counts['derecho_status'] == 1
        assert status_session.query(DerechoStatus).count() == 2

    def test_default_cutoff_is_utc_not_local(self, status_session, monkeypatch):
        """The script compared a *local* now() against UTC-stamped rows.

        On a Denver host that is 6-7 hours of extra deletion, silently, every
        run. Pin `utcnow_naive` and assert the derived cutoff came from it.
        """
        seen = {}

        def _fake_utcnow():
            return NOW

        monkeypatch.setattr(retention, 'utcnow_naive', _fake_utcnow)

        real_cleanup = retention._cleanup

        def _spy(session, cutoff, *args, **kwargs):
            seen['cutoff'] = cutoff
            return real_cleanup(session, cutoff, *args, **kwargs)

        monkeypatch.setattr(retention, '_cleanup', _spy)
        cleanup_old_data(retention_days=365, session=status_session)

        assert seen['cutoff'] == NOW - timedelta(days=365)

    def test_dry_run_deletes_nothing(self, status_session):
        _derecho(status_session, CUTOFF - timedelta(days=10))
        _derecho(status_session, CUTOFF - timedelta(days=20))
        status_session.flush()

        counts = cleanup_old_data(cutoff=CUTOFF, session=status_session,
                                  dry_run=True)

        assert counts['derecho_status'] == 2, 'dry run still reports the count'
        assert status_session.query(DerechoStatus).count() == 2, 'nothing deleted'


# ------------------------------------------------------------------- spans

class TestSpanPruning:
    """Decision 4. ``user_proj_queue_status`` was absent from the old table
    list, reaped only by ``ondelete='CASCADE'`` from its parent snapshot.

    The semantic is kept — a span whose *first_seen* falls out of the window
    goes — but it is now this module's own DELETE. That matters because the
    test tier is SQLite, which does not enforce foreign keys at all unless
    ``PRAGMA foreign_keys=ON`` (nothing in this repo sets it), and a bulk
    ``query.delete()`` bypasses SQLAlchemy's ORM cascade too. Under CASCADE
    these assertions would be testing nothing.
    """

    def test_span_is_pruned_on_its_own_row(self, status_session):
        parent = _derecho(status_session, CUTOFF - timedelta(days=5))
        _span(status_session, parent,
              first_seen=CUTOFF - timedelta(days=5),
              last_seen=CUTOFF - timedelta(days=5) + timedelta(hours=6))
        status_session.flush()

        counts = cleanup_old_data(cutoff=CUTOFF, session=status_session)

        assert counts['user_proj_queue_status'] == 1
        assert status_session.query(UserProjQueueStatus).count() == 0

    def test_recent_span_survives_an_old_parent(self, status_session):
        """The case CASCADE could not express.

        A span's parent FK is pinned at first_seen and never rewritten
        (PR #248), so a span can outlive its parent snapshot's window. Here
        first_seen is *inside* the window while the parent row is outside it.
        Deleting the parent must not take the span with it.
        """
        old_parent = _derecho(status_session, CUTOFF - timedelta(days=30))
        _span(status_session, old_parent,
              first_seen=CUTOFF + timedelta(days=1),      # inside the window
              last_seen=NOW - timedelta(hours=1))         # still live
        status_session.flush()

        counts = cleanup_old_data(cutoff=CUTOFF, session=status_session)

        assert counts['derecho_status'] == 1, 'the parent snapshot went'
        assert counts['user_proj_queue_status'] == 0
        assert status_session.query(UserProjQueueStatus).count() == 1, (
            'a span whose first_seen is inside the window must survive its '
            'parent being pruned')

    def test_orphan_span_with_no_parent_is_still_pruned(self, status_session):
        """Both parent FKs NULL. CASCADE could never have reached this row."""
        row = UserProjQueueStatus(
            timestamp=CUTOFF - timedelta(days=3),
            last_seen=CUTOFF - timedelta(days=3),
            system_name='derecho', queue_name='main',
            username='orphan', project_code='P0002',
            running_jobs=0, cores_allocated=0,
        )
        status_session.add(row)
        status_session.flush()
        assert row.derecho_status_id is None and row.casper_status_id is None

        counts = cleanup_old_data(cutoff=CUTOFF, session=status_session)

        assert counts['user_proj_queue_status'] == 1
        assert status_session.query(UserProjQueueStatus).count() == 0


# -------------------------------------------------------- curated tables

class TestCuratedTablesAreUntouched:
    """Decision 2. The script deleted resolved outages and past reservations
    in the same transaction as the snapshots, on two inconsistent predicates
    nobody had examined. A scheduled job does not delete hand-written history.
    """

    def test_outages_and_reservations_survive(self, status_session):
        sysrow = _system(status_session)
        ancient = CUTOFF - timedelta(days=100)

        status_session.add(SystemOutage(
            system_id=sysrow.system_id, title='ancient resolved outage',
            status='resolved', severity='minor',
            start_time=ancient, end_time=ancient + timedelta(hours=2)))
        # The leak the old predicate had: resolved but never closed out.
        status_session.add(SystemOutage(
            system_id=sysrow.system_id, title='resolved, end_time NULL',
            status='resolved', severity='minor',
            start_time=ancient, end_time=None))
        status_session.add(ResourceReservation(
            system_id=sysrow.system_id, reservation_name='ancient reservation',
            start_time=ancient, end_time=ancient + timedelta(days=1)))
        status_session.flush()

        counts = cleanup_old_data(cutoff=CUTOFF, session=status_session)

        assert 'system_outages' not in counts
        assert 'resource_reservations' not in counts
        assert status_session.query(SystemOutage).count() == 2
        assert status_session.query(ResourceReservation).count() == 1

    def test_lookup_tables_are_out_of_scope(self, status_session):
        """`systems`, `queues`, `status_users`, ... are not snapshots."""
        table_names = {name for _model, name in SNAPSHOT_TABLES}
        assert 'systems' not in table_names
        assert 'queues' not in table_names
        assert 'status_users' not in table_names
        assert 'project_codes' not in table_names


# ---------------------------------------------------------------- coverage

class TestPolicySurface:

    def test_default_is_one_year_in_every_consumer(self):
        """Three places could disagree about the number. None may."""
        import inspect
        from scripts.cleanup_status_data import build_parser

        assert DEFAULT_RETENTION_DAYS == 365

        # the function signature
        sig = inspect.signature(cleanup_old_data)
        assert sig.parameters['retention_days'].default == DEFAULT_RETENTION_DAYS

        # the hand-run script — the consumer that used to carry its own 7
        assert build_parser().parse_args([]).retention_days == 365

    def test_per_table_overrides_start_empty(self):
        """Deliberately unmeasured — see the RETENTION_DAYS docstring."""
        assert retention.RETENTION_DAYS == {}

    def test_every_snapshot_model_is_covered(self):
        """A new snapshot table must be added here, or it grows for ever.

        Catches the omission that left `user_proj_queue_status` out of the old
        script's list: any model carrying a `timestamp` column is a time series
        and needs a retention answer.
        """
        expected = {
            CasperNodeTypeStatus, CasperStatus, DerechoStatus, FilesystemStatus,
            JupyterHubStatus, LoginNodeStatus, QueueStatus, UserProjQueueStatus,
        }
        assert {model for model, _name in SNAPSHOT_TABLES} == expected

    def test_children_are_ordered_before_parents(self):
        """Deleting children first is what makes the result independent of
        whether the backend enforces CASCADE."""
        order = [name for _model, name in SNAPSHOT_TABLES]
        for child in ('user_proj_queue_status', 'login_node_status',
                      'queue_status', 'filesystem_status'):
            assert order.index(child) < order.index('derecho_status'), child
            assert order.index(child) < order.index('casper_status'), child

    def test_counts_include_every_table_even_at_zero(self, status_session):
        counts = cleanup_old_data(cutoff=CUTOFF, session=status_session)
        assert set(counts) == {name for _model, name in SNAPSHOT_TABLES}
        assert all(v == 0 for v in counts.values())

    def test_chunking_terminates_and_deletes_everything(self, status_session):
        """A chunk size below the row count must still drain the table."""
        for i in range(7):
            _derecho(status_session, CUTOFF - timedelta(days=i + 1))
        status_session.flush()

        counts = cleanup_old_data(cutoff=CUTOFF, session=status_session,
                                  chunk_size=2)

        assert counts['derecho_status'] == 7
        assert status_session.query(DerechoStatus).count() == 0

    def test_per_table_override_shifts_only_that_table(self, status_session,
                                                       monkeypatch):
        """A 30-day override prunes rows the 365-day cutoff would keep."""
        monkeypatch.setitem(retention.RETENTION_DAYS, 'jupyterhub_status', 30)

        recent = NOW - timedelta(days=60)      # inside 365, outside 30
        status_session.add(JupyterHubStatus(
            timestamp=recent, available=True, active_users=1,
            active_sessions=1))
        _derecho(status_session, recent)
        status_session.flush()

        counts = cleanup_old_data(cutoff=CUTOFF, retention_days=365,
                                  session=status_session)

        assert counts['jupyterhub_status'] == 1, 'override applied'
        assert counts['derecho_status'] == 0, 'other tables unaffected'
