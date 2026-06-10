# Phase 2 — Web (`src/webapp/`)

> Largest and most user-facing subsystem. Flask + Jinja2 + HTMX + Flask-Admin, OIDC (Entra), RBAC, API, audit log, rate limiter, caching. Highest blast radius for a directional review.

## Scope

- `src/webapp/` (all subtrees: `admin/`, `api/`, `audit/`, `auth/`, `caching/`, `dashboards/`, `limiter/`, `templates/`, `static/`, `utils/`)
- `src/webapp/config.py`, `extensions.py`, `logging_config.py`, `run.py`
- In-tree design docs: dispositions held for Phase 7 per Phase 1 register
- Tests: `tests/api/`, webapp-related entries in `tests/unit/` and `tests/integration/` — not deep-dived this phase

## Method

Mapped the request lifecycle (middleware → blueprints → decorators → schemas → ORM → response) from `run.py`, then ran five parallel deep-dives:

1. **Auth** — OIDC sign-in path, dev-auto-login, API-key auth, session security, Flask-Login wiring
2. **RBAC** — every project-scoped route audited for canonical decorator usage (`@require_project_access`, `@require_project_member_access`)
3. **Form validation** — every POST/PUT/PATCH/DELETE handler audited for `sam.schemas.forms` compliance
4. **Audit log / rate limiter / caching** — coverage, invalidation, fail modes, key strategies
5. **Template a11y** — strategic spot-check across the 167-template tree (Bootstrap 5 + HTMX 2 stack)

Findings below distill the deep-dives. Phase 1's verdict on `CLAUDE.md` (high trust) held — the documented conventions are real, and most divergences are localized.

## Lenses applied

- Architecture
- Security (primary focus)
- Testing (light — relies on Phase 1's `tests/` survey)
- Performance
- UX / A11y
- Operability

---

## Findings

### Headline

The webapp's **conventions are well-designed and mostly well-followed**. The `access_control.py` decorator family, the `sam.schemas.forms` pattern, the audit-via-`before_flush` mechanism, and the user-aware cache key are all thoughtful pieces of infrastructure. Where things drift, it's usually one of two patterns: **(a) routes that predate the convention and were never migrated** (wallclock exemptions, status outages, some HTMX threshold routes) or **(b) production-mode guards that aren't quite tight enough** (`DISABLE_AUTH=1`, `stub` provider, Flask-Admin's blanket read access).

Two security findings rise to the level of "fix in the next sprint" rather than "interesting to look at": **auto-login can be enabled in production via env var**, and **the `stub` provider (any-password auth) is not blocked in `ProductionConfig.validate()`**. Both are env-misconfig footguns rather than active vulnerabilities, but the blast radius is total admin compromise.

### Architecture

The factory in `src/webapp/run.py` is clean: extensions → middleware → blueprints → routes in a single linear pass, with `config_overrides` plumbing for the test harness. ProxyFix is on, request-ID tagging is on, health-probe noise is suppressed. The blueprint topology (auth + 5 dashboards + 9 API v1 modules) matches the system map from Phase 1.

One mild smell: `webapp/extensions.py` retains a `cache = caching.flask` alias with a "DEPRECATED" comment and a 40+ call-site footprint. Cross-cutting tag below.

### Security

**High**

- **H1. `DISABLE_AUTH=1` is honoured in production builds.** `webapp/utils/dev_auth.py:31` + `run.py:285-287` — `auto_login_middleware` registers on every config (Dev/Prod/Test), gated solely on the env var. No `FLASK_CONFIG` / `app.debug` check. An accidentally-set env var on a prod host yields silent unauthenticated login as `DEV_AUTO_LOGIN_USER` (defaults to `benkirk`, a real admin per `DEV_QUICK_LOGIN_USERS`). *Fix:* hard-fail registration when `ProductionConfig` is active.
- **H2. `StubAuthProvider` reachable in production by default.** `config.py:31` defaults `AUTH_PROVIDER='stub'`; `ProductionConfig.validate()` (`config.py:119-144`) only enforces OIDC env vars *when* `AUTH_PROVIDER=='oidc'`. `auth/providers.py:63-72` accepts any non-empty password for any active SAM user. *Fix:* reject `stub` in `ProductionConfig.validate()`.
- **H3. `GET /api/v1/allocations/<int:allocation_id>` is `@login_required` only — no permission/membership gate.** `api/v1/allocations.py:32-60`. PUT sibling (`:65`) correctly uses `@require_allocation_permission(EDIT_ALLOCATIONS)`. Any authenticated user can enumerate all allocations. *Fix:* a `require_allocation_read_access` decorator mirroring the PUT pattern.
- **H4. Flask-Admin `SAMModelView.is_accessible()` returns just `current_user.is_authenticated`.** `admin/default_model_views.py:64-78`. Every model — including `ApiCredentials` (bcrypt hashes), `Role`, charge summaries — is browsable by any logged-in user via `/database/<endpoint>/`. `can_edit`/`can_create` then gate on `EDIT_USERS`/`CREATE_USERS` regardless of model, so an `EDIT_USERS` holder can edit Allocations, Resources, etc. *Fix:* per-model permission map; tighten the base `is_accessible()`. **Verify with Ben whether anonymous-authenticated browsing of `ApiCredentials` is intentional.**

