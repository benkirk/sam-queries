"""Unit tests for the per-user / per-day summary queries that power the
resource-details page's lazy tier rendering.

Three new query functions land alongside the existing detail queries:

  - get_user_summary_for_project: one row per user, with totals and
    COUNT(DISTINCT queue) / COUNT(DISTINCT activity_date).
  - get_daily_summary_for_project: one row per day, with totals and
    COUNT(DISTINCT username).
  - get_monthly_user_counts_for_project: per-month distinct-user count
    for the 3-level daily-breakdown table header.

The existing get_user_queue_breakdown_for_project gains a ``username=``
kwarg that threads through to query_comp_charge_summaries so the
new lazy subtree route can fetch one user's full breakdown.

All tests build a fresh CompChargeSummary fixture (Layer-2 factories
for the FK graph + direct rows for exact totals) so assertions can
pin concrete values rather than fuzzy snapshot data.
"""
from datetime import date

import pytest

from sam.queries.charges import (
    get_daily_summary_for_project,
    get_monthly_user_counts_for_project,
    get_user_queue_breakdown_for_project,
    get_user_summary_for_project,
)
from sam.summaries.comp_summaries import CompChargeSummary

from factories import (
    make_account,
    make_machine,
    make_project,
    make_queue,
    make_resource,
    make_resource_type,
    make_user,
    next_seq,
)

pytestmark = pytest.mark.unit


def _add_summary_row(
    session, *, project, resource, machine, username,
    queue_name, activity_date, num_jobs, core_hours, charges,
):
    """Insert a single comp_charge_summary row directly.

    Bypasses upsert_comp_charge_summary on purpose — these tests pin
    exact counts/totals against the query layer; the row-creation path
    is exercised separately in test_manage_summaries.py.
    """
    row = CompChargeSummary(
        activity_date=activity_date,
        username=username,
        act_username=username,
        projcode=project.projcode,
        act_projcode=project.projcode,
        machine=machine.name,
        machine_id=machine.machine_id,
        queue=queue_name,
        resource=resource.resource_name,
        num_jobs=num_jobs,
        core_hours=core_hours,
        charges=charges,
    )
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def comp_fixture(session):
    """A small fixture project on a fresh HPC resource with three users:

      - alice: single queue 'main', single date 2099-01-15  → single_triple
      - bob:   two queues 'main' / 'gpu', single date 2099-01-15  → multi-queue
      - carol: single queue 'main', two dates 2099-01-15 / 2099-02-10
                → single-queue / multi-date; spans two months
    """
    user_a = make_user(session, username='alice_' + next_seq('x'))
    user_b = make_user(session, username='bob_' + next_seq('x'))
    user_c = make_user(session, username='carol_' + next_seq('x'))
    project = make_project(session, lead=user_a)
    rt = make_resource_type(session, resource_type=next_seq('HPCRT'))
    resource = make_resource(session, resource_type=rt)
    make_account(session, project=project, resource=resource)
    machine = make_machine(session, resource=resource)
    queue_main = make_queue(session, resource=resource, queue_name='main_' + next_seq('x'))
    queue_gpu  = make_queue(session, resource=resource, queue_name='gpu_'  + next_seq('x'))

    # alice — single (queue, date)
    _add_summary_row(session, project=project, resource=resource, machine=machine,
                     username=user_a.username, queue_name=queue_main.queue_name,
                     activity_date=date(2099, 1, 15),
                     num_jobs=10, core_hours=100.0, charges=50.0)
    # bob — two queues, one date
    _add_summary_row(session, project=project, resource=resource, machine=machine,
                     username=user_b.username, queue_name=queue_main.queue_name,
                     activity_date=date(2099, 1, 15),
                     num_jobs=5, core_hours=20.0, charges=10.0)
    _add_summary_row(session, project=project, resource=resource, machine=machine,
                     username=user_b.username, queue_name=queue_gpu.queue_name,
                     activity_date=date(2099, 1, 15),
                     num_jobs=3, core_hours=30.0, charges=15.0)
    # carol — one queue, two dates (Jan + Feb)
    _add_summary_row(session, project=project, resource=resource, machine=machine,
                     username=user_c.username, queue_name=queue_main.queue_name,
                     activity_date=date(2099, 1, 15),
                     num_jobs=4, core_hours=40.0, charges=20.0)
    _add_summary_row(session, project=project, resource=resource, machine=machine,
                     username=user_c.username, queue_name=queue_main.queue_name,
                     activity_date=date(2099, 2, 10),
                     num_jobs=6, core_hours=60.0, charges=30.0)
    session.flush()
    return {
        'project': project,
        'resource': resource,
        'users': {'alice': user_a, 'bob': user_b, 'carol': user_c},
        'queues': {'main': queue_main.queue_name, 'gpu': queue_gpu.queue_name},
        'start': date(2099, 1, 1),
        'end':   date(2099, 2, 28),
    }


