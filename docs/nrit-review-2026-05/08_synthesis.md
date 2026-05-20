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
*Synthesis pending.* **Now 9 footguns following the same fall-back-instead-of-refuse pattern.** Original 5 (DISABLE_AUTH, AUTH_PROVIDER=stub, RATELIMIT_STORAGE_URI, `system_status/base.py` Flask-fallback, `sam/base.py` Flask-fallback), plus 4 collector additions (`verify=False` on JupyterHub API, default `STATUS_API_URL=http://`, no SSH `BatchMode=yes`, `FROM python:3` unpinned). **This is firmly the strongest cross-cutting theme of the audit.** Deserves a single principled stance: fail-closed unless explicitly enabled.

### `[XC: convention-drift]`
*Synthesis pending.* Currently: hand-rolled authz in ~9 routes; inline coercion in ~10 mutation routes; ~10 `is_active` violations inside the very models that define the canonical hybrid; 4 pure-wrapper functions in `queries/lookups.py`; bare integer exit codes in `cli/accounting/`; inline date coercion in 3 CLI sites; stray `print()` at status-package import; shell-string SSH commands with f-string interpolation under `shell=True` in collectors; `try/except ImportError` import-path dance in 4 collector modules. Pattern is right and well-documented; coverage is incomplete.

### `[XC: docs-drift]`
*Synthesis pending.* Currently: stale stats in `CONTRIBUTING.md` + `README.md`, stale API section in `src/webapp/README.md`, AI-collab residue in `src/webapp/{DESIGN,IMPLEMENTATION_SUMMARY,REFACTORING_PLAN}.md`, overlap clusters in `docs/` setup + k8s docs.

### `[XC: a11y]`
*Synthesis pending.* Currently: systemic table + HTMX-swap + landmark gaps; forms/modals okay. Quick-win bundle (P1-12, P1-13, P1-14) gets the most ROI.

### `[XC: ops]`
*Synthesis pending.* **Biggest concentration of operational risk in the audit.** Audit log file-local with no off-host shipping; global cache invalidation on every commit; remediation logs checked into repo; HA limiter/caching story not fully worked out; `cleanup_status_data.py` not visibly scheduled in-repo; **collector zero-substitution failure mode misrepresents downtime; no alerting on persistent collector failure; cron paths reference `benkirk`'s personal Glade dir; collector stdout/stderr swallowed by wrapper; Dockerfile vs cron deployment ambiguity.** Phase 5's findings here outweigh all prior phases combined.

### `[XC: testing]`
*Synthesis pending.* Test infrastructure for `src/sam/` and `src/webapp/` is strong (~1,500+ tests, two-tier strategy is genuinely clean, schema-drift tests partially assertive). **But:** 2 schema-drift tests are informational-only despite CLAUDE.md claiming they catch drift; 2 documented query functions have zero coverage; CLI tests don't exercise exit codes 2 or 130; CRUD tests still construct ORM models directly where `create()` classmethods exist; **and collector subsystem (~2,600 LOC) has exactly one test file.** The reliability of the entire status tier depends on collector code that is largely untested. The README is honest about the gap; that doesn't close it.

### `[XC: perf]`
*Synthesis pending.* Currently: `AllocationWithUsageSchema` N×20 fanout (4-8× recomputation per allocation in `many=True`); `ProjectListSchema` N+1 on `lead`/`admin`; `usage_cache` lacks invalidation hooks on writes; 3 unbounded `.all()` queries in `sam/queries/`. None require architectural change — all are localized.

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

## Reviewer notes

*Composed at end. Caveats about scope (1-2 day directional), depth (no exhaustive testing, no formal threat model, no perf profiling), what we didn't get to (e.g. notebooks, full Flask-Admin model-view audit beyond the headline finding).*
