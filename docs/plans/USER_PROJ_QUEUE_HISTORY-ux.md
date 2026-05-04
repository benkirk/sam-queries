# Phase B: per-user / per-project queue load in queue-details drill-down

## Context

Phase A (PR #224, prod since 2026-05-04) populates
`user_proj_queue_status` + lookup tables every ~5 min but exposes **no**
read endpoints — the data is admin-sensitive. The new
`Permission.VIEW_SYSTEM_STATUS_USER_INFO` (commit 9349e05) was added to
gate read access.

Phase B surfaces this data inside the **existing per-queue drill-down**
that operators reach by clicking a queue row on the system_status
dashboard (route `status_dashboard.queue_history`, template
`templates/dashboards/status/queue_history.html`). The new content is
scoped to the clicked queue (`system_id` + `queue_id` filter applied to
every aggregation) and lets operators answer:

- *"Right now, who has the most held jobs in this queue?"* → sortable table
- *"Over the last 7 days, which users / project codes drove the most queue load?"* → top-N+Other stacked-area charts

We follow the established time-range-picker, matplotlib SVG chart, and
sortable-header patterns already in use elsewhere — no new UI primitives.

---

## Phase split (one PR, two commits)

### Phase 1 — Latest-snapshot table
Sortable table beneath the existing queue history chart, columns
matching the parent Queue Status rollup grain (so per-row values are
directly comparable to the queue-level totals already shown above):

`user · project_code · running · pending · held · cores_alloc ·
cores_pending · cores_held · gpus_alloc · gpus_pending · gpus_held ·
nodes_alloc`

Default sort running desc. Pulled from the latest tick for `(system, queue)`.
The `_unknown_` project bucket appears as just another row (no special
styling) — operator can sort to find it like any other.

### Phase 2 — Top-N + Other stacked-area charts
Single chart card with two selectors:
- **Group by**: User | Project code  *(radio / btn-group)*
- **Metric**:   Running | Pending | Held  *(radio / btn-group)*

→ 6 chart variants, one shown at a time. HTMX-swap of just the chart
SVG when selectors change; time range comes from the existing
`time_range_picker` macro (`hours` param shared with the parent chart).
Top-10 named series + "Others" bucket, identical to disk_usage.

---

## Files to add / modify

### New
| Path | Purpose |
|---|---|
| `src/system_status/queries/user_proj_queues.py` | New aggregators (see "Query API" below) |
| `src/webapp/templates/dashboards/status/partials/user_proj_table.html` | Sortable latest-snapshot table partial |
| `src/webapp/templates/dashboards/status/partials/user_proj_chart.html` | Chart fragment swapped by HTMX |
| `src/webapp/static/js/sortable_table.js` | Generic sortable-header JS extracted from allocations dashboard |

### Modified
| Path | Change |
|---|---|
| `src/system_status/queries/__init__.py` | Re-export new helpers |
| `src/webapp/dashboards/status/blueprint.py` | `queue_history()` passes new table data + chart selector defaults; **add** new endpoint `GET /htmx/queue/<system>/<queue_name>/user-proj-chart` (HTMX target) |
| `src/webapp/templates/dashboards/status/queue_history.html` | Conditionally render new section gated on `current_user` permission |
| `src/webapp/dashboards/charts.py` | New `generate_user_proj_stacked_area(timeseries, metric, group_by) -> str` mirroring `generate_disk_usage_stacked_area` |

### No changes
- `src/system_status/models/*` — schema landed in Phase A
- `src/webapp/utils/rbac.py` — permission already exists
- `src/webapp/static/css/allocations.css` — sortable-header CSS reused (or copied to a status-specific stylesheet only if scope warrants)

---

## Query API (new file `src/system_status/queries/user_proj_queues.py`)

Follows the shape of `sam.queries.disk_usage.get_disk_usage_timeseries_by_user()`.

```python
def get_latest_user_proj_queue_snapshot(
    session, *, system: str, queue_name: str
) -> list[dict]:
    """One row per (user, project_code) at MAX(timestamp) for this queue.
    Joins status_users, project_codes, queues, systems. Returns dicts
    with username, project_code, running/pending/held jobs, cores/gpus/
    nodes counters. _unknown_ project rows included with that literal
    string so the template can style them."""

def get_user_proj_queue_timeseries(
    session,
    *,
    system: str,
    queue_name: str,
    start_date: datetime,
    end_date: datetime,
    metric: str,        # 'running_jobs' | 'pending_jobs' | 'held_jobs'
    group_by: str,      # 'user' | 'project'
    top_n: int = 10,
) -> dict:
    """Returns {'dates': [...], 'series': [{'label': 'Others', 'values': [...]},
    {'label': 'alice', 'values': [...]}, ...]} ranked by MAX over
    the window (so a brief spike still ranks). Largest series last so
    matplotlib stacks it on top, matching disk_usage convention."""
```

Reuses the **exact** ranking / Others-bucketing / reverse-for-stack
logic from `src/sam/queries/disk_usage.py:306-413`.

---

## Route changes (`src/webapp/dashboards/status/blueprint.py`)

`queue_history(system, queue_name)`:
- Compute `can_view_user_info = has_permission(current_user, Permission.VIEW_SYSTEM_STATUS_USER_INFO)`
- If true: also fetch `get_latest_user_proj_queue_snapshot(...)` and pass to template
- Pass `can_view_user_info` to template; template skips the new section entirely when false (no 403, just hidden — matches how other gated UI works)

New endpoint:
```python
@bp.route('/htmx/queue/<system>/<queue_name>/user-proj-chart')
@login_required
@require_permission(Permission.VIEW_SYSTEM_STATUS_USER_INFO)
def htmx_user_proj_chart(system, queue_name):
    # reads ?hours=, ?metric=, ?group_by= from request.args
    # → query helper → generate_user_proj_stacked_area → render partial
```

This is the **only** new route gated by the decorator (the table comes
back inside the parent `queue_history` template, gated by the
`can_view_user_info` flag in Jinja). The HTMX endpoint stands alone so
swap-on-selector-change works.

---

## Template changes

`templates/dashboards/status/queue_history.html` — add at the bottom:

```jinja
{% if can_view_user_info %}
  <div class="card mb-4">
    <div class="card-header"><h5>Per-user / per-project (latest snapshot)</h5></div>
    <div class="card-body">
      {% include 'dashboards/status/partials/user_proj_table.html' %}
    </div>
  </div>

  <div class="card mb-4">
    <div class="card-header">
      <h5>User / project queue load over time</h5>
      <!-- group_by + metric btn-groups, hx-get=user-proj-chart endpoint,
           hx-target=#upq-chart, hx-include=time range hidden inputs -->
    </div>
    <div class="card-body">
      <div id="upq-chart"
           hx-get="{{ url_for('status_dashboard.htmx_user_proj_chart',
                              system=system, queue_name=queue_name,
                              hours=hours, metric='running_jobs',
                              group_by='user') }}"
           hx-trigger="load">
        <!-- swapped with partials/user_proj_chart.html -->
      </div>
    </div>
  </div>
{% endif %}
```

Sortable table partial mirrors
`templates/dashboards/allocations/partials/project_table.html` —
`class="sortable-header" data-sort="text|numeric"` headers,
`data-sort-value` cells.

Extract the ~40-line client sort handler currently inlined in
`templates/dashboards/allocations/dashboard.html:619-660` to
`src/webapp/static/js/sortable_table.js`. Both `allocations/dashboard.html`
and `status/queue_history.html` then `<script src="...">`-include it.
Behaviour preserved exactly: rebind on `htmx:afterSwap`, toggle
asc/desc, sort by `data-sort-value` with `numeric|text|date` typing.

---

## Chart renderer (`src/webapp/dashboards/charts.py`)

`generate_user_proj_stacked_area(timeseries, *, metric, group_by) -> str`

- Accepts the dict returned by `get_user_proj_queue_timeseries`
- Y-axis label derived from `metric` (e.g. "Running jobs", "Pending jobs", "Held jobs")
- Title includes the group_by + metric ("Top users by running jobs")
- Otherwise an exact structural mirror of
  `generate_disk_usage_stacked_area` (lines 187–263):
  Others = neutral grey, named series = tab10/tab20 cycle, legend
  reversed and right-anchored, `@_chart_cache` decorator applied

Counts are integers, not bytes — drop the TiB/PiB autoscale logic.

---

## RBAC summary

- Permission: `Permission.VIEW_SYSTEM_STATUS_USER_INFO` (already defined, `webapp/utils/rbac.py:105`)
- New chart endpoint `htmx_user_proj_chart` gated via `@require_permission`
- New table+chart section in `queue_history.html` gated via `can_view_user_info` flag (template-level hide; the parent route stays open)
- Initial role-holders: small set of CISL admins (per QUICKSTART); facility-scoped variants out of scope here

---

## Verification

End-to-end manual check (after each commit):

1. `docker compose up webdev --watch` from `/Users/benkirk/codes/project_samuel/devel`
2. As `benkirk` (auto-logged-in dev user with full perms):
   - Visit `/dashboards/status/`, click any non-empty queue row (e.g. derecho/main)
   - Phase 1: confirm table renders, all column headers sort asc/desc, `_unknown_` rows visible-but-styled, totals plausibly match the parent `queue_status` row (eyeball, not strict — ingest grain is per-tick)
   - Phase 2: confirm chart loads on page open, switching group_by / metric swaps just the chart, time-range buttons (6h/12h/1d/3d/7d/14d/30d) propagate via `hx-include` and re-request the chart with new `hours`
3. As a user *without* `VIEW_SYSTEM_STATUS_USER_INFO`: confirm the parent `queue_history` page still works and the new section is absent (template `{% if %}` guard).
4. Hit the chart endpoint directly without permission — expect 403.
5. Reconciliation sanity (DB-side, from the QUICKSTART runbook):
   ```
   SELECT qs.cores_allocated - SUM(upq.cores_allocated) AS diff
   FROM queue_status qs
   JOIN user_proj_queue_status upq
        ON upq.timestamp = qs.timestamp AND upq.queue_id = qs.queue_id
   WHERE qs.timestamp = (SELECT MAX(timestamp) FROM queue_status)
   GROUP BY qs.queue_id, qs.cores_allocated
   HAVING qs.cores_allocated <> SUM(upq.cores_allocated);
   ```
   Must return 0 rows, and the table on the page should be consistent
   with that invariant for the queue we drilled into.

Tests (run by user per CLAUDE.md):
- `tests/integration/test_system_status_*.py` — add a route test that hits `htmx_user_proj_chart` with and without the permission
- Unit test for the new query helpers in `tests/unit/test_system_status_queries.py` (or wherever existing query helpers are tested) — assert ranking + Others bucketing

---

## Out of scope (for this PR)

- Retention / pruning policy for `user_proj_queue_status` (still
  intentionally unenforced per QUICKSTART §Retention)
- A non-drill-down "global" view of per-user load across queues
- Facility-scoped variants of the permission
- *(none — sortable JS is being extracted as part of this PR)*
