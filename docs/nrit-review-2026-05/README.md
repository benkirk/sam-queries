# NRIT Web Team — Directional Review of sam-queries

**Reviewer:** David Vance (dvance@ucar.edu), NRIT Web Team lead
**Authored at the request of:** Ben Kirk (CISL USS), primary author of sam-queries
**Date started:** 2026-05-14
**Base commit:** `b166d9b` (current `main`)
**Branch:** `audit/dvance-2026-05` — do not merge

---

## Purpose

A friendly, external review of sam-queries to surface findings Ben can act on. Focus on **what's most worth knowing** for a project that has been largely AI-collaborated and is now production-adjacent (financial-leaning charging/allocation data).

This is a **directional** review (~1–2 days of effort), not an exhaustive audit. Expect high-confidence callouts and a punch list, not file-by-file commentary.

## Scope

**In scope** — every subsystem of the working tree:

- `src/webapp/` — Flask web app (OIDC, RBAC, HTMX, API, audit log, rate limiter)
- `src/system_status/` — SQLite-bound status DB tier
- `src/sam/`, `src/cli/`, `src/sam_search_cli.py` — ORM + CLI (`sam-search`, `sam-admin`)
- `collectors/` — sibling subproject (Casper/Derecho/JupyterHub usage ingest)
- Platform layer — deployment paths (Makefile, install scripts, compose, helm, infrastructure), CI/CD, secrets, supply chain, observability
- Documentation hygiene — README, CLAUDE.md, `docs/`, in-tree design docs

**Out of scope:**

- The MySQL schema itself (97 tables; CISL-owned; the ORM follows the DB by design).
- Any change to source files — this branch only adds review notes under `docs/nrit-review-2026-05/`.

## Conventions

### Severity

| Tag | Meaning | Example |
|---|---|---|
| **P0** | Production risk; act soon | Auth bypass, plaintext secret in repo, data corruption path |
| **P1** | Significant; act this quarter | Missing authz on a route, undefined error path, dependency CVE w/ upgrade available |
| **P2** | Worth fixing; act eventually | Doc drift, dead code, naming inconsistencies, minor a11y nits |
| **NOTE** | Observation, not a finding | "This pattern is good," "Consider…" |

### Cross-cutting tags

When a finding within a subsystem actually points to a system-wide pattern, tag it `[XC: <theme>]` so the synthesis phase (08) can roll them up. Themes used:

- `[XC: secrets]` — secret handling end-to-end
- `[XC: auth]` — AuthN/AuthZ patterns
- `[XC: deploy]` — install/deploy path overlap
- `[XC: docs-drift]` — claim in docs ≠ reality
- `[XC: testing]` — coverage or fixture gaps that recur
- `[XC: perf]` — N+1 / query patterns across endpoints
- `[XC: ops]` — operational toil that could be automated

### Lens checklist (applied per subsystem)

Each subsystem phase considers, where applicable:

- **Architecture** — boundaries, coupling, in-flight refactors
- **Security** — AuthN, AuthZ, input validation, secrets, audit log
- **Testing** — coverage, fixtures, write-path / error-path
- **Performance** — query patterns, caching, hot paths
- **UX / A11y** — only applies to the web subsystem
- **Operability** — logging, error surfaces, runbook readiness

---

## Phase tracker

| # | Phase | File | Status |
|---|---|---|---|
| 0 | Workspace setup | *(this file)* | ✅ Done |
| 1 | Orientation & doc-drift | [`01_orientation.md`](01_orientation.md) | ✅ Done |
| 2 | Web (`src/webapp/`) | [`02_web.md`](02_web.md) | ✅ Done |
| 3 | Status (`src/system_status/`) | [`03_status.md`](03_status.md) | ✅ Done |
| 4 | ORM/CLI (`src/sam/`, `src/cli/`) | [`04_orm_cli.md`](04_orm_cli.md) | ✅ Done |
| 5 | Collector (`collectors/`) | [`05_collector.md`](05_collector.md) | ✅ Done |
| 6 | Platform / cross-cutting | [`06_platform.md`](06_platform.md) | ✅ Done |
| 7 | Docs hygiene | [`07_docs.md`](07_docs.md) | ✅ Done |
| 8 | Synthesis & punch list | [`08_synthesis.md`](08_synthesis.md) | ✅ Done |

**Audit complete.** Read in this order:

1. **[`08_synthesis.md`](08_synthesis.md)** (~250 lines, ~15 min) — executive summary, headline themes, 5 sequenced PRs, 49 questions for Ben. The single read.
2. **[`08_action_register.md`](08_action_register.md)** (appendix) — full 182-item punch list (15 P0 / 55 P1 / 112 P2). Browse by area; look up specific findings by ID.
3. **[`08_strengths.md`](08_strengths.md)** (appendix) — full "what's working well" list (54 entries). For when the audit needs to feel balanced.
4. **Per-phase docs** (`01_orientation.md` … `07_docs.md`) — `file:line` context for any specific finding.

## How to read these reports

1. Start with `08_synthesis.md` for the punch list (when it exists).
2. Drop into a per-subsystem file for context on any finding.
3. Cross-cutting tags (`[XC: …]`) link a single-subsystem finding to the system-wide pattern it represents.

## Time budget (directional)

| Phases | Estimate |
|---|---|
| 0–1 (workspace + orientation) | ~1 hr |
| 2 (web) | ~3–4 hrs |
| 3 (status) | ~1 hr |
| 4 (ORM/CLI) | ~2 hrs |
| 5 (collector) | ~1 hr |
| 6 (platform) | ~2–3 hrs |
| 7–8 (docs + synthesis) | ~1–2 hrs |
| **Total** | **~10–14 hrs** |
