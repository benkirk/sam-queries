# Tablet chart layout — the third profile

**Status: IMPLEMENTED 2026-08-01** on `chart-ipad-profile`, stacked on
PR #416. Commits T1-T5. The answers to the open questions and the places the
work departed from this plan are recorded at the bottom; everything above that
is the handoff doc as written, kept because its measurements are the reason
for the design.

**Originally: NOT STARTED. Handoff doc, written 2026-08-01** at the end of the
mobile pass (PR #416), which is where the gap was measured.

Adds a `tablet` layout profile between `mobile` and `desktop`. The machinery
all exists — this is a third entry in four places plus a tuning pass.

---

## Why

Charts render server-side at a fixed figure size and are scaled by CSS. The
mobile pass gave phones their own figure. Tablets fall in the `desktop` band
and get an 18-inch figure squeezed into ~700px:

| viewport | client px | scale | smallest label |
|---|---|---|---|
| 390 phone (**mobile** layout) | 375 | 1.08 | **9.8px** |
| 768 iPad portrait | 753 | 0.52 | **6.2px** |
| 820 iPad Air portrait | 805 | 0.51 | **6.2px** |
| 1024 iPad landscape | 1009 | 0.67 | 8.1px |
| 1440 laptop | 1425 | 0.99 | 11.8px |

**A phone renders these charts better than an iPad does.** 768px portrait is
the worst legibility left in the product.

This is **not a regression** from PR #416 — verified against that branch's
parent at 1024px, which renders byte-identically (1288pt intrinsic, 0.672
scale, 8.1px). The tablet band is as the mobile pass found it.

Also worth knowing: `max-width: 767.98px` misses an iPad Mini in portrait by
0.02px. The current breakpoint is not a comfortable margin, it is a coin flip
on a device that exists.

Background: `MOBILE_CHARTS.md` (what shipped), `CHART_ARCHITECTURE.md` (the
hierarchy and the 4-PR roadmap this is a coda to).

---

## What already exists — do not rebuild it

| Thing | Where |
|---|---|
| `Layout` dataclass, `MOBILE_DEFAULTS`, `profile()`, `resolve_layout()` | `src/webapp/dashboards/charts/layout.py` |
| `LAYOUTS = profile(...)` — 6 declarations | `pie.py`, `stacked.py`, `histogram.py`, `dualpanel.py` ×2, `pace.py` |
| Lifecycle helpers reading the layout — `legend_kwargs`, `legend_entry_cap`, `label_kw`, `apply_tick_fontsize`, `apply_date_axis` | `charts/base.py` |
| Cache key composition (`layout.name` into the chart key) | `charts/base.py:chart_view._key` |
| `read_layout()` — query string → cookie → default, normalizing | `webapp/utils/htmx.py` |
| Cookie + `htmx:configRequest` injector, one `matchMedia` | `src/webapp/static/js/layout-axis.js` |
| Cached-HTML partitioning (`\|l:<layout>`) | `webapp/extensions.py:user_aware_cache_key` |
| `layout=read_layout()` for all 27 jobs/disk-scans fragments | `webapp/utils/fragments.py:_register_one` |
| Six hand-threaded call sites | user ×4, status ×4, allocations ×5 |
| Mobile chart gutter (negative margin + `width:100%`) | `static/css/dashboard.css`, `@media (max-width: 767.98px)` |

`resolve_layout` is already lenient and generic — it does a dict lookup with a
desktop fallback, so it needs **no change**. Likewise `chart_view._key`, which
keys on `layout.name` whatever that name is.

---

## The work

### 1. Decide the band boundaries — measure first

Recommended starting point: **mobile ≤767.98px, tablet 768–1199.98px, desktop
≥1200px** (Bootstrap `md`→`xl`).

The lower edge is forced. The upper edge is a judgement call and **must be
measured**: desktop gives 8.1px at 1024 and 11.8px at 1440, so it crosses into
"fine" somewhere between. Measure 1200 and 1280 (recipe below) and put the
boundary where desktop first clears ~10px. If that lands at 1280, use
Bootstrap's `xxl` (1400) rather than inventing a number.

### 2. `profile()` needs a third slot

Today: `profile(figsize, mobile_figsize, *, <desktop kwargs>, mobile={...})`.
`mobile_figsize` is positional and required *on purpose* — see the docstring;
the aspect-ratio default it replaced produced a 4.5×1.25in strip.

Options, in preference order:

1. `profile(figsize, mobile_figsize, tablet_figsize, *, ..., mobile={}, tablet={})`
   — three required positionals. Consistent with the existing reasoning (no
   defensible default for a figure size), and the diff is mechanical.
2. A `profiles({...})` dict-taking constructor. Cleaner for N profiles, but a
   bigger rewrite of all six declarations and their comments.

Add `TABLET_DEFAULTS` beside `MOBILE_DEFAULTS`. Keep the `mobile={...}` /
`tablet={...}` override dicts validated against `Layout`'s fields — that check
exists because bare keywords silently configured *desktop* instead (see
`test_keywords_configure_desktop_not_mobile`).

### 3. Retire `Layout.is_mobile`

**This is the one real refactor.** `is_mobile` is a boolean, and there are now
three values. It has exactly **two** call sites, both in `base.py` and both
about font sizing:

- `label_kw()` — "axis label size: the layout's on mobile, the chart's on
  desktop"
- `apply_tick_fontsize()` — same question for ticks

The codebase already solved this exact problem once, for legends:
`Layout.legend_fontsize: int | None`, where **`None` means "defer to the
chart's own attribute"**. Desktop is `None`, so it reproduces per-family sizes
(9/11/13pt) byte for byte; mobile sets a number.

Do the same: add `Layout.axis_label_fontsize` and `Layout.tick_fontsize`, both
`int | None`, and both sites become `layout.X or self.X`. `is_mobile`
disappears, and tablet needs no new branch anywhere. Keep the property only if
something outside the charts package wants it — nothing does today.

### 4. Sizing — the method, not a number

Target: **intrinsic width ≈ container width**, i.e. render scale ≈ 1.0. That
is what turns a 9pt label into ~9px on screen.

The conversion is ~71.5pt of tight-bbox width per figure inch for the wide
families (18in desktop → 1288pt measured). So a ~665px container wants roughly
a **9.3in** figure. **Do not trust that arithmetic** — the tight bbox depends
on legend contents, so measure each family and iterate, exactly as the mobile
pass did.

Every family needs its own, and heights are not the desktop aspect ratio.
Start from desktop and shrink, rather than from mobile and grow: a tablet is
much closer to a desktop, and `legend_placement='right'` probably still works
at 700px where it did not at 300px. **Check that** — it is the single biggest
lever on width.

### 5. Transport — a second media query

`layout-axis.js` holds one query and returns `'mobile' | 'desktop'`. It needs
three-way selection. Keep the injector unconditional and keep the
`URLSearchParams.delete` path dedupe — both are load-bearing (see the file's
header comment).

Add `'tablet'` to `_LAYOUTS` in `webapp/utils/htmx.py`. Nothing else on the
server changes: `read_layout` normalizes against that set, and every call site
passes the string through.

### 6. CSS

The chart gutter (negative margin + `width:100%`) is inside
`@media (max-width: 767.98px)`. Tablets have the same nested-card padding
problem — measured at 375px the two `.card-body` levels cost 88px; at 768px
they still cost 88px, which is proportionally less but not nothing. Extend the
media query to the tablet band and re-measure; drop it if it does not help.

### 7. Cache budget

Three layouts, and PR 3/4 will add themes. `helm/values.yaml:40-71` (the comment above `cache:`) currently
reasons about 4 combinations landing near 3.1× the desktop-only baseline;
recompute for 6. A tablet SVG will fall between mobile (0.76× desktop) and
desktop, so the total is unlikely to alarm, but the comment should not go
stale — it is the only record of why `maxmemoryMB: 192` stands.

`cache_maxsize` bounds only the no-Redis fallback (the attribute's docstring
says so). Three tight ones were already raised for the second profile:
`facility_pie_chart` 48, `nodetype_history` / `queue_history` 96.

---

## Tests that will fail, and why each is right to

None of these are incidental — each pins a two-value vocabulary on purpose.

| File | What breaks |
|---|---|
| `test_chart_layout_axis.py:128` | `set(cls.LAYOUTS) == {'desktop', 'mobile'}` |
| `:112` `test_mobile_figure_is_narrower_and_phone_sized` | asserts mobile width ∈ [3.5, 5.0]in; needs a tablet band, and an ordering assertion `mobile < tablet < desktop` |
| `:122` `test_mobile_is_not_the_desktop_aspect_ratio` | wants a tablet twin |
| `test_chart_cache_key_signatures.py:214` | `'desktop' in ... and 'mobile' in ...` |
| `:262` `test_axes_are_independent` | asserts exactly 4 distinct keys over 2×2 → 6 over 3×2 |
| `:274` | renders under every combination — extend the layout tuple |
| `test_chart_fingerprints.py` | `MOBILE_SUFFIX` is a single suffix; generalize to one per non-desktop layout. **79 keys → 111.** |
| `test_layout_transport.py::test_uses_the_app_wide_breakpoint` | compares `layout-axis.js`'s media-query *set* against `dashboard-init.js`'s and asserts equality. With two queries that fails by design — rework it to assert the mobile query still matches, and that any additional query is a known breakpoint |
| `test_layout_transport.py::TestReadLayout` | add tablet cases |
| `test_fmt_date_axis.py` | should be unaffected — the date vocabulary keys off tick spacing, not layout name. Confirm rather than assume |

**Keep the discipline that made the mobile pass reviewable:** pin the new
layout in the fingerprint gate *before* changing any rendering, so the tuning
commits have a baseline. Desktop and mobile fingerprints must not move —
a delta in either is a leak into users this pass is not for.

---

## Verification

```bash
source etc/config_env.sh
export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'
pytest -q                                    # full suite

CHART_FINGERPRINT_REGEN=1 pytest tests/unit/test_chart_fingerprints.py
# then confirm the delta is ONLY the new tablet keys:
python3 - <<'PY'
import json, subprocess
old = json.loads(subprocess.run(['git','show','HEAD:tests/unit/snapshots/chart_fingerprints.json'],
                                capture_output=True, text=True).stdout)
new = json.load(open('tests/unit/snapshots/chart_fingerprints.json'))
print('pre-existing keys changed:',
      sorted(k for k in old if old[k] != new.get(k)) or 'none')
PY
```

Browser (`docker compose up webdev --watch`, Quick Login as `benkirk`).
**Flush Redis and restart between measurements** — chart keys hash input data,
not rendering code, so a warm entry serves the previous figure:

```bash
docker compose exec -T cache redis-cli FLUSHALL && docker compose restart webdev
```

The measurement used throughout the mobile pass — paste into Playwright's
`browser_evaluate` after the htmx fragments settle:

```js
async () => {
  await new Promise(r => setTimeout(r, 7000));
  const d = document.documentElement;
  const svg = [...document.querySelectorAll('svg')].find(s => s.getBoundingClientRect().width > 40);
  if (!svg) return {err: 'no svg — fragment may still be loading'};
  const r = svg.getBoundingClientRect();
  const w = parseFloat(svg.getAttribute('width'));
  const sizes = [...svg.querySelectorAll('text')].map(t => parseFloat(getComputedStyle(t).fontSize));
  return {
    vw: d.clientWidth, cookie: document.cookie,
    overflow: d.scrollWidth - d.clientWidth,
    intrinsicW: Math.round(w), renderedW: Math.round(r.width),
    scale: +(r.width / w).toFixed(3),
    minTextPx: +(Math.min(...sizes) * (r.width / w)).toFixed(1),
  };
}
```

Widths to walk: **768, 820, 1024, 1200, 1280, 1440**. Surfaces:
`/status/derecho?hours=6` and `?hours=168` (the worst chart, and a date-axis
span check), `/allocations/projects?cachebust=1` (full-page + cookie path),
`/dashboards/user/jobs/machine/derecho/explore` (fragments + drill links).

Targets: `minTextPx` ≥ ~9 at every width; `overflow` 0; drill anchors still
present (`svg a[*|href]`, hrefs of the form `#sam/row/...`).

Known and **not** caused by chart work: the jobs *explorer* page overflows
20px at 375px — a wide table, verified against pre-PR-#416 code.

---

## Branching

PR #414 (`chart-architecture-refactor`) and #416 (`chart-mobile-refactor`,
stacked on it) were both open when this was written. **Prefer branching from
`staging` once #416 has merged** rather than stacking a third — see
`reference_stacked_pr_reopen`: merging a parent closes the child, and
reopening needs the base branch *and* the recorded `headRefOid` restored.

