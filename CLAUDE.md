# SAM Queries - Project Memory

## Project Overview

**SAM (System for Allocation Management)** - Python ORM and query tools for NCAR's
resource allocation and accounting database. Used by CISL to manage HPC allocations,
user accounts, project tracking, and charging for Derecho, Casper, and other
computational resources.

**Tech Stack**: SQLAlchemy 2.0, MySQL/MariaDB, Python 3.13, Flask + htmx
(vendored Bootstrap 5.3.3), marshmallow, pytest

---

## Session Setup

```bash
# Recommended: Full environment setup (activates conda, loads .env)
source etc/config_env.sh

# Alternative: Load variables only (if python env is already active)
source ../.env
```

---

## Database Connection

```python
# Local MySQL connection
mysql -u root -h 127.0.0.1 -proot sam

# Session creation (in code)
from sam.session import create_sam_engine
engine, _ = create_sam_engine()
session = Session(engine)
```

**Database**: `sam` — ~100 tables mirrored by ORM models (database is the schema
source of truth; SAM-side migrations are not used — Alembic covers only the
separate `system_status` DB, see `migrations/system_status/`).

---

## Related projects

**hpc-usage-queries** (`/Users/benkirk/codes/hpc-usage-queries/devel`) ships
the `jobhist` CLI for PBS job history, charging, and daily summaries. SAM
loads it as an optional plugin via `require_plugin(HPC_USAGE_QUERIES)` —
see `src/cli/core/base.py` and `sam/plugins.py`. The two CLIs share
architectural conventions deliberately:

- Same `Context` / `BaseCommand` hierarchy shape
- Same exit codes (`EXIT_SUCCESS=0` / `EXIT_NOT_FOUND=1` / `EXIT_ERROR=2` / `EXIT_KEYBOARD_INTERRUPT=130`)
- Same JSON envelope conventions (top-level `kind`, ISO-8601 dates, `float(Decimal)`, sorted sets, `indent=2`, `sort_keys=False`)
- Same `ExporterRegistry` interface (`rich` / `json` for stdout; `dat` / `csv` / `md` / `json-file` for files)

If you change the shape of the JSON envelope, the `Exporter` ABC, or
the `EXIT_*` codes in this repo, check the parallel structures in
`hpc-usage-queries/devel/job_history/cli/` before merging — and update
both repos in lockstep. See `hpc-usage-queries/devel/job_history/README.md`
§ *CLI Architecture* for the canonical recipe (mirrored in SAM's
`src/cli/README.md`).

---

## Code Organization

```
sam-queries/
├── src/sam/              # ORM models + query/write layers
│   ├── base.py              # Base classes, mixins
│   ├── core/                # Users, organizations, groups
│   ├── resources/           # Resources, machines, facilities, charging
│   ├── projects/            # Projects, contracts, areas of interest
│   ├── accounting/          # Accounts, allocations, adjustments
│   ├── activity/            # Job activity (HPC, DAV, disk, archive)
│   ├── summaries/           # Charge summaries
│   ├── integration/         # XRAS integration (tables + views)
│   ├── security/            # Roles, API credentials, access control
│   ├── operational.py       # Synchronizers, tasks, wallclock exemptions
│   ├── queries/             # Read-side query functions (dashboard, charges, ...)
│   ├── manage/              # Multi-entity write ops + management_transaction
│   ├── schemas/             # Marshmallow serialization schemas (3-tier)
│   │   └── forms/           # HTMX/API form-validation schemas (per domain)
│   └── caching/, session/, fmt.py, enums.py, geography.py, plugins.py
├── src/system_status/    # Separate status DB (own bind, Alembic-managed)
├── src/scheduling/       # Ledger-backed task dispatcher (schedules, registry,
│   └── tasks/            #   ledger, runner) — no Click/Flask/rich/k8s imports
│                         #   tasks/: cleanup_status, deactivate_expired,
│                         #           expiration_notices, xras_notices
├── src/querykit/         # Faceted-log query facade — SQLAlchemy only, imports
│                         #   nothing from sam/ or system_status/ (see its README)
├── src/cli/              # sam-search / sam-admin (see src/cli/README.md)
│   ├── core/                # Context, base command classes, exit codes
│   ├── user/ project/ allocations/ accounting/   # Command + display modules
│   ├── awards/ contracts/ tasks/ xras/           # Command + display modules
│   └── cmds/                # Entry points (search.py, admin.py)
├── src/webapp/           # Flask web application (see src/webapp/README.md)
│   ├── api/v1/              # REST blueprints (+ legacy-compat, see §API below)
│   ├── dashboards/          # user/, admin/, allocations/, status/ + project_members
│   │   └── admin/crud.py    # CrudSpec + register_crud (CRUD route generator)
│   ├── admin/               # Flask-Admin (auto-detected model views)
│   ├── auth/ audit/ caching/ disk_scans/ jobs/ limiter/ utils/
│   │   └── utils/form_handler.py   # HtmxFormHandler lifecycle class
│   └── templates/ static/   # Jinja2 + vendored assets
├── collectors/           # PBS/JupyterHub status collectors (own README)
├── helm/                 # k8s chart (see helm/README.md, docs/README-k8s.md)
├── migrations/           # Alembic (system_status DB only)
├── compose.yaml          # Docker Compose (webapp:7050, webdev:5050, DBs, cache)
└── tests/                # See docs/TESTING.md for suite size/timings
    ├── unit/  integration/  api/  perf/   # perf/ gated behind -m perf
    └── factories/           # Layer-2 builders (core, resources, projects,
                             #   operational, security, summaries, _seq)
```

---

## Key ORM Models

### Core Models
- **User** (`users`): System users with UPID, unix_uid
  - Properties: `primary_email`, `all_emails`, `full_name`, `display_name`, `all_projects`
  - Class methods: `get_by_username(session, username)`
  - Relationships: `email_addresses`, `projects`, `accounts`

- **Organization** (`organization`): NCAR labs/sections
- **Institution** (`institution`): Universities, research orgs

- **Project** (`project`): Research projects with projcode, unix_gid
  - Properties: `active`, `lead`, `admin`
  - Instance methods: `get_detailed_allocation_usage(resource_name=None, include_adjustments=True)` — dict of usage by resource
  - Class methods: `get_by_projcode(session, projcode)`
  - Relationships: `accounts`, `allocations`, `users`

### Accounting
- **Account** (`account`): Billing accounts — links projects to resources
- **Allocation** (`allocation`): Resource allocations (hierarchical tree)
  - `is_active` hybrid property; `is_active_at(check_date)`
  - Relationships: `account`, `parent`, `children`, `transactions`
- **AllocationType** (`allocation_type`): NSC, University, Staff, etc.

