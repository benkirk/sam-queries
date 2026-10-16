# SAM Queries - Project Memory

This file loads in every session. Topic detail lives in `.claude/rules/*.md`,
each loaded when Claude reads or edits a file its `paths:` matches (index
below). Section numbers (§1-§12) are stable across both: §1, §5, §7, §8 and §12
are here, §2-§4 and §6 in `orm.md`, §9-§11 in `webapp-ui.md`.

---

## Project Overview

**SAM (System for Allocation Management)** - Python ORM and query tools for NCAR's
resource allocation and accounting database. Used by CISL to manage HPC allocations,
user accounts, project tracking, and charging for Derecho, Casper, and other
computational resources.

**Tech Stack**: SQLAlchemy 2.0, MySQL/MariaDB, Python 3.13, Flask + htmx
(vendored Bootstrap 5.3.8), marshmallow, pytest

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

**hpc-usage-queries** (`/Users/benkirk/codes/hpc-usage-queries/devel`) ships the
`jobhist` CLI, loaded as an optional plugin (`require_plugin(HPC_USAGE_QUERIES)`).
The two CLIs share the `Context`/`BaseCommand` shape, the `EXIT_*` codes and the
JSON envelope **deliberately**: change either here and update both repos in
lockstep. Detail: `.claude/rules/cli.md`.

---

## Code Organization

```
sam-queries/
├── .claude/rules/        # Topic rules, loaded by path (index below)
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
│   ├── export/              # Write-only xlsx workbook builder (Flask-free)
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
│   ├── api/v1/              # REST blueprints (+ legacy-compat, see `webapp-api.md`)
│   ├── dashboards/          # user/, admin/, allocations/, status/ + project_members
│   │   └── admin/crud.py    # CrudSpec + register_crud (CRUD route generator)
│   ├── db_browser/          # /database read-only row browser (query half: src/dbbrowse/)
│   ├── auth/ audit/ caching/ disk_scans/ jobs/ limiter/ utils/
│   │   └── utils/form_handler.py   # HtmxFormHandler lifecycle class
│   └── templates/ static/   # Jinja2 + vendored assets
├── collectors/           # PBS/JupyterHub status collectors (own README)
├── helm/                 # k8s chart (see helm/README.md, docs/README-k8s.md)
├── migrations/           # Alembic (system_status DB only)
├── compose.yaml          # Docker Compose (samuel:7050, samuel-dev:5050, DBs, cache)
└── tests/                # See docs/TESTING.md for suite size/timings
    ├── unit/<domain>/       # per-domain dirs (gates xras notify tasks charts
    │                        #   cli webapp models queries manage); the matching
    │                        #   marker is auto-applied from the directory (C4)
    ├── integration/ api/ perf/   # perf/ gated behind -m perf; api/ has xras_audit_rows/
    └── factories/           # Layer-2 builders (core, resources, projects,
                             #   operational, security, summaries, _seq)
```

---

## Working with Ben

- After research, before presenting questions, give a layman's summary of the
  findings so far that give rise to the questions.

---

## Topic rules (`.claude/rules/`)

A rule loads only when Claude reads, writes or edits a file matching its
`paths:`; a Grep or Glob hit does not load it, and compaction drops it until the
next touch. **Before planning or delegating work in one of these areas, Read the
rule file directly, and name it in any subagent prompt.**

| Rule | Loads with | Holds |
|---|---|---|
| `orm.md` | `src/sam/**/*.py`, factories, model and integration tests | Key ORM models, §2-§4, §6, common ORM patterns, adding models |
| `webapp-api.md` | `src/webapp/api/`, `src/webapp/auth/`, `src/sam/schemas/`, `src/sam/security/`, `tests/api/` | Serialization schemas, API endpoints, legacy-compat and output shaping, RBAC models |
| `webapp-ui.md` | dashboards, templates, static, `webapp/utils/`, `/register`, DB browser, form schemas | §9 forms and htmx handlers, §10 active-only toggles, §11 static assets, DB browser |
| `notify-and-tasks.md` | `src/sam/notify/`, `src/scheduling/`, `helm/values.yaml`, the task CronJob, notify and task tests | Notification framework, the scheduled tasks, helm wiring traps |
| `charts.md` | the chart package, `utils/charts.py`, `charts.css`, `chart_bits.html`, chart tests | Render axes, gotchas, fingerprint regen |
| `account-requests.md` | the account-request model and manage layer, `/register`, event lifecycle | Account requests, events, invitations, the NUSD handoff |
| `cli.md` | `src/cli/`, CLI tests | sam-search and sam-admin, the jobhist lockstep |
| `directory.md` | `src/sam/integration/directory/`, `sam/queries/directory.py`, `sam/manage/directory.py`, directory tests | The LDAP directory client: reader contract, per-kind levers, the fail-open sync hook, the Mountain-offset date rule |
| `formatting.md` | `sam/fmt.py`, `src/cli/`, templates, dashboard Python | `sam.fmt` and its Jinja filters |
| `testing.md` | `tests/`, `pytest.ini` | Markers, the two test-data tiers, the `system_status` tier, route-map parity |

