# XRAS award mail: SAMuel takes over from XRAS (post automation phase C)

**Status: checklist, not started. Read from production 2026-10-14.** Phase C of
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
| `alloc@ucar.edu` (cc on every award mail) | nothing: production has no `notification_addressing` rows | add a cc or bcc row on scope `xras` (Admin → Configuration → Notification Addressing) |
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
2. Prove on the demo that a deleted rule silences Notify: delete one rule (Exploratory · New ·
   Action Approved · PI), approve, post and Notify a benkirk-only request, and read its
   Notifications view.
3. Record production's rules (the tables above are the record; re-adding them is the rollback).
4. One day: enable `xras_notices` in production (and phase B's activation notice), then delete
   the rules of § 2 in `admin-ncar`.
5. NUSD stops clicking Notify: a request leaves the admin queue at the post without it
   (`XRAS_DEV_PROBES.md` § 6.1, scenario 8).
6. Watch the first week: every applied action has a SAMuel `notification_log` row and no XRAS
   Action Approved row.
