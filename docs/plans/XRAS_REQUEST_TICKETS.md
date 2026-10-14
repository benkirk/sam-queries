# One NUSD ticket per blocked XRAS post

**Status: design, not built.** Phase D of [`XRAS_POST_AUTOMATION.md`](XRAS_POST_AUTOMATION.md).
Ben, 2026-10-14 (the #819 second-pass review): **the failed post opens the ticket.** Approvers
approve and post in one sitting; a post that applies is done, with no ticket and no NUSD. A post
that cannot apply answers 422 with its blockers, and that same post opens the ticket NUSD works
from. It needs no table of its own: `external_ticket` keyed on the request's `request_id`
(stable across the rename) holds the ticket, and `notification_log` the comments (§ 4).

## 1. Goal

A ticket exists only where a person has work to do. Of the News approved since the
2026-08-24 repoint, 73 have applied, 3 stand refused and 3 are unposted (the #819 census), so a
ticket filed on every approval would be opened and closed with nothing in it for nearly all.

- **Opened** by the first post of a request that answers 422. Its body is that post's blocker
  list.
- **Commented** by each later post that still answers 422, when its blocker list differs from
  the last one recorded.
- **Commented, not closed,** by the post that applies: a last internal comment says it applied
  and names the projcode, and NUSD closes the ticket. Travis, 2026-10-14: start without
  auto-close, to see how the process feels; closing on success is a later switch.

Every write rides the post itself: the ticket's life is driven by XRAS's calls to SAM, not by
a schedule. NUSD works the blockers by hand and posts again when they are clear.

The sweep keeps filing one `account_request` row per absent or inactive person when it sees an
approval, with no ticket of their own (`sam/notify/kinds.py`, "XRAS-sweep rows never ticket").
Those rows are NUSD's head start; the request ticket links to them (§ 3) instead of restating
them.

## 2. Trigger: the 422, not the approval

The New handler already accumulates every blocker into one error list before it answers
(`sam/xras/handlers/new.py`: the roster, area of interest, allocation type, mnemonic,
contracts, then dates and each resource, all append to `self.errors`). A 422 from a New is
therefore the checklist for that request at that moment, and it is the ticket's source of
truth. It is complete only for what could be checked: a step whose input failed is skipped
(no PI resolved leaves only "Could not produce affiliation data"; a resource, amount or date
error skips that resource's window check, `new.py` `_plan_allocations`), so clearing one
blocker can reveal another on the next post. The comment-on-change rule (§ 4) carries that.

So:

- No "first seen approved" instant is needed for the ticket, and no preflight digest built on
  the daily pass. The flapping problem of a sweep-built digest (verdicts present on the full
  pass, absent on light runs) does not arise.
- An action type other than New or Renewal does not open a ticket in this phase. A Supplement
  or Extension that answers 422 is an operator problem on an existing project, and its 422
  text already says what is wrong.

### 2.1 What production's refused News looked like (2026-08-24 to 2026-10-14)

22 requests had a New or Renewal refused, out of about 90 that applied. Their blockers, a request
often carrying more than one:

| Requests | Blocker |
|---|---|
| 14 | affiliation or mnemonic (the operator's fix is the card's mnemonic override) |
| 15 | a person missing or inactive in SAM |
| 5 | a contract |
| 1 | two Allocation Manager roles in range |

How they ended:

- **7 applied within 10 minutes of the first refusal.** The operator fixed it on the spot and
  posted again. A ticket filed on the first 422 would open and need closing by hand (no
  auto-close, § 7) for a fix that took two minutes.
- **2 within a day; 9 after more than a day** (up to 9 days).
- **4 are still open** (NCAR4281, NCAR4340, NCAR4395, NCAR4391).
- **6 applied under a different number.** NUSD created the project in SAM by hand, renamed the
  request to its projcode, and posted again, so the New applied as an update. The `request_id`
  stayed the same, so a ticket keyed on it (§ 4) resolves through that route too.

## 3. The ticket body

| Section | Source |
|---|---|
| Blockers, verbatim | the 422's error list, in the order the handler emits it |
| People already queued | the sweep's open `account_request` rows for this roster: id, person, purpose, link to the Accounts queue |
| Placeholders to merge | `sam_merge_targets` hits for the roster: an operator step in XRAS, not NUSD's |
| Request | request number as posted, `request_id`, PI, opportunity, the Remediations card link |

## 4. Repeated posts and XRAS retries

A request is posted more than once on purpose (NUSD re-posts after each fix) and by accident
(XRAS retried action 403317 three times during a MySQL stall; `XRAS_ACTION_STATUSES`
`duplicate` and #803 handle the apply side). The ticket side must be idempotent in the same way:

- **One ticket per request.** Before opening, look up the request's `external_ticket`
  (`entity_type` for an XRAS request, `entity_id` = `request_id`) and then `provider.find(handle)`. Open only when both miss.
  This is `handoff_mail.py`'s find-before-create routine, lifted (§ 5).
- **Comment only on change.** Hash the blocker list. Ledger each comment in `notification_log`
  as `xras_ticket:<request_id>:<digest>` (`dedup_key` is String(128)), so an identical retry
  writes nothing. **Caveat:** A -> B -> A does not post the second A; accept it, or fold a
  sequence number into the key.
- **Internal comments only.** A public comment moves the request to "Customer wait"
  (`reference_ithelp_jira_api`).

## 5. Filing from the post

The post arrives at the webapp (`webapp/api/xras/actions.py`), which already holds the Jira
levers and the provider. The scheduled task never files, comments or resolves. The task-side
"reads only" gates stand unchanged: `helm/templates/cronjob-tasks.yaml`,
`tests/unit/gates/test_helm_jira_levers.py` (`TestTheCronJobReadsOnly`),
`scheduling/tasks/account_requests_reconcile.py`, and `tickets/registry.py` / `learn.py`.

- **After the reply, never before.** The 422 goes back to XRAS first, and the open or comment
  runs in the after-reply hook beside the rename, so a slow JSM call never sits on the
  request XRAS is waiting on (Steve saw the admin app time out). The first 422 therefore cannot
  name its ticket; § 6 covers the wording.
- **Lift the filer, do not copy it.** `webapp/register/handoff_mail.py` holds a complete
  find-before-create routine: the ledger check, `provider.find(handle)`, a `queued` row then
  `create`, `_filed_anyway` for a create that timed out, and `_link_ticket` on its own session.
  Move it into a Flask-free `sam/integration/tickets/filing.py` taking the provider, the
  ledger, a session factory and `(entity_type, entity_id, handle, kind)`. Rename what stays to
  `webapp/register/handoff.py` (its importers are `register/blueprint.py`,
  `register/invite.py`, `dashboards/project_invites.py`, `dashboards/event_lifecycle.py` and
  `dashboards/admin/account_requests_routes.py`, plus four test files, two rule files and three
  docs).
- **A new kind.** `KINDS` entry `xras_request` (`tickets/base.py`) with its own
  `JIRA_XRAS_REQUEST_*` settings, on the webapp only. Handle `SAM-XR-<request_id>`; `jira.py`'s
  exact-match filter needs the prefix.
- **Provider gaps.** `transition(key, ...)` (resolve; Jira 761 needs a `resolution`) is needed
  only when closing on success is switched on; add it then, as an optional method with a safe
  default, like `comment`. `learn` covers only `account_request` /
  `account_ticket` (`tickets/queries.py`): extend it, or give this kind no mail fallback.

## 6. What the 422 says

XRAS shows SAM's error list verbatim, so the first line carries the ticket state and the
blockers follow:

| State | First line |
|---|---|
| First failed post | "This action cannot yet succeed. A ticket is being opened with NUSD." |
| Ticket open | "This action cannot yet succeed, see `<key>`." |
| Jira off (mail fallback) | "This action cannot yet succeed. NUSD has been notified by email." |
| Last filing failed | "This action cannot yet succeed. Opening a ticket failed; NUSD has not been notified." |

The key also appears on the request's Remediations card as soon as it is known.

## 7. Resolving

The post that applies writes the ticket's last comment in its after-reply hook ("applied as
<projcode>"), keyed on the `request_id` of the posted line; NUSD closes the ticket (§ 1). A family holds several lines, each with its own id, and `primary_line` moves
when a newer action lands on another line, so the key is the stored id, never one re-read from
`primary_line`. The `request_number` is carried and updated when observed; it changes at the
rename (`XRAS_CLOSED_LOOP.md` § 3), so it is never the key.

`xras_action_log` is the record: a processed row for the request (`request_number` as pushed,
`projcode_result`) is the landed push. `live_pending_numbers` (`sam/queries/xras_requests.py`)
already subtracts `pushed_request_numbers`, matched on the number as pushed; it lacks the
`request_id` rule of `resolve_project`, so a line whose number changed between approval and
push is the one case it still reads as pending.

## 8. An approval nobody posts

The old trigger covered this for free; the new one does not, because no post means no ticket.
The sweep already sees every approval within 15 minutes in business hours. A request approved
for more than N days with no post (no `xras_action_log` row for its `request_id`) goes on the
Remediations card as "approved, not posted" and into the account-queue digest. No ticket:
the operator's next step is the post, and the post opens the ticket if one is needed.

## 9. Rollout

1. **Dev, shadow.** The after-reply hook computes the body and records "would open / comment" in
   the action log's outcome, with `JIRA_XRAS_REQUEST_*` unset.
2. **Dev, filing** against the RC project with a label and immediate cancel, the way
   RC-40275/40276 were smoked. Work the blocked-New scenarios on the bed
   (`XRAS_CLOSED_LOOP.md` § 4). Scenario 6, the placeholder merge, ran 2026-10-14
   (`XRAS_DEV_PROBES.md` § 6.1): the four-error 422, the merge, the repost that applied.
3. **Prod** with the lever.

Probes on samuel-dev against XRAS-dev, 2026-10-13
([`XRAS_DEV_PROBES.md`](../xras/outgoing/XRAS_DEV_PROBES.md) § 4): the push row carries
`request_number` as sent (`NCAR4086`), `request_id` and `projcode_result` (`UCSG0004`), and
`requestId` is unchanged by the rename, so § 7's lookup from the log works.

## 10. Open questions for Ben

- **A grace period before filing.** A third of production's refused News applied within ten
  minutes (§ 2.1). Open the ticket only when the request is still refused some minutes after
  its first 422 (on the next post, or the next sweep pass), or open it at once and accept the
  closing by hand?
- **Ticket type and audience.** Request type 20 "Add a user", or a new one? Reporter
  `rc-sam-bot`; on behalf of the PI?
- **N for "approved, not posted".** Two business days?
- **Comment cadence.** A floor on comments, for example at most one per day per ticket, beyond
  "only on change"?
- **Drop-out.** When XRAS deletes a request with an open ticket, does SAM cancel it? The sweep
  sees the deletion; nothing acts on it today.
