# Phase 7 — Docs Hygiene

> Final pass. With the full picture from Phases 1–6 in hand, score each doc for accuracy and overlap; recommend a consolidation plan. This is the cheapest possible class of improvement: documentation drift is the kind of debt that compounds, and almost every entry below is a Tiny or Small fix.

## Scope

- `README.md` (941 lines), `CLAUDE.md` (1,013 lines), `GEMINI.md` symlink, `CONTRIBUTING.md` (668 lines)
- `docs/` tree, including:
  - Top-level (~20 files: INDEX, AUTHENTICATION, LOCAL_SETUP, SETUP_SUMMARY, GETTING_STARTED, CREDENTIALS, SCRIPTS, SCRIPT_ORGANIZATION, DATABASE_SWITCHING, WEBAPP_SETUP, DOCKER_TROUBLESHOOTING, TESTING, STAGING, k8s, README-k8s, …)
  - `docs/apis/` (3 integration docs)
  - `docs/plans/` (5 active + 29 archived in `implemented/`)
  - `docs/prompts/` (9 AI-collab prompt artifacts)
  - `docs/remediation/` (CESM0002 incident records, 5 files)
  - `docs/presentations/` (sub-project with own README + CLAUDE.md)
  - `docs/integration/NEXT_GID.md`
- In-tree design docs under `src/webapp/` (Phase 1 disposition recs)
- Subtree READMEs: `tests/docs/README.md`, `scripts/README.md`, `scripts/setup/README.md`, `collectors/README.md`, `migrations/README.md`, `infrastructure/README.md`, `helm/...`, `src/webapp/README.md`, `src/cli/README.md`

## Method

Synthesized from Phases 1–6 — no new code reading required for this phase. Each finding is grounded in a verified accuracy gap, drift, or recommendation surfaced earlier in the audit.

## Lenses applied

- Operability (docs as onboarding tool)

---

## Findings

### Headline

The doc tree is **mostly accurate, well-meaning, and over-built.** `CLAUDE.md` is the gold standard (Phase 1 verified, every spot-check passed; Phase 4 confirmed the cross-references to ORM/queries/manage/schemas hold up). `docs/AUTHENTICATION.md`, `docs/TESTING.md`, `docs/STAGING.md`, `docs/README-k8s.md`, and `docs/apis/*.md` are operationally useful. `docs/plans/implemented/` is **exemplary archive practice** (29 plan docs from completed work moved aside) — model this for the rest.

The drift concentrates in five places:

1. **Setup-doc cluster overlap** — 8 docs describe overlapping subsets of "how to install." A new engineer onboarding hits the cluster and doesn't know which doc to trust. Phase 6 P2-91 catches this; Phase 6 I1 shows the code has drifted from one of them.
2. **AI-collab residue in three locations** — `src/webapp/{IMPLEMENTATION_SUMMARY,DESIGN,REFACTORING_PLAN}.md` (Phase 1), `docs/prompts/*.txt` (literal prompts to Claude), `collectors/docs/PBS_COLLECTORS_*PLAN.md` (build-phase plans, Phase 5). None are dangerous; all are confusing.
3. **`docs/INDEX.md` is incomplete** — doesn't mention `plans/`, `prompts/`, `remediation/`, `integration/`, `presentations/` subdirs at all. Lists 9 docs in main sections but `AUTHENTICATION.md` (a high-value doc) appears only in the FAQ section.
4. **`README.md` (941 lines) and `CONTRIBUTING.md` are long but partly stale.** Phase 1 already flagged: test counts in CONTRIBUTING are 4.6× off (380+ claimed, ~1,750 actual). README internally inconsistent (claims both ~1,400 and 380+ tests in different sections). README's API section omits ~7 newer endpoint modules.
5. **Operational records checked into the repo** — `docs/remediation/CESM0002_*` (5 files, ops incident artifacts including `.txt` dry-run output and post-apply output) age poorly in a code repo. Phase 1 Q2 surfaced this; no policy yet.

