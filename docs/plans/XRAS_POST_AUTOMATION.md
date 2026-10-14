# XRAS: "Post to accounting service" does everything SAMuel can

**Status: roadmap, 2026-10-14.** Phase A (renames) is built and proven on XRAS-dev (#819);
phases B-E are not started. This file sequences the work and points at the plans that hold
the detail; it holds the decisions, not the designs.

## 1. The goal

An operator does two things in the XRAS admin app: **approve**, then **post to accounting
service**. The post does everything SAMuel can, and SAMuel files **one ticket per request**
for what it cannot. Ben, 2026-10-14.

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
| Users absent or inactive, ARC placeholders | the sweep files `account_request` rows (live); no ticket for them | users come only from IGA and the LDAP sync; the per-request ticket is unbuilt | D |
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

**D. One ticket per request.** `XRAS_REQUEST_TICKETS.md` (designed, not built). Filed when
the sweep first sees a request approved, with a checklist: users to create or reactivate,
placeholders to merge, contracts to create, push blockers. Resolved when the push lands.

Ben, 2026-10-14: **ticket first, post when clear, and a blocked post behaves coherently.**

- The operator posts once SAM shows the request ready. The post stays all-or-nothing: a
  project is never created without its full roster.
- A post while blockers remain still answers 422, and the error list XRAS shows verbatim
  reads "This action cannot yet succeed, see <ticket key>", then the remaining blockers.
- The same post keeps the ticket current. Ticket open (the normal case): the key is read
  from `external_ticket`, no outbound call before the reply, and an internal comment
  listing the blockers is added after it. No ticket yet (posted before the sweep saw the
  approval): the post opens one, either before replying so the 422 can name it, or after
  replying with "a ticket is being opened for this request"; measure the JSM call to choose.
- The message stays truthful in every ticket-provider state (Jira off means mail; a failed
  filing says so).

**E. Contracts where resolvable.** For an NSF award whose PI resolves to a SAM user, create
the contract at post time from `resolve_award`; otherwise it is a checklist item on the
request's ticket.

## 4. Order and dependencies

A first. D before any change to the New's 422, because the coherent blocked post needs the
ticket. B can land beside D. C waits for XRAS's mail to go off. E last, smallest. Each phase
is proven on samuel-dev ↔ XRAS-dev first (`XRAS_CLOSED_LOOP.md` § 4 holds the test bed and
its rules).

## 5. Related plans

- `XRAS_CLOSED_LOOP.md`: the rename (phase A) and the test bed.
- `XRAS_REQUEST_TICKETS.md`: the per-request ticket (phase D).
- `XRAS_SUBMISSION.md` § 4.2, § 4.6, § 4.7: the request record and the incoming side.
- `implemented/XRAS_AUTO_NOTICES.md`: the notice task and its phase-2 activation stub.
- `XRAS_INGEST_IMPROVEMENTS.md` § 4: paper cuts (a Supplement after a Renewal lands on the
  renewal's window).
