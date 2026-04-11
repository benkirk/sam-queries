# Plan: Project Create & Edit Workflow

## Context

The SAM web app has no mechanism to create or edit projects. Legacy SAM (Java) handles this but is being replaced. The `Project` ORM model is fully featured — nested-set tree hierarchy (MPPT), mnemonic-based projcode generation rules, all relationships — but has no `create()` classmethod and no projcode generation algorithm. This plan delivers:

1. **ORM layer**: `Project.create()` + `next_projcode()` utility
2. **Phase A (this PR)**: Create Project UI — full HTMX modal form following established patterns
3. **Phase B (next PR)**: Edit Project UI — inline edit form + allocation management scaffold

---

## Relevant Files

| Role | Path |
|---|---|
| Project ORM model | `src/sam/projects/projects.py` |
| NestedSetMixin (read-only) | `src/sam/base.py:199` |
| MnemonicCode model | `src/sam/core/organizations.py:421` |
| ProjectCode model (gen rules) | `src/sam/resources/facilities.py:322` |
| Manage functions | `src/sam/manage/__init__.py` |
| Admin blueprint (entry point) | `src/webapp/dashboards/admin/blueprint.py` |
| Sub-route pattern example | `src/webapp/dashboards/admin/orgs_routes.py` |
| HTMX utility | `src/webapp/utils/htmx.py` |
| RBAC permissions | `src/webapp/utils/rbac.py` (`Permission.CREATE_PROJECTS` already exists) |
| Admin dashboard template | `src/webapp/templates/dashboards/admin/dashboard.html` |
| Contract form (FK search pattern) | `src/webapp/templates/dashboards/admin/fragments/create_contract_form_htmx.html` |
| Project search results (FK reuse) | `src/webapp/templates/dashboards/admin/fragments/project_search_results_htmx.html` |
| htmx event config | `src/webapp/static/js/htmx-config.js` |

---

## Phase A: Create Project

### Step 1 — ORM: `next_projcode()` + `Project.create()`

**Add to `src/sam/projects/projects.py`** (near the existing classmethods, around line 200):

