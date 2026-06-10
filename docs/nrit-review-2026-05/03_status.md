# Phase 3 — Status (`src/system_status/`)

> Separate database tier from the main `sam` MySQL DB. Holds time-series snapshots of HPC system state (Derecho, Casper, JupyterHub) and is fed by the `collectors/` sibling pyproject. Alembic-migrated. Quick pass — small subsystem (~4,100 LOC across 22 files).

## Scope

- `src/system_status/` (base, session, models, queries, schemas, cli)
- `migrations/system_status/` (Alembic config + 4 revisions + runbooks)
- `scripts/cleanup_status_data.py`, `scripts/ingest_mock_status.py`, `scripts/setup_status_db.py`, `scripts/test_status_db.py`, `scripts/create_status_db.sql`
- `tests/conftest.py` system_status fixtures (per-worker SQLite tempfile)
- Status-related tests in `tests/integration/` and `tests/api/`
- The `/api/v1/status/*` write surface was already touched in Phase 2 — see findings F2/F3/F4 there.

## Method

1. Mapped bind routing (`StatusBase` resolution, `__bind_key__`, FLASK_ACTIVE gating).
2. Walked the ingest path: collectors POST → `CasperStatusSchema.load` → `_resolve_pending_lookup_names` before_flush listener → INSERTs + span-coalescing for `UserProjQueueStatus`.
3. Walked the read path: dashboard/CLI → `queries/__init__.py` helpers → joined SELECTs against `System` / `QueueDef` / `Filesystem` / `LoginNodeDef` lookups.
4. Reviewed Alembic env.py, alembic.ini, 4 revisions, and `PROD_BOOTSTRAP.md`.
5. Reviewed `cleanup_status_data.py` and test isolation in `tests/conftest.py:220-415`.

## Lenses applied

- Architecture (primary focus)
- Operability
- Testing
- Performance (lightly — small tier)

---

## Findings

### Headline

This subsystem is **the strongest engineered area I've seen so far in the audit.** The `before_flush` lookup-resolver, the `UserProjQueueStatus` span-coalescer, the Alembic env that explicitly forces standalone `StatusBase` resolution, the `URL.render_as_string(hide_password=False)` defensive trick, and the `PROD_BOOTSTRAP.md` runbook (with explicit "MySQL implicitly commits on DDL" warning and "stamp before upgrade" ordering) — these are all signs of someone who has been bitten by these problems before and wrote down the lessons. Inline comments are precise and load-bearing.

Findings below are mostly polish + a few operational concerns; no security-criticals.

### Architecture

**Strengths**

- **Lookup-resolver `before_flush` listener** (`queries/lookups.py:282-334`). Snapshot models declare property setters that stage `_pending_*_name` strings as instance attributes; the listener resolves them to FK relationships (assigning the Python object, not the id — SQLAlchemy resolves the FK at flush topo-sort). Re-entrancy-safe: it never calls `session.flush()` inside the listener, instead scanning `session.new` first. Cache-shared across the batch. This is the kind of code that's a pleasure to read.
- **`UserProjQueueStatus` span coalescer** (`queries/user_proj_queue_ingest.py`). Compresses time-series into spans by extending `last_seen` when counts match — turns N inserts per 5-minute tick into mostly UPDATEs. Includes a 20-minute `MAX_SPAN_GAP` guard so collector outages don't silently extend spans across the gap. Comments are excellent.
- **`StatusBase` dual-mode resolution** (`base.py:34-57`) — `db.Model` under Flask, plain `declarative_base()` (with `STATUS_NAMING_CONVENTION` metadata) for Alembic/CLI/standalone tests. The Alembic env explicitly forces the standalone path (`env.py:39`), and the test conftest forces the Flask path (`tests/conftest.py:280`). Both paths documented inline.
- **Alembic posture** (`migrations/system_status/env.py`) — lazy-imports `connection_string` so dialect/SSL dispatch isn't duplicated; uses `URL.render_as_string(hide_password=False)` to defeat SQLAlchemy's `__str__` password redaction; `render_as_batch=True` is unconditional so SQLite test ALTERs work while no-op on MySQL/Postgres; `ALEMBIC_SYSTEM_STATUS_URL` env override for test injection.
- **Migration runbooks** (`PROD_BOOTSTRAP.md`, `0002_NORMALIZATION_RUNBOOK.md`, `0003_USER_PROJ_QUEUE_RUNBOOK.md`) are exemplary — explicit backup-first, dry-run, stamp-before-upgrade, MySQL DDL-commits-on-implicit-COMMIT warnings, expected-table verification.

