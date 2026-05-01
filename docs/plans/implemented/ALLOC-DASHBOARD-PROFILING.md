# Allocations Dashboard Performance: Profiling Findings & Refactor Roadmap

**Branch:** `alloc-dashboard-profiling`
**Date:** 2026-03-30
**Status:** Phase 2 complete (19× total speedup over baseline). No further active work planned.

---

## Background

The allocations dashboard index route (`/allocations/`) showed severe performance degradation when "Show Usage" was enabled. A profiling investigation was conducted to identify and quantify hotspots, followed by an initial refactor targeting the three highest-impact bottlenecks.

---

## Profiling Setup

A standalone profiling script was created to bypass all caching layers and measure true query costs:

```bash
source etc/config_env.sh
pip install line_profiler   # optional, for line-by-line breakdown
python utils/profiling/profile_allocations.py 2>&1 | tee profile_output.txt
```

The script:
- Disables TTLCache via `ALLOCATION_USAGE_CACHE_TTL=0` before imports
- Disables audit logging (`AUDIT_ENABLED=0`) to suppress ORM event noise
- Clears matplotlib `lru_cache` before each scenario
- Attaches SQLAlchemy `Engine`-level cursor events to count and time every SQL query
- Profiles two scenarios: `show_usage=False` and `show_usage=True`
- Outputs: per-phase wall-clock timing, SQL query count/time, top-10 slowest queries, cProfile top-20, line_profiler on the hotspot function

---

## Baseline Profiling Results (before any fixes)

### show_usage=False — 1.47s, 4 SQL queries

| Phase | Time | % |
|---|---|---|
| `get_allocation_summary` (projcode=TOTAL) | 65.8 ms | 4.5% |
| `get_all_facility_overviews` (2nd alloc_summary call) | 135.1 ms | 9.2% |
| Facility pie chart generation | 468.4 ms | 31.9% |
| **Allocation type pie chart generation** | **794.1 ms** | **54.1%** |

**Dominated by Matplotlib SVG rendering** (86% of total). DB queries are negligible.

### show_usage=True — 347.8s, 52,923 SQL queries (**23,594× slower**)

| Phase | Time | % |
|---|---|---|
| `cached_allocation_usage` (projcode=TOTAL) — N+1 hotspot #1 | 114.5s | 32.9% |
| `get_all_facility_usage_overviews` (projcode=None) — N+1 hotspot #2 | 228.2s | 65.6% |
| Everything else (charts, metadata) | 5.1s | 1.5% |

### line_profiler breakdown inside `get_allocation_summary_with_usage()`

| Line | % Time | What |
|---|---|---|
| `allocations = query.all()` | **74.7%** | Per-row allocation fetch — 3,916 queries |
| `project.get_subtree_charges(...)` | **16.6%** | Per-allocation charge query |
| `project.get_subtree_adjustments(...)` | **5.8%** | Per-allocation adjustment query |
| ORM query object construction | ~2.5% | Rebuilding 5-join query 3,916× |

---

## Root Cause Analysis

### Root Cause 1: Per-row N+1 Allocation Queries (Priority 1)

**Location:** `src/sam/queries/allocations.py` — `get_allocation_summary_with_usage()`

The function loops over each summary row and issues a separate 5-join allocation query per row (3,916 queries for projcode="TOTAL", one per resource×facility×type combination; 3,856 queries for projcode=None, one per project).

Worse, `get_allocation_summary_with_usage()` was called **twice** with different cache keys:
- `cached_allocation_usage(projcode="TOTAL")` at `blueprint.py:307`
- `get_all_facility_usage_overviews()` → `cached_allocation_usage(projcode=None)` at `blueprint.py:351`

This means the entire N+1 computation ran twice, with the projcode=None call being 2× more expensive (3,856 per-project rows vs 62 aggregated rows for projcode=TOTAL).

### Root Cause 2: `account_user` Selectin Lazy-Load (Priority 2)

**Location:** `src/sam/accounting/accounts.py:55`

```python
users = relationship('AccountUser', back_populates='account', lazy='selectin', cascade='all')
```

`lazy='selectin'` fires a batch SELECT on `account_user` whenever ANY Account ORM object is loaded in the session. This was triggered in two ways:

1. **Directly** — the per-row allocation query at `allocations.py:541` explicitly selected `Account` as a full ORM entity, firing a selectin batch immediately on `.all()`

