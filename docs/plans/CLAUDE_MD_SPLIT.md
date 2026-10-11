# CLAUDE.md split: a lean root plus path-scoped rules

## Context

`CLAUDE.md` is 1,067 lines / 65 KB (~16k tokens) and loads into every session,
plus every non-Explore/Plan subagent. Official guidance is <200 lines per file
("longer files ... reduce adherence"). Ben flagged the split on 2026-10-10
(memory `project_claude_md_split`). Goal: keep or improve accuracy while paying
only for the context a task touches.

**What history says (audit of the 07-26 and 08-22 compressions):** nothing
valuable was lost. Every cut is relocated (docs/plans/implemented, area READMEs,
code docstrings) or was wrong/stale when cut (e.g. the old "HPC/DAV ->
CompChargeSummary + DavChargeSummary" SQL walkthrough contradicted
`calculator.py`). Three marginal items worth a line each in the new layout:
`from ..base import *` convention (28 `src/sam` files), `--job-id` matching
rules, `--has-active-project` (both only in `--help`).

**Mechanics (verified against current Claude Code docs, v2.1.296 installed):**
- `@imports` load eagerly -> no savings. Ruled out.
- Nested `src/x/CLAUDE.md` loads on first Read/Write/Edit inside that dir.
- `.claude/rules/*.md` with `paths:` frontmatter loads on Read/Write/Edit of a
  matching file; globs can span directories (incl. tests/, helm/). Recursive
  discovery. Invalid YAML silently loads the rule *unscoped* (every session).
- Both are summarized away on compaction and reload on the next matching touch;
  root reloads from disk. Glob/Grep hits are not documented triggers.
- Unknown: whether a general-purpose subagent loads path-scoped rules lazily.
  Verified in step 6 before relying on it.

## Recommended approach: `.claude/rules/` with `paths:`

Why rules over nested CLAUDE.md: our topics cross directories. The forms rule
governs `src/sam/schemas/forms/` *and* `src/webapp/**`; the notify/tasks traps
govern `src/sam/notify/`, `src/scheduling/`, `helm/values.yaml`,
`helm/templates/cronjob-tasks.yaml` *and* `tests/unit/{notify,tasks}/`. A
nested file fires only from its own subtree, so a test-only or helm-only task
would miss it. Rules also sit in one directory a gate can audit. Cost: Claude-
specific (GEMINI.md symlink sees only the root) and further from the code;
area READMEs stay the human docs.

### 1. Root `CLAUDE.md` (target ~350 lines, every session; Ben's call)

Keeps what any task can violate, including §7 and §8 whole, because they are
broken from many directories:
- Overview, Session setup, DB connection (condensed), Code Organization tree
  (the map; trimmed annotations), Related projects (3 lines: lockstep rule +
  pointer).
- §1 naive datetimes, §5 universal `is_active` + its exceptions, **§7 write
  operations whole** (model methods, `management_transaction`, allocation
  replay + membership/removal invariants), **§8 route protection whole**,
  "allocation usage is one computation, never write another sum", §12 comment
  budget. Root keeps its § numbering for these so existing §5/§7/§8 citations
  stay valid.
- Testing: run commands, outbound-closed trap, isolation one-liner.
- Skip-CI tokens trap, Git workflow, Contacts.
- **Tripwires** (one line each, because nested context may not be loaded):
  `NOTIFY_ENABLED` fail-closed, `SAM_TASKS_DISABLED` fail-open (new task needs
  `helm/values.yaml` in the same change), legacy-compat blueprints DO NOT
  REFACTOR, `_REQUIRED_BINDS`, `sam/queries/__init__.py` stays empty.
- **Rule index table**: file -> scope -> one-line "load me when". Tells Claude
  (and subagents) a rule exists before a trigger fires; instructs "Read the
  rule before editing in its scope" as the fallback if lazy loading misses.
- Common Pitfalls pruned to cross-cutting items; topic pitfalls move with topic.

### 2. Rule files (each <=250 lines, the default test_docs budget)

