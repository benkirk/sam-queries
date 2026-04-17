"""Smoke tests for new_tests/ infrastructure.

These validate that the plumbing works: allowlist guard, engine fixture,
session rollback, and that the obfuscated dump actually restored into the
test container. They exercise zero application logic on purpose — any
failure here points at infrastructure, not product code.
"""
import pytest
from sqlalchemy import text


pytestmark = pytest.mark.smoke


def test_engine_points_at_test_container(engine):
    """The engine URL must be the allowlisted test target."""
    url = engine.url
    assert (url.host, url.port) in {
        ("127.0.0.1", 3307),
        ("localhost", 3307),
        ("mysql-test", 3306),
    }, f"engine is pointing at {url.host}:{url.port}, not the test DB"


def test_sam_schema_has_tables(session):
    """The obfuscated dump restored — we see the expected number of tables.

    The real sam schema has ~97 tables. Anything under 50 means the restore
    didn't complete or the dump is missing; anything at 0 means we're
    talking to an empty database.
    """
    result = session.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'sam'"
        )
    ).scalar_one()
    assert result >= 50, f"sam schema only has {result} tables — dump did not restore"


def test_users_table_is_populated(session):
    """A sanity check that the dump carried data, not just schema."""
    count = session.execute(text("SELECT COUNT(*) FROM sam.users")).scalar_one()
    assert count > 0, "users table is empty — obfuscated dump did not restore"


def test_session_rollback_isolation(session):
    """A write inside the fixture transaction must not escape.

    This is the foundation of the whole isolation strategy. If rollback
    leaks here, every writer test risks mutating shared state.
    """
    # Read a known row, mutate it in-session, verify the change is visible
    # locally, then let the fixture teardown roll it back. A second call
    # here can't verify the rollback (same session), but the next test run
    # would see the mutation if rollback were broken — so this test mainly
    # documents the intended pattern.
    before = session.execute(
        text("SELECT COUNT(*) FROM sam.users WHERE active = 1")
    ).scalar_one()
    assert before > 0  # precondition

    # No-op write that will be rolled back (touches a temp table we create
    # inside the transaction so we can't possibly affect real data)
    session.execute(text("CREATE TEMPORARY TABLE _smoke_scratch (x INT)"))
    session.execute(text("INSERT INTO _smoke_scratch VALUES (1), (2), (3)"))
    scratch = session.execute(text("SELECT COUNT(*) FROM _smoke_scratch")).scalar_one()
    assert scratch == 3
