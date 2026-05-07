# Run-length compression for `user_proj_queue_status`

## Context

`user_proj_queue_status` snapshots every (user, project_code, queue)
tuple every ~5 min. A long-running job whose 10 counters
(`running_jobs`, `cores_allocated`, …) don't change for hours generates
identical interior rows that contain no information. Question: how
common is that, and what's the cheapest way to compress it?

Prior art in this repo (commit 6922b73) does RLE at *render time only*
on numpy arrays (`src/webapp/dashboards/charts.py:689-720`); not
reusable for storage but confirms the team is comfortable with the
pattern.

## Phase 0 — measurement (DONE, 2026-05-07 against prod CIRRUS)

```
total_rows  | run_starts | redundant_interior
   527,310  |     1,637  |          509,822     ← 96.7% redundant
```

```
num_runs | avg_len | max_len | p50 | p95 | p99 | total_rows | drop_keep_endpoints | drop_keep_one
  17,488 |   30.15 |     811 |   2 | 135 | 806 |    527,310 |             500,022 |       509,822
                                                                      ↑ 94.8%             ↑ 96.7%
```

**Headline numbers:**
- Compression ceiling: **96.7%** (one row per run, schema-level RLE)
- Compression realistic: **94.8%** (keep both endpoints, no schema change)
- Δ between the two options: **9,800 rows = 1.9% of total** — **negligible**
- Run-length distribution is bimodal: median 2 ticks (~10 min) but P99 of
  806 ticks is essentially the full window (jobs that never changed
  state during 67h of capture)

## A vs B — re-traded with measured data

| | **Option A** (ingest-time RLE w/ `effective_until`) | **Option B** (periodic compaction, keep endpoints) |
|---|---|---|
| Compression | 96.7% | 94.8% |
| Schema change | + 1 nullable column + Alembic migration | none |
| Ingest hot path | read+update per upu row (~3700 PK lookups/tick) | unchanged |
| **Read path change** | **same change required** | **same change required** |
| New code | ingest dedup + migration | compaction job + scheduled-task wiring |
| Backfill | one-time `UPDATE` over 524k rows | one-time `DELETE` over 524k rows |
| Race conditions | none (single-writer ingest) | trivial (DELETE older-than-T, no conflict with INSERTs at "now") |
| Reversibility | hard (column committed to schema) | easy (just stop running the compactor; data continues to arrive uncompressed) |

