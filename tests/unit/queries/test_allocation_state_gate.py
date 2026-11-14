"""The read-model freshness gate: every reason it says "live path" has a test,
and a stale tree is re-projected in memory rather than failing the scope.

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
from sqlalchemy.orm import object_session

import sam.queries.allocation_state as gate
from sam.queries.allocation_state import (
    db_now,
    fresh_state,
    project_allocation_state,
    structural_watermark,
)
from sam.resources.resources import ResourceType
from sam.summaries.allocation_state import AccountAllocationState


def _tree(project):
    return project.tree_root or project.project_id



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

    def test_a_threshold_edit_patches_its_tree_and_no_other(self, session, fed, enabled):
        fed['a1'].update_thresholds(first_threshold=150)
        session.flush()

        for scope in ({'resource_ids': [fed['r1'].resource_id]},
                      {'project_ids': [fed['p1'].project_id]}):
            look = fresh_state(session, **scope)
            assert look.reason == 'ok-patched'
            assert look.patched == 1
            assert look.stale_trees == (_tree(fed['p1']),)
            assert fed['alloc1'].allocation_id in look.rows
        for scope in ({'resource_ids': [fed['r2'].resource_id]},
                      {'project_ids': [fed['p2'].project_id]}):
            look = fresh_state(session, **scope)
            assert (look.reason, look.patched) == ('ok', 0)

    def test_a_new_allocation_is_served_from_the_patch(self, session, fed, enabled):
        """The old gate could only refuse here; the patch hands the new row over."""
        account = make_account(session, project=fed['p2'], resource=fed['r1'])
        alloc = make_allocation(session, account=account, amount=1.0)
        session.flush()

        by_res = fresh_state(session, resource_ids=[fed['r1'].resource_id])
        assert by_res.reason == 'ok-patched'
        assert alloc.allocation_id in by_res.rows
        assert fresh_state(session, project_ids=[fed['p2'].project_id]).reason == 'ok-patched'
        assert fresh_state(session, resource_ids=[fed['r2'].resource_id]).reason == 'ok'

    def test_a_project_scope_spans_its_tree(self, session, fed, enabled, hpc_type):
        """A child's change must stale the parent: subtree rollups read it."""
        make_project(session, facility_name='UNIV', parent=fed['p1'])
        session.flush()
        look = fresh_state(session, project_ids=[fed['p1'].project_id])
        assert (look.reason, look.patched) == ('ok-patched', 1)