**Tripwires** (one line each because their rule may not be loaded):
- ⚠️ Mail is **fail-closed**: `NOTIFY_ENABLED` defaults false everywhere but
  `helm/values.yaml`. Never default it on; dev runs a prod snapshot with a live relay.
- ⚠️ `SAM_TASKS_DISABLED` is **fail-OPEN**: a task registered in
  `src/scheduling/tasks/` runs in production on the next wake unless
  `helm/values.yaml` names it in the same change.
- ⚠️ Legacy-compat blueprints (`directory_access.py`, `project_access.py`, `fstree_access.py`,
  `queue.py`, `wallclock_exemption.py`): additive changes only; response bytes must not change.
- ⚠️ `sam/queries/__init__.py` stays empty and `sam/notify/__init__.py` stays
  lazy: import-graph gates (`test_layer_imports.py`, `test_notify_import_graph.py`).
- ⚠️ Charts: an intentional visual change regenerates the fingerprint snapshot in the same commit (`CHART_FINGERPRINT_REGEN=1`).
- ⚠️ Directory client: the ldapsync hook is fail-open after commit; `DIRECTORY_APPLY_<KIND>` is per kind, absent is off; a `-0600`/`-0700` LDAP stamp is Mountain wall time as written.

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

**Exceptions**: `sam/queries/statistics.py` keeps `User.active == True` so `active_users` and `locked_users`
stay separate counters; the identity sync (`sam/manage/{ldapsync,lifecycle,purge}.py`, `sam/queries/ldapsync.py`)
and HEUV's `role_logins` read `active` raw because legacy's rules do, and `is_active` would fold in `locked`.

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

**Caller pattern**: load object first (caller handles not-found), then call the method. Exception: `User.apply_sync(**fields)` writes `None` (the sync sends every column), hence its own name.

**What stays in `sam.manage`**: complex multi-entity ops (`add_user_to_project`),
audit-trail-heavy ops (`update_allocation` + transaction logging), summary
upserts, and the `management_transaction` context manager. **Every web write
runs inside `management_transaction(db.session)`** — it drives the implicit
audit logging; the form-handling helpers (§9, `webapp-ui.md`) enforce this by construction.
Allocation invariant: every `allocation_transaction` write must keep
replay(history) == amount.
Membership invariant: the lead and admin hold a live row on every live account
(`Project.live_accounts`: non-deleted, on an active resource; `account.deleted` is
never set in practice); `Project.update` seeds a new one (`ensure_members`).
`Project.users` is lead + admin + row holders; `account_linked_users` is rows only.
Reconcile (`reconcile_project_access`, CLI and web) skips users who are not `User.is_active`.
Removal is a soft delete: started rows get `end_date` = `membership_cutoff(now)` (now floored − 1 s; a midnight result steps back to 23:59:59), never-started rows are deleted, ended rows are history; `change_project_admin` needs an unended row.

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
@login_or_token_required()          # GET: key or session; a write keeps @login_required
@require_project_member_access(Permission.VIEW_ALLOCATIONS)
def get_project_allocations(project):   # ← project object, not projcode
    ...
