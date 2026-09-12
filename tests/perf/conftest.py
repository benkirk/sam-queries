"""Fixtures for the perf test suite.

Provides:
    count_queries       — context-manager fixture for SQL query counting (standalone engine)
    route_count_queries - same but for Flask-SQLAlchemy's db.engine (route tests)
    baseline            — parametrized fixture reading limits from baselines.json
    perf fixtures       — test data fixtures for the baseline targets
"""

import json
from pathlib import Path

import pytest

from ._query_count import count_queries as _count_queries_cm

_BASELINES_PATH = Path(__file__).parent / "baselines.json"


def _load_baselines():
    with open(_BASELINES_PATH) as f:
        data = json.load(f)
    # Strip the _comment key (documentation only)
    return {k: v for k, v in data.items() if not k.startswith("_")}


BASELINES = _load_baselines()


@pytest.fixture
def count_queries(engine):
    """Yield a context manager that counts SQL queries on the test engine.

    Usage::

        def test_something(session, count_queries):
            with count_queries() as stats:
                do_work(session)
            assert stats.count <= 25
    """
    def _counter():
        return _count_queries_cm(engine)
    return _counter


def get_baseline(name: str) -> int:
    """Return the max allowed query count for *name* from baselines.json."""
    entry = BASELINES.get(name)
    if entry is None:
        raise KeyError(
            f"No baseline found for {name!r} in {_BASELINES_PATH}. "
            f"Available: {sorted(BASELINES)}"
        )
    return entry["queries"]


# ---- Perf-specific data fixtures -------------------------------------------
#
# These are function-scoped so each test gets a fresh ORM instance bound to
# its own session (via the root conftest's SAVEPOINT-isolated session).
# They reuse the session-scoped ID fixtures from the root conftest.


@pytest.fixture
def route_count_queries(app):
    """Yield a context-manager factory that counts SQL queries on db.engine.

    For use with ``auth_client.get(...)`` route tests where queries go
    through Flask-SQLAlchemy's ``db.session`` / ``db.engine``, NOT the
    standalone ``engine`` fixture.

    The engine reference is resolved during fixture setup inside an app
    context.  The ``SQLStats`` attach/detach then works on the raw
    SQLAlchemy engine object, which does not require an active app context.

    Usage::

        def test_route(auth_client, route_count_queries):
            with route_count_queries() as stats:
                response = auth_client.get('/some/route')
            assert stats.count <= baseline
    """
    # Resolve db.engine inside an app context (the property is a proxy)
    with app.app_context():
        from webapp.extensions import db
        flask_engine = db.engine

    def _counter():
        return _count_queries_cm(flask_engine)
    return _counter


@pytest.fixture(autouse=True)
def _reset_usage_cache():
    """Reset usage_cache module globals before/after every perf test.

    Prevents TTLCache state from bleeding between tests.  Same pattern
    as ``test_allocations_performance.py::_reset_usage_cache_globals``.
    """
    import sam.queries.usage_cache as uc
    # disabled=False drops the memoised adapter so the next call re-reads
    # config, rather than pinning the bucket off.
    uc._CACHE.reset_for_tests(disabled=False)
    yield
    uc._CACHE.reset_for_tests(disabled=False)


# ---- Perf-specific data fixtures -------------------------------------------
#
# These are function-scoped so each test gets a fresh ORM instance bound to
# its own session (via the root conftest's SAVEPOINT-isolated session).
# They reuse the session-scoped ID fixtures from the root conftest.


@pytest.fixture
def perf_active_project(session, _active_project_id):
    """An active project for perf tests — same shape as root's ``active_project``."""
    from sam import Project
    return session.get(Project, _active_project_id)


@pytest.fixture
def perf_multi_project_user(session, _multi_project_user_id):
    """A multi-project user for perf tests."""
    from sam import User
    return session.get(User, _multi_project_user_id)


@pytest.fixture
def perf_hpc_resource(session, _hpc_resource_id):
    """An active HPC resource for perf tests."""
    from sam import Resource
    return session.get(Resource, _hpc_resource_id)


