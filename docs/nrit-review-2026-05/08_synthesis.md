# Phase 8 — Synthesis & Punch List

> The audit's headline deliverable — readable top-to-bottom in 15-20 minutes.
>
> **Companion files** (read them only when you need the detail):
> - [`08_action_register.md`](08_action_register.md) — the full 182-item punch list, severity-grouped with effort estimates. Browse by area, find a specific finding by ID.
> - [`08_strengths.md`](08_strengths.md) — the running "what's working well" list (54 entries across all phases). Useful to surface in conversation with Ben so the audit doesn't feel like a hit piece.
> - The per-phase docs (`01_orientation.md` … `07_docs.md`) — `file:line` context for any specific finding.

## Executive summary

A friendly external review of `sam-queries` at Ben's request: ~3 days of directional reading across `src/webapp/`, `src/system_status/`, `src/sam/`, `src/cli/`, `collectors/`, the platform layer (CI/CD, deploy, secrets, supply chain, observability), and the documentation tree. The MySQL schema itself was out of scope; the ORM follows it by design. Findings are calibrated to "what's most worth knowing," not "everything I noticed."

**The architecture is competent and the conventions are excellent.** `CLAUDE.md` is genuine project memory rather than aspirational documentation — every spot-check held up. The ORM layer (universal `is_active` hybrid, write-op co-location, two-tier test strategy) and the `system_status/` subsystem (lookup-resolver `before_flush`, span coalescer with outage guard, exemplary Alembic runbooks) are the strongest engineered surfaces I encountered. The Web team's authz primitives (`access_control.py` decorator family, three-tier RBAC), schema family (`HtmxFormSchema`, `AllocationWithUsageSchema`), and CI/CD posture (TruffleHog gating, concurrency groups, asymmetric skip-CI) all reflect a team that's been bitten by problems and wrote down the lessons. 54 distinct strengths called out across the audit.

**Where things drift, two patterns dominate.** First and most consequential: **the project is configured to fail open at the boundaries where ops would notice.** Fourteen separate footguns gate on optional env vars or fall back silently when an expected condition isn't met — `DISABLE_AUTH=1` honored in production, `AUTH_PROVIDER='stub'` allowed in `ProductionConfig`, Helm Deployment with no probes despite well-designed health endpoints, collector exception handler that substitutes all-zeros for node counts, staging RDS `publicly_accessible=true`, `verify=False` on the JupyterHub API, `FROM python:3` unpinned, …. Same shape every time. **This is the single most actionable observation of the audit and worth a principled "fail-closed unless explicitly enabled" stance.** Second: convention drift inside the very modules that define the conventions — 10+ raw `Model.active == True` comparisons inside `Project` and `User` themselves, hand-rolled access checks in ~9 routes where canonical decorators exist, inline date coercion in ~10 mutation routes despite the form-schema pattern being documented.

**Two concrete production bugs surfaced** that warrant immediate attention regardless of everything else: `ProjectListSchema.get_admin_username` has an empty function body and silently returns `null` for every project in every list response, and `ProjectSchema.get_panel` raises 500 on orphan projects (the documented `allocation_type=None` case). Both Tiny fixes; both possibly already shipping.

**The operational posture is the biggest gap.** No error ingestion (no Sentry); audit log written to ephemeral container path with no off-host shipping; no metrics endpoint or backend; healthcheck failures invisible; collector failures invisible. The system is observable only via log-grep against a partly-ephemeral destination. Combined with three independent single-points-of-failure on `benkirk` (Glade cron path, personal PAT in 3 maintenance workflows, `ghcr.io/benkirk/sam-queries` image), recovery from a real incident would be much harder than it should be given the quality of the underlying code.

**182 register items (15 P0 / 55 P1 / 112 P2) resolve to roughly one week of focused work**, best organized as 5 bundled PRs (sequencing below). The audit found nothing structurally wrong; the findings are mostly composable, well-scoped fixes that don't require architectural change. The headline take is upbeat with one significant operational reservation.

## What's working well

54 distinct strengths called out across the 8 phases. The greatest hits, by theme:

