# Allocations Dashboard: Show Usage Toggle + Caching

## Context

The allocations dashboard currently shows only allocated amounts. Users need to see how much of each allocation has been consumed. The CLI already supports `--show-usage` (via `get_allocation_summary_with_usage()`), and usage modals already exist per-project. This plan surfaces usage inline — as progress bars per project row and as a 2-tab pie chart — behind a toggleable "Show usage" filter, then adds a caching layer in Phase 2 because `get_allocation_summary_with_usage()` is expensive (one DB query per project).

---

## Phase 1: Show Usage Toggle

### Overview

- Add a **"Show usage"** `form-check form-switch` checkbox to the filter form, matching the style of the admin dashboard's "Active only" toggle (same Bootstrap class pattern: `form-check form-switch ms-3 mb-0`)
- `show_usage` becomes a query parameter included in form submission (default: `false`)
- When `show_usage=true`:
  - `projects_fragment` calls `get_allocation_summary_with_usage()` and renders a progress bar row per project
  - The facility pie chart panel gets a 2-tab interface (Allocated | Usage), with the usage tab showing usage-based slices

### Files to Modify

| File | Change |
|------|--------|
| `src/webapp/dashboards/allocations/blueprint.py` | Accept `show_usage` in `index()` + `projects_fragment()`; when true, call `get_allocation_summary_with_usage()` for facility overview; pass `show_usage` flag to templates |
| `src/webapp/templates/dashboards/allocations/dashboard.html` | Add toggle to filter form; thread `show_usage` into `hx-get` URLs for project fragment; conditionally render 2-tab pie UI |
| `src/webapp/templates/dashboards/allocations/partials/project_table.html` | Add usage progress bar when `show_usage=true` |

### Detailed Steps

#### 1. Filter Form Toggle (`dashboard.html`)

Add alongside the "Update" button in the filter form (same row, after the button):

```html
<div class="form-check form-switch ms-3 mb-0 align-self-end pb-1">
    <input class="form-check-input" type="checkbox" id="showUsage"
           name="show_usage" value="true"
           {% if show_usage %}checked{% endif %}>
    <label class="form-check-label small text-muted fw-normal" for="showUsage">
        Show usage
    </label>
</div>
```

Since it's inside `<form method="GET">`, it is included automatically on submit. Unchecked → param absent → `false`.

#### 2. Thread `show_usage` into the HTMX project fragment URL

In `dashboard.html`, the `hx-get` attribute on the `<td>` that lazy-loads projects needs `&show_usage={{ 'true' if show_usage else 'false' }}` appended.

#### 3. `blueprint.py` — `index()` changes

- Parse `show_usage = request.args.get('show_usage', 'false').lower() == 'true'`
- When `show_usage=True`, call a new helper `get_all_facility_usage_overviews()` (mirrors existing `get_all_facility_overviews()` but uses `get_allocation_summary_with_usage()`) to build per-facility `total_used` aggregates
- Generate a second set of pie chart SVGs (`resource_usage_overviews`) using the usage data
- Pass `show_usage`, `resource_usage_overviews` to template

New helper function in `blueprint.py`:
```python
def get_all_facility_usage_overviews(session, resource_names, active_at):
    """Like get_all_facility_overviews() but aggregates total_used instead of total_amount."""
    ...
    # calls get_allocation_summary_with_usage() with facility_name=None, projcode="TOTAL"
    # aggregates total_used per facility for pie slices
```

#### 4. `blueprint.py` — `projects_fragment()` changes

- Parse `show_usage = request.args.get('show_usage', 'false').lower() == 'true'`
- When `show_usage=True`:
  ```python
  projects = get_allocation_summary_with_usage(
      session=db.session, resource_name=resource,
      facility_name=facility, allocation_type=allocation_type,
      projcode=None, active_only=True, active_at=active_at
  )
  ```
- Pass `show_usage=show_usage` to `project_table.html`

#### 5. `project_table.html` — Progress bar column

When `show_usage=True`, add a "Usage" column between "Annual Rate" and "Start Date":

