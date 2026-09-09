# Allocation/usage read-model — design & implementation handoff

Status: **design approved 2026-09-08, refined 2026-09-09 against the code, being
built** on branch `allocation_read_model` (PR #534, base `staging`) as ordered
commits. This doc captures the grain, placement, refresh strategy, and the phased
rollout, with the file references a builder needs. Companion:
`docs/plans/FSTREE_LATENCY_INVESTIGATION.md` (the DB-bound finding that motivates
this).

## Problem

Three prod-slow paths share one cause, confirmed DB-bound by the #531 per-request
`db=`/`cpu=` split: **fstree** (`GET /api/v1/fstree_access/<resource>`, ~3.3s DB
cold), the **full allocations table** (`/allocations/projects`), and the **project
deep-dive** (`/admin/htmx/project-allocation-tree/<projcode>`, ~5.7s / 91% DB). The
cost is **not** the charge summaries (already cheap per-account/day aggregates) —
it is the **request-time work on top of them**, recomputed on every cold cache
miss:

1. the project-MPTT subtree charge rollup (`Project.batch_get_subtree_charges`),
2. the disk current-snapshot fan-out (`bulk_get_subtree_disk_capacity`),
3. the subtree adjustments join (`charge_adjustment`),
4. the inheriting-allocation second pass (shared-pool root subtree).

Short-term decision: **accept-as-is** (it holds ~4s cold, shared cache, no 5xx).
This is the durable lever.

## The idea

One denormalized **read-model** table holding the current rolled-up state of every
allocation the dashboards would show, built by an **hourly** authoritative pass,
that the slow paths **consult if present and fresh, else fall back to the live
computation** — the slow paths are never removed, only short-circuited. The hourly
feeder runs the *same* batched computation the live path uses, so the table
**cannot drift** from the live answer.

## Grain — one row per allocation the dashboards would show

All consumers bottom out at the `account` leaf; they differ only in consumer-side
passes (tree reconstruction, TOTAL aggregation, fstree's status cascade, phantom
lifecycle rows). The table stays **flat at leaf grain**, keyed by `allocation_id`.

**Why `allocation_id`, not `account_id`.** The local snapshot has 4,280 active
allocations and zero accounts with two active allocations, but the schema does not
prevent a second one, and the dashboards also show an allocation that ended within
the last 90 days (`Project.get_detailed_allocation_usage`, `projects.py:791-815`).
So a row is one allocation that is **active at run time or ended ≤ 90 days ago**,
with `is_current` marking the active ones (fstree filters on it); `account_id` is
an ordinary index.

**Columns (slim on purpose).** Only what is expensive to compute stays in the row;
every consumer already runs a cheap skeleton query that yields the rest.

- Keys: `allocation_id` (PK), `account_id`, `project_id`, `projcode`,
  `resource_id`, `resource_name`, `resource_type`, `facility_name`,
  `allocation_type`.
- Hierarchy: `parent_allocation_id`, `is_inheriting`, `root_projcode`.
- Values: `allocated`; `self_used` (own subtree, incl. adjustments), `used`
  (pool subtree for an inheriting allocation, else `self_used`), `remaining`,
  `percent_used`, `self_percent_used`, `charges_by_type` {comp,dav,disk,archive}
  (JSON), `adjustments`; disk point-in-time `current_used_bytes` +
  `activity_date`; `rolling_windows` (JSON `{30: {...}, 90: {...}}`, NULL unless the
  account carries a threshold — mirrors both consumers' gate).
- Lifecycle: `start_date`, `end_date`, `is_current`.
- Watermark: `refreshed_at` (freshness gate + drift audit).

**Dropped from the row, deliberately**: fair-share percentages, cutoff/first/second
thresholds, project/account active flags, MPTT coordinates. fstree's skeleton query
already selects all of them; the dashboards hold the ORM objects; the allocations
table gets them from `get_allocation_summary` (2 queries). Denormalizing them would
add staleness with no saving.

**Roster stays a live join, NOT in this table.** fstree's `users[]` is per account
but cheap (a join on `account_user`) and not the bottleneck; keeping it live also
means **membership changes never dirty the read-model**.

**Divergences are consumer-side passes over this one grain**, documented so nobody
forces them into the schema: tree reconstruction, `accountStatus` parent→child
cascade (`fstree_access.py`), TOTAL roll-ups (`_aggregate_usage_to_total`), and
fstree's zero-valued "Expired"/"No Account" phantom rows (its lifecycle query).

## Projection source — the dashboards' batched builder

`_build_user_projects_resources_batched(session, projects)`
(`src/sam/queries/dashboard.py`) already yields one `DashboardResource` per
(project, account) with every value column above. It handles the leaf/subtree
split, the inheriting root pass, the disk snapshot override and the
threshold-gated rolling windows, and it is equivalence-tested against the
per-project path (`test_user_dashboard_batched_matches_per_project`). The feeder
calls it over every candidate project and writes rows; the other readers
(`get_allocation_summary_with_usage`, fstree) look rows up by `allocation_id` /
`account_id`. One projection, three readers.

## Coverage (breadth)

A `make perf` census confirms this grain short-circuits the whole allocation/usage
core, not just the three motivating endpoints:

- **Covered** (all funnel through `get_detailed_allocation_usage` /
  `batch_get_subtree_charges` / `get_allocation_summary_with_usage`): user dashboard
  (`get_user_dashboard_data`), project + projects dashboard
  (`get_project_dashboard_data` / `get_projects_dashboard_data` → admin project
  card, admin expirations), `get_allocation_summary_with_usage` (the ~52k-query
  cache hotspot, biggest win), `/allocations/projects` + its fragment, pace chart,
  usage modal and xlsx export (all via `cached_allocation_usage`), fstree, the
  project deep-dive, and the rolling windows for threshold accounts.
- **A third seam, separate from `usage_cache`**:
  `/api/v1/projects/<code>/allocations` and `/charges/summary` dump
  `AllocationWithUsageSchema`, which recomputes per account, uncached
  (`src/sam/schemas/allocation.py`). It is short-circuited by handing the schema a
  precomputed `usage` in its context.
- **Deferred to companion tables (NOT a wider row, NOT part of this work):**
  per-day, per-user, per-user×queue, per-month time series — the
  `sam/queries/charges.py` drilldowns behind the resource-details pages and the
  `/charges` API. If wanted later, add companion rollup tables at (account × day)
  and (account × user[× queue]) — a rollup of the existing daily summaries, not a
  raw mirror. Not perf-gated today, so lower priority. Raw-row `/charges` stays
  live.
- **Out of scope (not allocation/usage):** contract graph
  (`/api/v1/projects/<code>`, admin contracts), org/institution graphs, the
  directory-access populators, and the per-directory disk subtree
  (`resource_details_disk`, `get_disk_quotas`) — the account-level disk snapshot
  serves the roots; the per-directory tree is disk-scan territory.

## Caveat: the resource-details page computes companion grains EAGERLY

Prod caught `GET /user/resource-details/<projcode>?resource=<hpc/dav>` at ~5.4s,
97% DB, on a deep long-lived project (P93300041 — compute/DAV/archive, no disk; ~46
allocations over 10 resources incl. retired machines). The read-model **does not,
by itself, make this page's first load fast.**

The base route (`src/webapp/dashboards/user/blueprint.py`, `resource_details()`)
requires `?resource=` and branches on that resource's type (DISK vs HPC/DAV). On the
HPC/DAV branch its **initial GET runs, synchronously before any HTMX**, not just the
account-grain summary but a stack of finer-grain aggregates over `comp_charge_summary`
(90-day window, MPTT-subtree joins): `daily_charges` + `get_daily_summary_for_project`
(per-day), `get_user_summary_for_project` (per-user),
`get_monthly_user_counts_for_project` (fires at the 90-day default), and
`get_charges_by_projcode` (tree rollup). The ~5.4s is the **sum of ~5–7 independent
subtree aggregates**; the read-model short-circuits only the summary/rolling cards.

**Lever — read-model-independent and cheap.** The two chart fragments (usage-chart,
user-pie) are already lazy HTMX; the per-day table, per-user summary, monthly counts,
and tree rollup are not. Deferring those to lazy fragments too lets the first paint
need only account-grain data (read-model-fast) while the breakdowns stream in "in the
seconds" — the accepted tolerance for drill-downs. This is a **small separate
follow-on** (no new read-model scope) and is the real fix for this page's first-paint
latency.

## Where it lives — SAM MySQL first (CNPG at full cutover)

Create the table in the **prod SAM MySQL** (operator has `CREATE`), with a
`__bind_key__`-free ORM model so it can move later. Rationale:

- Consumers run against SAM MySQL and must join read-model rows to *live* SAM
  entities (titles, PI, roster, permission checks). Same-DB = a plain local
  `SELECT`; CNPG would force a second bind + cross-DB joins now.
- It still **spares MySQL**: the expensive MPTT rollup runs once per hour (feeder)
  instead of once per cold miss (many pollers × resources × TTL cycles); the read
  side becomes trivial indexed `SELECT`s. Net MySQL load drops even with the table
  on MySQL.
- CNPG becomes its home at the eventual full-Postgres cutover — not now (avoids a
  fragile live MySQL→PG mirror).

Table name: **`account_allocation_state`**.

## Refresh: hourly baseline (push) + a freshness gate on read

**Hourly full push** (`Hourly()` schedule, task `refresh_allocation_state`) is the
**sole writer** of the table. It re-projects every candidate allocation via the
batched builder above, so the baseline **cannot drift** and the whole table is
reconciled each hour. The runner owns the commit (tasks never commit — see
`deactivate_expired.py`), so readers see either the previous full set or the new
one, never a half-written table. `refreshed_at` is taken **before** the projection
starts (conservative: a charge that lands mid-run is at worst re-included next
hour, never claimed early). Size `expected_runtime` so the lease exceeds the
CronJob deadline (the documented double-run trap).

**Freshness gate (read side).** A reader uses table rows for a scope only when

1. `READ_MODEL_ENABLED` is on,
2. the request is for *now* (any `active_at` other than today → live path),
3. the scope has rows and the oldest `refreshed_at` is within `READ_MODEL_MAX_AGE`
   (default 7200 s — one missed hourly run is tolerated), and
4. the scope's **structural watermark** is not newer than `refreshed_at`, where the
   watermark is `MAX()` over the scope of `allocation.creation_time`,
   `allocation.modified_time`, `allocation_transaction.creation_time`,
   `account.modified_time`, `project.modified_time`, and the `charge_adjustment`
   date columns.

Otherwise the reader runs the existing live computation for that scope. So an
allocation edit, renewal, relink, threshold change, or adjustment makes its scope
fall back to live *immediately* (the "respond quickly when a project/allocation
changes" requirement) and the next hourly pass picks it up; sibling scopes stay on
the read-model. Membership needs no signal (roster is a live join). The gate costs
a handful of `MAX()` scans over ≤50k-row tables — milliseconds. Charge accrual lags
by at most ~1 h on the read-model path.

**What was considered and dropped (2026-09-09).** An earlier draft added a
`comp_charge_summary_status` revival as a per-row change stamp plus a
"baseline + today's delta" incremental on cache miss. The code and data argue
against both: a status row per summary row is a second table the size of
`comp_charge_summary` (502k rows locally), `upsert_comp_charge_summary` cannot
distinguish an update from a no-op re-post, the status `modified` stamp only bumps
on a dirty ORM flush, and the delta is a second algorithm (comp/dav window, disk
snapshot re-read, dated adjustments, the rolling-window back edge at midnight, the
inheriting root pass) — precisely the drift the sole-writer design exists to avoid.
Nothing measured says hourly charge staleness matters. **If it does** (PBS
enforcement near a cutoff), the cheapest follow-on is a delta commit that calls
the *same* `batch_get_subtree_charges` with `start_date = today` for comp/dav and
adds it to the baseline — added only on a measurement, never pre-emptively.

**Single-flight.** `BucketedTTLCache.get_or_compute` runs the compute outside the
adapter lock and, under Redis, the lock is a no-op (`sam/caching/redis_ttl.py`) — N
concurrent misses recompute N times today. The read-model makes a miss cheap, so
this stays as-is; add a dogpile lock only if a herd is observed.

## Read-side short-circuit (additive, flag-gated, self-healing)

Each consumer gains one branch: **if `fresh_state(...)` returns rows for this scope
→ assemble from them; else run the existing live path.** A missing/stale/absent
table never breaks a page (the `xras_sweep` self-heal precedent). Gate on
`READ_MODEL_ENABLED` (default off): ship the feeder first, validate, then flip
readers on. Seams:

- `src/sam/queries/allocations.py` (`get_allocation_summary_with_usage`): build
  `all_charges` and the disk caps from rows instead of `batch_get_*_charges` +
  `bulk_get_subtree_disk_capacity`. Covers everything behind
  `cached_allocation_usage` (`usage_cache.py`, TTL default **3600 s** via
  `ALLOCATION_USAGE_CACHE_TTL`).
- `src/sam/queries/dashboard.py` (`_build_user_projects_resources_batched`): phases
  4/5 and the disk override read from rows. `_build_project_resources_data`
  delegates to the batched builder for `[project]`, so the project card, edit page,
  project-details modal and the deep-dive inherit the branch. The deep-dive's
  per-node loop (`projects_routes.py`, `htmx_project_allocation_tree`) becomes one
  `get_projects_dashboard_data(nodes)` call — a win with the flag off too.
- `src/sam/queries/fstree_access.py` (`get_fstree_data`): the charge rollup and
  the threshold-window queries read `self_used` / `rolling_windows` from rows;
  skeleton, lifecycle, roster and cascade are untouched. fstree's response shape
  is legacy-frozen (Flask cache TTL 300 s via `CACHE_DEFAULT_TIMEOUT`, poll ≈ TTL);
  gates: `tests/api/test_fstree_access.py`, `tests/unit/test_fstree_queries.py`.
- `AllocationWithUsageSchema`: optional precomputed `usage` in context, supplied by
  the two API routes when `fresh_state` has the project.

## Anti-drift

The hourly full pass is a full reconciliation. A **parity test** compares
read-model rows against the live computation for sampled projects (the feeder and
the live path share code, so equality is the invariant). The `task_run` ledger is
the feeder's success watermark. Perf baselines are **ceilings**: a drop is not
flagged by CI, so the covered names are re-measured and lowered explicitly.

## Phased implementation (ordered commits on one living PR, base `staging`)

Each phase is one-or-more commits on the same branch; the suite stays green at each
stage boundary so a CIRRUS dispatch is always from a green, behavior-neutral state.
Every stage is ship-dark / flag-gated — no prod behavior changes until the flags
flip.

0. **Design doc** (this file; refined 2026-09-09).
1. **Table + model + test-DB bootstrap, dark.**
   `scripts/sql/create_account_allocation_state.sql` (utf8mb3 identifiers, PK
   `allocation_id`, indexes for the read patterns: `account_id`,
   `(resource_id, is_current)`, `projcode`, `(facility_name, allocation_type)`);
   ORM model in `src/sam/summaries/` + register in `src/sam/__init__.py`; a
   session-scoped `tests/conftest.py` hook that applies the script's `CREATE`
   statements to the test DB (see Traps — this is how the table reaches CI before
   the prod DDL); schema-validation pin (`tests/integration/test_schema_validation.py`).
2. **Projection + Hourly feeder, ships disabled.** `project_allocation_state()`
   over the batched builder; the task in `src/scheduling/tasks/` +
   `tasks/__init__.py`; its name added to `SAM_TASKS_DISABLED` in
   `helm/values.yaml` in the same commit. Readers untouched.
3. **Freshness gate** (`fresh_state()`, `READ_MODEL_ENABLED`, `READ_MODEL_MAX_AGE`),
   unit-tested per fallback reason. No consumer wired yet.
4. **Short-circuit readers** (flag-gated, self-healing) — the seams above, each
   tested flag-on == flag-off and table-emptied → live; perf baselines lowered.
5. **(Deferred, separate PR)** delta accrual on measured need; the resource-details
   lazy-fragment follow-on; CNPG migration at the full-Postgres cutover.

**CIRRUS checkpoints** (dispatch `build-images-cirrus-deploy` at stage boundaries):
after stage 2 (task registers, disabled; the table does **not** exist in prod yet),
and after stage 4 — **apply the prod DDL only here**, once the schema has converged:
enable the task, soak dark for a day (ledger `detail`, row counts), then flip
`READ_MODEL_ENABLED` on the cluster and regenerate the CI LFS blob.

## Critical files to reuse
- Projection: `src/sam/queries/dashboard.py`
  (`_build_user_projects_resources_batched`, `DashboardResource`), which wraps
  `src/sam/projects/projects.py` (`batch_get_subtree_charges`,
  `batch_get_account_charges`) and `src/sam/queries/disk_usage.py`
  (`bulk_get_subtree_disk_capacity`).
- Readers: `src/sam/queries/allocations.py` (`get_allocation_summary_with_usage`,
  `_fetch_all_allocations`), `src/sam/queries/fstree_access.py`,
  `src/sam/queries/usage_cache.py`, `src/sam/schemas/allocation.py`.
- Task / schedule / registration: `src/scheduling/tasks/deactivate_expired.py`,
  `src/scheduling/schedules.py` (`Hourly`), `src/scheduling/registry.py`,
  `src/scheduling/ledger.py` (`lease_for`).
- New-table + ORM registration precedent: `scripts/sql/create_xras_request_override.sql`,
  `src/sam/integration/xras.py` (model + upsert classmethods), `src/sam/__init__.py`.
- Config seam usable outside Flask: `src/sam/notify/config.py` (`_raw`,
  `_config_bool`), `src/sam/caching/buckets.py` (`_config_int`).
- Short-circuit / self-heal precedent: `src/scheduling/tasks/xras_sweep.py`.

## Verification (per phase, on build)
- **Feeder:** `sam-admin tasks --run refresh_allocation_state --force` in webdev;
  ~4.3k rows populate; parity — rows equal `get_projects_dashboard_data` for the
  `subtree_project` and `inheriting_project` fixtures; a second run is idempotent
  and deletes rows that left the candidate set.
- **Gate:** each fallback reason has a test; editing an allocation stales its scope
  and no other.
- **Readers:** with the flag on, fstree/allocations/deep-dive render identically
  (fstree gates above); with the table emptied, pages still render via the live
  fallback (self-heal).
- Full suite + `test_schema_validation.py` + `test_docs.py`; `make helm-test`; re-run
  `make perf` with the flag on and a populated table and lower the covered baselines
  (`get_fstree_data`, `get_allocation_summary_with_usage[_all_resources]`,
  `get_user_dashboard_data`, `get_project_dashboard_data`, `fstree_api_route`,
  `allocations_index_route`, `user_dashboard_route`,
  `admin_expirations_expired_route`). Note the autouse `_reset_usage_cache`
  fixture in `tests/perf/conftest.py`.

## Traps (from the scaffolding survey)
- **The CI test DB is cloned from prod, and nothing applies `scripts/sql/*.sql`
  automatically** (`containers/sam-sql-dev/Dockerfile` says so on purpose). A new
  table cannot reach the LFS blob before the prod DDL exists. Until then the
  `tests/conftest.py` bootstrap executes the DDL script's `CREATE TABLE IF NOT
  EXISTS` against the test DB under `serial_file_lock`, so schema-validation compares
  the ORM against a *script-created* table — the convergence check we want. Once
  the prod DDL lands and the blob is regenerated, the hook is a no-op.
- **Task pods inherit nothing from `webapp.env`** — any env the feeder reads must be
  hand-added to `helm/templates/cronjob-tasks.yaml`; `SAM_DB_*` is already wired and
  the feeder needs nothing else.
- **`SAM_TASKS_DISABLED` is fail-open** — a new task goes live on the next wake
  unless its name is added to `helm/values.yaml` in the same change.
- **Lease vs `activeDeadlineSeconds`** — `lease = max(3 × expected_runtime, 900 s)`
  must exceed the CronJob's 3000 s, so `expected_runtime` > 1000 s.
- **fstree response bytes are legacy-frozen** — the read-model path must reproduce
  the shape exactly (`tests/api/test_fstree_access.py` guards it).
- **`usage_cache` key is day-granular on `active_at`** and the gate only serves
  "today"; historical as-of views always run live.
- **Baselines are ceilings** — lowering the real count never fails CI; lower the
  numbers in `tests/perf/baselines.json` on purpose.

## Out of scope
- CNPG placement now (SAM MySQL first).
- Roster / permission / carve-out / exchange live computations (stay live).
- Replacing the slow paths (they remain as the fallback).
- The per-day / per-user companion tables and the disk per-directory subtree.
- Any change to the comp ingest or `comp_charge_summary_status`.
