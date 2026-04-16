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
    uc._cache = None
    uc._disabled = False
    yield
    uc._cache = None
    uc._disabled = False


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