2. **Indirectly** — `get_subtree_charges()` and `get_subtree_adjustments()` use `.scalar()` aggregate queries that JOIN Account. SQLAlchemy hydrates Account objects into the session identity map during those JOINs, which queues additional selectin loads

### Root Cause 3: Duplicate `get_allocation_summary` Calls (Priority 3)

**Location:** `src/webapp/dashboards/allocations/blueprint.py`

`get_allocation_summary()` was called 4 times per request with essentially overlapping data:
1. Line 257: projcode="TOTAL" for the main table display
2. Line 275 → `get_all_facility_overviews()`: projcode=None for facility pie charts
3. Inside `cached_allocation_usage(projcode="TOTAL")` → `get_allocation_summary_with_usage()`: duplicate of #1
4. Inside `get_all_facility_usage_overviews()` → `cached_allocation_usage(projcode=None)` → `get_allocation_summary_with_usage()`: duplicate of #2

---

## Phase 1 Fixes (Implemented — this branch)

### Fix 1+2: Single Bulk Allocation Fetch

**File:** `src/sam/queries/allocations.py`

Added three helper functions:
- `_fetch_all_allocations()` — runs ONE bulk query for all matching allocations (replaces N per-row queries). Includes `noload(Account.users)` to prevent selectin fires on Account objects loaded in this query.
- `_group_allocations_by_summary_key()` — groups the flat result list in Python by the same key dimensions used by `get_allocation_summary()`. Facility, allocation_type, and projcode are fetched as explicit scalar columns to avoid lazy-loads when building keys.
- `_summary_item_key()` — builds the matching key from a summary item dict.

Added `_summary` optional parameter to `get_allocation_summary_with_usage()` to accept a pre-computed summary result and skip the internal `get_allocation_summary()` call.

### Fix 3: Summary Passthrough

**Files:** `src/sam/queries/allocations.py`, `src/sam/queries/usage_cache.py`, `src/webapp/dashboards/allocations/blueprint.py`

Added `_summary` passthrough parameter to `cached_allocation_usage()`. Blueprint now passes its already-computed `summary_data` to the `cached_allocation_usage(projcode="TOTAL")` call, eliminating the 3rd redundant `get_allocation_summary()` call.

### Phase 1 Results

| Metric | Before | After | Change |
|---|---|---|---|
| Total time (show_usage=True, cold cache) | **348s** | **114s** | **67% faster (3.1×)** |
| SQL queries issued | 52,923 | 21,689 | −59% |
| Per-row allocation queries | 3,916 | 0 | eliminated |
| `account_user` selectin from Account in bulk query | ~3,916 batches | 0 | eliminated |
| Remaining `account_user` selectins (from scalar JOIN hydration) | — | ~6 batches | still present |

Tests: **851 passed, 25 skipped, 2 xpassed** (no regressions).

---

## Phase 2 Fixes (Implemented — this branch)

Addressed the three remaining bottlenecks from Phase 1, plus two follow-up fixes identified during profiling.

### Fix B: Account.users Lazy Loading

**File:** `src/sam/accounting/accounts.py`

Changed `Account.users` and `AccountUser.account` from `lazy='selectin'` to `lazy='select'`.
Eliminated ~6 large `account_user` batch SELECTs (~12s) that fired whenever any Account
object was hydrated into the session identity map by scalar JOIN queries.

### Fix C: Single Allocation Fetch for Both projcode Variants

**Files:** `src/sam/queries/allocations.py`, `src/sam/queries/usage_cache.py`, `src/webapp/dashboards/allocations/blueprint.py`

Added `_aggregate_usage_to_total()` helper that derives projcode="TOTAL" grouping from
per-project usage data in Python. `index()` now calls `cached_allocation_usage(projcode=None)`
once and passes pre-fetched data into `get_all_facility_usage_overviews()` via new `_usage`
parameter. Eliminated one full `_fetch_all_allocations` pass and one full charge query set.

### Fix A: Batch Charge Queries — PRIMARY PATH (VALUES CTE)

**Files:** `src/sam/projects/projects.py`

Added `Project.batch_get_subtree_charges()` and `Project.batch_get_account_charges()` classmethods.

**Primary path** (MariaDB ≥10.3.3 / MySQL ≥8.0.19): all anchor coordinates inlined as a
VALUES CTE; database resolves the date-range JOIN and returns one aggregate per anchor_key.