```html
{% if show_usage and project.get('total_used') is not none %}
<td data-sort-value="{{ project['percent_used'] or 0 }}">
    <div class="progress progress-medium" style="min-width:120px">
        <div class="progress-bar
             {% if (project['percent_used'] or 0) >= 90 %}bg-danger
             {% elif (project['percent_used'] or 0) >= 75 %}bg-warning
             {% else %}bg-success{% endif %}"
             role="progressbar"
             style="width: {{ [project['percent_used'] or 0, 100]|min }}%">
            {{ "%.1f"|format(project['percent_used'] or 0) }}%
        </div>
    </div>
    <small class="text-muted">{{ "{:,.0f}".format(project['total_used'] or 0) }} used</small>
</td>
{% endif %}
```

Reuse the `progress-medium` class from `components.css` (already defined as 18px height).

#### 6. 2-tab pie chart interface in `dashboard.html`

When `show_usage=True`, wrap the `#facilityPieContainer` in a tab interface:

```html
{% if show_usage %}
<ul class="nav nav-pills nav-fill mb-2" id="pieChartTabs" role="tablist">
    <li class="nav-item"><a class="nav-link active" data-bs-toggle="pill" href="#pie-allocated">Allocated</a></li>
    <li class="nav-item"><a class="nav-link" data-bs-toggle="pill" href="#pie-usage">Usage</a></li>
</ul>
<div class="tab-content">
    <div class="tab-pane fade show active" id="pie-allocated">
        <!-- existing #facilityPieContainer content (allocated values) -->
    </div>
    <div class="tab-pane fade" id="pie-usage">
        <!-- usage-based panels, pre-rendered on server -->
    </div>
</div>
{% else %}
<!-- existing #facilityPieContainer as-is -->
{% endif %}
```

The JS `showPieForResource()` function needs to be aware of which tab is active and swap the correct panel set.

---

## Phase 2: Usage Calculation Caching

### Data Dimensions (measured from live DB)

| Dimension | Count |
|-----------|-------|
| Active resources | 19 |
| Active facilities | 7 |
| Active allocation types | 21 |
| Active projects with active allocations | 1,310 |
| Active allocations | 4,260 |
| **Distinct resource × facility × type cells** | **114** |

Project distribution per cell (the hot path — `projcode=None` calls):
- 1–5 projects: 49 cells
- 6–20 projects: 29 cells
- 21–50 projects: 14 cells
- **51+ projects: 22 cells** (e.g. Casper/UNIV/Small = 411, Derecho/UNIV/Small = 406)

### Cache Key Space Analysis

The blueprint makes these distinct `get_allocation_summary_with_usage()` call patterns:

| Call site | Key shape | Distinct keys |
|-----------|-----------|---------------|
| `get_all_facility_usage_overviews()` | `(resource_list, None, None, None, date)` | ~1–3 per active_at date (varies by resource selection) |
| `index()` type breakdown | `(resource_list, None, None, "TOTAL", date)` | ~1–3 per date |
| `projects_fragment()` | `(resource, facility, type, None, date)` | up to **114 per date** (one per rfat cell) |
| `usage_modal()` | `(resource, None, None, projcode, date)` | up to **1,310 per date** (one per project) |

**Worst-case total unique keys for a single active_at date:**
- Dashboard page: ~2–6 keys
- All fragments expanded: ~114 keys
- All usage modals: ~1,310 keys
- Grand total if all routes cached: **~1,430 keys/date**

Usage modals fire on demand and are already fast (single-project lookup), so they are lower priority for caching. The high-value targets are the `projects_fragment` calls (411 projects × N+1 charge queries = the real bottleneck).

### Memory Sizing

Each result dict is ~1 KB (16 scalar fields + nested `charges_by_type` dict with 4–5 floats). Lists are the unit of cache storage:

| Call type | Typical result list length | Memory per cache entry |
|-----------|---------------------------|------------------------|
| facility overview (`projcode=None`, all resources) | ~1,310 rows | ~1.3 MB |
| type breakdown (`projcode="TOTAL"`) | ~114 rows | ~115 KB |
| project fragment (per rfat cell, e.g. Casper/UNIV/Small) | 411 rows | ~420 KB |
| project fragment (median cell) | ~15 rows | ~15 KB |
| usage modal (single project) | 1 row | ~1 KB |

**For all 114 project-fragment cells (the primary use case):**
- Average ~12 projects/cell × 1 KB = ~12 KB/cell
- 114 cells = ~1.4 MB total for all fragment cells on one date

**Revised recommended `maxsize`:**

