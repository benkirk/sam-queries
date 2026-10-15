# XRAS: "Post to accounting service" does everything SAMuel can

**Status: roadmap, 2026-10-14.** Phase A (renames) is live in production (#819, #820); phases
B-E are not started. This file sequences the work and points at the plans that hold the
detail; it holds the decisions, not the designs.

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
| Rename `NCAR####` → projcode | live in production 2026-10-14 (#819, #820): the route and the task | NUSD stops renaming by hand | A |
| Project, accounts, memberships | automatic in the New handler (`sam/xras/handlers/new.py`) | the whole New answers 422 on any missing or inactive user, or unresolved contract | D |
| Activate the project | manual: the card's Activate (`webapp/dashboards/allocations/xras/lifecycle_routes.py`); a New is created inactive on purpose | an unattended path past `can_edit_project_governance` | B |
| Notify, New | manual Notify button; sent from XRAS today | must follow activation; XRAS's own notify mail | C |
| Notify, extend/supplement/adjust/update | built: the `xras_notices` task (1-day delay), disabled in production | soak; XRAS's own notify mail | C |
| Contracts | 422 with a seeded create form (Admin → Contracts), or the `ignore_contract` override | `contract.principal_investigator_user_id` is NOT NULL and SAM never creates a user; award lookup is NSF first, then USAspending (`resolve_award`, `sam/integration/awards`) | E |
| Users absent or inactive, ARC placeholders | the sweep files `account_request` rows (live); no ticket for them | users come only from IGA and the LDAP sync; the ticket a blocked post opens is unbuilt | D |
| XRAS's own notify mail | on | the Action Approved rules on the NCAR process's notification templates, which we own (§ 3, C) | C (cutover step) |

## 3. Phases

**A. Renames.** Live in production 2026-10-14 (`XRAS_CLOSED_LOOP.md` § 3.3, § 3.5):
- After a New applies, the inbound route renames its request to the projcode within a second
  of the reply (`XRAS_RENAME_ENABLED`).
- The hourly `xras_rename` task is the backstop.
- The backfill renamed the five requests posted before go-live (NCAR4293, 4212, 4300, 4354,
  4432).

NUSD stops renaming by hand (Travis agreed 2026-10-14). A race between the two is harmless:
- SAM's pre-read skips a line already renamed;
- the admin app's "Assign new request number" refuses a number in use.

NUSD still activates in SAM and notifies from XRAS until B and C. The route's first rename in
production waits for the next New that applies: the one New posted since go-live, NCAR4391,
was refused (2026-10-14).

**B. Activation on post.** Ben, 2026-10-14: yes, when nothing blocks it. Once the New
applies cleanly, activate the project behind its own lever (`XRAS_AUTO_ACTIVATE`, default
off, dev on), in the handler or the after-reply hook beside the rename, recording
`xras_activation_event` with the API actor as `created_by`. This is the one deliberate
unattended exception to `can_edit_project_governance` (`implemented/XRAS_AUTO_NOTICES.md`,
"Phase 2 (later)"). SAM holds no record of when NUSD activates today: since the repoint 7
activations went through the card, while 39 of the last 40 applied News are active, so the rest
were activated elsewhere. B's `xras_activation_event` is the first reliable record. A New that applied under an `ignore_contract` override activates too: the
waiver is the operator's decision (Ben, 2026-10-14). NUSD checks nothing before activating
beyond what the handler checks (Travis, 2026-10-14), so a New that applies is ready to
activate.

**C. Notifications on post.** [`XRAS_NOTICES_CUTOVER.md`](XRAS_NOTICES_CUTOVER.md) holds the
checklist: which XRAS rules to delete, the SAMuel notice that replaces each, and the gaps to close
first. XRAS's award mail is configuration we own: the Action Approved rules on the NCAR
process's notification templates, sent at the operator's Notify step (read on `admin-ncar`,
2026-10-14; production and the demo match).
- **SAM sends the award notices.** The New's notice goes out right after activation (B),
  through `build_xras_messages` and the Notify button's dedup key, so nothing double-mails. The
  extension, supplement, renewal and adjustment notices keep the `xras_notices` delay as a
  correction window or move to post time (decide then).
- **The cutover deletes those rules** for exactly the action types SAM then sends, and the
  operator stops clicking Notify: a request leaves the admin queue at the post without it
  (`XRAS_DEV_PROBES.md` § 6.1, scenario 8). Approve and post are the whole job.
- **Before it:** template content at parity, a copy to `alloc@ucar.edu` (every XRAS award mail
  carries one; production has no addressing rows), and one rule deleted on the demo to prove
  it silences Notify.

**D. The failed post opens the ticket.** `XRAS_REQUEST_TICKETS.md` (designed, not built).
Ben, 2026-10-14, the #819 second-pass review: of the News approved since the 2026-08-24
repoint, 73 have applied, 3 stand refused and 3 are unposted (`XRAS_CLOSED_LOOP.md` § 3.3),
so a ticket per approval is churn; the ticket belongs to the post that fails.

- **Approve and post in one sitting.** A post that applies is done. The post stays
  all-or-nothing: a project is never created without its full roster.
- **A blocked post answers 422 and opens the ticket.** The New handler already collects every
  blocker into one list, so the 422 is the complete checklist and the ticket's body. NUSD works
  the list by hand and posts again; the post that applies adds a last comment, and NUSD
  closes the ticket. Travis, 2026-10-14: no auto-close at first, to see how the process feels.
- **Nothing outbound before the reply.** The open or comment runs in the after-reply
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
5. **One checklist line per person.** The handler answers several errors for one missing
   person: four for a placeholder who is PI and Allocation Manager (`XRAS_DEV_PROBES.md`
   § 6.1, scenario 6). The ticket groups them by username; the 422 to XRAS may stay verbatim.
6. **A merge closes the placeholder's `account_request` rows.** Fulfillment matches by email,
   so when the merge target is not the SAM account holding the placeholder's email, the
   sweep's row for it stays open in NUSD's queue for good (scenario 6, row #22 on dev).
   `merge_placeholder` closes the source's rows with the target as the reason.

**E. Contracts where resolvable.** For an award `resolve_award` finds (NSF, else USAspending)
whose PI resolves to a SAM user, create the contract at post time; otherwise it is a checklist
item on the request's ticket.

## 4. Order and dependencies

A is live. The order from here:
1. **D**, before any change to the New's 422, because the blocked post's wording names the
   ticket.
2. **B**, beside or right after D.
3. **C**, after B, since the New's notice follows activation. It needs nothing from XRAS's
   side beyond our own template rules, changed at its cutover.
4. **E** last, as the smallest.
5. **Then the outgoing work** of `XRAS_SUBMISSION.md`: its phases 0 and 1.

Each phase is proven on samuel-dev ↔ XRAS-dev first (`XRAS_CLOSED_LOOP.md` § 4 holds the test
bed and its rules).

## 5. Related plans

- `XRAS_CLOSED_LOOP.md`: the rename (phase A) and the test bed.
- `XRAS_NOTICES_CUTOVER.md`: the award-mail cutover (phase C).
- `XRAS_REQUEST_TICKETS.md`: the ticket a blocked post opens (phase D).
- `XRAS_SUBMISSION.md` § 4.2, § 4.6: the request record and the incoming side; the outgoing
  work that follows phase D.
- `implemented/XRAS_AUTO_NOTICES.md`: the notice task and its phase-2 activation stub.
- `XRAS_INGEST_IMPROVEMENTS.md` § 4: paper cuts (a Supplement after a Renewal lands on the
  renewal's window).
