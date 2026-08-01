# Application-wide dark mode

**Status: PROPOSED (2026-08-01), verified against source 2026-08-01.**
This is **PR 3** of the four-PR roadmap declared in
`docs/plans/CHART_ARCHITECTURE.md` § *Roadmap*, which deferred it to "a
separate planning session". This is that session. No code has been written.

| # | PR | Depends on | State |
|---|---|---|---|
| 1 | Chart architecture refactor | — | branch `chart-architecture-refactor` |
| 2 | Mobile-friendly charts | 1 | not started |
| **3** | **App-wide dark mode** ← *this document* | — (parallel to 2) | this plan |
| 4 | Dark-mode charts | 1 **and** 3 | not started |

All line references are against the working tree at 2026-08-01 and were
re-verified while writing; where this document contradicts a count in
`CHART_ARCHITECTURE.md`, the correction and its reason are recorded in
Appendix C.

---

## Summary

Three findings set the architecture, and two of them are constraints the app
already imposes on itself:

1. **The CSP is nonce-free by design** (`webapp/utils/csp.py`, `script-src
   'self'`, no `'unsafe-inline'`, enforced by `test_template_csp_lint.py`
   whose debt ratchet is at `{}`). The industry-standard dark-mode bootstrap
   — an inline `<script>` in `<head>` that reads `localStorage` and sets the
   theme attribute before first paint — **is not available to us** as
   written. (A *hash*-blessed inline script would technically be; see
   § *Is the nonce-free CSP a problem?* for why that is a real option we
   still don't want.)
2. **Charts are server-rendered SVG, cached in Redis.** The colours are baked
   into the bytes. The server must therefore know the theme at render time;
   no client-side-only mechanism can theme them.

Both constraints point at the same answer, which is why this is a cheap
feature rather than an awkward one: **a cookie, read server-side, rendered
into `<html data-bs-theme="…">`.** One mechanism satisfies both, and it is
FOUC-free *by construction* rather than by racing the paint.

3. **Bootstrap 5.3.3 is already vendored with a complete, unused
   `[data-bs-theme=dark]` block** — 15 selectors covering body, borders,
   links, forms, navbar, dropdowns, accordions, tables and code. Most of SAM's
   chrome is Bootstrap components driven by `--bs-*` variables, so a large
   fraction of the app flips for free the moment the attribute is set.

The corrected inventory (§ *What actually has to change*) is materially
smaller than `CHART_ARCHITECTURE.md` estimated: **≈15 CSS declarations and
≈130 template class attributes**, not "83 hardcoded-white sites" plus an
unknown. The single largest utility-class usage in the app — `text-muted`,
624 occurrences — is **already theme-correct** in Bootstrap 5.3.3 and needs
no change at all.

The one piece of genuine refactoring this needs, and the thing worth doing
even if dark mode were cancelled, is § *The token layer*: SAM's design tokens
are named by **value** (`--text-dark`, `--bg-light`) rather than by **role**.
A token literally named `--text-dark` whose dark-mode value must be near-white
is not survivable. That rename is cheap — 28 `var()` callsites, all in CSS,
**zero in templates** — and it is the foundation everything else sits on.

---

## What PR 1 already bought

The chart refactor is not merely "unblocking" this work; it pre-solved the two
things that would otherwise be discovered late and expensively.

**The chrome/data colour split.** `charts/theme.py` establishes that the
`UNITY_*` palettes encode *data* (a wedge, a band) and are theme-invariant,
while everything a `Theme` carries is *chrome* (text, spines, grid, edges,
blend target). That distinction is exactly the one the CSS layer needs and
does not currently make, and § *The token layer* below adopts the same split:
tier-1 primitives are the brand constants, tier-2 semantic tokens are chrome.

**The cache-aliasing trap, closed structurally.** `charts/base.py:chart_view`
composes `layout` and `theme` into the cache key in one place, with a docstring
explaining that both `caching/chart.py` and `redis_chart.py` default to a key
function that *ignores every argument except the first positional one* — so a
`theme=` kwarg would have been silently dropped and the first-rendered theme's
SVG served to every user, globally, across pods. PR 4 inherits a correct key
without doing anything. **This plan must not undo that**, and § *Caching*
extends the same reasoning to the five fully-rendered-HTML cache entries.

`Theme.DARK` already exists with starting values. It is explicitly *not*
shippable — Appendix B of the chart plan records the two decisions a mechanical
swap cannot make, and both are still open. They are restated in § *Design
decisions* below because they belong to PR 4, not here.

---

## The theme carrier

### Decision: a cookie, rendered server-side

```
Cookie:  sam_theme = "light" | "dark"
         SameSite=Lax; Max-Age=31536000; Path=/; Secure (prod)
         NOT HttpOnly — the toggle sets it from JS
```

- No PII, no session coupling, no server state. Safe to send on every request.
- Read in a `before_request`/context processor into `g.theme`; exposed to Jinja.
- Rendered directly onto the root element of **both** page shells:
  `templates/dashboards/base.html:2` and `templates/auth/login.html:2` (the
  only two `<html>` tags the app owns — `templates/errors/429.html` inherits,
  and `admin/master.html` is Flask-Admin's, see § *Out of scope*).

```jinja
<html lang="en" data-bs-theme="{{ theme }}">
```

The theme is therefore correct in the **first byte of HTML**. There is no
flash to prevent, no `<head>` script to nonce, no `localStorage` read racing
the paint, and nothing for the CSP to forbid. The constraint that looked like
an obstacle produced the better design.

### The toggle

A control in the navbar utility menu (`base.html:62-85`, beside the user
dropdown; mobile gets it in the offcanvas drawer). Behaviour lives in a new
`static/js/theme-toggle.js`, loaded alongside the existing 16 scripts at
`base.html:219-234` — **external, not inline**, because
`test_template_csp_lint.py` will fail the build otherwise, and correctly so.

On click:

1. Write the cookie.
2. `document.documentElement.dataset.bsTheme = next` — the CSS flips instantly.
3. **Reload the page.**

Step 3 is the part worth arguing about, so: charts are server-rendered SVG with
baked colours, and there are 16 of them across the dashboards. The alternatives
are to leave them stale until the next navigation (visibly wrong — a light chart
on a dark page is worse than a brief reload), or to hunt down and re-issue every
chart fragment's htmx request (fragile, and it re-derives knowledge that
`nav-view-persistence.js` already owns). A reload re-renders everything
server-side in one pass, in the correct theme, from warm Redis entries after
the first user. Steps 1–2 still run first so the reload paints the new theme
rather than flashing the old one.

### Is the nonce-free CSP a problem?

Short answer: **no, and it should not be revisited for this feature.** It is
worth writing down why, because "the CSP blocks the standard approach" reads
like a limitation being worked around, and it isn't.

**What the constraint actually forbids is one thing:** a *pre-paint,
client-side* theme decision. That only matters if the theme lives somewhere
only the client can read — i.e. `localStorage`. Ours lives in a cookie, which
the server reads on the way in, so the constraint never binds. Everything else
about the feature is unaffected.

**Why nonces genuinely can't come back.** A nonce is per-request by
definition; five routes cache fully-rendered HTML in Redis. A nonce baked into
a cached response is stale on every subsequent hit, and the browser blocks the
script — so this is a correctness incompatibility, not a stylistic preference.
Restoring nonces means giving up rendered-HTML caching on the app's most
expensive pages (the allocations dashboard, the orgs card), or post-processing
cached HTML to rewrite the nonce on the way out, which is exactly the kind of
drift-prone coupling `csp.py` was built to avoid.

**Hashes are the honest caveat.** `script-src` also accepts
`'sha256-<digest>'`, which is a hash of the script *content* and therefore
static — fully compatible with cached HTML. A tiny inline theme bootstrap
could legitimately be blessed that way. We still don't want it: it buys
nothing the cookie doesn't already give us, and it introduces a hash that must
be kept in sync with the script body by hand. Silent, total failure of that
script on drift, in the one code path that runs before anything else on the
page — against zero benefit. Noted so a future reader doesn't rediscover
hashes and assume they were overlooked.

**What it costs elsewhere is small and already paid.** Behaviour must live in
a static JS file — which is where the repo already puts it (16 static scripts,
every inline handler extracted, lint ratchet empty). The toggle follows the
house pattern rather than fighting it. And note `style-src` retains
`'unsafe-inline'`: the 301 inline `style=` attributes are unaffected, and if
dark mode ever needed an inline colour it would be allowed. The restriction is
on *script* only.

**The one real price** is `auto` / `prefers-color-scheme` below — and it is
one extra page load on a user's first-ever visit, not a recurring tax.

### `auto` (follow the OS) — deferred to a follow-up, deliberately

`prefers-color-scheme` is a client-side media query; the server cannot read it,
so an `auto` cookie value would render light server-side and be corrected by
JS on load — reintroducing exactly the flash the cookie design eliminates.

The clean version, if it's wanted later: on a request with **no** `sam_theme`
cookie at all, `theme-toggle.js` sets the cookie from `matchMedia` and reloads
**once**. Every subsequent visit is cookie-driven and flash-free. The cost is a
single extra load on a user's first-ever visit, not on every page. Ship
explicit `light|dark` first; add this after the surfaces are proven.

---

## The token layer

This is the refactoring the request asked about, and the only structural change
in the plan.

### The problem

`static/css/variables.css` is one flat `:root` block mixing three different
kinds of thing, with the semantic ones named after their **light-mode values**:

```css
--bg-light:     #f6f7f6;   /* actually: the page surface */
--text-dark:    #323133;   /* actually: primary body text */
--bg-gray-light:  #f8f9fa; /* actually: a recessed/secondary surface */
--bg-gray-medium: #e9ecef;
--border-color: #E2E8F0;
```

`dashboard.css:13-21` sets `body { background-color: var(--bg-light); color:
var(--text-dark); }`. In dark mode `--text-dark` must resolve to near-white and
`--bg-light` to near-black. A maintainer reading `color: var(--text-dark)` in
six months has no way to know whether that means "the dark colour" (invariant)
or "the body text colour" (flips). That ambiguity is how theme bugs get
written, and it will not be caught by any test.

### The fix — three tiers

```css
/* ---- Tier 1: primitives. Brand constants. Theme-INVARIANT.
        Component CSS must never reference these directly. ---- */
:root {
    --ncar-space-blue: #011837;
    --ncar-blue:       #0057c2;
    /* … the existing 14 brand colours, unchanged … */
}

/* ---- Tier 2: semantic/role tokens. The ONLY thing component CSS uses.
        Defined twice — once per theme. ---- */
:root {
    --surface-page:      #f6f7f6;
    --surface-card:      #ffffff;
    --surface-raised:    #ffffff;   /* tabs, panels, chips, inputs */
    --surface-sunken:    #f8f9fa;   /* recessed wells, thead */
    --text-primary:      #323133;
    --text-secondary:    #718096;
    --text-on-brand:     #ffffff;   /* invariant: white on saturated fills */
    --border-default:    #E2E8F0;
    --shadow-sm: …
}

:root[data-bs-theme="dark"] {
    --surface-page:      #14181d;
    --surface-card:      #1b2733;   /* == charts/theme.py Theme.DARK targets */
    --surface-raised:    #222c38;
    --surface-sunken:    #11151a;
    --text-primary:      #e9ecef;
    --text-secondary:    #adb5bd;
    --text-on-brand:     #ffffff;   /* unchanged — see below */
    --border-default:    #39414b;
}

/* ---- Tier 3: the Bootstrap bridge. Makes Bootstrap components and our own
        CSS agree on one set of surfaces instead of two. ---- */
:root {
    --bs-body-bg:     var(--surface-page);
    --bs-body-color:  var(--text-primary);
    --bs-border-color: var(--border-default);
}
```

`--text-on-brand` is a tier-2 token that is deliberately **invariant**. It is
the answer to the 26 `color: #fff` declarations in § *What actually has to
change* — white text on an NCAR-blue button is correct in both themes, and
giving that case a *name* is what stops a future contributor from "fixing" it
into a theme-dependent token and breaking every button in light mode.

The dark `--surface-card` value is `#1b2733` on purpose: it is already
`Theme.DARK.legend_face` / `shade_toward` / `segment_edge` in
`charts/theme.py:266-276`. Charts sit inside cards, and PR 4's blend targets
have to match the surface they blend into. Picking the value here, once, is
what makes PR 4 mechanical.

### Sizing

The rename is far cheaper than it looks, which is the argument for doing it
first rather than deferring it:

| Token | `var()` uses in CSS | in templates |
|---|---|---|
| `--bg-light` | 4 | **0** |
| `--text-dark` | 4 | **0** |
| `--bg-gray-light` | 10 | **0** |
| `--bg-gray-medium` | 5 | **0** |
| `--border-color` | 4 | **0** |
| `--color-gray-muted` | 1 | **0** |

**28 callsites, all in `static/css/`, none in Jinja.** A mechanical
find-and-replace with zero template blast radius.

### Keep the palette single-sourced

The brand palette is currently **triplicated** — `variables.css`, the
`UNITY_NCAR_*` scalars in `charts/theme.py:126-136`, and the stack tints in
`UNITY_STACK_20`. `CHART_ARCHITECTURE.md` called consolidation "a build-step
question, not a chart question" and deferred it; it is not a dark-mode question
either, and this plan does **not** propose a build step.

It does propose the cheap half: a test asserting the tier-1 hex values in
`variables.css` and the `UNITY_NCAR_*` scalars agree. There is direct precedent
— `test_chart_theme.py` already asserts `Theme.LIGHT` matches the module-level
rcParams for exactly this reason ("so a dark theme never has to fight a stale
global"). Same argument, one layer out.

---

## What actually has to change

### CSS: the whites, correctly partitioned

`CHART_ARCHITECTURE.md` reports "**83** hardcoded `#fff`/`#ffffff`/`white`
occurrences across the app CSS". That count is accurate, and it overstates the
work by roughly 5×, because the three kinds of white behave completely
differently:

| Kind | Count | Verdict |
|---|---|---|
| `color: #fff` on a saturated brand fill (buttons, badges, active tabs, modal headers) | 26 | **Correct as-is.** Rename to `var(--text-on-brand)` for legibility; no value change, in either theme. |
| `--bs-*: #fff` component-variable overrides | 22 | Mostly button *foregrounds* → same as above. **3 are real surfaces**: `--bs-card-bg` (`dashboard.css:577`), `--bs-card-cap-bg` (`:580`), `--bs-nav-pills-link-active-bg` (`:1114`). |
| `background-color: #fff` — true page surfaces | **12** | **The actual work.** |

The 12 surfaces, in full:

| Site | What it is |
|---|---|
| `dashboard.css:29` | `.navbar` |
| `dashboard.css:35` | `.navbar-light` |
| `dashboard.css:1135` | inactive nav-pill badge |
| `dashboard.css:1184` | `.nav-tabs .nav-link` |
| `dashboard.css:1204` | `.nav-tabs .nav-link.active` |
| `dashboard.css:1211` | `.tab-content` panel |
| `dashboard.css:1233` | facet bar |
| `dashboard.css:1280` | `.facet-chip:hover` |
| `dashboard.css:1423` | `.filter-sidebar` inputs |
| `auth.css:36`, `:168`, `:177` | login card + panels |

Add the 3 card variables and that is **15 declarations** to token-swap. The
card case is, as the chart plan noted, genuinely two lines.

Two pastels are *not* whites and need the § *Design decisions* treatment:
`auth.css:105` `#fff8e1` and `status.css:24` `#fff3cd`.

### Templates: the Bootstrap utility classes

This is the larger surface, and it is where the theme-invariant utilities bite.
Bootstrap 5.3's `[data-bs-theme=dark]` block redefines `--bs-body-*`,
`--bs-secondary-*`, `--bs-tertiary-*`, `--bs-emphasis-*` and `--bs-border-color`
— but **not** `--bs-light-rgb`, `--bs-dark-rgb` or `--bs-secondary-rgb`
(verified: 0 occurrences in the dark block). So `.bg-light` stays `#f8f9fa` and
`.text-dark` stays `#212529` on a dark page.

| Class | Occurrences | Verdict |
|---|---|---|
| `text-muted` | **624** | **Free.** 5.3.3 defines it as `color: var(--bs-secondary-color)`, which the dark block *does* redefine. No change — the app's single biggest utility usage is already correct. |
| `text-dark` | 84 attrs | 64 co-occur with a `bg-*` class (a chip on a saturated fill) → **correct, leave**. **20 bare → break**, migrate to `text-body-emphasis`. |
| `table-light` | 49 | **All break** — `.table-light` hardcodes `--bs-table-bg:#f8f9fa; --bs-table-color:#000`. 35 on `<thead>`, 6 on `<tr>`, 1 `<tfoot>`. |
| `bg-light` | 59 | ~23 are `badge bg-light text-dark` pairs → `bg-body-secondary text-body-emphasis`; the rest are surfaces → `bg-body-secondary` / `bg-body-tertiary`. |
| `bg-secondary` | 56 | `--bs-secondary-rgb` is not redefined, but these carry white text on `#6c757d` — legible in both themes. **Leave.** |
| `text-white`, `bg-dark`, `border-light` | 11 / 0 / 0 | On brand fills or unused. Leave. |

**≈130 attributes across three classes.** Mechanical, but it must be done by
reading each site — `text-dark` in particular is 76 % false positives, and a
blind sed would wreck every badge in the app.

One declared side effect: `bg-light` (`#f8f9fa`) → `bg-body-secondary`
(`#e9ecef` in light) is a **visible light-mode shift**. It is small and it is
the right direction (those surfaces become properly recessed), but it must be
declared in the commit that makes it rather than discovered in review.

### Inline styles

301 `style=` attributes exist, and `csp.py` keeps `'unsafe-inline'` in
`style-src` for exactly that reason. Only **15** mention a colour or
background. Those 15 get audited; the other 286 are widths, tree-depth padding
and `z-index` and are theme-irrelevant. No CSP change.

---

## Design decisions a mechanical swap cannot make

Each of these needs a human answer before D7. None blocks the commits before it.

1. **The page watermark — resolved: keep it, unchanged.** `dashboard.css:15`
   tiles `img/UCAR-Waves-Lines-Only-66.png` across every page at 53 % size.
   Inspected rather than assumed (2026-08-01): it is a **1029×3088 RGBA PNG
   that is 99.3 % fully transparent**, and every visible pixel is the *same
   single colour* — `rgb(250,161,25)` = `#faa119`, which is exactly
   `--ncar-orange` — laid down at α ≈ 84/255 (33 %).

   That makes it already theme-safe. It is not a light-mode graphic; it is
   transparent line art in a mid-saturation warm brand accent, which holds up
   against a near-white page and a near-black one alike. On dark it composites
   to a muted amber that is, if anything, *less* obtrusive than on light. No
   dark variant, no `invert()`, no `background-image: none`.

   Two notes for whoever implements D7. First, `opacity` does not apply to a
   background image independently, so if the dark rendering wants tuning the
   lever is `background-blend-mode` or a layered gradient — **not** a filter on
   `body`, which would drag every descendant with it. Second, the asset being
   a single flat brand colour is *why* this works; if it is ever replaced with
   a multi-tone or baked-on-white graphic, this decision has to be re-made.
   Verify visually at D7 and revisit only if it actually reads badly.

2. **The logos.** `logo-ncar.png` and `NSF_Official_logo.png` are dark navy
   marks. NSF and UCAR both publish reversed/white variants — this is a brand
   request, not a CSS problem, and it should be started early because it has
   external lead time. Interim fallback: keep the navbar a light chip in dark
   mode (defensible — many dark UIs keep a branded header band).

3. **The navbar.** It is `#fff` today (`:29,:35`). In dark mode a near-black
   navbar on a near-black page loses the header entirely. Recommendation:
   `--surface-raised`, one step lighter than the page, plus the existing
   `border-top` on the nav row.

4. **Status badges.** `status.css:17-32` — `.status-online` / `.status-degraded`
   / `.status-offline` are hardcoded pastel background + dark text + pastel
   border triplets. Bootstrap 5.3 ships exactly the right primitives:
   `--bs-success-bg-subtle` / `--bs-success-text-emphasis` /
   `--bs-success-border-subtle`, all redefined in the dark block. Rewrite the
   three rules onto those variables and they become theme-correct with no dark
   block of their own. Same treatment for `auth.css:105` (`#fff8e1` →
   `--bs-warning-bg-subtle`) and the alert/badge tints in `dashboard.css`.

5. **Charts** (PR 4, restated here so it isn't rediscovered): `UNITY_PALETTE_10[8]`
   is space blue `#011837` used as a **pie wedge fill** — on a dark page it
   vanishes while still carrying a white percentage label. And the `alpha=0.85`
   stackplots composite against the *page*, so every band desaturates on dark.
   Both are data-colour decisions, which is why they can't be solved by the
   `Theme` chrome axis. Flagging them now because the tier-2 `--surface-card`
   value chosen in D1 is the number PR 4 will blend against.

---

## Caching

Two cache layers touch rendered bytes, and they need opposite treatment.

**Chart caches — already correct.** `chart_view._key` composes the theme name
into the key (PR 1). PR 4's chart-fragment routes just pass `theme=g.theme`.
Nothing to do here. Note for whoever ships PR 4: chart entries are the bulk of
Redis, and dark mode doubles the working set. The `svg.fonttype='none'` change
in PR 1 cut SVG size 40–77 %, which mostly pays for it, but the `maxmemory`
rationale should be revisited with real numbers rather than assumed.

**Fully-rendered HTML caches — need a decision.** Five routes cache complete
HTML under `user_aware_cache_key` (`extensions.py:26`):
`allocations/blueprint.py:307,622`, `admin/orgs_routes.py:105,175`,
`admin/contracts_routes.py:268`.

Today all five are table/card fragments with no chart SVG and no
`data-bs-theme` in their output, so their bytes are genuinely
theme-independent — theming reaches them by CSS inheritance from the root
attribute, which lives in the page shell, not the fragment. Strictly, the key
needs no theme component.

**Recommendation: add `theme` to `user_aware_cache_key` anyway.** The
invariant "no cached fragment ever contains a theme-dependent byte" is real
today and completely invisible tomorrow — the failure mode is a chart added to
the allocations dashboard fragment, after which one user's dark SVG is served
to every light-mode user with the same facility scope, and it will present as
an intermittent rendering bug rather than a caching bug. The cost is at most
5 extra key partitions on 5 routes. This is the same argument `chart_view`'s
docstring makes about the aliasing trap, and it should be resolved the same
way: make the wrong thing inexpressible.

---

## Commit plan

Ordered so that **D3 makes dark mode visible and broken**, and every commit
after it is verifiable in both themes rather than reasoned about. D0–D2 are
provably no-op in light mode.

| # | Commit | Visual change | Gate |
|---|---|---|---|
| **D0** | `tests/unit/test_css_tokens.py` — lint: no raw hex in `background-color` / `color` / `border-color` outside `variables.css`; allowlist the current sites so it passes at HEAD and shrinks per commit | none | new test green |
| **D1** | Token layer: three tiers, role names, tier-1 ↔ `charts/theme.py` agreement test. Light values byte-identical | **none** | D0 allowlist shrinks; browser-smoke green |
| **D2** | Bootstrap bridge (`--bs-body-bg`, `--bs-body-color`, `--bs-border-color`, `--bs-card-bg`, `--bs-card-cap-bg` → tier 2) | **none** | as above |
| **D3** | Carrier: cookie, context processor, `data-bs-theme` on both roots, `theme-toggle.js`, navbar + offcanvas control, empty `:root[data-bs-theme="dark"]` block | Bootstrap's own dark appears; app CSS still light → **deliberately broken, and now inspectable** | CSP lint green (external JS); route-map parity |
| **D4** | The 15 surface declarations → tier-2 tokens | none in light | visual diff both themes |
| **D5** | Utility-class codemod: 49 `table-light`, 20 bare `text-dark`, 59 `bg-light` | **declared** light-mode shift on `bg-light` surfaces | reviewed site-by-site |
| **D6** | Semantic colour sets → Bootstrap subtle/emphasis pairs: `status.css` badges, `auth.css`, alert/badge tints, the 15 colour-bearing inline styles | none in light | — |
| **D7** | The dark palette values + design decisions 2–4 (navbar, logos-or-fallback, status badges). Watermark needs no code — verify visually | **dark mode ships** | e2e sweep in both themes |
| **D8** | `theme` into `user_aware_cache_key`; e2e fixture parameterized over the cookie | none | full suite |

PR 4 (dark charts) unblocks after D7.

---

## Testing

**Existing gates that will fire, and how to stay on the right side of them:**

- `tests/unit/test_template_csp_lint.py` — forbids inline `<script>`. The
  toggle *must* be external JS. This is the gate that enforces the whole
  server-rendered design; if someone "fixes" a perceived flash with an inline
  head script, this catches it.
- `tests/unit/test_route_map_parity.py` — pins every dashboard
  `(endpoint, rule, methods)` triple. If the toggle is implemented as a POST
  route rather than a client-side cookie write, regen with `ROUTE_MAP_REGEN=1`
  and commit the snapshot in the same commit. (Client-side write avoids the
  route entirely and is the recommendation.)
- `tests/unit/test_chart_fingerprints.py` — unaffected until PR 4, since no
  chart call passes `theme=` before then. A fingerprint delta in D0–D8 is a bug.

**New coverage:**

- `test_css_tokens.py` (D0) — the raw-colour lint, with a shrinking allowlist.
  Its value is mostly *after* this work: it is what stops the next 83 whites.
- A Jinja test asserting both `<html>` roots carry `data-bs-theme` and that it
  round-trips from the cookie. Cheap, and it covers the login page, which is
  otherwise easy to forget (separate shell, separate CSS).
- `e2e/` already has a Playwright console sweep (`test_console_sweep.py`) and a
  CI workflow (`browser-smoke.yaml`). Parameterize the fixture over the
  `sam_theme` cookie so every swept page renders twice.

**On contrast checking — the honest gap.** Pixel snapshots would be noisy and
high-maintenance for a 257-template app. Recommendation: an axe-core pass (or a
small computed-contrast assertion) over a handful of representative surfaces —
card body, table header, navbar, a status badge, a form control — in both
themes. That catches the failure mode that actually matters (unreadable text)
without pinning appearance. Anything more ambitious should wait until the
surfaces have settled.

---

## Explicitly not in scope

- **Flask-Admin** (`templates/admin/master.html`). It extends Flask-Admin's own
  base, which is Bootstrap **3** with glyphicons — a different framework
  generation with its own theming model. It is gated behind the
  `FLASK_ADMIN_ENABLED` kill-switch and off in prod/public. Theming it means
  either overriding a vendored BS3 theme or migrating the whole admin surface;
  neither belongs in this PR. It will render light-on-light and that is
  accepted.
- **Chart dark rendering** (PR 4). This plan ships the `--surface-card` value
  PR 4 blends against and nothing else chart-side.
- **Mobile layout** (PR 2), which is parallel and independent.
- **The legacy-compat API blueprints** — JSON only, no HTML, per the standing
  repo rule.
- **Notification email templates** (`src/cli/templates/`). Mail clients have
  their own dark-mode handling (and their own CSS support matrix); it is a
  separate problem with a separate test story.
- **A CSS build step.** The palette triplication is real (§ *Keep the palette
  single-sourced*) and is addressed with a test, not a toolchain. Introducing
  Sass/PostCSS to a vendored-assets, CSP-locked, no-npm-in-CI app is a much
  larger decision than dark mode should be allowed to make.

---

## Appendix A — verified counts (2026-08-01)

| Measure | Value | How |
|---|---|---|
| `<html>` tags the app owns | 2 | `base.html:2`, `login.html:2` |
| App CSS files | 7 (6 + `variables.css`) | `static/css/` |
| `background-color: #fff` surfaces | 12 | `grep -E 'background(-color)?:\s*(#fff|#ffffff|white)'` |
| `color: #fff` on brand fills | 26 | — |
| `--bs-*: #fff` overrides | 22 | 3 are surfaces |
| Bootstrap dark-block selectors | 15 | vendored `bootstrap.min.css` |
| `--bs-secondary-rgb` in dark block | **0** | why `.bg-secondary` is invariant |
| `text-muted` | 624 | already correct |
| `text-dark` attrs / bare | 84 / **20** | 64 co-occur with `bg-*` |
| `table-light` | 49 | 35 `thead`, 6 `tr`, 1 `tfoot` |
| `bg-light` | 59 | ~23 badge pairs |
| Inline `style=` / colour-bearing | 301 / **15** | — |
| Value-named token `var()` callsites | 28 (CSS), **0** (templates) | the rename surface |
| Fully-rendered-HTML cache routes | 5 | `user_aware_cache_key` |
| Watermark PNG: transparent / visible | 99.3 % / **0.7 %** | RGBA, 1029×3088 |
| Watermark: distinct visible colours | **1** — `#faa119` (`--ncar-orange`) @ α≈33 % | why it needs no dark variant |

## Appendix B — the two constraints, and why they are load-bearing

Recorded because both look like obstacles and both improved the design.

**Nonce-free CSP** (`utils/csp.py`, `docs/plans/implemented/CSP-discussion.md`).
The policy is nonce-free because the rendered-HTML cache routes would serve a
stale nonce on every hit, and the browser would block the script — a
correctness incompatibility, not a preference. That rules out the standard
`<head>` bootstrap script as normally written, and thereby forces the theme
decision onto the server, where it has to be anyway for charts. A client-side
design would have satisfied neither constraint and would have flashed. See
§ *Is the nonce-free CSP a problem?* for the full assessment, including why
content-**hash**-blessed inline script — which *is* cache-compatible, unlike a
nonce — is available and still not worth taking.

**Server-rendered cached charts.** 16 matplotlib charts render to inline SVG
with baked colours, cached in Redis by content hash. No CSS can retheme them.
This is what makes the cookie mandatory rather than merely convenient, and it
is why `data-bs-theme` alone — the pure-Bootstrap answer — is insufficient for
this app specifically.

## Appendix C — corrections to `CHART_ARCHITECTURE.md`

Recorded per that document's own convention.

| # | It said | Actually | Impact |
|---|---|---|---|
| 1 | "83 hardcoded-white sites" as the scope of the CSS work | 83 is correct as a count, but **26 are foreground-on-brand (correct as-is)** and 22 are `--bs-*` overrides of which only 3 are surfaces. The real surface work is **12 declarations + 3 card variables**. | Scope estimate ~5× high. The nine `background-color: #fff` sites it lists for `dashboard.css` are exactly right. |
| 2 | (not stated) | The template-side utility classes — `table-light` (49), bare `text-dark` (20), `bg-light` (59) — are a **larger** surface than the CSS whites, and were not inventoried. | ~130 attributes of unbudgeted work. Partly offset by `text-muted` × 624 being free. |
| 3 | "PR 3 has a real head start" from Bootstrap's unused dark block | True, and stronger than implied: `text-muted`, the app's most-used utility, is already theme-correct. But the same block **does not** redefine `--bs-light-rgb` / `--bs-dark-rgb` / `--bs-secondary-rgb`, which is precisely why item 2 exists. | Head start is real; it is not uniform. |