| Scenario | Keys needed | Memory estimate |
|----------|------------|-----------------|
| Today only (typical use) | ~120 keys | ~10–15 MB |
| 7-day cache span (rare — users browsing historical dates) | ~120 × 7 = ~840 keys | ~70–100 MB |
| Full coverage incl. usage modals | ~1,430 keys | ~200 MB |

The original proposal of **512 entries** is too small for a full 7-day window across all cells but **adequate for single-day operation**. The correct size depends on acceptable memory budget.

**Recommendation: `maxsize=200`, TTL=3600s (1 hour)**
- Covers all 114 rfat cells + a handful of facility/type overview calls for the current date
- ~15–20 MB worst case
- 1-hour TTL is appropriate: charge summaries are written daily, not continuously; sub-hour staleness is acceptable
- Configurable via env var for tuning

### Architecture: `functools.lru_cache` vs `cachetools.TTLCache`

The existing `charts.py` uses `functools.lru_cache` (no TTL, LRU eviction). For usage data we need TTL expiry because the underlying charge data changes. Two options:

**Option A (recommended): `cachetools.TTLCache` in a new module**
- TTL expiry built in
- Thread-safe with a `RLock`
- Decoupled from Flask — works in CLI context too
- `cachetools` is already available (standard Python packaging; verify with `pip show cachetools`)

**Option B: Lean on existing Flask-Caching (`SimpleCache`)**
- Already configured on the blueprint routes with `@cache.cached(timeout=300, query_string=True)`
- Caches rendered HTML, not query results — so de-duplication across `index()` and `projects_fragment()` calling the same underlying function is impossible
- Does NOT work in CLI context
- Verdict: insufficient on its own

### Implementation

#### Config: `src/webapp/config.py` — add to `SAMWebappConfig`

```python
# Usage calculation cache (TTLCache wrapping get_allocation_summary_with_usage)
ALLOCATION_USAGE_CACHE_TTL  = int(os.getenv('ALLOCATION_USAGE_CACHE_TTL', 3600))   # seconds; 0 = disable
ALLOCATION_USAGE_CACHE_SIZE = int(os.getenv('ALLOCATION_USAGE_CACHE_SIZE', 200))    # max entries
```

`TestingConfig` should override both to 0 to prevent cross-test cache pollution:
```python
ALLOCATION_USAGE_CACHE_TTL  = 0
ALLOCATION_USAGE_CACHE_SIZE = 0
```

#### New file: `src/sam/queries/usage_cache.py`

```python
"""
In-memory TTL cache for get_allocation_summary_with_usage() results.

Sits transparently behind the query function. Works in both webapp and CLI.
Bypass with force_refresh=True; purge programmatically with purge_usage_cache().

Configuration is read from Flask app.config when available, falling back to
environment variables so the module works outside a Flask context (CLI, tests).
"""
import os
import threading
from datetime import datetime
from typing import Optional

from cachetools import TTLCache

from sam.queries.allocations import get_allocation_summary_with_usage

def _get_config(key: str, default: int) -> int:
    """Read config from Flask app context if available, else env var, else default."""
    try:
        from flask import current_app
        return int(current_app.config.get(key, default))
    except RuntimeError:
        return int(os.environ.get(key, default))

# Lazy-initialized: cache is built on first use so Flask config is available
_cache: Optional[TTLCache] = None
_lock = threading.RLock()


def _get_cache() -> TTLCache:
    global _cache
    with _lock:
        if _cache is None:
            ttl  = _get_config('ALLOCATION_USAGE_CACHE_TTL', 3600)
            size = _get_config('ALLOCATION_USAGE_CACHE_SIZE', 200)
            _cache = TTLCache(maxsize=max(size, 1), ttl=max(ttl, 1))
        return _cache

def _normalize(value):
    """Make list/string/None values hashable for use as cache key component."""
    if isinstance(value, list):
        return tuple(sorted(value))
    return value


def cached_allocation_usage(
    session,
    *,
    resource_name=None,
    facility_name=None,
    allocation_type=None,
    projcode=None,
    active_only: bool = True,
    active_at: Optional[datetime] = None,
    include_adjustments: bool = True,
    force_refresh: bool = False,
):
    """
    Cached wrapper for get_allocation_summary_with_usage().

    Cache key includes all parameters at day granularity (active_at → date).
    Identical calls within the TTL window return cached results without hitting DB.
    """
    key = (
        _normalize(resource_name),
        _normalize(facility_name),
        _normalize(allocation_type),
        _normalize(projcode),
        active_only,
        active_at.date() if isinstance(active_at, datetime) else active_at,
        include_adjustments,
    )

    cache = _get_cache()
    with _lock:
        if force_refresh:
            cache.pop(key, None)
        elif key in cache:
            return cache[key]

    result = get_allocation_summary_with_usage(
        session=session,
        resource_name=resource_name,
        facility_name=facility_name,
        allocation_type=allocation_type,
        projcode=projcode,
        active_only=active_only,
        active_at=active_at,
        include_adjustments=include_adjustments,
    )

    with _lock:
        cache[key] = result
    return result


def purge_usage_cache() -> int:
    """Clear all cached usage data. Returns number of entries cleared."""
    cache = _get_cache()
    with _lock:
        n = len(cache)
        cache.clear()
    return n


def usage_cache_info() -> dict:
    """Return cache statistics for monitoring/admin display."""
    cache = _get_cache()
    with _lock:
        return {
            'currsize': len(cache),
            'maxsize': cache.maxsize,
            'ttl': cache.ttl,
        }
```

