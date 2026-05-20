# Phase 8 — Synthesis & Punch List

> The deliverable Ben should be able to read top-to-bottom in 15-20 minutes. The per-phase docs are appendix / back-reference.
>
> **This is a running document.** The Action Register and "What's working well" sections get appended to as each phase lands; the Executive Summary, Recommendations, and Cross-cutting Themes are composed at the end.

## Executive summary

*Composed in Phase 8. Will be ~10 sentences: scope, headline take, top 3 priorities, what's solid, what's not in scope.*

## What's working well

*Running list — appended each phase. Things worth keeping/imitating, not just absence-of-defects.*

- **`CLAUDE.md` is genuine project memory, not aspirational.** Every spot-check passed in Phase 1; downstream phases trust it. (Phase 1)
- **`access_control.py` decorator family** — well-designed, consistently named, supports steward override + ancestor walk, injects `project`/`allocation` into the handler signature. The right shape for an authz primitive. (Phase 2)
- **`sam.schemas.forms` pattern.** ~75 of 105 mutation routes use it. `PUT /api/v1/allocations/<id>` (`api/v1/allocations.py:63-142`) is the textbook handler — point new contributors at it. (Phase 2)
- **Three-tier RBAC** (group bundles → user overrides → facility-scoped overrides) is documented and consistent between code and `QUICK_START_RBAC.md`. (Phase 2)
- **Auth hardening basics in place:** PKCE S256, bcrypt + timing-safe `checkpw`, `SameSite=Lax` + `HttpOnly` + `Secure` cookies, open-redirect protection. (Phase 2)
- **`audit/events.py` `before_flush` listener** covers all ORM writes app-wide with documented exclusions (`system_status` bind, `ApiCredentials`) that check out. (Phase 2)
- **429 errorhandler is content-negotiated** (JSON for `/api/`, HTMX fragment for `HX-Request`, HTML otherwise) — surfaces `Retry-After`, records to ring buffer. Solid. (Phase 2)
- **`user_aware_cache_key()` design** is right: scoped on user + facility + path + query string. Used where appropriate, skipped where genuinely shared. (Phase 2)
- **`system_status` is the strongest engineered subsystem in the audit so far.** The `before_flush` lookup-resolver, the span-coalescer for `UserProjQueueStatus`, the dual-mode `StatusBase` resolution, the `URL.render_as_string(hide_password=False)` defensive trick — these are signs of someone who's been bitten by these problems and wrote down the lessons. (Phase 3)
- **Alembic env.py** properly forces standalone `StatusBase` (`env.py:39`), uses `render_as_batch=True` for cross-dialect compatibility (SQLite tests + MySQL/Postgres prod), and lazy-imports `connection_string` so dialect/SSL dispatch isn't duplicated. (Phase 3)
- **Migration runbooks** (`PROD_BOOTSTRAP.md` and the per-revision RUNBOOKs) are exemplary — backup-first, dry-run-before-stamp, MySQL-DDL-implicitly-commits warnings, expected-table verification. The kind of runbook you wish every project had. (Phase 3)
- **Span coalescer with `MAX_SPAN_GAP` outage guard** (`user_proj_queue_ingest.py`) — turns 4,080 inserts/hour into mostly UPDATEs while explicitly not extending spans across collector outages. (Phase 3)
- **SQLite-per-worker test isolation for status tier** — portable column types let SQLAlchemy materialize the same schema cleanly; no per-worker MySQL DB dance; `DELETE FROM` on `sorted_tables` for ordered teardown. (Phase 3)

## Action register

> Every finding rolled up, severity-grouped, with effort estimate and one-line fix sketch. Cross-references to the source phase doc for the full reasoning.
>
> **Effort scale:**
> - **Tiny** — < 1 hour, mechanical
> - **Small** — a few hours, one or two files
> - **Medium** — ½–2 days, multiple files / requires thought
> - **Large** — > 2 days, architectural / cross-cutting

### P0 — Production risk (security / availability)

- **P0-1** [Phase 2 / H1] **`DISABLE_AUTH=1` is honoured in production builds.**
  Fix: hard-fail `auto_login_middleware` registration if `ProductionConfig` is active; log on startup when enabled.
  Effort: **Tiny**. → `02_web.md` §Security, `webapp/utils/dev_auth.py:31`, `run.py:285-287`