### Resources
- **Resource** (`resources`) / **ResourceType** (HPC, DAV, DISK, ARCHIVE, DATA ACCESS)
- **Machine** (`machine`): Derecho, Casper, Gust — `is_active` = commissioned, not decommissioned
- **Queue** (`queue`): `is_active` = within start/end date window
- **Facility** (`facility`): UNIV, WNA, NCAR
- **Factor** / **Formula**: charging factors and `@{variable}` template formulas

### Activity/Usage
- **CompJob**/**CompActivity**, **HPCActivity**/**HPCCharge**, **DavActivity**/**DavCharge**,
  **DiskActivity**/**DiskCharge**, **ArchiveActivity**/**ArchiveCharge**

### Security / Integration
- **Role**, **ApiCredentials** (bcrypt-hashed), **RoleApiCredentials**
- **XrasUserView**, **XrasAllocationView**, etc.: read-only database views

---

## Serialization Schemas (`sam/schemas/`)

Marshmallow schemas for API output. Three tiers per entity — pick by cost:

1. **Full** (`UserSchema`, `ProjectSchema`): all fields + nested relationships —
   single-object detail views only.
2. **List** (`UserListSchema`, `ProjectListSchema`): core fields, no expensive
   nesting — collection endpoints / pagination.
3. **Summary** (`UserSummarySchema`, `ProjectSummarySchema`): minimal identifiers —
   for nesting inside other schemas (e.g. `project.lead`).

Datetimes serialize to ISO automatically (DB stores naive datetimes). Use
`fields.Method` to expose `@property` values.

**`AllocationWithUsageSchema`** ⭐ is the key one — computes `used` / `remaining` /
`percent_used` / `charges_by_type` / `adjustments` from the pre-aggregated
summary tables (`comp_charge_summary`, `dav_charge_summary`,
`disk_charge_summary`, `archive_charge_summary`), matching
`Project.get_detailed_allocation_usage()` and the sam-search CLI. It needs
context: `{'account': ..., 'session': ..., 'include_adjustments': ...}`.
Resource-type routing: HPC/DAV → comp+dav summaries, DISK → disk, ARCHIVE →
archive; `remaining = allocated - (charges + adjustments)` over the
allocation's date range.

Form-validation schemas are a separate concern — see §9 below; they live in
`sam/schemas/forms/` (one module per domain, exported from its `__init__.py`).

---

## API Endpoints (webapp `api/v1/`)

Registered blueprints: `projects`, `users`, `charges`, `allocations`, `status`,
`health`, `admin`, plus the **legacy-compat** set below.

Key endpoints (all JSON):
- `GET /api/v1/users/`, `/users/<username>`, `/users/<username>/projects` — accept an
  API key (any valid key; sessions need `VIEW_USERS`/`VIEW_PROJECTS`); `/users/me` is session-only
- `GET /api/v1/projects/`, `/projects/<projcode>`, `/projects/<projcode>/members`
- `GET /api/v1/projects/<projcode>/allocations` → `AllocationWithUsageSchema(many=True)` ⭐
- `GET /api/v1/projects/<projcode>/charges` (+ `/summary`) — detailed / aggregated charge rollups ⭐
- `GET /api/v1/projects/expiring`, `/projects/recently_expired`
- `GET /api/v1/allocations/<allocation_id>` (+ `PUT` for updates)
- Project-member mutations: `POST/DELETE /api/v1/projects/<projcode>/members[...]`,
  `PUT .../admin`
- Charge-summary ingest: `POST /api/v1/charge-summaries/{comp,disk,archive}`

**Legacy-compat blueprints — DO NOT REFACTOR**: `directory_access.py`,
`project_access.py`, `fstree_access.py`, `queue.py`, `wallclock_exemption.py`
intentionally match legacy Java API response shapes for systems-integration
consumers. Additive changes only (e.g. `invalidate_queue_cache()` in queue.py);
response bytes must not change.

---

## Important Patterns & Conventions

### 1. DateTime Handling
```python
# Database uses NAIVE datetimes (no timezone)
from datetime import datetime
now = datetime.now()  # NOT datetime.now(UTC)
```
SAM/MySQL is naive-Mountain; `system_status` is naive-UTC (use
`system_status.timeutil.utcnow_naive`). TIMESTAMP columns auto-update via
`server_default=text('CURRENT_TIMESTAMP')` + `onupdate`.

### 2. Primary Keys
- Single PK: `primary_key=True, autoincrement=True`
- Composite PK: `PrimaryKeyConstraint('col1', 'col2', name='pk_tablename')` in
  `__table_args__`. Never assume single-column PKs — check the database
  (e.g. `dav_activity` is `(dav_activity_id, queue_name)`).

### 3. Relationships
Always bidirectional via `back_populates` on both sides.

### 4. Mixins (`sam/base.py`)
- `TimestampMixin`: creation_time, modified_time
- `SoftDeleteMixin`: deleted flag + `is_active` hybrid (not deleted)
- `ActiveFlagMixin`: active flag + `is_active` hybrid (active == True)
- `DateRangeMixin`: start_date/end_date + `is_active` hybrids
- `NestedSetMixin`: left/right tree coordinates (Project trees; Organization's
  coords are vestigial)
- `SessionMixin`: `self.session` property — required for `update()` instance methods

### 5. Universal `is_active` Interface
Every ORM model exposes `Model.is_active` as a SQLAlchemy hybrid property.
Use it in both Python and SQL contexts — **never** raw column comparisons:

```python
# ✅ DO — works in Python and SQL filter()
if project.is_active: ...
session.query(Project).filter(Project.is_active).all()
session.query(User).filter(~User.is_active).all()   # inversion

# ❌ DON'T — exposes column internals, can't invert cleanly
session.query(Project).filter(Project.active == True).all()
session.query(Machine).filter(Machine.decommission_date == None).all()
```

**Semantics by model type:**
| Mixin / Model | `is_active` meaning |
|---|---|
| `ActiveFlagMixin` (Project, Facility, Panel, …) | `active == True` |
| `SoftDeleteMixin` (Account, …) | `deleted == False` |
| `DateRangeMixin` (AccountUser, UserOrganization, …) | within start/end date range |
| Custom hybrids (Resource, Machine, Queue, PanelSession, …) | commissioned / within date range |
| `User.is_active` | `active == True AND locked == False` |

**Exception — `sam/queries/statistics.py`**: `User.active == True` is kept
intentionally so `active_users` and `locked_users` remain separate counters.

### 6. Views
Mark with `__table_args__ = {'info': {'is_view': True}}`. Never INSERT/UPDATE/DELETE.

### 7. Write Operations on ORM Models
Co-locate write logic with the model definition:
- **`update()` → instance method**: validate fields + `self.session.flush()`, return `self`
- **`create()` → classmethod**: takes `session` explicitly, validates,
  `session.add(obj)` + `session.flush()`, returns instance

```python
def update(self, *, description=None, active=None):
    if description is not None:
        self.description = description if description.strip() else None
    if active is not None:
        self.active = active
    self.session.flush()
    return self

@classmethod
def create(cls, session, *, required_field, optional_field=None):
    obj = cls(required_field=required_field, optional_field=optional_field)
    session.add(obj)
    session.flush()
    return obj
```

**Caller pattern**: load object first (caller handles not-found), then call the method.

**What stays in `sam.manage`**: complex multi-entity ops (`add_user_to_project`),
audit-trail-heavy ops (`update_allocation` + transaction logging), summary
upserts, and the `management_transaction` context manager. **Every web write
runs inside `management_transaction(db.session)`** — it drives the implicit
audit logging; the form-handling helpers in §9 enforce this by construction.
Allocation invariant: every `allocation_transaction` write must keep
replay(history) == amount.

### 8. API Route Protection (webapp)

All project-scoped routes **must** use a decorator from
`webapp/api/access_control.py` — never a hand-rolled helper. The decorators
resolve the URL parameter to the ORM object, 403/404 on failure, and pass the
**object** (not the code/id) to the view function:

| Decorator | Grants access when |
|---|---|
| `@require_project_access` | `VIEW_PROJECTS` OR project member |
| `@require_project_member_access(Permission.X)` | X OR project member |
| `@require_project_permission(Permission.X)` | X OR project lead/admin |
| `@require_project_facility_permission(Permission.X)` | X within the project's facility scope |
| `@require_project_operator_access` | site-operator remediation surfaces |
| `@require_member_management` / `@require_admin_change` | `can_manage_project_members` / `can_change_admin` |
| `@require_allocation_permission(P)` / `@require_allocation_facility_permission(P)` | P (facility-scoped variant); view receives `allocation` |

```python
@bp.route('/<projcode>/allocations', methods=['GET'])
@login_required
@require_project_member_access(Permission.VIEW_ALLOCATIONS)
def get_project_allocations(project):   # ← project object, not projcode
    ...
```

Note: the Basic-Auth path of `login_or_token_required` bypasses the Permission
check — it only gates browser sessions.

### 9. Form Validation & HTMX Handlers

**The rule:** any validated POST/PUT route — HTMX (`webapp/dashboards/.../*_routes.py`)
**or** API — loads input via a schema from `sam.schemas.forms`. If no schema
fits, **add one first** (in the matching domain module, exported from
`forms/__init__.py`), then wire the route. Never write `datetime.strptime`,
`float()`, or `int()` coercion ladders inline in a handler.

**Schema layer (`HtmxFormSchema` base) gives you for free:**
- `unknown=EXCLUDE` (drops CSRF tokens, stray fields)
- **Automatic empty-string dropping** (`_strip_empty_strings` pre-load): pass
  `request.form` straight to `.load()` — optional Int/Float/Date fields fall
  back to `load_default`. Do NOT hand-roll `{k: v for ... if v != ''}` any more.
- `fields.List` values read via `getlist` (multi-checkbox friendly)
- `split_errors()` → `(field_errors, form_level)` for inline rendering;
  `flatten_errors()` for legacy panel lists
- `normalize_end_date()` (23:59:59 convention) and `assert_date_range()`

**Still manual:** unchecked checkboxes send no key, so inject explicit booleans
when the field means "unchecked ≠ unchanged":
`data['active'] = 'active' in request.form`. FK existence checks need the DB and
stay in the route/handler — use
`validate_fk_existence(db.session, (Model, id, 'label'), ...)` from
`webapp/utils/fk_validation.py`.

**Three tiers of handler — pick the smallest that fits:**

1. **Straight-line create/edit** → `handle_htmx_form_post(...)` from
   `webapp/utils/htmx.py` (kwargs: schema_cls, template, do_action,
   success_triggers, extra_context/context_fn, after_commit, ...).
2. **Standard admin CRUD quintet** (edit-form/edit/create-form/create/delete)
   → declare a `CrudSpec` and `register_crud(bp, spec)` from
   `webapp/dashboards/admin/crud.py`. Hard rule: an entity needing more than
   the spec expresses stays a bespoke route — don't grow the spec for one case.
3. **Complex flows** (partial/PUT gating, ORM cross-field checks,
   domain-exception mapping, custom success responses) → subclass
   `HtmxFormHandler` (`webapp/utils/form_handler.py`). Lifecycle:
   `form_input() → load() → clean() → [management_transaction: perform()] →
   after_commit() → on_success()`; every error path funnels through
   `render_errors()`. Raise `FormError('msg')` from `clean()`/`perform()` for
   user-facing rejections. Routes stay 2 lines: load entities,
   `return _XHandler(entity=obj).handle()`.

**Error rendering is inline field errors** (`split_errors` → the
`form_fields.html` macros). Two caveats: (a) a template without those macros
needs the `FlattenedFieldErrors` mixin so field errors fold into the top panel;
(b) a field with no visible input (hidden picker values, cascading selects)
needs its errors rerouted to the panel in `render_errors()` — see
`_AddMemberHandler` / `_AddExemptionHandler` for the pattern.

**Typeahead/search endpoints** → `register_typeahead(bp, ...)` in
`webapp/utils/htmx.py` (rule+endpoint passed explicitly; search callable;
min_len). Endpoints whose branching is the feature stay hand-written.
`modal_triggers('reloadXCard')` builds the standard close-modal+reload
HX-Trigger payload.

**Modal fragments (the PR #464 trap):** a fragment that swaps into an
already-open modal body must **not** carry `data-bs-toggle="modal"` for that
modal — Bootstrap fires the toggle on the open modal and hides it, so the
control looks dead with nothing in the console. Openers live on the *card*
(modal closed); in-modal controls only `hx-target` the body. A fragment that
reaches for a host-page shell id is pinned in `HTMX_FRAGMENT_SHELL_DEPS`
(`tests/unit/test_modal_shell_contract.py`); the `wire-dashboard-feature` skill
(`.claude/skills/`) carries the author-time checklist for wiring dashboard UI.

**PUT (partial update) gating:** load with `partial=True`, then gate the
updates dict on keys present in the original `request.form`, NOT on the loaded
output — `load_default` fills absent fields with None, which would silently
clear them.

### 10. "Active only" Toggles

**Absent means OFF.** htmx omits an *unchecked* checkbox from the request
entirely — a missing `active_only` is how "include inactive rows" arrives.
Parse with the shared helper, never a hand-rolled comparison:

```python
from webapp.utils.htmx import read_active_only
active_only = read_active_only(request.args)                 # checkbox-backed
active_only = read_active_only(request.args, default=True)   # FK picker, no checkbox
```

Template side: emit `value="1"` and wire both directions via the
`active_toggle_search` macro (`dashboards/fragments/search_box.html`); for a
card switch, use `hx-trigger="change"` + `hx-include="this"` (see the
Resources/Organizations/Facilities cards).

### 11. Static Assets & Cache Headers

**Always reference an asset with `url_for('static', filename=...)`** — never a
literal `/static/...` path. `webapp/utils/static_assets.py` appends
`?v=<content hash>` to every `url_for` static URL, and that parameter is what
buys the asset a one-year cache.

Flask's default is `Cache-Control: no-cache`, which makes a browser revalidate
every asset on every page load. Measured on nwc1 2026-08-17 (one pod, 4h37m):
**87.5% of requests were `/static`, and 96% of those answered 304** — body-less
round trips whose only purpose was to say "unchanged". Vendored Bootstrap was
revalidated ~145 times in 4½ hours. Those 304s were also the dominant consumer
of `max_requests` in `gunicorn_config.py`, whose point is to bound *application*
heap growth.

**ONE rule sets the header** — is `?v=` present:

| request | `Cache-Control` |
|---|---|
| carries `?v=` | `public, max-age=31536000, immutable` |
| no `?v=` | `public, max-age=3600` |

No path table, no vendor special case. The conservative branch is deliberately
the **default**, so an asset referenced some novel way degrades to a short TTL
rather than pinning a stale copy for a year — getting the rule wrong costs
efficiency, never correctness. The short branch is load-bearing for the assets
no `url_for` can reach: relative `url()` targets inside a stylesheet
(FontAwesome's `../webfonts/*`, the one `../img/` in the app's own CSS).

- **Content hash, not mtime.** A container rebuild restamps every mtime, so an
  mtime tag would invalidate every asset on every deploy — safe, but it would
  discard exactly the cache this exists to create.
- ⚠️ **Rendered HTML is itself cached** (`user_aware_cache_key`), so after a
  deploy that changes an asset, cached fragments keep emitting the old `?v=`
  until they expire. Covered by the existing post-deploy step,
  `sam-admin cache --refresh`. The asset is never unreachable — `v` is a cache
  key, not a lookup key.
- Gates in `tests/unit/test_static_assets.py`: a real rendered page must emit
  **only** versioned static URLs, no template may hardcode `/static/`, and no
  template may append `?`/`#` after `url_for('static')` (that one is a
  correctness bug — the URL already carries a query string).

### 12. Comment Budget

⚠️ **This section overrides the instinct to match surrounding comment
density.** Much of `src/` is above budget and is being brought down; imitating
it reproduces the problem. `src/cli` (~22% doc lines) is the house baseline,
not the XRAS tree (~42%).

| | budget |
|---|---|
| Module docstring | 10 lines |
| Function/method docstring | 1 line, unless the signature is genuinely ambiguous |
| `Args:`/`Returns:` | omit when annotated names already say it; keep `Raises:` when non-obvious |
| In-body comment | 3 lines |
| Config comment | the constraint and the legal values, nothing else |

Anything longer is a design doc: put it in `docs/` and leave the constraint
plus the path. **Always keep** a comment recording a real trap, a past
production bug, or a non-obvious invariant — compress it, never delete it.

Enforced by `tests/unit/test_docs.py` (spelling, markdown links, cited paths,
changelog phrasing, doc length). Measured by `scripts/doc_ratio.py`. Full
rules and the cleanup sprint: `docs/plans/DOC_SLIMMING.md`.

❌ **DON'T** write changelog phrasing in a comment — `this used to be X`,
   `we previously`, `an earlier version of this comment`. Say what is true
   now; history is in git.
❌ **DON'T** put emoji or box-drawing separators in code or config comments.
   Write `WARNING:`, `NOTE:`, `DO NOT`. Markdown is exempt — including this
   file, whose ⚠️/❌/✅ markers are navigation.
❌ **DON'T** use British spelling in prose. The gate reads comments,
   docstrings and markdown only, so it never forces an identifier rename.

---

## Flask-Admin (`src/webapp/admin/`)

- Gated by the `FLASK_ADMIN_ENABLED` kill-switch (off in prod/public).
- **Model views are auto-detected**: `add_default_models.py` enumerates every
  ORM class on the SAM declarative Base (excluding `__bind_key__` models, i.e.
  system_status) and registers a `SAMModelView` under the "Everything"
  category. Adding an ORM model surfaces an admin view with no edit here.
- Endpoint slugs derive from the class name (`_camel_to_snake`) — stable URLs.
- To customize one model's view: subclass `SAMModelView` and add it to the
  `_CUSTOM_VIEWS` promotion dict; don't create per-model empty classes.

---

## Common ORM Patterns

```python
# Emails — never user.email (no such attribute)
user = User.get_by_username(session, 'benkirk')
user.primary_email;  user.all_emails

# Allocation usage (uses SessionMixin internally — do NOT pass session)
project = Project.get_by_projcode(session, 'SCSG0001')
usage = project.get_detailed_allocation_usage()          # {resource_name: {...}}
usage = project.get_detailed_allocation_usage(resource_name='Derecho')

# Expiration queries return 4-tuples — unpack them
from sam.queries import get_projects_by_allocation_end_date
for project, allocation, resource_name, days_remaining in get_projects_by_allocation_end_date(
        session, start_date=..., end_date=..., facility_names=['UNIV', 'WNA']):
    ...
# get_projects_with_expired_allocations(...) has the same tuple shape
# (project, allocation, resource_name, days_since_expiration).
```

---

## Testing

Suite size, timings, and tier breakdown live in **`docs/TESTING.md`** — keep
counts there, not here. `perf` and `stress` are gated off by default.

```bash
# One-time setup: isolated test container + URL
docker compose --profile test up -d mysql-test
export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'

source etc/config_env.sh && pytest          # parallel (-n auto from pytest.ini)
pytest tests/unit/test_query_functions.py -v
pytest -n 0                                 # force serial
```

**Key mechanics:**
- **Outbound is closed, twice.** No test may open an SMTP socket
  (`smtplib.SMTP` raises) or make a real outbound HTTP request
  (`requests.Session.request` raises for any non-localhost host).
  `pytest_configure` also pins `XRAS_API_KEY=''` and both XRAS levers off
  **before** `load_dotenv` can supply the real ones. ⚠️ That key is
  write-provisioned — a person merge deletes an account in production with no
  undo — so config alone is not enough: config is a value a test can override
  and a fixture can forget. Gates: `tests/unit/test_outbound_guards.py`.
  Tests drive the configured path with fakes, patching the transport on the
  *instance* (which shadows the guard) or replacing `from_environment`.
- **Isolation**: per-test SAVEPOINT rollback
  (`join_transaction_mode="create_savepoint"`) — xdist workers share one DB
  safely. **Safety guard**: `tests/conftest.py` refuses any database other
  than the allowlisted mysql-test container (host port 3307).
- **pytest.ini gates**: `-m "not perf and not stress"` — both tiers run only on
  request (`pytest -m perf -n 0`, `pytest -m stress`) — plus `--maxfail=5`,
  300 s per-test timeout, `-n auto`. Each gated tier owns a declaration file the
  tests check themselves against: `tests/perf/baselines.json` for query-count
  limits, `tests/stress/scenarios.json` for what each scenario expects the
  `xras_action_log` row to say.
- **Route handlers use Flask-SQLAlchemy's `db.session`** (its own connection) —
  they only see committed snapshot rows, and route-level writes would COMMIT.
  House convention: HTTP-layer tests cover auth/validation/404/render smoke;
  happy-path writes are covered at the model layer.

**Test data — two tiers (never blend inside one helper; composing in a test is fine):**
- **Layer 1 — representative fixtures** (`tests/conftest.py`): `active_project`,
  `multi_project_user`, `hpc_resource`, `any_*` — pick ANY snapshot row of a
  structural shape; survives obfuscated-snapshot refreshes.
- **Layer 2 — factories** (`tests/factories/`): `make_user`, `make_project`,
  `make_allocation`, ... — `session` first positional arg, auto-builds the FK
  graph, flushes, returns the instance. For write-path tests needing exact values.

**The `system_status` tier**: per-worker SQLite tempfile bound at
`SQLALCHEMY_BINDS['system_status']`; schema via
`db.create_all(bind_key='system_status')` in the session-scoped `app` fixture;
per-test isolation via `DELETE FROM`. **Critical**: `FLASK_ACTIVE=1` is set in
`pytest_configure` (not the fixture) because `system_status.base.StatusBase`
resolves at import time — without it, module-level
`from system_status import DerechoStatus` during collection binds to a
standalone declarative_base and the bind routing never engages.

**Route-map parity**: `tests/unit/test_route_map_parity.py` pins all dashboard
`(endpoint, rule, methods)` triples to `tests/unit/snapshots/`; regen with
`ROUTE_MAP_REGEN=1` and commit the diff when routes intentionally change.

---

## CLI Tools: sam-search & sam-admin

Modular class-based CLI (`src/cli/` — see its README for the architecture and
how to add commands). Command classes encapsulate logic; display functions are
stateless; admin commands extend search commands via inheritance. Exit codes:
0 success / 1 not-found / 2 error / 130 interrupt.

```bash
# Search
sam-search user benkirk --list-projects --verbose
sam-search user --search "ben%" ; sam-search user --abandoned
sam-search project SCSG0001 --list-users --verbose
sam-search project --upcoming-expirations --list-users
sam-search allocations --resource Derecho --total-facilities --total-types
sam-search contracts AGS-1852977 --list-projects        # SAM's contract table
sam-search contracts --search climate [--all] --pi poulsen --source NSF
sam-search awards AGS-1852977                           # ask NSF/USAspending, cross-ref SAM
sam-search awards --search turbulence                   # composite free-text
sam-search accounting --last 7d --user benkirk          # daily rollups (no plugin)
sam-search accounting --jobs --last 7d --user benkirk   # per-job (job_history plugin)
sam-search accounting --jobs --last 365d --job-id 6049117[28].desched1
sam-search --format json project SCSG0001 | jq          # JSON envelopes everywhere

# Admin (superset of search)
sam-admin user benkirk --validate
sam-admin project SCSG0001 --validate ; sam-admin project SCSG0001 --reconcile
sam-admin accounting --disk --dry-run                   # summary rebuild/reconcile ops
sam-admin xras --readiness | --mnemonic-report | --contract-report | --identity-report   # XRAS boards (docs/xras/)
# Cache refresh — HTTP client for POST /api/v1/admin/cache/refresh (caches live
# in the running webapp + Redis, NOT the DB). Needs SAM_API_USER / SAM_API_PASS.
sam-admin cache --refresh [--category flask|chart|usage|scans|jobs]
```

---

## Display Formatting — `sam.fmt`

All number, date, percentage, and size formatting goes through `src/sam/fmt.py`.
Jinja2 filters are registered in `create_app()` — use them in every template.

| Need | Jinja2 filter | Python (CLI) |
|---|---|---|
| Integer / compact number | `{{ x \| fmt_number }}` | `fmt.number(x)` |
| Percentage (0–100) | `{{ x \| fmt_pct }}` | `fmt.pct(x)` |
| Date / datetime | `{{ x \| fmt_date }}` | `fmt.date_str(x)` |
| Byte size | `{{ x \| fmt_size }}` | `fmt.size(x)` |
| Hours / charge factors / relative time | `fmt_hours`, `fmt_factor`, `fmt_ago` | `fmt.hours(x)`, … |
| Count + agreeing noun (`3 pushes`) | `{{ n \| fmt_plural('push', 'pushes') }}` | `fmt.plural(n, 'push', 'pushes')` |
| UTC→local, allocation units | `to_local_dt`, `alloc_unit` | — |

**Key behaviors**
- Numbers ≤ 100,000 → exact with commas (`34,283`); above → compact (`68.6M`)
- `None` → `'—'` by default for all filters
- `fmt_pct(decimals=N)`, `fmt_date(fmt='%b %Y')`, `fmt_number(raw=True)` overrides
- `SAM_RAW_OUTPUT=1` env-var forces exact integers everywhere (scripting/grepping)
- `fmt.mpl_number_formatter()` for matplotlib tick labels

**Do NOT** use raw `'{:,.0f}'.format(x)` or `.strftime(…)` in templates or CLI
display code. (Migration history: `docs/plans/implemented/FORMAT_DISPLAY.md`.)

---

## Notifications — `sam.notify`

One mailer, three consumers: `sam-admin project --upcoming-expirations
--notify`, the webapp's XRAS activation Notify button, and the weekly
`expiration_notices` scheduled task (below). Design and measurements:
`docs/plans/implemented/NOTIFICATION_FRAMEWORK.md`,
`docs/plans/implemented/EXPIRATION_NOTICES.md`.

```python
from sam.notify import Message, Notifier, Recipient
notifier = Notifier(ledger=NotificationLedger(lambda: Session(engine)))
notifier.send(Message(kind='expiration', recipient=Recipient(...), subject=...,
                      dedup_key='expiration:SCSG0001:2026-09-30:pi@x.edu'))
```

⚠️ **Fail-closed.** `NOTIFY_ENABLED` defaults to **false** everywhere; only
`helm/values.yaml` sets it. Every dev container runs an obfuscated production
snapshot, obfuscation does not remove the mail relay, and `ndir.ucar.edu`
relays for the whole `128.117.0.0/16` — a mailer that defaults on can reach
**any address on the internet**. `TestingConfig` also pins
`NOTIFY_TRANSPORT='null'`, and an autouse fixture in `tests/conftest.py`
makes `smtplib.SMTP` raise so no test can open a socket whatever its config.

| | |
|---|---|
| **Suppression** | every `Message` carries a `dedup_key`; `sent`/`redirected` suppress a repeat, `failed`/`suppressed` do not. `--force` overrides. |
| **Stale `queued`** | a `queued` row suppresses only until `NOTIFY_QUEUED_STALE_SECONDS`. Without that horizon one crash suppresses a recipient **permanently** — the counter on the admin card is the same predicate inverted. |
| **The key is pre-redirect** | built from the *intended* recipient, so `NOTIFY_REDIRECT_TO` cannot collapse a staging run onto one key. |
| **Ledger transactions** | `notification_log` commits on its **own** session — mail cannot be un-sent by a rollback. This is the inverse of `xras_activation_event`, which commits *inside* `management_transaction`. |
| **`preview()` writes no row** | a preview is not an attempt; a stray row would poison the dedup query. |
| **Templates** | `src/sam/notify/templates/`, resolved `{base}-{facility}` → `{base}-UNIV` → `{base}`. Text selects the variant and HTML follows it — never resolved independently, or a WNA recipient gets UNIV HTML. |
| **Visibility** | Admin → Configuration → Notifications (`VIEW_SYSTEM_CONFIG`, counts only) → `Details »` (`SYSTEM_ADMIN`, rows name real addresses). |
| **Approver's note** | `adminComments` from the XRAS reports feed (`src/sam/integration/xras_api/comments.py`, keyed by projcode + `actionId`), resolved by the Notify route and the `xras_notices` task and handed to `build_xras_messages(approver_comment=...)`. Fail-open: unconfigured/XRAS down/no match → `None` + one log line, never a withheld mail. Reaches the render context; the `xras_*` templates do not render it yet (template PR pending). |
| **Family addressing** | `NOTIFY_<FAMILY>_{CC,BCC,FROM,REPLY_TO}` (family = `NotificationKind.family`) is filled onto empty `Message` fields by the `Notifier`; a builder-set value wins; the transport drops cc/bcc on a redirect. The CronJob forwards every non-empty `NOTIFY_*` by prefix. `NOTIFY_BCC` is the kind-blind global. |

**Batch knobs**: `send_many(chunk_size=N)` opens one transport connection per N
*delivered* messages (`None` = one chunk, byte-identical to a plain batch) —
without it a 500-message run holds one SMTP connection throughout, and a relay
that drops it takes every remaining message. `ledger.already_sent_many(keys)` is
the batch suppression check; it shares `_suppression_conditions` with
`already_sent` so the two cannot drift, gated by
`TestTheSingleAndBatchFormsAgree`.

### The `expiration_notices` task

`src/scheduling/tasks/expiration_notices.py`, Monday 09:00 Mountain. The
first task with `needs=('sam',)`.

| | |
|---|---|
| **Window** | from `ctx.occurrence`, converted to the schedule's zone and truncated to **local midnight** — `ctx.occurrence` is naive UTC, `Allocation.end_date` is naive Mountain. |
| **Bands** | `Milestone(label, lo_days, hi_days)` in `sam/queries/expiration_notices.py`, one rung today. The label is **in the dedup key** so enabling 60/30/7 needs no key migration. `band_bounds()` builds the half-open upper edge by hand, because `get_all_expiring_allocations` filters `end_date <=`. |
| **The pre-filter is permanent** | the task drops already-notified messages itself. Left to `Notifier` the ~40 quiet weeks a year would each write hundreds of `suppressed` rows — ~26,000/yr into the table the admin card and the badge read. Deleting it as "redundant" is the regression; the comment says why it isn't. |
| **Guards** | raises if `NOTIFY_ENABLED` is false (else a chart mistake reports `succeeded` and mails nobody) and if the audience exceeds `SAM_TASKS_EMAIL_MAX` (2500). Both fire before any transport. |
| **`detail` always carries** window bounds + selected/suppressed/audience — "0 sent, succeeded" is the *normal* weekly result and must be distinguishable from a query that stopped matching. |
| **Summary mail** | one per run to `SAM_TASKS_SUMMARY_TO`, including on quiet weeks and on the cap trip (before the raise). Never fails the run. |
| **Replay** | `sam-admin tasks --run expiration_notices --force --occurrence 2026-11-23T09:00`. Honored only under `--force`, where the key is `M`-prefixed and cannot claim a real slot. |

### The `xras_notices` task

`src/scheduling/tasks/xras_notices.py`, hourly 08:00–17:00 Mon–Fri Mountain
(`BusinessHourly`). Mails the XRAS handoff notices nobody clicked. **Ships
named in `SAM_TASKS_DISABLED`** pending a soak.

| | |
|---|---|
| **Policy** | `AUTO_NOTICES` — `update`, `extend`, `supplement`, `adjust`, each with a `timedelta` delay (1 day). **Fail-closed**: a service not in that tuple is never auto-sent. `add` is out because a New is *two* writes (`active=True` **and** the notice, in that order); `transfer` has no kind at all. |
| ⚠️ **Key on `service`, never `action_type`** | a card row badged **New** *can* auto-send: `dispatch.select_service` routes a New whose projcode already exists to `update`. `action_type` is also nullable, aliased (`Adjust`/`Adjustment`), unconstrained, and includes `'Date Adjustment'`, which no service handles. |
| **Threshold** | from `ctx.occurrence` via `to_local_naive`, **not** truncated to midnight (unlike `expiration_notices`): this is a rolling threshold, not a band. `received_time` is naive-Mountain from the app clock. |
| **Window** | `[slot − LOOKBACK, slot]`, 14 days. The lower bound is a blast-radius bound — without it the first run after the switch clears mails every never-notified action in the table. The **upper** bound is determinism: a run reclaimed an hour late must select *its* slot's cohort. |
| **Guards** | `NOTIFY_ENABLED` false raises; audience past `SAM_TASKS_XRAS_MAX` (50, **not** the shared 2500) raises. Both before any transport. |
| **No summary mail** | deliberately, unlike `expiration_notices`: 50 wakes a week, most sending nothing. The ledger row and the admin card are the record. |
| **Writes** | one `XrasActivationEvent(event_type='notified', created_by='task:xras_notices')` per action that actually reached somebody — send first, record second, so the timeline never claims a handoff that did not leave. |
| **Why it cannot double-mail** | button and task both call `build_xras_messages`, so both mint `{kind}:{projcode}:{action_id}:{address}`. The ledger suppresses whichever is second. No locking needed around the card. |
| **Misfire** | the plain 6 h default. A missed slot costs nothing — the window is rolling, so the next slot subsumes it. |

⚠️ **`SAM_TASKS_DISABLED` is fail-OPEN.** Registering a task in
`src/scheduling/tasks/` puts it into production **live** on the next hourly
wake unless its name is added to `helm/values.yaml` in the *same change*. The
registry is code-side, the list is chart-side, and nothing couples them but the
reviewer — so `test_task_xras_notices.py` greps `values.yaml` for the name.
`docs/README-k8s.md` enumerates the set in three places.

⚠️ **`NOTIFY_*`/`MAIL_*` must reach the CronJob explicitly.**
`helm/templates/cronjob-tasks.yaml` renders `.Values.tasks.env` plus a
hand-listed set and does **not** inherit `.Values.webapp.env`. A missing
`NOTIFY_ENABLED` there is fail-closed: every message recorded `suppressed`,
`succeeded` reported, exit 0, green Job, no mail, no indication. Guarded twice
— the task's own `config.enabled` check and a **per-manifest**
(`-s templates/cronjob-tasks.yaml`) assertion in
`helm/tests/test-cronjob-render.sh`. A whole-render grep passes on the
Deployment's copy and proves nothing.

⚠️ **`expected_runtime` is a lease knob, not a timeout.** The lease is
`max(3×expected_runtime, 900s)` and **must exceed** the CronJob's
`activeDeadlineSeconds`, or a killed send is reclaimed while it is still
sending and every PI gets a second copy. The task cannot heartbeat
(`TaskContext` exposes no ledger handle). A drift test parses `values.yaml`
and asserts the inequality.

❌ **DON'T** add eager imports to `sam/notify/__init__.py` — `sam/__init__.py`
exports `NotificationLog`, so eager imports there put jinja2 and the
transports into every ORM consumer's import graph.
`tests/unit/test_notify_import_graph.py` is the gate.
❌ **DON'T** export `sam/queries/expiration_notices.py` **or
`sam/queries/xras_notices.py`** from `sam/queries/__init__.py` — that file
imports its submodules eagerly, so listing either would put `sam.notify.base`
into every `from sam.queries import ...`. The trap is that the near-identically
named `xras_activation.py` **is** exported, safely, because it imports no
`sam.notify`.
❌ **DON'T** import Click, Flask, `rich` or `kubernetes` anywhere under
`src/scheduling/` — a task writes to `ctx.logger`, never to stdout, because the
CronJob's stdout is a JSON envelope. The CLI routes logging to stderr as `LEVEL
name: message` (`configure_logging` in `src/cli/core/utils.py`); `kubectl logs`
merges the streams, so `cirrus_healthcheck.sh` strips that shape before
parsing. `test_task_ledger.py` AST-walks the package.

---

## Charts — `webapp/dashboards/charts/`

16 matplotlib charts, server-rendered to **inline SVG** (no PNG/base64/dpi
anywhere). Each is a `BaseChart` subclass bound to its cache by `chart_view`.

```
charts/
  __init__.py     facade: the chart_view bindings, re-exports, __all__
  base.py         BaseChart lifecycle + chart_view (the cache binder)
  theme.py        fonts, rcParams, Unity palettes, Theme, scale_bytes
  layout.py       Layout — the geometry axis
  links.py        drill targets                      [no matplotlib]
  series.py       stacked-band normalization         [no matplotlib]
  jobs_metrics.py plugin-envelope accessors          [no matplotlib]
  pie.py stacked.py histogram.py dualpanel.py pace.py    the five families
```

**Two render axes**, both composed into the cache key by `chart_view` *and* into
cached HTML by `user_aware_cache_key` — forget the latter and one layout is
served to everyone:

- **`layout`** — mobile ≤767.98px / tablet 768–1199.98px / desktop ≥1200px.
  Desktop is the identity. Tablet is desktop with a smaller figure; mobile is a
  redesign. Declared per family via `LAYOUTS = profile(...)`. Transported by
  **both** a `sam_layout` cookie and an htmx `?layout=` param, because 9 of 18
  call sites render in a full-page GET.
- **`theme`** — light / dark. Cookie only: a viewport is discovered, a theme is
  declared. Colors are baked into SVG bytes, which is why this needs a
  server-side carrier at all.

Routes read them with `read_layout()` / `read_theme()` from `utils/htmx.py`.
Date axes go through `self.apply_date_axis(ax, layout)`, never
`fig.autofmt_xdate()`. Drill-downs use one scheme, `#sam/<action>/<segments>`,
built by `links.py` — a row drill is one attribute declared at the chart, with
no JavaScript change.

Lifecycle, the family hierarchy, how to add a chart, the layout/theme design
and all the measurements: `docs/plans/implemented/CHART_ARCHITECTURE.md`,
`DARK_MODE.md`, `MOBILE_CHARTS.md`, `TABLET_CHARTS.md`, and the
`webapp/dashboards/charts/__init__.py` and `base.py` docstrings.

### Gotchas

❌ **DON'T** let `BaseChart` swallow exceptions — callers own that, and
   inconsistently (`disk_scans/routes.py` wraps its call, `jobs/routes.py`
   does not).
❌ **DON'T** rely on a base `is_empty` default: several charts hold ndarrays,
   where `not self.data` raises *"truth value of an array is ambiguous"*.
   Define it per family.
❌ **DON'T** import matplotlib into `links.py` / `series.py` /
   `jobs_metrics.py` — enforced by `test_chart_module_boundaries.py`.
❌ **DON'T** reorder the `chart_view` calls: that order is the admin Caching
   card's row order, and cache names are Redis key prefixes.
❌ **DON'T** put a mobile or tablet override in `profile()`'s bare keywords —
   those are desktop. Use `mobile={...}` / `tablet={...}`, which validate
   against `Layout`'s fields.
❌ **DON'T** accept a `layout` argument in a fragment renderer and then call a
   delegate without it — the fragment still renders, at desktop, forever.
   `test_renderers_forward_the_layout_they_are_given` is the gate.
❌ **DON'T** cap a legend without passing `ordered=True` to `link_legend` — it
   zips bands against patches by position, and a capped legend is no longer
   `reversed(bands)`. The fingerprint proves href *strings*, not the artists
   carrying them, so a misaligned drill looks green.
❌ **DON'T** put a theme-dependent color in `ax.text()` without setting it
   explicitly — `apply_chrome` skips `ax.texts` by design, so it will render in
   whatever the light rcParams baked in.
✅ **DO** regenerate the fingerprint snapshot in the *same commit* as an
   intentional visual change:
   `CHART_FINGERPRINT_REGEN=1 pytest tests/unit/test_chart_fingerprints.py`.
   A fingerprint delta in a commit that didn't declare one is a bug. **A
   desktop-light delta in a mobile- or dark-tuning commit always is.**
✅ **DO** run `sam-admin cache --refresh --category chart` after deploying a
   visible change — keys hash *input data*, not rendering code, so warm Redis
   entries serve old-code SVGs for up to 600 s.

Design + rationale: `docs/plans/implemented/CHART_ARCHITECTURE.md` (the hierarchy),
`docs/plans/implemented/DARK_MODE.md` (the theme axis),
`docs/plans/implemented/MOBILE_CHARTS.md` + `docs/plans/implemented/TABLET_CHARTS.md` (the layout
axis).

---

## Development Workflow

### Running the Web Application
```bash
docker compose up webdev --watch    # dev server, code-synced → http://localhost:5050
docker compose up                   # prod-like image → http://localhost:7050
```
Both show the stub login page with Quick Login buttons (stub auth accepts any
password; `DISABLE_AUTH=0` is pinned for `webapp`). True auto-login is opt-in —
see docs/AUTHENTICATION.md § Local development.

### Adding New ORM Models
1. Create the model in the matching domain module; add `SessionMixin` if it
   needs write methods; add `update()` / `create()` per §7.
2. Add to `sam/__init__.py` imports (this also auto-registers a Flask-Admin view).
3. Add tests; run schema validation
   (`pytest tests/integration/test_schema_validation.py`), then the full suite.

### Fixing Schema Mismatches
Database is the source of truth: check `SHOW CREATE TABLE x\G`, update the ORM
to match, rerun schema-validation tests (they catch future drift).

### Adding a Validated POST/PUT Route (HTMX or API)
1. Find or add the form schema in `src/sam/schemas/forms/` (export it).
2. Pick the handler tier per §9: `handle_htmx_form_post` → `CrudSpec` →
   `HtmxFormHandler` subclass.
3. FK existence checks in the route/handler `clean()`; PUT updates gated on
   original `request.form` keys.

### Skipping CI for trivial changes

Two *different* mechanisms answer to the same tokens — know which one you are
firing, because they differ in blast radius:

| Where the token appears | What skips |
|---|---|
| **PR title** | Only the workflows carrying the repo's `if:` guard — `sam-ci-docker`, `sam-ci-conda_make`, `test-install`, `ci-staging`, `browser-smoke`, `mega-linter`. `build-images-cirrus-deploy` / `deploy-staging` still run, so the deploy-path TruffleHog scan is unconditional. |
| **Commit message** | **Everything.** GitHub natively suppresses all `push` and `pull_request` workflow runs — including the deploy workflows, whose `if:` guards never even get evaluated. |

⚠️ **Never write the bare tokens in prose.** GitHub scans the *entire* commit
message for `[skip ci]`, `[ci skip]`, `[no ci]`, `[skip actions]` and
`[actions skip]` — inside backticks, inside a markdown table, inside a quoted
commit message, anywhere. When a token is found **no workflow run and no
Actions check suite are created at all**, which is indistinguishable from a
GitHub outage. `workflow_dispatch` still works, and that asymmetry is the tell.

This bites hardest on **squash merges**, because GitHub builds the squash
message from the PR title *and body*. It has cost this repo twice: #406 (which
quoted image-pin commits that legitimately used a token) and #408 (whose body
described this very convention). In both cases the resulting staging commit
shipped with no CI, and then suppressed CI on the open `staging -> main`
promotion PR whose head it became — and #408's also silently skipped
`deploy-staging`, so staging was never deployed.

When you need to *write about* the convention, break the string — `skip-ci
tokens`, or `[skip&nbsp;ci]`. To recover a branch already carrying one, land an
empty commit with a clean message; reopening the PR does not help, because the
head commit message is unchanged.

---

## Git Workflow

- PRs default to `--base staging`; staging → main promotion is a manual 2nd PR.
- Detailed commit messages with markdown: "## Summary", "### Test Results" when
  relevant, ending with:
```
🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
```

---

## Key Contacts & Context

- **User**: Ben Kirk (benkirk@ucar.edu)
- **Organization**: CISL USS (University Services Section)
- **Project**: SCSG0001 (CSG systems project)
- **Facilities**: UNIV (university), WNA (Wyoming-NCAR Alliance)
- **Resources**: Derecho, Casper, Gust (HPC); Stratus, Campaign Store (disk)

---

## Common Pitfalls to Avoid

❌ **DON'T** use `datetime.now(UTC)` — database uses naive datetimes
❌ **DON'T** use raw SQL strings in `session.execute()` — wrap with `text()`
❌ **DON'T** assume single-column primary keys — check the database first
❌ **DON'T** modify the SAM database schema — ORM follows database
❌ **DON'T** skip schema-validation tests after model changes
❌ **DON'T** create files unnecessarily — prefer editing existing files
❌ **DON'T** use `user.email` — use `user.primary_email` (no `email` attribute)
❌ **DON'T** use raw column comparisons (`Model.active == True`, raw date checks) — use `Model.is_active` (§5)
❌ **DON'T** pass `session` to `project.get_detailed_allocation_usage()` — SessionMixin provides it
❌ **DON'T** forget the 4-tuple unpack from the expiration queries
❌ **DON'T** add standalone `update_*(session, id, ...)` functions to `sam/manage/` — methods on the model (§7)
❌ **DON'T** write a local `_user_can_access_project` in a route file — use `webapp.api.access_control` decorators (§8)
❌ **DON'T** hand-roll form POST bodies: no inline `strptime`/`float()`/`int()` ladders, no manual empty-string dropping (`_strip_empty_strings` does it), no bespoke try/except-ValidationError flows when `handle_htmx_form_post` / `CrudSpec` / `HtmxFormHandler` fits (§9)
❌ **DON'T** hardcode a `/static/...` path in a template — use
`url_for('static', filename=...)` so the asset gets its `?v=` cache tag (§11);
and never append `?`/`#` to a `url_for('static')` result, which already carries one
❌ **DON'T** touch the legacy-compat API blueprints beyond additive changes
❌ **DON'T** hardcode integer PKs from lookup tables in app constants — pair rules with names, resolve IDs at runtime

✅ **DO** run schema-validation tests before committing model changes
✅ **DO** check the actual database schema when in doubt
✅ **DO** use bidirectional relationships with `back_populates`
✅ **DO** use proper exit codes (0, 1, 2, 130)
✅ **DO** use `Model.is_active` for any active check (§5)
✅ **DO** add `SessionMixin` to any ORM model that needs an `update()` method
✅ **DO** use the §8 access decorators on all project-scoped routes (view receives the object)
✅ **DO** load POST input via a `sam.schemas.forms` schema and pick the right §9 handler tier
✅ **DO** gate PUT update dicts on keys present in the original `request.form`, not the loaded output
