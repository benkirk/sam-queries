# Handoff: the modal sweep (templates round 5)

**Status:** unbuilt; written 2026-10-04 after sweep 8 (#729, open at writing).

## Context
Ben: modals look bloated, cartoonish or verbose, especially:
- the old admin CRUD (resource / machine / queue / facility / panel / allocation type);
- the Manage Project allocation workflows.

Many predate the `help.term` / `help.help_icon` popovers and the 33 `glossary.g_*` terms.

There are about 75 modal shells:
- 36 use `modal_scaffold` (`dashboards/fragments/modals.html:70`);
- 21 are hand-rolled `div.modal` in 14 files.

Headers come in five saturated fills, painted in `dashboard.css:1153-1250`:
- create is `bg-success`, painted blue;
- edit is `bg-warning`, painted navy;
- `bg-primary`;
- `bg-info` (cyan, unpainted: `edit_project.html:338`);
- `bg-danger` / `bg-secondary` on the outage modals.

That is ledger entry 5's open "app-wide design call" (`UNPLANNED_CITY_LEDGER.md:294`).

**Ben's decisions:**
- quiet accent headers everywhere;
- one PR for all modals;
- the three bugs first.

## Decisions already made
- **Header:**
  - `--surface-accent` background with `--text-title` ink, the project card's expanded-header
    treatment (`dashboard.css:1416`);
  - the verb goes in the title and the submit button, not in a color;
  - only `samConfirmModal` (a destructive confirm) keeps a danger header;
  - `modal_scaffold`'s `variant` stops choosing a fill.
- **Help:** prose that restates a glossary term goes, and the term's `help_icon` / `term`
  stays. A field's help line becomes a popover unless it must stay visible (a constraint the
  user needs while typing).
- **Keep every load-bearing warning** (list below). Trim only decoration and repetition.

## Plan: one PR, a commit per step, each green on its own
**Base:** if #729 (`alloc-project-list-2026-10`) is unmerged, branch from it and target it.
Otherwise branch from `origin/staging`.

**Bugs first.** Each gets a regression test and no visual change.
1. **Create Resource drops two pickers.**
   - `create_resource_form_htmx.html:39-48` posts `prim_sys_admin_user_id` and
     `prim_responsible_org_id`.
   - Neither is in `CreateResourceForm` nor in the CrudSpec `create_kwargs`
     (`resources_routes.py:785`), so `EXCLUDE` drops them silently.
   - Wire them through if `Resource.create` / the columns support them; otherwise remove the
     pickers.
   - Either way the modal drops `lg`.
2. **Detach / re-link / propagate errors are invisible.**
   - `projects_routes.py:2639/2661/2670/2687/2708` return `alert-danger` with status 400.
   - htmx does not swap a 4xx (`static/js/htmx-config.js:4`), so the user sees "Request failed
     (400)" instead.
   - These are also bare routes with no handler or schema (`htmx_detach_allocation` :2630,
     `htmx_link_allocation_to_parent` :2649, `htmx_propagate_to_remaining` :2681).
   - Move them onto `HtmxFormHandler` (CLAUDE.md §9) so errors re-render inline.
   - Add the missing route tests: none exist for these three today.
3. **The panel-session edit is an orphan.** Its route and template exist
   (`facilities_routes.py:94-150`, `edit_panel_session_form_htmx.html`), but no
   `#editPanelSessionFormContainer` shell or opener exists. Check for any consumer, then either
   wire an opener on the panel rows of `facility_card.html` or delete the route and template
   (regenerating the route map).

**Tooling.**

4. `scripts/ui_snapshots.py --modal '<opener selector>'`:
   - clicks the opener, waits for `.modal.show`, and screenshots `.modal-dialog`;
   - takes the same layout x theme matrix and `--styles`;
   - prints the dialog's height.

   Today `_settle` only clicks collapse toggles (`ui_snapshots.py:150`). Openers to reuse:
   - `e2e/test_console_sweep.py:61/91/122/141`;
   - `e2e/test_pr378_regressions.py:43`.

   A body with no live data is rendered and injected per wire-dashboard-feature §12.3.

**Shared pieces.**

5. **Header chrome:**
   - `modal_scaffold` renders the quiet header for both variants;
   - the `dashboard.css` repaint rules collapse to one default plus danger;
   - `.modal-content` and the footer border move from tier-1 `--ncar-gray-light` to a role
     token (`test_css_tokens`);
   - the scaffold spinner drops `fa-2x`.
6. **Hand-rolled shells onto `modal_scaffold`:**
   - `edit_project.html:245-368` (7);
   - `user/fragments/allocation_modals.html:4`;
   - `member_modals_htmx.html:11`, `invitation_modals_htmx.html:8`;
   - `outage_modals.html:6/45`.

   Keep each id. **Trap:** `test_modal_shell_contract.py` finds scaffold shells by regex and
   needs single-quoted literals plus the `container_id=` keyword (around line 140). A
   non-matching call silently drops out of `MODAL_SHELLS`. Short forms drop `lg`.
