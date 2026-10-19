# XRAS award mail: SAMuel takes over from XRAS (post automation phase C)

**Status: checklist, read from production 2026-10-14; carried out by
[`XRAS_PHASE_C_AND_TICKETS.md`](XRAS_PHASE_C_AND_TICKETS.md) (planned 2026-10-19).** Phase C of
[`XRAS_POST_AUTOMATION.md`](XRAS_POST_AUTOMATION.md). The goal: XRAS stops mailing award
notices and SAMuel sends them, with no PI missing a notice and none receiving two. Every item
below is either an XRAS rule to delete, paired with the SAMuel notice that replaces it, or a
gap to close before that rule is deleted.

## 1. How XRAS sends today

- **Configuration we own.** Admin → Notifications (`/notification_templates`) on
  `admin-ncar.xras.org`, where Ben has the admin role. A template has no on/off switch; it sends
  for each row of its Rules table (allocation type × action type × event × recipient).
  Production and the demo instance carry the same 15 templates, ids and rules.
- **The award mail is the Notify step.** The Action Approved event fires when the operator
  clicks Notify after the post. One mail goes to the PI, and the template's cc list rides in the
  same message.
- **Its record.** Each request's Process tab has a Notifications view
  (`/requests/<rid>/show_process?show=notifications`) listing every mail sent (event, contact,
  status, date). `/actions/<aid>.json` carries the same as `action_notifications`, with
  `notifications_generated`, plus a "Regenerate and Send Notifications" control.
- **What it did since the repoint.** Of the 250 actions SAMuel applied, 233 had an award mail;
  19 have none, 11 of them posted in the last week. Extension and Supplement are notified the
  same day (84 of 106, 37 of 44; at most 4 and 6 days). A New waits for activation: median 1
  day, up to 22.

## 2. The checklist

**Delete in XRAS at cutover** (the Action Approved rules, per action type):