```

Note: the Basic-Auth path checks the Permission only under `RBAC_SOURCE=db` (`api_key_allowed`);
key grants are never facility-scoped, and the project decorators admit a key by permission alone.

### Allocation usage is one computation

Kernel: `sam/accounting/calculator.py`
— `usage_anchor` (one anchor per (project, account); `Project.sums_as_subtree()`
picks the subtree path) and `batch_charges` / `anchored_charges` (one statement
per charge table, routed by `activity_type`, plus `charge_adjustment`). The one
assembly is `build_user_projects_resources_batched` (`sam/queries/dashboard.py`),
which also projects the read model; `Project.get_detailed_allocation_usage()`
reshapes its rows for one project; `AllocationWithUsageSchema`, `sam/queries/allocations.py`,
the rolling windows and fstree call the kernel. Never write another sum over the
summary tables — add an anchor.

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

Enforced by `tests/unit/gates/test_docs.py` (spelling, markdown links, cited paths,
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

## Testing

Suite size, timings, and tier breakdown live in **`docs/TESTING.md`** — keep
counts there, not here. `perf` and `stress` are gated off by default.

```bash
# One-time setup: isolated test container + URL
docker compose --profile test up -d mysql-test
export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'

source etc/config_env.sh && pytest          # parallel (-n auto from pytest.ini)
pytest tests/unit/queries/test_query_functions.py -v
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
  and a fixture can forget. Gates: `tests/unit/models/test_outbound_guards.py`.
  Tests drive the configured path with fakes, patching the transport on the
  *instance* (which shadows the guard) or replacing `from_environment`.
- **Isolation**: per-test SAVEPOINT rollback
  (`join_transaction_mode="create_savepoint"`) — xdist workers share one DB
  safely. **Safety guard**: `tests/conftest.py` refuses any database other
  than the allowlisted mysql-test container (host port 3307).

More mechanics (markers, test-data tiers, the `system_status` tier, route-map
parity): `.claude/rules/testing.md`.

---

## Development Workflow

### Running the Web Application
```bash
docker compose up samuel-dev --watch    # dev server, code-synced → http://localhost:5050
docker compose up                   # prod-like image → http://localhost:7050
```
Both show the stub login page with Quick Login buttons (stub auth accepts any
password; `DISABLE_AUTH=0` is pinned for `webapp`). True auto-login is opt-in —
see docs/AUTHENTICATION.md § Local development.

**samuel-dev** (`https://samuel-dev.k8s.ucar.edu`): the prod chart on nwc1 with
`helm/values-dev.yaml` — Postgres `sam_dev`, own `system_status_dev`, mail and
XRAS levers off. Argo app `sam-query-dev` deploys it into `sam-queries-dev` from
the `cirrus-dev` pin (every staging push, or `gh workflow run "Publish Images and
CIRRUS Deploy" --ref <branch>`); `make refresh-dev` reloads its data; every
`scripts/cirrus_*.sh` takes `--env dev` (`watch-dev` skill). Limiter tiers are off
on dev except login. Record: `docs/plans/implemented/K8S_DEV_ENVIRONMENT.md`; gate: `helm/tests/test-dev-render.sh`.

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

- PRs default to `--base staging`; `open-staging-promotion.yaml` opens the promotion PR.
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
- **Facilities / resources**: UNIV (university), WNA (Wyoming-NCAR Alliance); Derecho, Casper, Gust (HPC); Stratus, Campaign Store (disk)

---

## Common Pitfalls to Avoid

❌ **DON'T** use `datetime.now(UTC)` — database uses naive datetimes
❌ **DON'T** use raw SQL strings in `session.execute()` — wrap with `text()`
❌ **DON'T** modify the SAM database schema — ORM follows database
❌ **DON'T** create files unnecessarily — prefer editing existing files
❌ **DON'T** use raw column comparisons (`Model.active == True`, raw date checks) — use `Model.is_active` (§5)
❌ **DON'T** add standalone `update_*(session, id, ...)` functions to `sam/manage/` — methods on the model (§7)
❌ **DON'T** write a local `_user_can_access_project` in a route file — use `webapp.api.access_control` decorators (§8)
❌ **DON'T** hardcode integer PKs from lookup tables in app constants — pair rules with names, resolve IDs at runtime
❌ **DON'T** add a secondary bind to `/ready`'s required set (`_REQUIRED_BINDS` in
`webapp/api/v1/health.py`) — a shared external DB fails every replica at once and
empties the Service (2026-09-13; `docs/plans/implemented/CNPG_ROLL_RESILIENCE.md`)

✅ **DO** use `Model.is_active` for any active check (§5)
✅ **DO** add `SessionMixin` to any ORM model that needs an `update()` method
✅ **DO** use the §8 access decorators on all project-scoped routes (view receives the object)
