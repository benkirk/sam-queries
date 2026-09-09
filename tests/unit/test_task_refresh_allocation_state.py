"""The `refresh_allocation_state` task — its wiring and its projection.

The projection is the dashboards' batched builder, so equality with
`get_projects_dashboard_data` is the invariant, checked here on factory-built
rows and on the snapshot's subtree/inheriting fixtures. The rest is what the
schedule adds: registration, the ships-disabled switch, the lease-vs-deadline
inequality, idempotency, and that rows leaving the candidate set are deleted.
"""

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from factories.projects import make_account, make_allocation, make_project
from factories.resources import make_resource
from factories.summaries import make_comp_charge_summary

from sam.queries.allocation_state import (
    candidate_projects,
    project_allocation_state,
)
from sam.queries.dashboard import get_projects_dashboard_data
from sam.resources.resources import ResourceType
from sam.summaries.allocation_state import AccountAllocationState
from scheduling.ledger import lease_for
from scheduling.registry import TASKS, TaskContext
from scheduling.schedules import occurrence_key
from scheduling.tasks.refresh_allocation_state import refresh_allocation_state

pytestmark = pytest.mark.unit

NAME = 'refresh_allocation_state'
OCC = datetime(2026, 9, 9, 15, 0)
VALUES = Path(__file__).resolve().parents[2] / 'helm' / 'values.yaml'


@pytest.fixture
def ctx(session):
    return TaskContext(now=OCC + timedelta(minutes=7), occurrence=OCC,
                       occurrence_key=occurrence_key(OCC), task_name=NAME,
                       logger=logging.getLogger('test'), _sam_session=session)


@pytest.fixture
def hpc(session):
    """A fresh resource of the snapshot's HPC type, so no snapshot charge lands on it."""
    rtype = session.query(ResourceType).filter_by(resource_type='HPC').one()
    return make_resource(session, resource_type=rtype)


def _charged_project(session, resource, *, amount=1000.0, charges=(40.0, 60.0)):
    project = make_project(session, facility_name='UNIV')
    account = make_account(session, project=project, resource=resource)
    now = datetime.now()
    alloc = make_allocation(session, account=account, amount=amount,
                            start_date=now - timedelta(days=30),
                            end_date=now + timedelta(days=335))
    for i, c in enumerate(charges):
        row = make_comp_charge_summary(session, charges=c,
                                       activity_date=datetime.now() - timedelta(days=i))
        row.account_id = account.account_id
    session.flush()
    return project, account, alloc


@pytest.fixture
def scoped(session, hpc, monkeypatch):
    """Make the task see only factory-built projects, whatever the snapshot holds."""
    import sam.queries.allocation_state as mod
    built = []
    monkeypatch.setattr(mod, 'candidate_projects', lambda s, now: list(built))
    return built


# ---------------------------------------------------------------- registration

class TestRegistration:

    def test_it_is_registered_and_needs_only_sam(self):
        import scheduling.tasks                   # noqa: F401
        assert NAME in TASKS
        assert TASKS[NAME].needs == ('sam',)
        assert TASKS[NAME].schedule.describe() == 'hourly at :00 UTC'

    def test_the_lease_outlives_the_cronjob_deadline(self):
        """A killed run must not be reclaimed while its rows are being written."""
        match = re.search(r'^\s*activeDeadlineSeconds:\s*(\d+)', VALUES.read_text(),
                          re.MULTILINE)
        assert match
        assert lease_for(TASKS[NAME].expected_runtime).total_seconds() > int(match.group(1))

    def test_it_ships_switched_off(self):
        """`SAM_TASKS_DISABLED` is fail-open; the table does not exist in prod
        until the DDL is applied. Delete this test in the commit that clears
        the switch."""
        line, = [ln for ln in VALUES.read_text().splitlines()
                 if ln.strip().startswith('SAM_TASKS_DISABLED:')]
        assert NAME in line, line


# ------------------------------------------------------------------ projection