**Findings**

- **S1 [Med] Stray `print()` of connection info at module import** (`session/__init__.py:34`). Fires on every import in every context. The token `$STATUS_DB_PASSWORD` is literal (not interpolated — Python sees it as a plain string), but the print still emits `username@server/database` to stdout. Spammy in production logs and leaks operational info. Fix: remove the line, or guard behind `os.getenv('STATUS_DB_DEBUG')`. (Code likely left over from debugging the URL.create() vs f-string fix.)

- **S2 [Med] `base.py:44-51` silently falls back to standalone `declarative_base` when `FLASK_ACTIVE=1` but `from webapp.extensions import db` raises ImportError.** The fallback masks misconfiguration: a deploy that's missing the webapp module (or fails to install it) will still import status models, but they'll bind to whatever engine the caller assembles after the fact — potentially the wrong one — instead of the Flask-SQLAlchemy registry. Fix: log a warning when the fallback triggers in a Flask-active context, or fail loudly. The current docstring claims this is a "Fallback if webapp not available," but in a Flask context that situation is itself a bug.

- **S3 [Low] `schemas/status.py:24` uses `from system_status import *`** which pulls in `main` (the CLI entry point) into the schema module's namespace. Star imports across a package this circular-prone is fragile. Fix: explicit imports of the models actually used.

- **S4 [Low] `cli.py:22-23` does module-level `sys.path.insert(0, str(python_dir))`.** Works around proper packaging; works fine in practice but is the kind of thing that bites when the project is installed (vs. run from source). Not blocking — the `pyproject.toml` declares it as a console script entry point anyway.

- **S5 [Info] `cli.py:62` hardcodes `choices=['derecho', 'casper', 'jupyterhub']`.** Drifts when a new system is added. Could derive from `System` lookup table at parser-build time, but the friction of an extra DB call for `--help` may not be worth it. Comment-only fix would be enough.

### Operability

- **O1 [Med] Cleanup script is not visibly scheduled in-repo** (`scripts/cleanup_status_data.py`). The docstring suggests `0 2 * * *` cron, but no helm CronJob / GitHub workflow / systemd timer is checked into the tree (verified — no matches for `cleanup_status_data` outside the script itself in `helm/`, `.github/`, or `scripts/`). Either it's externally scheduled (good — verify with Ben) or it isn't running, which means `system_status` grows unbounded. Fix: either add the cron config or document the external scheduler.

- **O2 [Low] Cleanup script doesn't touch lookup tables** (`UserDef`, `ProjectCodeDef`, `QueueDef`, `Filesystem`, `LoginNodeDef`, `System`). In practice these are bounded by the user/project/queue catalog and won't blow up, but a one-off bad ingest with garbage names (or a renamed-then-removed queue) leaves orphans forever. Fix: optional lookup-table reaper that drops rows with no snapshot references after the snapshot cleanup runs.

- **O3 [Med] Ingest-time idempotency / out-of-order tolerance is implicit.** The span coalescer (`user_proj_queue_ingest.py:124-128`) keys "active set" on `last_seen == max(last_seen) for this system_id`. If an out-of-order or backfill ingest arrives with `T_new < prev_ts`, the comparison breaks down — the new spans will be inserted but won't extend or get correctly compared against, and the next forward-time ingest will see them as "active" and may extend them across the backfilled gap. Probably not a real concern (collectors push monotonically every 5 minutes), but worth documenting the assumption explicitly. The `MAX_SPAN_GAP` guard handles forward gaps but not backwards ones.

