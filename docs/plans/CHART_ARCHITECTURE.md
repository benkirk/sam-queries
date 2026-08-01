# SAM chart architecture

**Status: PROPOSED (2026-08-01).** Branch `claude/chart-architecture-refactor-72l2oz`.
This document is the design; no code has been written.

An OO refactor of `src/webapp/dashboards/charts.py`, plus two new rendering
axes — **layout** (desktop / mobile) and **theme** (light / dark) — that the
current flat-function design cannot express. Neither axis is *wired* here; both
are made possible, with today's rendering preserved exactly as the default.

All line references are against `charts.py` at 2,011 lines unless otherwise
qualified.

---

## Context

`src/webapp/dashboards/charts.py` is a single 2,011-line module holding every
matplotlib chart in the product:

| Date | Lines | |
|---|---|---|
| 2026-04-27 | 701 | initial |
| 2026-05-22 | 1,069 | clickable Usage Trend bars |
| 2026-07-27 | 1,717 | jobs explorer charts |
| 2026-08-01 | 2,011 | 16 flat `generate_*` functions, no shared structure |

Each chart was written by copying the nearest existing one and editing the dict
keys. Literally: `generate_allocation_type_pie_chart_matplotlib` (`:1005-1043`)
is `generate_facility_pie_chart_matplotlib` (`:958-996`) with two dict keys
changed. A 17-line pie-rendering block appears verbatim five times.

Two prior plans bound this work:

- **`WEBAPP_OO_REFACTOR.md` § 1.3** scoped itself to the chart *figure
  lifecycle* — it extracted `_fig_to_svg()` and `_empty_state()` for ≈ −45 lines
  and a figure-leak fix, and explicitly left the cache-key functions and the
  chart bodies alone. The bodies are what remains.
- **`MOBILE_FRIENDLY.md` § non-goals** ruled charts out entirely: "*No chart
  refactors. SVG/matplotlib charts stay exactly as they are.*" That deferral is
  what this plan reopens.

There is no dark mode anywhere in the app today, and no theming hooks in the
chart layer. Both mobile and dark mode need the same thing from charts: **the
ability to render the same data more than one way**. That capability is what a
class hierarchy buys, and it is the main reason to do this now.

---

## Goals

1. Commonality in base classes; only genuine differences in the leaves.
2. **Adding a chart gets cheap.** Today a new pie is ~70 lines of copy-paste and
   a new drill-down chart requires editing JavaScript. Target: ~10 lines and no
   JS change.
3. **Make `layout` and `theme` expressible** as first-class render axes, with
   `desktop`/`light` reproducing today's output exactly.
4. Break up a 2,011-line file that no longer fits in anyone's head.

**LOC reduction is explicitly not a goal.** For the record, the expected total is
~2,011 → ~1,850 across the package, and *executable* lines are roughly flat:
~285 removed as duplication, ~280 added as base classes, links, series adapters
and the two new render axes. The win is in the derivative, not the value. The
file is ~49 % prose (~1,000 executable, ~400 docstring, 282 comment, 307 blank)
and that prose is load-bearing — the notes explaining the palette reversal
(`:578-584`), charges-vs-hours non-proportionality (`:1269-1271`) and the pace
chart's run-length compression (`:1919-1932`) are the highest-value lines in the
file and must be relocated, not deleted.

---

## Today's code

### Inventory — 16 generators, 15 caches

All return an **inline SVG string**; there is no PNG, `BytesIO`, base64, dpi, or
file output anywhere.

| Chart | Line | Family | figsize | Cache name / maxsize |
|---|---|---|---|---|
| `generate_usage_timeseries_matplotlib` | 245 | bar | (18,5) | `usage_timeseries` / 128 |
| `generate_usage_timeseries_stacked_by_user` | 306 | stacked bar | (18,5) | `usage_timeseries_stacked` / 128 |
| `generate_disk_usage_stacked_area` | 419 | area | (18,5) | `disk_usage_stacked_area` / 128 |
| `generate_user_proj_stacked_area` | 535 | area | (18,5) | `user_proj_stacked_area` / 128 |
| `generate_distribution_histogram` | 719 | categorical stack | (14,5) | `distribution_histogram` / 128 |
| `generate_nodetype_history_matplotlib` | 814 | dual-panel | (18,10) | `nodetype_history` / 64 † |
| `generate_queue_history_matplotlib` | 876 | dual-panel | (14,8) | `queue_history` / 64 † |
| `generate_facility_pie_chart_matplotlib` | 958 | pie | (7,4) | `facility_pie_chart` / 32 † |
| `generate_allocation_type_pie_chart_matplotlib` | 1005 | pie | (7,4) | `allocation_type_pie_chart` / 64 † |
| `generate_disk_entity_pie_chart` | 1091 | pie | (7,4) | `disk_entity_pie_chart` / 64 |
| `generate_user_usage_pie_chart` | 1184 | pie | (7,4) | `user_usage_pie_chart` / 64 |
| `generate_jobs_histogram` | 1339 | categorical stack | (14,5) | `jobs_histogram` / 128 |
| `generate_jobs_timeseries_stacked` | 1488 | stacked bar | (18,5) | `jobs_timeseries` / 128 |
| `generate_jobs_usage_pie_chart` | 1615 | pie | (7,4) | `jobs_usage_pie_chart` / 64 |
| `generate_jobs_user_pie_chart` | 1705 | — | — | *(uncached delegator)* |
| `generate_pace_chart_matplotlib` | 1804 | area | (10,4) | `pace_chart` / 192 |

