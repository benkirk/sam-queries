"""The shared deactivation seam: `unique_projects` + `manage.deactivate_projects`.

Behavior lives here rather than in the task's test file, and every case scopes
its query with a factory-built `resource_name`. That is not fussiness: every
test container runs the obfuscated production snapshot (~22,000 allocations), so
an unscoped "expired 90+ days" query sweeps real rows and no count assertion
could ever hold. `deactivate_projects` itself takes a list and runs no query at
all, which is what lets its cases be plain fixtures.

The task's own wiring — occurrence conversion, registration, misfire policy —
is `test_task_deactivate_expired.py`.
"""

from datetime import datetime, timedelta

import pytest
from factories.projects import make_account, make_allocation, make_project
from factories.resources import make_resource

from sam.manage import deactivate_projects
from sam.queries.expirations import (
    DEACTIVATION_MIN_DAYS_EXPIRED,
    get_projects_with_expired_allocations,
    unique_projects,
)

pytestmark = pytest.mark.unit

#: A fixed reference instant, passed as `now=` so nothing here depends on when
#: the suite runs.
AS_OF = datetime(2026, 9, 3, 4, 30)


def _expired_project(session, resource, *, days_expired, active=True,
                     projcode=None):
    """A project whose latest allocation ended `days_expired` before AS_OF."""
    project = make_project(session, active=active, facility_name='UNIV',
                           projcode=projcode)
    account = make_account(session, project=project, resource=resource)
    make_allocation(session, account=account,
                    start_date=AS_OF - timedelta(days=days_expired + 365),
                    end_date=AS_OF - timedelta(days=days_expired))
    return project


@pytest.fixture
def resource(session):
    """A private resource, so every query below sees only this test's rows."""
    return make_resource(session)


def _select(session, resource, **kwargs):
    kwargs.setdefault('min_days_expired', DEACTIVATION_MIN_DAYS_EXPIRED)
    kwargs.setdefault('max_days_expired', None)
    return get_projects_with_expired_allocations(
        session, resource_name=resource.resource_name, now=AS_OF, **kwargs)


# ------------------------------------------------------------------ selection

class TestTheWindow:

    def test_the_floor_excludes_a_project_inside_the_grace_period(
            self, session, resource):
        _expired_project(session, resource, days_expired=89)

        assert _select(session, resource) == []

    def test_the_floor_includes_a_project_past_it(self, session, resource):
        project = _expired_project(session, resource, days_expired=91)

        assert [p.projcode for p in unique_projects(_select(session, resource))] \
            == [project.projcode]

    def test_no_ceiling_reaches_the_long_dead(self, session, resource):
        """THE bug the shipped button had. With `max_days_expired=365` this
        project — 400 days gone — was exempt, which is backwards: the ceiling
        excused exactly the projects that had been dead longest. Measured on a
        prod snapshot, the 90-365 window selected 0 while 6 sat 106-440 days
        expired."""
        project = _expired_project(session, resource, days_expired=400)

        assert unique_projects(_select(session, resource)) == [project]
        assert _select(session, resource, max_days_expired=365) == []

    def test_an_already_inactive_project_is_not_swept_again(
            self, session, resource):
        _expired_project(session, resource, days_expired=400, active=False)

        assert _select(session, resource) == []

    def test_now_pins_the_window_against_the_wall_clock(self, session, resource):
        """A task passes its occurrence here. Same project, two reference
        instants a month apart, two different answers — which is the whole
        reason `now=` exists."""
        _expired_project(session, resource, days_expired=91)

        assert len(_select(session, resource)) == 1
        assert get_projects_with_expired_allocations(
            session, min_days_expired=DEACTIVATION_MIN_DAYS_EXPIRED,
            max_days_expired=None, resource_name=resource.resource_name,
            now=AS_OF - timedelta(days=30)) == []


