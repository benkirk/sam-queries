# Job-history charts in the full-view explorer

> **Status (2026-07-28): IMPLEMENTED.** Branch `job_drilldown_plots`,
> commits C1–C8 below plus one fix the browser smoke turned up. Full
> suite 3,429 passed / 30 skipped, and again under CI emulation with
> `CACHE_REDIS_URL`. As-built deltas from the plan:
>
> - **C2**: `panel_trigger` was dropped from the design. An `intersect`
>   trigger on a *tab button* is useless — the buttons are always
>   visible, so all six would have fired at once. Lazy loading stays on
>   `shown.bs.tab once`, and the open tab owns `load_trigger`.
> - **Risk 1 happened, and cost more than predicted.** Apply from a chart
>   tab fetched the chart *and* a per-job table nobody asked for (16.9 s
>   measured, machine-wide Casper). The documented contingency shipped:
>   the filter form round-trips `active_tab`, whitelisted server-side.
>   Verified in the network panel — one panel request per Apply.
> - **C5**: `account` was deliberately left out of the explorer's panel
>   params (it narrows the table but not the aggregations, so baking it
>   in would hide By Project while its neighbours still counted every
>   project). So the plan's "explorer with `account=`" row of the
>   relevance table describes the card's drill path, not the explorer.
>   Likewise `user` is dropped from user-mode panel URLs: pinned
>   server-side, so carrying it would show a parameter that looks like a
>   filter and isn't.
> - One test assertion pinned query-param *adjacency*
>   (`?machine=derecho&start=…`); it now parses the panel URLs, since
>   order is `url_for`'s business.

## Context

The job-history card ships six segments — Jobs · By User · By Project · Wait
Times · Job Sizes · Durations — on three hosts (resource-details, Status,
My Jobs). The standalone **explorer** (`/dashboards/user/jobs/machine/<machine>/explore?days=90`
and its project/user siblings) has a far richer filter panel than any card,
but shows **only the per-job table**. That asymmetry was inherited from
fs_scans, where every drill click re-runs a 30–120 s on-the-fly scan and
charting each one was untenable. Job history does not have that property: its
aggregations are one bounded PG statement each (~1.7–2.8 s for a 30-day window,
4.4–8.2 s for a year, per the measured table in `JOB_HISTORY_FOLLOWUPS.md`),
they are already TTL-cached, and the resulting SVGs are content-addressed. So
the charts belong on the page where the filters live.

Outcome: the explorer becomes the card — same six tabs, driven by the full
filter panel instead of a baked 90-day window — the panel set adapts to the
scope so no pie-of-one is ever rendered, and the caches are retuned for the
wider filter fan-out an explorer produces.

**What already exists (no new query/service/plugin work):**

- `_parse_job_filters()` (`src/webapp/jobs/routes.py:602`) is the single
  filter parse shared by the table *and* every aggregation fragment; it
  already understands every explorer field, in both display units
  (`min_wait_hours`, `min_reqmem_gb`) and plugin-native units.
- `_ROUNDTRIP_KEYS` (`:549`) already lists every explorer filter, so a panel
  loaded with them round-trips them through its own metric/dimension/log pills.
- `_render_by_user` (`:757`), `_render_by_project` (`:807`) and
  `_render_histogram` (`:921`) already take an arbitrary `fragment_url` +
  `target_id`. Nothing in them is card-specific.
- The period pills already prove the update mechanism: a pill re-renders the
  **whole card shell** via `jobs.jobs_card*_fragment` → `_render_card_shell`
  (`:1573`), because each panel bakes its window into its own `hx-get` at
  render time. A filter "Apply" is that same operation with a bigger param set.

## Decisions (settled with Ben, 2026-07-28)

1. **The card owns the explorer page.** Lift all six tabs in; the standalone
   table container goes away because the card's Jobs tab *is* that table
   (same fragment, same chips/sort/pagination). No duplicate table, one htmx
   request per Apply, and — because panels are lazy — a plain "look at the
   table" visit costs exactly what it costs today.
2. **Cache**: `JOBS_CACHE_TTL` 6 h → 30 min; `JOBS_RECENT_CACHE_TTL` stays at
   15 min; both LRU sizes 256/128 → 512. Chart SVG TTL untouched — those keys
   are content hashes (`_jobs_histogram_cache_key`, `charts.py:1208`), so a
   longer chart TTL cannot serve stale data; the aggregation TTL is the only
   freshness lever. Redis `maxmemory` gets a matching bump (§C7).
