# Facility-Scoped RBAC Manager — Implementation Plan

> **Purpose**: introduce a third RBAC tier — per-user permission grants
> bounded to a specific subset of facilities — so that a "WNA manager"
> can hold `CREATE_PROJECTS`, `EDIT_PROJECTS`, `CREATE_ALLOCATIONS`,
> etc. but only within WNA.

This plan is self-contained: pick it up in a fresh session and execute
it phase-by-phase without needing additional context. All file
references include line numbers as of the commit that authored this
doc; verify with `git grep` if anything has shifted.

---

## Context

### Why this RBAC tier

Today's two RBAC tiers (`docs/plans/EDIT_PROJECTS.md`,
`webapp/utils/rbac.py`):

1. **Bundle / system grants** (`GROUP_PERMISSIONS`,
   `USER_PERMISSION_OVERRIDES`) — unscoped, granted globally. A holder
   of system `EDIT_PROJECTS` can edit any project in the database.
2. **Project-steward grants** (`_is_project_steward` in
   `src/webapp/utils/project_permissions.py`) — per-project, derived
   from being the project's lead, admin, or an ancestor lead/admin.

Operationally there's a missing tier: someone trusted to manage one
facility's project portfolio (create projects, allocate, edit
metadata) without giving them control over every other facility's
projects. Examples:

- A WNA program manager who provisions WNA projects/allocations.
- A NCAR-internal admin who governs only NCAR-classed projects.
- A CISL/CSL liaison whose grant straddles two facilities but stops
  short of UNIV/WNA/NCAR.

### Why this is feasible (cheap to add)

Three structural facts make this graft small:

1. **Every project has a deterministic facility chain** —
   `Project → allocation_type → panel → facility`. The 3-step lookup
   is consistent across the codebase and used by `get_active_projects(
   facility_name=...)` in `src/sam/queries/projects.py:56`. Edge case:
   `Project.allocation_type_id` is nullable; ~20 orphan projects exist
   without one. Deny-by-default fallback for scoped users (orphans
   require an unscoped admin).
2. **Project-scoped decorators already centralize the permission
   funnel** — `require_project_permission`, `require_project_member_access`,
   and `require_allocation_permission` (all in
   `src/webapp/api/access_control.py`) call `_is_project_steward`.
   A single change to that helper carries facility scope into every
   gated route automatically.
3. **`has_permission` stays untouched** — we add a sibling helper
   that takes the facility argument. Existing callers don't change.

### Out of scope for v1

- **Group-bundle facility scoping** (`GROUP_PERMISSIONS['csg']`
  scoped to NCAR) — defer until operational pressure exists.
- **DB-backed storage** — config stays in `src/webapp/utils/rbac.py`,
  matching the existing `USER_PERMISSION_OVERRIDES` pattern.
  Preserves git-blame audit trail. Revisit when ops asks for
  zero-deploy admin grants.
- **New `Permission` enum members** — facility scope is metadata
  about *who* gets which existing permission, not a new permission
  category.

---

## Architecture

### Storage

Add a new top-level dict in `src/webapp/utils/rbac.py` alongside
`USER_PERMISSION_OVERRIDES`:

```python
# Per-user, per-facility permission grants. The user is granted
# `permission` only when the target project's facility is in the set.
# Permissions held here are ADDITIVE to whatever USER_PERMISSION_OVERRIDES
# / GROUP_PERMISSIONS confer (which apply unconditionally).
#
# Format: {username: {facility_name: {Permission, ...}}}
USER_FACILITY_PERMISSIONS: dict[str, dict[str, set[Permission]]] = {
    'wna_manager': {
        'WNA': {
            Permission.CREATE_PROJECTS,
            Permission.EDIT_PROJECTS,
            Permission.CREATE_ALLOCATIONS,
            Permission.EDIT_ALLOCATIONS,
            Permission.EDIT_PROJECT_MEMBERS,
            Permission.VIEW_PROJECTS,
            Permission.VIEW_ALLOCATIONS,
            Permission.VIEW_PROJECT_MEMBERS,
            Permission.ACCESS_ADMIN_DASHBOARD,
        },
    },
    # Multi-facility example:
    'cisl_csl_admin': {
        'CISL': {Permission.EDIT_PROJECTS, Permission.CREATE_ALLOCATIONS},
        'CSL':  {Permission.EDIT_PROJECTS, Permission.CREATE_ALLOCATIONS},
    },
}
```

**Why a separate dict (not nested into `USER_PERMISSION_OVERRIDES`)**:

- Preserves the existing dict's value type (`set[Permission]`).
  Every current call site keeps its existing iteration semantics
  with zero defensive type-checking.
- Scoped vs unscoped grants compose cleanly: a user can hold
  `VIEW_PROJECTS` unscoped (sees everything in lists) AND
  `EDIT_PROJECTS` scoped to WNA (can only edit WNA projects). Two
  dicts, one entry in each.
- Easy to grep: scope-related logic only ever consults the new dict;
  unscoped logic is unchanged.

### Helpers

In `src/webapp/utils/rbac.py`, after the existing `has_permission`:

```python
def has_permission_for_facility(user, permission, facility_name):
    """True iff the user holds ``permission`` AND it applies to the
    given facility — either unconditionally (system grant) or via
    a USER_FACILITY_PERMISSIONS entry naming this facility.

    Args:
        user: Flask-Login current_user wrapper (AuthUser).
        permission: Permission enum member.
        facility_name: Facility.facility_name string. Pass None for
            orphan projects (no allocation_type chain) — only system
            admins can act on those.

    Returns:
        bool. False for unauthenticated users.
    """
    # System grant — applies to every facility, including unknown ones.
    if has_permission(user, permission):
        return True
    if facility_name is None:
        return False  # orphan projects: only unscoped admins
    if not getattr(user, 'is_authenticated', False):
        return False
    scoped = USER_FACILITY_PERMISSIONS.get(user.username, {})
    return permission in scoped.get(facility_name, set())


def user_facility_scope(user, permission):
    """Return the set of facility names where ``user`` may exercise
    ``permission``, or ``None`` for "unscoped" (any facility, including
    orphan projects).

    Use at listing-filter call sites: ``None`` → skip the filter,
    ``set`` → constrain results to those facilities.
    """
    if has_permission(user, permission):
        return None
    if not getattr(user, 'is_authenticated', False):
        return set()
    scoped = USER_FACILITY_PERMISSIONS.get(user.username, {})
    return {f for f, perms in scoped.items() if permission in perms}
```

In `src/sam/projects/projects.py`, add a property on `Project`:

```python
@property
def facility_name(self) -> str | None:
    """The facility this project belongs to, derived through the
    allocation_type → panel → facility chain. Returns None for
    orphan projects (no allocation_type — ~20 such rows in the
    snapshot DB)."""
    if not self.allocation_type:
        return None
    if not self.allocation_type.panel:
        return None
    if not self.allocation_type.panel.facility:
        return None
    return self.allocation_type.panel.facility.facility_name
```

### Enforcement points

#### A. Project-scoped decorators — automatic via `_is_project_steward`

In `src/webapp/utils/project_permissions.py`, the only line to change
in `_is_project_steward`:

```python
# Before (line ~52):
if has_permission(user, system_permission):
    return True

# After:
if has_permission_for_facility(user, system_permission, project.facility_name):
    return True
```

Every route currently using `@require_project_permission`,
`@require_project_member_access`, or `@require_allocation_permission`
(all funnel through `_is_project_steward`) inherits facility scope
without touching the decorators or the routes.

Affected decorators in `src/webapp/api/access_control.py`:
- `require_project_access` (lines ~87)
- `require_project_member_access` (lines ~121)
- `require_project_permission` (lines ~157)
- `require_allocation_permission` (lines ~205)

#### B. Bare `@require_permission(...)` on create routes

These have no project context — the user is creating something new.
Three handlers need inline checks after the form parse:

1. **`htmx_create_project`** in `src/webapp/dashboards/admin/projects_routes.py`
   (~line 110-200 area) — currently `@require_permission(CREATE_PROJECTS)`.
   The form's `facility_id` is the facility. After form validation:

   ```python
   chosen_facility = db.session.get(Facility, form_data['facility_id'])
   if not has_permission_for_facility(
       current_user, Permission.CREATE_PROJECTS, chosen_facility.facility_name
   ):
       abort(403)
   ```

2. **`htmx_add_allocation`** (POST handler) — already on the
   project-scoped chain (the route includes `<projcode>`); after
   the steward-helper update in (A), it inherits facility scope
   automatically. Verify in Phase 2 testing.