class TestUniqueProjects:

    def test_the_expiration_queries_do_not_actually_duplicate_today(
            self, session, resource):
        """Pins the fact the guard is guarding against, so a change is visible.

        `_get_latest_allocation_subquery` ends in `LIMIT 1`, so these queries
        emit one row per project however many expired allocations it has — the
        admin route's long-standing "a project can have multiple expired
        allocations" comment was wrong. Verified against a production snapshot
        too: 132/95/6 rows at three windows, zero duplicates.
        """
        project = make_project(session, active=True, facility_name='UNIV')
        account = make_account(session, project=project, resource=resource)
        for days in (500, 300, 120):
            make_allocation(session, account=account,
                            start_date=AS_OF - timedelta(days=days + 365),
                            end_date=AS_OF - timedelta(days=days))

        rows = _select(session, resource)

        assert len(rows) == 1
        assert len(unique_projects(rows)) == 1

    def test_a_repeated_project_is_collapsed(self, session):
        """The guard itself, on the shape `get_all_expiring_allocations` returns:
        every allocation, so genuinely several rows per project."""
        project = make_project(session, projcode='ZZDUP001')

        collapsed = unique_projects([(project, None, 'Derecho', 100),
                                     (project, None, 'Casper', 100)])

        assert collapsed == [project]

    def test_first_seen_order_is_preserved(self, session):
        """The result arrives most-expired-first and must stay that way — it is
        the order `sam-admin` echoes back after deactivating."""
        a = make_project(session, projcode='ZZORDER1')
        b = make_project(session, projcode='ZZORDER2')

        assert [p.projcode for p in
                unique_projects([(a, None, None, 9), (b, None, None, 8),
                                 (a, None, None, 7)])] == ['ZZORDER1', 'ZZORDER2']

    def test_an_empty_result_is_an_empty_list(self, session):
        assert unique_projects([]) == []


# ---------------------------------------------------------------- the write

class TestDeactivateProjects:

    def test_it_deactivates_and_stamps_every_project(self, session, resource):
        projects = [_expired_project(session, resource, days_expired=100 + n)
                    for n in range(3)]

        outcome = deactivate_projects(session, projects, when=AS_OF)

        assert outcome.count == 3
        assert set(outcome.projcodes) == {p.projcode for p in projects}
        assert all(not p.is_active for p in projects)
        assert all(p.inactivate_time == AS_OF for p in projects)

    def test_the_whole_batch_shares_one_stamp(self, session, resource):
        """Not one `datetime.now()` per project — a run is one event, and the
        column is what the user card renders as 'inactive since'."""
        projects = [_expired_project(session, resource, days_expired=100 + n)
                    for n in range(3)]

        outcome = deactivate_projects(session, projects)

        assert len({p.inactivate_time for p in projects}) == 1
        assert projects[0].inactivate_time == outcome.when

    def test_it_flushes_but_does_not_commit(self, session, resource):
        """The house convention, and load-bearing for the scheduled task: the
        runner owns the transaction, so a commit in here would make its rollback
        paths meaningless."""
        project = _expired_project(session, resource, days_expired=100)

        deactivate_projects(session, [project], when=AS_OF)

        assert not session.dirty              # flushed
        assert session.in_transaction()       # but still open

    def test_an_empty_batch_is_a_no_op(self, session):
        outcome = deactivate_projects(session, [], when=AS_OF)

        assert outcome.count == 0
        assert outcome.projcodes == ()
        assert outcome.when == AS_OF

    def test_the_result_order_matches_the_input_order(self, session, resource):
        projects = [_expired_project(session, resource, days_expired=100,
                                     projcode=f'ZZBATCH{n}') for n in range(3)]

        outcome = deactivate_projects(session, projects, when=AS_OF)

        assert outcome.projcodes == ('ZZBATCH0', 'ZZBATCH1', 'ZZBATCH2')

    def test_select_then_deactivate_round_trips(self, session, resource):
        """The composition all three callers perform, end to end: what the
        window selected is gone from the window afterwards."""
        _expired_project(session, resource, days_expired=400)

        deactivate_projects(session, unique_projects(_select(session, resource)),
                            when=AS_OF)

        assert _select(session, resource) == []
