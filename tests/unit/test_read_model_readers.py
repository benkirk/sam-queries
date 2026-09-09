"""Readers with the read-model on must equal the live computation, off must
never touch the table. Each seam is checked flag-on == flag-off on snapshot
fixtures after feeding the table from the projection, and a batch primitive
is armed to raise so a served scope is provably served, not recomputed.
"""

from datetime import datetime

import pytest

from sam.projects.projects import Project
from sam.queries.allocation_state import db_now, project_allocation_state
from sam.queries.allocations import get_allocation_summary_with_usage
from sam.queries.dashboard import (
    _build_project_resources_data,
    _build_user_projects_resources_batched,
    get_projects_dashboard_data,
)
from sam.summaries.allocation_state import AccountAllocationState

pytestmark = pytest.mark.unit

SCALAR = ('resource_name', 'allocation_id', 'parent_allocation_id', 'is_inheriting',
          'account_id', 'status', 'start_date', 'end_date', 'days_until_expiration',
          'date_group_key', 'bar_state', 'resource_type', 'root_projcode',
          'activity_date', 'rolling_30', 'rolling_90')
FLOAT = ('allocated', 'used', 'remaining', 'percent_used', 'adjustments', 'elapsed_pct')
OPTIONAL_FLOAT = ('self_used', 'self_percent_used')


def assert_resources_equal(live, served):
    assert [r['resource_name'] for r in live] == [r['resource_name'] for r in served]
    for a, b in zip(live, served):
        ctx = a['resource_name']
        for f in SCALAR:
            assert a[f] == b[f], (ctx, f, a[f], b[f])
        for f in FLOAT:
            assert float(a[f]) == pytest.approx(float(b[f])), (ctx, f)
        for f in OPTIONAL_FLOAT:
            if a[f] is None or b[f] is None:
                assert a[f] == b[f], (ctx, f)
            else:
                assert float(a[f]) == pytest.approx(float(b[f])), (ctx, f)
        assert a['charges_by_type'].keys() == b['charges_by_type'].keys(), ctx
        for k in a['charges_by_type']:
            assert a['charges_by_type'][k] == pytest.approx(b['charges_by_type'][k]), (ctx, k)


def _feed(session, projects=None):
    rows = project_allocation_state(session, now=datetime.now(), projects=projects)
    AccountAllocationState.bulk_replace(session, rows, refreshed_at=db_now(session))
    return rows


@pytest.fixture
def flag(monkeypatch):
    """Flip the reader flag; the table is fed separately."""
    def set_to(on):
        monkeypatch.setenv('READ_MODEL_ENABLED', '1' if on else '0')
    set_to(False)
    return set_to


@pytest.fixture
def armed(monkeypatch):
    """Make the batched charge primitives raise, so a served scope is proven."""
    def boom(*a, **k):
        raise AssertionError('live rollup ran while the read-model should serve')
    monkeypatch.setattr(Project, 'batch_get_subtree_charges', classmethod(boom))
    monkeypatch.setattr(Project, 'batch_get_account_charges', classmethod(boom))


def _projects_of(request, name):
    obj = request.getfixturevalue(name)
    if name == 'multi_project_user':
        return sorted(obj.active_projects(), key=lambda p: p.projcode)
    if isinstance(obj, tuple):
        obj = obj[0]
    return [obj]


class TestDashboards:

    @pytest.mark.parametrize('fixture', ['multi_project_user', 'subtree_project',
                                         'inheriting_project'])
    def test_batched_builder_on_equals_off(self, request, session, flag, fixture):
        projects = _projects_of(request, fixture)
        live = get_projects_dashboard_data(session, projects)

        _feed(session, projects)
        flag(True)
        served = get_projects_dashboard_data(session, projects)

        for a, b in zip(live, served):
            assert a['project'] is b['project']
            assert a['has_children'] == b['has_children']
            assert_resources_equal(a['resources'], b['resources'])

    @pytest.mark.parametrize('fixture', ['subtree_project', 'inheriting_project'])
    def test_per_project_builder_on_equals_off(self, request, session, flag, fixture):
        project, = _projects_of(request, fixture)
        live = _build_project_resources_data(project)

        _feed(session, [project])
        flag(True)
        served = _build_project_resources_data(project)

        # Served rows come from the batched builder, which sorts by resource;
        # the live per-project path yields account load order.
        by_name = lambda rows: sorted(rows, key=lambda r: r['resource_name'])
        assert_resources_equal(by_name(live), by_name(served))

    def test_a_served_scope_runs_no_rollup(self, request, session, flag, subtree_project):
        _feed(session, [subtree_project])            # the feed itself needs the primitives
        flag(True)
        request.getfixturevalue('armed')
        assert get_projects_dashboard_data(session, [subtree_project])[0]['resources']

    def test_an_empty_table_falls_back_to_live(self, session, flag, subtree_project):
        live = get_projects_dashboard_data(session, [subtree_project])
        flag(True)
        served = get_projects_dashboard_data(session, [subtree_project])
        assert_resources_equal(live[0]['resources'], served[0]['resources'])

    def test_a_historical_as_of_stays_live(self, request, session, flag, subtree_project):
        """The gate refuses any day but today, so the armed rollup must run."""
        _feed(session, [subtree_project])
        flag(True)
        request.getfixturevalue('armed')
        with pytest.raises(AssertionError, match='live rollup ran'):
            _build_user_projects_resources_batched(session, [subtree_project],
                                                   active_at=datetime(2024, 1, 15))