### Per-doc disposition

Severity-tagged where action is recommended; otherwise just verified-current. Cross-references to phase findings in brackets.

#### Top-level

| Doc | Status | Action | Effort |
|---|---|---|---|
| `README.md` | ⚠️ Partly stale | **Trim + update.** Drop the cluster of inline setup steps that duplicate LOCAL_SETUP.md; refresh test counts to match actual (~1,750); add the 7 missing API endpoint modules (charges, directory_access, fstree_access, health, project_access, status, allocations). [P1 / Phase 1] | **Small** |
| `CLAUDE.md` | ✅ Authoritative | Keep. Spot-check on next refresh: ORM model count ("91+" → actually ~106), test count ("~1,400 tests" → ~1,750). [Phase 1, Phase 4] | **Tiny** |
| `GEMINI.md` (symlink to CLAUDE.md) | ✅ Current | Keep. | — |
| `CONTRIBUTING.md` | ⚠️ Stats stale | Re-run `pytest --cov`, update "380+ tests / 77.47% coverage" line. [P2-23 / Phase 1] | **Tiny** |

#### `docs/` top-level

| Doc | Status | Action | Effort |
|---|---|---|---|
| `INDEX.md` | ⚠️ Incomplete | **Update.** Add sections for `plans/`, `apis/`, `presentations/`. Promote `AUTHENTICATION.md` from FAQ-only to a Configuration top-listing. Drop references to docs that get archived per below. | **Small** |
| `AUTHENTICATION.md` | ✅ Best-in-class | Keep. Model the runbook style for STATUS_API_KEY + JH-token rotation [P1-43 / Phase 6 Q47]. | — |
| `TESTING.md` | ✅ Most accurate test claim in repo | Keep. | — |
| `STAGING.md` | ✅ Current | Keep. | — |
| `README-k8s.md` | ✅ Unusually good | Keep. [Phase 6 strength] | — |
| `k8s.md` | ⚠️ Overlaps with README-k8s | **Merge.** Generic `kubectl`/OIDC cheat sheet — fold useful bits into README-k8s, archive. [Phase 1, Phase 6 D11] | **Small** |
| `CIRRUS-k8s-cmds.sh` | ⚠️ Odd home | **Move to `scripts/`** — a `.sh` file inside `docs/` is non-discoverable; duplicates `k8s.md` §1-2. [Phase 1] | **Tiny** |
| `LOCAL_SETUP.md` | ✅ Current (comprehensive) | Keep as **canonical setup doc.** | — |
| `SETUP_SUMMARY.md` | ⚠️ Heavy overlap | **Archive or merge into LOCAL_SETUP.md §Quickstart.** [Phase 1, Phase 6 I6] | **Tiny** |
| `GETTING_STARTED.md` | ⚠️ Big standalone | **Keep, but cross-link.** Non-overlapping primer; rename to `STACK_PRIMER.md` to clarify intent. | **Tiny** |
| `CREDENTIALS.md` | ⚠️ Overlaps LOCAL_SETUP §2 | **Reduce to a stub linking to LOCAL_SETUP §Credentials + AUTHENTICATION.md.** [Phase 1] | **Tiny** |
| `DATABASE_SWITCHING.md` | ✅ Current but thin | Keep, but cross-reference the switch-script drift fix (P1-51). [Phase 6] | — |
| `SCRIPTS.md` | ⚠️ Mirrors SETUP_SUMMARY table | **Merge into scripts/README.md** (the canonical location for script docs) and archive. | **Tiny** |
| `SCRIPT_ORGANIZATION.md` | ⚠️ Complements SCRIPTS.md | **Merge with above.** | **Tiny** |
| `DOCKER_TROUBLESHOOTING.md` | ✅ Unique content | Keep. | — |
| `WEBAPP_SETUP.md` | ⚠️ Subset of LOCAL_SETUP | **Reduce to a stub** — unique parts (dev auto-login + Flask debug) move to LOCAL_SETUP §Dev mode. | **Tiny** |
| `apis/SYSTEMS_INTEGRATION_APIs.md` | ✅ Current | Keep. | — |
| `apis/CHARGING_INTEGRATION.md` | ✅ Current | Keep. | — |
| `apis/HPC_DATA_COLLECTORS_GUIDE.md` | ✅ Current | Keep. | — |
| `integration/NEXT_GID.md` | ✅ Useful design history | Keep, but the parent `integration/` directory has only this one file. Consider promoting to `docs/` root or grouping with `apis/`. | **Tiny** |