#### `blueprint.py` changes

1. Import `cached_allocation_usage`, `purge_usage_cache`, `usage_cache_info` from `sam.queries.usage_cache`
2. Replace all 4 calls to `get_allocation_summary_with_usage()` with `cached_allocation_usage()`, passing `force_refresh=request.args.get('force_refresh') == 'true'`
3. Add a new admin-only route:

```python
@bp.route('/cache/purge', methods=['POST'])
@login_required
@require_permission(Permission.ADMIN)
def purge_cache():
    """Purge the usage calculation cache (admin only)."""
    n = purge_usage_cache()
    flash(f'Usage cache cleared ({n} entries removed).', 'success')
    return redirect(url_for('allocations_dashboard.index'))
```

4. Optionally pass `usage_cache_info()` to the template for display in a cache status badge (admin-only visibility)

#### Files to modify

| File | Change |
|------|--------|
| `src/webapp/config.py` | Add `ALLOCATION_USAGE_CACHE_TTL` + `ALLOCATION_USAGE_CACHE_SIZE` to `SAMWebappConfig`; set both to 0 in `TestingConfig` |
| `src/sam/queries/usage_cache.py` | **New file** — lazy-init TTLCache wrapper, reads config from Flask app context |
| `src/webapp/dashboards/allocations/blueprint.py` | Replace 4 `get_allocation_summary_with_usage()` call sites with `cached_allocation_usage()`; add admin purge route |
| `src/sam/queries/__init__.py` | Export `cached_allocation_usage`, `purge_usage_cache`, `usage_cache_info` |

### Cache Key Correctness

| Parameter | Normalization | Rationale |
|-----------|--------------|-----------|
| `resource_name` (list or str) | `tuple(sorted(...))` | list order shouldn't affect results |
| `facility_name`, `allocation_type`, `projcode` | same | same reason |
| `active_at` | `.date()` | dashboard only offers date granularity; charges don't change intraday |
| `include_adjustments` | bool as-is | different result sets |

### Bypass and Purge

- **Per-request bypass**: append `?force_refresh=true` to any allocations URL
- **Admin purge endpoint**: `POST /allocations/cache/purge` (admin permission)
- **Programmatic**: `purge_usage_cache()` importable from `sam.queries.usage_cache`
- **Testing**: `TestingConfig` sets both to 0 in `config.py`; the cache is effectively disabled (TTLCache with size=1, ttl=1 — entries expire immediately); test fixtures can also call `purge_usage_cache()` between tests if needed

### Verification

1. Cold load with `show_usage=True` — note load time
2. Repeat same URL within TTL — should be visibly faster; DB query count drops (verify via SQLAlchemy echo or slow query log)
3. Append `?force_refresh=true` — full recompute observed (same as cold load time)
4. `POST /allocations/cache/purge` — flash message confirms N entries cleared; next load is cold
5. `usage_cache_info()` returns correct size after each operation
6. Set `ALLOCATION_USAGE_CACHE_TTL=5` in `.env`, wait 6s, reload — cache expires, results recomputed
7. CLI `sam-search allocations --show-usage` is unaffected (does not use cache by default; can opt in)
