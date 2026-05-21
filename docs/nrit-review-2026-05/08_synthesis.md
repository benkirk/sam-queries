# Phase 8 — Synthesis & Punch List

> The deliverable Ben should be able to read top-to-bottom in 15-20 minutes. The per-phase docs are appendix / back-reference.
>
> **This is a running document.** The Action Register and "What's working well" sections get appended to as each phase lands; the Executive Summary, Recommendations, and Cross-cutting Themes are composed at the end.

## Executive summary

A friendly external review of `sam-queries` at Ben's request: ~3 days of directional reading across `src/webapp/`, `src/system_status/`, `src/sam/`, `src/cli/`, `collectors/`, the platform layer (CI/CD, deploy, secrets, supply chain, observability), and the documentation tree. The MySQL schema itself was out of scope; the ORM follows it by design. Findings are calibrated to "what's most worth knowing," not "everything I noticed."

**The architecture is competent and the conventions are excellent.** `CLAUDE.md` is genuine project memory rather than aspirational documentation — every spot-check held up. The ORM layer (universal `is_active` hybrid, write-op co-location, two-tier test strategy) and the `system_status/` subsystem (lookup-resolver `before_flush`, span coalescer with outage guard, exemplary Alembic runbooks) are the strongest engineered surfaces I encountered. The Web team's authz primitives (`access_control.py` decorator family, three-tier RBAC), schema family (`HtmxFormSchema`, `AllocationWithUsageSchema`), and CI/CD posture (TruffleHog gating, concurrency groups, asymmetric skip-CI) all reflect a team that's been bitten by problems and wrote down the lessons. 54 distinct strengths called out across the audit.

**Where things drift, two patterns dominate.** First and most consequential: **the project is configured to fail open at the boundaries where ops would notice.** Fourteen separate footguns gate on optional env vars or fall back silently when an expected condition isn't met — `DISABLE_AUTH=1` honored in production, `AUTH_PROVIDER='stub'` allowed in `ProductionConfig`, Helm Deployment with no probes despite well-designed health endpoints, collector exception handler that substitutes all-zeros for node counts, staging RDS `publicly_accessible=true`, `verify=False` on the JupyterHub API, `FROM python:3` unpinned, …. Same shape every time. **This is the single most actionable observation of the audit and worth a principled "fail-closed unless explicitly enabled" stance.** Second: convention drift inside the very modules that define the conventions — 10+ raw `Model.active == True` comparisons inside `Project` and `User` themselves, hand-rolled access checks in ~9 routes where canonical decorators exist, inline date coercion in ~10 mutation routes despite the form-schema pattern being documented.

**Two concrete production bugs surfaced** that warrant immediate attention regardless of everything else: `ProjectListSchema.get_admin_username` has an empty function body and silently returns `null` for every project in every list response, and `ProjectSchema.get_panel` raises 500 on orphan projects (the documented `allocation_type=None` case). Both Tiny fixes; both possibly already shipping.

**The operational posture is the biggest gap.** No error ingestion (no Sentry); audit log written to ephemeral container path with no off-host shipping; no metrics endpoint or backend; healthcheck failures invisible; collector failures invisible. The system is observable only via log-grep against a partly-ephemeral destination. Combined with three independent single-points-of-failure on `benkirk` (Glade cron path, personal PAT in 3 maintenance workflows, `ghcr.io/benkirk/sam-queries` image), recovery from a real incident would be much harder than it should be given the quality of the underlying code.

**182 register items (15 P0 / 55 P1 / 112 P2) resolve to roughly one week of focused work**, best organized as 5 bundled PRs (sequencing below). The audit found nothing structurally wrong; the findings are mostly composable, well-scoped fixes that don't require architectural change. The headline take is upbeat with one significant operational reservation.

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
- **Universal `is_active` hybrid + 5 mixins** (`TimestampMixin`, `SoftDeleteMixin`, `ActiveFlagMixin`, `DateRangeMixin`, `SessionMixin`). Clean single-responsibility design; `normalize_end_date` centralized; works in both Python and SQL. (Phase 4)
- **`update()` instance method + `create()` classmethod co-location is real** — zero standalone `update_*(session, id, ...)` / `create_*(session, ...)` helpers remain in `src/sam/`. The CLAUDE.md §7 migration actually happened. (Phase 4)
- **`fmt.py` matches the documented API exactly** — 4 filters, `SAM_RAW_OUTPUT` honored, naive-UTC datetime triad, None→'—' default. (Phase 4)
- **Two-tier test strategy is genuinely clean** — Layer 1 (representative fixtures from snapshot) vs Layer 2 (factory builders auto-building FK graph). No helper blurs the layers; tests compose both freely. ~1,500+ tests. (Phase 4)
- **Schema-drift tests promoted to assertive** for FK existence + UNIQUE constraints (`test_foreign_keys_exist`, `test_unique_constraints_match`). (Phase 4)
- **`sam/queries/dashboard.py` two-path strategy** — `_build_project_resources_data` vs `_build_user_projects_resources_batched`, with an equivalence test locking them in step. Exemplary. (Phase 4)
- **`HtmxFormSchema._strip_empty_strings`** centralizes the §9 pre-process (drop empty strings, inject False for unchecked checkboxes) so routes don't have to. (Phase 4)
- **Builder pattern in CLI** (`user/builders.py`, `project/builders.py`) cleanly separates ORM-to-dict extraction from rendering — same dict feeds Rich and JSON. (Phase 4)
- **`TZ=UTC` enforcement in `collectors/run_collectors.sh:14`** with a 25-line README section explaining the naive-UTC convention that ties into Phase 3's storage. Strong defensive choice. (Phase 5)
- **Custom typed exception hierarchy** in `collectors/lib/exceptions.py` (`CollectorError` → `PBSError`, `APIError`, `SSHError`, `ConfigError`) used consistently. (Phase 5)
- **Retry with exponential backoff in `api_client.post_status`** correctly skips retry on 400 (validation) and 401/403 (auth) — distinguishes retriable from non-retriable. (Phase 5)
- **Parallel SSH via ThreadPoolExecutor** for login nodes — ~10× speedup (20s → 2-3s for 8 nodes). (Phase 5)
- **`flock -xn`** in cron prevents overlapping runs. (Phase 5)
- **The collector test file uses an importlib trick** to avoid sys.path collision between `sam.config` and `collectors/lib/config.py` — sharp observation by the author. (Phase 5)
- **README is honest about deferred work** — explicit "Next Steps (Deferred)" listing 5 items most projects would hide. Strength of documentation if not execution. (Phase 5)
- **Three-tier secret injection is well-thought-out and tested** — `external_secret.yaml` cleanly maps OpenBao paths to k8s Secrets; `helm/tests/test-oidc-render.sh` asserts the prod/local rendering contract including a `dev-only-insecure-key` guard. (Phase 6)
- **`docs/README-k8s.md` is unusually good operator documentation** — local-vs-prod matrix + per-environment auth/OIDC matrix. The kind of runbook on-call needs. (Phase 6)
- **Three environments share one Dockerfile target** — Compose + ECS + k8s all run `containers/webapp/Dockerfile` stage `production`. True single-source-of-truth at build layer. (Phase 6)
- **Gunicorn worker sizing is cgroup-aware** — computes `workers = 2·cpu_limit + 1` from chart value rather than letting `multiprocessing.cpu_count()` see the node's 64 cores. Explicit defense against a documented OOM. (Phase 6)
- **CI concurrency groups everywhere** — every workflow has `cancel-in-progress: true` for PRs, `cancel-in-progress: false` for deploys. No wasted runs, no cancelled deploys. (Phase 6)
- **Skip-CI semantics are explicit `if:` checks (not GitHub native)** and asymmetric — CI workflows honor `[skip ci]`, deploy workflows don't because TruffleHog must run. Documented in CLAUDE.md. (Phase 6)
- **Test-install smoke workflow** exercises the user-facing `curl|bash` against fresh + update branches and asserts on `/api/v1/health/ready`. Catches drift the unit tests can't. (Phase 6)
- **Build provenance wired correctly** — `GIT_SHA` + `BUILD_DATE` injected as build args, Dockerfile places them after `pip install` so layer caching survives. (Phase 6)
- **Three-secret-store discipline for prod** — AWS SSM (Fargate), OpenBao (k8s), Compose env (local). No cross-pollination; no committed secrets in the scan. (Phase 6)
- **Defense-in-depth secret scanning** — TruffleHog (CI + deploy), GitGuardian (pre-push), `detect-private-key` (pre-commit). Three independent layers (modulo the `@main` pin). (Phase 6)
- **OIDC rotation procedure is best-in-class** (`docs/AUTHENTICATION.md:322-360`) — the kind of runbook every secret should have. (Phase 6)
- **`gen_api_key.py` cryptography is sound** — `secrets.token_urlsafe(32)` CSPRNG, bcrypt rounds=12 default, allows `--rounds 14` for prod hardening. (Phase 6)
- **LFS for the dev DB dump** — 133-byte pointer committed; obfuscation pipeline documented; `.gitignore` correctly excludes real-data local snapshots. (Phase 6)
- **`install.sh` in-place detection** is genuinely thoughtful — checks `compose.yaml`, `.env.example`, and `git rev-parse --is-inside-work-tree`. (Phase 6)
- **Health endpoint design is correct** — `/api/v1/health/{live,ready,db}` cleanly separates liveness (no DB call) from readiness (DB ping). Code is right; just unused by Helm. (Phase 6)
- **`docs/plans/implemented/` is exemplary archive practice** — 29 plan docs from completed work moved aside in a dedicated subdirectory. Model this for other doc archives. (Phase 7)
- **`docs/presentations/` has its own scoped `CLAUDE.md`** that auto-loads when Claude is working in that subtree. Nice subtree-scoped context pattern worth borrowing. (Phase 7)
- **`migrations/system_status/` runbooks** (`PROD_BOOTSTRAP.md`, `0002_NORMALIZATION_RUNBOOK.md`, `0003_USER_PROJ_QUEUE_RUNBOOK.md`) are operationally exemplary — backup-first, dry-run, expected-table verification. (Phase 3 strength, re-noted in Phase 7.) (Phase 7)