† uses the decorator's **default** `key_fn`, which hashes only `args[0]` — see
"the aliasing trap" below.

### Already factored — do not redo

`matplotlib.use('Agg')` appears once (`:24`); `plt.rcParams` once (`:63-82`);
`savefig`/`plt.close` once inside `_fig_to_svg` (`:85-96`); the no-data
placeholder once in `_empty_state` (`:99-102`).

### The four duplication clusters

**A. Pie — 5 functions** (`:958`, `:1005`, `:1091`, `:1184`, `:1615`). A 17-line
`ax.pie(...)` + autopct-recolour + legend block is *verbatim identical* at
`:979-994`, `:1026-1041`, `:1129-1145`, `:1219-1235`, `:1669-1685`. An 11-line
click-wiring loop repeats 3× (`:1149-1159`, `:1239-1249`, `:1690-1700`). Real
variation: trim strategy (`_pie_trim` fixed-cap-10 `:943` vs
`_pie_cumulative_keep` 90 %-cumulative `:1063`), "Other" derivation (3 variants),
legend formatter (`fmt.number` vs `fmt.size` `:1127`), drill target (4).

**B. Stacked series with clickable legend — 5 functions** (`:306`, `:419`,
`:535`, `:1488`, `:1804`):

- *palette cycling with an "Others" grey case* — 4 copies (`:340-347`,
  `:474-481`, `:585-594`, `:1538-1544`). `:585-594` deliberately **reverses** the
  index and uses `UNITY_STACK_20`; a real semantic difference that must remain a
  visible override, not collapse into a flag.
- *reversed proxy-`Patch` legend* — 5 copies (`:372-385`, `:494-506`, `:604-637`,
  `:1576-1583`, `:1976-1990`), each preceded by a redundant function-local
  `import matplotlib.patches as mpatches`.
- *legend link-wiring skipping "Others"* — 4 copies (`:388-393`, `:508-514`,
  `:641-646`, `:1584-1593`).

**C. Categorical stacked-bar histograms — 2 functions** (`:719`, `:1339`).
Parallel `_bucket_segments` (`:662`) / `_jobs_bucket_segments` (`:1296`), shared
`_shade_family` (`:679`), identical `log_y` fallback, identical inner stacking
loop differing only in the drill key, identical tail. Real differences to keep as
hooks: jobs' conditional flat-bar colour (`:1388`), and fs-scans scaling segments
by the byte scale where jobs does not.

**D. Dual-panel time series — 2 functions** (`:814`, `:876`).
`subplots(2,1,sharex=True)`; the same framed legend 4× (`:843`, `:861`, `:911`,
`:926`).

**Not duplication despite appearances:** the two byte-scale ladders differ —
`:458-463` is PiB/TiB, `:755-760` is PiB/TiB/GiB. A `floor` parameter, not a copy.

### Cross-cutting facts that constrain the design

- **20** `_empty_state` call sites; **39** `set_url` sites.
- **Consumers**: 18 call sites across `dashboards/user/blueprint.py`,
  `dashboards/allocations/blueprint.py`, `dashboards/status/blueprint.py`,
  `jobs/routes.py`, `disk_scans/routes.py`. All render an htmx fragment
  containing `{{ chart_svg | safe }}`.
- **Threading**: `containers/webapp/gunicorn_config.py:69,73` — `worker_class =
  'gthread'`, `threads = 4`. Four threads share one process-global
  `matplotlib.rcParams`.
- **CSS**: `dashboard.css:1774-1783` scales finished SVGs
  (`max-width:100%; height:auto`). `jobs-histogram-chart` and
  `jobs-timeline-chart` (`jobs_histogram.html:145`, `jobs_timeline.html:98`) opt
  out of `.chart-container` and have **zero CSS rules** — they get no
  `max-width` at all and are live mobile-overflow candidates.
- **Palette is triplicated**: `variables.css:39-53` (14 CSS tokens),
  `charts.py:122-132` (11 Python scalars, exact hex matches), and
  `charts.py:142-194` (`UNITY_STACK_20/10`, ~10 additional tint hexes with no CSS
  equivalent). The rcParams block re-inlines `'#011837'`/`'#bbbcbc'` as string
  literals because the named constants are defined 55 lines later.

---

## Decisions

### 1. Three layers, not four

`BaseChart` → family → concrete. An abstract/matplotlib split was considered and
rejected:

