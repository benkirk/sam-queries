"""get_allocation_summary over built trees: roots-only rollups, summed annual rates, tree fields."""

from datetime import datetime

import pytest

from sam.queries.allocations import get_allocation_summary, get_allocation_summary_with_usage
from sam.resources.resources import ResourceType

AT = datetime(2026, 6, 1)
ONE_YEAR = (datetime(2026, 1, 1), datetime(2026, 12, 31, 23, 59, 59))     # 365 days
TWO_YEARS = (datetime(2026, 1, 1), datetime(2027, 12, 31, 23, 59, 59))    # 730 days


@pytest.fixture
def tree(session):
    """A shared pool, a subdivided award, a project with two overlapping allocations, an open-ended one."""
    from factories.projects import make_account, make_allocation, make_allocation_type, make_project
    from factories.resources import make_resource

    hpc = make_resource(session, commission_date=datetime(2000, 1, 1),
                        resource_type=session.query(ResourceType).filter_by(resource_type='HPC').one())
    atype = make_allocation_type(session, facility_name='UNIV')

    def project(parent=None):
        return make_project(session, allocation_type=atype, parent=parent)

    accounts = {}

    def alloc(proj, amount, span=ONE_YEAR, parent=None):
        account = accounts.get(proj.project_id) or make_account(session, project=proj, resource=hpc)
        accounts[proj.project_id] = account
        return make_allocation(session, account=account, amount=amount,
                               start_date=span[0], end_date=span[1], parent=parent)

    pool = project()
    pool_alloc = alloc(pool, 1000.0)
    members = [project(parent=pool) for _ in range(2)]
    for m in members:
        alloc(m, 1000.0, parent=pool_alloc)

    award = project()
    alloc(award, 900.0)
    carved = [project(parent=award) for _ in range(2)]
    alloc(carved[0], 400.0)
    alloc(carved[1], 500.0)

    overlapping = project()
    alloc(overlapping, 300.0)
    alloc(overlapping, 400.0, span=TWO_YEARS)

    open_ended = project()
    alloc(open_ended, 100.0).end_date = None
    session.flush()
    return dict(resource=hpc.resource_name, pool=pool, members=members, award=award,
                carved=carved, overlapping=overlapping, open_ended=open_ended)


def _rows(session, tree, **kw):
    return get_allocation_summary(session, resource_name=tree['resource'], active_at=AT, **kw)


def test_roots_only_rollup_counts_each_tree_once(session, tree):
    (every,) = _rows(session, tree, projcode='TOTAL')
    (roots,) = _rows(session, tree, projcode='TOTAL', root_only=True)

    assert every['total_amount'] == 1000 * 3 + 900 + 400 + 500 + 300 + 400 + 100
    assert roots['total_amount'] == 1000 + 900 + 300 + 400 + 100
    assert roots['count'] == 5


def test_rollup_rate_is_the_sum_of_member_rates(session, tree):
    by_code = {r['projcode']: r for r in _rows(session, tree, root_only=True)}
    (roots,) = _rows(session, tree, projcode='TOTAL', root_only=True)

    assert roots['annualized_rate'] == pytest.approx(sum(r['annualized_rate'] for r in by_code.values()))
    assert roots['is_open_ended'] and roots['open_ended_count'] == 1
    assert roots['duration_days'] is None


def test_project_with_overlapping_allocations_gets_a_rate(session, tree):
    by_code = {r['projcode']: r for r in _rows(session, tree)}
    row = by_code[tree['overlapping'].projcode]

    assert row['count'] == 2
    assert row['annualized_rate'] == pytest.approx(300.0 + 400.0 / 2)
    assert row['duration_days'] is None
    assert by_code[tree['award'].projcode]['duration_days'] == 365
    assert by_code[tree['award'].projcode]['annualized_rate'] == pytest.approx(900.0)
    assert by_code[tree['open_ended'].projcode]['is_open_ended']


def test_tree_fields_without_usage(session, tree):
    by_code = {r['projcode']: r for r in _rows(session, tree)}
    pool, award = tree['pool'].projcode, tree['award'].projcode

    member = by_code[tree['members'][0].projcode]
    assert (member['is_root'], member['is_inheriting']) == (False, True)
    assert member['parent_projcode'] == member['project_root_projcode'] == pool

    child = by_code[tree['carved'][0].projcode]
    assert (child['is_root'], child['is_inheriting']) == (False, False)
    assert child['project_root_projcode'] == award

    root = by_code[award]
    assert (root['is_root'], root['parent_projcode'], root['project_root_projcode']) == (True, None, award)


def test_usage_marks_pool_members(session, tree):
    rows = get_allocation_summary_with_usage(session, resource_name=tree['resource'], active_at=AT)
    by_code = {r['projcode']: r for r in rows}

    member = by_code[tree['members'][0].projcode]
    assert member['is_inheriting'] and member['root_projcode'] == tree['pool'].projcode
    assert member['self_used'] == 0.0
    assert by_code[tree['carved'][0].projcode]['is_inheriting'] is False
