# Jobs-tab activity timeline (stacked bar time series)

> **Status (2026-07-28): plugin half DONE, SAM half BLOCKED.**
> hpc-usage-queries **PR #102** (`jobs_timeseries` + charges on every
> aggregate) is open against `main`. Nothing below can start until it merges
> **and** the container is rebuilt — SAM's conda-env carries a pip-installed
> `job_history` snapshot that shadows the working tree, so a new *method* is
> invisible until then.

## Context

The job-history card ships six tabs — Jobs · By User · By Project · Wait Times
· Job Sizes · Durations — from one macro
(`templates/dashboards/user/partials/jobs_card.html:116`) onto four surfaces
(Status → Job History, resource-details, My Jobs, the explorer). Five carry a
chart; **Jobs is the table alone**, and all five existing charts are
distributional or categorical. Nothing on the card has a time axis, on a page
whose entire framing is a date window.

SAM already renders that chart for *summaries* — the stacked-by-user Usage
Trend on `/user/resource-details`
(`generate_usage_timeseries_stacked_by_user`, `dashboards/charts.py:306`) —
but it is fed from `comp_charge_summary` and cannot answer anything about
queues, job sizes, wait times or exit status.

This brings that chart to the Jobs tab, driven by the live job filter set.

## What the plugin now provides

`JobQueries.jobs_timeseries(period='day', *, <full jobs_search filter set>,
owners_limit=None, owners_by='user', owners_sort_by='hours')`:

```python
{
  "period": "day", "owners_by": "user",
  "start": "2026-05-01", "end": "2026-05-31",   # resolved window
  "bands": [                                    # zero-filled, chronological
    {"label": "2026-05-01", "start": "2026-05-01", "end": "2026-05-01",
     "job_count": 812, "cpu_hours": 91234.5, "gpu_hours": 120.0,
     "cpu_charges": 96000.1, "gpu_charges": 180.0,
     "owners": {"alice": {...}}},               # SAME keys in EVERY band
    ...
  ],
  "totals": {...}, "null_count": 0, "total_count": 24680,
}
```

Properties SAM can rely on (all pinned by plugin tests):

- `total_count == jobs_count(**filters)`; `Σ bands + null_count == total_count`.
- **Band replay is `start`/`end`**, not `min_param`/`max_param`:
  `jobs_search(start=band['start'], end=band['end'], **filters)` returns
  exactly `band['job_count']`. Both keys are already in `_ROUNDTRIP_KEYS`.
- **Every band carries the same owner keys in the same global rank order**,
  zero-filled when idle — so a colour map is assigned once. (This differs from
  `jobs_histogram`, whose top-N is per-bucket.)
- Bands are **site-local** calendar periods, DST-exact, matching
  `_apply_date_filter` and the daily summaries.
- `periods`: `day` | `week` | `month`. No `quarter`/`year`.

**Also new:** `jobs_histogram` and `jobs_usage_by` now return `cpu_charges` /
`gpu_charges` too, and `_USAGE_SORT_KEYS` accepts `charges` — so `charges`
becomes a first-class metric on **all six tabs** with one shared vocabulary.

⚠️ **Charges are not proportional to hours.** `qos_factor` is a genuine `0.0`
for the `uncharged` QoS (jhublogin-style work), so a charges view can show an
empty bar where an hours view shows work. This needs a caption, not a bug
report.

## Measured cost (from the plugin PR)

- Charges on existing aggregates: **+7.8 %** (interleaved A/B, min of 10).
- `jobs_timeseries` vs one `jobs_histogram`: **2.06–2.10×** on the scan path
  (it is two statements — rank, then series).
- Band count: **~10 % at 180 bands**, ~65 % at 730, measured interleaved on
  PG 18 / casper_jobs (21.0 M jobs).

  > ⚠️ An earlier revision of this doc reported **+54 %** at 180 bands and
  > concluded auto-coarsening was load-bearing on cost grounds. That number
  > was retracted upstream: the periods were timed **sequentially**, so
  > buffer-cache warming rode along with band count. Auto-coarsening stays,
  > but the justification is **legibility** — 180 bars is already past what
  > an 18in axis can render distinguishably. `_MAX_TIMELINE_BARS = 120` is a
  > display budget, not a cost budget.