- **The abstract layer would own ~30 lines and shield nothing.** Strip matplotlib
  and what remains is the empty-state short-circuit, the cache key, and
  `render()`'s ordering. Everything else is matplotlib-shaped to the leaf —
  `figsize`, `ax.pie`, `stackplot`, `bbox_to_anchor`, and `Artist.set_url()` at
  39 sites.
- **The real backend coupling is not in Python.** `svg-chart-links.js` dispatches
  on `<a xlink:href>` anchors matplotlib's SVG backend emits. Any backend swap
  rewrites that JS regardless of Python layering, so the layer meant to buy
  "easier future migration" does not buy it.
- **The repo has no 4-level hierarchies and its idiom is the opposite.**
  `HtmxFormHandler` is one concrete base with ten documented hooks and no
  abstract parent; `CrudSpec`'s docstring sets a hard rule against growing the
  spec; `src/cli/core/base.py` is one ABC with one abstract method.

**The migration seam is bought with module boundaries instead.**
`charts/series.py` and `charts/links.py` must not import matplotlib, enforced by
an AST-scan test. Those two are exactly what a different backend would reuse.
*A leaf-ward split driven by observed duplication is earned; a root-ward split
driven by a hypothetical is not.*

### 2. `BaseChart` — the lifecycle

Not an ABC. Class-attribute configuration, defaulted hooks, one public driver —
the shape of `HtmxFormHandler` (`utils/form_handler.py:60-120`).

```
render(layout, theme)
    ├─ prepare()                    raw payload → plot-ready model
    ├─ is_empty()  → empty_state()  short-circuit
    ├─ make_figure()                plt.subplots(figsize=layout.figsize)
    ├─ draw(ax, model)              REQUIRED — the family implements the marks
    ├─ decorate(ax, model)          labels, ticks, grid, scale, THEME CHROME
    ├─ add_legend(ax, model)        placement from layout, colours from theme
    ├─ wire_links(artists, model)   set_url application
    ├─ finish(fig, ax, model)       autofmt_xdate, xlim, annotations
    └─ to_svg(fig)                  the :85 chokepoint, moved verbatim
```

| Class attribute | Purpose |
|---|---|
| `cache_name`, `cache_maxsize` | **verbatim** from today's decorator args |
| `LAYOUTS` | `{'desktop': Layout(...), 'mobile': Layout(...)}` |
| `empty_message`, `empty_classes` | the `_empty_state` contract |
| `grid` | kwargs or `None` — default `{'alpha':0.3}`; pace `{'alpha':0.2}`; histograms `{'axis':'y','alpha':0.3}` |
| `bar_drill`, `legend_drill` | a drill target or `None` |

`cache_key` is a **classmethod over the raw arguments**, deliberately not an
instance method, so a cache hit never constructs the chart or runs `prepare()`.

### 3. The families

| Family (file) | Concretes |
|---|---|
| `PieChart` (`pie.py`) | Facility, AllocationType, DiskEntity, UserUsage, JobsUsage |
| `StackedSeriesChart` (`stacked.py`) | UsageTrend, UsageTrendStacked, DiskUsageArea, UserProjArea, JobsTimeseries |
| `CategoricalStackChart` (`histogram.py`) | DistributionHistogram, JobsHistogram |
| `DualPanelTimeSeriesChart` (`dualpanel.py`) | NodetypeHistory, QueueHistory |
| `PaceChart` (`pace.py`) | direct `BaseChart` subclass — no family |

- **Bar vs area is a `stack_mode = 'bar' | 'area'` class attribute** dispatching
  to two ~12-line private methods, not two subclasses. This keeps three levels
  while honouring the real 3/2 split.
- **`FacilityPie` and `AllocationTypePie` become ~10-line attribute-only
  subclasses** — the declarative tier of the house idiom.
- **Flat and stacked Usage Trend merge.** With one series, `bottom=[0]*n` is
  identical output.
- **`UserProjStackedArea` overrides `colors()`** so the reversed palette index
  stays visible with its comment attached.
- **`PaceChart` is ~60 % bespoke** (`_pace_bands` `:1728`, run-length compression
  `:1933-1949`, ymax clamping `:1960-1962`, the today marker `:1966-1969`, the
  only `MonthLocator`). It gains only `to_svg`, `empty_state` and `link_legend`.
  Do not force it into a family; if the base starts growing hooks only Pace uses,
  let it override `render()` outright.
- **`generate_jobs_user_pie_chart` stays a 3-line facade function.** Binding it
  as a chart would register a 16th cache and add a row to the admin Caching card.

### 4. Two render axes: `layout` and `theme`

Both are orthogonal, both default to today's behaviour, both must enter the cache
key. `layout` and `theme` are unclaimed names — `variant` already means "the
per-metric cached rendering" in existing prose, and `preset` belongs to the
date/time pickers.

