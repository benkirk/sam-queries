"""Usage as of a day: get_allocation_usage_rows (per allocation in a window) and
get_allocation_summary_with_usage (allocations active at a date)."""

from datetime import datetime, timedelta

from sam.queries.allocations import get_allocation_summary_with_usage, get_allocation_usage_rows
from sam.resources.resources import ResourceType

AS_OF = datetime(2026, 10, 1)
WINDOW = timedelta(days=180)


def _build(session):
    from factories.projects import make_account, make_allocation, make_project
    from factories.resources import make_resource
    from factories.summaries import make_comp_charge_summary

    hpc = make_resource(session, commission_date=datetime(2000, 1, 1),
                        resource_type=session.query(ResourceType)
                        .filter_by(resource_type='HPC').one())
    project = make_project(session, facility_name='UNIV')
    account = make_account(session, project=project, resource=hpc)

    def alloc(start, end, amount):
        return make_allocation(session, account=account, amount=amount,
                               start_date=start, end_date=end)

    allocs = {
        'ended': alloc(datetime(2025, 10, 1), datetime(2026, 9, 30, 23, 59, 59), 1000.0),
        'current': alloc(AS_OF, datetime(2027, 9, 30, 23, 59, 59), 500.0),
        'future': alloc(datetime(2026, 11, 1), datetime(2027, 10, 31, 23, 59, 59), 300.0),
        'outside': alloc(datetime(2024, 1, 1), datetime(2025, 1, 1, 23, 59, 59), 50.0),
    }
    for day, charges in ((datetime(2026, 6, 1), 100.0), (datetime(2026, 9, 30), 50.0),
                         (AS_OF, 20.0), (datetime(2026, 10, 5), 7.0)):
        make_comp_charge_summary(session, charges=charges, activity_date=day).account_id = \
            account.account_id
    session.flush()
    return hpc, project, allocs


def test_rows_per_allocation_with_usage_as_of(session):
    hpc, project, allocs = _build(session)
    rows = get_allocation_usage_rows(
        session, resource_name=[hpc.resource_name],
        window_start=AS_OF - WINDOW, window_end=AS_OF + WINDOW, as_of=AS_OF)

    by_start = {r['start_date']: r for r in rows}
    assert len(rows) == 3
    assert allocs['outside'].start_date not in by_start
    assert {r['projcode'] for r in rows} == {project.projcode}
    assert {r['facility'] for r in rows} == {'UNIV'}

    assert by_start[allocs['ended'].start_date]['total_used'] == 150.0
    # Charged through the end of the as_of day; the 10-05 charge is after it.
    assert by_start[allocs['current'].start_date]['total_used'] == 20.0
    assert by_start[allocs['future'].start_date]['total_used'] == 0.0
    assert by_start[allocs['future'].start_date]['total_amount'] == 300.0


def test_total_used_counts_charges_before_the_window(session):
    from factories.summaries import make_comp_charge_summary
    hpc, _, allocs = _build(session)
    account_id = allocs['ended'].account_id
    make_comp_charge_summary(session, charges=1000.0,
                             activity_date=datetime(2025, 12, 1)).account_id = account_id
    session.flush()
    rows = get_allocation_usage_rows(
        session, resource_name=[hpc.resource_name],
        window_start=AS_OF - WINDOW, window_end=AS_OF + WINDOW, as_of=AS_OF)
    ended = next(r for r in rows if r['start_date'] == allocs['ended'].start_date)
    assert ended['total_used'] == 1150.0
    assert 'window_used' not in ended


def test_summary_usage_stops_at_the_active_at_day(session):
    hpc, project, _ = _build(session)

    def used(active_at):
        rows = get_allocation_summary_with_usage(
            session, resource_name=hpc.resource_name, projcode=project.projcode,
            active_at=active_at)
        assert len(rows) == 1
        return rows[0]['total_used']

    # A past date inside the ended allocation: its 2026-09-30 charge is later.
    assert used(datetime(2026, 7, 1)) == 100.0
    # The current allocation through the end of AS_OF: the 10-05 charge is later.
    assert used(AS_OF) == 20.0
    assert used(datetime(2026, 10, 5)) == 27.0
