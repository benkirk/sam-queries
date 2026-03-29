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

### Motivation

`get_allocation_summary_with_usage()` with `projcode=None` fires one usage query per project (N+1 pattern per resource/facility/type group). For large datasets, this is seconds of DB time per request. Caching avoids redundant calculation on repeat views.

### Architecture: Query-Level Memoization

Use **`cachetools.TTLCache`** at the query function level — decoupled from Flask-Caching so it works in CLI, API, and webapp alike. This sits transparently behind the existing function.

#### Cache module: `src/sam/queries/usage_cache.py`

```python
from cachetools import TTLCache, cached
from cachetools.keys import hashkey
import threading

# Default TTL=300s, max 512 cache entries
_usage_cache = TTLCache(maxsize=512, ttl=300)
_cache_lock = threading.Lock()

def cached_allocation_usage(session, *, resource_name, facility_name,
                             allocation_type, projcode, active_only,
                             active_at, include_adjustments=True,
                             force_refresh=False):
    """
    Cached wrapper for get_allocation_summary_with_usage().

    Cache key: (resource_name, facility_name, allocation_type, projcode,
                active_only, active_at_date, include_adjustments)
    """
    key = hashkey(
        _normalize(resource_name), _normalize(facility_name),
        _normalize(allocation_type), _normalize(projcode),
        active_only,
        active_at.date() if active_at else None,
        include_adjustments
    )
    with _cache_lock:
        if force_refresh and key in _usage_cache:
            del _usage_cache[key]
        if key in _usage_cache:
            return _usage_cache[key]

    result = get_allocation_summary_with_usage(
        session=session, resource_name=resource_name, ...
    )

    with _cache_lock:
        _usage_cache[key] = result
    return result

def purge_usage_cache():
    """Clear all cached usage data."""
    with _cache_lock:
        _usage_cache.clear()
```

#### Blueprint integration

- Replace `get_allocation_summary_with_usage()` calls in `blueprint.py` with `cached_allocation_usage()`
- Pass `force_refresh=request.args.get('force_refresh') == 'true'` from request args
- Add a staff-only `/allocations/cache/purge` endpoint for manual cache clearing

#### Cache key design

| Parameter | Normalization |
|-----------|--------------|
| `resource_name` | `tuple(sorted(v))` if list, else string |
| `facility_name` | same |
| `allocation_type` | same |
| `projcode` | same |
| `active_at` | `.date()` (day granularity — usage rarely changes within a day) |

#### Configuration

- TTL configurable via `ALLOCATION_USAGE_CACHE_TTL` env var (default 300s)
- Max entries configurable via `ALLOCATION_USAGE_CACHE_SIZE` (default 512)
- Can swap to Redis by replacing `TTLCache` with a Redis-backed adapter if needed

### Bypass and Purge

- **Per-request bypass**: `?force_refresh=true` on any allocations endpoint
- **Manual purge**: `POST /allocations/cache/purge` (admin permission required)
- **Programmatic**: `purge_usage_cache()` callable from CLI/maintenance scripts

---

## Verification

1. **Toggle off (default)**: Dashboard loads as today — no usage columns, single pie chart
2. **Toggle on → Submit**: Project rows show progress bars with color coding; pie shows 2-tab interface
3. **Pie tab switch**: Clicking "Usage" tab swaps to usage-value pie slices; tab state persists across resource tab switches
4. **Progress bar colors**: Green (<75%), yellow (75-89%), red (≥90%) — matches `usage_modal.html` thresholds
5. **Caching (Phase 2)**: Second load with same parameters is visibly faster; `?force_refresh=true` triggers re-query; purge endpoint clears all
6. **CLI parity**: Values in progress bars match `sam-search allocations --resource X --facility Y --show-usage` output

---

## Key Reused Functions

| Function | Location | Role |
|----------|----------|------|
| `get_allocation_summary_with_usage()` | `sam/queries/allocations.py` | Core usage fetch (already imported in blueprint) |
| `get_all_facility_overviews()` | `blueprint.py` | Template for new usage variant |
| `generate_facility_pie_chart_matplotlib()` | `dashboards/charts.py` | Reused for usage pie charts |
| `progress-medium` CSS class | `static/css/components.css` | Progress bar sizing |
| Progress bar color pattern | `partials/usage_modal.html` | Color thresholds to replicate |
