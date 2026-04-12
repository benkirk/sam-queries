# Edit Project Page — Implementation Plan (Revised)

## Context

The admin dashboard supports project creation via modal (Phase A). This plan implements **Phase B: Edit Project** — a dedicated full-page editor covering:

1. Editing project metadata (title, abstract, lead, etc.)
2. Viewing and managing allocations across the full project tree, organized by resource
3. Managing members (surfacing existing add/remove routes in a clean tab)

Scope-ahead: project admins will eventually be able to move HPC/DAV resources between sub-projects (tree total unchanged) — the data model already supports this; this PR lays the structural foundation.

---

## Critical Review of Existing Infrastructure

Before building anything, we reuse what already exists:

| Existing Component | Location | How We Reuse It |
|---|---|---|
| `NestedSetMixin.get_descendants()` etc. | `src/sam/base.py` | Subtree traversal in allocation tree route |
| `tree_fragment()` route | `src/webapp/dashboards/user/blueprint.py:255` | Refactor: extract inline recursion to shared Jinja2 macro |
| `_build_project_resources_data()` | `src/sam/queries/dashboard.py` | Call per-project-node for allocation tree data |
| `get_project_dashboard_data()` | `src/sam/queries/dashboard.py` | Drive each project node's data load |
| `render_project_resources()` macro | `src/webapp/templates/dashboards/user/partials/project_card.html` | Reuse (with minor param addition) inside the tree |
| `update_allocation()` | `src/sam/manage/allocations.py` | Edit allocation handler (already handles cascade + audit) |
| `log_allocation_transaction()` | `src/sam/manage/allocations.py` | CREATE audit on new allocations |
| `EditAllocationForm` | `src/sam/schemas/forms/` | Validate edit-allocation POST |
| `Account.get_by_project_and_resource()` | `src/sam/accounting/accounts.py` | Existence check before creating account |
| `_project_form_data()` | `src/webapp/dashboards/admin/projects_routes.py` | Already shared; reuse for edit Details form |
| `htmx_success_message()` | `src/webapp/utils/htmx.py` | Standard success response |
| `/admin/htmx/search/users?context=fk` | `src/webapp/dashboards/admin/blueprint.py` | FK picker for lead/admin fields |

### Identified Refactoring Opportunity: `tree_fragment()`

The existing `tree_fragment()` (user blueprint, line 255) renders a project hierarchy tree using **inline Python recursion embedded in the route handler**. This pattern:
- Cannot be reused by templates without an HTTP round-trip
- Duplicates any future admin-side tree rendering

**The refactor**: Extract the recursive tree rendering into a **shared Jinja2 macro** in a new shared partial template. This macro accepts an optional `resources_by_projcode` dict to conditionally show allocation data alongside each node. Both the user tree view (`tree_fragment()`) and the new admin allocation tree can use it. `tree_fragment()` is simplified to just call `render_template()`.

---

## Architecture

**URL**: `GET /admin/project/<projcode>/edit` — full Flask page, NOT a modal.

```
/admin/project/SCSG0001/edit
  ├── Tab 1: Details       → pre-populated htmx edit form
  ├── Tab 2: Allocations   → project tree with allocation data per resource (lazy loaded)
  └── Tab 3: Members       → existing add/remove member htmx routes, surfaced here
```

Entry: "Edit Project" button added to `project_card_wrapper.html` (visible to `EDIT_PROJECTS` holders).

---

## Changes Required

### Phase B-0: Refactor `tree_fragment()` ← do first (enables reuse)

**File**: Create `src/webapp/templates/dashboards/shared/project_tree.html`

New shared Jinja2 macro:
```jinja
{% macro render_project_tree(node, current_projcode, resources_by_projcode=none, can_view=false, active_only=false, depth=0) %}
  {# node: Project ORM object
     resources_by_projcode: dict of projcode -> list of resource dicts (from _build_project_resources_data)
                            If None, renders simple name-only tree (original behavior)
     can_view: if true, project codes are clickable (opens details modal) #}
  <li ...>
    [projcode link if can_view] [title] [badges]
    {% if resources_by_projcode and node.projcode in resources_by_projcode %}
      {# Render allocation bars for this node #}
      {% for res in resources_by_projcode[node.projcode] %}
        ... compact progress bar ...
      {% endfor %}
    {% endif %}
    {% if node.children %}
      <ul class="tree-list">
        {% for child in node.children | sort(attribute='projcode') %}
          {% if not active_only or child.active %}
            {{ render_project_tree(child, current_projcode, resources_by_projcode, can_view, active_only, depth+1) }}
          {% endif %}
        {% endfor %}
      </ul>
    {% endif %}
  </li>
{% endmacro %}
```

**Update `tree_fragment()`** in `user/blueprint.py` to use this macro instead of inline Python.