## Action register

> Every finding rolled up, severity-grouped, with effort estimate and one-line fix sketch. Cross-references to the source phase doc for the full reasoning.
>
> **Effort scale:**
> - **Tiny** — < 1 hour, mechanical
> - **Small** — a few hours, one or two files
> - **Medium** — ½–2 days, multiple files / requires thought
> - **Large** — > 2 days, architectural / cross-cutting

### P0 — Production risk (security / availability)

- **P0-5** [Phase 4 / B1] **`ProjectListSchema.get_admin_username` has no body — returns `None` for every project in every list response.** Silently shipping in production today. Fix: add `return obj.admin.username if obj.admin else None`. Effort: **Tiny**. → `sam/schemas/project.py:67-70`. Depends on Q14 (regression vs old).
- **P0-6** [Phase 4 / B2] **`ProjectSchema.get_panel` raises 500 on orphan projects** (missing None guard on `obj.allocation_type`). `GET /api/v1/projects/<orphan>` 500s. Effort: **Tiny**. → `sam/schemas/project.py:122`.

- **P0-9** [Phase 6 / D1] **Helm webapp Deployment has zero probes** despite the webapp exposing well-designed `/api/v1/health/{live,ready,db}` endpoints. On CIRRUS k8s, an unhealthy pod is never restarted; rolling updates serve traffic before gunicorn workers are ready. Fix: wire `livenessProbe`/`readinessProbe` to the endpoints (Compose + ECS already do). Effort: **Tiny**. Depends on Q36. → `helm/templates/deployment.yaml:103`
- **P0-10** [Phase 6 / CI1] **`trufflesecurity/trufflehog@main` is the only secrets gate on the deploy path.** Branch-pin = a TruffleHog repo compromise executes attacker code with `AWS_ACCESS_KEY_ID` on the next push to `staging`. Fix: pin to release tag or SHA. Effort: **Tiny**. → `deploy-staging.yaml:38`, `ci-staging.yaml:34`
- **P0-11** [Phase 6 / CI2] **`deploy-staging` uses long-lived AWS keys with no approval gate.** Every push to `staging` → build → ECS deploy in <15 min, no human in loop. Fix: OIDC + IAM role-to-assume + protected `environment: staging` with reviewers. Effort: **Small** (1-2 hours). Depends on Q37. → `deploy-staging.yaml:56-57`
- **P0-12** [Phase 6 / CI3] **`update-helm` force-pushes prod deploy branch** without server-side protection visible. Any contributor PAT compromise lands prod-image refs on `cirrus`. Fix: protect `cirrus` server-side; require signed commits or `environment:` approval. Effort: **Small**. Depends on Q38. → `build-images-cirrus-deploy.yaml:294-301`
- **P0-13** [Phase 6 / D2] **Staging RDS is `publicly_accessible=true`** with `skip_final_snapshot=true`. Mitigated only by UCAR CIDR. Defense-in-depth regression vs ECS tasks (private subnets). Fix: move to private subnet (~5-line change), flip `skip_final_snapshot`. Effort: **Small**. Depends on Q40. → `infrastructure/staging/rds.tf:26,29`
- **P0-14** [Phase 6 / O1] **No error ingestion path for unhandled exceptions.** No `@app.errorhandler(500)`, no Sentry/Rollbar. A 500 in production lands in CloudWatch (staging) or pod logs (prod) and stops there. `DESIGN.md:422` lists this as a TODO. Effort: **Small** (Sentry SDK + DSN env var). Depends on Q44.
- **P0-15** [Phase 6 / O2] **Audit log written to ephemeral container path with no shipping.** ECS Fargate writes to container writable layer → destroyed on redeploy. Phase 2's audit log is non-durable in practice. Fix: switch to stdout (logger.StreamHandler) and let the awslogs/cloudwatch driver handle it, OR mount EFS / ship to S3. Effort: **Small**. **Pairs with Phase 2 Q11.** → `src/webapp/audit/logger.py:64-69`

- **P0-7** [Phase 5 / O1] **Zero-substitution on collection failure** — every concrete collector's `_collect_node_data` exception handler substitutes all-zeros for node counts. A transient SSH/PBS hiccup turns "we don't know" into "system fully down" on the dashboard. Fix: skip the API POST entirely on `_collect_node_data` failure, OR add `degraded: True` flag so dashboards can distinguish. Effort: **Small**. Depends on Q31. → `derecho/collector.py:37-55`, `casper/collector.py:78-103`, `jupyterhub/collector.py:243-264`
- **P0-8** [Phase 5 / O2] **No alerting on persistent collector failure.** Six hours of consecutive failures look identical to one hour. Fix: integrate with NCAR's monitoring (Slack webhook, healthchecks.io heartbeat). Effort: **Small** (depends on NCAR ops infra). Depends on Q33.

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
- **P1-17** [Phase 3 / O1] **`cleanup_status_data.py` not visibly scheduled in-repo** — no helm CronJob, no GitHub Actions, no systemd timer checked in. Either it's externally scheduled (verify) or `system_status` grows unbounded. Effort: **Small** (add helm CronJob or document scheduler). Depends on Q15.

#### ORM / schema correctness (Phase 4)

- **P1-18** [Phase 4 / B3] **`Project.active_account_users` silently drops `start_date` guard** — can surface future-dated rows. Fix: use `au.is_active_at(check_date)` directly. Same anti-pattern in `core/users.py:410, 433`. Effort: **Tiny**. → `sam/projects/projects.py:386-393`
- **P1-19** [Phase 4 / B4] **Duplicate function definition `analyze_renew_preconditions`** at `sam/manage/renew.py:168` and `:206`. Second silently shadows first. Delete one. Effort: **Tiny**.
- **P1-20** [Phase 4 / B5] **`Organization` and `Institution` declare `deleted` column raw, bypassing `SoftDeleteMixin`** — their `is_active` (from `ActiveFlagMixin`) doesn't consider `deleted`. A `deleted=True, active=True` row is treated as "active." Either intentional or latent bug. Depends on Q16. Effort: **Small**. → `sam/core/organizations.py:49, 203`
- **P1-21** [Phase 4 / A1] **`sam/base.py:19-40` silent fallback to standalone `declarative_base`** — identical pattern to P1-16. Confirms the `[XC: prod-config-hardening]` theme: now 5 footguns following "fall back instead of refuse." Effort: **Tiny**.

#### Schema performance (Phase 4)

- **P1-22** [Phase 4 / P1] **`AllocationWithUsageSchema` recomputes usage 4-8× per allocation dump** → 16-24 DB queries per allocation, N×20 round trips on `many=True` endpoints. Fix: memoize on schema instance keyed by `allocation_id`. Effort: **Small**. Depends on Q17. → `sam/schemas/allocation.py:291-345`
- **P1-23** [Phase 4 / P3] **`usage_cache` has no invalidation hooks on write paths** (`manage/allocations.py`, `manage/renew.py`, `manage/extend.py`). Stale data window up to 3600s after admin UI writes. Fix: per-allocation/project key-prefix invalidation. Effort: **Medium**. Depends on Q20.
- **P1-24** [Phase 4 / P4] **`usage_cache.py:81` bypasses `app.config['CACHE_REDIS_URL']` reachability gate.** Reads env directly — doesn't see the webapp's startup PING downgrade to `SimpleCache`. **Sharpens P2-14.** Effort: **Tiny**.

#### `is_active` discipline cleanup (Phase 4)

- **P1-25** [Phase 4 / D1 + D2 + D3] **Convention drift: 10+ raw column comparisons inside `Project` and `User` themselves** (the very models that define the canonical hybrid). 4 sites in `sam/projects/projects.py`, 6 in `sam/core/users.py`, 1 in `sam/queries/statistics.py:89`. Plus `D6` in `manage/`. Effort: **Small** (mechanical).
- **P1-26** [Phase 4 / D7] **`Factor`/`Formula` redundantly redeclare `is_active`** (`resources/charging.py:36-48, 85-97`) when `DateRangeMixin` already provides the identical hybrid. Deletable. Effort: **Tiny**.

#### CLI hardening (Phase 4)

- **P1-27** [Phase 4 / C3] **`EXIT_KEYBOARD_INTERRUPT` (130) is defined but never used.** No top-level `try/except KeyboardInterrupt` in `cmds/search.py`/`cmds/admin.py`. Ctrl-C bubbles unhandled, defeating documented exit code. Effort: **Tiny**.
- **P1-28** [Phase 4 / C5] **CLI validation errors print to stdout, corrupting JSON pipelines** (`cmds/admin.py:93-109, 290-344, 372-430`). Standardize on `ctx.stderr_console`. Effort: **Small**.
- **P1-29** [Phase 4 / C6] **`ProjectExpirationCommand._deactivate_projects` mutates ORM directly** (`project.active = False; ...; self.session.commit()`). Per §7 should be a `Project.deactivate()` method. Effort: **Tiny**. → `cli/project/commands.py:248-259`
- **P1-30** [Phase 4 / C4] **Inline date coercion in 3 CLI sites** — `cmds/admin.py:338, 424`, `allocations/commands.py:39`. Route through `cli/accounting/dates.py` (already exists). Effort: **Tiny**.
- **P1-31** [Phase 4 / C2] **`cli/accounting/commands.py` uses bare integer return codes** at 50+ sites (`return 0`/`1`/`2`) instead of `EXIT_*` symbols. Effort: **Small** (mechanical). Depends on Q21 inheritance question.

#### Test gaps (Phase 4)

