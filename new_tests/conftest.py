"""pytest fixtures and safety guards for the new_tests/ suite.

The most important thing this file does: refuse to run against any database
other than the dedicated `mysql-test` container. Production safety depends
on the allowlist check in `pytest_configure` firing before any fixture or
test module touches a connection.

Any run of `pytest -c new_tests/pytest.ini new_tests/` MUST set
`SAM_TEST_DB_URL` to a SQLAlchemy URL pointing at an allowed host/port.
"""
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import sessionmaker


# ---- Safety allowlist -----------------------------------------------------
#
# (host, port) pairs that are permitted as a test database target. The
# mysql-test compose service binds host port 3307 on localhost; from inside
# a container on the sam-network it would be reachable as mysql-test:3306.
# Any other combination — in particular the main dev DB on 3306, or any
# remote production host — is rejected.

_ALLOWED_TEST_TARGETS = {
    ("127.0.0.1", 3307),
    ("localhost", 3307),
    ("mysql-test", 3306),
}


def _verify_test_target(url) -> None:
    """Raise pytest.exit if `url` does not point at an allowed test DB."""
    try:
        parsed = make_url(url)
    except Exception as exc:
        pytest.exit(
            f"new_tests: SAM_TEST_DB_URL is not a valid SQLAlchemy URL: {exc}",
            returncode=2,
        )

    host = parsed.host or ""
    port = parsed.port or 0

    if (host, port) not in _ALLOWED_TEST_TARGETS:
        allowed = ", ".join(f"{h}:{p}" for h, p in sorted(_ALLOWED_TEST_TARGETS))
        pytest.exit(
            "\n"
            "=" * 70 + "\n"
            "REFUSING TO RUN new_tests against this database.\n"
            f"  SAM_TEST_DB_URL points at: {host}:{port}\n"
            f"  Allowed targets:           {allowed}\n"
            "\n"
            "Start the isolated test container with:\n"
            "  docker compose --profile test up -d mysql-test\n"
            "\n"
            "Then set:\n"
            "  export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'\n"
            + "=" * 70,
            returncode=2,
        )


def pytest_configure(config):
    """Runs once at session start, before collection. Fail fast here."""
    url = os.environ.get("SAM_TEST_DB_URL")
    if not url:
        pytest.exit(
            "\n"
            "=" * 70 + "\n"
            "new_tests requires SAM_TEST_DB_URL to be set.\n"
            "\n"
            "Example:\n"
            "  docker compose --profile test up -d mysql-test\n"
            "  export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'\n"
            "  pytest -c new_tests/pytest.ini new_tests/\n"
            + "=" * 70,
            returncode=2,
        )

    _verify_test_target(url)

    # Put src on the import path so tests can `from sam...` without install
    proj_root = Path(__file__).parent.parent
    src_path = str(proj_root / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)


# ---- Engine / session fixtures --------------------------------------------


@pytest.fixture(scope="session")
def test_db_url() -> str:
    """Return the validated test DB URL."""
    return os.environ["SAM_TEST_DB_URL"]


@pytest.fixture(scope="session")
def engine(test_db_url):
    """SQLAlchemy engine bound to the isolated mysql-test container."""
    eng = create_engine(test_db_url, future=True, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def SessionFactory(engine):
    """Session factory for tests that want their own short-lived sessions."""
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@pytest.fixture
def session(engine):
    """Per-test SQLAlchemy session with SAVEPOINT-based rollback isolation.

    The pattern:
      1. Open a raw connection and begin an outer transaction on it.
      2. Bind the Session to that connection with
         `join_transaction_mode="create_savepoint"` — every call to
         `session.begin()`/`session.commit()`/`session.rollback()` from
         test code becomes a SAVEPOINT operation inside the outer
         transaction, NOT a real commit/rollback.
      3. At teardown, roll back the outer transaction. Nothing escapes.

    This is what lets 12 xdist workers share one `sam_test` database
    without stepping on each other — and it lets a read-only test call
    `session.rollback()` (as the XRAS view tests do) without tearing
    down the fixture's outer transaction.
    """
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        future=True,
        join_transaction_mode="create_savepoint",
    )
    sess = Session()
    try:
        yield sess
    finally:
        sess.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


# ---- Representative fixtures ----------------------------------------------
#
# Layer 1 of the two-layer test data strategy (see docs/plans/REFACTOR_TESTING.md).
#
# These fixtures pick ANY row from the snapshot that matches a structural
# shape — "any active project with allocations", "any HPC resource", "any
# user with >=2 active projects". They are session-scoped (one query at
# suite startup) and return IDs, not ORM instances — each test fetches a
# fresh instance bound to its own session via session.get().
#
# This layer is for READ-PATH tests that want snapshot-shaped data but
# don't care about specific projcodes/usernames. Tests built on this layer
# survive snapshot refreshes as long as AT LEAST ONE row of each shape
# still exists.
#
# Layer 2 (factories) lives in new_tests/factories/ and is for WRITE-PATH
# tests that need to assert on exact counts/values. Added when we start
# porting tests that need it.


