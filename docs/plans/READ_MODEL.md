# Allocation/usage read-model — design & implementation handoff

Status: **design approved, not yet built.** This doc is the handoff for a future
implementation. It captures the grain, placement, refresh strategy, and a phased
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
active account, fed on a schedule, that the slow paths **consult if present and
fresh, else fall back to the live computation** — the slow paths are never removed,
only short-circuited. Because the feeder runs the *same* batched computation the
live path uses, the table **cannot drift** from the live answer.

## Grain — one row per `account` = (project, resource, active allocation)

All consumers bottom out at the `account` leaf; they differ only in consumer-side
passes (tree reconstruction, TOTAL aggregation, fstree's status cascade, phantom
lifecycle rows). The table stays **flat at leaf grain** with the hierarchy columns
consumers need to reassemble.

**Columns**

- Anchors/keys: `account_id` (UNIQUE natural key — the existing summary tables lack
  one and accrue dup rows; this table will not), `allocation_id`, `projcode`,
  `resource_id` + `resource_name` + `resource_type`, `facility_name`,
  `allocation_type`.
- Hierarchy (tree/aggregation/cascade): `parent_projcode`, `is_root`, project MPTT
  `tree_root`/`tree_left`/`tree_right`, `is_inheriting`, `parent_allocation_id`,
  `root_projcode`.
- Values: `allocated` (raw amount); self `self_used`, `self_percent_used`,
  `charges_by_type` {comp,dav,disk,archive} (JSON), `adjustments`; subtree-rolled
  `used`, `remaining`, `percent_used` (= fstree `adjustedUsage`/`balance`); disk
  point-in-time `current_used_bytes`/`tib`, `snapshot_date`, `current_pct_used`;
  trailing-window `rolling_30`/`rolling_90` (self + pool); `cutoff_threshold`,
  `first_threshold`/`second_threshold`, raw `status`/`account_status` (consumers
  cascade); fairshare (tree-level, denormalized) `facility_fair_share_percentage`,
  `allocation_type_fair_share_percentage`; lifecycle `start_date`, `end_date`,
  `is_open_ended`, `project_active`, `account_active`.
- Watermark: `refreshed_at` per row (freshness gate + drift audit).

The `rolling_30`/`rolling_90` columns are load-bearing twice: fstree's threshold
breakdown and `get_project_rolling_usage`'s runway/threshold math both read them,
so the two fixed windows earn their place.

**Roster stays a live join, NOT in this table.** fstree's `users[]` is per account
but cheap (a join on `account_user`) and not the bottleneck; keeping it live also
means **membership changes never dirty the read-model**.

**Divergences are consumer-side passes over this one grain**, documented so nobody
forces them into the schema: tree reconstruction, `accountStatus` parent→child
cascade (`fstree_access.py`), TOTAL roll-ups (filter `is_root`), and fstree's
zero-valued "Expired"/"No Account" phantom rows (a fstree-only overlay query, or a
`lifecycle_status` column).

## Coverage (breadth)

A `make perf` census confirms this single grain (+ the two window columns)
short-circuits **9 of 10 function-level perf baselines and 4 route baselines** —
the whole allocation/usage core, not just the three motivating endpoints:

- **Covered** (all funnel through `get_detailed_allocation_usage` /
  `batch_get_subtree_charges` / `get_allocation_summary_with_usage`): user dashboard
  (`get_user_dashboard_data`), project + projects dashboard
  (`get_project_dashboard_data` / `get_projects_dashboard_data` → admin project
  card, admin expirations), `get_detailed_allocation_usage`, `get_allocation_summary
  [_with_usage][_all_resources]` (the ~52k-query cache hotspot, biggest win),
  `/allocations/projects`, fstree, `/api/v1/projects/<code>/charges/summary`,
  `/api/v1/projects/<code>/allocations`, the project deep-dive, and (via the window
  columns) `get_project_rolling_usage`.
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

## Where it lives — SAM MySQL first (CNPG at full cutover)

Create the table in the **prod SAM MySQL** (operator has `CREATE`), with a
`__bind_key__`-free ORM model so it can move later. Rationale:

- Consumers run against SAM MySQL and must join read-model rows to *live* SAM
  entities (titles, PI, roster, permission checks). Same-DB = a plain local
  `SELECT`; CNPG would force a second bind + cross-DB joins now.
- It still **spares MySQL**: the expensive MPTT rollup runs once per refresh
  (feeder) instead of once per cold miss (many pollers × resources × TTL cycles);
  the read side becomes trivial indexed `SELECT`s. Net MySQL load drops even with
  the table on MySQL.
- CNPG becomes its home at the eventual full-Postgres cutover — not now (avoids a
  fragile live MySQL→PG mirror).

Working table name: **`account_allocation_state`** (negotiable).

## Two-speed refresh (one projection, two WHERE clauses)

