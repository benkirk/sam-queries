# Phase 1 — Orientation & Doc-Drift

> Trust-calibration pass. Builds a one-page system map and scores top-level docs against reality so later phases know how much to trust each source.

## Scope

- `README.md`, `CLAUDE.md` (+ `GEMINI.md` symlink), `CONTRIBUTING.md`
- `docs/` tree (root files, `plans/`, `remediation/`, `integration/`, `prompts/`)
- `src/webapp/` in-tree design docs (`DESIGN.md`, `IMPLEMENTATION_SUMMARY.md`, `QUICK_START_RBAC.md`, `REFACTORING_PLAN.md`, `README.md`)
- `tests/docs/README.md`

## Method

Three parallel passes:
1. Spot-check factual claims in top-level docs against actual code (model counts, test counts, API routes, decorator names, script behaviors).
2. Per-file summary + accuracy hunch + overlap detection across the `docs/` tree.
3. Disposition assessment for the AI-collab residue inside `src/webapp/`.

Synthesized into the one-page system map and doc-drift register below.

## Lenses applied

- Architecture (system map only)
- Operability (does the documented onboarding actually work?)

---

## Findings

### Headline

**`CLAUDE.md` is the most trustworthy long-form doc in the repo** — virtually every factual claim spot-checked against current code: API routes exist, decorators exist, form schemas exist, ORM model classes exist where claimed. Use it as the reference for downstream phases.

`README.md` and `CONTRIBUTING.md` are largely accurate on *prose* but carry stale *numbers*. The `docs/` tree is mostly current but has a setup-doc overlap cluster and a k8s-doc overlap cluster. The `src/webapp/` in-tree design docs are a textbook case of AI-collab artifacts left in place — half are useful, half are misleading.

### Doc-drift register

| Doc | Status | Notes |
|---|---|---|
| `CLAUDE.md` | ✅ Current | Every spot-check passed. "91+ ORM models" is conservative (actual ~106); "~1,400 tests" is approximately right (actual 1,750). Treat this as the authoritative reference. |
| `README.md` | ⚠️ Mostly current | Setup flow (`install_local.sh` → `setup_local_db.sh` → `test_database.sh`) is real and accurate. Test counts cited as both "~1,400" and "380+" in different sections — internally inconsistent. API section omits newer endpoints (`charges`, `directory_access`, `fstree_access`, `health`, `project_access`, `status`). |
| `CONTRIBUTING.md` | ⚠️ Stats stale | Claims "380+ tests, 77.47% coverage" — actual test count is **1,750**, ~4.6× off. Other technical claims (setup, git, code style, ORM patterns) verified fine. The numerical claims appear to predate a major test-suite expansion. |
| `docs/INDEX.md` | ✅ Current | Good scaffolding; covers what's there. |
| `docs/SETUP_SUMMARY.md` | ✅ Current | Heavy overlap with `LOCAL_SETUP.md`. |
| `docs/LOCAL_SETUP.md` | ✅ Current | The comprehensive setup doc. Overlap with SETUP_SUMMARY, CREDENTIALS, DATABASE_SWITCHING. |
| `docs/WEBAPP_SETUP.md` | ✅ Current | Subset of LOCAL_SETUP. Unique parts: dev auto-login + Flask debug. |
| `docs/GETTING_STARTED.md` | ✅ Current | Big technology-stack primer; non-overlapping content for new devs. |
| `docs/CREDENTIALS.md` | ✅ Current | Overlaps LOCAL_SETUP §2. |
| `docs/AUTHENTICATION.md` | ✅ Probably current | OIDC flow, environment matrices, rotation procedures. Standalone. |
| `docs/STAGING.md` | ✅ Current | AWS staging + GitHub Actions → ECS + Terraform. |
| `docs/DATABASE_SWITCHING.md` | ✅ Current | Wrapper around `scripts/setup/switch_to_*_db.sh`. |
| `docs/SCRIPTS.md` | ✅ Current | Mirrors SETUP_SUMMARY table. |
| `docs/SCRIPT_ORGANIZATION.md` | ✅ Current | Rationale doc; complements SCRIPTS.md. |
| `docs/TESTING.md` | ✅ Current | Cites "~1,400 tests" and "67s parallel" — most accurate test claim in the repo. |
| `docs/DOCKER_TROUBLESHOOTING.md` | ✅ Current | Unique content. |
| `docs/CIRRUS-k8s-cmds.sh` | ⚠️ Odd home | A `.sh` file inside `docs/` — duplicates `k8s.md` §1-2. |
| `docs/k8s.md` | ⚠️ Overlap | Cheat sheet that overlaps both CIRRUS-k8s-cmds.sh and README-k8s.md. |
| `docs/README-k8s.md` | ✅ Current | The practical Helm deploy guide. |
| `src/webapp/README.md` | ⚠️ Partially stale | Quick-start (1–5) solid. API docs (line 265+) omit `charges`, `allocations`, `directory_access`, `fstree_access`, `health`, `project_access`, `status`. References "future Marshmallow" which is now done. |
| `src/webapp/DESIGN.md` | ❌ Stale (Dec 2025) | Describes the REST API as "not yet built." Directory listing misses `caching/`, `audit/`, `limiter/`, `config_inspect.py`, `api_auth.py`, `project_permissions.py`. Evergreen rationale ("why permission-based not role-based") is still valuable. |
| `src/webapp/IMPLEMENTATION_SUMMARY.md` | ❌ Pure scaffolding | Mid-sprint Claude work-log. "What's Next" treats shipped work as TODO. Net-misleading. |
| `src/webapp/QUICK_START_RBAC.md` | ✅ Fresh + useful | Documents the dual-layer permission model (group bundles + per-user overrides) and how to test roles locally. The doc to point new engineers at. |
| `src/webapp/REFACTORING_PLAN.md` | ⚠️ Stale aspirational | Backlog from 2025-12-04: centralize charges API queries, abstract status queries, consolidate Marshmallow schemas. Whether items are done needs spot-checks in later phases. |