```python
@dataclass(frozen=True)
class Layout:
    name: str                 # 'desktop' | 'mobile'
    figsize: tuple
    base_fontsize: int
    legend_placement: str     # 'right' | 'below' | 'none'
    max_legend_entries: int | None
    max_ticks: int
    label_rotation: int

@dataclass(frozen=True)
class Theme:
    name: str                 # 'light' | 'dark'
    text: str                 # tick labels, axis labels, titles, LEGEND TEXT
    spine: str
    grid: str
    bar_edge: str             # today UNITY_NCAR_NAVY
    segment_edge: str         # today literal 'white'
    legend_face: str | None   # today literal 'white' on dual-panel only
    shade_toward: str         # _shade_family blend target — today pure white
```

Public functions gain `layout='desktop', theme='light'`, so **all 18 call sites
and every existing test keep today's behaviour untouched**.

#### Why not rcParams, and why not `rc_context`

Today's chrome colours live in a process-global `plt.rcParams.update()` at
import (`:63-82`) — the **only** rcParams mutation in the repo. That is safe
precisely because it never changes. Per-request theming must not go through it:

- **`plt.rc_context` is thread-unsafe here.** It mutates the global `rcParams`
  dict and restores on exit, with no thread isolation. With `gthread` × 4
  threads, two concurrent requests rendering light and dark would interleave and
  cross-contaminate.
- **Serialising renders behind a lock** would kill concurrency on a pool
  explicitly tuned for I/O overlap (`gunicorn_config.py:60-66`).
- **Rendering both themes and letting CSS choose** doubles render cost and cache
  size for a feature most users will use one of.

**Therefore: the `Theme` object is threaded explicitly and colours are applied
per-artist in `decorate()` / `add_legend()`.** Mechanical, thread-safe, testable.
`Theme.LIGHT` reproduces today's values exactly, so the global rcParams block is
reduced to fonts and structural defaults (spines off, `legend.frameon`).

#### The aliasing trap — the load-bearing detail

`caching/chart.py:116` and `redis_chart.py:155` both default to
`key_fn or (lambda *args, **kwargs: content_hash(args[0]))` — **the default key
ignores every argument except the first positional one.** A `theme=` kwarg would
be silently dropped, and the first-rendered theme's SVG served to everyone. Four
charts use the default key_fn and would alias immediately (marked † in the
inventory). With Redis the cache is shared across workers *and pods*, so the
aliasing would be global.

Do not rely on eleven hand-written key functions remembering. Compose it once, in
the binder, so it is structurally impossible to get wrong:

```python
def _key(*args, layout='desktop', theme='light', **kwargs):
    return content_hash([cls.cache_key(*args, **kwargs), layout, theme])
```

Budget note: two axes eventually multiply every chart's working set by up to 4.
`charts.py:1800-1802` sizes `pace_chart` `maxsize=192` as "~30 resources × 3
sort_by × facility fanout". **Retuning belongs to whichever follow-on pass first
ships a second profile** — this plan ships desktop/light only, so the key space
does not actually grow.

#### What "groundwork" means, precisely

**This plan ships:** the `Layout` and `Theme` dataclasses; the parameters threaded
through render and cache; `desktop`/`light` reproducing today's output exactly; a
defined `mobile` layout and `dark` theme per family; and tests proving both render
and produce distinct cache keys. No client ever requests them.

**This plan does not ship:** any transport, any CSS, any toggle, any visual
tuning. See "Explicitly not in scope".

### 5. Drill-downs: one structured scheme

Today nine ad-hoc sentinel prefixes are split between Python and a hardcoded
`ROW_SENTINELS` prefix→attribute table in `svg-chart-links.js:71-85`. Six of the
nine already funnel through one generic handler, `openEntityRow(attr, id, scope)`
— the problem is that **the attribute name lives in the JS**, so adding a
drill-down chart requires editing JavaScript. The template side already treats
that attribute as data: `disk_scans_entities.html:94` computes
`'data-owner-uid' if _is_owner else 'data-group-gid'`.

Replace the nine prefixes with one parameterized fragment:

```
#sam/row/data-owner-uid/1234     → openEntityRow('data-owner-uid', '1234', pane)
#sam/day/2026-07-31              → openDayRow('2026-07-31')
#sam/user/benkirk                → openUserRow('benkirk')
```

Segments percent-encoded. The JS parses once and dispatches on the action;
`ROW_SENTINELS` is deleted. Python collapses to `RowDrill(attr)`, `DayDrill`,
`UserDrill` and `ModalRoute(endpoint, param)` — a row drill becomes just an
attribute name declared at the chart.

**Payoff:** adding a row-drill chart becomes a zero-JS-change operation, and
there is no longer a cross-language table that can drift.

**Keep `<a xlink:href>`; `set_gid()` was considered and is worse:** ids must be
unique, but one drill target spans three artists (bar + legend patch + legend
text); `<a href>` is keyboard-focusable and a `<g id>` is not; and the link
de-styling it would save is nine already-working lines
(`components.css:186-199`). A `#`-fragment also degrades safely if JS fails.

**Modals are left alone** — they carry real URLs, which are inspectable, degrade
gracefully, and need no server-side URL table in JS.

