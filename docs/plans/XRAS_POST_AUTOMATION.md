# XRAS: "Post to accounting service" does everything SAMuel can

**Status: roadmap, 2026-10-15.** Phase A (renames) is live in production (#819, #820). B is live
in production (built in #828, armed by #831; first case NCAR4407 → UMMM0018, 2026-10-15); D is built and on in samuel-dev, and armed for production in PR #845 with a
since-floor (D's item 2 deferred); C is planned, with D's production arming, in
[`XRAS_PHASE_C_AND_TICKETS.md`](XRAS_PHASE_C_AND_TICKETS.md) (2026-10-19); E is not started. This file sequences the work and points at the plans that hold the
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
| Activate the project | live: after the reply, behind `XRAS_AUTO_ACTIVATE` (on in both; `sam/manage/xras_activation.py`); NUSD no longer activates by hand | — | B |
| Notify, New | manual Notify button; sent from XRAS today | must follow activation; XRAS's own notify mail | C |
| Notify, extend/supplement/adjust/update | built: the `xras_notices` task (1-day delay), disabled in production | soak; XRAS's own notify mail | C |
| Contracts | 422 with a seeded create form (Admin → Contracts), or the `ignore_contract` override | `contract.principal_investigator_user_id` is NOT NULL and SAM never creates a user; award lookup is NSF first, then USAspending (`resolve_award`, `sam/integration/awards`) | E |
| Users absent or inactive, ARC placeholders | the sweep files `account_request` rows (live); the request ticket (`xras_request_tickets`): dev on dry-run, prod armed with a floor (#845) | users come only from IGA and the LDAP sync | D |
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

NUSD still notifies from XRAS until C. The route's first rename in production was NCAR4407 →
UMMM0018 on 2026-10-15 (`xras_remediation_event` #21, verified 1 s after the reply).

**B. Activation on post.** Ben, 2026-10-14: yes, when nothing blocks it. Once the New
applies cleanly, activate the project behind its own lever (`XRAS_AUTO_ACTIVATE`, default
off, dev on), in the handler or the after-reply hook beside the rename, recording
`xras_activation_event` with the API actor as `created_by`. This is the one deliberate
unattended exception to `can_edit_project_governance` (`implemented/XRAS_AUTO_NOTICES.md`,
"Phase 2 (later)"). SAM holds no record of when NUSD activates today: since the repoint 7
activations went through the card, while 39 of the last 40 applied News are active, so the rest
were activated elsewhere. B's `xras_activation_event` is the first reliable record. Live in production since 2026-10-15
19:01Z; the first case, UMMM0018 (action log #307), was activated by `xras:XRAS` in the second
of its post, and its rename followed. A New that applied under an `ignore_contract` override activates too: the
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

**D. A refused post becomes one ticket.** `XRAS_REQUEST_TICKETS.md` (built in #828; on in
samuel-dev through the `dry-run` provider; production waits on its § 9).
Ben, 2026-10-14, the #819 second-pass review: of the News approved since the 2026-08-24
repoint, 73 have applied, 3 stand refused and 3 are unposted (`XRAS_CLOSED_LOOP.md` § 3.3),
so a ticket per approval is churn; the ticket belongs to the request whose post is refused.

- **Approve and post in one sitting.** A post that applies is done. The post stays
  all-or-nothing: a project is never created without its full roster.
- **A blocked post answers 422 with what to fix.** The New handler already collects every
  blocker into one list, so the 422 is the complete checklist, and each blocker kind gets a
  line saying where it is fixed (contract form, mnemonic link or override, merge,
  reactivation).
- **A short grace period, then the ticket** (Ben, 2026-10-15). 7 of production's 22 refused
  News were fixed at the console within 10 minutes. So the ticket opens only when the request is
  still refused 10-15 minutes after its first refusal; after the cutover the approver posts and
  NUSD fixes. NUSD works the list and posts again; the post that applies earns a last comment,
  and NUSD closes the ticket. Travis, 2026-10-14: no auto-close at first, to see how the process
  feels.
- **A task does every Jira write.** The quarter-hourly `xras_request_tickets` task opens,
  comments and records success, and the webapp only answers posts. The grace period needs a
  clock, and no Jira call ever sits on the request XRAS is waiting on (its admin app times out
  on a slow reply). The CronJob gains `JIRA_WRITE_ENABLED` and `TICKET_PROVIDER` for this one
  writer, the way #820 gave it `XRAS_WRITE_ENABLED` for `xras_rename`. The task works behind
  its own switch, and the chart's "reads only" gates become a pin of that grant.

What the plan must hold (`XRAS_REQUEST_TICKETS.md` §§ 3, 4, 8):

1. **Repeats are idempotent.** NUSD re-posts on purpose, and an impatient operator re-posts
   by accident (action 403317 arrived three times during a database stall, every one a
   click). XRAS never retries a post on its own. The task reads each request's state, finds
   its ticket, and comments only when the blocker list changed (ledgered by digest), so a
   repeat writes nothing.
2. **An approval nobody posts.** No post means no ticket. The sweep, which sees every approval,
   puts "approved, not posted" past N days on the Remediations card and in the queue digest.
3. **Link, do not restate, the sweep's rows.** The sweep still files `account_request` rows for
   absent and inactive people at approval, NUSD's head start; the ticket links to them.
4. **The 422 says what happens next.** A first refusal says a ticket opens with NUSD if it is
   still refused in about 15 minutes; once the ticket exists, every 422 and SAM's card name its
   key.
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
1. **D**, before any other change to the New's 422, because D sets its wording: the ticket
   state and where each blocker is fixed.
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
- `XRAS_REQUEST_TICKETS.md`: the ticket for a request whose post is refused (phase D).
- `XRAS_SUBMISSION.md` § 4.2, § 4.6: the request record and the incoming side; the outgoing
  work that follows phase D.
- `implemented/XRAS_AUTO_NOTICES.md`: the notice task and its phase-2 activation stub.
- `XRAS_INGEST_IMPROVEMENTS.md` § 4: paper cuts (a Supplement after a Renewal lands on the
  renewal's window).