- **Project memory & documentation** — `CLAUDE.md` is the gold standard (every spot-check passed); migration runbooks are exemplary; `docs/AUTHENTICATION.md` is the best-in-class secret-rotation procedure; `docs/README-k8s.md` is unusually good operator documentation; `docs/plans/implemented/` is a model archive pattern.
- **`src/sam/` ORM & schemas** — universal `is_active` hybrid + 5 well-named mixins; write-op `update()`/`create()` co-location is real (zero standalone helpers remain); `fmt.py` matches the documented API exactly; `HtmxFormSchema._strip_empty_strings` centralizes the §9 form-validation pre-process.
- **`src/system_status/`** — the strongest engineered subsystem in the audit: `before_flush` lookup-resolver, `UserProjQueueStatus` span coalescer with outage guard, `URL.render_as_string(hide_password=False)` defensive trick, dual-mode `StatusBase` resolution, SQLite-per-worker test isolation.
- **Web app (`src/webapp/`)** — `access_control.py` decorator family is well-shaped; three-tier RBAC documented and applied; auth basics solid (PKCE S256, bcrypt + timing-safe `checkpw`, hardened cookies); `user_aware_cache_key()` correctly scoped; audit log `before_flush` listener covers all ORM writes; 429 errorhandler content-negotiated.
- **Tests** — ~1,500+ tests; two-tier strategy (representative fixtures + factory builders) is genuinely clean; schema-drift tests promoted to assertive for FK + UNIQUE.
- **Collector (`collectors/`)** — `TZ=UTC` enforced with 25-line README justification; retry-with-backoff correctly skips non-retriable codes; parallel SSH ~10× speedup; `flock -xn` prevents overlapping cron runs.
- **Platform layer** — three-tier secret injection (SSM/OpenBao/Compose) tested via `test-oidc-render.sh`; three environments share one Dockerfile target; cgroup-aware gunicorn worker sizing; CI concurrency groups everywhere; explicit (asymmetric) skip-CI semantics; LFS for dev DB dump with obfuscation pipeline.
- **CLI (`src/cli/`)** — builder pattern cleanly separates ORM-to-dict extraction from rendering (same dict feeds Rich and JSON); custom typed exception hierarchy used consistently.

The full annotated list lives in [`08_strengths.md`](08_strengths.md). The audit is not a hit piece — these strengths are doing real work and worth surfacing in conversation with Ben.

## Action register

**182 items total: 15 P0 / 55 P1 / 112 P2.** Severity-grouped with one-line fix sketches and effort estimates (Tiny / Small / Medium / Large). Full register lives in [`08_action_register.md`](08_action_register.md) — read it when you need the details on a specific finding or want to browse a particular area.

The shape of it, at a glance:

| Tier | Count | Character |
|---|---:|---|
| **P0** — production risk (security / availability) | 15 | Mostly Tiny/Small. Two silent-shipping schema bugs (`P0-5/6`), seven CI/CD + deploy hardening items, three auth-config items, two collector-ops items, one error-ingestion gap. Most are 1-hour fixes once Ben confirms intent. |
| **P1** — significant; act this quarter | 55 | Grouped into 13 thematic clusters: auth hardening (7), form-validation cleanup (2), audit/ops (2), a11y quick wins (3), status-tier polish (3), ORM correctness (4), schema performance (3), `is_active` discipline (2), CLI hardening (5), test gaps (3), collector subsystem (7), platform/CI/deploy (14). Most are Small effort; the largest single one is collector test coverage (Medium-Large). |
| **P2** — worth fixing; act eventually | 112 | Mostly Tiny. Dominated by docs hygiene (~28 items, all bundleable into one PR), CLI polish, schema/test/ORM polish, platform polish, a11y polish. Dead code, naming nits, dormant config, low-impact convention drift. |

**Cluster sizes by area:**

- Web app (`src/webapp/`) — 46 items (mostly P1 auth/RBAC/form-validation + P2 a11y polish)
- ORM/CLI (`src/sam/` + `src/cli/`) — 49 items (the broadest area — schema bugs, perf, conventions, schemas)
- Platform (CI/CD/deploy/secrets/observability) — 52 items (P0/P1 heavy — most of the prod-config-hardening footguns)
- Docs hygiene (`docs/` + in-tree) — 21 items (almost entirely Tiny; all P2; one bundled PR)
- Collector (`collectors/`) — 14 items (4 P1, 10 P2)
- Status tier (`src/system_status/`) — 8 items (3 P1, 5 P2)

The full register text is intentionally verbose so each finding stands on its own when grep'd. **Don't read it front-to-back** — read the executive summary above + the sequencing below, and dip into the register only when a specific finding catches your eye or you need to look something up by ID.

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