**Cost:** 18 references in the JS, 23 in `charts.py`, 51 test assertions
(`test_webapp_jobs_charts.py` 35, `test_webapp_disk_scans.py` 9,
`test_webapp_jobs.py` 4, `test_resource_details_partials.py` 3), and 8 prose
comments. All template and route references are **comments only** — verified, no
functional coupling.

### 6. Series normalization

Producers use three key names for one concept, which is why the cluster-B colour
loops could never be shared: `series[i]['label']` (`sam/queries/charges.py:789`,
`system_status/queries/user_proj_queues.py:205`), `series[i]['username']`
(`sam/queries/disk_usage.py:338,451`), and an owners dict (jobs plugin, via
`_jobs_timeseries_series` `:1427`).

```python
@dataclass(frozen=True)
class Series:
    label: str
    values: list
    link_key: str | None      # None ⇒ inert ('Others', unknown, aggregate)
```

Normalize at the **chart boundary** with three adapters; do not touch the query
layer, which has its own consumers and tests. The invariant this buys: eight
scattered `label == 'Others'` string tests and three `is None` tests collapse to
one rule — *an artist is linked iff its `link_key` is not None*.

### 7. Caching is preserved exactly

`caching.chart_cached` is untouched; a binder reads config off the class,
mirroring `register_crud`:

```python
def chart_view(cls):
    """Bind a BaseChart subclass to its cache; return the module-level callable.
    Called AT IMPORT so facade order IS _chart_caches registration order."""
```

- **Registry population at import**: 15 `chart_view(...)` calls in today's source
  order → `caching/__init__.py:121,125` appends in the same order. Pinned by a
  test asserting the exact ordered 15-name list.
- **`.cache_clear()` / `.cache_info()` / `.cache_bytes()`** remain attributes of
  the public callables (`caching/chart.py:122-124`), so
  `utils/profiling/profile_allocations.py:149` and
  `test_allocations_performance.py:470-503` need zero edits.
- **Cache names stay byte-identical** — they are Redis key prefixes
  (`redis_chart.py:34`) and `test_redis_cache.py:203,222,242` references them.
- **The 11 existing `_*_cache_key` functions move verbatim** into their classes as
  `@staticmethod cache_key`, because `caching/chart.py:117` calls
  `_key(*args, **kwargs)` with the view's own arguments.
- **No declarative `key_fields` DSL** — it cannot express the pace 5-field
  projection (`:1778`), the jobs `clickable` positivity vector (`:1330`), or
  `_distribution_cache_key`'s per-bucket segment tuples (`:706-710`).

### 8. Backward compatibility — the facade

`charts.py` becomes `charts/__init__.py`, preserving both
`from webapp.dashboards.charts import X` (18 call sites) and
`import webapp.dashboards.charts` (`test_chart_fonts.py:20`, which depends on the
import-time `addfont` + rcParams side effect).

It re-exports the 16 `generate_*` names and ~35 private names, including the 8
that tests import directly (`_JOBS_METRIC_KEYS`, `_jobs_bucket_segments`,
`_jobs_metric_value`, `_jobs_timeseries_series`, `_pie_cumulative_keep`, and the
three `_jobs_*_cache_key` helpers), with `from . import theme  # noqa` first so
the font/rcParams side effect still fires.

Because callers import names into their own namespace,
`monkeypatch.setattr('webapp.dashboards.user.blueprint.generate_disk_usage_stacked_area', …)`
(`test_resource_details_disk_chart_route.py:33`) is unaffected.

Callers keep owning exception handling — `disk_scans/routes.py:610` wraps the data
fetch and `jobs/routes.py:952` calls the chart *outside* its `try` — so
**`BaseChart` must not swallow exceptions.**

---

## File layout

```
src/webapp/dashboards/charts/
  __init__.py    ~150   facade: theme import, 15 chart_view bindings, delegator,
                        ~35 re-export aliases, __all__
  theme.py       ~230   fonts + structural rcParams, UNITY_* palettes (comments
                        intact), Theme.LIGHT / Theme.DARK, _autopct_color_for,
                        scale_bytes(peak, floor)
  layout.py       ~80   Layout dataclass + per-family desktop/mobile profiles
  base.py        ~200   BaseChart + chart_view + _fig_to_svg + _empty_state
  links.py        ~80   RowDrill/DayDrill/UserDrill/ModalRoute, encode,
                        apply_urls, link_legend            [no matplotlib]
  series.py       ~70   Series + three adapters            [no matplotlib]
  pie.py         ~240   PieChart + 5 concretes (2 of them ~10 lines)
  stacked.py     ~270   StackedSeriesChart + 5 concretes
  histogram.py   ~180   CategoricalStackChart + 2 concretes
  dualpanel.py   ~140   DualPanelTimeSeriesChart + 2 concretes
  pace.py        ~200   PaceChart + _pace_bands + _pace_key_fields
                ─────
                ~1840
```

No file over ~270 lines.

---

## Commit series

One branch, one PR → `staging`, commits C0–C11. **C12 is a separate PR.**
The suite must be green at every commit.

