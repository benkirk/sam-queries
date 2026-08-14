"""The `deactivate_expired_projects` task — its wiring, not its business rules.

What it deactivates is `test_deactivate_expired_projects.py`. What matters here
is everything the *schedule* adds: that the task is registered at all, that both
occurrence-derived values are converted out of UTC, and that the misfire policy
is the one the docstring claims — because for a monthly task a misfire forfeits
the whole month, which is a far more expensive mistake than it is for a nightly
one.

**Selection is patched in most cases.** The task deliberately filters by no
facility and no resource, so against the obfuscated snapshot every container
runs it would sweep real rows. One case at the bottom lets the real query run
and asserts only shape, which is what catches a signature drift.
"""

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from factories.projects import make_account, make_allocation, make_project
from factories.resources import make_resource
from sqlalchemy.orm import Session

from scheduling.ledger import TaskLedger
from scheduling.registry import TASKS, TaskContext, TaskResult
from scheduling.runner import run_due
from scheduling.schedules import occurrence_key, to_local_naive
from scheduling.tasks.deactivate_expired import (
    FACILITIES,
    SCHEDULE,
    deactivate_expired_projects,
)

pytestmark = pytest.mark.unit

NAME = 'deactivate_expired_projects'

#: The 2026-09-03 04:30 America/Denver slot, as the runner sees it: naive UTC.
#: MDT is UTC-6, so 04:30 local is 10:30 UTC.
OCC = datetime(2026, 9, 3, 10, 30)
#: ...and the same instant in the naive-Mountain terms every SAM date uses.
SLOT = datetime(2026, 9, 3, 4, 30)


@pytest.fixture
def status_engine(app, status_session):
    from webapp.extensions import db
    return db.engines['system_status']


@pytest.fixture
def ledger(status_engine):
    return TaskLedger(lambda: Session(status_engine))


@pytest.fixture
def ctx(session):
    """A context with the test's SAM session preset, as the runner would."""
    def build(occurrence=OCC, *, lateness=timedelta(minutes=7)):
        return TaskContext(now=occurrence + lateness,
                           occurrence=occurrence,
                           occurrence_key=occurrence_key(occurrence),
                           task_name=NAME,
                           logger=logging.getLogger('test'),
                           _sam_session=session)
    return build


@pytest.fixture
def scoped_selection(session, monkeypatch):
    """Patch the task's selection to a resource-scoped one.

    Returns the resource, so a test can build projects the task will then see —
    and only those, whatever the snapshot contains.
    """
    import sam.queries.expirations as mod
    resource = make_resource(session)
    real = mod.get_projects_with_expired_allocations

    def scoped(session_, **kwargs):
        kwargs['resource_name'] = resource.resource_name
        return real(session_, **kwargs)

    monkeypatch.setattr(mod, 'get_projects_with_expired_allocations', scoped)
    return resource


class _RecordingSession:
    """Lends the test session to the runner and records how it was closed out.

    `close_sessions` commits or rolls back and then **closes**, which on the
    suite's SAVEPOINT-isolated session would detach every instance the test
    still holds. Swallowing those three calls keeps the test readable while
    recording the one thing worth asserting: which way the runner went. Same
    trick, and same reason, as `cleanup_status._NonClosingSession`.
    """

    def __init__(self, inner):
        self._inner = inner
        self.committed = False
        self.rolled_back = False

    def commit(self):
        self.committed = True
        self._inner.flush()

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _expired(session, resource, *, days_expired, projcode=None):
    project = make_project(session, active=True, facility_name='UNIV',
                           projcode=projcode)
    account = make_account(session, project=project, resource=resource)
    make_allocation(session, account=account,
                    start_date=SLOT - timedelta(days=days_expired + 365),
                    end_date=SLOT - timedelta(days=days_expired))
    return project


# ---------------------------------------------------------------- registration

class TestRegistration:

    def test_the_task_is_registered_by_importing_the_package(self):
        import scheduling.tasks                   # noqa: F401
        assert NAME in TASKS

    def test_it_needs_only_the_sam_database(self):
        import scheduling.tasks                   # noqa: F401
        assert TASKS[NAME].needs == ('sam',)

    def test_it_runs_monthly_on_the_third_at_0430_mountain(self):
        import scheduling.tasks                   # noqa: F401
        assert TASKS[NAME].schedule.describe() == \
            'monthly on day 3 at 04:30 America/Denver'

    def test_it_declares_an_expected_runtime_so_the_lease_is_sized(self):
        import scheduling.tasks                   # noqa: F401
        assert TASKS[NAME].expected_runtime is not None

    def test_it_sweeps_every_facility(self):
        """The one place this task's audience differs from `expiration_notices`,
        which pins ('UNIV', 'WNA') because it mails external PIs. Deactivation
        sends nothing, so internal projects are not exempt."""
        assert FACILITIES is None


