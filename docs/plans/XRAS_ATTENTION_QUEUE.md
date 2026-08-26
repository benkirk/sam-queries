# XRAS Activations Card — Attention Queue by Default

**Status: HANDOFF, not started (2026-08-25 EOD).** Designed after three read-only
maps of the tree; nothing built. Restart from § *Commits*. The discussion that
produced it is § *Why this shape*; the consolidation question is § *Follow-up*.

## Problem

The first XRAS tab, "Pending Activations & Notifications"
(`src/webapp/dashboards/allocations/xras/card_routes.py::xras_pending_fragment`,
template `dashboards/allocations/partials/xras_activity_card.html`), renders every
`processed` action in the shared window (default 30 days, no upper bound), one row
per action. By design nothing leaves when somebody does their job (PR #424: a table
whose purpose is "what did we tell people" cannot have rows that vanish). At
cutover-day volume that was fine; on 2026-08-25 the allocation team posted 15
actions in an afternoon, each needing a Notify click. At that rate the window holds
300–450 rows and the operator finds the actionable ones by hand.

## Premise corrections (measured in the tree, not remembered)

1. **Dismiss exists.** Eye-slash button and Restore
   (`xras_activity_card.html:204-219`), routes `xras_dismiss_form` /
   `xras_dismiss` / `xras_restore` (`lifecycle_routes.py:323-390`), events
   `dismissed` / `restored` (`sam/integration/xras.py:307-313`), tests in
   `tests/unit/test_xras_dashboard.py` and `test_xras_action_queries.py`. PR #424
   removed only the *hiding*; dismissal now greys the row and swaps Activate for
   Restore.
2. **The card is not window-only.** It already has self-excluding State chips
   (`needs_activation`, `not_notified`, `notified`, `failed`, `dismissed`) and
   Action chips (`_activity_facets`, `_filter_activity` in `_shared.py`). What is
   missing is a *default selection*.
3. **Dismiss already means "no mail."** The `xras_notices` task's `select()`
   (`src/scheduling/tasks/xras_notices.py:195-207`) skips `dismissed` rows, so
   dismissing an un-notified row is already safe. Ben: it is valid to dismiss even
   when notifications were never sent.
4. **Row state is derived, never stored.** `needs_activation` = project inactive
   AND this is the project's all-time latest action AND not dismissed; `notified`
   comes from `notification_log` through the dedup key; dismissal supersedes per
   row (`sam/queries/xras_activation.py:271-320`). `get_xras_activity` already
   accepts `since=None`; `latest_action_id` is all-time regardless of window.

## The rule

`needs_attention(row, *, now, recent_days=3)` in `sam/queries/xras_activation.py`,
beside `activity_tags` — pure, testable, reusable by CLI and task:

```
needs_activation                                    # already excludes dismissed
or (notifiable and not notified and not dismissed)  # a Notify nobody clicked
or received_time >= now - recent_days               # informational; a dismissed
                                                    # row stays greyed here = undo
```

Decisions inside it:
- **No date window in the default mode**, as on the Remediations card: a New
  nobody activated three months ago is the point, not noise. The recency clause is
  the only time-bound part. `_ACTIVITY_RECENT_DAYS = 3` beside the pill constants
  in `_shared.py`.
- **Dismiss declutters again, in the default view only.** A dismissed row older
  than 3 days leaves the queue; "Everything in the window" still shows it greyed
  with Restore. The #424 principle (history never vanishes from the full table)
  and the SPRINT_B misclick concern (undo without a "show dismissed" toggle that
  "would vanish exactly when every row was dismissed") both hold.
- Chips keep working in both modes; facet counts come from the scoped set.
- Header badge: `N need attention` + `M more with Everything in the window`,
  the shape of Remediations' `N pending`.

**Said out loud:** the notification half self-clears only if somebody clicks
Notify or `xras_notices` is enabled (1-day delay; `update`/`extend`/`supplement`/
`adjust`; `add` stays manual because a New is two writes). With the task in
`SAM_TASKS_DISABLED`, 15 posts a day is 15 clicks a day or a growing queue. That is
real work the display reports, not a display bug. Separate decision, Ben's.

## Commits (one PR, ordered; `xras_incoming_triage` or its successor)

1. **Extract the scope seam, no behavior change.** `remediation.py::_scope_rows`
   (`:447-452`) becomes `scope_rows(rows, args, *, queue, in_window)` in
   `_shared.py`: `read_flag(args, 'show_all')` → window mode, else
   `[r for r in rows if queue(r)]`. `remediation.py` keeps `_scope_rows` as a
   one-line delegate; its tests pass unchanged. Move behind a shim, then switch
   callers — two commits if the diff reads badly as one.
2. **The predicate and its tests.** `needs_attention` in `xras_activation.py`.
   Extend `TestActivationDeriveRule` (`tests/unit/test_xras_action_queries.py`)
   with: each clause alone; an old notified row is out; an old dismissed row is
   out; a dismissed row one day old is in; a dismissed project with a new action
   is in (already covered — reopens); the `recent_days` boundary. Back-date with
   `make_xras_activation_event(when=)` (`tests/factories/xras.py`).
