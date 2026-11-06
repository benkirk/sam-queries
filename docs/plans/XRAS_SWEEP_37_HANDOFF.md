# Handoff: sweep 37, the XRAS surfaces read as one design

**Status:** handoff note, 2026-11-05, untracked. Not started. Starts after PR #920 merges
to `staging`. One session, **two PRs**, both against `staging`.

## The lens

Every XRAS surface answers "what is the current state of this request, and who is on it?" and
they answer it from different feeds:

| Surface | Reads | Window | Join key |
|---|---|---|---|
| Remediations card, request dialog | Feed B: the sweep's request index (`load_requests_index`, `xras_api/cache.py:230`); the dialog adds a live payload and takes its preflights from the index row (`xras/modals.py:205-215`) | card 30 d, sweep 90 d | request number |
| Accounts card, `sam-admin xras --identity-report`, `account_requests_reconcile` | Feed A (`records_from_action_log`) joined to Feed B by casefolded username (`xras_accounts.py:724-905`, `assemble_worklist` `:1219`) | card 30 d, task **none** | username |
| My Requests, account-requests admin card | Feed B via `request_numbers_by_username` (`xras_requests.py:792`) | the index | username to request numbers |
| Activity card | the action log | its own | action id |

Feed B is XRAS's current truth for every pending request, rebuilt daily at 04:00 MT with the
pending feed refreshed hourly. Feed A is history: a push SAM received, however old. The session's
question for each surface is: **what does it show that the index already contradicts?** The stale
placeholder (below) is the first known answer. Name every other one before changing anything.

## PR 1: the stale placeholder (behavior)

`docs/plans/STALE_PLACEHOLDER_ROWS.md` is the plan, decided with Ben on 2026-11-05: drop a
Feed-A-only placeholder row whose citing requests are all in the index and none of whose current
rosters names it; record `gone` as a `rejected` `merge_person` event; bound `ready_to_merge` to the
sweep's 90-day window. Tests and the prod proof are listed there. Its first commit turns that
handoff into the tracked plan. Keep this PR to the three pieces; anything the census turns up
beyond them goes to PR 2 or the ledger.

## PR 2: the sweep picks (cleanup)

`unplanned-city-sweep` in area mode over the XRAS tree: `src/sam/xras/`, `src/sam/queries/xras_*`,
`src/sam/manage/xras_remediation.py`, `src/sam/integration/xras_api/`, `src/scheduling/tasks/xras_sweep.py`,
`src/webapp/dashboards/allocations/xras/`, `src/webapp/api/xras/`, the XRAS templates, `xras.js`,
`xras.css`. Report first; Ben picks; one commit per pick.

Seeds, all previously read, none yet done:

- **Ledger entry 12, the deferred XRAS census** (`docs/plans/UNPLANNED_CITY_LEDGER.md:905-916`),
  deferred "until `XRAS_SUBMISSION.md` lands": sprints 1-3 landed (#874, #880, #886), so it is
  open. Its items: the seven editors in `remediation.py` repeating a `_safe_*` / GET modal /
  one-click-write triplet; `admin_client.py`'s 14 write calls sharing a read/write/re-read tail;
  **the preflight batch duplicated by `manage/xras_remediation.py` and `tasks/xras_sweep.py`**
  (on-theme: two derivations of one verdict); `queries/xras_remediations.py` with no `src/`
  caller; both HTTP clients importing parsers from `queries/xras_requests.py`; the over-budget
  prose in five modules. Re-measure each; the line numbers there are from 2026-10-05.
- **#920 review F3:** the Waiting band words live in three places (`_shared.py:433` chip labels,
  the `waiting` macro's "over 30/90 days" titles, `WAITING_BANDS` in `xras_requests.py`).
- **Ledger line 323:** `xras_request_detail.html`'s outline buttons beside its `btn-row`s; #920
  reshaped the footer (one line, a More dropup), so re-read before counting.
- `tests/unit/xras/test_xras_remediations.py` is past 2,300 lines; the sweep may split it by
  surface if a pick touches it.
- Propagate: `data-xras-queue` prev/next (#920) is the kind of piece an older dialog set
  could adopt; record candidates, do not build them.

## Order

1. Branch `xras-sweep-37` from `origin/staging` once #920 is in. Load `unplanned-city-sweep`,
   `wire-dashboard-feature`, read `.claude/rules/orm.md` and `webapp-ui.md`.
2. The census table above, verified against the code at head, extended with every disagreement
   found. Give Ben the layman's summary before any pick list.
3. PR 1 on its own branch (`stale-placeholder-rows`), reviewed with `/pr-review`.
4. The sweep report, Ben's picks, PR 2, the ledger entry (sweep 37, with the metrics row from
   `scripts/sweep_inventory.py --area py --top 40` plus `--area js --jscpd` and `--area css --jscpd`).

## Boundaries

- Legacy-compat blueprints: additive only. `sam` never imports `webapp`; `src/scheduling/`
  imports no Flask, Click or rich; the `test_xras_incoming_boundary` gate forbids `sam/xras`
  importing `xras_api` even lazily (#917 learned this).
- No new XRAS write path. The reconcile task's only new write is the `rejected` event.
- Do not build for failures nobody has seen: the census records disagreements with a concrete
  row (the stale placeholder has one: `kmerk-user-92wap`, NCAR4434).

## Census result (2026-11-05, this session; PR 1 = #922, merged to staging 8ea42a3f)

Prod reads, read-only, ~19:30 MT (index built 04:00, worklist 19:00). The table above holds at
head with three additions: the Remediations identity strip (`remediation.py:583-601`) and
`sam-admin xras --identity-report` (`cli/xras/builders.py`) read Feed A with **no window**, like
`ready_to_merge`; the sweep's step 3b (`xras_sweep.py:1039-1047`) is a fifth reading, Feed A
windowed 90 d, writing the queue rows. Four lookbacks on one question: 30 d (cards), 90 d (sweep
3b, now the task too), none (strip, CLI).

What the index contradicted, with rows:

1. `kmerk-user-92wap` -> `katarinam`, NCAR4434, log #350: "Ready to merge" on four surfaces,
   no such person in XRAS, index roster `katarinam`,`maraf`. PR 1 (#922) retires it.
2. The same request on two cards: Remediations blocks NCAR4434 on contract 2019589 + the PI's
   institution; Accounts blocked it on identity. Resolved by 1.
3. Queue row #54 (same placeholder) drew no request link from the index while the Accounts card
   chipped NCAR4434. The first reconcile wake after #922 deploys dismisses it (review F5).
4. No refused push older than 90 d names a placeholder today: PR 1 piece 3 is the guard only.
5. **Open, for PR 2's report (behavior, Ben decides):** six fulfilled, sweep-filed placeholder
   queue rows (#3, #4, #6, #13, #14, #43: `llee`, `mshaw`, `azhao`, `dolson`, `eyuan`, `jsantos`)
   have an active SAM user, no push in the log, and no index roster names them, yet XRAS still
   holds the placeholder (edited off the roster by hand, never merged), so the stale-close pass
   keeps them forever and each costs one XRAS read + one cap slot per run (review F3). The
   queue card shows them "submitted" with no request. A close rule keyed on "no index roster
   names it and SAM has the user" would retire them; it changes behavior, so it is a pick.
6. Request dialog: index preflights on a live roster can name a person the roster no longer
   carries for up to a day. No row seen; recorded only.

Added seeds for PR 2, beyond the list above:

- Review F4 (#922): `drop_stale_pushes` (`xras_accounts.py`) and `request_numbers_by_username`
  (`xras_requests.py:792`) both walk the index rosters; the drop also needs the indexed-request
  set. Fold candidate.
- The `_index()` / `load_requests_index()` payload is unpacked at four call sites into
  `index_rows=` + `index_generated_at=` (card, strip, CLI, `ready_to_merge`); a `RequestIndex`
  feed object beside `PendingFeed` would be the house shape.
- The unwindowed strip and CLI reads (above): decide whether they take the sweep's window too.

### Kickoff for PR 2 (fresh session)

> Read docs/plans/XRAS_SWEEP_37_HANDOFF.md, including its Census result. PR 1 is #922
> (merged to staging as 8ea42a3f); read its diff and review comments first, since PR 2 touches
> the same files. Branch `xras-sweep-37` exists locally at 4244d013 (pre-#922): delete it and
> re-branch from `origin/staging`. Load `unplanned-city-sweep` (area
> mode over the XRAS tree in the handoff) and `wire-dashboard-feature`; read
> `.claude/rules/orm.md` and `webapp-ui.md`. Produce the ranked report from the inventory runs
> and every seed above; I pick; one commit per pick; the ledger entry with the metrics row.
