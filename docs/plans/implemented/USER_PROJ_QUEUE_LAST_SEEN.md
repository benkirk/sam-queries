# Refactor `user_proj_queue_status` to span semantics

## Context

The `user_proj_queue_status` table currently stores one row per
`(timestamp, user_id, project_code_id, queue_id)`. For stable workloads, this
generates massive duplication: a user running the same job set produces an
identical row every 5 minutes. The table is on track to dwarf every other
status table.

This refactor reinterprets each row as a **span of unchanging counts**: the
existing `timestamp` column becomes "first_seen", and a new `last_seen` column
records the most recent tick at which the same `(user, project, queue,
counts)` tuple was observed. Ingest coalesces identical adjacent ticks by
`UPDATE`ing `last_seen` instead of inserting a new row. A new row is only
inserted when (a) counts change for a `(user, project, queue)` already
present, or (b) a new `(user, project, queue)` appears that wasn't there at
the previous tick. When the tuple disappears from the queue, its row is
simply left alone — `last_seen` is never bumped again, so the row becomes a
self-contained record of one steady-state run.

User-confirmed decisions:
- **Keep `timestamp` column name.** Reinterpret semantically as first_seen;
  no rename. Add `last_seen` only.
- **Wipe existing rows in the migration.** No backfill. Fresh start.
- **Parent FK** (`derecho_status_id` / `casper_status_id`) is set on INSERT
  to the parent at `timestamp` (first_seen) and never rewritten on UPDATE.
  CASCADE delete stays — pruning old parent snapshots prunes spans that
  started in that window.

## Critical files

| File | Role |
|---|---|
| `src/system_status/models/user_proj_queues.py` | Add `last_seen` column, update docstring. |
| `migrations/system_status/versions/0004_user_proj_queue_last_seen.py` | NEW: wipe + add column + index. |
| `src/system_status/queries/lookups.py` | Expose a synchronous resolver helper for ingest coalescer. |
| `src/system_status/queries/user_proj_queue_ingest.py` | NEW: `coalesce_user_proj_queue_spans(...)`. |
| `src/webapp/api/v1/status.py` | Call the coalescer before `db.session.add`. |
| `src/system_status/queries/user_proj_queues.py` | `get_latest_user_proj_queue_snapshot` + `get_user_proj_timeseries` updated for spans. |
| `src/system_status/queries/user_proj_usage.py` | `get_user_proj_usage` integrates spans via `cum_dt`. |

## Step 1 — Model (`user_proj_queues.py:1-137`)

- Add `last_seen = Column(DateTime, nullable=False, index=True, comment='...')`
  just below the FK columns (~line 60).
- Add `DateTime` to the SQLAlchemy import (~line 5).
- Keep the existing `(timestamp, user_id, project_code_id, queue_id)`
  UniqueConstraint — uniqueness still holds because each new span has a
  distinct `first_seen`.
- Update the class docstring to spell out span semantics: `timestamp` is
  first_seen; `last_seen` is the inclusive last tick the same counters
  were observed; parent FK points at the first_seen parent only.
- Append `last_seen` to `__str__` / `__repr__` (lines 128–136).

## Step 2 — Migration (`0004_user_proj_queue_last_seen.py`, new)

`revision = '0004_user_proj_queue_last_seen'`, `down_revision =
'0003_user_proj_queue_status'`.

`upgrade()`:
1. `op.execute('DELETE FROM user_proj_queue_status')` — wipe so the NOT NULL
   add is unconditionally safe.
2. `with op.batch_alter_table('user_proj_queue_status') as batch_op:`
   - `batch_op.add_column(sa.Column('last_seen', sa.DateTime(), nullable=True))`
   - `op.execute('UPDATE user_proj_queue_status SET last_seen = timestamp')`
   - `batch_op.alter_column('last_seen', nullable=False)`
   - `batch_op.create_index(batch_op.f('ix_user_proj_queue_status_last_seen'), ['last_seen'], unique=False)`

`downgrade()`: drop index then drop column.

## Step 3 — Reusable lookup resolver (`lookups.py:212-287`)

The existing `_resolve_pending_lookup_names` listener only iterates
`session.new`, but the coalescer needs to resolve FKs on a child *before*
deciding whether to attach it to the parent. Extract the per-row
`UserProjQueueStatus` resolution (lines 258–268) into a public helper:

```
def resolve_user_proj_queue_pending(session, obj, *, sys_cache, queue_cache,
                                    user_cache, proj_cache):
    # pop _pending_system_name → obj.system via _ensure_system
    # pop _pending_queue_name  → obj.queue   via _ensure_queue (system-scoped)
    # pop _pending_username    → obj.user    via _ensure_user
    # pop _pending_project_code→ obj.project via _ensure_project_code
```

Listener body still calls this for `UserProjQueueStatus` instances in
`session.new` (no behavior change for the existing INSERT-then-flush path).

## Step 4 — Coalescer (`user_proj_queue_ingest.py`, new module)

```
def coalesce_user_proj_queue_spans(session, parent_status, T_new) -> dict:
    """Mutates parent_status.user_project_queues in place. Returns
    {'inserted': N, 'extended': M}."""
```