- The plugin's own band cap is path-dependent: **400** on the `jobs`-scan
  path, **1200** on the `daily_summary` fast path (no CASE ladder there).
- Fast path: the plugin serves the series from `daily_summary` whenever the
  filter set is expressible in `(date, user_id, account_id, queue_id)` —
  measured **~15 ms vs ~7.4 s** for a 180 d daily series. That covers a
  card's normal scope; `qos` / `exit_status` / `job_id` / `name` and any
  `min_*`/`max_*` bound force the scan. Envelope is identical either way,
  so neither we nor the cache can tell which path ran.

## SAM work

### 1. Service wrapper — `src/webapp/jobs/service.py`

`jobs_timeseries(machine, scope, *, period, owners_limit, owners_by,
owners_sort_by, valid_qos_names, **filters)`, modelled line-for-line on
`jobs_histogram` (`:374`): `scope.check_filters` → `_plugin_filter_kwargs` →
`scope.apply` → `_cached_aggregation('timeseries', machine, kwargs, …,
period=period)`. `period` joins the cache `opts` so variants never alias.

### 2. Granularity — auto, with override pills

Auto-select from window length to hold bars in ~30–120:

| window | period |
|---|---|
| ≤ ~120 days | day |
| ≤ ~2 years | week |
| beyond | month |

