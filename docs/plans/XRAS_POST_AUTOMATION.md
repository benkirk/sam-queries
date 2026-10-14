# XRAS: "Post to accounting service" does everything SAMuel can

**Status: roadmap, 2026-10-14.** Phase A (renames) is built and proven on XRAS-dev (#819);
phases B-E are not started. This file sequences the work and points at the plans that hold
the detail; it holds the decisions, not the designs.

## 1. The goal

An operator does two things in the XRAS admin app, in one sitting: **approve**, then **post
to accounting service**. The post does everything SAMuel can. A post that cannot apply opens
**one ticket for that request** listing what it cannot do; a post that applies needs no ticket
and no NUSD. Ben, 2026-10-14.

What NUSD does by hand today after approval (Travis Fair, 2026-10-14): account creation and
user reconciliation, contract creation, the post, activating the project in SAM, copying the
new projcode into XRAS's "Edit Request Number", and sending the notification from XRAS.

## 2. Where each step stands

| Step | Today | Blocks full automation | Phase |
|---|---|---|---|
| Rename `NCAR####` → projcode | built (#819), off in production | merge, `XRAS_RENAME_ENABLED`, the `xras_rename` task | A |
| Project, accounts, memberships | automatic in the New handler (`sam/xras/handlers/new.py`) | the whole New answers 422 on any missing or inactive user, or unresolved contract | D |
| Activate the project | manual: the card's Activate (`webapp/dashboards/allocations/xras/lifecycle_routes.py`); a New is created inactive on purpose | an unattended path past `can_edit_project_governance` | B |
| Notify, New | manual Notify button; sent from XRAS today | must follow activation; XRAS's own notify mail | C |
| Notify, extend/supplement/adjust/update | built: the `xras_notices` task (1-day delay), disabled in production | soak; XRAS's own notify mail | C |
| Contracts | 422 with a seeded create form (Admin → Contracts), or the `ignore_contract` override | `contract.principal_investigator_user_id` is NOT NULL and SAM never creates a user; award lookup is NSF-only (`sam/integration/awards`) | E |
| Users absent or inactive, ARC placeholders | the sweep files `account_request` rows (live); no ticket for them | users come only from IGA and the LDAP sync; the ticket a blocked post opens is unbuilt | D |
| XRAS's own notify mail | on | an XRAS process setting (Steve) | C (cutover blocker) |

## 3. Phases

**A. Renames live in production.** `XRAS_CLOSED_LOOP.md` § 3.3, § 3.5.
Merge #819 and promote; backfill dry run (expect NCAR4293, 4212, 4300, 4354, 4432), apply;
flip `XRAS_RENAME_ENABLED`; enable `xras_rename` in production (the `tasks.env` lever, drop
it from `SAM_TASKS_DISABLED`, edit the two gate tests); tell NUSD to stop renaming (Travis
agreed 2026-10-14). A race is harmless: SAM's pre-read skips a line already renamed, and the
admin app's "Assign new request number" refuses a number in use. After A, NUSD still
activates in SAM and notifies from XRAS.

**B. Activation on post.** Ben, 2026-10-14: yes, when nothing blocks it. Once the New
applies cleanly, activate the project behind its own lever (`XRAS_AUTO_ACTIVATE`, default
off, dev on), in the handler or the after-reply hook beside the rename, recording
`xras_activation_event` with the API actor as `created_by`. This is the one deliberate
unattended exception to `can_edit_project_governance` (`implemented/XRAS_AUTO_NOTICES.md`,
"Phase 2 (later)").

**C. Notifications on post.** Blocked on turning off XRAS's own notify mail, which would
otherwise reach the PI twice; a cutover item for Steve. Then the New's activation notice
goes out right after activation through `build_xras_messages` (the button's dedup key, so
nothing double-mails), and the extend/supplement/adjust/update notices either move to post
time or keep the `xras_notices` delay as a correction window (decide then).

**D. The failed post opens the ticket.** `XRAS_REQUEST_TICKETS.md` (designed, not built).
Ben, 2026-10-14, the #819 second-pass review: 68 of 73 production News posted clean, so a
ticket per approval is churn; the ticket belongs to the post that fails.

- **Approve and post in one sitting.** A post that applies is done. The post stays
  all-or-nothing: a project is never created without its full roster.
- **A blocked post answers 422 and opens the ticket.** The New handler already collects every
  blocker into one list, so the 422 is the complete checklist and the ticket's body. NUSD works
  the list by hand and posts again; the post that applies resolves the ticket.
- **Nothing outbound before the reply.** The open, comment or resolve runs in the after-reply
  hook beside the rename, so a slow JSM call never delays XRAS. The first 422 therefore reads
  "a ticket is being opened"; later ones name the key, read from `external_ticket`. Every
  provider state has truthful wording (Jira off means mail; a failed filing says so).
- **The webapp files, the task never does.** Posts arrive at the webapp, which already holds
  the Jira levers, so the CronJob's "reads only" gates stand.

What the plan must hold (`XRAS_REQUEST_TICKETS.md` §§ 3, 4, 8):

1. **Repeats are idempotent.** NUSD re-posts on purpose and XRAS retries on its own (action
   403317 arrived three times). A later failed post finds the request's ticket and comments
   only when the blocker list changed (ledgered by digest), so a retry writes nothing.
2. **An approval nobody posts.** No post means no ticket. The sweep, which sees every approval,
   puts "approved, not posted" past N days on the Remediations card and in the queue digest.
3. **Link, do not restate, the sweep's rows.** The sweep still files `account_request` rows for
   absent and inactive people at approval, NUSD's head start; the ticket links to them.
4. **The first 422 cannot name its ticket.** The key lands on the ticket and SAM's card once
   it exists, and the next post's 422 names it.

**E. Contracts where resolvable.** For an NSF award whose PI resolves to a SAM user, create
the contract at post time from `resolve_award`; otherwise it is a checklist item on the
request's ticket.

## 4. Order and dependencies

A first. D before any change to the New's 422, because the blocked post's wording names the
ticket. B can land beside D. C waits for XRAS's mail to go off. E last, smallest. Each phase
is proven on samuel-dev ↔ XRAS-dev first (`XRAS_CLOSED_LOOP.md` § 4 holds the test bed and
its rules).

## 5. Related plans

- `XRAS_CLOSED_LOOP.md`: the rename (phase A) and the test bed.
- `XRAS_REQUEST_TICKETS.md`: the ticket a blocked post opens (phase D).
- `XRAS_SUBMISSION.md` § 4.2, § 4.6, § 4.7: the request record and the incoming side.
- `implemented/XRAS_AUTO_NOTICES.md`: the notice task and its phase-2 activation stub.
- `XRAS_INGEST_IMPROVEMENTS.md` § 4: paper cuts (a Supplement after a Renewal lands on the
  renewal's window).