**Medium**

- **M1. OIDC callback trusts `token['userinfo']` without explicit id_token verification** (`auth/providers.py:122-127`). Authlib does validate when `server_metadata_url` is set (it is — `run.py:196`), so this is defensible today. A future Authlib upgrade or config change could silently loosen it. *Fix:* explicit `parse_id_token(token, nonce=...)` or log verified `iss`/`aud` on success.
- **M2. OIDC account-linking is username-prefix-based and fragile** (`auth/providers.py:136-141`). Strips `@domain` from `preferred_username` / `email`. Collides if two users share a local-part across domains. *Fix:* match on stable `oid`/`sub` claim, stored on `User` row; keep prefix-match as a one-time migration fallback.
- **M3. OIDC logout doesn't pass `id_token_hint`** (`auth/blueprint.py:198-202`). End-session URL has only `post_logout_redirect_uri`. Most IdPs (Entra included) require `id_token_hint` to silently terminate the IdP session; without it the user sees an account-picker and the SAM session is killed but the SSO session is not.
- **M4. `GET /api/v1/projects/<projcode>/charges` and `/charges/summary` use system-permission gate on project-scoped routes** (`api/v1/charges.py:43-46, 158-161`). Gated on `@require_permission(VIEW_ALLOCATIONS)` only, not `@require_project_member_access`. Sibling `/allocations` route (`projects.py:254-256`) correctly uses the member decorator — inconsistent.
- **M5. `PUT /api/v1/projects/<projcode>/admin` hand-rolls the access check** (`api/v1/projects.py:502-523`); calls `can_change_admin(current_user, project)` inline rather than via decorator. Same anti-pattern repeats across 5 routes in `dashboards/project_members.py:36-229`.
- **M6. Three `dashboards/user/blueprint.py` threshold routes hand-roll authz** (lines 1067-1176). `htmx_rolling_section` GET has *no* access check beyond `@login_required` — any authenticated user can see rolling-rate data and current thresholds for arbitrary projects.
- **M7. `/api/v1/users/search` is auth-only, no permission gate** (`api/v1/users.py:161-210`). Returns username + display_name + email for any pattern match. Sibling `GET /api/v1/users/<username>` correctly requires `VIEW_USERS`.

**Low / Informational**

- **L1.** `load_user` raises on non-integer `user_id` cookie value (`run.py:222`) — 500 instead of "log in again" on a tampered cookie.
- **L2.** No `session.regenerate()` / `session.clear()` after login (`auth/blueprint.py:88, 171`) — small session-fixation window.
- **L3.** `AuthUser.__getattr__` proxies all unknown attributes (`auth/models.py:116-126`); SAM model property exceptions surface with confusing context.
- **L4.** `RATELIMIT_AUTH_LOGIN` (5/min) only decorates `/login`. `oidc_callback` / `oidc_login` inherit `RATELIMIT_ANON` (30/min) — 6× the brute-force budget of the password form.
- **L5.** Admin/HTMX projects-routes mix `@require_project_permission` and `@require_permission` inconsistently across read/write routes (`dashboards/admin/projects_routes.py:1859-2080`).

**Strengths**

