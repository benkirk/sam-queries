# The ARC baseline — one real round trip, observed from every surface

**Status: run 2026-09-15 on production ARC, XRAS and SAM; project `UHSS0001`
exists as a result.** This is W9 of [`XRAS_SUBMISSION_PROBES.md`](XRAS_SUBMISSION_PROBES.md):
the *existing* ARC → XRAS → SAM workflow driven once, for real, on a throwaway
Exploratory request under `benkirk`, with the ARC form, the XRAS admin app, the
report API, SAM's own XRAS page and the mail read at every step. It answers three
questions the design ([`XRAS_SUBMISSION.md`](../../plans/XRAS_SUBMISSION.md) § 3.2,
§ 4.2) had left open, and turns up one the design had not asked.

| Question | Answer |
|---|---|
| Which admin step rewrites `requestNumber` to the projcode? | **Neither the post nor the notify.** The family still answers only to `NCAR4354` at T+17 min after the post and T+11 min after the notify (§ 3). The rewrite is a later, asynchronous step — and until it lands, every action filed on the family parks in SAM (§ 3.1) |
| What does ARC show for an approved request? | `Approved` under the `NCAR####`, the moment the operator approves — before any post. The Actions menu offers **View, Extension, Supplement, Transfer** at that point and adds **Start a Renewal** after the notify (§ 2) |
| What is the ARC form? | Six pages, native-HTML5 required fields, no dates, no grants, no bound on amounts, and a date picker that turns an ISO date into year 31 (§ 1) |

Driver: Claude through Playwright, signed in as `benkirk` on all three sites; Ben
approved each irreversible click (approve, post, notify, SAM activate, SAM notify).
Ids: XRAS request `1449367` / `NCAR4354`, ARC request 22795, actions `399364`
(New), `399373` (Extension, declined), `399374` (Extension); SAM `xras_action_log`
#183 (New) and #184 (Extension); project `UHSS0001`.

---

## 1. The ARC form (New, Exploratory Allocation (University))

