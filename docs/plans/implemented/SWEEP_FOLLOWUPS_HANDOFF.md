# Handoff: follow-ups to the templates sweep (#726)

**Status:** unbuilt; written 2026-10-04, after the five sweep rounds were cascaded into #726
(open against `staging` at writing, CI running).

## Context
#726 carries templates sweep rounds 1–5 (ledger entries 5–9 in
`docs/plans/UNPLANNED_CITY_LEDGER.md`). Before the cascade the whole stack was reviewed as one
diff: three read-only review lanes plus a rendered pass (42 modals opened, 31 forms submitted
with an invalid value, both suites, perf, the browser tier). Seven fixes went onto the branch.
What is left is in the PR's "Open after this PR" list plus a few review items that did not make
that list.

Ben picked ten of them for one follow-on PR. They are small; five are real defects.

## Decisions already made
- **One PR, a commit per item**, in the order below, each green on its own.
- **Deferred, do not start:**
  - Resource Details;
  - the XRAS request-detail buttons;
  - the page tabs;
  - the brand-drift tokens (the fix moves chart fingerprints);
  - Resource edit's missing sysadmin / organization pickers (`EditResourceForm`,
    `Resource.update`): feature work, not sweep work;
  - Renew's and Extend's resource tables at 390px: they scroll inside their own
    `table-responsive`, and the fix is a design call.
- **An item that turns out larger than its size here stops and is reported**, not grown.

## Plan
**Base:** if #726 is merged, branch from `origin/staging`. If not, branch from
`origin/sweep-templates-ux-2026-10` and target it, then retarget to `staging` when it merges.

### Defects first
Each gets a regression test.

1. **The panels cascade is a 403 for an allocation-type creator.**
   - `htmx_panels_for_facility` (`src/webapp/dashboards/admin/projects_routes.py:226-229`) is
     `@require_permission_any_facility(Permission.CREATE_PROJECTS)`.
   - New Allocation Type uses it for its Facility → Panel cascade
     (`admin/fragments/create_allocation_type_form_htmx.html:22`), and that form's create
     permission is `CREATE_FACILITIES` (`facilities_routes.py:119`).
   - A holder of `CREATE_FACILITIES` alone gets a 403 on the cascade, so the Panel select never
     fills.
   - `require_permission_any_facility` (`src/webapp/utils/rbac.py:288`) takes one permission.
     Admit either permission; look for an existing any-of helper in `rbac.py` before adding one.
   - The other two callers (`create_project_cascade_row_htmx.html:14`,
     `edit_project_details_htmx.html:80`) must keep working.

2. **`test_ticket_card` fails under xdist.**
   - `tests/unit/webapp/test_ticket_card.py:66`, `test_a_request_without_tickets_renders_no_ticket_row`,
     fetches the card unfiltered and asserts `'closed without an account'` is absent.
   - The `ticketed_request` fixture (same file, line 11) commits a request with a closed ticket.
     Routes read `db.session`, which sees committed rows, so another worker's fixture row shows
     up in this test's page.
   - Scope the test to rows it owns: commit its own ticketless request and fetch with
     `?search=` on that address, as the other two tests do.
   - It passes serially today. Prove the fix with `-n auto` runs of the file alongside the rest
     of `tests/unit/webapp`.

3. **The Allocations project list's initial order.** Done: `_shown_used` in
   `src/webapp/dashboards/allocations/blueprint.py` sorts on the value the cell shows (ticked by
   `RESOURCE_DETAILS_SWEEP_HANDOFF.md` round B).
   - `src/webapp/dashboards/allocations/blueprint.py:1062` sorts rows by `r['used']`, the pool's
     usage.
   - The Used cell of a shared row shows `self_used` and sorts on it
     (`dashboards/shared/project_tree.html`, the inheriting branch of `allocation_cells`), under
     a header marked `sort-desc` (`allocations/partials/project_table.html:17`).
   - With pool members in the list the first paint is not in the order the header claims.
   - Sort on the value the cell shows: `self_used` when `is_inheriting`, else `used`.