Algorithm:
1. Iterate `parent_status.user_project_queues` (children attached by
   Marshmallow `load_instance=True`). For each child, call
   `resolve_user_proj_queue_pending` so `(user_id, project_code_id, queue_id,
   system_id)` are populated. May require an explicit `session.flush()` of
   the lookup rows to materialize their ids.
2. Determine `system_id` (all children on one parent share it).
3. `prev_ts = SELECT MAX(last_seen) FROM user_proj_queue_status WHERE
   system_id = :sid`. If `None` → first ever ingest for this system; skip
   to step 6.
4. **Span-gap guard**: if `T_new - prev_ts > MAX_SPAN_GAP` (module
   constant, default `timedelta(minutes=20)` — 3 missed ticks), skip
   coalescing. Counters that resume after a long collector outage start
   fresh spans even if they happen to match.
5. Active-set query: `SELECT id, user_id, project_code_id, queue_id,
   running_jobs, pending_jobs, held_jobs, cores_allocated, gpus_allocated,
   nodes_allocated, cores_pending, gpus_pending, cores_held, gpus_held FROM
   user_proj_queue_status WHERE system_id = :sid AND last_seen = :prev_ts`.
   Index by `(user_id, project_code_id, queue_id)` → row tuple.
6. For each child:
   - 10-tuple of metric columns (the `QueueRollupMetricsMixin` fields).
   - If `(uid, pid, qid)` exists in active_set with **all 10 metrics
     equal**:
     - `existing = session.get(UserProjQueueStatus, row_id)`
     - `existing.last_seen = T_new`
     - `parent_status.user_project_queues.remove(child)` — plain list
       mutation; `parent_status` is not yet in the session, so this is
       not a SQLAlchemy state operation.
     - `extended += 1`
   - Else:
     - `child.last_seen = T_new` (= `child.timestamp`)
     - leave attached. `inserted += 1`

**Why mutate before `db.session.add`**: at this point `parent_status` is
still transient; `parent.user_project_queues` is a plain Python list. No
`cascade='all, delete-orphan'` semantics are triggered. This avoids any
SQLAlchemy state surgery on detached duplicates.

## Step 5 — Wire into ingest (`webapp/api/v1/status.py:143-198`)

In `_ingest_system_status`, between `status_object = schema.load(data)`
(line 172) and `db.session.add(status_object)` (line 175):

```
from system_status.queries.user_proj_queue_ingest import coalesce_user_proj_queue_spans
coalesce_user_proj_queue_spans(db.session, status_object, timestamp)
```

`db.session.add(...)` then proceeds with the trimmed children collection.
The response field `'user_project_queue_ids'` (line 187) naturally reflects
only the newly inserted spans — extended spans aren't in the parent's
collection anymore.

## Step 6 — Read: `get_latest_user_proj_queue_snapshot` (`user_proj_queues.py:88-164`)

- Lines 113–118: `select(UserProjQueueStatus.last_seen).order_by(...desc())`
  instead of `timestamp`.
- Line 142: `UserProjQueueStatus.last_seen == latest_ts`.
- Returned dict (lines 147–164): keep key `'timestamp'` populated from
  `r.last_seen` (template binding stays); add new key `'first_seen':
  r.timestamp`.

## Step 7 — Read: `get_user_proj_usage` (`user_proj_usage.py:105-419`)

The big win — span integration is O(1) per row.

- Add `cum_dt = np.concatenate([[0.0], np.cumsum(dt_sec)])` after line 238
  (length `n_ticks + 1`).
- Base SELECT (lines 247–258): add `UserProjQueueStatus.last_seen` to the
  column list.
- Per-chunk query (lines 270–274): change overlap predicate to
  `last_seen >= t_lo AND timestamp <= t_hi`. Maintain a `seen_ids: set[int]`
  across chunks to dedup spans that fall into multiple chunks.
- Replace lines 296–319 (`searchsorted` + per-tick `dt_per_row` lookup) with
  span integration:
  ```
  first_clamped = np.maximum(ts_arr, np.datetime64(start_date))
  last_clamped  = np.minimum(last_arr, np.datetime64(end_date))
  i_first = np.searchsorted(ticks, first_clamped)
  i_last  = np.searchsorted(ticks, last_clamped, side='right') - 1
  valid = i_first <= i_last
  # filter all arrays by valid
  dt_total = cum_dt[i_last + 1] - cum_dt[i_first]
  core_sec = cores_arr * dt_total
  ...
  tick_count_per_row = (i_last - i_first + 1).astype(np.int64)
  ```
- `last_us` per group accumulator (lines 336–338): use `last_arr` (= span
  last_seen), not `ts_arr`. `first_us` keeps using `ts_arr`.
- Bincount and merge (lines 327–362) unchanged structurally.

## Step 8 — Read: `get_user_proj_timeseries` (`user_proj_queues.py:167-370`)

The chart needs per-tick values; spans must be exploded onto the parent
tick grid.

- `gpu_max` probe (lines 282–294): change overlap predicate to
  `last_seen >= start_date AND timestamp <= end_date`.
