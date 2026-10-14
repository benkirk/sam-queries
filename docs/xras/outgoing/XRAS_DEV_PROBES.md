# XRAS-dev probes: the demo instance mapped, and the first handoffs to samuel-dev

**Status: run 2026-10-13 against `xras-submit-api-demo.xsede.org` (process `NCAR`) and
`xras-admin-demo.xsede.org`, with samuel-dev on `sha-f5237f1`.** This is the first time
anything was pushed from XRAS-dev to samuel-dev. Claude made every API call; Ben made every
approve and post click in the admin app. It answers the "no clean test loop" gap of
[`XRAS_SUBMISSION.md`](../../plans/XRAS_SUBMISSION.md) § 5 and the experiments of
[`XRAS_REQUEST_TICKETS.md`](../../plans/XRAS_REQUEST_TICKETS.md) § 9, except those that
need a SAM-authored submit (§ 7).

Same method as [`XRAS_WRITE_PROBES.md`](XRAS_WRITE_PROBES.md) § 1: one attempt per write,
then a re-read. Status codes, ids and field names are recorded; response bodies are not.
Header set: `XA-API-KEY` (the production key; the demo accepts it), `XA-ALLOCATIONS-PROCESS:
NCAR`, `XA-CONTEXT`, `XA-USER: benkirk` unless stated.

---

## 1. What XRAS-dev is

| | |
|---|---|
| Data | a fork of production taken about **2026-03-24**. The newest shared request is NCAR4085 (submitted 2026-03-24); everything from 2026-04-14 on is demo-native (staff tests, these probes) |
| Numbering | requestIds and request numbers continue independently on each side. Demo `NCAR4086` (kyledavis, "TEST 8.17.26") is not production's NCAR4086, and demo requestIds (1439xxx) sit below production's (1449xxx). **A number seen on demo names a different request from the same number in production** |
| Open opportunities | 5: Classroom 532223, NCAR External Projects 533276, Data Analysis 532222, Small 532220, Exploratory 532221; EUA `opportunityAttributeId` 539640, 539641, 539639, 539637, 539638 respectively (Data Analysis also 537062, a textarea) |
| Contexts | `submit` and `report` as in production; **`review` and `admin` 401 on every endpoint**, although `GET /v1/permissions/benkirk` lists `administrator` and `review-impersonator`. The ceiling is the key's, not the person's |
| Inbound | demo XRAS reaches samuel-dev with the production credential. `GET /v1/projects` is proxied to `GET /api/xras/v1/users/projects/<u>` and left sam_dev `xras_action_log` #1 and #2 (`unmapped`, 404), the first rows that table ever held |

## 2. The read map

Every GET in the API index, under all four contexts. `review` and `admin` are 401 throughout,
so only the two that answer are shown.

| Endpoint family | `submit` | `report` | Notes |
|---|---|---|---|
| `announcements`, `funding_agencies`, `opportunities` (all, list, by id), `panels`, `resources`, `types/*`, rule book `required_fields` | 200 | 200 | `types/grants` is empty, as in production. NSF is `fundingAgencyId` 1 |
| `people/<u>`, `search/people`, `permissions/<u>` | 200 | 200 | not proxied to SAM (no action-log row) |
| `orcid/check_status/<u>` | 200 | 401 | deprecated |
| `projects` | 404 | 404 | SAM's own 404 envelope, via the proxy (§ 1) |
| `reports/*` (requests, by id, request_numbers, allocations, by fos, by opportunity, username) | 401 | 200 | `reports/allocations` returns 9,078 rows |
| `requests` (list) | 200 | 200 | the XA-USER's own requests, **each with `rules{}`** |
| `requests/<rid>` | 200 / 401 | 200 / 401 | **200 with `rules{}` when XA-USER holds a role on the line**, 401 otherwise. Production recorded 401 in every case (`XRAS_WRITE_PROBES.md` P1) |
| `requests/<rid>/reviews` | 200 | 200 | own line |
| `…/required_documents_status` | 200 | 200 | own line; 401 on someone else's |
| `…/validate` | 200 | 200 | 500 from about 15:00Z to about 20:30Z: "The :mode option must be one of [:all, :n_plus_one_only] but nil was provided." ActiveRecord's `strict_loading!` on objects cached by another Rails version; demo runs two deployments each of the rules and submit APIs over one cache. Steve redeployed and cleared the cache (rails/rails#48617). Submit failed the same way |
| `…/final_review` | **500** | **500** | "undefined method 'final_review' for an instance of Action": broken since final reviews became 1:n in 2018 and unused; they are in `GET /v1/requests`. Steve may remove the route |
| `roles/<number>/Users` | 401 | 401 | on NRAL0051, where benkirk holds no role |

