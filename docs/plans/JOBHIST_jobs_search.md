# Wire `JobQueries.jobs_search()` into the Flask webapp

## Context

PR #60 on `hpc-usage-queries` (branch `jobhist_search`, checked out at
`/Users/benkirk/codes/hpc-usage-queries/devel`) adds
`job_history.queries.JobQueries.jobs_search(...)` — a session-scoped API
that returns **individual job rows** (one dict per PBS job) filtered by
date range, user, account, queue, and status.

The goal: surface per-job rows on the existing Flask resource-usage
detail pages (Derecho / Casper / Derecho GPU / Casper GPU) under the
user dashboard, alongside today's rolled-up daily display
(`webapp/dashboards/user/blueprint.py:347`). HTML formatting is
deferred — this plan covers the **wiring**.

The hard part is that hpc-usage-queries owns its own database
(per-machine PostgreSQL in production: `derecho_jobs`, `casper_jobs`,
…) — a separate connection from SAM's MySQL. The existing CLI
caller in `src/cli/accounting/commands.py:314` creates a one-shot
session per command, which is fine for CLI but wrong for a long-lived
Flask process under request load. We need a pooled, app-scoped
integration.

## Key findings from exploration

- **`JobQueries(session, machine='derecho').jobs_search(...)`** —
  caller owns the session lifecycle; no commit/close inside. Filters
  compose via AND. Returns
  `List[Dict[str, Any]]` keyed by the columns in
  `job_history.cli.search.columns.DEFAULT_COLUMNS` (job_id, user,
  account, queue, numnodes, numcpus, numgpus, start, end, elapsed,
  cpu_hours, gpu_hours) or a caller-supplied `columns` list (full set
  in `VERBOSE_COLUMNS`, ~26 fields). Datetimes are ISO 8601 strings.
- **Docstring drift to flag back upstream**: PR's "same shape
  contract as daily_summary_report" is misleading — only `user`,
  `account`, `queue`, `cpu_hours`, `gpu_hours` overlap; everything
  else differs. Worth tightening the docstring in the PR.
- **Existing plugin pattern works**:
  `src/cli/accounting/commands.py:314-335` already loads
  `job_history` via `require_plugin(HPC_USAGE_QUERIES)` and uses
  `mod.get_session(machine)` + `mod.JobQueries`. The webapp can reuse
  the same plugin loader.
- **`get_engine(machine)` does NOT memoize** —
  `hpc-usage-queries/devel/job_history/database/session.py:149-184`
  builds a fresh `create_engine(...)` every call. Acceptable for CLI;
  destructive in Flask.
- **Flask DB layer already supports multi-bind**:
  `webapp/extensions.py:12` (Flask-SQLAlchemy), `SQLALCHEMY_BINDS` is
  used for the SQLite `system_status` DB. But job_history models
  aren't part of SAM's `db.Model` registry, so we won't use
  `SQLALCHEMY_BINDS` — we'll keep job_history's engine separate (this
  is cleaner; the two ORMs stay independent).
- **Drill-down landing site**:
  `webapp/dashboards/user/blueprint.py:347` (`/resource-details`)
  already drills date → user+queue via Bootstrap collapse, no HTMX.
  Per-job rows are the natural 4th level. HTMX pagination pattern
  exists at `webapp/dashboards/allocations/blueprint.py:732-794`.
- **Auth posture**: keep `@login_required` (matches existing page —
  confirmed in conversation).
- **Backend**: PostgreSQL in production (confirmed) → real pooling
  matters; we should pass `pool_size`, `max_overflow`,
  `pool_pre_ping`, `pool_recycle` into the engine.

## Recommended approach

**Cache engines upstream in hpc-usage-queries; create sessions
per-request in Flask.** This is the standard SQLAlchemy pattern (one
engine, many sessions) and unblocks any future web consumer, not just
SAM.

### Part A — small upstream change in hpc-usage-queries

**File**: `hpc-usage-queries/devel/job_history/database/session.py`

- Memoize `get_engine(machine)` per `(machine, backend, options)` key
  so repeated calls return the same `Engine` instance.
- Accept optional `pool_kwargs: dict | None = None` on `get_engine`
  and `get_session`, forwarded to `create_engine()` only when backend
  is `postgres` (SQLite ignores them). Examples: `pool_size`,
  `max_overflow`, `pool_pre_ping`, `pool_recycle`.
- Add a `dispose_engines()` helper that clears the cache (useful for
  tests and graceful shutdown).
- Keep current CLI call sites working — `pool_kwargs=None` preserves
  today's behavior, just memoized.

This is a non-breaking change. Open it as a follow-up PR on top of
#60 (or fold into #60 since #60 is on the same branch and not yet
merged).

### Part B — Flask integration in SAM

**New module**: `src/webapp/jobs/`

```
src/webapp/jobs/
├── __init__.py          # blueprint registration helper
├── session.py           # engine init at startup + per-request session factory
├── service.py           # thin wrapper around JobQueries.jobs_search
└── routes.py            # HTMX fragment route for per-job table
```

**`session.py`** — at `create_app()` time:
1. Call `require_plugin(HPC_USAGE_QUERIES)` once; stash module on
   `app.extensions['hpc_usage_queries']`. If the plugin is missing,
   log a warning and disable the per-job feature gracefully (don't
   crash the webapp — sam-admin already treats it as optional).
2. For each configured machine (derecho, casper, …), eagerly call
   `mod.get_engine(machine, pool_kwargs={...})` so connections are
   established at startup, not on first request.