- **O4 [Info] PROD_BOOTSTRAP.md mentions a Postgres prod target** (`csg-postgres.k8s.ucar.edu`). Worth confirming with Ben whether prod is Postgres or MySQL — the driver-dispatch code paths in `session/__init__.py` support both, but the test path is SQLite, so a real prod Postgres deployment is only being exercised by integration tests against the snapshot. Open question Q4 below.

### Testing

- **T1 [Strength] SQLite per-worker tempfile** (`tests/conftest.py:227-244`) is the right call: portable column types, no per-worker MySQL DB-creation dance, automatic cleanup via pytest tmp_path. Per-test isolation via `DELETE FROM` in `_truncate_status_tables` (uses `sorted_tables` so FK ordering is automatic). Documented inline.

- **T2 [Info] FLASK_ACTIVE import-order trap is mitigated but documented as fragile.** `tests/conftest.py:70-76` sets `FLASK_ACTIVE=1` in `pytest_configure` (not in a fixture) precisely because `StatusBase` resolves at import time. Fragile only in the sense that tooling that imports `system_status.*` outside pytest (linters, static analysis, IDE intellisense) may resolve `StatusBase` wrong without consequence — but if anyone ever writes a `bin/` script that imports a status model in standalone mode and expects Flask routing, it'll silently use the wrong base. Long-term cleaner fix: make `StatusBase` a callable factory (`get_status_base()`) so resolution is deferred, but that's a sizeable refactor for a theoretical concern.

- **T3 [Info] Schema drift coverage exists** (`tests/integration/test_alembic_migrations.py`, `test_normalization_migration.py`). Didn't deep-dive — assumed adequate per Phase 1's high-trust verdict on `tests/` claims.

### Performance

- **P1 [Strength] Span coalescer is a smart optimization** — for ~340 active `(user, project, queue)` tuples per tick at a 5-minute cadence, this turns 4,080 inserts/hour into mostly UPDATEs, with one extra `SELECT` per ingest. Right shape.

- **P2 [Strength] `get_upcoming_reservations` uses `COALESCE(updated_at, created_at)`** for the staleness filter (`queries/__init__.py:151`), correctly handling rows where `updated_at` is NULL on first insert. Sharp detail; the comment explains the failure mode (newly-inserted records would be incorrectly filtered out otherwise).

- **P3 [Info] No connection pool sizing on `create_status_engine`** beyond default `pool_pre_ping` + `pool_recycle=3600`. Production Postgres/MySQL traffic is low (one ingest tick per system per 5 minutes plus dashboard reads), so likely fine, but the webapp's status reads go through Flask-SQLAlchemy's pool (set in `run.py:90-96`), not this engine. Worth confirming the production read path uses Flask-SQLAlchemy and this engine is only for CLI / collectors / cleanup.

---

## Cross-cutting tags raised

- `[XC: ops]` — Cleanup script may not be scheduled; no in-repo cron/timer/CronJob. Lookup tables grow forever (probably fine in practice).
- `[XC: convention-drift]` — `print()` at module-import time in `session/__init__.py:34`.
- `[XC: convention-drift]` — Silent fallback in `base.py:44-51` when Flask import fails under `FLASK_ACTIVE=1` — masks misconfiguration.
- `[XC: prod-config-hardening]` — Same fail-open theme as Phase 2's auth findings: when "Flask context is expected" assumptions don't hold, the code falls back instead of refusing.

## Open questions for Ben

1. **Is `cleanup_status_data.py` actually running in production?** If yes, where's the scheduler config (helm CronJob, OS cron, GitHub Actions, …)? If no, what's keeping `system_status` from growing unbounded?
2. **`csg-postgres.k8s.ucar.edu`** — is that the canonical prod `system_status` host? Or is MySQL the prod target and Postgres a parallel deployment? Affects how heavily the dual-driver code paths in `session/__init__.py` actually get exercised.
3. **Out-of-order or backfill ingests** — are these ever expected? The span coalescer assumes monotonic `T_new`; nothing breaks if not, but the implicit assumption is worth documenting.
4. **The `print()` in `session/__init__.py:34`** — intentional debug breadcrumb or leftover from development?
