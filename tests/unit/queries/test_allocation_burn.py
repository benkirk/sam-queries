"""get_allocation_burn: per-allocation monthly charges for the calendar's Burn view."""

from datetime import datetime

from sam.queries.allocations import get_allocation_burn
from sam.resources.resources import ResourceType

AS_OF = datetime(2026, 10, 10)
WINDOW = (datetime(2025, 10, 1), datetime(2027, 11, 1))


def _hpc(session):
    from factories.resources import make_resource
    return make_resource(session, commission_date=datetime(2000, 1, 1),
                         resource_type=session.query(ResourceType)
                         .filter_by(resource_type='HPC').one())


def _charge(session, account, day, charges):
    from factories.summaries import make_comp_charge_summary
    make_comp_charge_summary(session, account=account, activity_date=day, charges=charges)


def _burn(session, hpc, as_of=AS_OF, window=WINDOW):
    return get_allocation_burn(session, resource_name=[hpc.resource_name],
                               window_start=window[0], window_end=window[1], as_of=as_of)


def test_mid_month_renewal_splits_its_month(session):
    from factories.projects import make_account, make_allocation, make_project
    hpc = _hpc(session)
    account = make_account(session, project=make_project(session, facility_name='UNIV'),
                           resource=hpc)
    old = make_allocation(session, account=account, amount=1000.0,
                          start_date=datetime(2025, 9, 15),
                          end_date=datetime(2026, 9, 14, 23, 59, 59))
    new = make_allocation(session, account=account, amount=1000.0,
                          start_date=datetime(2026, 9, 15),
                          end_date=datetime(2027, 9, 14, 23, 59, 59))
    for day, charges in ((datetime(2025, 9, 20), 999.0),   # before the window
                         (datetime(2026, 3, 3), 40.0), (datetime(2026, 3, 28), 2.0),
                         (datetime(2026, 9, 1), 10.0), (datetime(2026, 9, 14), 5.0),
                         (datetime(2026, 9, 15), 7.0), (datetime(2026, 9, 30), 1.0),
                         (datetime(2026, 10, 10), 3.0),
                         (datetime(2026, 10, 11), 500.0)):  # after the as-of day
        _charge(session, account, day, charges)

    burn = _burn(session, hpc)

    assert burn[old.allocation_id] == {202603: 42.0, 202609: 15.0}
    assert burn[new.allocation_id] == {202609: 8.0, 202610: 3.0}


def test_subtree_pool_counts_a_child_projects_charges(session):
    from factories.projects import make_account, make_allocation, make_project
    hpc = _hpc(session)
    parent = make_project(session, facility_name='UNIV')
    child = make_project(session, facility_name='UNIV', parent=parent)
    pool = make_allocation(session, account=make_account(session, project=parent, resource=hpc),
                           amount=5000.0, start_date=datetime(2026, 1, 1),
                           end_date=datetime(2026, 12, 31, 23, 59, 59))
    child_account = make_account(session, project=child, resource=hpc)
    _charge(session, child_account, datetime(2026, 4, 2), 11.0)
    _charge(session, child_account, datetime(2026, 5, 9), 4.0)

    assert _burn(session, hpc)[pool.allocation_id] == {202604: 11.0, 202605: 4.0}


def test_adjustments_land_in_their_month(session):
    from factories.projects import (make_account, make_allocation, make_charge_adjustment,
                                    make_project)
    hpc = _hpc(session)
    account = make_account(session, project=make_project(session, facility_name='UNIV'),
                           resource=hpc)
    alloc = make_allocation(session, account=account, amount=100.0,
                            start_date=datetime(2026, 1, 1),
                            end_date=datetime(2026, 12, 31, 23, 59, 59))
    _charge(session, account, datetime(2026, 2, 10), 30.0)
    make_charge_adjustment(session, account=account, amount=-10.0,
                           adjustment_date=datetime(2026, 2, 20, 14, 30))
    make_charge_adjustment(session, account=account, amount=6.0,
                           adjustment_date=datetime(2026, 6, 1, 8, 0))

    assert _burn(session, hpc)[alloc.allocation_id] == {202602: 20.0, 202606: 6.0}