# ---------------------------------------------------------------------------
# get_user_summary_for_project
# ---------------------------------------------------------------------------

class TestUserSummary:

    def test_one_row_per_user_sorted_by_charges_desc(self, session, comp_fixture):
        rows = get_user_summary_for_project(
            session, comp_fixture['project'].projcode,
            comp_fixture['resource'].resource_name,
            comp_fixture['start'], comp_fixture['end'],
        )
        # alice 50 + bob 25 + carol 50 ⇒ alice 50, carol 50, bob 25.
        usernames = [r['username'] for r in rows]
        assert set(usernames) == {
            comp_fixture['users']['alice'].username,
            comp_fixture['users']['bob'].username,
            comp_fixture['users']['carol'].username,
        }
        # Charges are sorted desc; both alice and carol = 50, then bob = 25.
        assert rows[-1]['username'] == comp_fixture['users']['bob'].username
        assert rows[-1]['charges'] == 25.0

    def test_distinct_queue_and_date_counts(self, session, comp_fixture):
        rows = get_user_summary_for_project(
            session, comp_fixture['project'].projcode,
            comp_fixture['resource'].resource_name,
            comp_fixture['start'], comp_fixture['end'],
        )
        by_user = {r['username']: r for r in rows}

        # alice: single triple — both counts 1
        alice = by_user[comp_fixture['users']['alice'].username]
        assert alice['queue_count'] == 1
        assert alice['date_count']  == 1
        # any_queue / any_date filled so the inline drawer URL can be built
        assert alice['any_queue'] == comp_fixture['queues']['main']
        assert alice['any_date']  == '2099-01-15'

        # bob: two queues, one date
        bob = by_user[comp_fixture['users']['bob'].username]
        assert bob['queue_count'] == 2
        assert bob['date_count']  == 1

        # carol: one queue, two dates
        carol = by_user[comp_fixture['users']['carol'].username]
        assert carol['queue_count'] == 1
        assert carol['date_count']  == 2

    def test_aggregated_totals(self, session, comp_fixture):
        rows = get_user_summary_for_project(
            session, comp_fixture['project'].projcode,
            comp_fixture['resource'].resource_name,
            comp_fixture['start'], comp_fixture['end'],
        )
        by_user = {r['username']: r for r in rows}
        bob = by_user[comp_fixture['users']['bob'].username]
        assert bob['jobs']       == 8        # 5 + 3
        assert bob['core_hours'] == 50.0     # 20 + 30
        assert bob['charges']    == 25.0     # 10 + 15

    def test_date_window_excludes_outside_rows(self, session, comp_fixture):
        # January-only window — carol's Feb row is excluded so her totals shrink.
        rows = get_user_summary_for_project(
            session, comp_fixture['project'].projcode,
            comp_fixture['resource'].resource_name,
            date(2099, 1, 1), date(2099, 1, 31),
        )
        by_user = {r['username']: r for r in rows}
        carol = by_user[comp_fixture['users']['carol'].username]
        assert carol['date_count'] == 1
        assert carol['charges']    == 20.0


# ---------------------------------------------------------------------------
# get_user_queue_breakdown_for_project(username=...)  filter
# ---------------------------------------------------------------------------

