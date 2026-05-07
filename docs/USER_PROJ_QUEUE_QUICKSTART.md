# `user_proj_queue_status` quickstart

Reference for Phase B (dashboard / UX) implementers. Phase A landed in
PR #224 and is live in prod since 2026-05-04. The plan/runbook documents
are in `docs/plans/implemented/USER_PROJ_QUEUE_HISTORY.md` and
`migrations/system_status/0003_USER_PROJ_QUEUE_RUNBOOK.md`.

## What the new tables capture

Per-user / per-project / per-queue rollup snapshots, refreshed every
~5 minutes by the derecho and casper collectors. Same counter shape
as `queue_status` (running/pending/held jobs, cores/gpus/nodes
allocated, cores/gpus pending, cores/gpus held) but at finer
aggregation grain.

| Table | Bind | Cardinality | Notes |
|---|---|---|---|
| `user_proj_queue_status` | `system_status` | ~340 rows/tick × 288 ticks/day ≈ 98k rows/day | The snapshot table |
| `status_users` | `system_status` | one row per distinct PBS Job_Owner ever seen | Lookup, write-once |
| `project_codes` | `system_status` | one row per distinct PBS Account_Name ever seen | Lookup, write-once |

After ~1 hour in prod the lookup tables held 571 users / 356
project_codes; new entries since then come in at ~1–4 per tick as new
users / project codes appear on the systems.

## Schema cheat sheet

`user_proj_queue_status` columns of interest for Phase B charting:

```
timestamp           datetime           -- exact match to parent QueueStatus.timestamp
user_id             FK status_users    -- denormalized username
project_code_id     FK project_codes   -- denormalized PBS Account_Name
system_id           FK systems         -- 'derecho' / 'casper'
queue_id            FK queues          -- 'main', 'htc', 'nvgpu', etc.
derecho_status_id   FK derecho_status  -- nullable, cascades on delete
casper_status_id    FK casper_status   -- nullable, cascades on delete

running_jobs / pending_jobs / held_jobs              integer
cores_allocated / gpus_allocated / nodes_allocated   integer
cores_pending / gpus_pending                         integer
cores_held / gpus_held                               integer

UNIQUE (timestamp, user_id, project_code_id, queue_id)
```

`system_id` is intentionally **omitted from the unique constraint** —
`queue_id` references one row in `queues` which is itself
`(system_id, name)`-keyed, so the queue uniquely identifies its system.
You can JOIN `queues.system_id` if a chart needs system-level
grouping without a separate JOIN to `systems`.

## ORM / schema / migration locations

| What | Path |
|---|---|
| Lookup ORMs (`UserDef`, `ProjectCodeDef`) | `src/system_status/models/lookups.py` |
| Snapshot ORM (`UserProjQueueStatus`) | `src/system_status/models/user_proj_queues.py` |
| Shared rollup mixin (`QueueRollupMetricsMixin`) | `src/system_status/base.py` |
| `before_flush` listener (resolves `_pending_username` etc.) | `src/system_status/queries/lookups.py` |
| Marshmallow schemas (`UserProjQueueSchema`) | `src/system_status/schemas/status.py` |
| Ingest endpoint changes | `src/webapp/api/v1/status.py` (id_mappers) |
| Alembic migration | `migrations/system_status/versions/0003_user_proj_queue_status.py` |
| Collector parser (`parse_user_project_queues`) | `collectors/lib/parsers/queues.py` |

The class is named `UserDef` (table `status_users`), **not** `User`,
to avoid a class-name **and** table-name collision with `sam.core.User`
in the shared declarative registry. `ProjectCodeDef` (table
`project_codes`) is similar but did not need a renamed table.

## JSON ingest contract (in case you extend collectors)

`user_project_queues[]` is nested in the existing
`POST /api/v1/status/derecho` and `POST /api/v1/status/casper` payloads.
Optional — collectors that don't send it keep working.

