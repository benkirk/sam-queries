"""Project.batch_get_account_charges / batch_get_subtree_charges, called directly.

Every usage figure goes through these two; the cases pin the contract their callers rely on.
"""

from datetime import datetime

import pytest

import sam.projects.projects as projects_module
from sam.projects.projects import Project
from sam.resources.resources import ResourceType

YEAR = (datetime(2026, 1, 1), datetime(2026, 12, 31, 23, 59, 59))
EMPTY = {'charges_by_type': {}, 'adjustment': 0.0}


@pytest.fixture(params=[True, False], ids=['values', 'no-values'], autouse=True)
def values_support(request, monkeypatch):
    """Run every case with and without VALUES row-constructor support."""
    monkeypatch.setattr(projects_module, '_values_cte_supported', request.param)


def _hpc(session):
    from factories.resources import make_resource
    return make_resource(session, commission_date=datetime(2000, 1, 1),
                         resource_type=session.query(ResourceType)
                         .filter_by(resource_type='HPC').one())


def _charge(session, account, day, charges):
    from factories.summaries import make_comp_charge_summary
    make_comp_charge_summary(session, account=account, activity_date=day, charges=charges)


def _info(key, account, start, end):
    project = account.project
    return {'key': key, 'account_id': account.account_id, 'resource_id': account.resource_id,
            'resource_type': 'HPC', 'activity_type': account.resource.activity_type,
            'tree_root': project.tree_root, 'tree_left': project.tree_left,
            'tree_right': project.tree_right, 'start_date': start, 'end_date': end}


def test_account_charges_follow_each_anchors_own_dates(session):
    from factories.projects import make_account, make_charge_adjustment, make_project
    account = make_account(session, project=make_project(session, facility_name='UNIV'),
                           resource=_hpc(session))
    for day, charges in ((datetime(2026, 3, 3), 40.0), (datetime(2026, 9, 14), 5.0),
                         (datetime(2026, 9, 15), 7.0), (datetime(2027, 1, 1), 500.0)):
        _charge(session, account, day, charges)
    make_charge_adjustment(session, account=account, amount=-10.0,
                           adjustment_date=datetime(2026, 3, 20, 14, 30))
    old = _info('old', account, datetime(2026, 1, 1), datetime(2026, 9, 14, 23, 59, 59))
    new = _info('new', account, datetime(2026, 9, 15), datetime(2026, 12, 31, 23, 59, 59))

    out = Project.batch_get_account_charges(session, [old, new])

    assert out == {'old': {'charges_by_type': {'comp': 45.0}, 'adjustment': -10.0},
                   'new': {'charges_by_type': {'comp': 7.0}, 'adjustment': 0.0}}
    without = Project.batch_get_account_charges(session, [old], include_adjustments=False)
    assert without == {'old': {'charges_by_type': {'comp': 45.0}, 'adjustment': 0.0}}


def test_subtree_charges_cover_descendants_on_the_anchors_resource(session):
    from factories.projects import make_account, make_charge_adjustment, make_project
    hpc, other = _hpc(session), _hpc(session)
    parent = make_project(session, facility_name='UNIV')
    child = make_project(session, facility_name='UNIV', parent=parent)
    pool = make_account(session, project=parent, resource=hpc)
    child_account = make_account(session, project=child, resource=hpc)
    elsewhere = make_account(session, project=child, resource=other)
    _charge(session, pool, datetime(2026, 2, 1), 3.0)
    _charge(session, child_account, datetime(2026, 4, 2), 11.0)
    _charge(session, child_account, datetime(2026, 8, 9), 4.0)
    _charge(session, elsewhere, datetime(2026, 4, 2), 900.0)      # another resource
    make_charge_adjustment(session, account=child_account, amount=6.0,
                           adjustment_date=datetime(2026, 8, 1, 8, 0))
    # Three date ranges on one set of tree coordinates, as a ('root', id) entry repeats them.
    infos = [_info(1, pool, *YEAR),
             _info(('root', 1), pool, datetime(2026, 1, 1), datetime(2026, 6, 30, 23, 59, 59)),
             _info('late', pool, datetime(2026, 7, 1), datetime(2026, 12, 31, 23, 59, 59))]

    out = Project.batch_get_subtree_charges(session, infos)

    assert out == {1: {'charges_by_type': {'comp': 18.0}, 'adjustment': 6.0},
                   ('root', 1): {'charges_by_type': {'comp': 14.0}, 'adjustment': 0.0},
                   'late': {'charges_by_type': {'comp': 4.0}, 'adjustment': 6.0}}


def test_an_anchor_without_charges_is_present_and_zeroed(session):
    from factories.projects import make_account, make_project
    hpc = _hpc(session)
    parent = make_project(session, facility_name='UNIV')
    make_project(session, facility_name='UNIV', parent=parent)
    account = make_account(session, project=parent, resource=hpc)
    info = _info(7, account, *YEAR)

    assert Project.batch_get_account_charges(session, [info]) == {7: EMPTY}
    assert Project.batch_get_subtree_charges(session, [info]) == {7: EMPTY}


def test_empty_input_returns_an_empty_dict(session):
    assert Project.batch_get_account_charges(session, []) == {}
    assert Project.batch_get_subtree_charges(session, []) == {}