The critical insight: **the read-path change is the same in both cases**.
Either way, the integration in `get_user_proj_usage` must switch from
"dt = next-parent-tick gap" to "dt = next-row-in-partition gap" (with
left-step fallback for the partition's last row). So that work is
unavoidable; Option A's 1.9% extra compression buys nothing the read
path needs.

**Recommendation: Option B.** The user's instinct ("compact rows
after the fact by dropping intermediate duplicates") is the right
call given the data — same read-path change, no schema migration,
fully reversible, ~equal compression.

## Recommended approach (Option B)

**Ordering (mandatory):** read-path change lands and is verified BEFORE
the compactor runs. After compaction, the existing left-step integration
that uses `dt = next_parent_tick - this_tick` is wrong by ~7× for a
typical 35-minute run (it would credit only the two kept endpoints' 5-min
ticks instead of the full 35 minutes). The new span-based integration
generalizes the old one: on uncompacted data it reproduces the current
result at float64 epsilon (regression-tested via the validate script);
only after that's in place do we run the compactor.

### B.1 Compactor — SQL window query, deletes "interior of uniform run"

A row qualifies for deletion when:
- It has both a previous and next row in its (user, project, queue)
  partition (LAG and LEAD non-null), AND
- All 10 counters match the previous AND next row's counters, AND
- **Both neighbor gaps are ≤ `max_run_gap` (default 15 min)** — guards
  against collapsing genuinely-unrelated job A and job B that
  coincidentally have identical counters but are separated by the user
  vanishing from the snapshot for several ticks. LAG/LEAD operates on
  *rows* not *ticks*, so without this guard a sandwich like
  "T+0 cores=128, gone T+5–T+20, T+25 cores=128 (different job)" would
  look like one continuous run. 15 min = 3× the current 5-min tick
  interval, comfortable safety factor; parameterize so it can track tick
  interval if that ever changes.
- The row is older than a "settled" cutoff (e.g. 30 min) so we never
  mutate the freshest tail.

```sql
WITH neighbored AS (
  SELECT user_proj_queue_status_id,
         "timestamp",
         running_jobs, pending_jobs, held_jobs,
         cores_allocated, gpus_allocated, nodes_allocated,
         cores_pending, gpus_pending, cores_held, gpus_held,
         LAG(running_jobs)    OVER w AS p_rj,
         LEAD(running_jobs)   OVER w AS n_rj,
         /* …same LAG/LEAD for the other 9 counters… */
         LAG("timestamp")     OVER w AS p_ts,
         LEAD("timestamp")    OVER w AS n_ts
  FROM user_proj_queue_status
  WHERE "timestamp" < NOW() - INTERVAL '30 minutes'
  WINDOW w AS (PARTITION BY user_id, project_code_id, queue_id
               ORDER BY "timestamp")
)
DELETE FROM user_proj_queue_status u
USING (
  SELECT user_proj_queue_status_id FROM neighbored
  WHERE p_rj IS NOT NULL AND n_rj IS NOT NULL
    AND running_jobs    = p_rj AND running_jobs    = n_rj
    AND pending_jobs    = p_pj AND pending_jobs    = n_pj
    AND held_jobs       = p_hj AND held_jobs       = n_hj
    AND cores_allocated = p_ca AND cores_allocated = n_ca
    AND gpus_allocated  = p_ga AND gpus_allocated  = n_ga
    AND nodes_allocated = p_na AND nodes_allocated = n_na
    AND cores_pending   = p_cp AND cores_pending   = n_cp
    AND gpus_pending    = p_gp AND gpus_pending    = n_gp
    AND cores_held      = p_ch AND cores_held      = n_ch
    AND gpus_held       = p_gh AND gpus_held       = n_gh
    AND "timestamp" - p_ts <= INTERVAL '15 minutes'
    AND n_ts - "timestamp" <= INTERVAL '15 minutes'
) d
WHERE u.user_proj_queue_status_id = d.user_proj_queue_status_id;
```

Idempotent — second run is a no-op (no row has same-counter neighbors
once interior is gone). First run on prod history would delete ~500k
rows in one shot; can be batched if the lock window is a concern
(by `timestamp` daily window, e.g.).

### B.2 Manual script + later cron (out of scope for this phase)

Ship the compactor as a standalone CLI script
(`scripts/compact_user_proj_queue_status.py`) with a `--dry-run` default
that reports counts without deleting, and `--execute` to actually run
the DELETE. User runs manually against prod for the first compaction;
cron wiring deferred to a follow-up.

Suggested flags:
- `--dry-run` (default) — print "would delete N rows of M" plus a
  small sample, exit 0. Idempotent and side-effect-free.
- `--execute` — run the DELETE in a transaction, print rows deleted.
- `--max-run-gap-minutes` (default 15) — see rationale in B.1.
- `--settled-cutoff-minutes` (default 30) — don't touch rows newer
  than this.
- `--batch-size N` (optional) — if specified, delete in chunks of N
  by timestamp window, otherwise single DELETE statement (~500k rows
  for the first prod run; Postgres handles this fine).

### B.3 Read-path change (`src/system_status/queries/user_proj_usage.py`)

Current integration loop (around lines 316–319) computes `value × dt[i]`
where `dt[i]` is the gap to the next *parent* tick. Change to compute
`value × span[i]` where `span[i]` is:

```python
# For each row, partitioned by (user_id, project_code_id, queue_id):
# span = next_row_in_partition.timestamp - this_row.timestamp
# fallback (last row in partition): span = next_parent_tick - this_row.timestamp
```

Implementable in numpy with a per-partition LEAD computed via
`np.diff` on a sort by `(user_id, project_code_id, queue_id, timestamp)`,
plus a partition-boundary mask. ~30 lines.

**Crucial property:** for an uncompressed series, this new integration
is mathematically identical to the current one (next-row-in-partition
== next-parent-tick when every parent tick has a row). So the
reconciliation continues to PASS at float64 epsilon both before and
after compaction runs, on the same data.

### B.4 Reconciliation (`scripts/validate_user_proj_usage.py`)

Mirror the same change in `integrate_queue_status_by_queue` — switch
from per-parent-tick dt to per-row-in-partition span. ~20 lines. The
PASS verdict on prod is the regression check.

### B.5 Tests

- Unit test: ingest a synthetic series with known runs, run compactor,
  assert row count matches expectation.
- Equivalence test: run `get_user_proj_usage` on uncompressed series
  AND on the compacted version; assert numerical equality at float64
  epsilon.
- Reconciliation test: factory-build a fixture with multi-tick runs,
  assert `reconcile()` PASS on the compacted fixture.

## Effort

| Task | Lines | Time |
|---|---|---|
| Compactor SQL + Python wrapper | ~80 | ½ day |
| ~~Scheduled-task wiring~~ — deferred, user crons manually | — | — |
| Read-path span-based integration | ~30 | ½ day |
| Reconciliation script update | ~20 | ¼ day |
| Tests (3 cases above) | ~150 | ½ day |
| Docs update (`USER_PROJ_QUEUE_QUICKSTART.md`) | ~20 | ¼ day |

**Total: ~1.5 days.** No schema migration. No ingest hot-path change.
No scheduling wiring (user will cron later). Fully reversible —
stopping the compactor leaves the table to grow back uncompressed.

## Order of operations

1. Implement read-path span-based integration (`get_user_proj_usage`).
2. Mirror the change in `scripts/validate_user_proj_usage.py`.
3. Run validate against current prod (uncompacted) — must still PASS
   with same totals as the 2026-05-07 baseline. **This is the
   regression check that the new integration generalizes the old.**
4. Implement compactor script with `--dry-run` default.
5. Run `--dry-run` against prod — expect ~500k rows would-delete.
6. Run `--execute` against prod (user-initiated).
7. Re-run validate script — must PASS with same totals; table size
   drops from 133 MB to ~5–6 MB.
8. Update `USER_PROJ_QUEUE_QUICKSTART.md` retention section with the
   new compacted footprint and the compactor script reference.

## Verification

After implementation:
1. Run compactor in dev against a snapshot of prod data; confirm row
   count drops to ~17,500 (matches measured `num_runs`).
2. Run `validate_user_proj_usage.py` against the compacted snapshot;
   confirm PASS verdict + total core/GPU/node-hours match the
   pre-compaction baseline (derecho 19,265,125 / 11,684 / 159,332;
   casper 160,593 / 3,878 / 33,435 — captured 2026-05-07).
3. Re-run `pg_total_relation_size('user_proj_queue_status')`; expect
   ~5–6 MB (vs current 133 MB).
4. **1-year storage projection** drops from ~17 GB to **~900 MB
   combined**.

## Critical files

| File | Change |
|---|---|
| `src/system_status/queries/` (new file: `compaction.py` or similar) | compactor SQL + Python entry point |
| (codebase scheduling pattern — TBD on inspection) | wire up daily run |
| `src/system_status/queries/user_proj_usage.py:~316` | span-based integration |
| `scripts/validate_user_proj_usage.py` | matching span-based reconciliation |
| `tests/unit/system_status/` | tests for compaction + numerical equivalence |
| `docs/USER_PROJ_QUEUE_QUICKSTART.md` | document compaction job + new run-length facts |

## Out of scope

- Option A schema migration — re-traded against measured data, not
  worth the 1.9% incremental gain.
- Compression of `queue_status` itself — already bounded at ~7-12
  rows per tick.
- Render-time chart compression — handled separately at
  `charts.py:689-720`.
