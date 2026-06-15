# ZAP baseline — 2026-06-15 (post-hardening, canonical)

**This is the current baseline.** It supersedes the original pre-hardening
`zap-basic-report.html` (NRIT, base `b166d9b`) and the interim verification in
[`09_zap_rescan-2026-06.md`](09_zap_rescan-2026-06.md). It was taken *after* the
header remediation in **#312** (CORP + Permissions-Policy) and the CSP/CSRF/SRI
hardening (#296/#303/#308) all merged.

## How to reproduce

```bash
docker compose build webdev          # ensure the image has current headers
./scripts/zap_probe_docker.sh        # passive baseline (default)
```

Passive `zap-baseline.py` (spider + AJAX spider + passive rules) from
`ghcr.io/zaproxy/zaproxy:stable`, authenticated as an ADMIN user via dev
auto-login, 131 URLs. Result: **`FAIL-NEW: 0  WARN-NEW: 11  PASS: 56`**.
Reports are git-ignored; this note is the committed record.

## Resolved ✅

Every finding from the original NRIT scan is closed:

| Original finding | Risk | Closed by |
|---|---|---|
| Content Security Policy (CSP) Header Not Set | Medium | CSP enforce (#303) |
| Missing Anti-clickjacking Header | Medium | CSP `frame-ancestors` (#303) |
| Sub Resource Integrity Missing | Medium | vendored assets + SRI (#296) |
| X-Content-Type-Options Missing | Low | security_headers (#296) |
| Cross-Domain JS Source File Inclusion | Low | CDNs retired (#296) |
| Big Redirect Detected | Low | — (not reproduced) |
| **Permissions-Policy Not Set** | Low | **#312** |
| **Cross-Origin-Resource-Policy Missing** | Low | **#312** (`same-origin`) |
| Absence of Anti-CSRF Tokens | Medium* | CSRF enforced (#296) |

HSTS is **confirmed live** at the production edge (it cannot be observed in this
local HTTP scan by design — it's HTTPS/prod-gated). Via
`scripts/cirrus_healthcheck.sh` § 7 against `https://samuel.k8s.ucar.edu/`
(2026-06-15): `Strict-Transport-Security: max-age=31536000; includeSubDomains`.

## Residual findings — risk-accepted / deferred

### 1. CSP `style-src 'unsafe-inline'` — Medium [rule 10055] — **ACCEPTED**

The CSP allows inline styles (`style-src 'self' 'unsafe-inline'` in
`webapp/utils/csp.py`). ZAP flags the `unsafe-inline`. **Accepted as low risk**:

- **It is not script execution.** No supported browser executes JavaScript from
  CSS — the historical CSS→JS vectors (IE `expression()`, Mozilla
  `-moz-binding`) are gone. Script execution is governed by `script-src`, which
  is locked to `'self'` (no `unsafe-inline`); full XSS is already blocked there.
- **The real residual is CSS-injection** (attribute-selector + `background:
  url()` exfiltration, UI redress), which *requires a pre-existing HTML/style
  injection foothold* in a rendered page. `unsafe-inline` doesn't create that
  foothold — it only means CSP wouldn't block the styling half of such an attack.
- **That foothold is not readily reachable here.** Verified posture: Jinja2
  autoescape is ON (no `autoescape false`, no `render_template_string`, no
  `Markup()`); the 16 `{{ … | safe }}` sinks are all *first-party
  server-generated* markup (matplotlib/SVG charts, template help text, the
  server-rendered members table), not raw user input. Combined with `script-src
  'self'` and CSRF, the marginal risk is low. (Accepting `style-src
  'unsafe-inline'` while refusing it for `script-src` is standard practice.)

**Standing condition for this acceptance:** autoescape stays on, and `|safe` is
never pointed at user-controlled data. A future `|safe` on user input reopens
the CSS-injection path — re-evaluate if that changes.

**Elimination path (if ever revisited):** CSP nonces/hashes for the inline
`<style>` blocks, or extract them to served `.css` files, then drop
`'unsafe-inline'` from `style-src`.

### 2. Cross-origin site isolation — COOP / COEP — Low [rule 90004] — **DEFERRED**

`#312` set `Cross-Origin-Resource-Policy: same-origin`, which clears the CORP
sub-finding. The same rule (Spectre site isolation) also wants
`Cross-Origin-Opener-Policy` and `Cross-Origin-Embedder-Policy`, which remain
unset. **Deferred as low practical risk:** the protection targets cross-origin
data theft via speculative execution, which needs `SharedArrayBuffer` /
cross-origin-isolated features this server-rendered dashboard does not use.
`COEP: require-corp` is also disruptive (forces every subresource to opt in) and
can break embeds/OIDC popups. If revisited, `Cross-Origin-Opener-Policy:
same-origin` is the low-risk half and can be added to `security_headers.py`
alone; `COEP` warrants its own validation pass.

## Noise / dev-only (no action)

- **Server Leaks Version Information ("Server" header)** [10036] — the local
  Werkzeug dev server; prod serves via gunicorn. Dev artifact only.
- **Dangerous JS Functions** [10110] — inside the vendored `htmx-2.0.4.min.js`.
- Suspicious Comments, Timestamp Disclosure, Modern Web Application, Session
  Management, Storable/Non-Storable Content — standard passive-scan noise.

## Worth a glance (low, partly scanner-induced)

- **Information Disclosure – Sensitive Information in URL** [10024] and **User
  Controllable HTML Element Attribute** [10031] — the admin *edit* forms and the
  allocations filters submit via **GET**, so field values land in the query
  string (→ referrer / logs / history) and are reflected into the page. Largely
  the ZAP spider exercising forms, and the XSS angle is mitigated by autoescape,
  but the GET-form URL exposure is concrete: consider POST for the admin edit
  forms / any filter carrying sensitive values. Tracked as a minor follow-up,
  not part of this baseline's remediation.

## Active scan (scoped) — 2026-06-15

Beyond the passive baseline above, a **scoped active scan** was run to probe for
injection-class bugs (the kind passive scanning cannot find):

```bash
./scripts/zap_probe_docker.sh --scoped --max-mins 45
```

`--scoped` runs an ACTIVE scan via ZAP's Automation Framework, time-boxed and
scoped by exclusion to the parameterized routes (it skips the heavy read-only
`/status`, `/admin/htmx/*`, `/static`, `/auth/logout` paths so it completes in
tens of minutes instead of the hours an unbounded `--full` takes). The run spent
~48 min deeply fuzzing the primary attack surface — `/admin/` filters,
`/admin/project/<code>/edit` (×5 projects), `/allocations/`, `/user/`.

**Result: no exploitable findings.** The only active-class alert was:

- **SQL Injection [rule 40018] ×35 — VERIFIED FALSE POSITIVE.** Independently
  code-traced end-to-end. The flagged params reach the DB only as bound ORM
  expressions or coerced form fields, never string-interpolated SQL:
  - `/admin/` filters → `Facility.facility_name.in_(...)` / `Resource.resource_name == …`
    (bound); `time_range` is used only as a dict key.
  - `/admin/project/<code>/edit` → `EditProjectForm` type-coerces every field
    (`active=3-2` fails `Bool` → HTTP 400 before any query), then ORM attribute
    assignment.
  - The only `text(f"…")` SQL in the codebase (`projects.py`, `base.py`)
    interpolates *only* loop-index placeholder names and hardcoded
    `__tablename__`; all values are bound. `system_status` queries are pure ORM
    `select()`.
  ZAP's boolean/expression heuristic fired on response-size differences from
  filtered/validated endpoints, not SQL parsing. (Optional regression guard: a
  CI grep asserting no `text(f"…")` interpolates request-derived data.)

No XSS, path-traversal, command-injection, or other active rules fired. The
`style-src 'unsafe-inline'` Medium and the info/noise items match the passive
baseline above.

**Coverage caveat:** the scoped scan uses the regular (JS-blind) spider, so it
focused on the main routed endpoints; HTMX-fragment routes are excluded by
design. The passive baseline's AJAX spider is what covered the broader 131-URL
surface for the header/passive checks. The injection-relevant surface (filters +
edit/mutation forms) was the part actively fuzzed.

## Caveats

- A **local** scan proves the code path emits the headers; the **prod**
  confirmation is `cirrus_healthcheck.sh` § 7 against the live HTTPS edge (run
  post `main→cirrus`).
- Re-run any time: `./scripts/zap_probe_docker.sh` (passive baseline),
  `--scoped` (time-boxed active, recommended), or `--full` (unbounded whole-app
  active). All active modes are local-only / prod-guarded.

## Bottom line

0 High, 0 unaccepted Medium, 0 unaccepted Low. All original NRIT findings closed;
HSTS confirmed live; a scoped active scan of the parameterized surface found no
exploitable injection (the one High SQLi is a verified false positive). Two
residuals are explicitly risk-accepted/deferred with rationale above
(`style-src 'unsafe-inline'`; COOP/COEP site isolation).