## 3. The write ladder

On draft **1439543** (Exploratory, action 372856), created for this. Each row is a 200
confirmed by a re-read of `GET /v1/requests/1439543`, unless it says otherwise.

| Verb | Result |
|---|---|
| `POST /v1/requests?opportunityId&requestType=New` | `Incomplete`, no number, `rules{}`, first action minted, creator = Allocation Manager, as in production |
| `POST …/roles/PI/benkirk`; `POST …/roles/User/hartter`; `PUT …/roles/<id>?endDate`; `DELETE …/roles/<id>` | all stick. **A second `roles/PI/benkirk` is accepted** and creates a duplicate PI row (deleted again) |
| `PUT …/attributes`; `PUT …/fos/<id>?isPrimary` (add a second, delete it) | stick |
| `PUT …/actions/<aid>/resources/<id>?amount` (add, add a second, delete it) | stick; stage `Requested` |
| `POST …/allocation_dates`; `PUT …/allocation_dates/<id>`; `DELETE …` | stick. **A second `POST` for the same stage is a raw 500** (`PG::UniqueViolation`, `uc_action_all…`): one Requested date per action, and `POST` is not an upsert |
| `PUT …/opportunity_attributes` (JSON, EUA, `attributeValue: null`) | sticks. The single-attribute `PUT …/opportunity_attributes/<id>` with no value is **400** "attributeValue not specified", so the bulk form is the way to write the EUA row. Single `DELETE` works |
| `PUT …/resource_attributes` (JSON, empty list) | 200 |
| `POST …/documents` (JSON, base64 of a `cupsfilter` PDF); `GET …/documents/<id>` | `documentId` 303872; the GET returns the PDF; `required_documents_status` "Requirement Met" |
| `PUT …/actions/<aid>?userComments` | sticks |
| `POST …/grants` (query params, `fundingAgencyId` 1); `PUT`; `DELETE` | stick. `fundingAgencyId` 500001 is a 404 |
| `POST …/publications` (JSON); `DELETE` | stick |
| `POST …/actions?actionType=Supplement`; `DELETE …/actions/<id>` | soft delete (`isDeleted: true`), as in production |
| `DELETE …/actions/<aid>/submit` on an unsubmitted action | 200, no change |
| `POST …/renew?opportunityId` on an `Incomplete` draft (1439544) | new line 1439545, `Renewal`; copies title and the Allocation Manager role only |
| `DELETE /v1/requests/<rid>` on our own `Incomplete` draft | **200, soft**: `isDeleted: true` on the line and its action under `report`; `submit` then reads 401. Production's 401 was on a 2015 `Approved` line held by another PI |
| `POST /v1/requests?opportunityId&requestType=Renewal&requestNumber=<n>` with `<n>` = UHSS0002 (a SAM projcode XRAS never minted) and `<n>` = ZZZZ9999 (nothing) | **both 200**: lines 1439547 and 1439548, each the only line of a **new family** under that number, unlinked from NCAR4087. XRAS checks nothing about the number, so "file the Extension under the projcode" would work only by creating a second, disconnected record. Both soft-deleted |

**The phase-3 verb.** `POST /v1/people/samprobe-20261013?isReconciled=false` (with name,
email, organization) is 200, and `isReconciled: false` **sticks**: it reads back under both
contexts and in `search/people`. The placeholder can author: as `XA-USER:
samprobe-20261013`, create (1439546) → `roles/PI/<self>` → `DELETE` all 200, and benkirk,
holding no role on the line, reads it as 401. The person is left in place.

## 4. The handoff experiments

