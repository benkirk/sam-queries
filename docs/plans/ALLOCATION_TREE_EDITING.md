# Plan: Allocation Tree Editing — Three-Case UX Enhancement

## Context

The Edit Project allocations tab currently supports a single edit flow for all allocation types.  
We need to differentiate UX across three distinct cases that arise in practice:

- **Case 1**: Single project, no tree — simplest path, standalone allocation
- **Case 2a**: Project tree with *inherited* allocations (e.g., NMMM0003) — propagate to subprojects when adding; high-friction detach when editing a child
- **Case 2b**: Project tree with *individual* (non-inherited) allocations (e.g., CESM0002/Derecho) — sanity-check that parent ≥ sum(children)

Future scope (design for, don't implement yet): mid-tier privileged users (root project lead/admin) redistributing amounts among siblings without changing the parent total.

---

## Architecture Background

| Concept | Key Code |
|---|---|
| `Allocation.is_inheriting` | `parent_allocation_id is not None` — `allocations.py:89` |
| `Allocation.parent` | `relationship('Allocation', remote_side=[allocation_id], ...)` — `allocations.py:55` ✅ confirmed |
| `Allocation.children` | `relationship('Allocation', back_populates='parent', cascade='all')` — `allocations.py:54` ✅ confirmed |
| `Allocation._walk_tree(fn)` | Recursive DFS applying `fn` to self + all descendants — `allocations.py:93` |
| `update_allocation()` | Raises `InheritingAllocationException` on child; cascades amount/dates via `_walk_tree` — `manage/allocations.py:205` |
| `Allocation.create()` | Internally creates `Account` if none exists for project+resource (lines 186–193) — no external account pre-creation needed |
| Project tree | `NestedSetMixin` — `has_children` hybrid, `get_descendants()`, `get_root()` |
| `Account.get_by_project_and_resource()` | `accounts.py:59` |
| Edit project route | `admin_dashboard.edit_project_page(projcode=...)` — `projects_routes.py:481` |
| HTMX routes | `projects_routes.py` — `htmx_add_allocation_form/POST`, `htmx_edit_allocation_form/POST` |

Propagation topology: **flat star** — all child allocations point directly to the root parent allocation (`parent_allocation_id = root.allocation_id`). This keeps `update_allocation()` cascade working correctly (it walks `allocation.children`).

Formatting convention: allocation amounts use `| fmt_number` throughout (confirmed in existing `project_allocation_tree_htmx.html:140`). This applies regardless of resource type (core-hours, TB, etc.) — consistent with existing display code.

---

## Files to Modify

1. `src/sam/accounting/allocations.py`
2. `src/sam/manage/allocations.py`
3. `src/sam/manage/__init__.py`
4. `src/webapp/dashboards/admin/projects_routes.py`
5. `src/webapp/templates/dashboards/admin/fragments/add_allocation_form_htmx.html`
6. `src/webapp/templates/dashboards/admin/fragments/edit_allocation_form_htmx.html`

---

## Step 1 — ORM: `src/sam/accounting/allocations.py`

**1a.** Add `DETACH` to `AllocationTransactionType` enum (after `DELETE`):
```python
DETACH = "DETACH"
```

**1b.** Add `parent_allocation_id: Optional[int] = None` parameter to `Allocation.create()` signature and pass it to the constructor.  Document that this creates a child in the shared-allocation tree.

---

## Step 2 — Manage layer: `src/sam/manage/allocations.py`

Add three new functions and expand `__all__`.

### `propagate_allocation_to_subprojects(session, parent_allocation, child_projects, user_id, skip_existing=True) -> Tuple[List[Allocation], List[Project]]`

**Atomicity note:** This function must always run inside the caller's `management_transaction()`. It performs N `Allocation.create()` + N `log_allocation_transaction()` calls with no internal commit. If any call raises, the outer transaction rolls back everything — all-or-nothing is guaranteed by the caller's transaction context.

**Account creation note:** `Allocation.create()` internally calls `Account.get_by_project_and_resource()` and creates the Account if absent (lines 186–193 of allocations.py). No pre-creation needed.

- Derives `resource_id` from `parent_allocation.account.resource_id`
- For each project in `child_projects`:
  - Check for existing non-deleted allocation: call `Account.get_by_project_and_resource(session, child.project_id, resource_id)` and check `[a for a in account.allocations if not a.deleted]` if account found
  - If exists and `skip_existing=True`: append to `skipped` list and continue
  - If exists and `skip_existing=False`: raise `ValueError`
  - Call `Allocation.create(session, project_id=..., resource_id=..., amount=parent_allocation.amount, start_date=parent_allocation.start_date, end_date=parent_allocation.end_date, parent_allocation_id=parent_allocation.allocation_id)`
  - Log `AllocationTransactionType.CREATE` with `propagated=True`, comment `"Propagated from parent allocation #<id>"`
- Returns `(created_list, skipped_list)`

### `detach_allocation(session, allocation_id, user_id) -> Allocation`

- Load allocation; raise `ValueError` if not found or `not is_inheriting`
- Record `old_parent_id = allocation.parent_allocation_id`
- Set `allocation.parent_allocation_id = None`; `session.flush()`
- Log `AllocationTransactionType.DETACH` with `comment=f"Detached from parent allocation #{old_parent_id}"`
- Return allocation

### `get_children_allocation_sum(allocation) -> float`

- Returns `sum(child.amount for child in allocation.children if not child.deleted)`
- Returns `0.0` if no non-deleted children

**Update `__all__`** to include the three new functions.

---

## Step 3 — `src/sam/manage/__init__.py`

Add the three new functions to the import block and `__all__`:
```python
from .allocations import (
    ...,
    propagate_allocation_to_subprojects,
    detach_allocation,
    get_children_allocation_sum,
)
```

---

## Step 4 — Routes: `src/webapp/dashboards/admin/projects_routes.py`

### 4a. `htmx_add_allocation_form` (GET ~line 732)

Pass tree context to template:
```python
active_descendants = [d for d in project.get_descendants() if d.active]
child_count = len(active_descendants)
# add to render_template:
project_has_children=project.has_children,
child_count=child_count,
```

Module-level imports to add at the top of `projects_routes.py` (not inside handler bodies — point 5 of review):
```python
from sam.manage.allocations import (
    create_allocation, update_allocation,
    propagate_allocation_to_subprojects,
    detach_allocation,
    get_children_allocation_sum,
)
from sam.accounting.allocations import (
    Allocation, AllocationTransactionType, InheritingAllocationException,
)
```

### 4b. `htmx_add_allocation` (POST ~line 767)

Read new form field early:
```python
apply_to_subprojects = request.form.get('apply_to_subprojects') == 'on'
```

Update `_reload_add_form()` — current signature is `def _reload_add_form(extra_errors=None)` (projects_routes.py:828). It computes `available` resources inline. Extend to pass two new template vars (all three call sites use this helper — only one definition to change):
```python
def _reload_add_form(extra_errors=None):
    from sam.resources.resources import Resource as R
    linked_ids = {a.resource_id for a in project.accounts}
    available = (db.session.query(R).filter(R.is_active).order_by(R.resource_name).all())
    available = [r for r in available if r.resource_id not in linked_ids]
    active_desc = [d for d in project.get_descendants() if d.active]
    return render_template(
        'dashboards/admin/fragments/add_allocation_form_htmx.html',
        project=project,
        available_resources=available,
        today=datetime.now().strftime('%Y-%m-%d'),
        errors=(extra_errors or []) + errors,
        form=request.form,
        project_has_children=project.has_children,   # NEW
        child_count=len(active_desc),                # NEW
    )
```

After creating the parent allocation inside `management_transaction`:
```python
if apply_to_subprojects and project.has_children:
    active_descendants = [d for d in project.get_descendants() if d.active]
    created, skipped = propagate_allocation_to_subprojects(
        db.session, parent_alloc, active_descendants,
        user_id=current_user.user_id, skip_existing=True,
    )
```
(No inner import needed — moved to module level in step 4a.)

Update success message to mention `len(created)` created and `len(skipped)` skipped.

### 4c. `htmx_edit_allocation_form` (GET ~line 872)

Compute and pass additional context (no inner imports needed — moved to module level):
```python
children_sum = get_children_allocation_sum(allocation) if allocation.children else 0.0

parent_info = None
if allocation.is_inheriting and allocation.parent:
    p = allocation.parent
    parent_proj = p.account.project if p.account else None
    parent_info = {
        'allocation_id': p.allocation_id,
        'amount': p.amount,
        # projcode is None if parent account/project missing; template guards on this
        'projcode': parent_proj.projcode if parent_proj and parent_proj.active else None,
    }

# For parent allocations: count active descendants without a linked child alloc
unlinked_descendants_count = 0
if not allocation.is_inheriting and allocation.children and allocation.account:
    project = allocation.account.project
    existing_child_project_ids = {
        child.account.project_id
        for child in allocation.children
        if not child.deleted and child.account
    }
    unlinked_descendants_count = sum(
        1 for d in project.get_descendants()
        if d.active and d.project_id not in existing_child_project_ids
    )

return render_template(...,
    children_sum=children_sum,
    parent_info=parent_info,
    unlinked_descendants_count=unlinked_descendants_count,
)
```

### 4d. `htmx_edit_allocation` (POST ~line 892)

Read `break_inheritance` early:
```python
break_inheritance = request.form.get('break_inheritance') == 'true'
```

Update `_reload_edit_form()` inner function to also recompute and pass `children_sum`, `parent_info`, and `unlinked_descendants_count=0` (skip the unlinked count recomputation on error re-renders for performance).

Replace the `except InheritingAllocationException` block with the detach+edit path:
```python
try:
    with management_transaction(db.session):
        if allocation.is_inheriting and break_inheritance:
            detach_allocation(db.session, alloc_id, current_user.user_id)
            # detach_allocation() calls session.flush() — the identity map now reflects
            # parent_allocation_id=None, so allocation.is_inheriting is False.
            # update_allocation() will no longer raise InheritingAllocationException.
            # Audit trail: DETACH transaction + EDIT transaction (two records — intentional).
            update_allocation(db.session, alloc_id, current_user.user_id, **updates)
        else:
            update_allocation(db.session, alloc_id, current_user.user_id, **updates)
except InheritingAllocationException:
    return _reload_edit_form([
        'Cannot directly edit an inherited allocation. '
        'Check "I understand — break inheritance" to detach it first, '
        'or edit the parent allocation to cascade changes automatically.'
    ])
```

Note: `break_inheritance=True` but `not allocation.is_inheriting` → falls through to normal `update_allocation()` call; the break flag is a no-op (not an error).

All imports for this handler come from module-level (step 4a).

### 4e. New route: `htmx_detach_allocation` (POST)

For standalone detach (no field edits). Permission: `EDIT_PROJECTS` — same as all other allocation mutations. The `AllocationTransaction(DETACH)` record provides audit visibility.

```python
@bp.route('/htmx/detach-allocation/<int:alloc_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_detach_allocation(alloc_id):
    """Break parent_allocation_id link without editing other fields."""
```

- Load allocation, verify `is_inheriting`
- Derive `projcode = allocation.account.project.projcode if allocation.account else ''`
- Call `detach_allocation()` inside `management_transaction()`
- Return `htmx_success_message({'closeActiveModal': {}, 'reloadAllocationTree': projcode}, ...)`

### 4f. New route: `htmx_propagate_to_remaining` (POST)

For parent allocations that have some unlinked descendants:
```python
@bp.route('/htmx/propagate-allocation-to-remaining/<int:alloc_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_propagate_to_remaining(alloc_id):
    """Create child allocations for active descendants that don't yet have one."""
```

- Load allocation; verify `not is_inheriting`
- Build `existing_child_project_ids` from `allocation.children`
- Find `unlinked = [d for d in project.get_descendants() if d.active and d.project_id not in existing_child_project_ids]`
- Call `propagate_allocation_to_subprojects()` inside `management_transaction()`
- Return success message with count

---

## Step 5 — Template: `add_allocation_form_htmx.html`

After the description field and before the bottom alert, add a conditional sub-project propagation section:

```html
{% if project_has_children %}
<div class="mb-3 border rounded p-3 bg-light">
  <div class="form-check">
    <input class="form-check-input" type="checkbox" id="applyToSubprojects"
           name="apply_to_subprojects"
           {% if form and form.get('apply_to_subprojects') == 'on' %}checked{% endif %}>
    <label class="form-check-label fw-semibold" for="applyToSubprojects">
      <i class="fas fa-sitemap me-1 text-info"></i>
      Apply to sub-projects
      <span class="badge bg-info text-dark ms-1">
        {{ child_count }} active sub-project{{ 's' if child_count != 1 else '' }}
      </span>
    </label>
  </div>
  <div class="form-text mt-1 ms-4">
    Creates a linked child allocation for each active sub-project using the same amount
    and dates. Sub-projects share the parent pool — charges count against the root.
    Sub-projects that already have an allocation for this resource are skipped.
  </div>
</div>
{% endif %}
```

Replace the existing bottom alert with context-aware text (show the old "add parent first" hint only when `not project_has_children`).

---

## Step 6 — Template: `edit_allocation_form_htmx.html`

### 6a. Context comment — document new variables: `children_sum`, `parent_info`, `unlinked_descendants_count`

### 6b. Replace `{% if allocation.is_inheriting %}` alert (lines 23–31)

Enhance to link to parent project and add a "Break inheritance" collapsible section.

Key ordering fix: the `<input type="hidden" id="break_inheritance">` is placed **before** the collapse block that references it via `document.getElementById('break_inheritance')` in the `onchange` handler. This ensures the element exists in the DOM before any JS fires.

Parent link guard: the `href` uses `url_for('admin_dashboard.edit_project_page', projcode=parent_info.projcode)` only when `parent_info.projcode` is set (route computes it as `None` for missing or inactive parent projects).

```html
{% if allocation.is_inheriting %}
{# Hidden input BEFORE the collapse so JS getElementById always succeeds #}
<input type="hidden" id="break_inheritance" name="break_inheritance" value="false">

<div class="alert alert-warning py-2 mb-2">
  <i class="fas fa-lock"></i>
  <strong>Inherited allocation.</strong>
  Linked to parent
  {% if parent_info %}
    ({% if parent_info.projcode %}
      <a href="{{ url_for('admin_dashboard.edit_project_page', projcode=parent_info.projcode) }}"
         class="alert-link">{{ parent_info.projcode }}</a>,
    {% endif %}
    alloc #{{ parent_info.allocation_id }}, {{ parent_info.amount | fmt_number }}).
  {% endif %}
  Edit the <strong>parent allocation</strong> — changes cascade here automatically.
  Only the description below is specific to this sub-project.
</div>

<!-- Break inheritance collapsible -->
<div class="mb-3">
  <button type="button" class="btn btn-sm btn-outline-danger"
          data-bs-toggle="collapse" data-bs-target="#breakInheritanceSection">
    <i class="fas fa-unlink me-1"></i> Break inheritance...
  </button>
  <div class="collapse mt-2" id="breakInheritanceSection">
    <div class="border border-danger rounded p-3 bg-danger bg-opacity-10">
      <p class="text-danger fw-semibold mb-2">
        <i class="fas fa-exclamation-triangle me-1"></i>
        Warning: This action is permanent and cannot be automatically undone.
      </p>
      <p class="small mb-2">
        Future edits to the parent allocation will <strong>no longer cascade here</strong>.
        This allocation becomes fully independent. The parent's amount is not adjusted.
      </p>
      <div class="form-check mb-2">
        <input class="form-check-input border-danger" type="checkbox"
               id="confirmBreakInheritance"
               onchange="
                 var unlock = this.checked;
                 document.getElementById('break_inheritance').value = unlock ? 'true' : 'false';
                 ['editAllocAmount','editAllocStart','editAllocEnd'].forEach(function(id){
                   var el = document.getElementById(id);
                   if (el) el.disabled = !unlock;
                 });
               ">
        <label class="form-check-label text-danger" for="confirmBreakInheritance">
          I understand — permanently break inheritance and allow editing these fields
        </label>
      </div>
      <!-- Standalone detach button (no field edits) -->
      <div class="mt-2">
        <button type="button" class="btn btn-sm btn-danger"
                hx-post="{{ url_for('admin_dashboard.htmx_detach_allocation', alloc_id=allocation.allocation_id) }}"
                hx-target="#editAllocationFormContainer"
                hx-confirm="Permanently detach allocation #{{ allocation.allocation_id }}?">
          <i class="fas fa-unlink me-1"></i> Detach only (no other changes)
        </button>
      </div>
    </div>
  </div>
</div>
{% endif %}
```

### 6c. Replace `{% if allocation.children %}` cascade notice (lines 33–41)

Enhance for Case 2b to show children amount summary and validation:

```html
{% if allocation.children %}
{%- set live_children = allocation.children | selectattr('deleted', 'equalto', False) | list -%}
<div class="alert alert-info py-2 mb-2">
  <i class="fas fa-sitemap"></i>
  This allocation has <strong>{{ live_children | length }}</strong>
  child allocation{{ 's' if live_children | length != 1 else '' }}.
  Changes to amount, start date, or end date cascade automatically.
</div>
{% if children_sum is defined and children_sum > 0 %}
<div class="d-flex gap-3 align-items-center flex-wrap small mb-3">
  <span><strong>Children total:</strong> {{ children_sum | fmt_number }}</span>
  <span><strong>This allocation:</strong> {{ allocation.amount | fmt_number }}</span>
  <span><strong>Available:</strong>
    {% set avail = allocation.amount - children_sum %}
    <span class="{{ 'text-danger fw-semibold' if avail < 0 else 'text-success' }}">
      {{ avail | fmt_number }}
    </span>
  </span>
</div>
{% if children_sum > allocation.amount %}
{# Advisory only — does not block save. Admin may be intentionally transitioning amounts. #}
<div class="alert alert-warning py-1 mb-3 small">
  <i class="fas fa-exclamation-triangle"></i>
  Children total exceeds this allocation's amount. Check amounts for charging consistency.
  Saving is still permitted.
</div>
{% endif %}
{% endif %}

{# Offer propagation to remaining unlinked sub-projects #}
{% if unlinked_descendants_count is defined and unlinked_descendants_count > 0 %}
<div class="alert alert-secondary py-1 mb-3 small">
  <i class="fas fa-sitemap me-1"></i>
  <strong>{{ unlinked_descendants_count }}</strong> active sub-project{{ 's' if unlinked_descendants_count != 1 else '' }}
  {{ 'do' if unlinked_descendants_count != 1 else 'does' }} not yet have a linked allocation.
  <button type="button" class="btn btn-sm btn-outline-secondary ms-2"
          hx-post="{{ url_for('admin_dashboard.htmx_propagate_to_remaining', alloc_id=allocation.allocation_id) }}"
          hx-target="#editAllocationFormContainer"
          hx-confirm="Create linked child allocations for {{ unlinked_descendants_count }} remaining sub-project(s)?">
    <i class="fas fa-sitemap me-1"></i> Apply to remaining sub-projects
  </button>
</div>
{% endif %}
{% endif %}
```

### 6d. Add Case 1 (standalone) note

Between the children block and the read-only context div:
```html
{% if not allocation.is_inheriting and not allocation.children %}
<div class="alert alert-secondary py-1 mb-3 small">
  <i class="fas fa-cube me-1"></i>
  Standalone allocation — not linked to any parent or sub-project allocations.
</div>
{% endif %}
```

---

## Edge Cases

| Case | Mitigation |
|---|---|
| Child project already has allocation when "Apply to subprojects" | `skip_existing=True` default; skipped count shown in success message |
| `break_inheritance=True` but `not allocation.is_inheriting` | Route ignores break flag, falls through to normal `update_allocation()` — no error |
| `children_sum` includes soft-deleted children | `get_children_allocation_sum()` explicitly filters `not child.deleted` |
| Template child count includes soft-deleted children | Jinja uses `selectattr('deleted', 'equalto', False)` |
| Deep project tree (grandchildren) | `project.get_descendants()` returns all levels via nested-set; all assigned `parent_allocation_id = root.allocation_id` (flat star) |
| `_reload_edit_form()` missing new context vars | Recompute `children_sum`, `parent_info`, pass `unlinked_descendants_count=0` (skip expensive recompute on error re-renders) |
| Detach+edit SQLAlchemy identity map | `detach_allocation()` calls `session.flush()` — the identity map reflects `parent_allocation_id=None` before `update_allocation()` runs; no race condition |
| Propagation partial failure | Runs inside caller's `management_transaction()` — all-or-nothing rollback on any exception |
| Parent project inactive or missing | Route sets `parent_info['projcode'] = None`; template skips link rendering when `projcode` is falsy |
| Case 2b amount validation | Advisory warning only (non-blocking) — admin may be mid-transition. This is intentional per requirements. |

---

## Future: Mid-tier Privilege (design note only)

When implementing redistribution for root project leads/admins:
- Add new `Permission.MANAGE_SUBTREE_ALLOCATIONS` or similar
- New endpoint: `htmx_transfer_between_children/<parent_alloc_id>` (POST)
- Accepts `{from_alloc_id, to_alloc_id, transfer_amount}` — validates sum unchanged
- Logs `AllocationTransactionType.TRANSFER` on both allocations
- The `related_transaction_id` FK on `AllocationTransaction` is already designed for this

---

## Verification

1. **Case 1** — Navigate to a single (leaf, no parent, no children) project → Edit Allocation → see "Standalone allocation" note, full editing enabled
2. **Case 2a add** — Navigate to a parent project with active children → Add Allocation → see "Apply to sub-projects" checkbox → check it → submit → confirm child allocations created in tree view with correct `parent_allocation_id`
3. **Case 2a edit (inherited child)** — Navigate to NMMM0003 child project → Edit Allocation on an inherited alloc → see "Inherited allocation" warning with parent link → expand "Break inheritance..." → check checkbox → fields unlock → save → confirm DETACH + EDIT transactions in audit log; allocation now shows standalone
4. **Case 2a edit (parent with children)** — Edit parent allocation → see cascade info + "Apply to remaining sub-projects" button if any unlinked
5. **Case 2b** — Navigate to CESM0002 parent project (Derecho) → Edit Allocation → see children total / available display → set amount below children sum → see advisory warning → confirm save still succeeds
6. **Standalone detach button** — Use "Detach only" button → confirm only DETACH transaction logged (no EDIT), allocation now editable independently

### Unit test cases to add to `tests/unit/test_allocation_tree.py`

- `TestDetachAllocation`: detach sets `parent_allocation_id=None`, logs DETACH transaction; raises `ValueError` if called on non-inheriting allocation
- `TestDetachThenUpdate`: after detach+flush, `is_inheriting` is False, `update_allocation()` succeeds without raising
- `TestPropagateToSubprojects`: creates child allocations with correct `parent_allocation_id`; skip_existing=True skips pre-existing allocations; returns correct (created, skipped) counts
- `TestGetChildrenAllocationSum`: returns sum of non-deleted children; excludes soft-deleted children; returns 0.0 when no children
- `TestCascadeAfterDetach`: after detach, editing the former parent's amount does NOT cascade to the detached allocation (it's no longer in `allocation.children`)

Run: `source ../.env && pytest tests/unit/test_allocation_tree.py -v` then full suite `pytest tests/ --no-cov`
