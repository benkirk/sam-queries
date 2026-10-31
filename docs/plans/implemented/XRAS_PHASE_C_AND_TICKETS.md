# XRAS phase C and production tickets: SAMuel sends the award mail; refused posts ticket NUSD

**Status: phase C live in production 2026-10-20: Part 1 (§ 4) merged in #845, Part 2 (§ 5) in #848, the cutover (§ 6) done; issue #846 closed 2026-10-20.** Tracked by issue #846 (burn-down checklist), PR #845; built on
branch `xras_incoming_tickets`. Lands phase C of [`XRAS_POST_AUTOMATION.md`](XRAS_POST_AUTOMATION.md)
and arms phase D ([`implemented/XRAS_REQUEST_TICKETS.md`](implemented/XRAS_REQUEST_TICKETS.md)) in production. The
award-mail checklist it carries out is [`implemented/XRAS_NOTICES_CUTOVER.md`](implemented/XRAS_NOTICES_CUTOVER.md).

## 1. Where things stand

| Phase | State |
|---|---|
| A, renames | live in production (#819, #820) |
| B, activation on post | live in production since 2026-10-15 (#828, #831) |
| D, a ticket for a refused post | built; dry-run on samuel-dev; production ships it in `SAM_TASKS_DISABLED`, no Jira lever in `tasks.env`, `XRAS_REQUEST_TICKETS_ENABLED=0` |
| C, SAMuel sends the award mail | not started: NUSD clicks Notify in XRAS, and XRAS mails from its own templates |

This work lands both remaining pieces:

1. **Arm D in production with a since-floor.** Only a request first refused after the floor ever
   gets a ticket, so the first run opens no backlog (Ben, 2026-10-19).
2. **Phase C.** SAMuel sends the New's notice right after B's activation, and the extension,
   supplement, renewal and adjustment notices through the `xras_notices` task. The mail is From
   `cislhelp@ucar.edu`, Reply-To `cislhelp@ucar.edu`, cc `alloc@ucar.edu`, **one message per
   action**: To the PI, cc the allocation manager when distinct. The wording reads coherently to
   both. Content draws on XRAS's award templates but does not need to match them.

Then the cutover: XRAS's Action Approved rules are deleted in `admin-ncar`, and a posted request
leaves the XRAS admin queue without a Notify click.

## 2. XRAS's award mail today (read 2026-10-19)

Read from `admin-ncar.xras.org` → Admin → Notifications, templates 154, 133, 1803, 417 (New and
Renewal), 135 (Extension) and 149 (Supplement).

| | XRAS New / Renewal | XRAS Extension / Supplement | SAMuel today |
|---|---|---|---|
| From | `cislhelp@ucar.edu` | `cislhelp@ucar.edu` | `sam-admin@ucar.edu` (`MAIL_DEFAULT_FROM`; `NOTIFY_XRAS_FROM` empty) |
| Reply-To | `cislhelp@ucar.edu` (417: none) | `cislhelp@ucar.edu` | `alloc@ucar.edu` (`NOTIFY_XRAS_REPLY_TO`) |
| cc | `alloc@`, Allocation Managers, Co-PIs, program officers; 133 also two nsf.gov addresses | Allocation Managers, program officers, `alloc@` | `alloc@` (`NOTIFY_XRAS_CC`) on **each** per-person message, so two copies when lead and admin differ |
| Subject | `<PI last> NCAR <Opportunity> Computing Request Approved` | `<PI last> NCAR [<AllocType>] <Action> Request Approved` | `NSF NCAR Project <projcode> is now active` and kin (`XRAS_KIND_SUBJECTS`) |

The New / Renewal body, in order: a letterhead; "Dear Dr. <last>"; approved, the reviewer's note
and the opportunity's note; a reporting-requirements paragraph; an allocation information block
(request number, end date, lead and organization, title, NSF award and award PI, awarded
resources); on 133 and 417 the reviews and an ARC link; getting-started and user-responsibility
links (End User Agreement, Getting Started, Help, Acknowledging NCAR and CISL); the acknowledgment
requirement and its consequence; a new-user-accounts paragraph; a questions block (cislhelp@,
303-497-2000); the Service Desk footer. Extension and Supplement are the short form: approved and
the reviewer's note, the information block, the questions block.

What SAMuel can fill:
- **The stored post** (`XrasActionLog.raw_payload`): title, PI name and organization
  (`roles[].person`), grants (`grantNumber`, as `sam/xras/handlers/_fields.py` reads it),
  opportunity name, allocation type, request number.
- **The project:** resources and end dates.
- **The reports feed:** the reviewer's note (`adminComments`, `sam/integration/xras_api/comments.py`).

Not available: the opportunity's note (`OPPORTUNITYADMINCOMMENTS`; probed at implementation) and
the reviews (the mail points to ARC instead).

## 3. Decisions (Ben, 2026-10-19)

- **Tickets:** armed with a since-floor; no backlog opens.
- **One message per action**, To the PI, cc the allocation manager (when distinct) and `alloc@`;
  one reader-neutral body, no per-role branches.
- **Addressing:** From and Reply-To `cislhelp@ucar.edu`; `alloc@ucar.edu` on the cc.
- **Salutation by name**, never an assumed "Dr.".
- **End User Agreement:** a muted footer line linking SAMuel's copy, e.g. "Your use of NSF NCAR
  HPC resources is contingent upon abiding by the terms of our End User Agreement", in place of
  XRAS's long NWSC paragraph.
- **Subjects:** `<PI last name> NSF NCAR <Opportunity> Request Approved`; without an opportunity
  name, `<PI last name> NSF NCAR <Action> Request Approved: <projcode>`.
- **Timing:** update, extend, supplement and adjust send at the task's next quarter-hour slot in
  business hours (no day's delay; the slots were hourly at the cutover); the New's notice goes
  out right after activation.
- **Adjustment** stays auto-sent, though XRAS never sent one.
- **Templates are reviewed one at a time** as they are written.
- **The cutover** deletes XRAS's award rules through the admin-ncar UX (Claude via Playwright,
  Ben's 2FA, his OK per rule) once SAMuel is sending.

## 4. Part 1: arm phase D in production

1. **Since-floor.** `SAM_TASKS_XRAS_TICKETS_SINCE` (ISO date) in
   `src/scheduling/tasks/xras_request_tickets.py`, parsed as `xras_rename.py` parses
   `SAM_TASKS_XRAS_RENAME_SINCE`. It drops an episode whose **first refusal ever** precedes the
   floor; clipping the window is not enough, because the first in-window refusal would then read
   as the first. Unset keeps today's behavior; `detail` records it.
2. **Chart** (`helm/values.yaml`): `xras_request_tickets` out of `SAM_TASKS_DISABLED`; in
   `tasks.env`, `TICKET_PROVIDER: "jira-servicedesk"`, `JIRA_WRITE_ENABLED: "1"`,
   `XRAS_REQUEST_TICKETS_ENABLED: "1"` and the floor; `webapp.env`
   `XRAS_REQUEST_TICKETS_ENABLED: "1"`; the Live/Disabled comment enumeration.
3. **Gates flip, not deletion:** every "production is off" pin becomes a pin of the one grant
   (`tests/unit/gates/test_helm_jira_levers.py`, `tests/unit/tasks/test_task_xras_request_tickets.py`,
   `helm/tests/test-cronjob-render.sh`, `helm/tests/test-dev-render.sh`).
4. **Docs:** `implemented/XRAS_REQUEST_TICKETS.md` § 9, `.claude/rules/notify-and-tasks.md`,
   `docs/README-k8s.md`, `XRAS_POST_AUTOMATION.md`.
5. **Before merge:** a production plan read with the floor set, in a task pod
   (`SAM_TASKS_DISABLED= SAM_TASKS_XRAS_TICKETS_SINCE=<date> sam-admin tasks --run xras_request_tickets --dry-run`,
   Ben's say-so); it should report 0 opens.

## 5. Part 2: SAMuel sends the award mail

**5.1 Addressing (chart only).** `NOTIFY_XRAS_FROM` and `NOTIFY_XRAS_REPLY_TO` become
`cislhelp@ucar.edu` (a ucar.edu address, so the SPF note in `values.yaml` holds);
`NOTIFY_XRAS_CC` stays `alloc@ucar.edu`. The CronJob takes all three by the `NOTIFY_*` prefix.

**5.2 One message per action.** `build_xras_messages` (`src/sam/queries/xras_notices.py`)
returns one `Message`: To the lead (the admin when the lead has no address), cc the admin when
distinct. A builder-set cc replaces the family cc today (`sam/notify/addressing.py`,
`tuple(cc) or env.cc`), so `alloc@` must be made to add, not vanish; the fix and its test are
decided at implementation. The dedup key stays `xras_dedup_key(kind, projcode, action_id,
<To address>)`, so existing ledger rows, the card's "notified" state and the Notify button's
suppression carry over. The reviewer's note rides on the one message. Callers: the Notify button
and its preview (`webapp/dashboards/allocations/xras/lifecycle_routes.py`), the `xras_notices`
task, `sam/queries/notification_previews.py`, `sam/notify/samples.py`.

**5.3 Templates** (`src/sam/notify/templates/xras_{activation,update,extension,supplement,adjustment}`,
text and HTML). New context, parsed from `raw_payload` and fail-open to `None`: request title,
PI name and organization, grants, opportunity name, allocation type, request number; the
samples in `samples.py` mirror them (they are the template editor's variables table). Blocks:
the approved line and reviewer's note; the allocation information block; for New and Renewal the
reporting requirement, getting-started links and the acknowledgment requirement, plus new user
accounts for a New; SAMuel's "manage your project" link; the questions block with cislhelp@; the
muted End User Agreement footer. Written and reviewed one template at a time against a real
project's preview. At implementation: check production for `notification_template_override`
rows on these files (an override shadows the shipped file), and probe the reports feed for the
opportunity's note.

**5.4 The New's notice right after activation.** In the after-reply hook
(`_activate_after_reply`, `src/webapp/api/xras/actions.py`): when `activate_posted_project()`
answers `'activated'`, build and send, then record `XrasActivationEvent('notified')` with the
`xras:` actor (send first, record second, as the Notify button does). Behind its own lever,
`XRAS_AUTO_NOTIFY` (default off), and fail-open. The site URL comes from `PUBLIC_BASE_URL`; there
is no request context after the reply. The shared dedup key means a later click or the task
cannot send it twice.

**5.5 The `xras_notices` task goes live.** `AUTO_NOTICES` gains `add`, selected only once the
project is active: the backstop for a failed hook or a card activation. The other services drop
to no delay. `SAM_TASKS_XRAS_NOTICES_SINCE` keeps the first run from mailing the 14-day backlog
XRAS already notified. Chart: `xras_notices` out of production's `SAM_TASKS_DISABLED`,
`XRAS_AUTO_NOTIFY: "1"` in both envs (samuel-dev's `NOTIFY_ENABLED=0` keeps it a no-send).
`test_task_xras_notices.py`'s switched-off pin is deleted, as its docstring asks.

**5.6 Docs.** `implemented/XRAS_NOTICES_CUTOVER.md` (its § 3 says production has no `alloc@` copy; the
copy has come from `NOTIFY_XRAS_CC` since #480), `XRAS_POST_AUTOMATION.md`,
`.claude/rules/notify-and-tasks.md`, one dated line in `implemented/XRAS_AUTO_NOTICES.md`.

## 6. Cutover

The order as decided (Ben, 2026-10-19); the detail is `implemented/XRAS_NOTICES_CUTOVER.md` § 6:

1. Production's rules recorded (`implemented/XRAS_NOTICES_CUTOVER.md` § 7) and the demo proof done (§ 8).
2. Promote the evening of 2026-10-19. SAMuel mails from the cutover floor, 08:00 MT 2026-10-20
   (`SAM_TASKS_XRAS_NOTICES_SINCE`, read by both the task and the New's notice).
3. NUSD stops clicking Notify from 08:00.
4. After the first SAMuel award mail is confirmed, delete the 29 Action Approved rules in
   `admin-ncar` (Claude via Playwright, Ben's 2FA, his OK per template; rules only).
5. First week: every applied action has a SAMuel `notification_log` row and no XRAS Action
   Approved mail; watch a New whose projcode already exists (it gets the Renewal letter).

## 7. Verification

- **Unit:** the builder (one message, To and cc, fallbacks, context from a fixture `raw_payload`
  such as `tests/fixtures/xras/actions/new_ncar4214_ok.json`); the addressing merge (builder cc,
  family cc and rows, no duplicates); the hook sends once and records `notified`; the task selects
  `add` only when active and honors its floor; the ticket floor filters on first refusal.
- **Gates:** `pytest` (full), `helm/tests/test-cronjob-render.sh`, `helm/tests/test-dev-render.sh`.
- **Previews:** each template through Admin → Notifications → Templates on the local dev server,
  for a real New, Extension and Supplement.
- **samuel-dev:** a New posted on XRAS-dev activates and logs its notice with no send; the ticket
  task stays on dry-run.
- **Production:** the plan read before merge (0 opens); after the promotion, the first refused New
  past the grace period files a real RC ticket, and the first applied New logs `xras_activation`
  From cislhelp@ with `alloc@` on the cc.