| Time (UTC) | Step | Observed |
|---|---|---|
| 19:20 | Ben approves NCAR4086 (1439308 / action 372621) | request `beginDate` 2026-10-13, **`endDate` null**; Approved dates on the action 2026-10-13 → 2027-10-12. In `status=Approved` (newest first), **absent from `active=true`** |
| 19:22 | dev `xras_sweep` (light slot) | `pending_push: 1`, sample `[NCAR4086]`. Within about 2 minutes of the approval. The pass before reported 0: the 8 older approved-not-pushed requests on demo are aged out by `OPEN_ENDED_MAX_AGE_DAYS` |
| 19:22:46 | Ben posts NCAR4086 | sam_dev #3: `New`, `add`, `processed`, 200, `request_id` 1439308, `projcode_result` **UCSG0004**. Project created inactive (by design, `sam/xras/handlers/new.py`), lead kyledavis, allocation type Small (No NSF award), Campaign Store / Casper / Casper GPU 2026-10-13 → 2027-10-12, kyledavis and metzger on each account. The payload carries `requestId` and a non-empty `opportunityQA` (the EUA) |
| 19:23 | re-read | request `endDate` **2027-10-12**, and the request now appears in `active=true`. **The post sets the request-level `endDate`**; the approval does not |
| 19:24:58 | Ben activates UCSG0004 in SAMuel | `xras_activation_event` #1, linked to #3; no mail (`NOTIFY_ENABLED` is 0 on dev) |
| 19:25:50 | Ben posts NRAL0051 (1436558 / 365664), a New that production applied on 2026-03-02 | sam_dev #4: `update`, `processed`, 200, no warning. **Applied again**: Derecho 4,000,000 → 6,000,000 and Casper 10,000 → 15,000 by two `SUPPLEMENT` transactions. Request `endDate` set to 2027-03-31 |
| 19:27:51 | Ben reposts NCAR4086 | sam_dev #5: `duplicate`, **422**, "repeat of #3 (processed)"; the admin app shows the message verbatim ("Failed to initiate the action …"). No second project |
| from 19:23 | re-read both keys every 1–15 min | see § 5 |
| 21:30 | dev `xras_sweep` | NCAR4086 **still `pending_push`**, two hours after #3 landed: `live_pending_numbers` is a set difference against `project.projcode`, and XRAS still names the request NCAR4086 (`XRAS_REQUEST_TICKETS.md` § 6) |
| 21:44:30 | Claude submits Draft B (1439543 / 372856) as benkirk | `Submitted`, **NCAR4087** minted. No mail reached benkirk's XA person address: demo sends no submit mail |
| ~22:00 | Ben approves NCAR4087 | as NCAR4086: `beginDate` 2026-11-01, `endDate` null |
| 22:07 | dev `xras_sweep` | `pending_push: 2`, `[NCAR4086, NCAR4087]` |
| 22:10:55 | Ben posts NCAR4087 | sam_dev #6: `New`, `add`, `processed`, 200 → **UHSS0002**, the first SAM-authored request to become a SAM project. Mnemonic HSS through benkirk's organization (no institution row), Derecho 1, 2026-11-01 → 2027-10-31. The payload has the same shape as an ARC one: `requestId`, `opportunityQA` with the EUA, PI and Allocation Manager both benkirk |
| ~22:12 | Claude files, validates and submits Extension 372862 (end 2028-01-31) and Supplement 372863 (Derecho +2) on NCAR4087 as benkirk | both `Submitted`; validate passes; the family is still NCAR4087 |
| 22:16:41 | Ben approves and posts the Extension | sam_dev #7: `manual`, **200**, "no service matches actionType='Extension'", `request_id` 1439543. UHSS0002 unchanged |
| 22:18:13 | Ben approves and posts the Supplement | sam_dev #8: `manual`, **200**, "no service matches actionType='Supplement'". UHSS0002 unchanged |
| 22:24:39 | Claude submits Draft A (1439539 / 372852), roster PI benkirk, User `hartter` (inactive in SAM), User `harrter` (absent) | `Submitted`, **NCAR4088** |
| ~22:26 | Ben approves NCAR4088 | — |
| 22:37 | dev `xras_sweep` | `pending_push: 3`; accounts 2 (`harrter` absent, `hartter` inactive, both reconciled in XRAS); account requests **0 created, 2 existing**: open rows #7 (`harrter`, submission) and #15 (`hartter`, reactivation), copied from production by the refresh, were reused, not duplicated |
| 22:49:38 | Ben posts NCAR4088 | sam_dev #9: `New`, `add`, **`failed`, 422**: "Username hartter is inactive", "Username harrter is missing". The admin app shows both verbatim. No project; a `failed` row does not block a repost |
| 23:4x | Claude: `PUT /v1/requests/1439543/attributes?requestNumber=UHSS0002` (submit, as the PI) | 200. **NCAR4087 now resolves to nothing; UHSS0002 holds 1439543** with its New, Extension and Supplement; `requestId` unchanged. The same shape as the manual production rename (UPSU0087) |
| 23:44:58, 23:46:34 | Ben uses "Repost to Accounting Service" | both send the request's **New** (372856), now as `requestNumber: UHSS0002`: #10 and #11, `duplicate`, 422. The repost targets the New, not a chosen action, so a parked action cannot be re-sent this way. Without the guard the New would have gone to `update` and applied again |
| 23:47:46 | Claude files and submits Extension 372866 (end 2028-03-31); Ben approves and posts | #12: `Extension`, **UHSS0002**, `extend`, **`processed`**, 200. UHSS0002's Derecho end is 2028-03-31 |

