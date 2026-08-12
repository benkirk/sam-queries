# Job History follow-ups — round 2 (memory dims, bar drill, facet chips, By-Project, elapsed/reqmem inputs)

**Status (2026-07-27): IMPLEMENTED & MERGED upstream** — plugin P1 pushed
as `e07238f` (PR #99); review fix `dcb177f` closed the cpus/nodes bucket
tables at the domain floor (leading `("0", 0, 0)` bands — sub-floor rows
were mis-filed into the "1" band, breaking the bar↔drill round-trip);
PR #99 then merged through hpc-usage-queries `main`. SAM S0–S6 on
`job_history_followups` (PR #382, rebased onto staging after #381
merged). Full suite 3,257 green (+ CI emulation with `CACHE_REDIS_URL`);
plugin suite 565→568. Playwright smoke passed. As-built deltas from the
plan below:

- **S1**: the per-job table's inline filter parse was UNIFIED onto
  `_parse_job_filters(include_user=…)` (the plan's "consider unifying"
  taken); native bounds parse after human-unit forms, so native wins on
  a double-spelled bound.
- **S3**: drill URLs are computed server-side in `_bucket_drill_url` as
  a parallel `bucket_drills` list (the envelope is a shared cache entry
  and must not be mutated); a same-named pane bound is dropped in favor
  of the clicked band's. The chart cache key gained the job_count-
  positivity vector (clickability follows job_count even on an hours
  metric).
- **S4**: the chip strip is rendered by the jobs fragment as an
  `hx-swap-oob` block (gated by `?chips=1`, sent only by the explorer)
  rather than by the explorer routes — a statically rendered strip
  would go stale after the first table refetch. `chips` rides
  `_ROUNDTRIP_KEYS`, the panel form, and `_initial_jobs_url`.
- **S5**: `jobs_usage_by_project` raises on empty username or a `user`
  filter (the strict user-family rule, not pin-overwrites); user-mode
  `account` narrowing surfaces as a "project:" header badge injected
  post-service.

## Context

Round 1 is complete: SAM **PR #381** (`job_history_expansion` → staging) is open with CI
green; plugin **PR #99** (`jobs_plugin_search_drilldown`, tip `24a35ed`) awaits Ben's
review. Merge order stays plugin → SAM. This round executes deferred items 1–5 from
`docs/plans/implemented/JOB_HISTORY_DASHBOARD.md` as: **additional commits on PR #99** (memory
dimensions) + **ONE stacked SAM PR** on top of #381. Item 6 (RedisTTLAdapter shared
`'usage:'` prefix for fs_scans/usage caches) is deliberately excluded — its own PR later.

**Window-policy decision (measured 2026-07-27, warm cache, webdev PG):**

| Window | Rows (der/cas) | count | histogram |
|---|---|---|---|
| 30 d | 0.36M / 0.56M | 0.17–0.22 s | 1.7–2.8 s |
| 365 d | 4.7M / 10.9M | 7.9–13.6 s | 4.4–8.2 s |
| unbounded | 13M / 21M | 1.7–2.3 s | 5.5–9.1 s |

Cost driver is rows-touched × cache temperature (btree(`end`) range scan + per-row
job_charges join), NOT boundedness — unbounded count beats the year count because the
planner flips to a seq/index-only scan. The historical "~200 s" was cold page cache.
**Policy: keep the 90-day defaults everywhere, no hard cap; the 60 s statement timeout +
jobs TTL cache guard the tail.** Cold whole-history first attempts may hit the timeout;
each attempt warms PG buffers so retries converge (raise JOB_HISTORY_STATEMENT_TIMEOUT_MS
if this bites in practice). Facet chips therefore compute within the current window.