**C0 — Characterization harness.** `svg_fingerprint()` plus pinned fingerprints
for all 16 charts including empty-data cases; the facade `dir()` snapshot; the
15-name cache-order test. Written *before* any source moves.

**C1 — `git mv charts.py charts/__init__.py`**, content byte-identical. Proves
the package form; imports and tests untouched.

**C2 — Extract `theme.py`.** rcParams, palettes, `_autopct_color_for`; the two
byte ladders become `scale_bytes(peak, floor)`. `Theme.LIGHT` defined but not yet
threaded.

**C3 — Structured drill scheme.** New `links.py`; rewrite `svg-chart-links.js`
(`ROW_SENTINELS` deleted, action dispatch); update 51 test assertions and 8
comments. Applied while the charts are still plain functions.

**C4 — `series.py`** and apply it at the stacked charts' link sites; hoist the 5
function-local `mpatches` imports; fix the missing `matplotlib.colors` import
(`:1723` works only because pyplot pulls the submodule in, while `_shade_family`
`:685` imports it locally as `mcolors`).

**C5 — `base.py` + `layout.py` + `chart_view`**, with `Theme` threaded into
`decorate()`. Migrate `dualpanel.py` as the pilot — no drills, no custom key_fn,
smallest blast radius.

**C6 — `pie.py`** family plus the two declarative pies.

**C7 —** the 3 cumulative pies and the delegator.

**C8 — `histogram.py`** family plus its 2 charts.

**C9 — `stacked.py`** family plus its 5 charts, including the Usage Trend merge
and the reversed-palette `colors()` override.

**C10 — `pace.py`.**

**C11 — Facade cleanup and docs.** `__all__`, re-export audit, cache-order test,
the matplotlib-free AST test. A `chart_card()` macro unifying the two wrapper
idioms (`.chart-container`, 12 sites, vs bare flex + bespoke class, 3 sites),
mirroring `modal_scaffold(variant=…)`. CSS for the two zero-rule wrapper classes.
Plus housekeeping:

- `pyproject.toml:87` omits `src/webapp/utils/charts.py` from coverage — a path
  that does not exist, so charts.py was never actually excluded.
- `helm/values.yaml:40` says "13 chart SVG caches"; there are 15.
- `profile_allocations.py:65` credits `_attach_cache_methods()`, long gone.
- `src/webapp/README.md:92`; add a **§ Charts** section to `CLAUDE.md`, which has
  none, covering the families, the drill scheme, the render axes, and the "add
  chart #17" recipe.

**C12 —** *(separate PR, needs sign-off — pixel-changing)* Cosmetic normalization:
pace grid alpha 0.2→0.3; the undocumented `color='grey'` on nodetype ax1 (`:845`);
legend fontsize 9/10/11/13; bbox anchor `(1.0)` vs `(1.01)`; `frameon`. **And one
latent bug:** `text.color` is never set in rcParams, so every legend label in
every chart renders pure black rather than the intended space-blue. Fixing it is
correct but visible, so it belongs here rather than in the refactor.

### The rule for C1–C11: zero *visual* change

Every style inconsistency is preserved as a class attribute **even where it looks
like an accident**. The one intentional byte-level change is the href scheme,
isolated in C3 — so the fingerprint's ordered-href list changes in exactly one
reviewable commit and nowhere else. That is what makes the fingerprint a real
gate rather than a rubber stamp.

---

## Explicitly not in scope

- **Any actual mobile rendering.** No transport, no `matchMedia`, no CSS media
  queries for charts, no visual tuning of the `mobile` layout. Only the parameter
  and a defined profile.
- **Any actual dark mode.** Chart dark mode is *blocked* on app dark mode:
  charts live inside `.card`s whose background is hardcoded `#fff`
  (`dashboard.css:577,580`), and there are 81 hardcoded `#fff`/`white`
  occurrences across the six app CSS files bypassing the token layer. Transparent
  SVG buys nothing until the surrounding chrome is themed. No `data-bs-theme`
  wiring, no toggle, no cookie, no `variables.css` dark block.
- **Consolidating the triplicated palette** (`variables.css` ↔ Python scalars ↔
  the stack tints). Worth doing, but it is a build-step question, not a chart
  question.
- **Any query-layer change.** Envelope normalization happens at the chart
  boundary.
- **The legacy-compat API blueprints**, per the standing repo rule.
- **`fmt.mpl_size_formatter`.** No chart sets a size formatter on an axis today —
  `disk_usage_stacked_area` scales manually and labels the axis
  `'Disk usage (TiB)'` (`:468`). The shared thing is `scale_bytes()`, and it
  belongs in `charts/theme.py`.

### Groundwork the follow-on passes will find waiting

