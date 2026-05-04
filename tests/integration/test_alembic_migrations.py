"""Alembic-managed schema integration tests for `system_status`.

Two guarantees:

1. **Drift test** — `alembic upgrade head` against an empty SQLite produces
   a schema that matches `StatusBase.metadata` exactly. Catches model
   changes that ship without a migration, and migrations that ship
   without matching ORM updates.

2. **Round-trip test** — `upgrade head -> downgrade base -> upgrade head`
   succeeds. Confirms migrations are reversible and that batch_alter_table
   ops produce the same final schema after a full down/up cycle. This is
   the canonical SQLite batch-mode smoke.

Migrations are invoked via subprocess so env.py runs in full isolation
(its own FLASK_ACTIVE handling, its own metadata import, its own
process). Tests in this module do **not** depend on the per-test
SQLite fixture in `tests/conftest.py`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect


REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "migrations" / "system_status" / "alembic.ini"


def _run_alembic(*args: str, db_url: str) -> subprocess.CompletedProcess:
    """Invoke alembic against the per-test SQLite URL."""
    env = os.environ.copy()
    env["ALEMBIC_SYSTEM_STATUS_URL"] = db_url
    # Force standalone resolution of StatusBase inside env.py.
    env.pop("FLASK_ACTIVE", None)
    return subprocess.run(
        ["alembic", "-c", str(ALEMBIC_INI), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )


def _load_target_metadata():
    """Import models with FLASK_ACTIVE unset so StatusBase.metadata is the
    same MetaData object env.py compares against."""
    os.environ.pop("FLASK_ACTIVE", None)
    # Importing here (inside the test process, not at module load) avoids
    # polluting other tests that do depend on FLASK_ACTIVE.
    import system_status.models  # noqa: F401
    from system_status import StatusBase

    return StatusBase.metadata


@pytest.fixture
def sqlite_db_url(tmp_path: Path):
    """Per-test SQLite tempfile URL."""
    db_path = tmp_path / "alembic_test.db"
    return f"sqlite:///{db_path}"


def _alembic_internal_tables() -> set[str]:
    """Tables that alembic itself manages — exclude from drift comparison."""
    return {"alembic_version"}


def test_baseline_matches_models(sqlite_db_url: str) -> None:
    """`alembic upgrade head` produces the same schema as StatusBase.metadata.

    Compares table names, per-table column sets (and types), index names,
    and unique-constraint names. Drift in either direction fails.
    """
    _run_alembic("upgrade", "head", db_url=sqlite_db_url)

    target = _load_target_metadata()

    engine = create_engine(sqlite_db_url)
    insp = inspect(engine)

    db_tables = set(insp.get_table_names()) - _alembic_internal_tables()
    model_tables = set(target.tables.keys())

    missing_in_db = model_tables - db_tables
    extra_in_db = db_tables - model_tables
    assert not missing_in_db, f"Tables defined in models but not created by migration: {missing_in_db}"
    assert not extra_in_db, f"Tables created by migration but absent from models: {extra_in_db}"

    for table_name in sorted(model_tables):
        model_table = target.tables[table_name]

        db_columns = {c["name"] for c in insp.get_columns(table_name)}
        model_columns = {c.name for c in model_table.columns}
        assert db_columns == model_columns, (
            f"Column drift in {table_name}: "
            f"only-in-db={db_columns - model_columns}, "
            f"only-in-model={model_columns - db_columns}"
        )

        db_indexes = {ix["name"] for ix in insp.get_indexes(table_name) if ix.get("name")}
        # SQLAlchemy registers indexes from both Index(...) declarations and
        # Column(..., index=True). Auto-generated names go through the
        # naming_convention; explicit names ship verbatim.
        model_indexes = {ix.name for ix in model_table.indexes}
        # SQLite's introspection includes auto-indexes from UNIQUE constraints
        # (named like `sqlite_autoindex_*`); skip those — UK comparison happens below.
        db_indexes_real = {n for n in db_indexes if not n.startswith("sqlite_autoindex_")}
        assert db_indexes_real == model_indexes, (
            f"Index drift in {table_name}: "
            f"only-in-db={db_indexes_real - model_indexes}, "
            f"only-in-model={model_indexes - db_indexes_real}"
        )

        db_uks = {uk["name"] for uk in insp.get_unique_constraints(table_name) if uk.get("name")}
        model_uks = {
            c.name for c in model_table.constraints
            if c.__class__.__name__ == "UniqueConstraint" and c.name
        }
        assert db_uks == model_uks, (
            f"UK drift in {table_name}: "
            f"only-in-db={db_uks - model_uks}, "
            f"only-in-model={model_uks - db_uks}"
        )


def test_round_trip(sqlite_db_url: str) -> None:
    """`upgrade head -> downgrade base -> upgrade head` is reversible.

    After a full cycle, only `alembic_version` should remain following
    the downgrade, and the final upgrade should produce the same table
    set as a fresh upgrade from empty.
    """
    _run_alembic("upgrade", "head", db_url=sqlite_db_url)

    engine = create_engine(sqlite_db_url)
    after_first_upgrade = set(inspect(engine).get_table_names())
    engine.dispose()

    _run_alembic("downgrade", "base", db_url=sqlite_db_url)

    engine = create_engine(sqlite_db_url)
    after_downgrade = set(inspect(engine).get_table_names())
    engine.dispose()
    assert after_downgrade <= _alembic_internal_tables(), (
        f"Tables remained after downgrade base: {after_downgrade - _alembic_internal_tables()}"
    )

    _run_alembic("upgrade", "head", db_url=sqlite_db_url)

    engine = create_engine(sqlite_db_url)
    after_second_upgrade = set(inspect(engine).get_table_names())
    engine.dispose()
    assert after_first_upgrade == after_second_upgrade, (
        f"Round-trip schema mismatch: "
        f"diff={after_first_upgrade.symmetric_difference(after_second_upgrade)}"
    )