- **P0-2** [Phase 2 / H2] **`StubAuthProvider` (any-password auth) reachable in prod by default.**
  Fix: reject `AUTH_PROVIDER='stub'` in `ProductionConfig.validate()`.
  Effort: **Tiny**. → `02_web.md` §Security, `config.py:31, 119-144`

- **P0-3** [Phase 2 / H4] **Flask-Admin `SAMModelView` grants blanket read access to all 90+ tables to any authenticated user.**
  Includes `ApiCredentials` (bcrypt hashes), `Role`, all charge summaries. Confirm intent with Ben (Q3) before fixing.
  Fix: per-model permission map; tighten base `is_accessible()`.
  Effort: **Medium**. → `02_web.md` §Security, `admin/default_model_views.py:64-78`

- **P0-4** [Phase 2 / H3] **`GET /api/v1/allocations/<id>` has no permission gate beyond `@login_required`.**
  Any authenticated user can enumerate every allocation balance.
  Fix: add `require_allocation_read_access` decorator mirroring the PUT pattern.
  Effort: **Small**. → `02_web.md` §Security, `api/v1/allocations.py:32-60`

### P1 — Significant; act this quarter

#### Auth / authz hardening

- **P1-1** [Phase 2 / M1] **OIDC callback doesn't explicitly re-verify id_token.** Defensible today (Authlib validates by default), but a config drift could silently loosen it. Fix: `parse_id_token(token, nonce=...)` or log verified `iss`/`aud`. Effort: **Small**. → `auth/providers.py:122-127`
- **P1-2** [Phase 2 / M2] **OIDC account linking is username-prefix-based.** Two users with the same local-part across domains collide onto one SAM account. Fix: match on stable `oid`/`sub`, stored on `User` row (depends on Q4). Effort: **Medium**. → `auth/providers.py:136-141`
- **P1-3** [Phase 2 / M3] **OIDC logout doesn't pass `id_token_hint`.** Users see an account-picker on logout; IdP session not terminated cleanly. Fix: stash id_token at login, append on logout. Effort: **Tiny**. → `auth/blueprint.py:198-202`
- **P1-4** [Phase 2 / M4] **Charges routes use system-permission gate where sibling routes correctly use project-scoped decorator.** Fix: `@require_project_member_access(VIEW_ALLOCATIONS)`. Effort: **Tiny**. → `api/v1/charges.py:43-46, 158-161`
- **P1-5** [Phase 2 / M5] **5 routes in `dashboards/project_members.py` hand-roll `can_manage_project_members` / `can_change_admin`** instead of using a decorator. The same anti-pattern lives at `api/v1/projects.py:502-523`. Fix: thin `require_can_change_admin` / `require_can_manage_members` decorators. Effort: **Small**. → `02_web.md` §Security
- **P1-6** [Phase 2 / M6] **Three threshold HTMX routes hand-roll authz; `htmx_rolling_section` has NO access check.** Any authenticated user can read rolling-rate data for arbitrary projects. Fix: `@require_project_member_access` decorator. Effort: **Small**. → `dashboards/user/blueprint.py:1067-1176`
- **P1-7** [Phase 2 / M7] **`GET /api/v1/users/search` returns username + email for any pattern match with no permission gate.** Fix: require `VIEW_USERS` or scope to project members. Effort: **Tiny**. → `api/v1/users.py:161-210`

#### Form-validation cleanup (convention-drift)

- **P1-8** [Phase 2 / F1] **Wallclock-exemption HTMX routes — 3 handlers, full inline coercion ladder** (`datetime.strptime` + `parse_input_end_date` + `float()`/`int()` + manual range check). Largest concentration of §9 anti-pattern in the codebase. Fix: add `sam/schemas/forms/exemptions.py` (CreateWallclockExemptionForm, EditWallclockExemptionForm), refactor handlers. Effort: **Small**. (depends on Q9) → `dashboards/admin/blueprint.py:777-1105`
- **P1-9** [Phase 2 / F2 + F3 + F4] **Status-outage routes — 3 handlers + 1 API endpoint with inline `datetime.fromisoformat`, silent-swallow on bad input.** Fix: `sam/schemas/forms/outages.py` (CreateOutageForm, EditOutageForm), use across HTMX + `/api/v1/status` endpoints. Effort: **Small**. → `dashboards/status/blueprint.py:475-582`, `api/v1/status.py:324-392, 512-559`