*Mobile.* The transport rail exists: `nav-view-persistence.js:324-333` already
injects params into outgoing htmx requests via `htmx:configRequest`; a sibling
listener sets `layout` from `matchMedia`. Respect the `detail.path` de-dup at
`:303-317` — htmx *appends* parameters to the path for GETs, so an injected key
also present in the `hx-get` URL yields `?metric=a&metric=b` and Werkzeug returns
the **first**, silently defeating the override. Server-side, add `_parse_layout()`
in the style of `jobs/routes.py:723-735` `_parse_period`, the best model in the
repo: it honours an explicit choice *only while it fits* and falls back
explicitly. House rule in these routes — never 400 on a bad selector, clamp
silently, because a stale localStorage replay must not break a panel. Charts
arrive via `hx-trigger="load"`/`intersect`, so `configRequest` fires after first
paint, which is fine and is the only place it can be evaluated
(`dashboard-init.js:225-232`: "*markup can't be viewport-conditional*").

*Dark.* A **cookie** is the right carrier, not localStorage: it avoids the
first-paint flash and sidesteps the CSP posture at `base.html:7-14`, where
`script-src 'self'` blocks the usual inline blocking script. Flask reads it into
`<html data-bs-theme>` and passes the same value to the chart renderer. Appendix B
is the complete chrome inventory the pass will need.

---

## Test strategy

**No golden SVGs.** Matplotlib SVG is unstable three independent ways: `<dc:date>`
is a wall-clock stamp, so byte-equality breaks between two runs of *identical*
code; clip-path ids derive from a per-process hash salt; and path data, font
metrics and `bbox_inches='tight'` dimensions shift across matplotlib patch
releases. The first two are suppressible (`metadata={'Date': None}`,
`rcParams['svg.hashsalt']`); the third is not, and would make every matplotlib
bump a 16-file golden churn.

**Use a structural fingerprint** — `svg_fingerprint(svg) -> dict` extracting only
what the app contracts on:

- ordered list of every `xlink:href` → the drill contract
- counts of `<path>`, `<g>`, `<use>`, `<text>`
- ordered list of `fill:#rrggbb` → the palette contract, including the reversal
- ordered `<text>` content → labels, legend entries, ticks, autopct strings
- root `width`/`height` to 1 dp → catches `bbox_inches='tight'` layout shifts

Stable across runs and matplotlib patch versions; sensitive to everything this
refactor could plausibly break.

**Existing tests change only where forced** — the 51 sentinel-string assertions in
C3, and the three cache-key helpers if they move.

**New tests:** cache-registry order (exact 15 names, exact order); facade `dir()`
subset; matplotlib-free AST scan on `series.py` and `links.py` (this is what makes
the seam real rather than aspirational); a render-axes test proving every chart
renders under `mobile` and `dark` and that each combination yields a distinct
cache key; and extend `test_chart_fonts.py` with
`assert plt.rcParams['font.family'][0] == 'Poppins'` — today's test only proves
`findfont` resolves and would miss a refactor that moved rcParams into an uncalled
function, silently degrading every chart.

**Grep gates → 0 by C11:** function-local `import matplotlib.patches`; duplicated
`ax.pie(` blocks; `for s, patch, text in zip(rev_series` loops; per-chart
`_*_cache_key` module functions; the nine legacy sentinel prefixes.

```bash
source etc/config_env.sh
export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'
pytest -q                                     # full suite, ~95 s
```

`tests/unit/snapshots/dashboard_route_map.json` is unaffected — no routes move.
`tests/perf/test_route_query_counts.py` baselines must be unchanged.

---

## Risks, and what must be checked in a browser

1. **The drill-scheme change touches JS**, so a mistake breaks click-through
   *silently* — nothing 500s, links just stop working. C3 is isolated and
   fingerprint-guarded precisely so it can be reviewed alone. Click every drill:
   all three actions, the pie wedge/legend duality, and both modal routes. The
   fingerprint proves the href *strings*; it does not prove they are on the right
   *artists*. `link_legend` zips `get_patches()` against `get_texts()` with index
   guards (`:1156-1159`), and an off-by-one produces valid-looking hrefs on the
   wrong swatches.
2. **Legend reversal** has five variants and one deliberately inverted palette —
   the likeliest silent visual regression.
3. **Layout shift from `bbox_inches='tight'`.** Most charts put legends outside
   the axes, so changing artist-creation order changes the tight bbox, hence the
   SVG's intrinsic size, hence card layout. The fingerprint's rounded
   width/height catches it; a human must confirm cards do not reflow.
4. **The pace chart against real data** — run-length compression plus ymax
   clamping plus today-marker-after-`set_ylim` is the most numerically fragile
   code in the file. Render a ~1,000-project resource and compare paths and file
   size; `:1891-1893` warns of a 20 MB SVG if the grouping breaks.
5. **All 20 empty-state sites** — these are htmx fragments, so an exception 500s
   a card rather than degrading.
6. **`is_empty` and numpy.** A base default of `return not data` raises
   `ValueError: truth value of an array is ambiguous` for any hook returning an
   ndarray (pace's `rates`, the histogram's `band_rates_full`). Define it
   explicitly per family. This is the likeliest way to ship a 500.
