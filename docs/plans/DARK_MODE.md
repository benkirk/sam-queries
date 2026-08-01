# Application-wide dark mode

**Status: IMPLEMENTED (2026-08-01) on `dark_mode_sans_charts`, D0–D8 landed.**
Deviations from this plan are recorded in **Appendix E** — read that before
treating any section below as a description of the shipped code.
This is **PR 3** of the four-PR roadmap declared in
`docs/plans/CHART_ARCHITECTURE.md` § *Roadmap*, which deferred it to "a
separate planning session". Implementation branch: `dark_mode_sans_charts`,
cut from `staging` at `bee0f4d`, PR targets `staging`.

| # | PR | Depends on | State |
|---|---|---|---|
| 1 | Chart architecture refactor (layout **and theme** render axes) | — | **merged** — `bee0f4d` (#414) |
| 2 | Mobile-friendly charts | 1 | landed with #414 |
| **3** | **App-wide dark mode** ← *this document* | 1 | this branch |
| 4 | Dark-mode charts | 1 **and** 3 | not started |

All line references are against `staging` at `bee0f4d` and were re-verified
while revising; where this document contradicts a count in
`CHART_ARCHITECTURE.md`, the correction and its reason are recorded in
Appendix C. Corrections made to *this* document's own first draft are in
Appendix D.

> **Revision note (2026-08-01).** The first draft was written before PR 1
> merged and assumed the theme carrier had to be designed from scratch. It
> does not: PR 1 shipped the **layout** axis using exactly the cookie-read-
> server-side mechanism proposed here, and left explicit hooks for this PR in
> `utils/htmx.py`, `static/js/layout-axis.js` and `extensions.py`. The carrier
> section below is rewritten to *mirror* that precedent rather than reinvent
> it, which removes a design decision and one of the two transport channels.
> See § *The theme carrier*.

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

That is no longer a proposal. PR 1 shipped this exact mechanism for the
**layout** axis and left hooks for this PR by name — so the carrier here is a
second instance of an established pattern, and the review question is
"does it match `read_layout`?" See § *What PR 1 already bought*.

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

**The carrier itself, already built and load-bearing.** This is the largest
correction to the first draft. PR 1's **layout** axis is the same problem
shape — a per-user rendering mode the server must know before it renders — and
it was solved the same way this document independently proposed:

| Piece | Layout (shipped) | Theme (this PR) |
|---|---|---|
| Cookie constant | `LAYOUT_COOKIE = 'sam_layout'` (`utils/htmx.py:14`) | `THEME_COOKIE = 'sam_theme'`, same module |
| Lenient reader | `read_layout(default='desktop')` (`:17`) | `read_theme(default='light')`, same shape |
| Validity set | `_LAYOUTS` frozenset, unknown → default, **never a 400** | `_THEMES` frozenset, same rule |
| Client writer | `static/js/layout-axis.js` | `static/js/theme-toggle.js` |
| Chart plumbing | `BaseChart.render(layout, theme)`, `chart_view(layout=, theme=)` | **already accepts `theme=`** |
| HTML cache key | `user_aware_cache_key` ends `\|l:{read_layout()}` | append `\|t:{read_theme()}` |

`static/js/layout-axis.js` says so in its own header comment:

> *PR 3 (app-wide dark mode) wants the same carrier for `theme`, with the added
> constraint that a theme flash is visible where a chart size is not. Keep the
> cookie shape reusable.*

and `user_aware_cache_key`'s docstring already names the change:

> *The dark-mode pass adds `theme` here for exactly the same reason, and with a
> more visible failure.*

So this PR does not choose a mechanism. It follows one, and the reviewer's
question becomes "does this match `read_layout`?" rather than "is a cookie
right?".

`Theme.DARK`, `THEMES` and `resolve_theme()` already exist and are live
(`charts/theme.py:266-278`) — nothing requests `DARK` yet. `Theme.DARK` is
explicitly *not* shippable: Appendix B of the chart plan records the two
decisions a mechanical swap cannot make, and both are still open. They are
restated in § *Design decisions* below because they belong to PR 4, not here.
The consequence for scoping is sharp: **PR 4 is now "pass `theme=read_theme()`
at the 18 chart call sites, then tune `Theme.DARK`" and nothing else.** Every
structural piece it needs is merged.

---

## The theme carrier

### Decision: a cookie, rendered server-side — the `read_layout` pattern

```
Cookie:  sam_theme = "light" | "dark"
         Path=/; SameSite=Lax; Max-Age=31536000
         NOT HttpOnly — the toggle sets it from JS
```

```python
# webapp/utils/htmx.py — directly beneath LAYOUT_COOKIE / read_layout
_THEMES = frozenset({'light', 'dark'})
THEME_COOKIE = 'sam_theme'

def read_theme(default: str = 'light') -> str:
    raw = (request.cookies.get(THEME_COOKIE) or '').strip().lower()
    return raw if raw in _THEMES else default
```

- No PII, no session coupling, no server state. Safe to send on every request.
- Read by a context processor into the Jinja globals (mirroring how
  `read_layout` is consumed at call sites); no `before_request`, no `g`.
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

`color-scheme` needs no work: Bootstrap's dark block opens with
`[data-bs-theme=dark]{color-scheme:dark; …}` (verified in the vendored 5.3.3),
so native scrollbars, form controls and the browser's own chrome flip with the
attribute.

### One transport channel, not two — where theme *diverges* from layout

`read_layout` reads **query string, then cookie**, and `layout-axis.js`
injects `?layout=` into every htmx request. `read_theme` reads **the cookie
only**, and `theme-toggle.js` injects nothing. This is a deliberate
simplification, and the reason is the asymmetry named in `layout-axis.js`:

- **Layout is *discovered*, client-side, after the server has already
  answered.** The server cannot know a viewport, so the first page a visitor
  ever loads is rendered before the cookie exists. The query-string channel is
  what makes htmx fragments correct on that first paint anyway.
- **Theme is *declared*, by an explicit click, which reloads.** There is no
  moment at which the browser knows a theme the server does not — the browser
  only ever learns it *from* the cookie the click wrote. A first-ever visitor
  has no preference to discover; `light` is the answer, not a stale guess.

Adding a `?theme=` param would therefore buy nothing and cost something real:
it would split the `chart_view` and `user_aware_cache_key` key spaces on a
value that can never disagree with the cookie, and it would need the same
duplicate-query-key defence `layout-axis.js` carries. Skip it.

The one thing lost is `?theme=dark` as a hand-debugging affordance. That is
worth having, so `read_theme` keeps the query string as an override **for
debugging only**, in the same precedence order as `read_layout` — but no JS
ever sets it, so it never appears in ordinary traffic. Cost: one line, and
`read_theme` stays literally the same function as `read_layout`, which is the
property a reviewer will check.

> **Consistency note for the implementer.** If `read_theme` and `read_layout`
> end up structurally different, that is the signal something has been
> reasoned about wrongly. They should be readable side by side and differ only
> in constant names and the cookie's lifetime.

### The toggle

A control in the navbar utility menu (`base.html:61-85` — the
`.utility-menu` div, beside the user dropdown; mobile gets it in the offcanvas
drawer). Behaviour lives in a new `static/js/theme-toggle.js`, loaded alongside
the existing 17 scripts — **external, not inline**, because
`test_template_csp_lint.py` will fail the build otherwise, and correctly so.

The utility menu is `{% if current_user.is_authenticated %}`-gated, so place
the toggle **outside** that conditional: an anonymous visitor on the login page
should be able to flip the theme, and `login.html` is a separate shell that
needs its own copy of the control regardless.

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

2. **The logos — RESOLVED and SHIPPED (Ben, 2026-08-01): dark navbar with a
   reversed mark.** D9 supersedes the light-chip fallback D7 shipped.

   Inspecting the asset rather than assuming changed the answer.
   `logo-ncar.png` is not "a dark navy mark" — it is a 2457×621 **lockup** of
   three separately-behaved parts, and only one was the problem:

   | Columns | Part | On a dark ground |
   |---|---|---|
   | `0–619` | NSF seal (gold gear, blue globe, white "NSF") | **correct as-is** |
   | `730–747` | vertical rule, `#404040` | invisible |
   | `869–1271` | NCAR wave disc (blue, white waves) | **correct as-is** |
   | `1386–2457` | "NCAR" + "OPERATED BY UCAR", brand blue | ~2.6:1 — the defect |

   So `img/logo-ncar-reversed.png` recolours **only the wordmark and the
   rule**, preserving alpha so antialiasing survives. The NSF seal is untouched
   per Ben's instruction — and needs nothing anyway. The wave disc is untouched
   because it already works.

   The wordmark is `--ncar-sky` **#42C0FF** (Ben's choice over white): 7.87:1
   on the band, and the same blue as `--text-link` in the dark theme, so the
   mark, the "Systems Accounting Manager" lockup text and every dark-mode link
   agree. The rule goes to a mid neutral #8C98A6.

   The asset is reproducible — `scripts/make_reversed_logo.py` derives the
   column spans from the alpha channel and fails loudly if the lockup's part
   count changes, rather than trusting hardcoded offsets.

   Still explicitly **not** `filter: invert()`: it would flatten the NSF seal
   and the wave disc into white silhouettes, which is not an approved variant
   of either organization's mark.

   **The swap is server-side**, from the same `theme` that sets
   `data-bs-theme` — no second `<img>` to hide, no CSS `content:` trick:

   ```jinja
   {{ url_for('static', filename='img/logo-ncar-reversed.png'
              if theme == 'dark' else 'img/logo-ncar.png') }}
   ```

   Note `NSF_Official_logo.png` is **referenced by no template** — the seal
   ships inside the lockup. Both shells use the lockup and both swap.

   The official reversed asset at `sundog.ucar.edu/page/10560` is behind
   Microsoft SAML SSO and could not be fetched; if it is preferred, dropping it
   in as `logo-ncar-reversed.png` is the entire change.

   `TestReversedBrandMark` pins the pairing in **both** directions — a dark
   navbar without the reversed asset is navy-on-navy, and a white
   `--surface-navbar` with the reversed asset is white-on-white.

3. **The navbar — settled by decision 2: it goes dark** (`--surface-navbar`
   `#1a222c`), once the reversed lockup lands with it. The paragraphs below
   describe the interim light-chip reasoning D7 shipped and D9 removed; kept
   because the `--bs-navbar-*` hazard it names is real and will bite anyone who
   pins the band light again.

   It is `#fff` today (`:29,:35`).
   The general dark-mode hazard is that a near-black navbar on a near-black
   page loses the header entirely; the usual answer is `--surface-raised`, one
   step lighter than the page, plus the existing `border-top` on the nav row.

   For **this** PR the navbar stays a **light chip** in dark mode, because the
   logos are not yet reversed (decision 2). `--surface-raised` becomes the
   right value once they are, and the `TODO(dark-logos)` comment marks the
   spot. Note the nav *links* must then be checked against the light chip —
   Bootstrap's dark block redefines `--bs-navbar-color` to
   `rgba(255,255,255,.55)`, which is invisible on a light navbar. Pin the
   navbar's `--bs-navbar-*` variables to their light values inside the dark
   block rather than letting them inherit.

4. **Status badges.** `status.css:17-32` — `.status-online` / `.status-degraded`
   / `.status-offline` are hardcoded pastel background + dark text + pastel
   border triplets. Bootstrap 5.3 ships exactly the right primitives:
   `--bs-success-bg-subtle` / `--bs-success-text-emphasis` /
   `--bs-success-border-subtle`, all redefined in the dark block. Rewrite the
   three rules onto those variables and they become theme-correct with no dark
   block of their own. Same treatment for `auth.css:105` (`#fff8e1` →
   `--bs-warning-bg-subtle`) and the alert/badge tints in `dashboard.css`.

5. **Heading/emphasis navy — new, found by reading the `text-dark` sites.**
   The first draft treated "20 bare `text-dark` → `text-body-emphasis`" as a
   mechanical swap. It isn't, because **15 of the 20 are the identical string**
   `class="stat-value text-dark text-detail"`, in three card partials
   (`user/partials/user_card.html` ×9, `user/partials/project_card.html` ×3,
   `admin/fragments/contract_card.html` ×2). And `.stat-value`
   (`dashboard.css:1607`) already declares `color: var(--ncar-navy)` — which
   `text-dark`'s `!important` is currently *overriding*.

   So that `text-dark` is not a colour choice; it is an accidental suppression
   of the brand navy. Three options, and this needs a human answer:

   | | Light-mode effect | Dark-mode effect |
   |---|---|---|
   | **(a) delete `text-dark`** | stat values become NCAR navy — a **visible change**, arguably the intended design | navy `#00357a` on `#14181d` is **unreadable**; forces a tier-2 `--text-heading` that lightens on dark |
   | **(b) → `text-body-emphasis`** | near-identical to today | correct, free |
   | **(c) keep, and let the token layer fix it** | identical | wrong — `.text-dark` is `--bs-dark-rgb`, not redefined in the dark block |

   **RESOLVED (Ben, 2026-08-01): (b).** `text-body-emphasis` for all 15. It is
   a true no-op in light mode, which is the property D5 is supposed to have;
   (a) changes the look of the three most-viewed cards in the app and deserves
   its own conversation rather than riding in on a dark-mode codemod. If (a) is
   later wanted, `--ncar-navy` gets a role-named tier-2 sibling and the swap is
   one line.

   The remaining 5 bare sites are genuine one-offs (3 `text-decoration-none`
   links on `status/casper.html`, 2 in allocation modals) — swap individually.

6. **Charts** (PR 4, restated here so it isn't rediscovered): `UNITY_PALETTE_10[8]`
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

**Fully-rendered HTML caches — decided, and already half-written.** Five routes
cache complete HTML under `user_aware_cache_key` (`extensions.py:26`):
`allocations/blueprint.py:309,635`, `admin/orgs_routes.py:105,175`,
`admin/contracts_routes.py:268`. (`utils/csp.py`'s module docstring still says
"Four routes" — pre-existing drift from before the fifth was added; fix it in
D0 while touching nothing else.)

Today all five are table/card fragments with no chart SVG and no
`data-bs-theme` in their output, so their bytes are genuinely
theme-independent — theming reaches them by CSS inheritance from the root
attribute, which lives in the page shell, not the fragment. Strictly, the key
needs no theme component.

**Decision: add `theme` to `user_aware_cache_key`, and do it in D3, not last.**
The invariant "no cached fragment ever contains a theme-dependent byte" is real
today and completely invisible tomorrow — the failure mode is a chart added to
the allocations dashboard fragment, after which one user's dark SVG is served
to every light-mode user with the same facility scope, and it will present as
an intermittent rendering bug rather than a caching bug. The cost is at most
5 extra key partitions on 5 routes. This is the same argument `chart_view`'s
docstring makes about the aliasing trap, and it should be resolved the same
way: make the wrong thing inexpressible.

The change is one line and its docstring already announces itself:

```python
return (f"u:{user_part}|{request.path}|{qs}|s:{scope_part}"
        f"|l:{read_layout()}|t:{read_theme()}")
```

**The first draft scheduled this as D8, the final commit. That is wrong, and
it is the ordering bug worth catching in review.** D3 is the commit that makes
the theme observable; D4–D7 are then spent clicking between themes with a live
Redis, against five routes whose cached HTML is keyed *without* the theme. Any
theme bug seen on those pages during that window would be indistinguishable
from a stale-cache artifact, and the debugging cost of one such false lead
exceeds the entire cost of the line. Ship it in the same commit that ships the
carrier — the mechanism and its cache key are one idea.

---

## Commit plan

Ordered so that **D3 makes dark mode visible and broken**, and every commit
after it is verifiable in both themes rather than reasoned about. D0–D2 are
provably no-op in light mode.

| # | Commit | Visual change | Gate |
|---|---|---|---|
| **D0** | `tests/unit/test_css_tokens.py` — lint: no raw hex in `background-color` / `color` / `border-color` outside `variables.css`; allowlist the current sites so it passes at HEAD and shrinks per commit. Fix `csp.py`'s stale "Four routes" docstring | none | new test green |
| **D1** | Token layer: three tiers, role names, tier-1 ↔ `charts/theme.py` agreement test. Light values byte-identical | **none** | D0 allowlist shrinks; browser-smoke green |
| **D2** | Bootstrap bridge (`--bs-body-bg`, `--bs-body-color`, `--bs-border-color`, `--bs-card-bg`, `--bs-card-cap-bg` → tier 2) | **none** | as above |
| **D3** | Carrier, complete: `THEME_COOKIE` + `read_theme()` beside `read_layout`, context processor, `data-bs-theme` on both roots, `theme-toggle.js`, navbar + offcanvas + login control, `\|t:` in `user_aware_cache_key`, empty `:root[data-bs-theme="dark"]` block | Bootstrap's own dark appears; app CSS still light → **deliberately broken, and now inspectable** | CSP lint green (external JS); route-map parity; new carrier unit test |
| **D4** | The 15 surface declarations → tier-2 tokens | none in light | visual diff both themes |
| **D5** | Utility-class codemod: 49 `table-light`, 20 bare `text-dark` (per decision 5), 59 `bg-light` | **declared** light-mode shift on `bg-light` surfaces | reviewed site-by-site |
| **D6** | Semantic colour sets → Bootstrap subtle/emphasis pairs: `status.css` badges, `auth.css`, alert/badge tints, the 15 colour-bearing inline styles | none in light | — |
| **D7** | The dark palette values + design decisions 2–4 (navbar, logos-or-fallback, status badges). Watermark needs no code — verify visually | **dark mode ships** | e2e sweep in both themes |
| **D8** | `e2e/` dark sweep; contrast assertions on the representative surfaces | none | full suite |
| **D9** | Reversed brand lockup + the navbar goes dark (supersedes D7's light chip) | **dark navbar** | both suites; `TestReversedBrandMark` |

Two changes from the first draft's ordering, both for the same reason — a
commit should be verifiable when it lands, not retroactively:

- **The cache key moved D8 → D3.** Rationale in § *Caching*: leaving five
  rendered-HTML routes theme-blind across D4–D7 turns every theme bug seen on
  them into a suspected cache bug for the four commits where we are most
  actively looking at them.
- **D8 is now testing only.** With the key in D3, the last commit has no
  production code in it, which makes it trivially revertable if the browser
  tier turns out flaky in CI.

PR 4 (dark charts) unblocks after D7, and is now scoped to *passing
`theme=read_theme()` at the 18 chart call sites plus tuning `Theme.DARK`* —
`chart_view` already accepts and keys on the argument.

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
- A carrier unit test (D3) mirroring whatever covers `read_layout`: unknown
  cookie value → `light` (never a 400), and both `<html>` roots carry
  `data-bs-theme` round-tripped from the cookie. Cheap, and it covers the login
  page, which is otherwise easy to forget (separate shell, separate CSS).
- **`e2e/` is at the repo root, not `tests/e2e/`** — `e2e/test_console_sweep.py`
  plus `e2e/conftest.py`, driven by `.github/workflows/browser-smoke.yaml`, and
  it runs against a *running stack over HTTP* (it imports no `sam`/`webapp`
  code — that boundary is load-bearing for CI, see its docstring).

  **Do not parameterize the whole suite over the cookie.** The sweep derives
  its route list from `tests/unit/snapshots/dashboard_route_map.json`
  (≥20 pages) and also runs several declared interaction flows; doubling all of
  it doubles browser-smoke wall time to catch, in the flows, nothing a theme
  can break. Instead:
  - parameterize **`test_page_loads_without_console_errors` only** over
    `('light', 'dark')`, setting the cookie via `context.add_cookies`;
  - leave the declared flows at the default theme.

  Note `ALLOWED_CONSOLE = ()` — the allowlist is empty and
  `test_console_allowlist_has_no_dead_entries` keeps it honest. Dark mode must
  not add an entry.

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
| Value-named token `var()` callsites | **27** (CSS), **0** (templates) | the rename surface |
| — of which `--bg-gray-medium` | **4** (draft said 5) | `grep -ro 'var(--bg-gray-medium)'` |
| Fully-rendered-HTML cache routes | 5 | `user_aware_cache_key` |
| Bare `text-dark` that are `stat-value text-dark text-detail` | **15 of 20** | 3 card partials; see decision 5 |
| App-owned JS files | 17 | `static/js/` — `theme-toggle.js` makes 18 |
| `color-scheme:dark` shipped by Bootstrap | **yes** | first declaration of its dark block |
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

## Handoff to PR 4 — dark charts

**Decision (Ben, 2026-08-01): PR 3 ships without chart theming.** The known
gap is that chart SVGs carry baked colours, so in dark mode their legend text
is `#011837` on the `#1b2733` card (~1.3:1) and `UNITY_PALETTE_10[8]` space
blue nearly vanishes as a wedge fill. Affected: the allocations dashboard,
the three status history pages, the jobs explorer and the disk-scans tabs.
Figures do render on a transparent background, so they read as low-contrast
rather than as white boxes.

PR 4 is now genuinely small, because PR 1 and this PR pre-solved its
structure:

1. **Pass `theme=read_theme()` at the chart call sites** — 22 places that
   already pass `layout=`, across 6 files. `chart_view` accepts `theme=` and
   composes it into the cache key today; nothing else has to change for the
   plumbing.

   | File | Sites | How layout arrives |
   |---|---|---|
   | `dashboards/allocations/blueprint.py` | 5 | 4 via a local `layout`, 1 `read_layout()` |
   | `dashboards/user/blueprint.py` | 4 | `read_layout()` at each |
   | `dashboards/status/blueprint.py` | 4 | `read_layout()` at each |
   | `jobs/routes.py` | 6 | a local `layout` threaded from the registrar |
   | `disk_scans/routes.py` | 2 | ditto |
   | `utils/fragments.py` | 1 | `read_layout()` — the registrar, covers 27 routes |

   The two that are *not* call sites are `charts/__init__.py:188` and
   `charts/base.py:349`, which are the plumbing itself and already forward
   `theme`.

   `utils/fragments.py:158` is the high-leverage one: it resolves the axis once
   for every jobs / disk-scans fragment, so `theme` should be added there the
   same way — and then **carried by hand** through each panel renderer, which
   is exactly the hop that silently failed for `layout` in the three jobs
   histogram panels. `test_renderers_forward_the_layout_they_are_given` is the
   gate that caught it; give it a `theme` sibling in the same commit, not
   after.
2. **Tune `Theme.DARK`.** Its chrome values exist. The two decisions a
   mechanical swap cannot make are still open and are recorded in
   `CHART_ARCHITECTURE.md` Appendix B: the space-blue pie wedge, and the
   `alpha=0.85` stackplots that composite against the page.
3. **Blend targets need no thought** — `--surface-card` is `#1b2733`, and
   `test_dark_card_matches_chart_blend_target` (added in D7) fails if the CSS
   and `Theme.DARK.shade_toward` / `legend_face` / `segment_edge` ever drift.
4. **Regenerate the fingerprint snapshot** at every layout × theme, in the
   same commit:
   `CHART_FINGERPRINT_REGEN=1 pytest tests/unit/test_chart_fingerprints.py`.
5. **Redis**: chart keys hash *input data*, not rendering code, so warm
   entries serve old-code SVGs for up to 600 s. Run
   `sam-admin cache --refresh --category chart` after deploying.

### Starting state for that session

- Branch `dark_mode_sans_charts`, PR #419 vs `staging`, D0–D9 landed and CI
  green. PR 4 should branch from it (or from `staging` once #419 merges).
- The defect to fix, precisely: chart `<text>` carries a baked
  `fill="rgb(1,24,55)"` (`--ncar-space-blue`) against the `#1b2733` card —
  **1.3:1**. Verified in-browser on `/allocations/projects`.
- Figures already render on a **transparent** background, so there is no white
  box to remove — only the ink is wrong.
- `e2e/test_dark_mode.py::test_sampled_surfaces_are_legible` samples chrome,
  not SVG text. Extending `SAMPLES` to chart `<text>` would turn the current
  chart defect into a red test — worth doing *first*, as the failing test that
  PR 4 then makes pass.
- Local browser tier needs `pytest-playwright` + chromium; it is already
  installed in this repo's conda env (see the Makefile `e2e` target).

Note for whoever ships it: dark mode doubles the chart cache working set. The
`svg.fonttype='none'` change in PR 1 cut SVG size 40–77 %, which mostly pays
for it, but the `maxmemory` rationale deserves real numbers rather than an
assumption.

## Appendix E — deviations found during implementation (2026-08-01)

The plan was a good map; these are the places the territory differed. All
were found by building and measuring, not by re-reading.

| # | Plan said | Implementation found | Where |
|---|---|---|---|
| 1 | D2 bridges `--bs-body-bg` to "tier 2" | It must be **`--surface-card`**, not `--surface-page`. Upstream, `--bs-body-bg` drives twelve component variables (card, modal, dropdown, table, form-control…) because Bootstrap's own page and card are both `#fff`. SAM separates them, so that variable already plays the *card* role. `--surface-page` would have tinted every modal and, in dark, made them track the page instead of the card. | D2 |
| 2 | D2 has "no visual change" | Two real light-mode shifts: `--bs-body-color` #212529→#323133 and `--bs-border-color` #dee2e6→#E2E8F0. Both are SAM's own token displacing a Bootstrap default — the point of the bridge — but they are changes. | D2 |
| 3 | The `-rgb` companions were not mentioned | Bootstrap consumes `--bs-body-bg-rgb` (×3), `--bs-body-color-rgb` (×5), `--bs-secondary-bg-rgb`, `--bs-tertiary-bg-rgb`. Bridging only the hex form leaves those on the framework default. Tier 2 needed matching triplets. | D2 |
| 4 | D5's `bg-light` → `bg-body-secondary` is "a declared light-mode shift" | **No shift needed.** D2's bridge makes `--bs-tertiary-bg` = `--surface-tertiary` = `#f8f9fa`, exactly what `.bg-light` renders — so `bg-body-**tertiary**` is byte-identical in light *and* follows the ramp in dark. The plan's one predicted visible regression was avoidable. | D5 |
| 5 | `table-light` → (unspecified) | No stock Bootstrap class flips: the dark block redefines **none** of the `.table-*` variants, nor `--bs-light-rgb`. Needed a SAM class, `.table-subtle`, valued to match `.table-light` exactly. | D5 |
| 6 | `text-dark` splits into "bare (convert)" vs "co-occurs with `bg-*`" (keep) | The real rule is "sits on a fill that **stays put**". Three families the inventory missed, all of which break on dark: `bg-light text-dark` badges (28 — the background became theme-aware), `bg-*-subtle text-dark` (6 — the `-subtle` tints *are* redefined in the dark block), and `bg-secondary bg-opacity-25 text-dark` (4 — a 25 % wash composites light in light and dark in dark). Also: a codemod cannot see through `bg-{% if %}danger{% endif %}`, so 2 sites had to be reverted by hand. | D5 |
| 7 | (not stated) | **59 CSS rules set a tier-1 brand primitive as `color:`** — `--ncar-space-blue` ×16, `--ncar-blue` ×21, `--ncar-gray` ×13, `--ncar-navy` ×9. Invariant by design, and `#011837` on `#1b2733` is unreadable. Needed four new role tokens (`--text-heading`, `--text-title`, `--text-link`, `--text-tertiary`) with light values identical to the primitives. The plan inventoried the `text-dark` *utility* but not CSS rules using brand colour as text. | D6 |
| 8 | (not stated) | The same defect again in **`--bs-*-color` custom properties** — `--bs-card-color: var(--ncar-space-blue)` gave every card body **1.17:1** contrast. Missed by D6's codemod, whose lookbehind excluded custom-property definitions. 19 sites. **Found only by the D8 contrast assertions**, on pages already eyeballed and called good. | D8 |
| 9 | (not stated) | The collapsed accordion chevron is a data-URI SVG with `fill='%23011837'` baked in. A data URI cannot reference a CSS variable, so dark mode needs a second URI. | D8 |
| 10 | Decision 3 recommends a `--surface-raised` navbar | Superseded by resolved decision 2: the navbar stays a **light chip** until the reversed brand marks land. That in turn requires pinning Bootstrap's `--bs-navbar-*` back to light values — its dark block sets `rgba(255,255,255,.55)`, invisible on white. | D7 |
| 11 | Cache key scheduled at D8 | Moved to **D3**. Leaving five rendered-HTML routes theme-blind across D4–D7 would make every theme bug seen on them indistinguishable from a stale-cache artifact, during exactly the commits spent clicking between themes. | D3 |
| 12 | (not stated) | `/admin/expirations` was being swept as a *page* by `e2e/test_console_sweep.py`, but it is an htmx fragment with no `<html>`. Pre-existing gap in the sweep's rule-based filter; it passed only because fragments emit no console errors. | D8 |

**What the plan got right and is worth repeating**: the cookie carrier, the
three-tier token layer, the ordering that makes D3 "visible and broken", the
`text-muted` × 624 free win, and the watermark analysis (single `#faa119` at
33 % alpha — it needed no code, exactly as predicted).

**The methodological lesson**: items 7, 8 and 9 are all the same defect —
*brand colour used as foreground* — found three times, at three different
syntactic hiding places, by three different means (a runtime palette
injection, a grep, and an automated contrast assertion). Only the third found
the worst instance. Eyeballing screenshots missed a 1.17:1 card body twice.

## Appendix D — corrections to this document's first draft (2026-08-01)

Recorded so the diff between drafts is auditable rather than silent. Every
count in the original was re-run against `staging@bee0f4d`; all of them held
except where noted.

| # | First draft | Actually | Impact |
|---|---|---|---|
| 1 | Treated the theme carrier as a new design ("Decision: a cookie…"), written before PR 1 merged | PR 1 shipped the identical mechanism for **layout** — `LAYOUT_COOKIE`/`read_layout`/`layout-axis.js` — and left named hooks for this PR in three files. `chart_view` already accepts `theme=` and keys on it; `user_aware_cache_key`'s docstring already specifies the change | The carrier is now *followed*, not designed. Removes a decision and shrinks PR 4 to "pass the argument" |
| 2 | Implied theme would ride the same two channels as layout | Theme needs **one** channel. Layout is discovered client-side after first paint; theme is declared by a click that reloads, so cookie and browser can never disagree | Drops the `?theme=` htmx param, its key-space split, and its duplicate-query-key defence |
| 3 | `theme` into `user_aware_cache_key` scheduled as **D8** (last) | Belongs in **D3**, with the carrier | Avoids four commits of theme bugs that are indistinguishable from stale-cache artifacts, on exactly the pages under manual test |
| 4 | "20 bare `text-dark` → `text-body-emphasis`" as a mechanical swap | 15 of 20 are one repeated string over `.stat-value`, which already sets `color: var(--ncar-navy)` that `text-dark`'s `!important` suppresses | New design decision 5; the "mechanical" framing would have shipped either an unreadable navy on dark or an undeclared light-mode restyle of the three most-viewed cards |
| 5 | "28 `var()` callsites"; `--bg-gray-medium` × 5 | **27**; `--bg-gray-medium` × **4** | Cosmetic |
| 6 | "`e2e/` … parameterize the fixture so every swept page renders twice" | `e2e/` is at the **repo root**, and "the fixture" would double the declared interaction flows too | Parameterize the route sweep only |
| 7 | `allocations/blueprint.py:307,622` | `:309,635` | Line drift from #414 |
| 8 | (not stated) | `utils/csp.py`'s docstring says "Four routes cache fully-rendered HTML"; there are **five** | Pre-existing drift, fixed in D0 |
| 9 | (not stated) | Bootstrap's dark block already sets `color-scheme:dark`, so native controls/scrollbars need no work | One less thing |
| 10 | (not stated) | The navbar utility menu is `is_authenticated`-gated; the login page is a separate shell | Toggle must sit outside the conditional and be duplicated into `login.html` |
