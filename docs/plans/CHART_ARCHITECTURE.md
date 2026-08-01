# SAM chart architecture

**Status: PROPOSED (2026-07-31), verified against source 2026-07-31.**
Branch `chart-architecture-refactor`. This document is the design; no code has
been written.

An OO refactor of `src/webapp/dashboards/charts.py`, plus two new rendering
axes — **layout** (desktop / mobile) and **theme** (light / dark) — that the
current flat-function design cannot express. Neither axis is *wired* here; both
are made possible, with today's rendering preserved exactly as the default.

All line references are against `charts.py` at 2,011 lines unless otherwise
qualified. Counts in this document were re-verified against the working tree;
where an earlier draft was wrong the corrected number is used and the error
noted, because several of these numbers are commit-completion gates.

---

## Roadmap — this PR and the three that follow

| # | PR | Depends on | Ships |
|---|---|---|---|
| **1** | **Chart architecture refactor** ← *this document* | — | The OO hierarchy, the structured drill scheme, `Layout`/`Theme` as inert parameters, plus cosmetic normalization (C12) and `svg.fonttype` (C2a). Visual change is permitted but confined to declared commits. |
| 2 | Mobile-friendly charts | 1 | Wires the `mobile` layout: transport, `_parse_layout()`, per-family mobile profiles, CSS for the unstyled wrappers. |
| 3 | App-wide dark mode | — (parallel to 2) | `data-bs-theme`, cookie carrier, `variables.css` dark block, the 83 hardcoded-white sites. **Separate planning session.** |
| 4 | Dark-mode charts | 1 **and** 3 | Wires the `dark` theme through the axis PR 1 built. |

