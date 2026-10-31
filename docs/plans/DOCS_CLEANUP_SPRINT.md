# Docs cleanup sprint: retire the shipped plans, one filing rule, the XRAS front door

**Status: planned 2026-10-31 (Ben + Claude, at the close of the sprint 2 session); not
started.** Runs before the signed-in type overview (the eligibility sprint). One PR against
`staging`, a commit per plan, the docs gate as the proof.

## 1. The case

`docs/plans/` holds 37 top-level plans and 138 retired ones. Read by their own status lines,
about half of the 37 describe work that shipped weeks ago and has been in production since.
`scripts/sweep_inventory.py --area docs --gh` flags five of them as retire-ready (every cited
PR merged, no open status, idle two weeks); the rest are younger or cite a PR that has since
merged under another number. A reader opening `docs/plans/` today cannot tell what is live
from what is a record.

The XRAS tree, `docs/xras/`, files its records by two rules at once: the seven sprint records
sit under `docs/xras/incoming/implemented/`, while two XRAS plans sit under
`docs/plans/implemented/` and the README says so "on purpose". Its README is also stale on the
outgoing side: it still says SAM's calls to XRAS are "read-only and GET-only" and that
submission is "design, not built", eleven writes and two shipped sprints later.

`docs/integration/` is one 27-line note on legacy GID assignment, not a tree.

## 2. Decisions (Ben, 2026-10-31)

| Decision | |
|---|---|
| **One filing rule: lifecycle** | A shipped plan or sprint record lives in `docs/plans/implemented/`, whatever its topic. `docs/xras/` keeps only living references, and its README links the records |
| **Every shipped plan, not only XRAS** | The LDAP sync set, the HEUV port, the HPC lane layering, the sunbursts, the Tomcat watch, the hard-delete audit and the CLAUDE.md split retire in the same PR |
| **"The integration cleanup" is `docs/xras/`** | The README rewrite and the record moves; `docs/apis/` is out of scope; `docs/integration/NEXT_GID.md` is folded (§ 4) |
| **One PR, a commit per plan** | Each commit: `git mv`, every citation fixed, the status line flipped to what shipped. The README and index refresh is the last commit |

## 3. The inventory

Citations counted 2026-10-31 across `src`, `tests`, `helm`, `scripts`, `docs`, `.claude` and
`CLAUDE.md`, the ledger excluded (it cites by design and moves nothing). "Outside docs" is the
number of code, test, chart and rule files whose comments name the plan: those are the
`git mv` cost, since `test_cited_paths_exist` reads only `docs/` and the source comments are
found by grepping the basename.

### 3.1 Retire to `docs/plans/implemented/` (19)