**Decisions (Ben, 2026-07-27):**
1. memory_used + memory_wasted → **Job Sizes pills on BOTH machines** (coverage is ~99.3%
   on both, last 90 d); Derecho gets a caption noting whole-node reqmem inflates "wasted"
   (median 176 GiB vs Casper's meaningful 19 GiB).
2. Bar drill = **inline bucket-row expansion** (mirror the disk band-drill / openBucketRow
   pattern), not a jump to the explorer.
3. Facet chips **with live counts** (self-exclusion on; jobs_facets ≈150 ms/month-window).
4. memory_wasted = `reqmem − memory` (bytes); 0.3–0.4% of jobs have used > requested →
   explicit **"over request" band** (hi = −1) leads the bucket table.

## How to resume (post-compact)

- SAM: branch **`job_history_followups`** off `job_history_expansion`. Commit THIS plan as
  `docs/plans/implemented/JOB_HISTORY_FOLLOWUPS.md` first (house convention).
- Plugin: `~/codes/hpc-usage-queries/devel`, branch `jobs_plugin_search_drilldown` (PR #99).
  Plugin tests: `./conda-env/bin/python -m pytest job_history/tests/` (peer repo).
- After pushing plugin commits, re-pin BOTH builds to the new tip:
  `HPC_USAGE_QUERIES_REF=<sha> docker compose build webdev` and
  `HPC_USAGE_QUERIES_REF=<sha> source etc/config_env.sh` (Claude runs these; pause for Ben
  only on failure). Record the new sha in PR #99 comment + the SAM plan doc.
- SAM tests: mysql-test on :3307, `export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'`;
  pytest directly authorized in both repos. Before pushing, run the CI-emulation sweep:
  full `pytest` with `CACHE_REDIS_URL='redis://127.0.0.1:6379/0'` AND, if feasible, under a
  main-ref conda env — **SAM tests must stay hermetic to plugin version**
  (`_FAKE_COLUMN_SPECS` / `_install_mock_plugin` patterns; CI builds plugin@main until #99
  merges).
- Playwright smoke on webdev :5050 (Quick Login: benkirk operator, bdobbins plain,
  sureshm WNA-scoped). Live data: SCSG0001 × Derecho; queues are cpu/gpu/cpudev (NOT main).

## Plugin side — commits on PR #99

### Commit P1 — memory_used + memory_wasted histogram dimensions + filters
Files: `job_history/queries/jobs.py`, `job_history/cli/cmds/jobhist.py`,
`job_history/cli/search/commands.py`, `job_history/tests/test_jobs_search.py`,
`test_cli_search.py`, README/plan-doc.

- **Filters** (bounds-round-trip invariant forces these): `min/max_memory_used`
  (`Job.memory`, bytes) and `min/max_memory_wasted` (`Job.reqmem - Job.memory` expression,
  bytes; NULL-strict — NULL when either column NULL; negatives legal) as new rows in
  `_apply_jobs_search_filters`'s range-bounds table + kwargs/docstrings on `jobs_search` /
  `jobs_count` / `jobs_facets` / `jobs_histogram` / `jobs_usage_by`
  (TestFilterSignatureParity enforces completeness).
- **Buckets** on `QueryConfig`: `MEMUSED_HIST_BUCKETS` (reuse the REQMEM GiB band
  boundaries `<1GB…>1000GB`); `MEMWASTED_HIST_BUCKETS` = `('over request', None, -1)` then
  the same ascending GiB bands (`_bucket_case`'s NULL-first + ascending `<= hi` ladder
  handles the negative band naturally).
- **`_HISTOGRAM_SPECS`** += `'memory_used'` → (Job.memory, …, 'bytes',
  'min_memory_used', 'max_memory_used') and `'memory_wasted'` → (reqmem−memory
  expression, …). `_bucket_case` takes SQLAlchemy expressions as-is.
- **CLI flags** (consistency with 06a8657): `--min/max-memory-used-gb`,
  `--min/max-memory-wasted-gb` (GB→bytes via `_BYTES_PER_GB`; wasted accepts negatives)
  + envelope `filters` keys.
- **Tests**: over-request routing into the negative band, either-NULL → null_count,
  zero-fill vector, existing bounds-round-trip invariant loop picks the new specs up
  automatically (verify), one-aggregate-scan guard, CLI conversions, parity-set updates.
- Push; do NOT merge — Ben reviews. New tip sha replaces `24a35ed` in all pins.
  **DONE 2026-07-27: tip is `e07238f5f77317ad36d67f111b92e934eae07528`**
  (suite 546 → 565; dev-PG verified: over-request band == count(max=-1),
  887 derecho / 2,566 casper).

## SAM side — stacked PR (`job_history_followups`, base `job_history_expansion`)

Retarget the PR base to `staging` after #381 merges (stacked-PR reopen recipe if the
parent merge closes it).

### Commit S0 — commit this plan as `docs/plans/implemented/JOB_HISTORY_FOLLOWUPS.md` `[skip ci]`

### Commit S1 — native-unit filter passthrough + memory dimension pills
- `jobs/routes.py`: `_parse_job_filters` (and `_jobs_table_response`'s inline parse —
  consider unifying them here) accept the **plugin-native** bound params the bar drill
  emits verbatim from the envelope: `min/max_eligible_secs`, `min/max_elapsed`,
  `min/max_reqmem`, `min/max_memory_used`, `min/max_memory_wasted` (ints; wasted may be
  negative — don't clamp to ≥0 for that pair). `_ROUNDTRIP_KEYS` += the native names.
  Human-unit inputs (wait-hours etc.) keep converting at the boundary as today.
- `_SIZE_DIMENSIONS` → `('nodes','cpus','gpus','memory','memory_used','memory_wasted')`;
  pill labels in `jobs_histogram.html`: Nodes / CPUs / GPUs / Req mem / Used mem / Wasted.
- Derecho caption in `jobs_histogram.html`: when `machine == 'derecho'` and
  `dimension == 'memory_wasted'` → note that whole-node scheduling assigns ~235 GB/node
  regardless of request, inflating "wasted" (Casper's shared nodes are the meaningful case).
- Update the pinned sha note in `docs/plans/implemented/JOB_HISTORY_DASHBOARD.md`.
  `_FAKE_COLUMN_SPECS` needs no change (COLUMNS untouched upstream).

### Commit S2 — explorer elapsed/reqmem inputs (item 5)
- `partials/_jobs_filters.html`: two more min/max pairs — "Elapsed (hours)" (float,
  ×3600 → `min/max_elapsed`) and "Req memory (GB)" (float, ×1024³ → `min/max_reqmem`;
  GB label matches the bucket labels, GiB internally). Add `min/max_elapsed_hours`,
  `min/max_reqmem_gb` to `_parse_job_filters` (human units), `_panel_filters`,
  `_initial_jobs_url`, `_ROUNDTRIP_KEYS`.

### Commit S3 — clickable histogram bars → inline bucket drill (item 3)
- `charts.py generate_jobs_histogram`: bars with `job_count > 0` get
  `set_url(f'#jh-bar-{i}')` (index-keyed; cache key already hashes the count vector).
- `jobs_histogram.html`: Bucket-counts rows become expandable — `tr[data-jh-bucket="i"]`
  + `data-bs-target` collapse row lazy-loading the **mode's jobs fragment** with
  `{min_param: lo, max_param: hi}` from the envelope (omit min when `lo` is None/negative
  band's open end, omit max when `hi` is None) + the pane's roundtrip params + machine +
  target_id; bind `shown.bs.collapse … once`.
- `svg-chart-links.js`: `#jh-bar-` prefix → open the matching `data-jh-bucket` row scoped
  to `.tab-pane` (parameterize `openBucketRow`'s attr or add a sibling fn), forcing the
  enclosing `<details>` open first before scrolling.
- Works uniformly across Wait Times / Job Sizes (all 6 dims) / Durations because S1's
  native passthrough accepts every envelope's `min_param`/`max_param`.

### Commit S4 — facet chips in the explorer (item 1)
- `jobs/service.py`: cached `jobs_facets(machine, *, facets=('queue','qos','exit_status'),
  account_projcodes=None, username=None, **filters)` wrapper (plugin `jobs_facets`,
  self_exclude default True) through `jobs_cache.cached_jobs_aggregation('facets', …)`
  with `bucket_for_window`.
- Explorer routes (`explore_page` / `explore_machine_page` / `explore_user_page`): fetch
  facets for the current window+filters (scope-pinned per mode); degrade to no chips on
  any error.
- `jobs_explore_page.html` (or a small `_jobs_facet_chips.html` partial): chip strip
  above the table — value + count per dim; click sets the matching panel form field and
  re-submits. CSP-clean: a `data-action="set-filter-submit"` handler in
  `static/js/actions.js` (writes the value into the named input of the panel form, then
  `requestSubmit()`); active chip renders highlighted + acts as clear.
- Cap values per dim (limit≈8) with the existing plugin `limit` param.

### Commit S5 — By-Project tab in user mode (item 2)
- `jobs/service.py`: `jobs_usage_by_project(machine, *, username, limit=25, **filters)` →
  plugin `jobs_usage_by('account', user=username, …)`, cached
  (`'usage_by_account'` query type). Username pin mandatory (mirror
  `jobs_usage_by_user`'s pin-overwrites rule).
- `search_jobs_user` / `count_jobs_user` accept an optional `account` narrowing filter
  (safe: the user pin still applies; this narrows one's OWN jobs). `_jobs_table_response`
  user branch parses a whitelisted `account` arg (project/machine modes unchanged —
  project mode's account remains server-derived only).
- `charts.py`: generalize the pie —
  `generate_jobs_usage_pie_chart(entity_data, metric, *, sentinel_prefix, unknown_label)`
  with the existing user pie delegating to it; **sentinel_prefix must join the cache
  key**. `#job-proj-<projcode>` sentinels.
- New route `/user/<machine>/by-project` (`@login_required`, pinned) + tab in
  `jobs_card.html` (user mode only, where By User is hidden); `jobs_by_project.html`
  clone of `jobs_by_user.html` with `tr[data-job-project=…]` rows drilling into the
  user-mode jobs fragment with `&account=<projcode>`; svg-chart-links.js `#job-proj-` →
  `openEntityRow('data-job-project', …)`.
- No route-map regen needed (jobs bp is not pinned; no new page routes) — verify parity
  test stays green.

### Commit S6 — docs + follow-ups ledger
- `JOB_HISTORY_DASHBOARD.md`: flip items 1–5 to done, keep item 6 (Redis prefix
  migration) as the open follow-up; `JOB_HISTORY_FOLLOWUPS.md` as-built notes;
  `src/webapp/README.md` touch-up if warranted.

## Test inventory

- **Plugin**: histogram fixture rows for memory (incl. used>requested and either-NULL);
  negative-band routing; round-trip invariant auto-covers new dims; CLI conversion tests;
  parity sets. Suite was 546 — expect ~560+.
- **SAM** (`test_webapp_jobs.py` + charts/cache files, all hermetic via mock plugin +
  `_FAKE_COLUMN_SPECS`): native param passthrough (incl. negative wasted bounds);
  6 sizes pills + Derecho wasted caption; bucket rows carry correct min/max drill params
  (None-end omission both directions); chips render with counts / click wiring / degrade
  on facets error; `jobs_usage_by_project` pin + `account` narrowing in user mode
  (`?user=` still ignored); explorer elapsed/reqmem inputs round-trip; pie generalization
  sentinel prefixes (incl. distinct cache keys); cache query types 'facets' /
  'usage_by_account'.
- Full `pytest` in both repos; SAM CI-emulation run with `CACHE_REDIS_URL` set before push.

## Verification

1. Plugin: suite green; quick timed check of both new dimensions on dev PG (month window).
2. Rebuild webdev + conda env at the new pin; verify `jobs_histogram('memory_wasted', …)`
   from the container.
3. SAM suites per commit; full sweep + CI emulation before push.
4. Playwright smoke (webdev :5050): 6 sizes pills switch (Derecho shows wasted caption;
   Casper doesn't); bar click on Wait Times / Sizes / Durations opens the matching bucket
   row with a correctly filtered jobs table (spot-check a count matches the bucket);
   explorer chips show counts, click filters, active chip clears; elapsed/reqmem panel
   fields round-trip through a deep link; My Jobs → By Project pie + wedge → row drill →
   account-narrowed own-jobs table; `?user=` override still ignored; quick RBAC spot-check
   (bdobbins: no machine surfaces).
5. Open the stacked PR (`--base job_history_expansion`); note in the body: retarget to
   staging after #381 merges; plugin PR #99 (new tip) merges before either.

## Out of scope (this round)
- Item 6: migrating fs_scans/usage caches off the shared `'usage:'` Redis prefix —
  separate PR after this lands (cutover orphans existing keys until TTL).
- `jobs_facets` name-glob facets, memory charging, By-Project outside user mode.