### One-page system map

```
sam-queries — system map (b166d9b, 2026-05-14)
─────────────────────────────────────────────────────────────────────

  USER SURFACES
    • CLI: sam-search, sam-admin            src/cli/, src/sam_search_cli.py
    • Web UI: Flask-Admin + HTMX            src/webapp/
    • REST API: /api/v1/*                   src/webapp/api/v1/
    • Notebooks                             src/notebooks/

                              │
                              ▼

  DOMAIN CORE                                src/sam/
    • ORM models (~106 classes)               core, resources, projects,
                                              accounting, activity,
                                              summaries, integration,
                                              security, operational
    • Marshmallow schemas (3-tier + forms)    src/sam/schemas/
    • Query helpers                           src/sam/queries/
    • Write/audit ops                         src/sam/manage/
    • Display formatting                      src/sam/fmt.py

                              │
                              ▼

  DATA LAYER
    • MySQL  `sam` (97 tables)              primary
    • SQLite `system_status` per-worker     dashboards + tests
    • Alembic migrations (system_status only)
    • bcrypt API credentials

                              ▲
                              │

  INGESTION
    • collectors/  (own pyproject)          Casper, Derecho, JupyterHub
                                              cron-driven, containerized
    • LDAP populator, XRAS integration views

═════════════════════════════════════════════════════════════════════

  PLATFORM (cross-cutting — Phase 6 territory)
    • Local      Makefile + conda-env.yaml + compose.yaml
    • Bootstrap  install.sh (curl|bash), install_local.sh
    • CI/CD      .github/workflows/ (10 workflows)
    • Deploy     Helm chart → CIRRUS k8s; GitHub Actions → staging (ECS)
    • Auth       OIDC (Microsoft Entra) + RBAC (group bundles + overrides)
    • Secrets    .env + helm/local-secrets.sh
    • Supply     Trivy + .trivyignore, pre-commit, mega-linter, jscpd
    • Logging    src/webapp/logging_config.py
```

### Subsystem entry points (for later phases)

