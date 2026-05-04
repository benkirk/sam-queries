# Phase A — UserProjQueueStatus: per-user/project queue rollups

## Context

PR #221/#223 just landed denormalization of the `system_status` schema (lookup tables
`systems`, `queues`, `filesystems`, `login_nodes`) and Alembic migration management
under `migrations/system_status/`. The existing `QueueStatus` collapses across all
users and projects into one row per (timestamp, queue).

We want a parallel time-series table that retains the same rollup metrics but
keyed by **(timestamp, user, project_code, system, queue)**. The parsed PBS qstat
payload already carries `Job_Owner` and `Account_Name` per job — both are currently
discarded in `collectors/lib/parsers/queues.py`. The downstream goal (Phase B,
separate PR) is admin-only dashboards showing queue load by user / project.

The new table is kept separate from `QueueStatus` (rather than just adding columns)
because volume is ~100× higher and we'll likely retain it for a shorter window.

### Storage cost (5-min cadence, 1 year)

- Snapshots/year: `12 × 24 × 365 ≈ 105k`
- Rows/snapshot: ~100 unique `(user, project, queue)` combinations is a fair
  baseline for Derecho; bursts in busy hours could hit 200–300.
- Annual rows: **10–30M** (vs. ~1M for `QueueStatus`)
- Per row: ~70 B data + ~80 B index = ~150 B → **1.5–5 GB/year** including indexes.

### How the new Def tables help

`UserDef` / `ProjectCodeDef` carry one row per distinct username / project code,
keeping the snapshot table's user/project keys as 4-byte ints. Compared to inlining
strings in the snapshot table, this:
- Cuts row width by ~30–40 B (typical username + projcode are ~16–24 chars total).
- Shrinks every index that includes the user/project key by the same factor —
  including the composite unique constraint, which dominates lookups.
- Makes the eventual Phase B "by user" / "by project" filters integer-keyed
  joins instead of string equality.

---

## Plan (single PR, three commits)

### Commit 1 — ORMs, mixin, lookups, Alembic migration

**Mixin extraction** — `src/system_status/base.py`

Add `QueueRollupMetricsMixin` carrying the columns shared between `QueueStatus`
and the new table:
- `running_jobs`, `pending_jobs`, `held_jobs`
- `cores_allocated`, `gpus_allocated`, `nodes_allocated`
- `cores_pending`, `gpus_pending`
- `cores_held`, `gpus_held`

