"""The read-model freshness gate: every reason it says "live path" has a test.

Flask-free on purpose — the gate reads the environment outside an app
context, which is how the CLI and the tasks see it. Stamps are second-granular
and a test runs inside one second, so the fixture backdates the factory rows'
stamps a minute and feeds the table half a minute later: fresh rows read as
fresh, and any change a test then makes is stamped newer than the refresh.
"""

from datetime import datetime, timedelta

import pytest
from factories.projects import make_account, make_allocation, make_project
from factories.resources import make_resource

from sam.queries.allocation_state import (
    db_now,
    fresh_state,
    project_allocation_state,
    structural_watermark,
)
from sam.resources.resources import ResourceType
from sam.summaries.allocation_state import AccountAllocationState

pytestmark = pytest.mark.unit


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv('READ_MODEL_ENABLED', '1')
    monkeypatch.delenv('READ_MODEL_MAX_AGE', raising=False)


@pytest.fixture
def hpc_type(session):
    return session.query(ResourceType).filter_by(resource_type='HPC').one()


def _project_on(session, resource):
    project = make_project(session, facility_name='UNIV')
    account = make_account(session, project=project, resource=resource)
    now = datetime.now()
    alloc = make_allocation(session, account=account, amount=500.0,
                            start_date=now - timedelta(days=30),
                            end_date=now + timedelta(days=335))
    return project, account, alloc


def _backdate(session, project, account, alloc, *, seconds=60):
    """Explicitly assigned stamps win over ON UPDATE / onupdate defaults."""
    then = db_now(session) - timedelta(seconds=seconds)
    for obj in (project, account, alloc):
        obj.creation_time = then
        obj.modified_time = then
    for txn in alloc.transactions:
        txn.creation_time = then
    session.flush()


def _feed(session, projects, *, age=timedelta(seconds=30)):
    rows = project_allocation_state(session, now=datetime.now(), projects=projects)
    AccountAllocationState.bulk_replace(session, rows,
                                        refreshed_at=db_now(session) - age)


@pytest.fixture
def fed(session, hpc_type):
    """Two resources, one project each, both fed."""
    r1 = make_resource(session, resource_type=hpc_type)
    r2 = make_resource(session, resource_type=hpc_type)
    p1, a1, alloc1 = _project_on(session, r1)
    p2, a2, alloc2 = _project_on(session, r2)
    _backdate(session, p1, a1, alloc1)
    _backdate(session, p2, a2, alloc2)
    _feed(session, [p1, p2])
    return {'r1': r1, 'r2': r2, 'p1': p1, 'p2': p2, 'a1': a1, 'alloc1': alloc1,
            'alloc2': alloc2}


class TestFallbackReasons:

    def test_off_by_default(self, session, fed):
        assert fresh_state(session, resource_ids=[fed['r1'].resource_id]).reason == 'disabled'

    def test_fresh_rows_serve_by_resource_and_by_project(self, session, fed, enabled):
        by_res = fresh_state(session, resource_ids=[fed['r1'].resource_id])
        assert by_res.reason == 'ok'
        assert set(by_res.rows) == {fed['alloc1'].allocation_id}
        assert by_res.by_account[fed['a1'].account_id].used == 0.0

        by_proj = fresh_state(session, project_ids=[fed['p2'].project_id])
        assert by_proj.reason == 'ok'
        assert set(by_proj.rows) == {fed['alloc2'].allocation_id}

    def test_a_historical_as_of_is_always_live(self, session, fed, enabled):
        yesterday = datetime.now() - timedelta(days=1)
        assert fresh_state(session, project_ids=[fed['p1'].project_id],
                           as_of=yesterday).reason == 'not-today'
        assert fresh_state(session, project_ids=[fed['p1'].project_id],
                           as_of=datetime.now()).reason == 'ok'

    def test_a_scope_without_rows_is_live(self, session, fed, enabled, hpc_type):
        unfed = make_resource(session, resource_type=hpc_type)
        assert fresh_state(session, resource_ids=[unfed.resource_id]).reason == 'no-rows'

    def test_rows_past_max_age_are_live(self, session, fed, enabled, monkeypatch):
        """Age is checked before structure: a 3 h old row is 'too-old' even with
        nothing changed, and raising the ceiling (with the stamps older still)
        serves it again."""
        _backdate(session, fed['p1'], fed['a1'], fed['alloc1'], seconds=5 * 3600)
        for row in session.query(AccountAllocationState).all():
            row.refreshed_at = db_now(session) - timedelta(hours=3)
        session.flush()
        assert fresh_state(session, resource_ids=[fed['r1'].resource_id]).reason == 'too-old'

        monkeypatch.setenv('READ_MODEL_MAX_AGE', str(4 * 3600))
        assert fresh_state(session, resource_ids=[fed['r1'].resource_id]).reason == 'ok'

    def test_a_threshold_edit_stales_its_scope_and_no_other(self, session, fed, enabled):
        fed['a1'].update_thresholds(first_threshold=150)
        session.flush()

        assert fresh_state(session, resource_ids=[fed['r1'].resource_id]).reason == \
            'structural-change'
        assert fresh_state(session, project_ids=[fed['p1'].project_id]).reason == \
            'structural-change'
        assert fresh_state(session, resource_ids=[fed['r2'].resource_id]).reason == 'ok'
        assert fresh_state(session, project_ids=[fed['p2'].project_id]).reason == 'ok'

    def test_a_new_allocation_stales_its_scope(self, session, fed, enabled):
        account = make_account(session, project=fed['p2'], resource=fed['r1'])
        make_allocation(session, account=account, amount=1.0)
        session.flush()

        assert fresh_state(session, resource_ids=[fed['r1'].resource_id]).reason == \
            'structural-change'
        assert fresh_state(session, project_ids=[fed['p2'].project_id]).reason == \
            'structural-change'
        assert fresh_state(session, resource_ids=[fed['r2'].resource_id]).reason == 'ok'

    def test_a_project_scope_spans_its_tree(self, session, fed, enabled, hpc_type):
        """A child's change must stale the parent: subtree rollups read it."""
        child = make_project(session, facility_name='UNIV', parent=fed['p1'])
        session.flush()
        assert fresh_state(session, project_ids=[fed['p1'].project_id]).reason == \
            'structural-change'


class TestWatermark:

    def test_nothing_in_scope_is_none(self, session, hpc_type):
        empty = make_resource(session, resource_type=hpc_type)
        assert structural_watermark(session, resource_ids=[empty.resource_id]) is None

    def test_it_reads_the_db_clock(self, session, fed):
        stamp = structural_watermark(session, resource_ids=[fed['r1'].resource_id])
        assert stamp is not None                      # the fixture backdated it 60 s
        assert 55 <= (db_now(session) - stamp).total_seconds() <= 120
