# Allocations Dashboard Performance: Profiling Findings & Refactor Roadmap

**Branch:** `alloc-dashboard-profiling`
**Date:** 2026-03-30
**Status:** Phase 1 complete (3.1× speedup). Phase 2 planned.

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

## Remaining Bottlenecks (Phase 2)

After Phase 1, the show_usage=True profile shows:

| Phase | Time | % |
|---|---|---|
| `cached_allocation_usage` (projcode=TOTAL) | 53.8s | 47% |
| `get_all_facility_usage_overviews` (projcode=None) | 55.4s | 48% |
| Charts, metadata | 5.2s | 5% |

The time within each `cached_allocation_usage` call breaks down as:
- `_fetch_all_allocations()` (1 bulk query + ORM materialization + residual account_user selectins): **~28s** for 2 calls
- `get_subtree_charges()` × 7,644 allocations × 2 calls: **~58s** total
- `get_subtree_adjustments()` × 7,644 allocations × 2 calls: **~20s** total

### Bottleneck A: `get_subtree_charges` / `get_subtree_adjustments` Per-Allocation Queries

**~21,000 scalar SQL queries** across the two `get_allocation_summary_with_usage()` calls.

Each call to `get_subtree_charges()` issues 1-2 scalar aggregate queries per allocation (one per charge summary table), using MPTT tree coordinates (`tree_root`, `tree_left`, `tree_right`) to aggregate across a project subtree. Currently issued sequentially.

**Proposed fix:** Replace with a single batch query per charge table type that aggregates across ALL allocations at once, using a subquery or JOIN against a values list of (account_id, start_date, end_date) tuples. Returns a dict keyed by allocation, which is then looked up in Python. This would replace ~21,000 queries with ~8 (one per charge table type per `get_summary_with_usage` call).

This is a significant refactor — `get_subtree_charges` would need to become a batch operation accepting a list of `(project, account, start_date, end_date)` tuples.

### Bottleneck B: Residual `account_user` Selectin from Scalar JOIN Hydration

~6 large batch selectin queries (`account_user`) still fire, totaling ~12s. These are triggered because `get_subtree_charges` scalar queries JOIN Account, causing SQLAlchemy to hydrate Account objects into the session identity map — and those Account objects are subject to the `lazy='selectin'` on `Account.users` (which our `noload` query option on `_fetch_all_allocations` cannot suppress).

**Option A:** Change `Account.users` in `src/sam/accounting/accounts.py` from `lazy='selectin'` to `lazy='select'`. Lower-risk — users is only loaded on explicit attribute access. Check all callers to ensure they don't rely on eager loading.

**Option B:** Use `aliased(Account)` in `get_subtree_charges` scalar queries to prevent SQLAlchemy from hydrating Account into the identity map.

**Option C:** Move charge queries to raw SQL (bypasses ORM identity map entirely).

### Bottleneck C: Two separate `cached_allocation_usage` calls with different keys

`cached_allocation_usage(projcode="TOTAL")` and `cached_allocation_usage(projcode=None)` produce different cache keys, so a cold-cache request must compute both. The two calls are currently independent, meaning `_fetch_all_allocations` runs twice with only the grouping dimension differing.

**Proposed fix:** Fetch allocations once (projcode=None bulk fetch), then derive projcode="TOTAL" results in Python by aggregating over the already-loaded allocation list. This eliminates one full `_fetch_all_allocations` call (~14s) and one set of charge queries (~40s).

### Bottleneck D: Matplotlib SVG Rendering (show_usage=False)

For the no-usage path, 86% of the 1.47s total is Matplotlib rendering (16 allocation-type charts + 10 facility charts). The `lru_cache` handles warm hits efficiently.

**Option:** Pre-render charts in a background Celery/APScheduler task and cache as static SVG files. Low priority — 1.47s is acceptable and the lru_cache already handles steady-state.

---

## Estimated Impact of Phase 2

If Bottleneck A (batched charge queries) and Bottleneck C (single bulk fetch covering both projcode variants) are implemented:

- `get_subtree_charges` queries: 21,000 → ~8 (eliminates ~78s)
- Second `_fetch_all_allocations` call: eliminated (~14s)
- Residual account_user selectins: reduced proportionally

Projected show_usage=True cold-cache time: **~20s** (from 114s → **~6×** improvement over baseline).

---

## Key Files

| File | Role |
|---|---|
| `src/sam/queries/allocations.py` | Main query logic — `get_allocation_summary_with_usage`, `_fetch_all_allocations`, `_group_allocations_by_summary_key` |
| `src/sam/queries/usage_cache.py` | TTL cache wrapper — `cached_allocation_usage` |
| `src/webapp/dashboards/allocations/blueprint.py` | Route handler — `index()`, `get_all_facility_usage_overviews()` |
| `src/sam/accounting/accounts.py` | Account model — `lazy='selectin'` on `Account.users` |
| `src/sam/projects/projects.py` | `get_subtree_charges`, `get_subtree_adjustments` |
| `utils/profiling/profile_allocations.py` | Profiling script — run to measure progress |