3. **Cascading dropdown filters** — `htmx_panels_for_facility`
   (`projects_routes.py:117`) and `htmx_alloc_types_for_panel`
   (`projects_routes.py:147`). Both currently
   `@require_permission(CREATE_PROJECTS)`. Filter the facility option
   list at the source: only render facilities the user is allowed
   to create within. For scoped users, also block the route from
   responding for a non-allowed facility (defense in depth):

   ```python
   allowed = user_facility_scope(current_user, Permission.CREATE_PROJECTS)
   if allowed is not None and chosen_facility.facility_name not in allowed:
       abort(403)
   ```

#### C. Listing routes — server-side filter + UI dropdown restriction

The user-preferred shape is to **restrict the available choices in
the UI** rather than just filtering results. Two patterns:

**C1. Routes with a facility-picker UI**:

Currently the only one is the admin expirations / recently-expired
page (`src/webapp/dashboards/admin/blueprint.py` `expirations_fragment`
at line ~325, `expirations_export` at line ~447, plus the dashboard
template at `src/webapp/templates/dashboards/admin/dashboard.html:131-140`).

Two changes per route:

1. **Render the facility multi-select from the user's allowed set**.
   Replace the hardcoded `<option>` list (currently 7 hardcoded
   facilities) with options derived from the union of:
   - `USER_FACILITY_PERMISSIONS[user.username].keys()` (scoped
     facilities the user can act on)
   - All facilities (when `user_facility_scope(VIEW_PROJECTS)` is
     `None` — i.e., system-permission holder)

   The view function passes `allowed_facility_names` into the
   template; the multi-select iterates that list.

2. **Server-side intersect**. After parsing the `facilities` query
   parameter, intersect with `user_facility_scope(VIEW_PROJECTS)`:

   ```python
   allowed = user_facility_scope(current_user, Permission.VIEW_PROJECTS)
   facilities = request.args.getlist('facilities')
   if allowed is not None:
       facilities = [f for f in facilities if f in allowed] or sorted(allowed)
   ```

   The existing `facility_names=...` argument on
   `get_projects_by_allocation_end_date` already accepts the list
   (`src/sam/queries/projects.py`), so no query-helper changes
   needed.

**C2. Routes with no facility UI** (global search):

`htmx_search_projects()` in `src/webapp/dashboards/admin/blueprint.py:659`
currently calls `search_projects_by_code_or_title()` unrestricted.
Append a facility filter when the user is scoped:

```python
allowed = user_facility_scope(current_user, Permission.VIEW_PROJECTS)
if allowed is not None:
    query = query.join(...).filter(Facility.facility_name.in_(allowed))
```

The `Project → AllocationType → Panel → Facility` join is the same
one used in `get_active_projects(facility_name=...)`.

#### D. API endpoints

`src/webapp/api/v1/projects.py:106` (`list_projects`) already accepts
a `?facility=` query parameter. For scoped users, default-restrict
the result set when the caller didn't pass the parameter (or
intersect when they did):

```python
allowed = user_facility_scope(current_user, Permission.VIEW_PROJECTS)
requested_facility = request.args.get('facility')
if allowed is not None:
    if requested_facility and requested_facility not in allowed:
        abort(403)
    facility_filter = requested_facility or sorted(allowed)
else:
    facility_filter = requested_facility
```

---

## Phased rollout

Each phase is independently shippable. Phases 1-4 add zero behavior
change for any user without a `USER_FACILITY_PERMISSIONS` entry.

### Phase 1 — Plumbing (foundation)

**Files:**
- `src/webapp/utils/rbac.py` — add `USER_FACILITY_PERMISSIONS` dict
  (initially empty), `has_permission_for_facility()`,
  `user_facility_scope()`.
- `src/sam/projects/projects.py` — add `Project.facility_name`
  property.

**Tests:** new `tests/unit/test_rbac.py::TestFacilityScopedPermissions`
class:
- `test_has_permission_for_facility_grants_via_system_perm`
- `test_has_permission_for_facility_grants_via_scoped_entry`
- `test_has_permission_for_facility_denies_other_facility`
- `test_has_permission_for_facility_denies_orphan_project`
- `test_has_permission_for_facility_unauthenticated_returns_false`
- `test_user_facility_scope_returns_none_for_system_admin`
- `test_user_facility_scope_returns_set_for_scoped_user`
- `test_user_facility_scope_returns_empty_set_for_unauthenticated`

`Project.facility_name` test in `tests/unit/test_new_models.py`
or wherever Project tests live:
- Returns the right facility for a project with a full chain.
- Returns `None` for a project with `allocation_type_id IS NULL`
  (use a factory).

No route changes; no behavior change.

### Phase 2 — Steward enforcement

