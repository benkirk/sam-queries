---
name: unplanned-city-sweep
description: >-
  A retrospective sweep for the duplication, drift and cruft that only shows
  once a run of related PRs has landed: helpers to lift to a common home,
  concepts implemented three ways, dead or tangled CSS and JS, and new shared
  pieces an older surface should adopt. Load after a series of merges (window
  mode) or to sweep one area of the tree (area mode). Produces a ranked report
  for Ben to pick from, then one PR with a commit per picked finding, and
  updates the ledger.
---

# Unplanned-city sweep

A city that grew without a plan works street by street and makes no sense from the air. A run
of PRs does the same: each was right alone, and the drift is between them. This sweep reads them
from the air.

The output is a **ranked report first**. Ben picks; the picked findings become one PR with a
commit each; the rest go to the ledger, `docs/plans/UNPLANNED_CITY_LEDGER.md`. "Nothing worth
doing" is an acceptable report.

## 1. Pick the mode

- **Window.** Read everything merged since the ledger's newest end commit, or the PRs Ben names.
  Find the range with `git log --oneline --merges <end>..origin/staging`, then read each PR body
  and the combined diff (`git diff <end>..origin/staging`).
- **Area.** One of `py`, `js`, `css`, `templates`, read across the whole tree. Start from the
  ledger's untriaged list for that area.

Branch from `origin/staging` before changing anything.

## 2. Read as one design

Apply the unplanned-city litmus to the window as a whole, not per commit:

- prose (docstrings, runbooks, comments, CLAUDE.md) still describing an earlier step's behavior
- a later step putting a silent or untested path on the default route
- the one branch a later step's assumptions rest on being the untested one
- a new route or view covered only by a route-map pin

## 3. Run the inventory

```bash
scripts/sweep_inventory.py --since <end-commit> --top 30   # window mode
scripts/sweep_inventory.py --area css --top 40             # area mode
scripts/sweep_inventory.py --area js --jscpd               # add jscpd clones (npx)
scripts/sweep_inventory.py --area docs --gh                # plans ready to retire (gh: merged PRs)
```

Every detector is a lead, not a verdict. Read the code before reporting anything it lists.
For `js` and `css`, always add `--jscpd`: in the js sweep the jscpd clone led to the bug, while
`js-dup`'s shared names were mostly short local helpers (`has`, `sync`). `js-dead` reports
`window.*` exports nothing else names and actions registered or used on one side only. jscpd
prints every pair; one whose lines straddle a `/* ==== */` banner is still banner noise. A class
styled on purpose before anything uses it goes in `CSS_KEEP` with its reason, not in a deletion.
Record the whole-tree totals line in the ledger's metrics table at the end of the sweep.

## 4. Heuristic passes

Run each pass and collect findings. The examples are real.

- **Lift.** A `_private` helper imported from another module wants a public home in the layer
  both callers can reach (`private-imports`). So does the same helper written twice
  (`dup-functions`, jscpd). Helpers too small for `dup-functions` (env readers, date parsers)
  show as one name defined in several modules (`py-dup-names`). Example: the rolling-window
  builders imported from `rolling_usage.py` into fstree, folded into `batch_charges` in #712.
- **Consolidate.** Three or more implementations of one concept go behind one facade, even at
  real refactor cost. The exception is when layer rules forbid it. Example: the bucketed caches
  behind `webapp.caching`; the usage cache was the last single-dict holdout.
- **Convention drift.** Several idioms for one thing: the leaf-versus-subtree rule exists in
  three query modules, and date arguments are parsed several ways. Pick the house idiom and
  move the others to it. Grep the idiom across the layer before proposing.
- **Bypassed helper.** A lifted helper fixes nothing until its call sites use it, and the sites
  that still hand-roll the idiom are invisible to every duplicate detector: each copy is one
  line. After any Lift, grep the raw idiom across the layer and add its pattern to
  `HELPER_BYPASS` (`helper-bypass`). Example: the filters sweep found 21 `getlist`
  comprehensions with no helper at all, and 11 flag, sort and page reads beside `read_flag` /
  `read_sort` / `read_page`, which already existed.
- **Delete.** Dead CSS classes (`css-dead`, held at zero by `test_css_dead.py`), JS functions nothing calls
  (`js-dead`), compatibility shims whose callers are gone, and options no caller passes.