7. **`form_fields` popover help:**
   - `_label` (`form_fields.html:26-33`) gains `tip=` / `tip_rich=`, rendering
     `help.help_icon` after the label;
   - `help=` stays for a must-see line;
   - add a gallery specimen.
8. **Glossary terms for what is now prose:**
   - fair share, grace window, charging exempt, commission dates;
   - align, carve-out, re-link, propagate, over-carved;
   - the invite link ("asks for phone, country, academic status…", repeated 4 times in the
     invitation forms).

   Tooltips stay one line; popovers may hold HTML (`glossary.html` header).

**Manage Project.** Domain functions are in `sam/manage/allocations.py`.

9. **Edit allocation** (`admin/fragments/edit_allocation_form_htmx.html`, 275 lines, the
   worst):
   - up to 7 alerts (:50, :73 + nested :83, :116, :142, :154, :178, :207);
   - prose :54-63 / :74-82 / :121 restates `g_shared_alloc` / `g_detach`, sitting beside
     those very icons (:53/:71/:120);
   - **Detach:** one confirmation path. Today the Save path asks for a checkbox (:90-97) and
     the Detach-only path an `hx-confirm` (:102).
   - Fix the inaccuracy at :76: detach "cannot be undone", but re-link undoes it, resetting
     amount and dates. If the checkbox label changes, update the error message that quotes it
     (`projects_routes.py:2545-2548`).
   - Move the identity grid (:215-230) into the title.
   - Fix the `btn  btn-` double spaces (:67/:99/:160/:191).
10. **Exchange** (109 lines) and **allocate-down** (116 lines):
    - drop the intro alerts that repeat `g_exchange` / `g_unallocated` (exchange :36-44,
      allocate-down :35-41);
    - hand-rolled selects and inputs become `form_fields`;
    - the empty states built as raw HTML in the routes (:1289-1297, :1443-1475) move into
      templates;
    - legacy icon aliases go (`exchange-alt`, `level-down-alt`).
11. **Renew / Extend / Align:**
    - share a partial for the notify + comment block (renew :176-197 and extend :160-181 are
      copies) and the hand-rolled date block (renew :63-87, extend :77-86);
    - the intro alerts become `term(g_renew_vs_extend)`; align gets a glossary term;
    - remove the inline styles (renew 9, extend 6, align 5);
    - **keep** extend's whole-tree alert (pinned by testid `extend-tree-scope`).
12. **Add allocation and Notify project:**
    - add allocation: the 5-line help (:134-139) repeats `g_shared_alloc`;
    - notify onto `htmx_form` (it hand-rolls its errors and footer, :28-32 and :141-151);
    - its delivery-mode alerts (:35-52) duplicate `fragments/email_preview.html:17-28`.
13. **The tree fragment and linked elements:**
    - the tree's grace-window alert (`project_allocation_tree_htmx.html:43-50`) becomes a
      glossary term;
    - remove the linked-elements inline styles (:47/:137/:246) and the coloured card icons.

**Admin CRUD family** (`facility_modals.html`, `resources_modals.html`; mostly CrudSpec).

14. Identity and fields:
    - the readonly identity pair repeated in 7 edit forms goes into the modal title, or one
      `readonly_pair` macro;
    - hand-rolled selects become `select_field`, which gains the missing error markers:
      - panel create :17-30;
      - the allocation-type facility→panel cascade :16-56 (`select_field` supports `hx_get`);
    - queue edit's start date (:25-31) becomes `readonly_display`;
    - help lines and `<p>`s become `tip=`:
      - queue create :37-44;
      - fair-share override :29-36;
      - disk roots;
      - resource edit "charging exempt" :47;
      - resource-type edit, whose placeholder and help say the same thing (:21-22).
15. *Measure first.* Fold each entity's create/edit pair into one `mode=` template where that
    is a net deletion. CrudSpec stays as is (`crud.py:11` "no new axes").

**The rest of the inventory.**

16. **Invitations:**
    - event form: 10 `help=` lines;
    - invite and roster forms: `<p>` blocks and the 4x invite-link text become the new term;
    - resend preview.
17. **Add member** (`add_member_form_htmx.html`, 109 lines, hand-rolled form and footer) onto
    `htmx_form`; its success fragment drops `fa-2x`.
18. **Create adjustment** (`create_adjustment_form_htmx.html`):
    - the per-type intent alerts (:49-80) become popovers;
    - `style="display:none"` :51 goes;
    - hand-rolled selects become `select_field`;
    - default size.
19. **Usage modal** (`allocations/partials/usage_modal.html`):
    - nested cards become an info panel;
    - `render_usage_bar` becomes `alloc_meter`, closing ledger entry 8's open item.