- **Web** — `src/webapp/run.py` → `__init__.create_app()` → `extensions.py` + blueprints under `admin/`, `api/`, `audit/`, `auth/`, `caching/`, `dashboards/`, `limiter/`
- **Status** — `src/system_status/base.py` (bind-routed via `__bind_key__`), migrations at `migrations/system_status/`
- **ORM/CLI** — `src/sam/__init__.py` exports; CLI entry points at `src/cli/cmds/{search,admin}.py`
- **Collector** — `collectors/run_collectors.sh`, per-source dirs (`casper/`, `derecho/`, `jupyterhub/`)
- **Platform** — `Makefile`, `install.sh`, `compose.yaml`, `helm/Chart.yaml`, `.github/workflows/*.yaml`

### AI-collab residue pattern (notable, recurring)

`src/webapp/` carries five doc files that together illustrate the most common AI-collab pattern in this repo: aspirational design docs and mid-sprint summaries from the build phase live alongside current operational guides. Net effect on a new engineer reading the tree top-to-bottom: confusion about what's actually shipped.

Recommendation (held for Phase 7 synthesis):

| File | Disposition |
|---|---|
| `QUICK_START_RBAC.md` | **Promote** to `docs/TESTING_RBAC.md` (or similar) — useful and current |
| `README.md` | **Trim** — keep quick-start; archive API section to `docs/archive/` |
| `DESIGN.md` | **Archive** to `docs/archive/` — preserve evergreen rationale, drop stale architecture diagram |
| `REFACTORING_PLAN.md` | **Verify, then move** — confirm what's done; surviving items move to a single backlog doc |
| `IMPLEMENTATION_SUMMARY.md` | **Delete** — pure scaffolding; net-misleading |

Same pattern likely repeats elsewhere; flag any similar artifacts in later phases.

---

## Cross-cutting tags raised

- `[XC: docs-drift]` — Stale test counts in CONTRIBUTING.md and `README.md` (380+ → actual 1,750). Coverage figure (77.47%) is unverifiable in current state.
- `[XC: docs-drift]` — `src/webapp/DESIGN.md` and `IMPLEMENTATION_SUMMARY.md` describe pre-implementation state as current.
- `[XC: docs-drift]` — `README.md` API section omits ~7 newer endpoint modules.
- `[XC: ops]` — `docs/remediation/CESM0002_*` files are operational incident records checked into the repo; no clear archive policy.
- `[XC: ops]` — `docs/prompts/` contains AI-collab prompt artifacts (auditing.md, populator prompts); not harmful but unusual repo content.

## Open questions for Ben

1. **CONTRIBUTING.md test stats** — is the "380+ tests / 77.47% coverage" figure intentionally pinned (i.e., do you treat it as a soft floor) or just stale? If stale, can you `pytest --cov` and update?
2. **Remediation log home** — is there an off-repo destination (Jira, Confluence, Wiki) you'd prefer for files like `docs/remediation/CESM0002_*`? Checking them into the main repo works but ages poorly.
3. **`src/webapp/REFACTORING_PLAN.md`** — is the charges-API centralization (Priority 1.1) actively scheduled, backlog, or shelved? Drives whether to archive or surface it.
4. **`POSTGRES_MIGRATION.md`** in `docs/plans/` — still planned, paused, or shelved? Affects how seriously Phase 4 should consider the current MySQL ORM as the long-term target.
5. **`docs/prompts/`** — intentional artifact (so future Claude/Gemini sessions have context) or residue? Either is fine; just want to know.

## Trust calibration for downstream phases

- **High trust** — `CLAUDE.md`, `docs/AUTHENTICATION.md`, `docs/TESTING.md`, `docs/STAGING.md`, `docs/DATABASE_SWITCHING.md`, `src/webapp/QUICK_START_RBAC.md`
- **Verify before quoting** — `README.md` (especially API section), `CONTRIBUTING.md` (numerical claims), `src/webapp/README.md`
- **Treat as historical** — `src/webapp/DESIGN.md`, `IMPLEMENTATION_SUMMARY.md`, `REFACTORING_PLAN.md`, anything in `docs/plans/implemented/`