- PKCE S256 enforced; bcrypt + `checkpw` for API keys; open-redirect protection in `_is_safe_redirect`; `SameSite=Lax` + `HttpOnly` + `Secure` (prod) on session cookies; API-key and Flask-Login paths mutually exclusive in `login_or_token_required`.
- The `access_control.py` decorator family is genuinely well-designed (factory-vs-bare polymorphism, steward override + ancestor walk, `project`/`allocation` injection into handler signature).
- `_is_project_steward` (`webapp/utils/project_permissions.py:20-75`) is a clean single-source-of-truth.
- Three-tier RBAC (group bundles → user overrides → facility-scoped overrides) is documented inline and consistent between `rbac.py` and `QUICK_START_RBAC.md`.

### Testing

Not deep-dived in this phase. Phase 1 already established that the documented `~1,400 tests / parallel ~65s` claim is approximately right (actual ~1,750). `tests/api/` exists; spot-coverage of the routes flagged above wasn't measured. Cross-cutting tag below for the synthesis pass.

### Performance & Operability — Audit, Rate Limiter, Caching

**Audit logging**

- **A1 [Med].** Auth and permission events are *not* in `/var/log/sam/model_audit.log`. `auth/blueprint.py` and `auth/providers.py` route login/logout through `app.logger`, not the audit logger. A SOC pull will show model writes but no "who logged in when" trail.
- **A2 [Med].** `cache.clear()` on every successful commit (`audit/events.py:206`) is a thundering-herd risk: every POST nukes all dashboard + LDAP-feed caches across all workers. Defeats the purpose of caching the expensive `*_access.py` endpoints. *Consider:* targeted invalidation by sub-prefix instead of global.
- **A3 [Low].** Audit log failure mode is silent — falls back to `/tmp/sam_audit.log` with a stdout warning (`audit/logger.py:35-40`). Inside `before_flush`, audit fails open by design (`events.py:181-184`) — right call for availability, worth an inline `[fail-open]` comment.
- **A4 [Low].** Audit log lines embed `obj={repr(obj)}` (`events.py:157,168,178`) with no allow-list. `ApiCredentials` is excluded but no other model is. If any model's `__repr__` ever embeds a secret/PII field, it lands in the log. *Consider:* `pk=` only.

**Rate limiter**

- **L1 [Med].** Coverage is implicit, not explicit, on **89 HTMX mutation routes** + ~14 mutating API routes. Protected only by the default `RATELIMIT_AUTHED = 200/min`. High-value targets (delete routes, allocation PUT, member-management routes) could carry an explicit lower tier.
- **L2 [High in HA, low today].** `memory://` storage fallback under multi-pod gunicorn amplifies real limits: `5/min` × `4 workers` × `N pods` = effective `20N/min` per attacker. Currently single-pod, so theoretical — flag for the helm/HA migration.
- **L3 [Low].** 429 errorhandler is well-built — content-negotiated (JSON for `/api/`, HTMX fragment for `HX-Request`, HTML otherwise), surfaces `Retry-After`, records to event ring. Nothing to fix.

**Caching**

- **C1 [Low].** `user_aware_cache_key()` coverage looks correct. 21 total `@cache.cached`/`@cache.memoize` call sites; 4 use the user-aware key (the ones whose response embeds the navbar / user scope), 17 use `query_string=True` (LDAP/fairshare snapshots, genuinely not user-scoped). No false negatives spotted.
- **C2 [Med].** `SimpleCache` (the no-Redis fallback) is per-worker. Each gunicorn worker pays cold-miss tax independently on first request to `get_fstree_data()` / fairshare tree. Acceptable if cold-start is rare; production wants Redis for both `webapp.caching` *and* the parallel `cachetools` layer in `sam/queries/usage_cache.py`.
- **C3 [Low].** `usage_cache.py:81` reads `CACHE_REDIS_URL` from `os.environ` directly, bypassing `app.config` and the resolved-reachability check that `webapp.caching` does. Could report different backends after a Redis flap.
- **C4 [Info].** Three explicit invalidation paths: audit-driven `cache.clear()` (A2), `refresh_cache` endpoints, manual `purge_usage_cache()`. Combined with A2, effective TTL of user-scoped dashboard caches is "until next write anywhere" — the 300s default rarely matters.

### Form validation

105 mutation routes total across `api/v1/` (9 files) and `dashboards/` (17 files). Rough compliance breakdown:

- **~75 use a `sam.schemas.forms` schema** (correct pattern)
- **~8 use a Marshmallow-SQLAlchemy schema** (acceptable alt., concentrated in `status.py` ingest paths)
- **~12 are trivial POST/DELETE with no body to validate**
- **~10 have inline coercion smells / no schema** — findings below

**High**

- **F1. Wallclock-exemption routes — full inline coercion ladder.** `dashboards/admin/blueprint.py:777-884` (`htmx_add_exemption`), `:911-1030` (`htmx_admin_exemption_create`), `:1033-1105` (`htmx_edit_exemption`). All three hand-roll `datetime.strptime` + `parse_input_end_date` + `float()`/`int()` + manual range check — exactly the §9 anti-pattern. Largest concentration in the codebase. *Fix:* add `sam/schemas/forms/exemptions.py` (`CreateWallclockExemptionForm`, `EditWallclockExemptionForm`), refactor the three handlers.
- **F2. Status-dashboard outage HTMX routes — silent-swallow inline parsing.** `dashboards/status/blueprint.py:475-531`, `:534-582`. `datetime.fromisoformat(...)` inside `try/except ValueError: pass` — bad input silently dropped with no user feedback. *Fix:* `CreateOutageForm` / `EditOutageForm`.
- **F3. `PATCH /api/v1/status/outages/<id>` — no schema, partial pattern hand-rolled** (`api/v1/status.py:512-559`). Should reuse the form from F2 with `partial=True`.

**Medium**

- **F4.** `POST /api/v1/status/outage` (`api/v1/status.py:324-392`) — required-field check via list comprehension; two `datetime.fromisoformat` blocks. Should reuse `CreateOutageForm`.
- **F5.** `PUT /api/v1/projects/<projcode>/admin` + HTMX twin (`api/v1/projects.py:502-562`, `dashboards/project_members.py:196-229`) — no schema for `admin_username`. Trivial today but empty-string-to-clear semantics duplicated.
- **F6.** `htmx_link_allocation_to_parent` (`dashboards/admin/projects_routes.py:1710-1737`) — bare `int(request.form.get(...))`. One field, but exact pattern §9 prohibits.
- **F7.** `htmx_save_threshold` (`dashboards/user/blueprint.py:1121-1176`) — inline `int()` + range check. Trivial form schema would clean this up.

**Strengths**

- `PUT /api/v1/allocations/<id>` (`api/v1/allocations.py:63-142`) is **textbook**: `partial=True`, `updates` dict gated on original `data` keys, `flatten_errors` for errors, audit-logged write via `management_transaction`. This is the model to point new contributors at.
- `htmx_create_adjustment` (`dashboards/allocations/blueprint.py:1037+`) and the project allocation create/edit/exchange/renew/extend routes (`projects_routes.py:713, 939, 1177, 1413, 1590`) consistently use domain schema + `flatten_errors`.

### UX / A11y

167 templates spot-checked across all families. Stack is Bootstrap 5 + Font Awesome 5 + HTMX 2.

| Area | Grade | Note |
|---|---|---|
| Base layout | C | `lang`, viewport, `<nav>`, `<footer>` present. **Missing `<main>` landmark, skip-to-content link, `aria-current` on nav.** |
| Forms | B− | `form_fields.html` macros wire `<label for=>` + `required` consistently. No `aria-describedby` / `aria-invalid` linking errors to fields. |
| Tables | **D** | Zero `<th scope="col">`, zero `<caption>` across all 167 templates. Sortable headers convey state visually only (no `aria-sort`). Row-click via `onclick` on `<tr>` is keyboard-inaccessible. |
| Modals | B+ | Bootstrap 5 defaults give `aria-labelledby`, `aria-hidden`, ESC/focus trap. Solid. |
| HTMX swaps | **D** | Zero `aria-live` / `aria-busy` on any swap container. Only `htmxErrorToast` (`base.html:154`) is announced. |
| Icons | C | ~12 of 167 templates set `aria-hidden`; icon-only buttons rely on `title=` (not reliably announced). |
| Color-only signaling | C | Some `text-warning`/`text-danger` spans with no icon/text differentiator. Status badges (`badges.html`) pair color + text — those are fine. |

**Quick wins (high ROI)**