**Fallback path**: Python-side MPTT range attribution (subtree) or date-group bucketing
(account). A WARNING is logged once per process on fallback so the deployment team can
act on it.

`get_allocation_summary_with_usage()` pre-builds all `alloc_infos`, dispatches to both
batch methods, then does a single Python-side enrichment loop over the results.

### Fix A follow-up: Leaf Node Routing

**File:** `src/sam/queries/allocations.py`

Phase 2 profiling revealed that `batch_get_subtree_charges` was receiving all 7,644
allocations (including leaf nodes). Leaf projects have no descendants — their subtree
query is identical to a direct account_id query. Fixed routing to use `project.is_leaf()`
(existing `NestedSetMixin` method) — only 131 genuine non-leaf projects now take the
CTE subtree path; the remaining 3,691 go to the faster account path.

### Fix A follow-up: batch_get_account_charges VALUES CTE

After routing leaves to `batch_get_account_charges`, it became the new bottleneck (6,955
queries, 26s) because it still grouped by `(resource_type, start_date, end_date)`. Applied
the same VALUES CTE strategy: per-anchor `start_date`/`end_date` inlined in the VALUES
table and enforced in the JOIN ON clause, reducing queries from ~6,900 to ~10 regardless
of date range diversity.

### Phase 2 Results

| Metric | Phase 1 | Phase 2 final | Change |
|---|---|---|---|
| Total time (show_usage=True, cold cache) | **114s** | **18.2s** | **84% faster (6.3×)** |
| Total time vs original baseline | **348s** | **18.2s** | **95% faster (19×)** |
| SQL queries issued | 21,689 | 42 | −99.8% |
| `account_user` selectin batches | ~6 | 0 | eliminated |
| `_fetch_all_allocations` calls | 2 | 1 | eliminated duplicate |
| Charge scalar queries | ~21,000 | ~18 | eliminated |

**Profiler confirmed**: 42 SQL queries, 18.2s cold-cache total. In production (warm TTL
cache), user-facing latency for the show_usage path is well under 1s.

Tests: **851 passed, 25 skipped, 2 xpassed** (no regressions).

---

## Remaining Cold-Cache Breakdown (Phase 2 final, 18.2s)

| Component | Time | % | Notes |
|---|---|---|---|
| `batch_get_account_charges` (CTE) | 6.2s | 34% | 3,691 accounts, ~10 CTE queries |
| `batch_get_subtree_charges` (CTE) | 4.1s | 23% | 131 non-leaf projects, ~8 CTE queries |
| `_fetch_all_allocations` | 2.1s | 12% | Single bulk ORM query, 3,822 rows |
| Charts (cold lru_cache) | 4.7s | 26% | Disappears on warm cache in production |
| Python overhead | 1.1s | 6% | Negligible |

### Remaining Opportunities (not pursued — diminishing returns)

**Temp table for large CTEs**: The largest VALUES CTEs (3,691 rows) take ~2.8s each.
MySQL must parse the entire parameter list before executing. Inserting anchors into a
temporary table and JOINing against it would amortize this overhead and allow indexing.
Estimated gain: ~3–5s. Medium complexity.

**`_fetch_all_allocations` ORM hydration**: 2.1s to materialize 3,822 ORM rows + joins.
Could use `yield_per` or a lighter column selection. Estimated gain: ~0.5–1s. Low priority.

**Matplotlib SVG rendering (show_usage=False)**: 1.4s total, 86% charts. Handled well by
`lru_cache` in steady state. Pre-rendering in a background task is an option if cold-cache
latency becomes a concern.

---

## Key Files

| File | Role |
|---|---|
| `src/sam/queries/allocations.py` | Main query logic — `get_allocation_summary_with_usage`, `_fetch_all_allocations`, `_group_allocations_by_summary_key` |
| `src/sam/queries/usage_cache.py` | TTL cache wrapper — `cached_allocation_usage` |
| `src/webapp/dashboards/allocations/blueprint.py` | Route handler — `index()`, `get_all_facility_usage_overviews()` |
| `src/sam/accounting/accounts.py` | Account model — `lazy='select'` on `Account.users` (Phase 2) |
| `src/sam/projects/projects.py` | `get_subtree_charges`, `get_subtree_adjustments`, `batch_get_subtree_charges`, `batch_get_account_charges` (Phase 2) |
| `utils/profiling/profile_allocations.py` | Profiling script — run to measure progress |