class TestAllocationSummary:

    def _rows_equal(self, live, served):
        assert len(live) == len(served)
        for a, b in zip(live, served):
            assert a.keys() == b.keys(), a.get('projcode')
            for k, v in a.items():
                if isinstance(v, float):
                    assert v == pytest.approx(b[k]), (a.get('projcode'), k)
                elif k == 'charges_by_type':
                    assert v.keys() == b[k].keys()
                    for t in v:
                        assert v[t] == pytest.approx(b[k][t]), (a.get('projcode'), t)
                else:
                    assert v == b[k], (a.get('projcode'), k)

    @pytest.mark.parametrize('fixture', ['subtree_project', 'inheriting_project'])
    def test_one_project_on_equals_off(self, request, session, flag, fixture):
        project, = _projects_of(request, fixture)
        live = get_allocation_summary_with_usage(session, projcode=project.projcode)

        _feed(session)                                 # whole snapshot: scope is the tree
        flag(True)
        served = get_allocation_summary_with_usage(session, projcode=project.projcode)

        self._rows_equal(live, served)

    def test_one_resource_over_the_snapshot_on_equals_off(self, request, session, flag,
                                                          hpc_resource):
        """The fstree-shaped scope: every root allocation on one resource."""
        name = hpc_resource.resource_name
        live = get_allocation_summary_with_usage(session, resource_name=name,
                                                 root_only=True)

        _feed(session)
        flag(True)
        request.getfixturevalue('armed')
        served = get_allocation_summary_with_usage(session, resource_name=name,
                                                   root_only=True)
        self._rows_equal(live, served)

    def test_without_adjustments_stays_live(self, request, session, flag, subtree_project):
        _feed(session)
        flag(True)
        request.getfixturevalue('armed')
        with pytest.raises(AssertionError, match='live rollup ran'):
            get_allocation_summary_with_usage(session, projcode=subtree_project.projcode,
                                              include_adjustments=False)



class TestFstree:

    def test_one_resource_on_equals_off(self, request, session, flag, hpc_resource):
        """The byte-frozen shape: the whole payload must be identical."""
        from sam.queries.fstree_access import get_fstree_data
        name = hpc_resource.resource_name
        live = get_fstree_data(session, resource_name=name)

        _feed(session)
        flag(True)
        request.getfixturevalue('armed')
        served = get_fstree_data(session, resource_name=name)

        assert served == live


class TestApiSchema:

    def test_leaf_project_on_equals_off(self, session, flag):
        """`AllocationWithUsageSchema` with a row in context dumps what it
        dumps without one, for a leaf project with charges and an adjustment."""
        from datetime import timedelta
        from factories.projects import (make_account, make_allocation,
                                        make_charge_adjustment, make_project)
        from factories.resources import make_resource
        from factories.summaries import make_comp_charge_summary
        from sam.queries.allocation_state import read_model_rows_for
        from sam.resources.resources import ResourceType
        from sam.schemas.allocation import AllocationWithUsageSchema

        hpc = make_resource(session, resource_type=session.query(ResourceType)
                            .filter_by(resource_type='HPC').one())
        project = make_project(session, facility_name='UNIV')
        account = make_account(session, project=project, resource=hpc)
        now = datetime.now()
        alloc = make_allocation(session, account=account, amount=1000.0,
                                start_date=now - timedelta(days=30),
                                end_date=now + timedelta(days=335))
        for c in (40.0, 60.0):
            row = make_comp_charge_summary(session, charges=c, activity_date=now)
            row.account_id = account.account_id
        make_charge_adjustment(session, account=account, amount=-5.0,
                               adjustment_date=now - timedelta(days=1))
        session.flush()
        # Factory rows are stamped this second; the gate reads a same-second
        # stamp as newer than the refresh, so age them past it.
        then = db_now(session) - timedelta(seconds=60)
        for obj in (project, account, alloc):
            obj.creation_time = obj.modified_time = then
        for txn in alloc.transactions:
            txn.creation_time = then
        session.flush()

        def dump(state):
            schema = AllocationWithUsageSchema()
            schema.context = {'account': account, 'session': session,
                              'include_adjustments': True, 'state': state}
            return schema.dump(alloc)

        live = dump(None)
        assert live['used'] == pytest.approx(95.0)

        _feed(session, [project])
        flag(True)
        rows = read_model_rows_for(session, project)
        assert set(rows) == {alloc.allocation_id}
        assert dump(rows[alloc.allocation_id]) == live


class TestDeepDive:

    def test_the_allocation_tree_fragment_renders(self, auth_client, subtree_project):
        resp = auth_client.get(
            f'/admin/htmx/project-allocation-tree/{subtree_project.projcode}')
        assert resp.status_code == 200
        assert subtree_project.projcode.encode() in resp.data
