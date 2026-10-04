"""get_charges_by_facility_type: charges over a window, across allocation renewals."""

from datetime import datetime, timedelta

from factories import make_account, make_project, make_resource
from factories.projects import make_charge_adjustment
from factories.summaries import make_comp_charge_summary
from sam.queries.charges import get_charges_by_facility_type, get_charges_by_project

END = datetime(2026, 9, 30)


def _setup(session, facility_name='UNIV'):
    resource = make_resource(session)
    project = make_project(session, facility_name=facility_name)
    return resource, make_account(session, project=project, resource=resource)


def _totals(session, resource, start=END - timedelta(days=89), end=END):
    return {(r['facility'], r['allocation_type']): r['charges']
            for r in get_charges_by_facility_type(session, [resource.resource_name], start, end)}


def test_window_is_inclusive_and_ignores_allocation_dates(session):
    """An account spans renewals, so charges before any current allocation still count."""
    resource, account = _setup(session)
    for day, charges in ((END - timedelta(days=89), 10.0), (END, 5.0),
                         (END - timedelta(days=90), 100.0), (END + timedelta(days=1), 100.0)):
        make_comp_charge_summary(session, account=account, activity_date=day, charges=charges)
    alloc_type = account.project.allocation_type.allocation_type
    assert _totals(session, resource) == {('UNIV', alloc_type): 15.0}


def test_adjustments_count_through_the_last_day(session):
    resource, account = _setup(session)
    make_comp_charge_summary(session, account=account, activity_date=END, charges=20.0)
    make_charge_adjustment(session, account=account, amount=-5.0,
                           adjustment_date=END.replace(hour=17))
    assert sum(_totals(session, resource).values()) == 15.0


def test_project_without_a_type_groups_under_none(session):
    resource = make_resource(session)
    account = make_account(session, project=make_project(session), resource=resource)
    make_comp_charge_summary(session, account=account, activity_date=END, charges=7.0)
    assert _totals(session, resource) == {(None, None): 7.0}


def test_no_resources(session):
    assert get_charges_by_facility_type(session, [], END, END) == []


def test_by_project_splits_what_by_facility_type_sums(session):
    resource, a = _setup(session)
    b = make_account(session, project=make_project(session, facility_name='WNA'), resource=resource)
    make_comp_charge_summary(session, account=a, activity_date=END, charges=4.0)
    make_comp_charge_summary(session, account=b, activity_date=END, charges=6.0)
    make_charge_adjustment(session, account=b, amount=-1.0, adjustment_date=END)
    by_project = get_charges_by_project(session, [resource.resource_name], END - timedelta(days=1), END)
    assert by_project == {a.project.projcode: 4.0, b.project.projcode: 5.0}
    assert sum(by_project.values()) == sum(_totals(session, resource).values())


def test_by_project_no_resources(session):
    assert get_charges_by_project(session, [], END, END) == {}