**Actions on a family whose number was never rewritten park, and XRAS is told they
succeeded.** `select_service` (`sam/xras/dispatch.py`) resolves only `requestNumber`, so #7
and #8 match no project; a `manual` park answers 200, which the admin app reports as "The
Action Post was Successful". The reason names the action type, while the cause is the
unknown number. Each row carries the `request_id` of #6, a `processed` `New` naming
UHSS0002: rule 2 of `XRAS_SUBMISSION.md` § 4.6 item 2 resolves them from SAM's own log.

**Idempotency, read together.** The route guard (`src/webapp/api/xras/actions.py`,
`_refuse_repeat`) refuses a post whose `actionId` already has a `processed` (or in-flight)
row, and it did. It can only see SAMuel's own log, so a post whose first application
predates that log is applied again: every action handed off before the 2026-08-24 repoint in
production, and every action at all on samuel-dev after `make refresh-dev` empties the log.
NRAL0051 is that case. Production's NRAL0051 also holds a 2026-05-22 `SUPPLEMENT` of exactly
the award (2,000,000 / 5,000) with no comment, which fits a legacy-era repeat; whether
production XRAS has a May Supplement action on that family is unchecked.

## 5. The `requestNumber` rewrite

**It is manual.** Steve (2026-10-13): XRAS mints the number at submit from the Client Settings
template ("Request Number Format"), does nothing with the reply to the accounting-service post,
and the production renames were done by hand by NCAR staff, who have since left. That explains
every observation: 23 of 24 production award letters from 2026-09-29 to 2026-10-12 name the
projcode (renamed between post and notify), the 2026-09-09 External Projects letters and
Simpson's (NCAR4401) did not, UHSS0001 never changed, and XRAS-dev never renames (a watch from
19:23Z to 23:30Z saw no change). Legacy SAM's only record of the mapping was its success mail to
`hdt@ucar.edu` ("XRAS Request Number X, SAM Project Code Y"); SAMuel sends no such mail.

