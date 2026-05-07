# `user_proj_queue_status` run-length compaction (Option A)

**Status**: parked 2026-05-07. Pick up from §"Order of operations" below.

## Why this exists

`user_proj_queue_status` snapshots every (user, project_code, queue)
tuple every ~5 min on derecho and casper. Phase A landed in PR #224
(live since 2026-05-04). After 2.8 days of capture, prod data on
csg-postgres.k8s.ucar.edu showed:

```
total_rows  | run_starts | redundant_interior
   527,310  |     1,637  |          509,822     (96.7%)

num_runs | avg_len | p50 | p95 | p99 | max_len
  17,488 |   30.15 |   2 | 135 | 806 |     811
```

Top consumers had `tick_count = 794+` for a 67h window — nearly every
parent tick, all with byte-identical counters. Run-length distribution
is bimodal: median 2 ticks (short jobs) but P99 of 806 ticks
(essentially the full capture window — long-running jobs that never
changed state).

Storage baseline (combined derecho + casper, 2026-05-07):
- 133 MB total (`pg_total_relation_size`)
- 63 MB heap + 70 MB indexes
- ~267 B/row, ~187k rows/day → **~17 GB/year on disk linearly extrapolated**

Lookup-table baseline:
- `status_users`: 967 (+141/day, tapering)
- `project_codes`: 534 (+64/day, tapering)

After Option A compaction the table footprint drops to ~5–6 MB at
current scale, **~570 MB/year** projected.

## What 2026-05-07 attempted, and why it didn't ship

We started down "Option B" (no schema change, periodic compactor that
keeps run endpoints and deletes interior duplicates, plus a
partition-aware integration in `get_user_proj_usage`).

**The blocker**: the read path can't disambiguate two cases that look
byte-identical in row data:

| Pattern | Same-counter row pair | Gap | Correct span for first row |
|---|---|---|---|
| Compacted contiguous run | `T_start`, `T_end` | hours | `T_end - T_start` (full run) |
| Two unrelated jobs / user-gone-then-back | `T_a`, `T_b` | minutes-to-hours | `parent_dt` (1 tick — user was idle in between) |

Both look identical: same counters, gap > tick interval, no rows in
between. With a tight compactor threshold (auto-detected
`median(parent_dt) × 1.2`) the second pattern is preserved correctly,
but post-compaction it's indistinguishable from the first.

Validation against prod showed **~10% over-count** on derecho cpu
(`upu = 21,285,822` vs `qs = 19,313,996` core-hours) — driven by users
running multiple discrete jobs separated by idle periods that
coincidentally had the same allocation shape (cores/gpus/nodes).

We tried several read-path rules:

- Simple `span = next_in_partition - this`: over-counts coincidental
  gaps.
- LAG-based "close-same-counter LAG → use parent_dt; else use
  next-in-partition": over-counts when the FIRST row after a gap has
  a same-counter next far away.
- Hybrid with cap: under-counts compacted long runs.

**There is no rule that gets both cases right without metadata on the
row.** This is fundamental — the storage representation literally
contains the same bits in both cases.

We considered a tick-presence guard in the compactor (only bridge if
every interior parent tick had a row for this partition pre-compaction).
That works for the FIRST compaction pass but fails on re-runs: once
interior rows are deleted, "absence at interior tick" becomes
ambiguous between "compactor cleaned up" and "user was gone". Long
active runs would accumulate ~365 checkpoint rows per year instead of
staying at 2.

## Recommended approach: Option A (one nullable column)

Add **one** nullable column to `user_proj_queue_status`:

```python
effective_until = Column(
    DateTime, nullable=True, index=True,
    doc="Last parent tick this row's counters apply for. NULL means "
        "single-tick (== timestamp). Set by the compactor when "
        "bridging an interior run.",
)
```

The data becomes self-describing. The read path has zero ambiguity:

```python
# For each row, span = (parent tick STRICTLY AFTER effective_until) - timestamp
# Where effective_until is NULL, treat as effective_until = timestamp,
# which makes span = next_parent_tick - timestamp (current behavior).
eu_us = np.where(has_eu, eu_arr.astype('int64'), ts_int_us)
eu_next_idx = np.minimum(
    np.searchsorted(ticks, eu_us.astype('datetime64[us]'), side='right'),
    n_ticks - 1,
)
span_sec = (ticks[eu_next_idx].astype('int64') - ts_int_us
            ).astype(np.float64) / 1e6
```

