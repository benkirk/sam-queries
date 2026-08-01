# Mobile-friendly charts

**Status: IMPLEMENTED (2026-08-01).** Branch `chart-mobile-refactor`,
stacked on `chart-architecture-refactor` (PR #414). This is **PR 2** of the
four-PR roadmap in `CHART_ARCHITECTURE.md`.

PR 1 built the `layout` axis and shipped it inert. This wires it: real
per-family mobile profiles, the two `Layout` fields nothing read, a transport
from browser to server, and the CSS that lets the result use the width it was
sized for.

---

## The problem, as measured

Charts are matplotlib SVGs rendered server-side at a fixed figure size and
then scaled by CSS. **Nothing overflows** — `MOBILE_FRIENDLY`'s pass fixed
that, and `scrollWidth == clientWidth` at 390px on every chart surface. The
defect is legibility, and no stylesheet can reach it:

| chart | intrinsic | rendered at 375px | scale | 9pt label lands at |
|---|---|---|---|---|
| user/proj area | 1219pt | 287px | 0.29 | **2.6px** |
| usage trend | 1059pt | 350px | 0.33 | **3.0px** |
| nodetype history | 1071pt | 287px | 0.33 | **2.9px** |
| jobs histogram | 842pt | 350px | 0.42 | **3.7px** |

The only fix is to re-render at a different `figsize`, which means the server
has to know the layout.

---

## What PR 1 actually left

`Layout` has six fields. Consumption before this pass:

| field | consumers | charts reached |
|---|---|---|
| `figsize` | `base.py`, `dualpanel.py` | 15 of 15 |
| `base_fontsize` | `dualpanel.py` ×6 | 2 of 15 |
| `max_ticks` | `stacked.py` ×1 | 1 of 15 |
| `label_rotation` | `histogram.py` ×1 | 2 of 15 |
| `legend_placement` | **none** | 0 |
| `max_legend_entries` | **none** | 0 |

Every test passed, because "renders without raising" is all a smoke test can
see. That is the lesson this pass is built around, and why
`tests/unit/test_chart_layout_axis.py` asserts through rendered output and
each of its claims was checked by mutation.

Mobile profiles were `MOBILE_DEFAULTS` plus a figsize derived by holding the
desktop aspect ratio at 4.5in wide. For the stacked family that is a
**4.5 × 1.25in strip** — under an inch of plot once a legend moves beneath it.
`profile()`'s `mobile_figsize` is now required and positional so no new family
can inherit that by omission.

---

## Decisions

### 1. Transport is a cookie *and* a query parameter

Neither alone covers the surface area:

- **9 of 18 chart call sites render inside a full-page GET** — the three
  status history pages, and the four pies on `/allocations/projects`. No htmx
  request exists for those, so `htmx:configRequest` never fires. → cookie.
- **The cookie cannot cover the first page of a session.** CSP is nonce-free
  by design (four routes cache rendered HTML in Redis, so a per-request nonce
  goes stale on a hit — `utils/csp.py:11-19`), which rules out the classic
  inline head script. The sender is external, so it runs at end of body,
  after the server already chose. → query parameter, which reaches fragments
  on the first paint because `hx-trigger="load"` fires after the listener
  registers.

Together they leave one gap: a first-ever visit renders one page of
desktop-sized charts. They still fit; they are just small.

`static/js/layout-axis.js` writes `sam_layout` from
`matchMedia('(max-width: 767.98px)')` — Bootstrap's `md`, the breakpoint
`dashboard-init.js:234` already uses — and injects `layout` into every
outgoing htmx request. The injector is **unconditional**, unlike
`nav-view-persistence.js`'s opt-in one: a chart fragment missing a marker
attribute would otherwise silently render desktop. It reuses that file's
`URLSearchParams.delete` path-dedupe, because htmx appends `parameters` to
`path` for GETs and `request.args.get` returns the *first* value.

`webapp/utils/htmx.py:read_layout()` reads query string → cookie → default,
lenient like `jobs/routes.py:_parse_period`. It **normalizes** rather than
passing an unknown value through: the chart layer is lenient too, but the
value reaches a cache key shared across workers and pods, and that key should
have two spellings rather than arbitrarily many.

### 2. `user_aware_cache_key` includes the layout

`/allocations/projects` renders four pies inline and is `@cache.cached` on a
key holding the query string but **not** the cookie. Without this, the first
visitor to warm the page decides whether everyone else gets phone-sized or
desktop-sized pies. PR 3 adds `theme` in the same slot, where the failure is
more visible. Routes with no chart pay a doubled key space for nothing; at
five call sites that is the right trade against a silent, user-visible bug.

### 3. `max_legend_entries` binds at a different point per family

Because "honest" is not the same place:

- **Pies cap slices**, not legend rows. Capping the legend would draw wedges
  nothing identifies — and every one of these pies is a drill target, so an
  unidentified wedge is an unlabelled click.
- **Pace clamps `top_n`** (default 20 — twenty legend rows under a 3in figure
  is taller than the chart). The surplus folds into the existing "Other"
  band, so the areas still sum to the same total.
- **The stacked family caps legend rows and still draws every band**, keeping
  the trailing "Others" entry: dropping it would leave a visible grey band
  with nothing explaining it.
- **The histogram has no legend**, so the field does not apply. It was not
  repurposed to mean "stack segments".

Pies and pace need the layout during `prepare()`, before the drawing hooks
get their arguments, so `render()` sets `self.layout` / `self.theme` first.
The hooks still take them as arguments; `self.layout` is not an invitation to
stop passing them.

Capping breaks `link_legend`'s positional zip, which would put valid-looking
hrefs on the wrong swatches — invisible to the fingerprint gate, which proves
href *strings*, not the artists carrying them. Hence the explicit `ordered=`
flag.

### 4. Dual-panel legends go *above*

The only family with two stacked Axes. Its lower panel's underside carries the
date labels and `Time (MDT)`; a legend there lands on top of them, and moving
the anchor down moves the label down with it because the tight bbox grows to
fit both — checked at three anchors before abandoning the direction. Above
puts the upper legend in the top margin and the lower one in the inter-panel
gap, which `sharex` leaves empty because the upper panel's tick labels are
hidden. `hspace=0.45` widens that gap to hold it.

### 5. A span-aware date axis, on **both** layouts

Scope grew here deliberately, after the mobile work exposed that the axis
vocabulary was the underlying problem and the phone was only where it hurt
first. Measured before:

| span | tick labels | redundancy |
|---|---|---|
| 6h | `07-26 00` `07-26 01` `07-26 02` … | date on **every** tick |
| 7d | `2026-07-26` `2026-07-27` … | year **and** month on every tick |
| 1y | `2026-09` `2026-11` `2027-01` … | year mostly repeated |

...all rotated 30°, so vertical space was being spent to render characters
identical across every label.

The rule is one line: **the tick carries what changes, a second line carries
the context, and the context is drawn only where it changes** — always at the
first tick, so an axis is never left without its date.

```
6h   00:00   01:00  02:00  03:00      1y   Jul    Sep  Nov  Jan    Mar
     Jul 26                                2026                2027
```

`fmt.mpl_date_ticks(max_ticks)` returns `(locator, formatter)`, matching the
existing `mpl_number_formatter` / `mpl_pct_formatter` factories. **`date_str`
is untouched** — a table column wants ISO, sortable and unambiguous; this is a
charting concern only.

**`ConciseDateFormatter` was tried, shipped for mobile, and then replaced.**
It implements the same
idea, but two behaviours disqualify it here: it derives its offset label from
the **last** tick, so a window showing Jul 26–31 gets labelled `2026-Aug`; and
at day scale it emits bare day numbers (`26 27 28`) with the month only in
that offset. Ours computes context from the visible ticks, and applies it
to desktop too — where the redundancy was just as bad and nobody had
looked.

**The band comes from actual tick spacing, not the data's span.** They usually
agree, but the locator has the last word on where ticks land, and a formatter
that guessed from the span would mislabel whenever they diverged.

Rotation is gone, on both layouts — it existed to fit labels this removes.

*The jobs timeline is the exception.* It plots band indices against period
strings the plugin already grouped (`2026-07-26` / `2026-07` / `2026`), so no
matplotlib formatter can reach it. `fmt.compact_date_labels()` applies the
same vocabulary to the strings — no plugin change — and returns them unchanged
if any fails to parse, so a week or quarter grain degrades to today's
rendering rather than raising inside a chart.

Blast radius, from the fingerprint: **15 of 47 desktop cases and 15 of 32
mobile** — exactly the eight date-axis charts and their variants. Every pie
and histogram case is byte-identical.

### 6. Charts bleed over their card padding on phones

Measured at 375px on `/status/derecho`: the chart sits in a card inside a
card, each `.card-body` taking 16px a side, so a 375px viewport handed the
chart **287px** and the new figure was scaled back down to 0.88. Cards nest on
several surfaces and un-nesting them is not a chart change, so the chart
bleeds back over its immediate card padding — 16px a side, matching
`.card-body` exactly, so it lines up with the card edge rather than pushing
past it. `width: 100%` on mobile so a figure narrower than the card fills it;
desktop keeps `max-width` alone, since upscaling an 18in figure would blur it.

Mobile figures were then sized against that real 287px, not the 350px first
assumed — 4.0in for the wide families, 3.6in for pies.

### 7. No re-render on resize

There is no resize listener anywhere in the app today, and adding one would be
a new pathway: a drag-resize across the breakpoint fires a burst of chart
renders, each a matplotlib figure. A rotated phone keeps the charts it has
until the next fetch or navigation. Asserted in
`test_layout_transport.py::test_has_no_resize_listener` so it stays a
decision rather than an oversight.

---

## Results

Measured in the browser at 390×844, logged in as `benkirk`, after htmx
fragments settled:

| surface | before | after |
|---|---|---|
| `/status/derecho` user/proj | 1219pt @ 0.29 → **2.6px** | 294pt @ 1.08 → **9.8px** |
| `/allocations/projects` facility pie | desktop | 279pt @ 1.23 → 9.9px |
| jobs explorer timeline | 1200pt | 291pt @ 1.12 → 10.1px |

Page overflow stays 0 on status and allocations. Drill links survive the
mobile render — 824 anchors on the timeline, `#sam/row/data-jt-period/0`.

**Pre-existing and untouched:** the jobs *explorer* page overflows 20px at
375px. Verified against this branch's parent — 20px there too, with a 1200pt
desktop chart — so it is a wide table, not a chart, and a surface
`MOBILE_FRIENDLY` never measured.

### Cache budget

A mobile SVG is a ~4in figure rather than an 18in one, so it carries fewer
path points and less text: **0.76× its desktop twin** (411 KB vs 542 KB across
the same 32 sample cases). Both layouts together are 1.76× desktop alone, not
2×; extrapolating, all four combinations land near 3.1× rather than the 4×
`helm/values.yaml` had budgeted. And the multiplier applies only to charts
actually requested in both layouts inside the 600s TTL, which desktop-majority
traffic makes well under 1.76×.

`cache_maxsize` bounds only the **no-Redis fallback** — under Redis, eviction
is instance-global `allkeys-lru`. The three tightest budgets were raised
(`facility_pie_chart` 32→48, `nodetype_history` and `queue_history` 64→96);
the rest had slack.

---

## Commit series

| | |
|---|---|
| **M0** | Pin `mobile` in the fingerprint gate *before* touching rendering — 47 keys → 79. Fix `generate_jobs_user_pie_chart`, the one generator that raised `TypeError` on `layout=`. |
| **M1** | The layout axis actually consumed: real profiles, all six fields, `legend_entries` capping, the `ordered=` link guard. |
| **M2/M3** | Transport (cookie + htmx param + `read_layout` + 18 call sites), the cache-key fix, `ConciseDateFormatter`, and the CSS gutter. |
| **M4** | Cache budget, docs. |

### The gate

`tests/unit/test_chart_fingerprints.py` renders every non-empty case at both
layouts, under `<case>` and `<case>@mobile`. The **desktop half is the more
important half**: a mobile-tuning commit that moves a desktop fingerprint has
leaked into the users this pass is not for, and that is the most likely way it
regresses anything. It held — all 47 desktop fingerprints are byte-identical
across the whole series.

Empty cases are pinned once, because `is_empty()` short-circuits before
`make_figure()`; `test_empty_state_is_layout_invariant` asserts that rather
than assuming it.

---

## Follow-ups this pass deliberately did not take

- **The jobs explorer's 20px overflow** — pre-existing, a table, not a chart.
- **Per-surface figure tuning.** Every family has one mobile profile; a
  surface whose card is unusually narrow or wide still gets the family's.
- **Tablet.** One breakpoint, two profiles. A 820px iPad gets `desktop`,
  which at that width is legible (scale ~0.65 on the widest chart).
- **`theme`** — PR 3 and PR 4. The cookie rail built here is the one they
  should reuse; `user_aware_cache_key` already has the slot beside `l:`.
