# Plan — `src/system_status/queries/user_proj_usage.py`

## Context

The Phase B work (PR #224, live since 2026-05-04) gives us
`user_proj_queue_status` — a per-user / per-project / per-queue rollup
table sampled every ~5 min. So far we use it only for instantaneous /
timeseries displays (`get_latest_user_proj_queue_snapshot`,
`get_user_proj_timeseries` in `src/system_status/queries/user_proj_queues.py`).

The next natural question — and what this module addresses — is
**integrated** consumption: "how many core-hours / GPU-hours / node-hours
did user *u* on project *p* burn on system *s* between *T₁* and *T₂*?"

Two non-trivialities the design must respect (per the user):

1. **Tick interval is not 5 min by assumption.** Derive `dt` from the
   actual delta between successive observed timestamps. Collectors stall,
   reschedule, or get reconfigured; hard-coding 300 s would silently
   mis-bill any window that crosses such an event.
2. **Sparse rows.** A `(user, project, queue)` row is only emitted when
   that tuple has ≥1 job at that tick. If a user's jobs end, the row
   simply disappears — there is no explicit zero. Naïve carry-forward
   would attribute load forever; the integration must treat absence at a
   tick as **0 cores / 0 gpus** for that tuple at that tick.

No existing module does this — SAM's billing path (`comp_charge_summary`,
`hpc_charge_summary`) is job-record based with explicit start/end times,
fundamentally different from snapshot integration. Build fresh.

## Design

### Integration scheme — left-step (rectangle) rule

For each `(user, project, queue)` tuple, given observations
`(t₀, x₀), (t₁, x₁), …, (t_n, x_n)` of `cores_allocated` (and same for
`gpus_allocated`, `nodes_allocated`), aligned to the **global tick
timeline** (see below), with absent ticks filled as `x = 0`:

```
core_hours = Σᵢ xᵢ · (tᵢ₊₁ − tᵢ) / 3600   for i in 0..n-1
```