# ------------------------------------------------------------ the occurrence

class TestEverythingComesFromTheOccurrence:

    def test_the_stamp_is_the_slot_in_mountain_time(self, session, ctx,
                                                    scoped_selection):
        """`ctx.occurrence` is naive UTC; `inactivate_time` is naive Mountain.
        Stamping the raw value would mark projects inactive since a time that
        has not happened yet, rendered as a future 'since <date>' on the user
        project card."""
        project = _expired(session, scoped_selection, days_expired=100)

        result = deactivate_expired_projects(ctx())

        assert project.inactivate_time == SLOT
        assert result.detail['inactivate_time'] == SLOT.isoformat()
        assert result.detail['as_of'] == SLOT.isoformat()

    def test_the_zone_is_read_off_the_schedule(self):
        """A second `'America/Denver'` literal in the body would silently
        survive a schedule move to another zone."""
        assert to_local_naive(OCC, ZoneInfo(SCHEDULE.tz)) == SLOT

    def test_a_late_dispatch_selects_what_a_punctual_one_would(
            self, session, ctx, scoped_selection):
        """The whole point of computing from the occurrence. This project sits
        just inside the floor at the slot; six hours of dispatch lateness must
        not change the answer."""
        _expired(session, scoped_selection, days_expired=91)

        punctual = deactivate_expired_projects(ctx(lateness=timedelta(minutes=7)))

        assert punctual.detail['deactivated'] == 1

    def test_the_window_moves_with_the_occurrence(self, session, ctx,
                                                  scoped_selection):
        """Same data, an earlier slot: the project is not yet 90 days expired,
        so the same task run against that slot must deactivate nothing."""
        _expired(session, scoped_selection, days_expired=91)

        earlier = deactivate_expired_projects(ctx(OCC - timedelta(days=30)))

        assert earlier.detail['deactivated'] == 0


# ------------------------------------------------------------------ the detail

class TestTheLedgerDetail:

    def test_a_quiet_month_is_distinguishable_from_a_broken_query(
            self, session, ctx, scoped_selection):
        """Most months deactivate nothing. `succeeded` with no numbers would
        make that indistinguishable from a query that stopped matching."""
        result = deactivate_expired_projects(ctx())

        assert result.detail['selected'] == 0
        assert result.detail['deactivated'] == 0
        assert result.detail['min_days_expired'] == 90
        assert result.detail['facilities'] == 'all'

    def test_projcodes_are_reported(self, session, ctx, scoped_selection):
        project = _expired(session, scoped_selection, days_expired=400,
                           projcode='ZZTASK001')

        result = deactivate_expired_projects(ctx())

        assert result.detail['projcodes'] == ['ZZTASK001']
        assert 'projcodes_truncated' not in result.detail
        assert not project.is_active

    def test_several_expired_allocations_still_count_as_one_project(
            self, session, ctx, scoped_selection):
        """`selected` is in units of projects. The query pins the latest
        allocation per project (`LIMIT 1` in the correlated subquery), so this
        is belt-and-braces — but it is the assertion that would catch a swap to
        `get_all_expiring_allocations`, which returns every allocation."""
        project = make_project(session, active=True, facility_name='UNIV')
        account = make_account(session, project=project,
                               resource=scoped_selection)
        for days in (400, 300, 200):
            make_allocation(session, account=account,
                            start_date=SLOT - timedelta(days=days + 365),
                            end_date=SLOT - timedelta(days=days))

        result = deactivate_expired_projects(ctx())

        assert result.detail['selected'] == 1
        assert result.detail['deactivated'] == 1
        assert not project.is_active

    def test_the_detail_is_json_serializable(self, session, ctx,
                                             scoped_selection):
        """It goes into a TEXT column via json.dumps."""
        import json
        _expired(session, scoped_selection, days_expired=400)

        result = deactivate_expired_projects(ctx())

        assert json.loads(json.dumps(result.detail))


# ------------------------------------------------------- the misfire policy