| XRAS template | Action types × allocation types | SAMuel replacement | Before deleting |
|---|---|---|---|
| 154 | New, Renewal × CISL Operations, Data Analysis, Educational, Exploratory, NCAR Strategic Computing, Small | `xras_activation` (New, after B's activation); `xras_update` (Renewal) | content parity (§ 4); recipients (§ 3) |
| 133 | New, Renewal × Large | the same two | the two NSF copies (§ 3) |
| 1803 | New × NCAR External Projects | `xras_activation` | — |
| 417 | New, Renewal × NCAR Strategic Computing | the same two | none: also matched by 154, so a Strategic award mails twice today; deleting both ends it |
| 135, Extension rows only | Extension × Data Analysis, Educational, Exploratory, Large, External Projects, Strategic Computing, Small | `xras_extension` | timing (§ 5) |
| 149, Supplement rows only | Supplement × Data Analysis, Educational, Exploratory, Large, Strategic Computing, Small | `xras_supplement` | timing (§ 5) |

**Keep in XRAS** (SAMuel has no counterpart, or should not have one):

| XRAS template | Why it stays |
|---|---|
| 128, 129, 2562 (Action Finalized/Submitted) | the submit acknowledgment to the PI and to `alloc@`; SAMuel does not see a submit |
| 131, 134, 143 (Action Rejected) | a decline happens in XRAS and never posts |
| 132 (Reviews Completed), 140 (COI Reported) | process mail to the Allocations Process Manager |
| 135's Appeal and Final Report rows, 149's Transfer rows, 142 (Advance, Large) | SAMuel has no notice kind for them; Transfer parks `manual` by design. None posted since the repoint |

**Decide before cutover:**

- **Adjustment.** SAMuel has `xras_adjustment`; XRAS sends nothing for an Adjustment. Enabling
  it adds a mail PIs never received.
- **The gaps XRAS has today:** Supplement and Transfer on External Projects and CISL
  Operations, Extension on CISL Operations, Renewal on External Projects. SAMuel's kinds cover
  them by action type, so after cutover those PIs start receiving a notice. None posted since
  the repoint.

## 3. Recipients

| XRAS sends to | SAMuel today | Item |
|---|---|---|
| PI (To) | the project lead (PI → lead in `sam/xras/roster.py`) | covered |
| Allocation Managers (cc) | the project admin (Allocation Manager → admin), mailed directly | covered |
| Co-PIs (cc) | — | none: NCAR's wire carries only PI, Allocation Manager and User roles, so the list is empty |
| `alloc@ucar.edu` (cc on every award mail) | `NOTIFY_XRAS_CC` (since #480), once per letter since #848 | covered |
| program officers (cc) | — | confirm the tag is empty at NCAR (no program officer on the wire); otherwise decide |
| `bgrant@nsf.gov`, `mwomack@nsf.gov` (cc, Large New and Renewal only) | — | addressing scopes are family, kind and facility, not allocation type: add an allocation-type scope, or drop the copy (Large News are rare; none since the repoint) |
| PI email again (417, Strategic Computing) | — | none: a duplicate of the To |

## 4. Content parity

XRAS's award mails carry, beside the greeting: the request number and project end date, the PI
and organization, the title, the awarded resources, the NSF award number and award PI, the
reviewer's note (`ADMINCOMMENTS`) and the opportunity's own note (`OPPORTUNITYADMINCOMMENTS`,
for example "please only save processed data to campaign store"). The New mail (154) adds
getting-started and user-responsibility links, the requirement to acknowledge NCAR and CISL
with its consequence ("subsequent allocations may be delayed"), a paragraph on new user
accounts, and the service-desk footer.

SAMuel's `xras_activation` carries the resources with end dates, the reviewer's note, one
documentation link, the SAMuel link and the lead/admin roles. To add before cutover, through the
template editor (`implemented/XRAS_AUTO_NOTICES.md`): the acknowledgment requirement, the
user-responsibility and End User Agreement links, the new-accounts paragraph, the NSF award, and
the opportunity's note. The extension and supplement templates get the same pass against 135
and 149.

## 5. Timing

XRAS mails when the operator clicks Notify: same day for most Extensions and Supplements, after
activation for a New. SAMuel's `xras_notices` task sends Extension, Supplement, Renewal and
Adjustment notices a day after the post (disabled in production); the New's notice follows
phase B's activation. Decide whether the task's day is kept as a correction window or the
notices move to post time; either is within the timing XRAS has today.

## 6. Order

1. SAMuel ready on samuel-dev: templates at parity (§ 4), the `alloc@` row (§ 3), the
   decisions of § 2 and § 5.
2. Prove on the demo that a deleted rule silences Notify. **Done 2026-10-19**
   (§ 8; `XRAS_DEV_PROBES.md` § 6.4): with Exploratory · New · Action Approved · PI deleted, an
   approved and posted New (NCAR4094 → UHSS0006) left Recent submissions and showed no Notify
   button ("Notifications Generated? N/A"). Existing posted News lost the button at once, and
   re-adding the rule brought it back.
3. Record production's rules (the tables above are the record; re-adding them is the rollback).
4. One day: enable `xras_notices` in production (and phase B's activation notice), then delete
   the rules of § 2 in `admin-ncar`.
5. NUSD stops clicking Notify: a request leaves the admin queue at the post without it
   (`XRAS_DEV_PROBES.md` § 6.1, scenario 8).
6. Watch the first week: every applied action has a SAMuel `notification_log` row and no XRAS
   Action Approved row.

## 7. Production rules, recorded 2026-10-19 (the rollback)

Read from `admin-ncar.xras.org` (Admin -> Notifications, each template's edit page) on
2026-10-19: 15 templates, 206 rules. The cutover deletes the 29 rows marked **delete**;
re-adding them is the rollback. Every rule here is a row of allocation type x action x
event x recipient; a cell lists the allocation types of one action, event and recipient.

| Template | Event -> recipient | Action | Allocation types | Cutover |
|---|---|---|---|---|
| 154 | Action Approved -> PI | New | CISL Operations, Data Analysis, Educational, Exploratory, NCAR Strategic Computing, Small | **delete** |
| 154 | Action Approved -> PI | Renewal | CISL Operations, Data Analysis, Educational, Exploratory, Small | **delete** |
| 133 | Action Approved -> PI | New | Large | **delete** |
| 133 | Action Approved -> PI | Renewal | Large | **delete** |
| 1803 | Action Approved -> PI | New | NCAR External Projects | **delete** |
| 417 | Action Approved -> PI | New | NCAR Strategic Computing | **delete** |
| 417 | Action Approved -> PI | Renewal | NCAR Strategic Computing | **delete** |
| 135 | Action Approved -> PI | Appeal | Educational, Large, Small | keep |
| 135 | Action Approved -> PI | Extension | Data Analysis, Educational, Exploratory, Large, NCAR External Projects, NCAR Strategic Computing, Small | **delete** |
| 135 | Action Approved -> PI | Final Report | Educational, Large, Small | keep |
| 149 | Action Approved -> PI | Supplement | Data Analysis, Educational, Exploratory, Large, NCAR Strategic Computing, Small | **delete** |
| 149 | Action Approved -> PI | Transfer | Data Analysis, Educational, Exploratory, Large, NCAR Strategic Computing, Small | keep |
| 142 | Action Approved -> PI | Advance | Large | keep |
| 128 | Action Finalized/Submitted -> PI | Advance | Data Analysis, Educational, Exploratory, Large, NCAR Strategic Computing, Small | keep |
| 128 | Action Finalized/Submitted -> PI | Appeal | Educational, Large, NCAR Strategic Computing, Small | keep |
| 128 | Action Finalized/Submitted -> PI | Extension | Data Analysis, Educational, Exploratory, Large, NCAR External Projects, NCAR Strategic Computing, Small | keep |
| 128 | Action Finalized/Submitted -> PI | Final Report | Educational, Exploratory, Large, Small | keep |
| 128 | Action Finalized/Submitted -> PI | New | Data Analysis, Educational, Exploratory, NCAR External Projects, Small | keep |
| 128 | Action Finalized/Submitted -> PI | Renewal | Data Analysis, Educational, Exploratory, NCAR External Projects, Small | keep |
| 128 | Action Finalized/Submitted -> PI | Supplement | Educational, Exploratory, Large, NCAR External Projects, NCAR Strategic Computing, Small | keep |
| 128 | Action Finalized/Submitted -> PI | Transfer | Educational, Exploratory, Large, NCAR External Projects, Small | keep |
| 2562 | Action Finalized/Submitted -> PI | New | Large, NCAR Strategic Computing | keep |
| 2562 | Action Finalized/Submitted -> PI | Renewal | Large, NCAR Strategic Computing | keep |
| 129 | Action Finalized/Submitted -> Allocations Process Manager | Advance | Exploratory, NCAR Strategic Computing | keep |
| 129 | Action Finalized/Submitted -> Allocations Process Manager | Appeal | Educational, Large, Small | keep |
| 129 | Action Finalized/Submitted -> Allocations Process Manager | Extension | Data Analysis, Educational, Exploratory, Large, NCAR External Projects, NCAR Strategic Computing, Small | keep |
| 129 | Action Finalized/Submitted -> Allocations Process Manager | Final Report | Educational, Large, Small | keep |
| 129 | Action Finalized/Submitted -> Allocations Process Manager | New | Data Analysis, Educational, Exploratory, Large, NCAR External Projects, NCAR Strategic Computing, Small | keep |
| 129 | Action Finalized/Submitted -> Allocations Process Manager | Renewal | Data Analysis, Educational, Exploratory, Large, NCAR External Projects, NCAR Strategic Computing, Small | keep |
| 129 | Action Finalized/Submitted -> Allocations Process Manager | Supplement | Data Analysis, Educational, Exploratory, Large, NCAR External Projects, NCAR Strategic Computing, Small | keep |
| 129 | Action Finalized/Submitted -> Allocations Process Manager | Transfer | Educational, Exploratory, Large, NCAR External Projects, Small | keep |
| 131 | Action Rejected -> PI | Appeal | Large | keep |
| 131 | Action Rejected -> PI | Extension | Data Analysis, Educational, Exploratory, Large, NCAR External Projects, NCAR Strategic Computing, Small | keep |
| 131 | Action Rejected -> PI | Final Report | Large | keep |
| 131 | Action Rejected -> PI | New | CISL Operations, Data Analysis, Educational, Exploratory, NCAR External Projects, Small | keep |
| 131 | Action Rejected -> PI | Renewal | CISL Operations, Educational, Small | keep |
| 131 | Action Rejected -> PI | Supplement | Data Analysis, Educational, Exploratory, Large, NCAR External Projects, NCAR Strategic Computing, Small | keep |
| 131 | Action Rejected -> PI | Transfer | Large | keep |
| 134 | Action Rejected -> PI | New | Large, NCAR Strategic Computing | keep |
| 134 | Action Rejected -> PI | Renewal | Large, NCAR Strategic Computing | keep |
| 143 | Action Rejected -> PI | Advance | Large | keep |
| 132 | Reviews Completed -> Allocations Process Manager | Appeal | Large | keep |
| 132 | Reviews Completed -> Allocations Process Manager | Extension | Educational, Large, Small | keep |
| 132 | Reviews Completed -> Allocations Process Manager | Final Report | Educational, Large, Small | keep |
| 132 | Reviews Completed -> Allocations Process Manager | New | Educational, Large, NCAR Strategic Computing, Small | keep |
| 132 | Reviews Completed -> Allocations Process Manager | Renewal | Educational, Large, NCAR Strategic Computing, Small | keep |
| 132 | Reviews Completed -> Allocations Process Manager | Supplement | Educational, Large, Small | keep |
| 132 | Reviews Completed -> Allocations Process Manager | Transfer | Educational, Large, Small | keep |
| 140 | COI Reported -> Allocations Process Manager | Advance | CISL Operations, Educational, Large, Small, TRAC | keep |
| 140 | COI Reported -> Allocations Process Manager | Appeal | CISL Operations, Educational, Large, Small | keep |
| 140 | COI Reported -> Allocations Process Manager | New | CISL Operations, Educational, Large, Small | keep |
| 140 | COI Reported -> Allocations Process Manager | Renewal | CISL Operations, Educational, Large, Small | keep |
| 140 | COI Reported -> Allocations Process Manager | Supplement | CISL Operations, Educational, Large, Small | keep |
| 140 | COI Reported -> Allocations Process Manager | Transfer | CISL Operations, Educational, Large, Small | keep |

## 8. The demo proof: a deleted rule removes the Notify step (2026-10-19)

The cutover question (§ 6, step 2): once SAMuel sends the award mail
and XRAS's Action Approved rules are gone, does an approve and post leave nothing behind? On
`xras-admin-demo` (Ben's 2FA, Playwright, the demo host asserted before every write), one
rule deleted from template 154: **Exploratory · New · Action Approved · PI**; the other ten
kept. No Notify was pressed. The field to read is `view_notify_button` on
`/actions/<aid>.json` (production's NCAR4412, posted the same day: `true`, with
`notifications_generated: false`).

| Step | Observed |
|---|---|
| Baseline, rule present | every posted, never-notified benkirk action shows Notify: News 372884, 372891, 372917; Extensions 372866, 372886; Supplement 372890 (`view_notify_button: true`) |
| Rule deleted (Save) | **the three Exploratory News lose the button at once** (`view_notify_button` null; Finalize and Post reads "Notifications Generated? N/A", only Save and Repost); the Extensions and the Supplement, whose rules on 135 and 149 stand, keep it. XRAS computes the button from the rules **at read time**, not at the post |
| NCAR4094 (1439637 / 372951) | a fresh benkirk-only Exploratory New, PI and Allocation Manager, authored through the demo API from inside the samuel-dev pod (§ 4's recipe; validate successful); submitted 16:33 MDT, listed in Recent submissions, Exploratory "3 pending" |
| Ben approves and posts | sam_dev **#29** `add` processed 200 → **UHSS0006**; activated 0.16 s later by `xras:XRAS` (`xras_activation_event` #5); renamed (`xras_remediation_event` #9, verified) |
| After the post | **gone from Recent submissions**, Exploratory "2 pending"; the action reads "Notifications Generated? N/A", **no Notify button**; its notifications are the two submit acknowledgments only |
| Rule re-added (Add Rules, Save) | template 154 back to its 11 rules; UHSS0006's Notify button **returns** ("No"). Re-adding the rules is a complete rollback |

A post already removes a request from Recent submissions with the rules in place (scenario 8;
production NCAR4412). Approved-but-unposted requests stay. What the rules decide is whether a
Notify step waits on the action. The production deletion also clears that step from every
posted, never-notified action at once.
