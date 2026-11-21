# More time or resources: one request, two XRAS actions

**Status: built on branch `xras-more-time-or-resources` (2026-11-20); dev smoke after the
branch rolls to samuel-dev.** Supersedes Kyle's plan `XRAS_EXTENSION_BALANCE_CHECK.md` (branch
`xras-extension-balance-check`, 2026-11-11, never built): his flow shape is kept, and his
per-type zero-balance minimums give way to the recommender in § 3.

## 1. What changed for a lead

On My Requests a project row offered **Extend** and **Supplement**, two forms filing one XRAS
action each. Leads think "more time and/or more hours", and reviewers often answered an
Extension with "file a Supplement too". Now one **Extend or add** button opens one dialog:

- an optional new end date, optional extra resources (at least one), one reason;
- one review, one submit. SAM files the Extension, then the Supplement
  (`sam.manage.xras_submission.request_actions`), stopping at the first that does not succeed;
- the outcome lists each action. My Requests keeps one row per action with its own status,
  and notes the pair (`filed_with`, from the shared payload `bundle`);
- the Supplement's comment opens with "Requested together with an Extension to <date>." so its reviewer
  sees the pair.

XRAS stays at two actions: the inbound handlers apply an Extension's end date only and a
Supplement's `resources[]` only (`sam/xras/handlers/extension.py`, `supplement.py`).

Ben's decisions (2026-11-20): one button replaces both; amounts come from a recommender over
the date window; two linked rows in the status table; probe XRAS before building.

## 2. The probe: two pending actions on one line (2026-11-20)

Bed: samuel-dev on `sha-cb397b6` against xras-admin-demo, Ben's 2FA, Claude's clicks.

1. sam_dev had lost UHSS0002 to a refresh; Claude reposted its New (1439543) from the admin
   app, which recreated the project.
2. From My Requests with the per-type buttons: Extension 373280 (end 2028-01-31), then at
   once Supplement 373281 (Derecho +1,000). The routing's `pending` gate did not block the
   second: a submitted row is not open.
3. Both sat **Submitted** together on the Renewal line 1439769, the line holding the family's
   newest action (`primary_line`).
4. Approved and posted each, never notified. sam_dev allocation 26045 went from 1 to 1,001
   core-hours and from 2027-10-31 to 2028-01-31; both `xras_submission` rows read applied.

**Answer: XRAS takes both.** Each is its own review, post and apply, with no order between them.

Dev-only quirk: sam_dev lacked the Renewal's window, so SAM's floor was the New's end and XRAS
showed the Extension inside the Renewal's window. On a database that holds the Renewal, the
floor is that window's end.

## 3. The suggested amount

`sam/queries/supplement_advice.py`, compute resources the project holds:

- **Rate:** recent use over the trailing 90 days (`get_project_rolling_usage`, once the live
  allocation is 30 days old), else the last allocation spread over its length (an ended
  window, the usual Extension case).
- **Need:** rate × days from today to the end date (the new one, or the current end for more
  resources alone), less what remains (`usage_by_resource` at the floor once the window has
  ended: an Extension revives what was left).
- **Suggestion:** the need rounded up to two significant figures, capped at the lower of the
  type's limit and the last allocation (Ben). None when the balance covers the window.
- The lead may change or remove it, or ask for more up to the type's limit (`_check_limits`).
  Changing the date re-suggests only the amounts the lead left alone (`suggested_<id>`).

## 4. Follow-ups, not built

| Item | Note |
|---|---|
| Remediations card with two actions in flight | names only the newer (`in_flight_action`); the request modal edits only the newest. Approval is in XRAS's admin app, so nothing breaks |
| Two approval mails per pair | `xras_extension` and `xras_supplement` each send one |
| `xras_activation` mail still says "through ARC" | pointing it at My Requests needs a SAM URL in its context and the six render snapshots regenerated |
| A Renewal's future window | both handlers land on `latest_allocation` (`XRAS_SUBMISSION.md` § 4.1) |
| `ui_snapshots` recipe for the dialog | the dialog needs an armed XRAS line, which a local server lacks |