#### `docs/plans/` (active)

| Doc | Status | Action | Effort |
|---|---|---|---|
| `DISK_CHARGE_SUMMARY-only.md` | Active plan | Verify status with Ben. If shipped, move to `implemented/`. | **Tiny** (depends on Q) |
| `POSTGRES_MIGRATION.md` | Open (Q4) | **Hold — depends on Q4.** Phase 4's MySQL-ORM dependence makes the migration status load-bearing. | **N/A until Q4** |
| `PRODUCTION_IMPROVEMENTS.md` | ⚠️ Out of date | **Cross-check with Phase 2/6 findings.** Doc says "8 of 12 complete" but `webprod` branch dates from 2026-02; Phases 2 and 6 found Rate Limiting was actually completed (matches doc) and most listed-as-open items aligned with current state. Worth a refresh pass marking what's still open. | **Small** |
| `RATE_LIMITING.md` | Implemented per Phase 2 (`webapp/limiter/`) | **Move to `implemented/`.** | **Tiny** |
| `SCHEMA_VISUALIZATION.md` | Verify status | Verify with Ben. | **Tiny** |
| `implemented/` (29 docs) | ✅ Exemplary archive | Keep. Model this pattern for other archives. [Strength] | — |

#### `docs/prompts/` (9 files, mostly `.txt`)

These are **literal prompts sent to Claude during the build phase.** `auditing.md` (verbose: includes the example Flask blueprint that was implemented in `webapp/audit/`); 8 `.txt` files for collectors, status dashboard, populators, fstree_api, etc.

Phase 1 Q5 raised the disposition. Recommendation:

- **Action: archive to `docs/archive/build-prompts/`.** Keep them for historical context (useful for "what was the original intent?") but signal they are *not* current operational docs. Tagging with a short README in the archive dir would be enough. Combined effort: **Tiny**.

#### `docs/remediation/`

Five files: 2 markdown reports + 3 `.txt` artifacts (dry-run output, post-apply output). All from the 2026-05-02 CESM0002 audit-trail remediation.

Phase 1 Q2 raised the question of off-repo destination. Recommendation:

- **Short-term: leave in place** but rename to `docs/remediation/2026-05-02-CESM0002/` (date-first directory for sortability) and add a `README.md` enumerating the files.
- **Long-term: ship to Confluence or Jira and remove from repo.** Per `CLAUDE.md` "Common Tasks", Confluence is the right home for ops records. Phase 1 Q2 awaits Ben's preference.
- Effort: **Tiny** (rename), **Small** (Confluence migration once policy is set).

#### `docs/presentations/`

Self-contained sub-project with its own `README.md`, its own `CLAUDE.md` (auto-loaded when working in that subtree — clever), a `common/` infrastructure dir, and individual deck dirs. Quarto → pptx pipeline. **Treat as out-of-scope for consolidation** — it follows its own conventions cleanly.

| File | Status | Action |
|---|---|---|
| `presentations/README.md` | ✅ Self-contained | Keep. |
| `presentations/CLAUDE.md` | ✅ Subtree-scoped CLAUDE | Keep. (Nice pattern — could be borrowed for `collectors/` or `helm/` if those subtrees grow.) |

#### `src/webapp/` in-tree design docs (Phase 1 dispositions, now finalized)