```json
{
  "timestamp": "2026-05-04T20:40:03.847602",
  "...other parent fields...",
  "queues": [...],
  "user_project_queues": [
    {
      "username": "benkirk",
      "project_code": "SCSG0001",
      "queue_name": "main",
      "running_jobs": 3,
      "pending_jobs": 0,
      "held_jobs": 0,
      "cores_allocated": 256,
      "gpus_allocated": 0,
      "nodes_allocated": 4,
      "cores_pending": 0,
      "gpus_pending": 0,
      "cores_held": 0,
      "gpus_held": 0
    }
  ]
}
```

The schema's `before_flush` listener resolves `username` →
`UserDef.user_id`, `project_code` → `ProjectCodeDef.project_code_id`,
`queue_name` → `QueueDef.queue_id` (system-scoped), `system_name` is
injected from the parent. New lookup rows are created on demand and
de-duplicated within the same flush.

The parent `QueueStatus` and its sibling `UserProjQueueStatus` rows
share the **exact** `timestamp` by construction (parent schema's
`@post_load` hook stamps both arrays from `data['timestamp']`). This
makes JOIN-driven "what fraction of queue X load came from user Y at
this tick?" queries trivial.

### `_unknown_` sentinel

Jobs whose `Account_Name` is missing/empty/whitespace-only are bucketed
into `project_codes.project_code = '_unknown_'`. Phase B charts that
group by project should display this bucket distinctly so it doesn't
get conflated with a real project. Filter it out with
`WHERE p.project_code <> '_unknown_'` if a chart needs only attributable
load.

## Useful query templates for Phase B

These all work as-is via the Flask-SQLAlchemy `db.session` against the
`system_status` bind. ORM versions follow the same shape using
`db.session.query(UserProjQueueStatus).join(...).filter(...)`.

### Top-N consumers by cores at the latest tick (single system)

```sql
SELECT u.username, p.project_code, q.name AS queue_name,
       upq.running_jobs, upq.pending_jobs,
       upq.cores_allocated, upq.gpus_allocated, upq.nodes_allocated
FROM   user_proj_queue_status upq
JOIN   systems  s ON s.system_id = upq.system_id
JOIN   queues   q ON q.queue_id  = upq.queue_id
JOIN   status_users  u ON u.user_id = upq.user_id
JOIN   project_codes p ON p.project_code_id = upq.project_code_id
WHERE  s.name = 'derecho'
  AND  upq.timestamp = (
         SELECT MAX(timestamp) FROM user_proj_queue_status WHERE system_id = s.system_id
       )
ORDER BY upq.cores_allocated DESC
LIMIT 25;
```

### Time series of cores allocated for one user across all queues

```sql
SELECT upq.timestamp, q.name AS queue_name,
       SUM(upq.cores_allocated) AS cores
FROM   user_proj_queue_status upq
JOIN   status_users u ON u.user_id = upq.user_id
JOIN   queues       q ON q.queue_id = upq.queue_id
WHERE  u.username = 'benkirk'
  AND  upq.timestamp >= NOW() - INTERVAL '24 hours'
GROUP BY upq.timestamp, q.name
ORDER BY upq.timestamp;
```

### Time series for one project code (sum across all its users)

```sql
SELECT upq.timestamp, s.name AS sys, q.name AS queue,
       SUM(upq.cores_allocated) AS cores,
       SUM(upq.gpus_allocated)  AS gpus,
       COUNT(DISTINCT upq.user_id) AS active_users
FROM   user_proj_queue_status upq
JOIN   project_codes p ON p.project_code_id = upq.project_code_id
JOIN   systems       s ON s.system_id = upq.system_id
JOIN   queues        q ON q.queue_id  = upq.queue_id
WHERE  p.project_code = 'SCSG0001'
  AND  upq.timestamp >= NOW() - INTERVAL '7 days'
GROUP BY upq.timestamp, s.name, q.name
ORDER BY upq.timestamp;
```

