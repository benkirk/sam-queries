# Doc slimming — comment density in source and configs

**Status: Phases 0, 1, 1b and 2 landed 2026-08-22 on `deslop_opus5` (PR #471).
Phases 3-9 open.**

This file is the handoff record. Each phase updates the table in section 5
in the same commit as its work, so a new session resumes from this document
plus a `scripts/doc_ratio.py` run, and needs nothing from a prior
conversation.


---

## 1. Context

Issue #461 measured doc-line ratios and found `src/` at a 6-month high. The lived
complaint is sharper than the ratio: rationale prose now outweighs the thing it
explains, and the codebase is getting hard for a person to read.

Two comparisons from inside this repo make the case:

| | ratio | note |
|---|---|---|
| `src/cli` | **21.8%** | written earlier; the internal healthy baseline |
| XRAS subtree (53 files, 17.8k lines) | **42.5%** | mostly written in the last month |
| `helm/values.yaml` | **61%** | was 5% in April; **doubled in the last 17 days** |
| `.env.example` | **80%** | comments grew 5.9x while the file grew 3.6x |

`src/` holds **33,601 doc lines**; the top 60 files hold **51%** of them.

**Two claims in #461 need correcting.** `GEMINI.md` is a symlink to `CLAUDE.md`, not a
duplicate copy. And `CLAUDE.md` / `README.md` are flat-to-shrinking (820→998 and
801→966 lines since February), so the root memory files are not a growth source.

**The finding that shapes the work:** comments that merely restate the adjacent line
are **under 1% of doc lines** (~189 repo-wide, and they sit in the *older* code).
Cutting "useless comments" buys nothing. The mass is narrative rationale, and a real
slice of it is load-bearing — wire-protocol measurements, recorded production defects,
deploy-coupling traps that exist nowhere else. So the operation is **relocate, not
delete**: design-doc prose living in the wrong file type moves to `docs/`, leaving the
constraint and a path reference.

There is precedent: `hpc-scheduling-tools` ran this exact sprint in August
(`docs/plans/implemented/DOCS_SCRUB.md`) and landed a stdlib-only `tests/test_docs.py`
to hold the line. Phase 0 ports it.

**Intended outcome:** `src/` back near the `src/cli` baseline, configs readable as
configs, and enough written rule plus mechanical gate that it does not re-inflate.

---

## 2. Decisions (Ben, 2026-08-22)

| | |
|---|---|
| Operation | **Relocate to `docs/`, leave a pointer.** Not delete. |
| Scope | Top ~60 `src/` files by absolute doc lines, plus all flagged configs. |
| `CLAUDE.md` | **In scope** — slimmed by the same relocate-and-point operation. |
| Guardrail | `scripts/doc_ratio.py` + the ported doc tests. **No CI ratio gate.** |
| Spelling gate | **Prose only** — comments, docstrings, markdown. Never identifiers or string literals. |
| Ported checks | Spelling, markdown links, cited paths, line-budget ratchet, **changelog phrasing**. The other three are skipped. |
| Emoji | Out of **code comments and configs**. `docs/` keeps its markers. |
| `docs/` | Stays as records. Not slimmed. |

---

## 3. The rules

Into `CONTRIBUTING.md` § *Code Style & Best Practices* and `CLAUDE.md`. These are also
the edit rule for the sprint itself.

**Python**
- Module docstring <= 10 lines. What the module is for, not how it came to be.
- Function/method docstring: one line, unless the signature is genuinely ambiguous.
- No `Args:`/`Returns:` block when annotated parameter names already say it. Keep
  `Raises:` when the exception is non-obvious.
- In-body comments <= 3 lines. Longer is a design doc — put it in `docs/`, reference the path.
- **Always keep** a comment recording a real trap, a past production bug, or a
  non-obvious invariant. Compress to the constraint; never delete it.

**Config** (`helm/`, `.env.example`, `compose.yaml`, Dockerfiles)
- State the constraint and the legal values. That is what an operator needs.
- Measurement logs, benchmark tables, release narrative, history → `docs/`.
- No inline comment restating the key name (`replicaCount: 2  # Number of pods`).

**Prose style, everywhere**
- American spelling.
- No emoji or decorative Unicode in code comments or config comments. Plain words:
  `WARNING:`, `NOTE:`, `DO NOT`. No box-drawing separators, no `->` arrows drawn with
  `→`. (`docs/` is exempt — its markers are load-bearing navigation in long documents.)
- **No changelog phrasing.** A comment describes what is true now, in the present
  tense. Repo history belongs in git and `docs/plans/`. The constraint survives the
  rewrite; the history framing does not:

  > ✗ *"This used to be a hand-copied duplicate of that ladder, and the copy had
  > already drifted: it never passed `action_id`, so every replayed row stored NULL."*
  > ✓ *"Call the shared ladder; do not re-inline it. A copy drifts — one here silently
  > stopped passing `action_id`, storing NULL in the duplicate-detection column."*

  Same facts, same warning, half the words, and it no longer requires the reader to
  care about a state of the world they never saw.

**Note the existing rule is not the missing one.** `CONTRIBUTING.md:429` already says
*"Comments explain 'why' not 'what'"* — and this codebase **passes** that; it is all
"why". The missing rule is a **length** rule.

---

## 4. Scope

**IN**
- Top ~60 `src/` files by absolute doc-line count. Representative:
  `webapp/dashboards/allocations/blueprint.py` (829 doc lines), `webapp/jobs/routes.py` (730),
  `sam/projects/projects.py` (588), `webapp/utils/rbac.py` (441), `sam/queries/dashboard.py` (435),
  `sam/integration/xras.py` (411), `sam/fmt.py` (358), `webapp/dashboards/charts/theme.py` (305),
  `webapp/utils/htmx.py` (269).
- The 64 module docstrings >= 30 lines (2,813 lines). Worst:
  `webapp/utils/static_assets.py` (87 lines over 29 lines of code),
  `webapp/api/xras/recheck.py` (75), `webapp/api/xras/roles.py` (71).
- `helm/values.yaml`, `.env.example`, `compose.yaml`, `containers/sam-sql-dev/Dockerfile`.
- `src/webapp/static/js/{layout-axis,theme-toggle,collapse-chevron,nav-view-persistence}.js`.
- `sql/driver.sh` — ~30 of its 36 "comment" lines are commented-out shell. Dead code.
- `CLAUDE.md`. Emoji + spelling sweeps also cover `tests/` and Jinja `{# #}` comments.

**OUT**
- `tests/` density (18.7%), Jinja comment ratio (4%), terraform (3%),
  `.github/workflows/` (17%), `helm/templates/` (16%). Terraform is arguably *under*-commented.
- Defensibly dense, leave alone: `pytest.ini`, `helm/tests/*.sh`, `scripts/lib/common.sh`.
- **Do not cut** — these are spec, not prose. Module headers may still be trimmed:
  `sam/xras/errors.py` (exact-byte XRAS error strings, plus a note that the canonical
  doc is wrong in seven places), `sam/schemas/forms/xras.py` (measurements against a
  41-payload corpus, each tied to a named test), `scheduling/tasks/xras_sweep.py` and
  `xras_notices.py` (deploy-coupling traps, fail-open registry),
  `sam/manage/allocations.py` (34% of doc lines are `Args:`/`Raises:` on a public write
  API), `sam/manage/xras_remediation.py`, `webapp/api/xras/recheck.py` below its header.
- `docs/` content.

---

## 5. Phases and the handoff protocol

The work is context-heavy, so each phase is sized to **one session** and must be
resumable from `docs/plans/DOC_SLIMMING.md` alone.

**Every phase ends by, in one commit:**
1. Running `python scripts/doc_ratio.py` and pasting the numbers into the status table.
2. Marking its row `DONE <date>`, with a one-line note on anything deferred.
3. Listing any file it deliberately skipped and why.

**Every phase starts by** reading that table and re-running `doc_ratio.py` to confirm
the recorded starting number still holds. Nothing else from prior sessions is needed.

| # | Phase | Files | Judgment | Gate | Status |
|---|---|---|---|---|---|
| 0 | Tooling and rules | ~8 | none | suite green | **DONE 2026-08-22** |
| 1 | Mechanical sweep | 433 | none | script-verified | **DONE 2026-08-22** |
| 1b | Changelog phrasing (45 lines) | 45 | medium | `PHRASING_EXEMPT` emptied | **DONE 2026-08-22** |
| 2 | Configs and JS headers | 6 | low | `helm template` byte-identical | **DONE 2026-08-22** |
| 3 | Module docstrings >= 30 lines | 64 | medium | suite green | open |
| 4 | `Args:`/`Returns:` restatement | ~40 | low | suite green | open |
| 5-8 | Narrative prose, ~12 files each | ~48 | **high** | suite green, per-file review | open |
| 9 | `CLAUDE.md` + `README.md` | 2 | medium | links resolve | open |

### Measured progress

`scripts/doc_ratio.py`, run at the end of each phase. Baseline is
`origin/staging` at c40cbf0.

| After phase | src/sam | src/webapp | src/cli | src/scheduling | TOTAL src/ |
|---|---|---|---|---|---|
| baseline | 37.0% | 34.6% | 21.8% | 41.9% | **34.3%** |
| 0 | 37.0% | 34.6% | 21.8% | 41.9% | **34.3%** |
| 1 | 37.0% | 34.6% | 21.8% | 41.9% | **34.3%** |
| 1b | 37.0% | 34.6% | 21.8% | 41.9% | **34.3%** |
| 2 | 37.0% | 34.6% | 21.8% | 41.9% | **34.3%** |

Phases 0 and 1 move the ratio by design: neither removes prose. Phase 0 lands
the gate, and Phase 1 rewrites decorative characters in place rather than
deleting lines -- 20,078 rich characters in code and config comments down to
8, but the same number of comment lines. **The ratio starts moving at
Phase 2.** Reporting a flat number here rather than omitting the row is the
point of the table: a phase whose value is readability should be visible as
one that did not change the volume.

`src/cli` at 21.8% is the target the other trees are being steered toward;
it is the same repo, written earlier.

### Phase 0 — tooling and rules
Land the gate before the cleanup, so every later phase is measurable.
1. Port `hpc-scheduling-tools/tests/test_docs.py` (stdlib only, no new dependencies).
   Keep `test_docs_use_american_spelling`, `test_markdown_links_resolve`,
   `test_cited_paths_exist`, `test_docs_stay_within_length_budget`, and
   `test_docs_avoid_changelog_phrasing`. Drop the other three.
   Keep its shared helpers (`tracked_files()` over `git ls-files` — never a directory
   walk; `unfenced_lines()`; the fenced-code-blanked-not-removed rule so line numbers hold).
2. **Retune for prose-only spelling**: match comment lines and docstring spans
   (`tokenize` + `ast`) in Python, unfenced lines in markdown, `#` lines in YAML/shell.
   Never identifiers or string literals. Do **not** add `analys` to the word list —
   "analysis" is correct American English and would false-positive
   `sam/xras/extractors.py:267`.
3. **Retune `CHANGELOG_PHRASES` for this repo — do not port the list verbatim.**
   Drop `coexist`: it appears 18 times here and **17 are correct present-tense use**
   ("a wedged run cannot coexist with its successor", "two conventions coexist
   deliberately", "so two controls can coexist"). It is ordinary vocabulary in this
   codebase and would be a pure false-positive generator. Keep `successor to`,
   `the old behavio(u)r`, `formerly`, and add the forms this repo actually exhibits:
   `used to be|live|call|read|pass|sit`, `we used to`, `renamed from`,
   `before PR #<n>`, `in the old`, and
   `this (comment|docstring) (originally|previously)` — that last one catches
   `helm/values.yaml:55`, where a comment argues with its own prior revision.
   Also do **not** add `no longer`, `previously`, or `historically`: all three are
   commonly correct present-tense description here.
   That tuned list yields **49 sites outside `docs/plans/`**, ~35 of them the
   `used to be` family. **Seed them into `PHRASING_EXEMPT` so Phase 0 lands green**;
   Phase 1b empties it.
4. **Seed `LINE_BUDGETS` from every doc's current line count**, so nothing fails today
   and only growth fails. This is what lets `docs/` stay as records with no carve-out.
5. Add `scripts/doc_ratio.py` — the `tokenize`/`ast` script behind these numbers.
   Per-tree table, `--top N` by absolute doc lines, `--since <rev>` for a window.
   Same methodology as #461 so numbers stay comparable.
6. Get green: fix the 4 broken markdown links (3 are the same
   `PROJECT_AND_ACCOUNT_LIFECYCLE.md` ref in `docs/xras/outgoing/XRAS_WRITE_FIXUPS.md`,
   1 is `assets/schema.png`), and the dangling `docs/plans/EXPIRATION_NOTICES.md` in
   `CLAUDE.md` (the file moved to `implemented/`).
7. Write § 3's rules into `CONTRIBUTING.md` and `CLAUDE.md`.
8. Remove any `paths-ignore: ['**.md', 'docs/**']` from the workflow that runs the
   suite, or a docs-only PR meets no gate.

### Phase 1 — mechanical sweep (no judgment, big line count)
Script-driven, repo-wide, including `tests/`. All of it verifiable by re-running the script.
- **Separators**: 720 pure box-drawing lines, incl. legacy `#-----bh-`/`#-----eh-`
  markers (`sam/projects/projects.py`, `sam/queries/fstree_access.py` has 29 alone) and
  the `{# ═══ CREATE MODALS #}` banners in Jinja.
- **Emoji and rich characters** in comments. Census, and it splits cleanly:

  | | in comments | in code |
  |---|---|---|
  | `src/*.py` | 5,602 | 99 |
  | `tests/*.py` | 5,902 | 35 |
  | `static/js/*.js` | 710 | 1 |
  | Jinja `{# #}` | 5,085 | — |

  **The one place care is needed is Jinja**: `→` also appears in rendered UI
  (`{{ start }} → {{ end }}`, `<span class="text-muted">→</span>`) and must stay.
  Strip inside `{# #}` and `<!-- -->` only. In Python and JS the ratio is ~56:1, so the
  sweep is safe; the ~100 in code are user-facing CLI/flash strings and stay.
- **British spellings**: 550 hits across 225 files, **174 lines in `src/*.py`, all in
  comments/docstrings, zero in code**.
- **Dead code**: `sql/driver.sh`'s commented-out shell block.

### Phase 1b — changelog phrasing (49 sites)
Empty the `PHRASING_EXEMPT` allowlist seeded in Phase 0. Bounded and enumerable, but
each site is a rewrite, not a deletion — apply § 3's rule: keep the constraint, drop
the history framing, present tense.

Roughly 35 of the 49 are the `used to be` / `used to live` family. Concentrations:
`src/sam/xras/handlers/` (4), `tests/unit/` (12), the two identical
`xras_*_card.html` comments (a copy-paste pair — fix once, apply twice),
`webapp/api/xras/recheck.py:176`, `sam/queries/xras_actions.py:200`,
`webapp/dashboards/charts/base.py:231`, `helm/values.yaml:55`.

**DONE 2026-08-22.** 45 lines across 44 files, each rewritten rather than
deleted. `PHRASING_EXEMPT` now holds only the two permanent entries.

**Two sites genuinely need the history and stay in the allowlist permanently**
— they document a deliberately inverted assertion, and the inversion is the point:
`tests/stress/test_parking_is_explained.py:125` and `tests/unit/test_task_runner.py:417`
("This assertion is INVERTED from what it used to be"). This is the analog of the
sibling repo's `PHRASING_EXEMPT = {"docs/CASPER_HOOK_MIGRATION.md"}`.
`scripts/repair/RUNBOOK-missing-projects.md` is a before/after runbook and is the
third candidate — judge it when you get there.

### Phase 2 — configs and JS headers
1. `helm/values.yaml`: the 54-line Redis benchmark essay above `maxmemoryMB: 192` → 4
   lines keeping the `allkeys-lru`-is-instance-global warning and the
   `INFO stats:evicted_keys` check; measurements to the already-referenced
   `docs/plans/implemented/REDIS.md`. Drop the 68 inline restatements
   (`# Number of identical pods to run`); **keep** those stating legal values or
   derivation (`workers: ""  # "" -> 2x requests.cpu+1`). Compress the XRAS block's
   release narrative but keep both irreversible-write warnings verbatim.
2. `.env.example`: keep operator instructions (uncomment-to-switch blocks, the
   `gen_api_key.py` invocation). Cut the 17-line compose pass-through essay and the 54
   lines describing two fully commented-out plugins that already defer to their own
   `.env.example`.
3. `containers/sam-sql-dev/Dockerfile`: 19 lines explaining a `COPY` that no longer
   exists → 2 lines keeping "an empty `initdb.d/` is not tracked by git, so a COPY of a
   missing directory fails the build".
4. JS: `layout-axis.js` (41-line header on a 105-line file, forward-referencing an
   unmerged PR) and `theme-toggle.js` (47 lines, half re-explaining its sibling) →
   narrative to `docs/plans/implemented/{DARK_MODE,MOBILE_CHARTS}.md`, which already
   cover it. Keep the CSP-nonce constraint that forces these to be external files.
5. **De-duplicate**: the XRAS write-capability warning is copy-pasted verbatim into
   `.env.example`, `helm/values.yaml`, and docs; the fs-scans paragraph into two. Three
   copies will drift → one canonical, two pointers. Bias toward leaving *more* in the
   operator-facing file, since that warning guards an irreversible production action.

### Phases 3-4 — module docstrings, then signature restatement
Mechanical enough to review quickly. Phase 3 relocates the 64 headers >= 30 lines;
Phase 4 removes `Args:`/`Returns:` blocks that restate annotated signatures.

### Phases 5-8 — narrative prose
Where the volume is (~2,300 lines across `blueprint.py`, `jobs/routes.py`, `htmx.py`,
`theme.py`) and where load-bearing content is interleaved. **No mechanical pass here.**
~12 files per session, ordered by absolute doc lines, one commit per file or tight group.

**Worked example** — `webapp/utils/rbac.py`, currently 70%:
- 30-line module header → ~8 lines. The "no dependency on the SAM `role_user`/`role`
  tables" fact is a real trap and stays.
- `class Permission` docstring: 8 lines → 1.
- The `# User management` / `# Project management` group labels over the 40-member enum
  **stay** — navigational, cheap, one line each.
- `has_permission_for_facility`: 20-line docstring over 13 lines of code. Drop the
  `Args:`/`Returns:` restatement; keep the orphan-project `None` semantics.
- `has_permission_any_facility`: **keep** its "Contrast with `has_permission`"
  paragraph — two near-identical names, that is disambiguation, not narration.

### Phase 9 — `CLAUDE.md` and `README.md`
Last, so the rules are already proven by the work. Three sections are over half of
`CLAUDE.md`'s 1,008 lines, and **their destinations already exist**:
- § *Charts* (164) → `docs/plans/implemented/{CHART_ARCHITECTURE,DARK_MODE,MOBILE_CHARTS,TABLET_CHARTS}.md`
  (2,935 lines already covering it). Keep the ❌/✅ gotcha list — those are the
  mistake-preventers — and a pointer.
- § *Notifications* (115) → `docs/plans/implemented/NOTIFICATION_FRAMEWORK.md` (978
  lines). Keep the fail-closed warning and the `SAM_TASKS_DISABLED` fail-open trap.
- § *Important Patterns & Conventions* (243) largely **stays** — it is the section that
  actually changes behavior. § 11 (static assets, ~40 lines of measurement) compresses
  to its rule table.

Also: reconcile the known duplicate structure tree (`README.md` ~:555 and `CLAUDE.md`
§ *Code Organization*) — one canonical, one pointer. Consider adding
`src/webapp/dashboards/charts/README.md` as the charts destination; `src/cli`,
`src/webapp`, and `src/querykit` each already have one.

---

## 6. Constraints and risks

- **No source changes.** Comments, docstrings, and config comments only. Any commit
  whose diff touches a non-comment line is a mistake.
- **One test pins docstring text.**
  `tests/unit/test_xras_extractors.py::test_the_two_known_exceptions_are_documented`
  asserts `'500026'`, `'500088'`, and `'not injective'` appear in
  `sam/xras/opportunity_types.py.__doc__` (a 53-line module docstring). Trim around them.
- Many tests read source as text (`test_route_map_parity`, `test_task_ledger`,
  `test_no_fstring_sql`, `test_static_assets`). Most are AST walks that will not care,
  but the full suite runs per phase.
- `helm/tests/test-cronjob-render.sh` greps `values.yaml` for task names — do not
  disturb what it matches.
- Docstrings feed no tooling (no sphinx/mkdocs/pdoc/doctest), so there is no
  generated-docs breakage.
- The emoji sweep must not touch rendered Jinja markup or CLI/flash strings.

## 7. Verification

1. `python scripts/doc_ratio.py src/` before and after each phase. Targets: `src/`
   34.2% → ~22-26%; XRAS subtree 42.5% → under 30%; `helm/values.yaml` 61% → under 25%;
   `.env.example` 80% → under 50%.
2. Per commit, `git diff -U0 | grep '^[+-]' | grep -v '^[+-][[:space:]]*[#*]'` should be
   empty except docstring-quote boundaries.
3. `source etc/config_env.sh && pytest` green after each phase (you run this by hand).
4. Phase 2: `helm template helm/ > /tmp/after.yaml` must be byte-identical to the same
   from `staging`. Plus `bash helm/tests/test-cronjob-render.sh`.
5. Phase 1-2: `docker compose up webdev --watch` smoke — confirm the layout cookie and
   theme toggle still work, and that no UI arrow or status glyph disappeared.

## 8. Follow-ups, deliberately not in this sprint

- **British-spelled identifiers**, logged here so the decision is recorded rather than
  lost: `mark_panel_authorised` in `sam/xras/handlers/_allocations.py:259` and
  `handlers/base.py:196` (production, with call sites), plus ~20 test names
  (`test_count_honours_every_filter`, `test_others_takes_the_neutral_colour`,
  `test_end_date_normalises_to_the_end_of_that_day`, ...). Renaming is a source change;
  take it as its own PR or leave it.
- **The three skipped doc checks** (`test_every_doc_is_reachable_from_the_readme`,
  `test_cited_tests_exist`, the `.env.example` tunables test) — each needs its own
  cleanup to go green.
- Terraform at 3% comments is the inverse problem and is not addressed here.