- Replace the GROUP BY query (lines 311–324) with span explosion:
  1. Fetch parent ticks for `system` in `[start_date, end_date]` (reuse
     `_PARENT_STATUS_BY_SYSTEM` from `user_proj_usage.py`; consider moving
     the dict to a shared helper module).
  2. Fetch span rows: `SELECT timestamp, last_seen, label_col, metric_col
     FROM user_proj_queue_status JOIN <UserDef|ProjectCodeDef> WHERE
     scope_filter AND last_seen >= start_date AND timestamp <= end_date`.
     No DB-side GROUP BY.
  3. Per row, compute `i_first, i_last` (clamped to window) via
     `searchsorted`, accumulate `value` into a numpy array of length
     `n_ticks` keyed by label.
- Rest of the function (rank by peak/current, top-N, Others) unchanged
  once `per_label[label]` is a length-`n_ticks` numpy array indexed by
  tick position.
- `dates = list(ticks)` for the return shape.

## Step 9 — Tests

| File | Change |
|---|---|
| `tests/integration/test_user_proj_queue_listener.py` | Existing tests (lines 27–96) still pass for INSERT path. Add `test_second_flush_with_same_counts_extends_last_seen` and `test_second_flush_with_different_counts_inserts_new_span`. |
| `tests/api/test_status_endpoints.py` | Update `test_post_derecho_user_project_queues_reuses_lookup_rows` (different counts → 2 rows is correct under new schema). Add `test_post_derecho_user_project_queues_coalesces_identical_counts` (same counts → 1 row, `last_seen` advanced). Add `test_post_derecho_response_id_list_excludes_extended_spans`. |
| `tests/unit/test_user_proj_queues_timeseries.py` | Update `_make_upq` to take `last_seen` (default `=parent.timestamp` for back-compat). Add `test_chart_explodes_span_across_ticks` — 1 span over 3 ticks → series `[V, V, V]`. |
| `tests/unit/test_user_proj_usage.py` | Same `_make_upq` update. Add `test_span_integrates_correctly_across_ticks`, `test_overlapping_window_clips_span`. Verify existing `test_two_ticks_left_step_integration` rebuilds correctly under spans. |
| `tests/integration/test_alembic_migrations.py` | Should auto-exercise `0004` upgrade/downgrade. Verify roundtrip clean. |
| `tests/conftest.py` (or new factory) | Add `make_span(session, *, parent, queue, user, project, first_seen, last_seen, **counts)` helper. |

## Risks

- **`cascade='all, delete-orphan'`** on `parent.user_project_queues` —
  mitigated by mutating the children list *before* `db.session.add(parent)`.
  At that point the parent is transient and the list is plain Python.
- **`before_flush` listener vs. resolver**: extracting the per-row
  resolver into a public helper means INSERTs still get `_pending_*`
  resolution (listener still works), and the coalescer can resolve
  synchronously without flushing twice.
- **Active-set query cost**: ~340 rows on Derecho per ingest, indexed
  scan — sub-millisecond. The new index `ix_user_proj_queue_status_last_seen`
  covers it.
- **Span-gap guard** prevents pathological coalescing across collector
  outages. `MAX_SPAN_GAP = timedelta(minutes=20)` (3 missed ticks).
- **Window-boundary edge cases** in usage integration (span ends at exact
  start_date, span fully outside window): handled by `valid = i_first <=
  i_last` mask. Add unit test.
- **`scripts/validate_user_proj_usage.py`** likely needs a parallel update
  (it re-implements integration). Out of scope for this plan but flagged.

## Implementation order

1. Model + migration (Steps 1–2) — schema first.
2. Resolver extract (Step 3).
3. Coalescer + ingest wiring (Steps 4–5).
4. `get_latest_user_proj_queue_snapshot` (Step 6) — trivial, gets templates working.
5. `get_user_proj_usage` (Step 7) — pure query change, easy to test.
6. `get_user_proj_timeseries` (Step 8) — most invasive, do last.
7. Tests in parallel with each step.

Steps 1–5 must ship together: a model with `last_seen NOT NULL` rejects
inserts from the un-updated ingest path.

## Verification

1. `alembic upgrade head` reaches `0004`. Confirm `last_seen DATETIME NOT
   NULL` + `ix_user_proj_queue_status_last_seen` exist.
2. `pytest` (full suite, ~65 s) green — model, migration, listener,
   ingest, both queries.
3. POST identical payloads at two timestamps via the API (or
   `scripts/ingest_mock_status.py` if it exists). Verify `SELECT timestamp,
   last_seen, COUNT(*) FROM user_proj_queue_status` yields 1 row, with
   `last_seen > timestamp`.
4. POST a payload with one user's `cores_allocated` changed. Verify 2
   rows: the original (now closed at the previous tick) and a new span
   starting at T_new.
5. Open the queue drill-down dashboard for a queue with span data.
   Confirm latest-snapshot table renders and chart shows constant
   plateaus across spans.
6. Spot-check `get_user_proj_usage` over a known window: core-hours
   should match the expected `value × span_duration` to within rounding.