**NOTE**: Jinja2 recursive macros require `call()` syntax — well-supported in Jinja2 2.10+. Alternatively, render via a recursive template include. Use whichever is cleaner in practice.

---

### Phase B-1: `Project.update()` instance method

**File**: `src/sam/projects/projects.py` — add after `Project.create()`:

```python
def update(self, *, title=None, abstract=None, area_of_interest_id=None,
           allocation_type_id=None, charging_exempt=None,
           project_lead_user_id=None, project_admin_user_id=None,
           unix_gid=None, ext_alias=None, active=None) -> 'Project':
    """Update mutable project fields. Uses SessionMixin for self.session.flush()."""
    if title is not None:
        self.title = title.strip()
    if abstract is not None:
        self.abstract = abstract.strip() or None
    if area_of_interest_id is not None:
        self.area_of_interest_id = area_of_interest_id
    if allocation_type_id is not None:
        self.allocation_type_id = allocation_type_id
    if charging_exempt is not None:
        self.charging_exempt = charging_exempt
    if project_lead_user_id is not None:
        self.project_lead_user_id = project_lead_user_id
    if project_admin_user_id is not None:
        self.project_admin_user_id = project_admin_user_id
    if unix_gid is not None:
        self.unix_gid = unix_gid
    if ext_alias is not None:
        self.ext_alias = ext_alias.strip() or None
    if active is not None:
        self.active = active
    self.session.flush()
    return self
```

**Read-only** (excluded from update): `projcode` (immutable), `parent_id` (tree restructuring = separate op).

---

### Phase B-2: `EditProjectForm` schema

**File**: `src/sam/schemas/forms/projects.py`

```python
class EditProjectForm(HtmxFormSchema):
    """Partial form for updating project metadata. All fields optional (use partial=True)."""
    title = f.Str(load_default=None, validate=v.Length(min=1, max=255))
    abstract = f.Str(load_default=None)
    area_of_interest_id = f.Int(load_default=None)
    allocation_type_id = f.Int(load_default=None)
    charging_exempt = f.Bool(load_default=None)
    project_lead_user_id = f.Int(load_default=None)
    project_admin_user_id = f.Int(load_default=None)
    unix_gid = f.Int(load_default=None)
    ext_alias = f.Str(load_default=None)
    active = f.Bool(load_default=None)
```

---

### Phase B-3: New routes in `projects_routes.py`

**File**: `src/webapp/dashboards/admin/projects_routes.py`

```
GET  /admin/project/<projcode>/edit                          → edit_project_page()
POST /admin/htmx/project-update/<projcode>                   → htmx_project_update()
GET  /admin/htmx/project-allocation-tree/<projcode>          → htmx_project_allocation_tree()
GET  /admin/htmx/add-allocation-form/<projcode>              → htmx_add_allocation_form()
POST /admin/htmx/add-allocation/<projcode>                   → htmx_add_allocation()
GET  /admin/htmx/edit-allocation-form/<int:alloc_id>         → htmx_edit_allocation_form()
POST /admin/htmx/edit-allocation/<int:alloc_id>              → htmx_edit_allocation()
```

**`edit_project_page(projcode)`** — full page
- `@require_permission(Permission.EDIT_PROJECTS)`
- Calls `get_project_dashboard_data(db.session, projcode)` for the project + its allocation summary
- Also calls `_project_form_data()` (already exists, reused from create flow)
- Renders `dashboards/admin/edit_project.html`

**`htmx_project_update(projcode)`**
- Validates: title required, lead required, AOI required, FK existence checks
- Calls `project.update(...)` inside `management_transaction`
- Error: re-render `fragments/edit_project_details_htmx.html` with errors
- Success: `htmx_success_message({'reloadEditProjectDetails': projcode}, 'Project updated.')`

**`htmx_project_allocation_tree(projcode)`** — lazy loaded on tab click
- Gets root project + all descendants via `get_descendants(include_self=True)`
- For each node calls `_build_project_resources_data(node)` → reuse existing helper
- Builds `resources_by_projcode` dict: `{projcode: [resource_dict, ...]}`
- Gets all unique resources across the tree, sorted by name
- For each resource, builds ordered list of (project_node, resource_dict_or_None) pairs
  maintaining tree order from `get_descendants()`
- Renders `fragments/project_allocation_tree_htmx.html`
  (calls the shared `render_project_tree` macro with `resources_by_projcode`)

**`htmx_add_allocation_form(projcode)`**
- Loads resources NOT already linked to this project (via existing accounts)
- Renders `fragments/add_allocation_form_htmx.html`

**`htmx_add_allocation(projcode)`**
- Validates: resource_id, amount > 0, start_date, end_date ≥ start_date
- Inside `management_transaction`:
  - `Account.get_by_project_and_resource()` — create Account if missing
  - `Allocation(account_id=..., amount=..., start_date=..., end_date=...)`
  - `log_allocation_transaction(..., AllocationTransactionType.CREATE, ...)`
