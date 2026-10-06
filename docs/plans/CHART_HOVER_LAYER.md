# Chart hover layer: per-mark tooltips on server-rendered SVG

**Status:** rollout steps 1 to 3 built (step 1 in #695 to #731, steps 2 and 3 in the charts
sweep, 2026-10-05). Step 4, the styled tooltip, is unbuilt and optional. Where the build
departed from this plan is marked **As built** below.

## Why

No server-rendered chart says what a mark is unless it carries a direct label, and
direct labels run out first exactly where the reader needs them:

- **Two-ring pies** (`charts/sunburst.py`). The Job History "By facility" pie names
  each facility's top 5 projects in the outer ring. On Derecho over 30 days only 6 of
  them get a label on CPU-hours (11 on Jobs). The rest are slivers under ~1.5% of the
  machine: clickable when the project is in the 25-row table, but anonymous.
- **Single-ring pies** label only wedges of 5% or more, and use the legend for the rest.
- **Stacked bars and histograms** name each segment only in the legend. Matching a
  thin band to a legend entry by color is guesswork in a 10-hue palette.

Origin: the follow-on note in `ADMIN_TABLE_POLISH.md`, which recorded the need and
asked for this plan.

## What matplotlib gives us (verified, matplotlib 3.11)

- `Artist.set_url(u)` wraps the artist in `<a xlink:href="u" target="_blank">`. Every
  drill link is built this way (`links.py`).
- `Artist.set_gid(g)` makes the SVG backend open the artist's group as `<g id="g">`
  instead of its automatic `patch_N` id. With both set, a wedge serializes as
  `<g id="tt-0"><a xlink:href=...><path .../></a></g>`.
- Matplotlib **never** writes the SVG `<title>` element, the browser's native
  tooltip, and has no per-artist hook for one. It needs a pass over the output.
- Every chart goes through `fig_to_svg()` in `charts/base.py`, so there is one place
  to apply that pass.

## Design

### Stage 1: a native `<title>` per mark (no JavaScript)

1. `BaseChart.tooltip(artist, text)` stamps the artist with a throwaway gid
   (`tt-<n>`, numbered per render) and records `{gid: text}` on the chart instance.
2. `render()` passes the recorded tooltips to `fig_to_svg(fig, tooltips)`. After
   `savefig`, a single regex pass rewrites each `<g id="tt-N">`:
   - **If the next element is `<a>`**, put `<title>` inside it as the first child.
     That gives the drill link an accessible name; today the anchors have none.
   - **Otherwise**, put `<title>` as the first child of the `<g>`.
   - Either way, **drop the id.** The same cached SVG can appear twice on a page
     (two machine subtabs), and ids must be unique. That is the same reason
     `links.py` avoids `set_gid` for drills.
3. Escape the text with `html.escape`: projcodes are safe, but a username or
   organization name may not be.

**Family hooks**, so a chart opts in once and its subclasses inherit it:

| Family | Hook | Default text |
|---|---|---|
| `PieChart` | `tooltip_text(label, value)` | the legend's text, plus share of total |
| `TwoRingPie` | `part_tooltip(row, name, value)` | `NMMM0043 · 44,250 jobs · 11.2% of machine` |
| `StackedSeriesChart` | one tooltip per band patch (the band, not each bar) | band label + total |
| `JobsHistogram` | per bar segment | bucket + owner + value |

Line and area charts (pace, usage timeseries) want a crosshair, not per-mark text.
That needs stage 2 plus x-positions in the SVG, so they are **out of scope** here.

**As built.** Area charts did get a title, one per band (its legend row): a band is the
thing a reader cannot tell from its neighbors, and that needs no crosshair. Bar segments
carry their own value (`alice · 2026-03-03 · 20`), not the band total: the element count is
the same and the legend cannot give that figure. One helper, `BaseChart.hover`, joins every
hover; `PieChart.tooltip_text` and the histogram and stacked hooks call it.

### Stage 2 (only if stage 1 proves too plain): a styled tooltip

The native tooltip appears after about a second, uses browser styling, ignores the dark
theme, and never shows on iOS. If that matters, a few lines in `svg-chart-links.js`
(already loaded on every chart page) can:

- On `mouseover` of an SVG element that has a `<title>`, show a Bootstrap tooltip
  with that text, and hide the native one by moving the text to `data-tip`.
- Initialize from `htmx.onLoad`, because charts arrive in swapped fragments.
- **Touch:** the first tap on a mark that has a tip reveals it; a second tap on the
  same mark drills. A mark with no drill link reveals on every tap.

This builds on stage 1 and does not replace it: `<title>` stays the data channel,
and the server code does not change. CSP is unaffected (no inline script and no
`on*` attributes; the behavior lives in the static file).

## Costs and traps

- **The fingerprint delta is expected and wide.** Every chart that opts in gains
  `<title>` elements, so its `chart_fingerprints.json` entries move. Regenerate in
  the same commit, one family per commit, so each delta is attributable.
- **No cache-key change.** The text derives from the data already hashed into each
  chart's `cache_key`. A deploy that changes tooltip *wording* is a rendering-code
  change: `sam-admin cache --refresh --category chart`, as for any visual change.
  **As built**, two keys did grow. Histogram segments are keyed with their owner's name
  (the hover names it). And a hover that names a project carries its title
  (`SCSG0001 · <title> · 3.4% · 1.2M`): the route passes a `titles` mapping
  (`webapp.utils.charts.hover_titles`, one `IN` query, only for projects the viewer's
  `VIEW_PROJECTS` scope covers, or their own) and the chart hashes it into its key, so a
  retitled project is a new entry. Charts stay query-free.
- **Measured (2026-10-05).** Titles add about 11% to the bar charts (a 365-day, 11-band
  usage trend: 1.39 MB, of which 156 KB is titles) and 12% to the three-ring chart on
  Derecho (242 KB to 270 KB with 436 project titles).
- **SVG bytes grow** by roughly 40-80 bytes per mark. A 25-bar histogram with 10
  owners is ~250 marks, ~15 KB. Check the largest charts before and after.
- **Do not put a tooltip on a legend patch or legend text.** The legend already
  says what it is, and a duplicate `<title>` makes a screen reader announce it twice.
- **`gid` is otherwise unused in the chart layer.** The `tt-` prefix must stay
  reserved; a gate rejects any `tt-` id that survives into output.
- **`bbox_inches='tight'` is safe.** It changes the viewBox, not element structure.

## Gates and tests

- `fig_to_svg` unit test: an artist with a tooltip and a link gets `<title>`
  inside the `<a>`; one without a link gets it inside the `<g>`; the output
  contains no `id="tt-`; markup in the text is escaped.
- Per family: a rendered sample contains one `<title>` per expected mark.
  `chart_samples.py` cases already cover every family.
- Stage 2 only: an e2e hover check on one pie (`e2e/`), plus a touch emulation for
  tap-to-reveal, then tap-to-drill.

## Rollout

1. Base mechanism plus `TwoRingPie` (the motivating case). Fingerprints move only
   for `allocation_sunburst`, `fair_share_sunburst` and `jobs_facility_sunburst`.
2. `PieChart` (disk entity, user usage, jobs usage pies).
3. `JobsHistogram`, then the stacked families.
4. Stage 2, if wanted after living with stage 1.

## Progress

- [x] 1. Base mechanism + two-ring pies
- [x] 2. Single-ring pies
- [x] 3. Histograms, stacked charts (and area bands, Pace, and project titles)
- [ ] 4. Styled tooltip (optional)