7. **Decimal/float.** `:1108-1112` documents that scan rollups arrive as
   `decimal.Decimal` and coerces at one entry point. Without `float()` in the
   adapter, `cum += v` in `_pie_cumulative_keep` raises `TypeError`.
8. **App context.** `ModalRoute` calls `url_for`, so it must resolve lazily inside
   `url()`; eager resolution at class-definition time breaks at import.
9. **Import cycle.** `base.py` imports `webapp.caching`, which imports blueprint
   modules under `bucketed_caches()`, which import `charts`. Today this is avoided
   because `caching/__init__.py:148` imports lazily inside the method. Confirm
   `webapp.caching`'s top level stays chart-free.
10. **Cache-key drift is silent** — nothing fails, it just serves cold for 600 s or
    aliases two charts. Prefer verbatim moves over rewrites.

---

## Verification

- Full suite green at every commit; final tally recorded in the PR.
- `docker compose up webdev --watch` → http://localhost:5050, and walk every
  chart surface: user resource-details (compute + disk), allocations dashboard
  and pace chart, status dashboard (nodetype, partition, queue, user/proj), jobs
  explorer (histogram, timeline, both usage pies), disk-scans (entities,
  distribution).
- Playwright at 390×844 / 820×1180 / 1280, per the recipe in `MOBILE_FRIENDLY.md`:
  the metric is `document.documentElement.scrollWidth == clientWidth` at 390 px,
  measured **after htmx fragments settle** — a page can measure clean at load and
  blow out once its fragment lands. `/allocations/projects` is Redis-cached
  per-user; append `?cachebust=1`. Confirm the two zero-CSS wrapper classes no
  longer overflow.
- Admin Caching card: 15 rows, correct order, correct names, counters
  incrementing.
- Poppins actually applied, not the DejaVu fallback.

---

## Deploy notes

The chart cache key hashes *input data*, not rendering code, so warm Redis entries
serve old-code SVGs after deploy — bounded by the 600 s TTL
(`redis_chart.py:21`). The C3 drill-scheme change makes this user-visible, since
stale SVGs carry dead hrefs. Run `sam-admin cache --refresh --category chart` as
part of the deploy. The in-process fallback dies with the worker and is
unaffected.

---

## Appendix A — the 16 charts and where they are consumed

| Chart | Route / caller |
|---|---|
| usage timeseries (flat + stacked) | `dashboards/user/blueprint.py:937,942` |
| user usage pie | `dashboards/user/blueprint.py:980` |
| disk usage stacked area | `dashboards/user/blueprint.py:1064` |
| facility pie | `dashboards/allocations/blueprint.py:419,497` |
| allocation-type pie | `dashboards/allocations/blueprint.py:429,481` |
| pace chart | `dashboards/allocations/blueprint.py:579` |
| nodetype history | `dashboards/status/blueprint.py:294,361` |
| queue history | `dashboards/status/blueprint.py:411` |
| user/proj stacked area | `dashboards/status/blueprint.py:543` |
| jobs usage pie | `jobs/routes.py:952` |
| jobs timeseries stacked | `jobs/routes.py:1167` |
| jobs histogram | `jobs/routes.py:1274` |
| disk entity pie | `disk_scans/routes.py:552` |
| distribution histogram | `disk_scans/routes.py:614` |

## Appendix B — chrome-colour inventory (input to the dark-mode pass)

Every colour below is **chrome** and must move onto `Theme`. Data/series colours
(the `UNITY_*` palettes) stay fixed.

| Site | Today | Theme field |
|---|---|---|
| `:67,68,70,73,74` rcParams title/label/edge/xtick/ytick | `#011837` | `text`, `spine` |
| `:75-77` `grid.color` | `#bbbcbc` @ α0.4 | `grid` |
| **rcParams `text.color` — never set** | matplotlib default **black** | `text` (latent bug; see C12) |
| `:282,358,776,789,1389,1404,1551` bar `edgecolor` | `UNITY_NCAR_NAVY` | `bar_edge` |
| `:795,1412` stack-segment `edgecolor` | literal `'white'` | `segment_edge` |
| `:844,862,911,927` dual-panel legend box | `facecolor='white', framealpha=0.9` | `legend_face` |
| `:845` nodetype ax1 grid | literal `'grey'` | `grid` |
| `:688` `_shade_family` blend target | pure white | `shade_toward` |
| `:1724,1966,1968` pace "today" marker + label | navy @ α0.7 | `accent` |

**Theme-invariant, leave alone:** `_autopct_color_for` (`:197-205`) picks label
colour from the *wedge* luminance, not the page, so it is already correct for both
themes.

**Needs a design decision, not a mechanical swap:** `UNITY_PALETTE_10[8]` is
`#011837` (space blue) used as a **pie wedge fill** (`:118`) — on a dark page it
vanishes into the background while still carrying a white percentage label. And
the `alpha=0.85` stackplots (`:482`, `:595`) plus `_PACE_OTHER_COLOR` composite
against the *page*, so every stacked band desaturates on dark.