- Success: `htmx_success_message({'reloadAllocationTree': projcode}, 'Allocation created.')`

**`htmx_edit_allocation_form(alloc_id)`**
- Loads allocation; warns if `allocation.is_inheriting` (read-only note)
- Shows child count if `len(allocation.children) > 0` (changes cascade)
- Renders `fragments/edit_allocation_form_htmx.html`

**`htmx_edit_allocation(alloc_id)`**
- Uses `EditAllocationForm().load(data, partial=True)` — already exists!
- Calls `update_allocation(db.session, alloc_id, current_user.user_id, **updates)` — already exists!
  - `update_allocation` handles: cascade to children, audit log, InheritingAllocationException guard
- Success: `htmx_success_message({'reloadAllocationTree': projcode}, 'Allocation updated.')`

---

### Phase B-4: Templates

**`src/webapp/templates/dashboards/admin/edit_project.html`** — full page

```
{% extends 'dashboards/base.html' %}
Breadcrumb: Admin → Edit Project → PROJCODE
Page header: projcode badge + title + active/inactive badge
Bootstrap tabs:
  Tab 1 "Details":
    hx-get="project-update-form/<projcode>" hx-trigger="load" hx-target="#editDetailsContainer"
    (or render inline since we have the data already)
  Tab 2 "Allocations":
    hx-get="project-allocation-tree/<projcode>" hx-trigger="shown.bs.tab once"
    (lazy — fires once when tab first shown)
  Tab 3 "Members":
    Embed existing member list + add/remove member htmx widgets from project_card.html
    (reuse the existing member display macros)
Modals: addAllocationModal, editAllocationModal (both lazy-loaded via htmx)
Back button → url_for('admin_dashboard.index')
```

**`src/webapp/templates/dashboards/admin/fragments/edit_project_details_htmx.html`**

```
{% call htmx_form(url_for('admin_dashboard.htmx_project_update', projcode=project.projcode),
                  '#editDetailsContainer', 'Save Changes', is_edit=True) %}
  Read-only: projcode, facility (from allocation_type.panel.facility if set)
  Editable fields:
    title (required), abstract (textarea), area_of_interest_id (dropdown),
    allocation_type_id (dropdown — cascades from facility, reuse existing htmx endpoints),
    project_lead_user_id (FK picker — reuse /admin/htmx/search/users?context=fk),
    project_admin_user_id (FK picker, optional),
    charging_exempt (checkbox), active (checkbox),
    unix_gid (number, optional), ext_alias (text, optional)
{% endcall %}
```

**`src/webapp/templates/dashboards/admin/fragments/project_allocation_tree_htmx.html`**

```
{% from 'dashboards/shared/project_tree.html' import render_project_tree %}

For each unique resource (sorted):
  <div class="card mb-3">
    <div class="card-header d-flex justify-content-between">
      <h6>Resource: {{ resource_name }}  [+ Add Allocation button → opens addAllocationModal]
    </div>
    <div class="card-body p-0">
      <ul class="tree-list">
        {{ render_project_tree(root_project, current_projcode=projcode,
                               resources_by_projcode=resources_by_resource[resource_name],
                               can_view=true) }}
      </ul>
    </div>
  </div>
"Add allocation for new resource" button at bottom
```

**`src/webapp/templates/dashboards/admin/fragments/add_allocation_form_htmx.html`**

```
{% call htmx_form(url_for('admin_dashboard.htmx_add_allocation', projcode=projcode), ...) %}
  resource_id (dropdown — resources not already linked to this project)
  amount (float > 0, required)
  start_date (date, required, default=today)
  end_date (date, optional — open-ended allowed)
  description (text, optional)
  Note: "Sub-project allocations can be linked after the parent allocation is created."
{% endcall %}
```

**`src/webapp/templates/dashboards/admin/fragments/edit_allocation_form_htmx.html`**

```
{% call htmx_form(url_for('admin_dashboard.htmx_edit_allocation', alloc_id=allocation.allocation_id), ...) %}
  Read-only: resource name, project code, allocation_id
  {% if allocation.is_inheriting %}
    <div class="alert alert-warning">This is a shared (inherited) allocation. ...</div>
  {% endif %}
  {% if allocation.children %}
    <div class="alert alert-info">Changes cascade to {{ allocation.children|length }} child allocation(s).</div>
  {% endif %}
  amount (float), start_date (date), end_date (date or blank), description (text)
{% endcall %}
```

---

### Phase B-5: Edit button in project card wrapper

**File**: `src/webapp/templates/dashboards/admin/fragments/project_card_wrapper.html`