**File:** `src/webapp/utils/project_permissions.py` —
`_is_project_steward` calls `has_permission_for_facility(user,
system_permission, project.facility_name)` instead of bare
`has_permission(user, system_permission)`.

**Tests:**
- `tests/unit/test_project_permissions.py::TestIsProjectSteward` —
  add cases for facility-scoped users:
  - Scoped to project's facility → passes.
  - Scoped to a different facility → fails (still falls through to
    lead/admin check, which itself can pass).
  - Project leads/admins still pass regardless of scope.

**Manual smoke** (after Phase 5 — once a real `wna_manager` is
configured):
- Log in as `wna_manager`. Hit `/admin/project/<wna_projcode>/edit` →
  expect 200. Hit `/admin/project/<ncar_projcode>/edit` → expect 403.
- Existing tests for non-scoped users stay green.

### Phase 3 — Listing filters & dropdown restriction

**Server side:**
- `src/webapp/dashboards/admin/blueprint.py` —
  `expirations_fragment`, `expirations_export`,
  `htmx_search_projects` add `user_facility_scope` intersection.
- `src/webapp/api/v1/projects.py:list_projects` — same.

**Template side:**
- `src/webapp/templates/dashboards/admin/dashboard.html:131-140` —
  facility multi-select renders from `allowed_facility_names`
  context var, not hardcoded list.
- View functions that render the page pass `allowed_facility_names`
  computed from `user_facility_scope(VIEW_PROJECTS)` + (if `None`)
  the full facility list.

**Tests:**
- `tests/api/` — `test_list_projects_facility_scoped` calls the API
  as a scoped user and asserts only allowed-facility projects come
  back; passing `?facility=NCAR` as a WNA-scoped user → 403.
- `tests/unit/` HTMX route smoke for the expirations fragment.

### Phase 4 — Create-route inline checks

**Files:**
- `src/webapp/dashboards/admin/projects_routes.py` —
  `htmx_create_project` adds inline `has_permission_for_facility`
  check after form parse.
- `src/webapp/dashboards/admin/projects_routes.py:117,147` —
  `htmx_panels_for_facility`, `htmx_alloc_types_for_panel` filter
  facility options + 403 on disallowed facility.
- `src/webapp/templates/dashboards/admin/fragments/create_project_form_htmx.html`
  — facility cascade dropdown options driven by allowed set.

**Tests:**
- `tests/unit/test_create_project.py` — POST as scoped user with a
  forged out-of-scope `facility_id` → 403; allowed `facility_id` →
  200.

### Phase 5 — Configure first manager + e2e

- Add `wna_manager` (or whichever real username we want) to
  `USER_FACILITY_PERMISSIONS` with the WNA-scoped permission set.
- Run full suite: `source etc/config_env.sh && pytest`.
- Manual e2e: `docker compose up webdev --watch`, log in via
  `dlawren` (or a designated WNA test user), confirm the
  user-experience matrix in **Verification** below.

---

## Verification matrix

For Phase 5 (full e2e). Test as three users:

| User              | System perms                         | Facility scope        |
|---|---|---|
| `benkirk`         | `set(Permission)` (full admin)       | n/a (unscoped)        |
| `wna_manager`     | none                                 | `WNA` → CRUD perms    |
| `dlawren`         | none                                 | none (project lead)   |

Expected behavior:

| Action | benkirk | wna_manager | dlawren |
|---|---|---|---|
| Open `/admin/` dashboard | ✓ | ✓ (via `ACCESS_ADMIN_DASHBOARD` in scope) | ✗ |
| Search "all projects" → see NCAR result | ✓ | ✗ | ✗ |
| Search "all projects" → see WNA result | ✓ | ✓ | ✗ |
| Open WNA project edit page | ✓ | ✓ | only own |
| Open NCAR project edit page | ✓ | ✗ | only own |
| Create new WNA project | ✓ | ✓ | ✗ |
| Create new NCAR project | ✓ | ✗ | ✗ |
| Forge POST with `facility_id=ncar` as wna_manager | n/a | ✗ (403) | n/a |
| Expirations dropdown shows NCAR option | ✓ | ✗ | ✗ |
| Expirations dropdown shows WNA option | ✓ | ✓ | ✗ |

Smoke `pytest`. The full suite should pass with no `wna_manager`
entry, and pass again with the entry added (only the new tests
should newly exercise the scoped paths).

---

## Pain points & decision log

### 1. Orphan projects (`allocation_type_id IS NULL`)

