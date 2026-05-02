# Phase 2: Collapse the disk-charging pipeline onto `disk_charge_summary`

## Restart instructions (read first when picking this up cold)

- Phase 1 has already shipped on branch `disk_orm_sync` as commit `42138a8`
  ("ORM parity for disk schema additions + soft-delete UX for root
  directories"). Confirm before starting:
  ```bash
  git log --oneline -1 disk_orm_sync   # → 42138a8
  source etc/config_env.sh && pytest    # baseline green (~65s, 1914 tests)
  ```
- Read `CLAUDE.md` end-to-end before editing — especially §5 "Universal
  `is_active` interface" (use `Model.is_active`, never raw column
  comparisons), §7 "Write Operations on ORM Models" (`update()` instance
  / `create()` classmethod pattern, NOT standalone `sam.manage` functions),
  and §9 "Form Validation in HTMX and API Routes" (schemas before routes,
  checkbox preprocessing). The full project conventions matter here.
- **Trap to avoid**: `disk_charge_summary.directory_id` is FK →
  `project_directory`, **NOT** `disk_resource_root_directory`. Confirm
  against `containers/sam-sql-dev/dump/foreign_keys.sql:50` (constraint
  `fk_dcs_directory`) before wiring relationships. Phase 1 initially
  pointed at the wrong table; the corrected mapping is what landed in
  `42138a8`.
- The user runs `pytest` by hand — do not invoke it via Bash unless asked.
  The webapp is started with `docker compose up webdev --watch`, not
  direct `flask run`.
- This plan was written assuming the four-PR split documented in the "PR
  strategy" section at the bottom. If you change that structure, update
  the verification + risk sections to match.

---

## Context

Phase 1 added two columns to the production schema and brought the ORMs into
parity:

- `disk_resource_root_directory.active` — soft-delete flag, wired through the
  admin UI.
- `disk_charge_summary.directory_id` — nullable FK → `project_directory`,
  unblocking per-directory attribution in the summary table.

Phase 2 cashes in on `directory_id`. The current disk-charging pipeline is a
three-tier convolution that pre-dates `disk_charge_summary`:

```
acct.{glade,quasar}.YYYY-MM-DD
        │
        ▼
   disk_activity            (per-user-per-fileset raw rows; UNIQUE on
        │                    (directory_name, username, activity_date, projcode))
        ▼
   disk_charge              (1:1 with disk_activity via disk_activity_id)
        │
        ▼
   disk_charge_summary      (aggregated per (user, project, date) — fileset
                             info collapsed away)
```

Today only Tier 3 (`disk_charge_summary`) drives charging math
(`Account.current_disk_usage()`, `Project.get_detailed_allocation_usage()`).
Tier 1 (`disk_activity`) is queried *only* for the WebUI Disk Usage Details
fileset drill-down (`src/sam/queries/disk_usage.py:416-658`). Tier 2
(`disk_charge`) has no live read-path.

With `directory_id` on Tier 3, the fileset drill-down can move there too —
which lets us delete the entire ingest path for `disk_activity` and
`disk_charge`. Per the user's decision, the legacy tables themselves stay
on disk as a historical record (no DROP), and their ORMs remain so the
existing rows are still readable; we simply stop writing to them.

The intended outcome of Phase 2:
1. **One writer**: ingest writes only `disk_charge_summary`, with
   `directory_id` set when a path resolves to a `project_directory`.
2. **One reader for drill-down**: fileset / per-directory views in the
   WebUI and CLI read `disk_charge_summary` joined to `project_directory`.
3. **Per-directory drill-down UX**: Disk Usage Details exposes a
   per-`project_directory` breakdown alongside the existing per-user
   timeseries — directly enabled by the new `directory_id` column.
4. **Unidentified rollup preserved**: gap-reconciliation rows continue to
   land with `directory_id=NULL` and `act_username='<unidentified>'`, with
   bytes attributed to the project lead — unchanged user-visible behavior.

Phase 1 has already been merged. This plan is what the follow-up PR will
deliver. The PR description for Phase 1 will summarize the schema parity
work and link forward to this plan as the agreed-upon Phase 2.

---

## High-level approach

A staged implementation that keeps the test suite green at every commit:

1. **Application-layer natural-key extension** (Python-only, no DDL) —
   extend the in-Python upsert probe in `_upsert_storage_summary` to
   include `directory_id`, so per-directory rows don't collide on upsert.
2. **Ingest cutover** — make `_run_disk` resolve `directory_id` per row,
   re-key the in-memory grouping by directory, write `disk_charge_summary`
   only, and stop calling the `disk_activity` / `disk_charge` writers.
3. **Read-path migration** — re-point all `disk_activity` consumers to
   `disk_charge_summary` (joined to `project_directory`), then ship the
   per-directory drill-down UI.
4. **Cleanup** — remove the now-dead `_write_disk_activity_and_charge`
   helper; mark `DiskActivity` / `DiskCharge` ORMs as legacy (read-only,
   write-path commented as "do not call"); keep their tests.

---

## Files to modify

### 1. Application-layer natural-key extension (no DB schema change)

**Python-only change — no `ALTER TABLE`.** The summary tables
intentionally have no DB `UNIQUE` constraint on their natural-key columns
(see comment in `src/sam/manage/summaries.py:16-19`: "concurrent writes
for the same natural key... processes must serialize writes for the same
natural key"). The "natural key" lives entirely inside the Python upsert
helper.

Today the in-Python existing-row probe keys on
`(activity_date, act_username, act_projcode, account_id)`
(`src/sam/manage/summaries.py:382-388`). To carry per-directory rows we
extend that probe to include `directory_id`, treating NULL as "no
directory" (a single bucket per (user, project, date)). The DB column
itself already exists from Phase 1 — no migration, no DDL, no index
changes.

- **`src/sam/manage/summaries.py`**
  - `_upsert_storage_summary()` (lines 307-415):
    - Add a kwarg `directory_id: Optional[int] = None`.
    - Extend the existing-row probe to include
      `model_cls.directory_id.is_(directory_id) if directory_id is None else model_cls.directory_id == directory_id`.
    - Pass `directory_id` into the `model_cls(...)` constructor.
  - **NB — ArchiveChargeSummary**: the helper is shared. Archive rows have
    no `directory_id` column, so default NULL keeps existing behavior. Add
    a `hasattr(model_cls, 'directory_id')` guard before the probe extension
    so we don't filter on a column that doesn't exist on `ArchiveChargeSummary`.
  - **No DB-side UNIQUE constraint** is added (CLAUDE.md notes summary
    tables intentionally have no UNIQUE; concurrent writes serialize at
    the application layer). Unchanged.

- **`tests/unit/test_upsert_disk_pre_resolved.py`** — add 3 cases:
  1. Two upserts with same (date, user, project, account) but different
     `directory_id` create two distinct rows.
  2. Re-upsert with same `directory_id` UPDATEs in place.
  3. NULL `directory_id` is treated as a distinct bucket (one row, not
     duplicated by repeated NULL).

### 2. Ingest cutover

- **`src/cli/accounting/commands.py`**

  - **Rename + rework `_group_disk_entries()`** (lines 65-117):
    - New key: `(activity_date, projcode, username, directory_path)`
      (today: `(activity_date, projcode, username)`).
    - Result: one entry per (user, project, directory) per snapshot day.
    - The function already passes synthetic `<unidentified>` rows through
      unchanged — preserve that branch verbatim.

  - **`_run_disk()` ingest loop** (lines 403-806):
    - The existing `pd_path_to_project` cache (lines 561-569) maps
      `directory_name` → `Project`. Extend it to
      `pd_path_to_pd_id_and_project: dict[str, (int, Project)]` so the
      ingest path can attach a `directory_id` cheaply.
    - In `_resolve_for_row()` (lines 573-592): when the path matches a
      `ProjectDirectory`, also stash the `project_directory_id` on the
      resolved entry (e.g. via a new `directory_id` field on
      `DiskUsageEntry`).
    - In the upsert loop (lines 700-750): pass
      `directory_id=entry.directory_id` to `upsert_disk_charge_summary()`.
      Unmatched paths emit `directory_id=None` (current behavior — no
      regression).

  - **Remove the Tier-1/Tier-2 writes**:
    - Delete the call to `_write_disk_activity_and_charge()` from `_run_disk()`.
    - Mark `_write_disk_activity_and_charge()` itself with a deprecation
      comment but **leave the function in place** for one release cycle so
      anyone running an old branch sees a clear "this path is gone" rather
      than an `AttributeError`. The CLI `--write-legacy-tiers` flag (if any
      exists — check) can flip it back on for emergency use; otherwise
      simply remove from the call graph.
    - Drop or rewrite `tests/unit/test_upsert_disk_pre_resolved.py` cases
      that exercise `disk_activity`/`disk_charge` upserts — these become
      legacy tests.

  - **`_build_unidentified_disk_rows()` (lines 972-1117)**: no signature
    change needed. Synthetic gap rows continue to flow through with
    `directory_id=None` (the default). Add a unit-test case verifying the
    emitted summary row has `directory_id IS NULL`.

- **`src/cli/accounting/disk_usage/base.py`**
  - `DiskUsageEntry` (lines 10-46): add an optional `directory_id:
    Optional[int] = None` field to carry the resolved PK forward to the
    upsert.

### 3. Read-path migration + per-directory drill-down

The drill-down currently lives in `src/sam/queries/disk_usage.py` and
queries `DiskActivity`. Re-implement against `DiskChargeSummary` joined
to `ProjectDirectory`:

- **`src/sam/queries/disk_usage.py`**
  - `get_disk_usage_timeseries_for_directory()` (lines 416-499):
    swap `DiskActivity` for `DiskChargeSummary` filtered by
    `directory_id == :pd_id` (resolve `directory_name → project_directory_id`
    once at the call site).
  - `get_directory_user_breakdown_at()` (lines 502-547): same swap.
  - `get_subtree_directory_usage_at()` (lines 550-596): instead of grouping
    by `DiskActivity.directory_name`, group by `DiskChargeSummary.directory_id`
    and `JOIN ProjectDirectory` to get the human-readable path.
  - `bulk_get_directory_usage_at()` (lines 599-658): same shape change.
  - **Behavior preserved**: queries continue to return per-user bytes/files
    for a directory, and the "Others" bucket math is unchanged.
  - **NULL `directory_id` handling**: rows with NULL participate in the
    project-level total but appear in the drill-down only as a single
    `(directory='<unattributed>', user=<lead>)` synthetic row, mirroring
    today's `<unidentified>` UI.

- **`src/webapp/dashboards/user/blueprint.py`**
  - `_render_disk_resource_details()` (lines 559-700+):
    - Replace its calls to the disk-usage query helpers with the new
      `directory_id`-aware variants. Most call sites are signature-stable.
    - Extend the template context with a per-directory breakdown:
      `directories=[(project_directory, bytes, tib_years, n_files, n_users), …]`.
    - Add a fileset-filter param (`?directory_id=<id>` alongside the
      existing `?fileset=<dirname>`) and prefer the ID form going forward.

- **`src/webapp/templates/dashboards/...` (Disk Usage Details template)**
  - Add a "By Directory" panel/table next to the existing "By User"
    timeseries. Each row: directory path · current bytes · TiB-years ·
    file count · top-N users sparkline.
  - Each directory row links to the existing per-fileset drill-down view
    (filtered by `directory_id`), so the per-user timeseries is scoped.
  - Inactive `disk_resource_root_directory` rows (Phase 1 soft-delete) are
    filtered or visually de-emphasized in the directory list.
  - Templates: identify the disk-details template under
    `src/webapp/templates/dashboards/user/` (likely
    `disk_resource_details.html` or similar) — confirm during implementation.

### 4. Legacy ORMs and tests

Per the user's decision, `disk_activity` / `disk_charge` tables are kept
indefinitely as a historical record:

- **`src/sam/activity/disk.py`** — `DiskActivity` and `DiskCharge` ORMs
  remain unchanged, but with a top-of-class docstring noting "writes
  stopped in <release/date>; rows present are historical".
- **`src/sam/manage/summaries.py`** — `upsert_disk_activity()` (lines
  437+) and `upsert_disk_charge()` keep their definitions but get a
  `# DEPRECATED: no longer in the live ingest path` comment. Don't delete
  yet — leave one release cycle for any out-of-tree callers.
- **`tests/unit/test_upsert_disk_pre_resolved.py`** — the upsert tests
  for `disk_activity` / `disk_charge` stay as-is so we can verify the
  legacy code still works on existing rows.

### 5. New tests

- **`tests/unit/test_accounting_disk_admin.py`** — extend:
  - `test_dry_run_produces_no_rows`: assert no `DiskActivity` rows touched
    (writer removed).
  - `test_live_run_writes_user_row`: assert `disk_charge_summary` rows
    have `directory_id` set when path matches a `ProjectDirectory`.
  - `test_multi_directory_rows_sum_per_user`: this test currently
    *expects* per-user collapse — invert it: same user, two filesets,
    two separate `disk_charge_summary` rows distinguished by `directory_id`.
  - `test_gap_reconciliation_creates_unidentified_row`: assert the gap
    row has `directory_id IS NULL`.
  - **New** `test_no_disk_activity_writes`: explicit assertion that the
    refactored ingest writes zero `DiskActivity` / `DiskCharge` rows on a
    representative input.

- **`tests/unit/test_disk_usage_queries.py`** — re-point existing test
  setup to seed `DiskChargeSummary(directory_id=…)` instead of
  `DiskActivity`, and verify the same query helpers return equivalent
  shapes.

- **`tests/integration/`** — add a small end-to-end test that drives
  `sam-admin accounting --disk` against a fixture acct file and asserts:
  1. `DiskActivity.row_count() == 0` after ingest.
  2. `DiskChargeSummary` rows exist with `directory_id` populated for
     known paths and `NULL` for `<unidentified>` rollup.
  3. WebUI route `/dashboard/.../disk` returns 200 and the per-directory
     panel renders the seeded directories.

### 6. CLI / docs

- **`docs/plans/DISK_CHARGING.md`** — update to describe the one-tier
  pipeline and document that `disk_activity` / `disk_charge` are
  read-only legacy.
- **`CLAUDE.md`** — under "Activity/Usage" remove `DiskActivity` /
  `DiskCharge` from the active-models list (move them to a new "Legacy
  models" subsection).
- **`sam-admin accounting --disk --help`** — copy update to mention that
  rows now land directly in `disk_charge_summary` with `directory_id`.

---

## Patterns and helpers being reused

- **`tib_years()`** in `src/sam/summaries/disk_summaries.py:125-135` —
  unchanged charging formula; ingest still feeds per-row bytes through it.
- **`mark_disk_snapshot_current()`** in
  `src/sam/summaries/disk_summaries.py` — unchanged; the snapshot
  status row still flips per snapshot day.
- **`_resolve_*` helpers** in `src/sam/manage/summaries.py` — unchanged
  user/account/project resolvers continue to drive `act_*` columns.
- **`_build_unidentified_disk_rows()`** in `src/cli/accounting/commands.py`
  — preserved verbatim; only its output-write path changes to one tier.
- **`Account.current_disk_usage()` / `Project.get_detailed_allocation_usage()`**
  — already read `disk_charge_summary` only (per CLAUDE.md). Zero changes.
- **`ProjectDirectory.is_currently_active`** hybrid (DateRangeMixin) — used
  during ingest to filter the candidate-directory cache to currently-valid
  rows, and during read to skip stale dirs in the drill-down.
- **`DiskResourceRootDirectory.is_active`** (Phase 1) — used in the
  WebUI drill-down to filter or label retired root prefixes.

---

## Critical risks and mitigations

| Risk | Mitigation |
|---|---|
| Natural-key change on `disk_charge_summary` causes upsert to silently double rows when `directory_id` is sometimes NULL and sometimes set for the same (user, project, day) | Land the `_upsert_storage_summary` change behind a unit test exercising both NULL-and-set forms in one snapshot before any production ingest runs. The test must assert exactly two rows, not three. |
| Existing rows pre-Phase-1 have `directory_id IS NULL` for everything | Backfill is **out of scope** for this PR. Document in the PR body that historical rows stay NULL (showing as `<unattributed>` in the new drill-down). A separate one-time backfill PR can attribute them later if desired. |
| Per-directory grouping inflates row counts in `disk_charge_summary` (today: 1 row per (user, project, day); proposed: ~1 row per (user, project, directory, day)) | Quantify in the PR description: typical projects have 1–3 active directories, so the multiplier is small. The new `idx_disk_charge_summary_directory_id` index keeps lookups bounded. |
| WebUI drill-down breaks for existing snapshot dates when `directory_id` is NULL on every row | Implement the `<unattributed>` synthetic-row path *first* in `get_subtree_directory_usage_at()` and verify against the obfuscated test DB (which is full of pre-Phase-2 NULLs) before relying on populated data. |
| `_write_disk_activity_and_charge` removal breaks an unknown out-of-tree caller | Grep `containers/`, `scripts/`, `legacy_sam/` and any deploy YAML for callers before deletion. Keep the function definition for one release; the change is "stop calling," not "remove definition." |

---

## Verification

End-to-end:

1. **Unit suite** — fast iteration:
   ```bash
   source etc/config_env.sh && pytest -k "disk or accounting" -v
   source etc/config_env.sh && pytest                              # full ~65s
   ```

2. **Schema parity** (no regression from Phase 1):
   ```bash
   source etc/config_env.sh && make check-db-vs-orms
   pytest tests/integration/test_schema_validation.py -v
   ```

3. **Ingest dry-run on a sample acct file**:
   ```bash
   sam-admin accounting --disk --resource Stratus \
       --user-usage path/to/acct.glade.YYYY-MM-DD --dry-run --verbose
   ```
   - Expect: zero "would write disk_activity/disk_charge" lines, only
     "would write disk_charge_summary".
   - Expect: per-fileset breakdown reported (one summary row per
     directory match), `<unidentified>` rollup intact.

4. **Live ingest into mysql-test**:
   ```bash
   sam-admin accounting --disk --resource Stratus --user-usage <fixture>
   mysql -h127.0.0.1 -P3307 -uroot -proot sam -e \
       "SELECT COUNT(*) FROM disk_activity WHERE activity_date = '<today>';"
   # → 0 (writer removed)
   mysql ... -e "SELECT COUNT(*), COUNT(directory_id) FROM disk_charge_summary
       WHERE activity_date = '<today>';"
   # → both >0; second count < first iff there are <unidentified> rows
   ```

5. **WebUI manual check**:
   ```bash
   docker compose up webdev --watch
   # Navigate: User Dashboard → Resource Details → a DISK resource
   ```
   - "By Directory" panel renders with the seeded paths.
   - Click a directory → per-user timeseries scopes to that directory.
   - `<unattributed>` row appears for the gap-rollup bytes (lead user).
   - Inactive `DiskResourceRootDirectory` rows do not appear (or are
     visibly labeled).

6. **CLI parity check** (no charging-math regression):
   ```bash
   sam-search project SCSG0001 --list-allocations
   ```
   - `Used` / `Remaining` columns identical to pre-Phase-2 baseline
     (they read `disk_charge_summary` totals — collapsing per-directory
     rows back to per-(user, project) sums must zero out).

---

## PR strategy

This plan is too large for a single PR. Recommended split:

- **PR-A (Phase 2a)** — Python-only natural-key extension in
  `_upsert_storage_summary` + tests. No DB changes, no ingest behavior
  change yet (callers still pass `directory_id=None`). Lands safely on
  its own.
- **PR-B (Phase 2b)** — ingest cutover:
  `_group_disk_entries`, `DiskUsageEntry.directory_id`,
  `_run_disk` write-path swap, stop calling
  `_write_disk_activity_and_charge`. Tests rewritten. **Behavioral PR**
  — flag a deploy maintenance window.
- **PR-C (Phase 2c)** — read-path migration:
  `src/sam/queries/disk_usage.py` re-pointed to `disk_charge_summary`,
  WebUI per-directory drill-down panel ships. Pure UX/read PR; no
  schema/ingest changes.
- **PR-D (Phase 2d, optional)** — historical backfill:
  one-time job to populate `directory_id` on pre-Phase-2
  `disk_charge_summary` rows by joining against `disk_activity`. Defer
  this decision until Phase 2c is in production and we observe how
  visible the `<unattributed>` rows are.