class TestUserSubtreeFilter:

    def test_filters_to_single_user(self, session, comp_fixture):
        rows = get_user_queue_breakdown_for_project(
            session, comp_fixture['project'].projcode,
            comp_fixture['resource'].resource_name,
            comp_fixture['start'], comp_fixture['end'],
            username=comp_fixture['users']['bob'].username,
        )
        assert len(rows) == 1
        assert rows[0]['username'] == comp_fixture['users']['bob'].username
        # Two queues for bob → two queue entries in the nested breakdown.
        assert len(rows[0]['queues']) == 2

    def test_unknown_username_returns_empty(self, session, comp_fixture):
        rows = get_user_queue_breakdown_for_project(
            session, comp_fixture['project'].projcode,
            comp_fixture['resource'].resource_name,
            comp_fixture['start'], comp_fixture['end'],
            username='no-such-user-' + next_seq('x'),
        )
        assert rows == []


# ---------------------------------------------------------------------------
# get_daily_summary_for_project
# ---------------------------------------------------------------------------

class TestDailySummary:

    def test_one_row_per_day_sorted_desc(self, session, comp_fixture):
        rows = get_daily_summary_for_project(
            session, comp_fixture['project'].projcode,
            comp_fixture['resource'].resource_name,
            comp_fixture['start'], comp_fixture['end'],
        )
        # Two distinct dates in the fixture: 2099-01-15 (all three users)
        # and 2099-02-10 (carol only).
        assert [r['date'] for r in rows] == ['2099-02-10', '2099-01-15']

    def test_per_day_user_count(self, session, comp_fixture):
        rows = get_daily_summary_for_project(
            session, comp_fixture['project'].projcode,
            comp_fixture['resource'].resource_name,
            comp_fixture['start'], comp_fixture['end'],
        )
        by_date = {r['date']: r for r in rows}
        # 2099-01-15: alice + bob + carol = 3 distinct users
        assert by_date['2099-01-15']['user_count'] == 3
        # 2099-02-10: carol only = 1 distinct user
        assert by_date['2099-02-10']['user_count'] == 1

    def test_per_day_totals(self, session, comp_fixture):
        rows = get_daily_summary_for_project(
            session, comp_fixture['project'].projcode,
            comp_fixture['resource'].resource_name,
            comp_fixture['start'], comp_fixture['end'],
        )
        by_date = {r['date']: r for r in rows}
        # 2099-01-15 totals: alice 10/100/50 + bob (5+3)/(20+30)/(10+15) + carol 4/40/20
        jan = by_date['2099-01-15']
        assert jan['jobs']       == 10 + 5 + 3 + 4
        assert jan['core_hours'] == 100.0 + 20.0 + 30.0 + 40.0
        assert jan['charges']    == 50.0 + 10.0 + 15.0 + 20.0

    def test_month_field_groups_by_yyyy_mm(self, session, comp_fixture):
        rows = get_daily_summary_for_project(
            session, comp_fixture['project'].projcode,
            comp_fixture['resource'].resource_name,
            comp_fixture['start'], comp_fixture['end'],
        )
        months = sorted({r['month'] for r in rows})
        assert months == ['2099-01', '2099-02']


# ---------------------------------------------------------------------------
# get_monthly_user_counts_for_project
# ---------------------------------------------------------------------------

class TestMonthlyUserCounts:

    def test_per_month_distinct_user_count(self, session, comp_fixture):
        counts = get_monthly_user_counts_for_project(
            session, comp_fixture['project'].projcode,
            comp_fixture['resource'].resource_name,
            comp_fixture['start'], comp_fixture['end'],
        )
        # January saw alice + bob + carol; February only carol.
        assert counts.get('2099-01') == 3
        assert counts.get('2099-02') == 1

    def test_empty_window_returns_empty_dict(self, session, comp_fixture):
        counts = get_monthly_user_counts_for_project(
            session, comp_fixture['project'].projcode,
            comp_fixture['resource'].resource_name,
            date(2050, 1, 1), date(2050, 12, 31),
        )
        assert counts == {}