| File | `paths:` (sketch) | Content moved from CLAUDE.md |
|---|---|---|
| `orm.md` | `src/sam/**/*.py`, `tests/factories/**`, `tests/unit/models/**`, `tests/integration/**` | Key ORM models, §2-§4, §6, Common ORM Patterns, adding models / schema mismatches, `from ..base import *` |
| `webapp-api.md` | `src/webapp/api/**`, `src/sam/schemas/**`, `src/sam/queries/*_access.py`, `src/sam/security/**`, `tests/api/**` | Serialization schemas, API endpoints, legacy-compat + output shaping, RBAC/Security |
| `webapp-ui.md` | `src/webapp/{dashboards,templates,static,utils}/**`, `src/sam/schemas/forms/**`, `tests/unit/webapp/**` | §9 forms/HTMX + modal trap, §10 active-only, §11 static assets, DB browser, running the webapp |
| `notify-and-tasks.md` | `src/sam/notify/**`, `src/scheduling/**`, `helm/values.yaml`, `helm/templates/cronjob-tasks.yaml`, `tests/unit/{notify,tasks}/**` | Notifications table, batch knobs, the three task sections, helm wiring traps, import-graph DON'Ts |
| `charts.md` | `src/webapp/dashboards/charts/**`, `tests/unit/charts/**`, chart CSS/fragment | Charts + Gotchas |
| `account-requests.md` | `src/sam/core/account_requests.py`, `src/sam/manage/account_requests.py`, `src/webapp/register/**`, event-lifecycle modules | Account requests block |
| `cli.md` | `src/cli/**`, `tests/unit/cli/**`, `src/sam/fmt.py`, `src/webapp/templates/**` | CLI tools, JSON envelope/exit-code lockstep, `--job-id` rules, Display Formatting |
| `testing.md` | `tests/**`, `pytest.ini` | Layer 1/2 data, system_status tier, route-map parity, perf baselines, HTTP-test convention (point at docs/TESTING.md, don't duplicate it) |

Overlapping globs are fine (both load). Boundaries settle during the move;
content is moved verbatim first, trimmed only where a README already holds it.

### 3. Re-point references (~38 sites)

- Citations of root-resident sections (§1, §5, §7, §8, §12) stay as they are.
  Citations of moved sections (mostly §9 forms, §10, §11 across ~25 sites in
  `src/`, `tests/`, `.claude/skills/`, `CONTRIBUTING.md`) -> cite the rule path,
  e.g. `.claude/rules/webapp-ui.md §9`. Rule files keep the § numbers as
  headings so citations stay short and unambiguous.
  `docs/plans/**` and nrit-review records are point-in-time: leave them.
- Gated anchor links: `README.md:671,672,955`, `src/webapp/README.md:172`
  (`#serialization-schemas-samschemas`, `#key-orm-models`) -> rule files.
- `wire-dashboard-feature/SKILL.md`: replace restated §9/§10/§11 text with
  pointers to `webapp-ui.md` where it is pure duplication; keep its checklist.

### 4. Gates (`tests/unit/gates/test_docs.py`, new tests beside the budget test)

Rules are tracked `.md`, so spelling, changelog-phrasing, cited-paths and the
250-line default already apply. Add:
- every `.claude/rules/*.md` has frontmatter that parses and carries `paths:`
  (an invalid file loads unscoped in every session, silently);
- every `paths:` glob matches >=1 tracked file (a renamed dir must not orphan
  a rule into never loading);
- the root CLAUDE.md rule index names every rule file, and vice versa;
- every `CLAUDE.md §N` citation under `src/`, `tests/`, `.claude/`,
  `docs/apis/`, `CONTRIBUTING.md` names a § that is still a root heading, and
  every `.claude/rules/X.md §N` names a heading that file has;
- lower `LINE_BUDGETS["CLAUDE.md"]` to the new size.

### 5. Sequencing (one PR to `staging`, branch cut from `origin/staging`)

1. Commit this plan as `docs/plans/CLAUDE_MD_SPLIT.md`.
2. Move content into rule files verbatim (commit), then trim root (commit).
3. Re-point references + skill (commit). 4. Gates (commit).
5. Update memory `project_claude_md_split` to done + rule index pointer.

## Verification

- `pytest tests/unit/gates/test_docs.py` and full suite green.
- Context cost: `/context` in a fresh session before vs after (expect ~16k ->
  ~5-6k memory-file tokens).
- Load behavior, via a temporary `InstructionsLoaded` hook in
  `.claude/settings.local.json` that appends `file_path load_reason
  trigger_file_path` to a scratch log:
  (a) fresh session: only root loads; (b) Read `src/sam/notify/service.py` ->
  `notify-and-tasks.md`; (c) Edit `helm/values.yaml` -> same rule;
  (d) Read a test in `tests/unit/charts/` -> `charts.md`;
  (e) a general-purpose subagent reading `src/webapp/dashboards/...` — record
  whether rules load there. If not, the root index's "Read the rule first"
  instruction is the documented mitigation.
- Spot-check accuracy: diff the union of root + rules against the old file
  (`scripts` one-off: every non-blank old line present somewhere, or listed as
  deliberately dropped) so nothing is lost in the move.

## As built (2026-10-11)

- **Sizes.**
  - The root is 439 lines and 23 KB, about 5.7k tokens per session, down from 65 KB. It is over the ~350-line target because the move was verbatim; the byte target was met.
  - The rules total 807 lines across nine files. `webapp-ui.md` is the largest at 179.
- **Nothing lost.** A script checked that every non-blank line of the old file appears in the root or in a rule. The only lines missing are deliberate edits (cross-reference retargets, heading renames, one duplicate pitfall).
- **Loading, observed live.**
  - Rules created mid-session load on the first Read of a matching file. Reading `src/sam/notify/kinds.py` loaded both `notify-and-tasks.md` and `orm.md`, so overlapping scopes both load and the `**/*` glob form works.
  - A general-purpose subagent also loaded `webapp-ui.md` lazily on its first matching Read.
  - Subagents get the root CLAUDE.md as it was when the session launched, so a split takes effect from the next session.
- **Deferred.**
  - De-duplicating `wire-dashboard-feature/SKILL.md` against `webapp-ui.md`. The skill loads on demand, so the duplication costs nothing per session.
  - The `/context` before/after measurement in a fresh session (Ben).