3. **`jobs_card.html` becomes a Jinja macro** with named args before the
   feature is built on it. It is currently an `{% include %}` reading ~10
   loose `{% set %}` names from four hosts, and this change would add more.
4. **Empty bands are trimmed at both edges, never in the interior** (§C3).
5. **Panel relevance becomes a rule, not per-mode special cases** (§C4) — a
   panel or an owner axis is shown only when the scope can actually vary
   along it.

## Commit series

### C1 — `jobs_card.html` → `{% macro jobs_card(...) %}` (mechanical, UI-identical)

- `src/webapp/templates/dashboards/user/partials/jobs_card.html`: wrap the
  body in `{% macro jobs_card(mode, cid, tablist_id, machine, load_trigger,
  projcode=none, scope=none, start=none, end=none, days=none,
  days_persist_id=none, …) %}`. The existing
  `{% set x = x | default(none) %}` preamble disappears — that is what macro
  defaults are for.
- New `partials/jobs_card_shell.html`: a few lines that import the macro and
  call it with the context `_render_card_shell` passes, so `render_template`
  keeps working from the `/card` routes.
- Update the four hosts to `{% from … import jobs_card %}` + `{{ jobs_card(…) }}`:
  `resource_details.html`, `partials/my_jobs_card.html`,
  `dashboards/status/partials/job_history.html`, and `_render_card_shell`.
- Rendered HTML must be byte-identical. `tests/unit/test_webapp_jobs.py`
  card-shell assertions are the guard.

### C2 — generalize the card's panel URLs

The card currently builds each panel URL as
`url_for('jobs.<panel>_fragment', machine=…, start=…, end=…, target_id=…)`.
Replace the fixed `start`/`end` pair with a merged dict:

- New macro args `panel_params={}` (merged into **every** panel `url_for`) and
  `jobs_params=none` (Jobs tab only — `per_page`, and anything else that
  shapes the table but not an aggregation).
- New display flags `show_pills=true`, `show_explore_link=true`.
  (As built, lazy loading stays on `shown.bs.tab once` for every tab
  but the open one, which owns `load_trigger` — see `active_tab` in
  the status note above. `intersect` on a tab *button* would have
  fired all six at once, since the buttons are always visible.)
- `_render_card_shell` splits into `_card_context(**kwargs) -> dict` + the
  render. `days` becomes optional: when `?days=` is present it wins and
  normalizes to `start=_days_start(days), end=None` exactly as today; when it
  is absent, explicit `start`/`end` are honoured (today the route forces
  `days = _parse_days() or DEFAULT_JOBS_WINDOW_DAYS`).
- Card hosts pass `panel_params={'start': start, 'end': end}` → identical
  output to C1.
- **Fixes a latent bug**: project mode never put `scope` in its panel URLs, so
  a re-rooted subtree would have been ignored by the aggregations. It has never
  bitten because resource-details sends no `?scope=`; the explorer does.
  `_tree_projcodes` → `_scope_project` (`routes.py:733, 741`) reads it from
  `request.args`, so routing it through `panel_params` is the whole fix.

### C3 — symmetric empty-band trimming