1. Add skip link + `<main>` landmark to `dashboards/base.html` (~3 lines, biggest keyboard-user win).
2. Wire `aria-live="polite"` + `aria-busy` toggling via `htmx:beforeRequest`/`afterSettle` in `htmx-config.js` — one handler covers every HTMX swap site-wide.
3. Mechanical sed pass: add `scope="col"` to all `<th>` in `partials/*_table.html` + `fragments/*_table.html` (~10-15 templates).

**Higher-effort**

4. Per-field error association in `form_fields.html` macros (`aria-invalid` + `aria-describedby`).
5. `aria-sort` on sortable column headers; `aria-current="page"` on active nav links.
6. Row-click `<tr>` navigation → per-cell `<a>` or `tabindex="0"` + keydown.
7. `<span class="sr-only">` → `visually-hidden` (Bootstrap 4→5 class rename); verify no silently-dropped loading text.
8. `aria-valuenow/min/max` on progress bars (`shared/usage_bar.html:49-62`).

---

## Cross-cutting tags raised

- `[XC: prod-config-hardening]` — Multiple env-var-misconfig footguns gate on optional flags rather than fail-closed in `ProductionConfig.validate()`. `DISABLE_AUTH` (H1), `AUTH_PROVIDER=stub` (H2), `RATELIMIT_STORAGE_URI` fallback (Limiter L2). Phase 6 territory but flagged here because the auth two are security-critical.
- `[XC: docs-drift]` — `webapp/extensions.py:14-17` keeps `cache = caching.flask` with a "DEPRECATED" comment + 40+ live call sites. Either complete the migration or drop the deprecation notice.
- `[XC: convention-drift]` — Hand-rolled access checks in `dashboards/project_members.py` (5 routes), `dashboards/user/blueprint.py` threshold routes (3 routes), `api/v1/projects.py:502` admin route. Pattern is right, decorator coverage is incomplete.
- `[XC: convention-drift]` — Form-validation §9 gaps cluster in wallclock-exemption (3 routes) and status-outage (3 routes) — both look like "predate the schema convention" rather than active rejection.
- `[XC: a11y]` — Tables and HTMX swap containers are systemic gaps. The base layout missing `<main>` and skip-link is a 3-line fix that unblocks keyboard navigation for the whole app.
- `[XC: ops]` — Audit log is file-local. No off-host shipping; rotation caps real history at a few weeks. Auth events not captured.
- `[XC: ops]` — Cache invalidation on every commit (`audit/events.py:206`) wipes the LDAP-feed caches that exist precisely *because* those queries are expensive.

## Open questions for Ben

1. **Is `DISABLE_AUTH=1` ever expected on a `FLASK_CONFIG=production` host** (incident debugging, etc.), or can we hard-fail it? Drives whether H1's fix is "refuse to register" or "log loudly + refuse if config is prod."
2. **Is the `stub` provider intended to ever be reachable from prod** (even behind VPN), or is OIDC the only acceptable prod auth path? Drives H2 validation wording.
3. **Flask-Admin `SAMModelView` — is "any authenticated user reads everything" intentional** for an internal-only `/database/` UI behind SSO, or has per-model gating not been wired yet? `ApiCredentials` browsability is the sharpest edge.
4. **For OIDC account linking — does Entra emit `oid`/`sub` claims** you'd be willing to store on the SAM `User` row? That'd replace the prefix-match in `resolve_user_from_claims` with a stable handle.
5. **`GET /api/v1/allocations/<id>` access policy** — same as the project allocations list (member-or-`VIEW_ALLOCATIONS`)? Easy fix once the policy is named.
6. **Audit retention/shipping** — is `/var/log/sam/model_audit.log` shipped off-host (rsyslog/Filebeat/Vector), or only rotated locally? 50MB cap on local rotation means real history is a few weeks at typical write volumes.
7. **Is the global `cache.clear()` on every commit intentional** (correctness-over-cost), or would you accept staleness in exchange for keeping LDAP-feed caches warm across writes? A per-blueprint cache + targeted invalidation would unblock this.
8. **UCAR/NCAR a11y compliance posture** — is SAM web subject to Section 508 or WCAG 2.1 AA? Internal NCAR tools historically aren't audited, but if SAM is consumed by university PIs (UNIV facility) it likely should be. Drives whether a11y findings are "nice-to-haves" or compliance gaps.
9. **Wallclock-exemption refactor** — appetite for a focused PR adding `sam/schemas/forms/exemptions.py` and migrating the three handlers off the inline coercion ladder?