Expose `Day / Week / Month` pills; **disable** (don't hide) an over-budget
choice with a title explaining why. An explicit `?period=` wins when in
budget. The explorer permits an unbounded window (clearing both date fields —
Risk 3 in `JOBS_EXPLORER_CHARTS.md`), which is ~1,500+ days on Derecho, so
this is required for correctness, not polish. The plugin also hard-caps at
`_MAX_TIMESERIES_BANDS = 400` and raises — handle that as a panel error.

### 3. Panel registration — `src/webapp/jobs/routes.py`

One row in the declarative `_PANELS` table (`:1690`):

```python
PanelSpec(key='timeline', rule='/timeline', render=_panel_timeline,
          siblings={'jobs_fragment_url': 'jobs'}),
```

`register_panels` (`utils/fragments.py:111`) generates the three mode routes
with each mode's existing gates. **This adds routes → regenerate the route-map
parity snapshot** (`ROUTE_MAP_REGEN=1`,
`tests/unit/snapshots/dashboard_route_map.json`) — the gate flagged in
`project_fs_scans_jobs_consolidation`.

`_render_timeline` mirrors `_render_histogram` (`:953`).

### 4. Metric pills gain `charges`

`_METRICS = ('jobs', 'cpu_hours', 'gpu_hours', 'charges')` (`routes.py:483`);
extend `_USAGE_SORT_BY` (`:499`) and `_JOBS_METRIC_KEYS` /
`_JOBS_METRIC_LABELS` (`dashboards/charts.py:1265`). The shared `metric:jobs`
persist family stays valid — one vocabulary across all six tabs. `group_by`
keeps the deliberately-bare app-wide key (`jobs_card.html:107`).

The `jobs_card.html:111` comment — *"The Jobs tab opts out: none of the four
apply to a per-job table"* — stops being true and must be rewritten.

### 5. Chart generator — `src/webapp/dashboards/charts.py`

`generate_jobs_timeseries_stacked(series, *, metric, period)`, modelled on
`generate_usage_timeseries_stacked_by_user` (`:306`), `@caching.chart_cached`
with a content-hash `key_fn`.

Reuse verbatim: `UNITY_STACK_10` for named owners and
`UNITY_NCAR_GRAY_LIGHT` for **Others** (emitted **first**, bottom of the
stack, does not advance the palette cursor); manual `bottoms` accumulation,
one `ax.bar()` per series, `width=1`, `lw=0.3` (the hairline matters — 365
bars × 11 segments at `lw=2` is a solid navy block); **legend built from proxy
`mpatches.Patch` handles, reversed** — the load-bearing trick that makes
`leg.get_patches()[i]` / `leg.get_texts()[i]` addressable for `set_url()`;
`fmt.mpl_number_formatter()`; `_empty_state(...)`; `_fig_to_svg`.

Adapt: input is the plugin envelope, not SAM's `{'dates','series'}`. Build
"Others" as `band totals − Σ band owners` (derivable, never synthesized).

### 6. Interactions — zero new JavaScript

- **Legend → entity.** Reuse `_USAGE_ENTITIES` (`routes.py:772`), which
  already carries `sentinel` (`job-user` / `job-proj`) and `sentinel_attr`. A
  legend click then fires the existing `ROW_SENTINELS` path
  (`svg-chart-links.js:63`), and **`activateOwningTab` (`:87`) handles the
  cross-pane hop for free** — that helper exists precisely because the
  resource-details stacked chart's legend addresses rows in another pane.
  **Guard:** only link the legend when the target tab is actually rendered.
  `panel_relevance()` (`routes.py:707`) can hide By User / By Project, and a
  link into a suppressed pane is a silent no-op — the regression
  `JOBS_EXPLORER_CHARTS.md` C5 shipped.
- **Bar → period.** Index-keyed `#jt-bar-<i>` sentinels plus **one line** in
  `ROW_SENTINELS`: `'#jt-bar-': 'data-jt-period'`. Index-keyed is the
  convention so the JS never parses band labels. Bars address rows in a
  per-period `<details>` table beneath the chart, each row drilling into the
  Jobs table for that period.

### 7. Placement — collapsible, expanded only on the explorer

The timeline goes **inside the Jobs pane, above the table**, as its own
fragment with its own target, so a metric/period pill re-fetches only the
chart and a sort/page click re-fetches only the table.

Jobs is the **default** tab on four surfaces, so an unconditional aggregation
there is paid by everyone who only wanted the table — a worse deal than the
five existing chart tabs, which are opt-in by definition. So: a Bootstrap
collapse, fetch gated on **`shown.bs.collapse` on the div** (not a click on
the button — `nav-view-persistence.js`'s restore-on-reload does not fire
click, see `feedback_persisted_collapse_htmx`).

| Surface | Default |
|---|---|
| Explorer (full filter panel) | **expanded** |
| Status / resource-details / My Jobs cards | **collapsed** |

State persists per surface.

### 8. Caching

No new bucket — `_cached_aggregation` routes on window (`jobs` 1800 s /
`jobs_recent` 900 s). Fan-out grows: `period` × 4 metrics × 2 `owners_by` adds
~8 keys per filter combination on top of the ~22 the explorer already
produces. Re-check Redis `INFO memory` / `evicted_keys` before the main
promotion, per `JOBS_EXPLORER_CHARTS.md` C7.

## Verification

```bash
docker compose --profile test up -d mysql-test
export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'
source etc/config_env.sh

pytest tests/unit/test_webapp_jobs.py tests/unit/test_webapp_jobs_charts.py \
       tests/unit/test_webapp_jobs_cache.py tests/unit/test_route_map_parity.py
pytest                                              # full sweep
CACHE_REDIS_URL='redis://127.0.0.1:6379/0' pytest   # CI emulation
```

Browser smoke (`docker compose up webdev --watch`, :5050), personas `benkirk`
(operator), `bdobbins` (plain), `sureshm` (WNA):

1. `/status/job-history` → Jobs tab: timeline collapsed, table paints as fast
   as today. Expand → one aggregation request.
2. Metric pills jobs → cpu_hours → gpu_hours → **charges**; switch to Wait
   Times and back — the metric persists (one shared vocabulary).
3. Explorer: `Queue=cpu` + `Nodes min=8` → Apply → chart, chips and table all
   reflect it; the chart total matches the table totalizer.
4. Period pills 30d → 1 yr: granularity auto-coarsens, caption states the
   period, over-budget overrides disabled.
5. Legend click → By User pane activates, that user's row expands. Pin a user
   → By User disappears **and the legend stops linking**.
6. `group_by` project → legend opens the By Project pane.
7. Bar click → the period's row expands and drills; count matches the bar.
8. A `uncharged`-QoS slice on the charges metric → empty bars while the jobs
   metric shows work; confirm the caption explains it.
9. **Timezone**: compare a single day's bar against `jobhist` for the same
   site-local day; an evening (post-18:00 MDT) job must land in the right bar.
10. Clear both date fields → coarsest period, bounded bars; a statement
    timeout or the plugin's band cap degrades to an error card, not a 500.
11. RBAC unchanged: `sureshm` and a plain user 403 on
    `/machine/<m>/timeline`; `/user/<m>/timeline` ignores `?user=<other>`.