#### Audit / ops

- **P1-10** [Phase 2 / A1] **Auth events not in `model_audit.log`.** Login/logout go through `app.logger` only. SOC pull will show writes but no "who logged in when." Fix: emit audit events on login_success / login_failure / logout / permission change. Effort: **Small**. (depends on Q6 for retention policy) → `auth/blueprint.py`, `audit/events.py`
- **P1-11** [Phase 2 / A2] **`cache.clear()` on every successful commit nukes all caches across all workers.** Defeats the purpose of caching LDAP/fairshare endpoints. Fix: targeted invalidation by sub-prefix (depends on Q7). Effort: **Medium**. → `audit/events.py:206`

#### A11y (a11y-quick-wins)

- **P1-12** [Phase 2 / A11y] **Add skip-link + `<main>` landmark to base layout.** 3-line change, biggest single keyboard-user win. Effort: **Tiny**. → `dashboards/base.html:118-123`
- **P1-13** [Phase 2 / A11y] **Wire `aria-live="polite"` + `aria-busy` toggling in `htmx-config.js`.** One handler covers all 60+ HTMX swap sites. Effort: **Small**. → `static/js/htmx-config.js`
- **P1-14** [Phase 2 / A11y] **Mechanical `scope="col"` pass on all `<th>` in `partials/*_table.html` + `fragments/*_table.html`.** ~10-15 templates; sed-able. Effort: **Tiny**. (compliance depends on Q8)

#### Status tier (Phase 3)

- **P1-15** [Phase 3 / S1] **Remove stray `print()` of `username@server/database`** at module import. Fires on every import in every context; spammy in prod logs, leaks operational info. Effort: **Tiny**. → `system_status/session/__init__.py:34`
- **P1-16** [Phase 3 / S2] **Silent fallback in `StatusBase` resolution under `FLASK_ACTIVE=1`.** When `from webapp.extensions import db` fails, falls back to standalone `declarative_base` instead of refusing — masks misconfiguration. Same fail-open pattern as the P0 auth findings. Fix: log a warning, or raise. Effort: **Tiny**. → `system_status/base.py:44-51`
- **P1-17** [Phase 3 / O1] **`cleanup_status_data.py` not visibly scheduled in-repo** — no helm CronJob, no GitHub Actions, no systemd timer checked in. Either it's externally scheduled (verify) or `system_status` grows unbounded. Effort: **Small** (add helm CronJob or document scheduler). Depends on Q14.

### P2 — Worth fixing; act eventually

#### Auth / authz polish

- **P2-1** [Phase 2 / L1] Wrap `int(user_id)` in `load_user` with try/except — tampered cookie should 401, not 500. Effort: **Tiny**. → `run.py:222`
- **P2-2** [Phase 2 / L2] `session.regenerate()` after login to close session-fixation window. Effort: **Tiny**. → `auth/blueprint.py:88, 171`
- **P2-3** [Phase 2 / L3] `AuthUser.__getattr__` masks attribute errors — surface `__getattr__` failures more clearly. Effort: **Tiny**. → `auth/models.py:116-126`
- **P2-4** [Phase 2 / L4] OIDC callback inherits `RATELIMIT_ANON` (30/min) instead of `RATELIMIT_AUTH_LOGIN` (5/min) — 6× the brute-force budget. Effort: **Tiny**. → `auth/blueprint.py:142-147`
- **P2-5** [Phase 2 / L5] Admin/HTMX projects-routes mix `@require_project_permission` and `@require_permission` inconsistently on related read/write routes. Either intentional (writes restricted to base RBAC) or oversight — comment if intentional. Effort: **Tiny**. → `dashboards/admin/projects_routes.py:1859-2080`

#### Form-validation polish