Entry: *Allocations → Opportunities → Start a New Request* lists the seven open
opportunities. The XRAS request row is created when page 1 is saved (the admin
app's entry timestamp is the page-1 save, three minutes before the submit).

| # | Page (URL slug) | Fields | Required | Notes |
|---|---|---|---|---|
| 1 | Request Information (`request_information`) | Request Title, Public Abstract, Keywords (optional) | title, abstract | help text on each; an empty submit only moves focus (native `required`, no message) |
| 2 | Fields of Science (`fields_of_science`) | one dropdown (39 entries), a table with a Primary checkbox and Remove per row | at least one, one primary | *Save & Continue* is disabled until a row exists; the first pick is primary |
| 3 | Related Personnel (`related_personnel`) | Project Lead picker, Project Admin picker (disabled — pre-filled with the submitter), User picker, **Create User** button, Collaborators text | a Project Lead | the submitter lands as **Project Admin** (Allocation Manager 14) and must add a lead, self included — the API's create-then-`roles/PI` shape, verbatim. Pickers are select2 with a 2-character minimum |
| 4 | Additional Questions (`data_analysis`) | the NWSC End User Agreement checkbox | the checkbox | native `required`; the slug is wrong on every opportunity, and the read-only view labels the section "Data Analysis" |
| 5 | Available Resources (`available_resources`) | four rows (Casper GPU, Derecho-GPU, Derecho, Casper): amount + comment | **none** | the page submits with every amount blank; nothing states the published limits (§ 3.5 of the probes doc) |
| 6 | Documents (`documents`) | Advisor letter file picker, *Additional documents*, **Submit Request** | the Advisor letter | "Documents are not uploaded until request is submitted"; Submit is disabled until the file is chosen |

What ARC never asks that the API path had to supply: **allocation dates** (the
report shows `allocationDates: []`; the admin app's "Approve the requested dates"
radio is disabled and the operator picks "Begin the award today" or "Other") and
**grants** (`isSupportedByGrants: null`, no page). What ARC forces that the API
did not: nothing — the EUA row and the Advisor letter are the same two rules
`validate` names. The submitted payload matches W8's API-built one field for field,
including the EUA `attributeValue: null`.

**Extension** (*Actions → Extension*): one page, Requested End Date + Comments +
Submit. The date box is a picker with **mm/dd/yy parsing**: typing `2027-12-31`
stored `0031-11-12`, XRAS accepted it, and the admin app offered to "approve the
requested end date" of year 31. Typing `12/31/2027` works. The first submit also
ended on a 504 from ARC after the action had been created. The view page for a
request is the six form sections read-only, with no status, number or award.

## 2. The round trip, step by step (times UTC)

| Step | Where | What each surface showed |
|---|---|---|
| Submit (15:05:06) | ARC *Submit Request* | ARC: "Your request has been submitted", listed as `NCAR4354 · Submitted`. API: `requestId 1449367`, `Submitted`, roster PI + Allocation Manager both `benkirk`, `Requested` resource line Derecho 1.0, document 319735, `allocationDates: []`. Admin app: on the dashboard within a minute; Summary shows Hold Off / Return for Corrections. Mail: the staff notice to `alloc@` twice (To and Cc), the submitter confirmation to the XRAS person's address (a gmail, not `benkirk@ucar.edu`). SAM: nothing |
| Approve (15:08:40) | admin app *Process* → dates "Other" 2026-10-01 → 2027-09-30, Approved amount 1, admin comment, **Save & Approve** | a warning modal "Not all awarded amounts have recommended amounts" (Continue) is the only reviewer step. API: `Approved`, an `Approved` date line and resource line beside `Requested`, `adminComments` echoed. ARC: `Approved`, Actions = View / Extension / Supplement / Transfer. Process tab: row `Approved · 2026-09-15 · Posted to: None · Notifications: No`, button **Post to Accounting Service**. `requestNumber` unchanged |
| Post (15:15:51) | **Post to Accounting Service** | admin app: "The Action Post was Successful", `Posted to: Accounting Service`, buttons **Repost** and **Notify**; **the request leaves the dashboard here**. SAM: `xras_action_log` #183 `New NCAR4354 → Processed 200 → UHSS0001`; Work Queue row `UHSS0001 · New · Needs activation · Not notified`. API at +5 s, +30 s, +3 min: `NCAR4354` still the only key, `UHSS0001` answers `[]`. Mail: none |
| Notify (15:21:05) | **Notify** | admin app: "The Notifications For This Action Have Been Generated", `Notifications Generated? Yes`. Mail at 15:22:06: "Kirk NCAR Exploratory Allocation (University) Computing Request Approved", To the gmail, Cc `alloc@`, body **`Project Number: NCAR4354 New`** — the award letter carries the NCAR number. ARC: Actions gains **Start a Renewal**. API: unchanged |
| SAM activate (15:26:57) | SAM Work Queue → **Activate this project** → confirm | row drops "Needs activation"; `xras_activation_event` history gains the activation |
| SAM notify (15:27:21) | SAM Work Queue → **Notify** → preview → **Send to 1** | preview: one recipient (`benkirk@ucar.edu`, lead), subject "NSF NCAR Project UHSS0001 is now active", Derecho 1 through 2027-09-30, no approver's note (the admin comment is on the action's `adminComments`, which the route reads from the XRAS reports feed — fail-open, none rendered). Mail from `sam-admin@ucar.edu` at once; row `Notified` |
| Extension submit (15:28:50 and 15:31:10) | ARC *Actions → Extension* | first attempt stored year 31 (§ 1) and was rejected in the admin app (`Declined`, `adminComments` set); second stored 2027-12-31. Staff notice "Extension … submitted by Kirk (NCAR4354)" to `alloc@`. ARC's list still shows one `Approved` row — a pending extension is not surfaced. Admin app: `Extension - NCAR4354 · Submitted`, dates radio "Approve the requested end date" now enabled |
| Extension approve (15:32:20) | **Save & Approve** | no warning modal (no amounts). API: `399374 Extension Approved`, `Approved` date line `→ 2027-12-31`. Process tab: **Post to Accounting Service** |
| Extension post (15:33:04) | **Post to Accounting Service** | admin app: "Post was Successful", `Posted to: Accounting Service`. SAM: `xras_action_log` #184 `Extension NCAR4354 → Manual 200`, wire payload `requestNumber: "NCAR4354"`, `actionEndDate: "2027-12-31"`, `resources: []`. **Parked**: `select_service` finds no project `NCAR4354`, so no service runs (§ 3.1). `UHSS0001` still answers `[]` |