Three lines, no LAG inspection, no heuristics, byte-identical to
current logic on uncompacted data (`effective_until = NULL`
everywhere).

### Why this is the right call (re-traded vs Option B)

| | Option A (`effective_until`) | Option B (no schema change) |
|---|---|---|
| Compression | 96.7% | 94.8% (1.9% less, negligible) |
| Schema change | 1 nullable column + Alembic migration | none |
| Read-path correctness | unambiguous in all states | **broken** — over-counts coincidental gaps by ~10% |
| Backward compat | NULL = single-tick = old behavior | n/a |
| Read-path complexity | 3 lines | LAG inspection, mode flags, or heuristics |
| Compactor complexity | identifies run + sets `effective_until` on start + deletes rest | identifies run + deletes interior; keeps both ends |

The 1.9% extra compression isn't the case for Option A — the case is
**unambiguous semantics**. Option B's read-path bug isn't a
performance issue, it's a correctness issue.

## Schema migration

`migrations/system_status/versions/0004_user_proj_queue_effective_until.py`:

```python
"""user_proj_queue_status: add effective_until for run-length compaction.

Revision ID: 0004_user_proj_queue_effective_until
Revises:    0003_user_proj_queue_status
"""
import sqlalchemy as sa
from alembic import op

revision = '0004_user_proj_queue_effective_until'
down_revision = '0003_user_proj_queue_status'

def upgrade():
    op.add_column(
        'user_proj_queue_status',
        sa.Column('effective_until', sa.DateTime(), nullable=True),
    )
    op.create_index(
        'ix_user_proj_queue_effective_until',
        'user_proj_queue_status',
        ['effective_until'],
    )

def downgrade():
    op.drop_index(
        'ix_user_proj_queue_effective_until',
        table_name='user_proj_queue_status',
    )
    op.drop_column('user_proj_queue_status', 'effective_until')
```

Per `project_alembic_system_status.md` memory: system_status schema
changes go through Alembic, not `db.create_all`.

ORM update in `src/system_status/models/user_proj_queues.py` — add the
column to `UserProjQueueStatus`. Existing UNIQUE constraint
`(timestamp, user_id, project_code_id, queue_id)` stays valid;
`timestamp` is run-start, still unique per tuple.

## Compactor script

`scripts/compact_user_proj_queue_status.py` (NEW). CLI flags:

- `--dry-run` (default) — count what would change, no writes.
- `--execute` — actually mutate.
- `--max-row-gap-seconds N` — defaults to auto-detected
  `median(parent_dt) × 1.2` per system. Override for diagnostic runs.
- `--settled-cutoff-minutes M` (default 30) — leave the freshest
  rows alone.
- `--system {derecho,casper,all}` (default `all`).

Auto-detect threshold per system:

```sql
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM dt))
FROM (
  SELECT timestamp - lag(timestamp) OVER (ORDER BY timestamp) AS dt
  FROM derecho_status   -- or casper_status
) sub
WHERE dt IS NOT NULL;
```

Multiply result by 1.2 to get the per-system threshold in seconds.
Important: this auto-tracks any future tick-interval change AND
correctly refuses to bridge across collector downtime gaps (those
are >> median × 1.2).

Compaction itself, two-pass with shared CTE / temp table:

```sql
-- Pass 1: identify maximal uniform runs and stamp effective_until on
-- the run-start row.
WITH ranked AS (
  SELECT user_proj_queue_status_id, "timestamp",
         user_id, project_code_id, queue_id,
         running_jobs, pending_jobs, held_jobs,
         cores_allocated, gpus_allocated, nodes_allocated,
         cores_pending, gpus_pending, cores_held, gpus_held,
         sum(
           CASE WHEN
             /* counters changed vs LAG */
             running_jobs    IS DISTINCT FROM lag(running_jobs)    OVER w OR
             pending_jobs    IS DISTINCT FROM lag(pending_jobs)    OVER w OR
             held_jobs       IS DISTINCT FROM lag(held_jobs)       OVER w OR
             cores_allocated IS DISTINCT FROM lag(cores_allocated) OVER w OR
             gpus_allocated  IS DISTINCT FROM lag(gpus_allocated)  OVER w OR
             nodes_allocated IS DISTINCT FROM lag(nodes_allocated) OVER w OR
             cores_pending   IS DISTINCT FROM lag(cores_pending)   OVER w OR
             gpus_pending    IS DISTINCT FROM lag(gpus_pending)    OVER w OR
             cores_held      IS DISTINCT FROM lag(cores_held)      OVER w OR
             gpus_held       IS DISTINCT FROM lag(gpus_held)       OVER w OR
             /* gap to LAG > threshold */
             "timestamp" - lag("timestamp") OVER w > make_interval(secs => :max_gap)
           THEN 1 ELSE 0 END
         ) OVER w AS run_id
  FROM user_proj_queue_status
  WHERE "timestamp" < NOW() - make_interval(mins => :settled_cutoff)
    AND system_id = :sys_id
  WINDOW w AS (PARTITION BY user_id, project_code_id, queue_id
               ORDER BY "timestamp")
),
run_bounds AS (
  SELECT user_id, project_code_id, queue_id, run_id,
         min("timestamp") AS run_start_ts,
         max("timestamp") AS run_end_ts,
         count(*)         AS run_len
  FROM ranked
  GROUP BY user_id, project_code_id, queue_id, run_id
  HAVING count(*) >= 2
)
UPDATE user_proj_queue_status u
SET effective_until = rb.run_end_ts
FROM run_bounds rb
WHERE u.user_id          = rb.user_id
  AND u.project_code_id  = rb.project_code_id
  AND u.queue_id         = rb.queue_id
  AND u."timestamp"      = rb.run_start_ts;

-- Pass 2: delete the rest of each run.
DELETE FROM user_proj_queue_status u
USING run_bounds rb
WHERE u.user_id         = rb.user_id
  AND u.project_code_id = rb.project_code_id
  AND u.queue_id        = rb.queue_id
  AND u."timestamp"     >  rb.run_start_ts
  AND u."timestamp"     <= rb.run_end_ts;
```

Materialize `run_bounds` as a temp table (`CREATE TEMP TABLE … AS …`)
so both statements share it without re-deriving.

**Idempotent**: second run sees no rows with `len >= 2` because
interior was already deleted. Re-running on a previously-compacted run
that has new ticks appended (run still active) extends the existing
run-start row's `effective_until` by re-running the same SQL — the old
run-start has the same counters as the new appended ticks, so `run_id`
lumps them together.

## Order of operations to ship

1. **Bootloader: revert the WIP partition-aware integration** in
   `src/system_status/queries/user_proj_usage.py` from the 2026-05-07
   branch state. The current code over-counts on uncompacted data
   (~10% on prod). After revert, run `validate_user_proj_usage.py`
   against prod (uncompacted) and confirm PASS at float64 epsilon
   matching the 2026-05-07 baseline (table below).
2. Alembic migration `0004_user_proj_queue_effective_until` — add the
   column.
3. ORM model update in `src/system_status/models/user_proj_queues.py`.
4. Read-path update in `get_user_proj_usage` to consult
   `effective_until` per the 3-line snippet in §"Recommended approach".
5. Run validate script against prod (still all NULL `effective_until`,
   all rows single-tick) — confirm PASS at float64 epsilon (the new
   read-path is byte-identical to old logic when `effective_until` is
   NULL everywhere).
6. Implement compactor script with `--dry-run` default.
7. Test compactor in dev against a snapshot of prod data; confirm row
   count drops from ~530k to ~17,500 and validate still PASSes with
   the same totals.
8. Run `--dry-run` against prod; confirm expected ~510k rows
   would-delete, ~1.5k UPDATEs.
9. Run `--execute` against prod (user-initiated).
10. Re-run validate; confirm PASS at float64 epsilon and
    `pg_total_relation_size('user_proj_queue_status')` drops from
    133 MB to ~5–6 MB.
11. Update `docs/USER_PROJ_QUEUE_QUICKSTART.md` retention section with
    measured post-compaction footprint and the compactor script
    reference; document `effective_until` semantics.
12. (Optional, deferred) Wire compactor into a cron / scheduled task
    so retention stays bounded long-term.

## Verification baseline (captured 2026-05-07)