- **P1-32** [Phase 4 / T3] **`test_column_types_match` and `test_database_columns_in_orm` are informational, not assertive.** The documented Boolean → BIT(1) historical bug would NOT be caught. CLAUDE.md claim is partly aspirational. Effort: **Small** (promote to assertive). Depends on Q23.
- **P1-33** [Phase 4 / T1] **Documented query functions untested** — `get_projects_by_allocation_end_date` / `get_projects_with_expired_allocations` have zero direct coverage despite being featured in CLAUDE.md with explicit return-shape tuples. Effort: **Tiny** (~4 tests).
- **P1-34** [Phase 4 / T2] **CLI tests cover exit codes 0 and 1 only.** Should assert at least one error path produces `2`. Pairs with P1-27 (KeyboardInterrupt → 130). Effort: **Tiny**.

#### Collector subsystem (Phase 5)

- **P1-35** [Phase 5 / S1] **Shell injection by config in `pbs_client.py:40` and `ssh_utils.py:113-116`.** Args come from `config.yaml` (trusted today) but f-string interpolation under `shell=True` is the classic CWE-78 pattern. Fix: `subprocess.run([...args])` without `shell=True`, or `shlex.quote()` every value. Effort: **Small**.
- **P1-36** [Phase 5 / O3] **`run_collectors.sh:91` swallows collector stdout/stderr.** Python logger's stdout output is discarded; only `--log-file=` path survives. If file logger creation fails, no fallback. Effort: **Tiny** (redirect to fallback stderr file).
- **P1-37** [Phase 5 / O4] **Cron paths reference `/glade/work/benkirk/repos/sam-queries/`.** If `benkirk` leaves NCAR or quota gets cleared, collectors break silently. Move to shared/system location. Effort: **Small**. Depends on Q32.
- **P1-38** [Phase 5 / T1] **One test file for ~2,600 LOC of collector code.** `api_client` retry logic, `base_collector` exception handling, `ssh_utils` parallel collection, `pbs_client`, the JupyterHub statistics calculator, all 7 parsers — none have dedicated tests. **The reliability of the entire status tier depends on this code.** Effort: **Medium-Large** (broad coverage gap).
- **P1-39** [Phase 5 / S2] **`verify=False` on JupyterHub API** (`jupyterhub/collector.py:289`) with `urllib3.disable_warnings(...)` at module level. Auth token still sent in `Authorization` header. Fix: either trust NCAR's CA (`verify='/path/to/ca.crt'`) or document the historical reason. Effort: **Small** depending on cert situation. Depends on Q30.
- **P1-40** [Phase 5 / S3] **Default `STATUS_API_URL=http://localhost:5050` over HTTP.** API key sent in cleartext if anyone leaves the default in prod. Fix: startup warning if HTTP and host isn't localhost. Effort: **Tiny**. Depends on Q35.
- **P1-41** [Phase 5 / S4] **No `BatchMode=yes` on SSH** — falls back to interactive prompts under cron. Fix: add `-o BatchMode=yes -o StrictHostKeyChecking=accept-new` to every SSH invocation. Effort: **Tiny** (3 sites).

#### Platform / CI / deploy (Phase 6)

- **P1-42** [Phase 6 / D6] **Webapp container runs as root** in all 3 environments — no `USER` directive in `containers/webapp/Dockerfile`, no `securityContext: runAsNonRoot` in `deployment.yaml`. Same pattern as Phase 5's collector Dockerfile. Effort: **Small** (add USER + fix file ownership in image). → `containers/webapp/Dockerfile`, `helm/templates/deployment.yaml`
- **P1-43** [Phase 6 / SS1] **No documented rotation procedure for `STATUS_API_KEY` or `JUPYTERHUB_API_TOKEN`.** OIDC rotation is best-in-class (`AUTHENTICATION.md:322-360`); other secrets are operator-tribal-knowledge. Effort: **Small** (doc). Depends on Q47.
- **P1-44** [Phase 6 / SS2] **Python deps loose-pinned, no lockfile anywhere.** 24 unpinned deps in `pyproject.toml`; no `poetry.lock`/`conda-lock.yml`/`requirements.lock`. CI image and last week's CI image can diverge silently. Effort: **Small** (introduce `pip-compile` or `uv lock`). Depends on Q46.
- **P1-45** [Phase 6 / CI4] **No third-party CI action SHA-pinned.** TruffleHog, `peter-evans/create-pull-request`, `stefanzweifel/git-auto-commit-action`, `oxsecurity/megalinter`, `conda-incubator/setup-miniconda` all tag-pinned. Fix: SHA-pin third-party + Dependabot for actions. Effort: **Small**. Pairs with P0-10.
- **P1-46** [Phase 6 / CI5] **`BENKIRK_GITHUB_TOKEN` is a personal PAT in 3 maintenance workflows.** Bus factor 1: when Ben rotates / leaves, log cleanup + GHCR pruning silently stop. Fix: switch to `GITHUB_TOKEN` with appropriate `permissions:` block (the fallback already exists in `clean-ghcr.yaml:29`). Effort: **Tiny**. Depends on Q39.
- **P1-47** [Phase 6 / CI6] **No `permissions:` block on 5 workflows.** Jobs inherit org default `GITHUB_TOKEN` perms. Fix: `permissions: contents: read` at workflow top, elevate per-job. Effort: **Tiny**. → `deploy-staging.yaml`, `sam-ci-docker.yaml`, `sam-ci-conda_make.yaml`, `ci-staging.yaml`, `test-install.yaml`
- **P1-48** [Phase 6 / CI8] **Mega-linter is non-blocking + KICS HIGH+ only.** Combined with `.trivy.yaml` HIGH/CRITICAL gate, project is blind to MEDIUM-severity IaC + dep findings by configuration. Fix: at minimum surface MEDIUM as warnings; ideally fail on MEDIUM with a documented `.trivyignore` for known-acceptable. Effort: **Small**.
- **P1-49** [Phase 6 / O3 + O4] **No structured logging + `request_id` doesn't propagate.** Human format only; `g.request_id` embedded in one line, no downstream code reads it. CloudWatch Insights / Loki structured queries impossible. Fix: JSON logger + `LoggerAdapter` that injects `rid` everywhere. Effort: **Small**.
- **P1-50** [Phase 6 / O5] **No metrics endpoint or backend.** No `prometheus_client`, no `/metrics`, no histograms. The only "metric" is `run.py:180-184`'s hardcoded `elapsed_ms > 5000` warning. Fix: add `prometheus_client`, expose `/metrics`, hook into k8s/ECS scraping. Effort: **Medium**.
- **P1-51** [Phase 6 / I1] **DB switch scripts silently no-op against the canonical `.env`.** `switch_to_local_db.sh:30-33` patches the legacy raw-value format; `.env.example` uses variable-indirection. Developer ends up with `.env` having no `SAM_DB_*` set. Fix: regex-match both formats, or assert the patch happened. Effort: **Tiny**. Depends on Q49.
- **P1-52** [Phase 6 / I5] **`setup_local_db.sh:90` destroys MySQL volume unconditionally** on non-healthy container. No confirmation prompt. Local data lost silently. Fix: prompt unless `--force`. Effort: **Tiny**.
- **P1-53** [Phase 6 / D9 + Phase 5 P2-67] **Collector deployment not in Helm chart** — container image built and pushed but no `CronJob`/`Deployment` in `helm/templates/`. Confirms two-parallel-models finding. Either ship the CronJob or delete the image build. Effort: **Small** (write CronJob) or **Tiny** (delete + document Glade cron is canonical). Depends on Q42.
- **P1-54** [Phase 6 / D11] **`helm/tests/test-oidc-render.sh` not invoked by CI** — the `dev-only-insecure-key` guard exists but isn't enforced. Fix: add to `build-images-cirrus-deploy.yaml`. Effort: **Tiny**.
- **P1-55** [Phase 6 / O6] **Healthcheck failures invisible.** A failing `/health` appears as a normal `INFO 503` line. No CloudWatch alarms in `ecs.tf`. DB outage produces nothing humans see. Fix: explicit logger.error path for health failures + CloudWatch alarm on 5xx rate. Effort: **Small**.

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

#### ORM / library polish (Phase 4)

- **P2-39** [Phase 4 / A2] **`Project` god-class (1,387 LOC, 38 methods).** `batch_get_subtree_charges` / `batch_get_account_charges` are 200+ lines each and don't access `self` — candidates to move to `sam/queries/charges.py`. Maintainability; depends on Q19. Effort: **Medium**.
- **P2-40** [Phase 4 / A3] **`NestedSetMixin` concurrent-write risk (speculative).** Two non-atomic UPDATEs could race on `Organization` tree (unscoped path). Mitigations: `SELECT FOR UPDATE` on parent. Depends on Q18. Effort: **Small**.
- **P2-41** [Phase 4 / A4] **8 models reinvent `DateRangeMixin` end-date normalization.** Refactor: thin `EndDateNormalizerMixin`. Effort: **Small**.
- **P2-42** [Phase 4 / A5] **`cli/{accounting,notifications,templates}/` undocumented in CLAUDE.md.** Production code, not stale. Effort: **Tiny** (doc-only).
- **P2-43** [Phase 4 / P2] **`ProjectListSchema` N+1 on `lead`/`admin` lookups** defeats the "List tier is lightweight" contract. Fix: eager-load hint in route or relationship default. Effort: **Small**.
- **P2-44** [Phase 4 / P5] **3 unbounded `.all()` results** in `queries/projects.py:67, 74` and `queries/users.py:217`. Add `limit=` defaults. Effort: **Tiny**.
- **P2-45** [Phase 4 / P6] **`queries/expirations.py:31` uses sentinel date `datetime(9999, 12, 31)`** for NULL coalescing. SQL anti-pattern. Effort: **Tiny**.
- **P2-46** [Phase 4 / D4] **4 pure-wrapper functions in `queries/lookups.py`** (`find_user_by_username`, `find_users_by_name`, `find_project_by_code`, `get_group_by_name`) that just delegate to existing model classmethods. Deprecate; update ~8 callers. Effort: **Small**.
- **P2-47** [Phase 4 / D5] **`queries/examples.py` is dead code** with `from sam import *`. Move to `docs/examples/` or `notebooks/`. Effort: **Tiny**.