| Doc | Final disposition | Effort |
|---|---|---|
| `IMPLEMENTATION_SUMMARY.md` | **Delete.** Mid-sprint scaffolding, net-misleading. [Phase 1] | **Tiny** |
| `DESIGN.md` | **Archive to `docs/archive/webapp-design.md`.** Evergreen rationale ("why permission-based not role-based") worth preserving; stale architecture diagram drops. [Phase 1] | **Tiny** |
| `IMPLEMENTATION_SUMMARY.md` references aside, `REFACTORING_PLAN.md` | **Reconcile with current code.** Priority 1.1 (Centralize charges API queries via `sam.queries`) is partly done per Phase 2 (`api/v1/charges.py` does delegate, but inconsistently — see Phase 2 M4). Priority 2.1 (Consolidate duplicate schemas) was implemented per Phase 4's "3-tier schemas." Verify each item against current code, retire items, move surviving items to a single backlog doc (`docs/plans/WEBAPP_REFACTORING_BACKLOG.md`). [Phase 1 Q3, Phase 4] | **Small** |
| `QUICK_START_RBAC.md` | **Promote to `docs/TESTING_RBAC.md`.** The doc to point new engineers at — fresh, current, useful. [Phase 1, Phase 2 strength] | **Tiny** |
| `src/webapp/README.md` | **Trim.** Quick-start solid; API section (line 265+) omits ~7 newer endpoints and references "future Marshmallow" which is now done [Phase 1]. Cross-reference `docs/apis/*` for the integration view. | **Small** |

#### Subtree READMEs

| File | Status | Action |
|---|---|---|
| `tests/docs/README.md` | ✅ Useful | Keep. |
| `scripts/README.md` | ✅ Useful (if SCRIPTS.md + SCRIPT_ORGANIZATION.md merge in) | Make this the canonical scripts doc. |
| `scripts/setup/README.md` | ✅ Useful | Keep. |
| `collectors/README.md` | ⚠️ Mostly current | Keep; fix `STATUS_API_KEY` line that says "your_password" [Phase 5 P2-74]. | **Tiny** |
| `collectors/docs/PBS_COLLECTORS_PLAN.md` | ⚠️ Build-phase plan | **Archive** to `docs/archive/build-plans/collectors/` [Phase 5 P2-75]. | **Tiny** |
| `collectors/docs/PBS_COLLECTORS_ADD_RESERVATIONS_PLAN.md` | ⚠️ Build-phase plan | Same. | **Tiny** |
| `migrations/README.md` | ✅ Operational | Keep. (Phase 3 strength — runbook style is exemplary.) | — |
| `migrations/system_status/PROD_BOOTSTRAP.md` | ✅ Exemplary | Keep. [Phase 3 strength] | — |
| `migrations/system_status/0002_NORMALIZATION_RUNBOOK.md` | ✅ Exemplary | Keep. [Phase 3 strength] | — |
| `migrations/system_status/0003_USER_PROJ_QUEUE_RUNBOOK.md` | ✅ Exemplary | Keep. [Phase 3 strength] | — |
| `migrations/system_status/implemented/2026-05-04-update-prod.md` | ✅ Right archive pattern | Keep. | — |
| `infrastructure/README.md` | Verify | Phase 6 D2 flagged staging RDS public; verify README reflects current intent. | **Tiny** |
| `src/cli/README.md` | ⚠️ References dead file | Fix the `sam_search_cli_original.py` reference (no longer exists) [Phase 4 C10]. Confirm/reconcile `AccountingAdminCommand` inheritance claim [Phase 4 C1 / Q25]. | **Tiny** |

### Recommended consolidation — target IA

After the dispositions above, the doc tree should look approximately:

```
README.md                             ← project landing, trimmed
CLAUDE.md                             ← authoritative reference (refresh counts)
GEMINI.md → CLAUDE.md
CONTRIBUTING.md                       ← updated test stats

docs/
├── INDEX.md                          ← updated to reflect this IA
├── LOCAL_SETUP.md                    ← canonical onboarding
├── STACK_PRIMER.md                   ← formerly GETTING_STARTED
├── DOCKER_TROUBLESHOOTING.md
├── DATABASE_SWITCHING.md
├── AUTHENTICATION.md                 ← include rotation runbook section for all secrets, not just OIDC
├── TESTING.md
├── TESTING_RBAC.md                   ← formerly src/webapp/QUICK_START_RBAC.md
├── STAGING.md
├── README-k8s.md                     ← absorbs k8s.md
├── apis/
│   ├── SYSTEMS_INTEGRATION_APIs.md
│   ├── CHARGING_INTEGRATION.md
│   └── HPC_DATA_COLLECTORS_GUIDE.md
├── plans/
│   ├── *.md                          ← live plans (verified against Phase 6 register)
│   └── implemented/                  ← exemplary archive, keep growing
└── archive/                          ← new: durable home for stale-but-useful
    ├── build-prompts/                ← docs/prompts/ moves here
    ├── webapp-design.md              ← src/webapp/DESIGN.md moves here
    └── build-plans/
        └── collectors/               ← PBS_COLLECTORS_*PLAN.md move here

src/webapp/
├── README.md                         ← trimmed; API section deferred to docs/apis/
└── REFACTORING_PLAN.md  → docs/plans/WEBAPP_REFACTORING_BACKLOG.md (verified + retired-items removed)

(IMPLEMENTATION_SUMMARY.md, DESIGN.md, QUICK_START_RBAC.md, k8s.md, SETUP_SUMMARY.md, SCRIPTS.md, SCRIPT_ORGANIZATION.md, WEBAPP_SETUP.md, CREDENTIALS.md all gone or stub-redirect)

scripts/README.md                     ← canonical (absorbs SCRIPTS + SCRIPT_ORGANIZATION)
scripts/setup/README.md               ← keep

tests/docs/README.md                  ← keep
collectors/README.md                  ← fixed
migrations/                           ← unchanged
helm/                                 ← unchanged (README-k8s is good)
docs/remediation/2026-05-02-CESM0002/ ← renamed, with index README; long-term move to Confluence
docs/presentations/                   ← unchanged (self-contained)
```

### Net effect

- **~10 docs deleted or merged**, eliminating most of the setup-cluster overlap and AI-collab residue
- **One new `docs/archive/`** as the durable destination for stale-but-useful artifacts (matches the pattern of `docs/plans/implemented/`)
- **`INDEX.md` becomes the authoritative entry point** rather than one of many parallel readmes
- **`REFACTORING_PLAN.md` reconciled against actual implementation** rather than living as aspirational backlog

All findings here resolve to **Tiny or Small effort.** This is the cheapest phase to act on — most fixes are mv / sed / one-paragraph edits.

---

## Cross-cutting tags raised

- `[XC: docs-drift]` — all of the above; concentrated in setup-cluster overlap, AI-collab residue, and INDEX.md being incomplete.
- `[XC: ops]` — `docs/remediation/` policy gap (Phase 1 Q2) is the one with operational implications: where should incident records actually live?

## Open questions for Ben

These mostly resolve to "execute the recommendations" — no new heavy questions from this phase. Carrying forward the ones that gate Phase 7 work:

1. **`docs/remediation/` home** — Confluence, Jira, in-repo? [Phase 1 Q2 still open]
2. **`docs/prompts/` archive vs delete** — keep as historical or drop entirely? [Phase 1 Q5]
3. **`docs/plans/POSTGRES_MIGRATION.md`** — still planned, paused, shelved? [Phase 1 Q4]
4. **`src/webapp/REFACTORING_PLAN.md`** — appetite for the verify-and-reconcile pass? [Phase 1 Q3]
5. **`collectors/docs/PBS_COLLECTORS_*PLAN.md`** — archive or delete? [Phase 5 Q34]

Phase 7 surfaces no new questions of its own — it inherits all five from earlier phases and resolves them with concrete dispositions above.