def _session_for_setup(engine):
    """One-shot session for fixture setup queries, separate from test session."""
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    return Session()


@pytest.fixture(scope="session")
def _active_project_id(engine):
    """ID of any project with at least one account AND at least one active allocation."""
    from sqlalchemy import text as _text
    with _session_for_setup(engine) as s:
        row = s.execute(_text("""
            SELECT p.project_id
            FROM project p
            JOIN account a ON a.project_id = p.project_id
            JOIN allocation al ON al.account_id = a.account_id
            WHERE p.active = 1
              AND al.start_date <= NOW()
              AND (al.end_date IS NULL OR al.end_date >= NOW())
            GROUP BY p.project_id
            HAVING COUNT(DISTINCT al.allocation_id) >= 1
            ORDER BY p.project_id
            LIMIT 1
        """)).first()
    assert row is not None, "snapshot has no active projects with allocations"
    return row[0]


@pytest.fixture(scope="session")
def _multi_project_user_id(engine):
    """ID of any active user who belongs to >=2 active projects (for dashboard tests)."""
    from sqlalchemy import text as _text
    with _session_for_setup(engine) as s:
        row = s.execute(_text("""
            SELECT u.user_id
            FROM users u
            JOIN account_user au ON au.user_id = u.user_id
            JOIN account a ON a.account_id = au.account_id
            JOIN project p ON p.project_id = a.project_id
            WHERE u.active = 1 AND u.locked = 0
              AND p.active = 1
            GROUP BY u.user_id
            HAVING COUNT(DISTINCT p.project_id) >= 2
            ORDER BY u.user_id
            LIMIT 1
        """)).first()
    assert row is not None, "snapshot has no multi-project active users"
    return row[0]


@pytest.fixture(scope="session")
def _hpc_resource_id(engine):
    """ID of any currently-active HPC resource.

    "Active" means commissioned on or before today AND either still
    commissioned (NULL decommission_date) or decommissioned in the future.
    The low-ID HPC resources in the obfuscated snapshot are long-retired
    (Bluefire, Yellowstone, Jellystone, ...) so ORDER BY resource_id
    without this filter returns a dead resource with no fstree presence.
    """
    from sqlalchemy import text as _text
    with _session_for_setup(engine) as s:
        row = s.execute(_text("""
            SELECT r.resource_id
            FROM resources r
            JOIN resource_type rt ON rt.resource_type_id = r.resource_type_id
            WHERE rt.resource_type = 'HPC'
              AND (r.commission_date IS NULL OR r.commission_date <= NOW())
              AND (r.decommission_date IS NULL OR r.decommission_date >= NOW())
            ORDER BY r.resource_id
            LIMIT 1
        """)).first()
    assert row is not None, "snapshot has no currently-active HPC resources"
    return row[0]


@pytest.fixture(scope="session")
def _subtree_project_id(engine):
    """ID of any active project with >=3 active child projects.

    Used by subtree-rollup tests that need a non-leaf project for MPTT
    aggregation. The threshold of 3 children is arbitrary — just large
    enough to produce a meaningful rollup and ensure we don't pick a
    project that happens to have 1 descendant.
    """
    from sqlalchemy import text as _text
    with _session_for_setup(engine) as s:
        row = s.execute(_text("""
            SELECT p.project_id
            FROM project p
            JOIN project c ON c.parent_id = p.project_id
            WHERE p.active = 1 AND c.active = 1
            GROUP BY p.project_id
            HAVING COUNT(c.project_id) >= 3
            ORDER BY p.project_id
            LIMIT 1
        """)).first()
    assert row is not None, "snapshot has no active projects with >=3 active children"
    return row[0]


@pytest.fixture
def active_project(session, _active_project_id):
    """A Project row bound to the test session. Has at least one active allocation."""
    from sam import Project
    return session.get(Project, _active_project_id)


@pytest.fixture
def multi_project_user(session, _multi_project_user_id):
    """A User bound to the test session with >=2 active projects."""
    from sam import User
    return session.get(User, _multi_project_user_id)


@pytest.fixture
def hpc_resource(session, _hpc_resource_id):
    """An HPC Resource bound to the test session."""
    from sam import Resource
    return session.get(Resource, _hpc_resource_id)


@pytest.fixture
def subtree_project(session, _subtree_project_id):
    """A Project bound to the test session that has >=3 active child projects."""
    from sam import Project
    return session.get(Project, _subtree_project_id)