@pytest.fixture(scope="session")
def _disk_target(engine):
    """``(projcode, disk_resource_name)`` for the smallest active tree-root
    project that has at least one disk-resource account anywhere in its
    subtree. Session-scoped so the lookup runs once per worker.

    Used by the resource-details disk-path route test. Picking the
    *root* (``project_id == tree_root``) is what exercises
    ``build_disk_subtree``'s descendant walk — which is the path the
    bulk_current_disk_usage refactor was meant to flatten.
    """
    from sqlalchemy import text as _text
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    with Session() as s:
        row = s.execute(_text("""
            SELECT p.projcode, r.resource_name
            FROM project p
            JOIN project descendant
              ON descendant.tree_root = p.project_id
             AND descendant.tree_left BETWEEN p.tree_left AND p.tree_right
            JOIN account a
              ON a.project_id = descendant.project_id AND a.deleted = FALSE
            JOIN resources r ON r.resource_id = a.resource_id
            JOIN resource_type rt ON rt.resource_type_id = r.resource_type_id
            WHERE rt.resource_type = 'DISK'
              AND p.project_id = p.tree_root
              AND p.active = TRUE
            GROUP BY p.projcode, r.resource_name
            ORDER BY p.project_id, r.resource_name
            LIMIT 1
        """)).first()
    assert row is not None, (
        "snapshot has no active tree-root projects with disk-resource accounts"
    )
    return row[0], row[1]


# ---- Read-model fixtures ---------------------------------------------------
#
# The live path stays the fallback, so its baselines stay. These switch the
# readers on against a table fed from the snapshot, for the *_read_model
# baselines. Function tests feed inside the SAVEPOINT; route tests go through
# Flask-SQLAlchemy's own connection, which sees only committed rows.


def _feed_read_model(s):
    from datetime import datetime
    from sam.queries.allocation_state import db_now, project_allocation_state
    from sam.summaries.allocation_state import AccountAllocationState
    rows = project_allocation_state(s, now=datetime.now())
    AccountAllocationState.bulk_replace(s, rows, refreshed_at=db_now(s))


@pytest.fixture
def read_model_on(session, monkeypatch):
    """Feed the table in the test transaction and switch the readers on."""
    _feed_read_model(session)
    monkeypatch.setenv('READ_MODEL_ENABLED', '1')


@pytest.fixture
def read_model_on_committed(app, SessionFactory, monkeypatch):
    """Feed and COMMIT the table for route tests; truncate it afterwards."""
    from sam.summaries.allocation_state import AccountAllocationState
    s = SessionFactory()
    try:
        _feed_read_model(s)
        s.commit()
    finally:
        s.close()
    monkeypatch.setitem(app.config, 'READ_MODEL_ENABLED', True)
    yield
    s = SessionFactory()
    try:
        s.query(AccountAllocationState).delete()
        s.commit()
    finally:
        s.close()


# ---- The stale-tree regime --------------------------------------------------
#
# Production's third state: the table is fresh but one tree changed since the
# refresh (an XRAS handoff, an admin allocation edit), so the gate re-projects
# that tree in memory. A same-second stamp counts as newer, by design, so
# stamping one allocation right after the feed is enough.


@pytest.fixture
def perf_subtree_project(session, _subtree_project_id):
    """A tree root with >=3 active children — the admin tree view's shape."""
    from sam import Project
    return session.get(Project, _subtree_project_id)


def _an_allocation_of(s, project_id):
    from sam.accounting.accounts import Account
    from sam.accounting.allocations import Allocation
    return (s.query(Allocation).join(Account, Account.account_id == Allocation.account_id)
            .filter(Account.project_id == project_id, Account.deleted == False)  # noqa: E712
            .order_by(Allocation.allocation_id).first())


@pytest.fixture
def read_model_on_stale(session, monkeypatch, perf_subtree_project):
    """Feed in the test transaction, then stamp the subtree project's tree."""
    from sam.queries.allocation_state import db_now
    _feed_read_model(session)
    _an_allocation_of(session, perf_subtree_project.project_id).modified_time = db_now(session)
    session.flush()
    monkeypatch.setenv('READ_MODEL_ENABLED', '1')


@pytest.fixture
def read_model_on_committed_stale(app, SessionFactory, monkeypatch, _subtree_project_id):
    """Feed and COMMIT, then stamp one committed allocation; both are undone."""
    from sam.queries.allocation_state import db_now
    from sam.summaries.allocation_state import AccountAllocationState
    s = SessionFactory()
    try:
        _feed_read_model(s)
        alloc = _an_allocation_of(s, _subtree_project_id)
        allocation_id, was = alloc.allocation_id, alloc.modified_time
        alloc.modified_time = db_now(s)
        s.commit()
    finally:
        s.close()
    monkeypatch.setitem(app.config, 'READ_MODEL_ENABLED', True)
    yield
    s = SessionFactory()
    try:
        from sam.accounting.allocations import Allocation
        s.get(Allocation, allocation_id).modified_time = was
        s.query(AccountAllocationState).delete()
        s.commit()
    finally:
        s.close()