#### `next_projcode(session, facility_id, mnemonic_code_id) → str`
A standalone module-level helper (not a classmethod, since it's a generation concern):
```python
def next_projcode(session, facility_id, mnemonic_code_id):
    """Compute the next available projcode for a given facility+mnemonic rule."""
    from ..resources.facilities import ProjectCode
    pc = session.get(ProjectCode, (facility_id, mnemonic_code_id))
    if not pc:
        raise ValueError("No ProjectCode rule for facility_id={facility_id}, mnemonic_code_id={mnemonic_code_id}")
    prefix = pc.mnemonic_code.code          # e.g. "UCB"
    digits = pc.digits                      # e.g. 4 → "UCB0001"
    # Find the highest existing numeric suffix for this prefix
    existing = (
        session.query(Project.projcode)
        .filter(Project.projcode.like(f'{prefix}%'))
        .all()
    )
    max_num = 0
    for (code,) in existing:
        suffix = code[len(prefix):]
        if suffix.isdigit():
            max_num = max(max_num, int(suffix))
    return f"{prefix}{str(max_num + 1).zfill(digits)}"
```

#### `Project.create()` classmethod
```python
@classmethod
def create(cls, session, *, projcode, title, project_lead_user_id,
           area_of_interest_id, abstract=None, project_admin_user_id=None,
           allocation_type_id=None, parent_id=None, charging_exempt=False,
           unix_gid=None, ext_alias=None):
    parent = session.get(cls, parent_id) if parent_id else None
    project = cls(
        projcode=projcode.upper().strip(),
        title=title.strip(),
        abstract=abstract.strip() if abstract else None,
        project_lead_user_id=project_lead_user_id,
        project_admin_user_id=project_admin_user_id,
        area_of_interest_id=area_of_interest_id,
        allocation_type_id=allocation_type_id,
        parent_id=parent_id,
        tree_root=parent.tree_root if parent else None,  # set after flush below
        charging_exempt=charging_exempt,
        unix_gid=unix_gid,
        ext_alias=ext_alias.strip() if ext_alias else None,
    )
    session.add(project)
    session.flush()   # assigns project_id
    # Root projects own their own tree_root
    if parent is None:
        project.tree_root = project.project_id
    session.flush()
    return project
```

**Tree coordinates — MPTT insertion via `NestedSetMixin._ns_place_in_tree()`**:

Add `_ns_place_in_tree(self, session, parent=None)` to `NestedSetMixin` in `src/sam/base.py`:

```python
def _ns_place_in_tree(self, session, parent=None):
    """
    Set nested-set coordinates on a newly-flushed node and shift siblings.
    Must be called AFTER session.flush() so self has a PK.

    For a root node (parent=None): assigns tree_left=1, tree_right=2, tree_root=self.pk.
    For a child node: inserts as last child of parent, shifting affected nodes right by 2.
    Uses raw SQL UPDATEs scoped by tree_root (when _ns_root_col is set) for efficiency.
    """
    cls = type(self)
    my_pk = getattr(self, self._ns_pk_col)
    table = cls.__tablename__

    if parent is None:
        # Standalone root tree
        self.tree_left = 1
        self.tree_right = 2
        if self._ns_root_col:
            setattr(self, self._ns_root_col, my_pk)
    else:
        parent_right = parent.tree_right
        tree_root_val = getattr(parent, self._ns_root_col) if self._ns_root_col else None

        if self._ns_root_col and tree_root_val:
            # Shift only within the parent's tree
            session.execute(text(
                f"UPDATE {table} SET tree_left = tree_left + 2 "
                f"WHERE tree_left >= :pr AND {self._ns_root_col} = :root"
            ), {'pr': parent_right, 'root': tree_root_val})
            session.execute(text(
                f"UPDATE {table} SET tree_right = tree_right + 2 "
                f"WHERE tree_right >= :pr AND {self._ns_root_col} = :root"
            ), {'pr': parent_right, 'root': tree_root_val})
        else:
            # Global tree (Organization style: no tree_root scoping)
            session.execute(text(
                f"UPDATE {table} SET tree_left = tree_left + 2 WHERE tree_left >= :pr"
            ), {'pr': parent_right})
            session.execute(text(
                f"UPDATE {table} SET tree_right = tree_right + 2 WHERE tree_right >= :pr"
            ), {'pr': parent_right})

        self.tree_left = parent_right
        self.tree_right = parent_right + 1
        if self._ns_root_col:
            setattr(self, self._ns_root_col, tree_root_val)

    session.flush()
```

Update `Project.create()` to call `_ns_place_in_tree` after the initial flush:
```python
session.add(project)
session.flush()                          # assigns project_id
project._ns_place_in_tree(session, parent)   # sets tree coords + shifts siblings
```

This uses `text()` (per conventions) and is scoped correctly for Project's `tree_root`. `Organization` can adopt this same method in a follow-up (its `create()` currently leaves tree coords to the IDMS sync process).

Export `next_projcode` from `src/sam/projects/__init__.py`.

---

### Step 2 — New route sub-module: `projects_routes.py`

Create `src/webapp/dashboards/admin/projects_routes.py` following the same pattern as `orgs_routes.py`.

#### Routes

```
GET  /admin/htmx/project-create-form         → render create form fragment
GET  /admin/htmx/project-search-for-parent   → project FK search (parent selector)
GET  /admin/htmx/project-next-projcode       → AJAX: compute next projcode preview
POST /admin/htmx/project-create              → validate + create + htmx_success
```

**`htmx_project_create_form()`** loads:
- `AreaOfInterest` list (all active, ordered)
- `AllocationType` list (all, ordered by name)
- `Facility` list (active, for projcode generation dropdowns)
- `MnemonicCode` list (active, for projcode generation dropdowns)

**`htmx_project_search_for_parent(q)`**: reuses `search_projects_by_code_or_title`, renders a new `project_search_results_fk_htmx.html` fragment (same data, different click-behavior — sets hidden FK input rather than loading a card).

**`htmx_project_next_projcode(facility_id, mnemonic_code_id)`**: calls `next_projcode()`, returns plain text/JSON preview. Called via `hx-get` with `hx-trigger="change"` on the mnemonic or facility dropdowns.

**`htmx_project_create()` POST validation**:
- `projcode`: non-empty, matches `[A-Z0-9]{2,30}` (uppercased), unique check via `Project.get_by_projcode`
- `title`: non-empty, ≤255 chars
- `project_lead_user_id`: valid user FK
- `area_of_interest_id`: valid AOI FK
- `parent_id` (optional): valid project FK
- On error: re-render form with `errors=[]` + `form=request.form`
- On success:
  ```python
  with management_transaction(db.session):
      project = Project.create(db.session, ...)
  return htmx_success(
      'dashboards/admin/fragments/project_create_success_htmx.html',
      {'closeActiveModal': {}, 'loadNewProject': {'projcode': project.projcode}}
  )
  ```

**Permission**: `@require_permission(Permission.CREATE_PROJECTS)`

---

### Step 3 — Templates

**New files:**

1. `src/webapp/templates/dashboards/admin/fragments/project_modals.html`
   - Modal shell `#createProjectModal` with spinner placeholder in `#createProjectFormContainer`
   - Same Bootstrap modal pattern as `organization_modals.html`

2. `src/webapp/templates/dashboards/admin/fragments/create_project_form_htmx.html`
   - Form with `hx-post`, `hx-target="#createProjectFormContainer"`, `hx-disabled-elt`
   - **Projcode section**: radio toggle "Auto-generate" / "Manual"
     - Auto: `<select>` for Facility + `<select>` for Mnemonic → triggers `hx-get` to `/htmx/project-next-projcode` → preview shown in read-only badge; hidden `<input name="projcode">` populated by JS
     - Manual: plain `<input name="projcode">` (uppercased via `oninput`)
   - **Title** (text input, required)
   - **Abstract** (textarea, optional)
   - **Lead** (search-as-you-type, reuses `htmx_search_users_for_org` endpoint — same pattern as contract PI field)
   - **Admin** (optional, same search pattern)
   - **Area of Interest** (`<select>`, required, grouped by AOI group if available)
   - **Allocation Type** (`<select>`, optional)
   - **Parent Project** (search-as-you-type → `htmx_project_search_for_parent`, optional; when selected, show breadcrumb badge)
   - **Charging Exempt** (checkbox, default unchecked)
   - **Unix GID** (number input, optional)
   - **External Alias** (text, optional)
   - Error display block (`{% if errors %}`)

3. `src/webapp/templates/dashboards/admin/fragments/project_search_results_fk_htmx.html`
   - Renders projects as `.fk-search-result` list items with `data-project-id` and `data-project-display` attributes
   - Used for parent project FK selection (click → sets hidden input + breadcrumb badge)

4. `src/webapp/templates/dashboards/admin/fragments/project_create_success_htmx.html`
   - Minimal success message fragment (shows projcode + title + link to view)

---

### Step 4 — Wire up dashboard

**`src/webapp/templates/dashboards/admin/dashboard.html`**:
- Add `{% include 'dashboards/admin/fragments/project_modals.html' %}` near bottom (with other modal includes)
- In the "Search Projects" card header, add:
  ```html
  {% if has_permission(Permission.CREATE_PROJECTS) %}
  <button class="btn btn-sm btn-success" ... hx-get=".../project-create-form" ...>
      <i class="fas fa-plus"></i> Create Project
  </button>
  {% endif %}
  ```

**`src/webapp/static/js/htmx-config.js`**:
- Add handler for `loadNewProject` event: fires `hx-get` to load the new project's card into `#projectCardContainer`

**`src/webapp/dashboards/admin/blueprint.py`** (last line):
- Add `projects_routes` to the import:
  ```python
  from . import resources_routes, facilities_routes, orgs_routes, projects_routes  # noqa
  ```

---

## Phase B: Edit Project (next PR — design only)

### Overview

Edit is triggered from the project card (an "Edit" button visible to `EDIT_PROJECTS` permission holders). It opens a modal with the same fields as Create, pre-populated.

### Additional scope for Edit
- All Project fields (same form, pre-populated)
- **Manage Members**: already works via existing `add_member` / `remove_member` routes; just needs "Edit" tab to surface them cleanly
- **Add Allocation**: new sub-form within Edit to:
  1. Select a `Resource` (search/dropdown)
  2. Set allocation start/end dates + amount
  3. Creates `Account` (project ↔ resource) if not existing, then creates `Allocation` record
  - Uses `sam.manage.allocations` functions

### New routes needed (Phase B)
```
GET  /admin/htmx/project-edit-form/<projcode>     → pre-populated edit form
POST /admin/htmx/project-update/<projcode>         → validate + update fields
GET  /admin/htmx/project-add-allocation-form/<projcode>  → allocation sub-form
POST /admin/htmx/project-add-allocation/<projcode>       → create account + allocation
```

### Refactoring needed for Phase B
- Add `Project.update()` instance method (pattern: validate fields, `self.session.flush()`, return `self`)
- Shared `_project_form_data(session)` helper (loads AOI/AllocationType/Facility/Mnemonic lists) reused by both create and edit form routes

---

## Verification

```bash
# 1. Start the webapp
docker compose up

# 2. Navigate to http://localhost:5050/admin
# 3. In Projects tab, verify "Create Project" button appears
# 4. Click → modal opens with form
# 5. Test auto-generate projcode: select Facility + Mnemonic → preview updates
# 6. Test manual projcode: switch to manual, type code
# 7. Test lead user search: type name, select result → badge appears
# 8. Test parent project search: type code, select → breadcrumb badge appears
# 9. Submit with missing required fields → inline errors shown, modal stays open
# 10. Submit valid form → modal closes, new project card loads in #projectCardContainer
# 11. Verify project visible in search
# 12. Run tests:
source ../.env && pytest tests/ --no-cov
```