`active_users` stays on `QueueStatus` only (it's not meaningful per-user). All
columns use `@declared_attr` so subclasses get fresh `Column` instances.

**Refactor** — `src/system_status/models/queues.py:11`

`class QueueStatus(StatusBase, StatusSnapshotMixin, QueueRollupMetricsMixin, SessionMixin)`
— drop the explicit metric columns now provided by the mixin; keep
`queue_status_id`, parent FKs, lookup FKs, `active_users`, relationships,
property accessors, unique constraint, `__repr__`.

**New lookup ORMs** — `src/system_status/models/lookups.py` (extend file)

```python
class UserDef(StatusBase, SessionMixin):
    __bind_key__ = "system_status"
    __tablename__ = "users"
    user_id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(32), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now,
                        server_default=text("CURRENT_TIMESTAMP"))

class ProjectCodeDef(StatusBase, SessionMixin):
    __bind_key__ = "system_status"
    __tablename__ = "project_codes"
    project_code_id = Column(Integer, primary_key=True, autoincrement=True)
    project_code = Column(String(16), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now,
                        server_default=text("CURRENT_TIMESTAMP"))
```

Both are global (not scoped by `system_id`) — a username/projcode is the same
person/project regardless of which HPC system they're using. CLAUDE.md note: the
class is named `UserDef` (not `User`) to avoid collision with `sam.core.User` in
the shared declarative registry — same rationale as `QueueDef` vs `sam.resources.Queue`.

These tables intentionally do **not** FK to `sam.users` / `sam.project` — per the
prompt, we may later query by username/projcode strings sourced from SAM, but the
status DB stays self-contained.

**New snapshot ORM** — `src/system_status/models/user_proj_queues.py` (new file)

```python
class UserProjQueueStatus(StatusBase, StatusSnapshotMixin,
                          QueueRollupMetricsMixin, SessionMixin):
    __bind_key__ = "system_status"
    __tablename__ = "user_proj_queue_status"
    __table_args__ = (
        UniqueConstraint("timestamp", "user_id", "project_code_id", "queue_id",
                         name="uq_user_proj_queue_status_snapshot"),
    )

    user_proj_queue_status_id = Column(Integer, primary_key=True, autoincrement=True)

    # parent FKs (mirror QueueStatus): cascade delete with parent snapshot
    derecho_status_id = Column(Integer, ForeignKey("derecho_status.status_id",
                               ondelete="CASCADE"), nullable=True, index=True)
    casper_status_id  = Column(Integer, ForeignKey("casper_status.status_id",
                               ondelete="CASCADE"), nullable=True, index=True)

    # lookup FKs
    user_id         = Column(Integer, ForeignKey("users.user_id"),         nullable=False, index=True)
    project_code_id = Column(Integer, ForeignKey("project_codes.project_code_id"), nullable=False, index=True)
    system_id       = Column(Integer, ForeignKey("systems.system_id"),     nullable=False, index=True)
    queue_id        = Column(Integer, ForeignKey("queues.queue_id"),       nullable=False, index=True)

    # relationships + parent back_populates="user_proj_queues"
    # property accessors: username, project_code, system_name, queue_name
    # (mirroring the _pending_*-staging pattern in queues.py:77-97)
```

The unique constraint deliberately omits `system_id`: post-normalization,
`queue_id` is an FK to a single row in `QueueDef` which is itself
`(system_id, name)`-keyed, so the queue uniquely identifies its system.
This matches `QueueStatus`'s post-normalization `(timestamp, queue_id)`
constraint at `models/queues.py:26` — no change needed there either.

Add `user_proj_queues` back-relationship on `DerechoStatus` and `CasperStatus`
(`src/system_status/models/derecho.py`, `casper.py`) mirroring how `queues` is
declared there.

**Module exports** — `src/system_status/models/__init__.py`

Add `UserDef`, `ProjectCodeDef`, `UserProjQueueStatus` to imports + `__all__`.

**Lookup helpers + before_flush** — `src/system_status/queries/lookups.py`

Add `get_or_create_user(session, username) → UserDef` and
`get_or_create_project_code(session, code) → ProjectCodeDef`, mirroring
`get_or_create_filesystem` (file:line `60-66`).

Extend the `@event.listens_for(Session, "before_flush")` handler at line 189:
- Add `UserProjQueueStatus` to `_snapshot_models()` (line 97).
- Add `_ensure_user(session, cache, name)` and `_ensure_project_code(session, cache, name)`
  helpers mirroring `_ensure_filesystem` (line 156).
- In the handler loop (line 211), add an `elif isinstance(obj, UserProjQueueStatus)`
  branch that pops `_pending_username`, `_pending_project_code`, `_pending_queue_name`
  and resolves `obj.user`, `obj.project_code`, `obj.queue` (system already resolved
  by the `_pending_system_name` block at line 206).

**Alembic migration** — `migrations/system_status/versions/0003_add_user_proj_queue_status.py`

```bash
cd migrations/system_status
alembic revision --autogenerate -m "add user_proj_queue_status with user/project lookups"
```

Inspect the generated file. Expect: `op.create_table("users")`,
`op.create_table("project_codes")`, `op.create_table("user_proj_queue_status")`
with the FKs and unique constraint. The `STATUS_NAMING_CONVENTION` in
`base.py:25` ensures portable index/uq names. Verify a clean
`upgrade head → downgrade base → upgrade head` cycle on a fresh SQLite.

**Tests**

- `tests/integration/test_normalization_migration.py` (or new file) — cover the
  before_flush listener resolving `_pending_username` and `_pending_project_code`,
  including the case where two snapshot rows in the same flush share a new user.
- `tests/api/test_status_schemas.py` — new ORM round-trip (construct via
  `UserProjQueueStatus(username='x', project_code='y', system_name='derecho',
  queue_name='main', ...)`, flush, assert FKs resolved).
- Migration smoke test: `alembic upgrade head` then `downgrade -1` against the
  per-worker SQLite tempfile bind.

---

### Commit 2 — API schema + nested ingest

**New schema** — `src/system_status/schemas/status.py`

Add `UserProjQueueSchema(BaseSchema)` modeled on `QueueSchema` (file:line `82-100`):
- `Meta.model = UserProjQueueStatus`
- `Meta.exclude = ('derecho_status', 'casper_status', 'system', 'queue', 'user', 'project_code')`
- `timestamp` / `system_name` `dump_only` (parent injects)
- `username = fields.String(required=True)`, `project_code = fields.String(required=True)`
- `queue_name = fields.String(required=True)`
- `system_id`, `queue_id`, `user_id`, `project_code_id` all `dump_only`
- Inherits the rollup metric integer fields from the model auto-schema

**Wire into parent schemas** — same file, `DerechoStatusSchema` (line 107) and
`CasperStatusSchema` (line 173):

```python
user_project_queues = fields.Nested(UserProjQueueSchema, many=True,
                                    required=False, load_default=[])
```

Extend each `@post_load link_nested_objects` (line 129 / 189) to set
`q.timestamp = timestamp; q.system_name = '<system>'` on every row in
`data.get('user_project_queues', [])`.

**Ingest handler** — `src/webapp/api/v1/status.py`

The generic `_ingest_system_status` helper (line 143) flushes and commits the
parent + nested objects via the schema → ORM → before_flush chain. No code
changes needed beyond adding `'user_project_queue_ids':
[r.user_proj_queue_status_id for r in status_object.user_proj_queues]` to the
response dict at line 187 (mirrors `queue_ids`).

**Shared timestamp guarantee.** The parent `DerechoStatusSchema` /
`CasperStatusSchema` `@post_load` hook (`schemas/status.py:129, 189`) stamps
every nested row with `data['timestamp']` from the parent payload. Because
`queues[]` and `user_project_queues[]` are both nested under the same parent,
the matching `QueueStatus` and `UserProjQueueStatus` rows for one snapshot will
share the **exact** timestamp by construction — useful for `JOIN`-driven Phase B
queries (e.g. "what fraction of queue X load came from user Y at this tick?").

**Tests** — `tests/api/test_status_endpoints.py`

Extend `TestDerechoPost` / `TestCasperPost` with:
- `test_post_derecho_with_user_project_queues` — POST a payload containing a
  `user_project_queues[]` array of 2–3 rows (mix of users and projects, including
  one `_unknown_` project_code). Assert 201, response includes `user_project_queue_ids`
  list of correct length, and DB rows exist with FKs populated correctly
  (including newly-created `UserDef` and `ProjectCodeDef` rows).
- One row that uses an existing `UserDef`/`ProjectCodeDef` (assert no duplicate
  insertion).
- Schema validation: `tests/api/test_status_schemas.py` — round-trip via
  `UserProjQueueSchema` directly.

---

### Commit 3 — Collector parser + wire-up

**Parser** — `collectors/lib/parsers/queues.py`

Extend `QueueParser` with `parse_user_project_queues(qstat_json) → List[dict]`,
mirroring the existing `parse_queues` (line 16). Differences:
- Group by `(user, project_code, queue)` instead of `queue` alone.
- Extract `Account_Name` per job: `project = job_data.get('Account_Name', '').strip() or '_unknown_'`.
- No `active_users` field in output (one row = one user, by definition).
- Per-row dict keys: `username`, `project_code`, `queue_name`, plus the same
  rollup metric keys as `parse_queues` minus `active_users`.

The existing `parse_queues` is **not modified** — we deliberately keep the two
parsers parallel so QueueStatus aggregation stays untouched, and the refactor
to the mixin lives entirely on the ORM/schema side.

**Collector wire-up** — `collectors/lib/base_collector.py:71`

```python
data['queues']               = QueueParser.parse_queues(queue_summary, jobs_json)
data['user_project_queues']  = QueueParser.parse_user_project_queues(jobs_json)
```

The existing `api_client.post_status()` already serializes the whole `data` dict
as JSON; no changes there.

**Tests** — new `tests/collectors/test_queue_parser.py` (or `tests/unit/`):

- Bootstrap a small fixture — a 5-job qstat JSON dict with two users on two
  projects in the same queue, one job missing `Account_Name`, one held.
- Assert `parse_user_project_queues` produces the expected unique
  `(username, project_code, queue_name)` rows with correctly summed metric counters,
  including a `_unknown_` project_code row for the job without `Account_Name`.
- Re-use this fixture for an end-to-end `tests/api/` test: feed the parser
  output into the ingest endpoint and assert DB state.

(The collector codebase currently has no tests — `collectors/README.md:319`
defers them. Adding the parser test is in scope; deeper collector test
infrastructure is not.)

---

## Critical files to modify

| File | What |
|---|---|
| `src/system_status/base.py` | + `QueueRollupMetricsMixin` |
| `src/system_status/models/lookups.py` | + `UserDef`, `ProjectCodeDef` |
| `src/system_status/models/queues.py` | refactor `QueueStatus` onto mixin |
| `src/system_status/models/user_proj_queues.py` | new — `UserProjQueueStatus` |
| `src/system_status/models/derecho.py`, `casper.py` | + `user_proj_queues` relationship |
| `src/system_status/models/__init__.py` | + exports |
| `src/system_status/queries/lookups.py` | + helpers + extend before_flush listener |
| `src/system_status/schemas/status.py` | + `UserProjQueueSchema`, nest into parents |
| `src/webapp/api/v1/status.py` | + ID list in ingest response (one-line) |
| `migrations/system_status/versions/0003_*.py` | new — autogenerated, hand-verified |
| `migrations/system_status/0003_USER_PROJ_QUEUE_RUNBOOK.md` | new — prod (Postgres) cut-over steps |
| `collectors/lib/parsers/queues.py` | + `parse_user_project_queues` |
| `collectors/lib/base_collector.py:71` | + payload key |
| `tests/api/test_status_endpoints.py` | + nested-POST test |
| `tests/api/test_status_schemas.py` | + `UserProjQueueSchema` round-trip |
| `tests/integration/test_normalization_migration.py` | + before_flush coverage |
| `tests/collectors/test_queue_parser.py` (new) | + parser unit test |

## Existing utilities to reuse

- `get_or_create_*` pattern (`queries/lookups.py:40-79`) — copy shape for
  `get_or_create_user` / `get_or_create_project_code`.
- `_ensure_*` in-flush helpers (`queries/lookups.py:123-186`) — copy shape for
  `_ensure_user` / `_ensure_project_code`.
- `_pending_*` setter pattern (`models/queues.py:77-97`) — copy onto new model.
- `STATUS_NAMING_CONVENTION` (`base.py:25`) — applies automatically.
- `BaseSchema` (`schemas/__init__.py`) and the `LoginNodeSchema` /
  `QueueSchema` shape — direct template for `UserProjQueueSchema`.
- `api_key_client` and `status_session` fixtures (`tests/conftest.py:378, 403`)
  — drive ingest tests as-is.

## Out of scope (Phase B follow-up)

- New role / RBAC for admins viewing per-user/project queue load.
- Dashboard charts on the system_status frontend.
- Retention policy / pruning job for `user_proj_queue_status`.
- Backfill from any historical qstat archives (none exist).

## Production migration (Postgres)

Migration `0003` is **purely additive** — three new tables (`users`,
`project_codes`, `user_proj_queue_status`), no column changes on existing
tables, no data backfill. That makes the runbook dramatically simpler than
the Phase 2 normalization runbook (`migrations/system_status/0002_NORMALIZATION_RUNBOOK.md`),
but the established pattern still applies. A new
`migrations/system_status/0003_USER_PROJ_QUEUE_RUNBOOK.md` ships in the same
commit as the migration, covering:

1. **Backup** — `pg_dump --format=custom` against
   `csg-postgres.k8s.ucar.edu` per `PROD_BOOTSTRAP.md:33`. Mandatory even
   though the migration is additive.
2. **Stop collectors** — `kubectl scale deployment <system>-collector --replicas=0`
   (mirrors `0002_NORMALIZATION_RUNBOOK.md:33-44`). Optional for `0003` since
   the migration is additive and existing POST handlers will keep working
   while the new table is missing — but doing it removes any window where
   collectors send `user_project_queues[]` keys to a webapp/DB pair that
   isn't ready yet.
3. **Dry-run** — `alembic -c migrations/system_status/alembic.ini upgrade head --sql > /tmp/0003.sql`
   and inspect. Expect three `CREATE TABLE` statements, three `CREATE INDEX`
   per FK, and the unique-constraint creation. **No `DROP`, no `UPDATE`.**
4. **Apply** — `make migrate-status-up` (or
   `alembic -c migrations/system_status/alembic.ini upgrade head`).
   Expected runtime: subsecond — Postgres runs DDL in one transaction, and
   there's no data migration.
5. **Verify** — `make migrate-status-current` should report `0003_…` as
   head; `psql … -c "\dt"` should show the three new tables.
6. **Deploy webapp** with the new schemas/ingest path.
7. **Restart collectors** — same `kubectl scale … --replicas=1` commands.
   Watch the next 5-min tick: the new table should grow by ~50–200 rows,
   `users` and `project_codes` by however many distinct names appeared.

**Rollback:** `make migrate-status-down` (or
`alembic … downgrade -1`). Drops the three new tables. The webapp must be
reverted at the same time so it stops sending `user_project_queues[]`.

Outage window estimate: ~30 seconds for the migration itself plus ~2 minutes
per `kubectl scale` operation. Collector POSTs missed during this window are
not recoverable; the next tick repopulates everything.

## Verification

1. **Unit + integration tests** (`source etc/config_env.sh && pytest`):
   - new parser test, new schema/ingest tests, new before_flush coverage,
   - all existing `tests/api/test_status_*` and
     `tests/integration/test_normalization_migration.py` still green.
2. **Migration round-trip** against the per-worker SQLite tempfile:
   `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`
   inside `migrations/system_status/`.
3. **End-to-end manual smoke** (optional, if a qstat JSON sample is available):
   `cd collectors/derecho && python collector.py --dry-run --json-only`
   — assert `user_project_queues` key present and shape correct.
4. **Docker-compose run** (optional):
   `docker compose up`, POST a hand-crafted Derecho payload via
   `tests/fixtures/`-style sample, then `mysql … -e "SELECT * FROM
   user_proj_queue_status LIMIT 5"` (or query `users` / `project_codes` to verify
   denormalization rows).