3. **Route and card.** `xras_pending_fragment`: `show_all = read_flag(request.args,
   'show_all')`; default mode calls `get_xras_activity(since=None, until=None)`
   and scopes with `needs_attention`; `show_all` keeps today's window path
   byte-for-byte. Facets and chip filtering run on the scoped set. Context gains
   `show_all`, `attention_total`, `hidden_count`, `recent_days`.
   `xras_activity_card.html`: copy the Remediations switch idiom
   (`xras_remediations_card.html:250-256` — a form-associated
   `<input type="checkbox" name="show_all" value="1" form="…">` inside the swap
   target, `hx-get` the fragment, `hx-include="#xras-window-filters,
   #xras-activity-filters"`), label **Everything in the window**, glossary popover
   `g_xras_attention_queue` (three clauses, the undo window, dismiss hides here but
   not there). Empty-queue state renders the switch and "Nothing needs attention —
   N actions in the window", never a bare empty table. `xras.html` unchanged: the
   switch lives inside the swap target. No new routes, so the route-map snapshot is
   unchanged.
4. **Stale comments** (compress, do not delete): `lifecycle_routes.py:342-347`
   names `get_xras_pending_activation`, which no longer exists, and says "hide";
   `partials/xras_pending_event_form.html:9-12` still describes pre-#424 hiding.
   Both become the precise rule: clears the call to action, suppresses the
   auto-notice, leaves the attention queue after 3 days, visible under Everything.
5. **Docs.** `docs/plans/XRAS_PENDING_WORK.md` gains a section for this card (the
   rule, no-window decision, undo window, the `xras_notices` interaction). One
   playbook row. A cutover-log row in `docs/plans/XRAS_TRIAGE_WEEK.md` at deploy.

Verification: `pytest tests/unit/test_xras_action_queries.py
tests/unit/test_xras_dashboard.py tests/unit/test_xras_remediations.py
tests/unit/test_modal_shell_contract.py tests/unit/test_collapse_trigger_rows.py
tests/unit/test_route_map_parity.py tests/unit/test_docs.py -n 0` under
`set -o pipefail`; a route-test pair (default hides a 10-day-old notified row and
shows an un-notified one; `show_all=1` shows both; badge text; switch renders on an
empty queue); a webdev Playwright walk (queue → switch → window rows; dismiss a
recent row stays greyed, an old one leaves; chips still filter). The e2e suites pin
*exactly two tabs* — unchanged.

## Why this shape — the three cards compared

| Card | Row key | Default set | Full set | Self-clearing by |
|---|---|---|---|---|
| Remediations | request | `is_pending_work` (state, no window) | window under `show_all` | the sweep + live re-check |
| Pending Users | username | facets only (Needs/Role/Identity/Source) | same | both feeds drop a row once SAM holds the account |
| Activations (this) | action | *none today* → `needs_attention` | window under `show_all` | Activate / Notify / Dismiss / 3 days |

Pending Users needs no toggle: its feeds already self-clear, so "Needs" is a
facet, not a queue. Remediations and Activations are the same shape, which is why
commit 1 shares the seam instead of copying `_scope_rows` a second time.

## Follow-up — is this spaghetti?

Measurably duplicated, not yet spaghetti, and a "unified worklist" would be the
wrong fix: the three cards key rows differently on purpose. Inventory, all in
`src/webapp/dashboards/allocations/xras/` unless noted:

- Three date-window parsers, none reusing another: `_parse_xras_filters`
  (`_shared.py:184`), `_parse_activity_window` (`_shared.py:268`),
  `_parse_audit_filters` (`blueprint.py:765`); plus two per-card date predicates
  saying "keep a dateless row" in different words (`_in_window`,
  `_submitted_since`).
- Four facet builders with the same self-exclusion shape: `_activity_facets`,
  `_account_facets`, and Remediations' `_facet` / `_action_facet` (the same
  function with a hardcoded key) / `_push_facet` / `_readiness_facet`.
- Three copies of the `getlist` preamble; only Remediations factored it
  (`_selected_facets`).
- Two filter idioms (`_filter_accounts`, `_apply`, `_filter_activity`: AND across,
  OR within, no shared line); request-number filtering inlined in the accounts
  route, so `_request_facets` self-excludes only `remedies`.
- Sort state duplicated per card (`_ACCOUNTS_SORT`, `_REMEDIATION_SORT`) with
  matching hidden inputs hand-written in `xras.html`; the four form/target ids
  "defined once" but also written by hand there; `_WINDOW_TARGET` is dead.
- Remediations walks scope → search → facets twice per render (`remediation.py:128`
  and `:188`).

Proposed seams, as their own PR after this ships and measured on three cards:
one `read_selected(args, dims)`, one generic self-excluding `facets(rows, dims,
filter_fn)`, one date-parser family, one sort-spec helper, ids defined once.
Record as `docs/plans/XRAS_WORKLIST_CONSOLIDATION.md` when that PR starts.

## Open for Ben

- 3 days for the recency/undo window, or a different number.
- Enable `xras_notices` (chart-side `SAM_TASKS_DISABLED`) so the notification half
  self-clears — independent of this card, but it is what makes the queue drain.
- Whether this rides #482 or a fresh branch off staging after #482 squashes.