Rationale for **left-step**, not trapezoidal:
- Each row is a discrete observation of *current* allocation. Between
  ticks, cores don't ramp linearly — they jump as jobs start/end. The
  step interpretation ("xᵢ cores were allocated from tᵢ until we next
  sampled at tᵢ₊₁") matches reality better than averaging two adjacent
  samples.
- Left-step composes cleanly with the absence-as-zero rule: if a tuple
  appears at tᵢ with 128 cores and is absent at tᵢ₊₁, it contributes
  `128 · (tᵢ₊₁ − tᵢ)` to that row's window — i.e., it gets charged for
  one tick worth of holding 128 cores, then vanishes. Trapezoidal would
  charge `64 · dt` (averaging with the 0), which under-counts.
- Symmetric to how the dashboard already treats these snapshots in
  `get_user_proj_timeseries`: each tick stands on its own.

Last tick in the window has no successor — we skip its contribution
(equivalent: it contributes `0 · 0 = 0`). Document this; for typical
windows (hour+) the boundary effect is negligible.

### Tick timeline source

Use **the parent system status table** (`DerechoStatus` /
`CasperStatus`) for `SELECT DISTINCT timestamp WHERE timestamp IN
[start, end] ORDER BY timestamp`. These rows are written every collector
tick *regardless of whether any user had jobs*, so they are the canonical
"did we sample at this moment" record. (`user_proj_queue_status` would
miss ticks where the entire system was idle — improbable on prod but
possible on Casper between maintenance windows.)

Resolve the table by `system` arg: `'derecho'` → `DerechoStatus`,
`'casper'` → `CasperStatus`. If we want a system-agnostic helper later,
factor into a small map.

### Sizing the worst case (1 year)

```
ticks/day × days × tuples/tick ≈ 288 × 365 × 340 ≈ 36 M rows
columnar (ts:8 + 3 fk-ids:12 + 3 metric ints:12) ≈ 32 B/row → ~1.2 GB
```

Untouchable as a single fetch. But:
- **Aggregated output** is bounded by `distinct (user, project, queue)`
  combos in the window — at most O(few × 10⁴), easily a few hundred KB.
- **Per-month chunk** is ~3 M rows × 32 B ≈ ~100 MB — fits in a
  numpy column block.

So the design is **chunked columnar aggregation** with numpy doing the
heavy multiplication and grouping, and a single accumulator dict for
final results across chunks.

### Algorithm

```python
def get_user_proj_usage(
    session, *,
    system: str,                              # 'derecho' | 'casper'
    start_date: datetime,
    end_date: datetime,
    queue_name: Optional[str] = None,         # filter by single queue
    username: Optional[str] = None,           # filter by single user
    project_code: Optional[str] = None,       # filter by single project
    exclude_unknown_project: bool = False,    # skip '_unknown_' bucket
    chunk_days: int = 30,                     # streaming window for big ranges
) -> List[Dict[str, Any]]:
    """
    Returns one dict per (user, project, queue) tuple with usage > 0:
      {
        'username': str, 'project_code': str, 'queue_name': str,
        'system': str,
        'core_hours': float, 'gpu_hours': float, 'node_hours': float,
        'first_seen': datetime, 'last_seen': datetime, 'tick_count': int,
      }
    """
```

Steps:

1. **Resolve scope** — reuse `_resolve_system_id` / `_resolve_queue_id`
   from the sibling module (lines 66–84 of `user_proj_queues.py`).
2. **Fetch global tick timeline** for the whole window in one query —
   `SELECT timestamp FROM <derecho|casper>_status WHERE timestamp
   BETWEEN start AND end ORDER BY timestamp`. Convert to
   `ticks = np.array(..., dtype='datetime64[s]')`. ~105 k entries for a
   year, trivial. Compute `dt = np.diff(ticks).astype(np.float64)` —
   length `n-1`, seconds. Last tick contributes 0 (no successor).
3. **Stream `user_proj_queue_status` in monthly chunks**. For each
   chunk `[t_lo, t_hi]`:
   - `SELECT timestamp, user_id, project_code_id, queue_id,
     cores_allocated, gpus_allocated, nodes_allocated FROM
     user_proj_queue_status WHERE system_id=:sys AND timestamp BETWEEN
     :t_lo AND :t_hi AND ... filters ...` (no JOIN to lookups yet — keep
     it columnar; resolve names once at the end).
   - Convert results to numpy column arrays (`ts_arr`, `uid_arr`,
     `pid_arr`, `qid_arr`, `cores_arr`, `gpus_arr`, `nodes_arr`).
   - **Vectorized dt lookup**: `idx = np.searchsorted(ticks, ts_arr)`;
     valid mask = `(idx < len(ticks)-1) & (ticks[idx] == ts_arr)`. Drop
     anything not on a known tick (defensive — should always match).
   - `dt_per_row = dt[idx]` (using masked idx).
   - Compute three columnar products: `cores_sec = cores_arr * dt_per_row`,
     etc.
   - **Group by (uid, pid, qid)**: build a 64-bit composite key
     `key = (uid << 42) | (pid << 21) | qid` (assuming each fits in 21
     bits — `status_users` and `project_codes` are well under 2 M, queues
     are tiny; assert). Then `np.unique(key, return_inverse=True)` →
     compact group index, and `np.bincount(group_idx, weights=cores_sec)`
     gives per-group sums in one shot. Same for gpus/nodes.
   - Merge each chunk's per-group sums into a master `dict[(uid, pid,
     qid)] -> [cores_sec, gpus_sec, nodes_sec, first_seen, last_seen,
     tick_count]`. Track `first_seen`/`last_seen`/`tick_count` per chunk
     using `np.unique` plus `np.minimum.reduceat` / `np.add.reduceat`,
     then merge.
4. **Resolve labels** — at the end, batch-fetch
   `(user_id → username)`, `(project_code_id → project_code)`,
   `(queue_id → queue_name)` for the keys actually present. Three small
   indexed lookups, no N+1.
5. Convert seconds → hours (`/ 3600.0`), drop zero-usage entries, sort
   descending by `core_hours`, return.

### Performance envelope

| Window | Rows | Wall time (target) |
|---|---|---|
| 1 hour | ~4 k | <50 ms |
| 1 day | ~100 k | <500 ms |
| 1 week | ~700 k | ~3 s |
| 1 month | ~3 M | ~10–15 s |
| 1 year | ~36 M | ~2–4 min |

Year-scale at 2-4 min is acceptable for an offline / batch report but
NOT for a synchronous UI request. Two safeguards:
- A `chunk_days` knob (default 30) so memory stays bounded regardless
  of window length.
- Document in the docstring that windows > 60 days should not be hit
  from a request handler. If we later need year-scale UI, the right
  answer is a nightly-rolled summary table
  (`user_proj_queue_daily_usage`), not optimizing this query — that's
  a future Phase C.

### Why not push into SQL?

A pure-SQL aggregation using `LAG`/`LEAD` over the tick subquery would
group + sum server-side and return ~10⁴ rows. Tempting, but blocked by
**portable time arithmetic** — prod is PostgreSQL (`extract(epoch from
…)`) and tests run against SQLite (`julianday(...) * 86400`). The
SQLAlchemy ORM has no portable "interval-to-seconds" expression. We
could dialect-branch, but the test surface doubles for marginal speedup
on the only case it would matter for (year-scale, which is offline
anyway). Keeping the integration in numpy lands once, runs anywhere.

### What to expose

- `get_user_proj_usage(...)` — main aggregator returning per-tuple dicts.
- Add to `src/system_status/queries/__init__.py` exports.
- Two convenience wrappers as thin aggregations over the same machinery
  (no new SQL): `get_user_usage(... aggregate_over='project,queue')` and
  `get_project_usage(... aggregate_over='user,queue')`. Keep optional —
  callers can also just sum result dicts in-place.

## Critical files to read / modify

| Purpose | Path |
|---|---|
| **New module** | `src/system_status/queries/user_proj_usage.py` |
| Reuse `_resolve_system_id` / `_resolve_queue_id` | `src/system_status/queries/user_proj_queues.py:66-84` |
| Parent status models for tick set | `src/system_status/models/{derecho,casper}.py` |
| Lookup ORMs for joins | `src/system_status/models/lookups.py` (UserDef, ProjectCodeDef, QueueDef, System) |
| Snapshot ORM | `src/system_status/models/user_proj_queues.py` |
| Export | `src/system_status/queries/__init__.py` |
| numpy (already a dep via matplotlib) | imported as `np` in the new module |

## Verification

1. **Reconciliation against `queue_status`** — over the same window, sum
   `core_hours` returned by `get_user_proj_usage` for ALL tuples on a
   given `(system, queue)`, then compute the same integral over
   `QueueStatus.cores_allocated` for that `(system, queue)`. The two
   must match within float epsilon for any window. Use the existing
   reconciliation invariant (USER_PROJ_QUEUE_QUICKSTART.md, line 196).
2. **Sparse-row probe** — pick a known short-lived job pair (one that
   appears in a single tick), verify `tick_count == 1`,
   `core_hours ≈ cores * dt / 3600`, `first_seen == last_seen`.
3. **Variable-`dt` probe** — synthesize a test where the gap between
   tick `i` and `i+1` is 10 min instead of 5; verify the integration
   weights that interval correctly (no hard-coded 300 s).
4. **Boundary** — assert that the row at the last in-window tick
   contributes 0 (no successor), and that a window with a single tick
   returns no usage (no interval to integrate over).
5. **Unit tests** in `tests/unit/system_status/test_user_proj_usage.py`
   using factories or hand-built `UserProjQueueStatus` rows with crafted
   timestamps; per-test SAVEPOINT isolation per project test conventions.
6. **Smoke against prod-shaped data** — once wired, run `sam-search` /
   ad-hoc query for `benkirk` over a known busy 24 h window, eyeball
   against billing's per-job `core_hours` for the same span as a
   plausibility check (won't match exactly — billing uses real
   start/end, we use 5-min snapshots — but should be within a few %).

## Open question for the user

I went with **left-step rectangle** integration over trapezoidal. If you
have a strong opinion (or remember why billing uses one or the other for
similar cases), say so before we implement — flipping later means
re-validating reconciliation tests.