`Project.facility_name` returns `None`. `has_permission_for_facility`
returns False for any scoped user when `facility_name` is None. Only
unscoped system admins can act on orphans.

**Decision**: deny-by-default for scoped users. Acceptable — orphan
projects are rare (~20 in the snapshot) and represent
data-cleanup-pending state. Operationally, full admins handle them.

### 2. Group bundles stay unscoped in v1

`GROUP_PERMISSIONS['csg' / 'nusd' / 'hsg']` remain unscoped.
Extending the bundle storage to support facility scope is a
follow-up if the operational pattern emerges. Today's bundles
already grant their members fairly broad access, so there's no
clear "scoped bundle" use case.

**Decision**: defer. Per-user `USER_FACILITY_PERMISSIONS` covers
the immediate need.

### 3. Read access semantics

A WNA-scoped manager with `VIEW_PROJECTS` in their
`USER_FACILITY_PERMISSIONS` entry sees only WNA projects in
admin lists. If they should see — but not edit — other
facilities' projects, grant them unscoped `VIEW_PROJECTS` in
`USER_PERMISSION_OVERRIDES` plus scoped `EDIT_PROJECTS` /
`CREATE_PROJECTS` in `USER_FACILITY_PERMISSIONS`. The two dicts
compose: unscoped grants always pass `has_permission_for_facility`;
scoped grants apply only to matching facilities.

**Decision**: document the pattern; no code change needed.

### 4. Audit trail

Existing `management_transaction` logging
(`src/sam/manage/transaction.py`) doesn't distinguish "facility-scoped
grant" from "global grant" — both show as `user=X action=UPDATE
model=Y`. The distinction is in `rbac.py` config (visible in git
blame).

**Decision**: acceptable for v1. If audit needs to surface "this
edit was made under facility-scoped authority," add a structured
metadata field to the audit row in a follow-up.

### 5. Storage in source vs DB

Per the prior decision recorded in
`~/.claude/projects/.../memory/feedback_contain_unstable_modules.md`'s
neighborhood (and the `USER_PERMISSION_OVERRIDES` precedent), keeping
RBAC config in source preserves git-blame audit trail of who got
admin when. No DB migration needed.

**Decision**: in-source. Revisit if zero-deploy admin grants become
a real ops requirement.

---

## Critical files

| Concern | File |
|---|---|
| Storage + helpers | `src/webapp/utils/rbac.py` |
| Facility resolution on Project | `src/sam/projects/projects.py` |
| Steward check (single line) | `src/webapp/utils/project_permissions.py:_is_project_steward` |
| Project-create handler inline check | `src/webapp/dashboards/admin/projects_routes.py` (`htmx_create_project`, `htmx_panels_for_facility`, `htmx_alloc_types_for_panel`) |
| Listing filters (server) | `src/webapp/dashboards/admin/blueprint.py:325,447,659` |
| API listing | `src/webapp/api/v1/projects.py:106` |
| Filter dropdown (UI) | `src/webapp/templates/dashboards/admin/dashboard.html:131-140` |
| Cascade dropdown (UI) | `src/webapp/templates/dashboards/admin/fragments/create_project_form_htmx.html` |
| Tests | `tests/unit/test_rbac.py`, `tests/unit/test_project_permissions.py`, `tests/unit/test_create_project.py`, `tests/api/test_list_projects_*.py` |

---

## Reference: facility list (current snapshot)

```
facility_id | facility_name | active
1           | NCAR          | 1
2           | UNIV          | 1
3           | CSL           | 1
4           | WNA           | 1
5           | CISL          | 1
6           | XSEDE         | 0
7           | ASD           | 1
```

(Verify with `mysql -h 127.0.0.1 -uroot -proot sam -e "SELECT
facility_id, facility_name, active FROM facility ORDER BY
facility_name;"`.)

---

## Pickup checklist

When restarting in a fresh session, confirm:

- [ ] No `USER_FACILITY_PERMISSIONS` entry exists yet in
  `src/webapp/utils/rbac.py` (Phase 5 not started).
- [ ] `Project.facility_name` not yet present in
  `src/sam/projects/projects.py` (Phase 1 not started).
- [ ] `has_permission_for_facility` not yet defined in
  `src/webapp/utils/rbac.py` (Phase 1 not started).
- [ ] `_is_project_steward` still uses bare `has_permission` (Phase
  2 not started — `git grep "has_permission(user, system_permission)"
  src/webapp/utils/project_permissions.py`).

If any check fails, that phase is partially landed — `git log`
reveals where to resume.