- **P2-6** [Phase 2 / F5] `PUT /api/v1/projects/<projcode>/admin` + HTMX twin — add `ChangeProjectAdminForm`. Effort: **Tiny**. → `api/v1/projects.py:502-562`, `dashboards/project_members.py:196-229`
- **P2-7** [Phase 2 / F6] `htmx_link_allocation_to_parent` — bare `int(request.form.get(...))`. Add one-field form schema. Effort: **Tiny**. → `dashboards/admin/projects_routes.py:1710-1737`
- **P2-8** [Phase 2 / F7] `htmx_save_threshold` — inline `int()` + range check. Add one-field form schema. Effort: **Tiny**. → `dashboards/user/blueprint.py:1121-1176`

#### Audit / ops polish

- **P2-9** [Phase 2 / A3] Audit log fail-open is by design; add inline `[fail-open]` comment so future readers don't tighten it accidentally. Effort: **Tiny**. → `audit/events.py:181-184`
- **P2-10** [Phase 2 / A4] Audit log lines embed `obj={repr(obj)}` with only `ApiCredentials` excluded — risk if any model's `__repr__` ever embeds a secret/PII field. Fix: switch to `pk=` only. Effort: **Tiny**. → `audit/events.py:157, 168, 178`
- **P2-11** [Phase 2 / Limiter L1] 89 HTMX mutation routes + ~14 mutating API routes rely on default `RATELIMIT_AUTHED = 200/min`. High-value targets (deletes, allocation PUT, member management) could carry an explicit lower tier. Effort: **Small** for the high-value subset.
- **P2-12** [Phase 2 / Limiter L2] `memory://` storage fallback amplifies real limits under multi-pod gunicorn. Theoretical today (single-pod), flag for HA migration. Effort: **N/A** until HA — covered by `RATELIMIT_STORAGE_URI` config; ensure helm chart sets it.
- **P2-13** [Phase 2 / C2] `SimpleCache` cold-miss per worker can thunder on first hit to expensive `*_access.py` endpoints. Acceptable if cold-start is rare; production wants Redis for both `webapp.caching` *and* the `cachetools` layer in `sam/queries/usage_cache.py`. Effort: **N/A** — config concern.
- **P2-14** [Phase 2 / C3] `usage_cache.py:81` reads `CACHE_REDIS_URL` from `os.environ` directly, bypassing `app.config` and the reachability check. Could report different backends after Redis flap. Effort: **Tiny**.

#### A11y polish

- **P2-15** [Phase 2 / A11y] Form-error association — `aria-invalid` + `aria-describedby` in `form_fields.html` macros. Effort: **Small**. → `templates/dashboards/fragments/form_fields.html:329-337`
- **P2-16** [Phase 2 / A11y] `aria-sort` on sortable table headers. Effort: **Tiny**. → `partials/project_table.html:7-23`
- **P2-17** [Phase 2 / A11y] `aria-current="page"` on active nav. Effort: **Tiny**. → `dashboards/base.html:30-54`
- **P2-18** [Phase 2 / A11y] Row-click `<tr>` navigation is keyboard-inaccessible. Either wrap content in `<a>` (per-cell) or `tabindex="0"` + keydown. Effort: **Small**. → `partials/queue_table.html:36`
- **P2-19** [Phase 2 / A11y] `<span class="sr-only">` is Bootstrap 4; BS5 renamed to `visually-hidden`. Verify shim or migrate. Effort: **Tiny**. → `loading_spinner.html:5`, `shared/project_details_modal.html:14`
- **P2-20** [Phase 2 / A11y] `aria-valuenow/min/max` on progress bars. Effort: **Tiny**. → `shared/usage_bar.html:49-62`
- **P2-21** [Phase 2 / A11y] Icon-only buttons rely on `title=`; add `aria-label`. Effort: **Small** (mechanical across handful of templates). → `members_table.html:67-87, 91-111`
- **P2-22** [Phase 2 / A11y] Decorative FA icons next to text mostly lack `aria-hidden="true"`. Low practical impact, mechanical fix. Effort: **Tiny**.

#### Status tier polish (Phase 3)