4. **A dead Add Exemption button in the user details modal on Allocations pages.**
   - `dashboards/user/partials/user_card.html:401-407` opens `#addExemptionModal`.
   - The shell is included only by `dashboards/admin/base_admin.html:56`
     (`admin/fragments/exemption_modals.html`).
   - `allocations/base_allocations.html:31` includes the user details modal, so on
     `/allocations/transactions` the button targets a modal the page does not have. The edit
     buttons beside each exemption (`#editExemptionModal`) have the same problem.
   - This is on staging already; it is not from the sweep.
   - Prefer rendering the exemption controls only where the host supplies the shell. Including
     the shell on the Allocations base would open a modal from inside an open modal; read
     CLAUDE.md §9 "Modal fragments" and the `show-user-details` comment in
     `static/js/actions.js` before choosing that.
   - `HTMX_FRAGMENT_SHELL_DEPS` in `tests/unit/gates/test_modal_shell_contract.py:392` lists
     these ids for `user_card.html`. Work out why the gate passes with the shell missing on one
     host, and close that hole if it is small.

5. **A stale title over an Allocate down or Exchange notice.**
   - Both forms set the header out of band with `modal_title`
     (`allocate_down_form_htmx.html:20`, `exchange_allocation_form_htmx.html:16`).
   - `admin/fragments/allocation_form_notice_htmx.html` loads into the same containers and sets
     no title, so the header can still name the previous allocation.
   - Not reproduced in a browser: the openers are hidden when a notice would result.
   - The notice renders from `projects_routes.py:1287`, `1289`, `1435`, `1441`, `1444`. Give the
     notice a `modal_title` call; each call site needs to pass which modal it is in.

### Visual and cleanup

6. **Toasts.**
   - `dashboards/base.html:211` and `:217` are `text-bg-danger` / `text-bg-success`;
     `static/js/htmx-config.js:39` sets `text-bg-<variant>` for `showToast`.
   - They are the last solid-fill surfaces beside the tinted alerts. Reuse the alert recipe
     (`dashboard.css:1125`, `--alert-hue` / `--alert-tint` in `variables.css:164-169`, dark at
     `:385`).
   - `btn-close-white` on both close buttons goes with the fill.
   - Measure contrast in both themes, alpha blended; do not eyeball it.
   - A visible change: `ui_snapshots.py` before and after.

7. **Exchange and Allocate down: inline field errors.**
   - `_ExchangeAllocationHandler` (`projects_routes.py:1299`) and `_AllocateDownHandler`
     (`:1457`) mix in `FlattenedFieldErrors`, so "Target: Missing data…" lands in the top panel.
   - Both templates are on `form_fields` since #730.
   - Allocate down: `target`, `amount`, `comment` all have visible inputs, so the mixin can go.
   - Exchange has two hidden fields (`resource_id`, `active_at`,
     `exchange_allocation_form_htmx.html:34-35`). Their errors have no input to sit beside and
     must be rerouted to the panel in `render_errors()`; `_AddMemberHandler` is the pattern
     (CLAUDE.md §9, caveat b).
   - Leave Renew, Extend and Align flattened: their fields are table rows.

8. **Leftovers from later rounds.** Verify each has no reader before deleting.
   - Classes `project-resources` (`user/partials/project_card.html:146`) and
     `project-alloc-tree` (`admin/fragments/project_allocation_tree_htmx.html:95`): no CSS, JS or
     test reads them.
   - `render_project_resources(..., usage_warning_threshold, usage_critical_threshold, ...)`
     (`project_card.html:142`) does not read the two thresholds. They are passed from
     `user/blueprint.py:141-142`, `:1468-1469` and `admin/blueprint.py:368-369`, `:708-709`;
     check every template those contexts reach before removing a kwarg.
   - `status_badge` state `open-ended` (`fragments/badges.html:35`, `:56`, `:79`): only the
     gallery loop reaches it.

