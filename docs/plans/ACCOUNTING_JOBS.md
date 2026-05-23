# Plan: `sam-search accounting --jobs` (individual job listing via the job_history plugin)

**Status:** Implemented end-to-end (this branch).
`--job-id` now forwards directly to the plugin's `job_id` filter
(hpc-usage-queries PR #71 landed it as a single text-column,
boundary-anchored `LIKE` that catches scalar + array parent + every
array element of one job without bleeding into longer numeric prefixes).
`--largest` continues to ship as a client-side union of two upstream
sort keys; that can later collapse to one query if a combined-charge
sort key is added upstream.

## Context

Recent PRs gave the **web UI** an individual-job listing backed by the
`job_history` (hpc-usage-queries) plugin — `webapp/jobs/service.py` calls
`JobQueries(session, machine=machine).jobs_search(...)` and renders per-job
rows. The **CLI** had fallen behind: `sam-search accounting` only read SAM's
own pre-aggregated `comp_charge_summary` table (via
`query_comp_charge_summaries`, no plugin), so an operator could see *daily
rollups* but not *individual jobs*.

This adds a `--jobs` mode to `sam-search accounting` that flips the command
from the summary-table path to the per-job plugin path, bringing the CLI back
to parity with the UI. It reuses the existing date filters, the existing
plugin-session pattern, and the existing GPU/CPU charge-classification
convention. It fully supports both `rich` and `json` output.

## Decisions

- **Charges column** uses the existing **`GPU_FRACTION_THRESHOLD`** convention
  (`commands.py`). Per job we classify CPU-vs-GPU exactly like the daily
  poster's `adapt_jobstats_row` and show the *one* classified charge
  (`gpu_charges` if GPU-classified, else `cpu_charges`). This logic is
  **refactored into a shared helper** (`classify_comp_resource`) that both
  `adapt_jobstats_row` and the new per-job path call — no duplicated billing
  rules.
- **Selection**: default `--recent M` (most-recent M jobs); also `--largest N`
  (top N by charges). Mutually exclusive; default `--recent 50`.
- **Job-ID search**: `--job-id` forwards the user's input verbatim to the
  plugin's `job_id` filter (hpc-usage-queries PR #71). The plugin owns
  the shape classifier: bare digits (`6049117`) match the scalar form
  plus every array element via two boundary-anchored `LIKE` clauses; a
  partial array form (`6049117[28]`, `6049117[]`) prefix-matches across
  hosts; a full id with host (`6049117[28].desched1`) is an exact match.
  SAM-side, `--job-id` is its own selector — mutually exclusive with
  `--recent`/`--largest`, runs one no-sort/no-limit query per machine,
  and labels the envelope `mode='job_id'`.