#### CLI polish (Phase 4)

- **P2-48** [Phase 4 / C1] **`AccountingAdminCommand` doesn't extend `AccountingSearchCommand`** — inheritance pattern documented in `cli/README.md:99` is broken for the largest module (1,720 LOC). Reconcile docs or refactor. Depends on Q21. Effort: **Medium**.
- **P2-49** [Phase 4 / C7] **`--validate` / `--reconcile` admin commands are placeholders** with no real logic. Either implement or hide from `--help`. Depends on Q22. Effort: **Tiny** (hide) or Medium (implement).
- **P2-50** [Phase 4 / C8] **No TTY detection / `NO_COLOR` opt-out** in `Console()` construction. Effort: **Tiny**.
- **P2-51** [Phase 4 / C9] **`cmds/admin.py:38-39` and `cmds/search.py:46-50`** use `sys.exit(1)` (not-found) for DB connect failure (should be `2`). Effort: **Tiny**.
- **P2-52** [Phase 4 / C10] **`cli/README.md` references `sam_search_cli_original.py`** which no longer exists. Effort: **Tiny**.

#### Schema polish (Phase 4)

- **P2-53** [Phase 4 / S1] **Edit-form checkbox semantics inconsistent** — 7 Edit forms don't use `partial=True`, so "unchecked checkbox = deactivate" is the implicit contract. Either standardize on `partial=True` or document the deactivation contract. Depends on Q22's spirit. Effort: **Small**.
- **P2-54** [Phase 4 / S2] **Missing schema tiers** — `AllocationListSchema`, `AllocationSummarySchema`, `ResourceListSchema` don't exist. List endpoints reuse heavy variants. Effort: **Small**.
- **P2-55** [Phase 4 / S3] **`AllocationSchema.is_active` Method override is dead code** — overrides the hybrid with `obj.is_active_at(datetime.now())`, which is identical. Drop. Effort: **Tiny**.
- **P2-56** [Phase 4 / S4] **`CompJobSchema` manually `.isoformat()`s inside Method fields** — violates the documented "no manual `.isoformat()`" rule. Effort: **Tiny**.
- **P2-57** [Phase 4 / S5] **~35 Method fields hand-wrap simple `@property` accessors** when `fields.Str(attribute='full_name', dump_only=True)` would do. Cleanup. Effort: **Small**.

#### Test polish (Phase 4)

- **P2-58** [Phase 4 / T4] **`test_crud_operations.py` instantiates models directly** instead of going through `create()` classmethods. Add explicit tests for `create()` validation paths. Effort: **Small**.
- **P2-59** [Phase 4 / T5] **`make_organization` factory has fragile 100k-org-per-worker carve-out.** Document or assert. Effort: **Tiny**.
- **P2-60** [Phase 4 / T6] **Stale `new_tests/conftest.py` references** in 5 test files. Effort: **Tiny**.
- **P2-61** [Phase 4 / T7] **Raw `User.active == True` in 3 test files** — convention violation. Effort: **Tiny**.
- **P2-62** [Phase 4 / T8] **CLI tests pin `benkirk` in content assertions** — fragile if test-user changes. Add explicit precondition assert. Effort: **Tiny**.
- **P2-63** [Phase 4 / T9] **`test_views.py:96-106` swallows bare `Exception`** for an XRAS view GROUP BY issue. Narrow to `OperationalError` + TODO. Effort: **Tiny**.
- **P2-64** [Phase 4 / T10] **`test_redis_cache.py:75` uses `time.sleep(1.1)`** — one occurrence, documented case, but lists as a smell per CLAUDE.md. Effort: **Tiny**.
- **P2-65** [Phase 4 / T11] **`_session_for_setup()` opens a Session without explicit transaction.** Defensive: use `engine.connect()` + `text()` for read-only intent. Effort: **Tiny**.

#### Platform polish (Phase 6)

- **P2-76** [Phase 6 / CI7] **`mega-linter.yaml` dormant apply-fixes/auto-commit blocks.** `APPLY_FIXES: none` means they don't fire. Dormant code = misconfig surface. Delete until needed. Effort: **Tiny**.
- **P2-77** [Phase 6 / CI9] **`paths-ignore: ['docs/**', '**.md']`** on secret-scan workflow. A doc-only PR could carry a leaked credential. Drop from `ci-staging.yaml`. Effort: **Tiny**.
- **P2-78** [Phase 6 / D3] **Image tag `:main` + `imagePullPolicy: IfNotPresent` non-updating combo** (`values.yaml:31`). CI rewrites to `:sha-<short>` on `cirrus` branch. Two sources of truth. Effort: **Tiny** (doc) or **Small** (consolidate). Depends on Q41.
- **P2-79** [Phase 6 / D4] **ECS task pinned to `:latest`** while workflow pushes both `:<sha>` and `:latest`. `lifecycle.ignore_changes` masks but `terraform apply` will drift back. Fix: parameterize image tag via TF var. Effort: **Small**.
- **P2-80** [Phase 6 / D5] **No HPA, no PDB, no `topologySpreadConstraints`** on webapp Deployment. Voluntary disruption on 2 replicas can take both down. Effort: **Small**.
- **P2-81** [Phase 6 / D7] **Bcrypt-hashed API key committed in `values.yaml`.** Defensible (hash, not key) but inconsistent with OIDC pattern. Rotating requires chart commit + redeploy. Effort: **Small** (move to ExternalSecret).
- **P2-82** [Phase 6 / D8] **`local-secrets.sh` inconsistent default handling** — some vars use `${VAR:-default}`, `SAM_DB_USERNAME/PASSWORD` don't (`unbound variable` errors under `set -u`). Effort: **Tiny**.
- **P2-83** [Phase 6 / D10] **`Chart.yaml` version stuck at `0.0.1`** — never bumped, loses chart-level diffing. Effort: **Tiny**.
- **P2-84** [Phase 6 / SS3] **`.env.example` incomplete vs code** — ~30 env vars consumed by code not enumerated. None secrets, but the "what knobs exist" gap is real. Effort: **Small**.
- **P2-85** [Phase 6 / SS4] **`helm/local-secrets.sh` silently defaults to `root/root`** for DB creds. No `kubectl context` guard. Effort: **Tiny**.
- **P2-86** [Phase 6 / SS5] **`.trivyignore` entries have no expiry/review.** `AVD-DS-0002` (non-root USER) globally ignored — pairs with P1-42 (root container) and should be revisited together. Effort: **Tiny**.
- **P2-87** [Phase 6 / SS6] **`conda-env.yaml` is fully unpinned** except `postgresql=18.*`. Effort: **Tiny** (add version pins).
- **P2-88** [Phase 6 / I2] **`install_local.sh` is a thin wrapper that diverges from `make conda-env`** with incoherent Apple Silicon advice and a pointer to the broken `switch_to_production_db.sh`. Either consolidate or clearly delineate. Effort: **Small**.
- **P2-89** [Phase 6 / I3] **`install.sh` does not pin/verify what it pulls.** Defaults to `REPO_BRANCH=main`; no SHA pin, no signed-commit verification, no checksum, no `chmod 600` on copied `.env`. Effort: **Small**.
- **P2-90** [Phase 6 / I4] **`etc/config_env.sh` runs `make` unconditionally on every source.** No strict-mode pragma; `exit 1` paths kill the user's terminal session. Effort: **Tiny**.
- **P2-91** [Phase 6 / I6] **Setup-doc cluster overlap** (matches Phase 1 finding P2-32). 8 install-related docs. Combined with I1's drift, docs and code haven't been cross-checked. Effort: **Small**.
- **P2-92** [Phase 6 / I7] **`Makefile fixperms` uses NCAR-specific group ACLs** that won't exist on a fresh laptop. `make help` doesn't warn server-only. Effort: **Tiny**.

#### Collector polish (Phase 5)

- **P2-66** [Phase 5 / A1] **`try/except ImportError` import-path dance** in 4 collector modules. Pick: `pip install -e collectors/` + relative imports, or drop the pyproject entry points. Effort: **Small**.
- **P2-67** [Phase 5 / A2] **Two parallel deployment models** (container vs cron-from-host) with unclear winner. Document canonical; remove or label the other. Depends on Q29. Effort: **Tiny** (doc) to Medium (consolidate).
- **P2-68** [Phase 5 / A3] **JupyterHub collector overrides `collect()`** to skip 3 BaseCollector steps. Tidier: skip-flags pattern (no-op when config list is empty). Effort: **Small**.
- **P2-69** [Phase 5 / O5] **Cron log redirection writes to relative paths.** Use absolute paths so ops can find logs without insider knowledge. Effort: **Tiny**.
- **P2-70** [Phase 5 / O6] **Dockerfile unpinned + suboptimal** — `FROM python:3` (no version pin), dead `apt-get update` step, no `--no-cache-dir`, no `USER` directive (runs as root), embedded debugging script. Effort: **Small** if container is canonical; **Tiny** (delete) if not.
- **P2-71** [Phase 5 / O7] **No idempotency key on status ingest.** Re-run at same minute = duplicate row. Probably fine for append-only audit tables. Pairs with Phase 3 Q17. Effort: **Tiny** (document) or **Small** (add unique constraint).
- **P2-72** [Phase 5 / L1] **`api_password` named "password" but is API key** in code + README §Configuration line 159. `.env.example` got it right. Rename param + fix README. Effort: **Tiny**.
- **P2-73** [Phase 5 / L3] **Bare `except:` in `api_client.py:77`** swallows JSON parse failure. Narrow to `except json.JSONDecodeError`. Effort: **Tiny**.
- **P2-74** [Phase 5 / Doc] **README §Configuration contradicts `.env.example`** on `STATUS_API_KEY` meaning. One-line fix. Effort: **Tiny**.
- **P2-75** [Phase 5 / Doc] **`collectors/docs/PBS_COLLECTORS_PLAN.md` and `…_ADD_RESERVATIONS_PLAN.md`** are plan docs likely stale post-implementation. Disposition: archive / delete / keep. Pairs with Phase 1 docs hygiene. Depends on Q34.

