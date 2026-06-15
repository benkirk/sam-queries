# Content-Security-Policy: Straight-to-Enforce Implementation

## Context

Phase A/B hardening (PRs #296/#297, merged as b0797ff) landed every CSP prerequisite:
the `VENDOR_ASSETS` registry (`src/webapp/vendor_assets.py`) as single source of truth
for the 5 CDN origins with SRI, and the `init_security_headers()` hook
(`src/webapp/utils/security_headers.py`) with an explicit "CSP goes here, generate it
from the registry" comment. SAM is heading for non-VPN exposure; CSP is the backstop
that turns "template-escaping slip + phishing email = session compromise" into
"console error" by refusing to execute anything not on the allowlist.

**User decisions (2026-06-12):** straight to enforce in one push (no report-only soak),
no self-hosted report endpoint (console-only violations), accept
`style-src 'unsafe-inline'` permanently (245 inline `style=` attrs stay; inline styles
can't execute script), and **vendor all 6 CDN assets locally**. Rationale for
vendoring: `script-src https://cdn.jsdelivr.net https://unpkg.com` allowlists every
npm package ever published (an injected `<script src>` tag bypasses CSP entirely —
Google CSP Evaluator flags these origins for exactly this), browser cache
partitioning (~2020) killed the shared-CDN performance benefit, and self-hosting
Poppins closes the one asset that can't carry SRI. Result: zero third-party origins
in the policy.

## Honest cost/benefit

**Benefit:** Neutering XSS exfiltration/execution is the headline — SAM renders
user-influenced text (project titles/abstracts, XRAS-sourced names) and an
authenticated session sees cross-NCAR allocation data. Defense-in-depth, not a known
hole: Jinja autoescaping, CSRF, SRI, cookie flags are all in place. Secondary wins:
`frame-ancestors` (retires `X-Frame-Options`), `object-src 'none'`, `base-uri 'self'`,
`form-action 'self'`, plus htmx hardening (`allowEval:false`, `allowScriptTags:false`)
that closes script execution paths CSP alone doesn't.

**Cost:** ~777 lines of inline `<script>` across 20 templates extracted into ~6
consolidated static JS files; 45 inline `on*=` handlers across 30 files rewritten to
delegated listeners; 3 `hx-on::` attributes migrated; 4 `<style>` blocks extracted.
~5-7 focused dev-days. Ongoing complexity: every future inline script/handler breaks
loudly in dev (mitigated by a CI lint guard + the registry-generated policy, so CDN
additions auto-flow). Real risk without a report-only soak: silent breakage in dusty
admin corners — mitigated by enforce-in-dev during development, a manual smoke
checklist, and an env-var rollback (`CSP_MODE=report-only|off`, no rebuild).

**Key design constraint discovered:** four routes cache fully-rendered HTML in Redis
per-user (`dashboards/admin/orgs_routes.py:101,171`,
`dashboards/allocations/blueprint.py:246` — the full allocations dashboard — and
`:578`). Per-request nonces are incompatible with cached HTML (stale nonce ≠ fresh
header), so the design is **nonce-free**: zero inline executable scripts;
`script-src 'self' + CDN origins` only.

## Target policy (generated, not hardcoded)

```
default-src 'self';
script-src 'self';
style-src 'self' 'unsafe-inline';
font-src 'self';
img-src 'self' data:;
connect-src 'self';
frame-src 'self' [+ origin of GOOGLE_CALENDAR_EMBED_URL when configured];
frame-ancestors 'self';
object-src 'none'; base-uri 'self'; form-action 'self'
```

Notes: with all assets vendored, no third-party origins remain except the optional
calendar iframe; `data:` in `img-src` for Bootstrap/dashboard.css SVG data URIs;
OIDC (Entra) is pure 302 — no IdP directives needed; **no `report-uri`** (console-only
per decision). The calendar iframe is at
`templates/dashboards/status/fragments/reservations.html:32` /
`config.py:61 GOOGLE_CALENDAR_EMBED_URL`.

## Delivery model

A series of commits on the current `hardening` branch, each locally testable via
`docker compose up webdev --watch` (and `webapp` for the prod-like image), then **one
PR to `staging`**. During the branch work the CSP header ships in **report-only**
mode so webdev stays usable and the browser console acts as a live violation
worklist; the enforce flip is the last commit. Before opening the PR: a dedicated
**Playwright smoke session** (user restarts Claude with Playwright available)
exercising the full checklist against webdev with enforce active.

Commit sequence (≈ one commit per numbered step below; each leaves the branch green):
1. Vendor assets + registry rework + TTF cleanup + linter excludes  (step 0)
2. CSP builder + config flags (default `report-only`) + header hook + header/builder
   tests + template lint guard seeded with current-debt allowlist  (steps 1, 2, 6)
3. Delegation core (`actions.js`) + pickers extraction as the exemplar  (step 3 + pickers)
4. Allocations + admin dashboards (the cached pages)  — ratchet lint allowlist
5. Admin cards + modals  — ratchet
6. Form fragments + `hx-on::` migrations + remaining `on*=` sweep + `<style>` blocks  — ratchet to zero
7. Enforce flip: default `CSP_MODE=enforce`, XFO retirement, htmx hardening meta tag,
   docs moves  (steps 5, 7)
8. *(separate session, Playwright available)* Smoke-test the full checklist against
   webdev with enforce; fix findings; then PR to `staging`.

## Implementation steps (detail per area)

### 0. Vendor all CDN assets locally — `static/vendor/`, committed to the repo
- **Storage decision: commit to repo** (user, 2026-06-12). Precedent exists
  (`static/fonts/poppins/` already commits ~6 MB of TTFs, dormant since ~PR #271);
  committed assets ride the existing packaging/serving/bind-mount/CI paths with zero
  new machinery. Hygiene: mega-linter `FILTER_REGEX_EXCLUDE` for `static/vendor/`,
  `.gitattributes` `linguist-vendored` marks.
- Download the 6 registry assets, **verifying each against its existing SRI hash**
  (the pinned hashes become download checksums — supply-chain pinning survives):
  - `bootstrap-5.3.3.min.css` + `bootstrap-5.3.3.bundle.min.js` (jsdelivr)
  - `jquery-3.6.0.min.js` (code.jquery.com)
  - `htmx-2.0.4.min.js` (unpkg 302 target `dist/htmx.min.js`)
  - Font Awesome 6.5.2: `all.min.css` + the `webfonts/` directory it references via
    relative `../webfonts/` paths — preserve that layout under
    `static/vendor/fontawesome-6.5.2/{css,webfonts}/`
  - Poppins: woff2 files for weights 300–700 + a hand-written local
    `poppins.css` with `@font-face` rules (Google serves per-UA CSS; we fetch the
    woff2 variants directly — no SRI existed here, so this *closes* a gap).
    **Delete the dormant `static/fonts/poppins/` TTF set** (~6 MB, 18 variants,
    unreferenced) — net repo shrink despite the new vendor files.
- Rework `src/webapp/vendor_assets.py`: entries get a local `path` (rendered via
  `url_for('static', ...)` in the macros); `integrity`/`crossorigin` dropped for
  local assets; version stays in filename + registry comment for upgrade tracking.
  Keep the registry + `vendor_css()`/`vendor_js()` macros as the single source of
  truth — templates don't change beyond what the macros emit.
- Docstring: document that any *future* genuinely-external asset must carry a full
  URL + SRI + (if it fetches at runtime) a `csp_extra: {directive: source}` key, and
  the CSP builder will pick its origin up automatically.

### 1. Policy builder — new `src/webapp/utils/csp.py`
- `build_csp_directives(vendor_assets, config)` derives directives from
  `VENDOR_ASSETS` (urlsplit → scheme+host for any remaining/future external URLs;
  local-path entries contribute nothing beyond `'self'`), `kind` → directive mapping,
  optional per-asset `csp_extra` key, `GOOGLE_CALENDAR_EMBED_URL` → `frame-src`.
  `render_csp(directives)` joins to header string. Pure, unit-testable. With
  everything vendored the output is the all-`'self'` policy above, but the
  registry-derivation machinery is what prevents drift if a CDN asset ever returns.

### 2. Header hook — `src/webapp/utils/security_headers.py`
- `CSP_MODE` config: `'enforce' | 'report-only' | 'off'` (env-overridable). Code
  default **`report-only`** while extraction commits land (commit 2), flipped to
  **`enforce`** in the final commit; thereafter `report-only`/`off` remain as the
  no-rebuild rollback knob (helm values change). Note: in report-only mode,
  `frame-ancestors` is ignored by browsers — acceptable for a diagnostic mode.
- Compute policy once at init; `h.setdefault()` per response (preserves route override).
- Retire `X-Frame-Options` only when mode == enforce (`frame-ancestors` replaces it);
  keep XFO in report-only/off modes.
- Skip CSP for `request.path.startswith('/database')` — Flask-Admin's bundled templates
  carry inline JS; it's dev-only (`FLASK_ADMIN_ENABLED` off in prod). One-line carve-out
  with comment.
- `CSP_REPORT_URI` is NOT implemented (console-only decision).

### 3. Delegation core — new `static/js/actions.js`
- `registerAction(name, fn)` registry + delegated `click`/`change`/`submit` (capture)/
  `input` listeners on `document.body` dispatching on `[data-action]` /
  `form[data-confirm]`. Delegation is **mandatory**, not stylistic: htmx swaps
  fragments, per-element bindings die after swap.
- Convention for per-swap init: `htmx.onLoad(root => ...)` — fires on initial load AND
  every swapped subtree. Each extraction classifies code as "delegated handler (bind
  once)" vs "init-on-swap (htmx.onLoad, scoped to root)". Misclassification = UI death
  on the *second* swap — review for this explicitly.
- samConfirm forms: `samConfirm` (htmx-config.js:259) is an **async Bootstrap modal**
  with `onConfirm` callback — the 2 `onsubmit=` impersonate forms become
  `form[data-confirm]` + capture-phase listener that always `preventDefault()`s, then
  `form.submit()` inside `onConfirm` (NOT the sync `return samConfirm(...)` shape).

### 4. Inline-script extraction — 20 templates → ~6 consolidated JS files
| New file | Absorbs |
|---|---|
| `static/js/actions.js` | all 45 `on*=` attrs; samConfirm forms |
| `static/js/pickers.js` | `dashboards/fragments/date_range_picker.html` (71 ln), `time_range_picker.html` (12) |
| `static/js/dashboard-init.js` | `dashboards/allocations/dashboard.html` (84 — **cached full page**), `dashboards/admin/dashboard.html` (79) |
| `static/js/admin-cards.js` | `organization_card.html` (69), `institutions_table.html` (63), `resources_card.html` (39) — incl. the 2 cached org/institution fragments |
| `static/js/form-helpers.js` | `exchange_allocation_form_htmx.html` (92), `create_project_form_htmx.html` (74), `create_mnemonic_code_form_htmx.html` (29), `add_member_form_htmx.html` (17), `edit_project.html` (24), `project_card.html` (13) |
| `static/js/modals.js` | `outage_modals.html` (48), `allocation_modals.html` (26) |

- Dynamic data: scalars/URLs → `data-*` attrs (`data-action-url="{{ action }}"`);
  structured → `<script type="application/json">{{ x|tojson }}</script>` data blocks
  (non-executable, exempt from script-src; `|tojson` escapes `</script>`). Behavior
  keys off `closest('.component-class')` so multi-instance pages (date pickers) work
  without the current uid-suffixed-global hack.
- Migrate the 3 `hx-on::after-request` attrs into delegated `htmx:afterRequest`
  listeners in `static/js/htmx-config.js` (266 ln, existing home for global htmx glue).
- Extract the 4 `<style>` blocks (`edit_project.html`, `configuration_card.html`,
  `project_allocation_tree_htmx.html`, `resource_details.html`) to static CSS.
- Load new JS files from the base templates (small files, HTTP-cached; not per-page).

### 5. htmx hardening — base template meta tag
`<meta name="htmx-config" content='{"allowEval": false, "selfRequestsOnly": true, "allowScriptTags": false}'>`
— only valid once ALL fragment inline scripts are extracted (`allowScriptTags:false`
kills swapped-in `<script>` execution; that's the point, and it must come last).

### 6. Tests
- Extend `tests/unit/test_security_headers.py`: builder unit tests (script-src is
  exactly `'self'` — no CDN origins, no `unsafe-inline`, no nonce; style-src has
  `unsafe-inline`; frame-src picks up calendar URL iff configured; a fake external
  registry entry with URL/`csp_extra` flows into the right directives — the
  anti-drift guarantee). Vendored-asset sanity test: every registry `path` exists
  under `static/`. Mode tests:
  enforce → `Content-Security-Policy` present + XFO absent; report-only →
  `...-Report-Only` + XFO present; off → no CSP; `/database/*` → no CSP any mode.
- New `tests/unit/test_template_csp_lint.py` — CI drift guard, regex over
  `src/webapp/templates/**/*.html`: forbid `<script>` without `src=` and without
  `type="application/json"`; forbid explicit `on(click|change|submit|input|...)=` attr
  list; forbid `hx-on:`; forbid `<style>`. Lands in commit 2 with an
  `ALLOWED_VIOLATIONS = {path: count}` dict seeded from the current inventory
  (documents the debt, blocks NEW debt immediately); each extraction commit ratchets
  it down; commit 6 empties it and it stays empty forever.

### 7. Docs + deploy notes
- Move `docs/plans/DEFERRED-CSP-discussion.md` → `docs/plans/implemented/` with outcome
  note; update `security_headers.py` docstring.
- Deploy note in PR: flush the Flask-Cache Redis DB at deploy (cached pre-extraction
  HTML still contains inline scripts; `CACHE_DEFAULT_TIMEOUT=300` self-heals in 5 min,
  flush makes it instant). Rollback: set `CSP_MODE=report-only` via helm values.

## Verification

- Per-commit: user runs pytest themselves (`source etc/config_env.sh && pytest`) —
  full suite + the new header/builder/lint tests; quick eyeball in webdev with the
  report-only console worklist (violation count must only decrease).
- Final smoke (separate session with Playwright; user restarts Claude): drive webdev
  with **enforce active**, walking the checklist below; console must show zero CSP
  violations on every page. Also run once against the prod-like `webapp` compose
  service. Checklist
  of dusty corners: date/time range pickers (preset + custom + epoch), allocations
  dashboard tabs/CSV export **on cache hit and miss**, org/institution cards
  (expand/sort/filter, then re-expand to exercise swap re-init), exchange-allocation +
  create-project + mnemonic forms, outage modals, impersonate confirm-modal flow,
  fk-picker, login page, status dashboard incl. calendar iframe.
- Confirm headers with `curl -sI` : CSP present, XFO absent, HSTS per env.
- Browser devtools network tab: zero requests to third-party origins (fonts render
  as Poppins, Font Awesome icons present, htmx/Bootstrap behaviors work — proves
  vendored assets actually serve).
- `/database` admin in dev: loads without CSP (carve-out works).

## Effort estimate (honest)

~5.5-7.5 dev-days: vendoring assets + registry rework (0.5), builder+hook+tests (1),
delegation core + pickers exemplar (1),
allocations/admin dashboards incl. cached pages (1-1.5), admin cards + modals (1-1.5),
form fragments + hx-on + on* sweep + style blocks (1-1.5), hardening flip + lint
ratchet to zero + smoke pass (0.5-1). Uncertainty: the 92-line exchange-allocation and
84-line allocations-dashboard scripts may hide cross-script globals (+0.5 day each);
manual smoke-testing is the real cost driver.