### GPU consumers by user across all systems (last hour avg)

```sql
SELECT u.username,
       AVG(upq.gpus_allocated) AS avg_gpus,
       MAX(upq.gpus_allocated) AS peak_gpus
FROM   user_proj_queue_status upq
JOIN   status_users u ON u.user_id = upq.user_id
WHERE  upq.timestamp >= NOW() - INTERVAL '1 hour'
  AND  upq.gpus_allocated > 0
GROUP BY u.username
ORDER BY avg_gpus DESC
LIMIT 25;
```

### Reconciliation sanity check (must always be 0 rows)

```sql
SELECT s.name AS sys, q.name AS queue, qs.timestamp,
       qs.cores_allocated - SUM(upq.cores_allocated) AS diff_cores
FROM   queue_status qs
JOIN   user_proj_queue_status upq
       ON upq.timestamp = qs.timestamp AND upq.queue_id = qs.queue_id
JOIN   systems s ON s.system_id = qs.system_id
JOIN   queues  q ON q.queue_id  = qs.queue_id
WHERE  qs.timestamp = (SELECT MAX(timestamp) FROM queue_status)
GROUP BY s.name, q.name, qs.timestamp, qs.cores_allocated
HAVING qs.cores_allocated <> SUM(upq.cores_allocated);
```

## RBAC for Phase B

Phase A intentionally exposed **no** read endpoints — the data is
admin-sensitive (per-user job rollups). Phase B should:

1. Define a new permission like `VIEW_QUEUE_LOAD_BY_USER` or
   `VIEW_USER_PROJECT_ATTRIBUTION` (name TBD with stakeholders) in
   `src/webapp/utils/rbac.py`.
2. Gate every new GET endpoint and dashboard route on it via the
   existing decorator pattern in `webapp/api/access_control.py`.
3. **Do not** reuse `VIEW_PROJECTS` — that grants project-level view
   rights to project members, which is the wrong scope here.

The user (Ben) anticipates a small set of CISL admins as the
initial role-holders. `sureshm` is already a precedent of a
facility-scoped admin (WNA only) — see
`USER_FACILITY_PERMISSIONS` in `webapp/utils/rbac.py` if Phase B
needs facility-scoped variants.

## Retention

Not yet enforced. Measured in prod after 2.8 days of capture across
derecho + casper combined: **~187k rows/day at ~267 B/row including
indexes** (133 MB total: 63 MB heap + 70 MB indexes). Linear
extrapolation: **~17 GB/year on disk** combined for both systems. The
table is intentionally separate from `queue_status` so retention can be
shorter — Phase B (or a follow-on) should add a periodic
`DELETE WHERE timestamp < NOW() - INTERVAL 'N days'` job.
Cascade-delete on parent `derecho_status` / `casper_status` already
removes rows whose parent snapshot is pruned.

Lookup-table growth observed in the same window: `status_users`
571 → 967 (+141/day, tapering), `project_codes` 356 → 534 (+64/day,
tapering). Both bounded by the population of distinct PBS owners /
account names, so growth is expected to saturate well below the
snapshot-table footprint.

## Verifying production health

The standard sanity bundle (run from `etc/config_env.sh`-loaded shell
pointed at prod):

```bash
PGPASSWORD="$STATUS_DB_PASSWORD" psql -U "$STATUS_DB_USERNAME" \
    -h "$STATUS_DB_SERVER" -d system_status -c "
  SELECT COUNT(DISTINCT timestamp) AS ticks,
         MAX(timestamp) AS last_tick,
         COUNT(*) AS total_rows
  FROM user_proj_queue_status;"
```

A healthy prod state has:
- New ticks landing every 5 minutes
- Reconciliation diff vs `queue_status` = 0
- 0 orphan FKs
- Lookup-table growth tapered to ≤ ~5 new rows per tick
- Parent-FK invariant: each row has exactly one of
  `derecho_status_id` / `casper_status_id` set