#### Status tier polish (Phase 3)

- **P2-34** [Phase 3 / S3] **`schemas/status.py:24` uses `from system_status import *`** which pulls `main` (the CLI entry point) into schema namespace. Replace with explicit imports. Effort: **Tiny**. → `system_status/schemas/status.py:24`
- **P2-35** [Phase 3 / S4] **`cli.py:22-23` does module-level `sys.path.insert`.** Works around proper packaging; works fine in practice but bites when installed vs. run-from-source. Effort: **Tiny**.
- **P2-36** [Phase 3 / S5] **`cli.py:62` hardcodes system choices `['derecho', 'casper', 'jupyterhub']`.** Drifts when new systems are added. Optional: derive from `System` lookup table at parser-build time. Effort: **Small** (or document-only).
- **P2-37** [Phase 3 / O2] **Cleanup script doesn't reap lookup tables** (`UserDef`, `ProjectCodeDef`, etc.). In practice these are bounded by the user/project/queue catalog and won't blow up, but a one-off bad ingest leaves orphans forever. Effort: **Small**.
- **P2-38** [Phase 3 / O3] **Document the monotonic-`T_new` assumption in the span coalescer.** Out-of-order or backfill ingests aren't currently a concern (collectors push monotonically every 5 minutes), but the assumption is implicit. Effort: **Tiny** (comment-only).

#### Docs hygiene — final dispositions (Phase 7)

> Most are Tiny effort. Best executed as **one bundled "docs-consolidation" PR.** Total effort: ~½ day; total surface: ~10 docs deleted/merged, ~5 stub-redirects, `docs/archive/` created.