Reconciliation totals on uncompacted prod data — Option A integration
on compacted data must match these byte-for-byte at float64 epsilon:

| System | core_hours | gpu_hours | node_hours |
|---|---|---|---|
| derecho | 19,265,125.099 | 11,683.970 | 159,332.402 |
| casper | 160,592.774 | 3,877.539 | 33,435.166 |

Reconciliation PASS verdict on every queue, both systems. Max relative
delta ~1.8e-15 (derecho), ~2e-15 (casper).

After running the compactor, the same totals must reproduce. Storage
must drop to ~5–6 MB.

## What 2026-05-07 did ship

- `docs/USER_PROJ_QUEUE_QUICKSTART.md` retention section: replaced
  the doc's `~3.4 GB/year` estimate (which was wrong even by its own
  arithmetic) with measured `~17 GB/year` and corrected
  rows-per-day numbers (combined-systems vs single-system).
- `scripts/validate_user_proj_usage.py` docstring: replaced the
  speculative Today/Week 1/Month 1 milestones with the actual
  measured timing table (24h window already past 1s on both systems
  pre-compaction, signalling the daily-summary threshold from the
  Phase-A design doc is approaching).

These edits stand regardless of whether Option A ships. They're
correct as facts about pre-compaction prod state.

## Critical files

| File | Action |
|---|---|
| `src/system_status/queries/user_proj_usage.py` | revert WIP, then add `effective_until`-based span (~3 lines) |
| `src/system_status/models/user_proj_queues.py` | add `effective_until` column |
| `migrations/system_status/versions/0004_user_proj_queue_effective_until.py` | NEW Alembic migration |
| `scripts/compact_user_proj_queue_status.py` | NEW compactor CLI |
| `scripts/validate_user_proj_usage.py` | no changes needed |
| `docs/USER_PROJ_QUEUE_QUICKSTART.md` | post-compaction retention numbers + compactor pointer + `effective_until` semantics |
| `tests/unit/system_status/test_user_proj_compaction.py` | NEW |

## Tests

- **Unit (compactor)**: ingest a synthetic series with known runs, run
  compactor, assert `effective_until` set on run-starts and interior
  deleted.
- **Equivalence**: run `get_user_proj_usage` on synthetic uncompacted
  series AND on the compacted version; assert numerical equality at
  float64 epsilon.
- **Reconciliation**: factory-build a fixture with multi-tick runs,
  assert `reconcile()` PASS on both pre- and post-compaction states.
- **Idempotent compactor**: run compactor twice; second run should be
  a no-op (zero rows changed).
- **Re-extension**: compact a 5-tick run, append 3 more identical
  ticks, compact again; assert `effective_until` extended on the same
  run-start row and the 3 appended rows are deleted.

## Out of scope (rejected with reasoning)

- **Option B (time-gap compactor, no schema)**: rejected — read-path
  ambiguity, ~10% over-count on prod uncompacted data. Tried 3
  variants of the read-path rule, each fails one of {compacted long
  run, coincidental same-counter gap}.
- **Tick-presence compactor guard**: rejected — works on first pass,
  fails on re-runs (long active runs accumulate ~one row per
  compactor cycle; year-long run keeps ~365 rows instead of 2).
- **Two-mode read-path with caller flag**: rejected — every caller
  needs to know data state; one wrong flag silently returns wrong
  integrals.
- **Heuristic per-partition detection of compaction state**: rejected
  — brittle for mixed-state partitions.
- **Compression of `queue_status`**: out of scope; already bounded at
  ~7-12 rows per tick.
- **Render-time chart compression**: handled separately by
  `src/webapp/dashboards/charts.py:689-720` (allocations pace chart,
  commit 6922b73).

## Connection to existing codebase patterns

- Alembic migrations for system_status DB: see existing migrations in
  `migrations/system_status/versions/` — `0003_user_proj_queue_status`
  is the immediate predecessor.
- Compactor as a manual script first, cron later: matches
  `feedback_keep_image_debug_helpers.md` philosophy of "ship as ad-hoc
  human tool, automate when proven."
- Test database setup: `tests/conftest.py` provides `system_status`
  SQLite bind; new tests use this without docker.
- Auto-detected tick interval: median of parent ticks tracks any
  future interval change AND correctly excludes downtime as outliers.