Add (immediately after the `render_project_card` macro call):
```html
{% if has_permission(Permission.EDIT_PROJECTS) %}
<div class="d-flex justify-content-end mt-1 mb-3">
  <a href="{{ url_for('admin_dashboard.edit_project_page', projcode=project_data.project.projcode) }}"
     class="btn btn-sm btn-outline-warning">
    <i class="fas fa-edit"></i> Edit Project
  </a>
</div>
{% endif %}
```

---

## Data Flow: Allocation Tree Tab

```
htmx_project_allocation_tree(projcode)
  → project = get_by_projcode(projcode)
  → all_nodes = [project] + project.get_descendants()  # NestedSetMixin, ordered root→leaf
  → for each node:
       node_resources = _build_project_resources_data(node)  # REUSE existing helper
       resources_by_projcode[node.projcode] = node_resources
  → unique_resources = sorted set of resource_names across all nodes
  → render template with: root_project=project, all_nodes, resources_by_projcode, unique_resources
```

Template renders one card per resource. Within each card, calls `render_project_tree` macro — which recursively walks `node.children`, looking up each node's allocation data from `resources_by_projcode[node.projcode]`.

**Performance**: For typical NCAR project trees (2-5 levels, ≤20 nodes), calling `_build_project_resources_data()` per node is acceptable. Each call uses `get_detailed_allocation_usage(hierarchical=False)` at the leaf level. If profiling shows N+1 issues on large trees, the existing `batch_get_subtree_charges()` infrastructure can be wired in as a follow-up optimization.

---

## Access Control

| Action | Guard |
|---|---|
| View edit page | `@require_permission(Permission.EDIT_PROJECTS)` |
| Update project details | same |
| Add/edit allocations | same |
| Members tab | `EDIT_PROJECT_MEMBERS` (routes already exist) |
| Future project-admin self-service | `can_manage_project_members()` in `utils/project_permissions.py` |

---

## File Index

| File | Action | Notes |
|---|---|---|
| `src/webapp/templates/dashboards/shared/project_tree.html` | **Create** | Shared Jinja2 macro (replaces inline Python) |
| `src/webapp/dashboards/user/blueprint.py` | **Modify** | Simplify `tree_fragment()` to use the shared macro |
| `src/sam/projects/projects.py` | **Modify** | Add `Project.update()` instance method |
| `src/sam/schemas/forms/projects.py` | **Modify** | Add `EditProjectForm` |
| `src/webapp/dashboards/admin/projects_routes.py` | **Modify** | Add 7 new routes |
| `src/webapp/templates/dashboards/admin/edit_project.html` | **Create** | Full edit page |
| `src/webapp/templates/dashboards/admin/fragments/edit_project_details_htmx.html` | **Create** | Details form fragment |
| `src/webapp/templates/dashboards/admin/fragments/project_allocation_tree_htmx.html` | **Create** | Allocation tree fragment |
| `src/webapp/templates/dashboards/admin/fragments/add_allocation_form_htmx.html` | **Create** | Add allocation form |
| `src/webapp/templates/dashboards/admin/fragments/edit_allocation_form_htmx.html` | **Create** | Edit allocation form |
| `src/webapp/templates/dashboards/admin/fragments/project_card_wrapper.html` | **Modify** | Add "Edit Project" button |

**Existing code reused without modification:**
- `_build_project_resources_data()` — drives per-node allocation data in tree
- `update_allocation()` — edit allocation handler (cascade + audit built in)
- `log_allocation_transaction()` — CREATE audit on new allocations
- `EditAllocationForm` — already validates edit-allocation POSTs
- `Account.get_by_project_and_resource()` — account existence check
- `_project_form_data()` — option lists (AOI/AllocationType/Facility/Mnemonic)
- All existing FK picker htmx endpoints (user search, cascading panel/alloc-type selects)
- All existing member management routes

---

## Verification

1. Admin dashboard → search project → click "Edit Project" button
2. Edit page loads with pre-populated Details tab
3. Submit title change → success message, re-render shows updated title
4. Click Allocations tab → lazy load shows tree by resource with usage bars
5. Click "Add Allocation" → form loads, fill resource/amount/dates → allocation appears in tree
6. Click "Edit" on allocation → modify amount → cascade to children verified
7. Attempt to directly edit inheriting allocation → warning shown
8. Members tab → add/remove member → existing routes work as expected
9. `pytest tests/ --no-cov` — all existing tests pass
10. Verify user dashboard's `/tree/<projcode>` still works (regression on tree_fragment refactor)

---

## Out of Scope

- Moving allocations between sub-projects (project-admin self-service) — architecture ready, UI not yet
- Project tree restructuring (changing parent_id) — separate complex PR
- Soft-deleting an allocation via Edit page — separate route/PR
- Batch "Apply to all sub-projects" allocation creation (Legacy SAM Phase C)