## 3. Findings

**The rewrite is neither step.** Both `reports/request_numbers` keys were read 5 s,
30 s and 3 min after the post and 25 s and 1½ min after the notify, then every 5
min: `NCAR4354` resolves, `UHSS0001` does not. The award letter XRAS mails at the
notify names `NCAR4354`. Whatever rewrites the number runs later and on its own
clock — consistent with the hours-to-a-day lags in the design's § 2 table.

### 3.1 The gap has a cost: actions filed before the rewrite park

An Extension approved and posted 17 minutes after the New arrived with
`requestNumber: NCAR4354`. SAM's `select_service` (`sam/xras/dispatch.py`)
resolves that against `project.projcode`, finds nothing, and every service except
`add` needs a project — so the row is `manual`, HTTP 200, and the operator has to
notice. SAM already holds the mapping: action #183's payload carries
`requestId: 1449367` and its `projcode_result` is `UHSS0001`. A fallback in
`select_service` — no project under `requestNumber`, but a `processed` `New` row
with the same `requestId` — would route it. The design's `xras_submission` row
(keyed on `request_id`) is the same fallback for SAM-authored requests.

**The dashboard clears at the post.** The request left the admin app's *Recent
submissions* the moment the post succeeded, before the notify. The rows that stay
there for days (the four extensions from 2026-09-14) are approved and **not yet
posted** — they are in SAM's Remediations card and absent from the action log.
"Disappears on a successful post" is already how it works.

**Extend comes from the approval.** ARC offers Extension, Supplement and Transfer
as soon as the request is `Approved`, before the post; Renewal appears after the
notify. Nothing ARC shows depends on SAM.

**Mail.** Submit: staff notice to `alloc@` (twice, To + Cc), submitter
confirmation to the XRAS person's email. Notify: the award letter to the person's
email, Cc `alloc@`. SAM's activation notice goes to the SAM address (`benkirk@ucar.edu`).
A PI whose XRAS email differs from their SAM email gets the two halves in two inboxes.

**The mnemonic resolved on the organization branch.** `benkirk` has no
`user_institution` row, so `resolve_mnemonic_code` fell through the institution
branch to the organization (High-End Services Section → `HSS`), minting
`UHSS0001`. The local snapshot predicted it exactly. A `UHSS0001` "is now active"
mail from 2026-08-10 is the pre-deploy smoke test on a non-production database,
not a collision; production's `project_code` counter had never issued the code.

**Two admin-app quirks.** The Approved-amount box throws its own console error on
change (`exchange_rate` of undefined), harmless. Save & Approve on a New without
recommended amounts needs the warning modal's Continue.

## 4. Final state

| Object | State |
|---|---|
| XRAS `1449367` / `NCAR4354` | `Approved`; `399364 New/Approved` posted + notified; `399373 Extension/Declined` (year-31 date); `399374 Extension/Approved`, posted, **not notified**, awaiting the repost after the rewrite |
| SAM `UHSS0001` | real project, active, lead `benkirk`, Derecho 1 core-hour 2026-10-01 → 2027-09-30, SAM activation notice sent; `xras_action_log` #183 `processed`, #184 `manual` |
| ARC 22795 | `Approved`, Actions = View / Extension / Supplement / Transfer / Start a Renewal |
| `benkirk` in XRAS | PI + Allocation Manager on `NCAR4354`, plus the W1/W8 rejected families |
