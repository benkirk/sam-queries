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
