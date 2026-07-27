# Job History UX round 3 — cross-repo status & restart doc

**State (2026-07-27): Phase A done; upstream review added two follow-ups to PR #100 (`04684f8` owners_sort_by, `0ae1823` FK-based lookup filters) and SAM reacted (`62c89e2`: threads `owners_sort_by=metric` into the histogram call + cache opts). Phase B implemented on `redis_and_ux_tweaks`, Playwright-verified except the stacked-GPU ranking, which needs a container rebuild at plugin `0ae1823` (the pre-review build TypeErrors on `owners_sort_by` — degrades to the error banner). Remaining: rebuild + GPU-pill re-smoke, #100 merge to `main`, then the SAM PR to `staging` with the lockstep-deploy note (now pinned at plugin ≥ `04684f8`).**

## The four issues (Ben's review of PRs #381/#382)

1. Job name column blows up table width → truncate at `max-width: 35ch` (tooltip already exists).
2. No User column in job tables → add (sortable), smart-suppressed when the view has one user.
3. Histograms flat vs fs_scans; bucket table single-level behind `<details>` → owner-stacked gradient bars, table always visible, two-level bin → users → user's-jobs drill.
4. Derecho / By User / GPU-Hours shows one user → plugin ranked by combined hours before top-25 truncation.

## Phase A — DONE

Plugin PR: **https://github.com/benkirk/hpc-usage-queries/pull/100** (branch `jobs_plugin_enhancements`, base `main`).

- `jobs_histogram(..., owners_limit=N)` → per-bucket `owners: {username: {job_count, cpu_hours, gpu_hours}}`, top-N by combined hours, appended key, totals authoritative (remainder = totals − Σ owners). Default `None` = byte-identical envelope.
- `jobs_usage_by(..., sort_by='hours'|'cpu_hours'|'gpu_hours'|'job_count')` — ranks before `limit` truncation (fixes issue 4). Totals stay pre-truncation.
- `jobs_search` `sort_by` on `user`/`account`/`queue`/`qos` → OUTER-join lookup + ORDER BY name column (kills the 10× correlated-subquery path); `_SORT_LOOKUP_JOINS` table.
- 178/178 in `job_history/tests/test_jobs_search.py` (15 new). fs_scans facade failures are a pre-existing live-Postgres env issue.

**Gate to Phase B: Ben merges PR #100, then rebuild picks up plugin `main`** (SAM's dependency is an unpinned git ref; webdev container + conda-env rebuild).

## Phase B — SAM landing (not started)

Full spec lives in the approved plan (session plan file); summary:

- **B1 charts.py**: `_jobs_bucket_segments(bucket, key)` (ascending owner values + remainder-first segment); `generate_jobs_histogram` stacks with `_shade_family` per-bucket `UNITY_STACK_10` colors when owners present, byte-identical flat path when absent; sentinels `#jh-bar-<i>` on every segment iff `job_count`; cache key gains `(label, value, segments)` payload.
- **B2 jobs_histogram.html**: drop `<details>`; bucket rows clickable; per-bucket collapse row → server-rendered owner table (ranked by active metric `_mkey`, rank/User/Jobs/CPU-h/GPU-h/% of bucket, "Other users" remainder row) → per-user collapse rows lazy-loading jobs via `user_jobs_drill(dt_id, uname, drill_url)` macro (`&user=<urlencoded>`, `hx-trigger="shown.bs.collapse from:closest tr.collapse once"`); single-owner shortcut; owners-absent fallback keeps today's markup **verbatim** (drill-URL regression tests regex it); `data-no-persist` on all histogram collapse rows (nav-view-persistence would auto-refetch).
- **B3 svg-chart-links.js**: fold `openBucketRow` + `openJobsBucketRow` into `openEntityRow('data-ah-bucket'|'data-jh-bucket', idx, pane)`.
- **B4 routes.py + jobs_fragment.html**: `'user'` second in `_DEFAULT_COLS`, out of `_VERBOSE_EXTRAS`; `_user_col_suppressed(pinned_user, filters, rows, total, per_page)` — pin / `user=` filter / single-page-uniform; uniform case emits `user_badge`; name td gets `text-truncate` + `style="max-width: 35ch"`.
- **B5 service.py/routes.py**: thread `sort_by` (`_USAGE_SORT_BY` map) into `jobs_usage_by_user/_project` + cache opts; `owners_limit=_HIST_OWNERS_LIMIT` (10) into `service.jobs_histogram` + opts; by-user/by-project initial sort indicator follows metric.
- **B6 tests**: per plan — stacked/flat chart tests, owner-table render, drill `user=` param, suppression matrix, `sort_by` threading, cache-key coverage. `test_jobs_fragment_renders_rows_when_enabled` passes via `user_badge` (update stale comment).

Deploy note: SAM will always send `owners_limit` → old plugin TypeErrors. Lockstep ship (atime_recursive precedent); document in the SAM PR.