- **Memory**: excluded from *both* rich and JSON (we track but don't bill it).
- **`--largest` ships now, client-side** (no peer dependency): see §3.

## Existing pieces reused

| Need | Reuse | Location |
|---|---|---|
| Date validate/resolve (`--date/--today/--last/--start/--end`) | `_validate_accounting_dates`, `_resolve_accounting_dates` | `src/cli/accounting/dates.py` |
| Open a plugin session in the CLI (no Flask) | `self.require_plugin(HPC_USAGE_QUERIES)` → `mod.get_session(machine)` → `mod.JobQueries(...)` | template at `src/cli/accounting/commands.py` (`_run_comp`) |
| CPU/GPU classification + charge selection | `GPU_FRACTION_THRESHOLD`, vis-queue rule, machine routing | `src/cli/accounting/commands.py` |
| Decimal-hours formatting | `fmt.hours(seconds)` | `src/sam/fmt.py` |
| Number/date formatting, `None`→`—` | `fmt.number`, `fmt.date_str` | `src/sam/fmt.py` |
| JSON envelope encoder | `output_json` (`kind`, ISO dates, Decimal→float, indent=2) | `src/cli/core/output.py` |
| Per-job field names & default columns | `_DEFAULT_COLS`, `_VERBOSE_EXTRAS`, all-zero suppression | `src/webapp/jobs/routes.py` |
| Plugin call shape (`columns`, `account`, `user`, `queue`, `qos`, `status`, `start`, `end`, `limit`, `sort_by`, `sort_dir`) | `search_jobs()` | `src/webapp/jobs/service.py` |

## Implementation

### 1. Shared charge classifier (`src/cli/accounting/commands.py`)

`classify_comp_resource(machine, queue, cpu_hours, gpu_hours, cpu_charges,
gpu_charges) -> (resource_name, machine_name, comp_hours, charges)` — pure
function holding the vis-queue zeroing, the `gpu_fraction >=
GPU_FRACTION_THRESHOLD` test, and the derecho/casper → resource/machine
routing. It never skips zero rows; callers decide. `adapt_jobstats_row` is now
a thin wrapper that keeps the poster-only `comp_hours <= 0 → None` skip and
delegates classification. `comp_hours` is the billing metric (GPU-hours for a
GPU charge, CPU core-hours for a CPU charge).

### 2. CLI surface (`src/cli/cmds/search.py`)

New options on the existing `accounting` command:
`--jobs`, `--recent M`, `--largest N` (mutually exclusive, default
`--recent 50`), `--job-id ID`, `--qos NAME`. Reuses `--user`, `--project`
(comma-separated → list of exact account codes), `--queue`, `--machine`, and
all date flags. Validation: `--resource` rejected with `--jobs` (resource is
derived per machine); the `--jobs`-only flags error if used without `--jobs`.
Dispatch: `--jobs` → `AccountingJobsCommand`; else the unchanged
`AccountingSearchCommand`.

### 3. Command class `AccountingJobsCommand` (`commands.py`)

1. `--job-id` → clear "not yet available" error, exit 2.
2. Resolve selection mode/limit; `--recent`/`--largest` mutually exclusive.
3. Resolve machines: `--machine` (validated against `VALID_JOB_MACHINES`) else
   `$JOB_HISTORY_MACHINES` (default `derecho,casper`).
4. `require_plugin(HPC_USAGE_QUERIES)`; build `columns` = display fields + the
   four classifier fields (`cpu/gpu_hours`, `cpu/gpu_charges`); never memory.
5. Per machine open `get_session(machine)` (try/except/finally close) and call
   `jobs_search`:
   - `--recent M`: one query, `sort_by='end', sort_dir='desc', limit=M`.
   - `--largest N`: two queries `sort_by='cpu_charges'` and `'gpu_charges'`
     (both desc, limit N), union by `(machine, job_id)`. Both keys are already
     in the plugin sort whitelist — **no peer PR needed**.
6. Merge, classify each row (attach `resource`/`comp_hours`/`charges`),
   re-rank (end desc for recent; classified `charges` desc for largest),
   truncate to M/N. Output JSON envelope or `display_jobs_table`. No rows →
   exit 1; plugin missing / bad machine / session error → exit 2.

### 4. Rich display `display_jobs_table` (`src/cli/accounting/display.py`)

Default (≤80 cols): Job ID, Project, User, [Machine when >1 queried], Nodes,
Cores, [GPUs when any nonzero], Wall-h (`fmt.hours(elapsed)`), Charges
(`fmt.number`). `--verbose` adds Comp-h, Resource, QoS, Factor, Queue, Status.
Memory is never shown. Totals footer = job count + summed charges.

### 5. JSON envelope

`{kind:'comp_jobs', start_date, end_date, mode:'recent'|'largest', machines,
count, rows:[…]}` where each row is the plugin dict plus derived
`machine`/`resource`/`comp_hours`/`charges`; memory keys are never requested
or emitted. Built whenever `output_format=='json'`.

### 6. Peer-repo work — hpc-usage-queries

1. **`job_id` filter** on `JobQueries.jobs_search` / `jobs_count` → **landed
   in hpc-usage-queries PR #71** (single text column, two boundary-anchored
   `LIKE` prefixes, no `short_id` dependency — chosen after live-DB probe
   showed `short_id` is `NULL` on every array-job row). Closes the
   `--job-id` deferral.
2. *(optional, still future)* **combined-charge sort key** (`cpu_charges +
   gpu_charges`) so `--largest` collapses from two union queries to one
   exact query.
3. *(verified live)* **`account` optional** path works — `--user`-only
   queries don't break on the upstream `account=None` code path.

## Files

- `src/cli/cmds/search.py`, `src/cli/accounting/commands.py`,
  `src/cli/accounting/display.py`
- `tests/unit/test_sam_search_jobs_cli.py`
- hpc-usage-queries (peer repo, PR #71): `jobs_search` / `jobs_count`
  `job_id` kwarg + `jobhist search --job-id` CLI option

## Verification

`tests/unit/test_sam_search_jobs_cli.py` (CliRunner + fake plugin) covers the
recent/largest/job_id/multi-machine/suppression/error paths and a
classifier-parity guard. Manual smoke (full env, plugin + DB):

```
sam-search accounting --jobs --last 7d --user benkirk
sam-search accounting --jobs --largest 20 --last 30d --project SCSG0001
sam-search accounting --jobs --last 7d --machine derecho --verbose
sam-search --format json accounting --jobs --recent 5 | jq '.rows[0]'
sam-search accounting --jobs --last 365d --job-id 6049117
sam-search accounting --jobs --last 365d --job-id 6049117[28].desched1
```
