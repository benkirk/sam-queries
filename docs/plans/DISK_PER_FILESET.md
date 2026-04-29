# Per-Fileset Disk Granularity (Layer 2 — Future Work)

## Status

**Deferred.** Not in scope for the current branch. Layer 1 (the
SUM-at-import fix) ships first; this doc tracks what we're choosing
NOT to do today, why, and what re-scoping it would look like.

## Background

The disk-charging input file `acct.glade.YYYY-MM-DD` ships one row
per `(user, project, fileset)` triple. Today we sum-at-import, so
every `(user, project)` lands as a single row in
`disk_charge_summary` — fileset granularity is discarded. Multi-fileset
projects (e.g. `P43713000` with 4 csfs1 filesets) appear in the
webapp Resource Details view as a single tree node with one
aggregate `current_bytes` and a flat comma-joined label of all
the project's directory names.

This is correct for **billing math** (matches legacy SAM exactly —
see `legacy_sam/.../AccountingNamedQuery.xml`'s
`calculateDiskChargeSummaries` query) but loses information that
operators sometimes want:

- Which fileset within a multi-fileset project is filling up?
- For projects with directories on multiple disk resources (Quasar,
  Stratus, Campaign_Store), the disk Resource Details tree node
  shows ALL of them together regardless of which resource the page
  is rendering — paths from Quasar/Stratus bleed into the
  Campaign_Store view because `ProjectDirectory` has no `resource_id`
  and `build_disk_subtree` doesn't filter by path prefix.

Operators today get fileset granularity from
`sam-admin accounting --resource Campaign_Store --reconcile-quotas`,
which reads `cs_usage.json` fresh per run. That works but doesn't
serve the webapp UX.

## Why we deferred

The SUM-at-import patch is small, fully reversible, and matches a
known-good legacy behavior. Adding fileset granularity to the schema
is a real-world investment: schema add, importer rewrite, query-side
rollup, template work, backfill. Worth it only if the UX win is
worth the operator cost. Today the answer is "not yet — the
correctness fix gets us back to parity with legacy and unblocks
operations."

## What this work would look like

### Schema

Add a nullable `directory_id` FK to `disk_charge_summary`:

```sql
ALTER TABLE disk_charge_summary
  ADD COLUMN directory_id INT NULL,
  ADD INDEX idx_dcs_directory (directory_id),
  ADD CONSTRAINT fk_dcs_directory FOREIGN KEY (directory_id)
    REFERENCES project_directory(project_directory_id);
```

NULL is allowed for synthetic gap rows (`<unidentified>`), Quasar
`'total'` rollup rows, and historical rows imported before this
change. Operator runs the ALTER manually (per CLAUDE.md "ORM
follows database, never modify database schema").

### Natural key

Extend `_upsert_storage_summary`'s natural key
(`src/sam/manage/summaries.py:381–387`) to include `directory_id`,
treating NULL as a distinct slot via `directory_id IS NULL` filter.
Two rows differing only by directory now coexist instead of
overwriting.

### Importer

Drop the Layer-1 `_group_disk_entries` aggregation (it's a
correctness patch made obsolete by the schema change). Each
`DiskUsageEntry` carries a `directory_path` already — extend
`_resolve_for_row` in `src/cli/accounting/commands.py` to also
return the `ProjectDirectory.project_directory_id` and pass it
through to the upsert.

### Display layer

**Path-prefix `_directory_resource_for_path()` helper** in
`src/sam/queries/disk_usage.py`:

```
/gpfs/csfs1/...  → Campaign_Store
/quasar/...      → Quasar
/stratus/...     → Stratus
```

Use it in `build_disk_subtree` to filter
`ProjectDirectory.directory_name` to only directories on the
requested resource. Today they bleed across resources.

(Cleaner alternative: add `resource_id` to `ProjectDirectory`.
Path-prefix is fine until something else needs the column.)

**Per-fileset capacity rendering** in
`src/webapp/templates/dashboards/user/resource_details_disk.html`:
when a project node has >1 fileset on the current resource, render
each as a sub-row with its own bytes / files / mini bar.
Single-fileset projects keep the current single-line render.

### Query-side rollup

Extend `bulk_get_subtree_disk_capacity` in
`src/sam/queries/disk_usage.py` to group by both `account_id` AND
`directory_id`. Project-level capacity becomes SUM across the
project's filesets — same total as today, but the per-fileset
detail is also available.

### Tests

- New `tests/unit/test_accounting_disk_admin.py::TestDiskAdminCli
  ::test_multi_directory_rows_preserved_per_fileset` — two rows in
  → two rows out, each tagged with the right `directory_id`.
- Extend `tests/unit/test_disk_usage_queries.py` with a
  multi-fileset case asserting per-directory `current_bytes`.
- Webapp integration test that `/user/resource-details?resource=
  Campaign_Store` for a multi-fileset project renders one sub-row
  per fileset and only shows csfs1 paths (not Quasar / Stratus).

### Backfill

Re-run the existing CLI import loop against archived `acct.*`
files. The new code path resolves `directory_path → directory_id`
and writes the FK; older NULL rows get superseded under the
existing delete+upsert idempotency.

### Open question — granular table?

Legacy SAM kept TWO tables: `disk_activity` (granular,
per-(activity, directory)) and `disk_charge_summary` (per-(user,
project, day) rollup). They had clean separation between "what
we collected" and "what we charge from."

The current Python pipeline merges them. If `disk_charge_summary`
gets `directory_id` and we let one (user, project) become N
fileset rows, we'll roughly multiply table cardinality by the
average filesets-per-project. Initial sketch: ~2k rows/day for
Campaign_Store today; per-fileset it's maybe 4–6k/day. Not
catastrophic, but worth measuring.

If that grows uncomfortably, mirror legacy's two-tier design:
introduce a `disk_activity_daily` granular table and keep
`disk_charge_summary` per-(user, project, day) for billing. The
webapp queries against the granular table for fileset breakdown.

Defer answering until Layer 1 is live, we have a couple of months
of imports under the new behavior, and we can measure real-world
cardinality.

## Pre-requisites for picking this up

- Layer 1 (`_group_disk_entries`) shipped and stable on prod.
- Concrete UX request from operators (right now this is forward-
  looking; revisit once someone hits a real "I need to see which
  fileset is full" moment).
- Decision on the granular-table question above.

## Related files (reference, no changes today)

- `src/sam/summaries/disk_summaries.py` — `DiskChargeSummary` model.
- `src/sam/manage/summaries.py` — `_upsert_storage_summary` natural
  key.
- `src/cli/accounting/commands.py` — `_resolve_for_row`,
  `_group_disk_entries`.
- `src/sam/queries/disk_usage.py` — `build_disk_subtree`,
  `bulk_get_subtree_disk_capacity`.
- `src/webapp/templates/dashboards/user/resource_details_disk.html`
  — per-fileset render target.
- `legacy_sam/src/main/resources/hibernate/AccountingNamedQuery.xml`
  — legacy `calculateDiskChargeSummaries` reference.
