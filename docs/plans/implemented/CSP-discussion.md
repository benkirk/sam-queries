# Content-Security-Policy — IMPLEMENTED 2026-06-12

*Captured 2026-06-10, during Phase A hardening wrap-up (PR #296). CSP was
deliberately deferred in `PRODUCTION_IMPROVEMENTS.md` item 3; this note records
the what/why/when for future reconsideration (likely Phase B or after).*

> **Outcome (2026-06-12, `hardening/csp` branch):** Implemented straight to
> enforcing — and stricter than sketched below. All six CDN assets were
> vendored into `static/vendor/` (committed, verified against the pinned SRI
> hashes; Poppins self-hosted closes the one un-pinnable asset), so the policy
> is essentially all-`'self'` with zero third-party origins — avoiding the
> mega-CDN allowlist weakness (jsdelivr/unpkg serve every npm package).
> The design is **nonce-free**: per-request nonces are incompatible with the
> four Redis-cached rendered-HTML routes, so every inline script/handler was
> extracted to static JS (`actions.js` delegation core, `pickers.js`,
> `dashboard-init.js`, `admin-cards.js`, `modals.js`, `form-helpers.js`);
> dynamic data rides `data-*` attributes and non-executable
> `<script type="application/json">` blocks. `style-src` keeps
> `'unsafe-inline'` (245 inline `style=` attrs, accepted tradeoff).
> The policy is generated from `webapp/vendor_assets.py` by
> `webapp/utils/csp.py`; `CSP_MODE` (enforce | report-only | off) is the
> no-rebuild rollback knob; `tests/unit/test_template_csp_lint.py` keeps the
> templates inline-script-free; `X-Frame-Options` is retired under enforce
> (`frame-ancestors 'self'` supersedes it). htmx hardened with
> `allowEval:false` (`allowScriptTags` deliberately left true — false would
> strip the JSON data blocks from swaps; CSP itself blocks injected inline
> scripts). No report endpoint (console-only, per decision).
> Deploy note: flush the Flask-Cache Redis DB at rollout — pre-extraction
> cached HTML still references inline handlers (self-heals in
> CACHE_DEFAULT_TIMEOUT=300s otherwise).

## What CSP is

Content-Security-Policy is a single HTTP response header in which the server
hands the browser an **allowlist of where content is permitted to come from**.
Something like:

```
Content-Security-Policy: script-src 'self' cdn.jsdelivr.net unpkg.com; style-src 'self' cdn.jsdelivr.net fonts.googleapis.com; ...
```

The browser then *refuses to execute or load anything outside that list* —
scripts, stylesheets, fonts, images, XHR/fetch destinations, frames — each
controllable by its own directive. Critically, a strict policy also blocks
**inline scripts** (`<script>...</script>` blobs embedded in the HTML and
`onclick=`-style attributes) unless each one is explicitly blessed with a
nonce or hash.

## What it would buy us

The headline win is **neutering cross-site scripting (XSS)**. Every other
defense we have tries to *prevent injection* (Jinja2 auto-escapes user data
into templates). CSP is the backstop for when prevention fails: if an attacker
ever manages to smuggle `<script>steal(document.cookie)</script>` into a
rendered page — through a `|safe` filter someone adds carelessly, an unquoted
HTML attribute, a future markdown renderer — a strict CSP means the browser
**won't run it anyway**, because inline script isn't on the allowlist. And it
can't exfiltrate to `evil.com` because `connect-src` doesn't include it. One
header turns "XSS bug = account compromise" into "XSS bug = console error."

Secondary wins:

- `frame-ancestors` — the modern replacement for our `X-Frame-Options` header.
- Complements SRI: SRI says *"this exact file content from that CDN"*, CSP
  says *"only these origins at all."* SRI protects against a compromised CDN
  serving tampered files; CSP protects against our own pages being tricked
  into loading from somewhere new.

## Why it was deferred

Strict CSP's cost is the **inline-script problem**. SAM's templates carry a
meaningful amount of inline JavaScript — the `onsubmit="samConfirm(...)"`
handlers on the impersonate forms, chart-setup snippets, small per-page
`<script>` blocks. Under a strict policy every one of those either breaks or
needs a per-request nonce threaded through the templates. That's the
"inline-script audit": find them all, move them into static `.js` files or
nonce them. Doing it carelessly means a silently broken UI in exactly the
dusty corners we don't smoke-test.

What *did* land in Phase A is the prerequisite: **`src/webapp/vendor_assets.py`**
is the single source of truth for our five external origins (jsdelivr, cdnjs,
code.jquery.com, unpkg, Google Fonts), so when CSP comes, the header should be
**generated from the registry** — add a CDN dependency and the policy updates
itself, no drift between what templates load and what the header permits.

## Do we care, for this application?

Honest assessment: **moderately — defense-in-depth, not a gap.**

*For:* SAM is heading for public internet exposure, renders user-influenced
data (project titles, abstracts, names — some arriving from XRAS/external
users), and an authenticated session can see allocation/charging data across
NCAR. One template escaping slip plus one phishing email is the scenario CSP
kills.

*For patience:* the primary defenses are genuinely in place — Jinja2
autoescaping everywhere, app-wide CSRF, SameSite + Secure + HttpOnly cookies,
no raw-HTML rendering of user input, SRI on the CDN supply chain. CSP would
protect against a *mistake class*, not a known hole.

## Recommended adoption path (when picked up)

1. Generate a candidate policy from `webapp/vendor_assets.py` origins +
   `'self'`.
2. Deploy as **`Content-Security-Policy-Report-Only`** — the browser reports
   violations (to a `report-uri`/`report-to` endpoint or just the console)
   but **blocks nothing**. Near-zero risk.
3. Run for a couple of weeks of real traffic: the reports enumerate every
   inline script users actually hit — the audit does itself.
4. Fix the findings (move inline JS to static files, or nonce what must stay
   inline), then flip the header to enforcing.
5. Fold `frame-ancestors 'self'` in and retire `X-Frame-Options` at the same
   time.

Natural home: the `init_security_headers()` hook in
`src/webapp/utils/security_headers.py`, which already carries the comment
pointing here.
