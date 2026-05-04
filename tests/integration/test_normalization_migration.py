"""Phase 2 normalization backfill verification.

Builds a SQLite DB at revision ``0001_baseline`` (legacy text-column
shape), seeds it with rows that exercise every text-name column being
normalized, then runs ``alembic upgrade head`` to ``0002_normalize_lookups``
and asserts:

  * The four lookup tables (``systems``, ``queues``, ``filesystems``,
    ``login_nodes``) are populated with exactly the distinct names
    seeded.
  * Every snapshot row's FK columns (``system_id``, ``queue_id``,
    ``filesystem_id``, ``login_node_def_id``) are NOT NULL and resolve
    back to the original text via the lookup tables.
  * The legacy text columns are gone.

This is the canonical "migration runs cleanly against a populated DB"
smoke for the destructive Phase 2 migration.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text


REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "migrations" / "system_status" / "alembic.ini"


def _run_alembic(*args: str, db_url: str) -> None:
    env = os.environ.copy()
    env["ALEMBIC_SYSTEM_STATUS_URL"] = db_url
    env.pop("FLASK_ACTIVE", None)
    subprocess.run(
        ["alembic", "-c", str(ALEMBIC_INI), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )


@pytest.fixture
def populated_baseline_db(tmp_path: Path):
    """Build a SQLite DB at 0001_baseline and seed it with mixed legacy data."""
    db_path = tmp_path / "phase2_backfill.db"
    db_url = f"sqlite:///{db_path}"

    _run_alembic("upgrade", "0001_baseline", db_url=db_url)

    engine = create_engine(db_url)
    now = datetime(2026, 5, 4, 12, 0, 0)

    with engine.begin() as conn:
        # Two parent status snapshots (one Derecho, one Casper).
        conn.execute(text(
            "INSERT INTO derecho_status (status_id, timestamp, "
            "  cpu_nodes_total, cpu_nodes_available, cpu_nodes_down, cpu_nodes_reserved, "
            "  gpu_nodes_total, gpu_nodes_available, gpu_nodes_down, gpu_nodes_reserved, "
            "  cpu_cores_total, cpu_cores_allocated, cpu_cores_idle, "
            "  gpu_count_total, gpu_count_allocated, gpu_count_idle, "
            "  memory_total_gb, memory_allocated_gb, "
            "  running_jobs, pending_jobs, held_jobs, active_users) "
            "VALUES (1, :ts, "
            "  100, 90, 0, 10, 50, 40, 0, 10, 12800, 11500, 1300, "
            "  200, 160, 40, 50000.0, 45000.0, 100, 50, 5, 80)"
        ), {"ts": now})

        conn.execute(text(
            "INSERT INTO casper_status (status_id, timestamp, "
            "  viz_nodes_total, viz_nodes_available, viz_nodes_down, viz_nodes_reserved, "
            "  viz_count_total, viz_count_allocated, viz_count_idle, "
            "  cpu_nodes_total, cpu_nodes_available, cpu_nodes_down, cpu_nodes_reserved, "
            "  gpu_nodes_total, gpu_nodes_available, gpu_nodes_down, gpu_nodes_reserved, "
            "  cpu_cores_total, cpu_cores_allocated, cpu_cores_idle, "
            "  gpu_count_total, gpu_count_allocated, gpu_count_idle, "
            "  memory_total_gb, memory_allocated_gb, "
            "  running_jobs, pending_jobs, held_jobs, active_users) "
            "VALUES (1, :ts, "
            "  10, 8, 0, 2, 10, 6, 4, 60, 50, 0, 10, 30, 20, 0, 10, "
            "  2160, 1800, 360, 120, 80, 40, 25000.0, 22000.0, "
            "  200, 30, 2, 50)"
        ), {"ts": now})

        # Queue snapshots — two queues on derecho, one on casper.
        conn.execute(text(
            "INSERT INTO queue_status (timestamp, derecho_status_id, queue_name, system_name, "
            "  running_jobs, pending_jobs, held_jobs, active_users, "
            "  cores_allocated, gpus_allocated, nodes_allocated, "
            "  cores_pending, gpus_pending, cores_held, gpus_held) "
            "VALUES (:ts, 1, 'main', 'derecho', 50, 20, 2, 30, "
            "  5000, 0, 40, 1000, 0, 100, 0)"
        ), {"ts": now})
        conn.execute(text(
            "INSERT INTO queue_status (timestamp, derecho_status_id, queue_name, system_name, "
            "  running_jobs, pending_jobs, held_jobs, active_users, "
            "  cores_allocated, gpus_allocated, nodes_allocated, "
            "  cores_pending, gpus_pending, cores_held, gpus_held) "
            "VALUES (:ts, 1, 'preempt', 'derecho', 10, 5, 0, 8, "
            "  1000, 0, 8, 500, 0, 0, 0)"
        ), {"ts": now})
        conn.execute(text(
            "INSERT INTO queue_status (timestamp, casper_status_id, queue_name, system_name, "
            "  running_jobs, pending_jobs, held_jobs, active_users, "
            "  cores_allocated, gpus_allocated, nodes_allocated, "
            "  cores_pending, gpus_pending, cores_held, gpus_held) "
            "VALUES (:ts, 1, 'casper', 'casper', 100, 10, 1, 25, "
            "  3600, 60, 50, 360, 8, 100, 4)"
        ), {"ts": now})

        # Filesystem snapshots — glade on both, campaign on derecho only.
        conn.execute(text(
            "INSERT INTO filesystem_status (timestamp, derecho_status_id, "
            "  filesystem_name, system_name, available, degraded) "
            "VALUES (:ts, 1, 'glade', 'derecho', 1, 0)"
        ), {"ts": now})
        conn.execute(text(
            "INSERT INTO filesystem_status (timestamp, derecho_status_id, "
            "  filesystem_name, system_name, available, degraded) "
            "VALUES (:ts, 1, 'campaign', 'derecho', 1, 0)"
        ), {"ts": now})
        # Casper rows use the same filesystem_name 'glade' but DIFFERENT timestamp,
        # because (timestamp, filesystem_name) is the legacy UK.
        conn.execute(text(
            "INSERT INTO filesystem_status (timestamp, casper_status_id, "
            "  filesystem_name, system_name, available, degraded) "
            "VALUES (:ts, 1, 'glade', 'casper', 1, 0)"
        ), {"ts": now + timedelta(seconds=1)})

        # Login node snapshots.
        conn.execute(text(
            "INSERT INTO login_node_status (timestamp, derecho_status_id, "
            "  node_name, system_name, node_type, available, degraded) "
            "VALUES (:ts, 1, 'derecho1', 'derecho', 'cpu', 1, 0)"
        ), {"ts": now})
        conn.execute(text(
            "INSERT INTO login_node_status (timestamp, derecho_status_id, "
            "  node_name, system_name, node_type, available, degraded) "
            "VALUES (:ts, 1, 'derecho-data', 'derecho', 'data-access', 1, 0)"
        ), {"ts": now})
        conn.execute(text(
            "INSERT INTO login_node_status (timestamp, casper_status_id, "
            "  node_name, system_name, node_type, available, degraded) "
            "VALUES (:ts, 1, 'casper1', 'casper', 'cpu', 1, 0)"
        ), {"ts": now})

        # Outages — one for derecho, one for casper.
        conn.execute(text(
            "INSERT INTO system_outages (system_name, severity, status, "
            "  title, start_time, created_at) "
            "VALUES ('derecho', 'minor', 'investigating', "
            "  'login node degraded', :ts, :ts)"
        ), {"ts": now})
        conn.execute(text(
            "INSERT INTO system_outages (system_name, severity, status, "
            "  title, start_time, created_at) "
            "VALUES ('casper', 'major', 'monitoring', "
            "  'queue backlog', :ts, :ts)"
        ), {"ts": now})

        # Reservation — only on derecho.
        conn.execute(text(
            "INSERT INTO resource_reservations (system_name, reservation_name, "
            "  start_time, end_time, created_at) "
            "VALUES ('derecho', 'maint-2026-Q2', :start, :end, :ts)"
        ), {"start": now, "end": now + timedelta(hours=4), "ts": now})

    engine.dispose()
    return db_url


def test_phase2_backfill(populated_baseline_db: str) -> None:
    """Run upgrade to head against a populated baseline DB and verify integrity."""
    _run_alembic("upgrade", "head", db_url=populated_baseline_db)

    engine = create_engine(populated_baseline_db)
    insp = inspect(engine)

    # 1. Lookup tables exist with expected row counts.
    with engine.connect() as conn:
        systems = {row[0]: row[1] for row in conn.execute(
            text("SELECT system_id, name FROM systems ORDER BY name"))}
        assert set(systems.values()) == {"derecho", "casper"}, (
            f"systems lookup wrong: {systems}"
        )

        queues = conn.execute(text(
            "SELECT q.name, s.name FROM queues q JOIN systems s ON s.system_id = q.system_id "
            "ORDER BY s.name, q.name"
        )).all()
        assert sorted(queues) == sorted([
            ("casper", "casper"), ("main", "derecho"), ("preempt", "derecho"),
        ]), f"queues lookup wrong: {queues}"

        filesystems = {row[0] for row in conn.execute(
            text("SELECT name FROM filesystems"))}
        assert filesystems == {"glade", "campaign"}, (
            f"filesystems lookup wrong: {filesystems}"
        )

        login_nodes = conn.execute(text(
            "SELECT ln.name, ln.node_type, s.name "
            "FROM login_nodes ln JOIN systems s ON s.system_id = ln.system_id "
            "ORDER BY s.name, ln.name"
        )).all()
        assert sorted(login_nodes) == sorted([
            ("casper1", "cpu", "casper"),
            ("derecho-data", "data-access", "derecho"),
            ("derecho1", "cpu", "derecho"),
        ]), f"login_nodes lookup wrong: {login_nodes}"

    # 2. Snapshot FK columns are populated and the legacy text columns are gone.
    with engine.connect() as conn:
        # No NULL FKs anywhere.
        for tbl, col in [
            ("queue_status", "system_id"), ("queue_status", "queue_id"),
            ("filesystem_status", "system_id"), ("filesystem_status", "filesystem_id"),
            ("login_node_status", "system_id"), ("login_node_status", "login_node_def_id"),
            ("system_outages", "system_id"),
            ("resource_reservations", "system_id"),
        ]:
            n = conn.execute(text(
                f"SELECT COUNT(*) FROM {tbl} WHERE {col} IS NULL"
            )).scalar()
            assert n == 0, f"{tbl}.{col} has {n} NULL rows after backfill"

        # Legacy text columns are gone from every snapshot table.
        legacy_drops = {
            "queue_status": ("queue_name", "system_name"),
            "filesystem_status": ("filesystem_name", "system_name"),
            "login_node_status": ("node_name", "system_name", "node_type"),
            "system_outages": ("system_name",),
            "resource_reservations": ("system_name",),
        }
        for tbl, dropped in legacy_drops.items():
            cols = {c["name"] for c in insp.get_columns(tbl)}
            still_there = cols.intersection(dropped)
            assert not still_there, (
                f"{tbl}: legacy text columns still present: {still_there}"
            )

    # 3. Resolve a few rows back through the lookup tables and confirm the
    #    text identifiers round-trip.
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT q.name, s.name FROM queue_status qs "
            "JOIN queues q ON q.queue_id = qs.queue_id "
            "JOIN systems s ON s.system_id = qs.system_id "
            "ORDER BY s.name, q.name"
        )).all()
        assert sorted(rows) == sorted([
            ("casper", "casper"), ("main", "derecho"), ("preempt", "derecho"),
        ]), f"queue_status round-trip: {rows}"

        rows = conn.execute(text(
            "SELECT ln.name, s.name FROM login_node_status lns "
            "JOIN login_nodes ln ON ln.login_node_def_id = lns.login_node_def_id "
            "JOIN systems s ON s.system_id = lns.system_id "
            "ORDER BY s.name, ln.name"
        )).all()
        assert sorted(rows) == sorted([
            ("casper1", "casper"),
            ("derecho-data", "derecho"),
            ("derecho1", "derecho"),
        ]), f"login_node_status round-trip: {rows}"

        rows = conn.execute(text(
            "SELECT s.name FROM system_outages so "
            "JOIN systems s ON s.system_id = so.system_id "
            "ORDER BY s.name"
        )).all()
        assert [r[0] for r in rows] == ["casper", "derecho"]

    engine.dispose()
