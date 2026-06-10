# SAMuel Internet-Exposure Hardening — Merged Assessment (this review + PR #295)

## STATUS (updated 2026-06-10)

**Phase A (items 1–3) is COMPLETE AND VERIFIED IN PRODUCTION** — implemented as
8 commits on `hardening`, PR #296 vs `staging`, upstream CI fully green, local
suite 2250 passed; deployed to CIRRUS (`sha-77f82a3`) and verified live
2026-06-10 (see *Production verification* below and the PR #296 comment).
**Items 4 & 5 (Phase B) are OUTSTANDING** — see the Phase B section at the
bottom for the restart point.

| # | Commit | Scope |
|---|---|---|
| 1 | `7e70dd0` | Fail-closed prod auth: `ProductionConfig.validate()` rejects `AUTH_PROVIDER != oidc` + `DISABLE_AUTH=1`; `DEV_AUTO_LOGIN_ALLOWED` config flag gates auto-login registration; loud warnings on silent `declarative_base` / limiter `memory://` fallbacks [P0-1, P0-2] |
| 2 | `5a34fb9` | Security headers via `webapp/utils/security_headers.py`: HSTS (gated on `SESSION_COOKIE_SECURE`), nosniff, XFO SAMEORIGIN, Referrer-Policy `strict-origin-when-cross-origin` |
| 3 | `52496a6` | Vendor-asset registry + SRI: `src/webapp/vendor_assets.py` + `templates/fragments/vendor_assets.html` macros; base.html + login.html rewired; Google Fonts exempt (per-UA, no SRI possible) |
| — | `adb8419` | Suite hygiene: jobs-drawer test tracked plugin header rename ("MPI" → "Ranks per Node", hpc-usage-queries `7f4fd7b`); quiet limiter warning on *explicit* `memory://` |
| 4 | `820cc1d` | CSRF app-wide (new `flask-wtf` dep): `<body hx-headers>` token inheritance, hidden inputs on 4 plain forms, `@csrf.exempt` only on 7 non-cookie-credential routes, HTMX/API-aware CSRFError handler |
| 5 | `cf88470` | `FLASK_ADMIN_ENABLED` kill-switch: off by default in ProductionConfig + `helm/values.yaml`; `/database` never mounted publicly [P0-3] |
| 6 | `69cc6d3` | Route authz: allocations GET [P0-4], users/search [P1-7], charges → `require_project_member_access` [P1-4], rolling/threshold [P1-6], `members_fragment` gated, thin decorators (`require_member_management` / `require_admin_change` / `require_threshold_edit`) replace hand-rolled checks [P1-5]; HTMX-aware 403 handler |
| 7 | `97bd282` | OIDC polish: callback on `RATELIMIT_AUTH_LOGIN` [P2-4]; `id_token_hint` on logout (token stashed in session at callback) [P1-3] |

**Local interactive verification — DONE 2026-06-10** (Playwright + curl against
rebuilt containers): webdev:5050 stub click-login, HTMX member add/remove round-trip
under CSRF, fonts/icons/charts under SRI; webapp:7050 gunicorn headers + CSRF
round-trip; negative boots refused with the expected `EnvironmentError`s;
`/database` still served locally. Note: `flask-wtf` is a new dependency — fresh
checkouts rebuild the env via `source etc/config_env.sh`.

**Production verification — DONE 2026-06-10** (CIRRUS deploy `sha-77f82a3`,
evidence in the PR #296 comment):
- `cirrus_healthcheck.sh` 20 PASS / 2 WARN / 0 FAIL (both WARNs pre-existing:
  `baotoken` ES housekeeping + rollout-transient events); re-run post-smoke, same.
- Deployment env canary: `FLASK_CONFIG=production`, `AUTH_PROVIDER=oidc`,
  `DISABLE_AUTH=0`, `FLASK_ADMIN_ENABLED=0`; pods Ready ⇒ fail-closed
  `validate()` accepted the real OIDC env.
- HSTS live (`max-age=31536000; includeSubDomains`) + nosniff/XFO/Referrer-Policy;
  `/database/` → 404; login page OIDC-only with SRI attrs; anon API → 302.
- CSRF: tokenless POST → 400 both anon and authenticated; with-token POST passes
  the gate (no-write bogus-username probe).
- Real Entra/Duo login: zero SRI digest errors; session cookie post-login
  **1,920 bytes** (`Secure; HttpOnly; SameSite=Lax`) — under the ~4 KB ceiling
  with the id_token stored.
- Logout redirect captured: end-session URL carries `id_token_hint` + encoded
  `post_logout_redirect_uri`; SAM session dead afterward.
- **Follow-up (config, not code)**: Entra parks on its "Sign out" page instead of
  following `post_logout_redirect_uri` — register
  `https://samuel.k8s.ucar.edu/status/` as a post-logout redirect URI in the
  Entra app registration.
- ZAP passive-scan rerun: deferred (separate session).

## Context

SAMuel runs VPN-only at https://samuel.k8s.ucar.edu/ (CIRRUS/nwc1, ns `sam-queries`); goal is
public internet exposure. OIDC + rate limiting already in place and verified working. Two
independent investigations now exist:

- **This review (2026-06-10)**: probed the live cluster (`cirrus_healthcheck.sh`: 20 PASS /
  2 WARN / 0 FAIL) + `src/`, `helm/`, `containers/`, with claims verified directly in code.
- **PR #295 (dvance, NRIT audit, May 2026)**: 182-item register (15 P0 / 55 P1 / 112 P2),
  ZAP scans, synthesis in `docs/nrit-review-2026-05/08_synthesis.md`.

The two agree on the headline theme and complement each other on specifics. One of David's
P0s is already fixed: **P0-9 "Helm Deployment has zero probes" is stale** — live cluster has
readiness/liveness/startup probes wired to `/api/v1/health/*` (verified 2026-06-10).

**Already solid (verified, both reviews concur):** OIDC w/ PKCE, bcrypt timing-safe API
tokens, Redis-backed rate limiting with smart key segregation, ProxyFix, hardened session
cookies, ExternalSecrets/OpenBao for all creds, immutable `sha-*` image pins on the deploy
branch, probes/PDB/topology-spread, ORM-parameterized queries, TruffleHog+GitGuardian+
pre-commit secret scanning (zero committed secrets found).

## The 5 Considerations (merged, prioritized)

### 1. Auth config fails OPEN — make production fail CLOSED  (HIGH) ✅ DONE (`7e70dd0`)
*Found independently by both reviews; David's strongest cross-cutting theme (14 fail-open footguns).*
- `AUTH_PROVIDER` defaults to `'stub'` (`src/webapp/config.py:31`); `StubAuthProvider` accepts
  **any non-empty password** (`src/webapp/auth/providers.py:51-72`) [PR295 P0-2]
- `DISABLE_AUTH=1` auto-login honored under any config (`src/webapp/utils/dev_auth.py:31`) [P0-1]
- `FLASK_CONFIG` defaults to `'development'` (`src/webapp/config.py:217`)
- `ProductionConfig.validate()` (`config.py:166-172`) never rejects stub or DISABLE_AUTH

Helm sets all three correctly today (`helm/values.yaml:87,89,106`) — but one dropped env var
in a future chart edit silently turns the public site into "any password works."

**Fix**: `ProductionConfig.validate()` raises if `AUTH_PROVIDER != 'oidc'` or
`DISABLE_AUTH == '1'`; refuse `auto_login_middleware` registration under ProductionConfig.
Scoping is clean: `containers/webapp/Dockerfile` does NOT set `FLASK_CONFIG` (only helm
does), so the local compose `webapp` service (7050, production image target) stays on
DevelopmentConfig and its stub click-login keeps working — only CIRRUS enforces.
Per David's recommendation, adopt the *principle* (fail-closed in prod, permissive only in
dev) and sweep his census: silent `declarative_base` fallbacks (`sam/base.py:19-40`,
`system_status/base.py:44-51`), `RATELIMIT_STORAGE_URI` memory fallback warning. Unit tests
for each rejection path.

### 2. Post-login authorization gaps — "authenticated" ≠ "authorized"  (HIGH) ✅ DONE (`cf88470`, `69cc6d3`)
*From PR #295; verified intent question Q3/Q8/Q10 pending. Internet exposure makes "any
authenticated user" a much larger, phishable population, so these graduate from polish to
launch-gating.*
- **Flask-Admin `SAMModelView` grants blanket read of all 90+ tables to any authenticated
  user** — including `ApiCredentials` (bcrypt hashes), `Role`, all charge data
  (`admin/default_model_views.py:64-78`) [P0-3, Medium effort]
- `GET /api/v1/allocations/<id>` gated only by `@login_required` — any user enumerates every
  allocation balance (`api/v1/allocations.py:32-60`) [P0-4]
- `htmx_rolling_section` has **no access check at all**; sibling threshold routes hand-roll
  authz (`dashboards/user/blueprint.py:1067-1176`) [P1-6]
- `GET /api/v1/users/search` returns username+email for any pattern, no permission gate —
  PII/user-enumeration (`api/v1/users.py:161-210`) [P1-7]
- Charges routes use system gate where siblings use project-scoped decorator
  (`api/v1/charges.py:43-46,158-161`) [P1-4]; ~9 routes hand-roll `can_manage_*` checks
  instead of decorators [P1-5]

**Fix** (per Ben's decision 2026-06-10): add a **`FLASK_ADMIN_ENABLED` config flag** —
when off, `init_admin()` is skipped entirely (no /admin blueprint registered). Default ON
in DevelopmentConfig, OFF in ProductionConfig; helm values set it explicitly. Full-CRUD
admin stays available locally (and could be VPN-gated later); the public deploy simply
doesn't mount it. This replaces the heavier per-model permission map. Separately, add
`@require_project_member_access`/`require_permission` decorators to the ungated
API/HTMX routes and thin decorators to replace hand-rolled checks (those routes stay
mounted publicly, so they need real gates).

### 3. Browser-side defenses: CSRF + security headers  (HIGH) ✅ DONE (`5a34fb9`, `52496a6`, `820cc1d`, `97bd282`)
*From this review — not in PR #295's register; ZAP passive scan didn't grade it high.*
- `CSRFProtect` is never initialized; the only flask-wtf reference in the codebase is
  `WTF_CSRF_ENABLED = False` in TestingConfig (`src/webapp/config.py:179`). No template
  renders a csrf_token. `SameSite=Lax` is the only layer protecting ~90 state-changing
  HTMX/API routes.
- Zero security headers: no HSTS, `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, CSP. Talisman was reverted (redirect loop, f6840ae/a5991aa) but
  ProxyFix is now wired, so a plain `after_request` hook is safe.

**Fix**: init `CSRFProtect(app)` in `run.py`; `<meta name="csrf-token">` + htmx
`hx-headers` snippet in `templates/dashboards/base.html` covers all HTMX calls; exempt
token-auth API blueprints (Basic auth, no cookies — safe). ~10-line `after_request` for
HSTS (prod-gated) + nosniff + XFO + Referrer-Policy. Tests: POST without token → 400;
headers asserted on responses. Related polish while in there: OIDC callback should use
the `RATELIMIT_AUTH_LOGIN` tier, not `RATELIMIT_ANON` [PR295 P2-4]; pass `id_token_hint`
on logout [P1-3].

**Vendor-asset registry** (per Ben 2026-06-10 — confirmed relevant): all CDN deps live
in exactly 2 templates, duplicated, with pinned versions but **zero SRI hashes** —
`dashboards/base.html:7-11,206-208` and `auth/login.html:7-11,128-129`. Five origins:
Bootstrap 5.3.3 (cdn.jsdelivr.net), Font Awesome 6.5.2 (cdnjs.cloudflare.com), jQuery
3.6.0 (code.jquery.com), htmx 2.0.4 (unpkg.com), Poppins (fonts.googleapis.com/gstatic).
Lift into a central registry (`src/webapp/vendor_assets.py`: name → url + integrity +
crossorigin, sensible defaults, exposed via context processor; templates render via a
shared macro). Immediate win: add SRI `integrity=` hashes in one place. Deferred CSP
then becomes *generated from the registry's origins* so header and templates can't
drift. CSP itself still post-launch (inline-script audit remains), but the registry is
its prerequisite and lands now.

### 4. Shrink the blast radius: non-root, securityContext, Redis auth, NetworkPolicy  (MEDIUM) ⏳ OUTSTANDING (Phase B)
*Root/securityContext found by both [PR295 P1-42]; Redis/NetworkPolicy/SA from this review.*
- No `USER` in `containers/webapp/Dockerfile` — gunicorn runs as UID 0; `.trivyignore`
  globally suppresses the non-root rule (AVD-DS-0002) [P2-86]
- No `securityContext` in `helm/templates/deployment.yaml` or `redis-deployment.yaml`
- Default ServiceAccount with k8s API token auto-mounted into the pod
- Redis: no `--requirepass`, no NetworkPolicy — any pod on the shared nwc1 cluster can
  reach it and zero out the Flask-Limiter counters (DB 1), neutering login brute-force
  protection

**Fix**: non-root user in Dockerfile prod stage (chown `/var/log/sam`, set `MPLCONFIGDIR`);
note both compose services bind-mount host dirs at `/var/log/sam` (`./logs/{prod,dev}`),
so the non-root UID must be able to write those — fix host-dir ownership expectations in
compose docs and add `fsGroup` in the k8s podSecurityContext for the audit-log path;
pod+container securityContext (runAsNonRoot, drop ALL caps, seccomp RuntimeDefault,
allowPrivilegeEscalation: false) for both deployments; dedicated ServiceAccount with
`automountServiceAccountToken: false`; NetworkPolicy allowing only `app=samuel` →
redis:6379 (+ default-deny on redis); `--requirepass` via secret if OpenBao plumbing is
cheap, else NetworkPolicy alone gets ~90%. Drop the AVD-DS-0002 trivyignore.

### 5. Detection & response: you will be probed — be able to see it  (MEDIUM) ⏳ OUTSTANDING (Phase B)
*PR #295's second-strongest theme ([XC: ops]); becomes launch-relevant once public.*
- **No error ingestion**: no Sentry/equivalent, no `@app.errorhandler(500)` reporting —
  a production 500 lands in pod stdout and stops there [P0-14]
- **Audit log non-durable**: written to `/var/log/sam/model_audit.log` in the container
  writable layer — lost on every redeploy; k8s has no shipping for it [P0-15]
- **Auth events not audited**: login success/failure/logout only hit `app.logger`, not the
  audit stream — exactly the events you want when internet-exposed [P1-10]
- Healthcheck failures log as routine INFO 503; no alerting [P1-55]

**Fix**: audit logger → `StreamHandler(stdout)` so the cluster log pipeline captures it
(cheapest durable option); emit audit events for login/logout/login-failure; add Sentry
SDK behind `SENTRY_DSN` env (off by default) or at minimum a loud `logger.error` path +
log-based alert for 5xx. Scope to the webapp; collector alerting (P0-7/8) can follow.

## Worth noting, not in the top 5
- **Two silent prod bugs from PR295, fix regardless**: `ProjectListSchema.get_admin_username`
  has an empty body (returns null always) [P0-5]; `ProjectSchema.get_panel` 500s on orphan
  projects [P0-6] — both Tiny
- **Supply chain** [P0-10/12, P1-45/46/47]: SHA-pin `trufflehog@main` and other actions;
  server-side protect `cirrus` branch; migrate `BENKIRK_GITHUB_TOKEN` PATs; `permissions:`
  blocks on workflows — deploy-path integrity, do this quarter
- **DB least-privilege**: verify `csg/sam-readuser` MySQL grants are actually read-only
- **Ingress flip** (`visibility: internal → external`): add `ssl-protocols: TLSv1.2 TLSv1.3`,
  `proxy-body-size`, consider edge rate-limit annotations as defense in depth
- **OIDC account linking** is username-prefix-based [P1-2] — collision risk grows with a
  broader user population; store Entra `oid`/`sub` on User (needs schema decision, Q9)
- pip-audit / lockfile / MEDIUM-severity Trivy gates [P1-44/48, PROD_IMPROVEMENTS item G]
- `baotoken` ExternalSecret SecretSyncedError on cluster (user-token store, housekeeping)
- `PRODUCTION_IMPROVEMENTS.md` is stale (items 1-9 done); after this work, fold remainder +
  this assessment into one refreshed doc and archive PR295 register cross-refs

## Implementation plan — Phase A: items 1–3, this branch, ONE PR vs staging ✅ COMPLETE

*Executed 2026-06-10 as commits `7e70dd0`..`97bd282` on `hardening` (PR #296).
Commit-by-commit summary in the STATUS section above; the original plan table
below is kept for reference. One deviation: an extra hygiene commit (`adb8419`)
fixed a stale test assertion against a renamed hpc-usage-queries column header —
pre-existing CI red, unrelated to the hardening changes.*

*Per Ben 2026-06-10: PR #295 is merged to staging and `hardening` is reset from it
(David's audit assets now live at `docs/nrit-review-2026-05/`). Items 1–3 land as a
series of commits on `hardening` → single PR → `staging`, deployable as a logical
break. Items 4 & 5 are explicitly DEFERRED to a follow-up phase.*

Everything in Phase A is pure Python/templates/config — fully developable and testable
locally (pytest + webdev:5050 + webapp:7050). The `helm/values.yaml` flag edit rides in
the PR but only takes effect at the later main→cirrus promotion.

Commit sequence on `hardening`:

| Commit | Scope | Files | Local verification |
|---|---|---|---|
| 1 | Fail-closed prod auth | `src/webapp/config.py` (ProductionConfig.validate rejects `AUTH_PROVIDER != 'oidc'` and `DISABLE_AUTH=1`), `src/webapp/utils/dev_auth.py` (refuse registration under prod config); loud-warn sweep: `sam/base.py`, `system_status/base.py` silent fallbacks | unit tests for each rejection path; `docker compose run -e FLASK_CONFIG=production webapp` (OIDC vars unset) → refuses to boot; webdev stub click-login still works |
| 2 | Security headers | `src/webapp/run.py` `after_request` (HSTS prod-gated, nosniff, XFO SAMEORIGIN, Referrer-Policy) | unit test asserting headers; `curl -sI http://127.0.0.1:7050/` |
| 3 | Vendor-asset registry + SRI | new `src/webapp/vendor_assets.py` (name → url/integrity/crossorigin, context processor), shared macro; rewire `templates/dashboards/base.html:7-11,206-208`, `auth/login.html:7-11,128-129` | browser loads all assets (SRI mismatch = hard failure, so a working page IS the test); webdev visual smoke incl. charts/icons/fonts |
| 4 | CSRF protection | `CSRFProtect(app)` in `run.py`; `<meta name="csrf-token">` + htmx `hx-headers` in base layout + login.html; exempt token-auth API blueprints | tests: HTMX/form POST w/o token → 400, with → 200; M2M status POST w/ Basic auth unaffected; full pytest; interactive member-add/remove on webdev |
| 5 | Flask-Admin kill-switch | `src/webapp/config.py` (`FLASK_ADMIN_ENABLED`: True dev / False prod), `run.py` (conditional `init_admin`), `helm/values.yaml` (`FLASK_ADMIN_ENABLED: "0"`) | flag off → /admin 404s (test via prod-config app fixture); webdev still serves /admin |
| 6 | Ungated-route authz | `api/v1/allocations.py:32-60`, `api/v1/users.py:161-210` (search), `api/v1/charges.py:43-46,158-161`, `dashboards/user/blueprint.py:1067-1176` (rolling/threshold), thin decorators replacing hand-rolled checks in `dashboards/project_members.py` + `api/v1/projects.py:502-523` | RBAC tests: non-member/non-privileged user → 403 on each formerly-open route; member access preserved |
| 7 | OIDC polish (small, same theme) | `auth/blueprint.py`: callback uses `RATELIMIT_AUTH_LOGIN` tier [PR295 P2-4]; logout passes `id_token_hint` [P1-3] | existing auth tests + rate-limit flow test |

Then: ONE PR `hardening` → `staging` referencing this assessment + PR295 IDs
(P0-1/2/3/4, P1-4/5/6/7, P2-4, P1-3). User runs full pytest by hand before the PR.

## Phase B — OUTSTANDING WORK (restart here in a fresh session)

*Separate effort after Phase A deploys. Full rationale and evidence in
considerations 4 & 5 above; PR295 IDs noted there.*

- Item 4: blast radius — non-root Dockerfile USER (+ `./logs/{prod,dev}` bind-mount
  ownership + k8s fsGroup), pod/container securityContext both deployments,
  ServiceAccount automount off, Redis NetworkPolicy (+ requirepass), drop AVD-DS-0002
  from `.trivyignore`
- Item 5: detection — audit log → stdout StreamHandler, auth events into audit stream,
  Sentry-or-loud-error path for 5xx

Also still open (from "Worth noting" above):
- Two tiny silent prod bugs: `ProjectListSchema.get_admin_username` empty body [P0-5];
  `ProjectSchema.get_panel` 500s on orphan projects [P0-6]
- Supply chain: SHA-pin actions, server-side protect `cirrus`, migrate PATs,
  workflow `permissions:` blocks [P0-10/12, P1-45/46/47]
- DB least-privilege grant check; ingress flip annotations (TLS protocols,
  proxy-body-size, edge rate-limit); OIDC account linking via Entra `oid`/`sub` [P1-2];
  pip-audit/lockfile/Trivy gates [P1-44/48]; `baotoken` ExternalSecret housekeeping
- CSP: now generatable from `webapp/vendor_assets.py` origins (Phase A landed the
  prerequisite); inline-script audit still required first
- Phase A post-deploy checks (after staging→main→cirrus promotion): header curl on
  samuel.k8s.ucar.edu, `scripts/cirrus_healthcheck.sh`, re-run David's authenticated
  ZAP script, verify OIDC Set-Cookie size (~4 KB ceiling with id_token stored)

## Verification

Local environments (compose.yaml): **webdev** at http://127.0.0.1:5050 (dev target, Flask
dev server, stub click-login — primary interactive testing) and **webapp** at
http://127.0.0.1:7050 (production image target, gunicorn, audit log bind-mounted at
`./logs/prod`) — the closest local proxy for CIRRUS behavior. Playwright available for
interactive checks against both.

All Phase A verification is local (no cluster needed):
- `pytest` full suite (user runs by hand per project convention)
- `docker compose up webdev --watch` (5050): stub login flow, HTMX mutation (member
  add/remove), charts/icons/fonts — confirms CSRF + headers + SRI don't break anything
- `webapp` (7050, gunicorn/prod image target): same smoke under multi-worker gunicorn;
  headers via `curl -sI http://127.0.0.1:7050/`
- Negative boot tests: `docker compose run -e FLASK_CONFIG=production webapp` (OIDC vars
  unset) → must refuse to start; `FLASK_CONFIG=production` + OIDC vars set but
  `DISABLE_AUTH=1` → must refuse
- Playwright available for interactive exploration of both local stacks if needed

Post-deploy (after PR merges and promotes): `curl -sI https://samuel.k8s.ucar.edu/ |
grep -iE 'strict-transport|x-frame|x-content'`; `scripts/cirrus_healthcheck.sh`; re-run
David's authenticated ZAP script (`docs/nrit-review-2026-05/run-zap-manual-explore.sh`)
to confirm header/CSRF deltas