20. **Detail modals:**
    - `transaction_details_modal.html`, `adjustment_details_modal.html` and
      `xras_action_details_modal.html` opt into `.stat-item-inline` (ledger entry 7 open
      item);
    - their inline grid and `pre` styles become classes.
21. **Queue-cleanup and bulk directory deactivate:**
    - previews: trim the alerts and inline styles;
    - bulk deactivate onto `htmx_form`;
    - the directory edit's 5-line orphan explanation becomes a tip, keeping the warning
      itself.
22. **XRAS merge / action / notify bodies:** decoration only. Their safety wording is
    deliberate (WARNING comments). Keep the words, trim the cards and badges.
23. **Ledger entry 9:**
    - metrics row;
    - per-modal dialog height before -> after;
    - deviations;
    - leftovers to the open list.

    Consider a `sweep_inventory.py` detector counting `class="alert` in modal bodies, and
    ratchet it.

## Load-bearing warnings: keep the meaning, rewording is fine
- **Edit allocation:**
  - the detach `hx-confirm` (:102);
  - the OVERSPENT/suspension note (:85-88);
  - the re-link `hx-confirm` with old -> new amount (:195);
  - "changes cascade to N shared sub-project allocations" (:118-121);
  - the negative-residual warning (:140-146);
  - the propagate `hx-confirm` count (:163);
  - "description is not cascaded" (:270).
- **Exchange / allocate-down:**
  - exchange from / to previews;
  - allocate-down date hint (:83-87) and maximum (:103).
- **Extend:** the whole-tree alert.
- **Renew / extend:** email preview and notify checkboxes.
- **Linked elements and directories:**
  - contract removal deactivating the contract (`linked_elements:204`);
  - the directory final-path preview (:273-276);
  - the orphaned-directory warning (`project_directory_edit_form_htmx.html:33`).
- **XRAS forms:** all WARNING-commented wording.

## Tests that pin strings and ids (update deliberately, never delete)
- `tests/unit/webapp/test_htmx_allocate_down.py:144-155` pins "shared allocation",
  "Unallocated", `name="target"`, `name="amount"` and `alert-danger`.
- `test_htmx_handler_routes.py:135/188/194/573/582` (the `extend-tree-scope` testid).
- `test_modal_shell_contract.py`:
  - `HTMX_FRAGMENT_SHELL_DEPS` for `shared/project_tree.html` (:379) and
    `project_linked_elements_htmx.html` (:242);
  - `EDIT_MODAL_ID` (:436);
  - `edit_project.html` is in `NOT_TOP_LEVEL` (:490).
- e2e: `test_pr378_regressions.py:30-59`, `test_console_sweep.py:61-141`.
- Route-map parity only if step 3 deletes a route.

## Tools and traps
- **Servers:** base `scripts/dev_server_alt.sh ../sweep5-base 5053` (re-point the worktree),
  branch `. 5052`.
  - Fragment cache: Redis DB `2 + port % 14`.
  - CSS re-hash: `touch src/webapp/utils/static_assets.py`.
- **Proof per commit:** `ui_snapshots.py --modal` before/after at desktop and 390px in both
  themes.
  - Measure dialog height with `getBoundingClientRect`.
  - Compute contrast with alpha blended.
  - Run `ui_snapshots.py --headers` after header or table edits.
- **Traps:**
  - PR #464: in-modal controls never carry `data-bs-toggle` for their own modal;
  - `bg-warning text-dark` is `!important` (`edit_project.html:266/284`);
  - `base_admin.html:47-64` includes about 45 admin shells on every admin page.
- **Gates:**
  - `tests/unit/gates/`;
  - `tests/unit/webapp`;
  - e2e `test_console_sweep.py` + `test_pr378_regressions.py` against the branch server;
  - the full MySQL suite (`> out.txt; echo rc=$?`);
  - postgres-test for `tests/unit/webapp`.
- **Writing:** comment budget, American spelling, no skip-ci tokens. The skills' 250-line
  budget can rise per file through `LINE_BUDGETS` in `test_docs.py` if it must.

## Prompt for a new session
> Read `docs/plans/implemented/MODAL_SWEEP_HANDOFF.md` and do it with the unplanned-city-sweep,
> wire-dashboard-feature and frontend-design skills. Check whether #729 has merged; branch from
> it if not, else from origin/staging, and open a draft PR against the right base. Ship the
> three bugs first, each with a regression test, then the `--modal` capture tool, then one
> commit per remaining step. Re-check every file:line before editing, keep every load-bearing
> warning listed in the handoff, and prove each visual commit with before/after
> `ui_snapshots.py --modal` captures at desktop and 390px in both themes, with dialog heights.
> Finish with ledger entry 9 and send me before/after screenshots of the worst five modals.