The feeder calls the **same batched computation the slow path uses**
(`get_allocation_summary_with_usage` / `Project.batch_get_subtree_charges` /
`bulk_get_subtree_disk_capacity`) and writes the result — so the read-model cannot
drift (the feeder *is* the expensive path). Two cadences differ only in which
accounts they re-project:

- **Hourly full pass** (`Hourly()` schedule) — re-project all active accounts; the
  authoritative usage refresh and the reconciliation that heals incremental drift.
  Tolerates slop.
- **Fast changed-only pass** (`CronExpr('*/N * * * *')`) — re-project only accounts
  changed since the last fast watermark. Three dirty-signals:
  - **Structural** (durable, already timestamped): `allocation_transaction.creation_time`
    (CREATE/EDIT/ADJUSTMENT/LINK/TRANSFER/DETACH — covers allocation *and* tree
    changes) and `project.modified_time` (create/deactivate). Membership needs no
    signal (roster is live).
  - **Usage:** `comp_charge_summary` is a *daily* summary whose current-day rows are
    rewritten every couple minutes as usage accrues, so `used` goes stale within the
    hour — the fast pass must re-project accounts whose summary rows changed. The
    freshness signal comes from the revived status table (below).
  - **Disk/archive** change once/day; their status `current`-flag flip /
    `activity_date` advance triggers re-projection of affected accounts (low volume;
    the hourly pass also covers them).

**Delta-accrual option** (worth measuring): store `used_prior` (through yesterday,
stable) + recompute only `used_today` per fast pass, so a pass costs "today's
charges for dirty accounts," not a full-window re-roll; the hourly pass folds
yesterday into the baseline. Simpler alternative: re-roll the full window for each
dirty account. Decide on the measured fast-pass cost.

Writes use the `upsert + management_transaction` idiom
(`src/sam/manage/summaries.py` shape); the scheduler runner owns the commit. Size
`expected_runtime` so the lease exceeds the CronJob deadline (the documented
double-run trap).

## Freshness stamp — revive `comp_charge_summary_status`

The comp ingest must record when a summary row changed so the fast pass can key on
it. **Decision: repurpose `comp_charge_summary_status`** (it already carries a
`modified` DateTime with `onupdate`), rather than add a `comp_charge_summary`
column. That table is currently a dormant in-flight lock (a legacy migration
reshaped it; ~438 stale rows; keyed `UNIQUE(command_id, charge_summary_id)`), so
reviving it safely means:

- **Stable `command_id` per feed** (one constant, e.g. `'read_model'`), NOT a fresh
  id per run. With the unique key that yields exactly one status row per summary
  row, bumped in place — bounded growth even though the current day is rewritten
  every couple minutes.
- **Write the status row from the ingest only when the summary actually changed** —
  `upsert_comp_charge_summary` returns `created`/`updated`; on either, upsert the
  status row and set `modified = now()`. An idempotent re-post that changes nothing
  writes no status row, so `modified` means "charges changed." This is the one new
  write in `src/cli/accounting/commands.py` (which today deliberately skips the
  table) and `src/sam/manage/summaries.py`.
- **Fast pass reads** `MAX(modified) per account_id > watermark` via a
  `summary → status` join.
- **One-time cleanup** of the stale lock rows / abandoned `command_id` when the new
  convention lands (a small script).
- Likely **no DDL** (the column exists) — confirm the type/index and add a schema
  pin.

The rejected alternative — a new `comp_charge_summary.modified_time` column — is
simpler; revisit only if the status-table revival proves awkward.

## Read-side short-circuit (additive, flag-gated, self-healing)

Each consumer gains a branch: **if `account_allocation_state` has rows for this
scope with `refreshed_at` within tolerance → assemble from them; else run the
existing live path.** A missing/stale/absent table never breaks a page (the
`xras_sweep` self-heal precedent). Gate on `READ_MODEL_ENABLED` (default off): ship
the feeder first, validate, then flip readers on. Seams:

- `src/sam/queries/usage_cache.py` (`cached_allocation_usage` → allocations
  dashboard + the `/charges/summary` and `/projects/<code>/allocations` APIs),
- `src/sam/queries/dashboard.py` (`get_user_dashboard_data` /
  `get_project_dashboard_data` / `get_projects_dashboard_data`, and the deep-dive
  `_build_project_resources_data`),
- `src/webapp/api/v1/fstree_access.py` (`_fstree_data`) — fstree's response shape is
  legacy-frozen; assert the `tests/api/test_allocations_endpoint.py` contract holds.

## Anti-drift

The hourly full pass is a full reconciliation. A **parity test** compares
read-model rows against the live computation for sampled projects (the feeder and
the live path share code, so equality is the invariant). The `task_run` ledger is
the feeder's success watermark.

## Phased implementation (ordered commits on one living PR, base `staging`)

Each phase is one-or-more commits on the same branch; the suite stays green at each
stage boundary so a CIRRUS dispatch is always from a green, behavior-neutral state.
Every stage is ship-dark / flag-gated — no prod behavior changes until the flags
flip.

