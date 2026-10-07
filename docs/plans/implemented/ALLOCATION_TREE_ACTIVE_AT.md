# "Active at" on the allocation tree sees inactive projects

**Status:** implemented 2026-10-07. Found while answering "when did WYOM0201 last have an
active allocation?" — the project editor's Allocations tab showed nothing for any date,
because the project is inactive.

## The symptom

`/admin/project/WYOM0201/edit?tab=allocations` with **Active at: 2026-05-02** rendered an
empty tab. WYOM0201 holds three allocations (Derecho 3.9M, Casper 10,000, Campaign_Store
26), all 2024-07-01 → 2026-06-30, none deleted, none renewed; the project is `active=0`.
For 2026-05-02 all three should show.

## The cause — the flag was applied twice

The node list was filtered on the project **flag** before the `active_at` date was applied,
in two places, so fixing either alone changed nothing visible:

1. `htmx_project_allocation_tree` (`src/webapp/dashboards/admin/projects_routes.py`)
   kept `[n for n in tree if n.active]` — an inactive root gave an empty node list.
2. The shared `project_tree_rows` macro (`src/webapp/templates/dashboards/shared/project_tree.html`),
   called with `active_only=true`, skipped an inactive node *and its whole subtree*, root
   included.

The picker therefore could not do what it says: the flag answers "today", the date is
supposed to answer "then".

## As built

**Predicate.** A node is shown when it is the root, is active, or holds a *displayed*
allocation row at `active_at` — the row `Account.display_allocation` picks: window
contains the date, or ended within the 90-day post-expiry window, or not yet started. That
is the rule the root already followed, so one rule drives the whole table. Ancestors of a
shown node are kept so rows nest. `_visible_tree_nodes(root, tree, by_project)` in
`projects_routes.py` computes the set *after* `build_user_projects_resources_batched` has
run for the whole tree (the kernel never filtered on the flag; a node with nothing to show
simply has an empty list).

**Macro.** `project_tree_rows` takes `visible=` (a set of projcodes); when given it is the
whole visibility rule for the node and its children, replacing `active_only`. The existing
`row-inactive` class and `inactive` state tag fire for any admitted inactive node, so a
reader can tell "was live then, retired since" from "live now" with no new styling. The
other callers (user dashboard hierarchy, Resource Details scope tree) pass nothing and are
unchanged.

**Still flag-gated, on purpose:**

- Exchange eligibility (`descendant_projcodes`) counts active descendants only.
  `_exchange_candidates` never offers an inactive project, so counting one would show a
  button that opens the "needs two" notice. (Pre-existing and untouched: the count also
  admits grace-window and future rows that the candidates' strict window excludes.)
- The carve-out residual walk skips nodes with no active children; `get_carveout_frontier`
  skips inactive projects, and the residual is a today's-editing concept.
- Add / Extend / Renew / Align / Notify and per-row Edit stay enabled on inactive nodes:
  Renew on an inactive project is the operator's likely intent here, and fixing a retired
  child's end date is legitimate. No permission change.
- The Resource Details page (`src/webapp/dashboards/user/blueprint.py`) filters children on
  the flag but takes no `active_at`; out of scope.

## Tests

`tests/unit/webapp/test_allocation_tree_active_at.py`: the predicate with factories on the
SAVEPOINT session (inactive root always in; inactive child in only while it holds a row;
an inactive middle node with no row is kept when its leaf shows), the macro's `visible=`
rendering with and without the set, and route smoke on ANY inactive snapshot project with a
dated allocation (fragment inside the window names the resource; today renders; the
`?active_at=` deep link lands on the Allocations tab).

## Related

- Timeline of a project's end: `docs/presentations/samuel/_2-concepts.qmd`, "The end of an
  allocation, on a timeline" (90-day grace, 180-day drop) — the same question an operator
  is asking when they pick a past date here.
- `docs/plans/implemented/ALLOCATION_TREE_EDITING.md` for the tree's editing rules.