3. Build per-machine `sessionmaker`s, stash on `app.extensions`.
4. Provide a `job_history_session(machine)` context manager (yields a
   `Session`, closes on exit) and a Flask teardown handler that
   closes any sessions opened in `flask.g`.

Pool kwargs come from new env vars (added to `.env.example`):

- `JOB_HISTORY_DB_BACKEND=postgres`
- `JOB_HISTORY_PG_HOST`, `JOB_HISTORY_PG_PORT`,
  `JOB_HISTORY_PG_USER`, `JOB_HISTORY_PG_PASSWORD`,
  `JOB_HISTORY_PG_REQUIRE_SSL` (already exist in hpc-usage-queries)
- `JOB_HISTORY_PG_<MACHINE>_DB` per machine (already exists)
- `JOB_HISTORY_POOL_SIZE=5` (new, Flask-side; default conservative)
- `JOB_HISTORY_POOL_MAX_OVERFLOW=10` (new)
- `JOB_HISTORY_POOL_RECYCLE=3600` (new)

**`service.py`** — single function the route layer calls:

```python
def search_jobs(machine, *, start, end, user=None, account=None,
                queue=None, status=None, columns=None, limit=None):
    JobQueries = current_app.extensions['hpc_usage_queries'].JobQueries
    with job_history_session(machine) as s:
        return JobQueries(s, machine=machine).jobs_search(
            start=start, end=end, user=user, account=account,
            queue=queue, status=status, columns=columns, limit=limit,
        )
```

Keep this layer dumb. No transformation, no pagination math, no
authz — those belong in the route.

**`routes.py`** — HTMX fragment, mirrors
`allocations/blueprint.py:732` pagination pattern. Route lives under
the user dashboard blueprint (or its own bp registered under
`/dashboards/user/jobs`). Decorators: `@login_required` (matches
existing page). The route enforces that filters come from the
drill-down context the user already had access to — pass
`projcode` + `date` + `user` + `queue` + `machine` as query params,
look up the account via the SAM session, and only return job rows for
that account. **Do not** allow arbitrary user/account filters from
query string; derive them from the project-scoped context the page
already opened.

**Wire-in point**: extend
`webapp/dashboards/user/blueprint.py:347` (`/resource-details`)
template to add a 4th collapsible level under date+user+queue that
issues `hx-get` to the new fragment route. The template change is
out of scope for this plan (deferred per user).

### Part C — config + tests

- Add env vars to `.env.example` and to the Flask `Config` class
  (likely `src/webapp/config.py` or wherever `SQLALCHEMY_*` lives —
  check during implementation).
- New tests in `tests/api/` or `tests/unit/test_webapp_jobs.py`:
  - Mock `JobQueries.jobs_search` and assert the service forwards
    args correctly.
  - Smoke test the route returns 200 + an HTMX fragment for a
    project the test user is on, 403 otherwise.
  - Test plugin-missing path: webapp boots, feature gracefully off.

## Critical files to read / modify

- `src/sam/plugins.py:82` — `HPC_USAGE_QUERIES` descriptor (no
  change, just reuse)
- `src/cli/core/base.py:30` — `require_plugin()` (reuse)
- `src/cli/accounting/commands.py:314` — reference pattern for
  current plugin usage (no change)
- `src/webapp/extensions.py:12` — Flask-SQLAlchemy init (no
  change; job_history stays separate)
- `src/webapp/__init__.py` / `run.py:80-100` — `create_app()` and
  pool config; add startup hook here
- `src/webapp/dashboards/user/blueprint.py:347` —
  `/resource-details` route (template wire-in deferred)
- **New**: `src/webapp/jobs/{__init__,session,service,routes}.py`
- **Upstream**:
  `hpc-usage-queries/devel/job_history/database/session.py:149-201`
  — engine memoization + pool_kwargs (separate PR)
- `.env.example` — new env vars

## Verification

1. **Upstream change**: in `hpc-usage-queries`, run `pytest` for
   `job_history`; confirm `get_engine(machine)` returns the same
   instance twice and that `dispose_engines()` clears the cache.
2. **SAM webapp**: `docker compose up webdev --watch` (per
   `feedback_webapp_testing.md`). On startup, logs should show
   "hpc-usage-queries plugin loaded; engines initialized for [derecho,
   casper]" (or a clean warning if plugin missing).
3. **Manual route test**: visit `/dashboards/user/resource-details`
   as `bdobbins` (per `project_profiling_target_user.md`) →
   issue the HTMX fragment URL directly with curl/devtools to confirm
   per-job rows return for one of bdobbins's projects, 403 for a
   project he's not on.
4. **Pool behavior**: hit the fragment route N times in quick
   succession; confirm via Postgres `pg_stat_activity` (or
   SQLAlchemy logging) that the connection count stays bounded by
   `pool_size + max_overflow`, not N.
5. **Tests**: `source etc/config_env.sh && pytest tests/api/
   tests/unit/test_webapp_jobs.py` (user runs by hand per
   `feedback_testing.md`).

## Deferred / out of scope

- HTML/HTMX template changes for the per-job table (user deferred).
- Pagination policy (per-page default, max limit) — pick during UI
  pass.
- Cross-machine queries from a single page (Derecho + Casper rows
  side by side). Today each machine has its own DB; a unified view
  means issuing N queries and merging client-side or in service.py.
- Caching of `jobs_search` results (Redis / Flask-Caching) — not
  needed at v1 with bounded result sets and pool reuse.
- Tightening the upstream docstring's "same shape contract as
  daily_summary_report" claim — flag in the PR review.
- Whether to add `@require_project_access` to the parent
  `/resource-details` route. Pre-existing gap; not introduced by
  this work.