- **P2-93** [Phase 7] **Create `docs/archive/` as the durable home for stale-but-useful artifacts.** Mirrors the `docs/plans/implemented/` pattern. Effort: **Tiny** (just `mkdir` + README).
- **P2-94** [Phase 7] **Update `docs/INDEX.md`** — currently doesn't mention `plans/`, `apis/`, `presentations/` subdirs; AUTHENTICATION.md is only in the FAQ. Effort: **Small**.
- **P2-95** [Phase 7] **`README.md` (941 lines) — trim and update.** Drop inline setup steps that duplicate LOCAL_SETUP.md; refresh test counts to actual (~1,750); add 7 missing API endpoint modules. Effort: **Small**.
- **P2-96** [Phase 7] **Refresh `CLAUDE.md` stats on next pass** — model count "91+" → ~106; test count "~1,400" → ~1,750. Effort: **Tiny**.
- **P2-97** [Phase 7] **Merge `k8s.md` into `README-k8s.md`** (Phase 1 + Phase 6 D11). Effort: **Small**.
- **P2-98** [Phase 7] **Move `docs/CIRRUS-k8s-cmds.sh` to `scripts/`.** A `.sh` inside `docs/` is non-discoverable. Effort: **Tiny**.
- **P2-99** [Phase 7] **Reduce setup-doc cluster** — `LOCAL_SETUP.md` becomes canonical; `SETUP_SUMMARY.md` / `WEBAPP_SETUP.md` / `CREDENTIALS.md` become stub-redirects; `SCRIPTS.md` + `SCRIPT_ORGANIZATION.md` merge into `scripts/README.md`. Effort: **Small** (sequence of mv + edit + redirect).
- **P2-100** [Phase 7] **Rename `docs/GETTING_STARTED.md` to `STACK_PRIMER.md`** to clarify it's a tech-stack primer, not a setup doc. Effort: **Tiny**.
- **P2-101** [Phase 7] **`src/webapp/IMPLEMENTATION_SUMMARY.md` → delete** (Phase 1 disposition finalized). Effort: **Tiny**.
- **P2-102** [Phase 7] **`src/webapp/DESIGN.md` → `docs/archive/webapp-design.md`** (Phase 1 disposition finalized). Effort: **Tiny**.
- **P2-103** [Phase 7] **`src/webapp/QUICK_START_RBAC.md` → `docs/TESTING_RBAC.md`** (promote per Phase 1 + Phase 2 strength). Effort: **Tiny**.
- **P2-104** [Phase 7] **`src/webapp/REFACTORING_PLAN.md` → verify + move to `docs/plans/WEBAPP_REFACTORING_BACKLOG.md`** — Priority 1.1 and 2.1 are partly/fully done per Phase 2/4. Retire completed items. Effort: **Small**. Depends on Q3.
- **P2-105** [Phase 7] **`src/webapp/README.md` — trim.** API section omits ~7 endpoint modules; references "future Marshmallow" (now done). Cross-reference `docs/apis/*`. Effort: **Small**.
- **P2-106** [Phase 7] **`docs/prompts/` → `docs/archive/build-prompts/`** with an explanatory README. Effort: **Tiny**. Depends on Q5 (Phase 1).
- **P2-107** [Phase 7] **`docs/remediation/` — short-term: rename to date-first directory** (`2026-05-02-CESM0002/`) with an index README. **Long-term: ship to Confluence per Q2.** Effort: **Tiny** (rename) / **Small** (Confluence migration).
- **P2-108** [Phase 7] **`docs/plans/` reconciliation** — verify `DISK_CHARGE_SUMMARY-only.md`, `SCHEMA_VISUALIZATION.md` status (move to `implemented/` if shipped); move `RATE_LIMITING.md` to `implemented/` (verified done in Phase 2); refresh `PRODUCTION_IMPROVEMENTS.md` against Phases 2/6 findings. Effort: **Small**.
- **P2-109** [Phase 7] **`collectors/docs/PBS_COLLECTORS_*PLAN.md` → `docs/archive/build-plans/collectors/`** (Phase 5 P2-75). Effort: **Tiny**. Depends on Q34.
- **P2-110** [Phase 7] **`collectors/README.md`** — fix line saying `STATUS_API_KEY=your_password` (it's an API key, not password). Effort: **Tiny**.
- **P2-111** [Phase 7] **`src/cli/README.md`** — fix `sam_search_cli_original.py` reference (file no longer exists). Phase 4 C10. Effort: **Tiny**.
- **P2-112** [Phase 7] **`docs/integration/NEXT_GID.md`** — promote to `docs/` root or group with `apis/`. Single-file subdirectory. Effort: **Tiny**.

#### Docs hygiene (Phase 1, retained for completeness)

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

### `[XC: prod-config-hardening]` — THE strongest theme

Fourteen separate findings across phases 2, 3, 5, and 6 follow the same shape: **when an expected condition doesn't hold, the code falls back to a working-but-permissive default instead of refusing to start.**

The full census:
- **Phase 2 (5):** `DISABLE_AUTH=1` honored in any `FLASK_CONFIG`; `AUTH_PROVIDER='stub'` not blocked by `ProductionConfig.validate()`; `RATELIMIT_STORAGE_URI` fallback to `memory://` per-pod under HA; `Base` dual-mode silent fallback in `sam/` and `system_status/` when Flask import fails despite `FLASK_ACTIVE=1`.
- **Phase 5 (4):** `verify=False` on the JupyterHub API call ("Matches existing behavior"); default `STATUS_API_URL=http://localhost:5050` with no warning when used against non-localhost; no SSH `BatchMode=yes` (cron falls back to interactive prompts on first-connect); collector Dockerfile `FROM python:3` unpinned.
- **Phase 6 (5):** Helm webapp Deployment has zero probes despite well-designed `/api/v1/health/{live,ready,db}` endpoints; webapp gunicorn runs as root in all 3 environments; TruffleHog pinned to `@main` on the deploy path; `deploy-staging` uses long-lived AWS keys with no approval gate; staging RDS `publicly_accessible=true`.

Each individual finding is defensible in isolation — "convenience for development," "the secret manager always works," "the cert was already trusted somewhere." Together they describe a default operating philosophy: **make the code keep running rather than refuse insecurely.** That philosophy is reasonable for a dev environment and dangerous in production. The cumulative effect is a system that quietly degrades into a less safe configuration when any component upstream of it fails or is misconfigured.

**Recommendation:** rather than fixing 14 things individually, adopt a single principled stance — *fail-closed in `ProductionConfig`; permissive defaults only in `DevelopmentConfig`* — and audit each footgun against it. This is one focused PR (a few hours of work) plus its corresponding config-class refactor. Resolution is binary: a startup error in production beats a silent compromise.

### `[XC: convention-drift]`

The conventions in `CLAUDE.md` are excellent and largely followed — but the divergences cluster in revealing places.

The **clearest pattern**: `is_active` discipline. `CLAUDE.md` §5 documents the universal `Model.is_active` hybrid and forbids raw column comparisons. The mixin is implemented correctly on every model that needs it. Yet *inside `Project` itself* (the class that defines the hybrid for projects) there are 4 sites using `cls.active == True` or `Allocation.deleted == False`; inside `User` itself there are 6. Plus one extra in `queries/statistics.py:89` that looks like an inadvertent extension of the documented exception. The pattern was migrated successfully but the migration didn't include the defining files.

Same shape elsewhere:
- **Form validation §9** — pattern documented; ~75 of 105 mutation routes use it; ~10 routes (wallclock-exemption, status-outage) hand-roll `datetime.strptime` + `float()` + `int()` coercion ladders. Two clusters likely pre-date the convention.
- **RBAC decorators §8** — `access_control.py` is well-designed; ~9 routes hand-roll `can_change_admin` / `can_manage_project_members` inline. Same anti-pattern across `project_members.py` (5 routes), `api/v1/projects.py` (1 route), `dashboards/user/blueprint.py` threshold routes (3 routes, one with NO access check at all).
- **CLI exit codes** — `EXIT_*` constants documented; `cli/accounting/commands.py` uses bare integer literals at 50+ sites; `EXIT_KEYBOARD_INTERRUPT` defined but never used.
- **Collectors** — `try/except ImportError` import-path dance in 4 modules suggests the `pyproject.toml` entry points are aspirational rather than installed.

**Recommendation:** one focused convention-cleanup PR per cluster (is_active, form-validation, RBAC, CLI exit codes). All Tiny-or-Small effort individually. These would also be excellent first-PRs for any new contributor.

### `[XC: docs-drift]`
*Synthesis pending.* Phase 7 inventoried the whole tree. Drift concentrates in 5 places: stale stats (`CONTRIBUTING.md`, `README.md`, `CLAUDE.md`), AI-collab residue in 3 locations (`src/webapp/{DESIGN,IMPLEMENTATION_SUMMARY,REFACTORING_PLAN}.md` + `docs/prompts/*` + `collectors/docs/PBS_COLLECTORS_*PLAN.md`), `INDEX.md` doesn't mention 5 subdirs, the 8-doc setup cluster, ops records (`docs/remediation/CESM0002_*`) checked into the repo. **Cheap to fix** — Phase 7 enumerates ~20 dispositions, almost all Tiny or Small, executable as one bundled docs-consolidation PR (~½ day).

### `[XC: a11y]`

Phase 2's template audit graded the 167-template tree by area. Base layout (C), forms (B−), tables (D), modals (B+), HTMX swaps (D), icons (C), color-only signaling (C). Bootstrap 5 + HTMX 2 give forms and modals most of what they need for free; the gaps are concentrated in the surfaces Bootstrap doesn't help with.

The three highest-ROI fixes total maybe a half-day:
1. **Skip-link + `<main>` landmark in `base.html`** (3-line change, biggest single keyboard-user win).
2. **`aria-live="polite"` + `aria-busy` toggling in `htmx-config.js`** — one event handler covers every HTMX swap site.
3. **Mechanical `scope="col"` pass** on the 10-15 `*_table.html` partials — sed-able, no visual change, real assistive-tech improvement.

Whether this matters depends on **Q13: is SAM web subject to Section 508 / WCAG 2.1 AA?** Internal NCAR tools historically aren't audited, but if university PIs (UNIV facility members) consume the dashboards, this likely should be. The "doesn't matter today" answer is just as actionable as the "we need to comply by Q3" answer.

### `[XC: ops]` — the second-strongest theme

**The system is built defensively where it counts and operationally unobservable where it matters.** The contrast is striking: RBAC machinery, audit-log `before_flush` listener, retry-with-backoff in the API client, span coalescer with outage guard, OIDC rotation runbook — all thoughtful, all defensive. But the moment any of these *fails*, the operator has essentially no way to know.

Concrete gaps:
- **No error ingestion** anywhere. No Sentry/Rollbar. A 500 in production lands in CloudWatch (staging) or pod stdout (k8s) and stops there. `DESIGN.md:422` lists this as a TODO; the TODO is still open.
- **Audit log non-durable in practice.** Written to `/var/log/sam/model_audit.log` inside the container; in ECS Fargate this is the container's writable layer, destroyed on redeploy. The audit log Phase 2 admired is operationally ephemeral.
- **No metrics endpoint or backend.** No `prometheus_client`, no `/metrics` route. Only "metric" is a hardcoded `elapsed_ms > 5000` warning.
- **No structured logging; `request_id` doesn't propagate.** Human format only; `g.request_id` embedded in exactly one log line. CloudWatch Insights / Loki structured queries impossible.
- **Helm Deployment has zero probes** despite well-designed `/api/v1/health/{live,ready,db}` endpoints. Compose probes them. ECS probes them. k8s does not.
- **Healthcheck failures invisible** — failing `/health` returns a regular `INFO 503` line, no CloudWatch alarms in `ecs.tf`.
- **Collector failures invisible** — zero-substitution exception handler makes "transient SSH hiccup" look identical to "system fully down" on the dashboard; no alerting on persistent failure; `run_collectors.sh:91` swallows collector stdout/stderr.

**Plus operational fragility:** audit log file-local with no off-host shipping; global `cache.clear()` on every commit (defeats LDAP-feed caches); `cleanup_status_data.py` not visibly scheduled in-repo; collector cron paths reference `benkirk`'s personal Glade directory; remediation logs checked into the code repo; HA limiter/caching not fully worked out.

**Recommendation: one focused "observability bring-up" PR.** Sentry SDK + DSN env var (P0); structured JSON logger + `LoggerAdapter` for `request_id` propagation (P1); wire Helm probes to the existing health endpoints (P0); promote slow-request warning to a Prometheus histogram (P1). The system was built ready for this — it just isn't connected yet.

### `[XC: testing]`

Test infrastructure for `src/sam/` and `src/webapp/` is **genuinely strong** — ~1,500+ tests, two-tier strategy (Layer 1 representative fixtures + Layer 2 factory builders) is cleanly applied, schema-drift tests promoted to assertive for FK existence + UNIQUE constraints, SQLite-per-worker isolation for the status tier is the right shape. This is one of the better-tested codebases I've audited.

But the layer is uneven where it matters most:
- **Collector subsystem has exactly one test file** for ~2,600 LOC. The reliability of the entire status tier depends on this code: `api_client` retry logic, `base_collector` exception handling, `ssh_utils` parallel collection, the JupyterHub statistics calculator, all 7 parsers — none have dedicated tests. The README's "Next Steps (Deferred)" is honest about it.
- **Two `test_column_types_match` / `test_database_columns_in_orm` schema-drift tests are informational-only** despite CLAUDE.md claiming they catch drift. The documented Boolean → BIT(1) historical bug would NOT be caught with current Boolean type-mapping.
- **Two documented query functions have zero direct coverage** (`get_projects_by_allocation_end_date`, `get_projects_with_expired_allocations`) despite being featured in CLAUDE.md with explicit return-shape tuples.
- **CLI tests cover exit codes 0 and 1 only** — never 2 (error) or 130 (KeyboardInterrupt). The KeyboardInterrupt handler doesn't exist yet (P1-27), so this pairs naturally.
- **CRUD tests construct ORM models directly** even where `create()` classmethods exist; the validation paths in `Allocation.create()` (`amount > 0`, etc.) are only exercised implicitly via the factory.

**Recommendation:** the testing gap that actually matters is the collector subsystem. That alone is ~Medium effort but eliminates the largest test-coverage risk in the audit. The other gaps are individually Tiny.

### `[XC: perf]`

No architectural performance problem; four localized issues, all fixable in well-scoped PRs.

The biggest is `AllocationWithUsageSchema` calling `_calculate_tree_usage` and `_calculate_usage` 4-8× per allocation dump (`get_used`, `get_remaining`, `get_percent_used`, `get_root_projcode`, `get_charges_by_type`, `get_adjustments`, `get_self_used`, `get_self_percent_used` each independently). That's 16-24 DB queries per allocation; on `GET /api/v1/projects/<projcode>/allocations` with `many=True`, ~N×20 round trips. A memoization pass keyed by `allocation_id` on the schema instance should be 5-10× speedup.

The others: `ProjectListSchema` triggers N queries for `lead`/`admin` on list dumps (N+1); `usage_cache` lacks invalidation hooks on writes (1-hour stale window after admin renew/extend/update); 3 unbounded `.all()` queries in `sam/queries/projects.py` and `users.py` (limit defaults missing).

**Recommendation:** the memoization PR alone is the single highest-leverage perf change. Worth doing before P0-1/2 (the silent-shipping schema bugs) if confidence in shipping touches the same file.

### `[XC: secrets]`

Phase 6's secret audit found **zero committed secrets in the scan** (clean), and the three-store discipline for prod (AWS SSM / OpenBao / Compose env) holds cleanly. Defense-in-depth scanning runs at 3 layers: TruffleHog (CI + deploy), GitGuardian (pre-push), `detect-private-key` (pre-commit). The OIDC rotation procedure (`docs/AUTHENTICATION.md:322-360`) is best-in-class — the kind of runbook every secret should have. `gen_api_key.py` uses `secrets.token_urlsafe(32)` (CSPRNG) and bcrypt rounds=12 default with a `--rounds 14` option for prod.

The gaps are documentation, not implementation:
- **No documented rotation procedure for `STATUS_API_KEY` or `JUPYTERHUB_API_TOKEN`.** Both are operator-tribal-knowledge.
- **TruffleHog pinned to `@main`** on the deploy path — covered under `[XC: prod-config-hardening]` but worth noting here too.
- **`.trivyignore`** has rationale comments but no expiry dates.
- **One bcrypt hash committed in `helm/values.yaml:61`** (the collector API key). Defensible (it's a hash, not a key) but inconsistent with the OIDC pattern of ExternalSecret-only.

**Recommendation:** model the STATUS_API_KEY + JH-token rotation runbooks on the OIDC procedure. ~1 hour of doc work each.

### `[XC: bus-factor]` — new theme, Phase 6

A meaningful chunk of prod-recoverability concentrates on one person. Three independent single-points-of-failure:
- Cron paths reference `/glade/work/benkirk/repos/sam-queries/collectors/cron_scripts` — if `benkirk` rotates / departs / their Glade quota gets cleared, collectors break silently (Phase 5 P1-37).
- `BENKIRK_GITHUB_TOKEN` is a personal PAT in 3 maintenance workflows (`clean-ghcr.yaml`, `cron-clean-action-log.yaml`, `manually-clean-action-log.yaml`). When the PAT rotates or `benkirk` leaves, log cleanup + GHCR pruning silently stop. The fallback to `GITHUB_TOKEN` exists in `clean-ghcr.yaml:29` but isn't the default (Phase 6 P1-46).
- `helm/values.yaml:31` image is `ghcr.io/benkirk/sam-queries/webapp:main` — namespaced under a personal GHCR account rather than an org/team namespace.

Combined with the missing STATUS_API_KEY rotation runbook (`[XC: secrets]`), the system is recoverable-by-`benkirk` more than recoverable-by-team. None of these are urgent; all worth a roll-up to org-namespace + team-shared credentials.

### `[XC: deploy]`

The three-environment topology (Compose / ECS / k8s) is the most architecturally interesting part of the deploy story, and it's largely well-executed: one shared `containers/webapp/Dockerfile` stage `production` is the artifact in all three environments; secret injection is correctly factored (env / SSM / OpenBao); cgroup-aware gunicorn worker sizing avoids the documented OOM. The Helm chart is clean; ExternalSecret integration is real and tested; `docs/README-k8s.md` is unusually good operator documentation.

The drift is at the edges:
- **Image tag `:main` + `imagePullPolicy: IfNotPresent`** is a non-updating combo in `values.yaml`. CI rewrites to `:sha-<short>` on the `cirrus` branch — so prod is mutable-tagged on `main`, immutable-tagged on `cirrus`. Two sources of truth.
- **ECS task pinned to `:latest`** while workflow pushes both `:<sha>` and `:latest`. `lifecycle.ignore_changes = [task_definition]` masks but `terraform apply` will drift back.
- **`Chart.yaml` version stuck at `0.0.1`** — never bumped.
- **Collector deployment not in Helm chart** despite the container image being built and pushed. Phase 5's "two parallel deployment models" surfaces here as concrete absence.
- **`helm/tests/test-oidc-render.sh` not invoked by CI** — the `dev-only-insecure-key` guard exists but isn't enforced.

**Recommendation:** decide collector deployment direction (CronJob via Helm, or stay on Glade and remove the unused image build) — Q42. The rest are individually Tiny and naturally cluster into a "deploy hygiene" PR alongside the prod-config-hardening work.

## Recommendations (sequencing)

154 register items is a flat punch list. Here's an opinionated order of attack — five bundled PRs, sequenced by leverage and risk.

### PR 1 — "Production-mode hardening" (highest leverage)

The single most impactful work. Picks up the **`[XC: prod-config-hardening]` theme** plus the two silent-shipping schema bugs. Defensible as one PR because every change tightens `ProductionConfig` or its boundaries; review surface is small and locally testable.

- **P0-1, P0-2** Fail-closed in `ProductionConfig.validate()`: reject `AUTH_PROVIDER='stub'`, reject `DISABLE_AUTH=1`. (Phase 2 H1, H2.)
- **P0-5, P0-6** Fix `ProjectListSchema.get_admin_username` empty body + `ProjectSchema.get_panel` orphan guard. (Phase 4 B1, B2.) These ship today silently null / 500-on-orphan; no reason to defer.
- **P0-9** Wire Helm `livenessProbe`/`readinessProbe` to the existing `/api/v1/health/*` endpoints. (Phase 6 D1.)
- **P0-13** Move staging RDS to private subnet, flip `skip_final_snapshot`. (Phase 6 D2.)
- **P1-21, P1-16** Make the silent `Base` Flask-fallback in `sam/base.py` and `system_status/base.py` log loudly or raise. (Phase 3 S2, Phase 4 A1.)
- **P1-15** Remove the stray `print(username@server/database)` at status-package import. (Phase 3 S1.)

**Effort: ~1 day.** Safety-net: every change has localized blast radius; rollback is one revert per finding.

### PR 2 — "Observability bring-up" (high leverage, gates incident response)

Picks up the **`[XC: ops]` theme.** Without it, the rest of the audit's work is invisible if something goes wrong. With it, every subsequent change has a feedback loop.

- **P0-14** Add `@app.errorhandler(Exception)` → Sentry SDK (or equivalent). Add `SENTRY_DSN` env var; off by default in dev. (Phase 6 O1.)
- **P0-15** Switch audit log handler from `RotatingFileHandler` to `StreamHandler(sys.stdout)` so the awslogs/cloudwatch driver picks it up. Or mount EFS / ship to S3. (Phase 6 O2.) Pairs with Phase 2 Q11.
- **P1-49** Structured JSON logger + `LoggerAdapter` that injects `request_id` everywhere. (Phase 6 O3/O4.)
- **P1-50** `prometheus_client` + `/metrics` route. Promote slow-request warning to a histogram. (Phase 6 O5.)
- **P1-55** Explicit `logger.error` path for healthcheck failures + CloudWatch alarm on 5xx rate in `ecs.tf`. (Phase 6 O6.)
- **P0-8, P1-36** Wire collector failures to alerting (healthchecks.io heartbeat is the easy path); fix `run_collectors.sh:91` stdout/stderr swallowing. (Phase 5 O2/O3.) Depends on Q33.
- **P0-7** Decide collector failure-mode UX (Q31) and fix the zero-substitution code accordingly. (Phase 5 O1.)

**Effort: ~2 days** depending on Sentry/metrics-backend procurement at NCAR. Two of these are P0; others move from P1 once the foundation lands.

### PR 3 — "Convention cleanup" (good first issue territory)

Picks up the **`[XC: convention-drift]` theme.** Mechanical work; minimal review burden; high pedagogical value as a reference PR for new contributors.

- **P1-25** 10+ `is_active` violations inside `Project` and `User`. Mechanical replace. (Phase 4 D1/D2/D3.)
- **P1-8** Wallclock-exemption routes — add `sam/schemas/forms/exemptions.py`, refactor 3 handlers. (Phase 2 F1.)
- **P1-9** Status-outage routes — add `sam/schemas/forms/outages.py`, refactor across HTMX + API. (Phase 2 F2/F3.)
- **P1-5, P1-6** Hand-rolled authz in `dashboards/project_members.py` (5 routes) + threshold routes (3 routes incl. one with NO access check). Add thin decorators. (Phase 2 M5/M6.)
- **P1-27** Add top-level `try/except KeyboardInterrupt` in `cmds/search.py` + `cmds/admin.py` (exit 130). (Phase 4 C3.)
- **P1-29** `Project.deactivate()` method, replace inline `project.active = False; session.commit()` in `ProjectExpirationCommand`. (Phase 4 C6.)
- **P1-31** `cli/accounting/commands.py` bare integers → `EXIT_*` constants. Pure sed. (Phase 4 C2.)
- **P1-28** Validation errors to `ctx.stderr_console` (corrupts JSON pipelines otherwise). (Phase 4 C5.)

**Effort: ~1 day.** None of these are urgent individually; bundled they make a single coherent "tighten the conventions" pass.

### PR 4 — "Docs consolidation" (cheap big wins)

Picks up the **`[XC: docs-drift]` theme** and the Phase 1/Phase 7 dispositions. ~10 docs deleted/merged; ~5 stub-redirects; new `docs/archive/`. Almost all `mv` and one-paragraph edits.

- **P2-93..P2-112** (20 items). All Tiny-or-Small. Sequence:
  1. Create `docs/archive/` with index README.
  2. Update `docs/INDEX.md` to reflect target IA.
  3. Move `src/webapp/IMPLEMENTATION_SUMMARY.md` to delete; `DESIGN.md` to `docs/archive/webapp-design.md`; `QUICK_START_RBAC.md` to `docs/TESTING_RBAC.md`.
  4. Reconcile `src/webapp/REFACTORING_PLAN.md` against current code (P2-104, depends on Q3); surviving items → `docs/plans/WEBAPP_REFACTORING_BACKLOG.md`.
  5. `docs/prompts/` → `docs/archive/build-prompts/`; `collectors/docs/PBS_COLLECTORS_*PLAN.md` → `docs/archive/build-plans/collectors/`.
  6. Merge `k8s.md` into `README-k8s.md`; move `CIRRUS-k8s-cmds.sh` to `scripts/`.
  7. Reduce setup cluster: `LOCAL_SETUP.md` canonical; `SETUP_SUMMARY.md`/`WEBAPP_SETUP.md`/`CREDENTIALS.md` become stubs; `SCRIPTS.md` + `SCRIPT_ORGANIZATION.md` merge into `scripts/README.md`.
  8. Refresh test counts in `CONTRIBUTING.md` + `CLAUDE.md`; trim `README.md`.
  9. Rename `docs/remediation/` to date-first; long-term: ship to Confluence (Q2).

**Effort: ~½ day.** Best done after PR 1-3 so any code-flagged docs (e.g. REFACTORING_PLAN items completed during PR 3) are reflected.

### PR 5 — "Architectural cleanup" (when time permits)

Picks up the items that don't fit the above PRs but are worth doing.

- **P1-42** Webapp container `USER` non-root + `securityContext: runAsNonRoot: true`. Pairs with P2-86 (drop `AVD-DS-0002` ignore). (Phase 6 D6.)
- **P1-22** `AllocationWithUsageSchema` memoization keyed by `allocation_id`. 5-10× speedup on `many=True`. (Phase 4 P1.) Depends on Q21.
- **P1-23** `usage_cache` invalidation hooks on write paths in `manage/allocations.py`, `manage/renew.py`, `manage/extend.py`. (Phase 4 P3.) Depends on Q22.
- **P1-38** Collector test coverage — `api_client` retry logic, `base_collector` exception handling, JupyterHub statistics calculator, parsers. Largest test-coverage risk in the audit. (Phase 5 T1.)
- **P1-43** STATUS_API_KEY + JH-token rotation runbooks modeled on AUTHENTICATION.md OIDC procedure. (Phase 6 SS1.) Depends on Q47.
- **P1-44** Python lockfile — `pip-compile` or `uv lock`. (Phase 6 SS2.) Depends on Q46.
- **P0-10, P0-11, P0-12** CI/CD deploy hardening: SHA-pin TruffleHog, switch to OIDC + IAM role for AWS, protect `cirrus` branch. (Phase 6 CI1/CI2/CI3.) These are P0 but typically require some org-side coordination — moving them to PR 5 reflects that practical reality.
- **A11y quick-win bundle** (P1-12, P1-13, P1-14) — depending on Q13 (compliance posture).

**Effort: ~3 days** total but parallelizable. None are blockers.

---

**Out-of-band:** the 14 cross-cutting questions for Ben (numbered in the next section) gate ~15 of the above items. Worth a 30-minute call once Ben has time to read this synthesis — most are yes/no / one-line decisions.

### What I would NOT do

A directional audit should also be clear about what doesn't need attention:

- **No architectural refactor** — the structure is sound. Avoid the temptation to split `Project` god-class or rework the schema tiers unless Ben specifically wants it (P2-39, P2-54 are P2 for a reason).
- **No "rewrite the collectors" instinct** — the architecture is correct; the operational posture is the issue. PR 2 fixes the visibility; PR 5 fills the test gap.
- **No CLAUDE.md overhaul** — refresh counts on next pass, but the doc is genuine project memory. Don't touch the structure.
- **No premature decommission of dual deploy paths** — until Q29/Q42 confirm canonical-vs-stale, both paths stay.

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

### From Phase 4 (ORM / CLI)
19. **`ProjectListSchema.get_admin_username` (P0-5)** — has `admin_username` been silently null in production list responses for a while, or recent regression?
20. **`Organization`/`Institution` `deleted` semantics (P1-20)** — should their `is_active` consider `deleted`? Right now a `deleted=True, active=True` row is "active." Bug or intentional?
21. **`AllocationWithUsageSchema` memoization (P1-22)** — would you accept a memoization pass keyed by `allocation_id` on the schema instance? Expected 5-10× speedup on `many=True`.
22. **`usage_cache` invalidation gap (P1-23)** — 1-hour stale window after admin UI renew/extend/update. Acceptable by design, or worth surgical hooks?
23. **`NestedSetMixin` concurrency (P2-40)** — are project/org tree mutations gated behind any app-level lock, or assumed admin-only / serialized?
24. **`Project` god-class refactor (P2-39)** — split batch charge methods into `sam/queries/charges.py`, or keep as Project classmethods for discoverability?
25. **`AccountingAdminCommand` inheritance break (P2-48)** — intentional (search/admin diverge enough that inheritance is wrong) or aspirational? Reconcile docs vs reality either way.
26. **`--validate` / `--reconcile` CLI placeholders (P2-49)** — awaiting real logic, or hide from `--help` until ready?
27. **Edit-form checkbox semantics (P2-53)** — is "unchecked = deactivate" the intended contract for the 7 Edit forms that don't use `partial=True`?
28. **Informational-only schema-drift tests (P1-32)** — intentional human diagnostic, or should they fail? CLAUDE.md overstates strictness today.

### From Phase 5 (collector)
29. **Canonical production deployment** — the container in `containers/collectors/` or the cron-from-`benkirk`'s-Glade setup? Decides which has stale ops surface.
30. **`verify=False` on JupyterHub API (P1-39)** — self-signed cert / NCAR CA / quick patch from a historical issue?
31. **Zero-substitution failure mode (P0-7)** — is "show all zeros on the dashboard when collection fails" the intended UX, or should the dashboard surface "stale" / "collection failed"? Fix differs based on intent.
32. **`/glade/work/benkirk/repos/...` in cron (P1-37)** — is this prod, dev, or transitional?
33. **Alerting on persistent failure (P0-8)** — what does NCAR ops use for "service has been failing 30 min"? Healthchecks.io heartbeat? Slack webhook? Pagerduty?
34. **`collectors/docs/PBS_COLLECTORS_*PLAN.md` (P2-75)** — implementation-plan docs from build phase. Archive, delete, or keep as historical?
35. **`STATUS_API_URL` HTTPS in prod (P1-40)** — does production set HTTPS, or is the cleartext HTTP path live? Worth a startup warning either way.

### From Phase 6 (platform / cross-cutting)
36. **CIRRUS k8s probes (P0-9)** — was the missing `livenessProbe`/`readinessProbe` intentional (ingress does its own?) or just an oversight? The endpoints exist and are well-designed.
37. **Long-lived AWS keys (P0-11)** — why not OIDC + IAM role-to-assume? Migration is ~1 hour. Any CISL-side constraint (AWS account doesn't trust GitHub's OIDC issuer)?
38. **`cirrus` branch protection (P0-12)** — is it protected server-side? The force-push works either because protection is off or `github-actions[bot]` bypasses it.
39. **`BENKIRK_GITHUB_TOKEN` ownership (P1-46)** — if you're hit by a bus, does anyone else have PAT scopes to keep GHCR/log cleanup running?
40. **Staging RDS `publicly_accessible=true` (P0-13)** — intentional (for VPN-based queries via `query-staging-db.sh`)? If so, worth a comment; if not, moving to private subnets is a 5-line change.
41. **Image tag in CIRRUS today (P2-78)** — `:main` (per `values.yaml`) or the CI-rewritten `:sha-<short>` on `cirrus`? Determines whether this is a doc fix or a real issue.
42. **Collector deployment plan (P1-53)** — container CronJob via Helm, or stay on Glade cron? The container is built and published but never used in Helm.
43. **TruffleHog `@main` (P0-10)** — any objection to SHA-pinning with Renovate/Dependabot to keep it current?
44. **Staging error ingestion (P0-14)** — CloudWatch catches gunicorn stderr; is anyone alerting on `ERROR`-level lines, or is it diagnostic-on-demand?
45. **Audit log durability (P0-15)** — is the ephemeral `/var/log/sam/model_audit.log` accepted (stdout-via-CloudWatch covers most needs), or worth fixing? Pairs with Phase 2 Q11.
46. **Lockfile policy (P1-44)** — deliberate reason no `conda-lock.yml` or `pip-compile` artifact in-repo?
47. **`STATUS_API_KEY` rotation runbook (P1-43)** — would you welcome an issue spec'd up to mirror the AUTHENTICATION.md OIDC pattern for the collector key pair + JH token?
48. **MEDIUM-severity dep/IaC scanning (P1-48)** — Mega-linter + Trivy configured to ignore MEDIUM. Intentional noise reduction, or worth re-enabling with a documented `.trivyignore`?
49. **`switch_to_local_db.sh` sed mismatch (P1-51)** — is the canonical `.env` in current developer use actually based on `.env.example`, or do you maintain a different template internally that uses raw `SAM_DB_USERNAME=root` lines?

## Reviewer notes

### What this audit is, and isn't

**This is a directional review** — high-confidence callouts and a punch list, not a file-by-file audit. Roughly 3 days of focused reading across ~30,000 LOC of Python + Helm + Terraform + YAML, supplemented by parallel agent-based deep-dives along independent verticals (CI/CD vs auth vs RBAC vs schemas, etc.). Each agent received a focused prompt with explicit scope boundaries to avoid duplicated work. Every finding cites `file:line` so Ben can verify rather than trust.

**It is NOT:**
- A formal threat model. No STRIDE / attack-tree analysis; no penetration testing.
- A code-quality enforcement pass. Style nits, micro-optimizations, refactoring opportunities are deliberately omitted.
- An exhaustive test of all 154 findings against current `main`. Several depend on quick fixes Ben may have already merged since the base commit (`b166d9b`).
- A performance profile. The four `[XC: perf]` findings are pattern-detected; only the `AllocationWithUsageSchema` fanout was measured (queries-per-allocation count). No load test was run.

### What I didn't get to

- **Notebooks under `src/notebooks/`** — referenced in the system map; not audited.
- **The full Flask-Admin model-view surface.** Phase 2 flagged the headline `is_accessible()` issue (any authenticated user reads everything); the per-model-view configs weren't enumerated.
- **The `etc/` directory beyond `config_env.sh`.**
- **The `utils/` top-level directory.** Some scripts surfaced through other phases (`sample_collector_commands.sh`, etc.) but not a directed pass.
- **The audit log's `events.py` filter logic** beyond confirming the documented exclusions are correct.
- **Charge-summary upsert paths in `sam/manage/summaries.py`** — surfaced as a strength in Phase 4 but not deeply audited.
- **`docs/presentations/`** — explicitly out of scope per Phase 7 (self-contained subtree).

### Depth caveats by phase

| Phase | Depth | What that means |
|---|---|---|
| 1 Orientation | High | Every CLAUDE.md spot-check verified. |
| 2 Web | Medium-high | 5 parallel deep-dives; sampled but did not enumerate all 167 templates or all 105 mutation routes. |
| 3 Status | High | Small subsystem; direct end-to-end reading. |
| 4 ORM/CLI | Medium-high | 5 parallel deep-dives; ORM models sampled per domain, not every class read. |
| 5 Collector | High | Small subsystem; direct end-to-end reading. |
| 6 Platform | Medium-high | 4 parallel deep-dives; Helm chart + Terraform read; CI workflows mostly read. |
| 7 Docs | High | Whole tree enumerated; no code re-reading. |

### What I'd recommend doing if budget reopens

If the team wants a follow-up:
1. **Threat model the `/api/v1/*` surface.** Pair with Phase 2's RBAC findings as the starting point. Half-day exercise.
2. **Load test the dashboard `many=True` endpoints** before and after the `AllocationWithUsageSchema` memoization (PR 5).
3. **Run a `sam-search`/`sam-admin` UX session with a non-CISL operator** to surface CLI ergonomics gaps. The placeholder `--validate` / `--reconcile` commands (Phase 4 C7) would likely surface immediately.

### Disposition

This branch (`audit/dvance-2026-05`) is for review notes only. It should not merge. Findings are best landed as the 5 bundled PRs described above; any direct cherry-pick from the audit branch should go through `main` PRs with their own review.

The synthesis (this file) is the deliverable. The per-phase docs are appendix for "show me the file:line" — Ben should be able to read this section + the executive summary + the action register + sequencing in about 20 minutes, drilling down only when a specific finding catches his eye.

---

*— dv, 2026-05-21*