0. **This design doc.**
1. **Table + model + feeder, dark.** `scripts/sql/create_account_allocation_state.sql`
   (utf8mb3 identifiers, UNIQUE `account_id`, indexes for the read patterns:
   resource / facility+type / projcode / `tree_root,tree_left`); ORM model in
   `src/sam/summaries/` + register in `src/sam/__init__.py`; regen the CI LFS blob
   (`containers/sam-sql-dev/backups/*.sql.xz` via `make bootstrap`, recommit) +
   schema-validation pins (`tests/integration/test_schema_validation.py`); the
   projection function (reuse the batched path); the **Hourly** feeder in
   `src/scheduling/tasks/` + `tasks/__init__.py` + `helm/templates/cronjob-tasks.yaml`
   env + `SAM_TASKS_DISABLED` rollout. Readers untouched.
2. **Freshness stamp + fast changed-only pass.** Revive `comp_charge_summary_status`
   (stable `command_id`, write on `created`/`updated`, `MAX(modified)` read; stale-
   row cleanup; schema pin), then the `CronExpr('*/N')` pass keyed off the three
   watermarks.
3. **Short-circuit readers** (flag-gated, self-healing) — wire the seams above;
   flip `READ_MODEL_ENABLED` after validation.
4. **(Deferred, separate PR)** CNPG migration at the full-Postgres cutover.

**CIRRUS checkpoints** (dispatch `build-images-cirrus-deploy` at stage boundaries):
after stage 1 (feeder populates dark — table fills, no behavior change), stage 2
(fast pass + stamp — freshness/cost), stage 3 (readers, flag still off — flip
`READ_MODEL_ENABLED` on the cluster to validate).

## Critical files to reuse
- Projection source of truth: `src/sam/queries/allocations.py`
  (`get_allocation_summary_with_usage`), `src/sam/projects/projects.py`
  (`batch_get_subtree_charges`, `get_detailed_allocation_usage`),
  `src/sam/queries/disk_usage.py` (`bulk_get_subtree_disk_capacity`).
- Write idiom: `src/sam/manage/summaries.py`, `src/sam/manage/transaction.py`.
- Task / schedule / registration: `src/scheduling/tasks/deactivate_expired.py`,
  `src/scheduling/schedules.py` (`Hourly`, `CronExpr`), `src/scheduling/registry.py`.
- New-table + ORM registration precedent: `scripts/sql/create_xras_request_override.sql`,
  `src/sam/integration/xras.py` (model + upsert classmethods), `src/sam/__init__.py`.
- Short-circuit / self-heal precedent: `src/scheduling/tasks/xras_sweep.py`.
- Summary tables + status: `src/sam/summaries/comp_summaries.py`
  (`CompChargeSummary`, `CompChargeSummaryStatus`), `dav_summaries.py`,
  `disk_summaries.py`, `archive_summaries.py`.

## Verification (per phase, on build)
- **Feeder:** `sam-admin tasks --run <feeder> --force` in webdev; confirm rows
  populate; parity — read-model rows equal the live
  `get_allocation_summary_with_usage` for sampled projects/resources.
- **Fast pass:** change an allocation locally, confirm the row updates within a
  fast-cadence window (not the hourly one).
- **Readers:** with the flag on, fstree/allocations/deep-dive render identically
  (fstree byte-shape gate `tests/api/test_allocations_endpoint.py`); with the table
  emptied, pages still render via the live fallback (self-heal).
- Full suite + `test_schema_validation.py` + `test_docs.py`; re-run `make perf` and
  confirm the covered baselines drop.

## Traps (from the scaffolding survey)
- **CI test DB is an LFS blob** — a new table reaches CI only after the blob is
  regenerated (`make bootstrap`) and recommitted; the DDL script alone only reaches
  prod out-of-band.
- **Task pods inherit nothing from `webapp.env`** — any env the feeder reads
  (a flag, `CACHE_REDIS_URL`) must be hand-added to
  `helm/templates/cronjob-tasks.yaml`; `SAM_DB_*` is already wired.
- **`SAM_TASKS_DISABLED` is fail-open** — a new task goes live on the next wake
  unless its name is added to `helm/values.yaml` in the same change.
- **Lease vs `activeDeadlineSeconds`** — the lease must exceed the CronJob deadline
  or a killed run is reclaimed mid-flight.
- **fstree response bytes are legacy-frozen** — the read-model path must reproduce
  the shape exactly (the endpoint contract test guards it).
- **comp status growth** — the stable-`command_id` convention is what keeps the
  revived status table to one row per summary; a per-run id would grow it without
  bound.

## Out of scope
- CNPG placement now (SAM MySQL first).
- Roster / permission / carve-out / exchange live computations (stay live).
- Replacing the slow paths (they remain as the fallback).
- The per-day / per-user companion tables and the disk per-directory subtree.