PR 1 is the only one specified here. Its job is to make 2 and 4 *small*, and to
leave 3 unblocked. § **Groundwork the follow-on passes will find waiting**
records what was measured for PR 2 specifically; PR 3 gets only the constraints
that would be expensive to discover late (§ *Explicitly not in scope*).

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
- **`docs/plans/implemented/MOBILE_FRIENDLY.md` § non-goals** ruled charts out
  entirely: "*No chart refactors. SVG/matplotlib charts stay exactly as they
  are.*" That pass is **IMPLEMENTED — all six of its work items shipped**
  (2026-07-19, branch `mobile_polish`, merged in #374); its surviving
  "work items, in priority order" list is historical, not a backlog. Charts were
  the one thing it deliberately did not touch, and that deferral is what this
  plan reopens.

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

**A. Pie — 5 functions** (`:958`, `:1005`, `:1091`, `:1184`, `:1615`). A 16-line
`subplots` + `ax.pie(...)` + autopct-recolour block is **byte-identical** in all
five (`:979-994`, `:1026-1041`, `:1129-1144`, `:1219-1234`, `:1669-1684`),
followed by a legend call that splits only on clickability: the two `_pie_trim`
pies discard the return (`ax.legend(...)`), the three `_pie_cumulative_keep` pies
bind it (`legend = ax.legend(...)`) so they can wire clicks. An 8-line
click-wiring loop then repeats 3× (`:1146-1159`, `:1236-1249`, `:1686-1700`).
Real variation: trim strategy (`_pie_trim` fixed-cap-10 `:943` vs
`_pie_cumulative_keep` 90 %-cumulative `:1063`), "Other" derivation (3 variants),
legend formatter (`fmt.number` vs `fmt.size` `:1127`), drill target (4). Note the
trim strategy and the cache-key strategy align exactly: the two `_pie_trim` pies
are also the two that use the default `key_fn`.

**B. Stacked series with clickable legend — 5 functions** (`:306`, `:419`,
`:535`, `:1488`, `:1804`):

- *palette cycling with an "Others" grey case* — **4** copies (`:340-347`,
  `:474-481`, `:586-595`, `:1538-1546`). `:586-595` deliberately **reverses** the
  index and uses `UNITY_STACK_20`; a real semantic difference that must remain a
  visible override, not collapse into a flag. **Pace is not one of the four** —
  it builds `color_map` directly at `:1885` against a module-level
  `_PACE_OTHER_COLOR` (`:1723`), which is one more reason it resists the family.
- *proxy-`Patch` legend* — **5 blocks, of which 4 are reversed** (`:372-385`,
  `:494-506`, `:604-636`, `:1576-1584`); pace's (`:1976-1990`) builds handles
  **forward** over `top_projs` and appends an "Other" patch past the zip. Do not
  fold pace's into the shared reversed helper. `:604-636` is the long variant,
  carrying an inlined `_legend_value` closure at `:613-622`. Each of the five is
  preceded by a redundant function-local `import matplotlib.patches as mpatches`.
- *legend link-wiring* — **5 zip-over-legend loops, 4 with the "Others" skip**
  (`:388-393`, `:509-514`, `:640-646`, `:1587-1593`); pace's (`:1997-2000`) has
  no skip because its "Other" sits past the zip. The four differ in their gate:
  unconditional, `link_kind == 'user'`, `link_kind in ('user','project')`,
  `if link_entities`.

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

- **20** `_empty_state` call sites (21 matches, 1 is the def at `:99`); **27**
  `set_url` call sites (35 matches — 8 are docstring/comment mentions at `:210`,
  `:256`, `:371`, `:431`, `:491`, `:551`, `:602`, `:1052`). *An earlier draft said
  39; the grep had not excluded prose.*
- **Consumers**: 18 call sites across `dashboards/user/blueprint.py` (4),
  `dashboards/allocations/blueprint.py` (5), `dashboards/status/blueprint.py`
  (4), `jobs/routes.py` (3), `disk_scans/routes.py` (2). All render an htmx
  fragment containing `{{ chart_svg | safe }}`.
- **`generate_jobs_user_pie_chart` has zero call sites in `src/`.** The import at
  `jobs/routes.py:63` is dead; the live By-User path is
  `generate_jobs_usage_pie_chart(..., sentinel_prefix=entity['sentinel'])`
  (`routes.py:952-953`). Only `test_webapp_jobs_charts.py:21` exercises it. Keep
  the facade (deleting it is a test edit for no gain) but **drop the dead import
  in C11**.
- **The module docstring at `:4` is already wrong**: "*All chart functions are
  decorated with `@caching.chart_cached(...)`*" — `generate_jobs_user_pie_chart`
  is not. Fix while rewriting it.
- **Threading**: `containers/webapp/gunicorn_config.py:69,73` — `worker_class =
  'gthread'`, `threads = 4`. Four threads share one process-global
  `matplotlib.rcParams`.
- **CSS**: `dashboard.css:1774-1783` scales finished SVGs
  (`max-width:100%; height:auto`). **Three** wrapper classes opt out of
  `.chart-container` and have **zero CSS rules anywhere**:
  `jobs-histogram-chart` (`jobs_histogram.html:145`), `jobs-timeline-chart`
  (`jobs_timeline.html:98`) and `jobs-user-pie` (`jobs_usage_panel.html:81`).
  `disk-entity-pie` (`disk_scans_entities.html:63`) is also bare-wrapped but
  *does* have a rule (`dashboard.css:1788-1790`), so it is a deliberate
  exception, not an oversight. **The failure mode is not overflow — see the
  measurement below.**
- **Palette is quadruplicated, not triplicated**: `variables.css:38-52` (13 hex
  tokens + 2 rgb-triplet tokens), `charts.py:122-132` (11 named Python scalars),
  `charts.py:109-120` (`UNITY_PALETTE_10` — the same 10 hexes again as literals),
  and the rcParams block `:67-75` (`'#011837'` ×5 and `'#bbbcbc'` re-inlined,
  because the named constants are defined 55 lines later). Plus
  `charts.py:142-194` (`UNITY_STACK_20/10`, ~13 tint hexes with no CSS
  equivalent). All 11 scalars match CSS **exactly, case-identical** — zero
  mismatches. Two CSS tokens have no Python scalar: `--ncar-cyan: #34e1f4`
  (`:44`, though it appears inside `UNITY_STACK_20` at `:164`) and
  `--ncar-navy-mid: #003579` (`:53`).
- **Latent hex-case split inside `charts.py`**: the STACK tuples spell two brand
  colours lowercase — `#00a2b4` (`:158`, `:190`) and `#42c0ff` (`:162`, `:191`) —
  while the scalars and CSS use `#00A2B4`/`#42C0FF`. Same colour, different
  string. Any dedup pass that compares hex strings naively will treat them as
  distinct. (Harmless for the fingerprint: matplotlib normalizes to lowercase in
  SVG output.)

### Measured: what is actually wrong with charts on a phone

Taken with Playwright against `webdev` at 390×844, logged in as `benkirk`,
**after htmx fragments settled**. This replaces the earlier draft's guess that
the unstyled wrappers were "live mobile-overflow candidates" — they are not.

**No page overflows.** `scrollWidth == clientWidth == 375` on `/user/jobs`,
`/allocations/projects?cachebust=1`, and `/status/derecho`. `MOBILE_FRIENDLY`'s
item 1 did its job, and the bare `d-flex` wrappers do not overflow either,
because a flex item shrinks below its intrinsic width by default.

Two real defects, neither of them overflow:

**(a) Aspect-ratio letterboxing on the three unstyled wrappers.** Without
`height:auto`, the SVG box keeps its intrinsic `height` attribute while flexbox
shrinks its width, and the viewBox letterboxes the content inside it:

| Wrapper | Intrinsic | Rendered box | AR intrinsic → rendered |
|---|---|---|---|
| `.chart-container` (facility pie) | 504×353pt | 311×217px | 1.43 → **1.43** ✅ |
| `.jobs-histogram-chart` | 835×334pt | 277×**446**px | 2.50 → **0.62** ❌ |

The histogram draws into ~277×111px and pads ~335px of empty vertical space
around it. **One CSS rule fixes it**, and it is already scheduled in C11 — the
action was right in the first draft, only the stated reason was wrong.

**(b) The wide charts are illegible, and no CSS can fix that.** The
`.chart-container` chart on `/status/derecho`: intrinsic 1281×453pt (post
`bbox_inches='tight'`) rendered at 287×101px — **scale 0.224**. Axis and legend
text is set at 9–11 pt, so it lands at **≈2.0–2.5 px on screen**. The facility
pie fares better only because it starts at (7,4) rather than (18,10).

That is the whole argument for the `layout` axis, and it is why PR 2 cannot be a
CSS-only pass: *the fix is to re-render at a different `figsize`/`base_fontsize`,
which requires the server to know the layout.* Everything `Layout` carries —
`figsize`, `base_fontsize`, `max_ticks`, `legend_placement`, `label_rotation` —
is a lever on that 0.224.

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

All four are single-positional-argument functions today, so the default is
currently *correct* — which is precisely why it is dangerous: nothing is broken,
nothing will fail, and the first kwarg anyone adds silently poisons the cache.

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
`ROW_SENTINELS` prefix→attribute table in `svg-chart-links.js:71-85`. **Seven of
the nine** already funnel through one generic handler,
`openEntityRow(attr, id, scope)` (`#ah-bar-`, `#disk-ent-owner-`,
`#disk-ent-group-`, `#jh-bar-`, `#jt-bar-`, `#job-user-`, `#job-proj-`); the
remaining two are bespoke openers by design (`#day-bar-` → `openDayRow`,
`#usage-user-` → `openUserRow`, rationale at `svg-chart-links.js:62-65`). The
problem is that **the attribute name lives in the JS**, so adding a drill-down
chart requires editing JavaScript. The template side already treats that
attribute as data: `disk_scans_entities.html:94` computes
`'data-owner-uid' if _is_owner else 'data-group-gid'`.

One asymmetry to preserve deliberately: the sentinel branches match with
`indexOf(prefix) === 0` while the `MODAL_ROUTES` branch uses
`indexOf(prefix) === -1` (substring, not prefix). Keep modal matching as-is —
§ *Modals are left alone*.

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

**Cost:** 18 references in the JS (exactly 2 per prefix — one in the module
header comment `:12-36`, one in code), 23 in `charts.py`, and **51 sentinel-string
occurrences across 50 test lines** — `test_webapp_jobs_charts.py` 35 (34 lines;
one line carries two), `test_webapp_disk_scans.py` 9, `test_webapp_jobs.py` 4,
`test_resource_details_partials.py` 3. These are string *references*, not all of
them bare `assert`s. Plus 8 prose comments. All template and route references are
**comments only** — verified, no functional coupling.

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
  the public callables (`caching/chart.py:128-130`, `redis_chart.py:167-169`), so
  `utils/profiling/profile_allocations.py:149` and
  `test_allocations_performance.py:470-503` need zero edits. Preserve the
  existing name asymmetry verbatim — the *attribute* is `cache_bytes` but the
  underlying method is `bytes_used()` (`chart.py:75`, `redis_chart.py:76`).
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

Callers keep owning exception handling, inconsistently, and that inconsistency is
pre-existing and out of scope: `disk_scans/routes.py:610-621` opens its `try` for
`service.scan_distribution()` and the chart call at `:614` merely falls inside
it (failure → `chart_svg=None` + an error string), whereas `jobs/routes.py`
closes its `try` at `:951` and calls the chart at `:952` **outside** it, guarded
only by an `if usage else None` truthiness check — so a raise there is a 500. The
other 16 call sites are unwrapped. Conclusion for the design: **`BaseChart` must
not swallow exceptions**, or `disk_scans` silently starts rendering blank cards
where it used to show an error. (Normalizing the 18 call sites is a reasonable
follow-up; it is not this PR.)

---

## File layout

Planned, then **as shipped**:

```
src/webapp/dashboards/charts/          planned   actual
  __init__.py     facade: 15 chart_view bindings,   ~150     231
                  re-export aliases, __all__
  theme.py        fonts + rcParams, UNITY_* palettes, ~230     358
                  Theme.LIGHT / DARK, scale_bytes
  layout.py       Layout + per-family profiles        ~80     105
  base.py         BaseChart + chart_view + to_svg    ~200     224
  links.py        drill targets  [no matplotlib]      ~80     144
  series.py       Series + adapters [no matplotlib]   ~70      99
  jobs_metrics.py plugin envelopes  [no matplotlib]     —     110
  pie.py          PieChart + 5 concretes             ~240     388
  stacked.py      StackedSeriesChart + 5 concretes   ~270     493
  histogram.py    CategoricalStackChart + 2          ~180     321
  dualpanel.py    DualPanelTimeSeriesChart + 2       ~140     182
  pace.py         PaceChart + bands + key fields     ~200     362
                                                   ──────  ──────
                                                    ~1840    3017
```

**The estimate was 60 % low, and the reason is worth recording.** The plan said
*"LOC reduction is explicitly not a goal"* and predicted roughly break-even.
Actual is +1,000 lines over the 2,011-line original. Three causes, in order of
size:

1. **Prose.** The original was ~49 % comment/docstring, and every load-bearing
   note was preserved *and* given the context its new location needed — plus
   new docstrings explaining each family's hooks and each deliberate
   non-unification. This is the bulk of the growth and it is the point.
2. **Explicit hooks beat implicit copies.** `bucket_is_clickable`,
   `flat_bar_color`, `legend_label`, `band_values` each cost a method
   definition per subclass where the flat version had an inline expression.
3. **`jobs_metrics.py` was unplanned** — extracted in C11 once three modules
   needed it.

`stacked.py` at 493 exceeds the "no file over ~270" target; it holds five
concrete charts and is the honest home for them. A 550-line ceiling is
enforced by `test_chart_module_boundaries.py` so nothing drifts back toward
the monolith.

*See `feedback_extraction_loc_estimates` — discount extraction LOC estimates
here by ~3x, and lead with structural counts rather than line count.*

---

## Commit series

One branch, one PR → `staging`, commits C0–C12, **all in PR 1**. The suite must
be green at every commit.

Visual changes are permitted in this PR (see § *The discipline* below) but are
**confined to designated commits** — C2a and C12 — so that the refactor commits
stay independently verifiable.

**C0 — Characterization harness.** `svg_fingerprint()` plus pinned fingerprints
for all 16 charts including empty-data cases; the facade `dir()` snapshot; the
15-name cache-order test. Written *before* any source moves.

**C1 — `git mv charts.py charts/__init__.py`**, content byte-identical. Proves
the package form; imports and tests untouched.

**C2 — Extract `theme.py`.** rcParams, palettes, `_autopct_color_for`; the two
byte ladders become `scale_bytes(peak, floor)`. `Theme.LIGHT` defined but not yet
threaded.

**C2a — `svg.fonttype = 'none'`** *(visual; one-line revert; decide at the browser
check)*. Today matplotlib renders every glyph as a path outline: one measured
chart carries **376 `<use>` glyph refs and 98 `<path>`s for zero `<text>`
elements**. Switching to `'none'` emits real `<text>` and buys four things at
once — a much smaller SVG payload (directly relevant to the pace chart's
documented 20 MB blowup risk at `:1891-1893` and to Redis sizing at ~49 KB/pie),
selectable and screen-readable chart text, label colour that PR 4 can theme from
CSS, and a fingerprint whose text component is native rather than glyph-id
inference.

Safe to attempt because Poppins is already vendored to the browser as woff2
(`static/vendor/poppins/`, 15 faces; `document.fonts.check('11pt Poppins')` is
true in the running app). **The risk is real and specific**: matplotlib still
computes the `bbox_inches='tight'` box from *TTF* metrics while the browser
re-lays-out the glyphs from *woff2*, so long labels in tight legends may sit a
pixel or two differently. Land it early so the whole refactor benefits, verify in
the browser immediately, and revert the one line if the drift is visible. If it
sticks, simplify the C0 fingerprint's text component from glyph-id sequences to
`<text>` content in the same commit.

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
idioms (`.chart-container` — 8 SVG render sites, plus 6 htmx *target* divs in
`allocations/projects.html` that receive partials rather than rendering SVG —
vs bare flex + bespoke class, 4 sites), mirroring `modal_scaffold(variant=…)`.
**CSS `max-width:100%; height:auto` for the three zero-rule wrapper classes**
(`jobs-histogram-chart`, `jobs-timeline-chart`, `jobs-user-pie`) — this fixes the
measured aspect-ratio letterboxing, not an overflow. Plus housekeeping:

- `pyproject.toml:101` omits `src/webapp/utils/charts.py` from coverage — a path
  that **does not exist**, so `dashboards/charts.py` was never actually excluded
  and *is* counted against `fail_under = 75.0` (`pyproject.toml:165`). Splitting
  the module will shift coverage; fix the stale entry deliberately rather than
  discovering it as a red CI run.
- `helm/values.yaml:40` says "13 chart SVG caches"; there are 15.
- `profile_allocations.py:65` credits `_attach_cache_methods()`, long gone.
- `charts.py:34` calls `_content_hash` a "legacy alias used by
  `_pace_cache_key`"; `_jobs_histogram_cache_key` also calls it (`:1332`).
- Drop the dead `generate_jobs_user_pie_chart` import at `jobs/routes.py:63`.
- `src/webapp/README.md:92`; add a **§ Charts** section to `CLAUDE.md`, which has
  none, covering the families, the drill scheme, the render axes, and the "add
  chart #17" recipe.

**C12 — Cosmetic normalization** *(visual, by design)*. Now that the families
exist, the accumulated inconsistencies are one-line class attributes instead of
16 scattered edits — which is exactly why this belongs at the *end* of the
refactor rather than in a separate PR. Normalize: pace grid alpha 0.2→0.3; the
undocumented `color='grey'` on nodetype ax1 (`:845`); legend fontsize
9/10/11/13; bbox anchor `(1.0)` vs `(1.01)`; `frameon`. **And one latent bug:**
`text.color` is never set in rcParams, so every legend label in every chart
renders pure black rather than the intended space-blue.

Latitude granted for this commit: tweak styling in the spirit of the current
charts wherever the refactor exposed something that simply looks wrong. Keep it
to chrome — palette, data encoding and chart *types* are not in play.

### The discipline: declared visual change, not zero visual change

PR 1 is allowed to change how charts look. What it is **not** allowed to do is
change how they look *by accident*, in a commit whose stated purpose was
something else. So the rule is not "the fingerprint never moves" — it is:

> **The fingerprint may move only in a commit that declares a visual change.**
> C2a, C3 (href scheme) and C12 declare one. C1, C2, C4–C11 do not, and a
> fingerprint delta in any of those is a bug until proven otherwise.

That distinction is what keeps the gate meaningful. If restyling were sprinkled
through C5–C10, a fingerprint diff would carry two meanings at once — "I
restructured this" and "I restyled this" — and reviewing it would collapse into
eyeballing 16 charts by hand, which is the status quo this plan exists to escape.
Batching every intentional change into three named commits means each refactor
commit still answers one question cleanly: *did moving this code change its
output?*

Corollary: every style inconsistency is still preserved as a class attribute
through C4–C11 **even where it looks like an accident** — not because the
accident is sacred, but because C12 is where it gets fixed, in one reviewable
place, with the fingerprint delta as the evidence.

---

## Explicitly not in scope

- **Any actual mobile rendering** (PR 2). No transport, no `matchMedia`, no CSS
  media queries for charts, no visual tuning of the `mobile` layout. Only the
  parameter and a defined profile. The one exception is C11's CSS for the three
  zero-rule wrapper classes, which is a today-bug fix, not layout work.
- **Any actual dark mode** (PR 3, then PR 4). Chart dark mode is *blocked* on app
  dark mode: charts live inside `.card`s whose background is hardcoded `#fff`,
  and there are **83** hardcoded `#fff`/`#ffffff`/`white` occurrences across the
  app CSS files bypassing the token layer (dashboard.css 75, auth.css 3,
  admin.css 2, allocations.css 1, components.css 1, variables.css 1). Transparent
  SVG buys nothing until the surrounding chrome is themed. No `data-bs-theme`
  usage and no `prefers-color-scheme` block exists outside `static/vendor/`
  (Bootstrap 5.3.3 ships its own `[data-bs-theme=dark]`, currently unused — so
  PR 3 has a real head start).
  One scoping note for that session: `.card` is the *easy* case — it is set via
  `--bs-card-bg: #fff` / `--bs-card-cap-bg: #fff` (`dashboard.css:577,580`), a
  two-line token swap. The expensive sites are the nine raw
  `background-color: #fff` declarations (`:29`, `:35` navbar, `:1135`, `:1184`
  `.nav-tabs .nav-link`, `:1204`, `:1211`, `:1233`, `:1280`, `:1423`).
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

*Mobile.* The transport rail exists and **already solves the hard part** —
`nav-view-persistence.js:324` listens on `htmx:configRequest` and injects saved
params into outgoing requests. Its `injectSaved` helper (`:290-316`) injects
first and then **strips those keys from `detail.path`**, so the injected value
is authoritative:

```js
// htmx appends `parameters` to `path` for GET requests, so any param already
// encoded in the hx-get URL would produce a duplicate query key
// (?metric=jobs&metric=cores). Werkzeug's request.args.get returns the FIRST
// value, which silently defeats the override. Strip ours from `path` so the
// injected parameters become authoritative.
```

*(An earlier draft of this plan described that de-dup backwards, as a hazard to
work around. It is the opposite: storage already wins over the URL. **Reuse
`injectSaved` — do not re-derive the precedence rule**, and note that any code
assuming URL-precedence will regress.)* A sibling listener sets `layout` from
`matchMedia`.

Server-side, add `_parse_layout()` in the style of `jobs/routes.py:723-733`
`_parse_period`, the best model in the repo: it honours an explicit choice *only
while it fits* and falls back explicitly ("*Lenient like `_parse_metric` —
unknown values mean 'no override', never a 400*"). House rule in these routes —
never 400 on a bad selector, clamp silently, because a stale localStorage replay
must not break a panel. Charts arrive via `hx-trigger="load"`/`intersect`, so
`configRequest` fires after first paint, which is fine and is the only place it
can be evaluated (`dashboard-init.js:225-232`: "*markup can't be
viewport-conditional*").

PR 2 therefore reduces to: the `matchMedia` sender, `_parse_layout()` at the
fragment boundary, real `figsize`/`base_fontsize` numbers per family, one CSS
rule for the three unstyled wrappers, and cache-budget retuning (below).

*Dark.* A **cookie** is the right carrier, not localStorage — it avoids the
first-paint flash and sidesteps the CSP posture, which is stricter than an
earlier draft assumed. CSP is **not** a `<meta>` tag in `base.html`; it is
emitted from an `after_request` (`utils/security_headers.py:36-67`) built by
`utils/csp.py:56-69`, enforcing by default (`config.py:93`) with:

```python
'default-src': [SELF],
# Nonce-free: no 'unsafe-inline', no nonces (cached-HTML constraint).
'script-src':  [SELF],
```

Three reinforcing constraints, all of which PR 3 must respect: it is **nonce-free
by design** because four routes cache rendered HTML in Redis per-user, so a
per-request nonce goes stale on cache hits (`csp.py:11-19`);
`tests/unit/test_template_csp_lint.py` fails CI on any inline executable script;
and `templates/dashboards/base.html:5-13` restates the same. So the classic
inline anti-flash script is **not available** — the cookie must be read
server-side into `<html data-bs-theme>`. Data rides `data-*` attributes or
non-executable `<script type="application/json">`. `style-src` does keep
`'unsafe-inline'` (`csp.py:60-61`).

Flask reads the same cookie value and passes it to the chart renderer.
Appendix B is the complete chrome inventory PR 4 will need.

---

## Test strategy

**No golden SVGs.** Matplotlib SVG is unstable three independent ways: `<dc:date>`
is a wall-clock stamp, so byte-equality breaks between two runs of *identical*
code; clip-path ids derive from a per-process hash salt; and path data, font
metrics and `bbox_inches='tight'` dimensions shift across matplotlib patch
releases. The first two are suppressible (`metadata={'Date': None}`,
`rcParams['svg.hashsalt']`); the third is not, and would make every matplotlib
bump a 16-file golden churn.

**Use a structural fingerprint** — `svg_fingerprint(svg) -> dict`. An earlier
draft of this spec had **two components that would have been silently empty**;
both were caught by dumping a real chart SVG from the running app, and the
corrected spec is below. This matters more than any other detail in the plan: a
fingerprint that returns `[]` still *passes*, so a broken gate looks exactly like
a green one.

`svg.fonttype` is **not** set in the rcParams block (`:63-82`), so matplotlib
3.11's default `'path'` applies: **text is emitted as glyph outlines, not
`<text>` elements.** Measured on one live chart:

| | Count |
|---|---|
| `<text>` elements | **0** |
| `xlink:href` attributes, total | **406** |
| …of which are real drill links (`<a>` elements) | **30** |
| …of which are `<use>` glyph / clip-path references | **376** |
| `<use>` | 376 |
| `<path>` | 98 |

So, corrected:

- **drill contract** → ordered `@href` of **`<a>` elements only**
  (`root.iterfind('.//{*}a')`). Taking *every* `xlink:href` yields 93 % glyph
  noise, and — fatally — includes hash-salted clip-path ids like
  `#m9571e8cb24`, which change per process. That component would have been
  *unstable*, i.e. the exact failure the "no golden SVGs" argument exists to
  avoid, reintroduced one paragraph later.
- **text contract** → the ordered sequence of **glyph ids** per text group
  (`<use xlink:href="#Poppins-Regular-2a5">` → `Poppins-Regular-2a5`). Glyph ids
  are stable per (font, character), so the *sequence* is a faithful proxy for the
  string without decoding it — a label change, a legend reorder, a tick-format
  change all move the sequence. Filter out the salted `^m[0-9a-f]{10}$` ids.
  Optionally pin `rcParams['svg.hashsalt']` in the harness to make those ids
  deterministic too, but filtering is simpler and does not touch global state.
- **palette contract** → ordered `fill:#rrggbb` from style attributes, including
  the reversal. Matplotlib lowercases, so the `#00A2B4`/`#00a2b4` source split
  does not leak in.
- **geometry** → counts of `<path>` (excluding `<defs>`), `<use>`, `<g>`.
- **layout** → root `width`/`height` to 1 dp → catches `bbox_inches='tight'`
  shifts.

Stable across runs and matplotlib patch versions; sensitive to everything this
refactor could plausibly break.

> **Note the ordering dependency with C2a.** The glyph-id fingerprint above is
> written so that it works *today*, with `svg.fonttype` at its `'path'` default —
> it must, because C0 lands before C2a and has to characterize the code as-is. If
> C2a sticks after its browser check, the text component simplifies to plain
> `<text>` content and the pinned fingerprints are regenerated in that same
> commit, with the diff reviewed as the declared visual change. If C2a is
> reverted, the glyph-id form stays and nothing else in the plan moves. Do not
> make C0 depend on C2a's outcome.

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
9. **Import cycle — verified clear today, and must stay that way.** `base.py`
   will import `webapp.caching`, whose top level imports only
   `sam.caching`, `webapp.caching.chart` and `webapp.caching.flask_adapter`
   (`:37-39`) — no blueprint, no chart module. Everything that *would* close the
   cycle is deliberately lazy: `redis_chart` inside `chart_cached` (`:119`), the
   bucketed modules inside `bucketed_caches()` (`:150`), `flask`/`usage_cache`
   inside `stats()` (`:183-184`), with the rationale documented at `:44-47` and
   `:133-137`. **Add a test asserting `webapp.caching`'s top level stays
   chart-free**, because the failure is an import-time crash of the whole app,
   and the guard is currently only a comment.
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
- Playwright at 390×844 / 820×1180 / 1280, per the recipe in
  `docs/plans/implemented/MOBILE_FRIENDLY.md:39-56`: the metric is
  `document.documentElement.scrollWidth == clientWidth` at 390 px, measured
  **after htmx fragments settle** — a page can measure clean at load and blow out
  once its fragment lands. `/allocations/projects` is Redis-cached per-user;
  append `?cachebust=1`. Quick Login as `benkirk`.
  The assertion through C11 is **no regression**: overflow stays 0 (it already
  is — see § *Measured*), and the aspect-ratio check
  `intrinsicWidth/intrinsicHeight ≈ renderedWidth/renderedHeight` holds wherever
  it holds today — correct under `.chart-container`, and *still* wrong for the
  three bare wrappers right up until C11's CSS lands and fixes them.
- **C2a needs its own browser pass**, and it is the only step in the plan with a
  real fidelity risk. Compare before/after at 1280 px on the two worst cases:
  a long-label legend (jobs By-Project pie) and the densest tick axis (nodetype
  history). Check text is not clipped by the tight bbox, and confirm the SVG is
  in fact smaller. Revert the one line if anything drifts visibly.
- **C12 is a deliberate visual diff** — walk the same chart surfaces and confirm
  each change is the intended one, in particular that legend labels have gone
  from black to space-blue everywhere.
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
themes. (It does return a bare `'#fff'` at `:205` — a literal, but a correct one.)

**Needs a design decision, not a mechanical swap:** `UNITY_PALETTE_10[8]` is
`#011837` (space blue) used as a **pie wedge fill** (`:118`) — on a dark page it
vanishes into the background while still carrying a white percentage label. And
the `alpha=0.85` stackplots (`:482`, `:595`) plus `_PACE_OTHER_COLOR` composite
against the *page*, so every stacked band desaturates on dark.

## Appendix C — corrections applied on re-verification (2026-07-31)

Every count in the first draft was re-checked against the working tree. Recorded
here because several are commit-completion gates, and because two of them were
defects in the *plan* rather than in the code.

| # | First draft said | Actually | Impact |
|---|---|---|---|
| 1 | fingerprint on ordered `<text>` content | **0 `<text>` elements** — `svg.fonttype` defaults to `'path'` | **Defect.** That component returns `[]` for every chart and still passes. Respecified on glyph-id sequences. |
| 2 | fingerprint on every `xlink:href` | 406 attrs, only **30** are drill links; the rest are glyphs + **hash-salted** clip-path ids | **Defect.** Would be both 93 % noise and run-unstable. Respecified to `<a>` elements only. |
| 3 | the bare wrappers are "live mobile-overflow candidates" | **No page overflows at 390 px.** Real defect is aspect-ratio letterboxing (AR 2.50 → 0.62) | Right fix, wrong reason. Also surfaced the actual mobile problem: 0.224 render scale ⇒ ≈2 px text. |
| 4 | `configRequest` de-dup is a hazard to work around | It already **strips injected keys from `detail.path`** so storage wins | Reuse `injectSaved`; don't re-derive. |
| 5 | CSP is a `<meta>` in `base.html:7-14` | `after_request` via `utils/csp.py`; **nonce-free by design** (Redis-cached HTML) + CI lint | Stricter than assumed; constrains PR 3's anti-flash approach. |
| 6 | `MOBILE_FRIENDLY.md` is a live plan in `docs/plans/` | `docs/plans/implemented/`, **all six items shipped** | Its "remaining items" list is historical. |
| 7 | 39 `set_url` sites | **27** (8 of 35 matches are prose) | Gate count. |
| 8 | 5 reversed proxy-`Patch` legends | **5 blocks, 4 reversed** — pace's is forward | Folding pace in would invert its legend. |
| 9 | palette triplicated, ~14 CSS tokens | **quadruplicated**; 2 CSS-only tokens; `#00A2B4`/`#00a2b4` case split | Naive hex dedup would miss two. |
| 10 | 6 of 9 sentinels use `openEntityRow` | **7 of 9** | — |
| 11 | 2 zero-CSS wrapper classes | **3** (`jobs-user-pie` too) | C11 CSS scope. |
| 12 | 81 hardcoded whites | **83**; `.card` is a 2-line `--bs-card-bg` swap, 9 raw sites are the cost | PR 3 sizing. |
| 13 | `pyproject.toml:87` | `:101`; and the omit means charts.py counts against `fail_under=75` | Coverage will move when the module splits. |

**Scope decisions taken 2026-07-31, after review:** C12 folds **into PR 1** rather
than becoming a fifth PR — PR 1 is explicitly *not* required to be zero-visual-change,
and may tweak styling in the spirit of the current charts. The zero-change rule is
replaced by the declared-change discipline (§ *The discipline*), which preserves
the fingerprint's value as a gate. This also promotes `svg.fonttype = 'none'` from
"deferred" to **C2a**, an early commit in PR 1 with an explicit revert path. Both
render axes stay in PR 1, for the cache-key reason in § *The aliasing trap*.

Verified accurate as first drafted, no change: the default-`key_fn` aliasing trap
(4 charts, all single-positional); 20 `_empty_state` sites; 16 generators / 15
caches; 11 `_*_cache_key` functions; 51 test sentinel occurrences; 18 JS
references; 18 consumer call sites; `jobs/routes.py:952` outside its `try`; the
two divergent byte ladders; the implicit `matplotlib.colors` at `:1723-1724`;
`text.color` never set in rcParams; gunicorn `gthread`×4; helm's stale "13".

Newly found, folded in: `generate_jobs_user_pie_chart` has **zero `src/` call
sites** (dead import at `jobs/routes.py:63`); the module docstring at `:4`
falsely claims all generators are cached; `_content_hash`'s "legacy alias" comment
at `:34` is stale; `cache_bytes` attribute vs `bytes_used()` method asymmetry;
the `webapp.caching` import cycle is clear today but unguarded by any test.