`_trim_leading_empty_bands` (`routes.py:892`) already drops leading all-zero
bands, and already encodes the two invariants a trailing trim needs: emptiness
is judged on `job_count` alone (so flipping the Jobs / CPU-hours / GPU-hours
pill can't shift the axis under the viewer), and it returns a shallow copy
because the envelope is a shared cache entry. Generalize it to
**`_trim_empty_edge_bands(hist)`**, trimming leading *and* trailing runs of
zero-count bands while preserving interior zeros.

The trailing zeros are structural, not a filter artifact: `CPU_HIST_BUCKETS`
runs to `>32768` and `NODE_HIST_BUCKETS` / `GPU_HIST_BUCKETS` / the GiB memory
bands are sized for the largest machine the plugin serves. Casper's few hundred
nodes mean its top node/CPU/GPU/memory bands can never fill — so today every
Job Sizes pane on Casper spends a third of its x-axis on bands that are zero by
construction.

Rules, exactly:

| Case | Treatment |
|---|---|
| Leading run of zeros | trimmed (today's behaviour, unchanged) |
| Trailing run of zeros | **trimmed** (new) |
| Interior zeros | **kept** — a gap inside the distribution is a finding |
| Every band zero | no trim; the panel renders an empty state (below) |
| Exactly one populated band | trims to one bar; the `_single` owner shortcut already drills straight to jobs |

Call site stays the single one, **before** both `generate_jobs_histogram` and
the `bucket_drills` list — the `#jh-bar-<i>` sentinels, `data-jh-bucket` row
indices and the drill list are three consumers of one index space and must all
see the trimmed vector. `_jobs_histogram_cache_key` hashes bucket labels and
values, so a differently-trimmed vector keys differently on its own; no cache
change needed.

**"Never show a histogram that is uniformly 0"** needs a second fix beyond the
trim. Today an all-zero window returns `hist` untrimmed, and while
`generate_jobs_histogram` swaps in its `_empty_state` placeholder, the template
still renders the full bucket-breakdown table — a dozen rows of zeros under a
"no jobs" box. In `_render_histogram`, detect no populated band and route to
the empty branch instead, distinguishing the two reasons:

- `total_count == 0` → "No jobs match these filters."
- `total_count > 0` and `null_count == total_count` → every matching job is
  unmeasured on this dimension (Derecho waits before 2025-01-07 is the real
  case). Reuse the existing `null_count` caption wording rather than claiming
  there are no jobs — there are, they just have no value here.

**Tradeoff recorded in the docstring**, because it partly reverses a documented
intent: the plugin returns a complete zero-filled vector precisely so the
x-axis stays stable as filters change (`generate_jobs_histogram` docstring).
Trimming both edges trades some of that stability for legibility — two panes
side by side (Derecho vs Casper subtabs, or before/after a filter change) can
now have different axes. Interior zeros are preserved specifically so the shape
*within* the populated range stays comparable. The open-ended overflow band
(`hi=None`) is trimmable like any other; when it goes, the new last band's
drill URL uses its own finite `max_param`, so drills stay exact.
`memory_wasted`'s leading `('over request', None, -1)` band trims like any
other, which is the common case (0.3–0.4% of jobs land there).

### C4 — panel relevance rules (suppress pies-of-one everywhere)

Today relevance is three unrelated special cases: `mode != 'user'` gates By
User in the template; `jobs_multi_project` gates By Project and is computed
independently in `jobs_card_fragment` and in `dashboards/user/blueprint.py`
(a **third** copy of the tree expansion, via `_resolve_scope_projcodes`); and
`_render_histogram` has its own inline `owners_toggle` expression. The explorer
breaks all three, because its filters can pin a single user or a single project
in any mode.

One helper in `routes.py`, derived only from statically-known pins (never from
query results):

```python
def _panel_relevance(*, mode, user_filter, account_filter, account_projcodes):
    """Which panels and owner axes can actually vary in this scope."""
    user_pinned    = (mode == 'user') or bool(user_filter)
    project_pinned = bool(account_filter) or (
        account_projcodes is not None and len(account_projcodes) <= 1)
    return {
        'show_by_user':     not user_pinned,
        'show_by_project':  not project_pinned,
        'owners_toggle':    not user_pinned and not project_pinned,
        'default_group_by': 'project' if user_pinned else 'user',
        'owners_enabled':   not (user_pinned and project_pinned),
    }
```

`account_projcodes` is `_tree_projcodes(project)` in project mode (already
scope-aware), `None` (= all projects) in machine mode, and `None` in user mode
unless `account=` narrows it.

Consumers — the point is that **one** call feeds both the tab strip and the
panel internals, so they cannot disagree:

- `_card_context` / `_explorer_card_context` → `show_by_user` /
  `show_by_project` macro args, replacing `mode != 'user'` and
  `jobs_multi_project`.
- `_render_histogram` → `owners_toggle`, the default `group_by`, and
  `owners_enabled`; when `owners_enabled` is False, pass `owners_limit=None`
  so the plugin skips the owner grouping entirely and
  `generate_jobs_histogram` renders its flat single-series bars (the
  documented owner-less fallback), with band rows drilling straight to jobs.
- `dashboards/user/blueprint.py` drops `_resolve_scope_projcodes` in favour of
  the shared expansion, retiring the third copy.

Behaviour this fixes:

| Scope | Today | After |
|---|---|---|
| My Jobs (any host) | histograms stack by **user** — one segment, the viewer; per-band tier is a one-row table saying "you" | stack by **project**: which of my projects owns each band |
| Explorer with `user=<someone>` | By User tab renders a pie of one; histograms stack by that one user | By User hidden; histograms stack by project |
| Explorer / drill with `account=<projcode>` | By Project renders a pie of one | By Project hidden; histograms stack by user |
| My Jobs → By Project row drill (`account=` **and** user pinned) | single-segment stack over a one-row tier | flat bars, band → jobs in one click |
| Project mode, `?scope=<leaf>` | correct already (`_tree_projcodes` is scope-aware) | unchanged |

Crafted URLs stay harmless rather than 403: an irrelevant `group_by` is
ignored exactly as `_render_histogram` already documents, and the By User /
By Project routes still render if hit directly — they are simply not linked.
Suppressing the tab a viewer was last on is safe: `nav-view-persistence.js`
already no-ops when the saved pane is absent ("saved tab no longer in DOM —
leave default active"), falling back to Jobs.

### C5 — the explorer adopts the card

- `src/webapp/jobs/routes.py`: one shared `_explorer_card_context(...)` used by
  **both** `explore_page`, `explore_machine_page`, `explore_user_page` **and**
  the three `/card` routes when called with `surface=explorer`. It turns
  `_panel_filters()` into `panel_params` (display units, empty values dropped)
  + `jobs_params` (the table's `per_page`), folds in `panel_relevance`, and sets
  `show_pills=False`, `show_explore_link=False`,
  `panel_trigger='intersect once'`, `days_persist_id=None`.
  - `show_pills=False` because the panel's own date fields own the window on
    this page — the same reasoning that makes resource-details opt out of
    `days_persist_id`. `?days=` on the URL keeps working: `_panel_filters`
    already folds it into `start`.
  - `tablist_id='jobsExploreTabs'`, distinct from the cards' `jobsCardTabs`, so
    the explorer's tab choice does not fight resource-details'. The
    `data-chart-persist-shared` lens (`group_by metric:jobs dimension:jobs
    log:jobs`) is app-wide **on purpose** and stays shared.
- `templates/dashboards/user/jobs_explore_page.html`: delete the
  `<div class="card">` + `#{{ target_id }}` container and render the card macro
  in its place. `target_id` on this page now means the *table's* container id
  and becomes `cid ~ '-jobs'` (e.g. `jobs-explore-jobs`), because
  `jobs_fragment.html` derives both the chip placeholder id and the panel form
  id from it. Derive all three from one variable.
- `partials/_jobs_filters.html`: the macro's `fragment_url`/`target_id` pair
  becomes `submit_url` / `submit_target` / `submit_swap` so the form posts to
  the card-shell route with `hx-swap="outerHTML"`. The hidden `target_id`
  input still carries the *table's* id (the fragment needs it for its own
  sort/pagination URLs). Add hidden `surface=explorer`.
- **Do not** put `hx-target`/`hx-swap` on the card wrapper. htmx inherits both,
  and a pair there hijacks every descendant request that doesn't name its own
  target — the trap documented in `jobs_card.html` and in
  `nav-view-persistence.js`. The form outside the card owns the swap.
- Sort and pagination are unaffected: they target `#<table id>` and
  `hx-include="#jobs-filters-<table id>"` (the fragment's own roundtrip form),
  so paging never re-renders the shell.

### C6 — facet chips move from the table fragment to the card shell

Today the chip strip is an `hx-swap-oob` block emitted by the table fragment
when `chips=1`. With the card in place, a filter change while sitting on a
chart tab would not re-fetch the table, so the chips would show counts for the
previous filter set.

- Compute `service.jobs_facets(...)` in `_explorer_card_context` and render
  `_jobs_facet_chips.html` inside the card shell, above the tab strip. Degrade
  to no chips on any facets error, as today.
- Drop the OOB block from `jobs_fragment.html`, the facets call from
  `_jobs_table_response`, `chips` from `_ROUNDTRIP_KEYS` and
  `_initial_jobs_url`, and the hidden `chips` input from `_jobs_filters.html`.
- Strictly more correct than the OOB version: chips now refresh exactly when
  the filters change, and no longer on a sort or page click that cannot have
  changed them. Chip clicks keep working unchanged —
  `data-action="set-filter-submit"` (`static/js/actions.js`) writes into the
  panel form and calls `requestSubmit()`, which now re-renders the shell.

### C7 — cache retune + Redis headroom

`src/webapp/jobs/cache.py` `_BUCKETS` + module docstring:

| Key | Was | Now |
|---|---|---|
| `JOBS_CACHE_TTL` (closed windows, `end < today`) | 21600 (6 h) | **1800 (30 min)** |
| `JOBS_CACHE_SIZE` | 256 | **512** |
| `JOBS_RECENT_CACHE_TTL` (open / touches today) | 900 (15 min) | 900 |
| `JOBS_RECENT_CACHE_SIZE` | 128 | **512** |

Sizes matter because an explorer fans out far more distinct aggregation keys
than the cards did: per filter combination, up to 8 histogram dimensions × 2
`owners_by` values + 2 usage rollups × 3 `sort_by` values ≈ 22 entries. At 128
that is roughly six filter combinations before eviction. (Under Redis,
eviction is instance-global `allkeys-lru` and `maxsize` is advisory; the bump
is load-bearing for the in-process fallback used in local dev and any
Redis-less deploy.) No `config.py` change needed — `_get_config` reads app
config, then env, then the default; Admin → Configuration picks the new numbers
up via `jobs_cache_info()`.

**Redis sizing.** `helm/values.yaml` and `compose.yaml` both pinned
`maxmemory 64mb` (helm container `limits.memory: 96Mi`, `requests: 32Mi`).
Measured on the local cache while planning: one cached jobs **pie** SVG is
**49 KB** (`chart:jobs_usage_pie_chart:*` → `MEMORY USAGE`); owner-stacked
histograms carry many more artists and run larger. One instance holds all 13
chart caches, both jobs aggregation buckets, both fs-scans buckets, the
allocation usage cache, and the whole Flask-Caching layer — and `allkeys-lru`
is **instance-global**, so chart churn is eligible to evict the rate limiter's
DB 1 counters. Resident bytes are governed by TTL × arrival rate, and this
change cuts aggregation dwell (6 h → 30 min) while raising chart-key
cardinality, so headroom is the safe direction.

`cache.maxmemoryMB: 64 → 192`, `limits.memory: 96Mi → 320Mi`,
`requests.memory: 32Mi → 64Mi`, and `--maxmemory 192mb` in `compose.yaml` for
dev parity. **Confirm against prod before the main promotion** — `kubectl exec`
the redis pod and read `INFO memory` (`used_memory_peak_human`) and
`INFO stats` (`evicted_keys`). If prod peak is still far under 64 MB with zero
evictions, 128 MB is enough; the local instance is idle and is not evidence
either way.

### C8 — docs

- This file, annotated to as-built.
- `JOB_HISTORY_DASHBOARD.md` § Deferred follow-ups: record this round; refresh
  the cache TTL numbers it quotes.
- `implemented/REDIS.md` / `helm/README.md`: note the new `maxmemoryMB`.
- Fix a pre-existing doc drift found while mapping this: the `jobs` cache
  category is missing from the category list in `CLAUDE.md` and
  `docs/apis/SYSTEMS_INTEGRATION_APIs.md`, though `caching/__init__.py` and
  `api/v1/admin.py` have supported it since the job-history rounds.

## Explicitly not in scope

- No plugin (`hpc-usage-queries`) change — nothing new is asked of it.
- No new routes. The three `/card` routes absorb `surface=explorer`; the
  aggregation fragments are reused verbatim, with their existing gates
  (`@require_project_access` / `@require_permission(VIEW_ALL_JOB_DATA)` /
  server-side user pin).
- No route-map snapshot regen — the `jobs` blueprint is not pinned and no page
  route is added or renamed.
- No `service.py` change; `search_jobs`/`count_jobs` stay uncached by design.
- No data-dependent tab suppression (e.g. hiding Wait Times when every job in
  the window has a NULL wait). That cannot be known without running the query
  the tab exists to run; the existing `null_count` caption stays the answer.

## Risks / things to verify in the browser, not just in tests

1. **Restore-order race.** After Apply the shell re-renders with Jobs marked
   `active`; `nav-view-persistence.js` then restores the tab the user was on.
   `intersect once` should not fire for the Jobs pane because
   IntersectionObserver reports at the next rendering step, by which time the
   pane is `display:none`. Confirm in the network panel that Apply while on
   Wait Times issues exactly **one** panel request. Contingency if it does
   fire: add a hidden `active_tab` input to the filter form and have
   `_explorer_card_context` mark that tab active server-side.
2. **Sentinel scoping.** `svg-chart-links.js` scopes `#jh-bar-<i>` and
   `#job-user-…` / `#job-proj-…` to the originating `.tab-pane`. Keeping the
   charts inside the tabbed card preserves that invariant.
3. **Unbounded windows.** Clearing both date fields is an explicit opt-in to
   full history. With charts on the page that now also means an unbounded
   aggregation. The 60 s `JOB_HISTORY_STATEMENT_TIMEOUT_MS` plus the
   panel-level error path are the guard; confirm a timeout degrades to an error
   card rather than a 500.
4. **RBAC unchanged.** `sureshm` (WNA-scoped) and a plain user must still get
   403 on `/machine/<m>/explore` and see no machine surfaces;
   `/user/<m>/explore` must still ignore `?user=<other>`.

## Verification

```bash
docker compose --profile test up -d mysql-test
export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'
source etc/config_env.sh

pytest tests/unit/test_webapp_jobs.py tests/unit/test_webapp_jobs_cache.py \
       tests/unit/test_webapp_jobs_charts.py tests/unit/test_route_map_parity.py
pytest                                   # full sweep before the PR
CACHE_REDIS_URL='redis://127.0.0.1:6379/0' pytest    # CI emulation
```

Browser smoke on webdev (`docker compose up webdev --watch`, :5050), Quick
Login personas `benkirk` (operator), `bdobbins` (plain), `sureshm` (WNA):

1. `/dashboards/user/jobs/machine/casper/explore?days=90` — six tabs, Jobs
   active, table identical to before; chips above the tab strip with counts.
2. Switch to Job Sizes → one aggregation request; switch dimension/metric pills
   → filters survive.
3. Set `Nodes min=8` + `Queue=cpu` → Apply → chart and chips both reflect it;
   exactly one panel request fires (Risk 1).
4. Pick a user in the filter panel → **By User tab disappears**, histograms
   stack by project; clear it → tab returns. Drill By Project → `account=` in
   the URL → **By Project disappears**, histograms stack by user.
5. `/user/jobs` (My Jobs) → Wait Times bands show a **Project** owner tier, not
   a one-row "you" tier; drilling By Project first gives flat bars with a
   single-click band → jobs drill.
6. Casper → Job Sizes → Nodes/CPUs/GPUs: the structurally-empty top bands are
   gone, the bucket table's last row is populated, and a bar click still opens
   the correct row. Confirm an interior zero band survives.
7. A window with no matching jobs → one empty-state message, not a "no jobs"
   box above a table of zero rows. A pre-2025 Derecho window on Wait Times →
   the "unmeasured" message, not "no jobs match".
8. Click a histogram bar → the matching bucket row expands with a job list
   whose count matches the band.
9. Sort a table column and page forward → no shell re-render, chips unchanged.
10. Project mode: `.../<projcode>/explore?machine=derecho&scope=<child>` — the
    pies are re-rooted to the subtree; a single-projcode subtree hides
    By Project.
11. Resource-details, Status → Job History and My Jobs cards otherwise
    unchanged (period pills, persisted window, Open full view).
12. Admin → Configuration shows `jobs` at 1800 s / 512 and `jobs_recent` at
    900 s / 512; `redis-cli INFO stats` shows `evicted_keys:0`.

## Deploy notes

- The `jobs`/`jobs_recent` TTL change takes effect on restart; existing 6-hour
  entries linger until their own TTL expires, so either run
  `sam-admin cache --refresh --category jobs` at the deploy window or accept
  one stale window.
- `helm/values.yaml` (`cache.maxmemoryMB`) reaches CIRRUS only via `main` —
  confirm the prod redis `INFO memory` / `evicted_keys` numbers before that
  promotion.