class TestProjection:

    def test_a_row_carries_the_builders_numbers(self, session, hpc):
        project, account, alloc = _charged_project(session, hpc)
        now = datetime.now()

        rows = project_allocation_state(session, now=now, projects=[project])

        row, = rows
        assert row['allocation_id'] == alloc.allocation_id
        assert row['account_id'] == account.account_id
        assert row['projcode'] == project.projcode
        assert row['resource_id'] == hpc.resource_id
        assert row['resource_type'] == 'HPC'
        assert row['facility_name'] == 'UNIV'
        assert row['allocated'] == 1000.0
        assert row['used'] == pytest.approx(100.0)
        assert row['self_used'] == pytest.approx(100.0)
        assert row['remaining'] == pytest.approx(900.0)
        assert row['percent_used'] == pytest.approx(10.0)
        assert row['charges_by_type']['comp'] == pytest.approx(100.0)
        assert row['is_current'] is True
        assert row['is_inheriting'] is False
        assert row['rolling_windows'] is None

    def test_a_recently_ended_allocation_is_a_non_current_row(self, session, hpc):
        project = make_project(session, facility_name='UNIV')
        account = make_account(session, project=project, resource=hpc)
        now = datetime.now()
        make_allocation(session, account=account,
                        start_date=now - timedelta(days=400),
                        end_date=now - timedelta(days=10))

        assert project in candidate_projects(session, now)
        row, = project_allocation_state(session, now=now, projects=[project])
        assert row['is_current'] is False

    def test_an_allocation_ended_long_ago_is_not_a_candidate(self, session, hpc):
        project = make_project(session, facility_name='UNIV')
        account = make_account(session, project=project, resource=hpc)
        now = datetime.now()
        make_allocation(session, account=account,
                        start_date=now - timedelta(days=500),
                        end_date=now - timedelta(days=120))

        assert project not in candidate_projects(session, now)
        assert project_allocation_state(session, now=now, projects=[project]) == []

    @pytest.mark.parametrize('fixture', ['subtree_project', 'inheriting_project'])
    def test_rows_equal_the_dashboard_builder_on_the_snapshot(self, request,
                                                              session, fixture):
        """The parity invariant: the feeder IS the batched builder."""
        project = request.getfixturevalue(fixture)
        if isinstance(project, tuple):        # inheriting_project is (Project, resource)
            project = project[0]
        now = datetime.now()
        rows = {r['allocation_id']: r
                for r in project_allocation_state(session, now=now, projects=[project])}
        live, = get_projects_dashboard_data(session, [project])

        live_by_alloc = {r['allocation_id']: r for r in live['resources']
                         if r['allocation_id'] is not None}
        assert set(rows) == set(live_by_alloc)
        for aid, res in live_by_alloc.items():
            row = rows[aid]
            for key in ('allocated', 'used', 'remaining', 'percent_used', 'adjustments'):
                assert row[key] == pytest.approx(float(res[key])), (aid, key)
            assert row['is_inheriting'] == res['is_inheriting']
            assert row['root_projcode'] == res['root_projcode']
            assert row['activity_date'] == res['activity_date']
            expected_self = res['self_used'] if res['self_used'] is not None else res['used']
            assert row['self_used'] == pytest.approx(float(expected_self))


# ------------------------------------------------------------------- the task

class TestTheTask:

    def _table(self, session):
        return {r.allocation_id: r for r in session.query(AccountAllocationState).all()}

    def test_it_fills_the_table_and_reports_counts(self, session, hpc, ctx, scoped):
        project, account, alloc = _charged_project(session, hpc)
        scoped.append(project)

        result = refresh_allocation_state(ctx)

        assert result.detail['rows'] == 1
        assert result.detail['projects'] == 1
        assert result.detail['inserted'] == 1
        assert result.detail['current'] == 1
        row = self._table(session)[alloc.allocation_id]
        assert row.used == pytest.approx(100.0)
        assert row.refreshed_at is not None
        assert row.refreshed_at == datetime.fromisoformat(result.detail['refreshed_at'])

    def test_a_second_run_updates_in_place(self, session, hpc, ctx, scoped):
        project, account, alloc = _charged_project(session, hpc)
        scoped.append(project)
        refresh_allocation_state(ctx)
        extra = make_comp_charge_summary(session, charges=50.0,
                                         activity_date=datetime.now())
        extra.account_id = account.account_id
        session.flush()

        result = refresh_allocation_state(ctx)

        assert (result.detail['inserted'], result.detail['updated'],
                result.detail['deleted']) == (0, 1, 0)
        assert self._table(session)[alloc.allocation_id].used == pytest.approx(150.0)

    def test_a_row_that_left_the_candidate_set_is_deleted(self, session, hpc, ctx,
                                                          scoped):
        project, account, alloc = _charged_project(session, hpc)
        scoped.append(project)
        refresh_allocation_state(ctx)

        scoped.clear()
        other, _, other_alloc = _charged_project(session, hpc)
        scoped.append(other)
        result = refresh_allocation_state(ctx)

        assert result.detail['deleted'] == 1
        table = self._table(session)
        assert alloc.allocation_id not in table
        assert other_alloc.allocation_id in table

    def test_an_empty_projection_raises_and_leaves_the_table_alone(
            self, session, hpc, ctx, scoped):
        project, _, alloc = _charged_project(session, hpc)
        scoped.append(project)
        refresh_allocation_state(ctx)

        scoped.clear()
        with pytest.raises(RuntimeError) as err:
            refresh_allocation_state(ctx)

        assert err.value.task_detail['rows'] == 0
        assert alloc.allocation_id in self._table(session)

    def test_a_real_run_covers_the_snapshot(self, session, ctx):
        """Unscoped: the whole obfuscated snapshot. Shape only, plus a floor
        that a query drift would fall through."""
        result = refresh_allocation_state(ctx)

        assert result.detail['rows'] > 100
        assert result.detail['current'] <= result.detail['rows']
        assert set(result.detail) >= {'refreshed_at', 'projects', 'rows', 'current',
                                      'inserted', 'updated', 'deleted', 'elapsed_s'}
