# Phase 1 — Orientation & Doc-Drift

> Trust-calibration pass. Build a one-page system map. Check whether `README.md`, `CLAUDE.md`, and `docs/` accurately describe what's actually in the tree. Findings here calibrate how much we trust documentation in later phases.

## Scope

- `README.md` (top level, 941 lines)
- `CLAUDE.md` (top level, 39 KB) — also symlinked as `GEMINI.md`
- `CONTRIBUTING.md`
- `docs/` tree (INDEX, setup guides, plans, remediation, apis)
- In-tree design docs inside `src/webapp/` (`DESIGN.md`, `IMPLEMENTATION_SUMMARY.md`, `QUICK_START_RBAC.md`, `REFACTORING_PLAN.md`, `README.md`)
- `tests/docs/README.md`

## Method

1. Build a one-page system map: subsystems, deploy targets, data flows.
2. For each top-level doc, score: **Accurate / Partially / Stale / Aspirational**.
3. Look for "claim-vs-reality" gaps (e.g., docs reference files or commands that don't exist, or the reverse).
4. Note doc overlap (same content in multiple places).
5. Park deeper doc consolidation recommendations for Phase 7.

## Lenses applied

- Architecture (system map only)
- Operability (does the documented onboarding actually work?)

## Findings

*TBD*

### Doc-drift register

| Doc | Status | Notes |
|---|---|---|
| | | |

### One-page system map

*TBD*

## Cross-cutting tags raised

*TBD*

## Open questions for Ben

*TBD*