- **Retire plans.** A top-level `docs/plans/*.md` whose PRs have merged and that nobody has
  touched lately moves to `docs/plans/implemented/` (`plans-stale`). The detector holds back
  any plan whose `**Status:**` line says unbuilt, deferred, brainstorm, sketch or in progress,
  and it is only a lead: read the plan, and flip a stale "in review" Status line to what
  shipped. `git mv`, then fix every reference. The docs gate catches back-ticked paths in docs
  outside `docs/plans/`; grep the basename for the rest, including `.env.example`,
  `scripts/sql/*.sql`, `tests/perf/baselines.json` and other plans (the #641 triage). Run
  window mode right after the merge that lands a plan.
- **Legibility.**
  - CSS: a feature section that has outgrown its shared file, `!important`, repeated
    declaration blocks (`css-shape`), inline `style=""` (`inline-styles`), and Bootstrap 4
    class names that style nothing under 5.3 (`bs4-classes`; `thead-light` was 15 silent no-ops).
  - JS: the same function in several files, and htmx listeners spread across files (`js-dup`).
    A jscpd clone between two JS files can be two modules binding the *same markup*: grep the
    templates for the selector before calling it duplication (the js sweep's admin-card sort
    ran twice per click). Also look for `toISOString()` used as a local calendar date.
  - Prefer moving a feature's rules into its own file, as `allocations.css` did, over adding
    another section to `dashboard.css`.
  - Tables: a header wider than its column's figures (measure the label against the widest
    cell with a `Range`, never by eye). `.col-num` / `.col-shrink` headers wrap at their spaces
    by default, so anything that must not lead a line (a sort icon, `#`) joins its word with
    `&nbsp;`. `scripts/ui_snapshots.py --headers --layout desktop --layout mobile` over every
    page is the proof (wire-dashboard-feature §7).
  - Modals: an alert per fact, prose restating a glossary term beside its own icon, a read-only
    pair restating the title (`modal-alerts`, ratcheted). Facts go in `.modal-facts`, help in
    `tip=` / a term, identity in `modal_title`. Open the opener first: two "modals" in the
    sweep had none (an orphan is a deletion). Prove it with `ui_snapshots.py --recipes`
    heights before and after.
- **Propagate.** List the shared pieces the window introduced: macros, chart families, JS
  modules, query helpers. For each, find an older surface doing the same job the old way. The
  sunburst started on the allocations page and then moved to job history; that is the shape to
  look for. Record candidates in the ledger even when nobody picks them.
- **Ratchet.** Where a fix is cheap to pin, add an equality ratchet so the city does not regrow.
  Models: `tests/unit/gates/test_css_tokens.py` (raw colors) and
  `tests/unit/gates/test_no_fstring_sql.py`. Only ratchet a count that is going down.

## 5. Report

One ranked list. For each finding, give:

| field | content |
|---|---|
| category | lift, consolidate, drift, delete, legibility, propagate, ratchet |
| where | `file:line` for each site |
| proposal | the target home or shape, in one sentence |
| cost | files touched; whether a snapshot or baseline moves |
| risk | whether output, markup or a legacy API could change |

Rank by value over cost. Mark anything that changes behavior. Then stop and let Ben pick.

## 6. Do the picked work

- One branch and one PR against `staging`, with a commit per finding. Each commit passes its own
  tests.
- **If output could move**, capture it before and after and compare: a parity capture of the
  affected functions on both MySQL and Postgres. Keep throwaway capture scripts untracked under
  `utils/profiling/`. #712's capture found a real backend inconsistency that no test covered.
- **Front-end changes** get the same before/after, in a browser. Serve the old code from a
  worktree with `scripts/dev_server_alt.sh <worktree> <port>` (outbound off, own Redis DB; never
  copy a running server's env by hand) and the branch on another port. A change meant to look
  the same has two proofs, by what it does to the DOM:
  - *DOM kept* (a class swap, a CSS move): `ui_snapshots.py --styles` on both, then `--compare
    before after`: zero captures differ. A custom property that differs alone is counted, not
    failed (`--strict` fails it). A live page (status charts, a clock label) needs
    `--px-tolerance 0.05` and still differs where its content moved; leave it out or say so.
  - *DOM restructured* (markup moved onto a macro): every element path shifts, so `--compare`
    is noise. Shoot the component with `--element SELECTOR` on both, then `--compare-pixels`.

  Pin time with Playwright's `page.clock.install` for date logic. Load
  `wire-dashboard-feature` for its smoke and gates.
- **An aesthetic sweep** changes looks on purpose, so `--compare` cannot be its proof. Shoot
  `ui_snapshots.py` before and after in all six states and review them by eye. Measure contrast
  (WCAG ratio computed in the page, both themes) and spacing (`getBoundingClientRect`) rather
  than judging a scaled screenshot. Name every departure from the NCAR brand or Unity in its
  commit and in the ledger entry's deviations list.
- **Measure before claiming a speedup**, with repeats, on both backends. A change that measures
  flat is dropped and recorded, not shipped.
- Run the gates the change touches: route-map parity, chart fingerprints, CSS tokens, docs, and
  the perf tier when query counts can move.

## 7. Close out

- Add a ledger entry: mode, range or area, end commit (in area mode, the `origin/staging` commit
  the branch started from), done (with the PR), tried and dropped
  (with numbers), open items, and a metrics row from a whole-tree inventory run.
- Move the unpicked findings onto the entry's open list or the untriaged list.
- **Growth rule:** when a sweep meets a new class of problem, add the heuristic here, or a
  detector to `scripts/sweep_inventory.py` with a fixture test in
  `tests/unit/gates/test_sweep_inventory.py`, in the same PR.

## Boundaries

- The legacy-compat API blueprints (`directory_access`, `project_access`, `fstree_access`,
  `queue`, `wallclock_exemption`) take additive changes only, and their response bytes do not
  change.
- Layer rules hold: `sam` never imports `webapp`; nothing under `src/scheduling/` imports Flask,
  Click or rich; chart helper modules stay free of matplotlib
  (`tests/unit/gates/test_chart_module_boundaries.py`).
- Do not build for failures nobody has seen. A sweep removes code more often than it adds it.
- A refactor may change visuals, but say so in the commit, and regenerate chart fingerprints in
  that same commit.
- Follow the comment budget in CLAUDE.md §12. A lifted helper keeps a one-line docstring.