def test_window_and_as_of_bound_what_counts(session):
    from factories.projects import make_account, make_allocation, make_project
    hpc = _hpc(session)
    account = make_account(session, project=make_project(session, facility_name='UNIV'),
                           resource=hpc)
    long = make_allocation(session, account=account, amount=9000.0,
                           start_date=datetime(2024, 1, 1),
                           end_date=datetime(2026, 12, 31, 23, 59, 59))
    future = make_allocation(session, account=make_account(
        session, project=make_project(session, facility_name='UNIV'), resource=hpc),
        amount=10.0, start_date=datetime(2026, 11, 1), end_date=datetime(2027, 10, 31))
    _charge(session, account, datetime(2025, 9, 30), 50.0)   # before the window start
    _charge(session, account, datetime(2025, 10, 1), 2.0)

    burn = _burn(session, hpc)

    assert burn[long.allocation_id] == {202510: 2.0}
    assert future.allocation_id not in burn
    assert all(isinstance(k, int) for k in burn[long.allocation_id])


def test_union_all_anchors_where_values_is_unsupported(session, monkeypatch):
    import sam.accounting.calculator as calculator
    from factories.projects import make_account, make_allocation, make_project
    hpc = _hpc(session)
    account = make_account(session, project=make_project(session, facility_name='UNIV'),
                           resource=hpc)
    alloc = make_allocation(session, account=account, amount=100.0,
                            start_date=datetime(2026, 1, 1),
                            end_date=datetime(2026, 12, 31, 23, 59, 59))
    _charge(session, account, datetime(2026, 3, 4), 9.0)
    monkeypatch.setattr(calculator, '_values_supported', False)

    assert _burn(session, hpc)[alloc.allocation_id] == {202603: 9.0}


def test_burn_caches_in_its_own_bucket(monkeypatch):
    import sam.queries.usage_cache as uc
    monkeypatch.delenv('CACHE_REDIS_URL', raising=False)
    monkeypatch.setenv('ALLOCATION_USAGE_CACHE_TTL', '0')
    monkeypatch.setenv('ALLOCATION_BURN_CACHE_TTL', '60')
    monkeypatch.setenv('ALLOCATION_BURN_CACHE_SIZE', '5')
    calls = []
    monkeypatch.setattr(uc, 'get_allocation_burn', lambda *a, **k: calls.append(1) or {1: {}})
    uc._CACHE.reset_for_tests(disabled=False)
    try:
        for _ in range(2):
            uc.cached_allocation_burn(None, resource_name=['Derecho'], window_start=WINDOW[0],
                                      window_end=WINDOW[1], as_of=AS_OF)
        assert len(calls) == 1
        assert uc.burn_cache_info()['name'] == 'allocation_burn'
    finally:
        uc._CACHE.reset_for_tests(disabled=False)


def test_month_sums_add_up_to_usage_rows_total_used(session):
    """The burn builder and the batch charge builders share one join: pin them together."""
    from factories.projects import (make_account, make_allocation, make_charge_adjustment,
                                    make_project)
    from sam.queries.allocations import get_allocation_usage_rows
    hpc = _hpc(session)
    span = dict(start_date=datetime(2026, 1, 1), end_date=datetime(2026, 12, 31, 23, 59, 59))
    leaf = make_account(session, project=make_project(session, facility_name='UNIV'), resource=hpc)
    parent = make_project(session, facility_name='UNIV')
    child = make_account(session, project=make_project(session, facility_name='UNIV', parent=parent),
                         resource=hpc)
    allocs = [make_allocation(session, account=leaf, amount=1000.0, **span),
              make_allocation(session, account=make_account(session, project=parent, resource=hpc),
                              amount=5000.0, **span)]
    for account in (leaf, child):
        _charge(session, account, datetime(2026, 2, 10), 30.0)
        _charge(session, account, datetime(2026, 9, 30), 12.5)
        _charge(session, account, datetime(2026, 10, 11), 500.0)   # after the as-of day
        make_charge_adjustment(session, account=account, amount=-4.0,
                               adjustment_date=datetime(2026, 5, 5, 9, 0))

    burn = _burn(session, hpc)
    used = {r['allocation_id']: r['total_used'] for r in get_allocation_usage_rows(
        session, resource_name=[hpc.resource_name], window_start=WINDOW[0], window_end=WINDOW[1],
        as_of=AS_OF)}

    for alloc in allocs:
        assert sum(burn[alloc.allocation_id].values()) == used[alloc.allocation_id] == 38.5