- **P2-34** [Phase 3 / S3] **`schemas/status.py:24` uses `from system_status import *`** which pulls `main` (the CLI entry point) into schema namespace. Replace with explicit imports. Effort: **Tiny**. → `system_status/schemas/status.py:24`
- **P2-35** [Phase 3 / S4] **`cli.py:22-23` does module-level `sys.path.insert`.** Works around proper packaging; works fine in practice but bites when installed vs. run-from-source. Effort: **Tiny**.
- **P2-36** [Phase 3 / S5] **`cli.py:62` hardcodes system choices `['derecho', 'casper', 'jupyterhub']`.** Drifts when new systems are added. Optional: derive from `System` lookup table at parser-build time. Effort: **Small** (or document-only).
- **P2-37** [Phase 3 / O2] **Cleanup script doesn't reap lookup tables** (`UserDef`, `ProjectCodeDef`, etc.). In practice these are bounded by the user/project/queue catalog and won't blow up, but a one-off bad ingest leaves orphans forever. Effort: **Small**.
- **P2-38** [Phase 3 / O3] **Document the monotonic-`T_new` assumption in the span coalescer.** Out-of-order or backfill ingests aren't currently a concern (collectors push monotonically every 5 minutes), but the assumption is implicit. Effort: **Tiny** (comment-only).

#### Docs hygiene (deferred to Phase 7 for final dispositions)

- **P2-23** [Phase 1] **CONTRIBUTING.md test stats stale** (380+ claimed, 1,750 actual). Re-run `pytest --cov`, refresh. (Depends on Q1.) Effort: **Tiny**.
- **P2-24** [Phase 1] **`README.md` test count internally inconsistent** (claims both ~1,400 and 380+ in different sections). Effort: **Tiny**.
- **P2-25** [Phase 1] **`README.md` API section omits ~7 endpoint modules** (charges, directory_access, fstree_access, health, project_access, status, allocations). Effort: **Small**.
- **P2-26** [Phase 1] **`src/webapp/IMPLEMENTATION_SUMMARY.md`** — delete (net-misleading mid-sprint scaffolding). Effort: **Tiny**.
- **P2-27** [Phase 1] **`src/webapp/DESIGN.md`** — archive to `docs/archive/` (evergreen rationale worth keeping; stale architecture not). Effort: **Tiny**.
- **P2-28** [Phase 1] **`src/webapp/QUICK_START_RBAC.md`** — promote to `docs/TESTING_RBAC.md`. Effort: **Tiny**.
- **P2-29** [Phase 1] **`src/webapp/REFACTORING_PLAN.md`** — verify done-ness of each item in later phases, then move surviving items to a single backlog doc. Effort: **Small**.
- **P2-30** [Phase 1] **`src/webapp/README.md`** — trim API section (now stale); keep quick-start. Effort: **Tiny**.
- **P2-31** [Phase 1] **`webapp/extensions.py` `cache = caching.flask` alias** is marked "DEPRECATED" with 40+ live call sites. Either finish the migration or drop the deprecation notice. Effort: **Small**.
- **P2-32** [Phase 1] **Setup-doc cluster overlap** — `SETUP_SUMMARY.md` + `LOCAL_SETUP.md` + `CREDENTIALS.md` + `DATABASE_SWITCHING.md`. Pick a canonical doc; redirect the others. Effort: **Small**.
- **P2-33** [Phase 1] **k8s-doc cluster overlap** — `k8s.md` + `README-k8s.md` + `CIRRUS-k8s-cmds.sh` (the `.sh` file in `docs/` is an odd home). Pick canonical, redirect. Effort: **Small**.

## Cross-cutting themes

> Rolled up from `[XC: …]` tags across phases — composed at end of audit.

### `[XC: prod-config-hardening]`
*Synthesis pending.* Currently: 4 env-misconfig footguns gating on optional flags (DISABLE_AUTH, AUTH_PROVIDER=stub, RATELIMIT_STORAGE_URI, `StatusBase` Flask-fallback). Same theme each time: when the "expected" env doesn't hold, code falls back silently instead of refusing. Deserves a single principled stance: fail-closed unless explicitly enabled.

### `[XC: convention-drift]`
*Synthesis pending.* Currently: hand-rolled authz in ~9 routes (where canonical decorators exist), inline coercion in ~10 mutation routes (where `sam.schemas.forms` is the documented pattern), stray `print()` of connection info at status-package import time. Pattern is right; coverage is incomplete.