| Plan | Status line today | Flip to | Citations (outside docs) |
|---|---|---|---|
| `XRAS_SUBMISSION_SPRINT1.md` | shipped 2026-10-29 in #874 | as is | 14 (9) |
| `XRAS_SUBMISSION_SPRINT2.md` | planned 2026-10-31 | shipped 2026-10-31 in #880 (`2098adce`), dev-proven twice (UCUB0208, UCUB0209), prod DDL applied | 24 (22) |
| `XRAS_REQUEST_TICKETS.md` | built, armed 2026-10-19 | as is | 14 (7) |
| `XRAS_CLOSED_LOOP.md` | Track 1 built | Tracks 1 and 2 live in production (the first live rename 2026-10-15) | 13 (7) |
| `XRAS_NOTICES_CUTOVER.md` | checklist, carried out | as is | 4 (0) |
| `XRAS_PHASE_C_AND_TICKETS.md` | Part 1 merged, cutover next | phase C live 2026-10-20, issue #846 closed | 11 (5) |
| `XRAS_EXTENSION_INACTIVE_PROJECT.md` | none | built (#756), the auto-reactivation flagged | 1 (0) |
| `LDAP_SYNC_API.md` | implemented 2026-10-09 | levers on prod 2026-10-19 (#844) | 21 (12) |
| `LDAPSYNC_DEV_SHADOW.md` | exit bar met 2026-10-19 | as is | 7 (4) |
| `LDAPSYNC_TESTBED_VERIFICATION.md` | done 2026-10-09 | as is | 1 (0) |
| `SAM_LDAP_SYNCD_REFERENCE.md` | reference, 2026-09-25 | a record of George's daemon as read 2026-09-25 (the client and the sync API are the living docs) | 10 (2) |
| `HEUV_API_PORT.md` | implemented, cutover Ben's | ARC fully cut over 2026-10-23 | 4 (2) |
| `HPC_LANE_ENV_LAYERING.md` | done 2026-10-09 | as is | 5 (4) |
| `WATCH_TOMCAT.md` | built 2026-10-10 | as is | 0 |
| `HARD_DELETE_AUDIT.md` | none | audit complete; the deletes were not applied (Ben), the repair scripts are the record | 4 (3) |
| `ALLOCATIONS_SUNBURST.md` | implemented and merged | as is | 3 (1) |
| `PANEL_SUNBURST.md` | implemented and merged | as is | 2 (0) |
| `CLAUDE_MD_SPLIT.md` | none | done: the root `CLAUDE.md` and `.claude/rules/` are the result | 0 |
| `DIRECTORY_CLIENT.md` | steps 1 to 4 built, draft PR #841 | #841 merged and live on prod 2026-10-16 (`6e0f00d2`); the follow-on is `DIRECTORY_CLIENT_NEXT.md` | 13 (9) |

The `.claude/rules/` files cite `XRAS_REQUEST_TICKETS.md` and `XRAS_CLOSED_LOOP.md` by path
(`notify-and-tasks.md`); those two commits touch a rule, which `test_rules_*` in the docs gate
checks too.

### 3.2 The seven XRAS sprint records move with them

`docs/xras/incoming/implemented/*` → `docs/plans/implemented/` (the one rule). Citations:
`XRAS_SPRINT_C.md` 31, `XRAS_SPRINT_B.md` 9, `XRAS_STRESS_AND_SCHEMA.md` 9, `XRAS_SPRINT_A.md` 7,
`XRAS_HANDLER_REFACTOR.md` 6, `XRAS_PRE_DEPLOY_SMOKE.md` 2, `XRAS_PRE_CUTOVER_TIDY.md` 1. The
`docs/xras/incoming/implemented/` folder goes with the last one.

### 3.3 Stay in `docs/plans/` (living, or unbuilt on purpose)

| Plan | Why it stays |
|---|---|
| `XRAS_SUBMISSION.md` | the living design; its status line gains sprint 2's shipping and the eligibility follow-on |
| `XRAS_POST_AUTOMATION.md` | roadmap; A to D live, E open |
| `XRAS_ACCOUNT_QUEUE.md`, `XRAS_INGEST_IMPROVEMENTS.md` | deliberately unbuilt, say so |
| `DIRECTORY_CLIENT_NEXT.md`, `CLI_VERBS.md`, `LDAPSYNC_DIRECTORY_AFFILIATIONS.md` | in progress or agreed direction |
| `DOC_SLIMMING.md` | phases 5 to 8 open |
| `HTMX_API_READINESS.md` | a living register of handler readiness |
| `GALLERY_VISUAL_SNAPSHOTS.md`, `GRANULAR_EXPIRATIONS_SKETCH.md` | deferred, sketch |
| `DECK_PUBLISHING.md`, `SAMUEL_PRESENTATION.md` | the deck series, stage 2 open |
| `UNPLANNED_CITY_LEDGER.md` | the ledger |
| `WATCH_LEGACY.md`, `SWEEP_32_HANDOFF.md` | untracked handoffs; Ben's call whether to commit or drop |

### 3.4 Ask Ben at the first commit

No status line and no memory of a verdict: `DISK_INGEST_TRIAGE.md`, `SCHEMA_RETIREMENT.md`,
`DISK_CHARGE_SUMMARY-only.md`, `EXTERNAL_CONTACTS.md`. Each gets a status line either way.

## 4. The XRAS front door

`docs/xras/README.md`, rewritten in the last commit:

- the outgoing paragraph says what is true: SAM reads XRAS (queries, probes) and writes to it
  through one audited client (remediations, renames, the submission flow), each write behind
  its own lever;
- the "Shipped" table points at `docs/plans/implemented/` for the seven records, and the
  "Two XRAS docs live elsewhere" section goes, since nothing is elsewhere any more;
- the submission row says shipped (sprints 1 and 2) and links the living design and the two
  sprint records;
- `XRAS_DEV_PROBES.md`, `XRAS_SUBMISSION_PROBES.md`, `XRAS_WRITE_PROBES.md`,
  `XRAS_ARC_BASELINE.md` are listed as what they are, the probe records, kept under
  `outgoing/` because the next probe appends to them;
- `REQUEST_EDITOR.md` and `XRAS_OPPORTUNITY_ALLOCATION_TYPE.md` get a status line.

`docs/INDEX.md`: the `xras/` line reads "XRAS integration: the reference, runbook, playbook
and probe records; sprint records under plans/implemented". `docs/integration/NEXT_GID.md`
moves to `docs/plans/implemented/LEGACY_GID_ASSIGNMENT.md` with a status line (an
investigation, 2025; the finding that SAM never mints a GID) and the folder goes.

## 5. Mechanics, per commit

1. `git mv docs/plans/X.md docs/plans/implemented/X.md`.
2. Flip the status line to what shipped (§ 3.1), in the plan's own words, no changelog
   phrasing (`test_prose_avoids_changelog_phrasing`).
3. `grep -rl 'X.md' src tests helm scripts docs .claude CLAUDE.md containers etc` and fix every
   path, including the back-ticked ones in source comments and the relative links in
   `docs/xras/README.md`. The ledger's entries keep their paths as written: they are a record.
4. `pytest tests/unit/gates/test_docs.py`: links resolve, cited paths exist, rules intact.
5. The house commit message: the plan, what shipped, the citation count moved.

Memory pointers (`MEMORY.md` names `docs/plans/CLI_VERBS.md`, which stays) need no change; the
project memories that name moved files are updated at the close of the sprint.

## 6. Verification

- `scripts/sweep_inventory.py --area docs --gh` after the PR: zero retire candidates, the
  untriaged list unchanged.
- `pytest tests/unit/gates/test_docs.py` green on every commit.
- `grep -rn 'xras/incoming/implemented' docs src tests .claude` finds nothing.
- The README's three tables each link a file that exists (`test_markdown_links_resolve`).

## 7. Out of scope

- `docs/apis/` (the integration API docs) and the open phases of `DOC_SLIMMING.md`.
- The ledger's 155 open boxes.
- Rewriting any retired plan's body: a record is a record.
