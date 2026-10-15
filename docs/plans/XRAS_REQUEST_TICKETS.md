# One NUSD ticket per blocked XRAS post

**Status: built 2026-10-14, dev first** (`xras-post-automation-bd`). samuel-dev files through
the `dry-run` provider; production ships the task disabled with no ticket lever. § 11 holds what
differs from this design. Phase D of [`XRAS_POST_AUTOMATION.md`](XRAS_POST_AUTOMATION.md).

**The model** (Ben, 2026-10-14 and 2026-10-15):
- Approvers approve and post in one sitting, and a post that applies is done: no ticket, no
  NUSD.
- A post that cannot apply answers 422 with its blockers, each naming where it is fixed.
- If the request is still refused after a short grace period, the `xras_request_tickets` task
  opens one ticket for it, and NUSD works from that ticket.
- Every Jira write is the task's.

The design needs no table of its own: `external_ticket`, keyed on the request's `request_id`
(stable across the rename), holds the ticket, and `notification_log` holds the comments (§ 4).

## 1. Goal

A ticket exists only where a person has work to do. Of the News approved since the 2026-08-24
repoint, 73 have applied, 3 stand refused and 3 are unposted (the #819 census). A ticket filed
on every approval would be opened and closed with nothing in it for nearly all of them.

- **Opened** by the task when a request's New or Renewal has been refused for longer than the
  grace period (§ 2.1) and no post has applied since. Its body is the latest refusal's
  blocker list.
- **Commented** by the task when a later refusal's blocker list differs from the last one
  recorded.
- **Commented, not closed,** once a post applies: a last internal comment says so and names
  the projcode, and NUSD closes the ticket. Travis, 2026-10-14: start without auto-close, to see
  how the process feels; closing on success is a later switch.

The posts drive the ticket's content and the task carries it to Jira, so nothing about tickets
runs on the request path. NUSD works the blockers by hand and posts again when they are clear.

The sweep keeps filing one `account_request` row per absent or inactive person when it sees an
approval, with no ticket of their own (`sam/notify/kinds.py`, "XRAS-sweep rows never ticket").
Those rows are NUSD's head start; the request ticket links to them (§ 3) instead of restating
them.

## 2. Trigger: a refusal that outlives the grace period

The New handler already accumulates every blocker into one error list before it answers
(`sam/xras/handlers/new.py`: the roster, area of interest, allocation type, mnemonic,
contracts, then dates and each resource, all append to `self.errors`). A 422 from a New is
therefore the checklist for that request at that moment, and it is the ticket's source of
truth.

It is complete only for what could be checked. A step whose input failed is skipped: no PI
resolved leaves only "Could not produce affiliation data", and a resource, amount or date
error skips that resource's window check (`new.py` `_plan_allocations`). So clearing one
blocker can reveal another on the next post; the comment-on-change rule (§ 4) carries that.

So:

- No "first seen approved" instant is needed, and no preflight digest built on the daily pass.
  The flapping problem of a sweep-built digest (verdicts present on the full pass, absent on
  light runs) does not arise.
- An action type other than New or Renewal does not open a ticket in this phase. A Supplement
  or Extension that answers 422 is an operator problem on an existing project, and its 422 text
  already says what is wrong.

### 2.1 What production's refused News looked like (2026-08-24 to 2026-10-14)

22 requests had a New or Renewal refused, out of about 90 that applied. Their blockers, as
`row_blockers` classifies them (a request often carries more than one):

| Requests | Blocker |
|---|---|
| 12 (7 with nothing else) | a person: a roster member missing or inactive in SAM, or two Allocation Manager roles in range |
| 11 (6 with nothing else) | a mnemonic: a PI SAM holds whose affiliation has no code; the card's mnemonic override fixes it |
| 5 | a contract |

A PI SAM does not hold also reports "Could not produce affiliation data". That line is part of
the person blocker, not a mnemonic one: an override clears it and leaves the 422.

How they ended:

- **7 applied within 10 minutes of the first refusal, the slowest in 9.** Each was one fix at
  the console, then a repost:
  - 3 contracts created, through the 422's seeded form;
  - 1 mnemonic link created;
  - 3 inactive members dropped or reactivated;
  - 1 placeholder merged, with a mnemonic override (NCAR4306 needed two of these fixes).
- **2 within a day; 9 after more than a day** (up to 9 days).
- **4 are still open** (NCAR4281, NCAR4340, NCAR4395, NCAR4391).
- **6 applied under a different number.** NUSD created the project in SAM by hand, renamed the
  request to its projcode, and posted again, so the New applied as an update. The `request_id`
  stayed the same, so a ticket keyed on it (§ 4) follows that route too.

**The grace period is 10-15 minutes** (Ben, 2026-10-15). In these 7 cases NUSD posted and fixed
in one sitting. After the cutover the approver posts, and the same refusals are exactly the
hand-off a ticket is for; the grace period absorbs a fix the poster can make on the spot, and
the 422's pointers (§ 6) make that fix quicker. It counts from the request's first refusal, so
an operator posting repeatedly cannot postpone the ticket indefinitely.

## 3. The ticket body

| Section | Source |
|---|---|
| Blockers, verbatim | the latest refusal's error list, in the order the handler emits it |
| People already queued | the sweep's open `account_request` rows for this roster: id, person, purpose, link to the Accounts queue |
| Placeholders to merge | `sam_merge_targets` hits for the roster: an operator step in XRAS, not NUSD's |
| Request | request number as posted, `request_id`, PI, opportunity, the Remediations card link |

## 4. Idempotency

A request is posted more than once on purpose (NUSD re-posts after each fix) and by accident
(an operator clicked Post three times for action 403317 during a MySQL stall;
`XRAS_ACTION_STATUSES` `duplicate` and #803 handle the apply side). XRAS never retries a post
on its own: every post is a click in the admin app. The task reads the request's state rather
than reacting to each post, and it is idempotent the same way:

- **One ticket per request.** Before opening, look up the request's `external_ticket`
  (`entity_type` for an XRAS request, `entity_id` = `request_id`), then `provider.find(handle)`.
  Open only when both miss. This is `handoff_mail.py`'s find-before-create routine, lifted (§ 5).
- **Comment only on change.** Hash the blocker list and ledger each comment in
  `notification_log` as `xras_ticket:<request_id>:<digest>` (`dedup_key` is String(128)). A wake
  that finds nothing new writes nothing. **Caveat:** A -> B -> A does not post the second A;
  accept it, or fold a sequence number into the key. The applied comment is keyed
  `xras_ticket:<request_id>:applied`.
- **Internal comments only.** A public comment moves the request to "Customer wait"
  (`reference_ithelp_jira_api`).

## 5. Filing from a task

The task owns every Jira write for two reasons:
- **The grace period needs a clock.** Once it ends there may be no further post to file from.
- **Nothing outbound sits on the request XRAS is waiting on.** XRAS's admin app times out on a
  slow reply (Steve saw it), so a Jira call on the post's path, before or after the reply,
  was always the risk.

The `xras_request_tickets` task (`src/scheduling/tasks/`) wakes with the CronJob at
:07/:22/:37/:52, so a ticket opens 10-25 minutes after the first refusal. On each wake it
reads `xras_action_log`:

- **Open:** requests whose first New or Renewal refusal is older than the grace period, with
  no `processed` row for the same `request_id` since and no ticket yet.
- **Comment:** requests with a ticket whose latest refusal's blocker list is new (§ 4).
- **Applied:** requests with a ticket and a `processed` row after it, not yet commented.

The task holds the Jira levers like the webapp: `JIRA_WRITE_ENABLED` and `TICKET_PROVIDER` in
`tasks.env`. It works behind its own `XRAS_REQUEST_TICKETS_ENABLED` (off by default) and
`SAM_TASKS_DISABLED` until rollout. This is the CronJob's one Jira writer: the chart comment,
`test_helm_jira_levers.py` and `test-cronjob-render.sh` name it, and every other task stays
read-only for Jira.

- **Lift the filer, do not copy it.**
  - `webapp/register/handoff_mail.py` holds a complete find-before-create routine: the ledger
    check, `provider.find(handle)`, a `queued` row then `create`, `_filed_anyway` for a create
    that timed out, and `_link_ticket` on its own session.
  - Move it into a Flask-free `sam/integration/tickets/filing.py` taking the provider, the
    ledger, a session factory and `(entity_type, entity_id, handle, kind)`.
  - Rename what stays to `webapp/register/handoff.py`. Its importers are
    `register/blueprint.py`, `register/invite.py`, `dashboards/project_invites.py`,
    `dashboards/event_lifecycle.py` and `dashboards/admin/account_requests_routes.py`, plus four
    test files, two rule files and three docs.
- **A new kind.** A `KINDS` entry `xras_request` (`tickets/base.py`) with its own
  `JIRA_XRAS_REQUEST_*` settings, carried by the task. Handle `SAM-XR-<request_id>`; `jira.py`'s
  exact-match filter needs the prefix.
- **Provider gaps.**
  - `transition(key, ...)` (resolve; Jira 761 needs a `resolution`) is needed only when closing
    on success is switched on. Add it then, as an optional method with a safe default, like
    `comment`.
  - `learn` covers only `account_request` / `account_ticket` (`tickets/queries.py`): extend it,
    or give this kind no mail fallback.
- **What else changes with it:**
  - the warning comment in `helm/templates/cronjob-tasks.yaml`;
  - `TestTheCronJobReadsOnly`, which becomes a pin of the one grant;
  - the `account_requests_reconcile` docstring, whose "never a tracker write" holds for that
    task but should not cite the CronJob's lack of the lever;
  - one dated line in `implemented/TICKET_PROVIDER.md` § 9;
  - one line in `.claude/rules/notify-and-tasks.md`.

## 6. What the 422 says

XRAS shows SAM's error list verbatim, so the 422 carries three things:
- a first line with the ticket state;
- the blockers;
- for each blocker kind present, one line saying where it is fixed.

The legacy blocker strings stay byte-identical (the parity oracle keys on them), so the pointers
are extra lines, never edits to them. The webapp reads the state from `external_ticket` and the
task's last attempt, and makes no outbound call.

| State | First line |
|---|---|
| Refused, no ticket yet | "This action cannot yet succeed. If it is still refused in about 15 minutes, a ticket opens with NUSD." |
| Ticket open | "This action cannot yet succeed, see `<key>`." |
| Jira off (mail fallback) | "This action cannot yet succeed. NUSD has been notified by email." |
| Last filing failed | "This action cannot yet succeed. Opening a ticket failed; NUSD has not been notified." |

| Blocker kind | Pointer line |
|---|---|
| contract | create it from the seeded form (Admin → Contracts), or waive it with the request's contract override |
| mnemonic | link the organization or institution to a code (Admin → Mnemonic Codes), or set the request's mnemonic override |
| person, placeholder | merge the placeholder into the real account (Allocations → XRAS → Pending Users) |
| person, missing or inactive | create or reactivate the account, or drop the member from the request in XRAS |

The key also appears on the request's Remediations card as soon as it is known.

## 7. Resolving

Once a post applies, the task writes the ticket's last comment ("applied as <projcode>"),
keyed on the `request_id`, and NUSD closes the ticket (§ 1). A family holds several lines, each
with its own id, and `primary_line` moves when a newer action lands on another line, so the key
is the stored id, never one re-read from `primary_line`. The `request_number` is carried and
updated when observed; it changes at the rename (`XRAS_CLOSED_LOOP.md` § 3), so it is never the
key.

`xras_action_log` is the record: a processed row for the request (`request_number` as pushed,
`projcode_result`) is the landed push. `live_pending_numbers` (`sam/queries/xras_requests.py`)
already subtracts `pushed_request_numbers`, matched on the number as pushed. It lacks the
`request_id` rule of `resolve_project`, so a line whose number changed between approval and
push is the one case it still reads as pending.

## 8. An approval nobody posts

No post means no ticket. The sweep already sees every approval within 15 minutes in business
hours. A request approved for more than N days with no post (no `xras_action_log` row for its
`request_id`) goes on the Remediations card as "approved, not posted" and into the
account-queue digest. No ticket: the operator's next step is the post.

## 9. Rollout

1. **Dev, dry-run filing.** samuel-dev has no Jira, so the task runs armed on
   `TICKET_PROVIDER=dry-run`: real `external_ticket` and ledger rows, `DRY-SAM-XR-<id>` keys,
   each body and comment in the task log, nothing posted. (Off, the task is a shadow: its detail
   records "would open / comment / applied".)
2. **Dev, the scenarios.** Work the blocked-New scenarios on the bed
   (`XRAS_CLOSED_LOOP.md` § 4). Scenario 6, the placeholder merge, ran 2026-10-14
   (`XRAS_DEV_PROBES.md` § 6.1): the four-error 422, the merge, the repost that applied.
3. **Prod:** the request type and labels (`JIRA_XRAS_REQUEST_*`), then
   `XRAS_REQUEST_TICKETS_ENABLED` in both envs, `TICKET_PROVIDER` and `JIRA_WRITE_ENABLED` in
   `tasks.env`, and the task out of `SAM_TASKS_DISABLED`. A first filing against the RC project
   with immediate cancel, the way RC-40275/40276 were smoked. **First, read the plan:** the
   30-day lookback opens a ticket for every refused New or Renewal that never applied,
   including ones NUSD handled without a re-post, and past 10 opens the cap raises. The kill
   switch outranks `--dry-run`, so clear it for that one run, in a task pod:
   `SAM_TASKS_DISABLED= sam-admin tasks --run xras_request_tickets --dry-run`.

Probes on samuel-dev against XRAS-dev, 2026-10-13
([`XRAS_DEV_PROBES.md`](../xras/outgoing/XRAS_DEV_PROBES.md) § 4): the push row carries
`request_number` as sent (`NCAR4086`), `request_id` and `projcode_result` (`UCSG0004`), and
`requestId` is unchanged by the rename, so § 7's lookup from the log works.

## 10. Open questions for Ben

- **Ticket type and audience.** Request type 20 "Add a user", or a new one? Reporter
  `rc-sam-bot`; on behalf of the PI?
- **N for "approved, not posted".** Two business days?
- **Comment cadence.** A floor on comments, for example at most one per day per ticket, beyond
  "only on change"?
- **Drop-out.** When XRAS deletes a request with an open ticket, does SAM cancel it? The sweep
  sees the deletion; nothing acts on it today.

## 11. As built (2026-10-14)

| Design | Built | Why |
|---|---|---|
| Rename `handoff_mail.py` to `handoff.py` | the filer lifted to `sam/integration/tickets/filing.py`; `handoff_mail.py` keeps its name | it still holds the mail fallback; the rename is churn for later |
| Four 422 states, one a mail fallback | three: no ticket yet, open under its key, last filing failed | this kind has no mail path |
| The state line always | only with `XRAS_REQUEST_TICKETS_ENABLED`; the pointer lines always | a disabled task must not have the 422 promise a ticket |
| Open keyed on the blocker digest | `xras_ticket:<id>:open`, then a `<digest>` row for the body it carried | the open is once-only even when the list changes before the link lands |
| — | 30-day lookback; `SAM_TASKS_XRAS_TICKETS_MAX` (10 opens) raises before any filing | the first arming's blast radius |
| § 8, approved not posted | not built | deferred (Ben, 2026-10-14) |
| Dev, a filing against RC | the `dry-run` provider, outside `PROVIDERS` and refused to the webapp | dev has no Jira |
| — | a linked ticket counts as open even after NUSD closes it: a later refusal comments on it and the 422 names it | edge, unbuilt: the hourly refresh already stamps `closed_at` on these rows in prod, so the fix is reading `ExternalTicket.is_active` |