**SAM can perform it.** The create and update routes take `requestNumber`; a number with no
request master creates one, an existing one joins it (§ 3, § 4 at 23:4x). After the rename, a
newly filed Extension applied normally (#12).

## 6. SAMuel renames: the closed loop on `sha-726c77f` (2026-10-13/14)

The first runs of `XRAS_CLOSED_LOOP.md` Track 1 (#819) on samuel-dev, `XRAS_RENAME_ENABLED=1`.
Claude made every API call and admin-app click; Ben signed in (2FA). Nothing was notified.

| Time (UTC) | Step | Observed |
|---|---|---|
| 10-14 03:22 | first `xras_rename` run | 2 candidates. NCAR4087 (1439543) already `UHSS0002`: no write. NCAR4086 (1439308, kyledavis's "TEST 8.17.26") renamed to **UCSG0004** as kyledavis, `xras_remediation_event` #1 `verified`. Ben: the rename is fine; mail to others is the concern, and none is sent |
| 03:22-11:20 | hourly runs | each 2 reads, no write, no audit row |
| 11:49 | Claude creates and submits a benkirk-only New on Exploratory (1439571 / 372884): PI, attributes, Derecho 1, dates, fos, EUA, Advisor Letter (`cupsfilter` PDF), validate | **NCAR4089**. No mail reached benkirk's XRAS address |
| 12:45 | Claude approves in the admin app (requested dates, Derecho 1) | "Notifications Generated? No" |
| 12:46 | `POST …/requests/1439571/renew` before posting | line 1439572, Renewal, Incomplete, **already under NCAR4089** |
| 12:46:07 | Claude posts the New | sam_dev #13, `add`, processed → **UHSS0003**; reply 200 in 248 ms |
| 12:46:09 | the route's after-reply rename | `NCAR4089 -> UHSS0003` `verified`, event #2, **1,770 ms**, after the reply. The admin app now titles it "New - UHSS0003"; `NCAR4089` holds only the renewal line |
| 12:47 | Extension 372886 on 1439571 (validate first failed: "User Comments is a required field"); approved; posted | sam_dev #14, `extend`, processed under UHSS0003 (warning: project inactive). No rename: the number is the projcode |
| 13:22 | `xras_rename` | sibling pass renames 1439572 onto the live family, event #3 `verified`. **UHSS0003 holds both lines; NCAR4089 answers nothing.** The `requestNumber` PUT joins an existing family, as the admin app's "Merge" does |

**Where staff renamed by hand:** a request's Summary tab, "Request Number (Edit)" →
`/requests/<requestId>/edit`, two forms. "Assign new request number" takes a number not in use
(the first line); "Merge request with another" moves the line under an existing number (a
sibling). Any admin can still use it, unseen by SAM (`UNPLANNED_CITY_LEDGER.md` entry 26).

### 6.1 Track 2 on the same bed (2026-10-14, `XRAS_CLOSED_LOOP.md` § 4)

Every request benkirk-only (6: the placeholder, then benkirk); no Notify pressed.

| # | Done | Observed |
|---|---|---|
| 7 | New 1439573 / 372887 created **into** family UHSS0003 (`requestType=New&requestNumber=UHSS0003`); submit keeps the number (no `NCAR` mint). Approved, then **double-clicked** Post | Both clicks post, 0.45 s apart: #15 and #16 `update`, `failed` 422, "Action end date before existing allocation end date for Derecho" (UHSS0003 ran to 2028-03-31), shown verbatim in the admin app. Reset, re-approved with dates to 2028-10-31, double-clicked again: **#17 `update` processed** (Derecho extended to 2028-10-31, supplemented +2 → 3; replay 1+2 = 3), **#18 `duplicate` 422** "already applied by an earlier post (SAM #17)" 0.4 s later. The first post took 66 ms, so the in-flight branch (a first post still running) was not reached |
| 2 | Renewal line 1439572 (from § 6) completed | `/renew` copies roster, title, keywords, fos; not abstract, resources, dates, EUA, documents. Exploratory's renewal needs an Advisor Letter, no Progress Report; dates not required. Posted: **#19 `update`**, a new Derecho allocation 2028-11-01 → 2029-10-31 (4) after the current one; no rename (number = projcode) |
| 8 | dashboard read after the posts | **A posted request leaves the admin queue without a notify**: none of UHSS0002, UHSS0003 (New, Extension, Renewal) or UCSG0004 is listed; Exploratory "pending" 3 → 2. NCAR4088 (approved, post refused) stays |
| 3 | NCAR4090 (1439574 / 372888): Hold Off, Return for Corrections, Reject | **Hold Off** is admin-only ("Visible in XRAS Admin only"): status stays `Submitted`, only `states[]` gains `Hold Off`. **Return for Corrections**: request and action `Incomplete`, `returnedForCorrections: true`, the comment in `adminComments`, `Hold Off` kept. **Reject**: request `Rejected`, action `Declined`, `Hold Off` cleared |
| 4 | on NCAR4090 after the return: resubmit; withdraw (`DELETE …/submit`); submit | resubmit → **`Submitted`** (not `Under Review`, as production probe P3's re-submit was); withdraw → `Incomplete`; submit → `Submitted`. `Hold Off` survives all three |
| 1 | Supplement 372889 on UHSS0003, resource set, validate, then aborted (`DELETE`); fresh Supplement 372890, submitted, approved, posted | the aborted action is kept `isDeleted: true`; a Supplement validates without user comments (an Extension does not). **#20 `supplement`** processed, but onto the **renewal's future window** (2028-11 → 2029-10, 4 → 9), not the current one: `latest_allocation` is legacy's max-end-date rule. A Supplement filed after a Renewal posts lands there |
| 5 | `PUT …/attributes` and `GET /v1/requests/1439571` as `samprobe-20261013` (no role on the line) | 401 on both; nothing changed |
| 6 | New 1439575 / 372891 authored by `samprobe-20261013` (PI and Allocation Manager), the New recipe of `XRAS_CLOSED_LOOP.md` § 4; validates and submits as the placeholder (**NCAR4091**). Approved 19:58Z; posted after the 20:07Z sweep; merged in SAMuel; reposted | **The sweep** at approval: `pending_push` NCAR4091, the placeholder counted absent, not `placeholder` (SAM's test is the name shape `^\S+-user-\S+$`, `sam/queries/xras_accounts.py`), so `account_request` #22 (`submission`, `task:xras_sweep`) and no merge button on Pending Users. **The post**: #21 `add` `failed` 422 with four errors for one missing person ("PI … is not in database", "Allocation Manager … is not in database: " with an empty tail, "Username … is missing", "Could not produce affiliation data for PI …"), shown verbatim in the admin app. **The merge**: the card's own form (`xras_merge_form`, loaded into `#xrasModal`), free-text target benkirk: `xras_remediation_event` #4 `merge_person` `verified`; the placeholder 404s; benkirk holds PI and Allocation Manager on 1439575. **The repost** 20:09:11Z: #22 `add` processed 200, UHSS0004 (inactive, lead benkirk, Derecho 1 to 2027-10-31); rename event #5 `verified` 0.7 s later. `account_request` #22 stays `submitted`: fulfillment matches by email and the merge does not close the source's rows |

## 7. Open, and what unblocks it

| Open | Blocked on |
|---|---|
| A parked action on a renamed family (#7, #8) | the admin app's repost re-sends the New; re-filing, or SAM-side log resolution (`XRAS_SUBMISSION.md` § 4.6) |
| A phase-1 action on a family already numbered by its projcode | a demo family under a projcode on which benkirk holds a PI role |
| An approval without a click | a `review`/`admin` demo key or an auto-approve rule (Steve) |

## 8. Final state left on XRAS-dev

| Object | State |
|---|---|
| 1439539 / NCAR4088 (Draft A) | Approved; posted and refused (#9, 422) |
| 1439543 / **UHSS0002** (Draft B; minted as NCAR4087, renamed by Claude) | Approved and posted (UHSS0002); Extension 372862 and Supplement 372863 parked on SAM (#7, #8); Extension 372866 applied (#12); actions 372857–58 soft-deleted. Family UHSS0002 also holds soft-deleted 1439547 |
| 1439538, 1439540–42, 1439544–48 | soft-deleted drafts (1439545 is the renewal line; 1439547 and 1439548 the `requestNumber` probes) |
| person `samprobe-20261013` | **deleted**: merged into benkirk (sam_dev remediation #4, scenario 6) |
| 1439308 / **UCSG0004** (minted as NCAR4086, renamed by `xras_rename` 2026-10-14) | Approved, posted, not notified; SAM project UCSG0004 (active) |
| 1439571 + 1439572 + 1439573 / **UHSS0003** (minted as NCAR4089) | New 372884, Extension 372886, Supplement 372890 (372889 soft-deleted) on 1439571; Renewal 372885 on 1439572 (joined by `xras_rename`); New 372887 on 1439573; all approved and posted (#13, #14, #17, #19, #20), none notified |
| 1439575 / **UHSS0004** (minted as NCAR4091, authored by the placeholder) | Approved; posted 422 (#21), reposted after the merge (#22, processed), renamed by the after-reply hook (#5); not notified; PI and Allocation Manager benkirk |
| 1439574 / NCAR4090 | Rejected (action Declined) after Hold Off, Return for Corrections, resubmit, withdraw |
| samuel-dev | UCSG0004 (active), UHSS0002 and UHSS0003 (inactive); `xras_action_log` #1–#22; `xras_remediation_event` #1–#3 and #5 (renames), #4 (the merge); UHSS0004 (inactive); `account_request` #22 (Sam Probe, `submitted`, never fulfilled); NRAL0051 inflated by #4; UHSS0002 Derecho ends 2028-03-31; UHSS0003 Derecho 3 to 2028-10-31 and 9 for 2028-11 → 2029-10 |
| NRAL0051 (1436558) | Approved, posted, not notified; sam_dev allocations inflated by the repeat |
