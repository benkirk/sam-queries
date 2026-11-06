# Handoff: a placeholder XRAS no longer holds still reads "Ready to merge"

**Status:** in progress, 2026-11-05: sweep 37's PR 1 (branch `stale-placeholder-rows`), after the
XRAS approval sprint (#920) landed. The census that preceded it: `XRAS_SWEEP_37_HANDOFF.md`.
**Kickoff:** "Read docs/plans/STALE_PLACEHOLDER_ROWS.md. Make the Accounts card retire a placeholder
that only an old push names and XRAS's current roster does not, record a `gone` in the ledger, and
bound the reconcile task's ready set. Branch from `origin/staging`, one PR."

## What happened

First production wake of `XRAS_AUTO_MERGE` (`account_requests_reconcile`, 2026-11-05 16:52 MST,
image `sha-ba4b9b1`). The Accounts card and `sam-admin xras --identity-report` showed one
"Ready to merge" row: `kmerk-user-92wap` into `katarinam`, unblocking NCAR4434. The task asked
XRAS for the placeholder, got nothing, counted it `gone`, and merged nothing. Correct: XRAS's
`/v1/people/<u>` has no such person (checked from the pod; the target and another unreconciled
placeholder both resolve, so it is not a visibility difference between API contexts). The row
came from Feed A alone: SAM's `xras_action_log` #350, the one push XRAS sent for NCAR4434
(2026-10-21, refused 422), whose roster names the placeholder. Someone removed or merged that
person inside the XRAS admin app and never re-posted. The same run's stale-close pass dismissed
six fulfilled placeholder requests on the same reading (ids 5-12), also correct.

Cost while it stands: the card invites a Merge that will 404, the identity report counts an
unblock that cannot happen, and the task asks XRAS about the same name every 15 minutes.

## The mechanism (read, not assumed)

- `assemble_worklist` (`src/sam/queries/xras_accounts.py` ~1219-1246) joins Feed A
  (`records_from_action_log`, pushes SAM received) and Feed B (the sweep's cached pending
  requests) **by casefolded username only** (`classify_accounts` ~724-794, `merge_worklists`
  ~850-905). Nothing compares an A row's username against XRAS's current roster for the same
  `request_number`, so a username B stopped naming survives as long as A names it.
- Two things retire a Feed-A username today: a later real post for the same `action_id`
  (`superseded_log_ids` ~405-422) and a `merge_person` event with status `verified`
  (`merged_away_usernames` ~425-430). A hand fix inside XRAS with no re-post produces neither.
- `stamp_merge_targets` (~1113-1145) marks `remedy = 'merge'` from the SAM side alone: one
  active SAM user holds the placeholder's email. No XRAS state is consulted.
- `ready_to_merge` (`src/sam/manage/xras_remediation.py` ~497-510) calls `assemble_worklist`
  with **no `since`**, so Feed A on the task path covers every log row ever written. The card
  defaults to 30 days (`_ACTIVITY_DEFAULT_DAYS`), the sweep to 90 (`DEFAULT_WINDOW_DAYS`).
- The `gone` branch of `merge_ready_placeholders` (~538-541) is a counter only: no event, no
  cache patch. `task_merge_attempts` (`src/sam/integration/xras.py` ~1106-1119) reads
  `merge_person` events by a `task:` actor **of any status**, so an event row of any status
  would already stop the retry. `XRAS_REMEDIATION_STATUSES` has no `gone`; `rejected` fits.
- `close_stale_placeholders` deliberately excludes the ready set
  (`stale_placeholder_requests(exclude=ready)`), so the stale row is skipped by both passes.
- The signal a fix needs already exists: the sweep's request index stores each pending request's
  **current** XRAS roster (`request_index_entry`, `src/sam/queries/xras_requests.py` ~503-591;
  `load_requests_index()` in `xras_api/cache.py`), and `request_numbers_by_username` (~775-785)
  answers "which requests still name this person". Used today only by My Requests and the
  account-requests admin route.

## The fix, three pieces, one PR

1. **Assembly-time comparison, no XRAS call.** After `stamp_merge_targets`, for each placeholder
   row whose `sources` is Feed A only: take its citing request numbers; if every one of them is in
   the request index and none of their current rosters names the username, the row is a stale
   push: **drop it** (Ben, 2026-11-05). The request's own Remediations row still shows its real
   blockers, so nothing an operator must do is hidden. Count the drops in the assembled
   worklist's stats so the card's counts and the identity report agree. A request absent from
   the index (aged out of the sweep) is undecidable: leave the row as it is. The index is rebuilt
   on the daily full sweep (04:00 MT) while the pending feed refreshes hourly; read the fresher
   of the two, or accept a day's lag and say so in the docstring.
2. **Record `gone`.** In `merge_ready_placeholders`, a `gone` source opens and closes a
   `merge_person` event as `rejected` with reason "XRAS holds no such person", so the no-retry
   check sees it and the ledger says who found it and when. Keep the counter.
3. **Bound the task's ready set.** `ready_to_merge` passes `since` = the sweep's 90-day window
   (the same `DEFAULT_WINDOW_DAYS` and env override), so a refused push from any date cannot put
   a placeholder on the auto-merge list.

Keep the stale-close pass's exclusion of the ready set; with piece 1 the stale row leaves the
ready set and the pass can then judge its account request like any other.

## Tests to extend

- `tests/unit/xras/test_xras_accounts_query.py`: `TestFeedA` (beside
  `test_a_merged_away_identity_is_history_not_work`), `TestMergeTargets`, `TestFeedB`.
- `tests/unit/xras/test_xras_identity_report.py`: `targets` and the counts exclude a dropped row.
- `tests/unit/xras/test_xras_accounts_card.py`: the row is absent; `counts.merge_ready` agrees.
- `tests/unit/xras/test_xras_remediation_service.py` `TestTheReadyMerge`: `gone` writes a
  `rejected` event; a second run reports it `refused`; the window is passed.
- `tests/unit/tasks/test_task_account_requests_reconcile.py`: unchanged shape, detail keys.

## Verification

- Unit: the suites above plus the gates (`test_modal_shell_contract.py`, `test_docs.py`).
- Prod, read-only, after deploy: `sam-admin xras --identity-report` no longer lists
  `kmerk-user-92wap` (the index's NCAR4434 roster is `maraf`, `katarinam`, checked 2026-11-05);
  the Accounts card has no row for it; the reconcile run's detail shows no `due` for it. Piece 2
  is proved by a placeholder that IS still on a roster but missing from `/v1/people`: `gone`
  once with a `rejected` ledger row, then `refused` on later runs. NCAR4434 itself then stops on
  contract 2019589 and its PI's missing institution, not on identity.

## Open

- Decided 2026-11-05 (Ben): drop the stale row rather than label it. The request's Remediations
  row carries the re-post chore already.
- Whether `gone` should also dismiss the row's open `AccountRequest` directly, or leave it to the
  stale-close pass once the row leaves the ready set (recommended: leave it).