### `[XC: docs-drift]`
*Synthesis pending.* Currently: stale stats in `CONTRIBUTING.md` + `README.md`, stale API section in `src/webapp/README.md`, AI-collab residue in `src/webapp/{DESIGN,IMPLEMENTATION_SUMMARY,REFACTORING_PLAN}.md`, overlap clusters in `docs/` setup + k8s docs.

### `[XC: a11y]`
*Synthesis pending.* Currently: systemic table + HTMX-swap + landmark gaps; forms/modals okay. Quick-win bundle (P1-12, P1-13, P1-14) gets the most ROI.

### `[XC: ops]`
*Synthesis pending.* Currently: audit log file-local with no off-host shipping; global cache invalidation on every commit; remediation logs checked into repo; HA limiter/caching story not fully worked out; `cleanup_status_data.py` not visibly scheduled in-repo.

### `[XC: testing]`
*Synthesis pending.* So far: test infrastructure is generally strong (Phase 1's ~1,750-test claim, Phase 3's SQLite-per-worker isolation pattern). Two import-order traps documented but mitigated: `FLASK_ACTIVE=1` must land before any `system_status.*` import, and the `app` fixture is session-scoped (one per xdist worker). No findings yet — but coverage of the routes flagged in Phase 2 (hand-rolled authz, inline coercion) wasn't measured.

### `[XC: perf]`
*Pending Phase 4+.*

### `[XC: secrets]`
*Pending Phase 6.*

### `[XC: deploy]`
*Pending Phase 6.*

## Recommendations (sequencing)

*Composed at end. A suggested order of attack so Ben isn't staring at a flat punch list. Likely shape: (a) the P0 batch as one focused PR — they're all small, security-critical, low risk; (b) a convention-drift PR rolling up the hand-rolled-authz + inline-coercion findings; (c) the a11y quick-win bundle; (d) docs-hygiene pass; (e) the more architectural items (OIDC linking, cache-invalidation strategy, audit shipping).*

## Open questions for Ben

> Consolidated from `project_audit_questions_for_ben` memory and per-phase docs. Answers from Ben unblock several P1 items above (noted as "depends on Q#" in the register).

### From Phase 1 (orientation & doc-drift)
1. CONTRIBUTING.md test stats — intentional pin or stale?
2. Remediation log home — Jira/Confluence/Wiki, or keep in-repo?
3. `src/webapp/REFACTORING_PLAN.md` charges-API centralization — scheduled, backlog, or shelved?
4. `docs/plans/POSTGRES_MIGRATION.md` — planned, paused, shelved?
5. `docs/prompts/` — intentional artifact or residue?

### From Phase 2 (web)
6. `DISABLE_AUTH=1` ever expected in prod (incident debugging), or hard-fail?
7. `stub` provider ever acceptable in prod, or OIDC-only?
8. Flask-Admin SAMModelView "any authenticated user reads everything" — intentional or pending?
9. Entra `oid`/`sub` claims — willing to store on `User` row to replace prefix-matching?
10. `GET /api/v1/allocations/<id>` access policy — same as project list (member-or-`VIEW_ALLOCATIONS`)?
11. Audit log shipped off-host, or rotated locally only?
12. Global `cache.clear()` on commit — intentional, or open to staleness for warmer caches?
13. UCAR/NCAR a11y compliance posture — Section 508 / WCAG 2.1 AA?
14. Wallclock-exemption refactor — appetite for a focused PR?

### From Phase 3 (status)
15. Is `cleanup_status_data.py` actually running in production? If yes, where's the scheduler (helm CronJob, OS cron, GitHub Actions, …)? If no, what's keeping `system_status` from growing unbounded?
16. `csg-postgres.k8s.ucar.edu` — is that the canonical prod `system_status` host, or is MySQL the prod target and Postgres a parallel deployment? Affects how much the dual-driver code paths actually get exercised.
17. Out-of-order or backfill ingests — ever expected? The span coalescer assumes monotonic `T_new`; worth documenting either way.
18. The stray `print()` in `system_status/session/__init__.py:34` — intentional debug breadcrumb or leftover?

## Reviewer notes

*Composed at end. Caveats about scope (1-2 day directional), depth (no exhaustive testing, no formal threat model, no perf profiling), what we didn't get to (e.g. notebooks, full Flask-Admin model-view audit beyond the headline finding).*