class TestTheMisfirePolicy:
    """For a monthly task a misfire costs the whole month: past the grace the
    runner records a `skipped` row INSTEAD of running, and that row settles the
    slot. These pin the policy the 7 days actually buys, not the constant.
    """

    @pytest.fixture
    def registry(self):
        """The real task's schedule and grace, with an inert body.

        `needs=()` so the runner opens no sessions — this is about dispatch,
        and a real body would sweep the snapshot.
        """
        import scheduling.tasks                   # noqa: F401
        self.ran = []

        def spy(ctx):
            self.ran.append(ctx.occurrence)
            return TaskResult(detail={'deactivated': 0})

        return {NAME: replace(TASKS[NAME], fn=spy, needs=())}

    def test_a_three_day_outage_still_runs(self, ledger, registry):
        out = run_due(now=OCC + timedelta(days=3), ledger=ledger,
                      registry=registry)

        assert out['counts'] == {'succeeded': 1}
        assert self.ran == [OCC]

    def test_past_the_grace_it_skips_and_waits_for_next_month(self, ledger,
                                                              registry):
        out = run_due(now=OCC + timedelta(days=10), ledger=ledger,
                      registry=registry)

        assert out['counts'] == {'skipped': 1}
        assert out['results'][0]['reason'] == 'misfire'
        assert self.ran == []

    def test_the_grace_is_long_enough_to_matter_and_short_enough_to_fire(self):
        """Both halves are deliberate. `CatchUp.SKIP` means `last_occurrence` is
        always the most recent slot, so lateness is bounded by one gap (~31 days
        here) — a grace above that would make the misfire branch unreachable,
        and the 6h default would forfeit a month to a weekend outage.
        """
        grace = TASKS[NAME].misfire_grace
        assert timedelta(hours=6) < grace < timedelta(days=31)

    def test_a_second_dispatch_in_the_slot_does_not_run_twice(self, ledger,
                                                              registry):
        run_due(now=OCC + timedelta(minutes=7), ledger=ledger, registry=registry)
        out = run_due(now=OCC + timedelta(hours=1), ledger=ledger,
                      registry=registry)

        assert out['counts'] == {'already_claimed': 1}
        assert len(self.ran) == 1


# ------------------------------------------------------------------- dry run

class TestDryRun:
    """`--dry-run` deactivates and rolls back, which is honest coverage here.

    This task has no `ctx.dry_run` branch and needs none: everything it does is
    transactional, unlike `expiration_notices`, whose mail a rollback cannot
    recall. So the preview is the real thing, undone.
    """

    def test_it_reports_what_it_would_deactivate_and_then_rolls_back(
            self, session, ledger, scoped_selection):
        """Asserts the *contract*, not the row state.

        The runner calls `close_sessions(commit=False)`, which both rolls back
        AND closes — so reading the ORM instance afterwards raises
        `DetachedInstanceError`, and a genuine rollback would unwind the
        factory rows along with the deactivation (the suite isolates with
        SAVEPOINT). Recording the call is what this layer can honestly check;
        that a rollback undoes an UPDATE is SQLAlchemy's job, not this task's.
        """
        import scheduling.tasks                   # noqa: F401
        _expired(session, scoped_selection, days_expired=400,
                 projcode='ZZDRY001')
        recorder = _RecordingSession(session)

        out = run_due(now=OCC + timedelta(minutes=7), ledger=ledger,
                      registry={NAME: TASKS[NAME]}, dry_run=True,
                      sam_session_factory=lambda: recorder)

        result = out['results'][0]
        assert result['outcome'] == 'would_claim'
        assert result['would_be'] == 'succeeded'
        assert result['dry_run'] is True
        assert 'ZZDRY001' in result['detail']['projcodes']

        assert recorder.rolled_back, 'a dry run must roll the task session back'
        assert not recorder.committed, 'and must never commit it'

    def test_a_dry_run_writes_no_ledger_row(self, session, ledger,
                                            scoped_selection, status_session):
        """No claim, so the real monthly slot stays free."""
        import scheduling.tasks                   # noqa: F401
        from system_status.models import TaskRun

        run_due(now=OCC + timedelta(minutes=7), ledger=ledger,
                registry={NAME: TASKS[NAME]}, dry_run=True,
                sam_session_factory=lambda: _RecordingSession(session))

        assert status_session.query(TaskRun).filter_by(task_name=NAME).all() == []


# ------------------------------------------------------------------ the real query

class TestAgainstTheRealSelection:

    def test_the_unpatched_query_runs_and_returns_the_documented_shape(
            self, session, ctx):
        """No patching, no count assertions — the snapshot decides those. This
        exists to catch a signature drift between the task and the query it
        calls, which every other case in this file patches away.
        """
        result = deactivate_expired_projects(ctx())

        assert set(result.detail) >= {
            'as_of', 'min_days_expired', 'facilities',
            'selected', 'deactivated', 'inactivate_time', 'projcodes'}
        assert result.detail['deactivated'] == result.detail['selected']
        assert result.state == 'succeeded'
