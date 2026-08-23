# Pending Users — merge the two user-keyed XRAS worklist tabs into one

**Status: handoff, written 2026-08-23, unbuilt.** Decision taken with Ben the same day;
nothing below exists yet. Written so a fresh session can build it without re-deriving the
touchpoint map — every file:line here was verified on `xras_screener` @ `160ad330`
(rebased on `origin/staging` @ `102607a9`).

Companion pages: [`XRAS_PUSH_READINESS.md`](XRAS_PUSH_READINESS.md) (Phase 3 later adds a
Pre-flight column to the tab this page creates), [`XRAS_REMEDIATIONS.md`](XRAS_REMEDIATIONS.md)
§ 6b/§ 7.4 (the merge entry and the snapshot-patch idiom),
[`../xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md`](../xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md) § 3 (the
two-tab rationale this page reverses).

---

## 1 · Why

Allocations → XRAS has three worklist tabs (`#xrasWorklistTabs`). Two are user-keyed lists
of the same thing — people who need a SAM account before an XRAS handoff can land — split
only by *where the evidence came from*:

| | **Accounts Needed** (Feed A) | **Pending Requests** (Feed B) |
|---|---|---|
| Source | `xras_action_log` rows in `received` / `failed` / `manual` | the `xras_sweep` snapshot of Approved, not-yet-pushed requests |
| Means | a push **already arrived** and a person on it has no usable account — ACCESS is waiting on a re-post | a person on an approved request **will** block the push when it comes |
| Availability | always | only with `XRAS_OUTGOING_ENABLED` + a published sweep |

Ben's read: Feed A trends toward empty once the proactive side works, and "Pending
Requests" misnames a user-keyed list. Assessment, agreed: **Feed A is the residual, not a
dead end** — the sweep is periodic and the push is not; Feed A is ground truth (the
usernames XRAS actually sent); a Feed-A row is the *more urgent* flavor of the same
problem; and its emptiness is the health metric for the proactive side. But it is an
urgency dimension of one list, not a second list.

The code already agrees: `get_account_worklist(session, pending_rows=…)` →
`merge_worklists()` (`src/sam/queries/xras_accounts.py:543-626`) unions the two feeds on the
casefolded username with a `sources` field, and **`sam-admin xras --accounts` already shows
that union** (`src/cli/xras/commands.py:168-234`). Only the webapp splits it:
`xras_accounts_fragment` omits `pending_rows` on purpose
(`src/webapp/dashboards/allocations/xras/card_routes.py:313-321`, the "Feed A ONLY" block
this change deletes) and `xras_pending_requests_fragment` (`:418-497`) renders Feed B alone.
`enrich_worklist` already skips rows carrying an inline person (`xras_accounts.py:673-676`)
— it was written for the union.

**Decision (Ben, 2026-08-23):** one tab, **"Pending Users"**, fed by the merged worklist,
per-row source badge with received-push rows pinned first; the Accounts Needed tab retired
and its count kept as the residual signal. One standalone PR vs `staging`, independent of
the push-readiness work.

---

## 2 · Design

**The Accounts Needed identity survives, relabeled.** Route `xras_accounts_fragment` (URL
unchanged), pane `#xras-pane-accounts` (keeps the larger persisted `tab:xrasWorklistTabs`
cohort — a stale `#xras-pane-pending` value falls back to the default pane,
`static/js/nav-view-persistence.js:64`), container `#alloc-xras-accounts`, form
`#xras-accounts-filters`, template `partials/xras_accounts_card.html` (the superset: the
`request_cell` macro, four identity states, the merge entry, the Waiting and Pre-flight
columns). It absorbs Feed B. **Retired:** route `xras_pending_requests_fragment`,
`partials/xras_pending_requests_card.html`, `#xras-pane-pending`,
`#alloc-xras-pending-requests`, `#xras-pending-filters`, and in `_shared.py`
`_PENDING_FORM_ID` (`:390`), `_PENDING_TARGET` (`:504`), `_pending_account_total()`
(`:472-501`). Tabs go 3 → 2; the Remediations card stays a card.

Source vocabulary is the existing `sources` values — `'action_log'` and `'reports'`
(`xras_accounts.py:121,266,367,517`) — which are **rendered nowhere today** (the CLI JSON
carries them, the terminal table does not). Operator labels: `Received push` /
`Pending request`.

---

## 3 · Commits (one PR, ordered)

