# XRAS-dev probes: the demo instance mapped, and the first handoffs to samuel-dev

**Status: run 2026-10-13 against `xras-submit-api-demo.xsede.org` (process `NCAR`) and
`xras-admin-demo.xsede.org`, with samuel-dev on `sha-f5237f1`.** This is the first time
anything was pushed from XRAS-dev to samuel-dev. Claude made every API call; Ben made every
approve and post click in the admin app. It answers the "no clean test loop" gap of
[`XRAS_SUBMISSION.md`](../../plans/XRAS_SUBMISSION.md) § 5 and the experiments of
[`XRAS_REQUEST_TICKETS.md`](../../plans/XRAS_REQUEST_TICKETS.md) § 7, except those that
need a SAM-authored submit (§ 6).

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

## 6. Open, and what unblocks it

| Open | Blocked on |
|---|---|
| A parked action on a renamed family (#7, #8) | the admin app's repost re-sends the New; re-filing, or SAM-side log resolution (`XRAS_SUBMISSION.md` § 4.6) |
| A phase-1 action on a family already numbered by its projcode | a demo family under a projcode on which benkirk holds a PI role |
| An approval without a click | a `review`/`admin` demo key or an auto-approve rule (Steve) |

## 7. Final state left on XRAS-dev

| Object | State |
|---|---|
| 1439539 / NCAR4088 (Draft A) | Approved; posted and refused (#9, 422) |
| 1439543 / **UHSS0002** (Draft B; minted as NCAR4087, renamed by Claude) | Approved and posted (UHSS0002); Extension 372862 and Supplement 372863 parked on SAM (#7, #8); Extension 372866 applied (#12); actions 372857–58 soft-deleted. Family UHSS0002 also holds soft-deleted 1439547 |
| 1439538, 1439540–42, 1439544–48 | soft-deleted drafts (1439545 is the renewal line; 1439547 and 1439548 the `requestNumber` probes) |
| person `samprobe-20261013` | `isReconciled: false`, no roles |
| NCAR4086 (1439308) | Approved, posted, not notified; SAM project UCSG0004 (active) |
| samuel-dev | UCSG0004 (active), UHSS0002 (inactive); `xras_action_log` #1–#12; NRAL0051 inflated by #4; UHSS0002 Derecho ends 2028-03-31 |
| NRAL0051 (1436558) | Approved, posted, not notified; sam_dev allocations inflated by the repeat |