If it must start before then, branch from `chart-mobile-refactor` and expect
a retarget.

---

## Open questions for whoever picks this up

1. **Upper boundary** — 1200 or 1400? Measure before choosing (§1).
2. **Does `legend_placement='right'` survive at tablet width?** It is the
   biggest single lever on figure width and the mobile pass had to abandon it.
   Likely yes at ~700px; verify before sizing anything else.
3. **Landscape iPad at 1024 — tablet or desktop?** It measures 8.1px today,
   which is borderline. Falls out of (1).
4. **Is a third profile the right shape at all**, versus making `desktop`
   itself responsive by sizing the figure from a client-reported container
   width? That would be one profile with a continuous parameter rather than
   three discrete ones — more accurate, but it puts a measured pixel width in
   the cache key, which fragments the cache badly. The discrete-profile answer
   is almost certainly right; recorded so nobody re-derives it from scratch.

---

## As built — answers, deviations, and what this plan missed

Implemented on `chart-ipad-profile` (stacked on PR #416) in five commits,
T1-T5. Full suite green; fingerprint deltas confined to `@tablet` keys in
every commit that declared one.

### The four open questions

1. **Upper boundary — 1200.** Measured on `/status/derecho`, where the chart's
   card is the viewport less 144px and the desktop figure's smallest label is
   12 user-units:

   | clientWidth | card | smallest label |
   |---|---|---|
   | 785 | 641 | 6.0px |
   | 1009 | 865 | 8.1px |
   | 1185 | 1041 | 9.7px |
   | 1265 | 1121 | 10.4px |

   Desktop clears 9px at ~1110 and 10px at ~1217. This plan said "if it lands
   at 1280, use `xxl` (1400)". It lands between the two, and `xl` won anyway:
   `xxl` would hand every 1280 laptop a figure sized for a 640px card. 1200
   measures 9.7px, which is inside the noise of the 10px target.

2. **`legend_placement='right'` survives** — comfortably, at every tablet
   width. It is not overridden; `TABLET_DEFAULTS` says nothing about
   placement, so it inherits desktop's.

3. **Landscape iPad at 1024 is tablet**, falling out of (1). It reads
   10-13px there.

4. **Discrete profiles were right.** Nothing encountered argued for a
   container-width parameter.

### Deviations

- **`PieChart`'s tablet figure IS its desktop figure.** A pie trims to its own
  square, so its tight bbox is ~360pt where the wide families' is near their
  declared inches — every surface already gives a pie more width than it can
  use (measured 10.7px, 13.8px and 16.6px at a 768 viewport). Shrinking it
  would make the jobs pie smaller on screen and the allocations pie's labels
  larger. The ordering assertions are therefore `mobile < tablet <= desktop`,
  non-strict at the desktop end, and the aspect-ratio test skips a figure that
  is unchanged rather than scaled.

- **`TABLET_DEFAULTS` is two fields, not a mobile-shaped block.** Everything
  absent inherits *desktop*, which is the whole design: a tablet is a small
  desktop. The per-family type hierarchy the phone had to flatten survives.

- **Fonts are not overridden at all.** With the chart always filling its card,
  on-screen label size is `fontsize x card / bbox` — so font size and figure
  size are one knob, not two, and the plan's "~9.3in figure" arithmetic was
  replaced by measuring each family against the card it renders into.

- **The gutter CSS split in two.** The negative margin extends to the whole
  tablet band (worth 625 -> 657px and 9.0 -> 9.4px on the status page);
  `width: 100%` stays on phones only, because no tablet figure needs
  upscaling and extending it would only inflate the pies.

- **`Layout.is_mobile` retirement grew two fields, not one.** `label_kw()` and
  `apply_tick_fontsize()` needed `axis_label_fontsize` *and* `tick_fontsize`
  as separate `int | None` fields, because `UserProjectAreaChart` labels at
  13pt and ticks at 12pt and desktop has to reproduce both.

### What this plan did not predict

**Three fragments never received the axis at all.** `_panel_histogram` in
`webapp/jobs/routes.py` accepted `layout` and dropped it, so Wait Times, Job
Sizes and Durations rendered the 18in desktop figure at every viewport — a
mobile-pass bug that had been shipping since PR #416, found only because
shrinking the tablet figure changed nothing on screen.
`test_renderers_forward_the_layout_they_are_given` now gates that last hop:
a renderer that accepts `layout` and calls another layout-aware callable must
forward it. Renderers that draw no chart are exempt by construction — they
call nothing that takes one — so there is no allowlist to rot.

### Measured result, at a 768 viewport (min on-screen label px)

| surface | before | after |
|---|---|---|
| /status/derecho stacked, 625px card | 6.0 | 9.4 |
| /status/partition-history dual panel | 6.4 | 10.7 |
| /status/queue-history dual panel | 8.1 | 10.5 |
| jobs explorer timeline | 6.5 | 10.4 |
| jobs explorer histograms | 8.8 (stuck at desktop) | 11.0 |
| /status/filesystem-scans distribution | — | 10.7 |
| /allocations pace | 5.9 | 9.2-10.6 |
| pies (unchanged by design) | 10.7-16.6 | 10.7-16.6 |

Across the band the same chart reads 9.4px at 768 and 15.4px at 1199, filling
its card at both ends. At a 1215px window the cookie flips to `desktop` and
the 18in figure returns at 9.8px; at 390px `mobile` still measures 9.8px.

### Cache

Measured across the 32 sample cases: desktop 562 KB, tablet 483 KB (0.86x),
mobile 417 KB (0.74x) — three layouts are **2.60x** desktop alone, and six
combinations with the theme axis land near **5.2x**, up from the 3.1x
`helm/values.yaml` budgeted for four. Still comfortably inside 192 MB against
a base that dropped 58% with `svg.fonttype: none`. The three tightest
`cache_maxsize` budgets were raised again: `facility_pie_chart` 48 -> 72,
`nodetype_history` and `queue_history` 96 -> 144.