### 3.1 Query layer + CLI delegation
`src/sam/queries/xras_accounts.py`:
- `SOURCE_ACTION_LOG = 'action_log'`, `SOURCE_REPORTS = 'reports'`; use at the four sites.
- **`load_pending_worklist_rows()`** → `PendingFeed(rows, checked, reason, snapshot)` — lifted
  from the CLI's `_pending_worklist()` (`commands.py:195-234`): `reason` ∈ `unconfigured` /
  `unreadable` / `no_snapshot` / `None`; deferred imports of `xras_api_configured` and
  `load_pending_worklist` (this module's rule — see its docstring); never raises. The CLI
  delegates and keeps its stderr warning for `unreadable`
  (pinned: `tests/unit/test_admin_xras_cli.py:227-254`).
- **`merge_worklists`** (`:578-626`): copy each action dict — `[dict(a) for a in …]` — on both
  the first-seen and the merged arm (`:601-607`). Today the action dicts are the snapshot's
  own objects, and `stamp_project_existence` writes `action['is_project']` in place
  (`:765-775`): with the in-process cache adapter that mutates the shared snapshot. Sort
  gains a leading tier — rows carrying `SOURCE_ACTION_LOG` first — ahead of the existing
  absent-before-inactive-then-username order (`:624-626`). The CLI inherits the order.
- **`worklist_counts`** (`:698-714`) gains `received_push` / `pending_request`. Additive for the
  `kind: xras_accounts` envelope (`src/cli/xras/builders.py:198-207`); `display.py:346-428`
  gains a short Source column.
- Tests: `tests/unit/test_xras_accounts_query.py` (`TestMergeWorklists` `:684-732` + copies
  + ordering; the three `reason`s via monkeypatched `xras_api_configured` /
  `load_pending_worklist`; counts). `test_admin_xras_cli.py:256-283` still holds.

### 3.2 Route + template merge
**`card_routes.py::xras_accounts_fragment`** (`:279-390`), in order:
1. args `classification`, `role`, `origin`, **`source`**, `request_number`; `window =
   _parse_activity_window(request.args)`.
2. `feed = load_pending_worklist_rows()`; `pending = [r for r in feed.rows if
   _submitted_since(r, window['since'])]` **before** injection — after the merge every Feed-A
   row would pass `_submitted_since` (no `submit_date` ⇒ `True`, `_shared.py:413-414`);
   `pending_hidden = len(feed.rows) − len(pending)` is the Feed-B-scoped "outside the date
   filter" count (denominator = the snapshot's rows, not `snapshot['counts']`).
3. `rows = get_account_worklist(db.session, since=…, until=…, pending_rows=pending)`.
4. `stamp_waiting_days(rows)`; `stamp_project_existence(db.session, rows)` — safe after 3.1,
   and Feed-B actions gain `is_project` so `request_cell` links them;
   `enrich_worklist(rows, max_lookups=_ACCOUNTS_ENRICH_BUDGET)`.
5. PII strip **after** the merge, in place (`person=None` unless `may_manage`) — the rows are
   copies now; never strip `pending_rows` beforehand.
6. facets: `_account_facets` (`_shared.py:441-469`) gains `'source'` (self-excluding; a row with
   both sources counts in both); `_filter_accounts` (`:364-387`) gains `sources=` — keep a row
   when any selected source ∈ `row['sources']`; request intersection unchanged.
7. context: + `feed_checked`, `feed_reason`, `feed_generated_at`, `feed_window_days`,
   `pending_hidden`, `source_values`, `selected_sources`; − `pending_total`.

**`_shared.py`**: `_SOURCE_LABELS = {SOURCE_ACTION_LOG: 'Received push', SOURCE_REPORTS:
'Pending request'}` beside `_ORIGIN_LABELS` (`:358-361`); the dimension/filter additions;
delete the three `_PENDING_*` names.

**`partials/xras_accounts_card.html` → the Pending Users card** (keep its macros):
- title "Pending Users" + "accounts needed before an XRAS handoff can land". Header badges:
  total · `N from received pushes` (danger-subtle when > 0) · `M from pending requests` ·
  `oldest Nd` · `N placeholder` · `swept {feed_generated_at|fmt_date} · {feed_window_days}d
  lookback` when `feed_checked` · amber `N pending-request rows outside the date filter`
  when > 0 (ported from `xras_pending_requests_card.html:41-62`).
- one **degraded-half note** replaces the retired card's three empty states
  (`xras_pending_requests_card.html:67-82`): `unconfigured` → "look-ahead is off
  (`XRAS_OUTGOING_ENABLED`); showing received pushes only" · `no_snapshot` → "configured, no
  sweep has published yet" · `unreadable` → "the snapshot could not be read". Styled like the
  enrichment notes (`:153-168`). Delete the cross-tab blurb (`:141-152`).
- facet rows: Needs / Role / Identity / **Source** / Request.
- columns: Username · Needs · Role · **Source** (text badges — never an `<i class="fas">` on a
  summary row, `e2e/test_xras_accounts_card.py:114-136`) · Request · [Person] · XRAS identity
  · Waiting. `colspan` computed (`{{ 8 if may_manage else 7 }}`; the hardcoded `7` at `:392`
  is a pre-existing bug when `may_manage` is false).
- expansion subtable: Request / Action / Status / Source / **When** (`received_time` for
  `action_log`, `submit_date` for `reports`) / Pre-flight — Feed-B rows read "not checked"
  until `XRAS_PUSH_READINESS.md` Phase 0–1 fills that slot.
- merge entry (`:393-415`, `may_manage ∧ placeholder ∧ is_reconciled`) unchanged; it now
  reaches Feed-B rows too (inline person + `is_reconciled`).
- empty state: "No users are waiting on an account" (+ "for these filters"), degraded note
  still rendered beside it. The `request_cell` two-`<td>` rule stands
  (`tests/unit/test_collapse_trigger_rows.py` scans source).

**`templates/dashboards/allocations/xras.html`**: nav `:64-80` → two tabs (Activity,
**Pending Users** → `#xras-pane-accounts`); delete the pending pane (`:147-157`) and
`#xras-pending-filters` (`:115-118`); `#xras-accounts-filters` (`:104-114`) gains a hidden
`source` select — the `:107-111` rule: a facet without a control renders chips that do
nothing; Remediations comment `:161-164` says "two". Strings naming the old tabs:
`fragments/xras_person_detail.html:4-5`, `partials/xras_merge_form.html:111`,
`sam/manage/xras_remediation.py:241`.

**Tests** (Ben runs `pytest` by hand):
- `tests/unit/test_xras_accounts_card.py`: title literal (`:251`);
  `TestTheWindowNeverHidesSilently` (`:453-511`) re-targets the merged route and the
  Feed-B-scoped wording; `TestBothTabsShowTheSameDetail` (`:513-598`) → one route, rows of
  both sources carry every declared field, view-only never receives Feed-B person bytes;
  new: source chips render + filter, received-push rows sort first, an unconfigured feed
  renders the note and still lists Feed-A rows, the route does not mutate the snapshot.
- `tests/unit/test_xras_remediations.py:251-259` → `== 2`; the ordering pin indexes
  `alloc-xras-accounts` (indexing the deleted id raises `ValueError`, not an assert).
- `tests/unit/test_audit_window_control.py:192-193,241-247` drop the pending form/route.
- `tests/unit/test_modal_shell_contract.py:278-284` drop the pending template entry.
- Route-map snapshot: `ROUTE_MAP_REGEN=1 pytest tests/unit/test_route_map_parity.py`; diff
  = one removed route (`tests/unit/snapshots/dashboard_route_map.json:1647-1653`).
- e2e: `e2e/test_xras_accounts_card.py` (`PANES`/`PENDING_CARD` `:23-27`, title `:67`, count
  `:85`, the three-states test `:138-150` → a degraded-note test);
  `e2e/test_xras_remediations_card.py:57-63` → 2.

### 3.3 Cache coherence after a merge-in-XRAS
`XRAS_REMEDIATIONS.md:262-264` designed "drop the username's row in the Feed-B `worklist`
key" and deferred it because "Accounts Needed classifies live on render, so that row clears
itself". With one card rendering the cached Feed-B half, that argument ends: a just-merged
placeholder would keep its `Pending request` row until the next sweep. Add
`drop_pending_worklist_row(username) -> bool` to `sam/integration/xras_api/cache.py`
(read-modify-write under the adapter lock, mirroring `patch_requests_index` at `:209`; there
is no `patch_pending_worklist` today); `merge_placeholder`
(`sam/manage/xras_remediation.py:233-283`) calls it best-effort after `invalidate_person` ×2
(`:277-279`), logged on failure. Test in `tests/unit/test_xras_remediation_service.py`.

### 3.4 Docs
- `docs/xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md` § 3 (`:112-138`) — rewrite "two feeds, why two
  tabs" as "two feeds, one tab": keep Source/Means/Availability, add the badge, drop "kept
  apart because…" and the CLI-is-where-the-union-lives line, keep the window-pills warning.
- `docs/xras/incoming/XRAS_TRIAGE_PLAYBOOK.md`: § 1 table two rows → one "Pending Users"
  row; the "populated Pending Requests beside empty Accounts Needed is correct" bullet →
  "before the repoint every row is `Pending request`; the first `Received push` row is the
  first sign XRAS has repointed"; § 3.3 "work the Accounts Needed tab" → "Pending Users".
- `docs/xras/incoming/XRAS_CUTOVER_RUNBOOK.md` triage-week table; `docs/xras/README.md`;
  `XRAS_REMEDIATIONS.md` § 6b/§ 7.4; `XRAS_PUSH_READINESS.md` Phase 3 surface name;
  `XRAS_INGEST_IMPROVEMENTS.md`; `docs/xras/outgoing/XRAS_WRITE_FIXUPS.md` UI-name mentions.
- Gates (`tests/unit/test_docs.py`): American spelling, no changelog phrasing, cited paths
  exist, budgets (playbook 455/490, lifecycle 159/250, write-fixups budget 460). Run the
  gate's regexes over the touched docs before committing.

---

## 4 · Traps, verified

1. **Two window semantics.** Feed A is windowed in SQL (`get_account_worklist(since=,until=)`);
   Feed B by `_submitted_since` in Python (`card_routes.py:465`). Filter `pending` *before*
   injecting, or every Feed-A row passes.
2. **Shared action dicts.** `merge_worklists` rebuilds the `actions` list but not the dicts
   inside; `stamp_project_existence` mutates them. Fix in `merge_worklists` (3.1), not at the
   call site.
3. **PII strip placement.** Feed A strips in place (`:335-339`), Feed B by copy (`:467-468`);
   after the merge every row is a copy — strip after merging, never before.
4. **Three empty states → one note.** Feed A always works; Feed B is contingent. "Unconfigured"
   / "no snapshot" cannot be card-level empty states on the merged card.
5. **"N of M outside the date filter"** (`xras_pending_requests_card.html:41-53`, pinned by
   `test_xras_accounts_card.py:496-511`) used `snapshot['counts']['total']`; restate it
   Feed-B-scoped against `len(feed.rows)`.
6. **Facet control parity.** Every facet dimension needs a hidden control in
   `#xras-accounts-filters` (`xras.html:107-111`); `_account_facets` raises on an unknown
   dimension (`_shared.py:469`).
7. **Three `== 3` pins + one index pin** — `e2e/test_xras_accounts_card.py:85`,
   `e2e/test_xras_remediations_card.py:63`, `tests/unit/test_xras_remediations.py:254,257-259`.
8. **Static source guards**: `test_collapse_trigger_rows.py` (why the Request cell is two
   `<td>`s), `test_modal_shell_contract.py:265-284` (per-template modal allow-lists),
   `TestEveryExpandableRowShowsAChevron` (`test_xras_remediations.py:263`).
9. **`sources` is new UI** — no rendering precedent; text badges only on summary rows.
10. **Permissions unchanged**: both fragments are `VIEW_XRAS` (`card_routes.py:280-281,
    419-420`); `MANAGE_XRAS` is a render flag (person column, merge button); `is_reconciled`
    survives the strip on purpose (`xras_accounts.py:301-303`).

---

## 5 · Verification

- `docker compose up webdev --watch`; the sweep has already published on dev (2026-08-23:
  16 pending-request rows, 364 index requests). Allocations → XRAS: two tabs; Pending Users
  shows 16 `Pending request` rows, the Source facet, the freshness line, 0 `Received push`.
  Flip `XRAS_OUTGOING_ENABLED=0` → degraded note + Feed-A rows only. Seed Feed-A rows with
  `scripts/xras/seed_dev_actions.py` → they sort first with the danger badge. The merge entry
  appears on placeholder ∧ reconciled rows of either source. `sam-admin xras --accounts`
  shows the Source column and the same order.
- Ben runs `pytest` (route-map regen included; e2e against webdev if wanted).
- Afterwards: update the `xras-push-readiness` memory note — Phase 3 targets the Pending
  Users tab.