9. **Stale text.** Say what is true now; no history in the sentence.
   - Ledger, `docs/plans/UNPLANNED_CITY_LEDGER.md` (line numbers approximate):
     - :272, the Bootstrap 4 gate is `test_template_detectors.py`;
     - :293, "its 15 small outline row buttons";
     - :360, the double-space leftover count;
     - :413, "Resource Details and the usage modal still use `render_usage_bar`" (the modal is
       deleted);
     - :421, `group_card.html` named as using `stat-item-block`;
     - :495, "#730 (draft)";
     - :606, the inline `style=""` count.
   - `static/css/dashboard.css:1155` and `static/js/htmx-config.js:224` say a destructive
     confirm's header is filled. It is danger ink plus a rule.
   - `dashboards/fragments/modal_form.html:8`: the usage example passes `'Create Facility'`,
     against the bare-verb rule.
   - `docs/plans/HTMX_API_READINESS.md:66`: the propagate logic lives in
     `_PropagateAllocationHandler`; the file also still mentions `break_inheritance`.
   - `scripts/sweep_inventory.py:4`: "A report, not a gate"; three detectors are asserted in CI.
   - `.claude/skills/wire-dashboard-feature/SKILL.md`: §1 Modals lists only `modal_scaffold`
     (add `modal_title`, `.modal-facts`, `tip=`); §12.6's gate list omits
     `test_template_detectors.py`. The skill has a 250-line budget in the docs gate.
   - `scripts/README.md:15` mentions `--headers` but not `--modal` / `--recipes`.
   - Tick the items this PR closes in ledger entry 9's open list.

10. **Commit the modal recipes.**
    - `docs/plans/modal_recipes.json` (untracked) is the 42-recipe file the ledger's height
      table was measured with: `{name, page, steps, modal}` per modal.
    - Move it beside the script it feeds (for example `scripts/ui_snapshots_modals.json`), name
      it in `scripts/README.md` and in ledger entry 9.
    - Its pages name projcodes and allocation ids from the local dev database (`NMMM0003`,
      `CESM0002`, allocation `25621`). Say so in the README line: a recipe whose row is gone
      prints "not opened" and the run carries on.

## Facts worth having
- **Second dev server:** `scripts/dev_server_alt.sh <worktree> <port>`. Ports 5051 and 5053
  were in use by other sessions on 2026-10-04; 5054 was free. The script flushes Redis db
  `2 + port % 14` before it starts, even if the port then turns out to be taken.
- **Stub login:** `benkirk` / `e2e`.
- **A shared allocation to test with:** #25621 (NMMM0004 on Derecho) under pool root #25620
  (NMMM0003). A carve-out pair: #24512 under #24511 (CESM0002).
- **`make check-all` is green again** as of #726: it had been stopping at
  `scripts/orm_inventory.py` (SQLAlchemy 2.1) and then at `test_reconcile_quotas`, which was a
  real bug in `ProjectDirectory.create`.
- **The browser tier can fail with `net::ERR_NETWORK_IO_SUSPENDED`** on a random page. It is
  host-side; rerun `make e2e` alone before reading app code.
- **`modal-alerts` is pinned at 33** (`tests/unit/gates/test_template_detectors.py`). Item 6
  does not touch it; an alert removed anywhere in a modal body lowers it.

## Verification
- Load the `wire-dashboard-feature` skill before touching templates.
- Per commit: `pytest tests/unit/gates tests/unit/webapp` plus the item's own test.
- Before the PR: `make check-all`. Read each stage's summary line, not only the exit code.
- Browser, for items 4, 5, 6 and 7: both themes, desktop and 390px. Item 7 with a deliberately
  invalid submit, to see the error beside its field.
- Item 6: `ui_snapshots.py` before and after, with the base served by `dev_server_alt.sh`.
- PR against `staging`, as a draft when remote CI is wanted. No CI-skip tokens in the title,
  body or any commit message.