class TestPatch:
    """What the in-memory re-projection of a stale tree looks like."""

    def test_the_sibling_tree_is_served_from_the_table(self, session, fed, enabled):
        p3, a3, alloc3 = _project_on(session, fed['r1'])
        _backdate(session, p3, a3, alloc3)
        _feed(session, [fed['p1'], fed['p2'], p3])

        fed['a1'].update_thresholds(first_threshold=150)
        session.flush()
        look = fresh_state(session, resource_ids=[fed['r1'].resource_id])

        assert look.reason == 'ok-patched'
        assert look.stale_trees == (_tree(fed['p1']),)
        patched, kept = look.rows[fed['alloc1'].allocation_id], look.rows[alloc3.allocation_id]
        assert object_session(kept) is session
        assert object_session(patched) is None       # transient: never written
        assert abs((db_now(session) - patched.refreshed_at).total_seconds()) < 60
        assert patched.rolling_windows is not None    # the threshold it was patched for

    def test_a_project_created_since_the_refresh_is_patched_in(self, session, fed, enabled):
        _p, _a, alloc = _project_on(session, fed['r1'])
        session.flush()
        look = fresh_state(session, resource_ids=[fed['r1'].resource_id])
        assert look.reason == 'ok-patched'
        assert alloc.allocation_id in look.rows

    def test_a_retired_tree_without_rows_is_not_patched(self, session, fed, enabled):
        """Hundreds of long-ended projects sit in a resource scope with no rows;
        their stamps predate every refresh and must never be re-projected."""
        project = make_project(session, facility_name='UNIV')
        account = make_account(session, project=project, resource=fed['r1'])
        now = datetime.now()
        alloc = make_allocation(session, account=account, amount=5.0,
                                start_date=now - timedelta(days=565),
                                end_date=now - timedelta(days=200))
        _backdate(session, project, account, alloc)
        look = fresh_state(session, resource_ids=[fed['r1'].resource_id])
        assert (look.reason, look.patched) == ('ok', 0)

    def test_patched_rows_equal_a_fresh_projection(self, session, fed, enabled):
        fed['a1'].update_thresholds(first_threshold=150)
        session.flush()
        look = fresh_state(session, project_ids=[fed['p1'].project_id])
        assert look.reason == 'ok-patched'

        expect = {r['allocation_id']: r for r in project_allocation_state(
            session, now=datetime.now(), projects=[fed['p1']])}
        assert set(look.rows) == set(expect)
        cols = [c for c in AccountAllocationState.VALUE_COLUMNS if c != 'refreshed_at']
        for aid, row in look.rows.items():
            for c in cols:
                assert getattr(row, c) == expect[aid][c], (aid, c)

    def test_an_allocation_ended_long_ago_leaves_the_answer(self, session, fed, enabled):
        """The dashboards drop an allocation 90 days after it ends; so does the
        patch, because the stale tree's table rows are replaced, not merged."""
        now = datetime.now()
        fed['alloc1'].start_date = now - timedelta(days=565)
        fed['alloc1'].end_date = now - timedelta(days=200)
        session.flush()
        look = fresh_state(session, resource_ids=[fed['r1'].resource_id])
        assert look.reason == 'ok-patched'
        assert look.rows == {}                       # served, and empty

    def test_a_failed_patch_falls_back_to_live(self, session, fed, enabled, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError('projection broke')
        monkeypatch.setattr(gate, 'project_allocation_state', boom)
        fed['a1'].update_thresholds(first_threshold=150)
        session.flush()
        look = fresh_state(session, resource_ids=[fed['r1'].resource_id])
        assert (look.reason, look.rows) == ('patch-failed', None)
        assert look.stale_trees == (_tree(fed['p1']),)

    def test_too_many_stale_trees_go_live(self, session, fed, enabled, monkeypatch):
        monkeypatch.setenv('READ_MODEL_PATCH_MAX_TREES', '0')
        fed['a1'].update_thresholds(first_threshold=150)
        session.flush()
        look = fresh_state(session, resource_ids=[fed['r1'].resource_id])
        assert (look.reason, look.rows) == ('too-many-stale', None)

    def test_the_observer_sees_every_verdict(self, session, fed, enabled, monkeypatch):
        seen = []
        monkeypatch.setattr(gate, '_LOOKUP_OBSERVER', seen.append)
        fresh_state(session, resource_ids=[fed['r1'].resource_id])
        fed['a1'].update_thresholds(first_threshold=150)
        session.flush()
        fresh_state(session, resource_ids=[fed['r1'].resource_id])
        # The projection's own inner gate call is not a verdict and is not seen.
        assert [(s.reason, s.patched) for s in seen] == [('ok', 0), ('ok-patched', 1)]


class TestWatermark:

    def test_nothing_in_scope_is_none(self, session, hpc_type):
        empty = make_resource(session, resource_type=hpc_type)
        assert structural_watermark(session, resource_ids=[empty.resource_id]) is None

    def test_it_reads_the_db_clock(self, session, fed):
        stamp = structural_watermark(session, resource_ids=[fed['r1'].resource_id])
        assert stamp is not None                      # the fixture backdated it 60 s
        assert 55 <= (db_now(session) - stamp).total_seconds() <= 120


@pytest.fixture
def patch_cache(monkeypatch):
    """The patch cache on, empty, and off again afterwards."""
    monkeypatch.setenv('READ_MODEL_PATCH_CACHE_TTL', '3600')
    gate._PATCH_CACHE.reset_for_tests(disabled=False)
    adapter = gate._PATCH_CACHE.adapter('default')
    adapter.clear()
    yield adapter
    adapter.clear()
    gate._PATCH_CACHE.reset_for_tests(disabled=True)


@pytest.fixture
def projections(monkeypatch):
    """Count the projections the gate runs."""
    calls = []
    real = gate.project_allocation_state

    def counted(session, **kw):
        calls.append(kw.get('projects'))
        return real(session, **kw)
    monkeypatch.setattr(gate, 'project_allocation_state', counted)
    return calls


def _edit(session, account, *, seconds_ago):
    """A threshold change stamped ``seconds_ago`` on the DB clock, after the feed (30 s)."""
    account.update_thresholds(first_threshold=150)
    account.modified_time = db_now(session) - timedelta(seconds=seconds_ago)
    session.flush()


class TestPatchCache:
    """A stale tree is projected once per change, not once per request."""

    def test_the_second_request_for_an_unchanged_tree_hits(self, session, fed, enabled,
                                                          patch_cache, projections):
        _edit(session, fed['a1'], seconds_ago=20)
        scope = dict(resource_ids=[fed['r1'].resource_id])
        first, second = fresh_state(session, **scope), fresh_state(session, **scope)

        assert (first.reason, first.patched, first.patch_hits) == ('ok-patched', 1, 0)
        assert (second.reason, second.patched, second.patch_hits) == ('ok-patched', 1, 1)
        assert len(projections) == 1
        aid = fed['alloc1'].allocation_id
        cols = [c for c in AccountAllocationState.VALUE_COLUMNS if c != 'refreshed_at']
        assert ([getattr(second.rows[aid], c) for c in cols]
                == [getattr(first.rows[aid], c) for c in cols])

    def test_a_later_change_to_the_tree_is_projected_again(self, session, fed, enabled,
                                                          patch_cache, projections):
        scope = dict(resource_ids=[fed['r1'].resource_id])
        _edit(session, fed['a1'], seconds_ago=20)
        fresh_state(session, **scope)
        alloc = fed['alloc1']
        alloc.amount = 777.0
        alloc.modified_time = db_now(session) - timedelta(seconds=10)
        session.flush()
        look = fresh_state(session, **scope)

        assert look.patch_hits == 0
        assert len(projections) == 2
        assert look.rows[alloc.allocation_id].allocated == 777.0

    def test_a_tree_stamped_at_the_db_clock_is_not_stored(self, session, fed, enabled,
                                                          patch_cache, projections, monkeypatch):
        """MySQL stamps are whole seconds, so it could change again under the same stamp."""
        # Pinned at the gate: Postgres reads statement_timestamp(), so no row stamp equals it.
        real = gate._tree_stamps

        def stamped_now(*a):
            now, tree_of, stamp_of = real(*a)
            return now, tree_of, {t: now for t in stamp_of}
        monkeypatch.setattr(gate, '_tree_stamps', stamped_now)
        _edit(session, fed['a1'], seconds_ago=20)
        scope = dict(resource_ids=[fed['r1'].resource_id])
        fresh_state(session, **scope)
        look = fresh_state(session, **scope)
        assert look.patch_hits == 0
        assert len(projections) == 2

    def test_a_cache_fault_still_serves_the_patch(self, session, fed, enabled,
                                                  patch_cache, monkeypatch):
        class Broken:
            lock = gate._PATCH_CACHE.adapter('default').lock

            def __getitem__(self, key):
                raise ConnectionError('redis down')

            def __setitem__(self, key, value):
                raise ConnectionError('redis down')
        monkeypatch.setattr(gate._PATCH_CACHE, 'adapter', lambda bucket: Broken())
        _edit(session, fed['a1'], seconds_ago=20)
        look = fresh_state(session, resource_ids=[fed['r1'].resource_id])
        assert (look.reason, look.patch_hits) == ('ok-patched', 0)
