# Reimplementing XRAS submission from SAMuel

**Status: sprint 1 (phases 0, 1, 1b and 2) shipped 2026-10-29 in #874, issue #871, build plan
[`XRAS_SUBMISSION_SPRINT1.md`](XRAS_SUBMISSION_SPRINT1.md); sprint 2 (phase 3) is issue #872.
The incoming half (§ 4.6 items 1-3) and every phase of
[`XRAS_POST_AUTOMATION.md`](XRAS_POST_AUTOMATION.md) this plan waited on are live in
production.** The decisions of 2026-10-27 are § 3.5. A project's allocation
life begins at ARC (`arc.ucar.edu/xras_submit`), is reviewed in XRAS, and reaches SAM at
approval (`docs/xras/incoming/`). This document designs the return path, **SAM → XRAS (review)
→ SAM (apply)**, so a request can be authored from SAMuel while ARC keeps working beside it.
The evidence:

- every XRAS verb below, driven live on production 2026-09-14
  ([`XRAS_SUBMISSION_PROBES.md`](../xras/outgoing/XRAS_SUBMISSION_PROBES.md));
- the ARC workflow, driven once for real 2026-09-15
  ([`XRAS_ARC_BASELINE.md`](../xras/outgoing/XRAS_ARC_BASELINE.md));
- the samuel-dev ↔ XRAS-dev loop, 2026-10-13/14
  ([`XRAS_DEV_PROBES.md`](../xras/outgoing/XRAS_DEV_PROBES.md)).

The identity step of phase 3 is its own product,
[`ACCOUNT_REGISTRATION.md`](implemented/ACCOUNT_REGISTRATION.md).

---

## 1. Goal

**Reimplement the XRAS submission workflows from SAMuel, coexisting with ARC.** XRAS stays the
system of review: the admin app, the approve → post → notify steps and the handoff to SAM are
untouched, and ARC keeps submitting. SAM becomes a second door onto the same XRAS request
object. A PI already on the SAM dashboard files the request there; a PI who prefers ARC is
unaffected. W9 measured that nothing ARC shows depends on SAM, so the two doors cannot
interfere.

**How this relates to post automation.** `XRAS_POST_AUTOMATION.md` works the other end of the
same request: the operator approves and posts, the post does everything SAM can, and a post
that cannot apply opens one ticket listing what is missing. That covers every request,
whoever authored it, and it reports a failure after approval. This plan moves resolution
earlier, to authoring time, for the requests SAM files. Since phase D armed (2026-10-19) a
refused post is one ticket (`XRAS_REQUEST_TICKETS.md`), so the failure classes below are read
from those tickets. The case for each phase is different:

- **Phase 1 (an action on an existing project) stands on its own.**
  - A lead extends or supplements from the project card they already use.
  - That keeps the promise in the `xras_activation` mail, that a lead can "request
    extensions or supplements through the SAM portal".
  - The amounts are bounded (§ 4.5).
  - It replaces the ARC Extension form, whose date picker turns an ISO date into year 31
    (§ 3.2).
- **Phases 2-3 (a New) earn their cost only through the failure classes a ticket can report
  but not prevent.** These are the classes below. Their size is read from phase D's tickets
  before either phase starts.

Every `New` handoff is resolved at approval by a handler that has only the wire payload:
- the PI's username → a `User`;
- the PI's affiliation → a mnemonic (an eleven-strategy ladder, `sam/xras/extractors.py`);
- `opportunityId` → an allocation type → a facility;
- the grant number → a contract;
- each resource key → a resource.

If SAM authors the request, each resolution happens **while the submitter is in front of us**,
weeks before approval, and is stored:

| Incoming failure class (`XRAS_TRIAGE_PLAYBOOK.md`) | With a SAM-authored request |
|---|---|
| Unreconciled ARC placeholder PI (55% of `New` failures) | the PI is `current_user`. `XA-USER` must resolve at the allocations process anyway, because XRAS proxies our `/api/xras/v1/people`. Phase 3 keeps the property with a placeholder SAM mints and merges itself |
| Mnemonic unresolvable, or valid but wrong (24%) | resolved with the submitter present, using the suggestor (`sam/queries/mnemonic_console.py`). `mnemonic_code_id` is stored, **not minted** |
| A WNA `Small` minted in the UNIV series, silently | the facility and `allocation_type_id` are explicit form choices, stored |
| "Cannot find contract" | the award is picked through the awards search (`sam-search awards`) and `contract_id` is stored. Post automation phase E creates the contract at post time when the award's PI resolves to a SAM user; the picker covers the rest |
| Unmapped resource key | the form offers only the opportunity's `rules.resourceIdsAvailableForNewRequest` ∩ `xras_resource_repository_key_resource` |
| Requested amounts uncalibrated to the resource | a per-type, per-resource default and bounds (§ 4.5). The ARC forms bound nothing |
| A refused post, reposted after each fix | the pre-submit preflight: the **existing** `sam/xras/preflight.py`, fed the draft instead of a `reports/requests` row |

On approval, the handler's job shrinks from *resolve* to *look up and apply*.
**Coexistence rule:** an ARC-originated request keeps the ladder. A SAM-originated one
short-circuits it, with the ladder as the fallback, in the same shape as
`lookup_request_override` at the top of `resolve_mnemonic_code` and in `plan_contracts`.

## 2. Phases

Three user-facing phases on one layer of plumbing. **Sprint 1 (issue #871) builds phases 0, 1,
1b and 2 as one PR with commits grouped per phase; sprint 2 (issue #872) builds phase 3.** Post
automation's A-D are live; its E is independent of this work. The home of phases 1 and 2 is a
My Requests tab on the user dashboard (§ 4.0).

| Phase | Who | What | Proves | Depends on | State |
|---|---|---|---|---|---|
| **0 — plumbing** | — | a JSON-body path in the write client's `_write`; the vocabulary module; the `xras_submission` table and the flow layer (§ 4.2, § 4.3); the ownership predicate; the routing policy (§ 4.4); the limits table and its admin card (§ 4.5); `XRAS_SUBMIT_ENABLED`; the asks to Steve (§ 5) | nothing by itself | nothing external | sprint 1, commits 0a and 0b |
| **1 — existing projects** | the lead or admin, from the project card | Supplement and Extension, with a status readout beside the control (described below the table); the admin Add-action modal re-pointed at the flow; the routed answer for projects that cannot use XRAS. **1b — Renewal:** `/renew` on the primary line, then the abstract, grants on Large and Small, and the document the rule book names, through the base64 upload | the flow layer, the audit trail and the lever. The incoming side is unchanged, because the request is the shape ARC would have sent | 0 | sprint 1, commits 1a-1c; every verb measured, the Extension's two-write shape on 2026-10-27 |
| **2 — new request, logged in** | any SAM user | the form of § 4.1, through preflight and submit (its parts are listed below the table); the `New` handler's consult point (§ 4.6 item 4) | the first phase that changes what arrives at the handoff | 0, 1 | sprint 1, commits 2a and 2b; the New rule book and the grant validation measured 2026-10-27 (§ 3.1) |
| **3 — new request, no account** | someone without a SAM login | `ACCOUNT_REGISTRATION.md` phase 2 first. The request is created under an XRAS person SAM mints (`POST /v1/people/<u>` with `isReconciled = false`); the `xras_submission` row carries `registration_id`; the account's arrival merges the placeholder into the real username with `merge_placeholder`, which moves the PI and Allocation Manager roles (§ 5 has the two constraints this brings) | the 55% row of § 1 | 2 in production; `ACCOUNT_REGISTRATION.md` phase 2 | sprint 2 (#872); the person verb and the merge measured on XRAS-dev |

**Phase 1's status readout** is the sweep cache entry for the projcode with
`is_pending_work`, plus `get_recent_xras_actions`. `manual` and `failed` read as "being reviewed
by CISL", and nothing names a notified address.

**Phase 2's form** asks for:
- the opportunity;
- the affiliation, failing visibly when the `user_institution` row is end-dated (the case that
  minted `CGD` for a CU Boulder PI);
- a mnemonic, through the suggestor;
- an award, looked up in the awards registry and confirmed: XRAS validates six grant fields on
  Small, Large and External (§ 3.1), and `AwardRecord` carries all six for an NSF award;
- resources bounded by the opportunity's `rules`, and amounts bounded by § 4.5;
- the explicit PI role;
- acceptance of SAMuel's own NWSC end-user agreement, recorded on the row (§ 4.2) as on every
  action SAM files; the XRAS attribute row is written from it until the pivot (§ 3.5);
- documents where the rule book demands them.

Every phase is proven on samuel-dev ↔ XRAS-dev first, where an approval posts to `sam_dev`,
never to production. The bed, its recipe and its rules are in `XRAS_CLOSED_LOOP.md` § 4. The
benkirk-held families UHSS0002 and UHSS0003 are phase 1's test bed there.

## 3. What we have measured

### 3.1 The wire surface

Every call runs under our key's `submit` context, impersonating the PI. `review` and `admin`
answer 401 for every identity, so only the *Requested* stage is ours. Reads for
verify-by-reread go through the `report` context, the dual-context arrangement
`XrasAdminClient` already uses.

| Step | Endpoint | Measured behavior |
|---|---|---|
| Create | `POST /v1/requests?opportunityId&requestType[&requestNumber&grantTypeId]` | returns the full object **with `rules{}`**, status `Incomplete`, **the first action already minted**, `requestNumber` **null**, and the creator as **Allocation Manager, not PI**. The request's `rules{}` is only `allowedOperations`, `allowedActions` and `existingActions`: **the resource list is on the opportunity's rules** (below). A deleted draft answers **401 by id** afterwards, which is the verify signal (2026-10-27) |
| Opportunity rules | `GET /v1/opportunities[/<id>]` under `submit` as the user | `rules{}` per opportunity: `canSubmitNewRequest`, `resourceIdsAvailableForNewRequest`, `defaultAllocationAwardPeriod` (12 months on every open one) and **`availableActionRequests`**, the actions XRAS offers *that* user per family (a user with no role sees none). The request's own `allowedActions` is advisory: XRAS accepted an Extension it did not list. Measured on XRAS-dev 2026-10-27 and **on production 2026-10-28** as benkirk: UHSS0001 offers Renewal, and `GET /v1/requests/1449367` by id answers 200 with `allowedActions ["Supplement","Extension","Transfer"]` |
| PI | `POST /v1/requests/<rid>/roles/PI/<username>` | required. One person may hold both PI and Allocation Manager. Validate and submit run as the PI: the same call passes as the PI and fails as an Allocation Manager who is not also the PI |
| Text | `PUT /v1/requests/<rid>/attributes` | `title shortTitle abstract keywords isSupportedByGrants`. The apidoc also documents `requestType`, `grantTypeId` and `requestNumber`, which renames the request into that family (§ 4.6 item 3) |
| Action | `POST /v1/requests/<rid>/actions?actionType[&userComments]` → `{actionId}` | accepted on `Approved`, `Incomplete` and `Submitted` lines; `userComments` sticks as a create param, so an Extension is two writes, this and `allocation_dates?endDate` alone (`beginDate` null is accepted). `DELETE …/actions/<aid>` is a **soft delete**: the row stays with `isDeleted: true`, and the line's own `isDeleted` is derived from its actions. A deleted action can hold the family's highest `actionId`, so anything that walks `actions[]` must filter it |
| Renew | `POST /v1/requests/<rid>/renew?opportunityId` | a new line with `rules{}`. It **copies the roster, title, keywords and field of science**, not the abstract, grants, resources, dates or documents. Create-with-`requestNumber` also spawns a line but copies nothing, so use `/renew` |
| Resources, dates | `PUT …/actions/<aid>/resources/<resourceId>?amount`; `POST …/allocation_dates?beginDate&endDate` → `{allocationDateId}` | query params; the resource id is the resource *type* id |
| Field of science, grants, publications | `PUT …/fos/<fosTypeId>?isPrimary`; `POST …/grants` → `{grantId}`, `PUT …/grants/<gid>`; `POST …/publications` (JSON) | all three stick on XRAS-dev (`XRAS_DEV_PROBES.md` § 3). A grant stores with any subset of its params, but validate on Small (and the rule book on Large and External) demands **six**: PI name, title, begin and end date, program officer name and email (`XRAS_SUBMISSION_SPRINT1.md` § 9) |
| Opportunity attributes | `PUT …/opportunity_attributes`, **JSON body** | the EUA row (§ 4.2). The bulk form is the one that works: the single-attribute `PUT` with no value is a 400 |
| Documents | `POST …/documents`, **JSON with a base64 body**; `GET …/required_documents_status` | XRAS parses the bytes: a hand-written PDF is a 400, a rendered one a 200 with a `documentId`; a non-PDF is 400 "document is not a PDF file"; a 15 MB PDF is a 200 in 4.7 s, and the type is not checked against the rule book at upload. The status endpoint names exactly the unmet rule, and answers `[]` on an Extension. No multipart transport is needed |
| Rule book | `GET /v1/allocation_types/<at>/action_types/<act>/required_fields` | `<at>` is `opportunities[].allocationTypeInfo.allocationTypeId`; the tables are in the probes doc § 5 |
| Preflight, submit | `GET …/validate`; `POST …/submit` | validate names exactly the unmet rules. A first submit lands **`Submitted`** and mints the number. A resubmit after Return for Corrections or a withdraw also lands `Submitted` on XRAS-dev, while production's one re-submit (P3) landed `Under Review`. The submit body is `null` |

Every step is a single attempt with verify-by-reread (`XRAS_WRITE_PROBES.md` § 4.4: a 200 proves
nothing), and the audit row is committed **before** the write leaves
(`sam/manage/xras_remediation.py`, `_editor_op`).

Two entries belong in the privilege register (`XRAS_WRITE_PROBES.md` § 7):
- The create response is captured for its `rules{}`. `GET /v1/requests/<rid>` is 401 on
  production for an identity with no role on the line and 200 for a role-holder (benkirk on
  UHSS0001, 2026-10-28), the same as XRAS-dev.
- The Requested stage is the only stage a SAM submission can populate.

XRAS checks nothing at submit about the people on the line. A request whose PI is an
unreconciled placeholder validates and submits (`XRAS_DEV_PROBES.md` § 6.1, scenario 6), so
SAM is the only gate.

**Vocabulary is process-scoped**; the apidoc's examples are XSEDE's.
- NCAR request types are New and Renewal only.
- Action types carry ids: Supplement 500020, Extension 500017, Renewal 500021, New 500019,
  Adjustment 500168, Date Adjustment 500334.
- Allocation-date stages are Requested 1, Suggested 3 and Approved 2.
- A request is `Incomplete · Submitted · Under Review · Approved · Rejected`, and a rejected
  action is `Declined`.
- Hold Off is not a status but a `states[]` entry on the action. It is admin-only, kept through
  a return and a withdraw, and cleared by a reject.
- Return for Corrections sets the request and action back to `Incomplete` with
  `returnedForCorrections: true` (`XRAS_DEV_PROBES.md` § 6.1).

`sam/integration/xras_api/vocabulary.py` holds the role types and the action and request type
ids (`ACTION_TYPES`), because the rule-book endpoint takes them.

**The rule book is exact** (every type, including New, read 2026-10-27).
- A Supplement requires **nothing**.
- An Extension requires an end date and user comments (External and NSC: nothing).
- A Renewal requires a title and abstract, plus grants on Large and Small.
- A New requires a title and abstract, plus grants on Large, Small and External.

Documents are a separate rule (`/types/documents`, with a `hardPageLimit` per pair):
- a Main Document for New and Renewal on Large and NSC (10 pages);
- a Progress Report for Renewal on Small and Educational;
- Supporting Information on Educational;
- an Advisor Letter on Exploratory.

**No document rule names Supplement or Extension.** Across 400 approved requests the corpus
agrees: 0 of 28 Extensions carry a document. That line is what phases 1 and 1b are cut along.

**What the PI sees.** An API-created request is a first-class object in ARC:

- a draft is listed under its title, with a completeness bar and an Edit link;
- a submitted request is listed under its number, with View, Edit request and **Delete
  request**. The PI can delete from ARC what our key cannot delete through the API;
- in the admin app it reaches the dashboard within a minute. Hold Off and Return for
  Corrections are the operator's first choices, and the Process tab's "Finalize and Post" table
  records the post and the notify as two separate steps (probes doc § 3.5).

### 3.2 The ARC baseline (W9, 2026-09-15)

The existing workflow was driven once for real, on a throwaway Exploratory request under
`benkirk`: ARC → XRAS admin app → SAM, with every surface read at every step
(`XRAS_ARC_BASELINE.md`). Result: XRAS `1449367` / `NCAR4354` → SAM project **`UHSS0001`**
(`xras_action_log` #183), active, lead `benkirk`, Derecho 1 core-hour 2026-10-01 → 2027-09-30.
The mnemonic `HSS` came through the organization branch, since `benkirk` has no institution row.

The ARC form is six pages, each simple:

| # | Page | Required | What loses people |
|---|---|---|---|
| 1 | Request Information: title, abstract, keywords | title, abstract | native HTML5 `required`: an empty submit moves focus and says nothing |
| 2 | Fields of Science: one dropdown, one primary | one row | — |
| 3 | Related Personnel: Project Lead picker, Project Admin (disabled, pre-filled with the submitter), User picker, **Create User** | a Project Lead | no pre-filled lead; a "Project Lead / Project Admin" vocabulary that differs from XRAS's own; *Create User* mints the untracked placeholders of § 1 |
| 4 | Additional Questions: the NWSC End User Agreement checkbox | the checkbox | the slug is `data_analysis` on every opportunity |
| 5 | Available Resources: four rows, amount + comment | **none** | every amount blank submits; the published limits appear nowhere |
| 6 | Documents: the Advisor letter, Submit | the letter | uploaded only at submit |

ARC never asks for allocation dates (the operator picks them at approval), or for grants on
this opportunity. The Extension form is one page, an end date and comments. Its date picker
parses **mm/dd/yy**, so an ISO date becomes year 31; XRAS accepts it and the admin app offers to
approve it. ARC also answered that submit with a 504 after the action already existed.

Four findings shape the design:

- **XRAS never renames a request to its projcode.** Neither the post nor the notify does it:
  17 minutes after the post and 11 after the notify, the family still answered only to
  `NCAR4354`, and the award letter named it. SAM performs the rename (§ 4.6 item 3).
- **An action filed before the rename arrives under the old number.** The Extension filed from
  ARC 17 minutes after the New carried `requestNumber: NCAR4354`. SAM resolves it from its own
  log (§ 4.6 item 2).
- **The admin dashboard clears at the post**, before the notify. The rows that linger for days
  are approved and not yet posted.
- **Extend comes from the approval.** ARC offers View, Extension, Supplement and Transfer the
  moment the operator approves, and Start a Renewal after the notify. A pending extension is
  not surfaced in ARC's list at all. Phase 1's control on the SAM project card is the same
  affordance, keyed the other way round.

### 3.3 What SAM already has

Below its routes, the admin request editor is caller-agnostic, and that is where it overlaps
with a PI-facing form. The operator assumptions sit in:
- the editor's routes and their `MANAGE_XRAS`/`ADMIN_XRAS` decorators;
- `_impersonation()`: `xa_user` is the PI, else any role-holder, since the choice is who the
  write is attributed to;
- the operator-only keys of the detail modal's context;
- the copy.

| Piece | Where | Reused how |
|---|---|---|
| the write client: PI and other roles, attributes, add action, resources, dates, user comments, validate, submit, withdraw, renew, rename, merge; `xa_user` and `context` per call. **Missing** (census 2026-10-27): create request, person-create, opportunity attributes, documents, action delete (§ 4.3's abort), field of science, grants and publications, and a JSON-body path in `_write` | `sam/integration/xras_api/admin_client.py` | as-is, plus the missing verbs (sprint 1, commit 0a) |
| the service ops, taking `operator` and the PI as parameters; the audit row opened before the write, the sweep cache patched after | `sam/manage/xras_remediation.py`, `_editor_op` | each flow step is one op |
| `xras_remediation_event` | the audit table | `created_by` and `xa_user` coincide for a PI, which the columns allow |
| the preflight: `preflight_action`, which shapes the wire dict (`synthesize_action`) and runs `dispatch_action(validate_only=True)` in a SAVEPOINT | `sam/xras/preflight.py` | fed the draft directly: `actionId` and `requestId` are optional in `XrasActionSchema`, so a draft with no XRAS ids loads |
| the mnemonic suggestor | `sam/queries/mnemonic_console.py` | the affiliation step |
| the awards search | `sam-search awards` | the contract step |
| the field validators (amount `≥ 0`, `end ≥ begin`, the comment caps) | `sam/schemas/forms/xras_remediation.py` | composed into one `ActionRequestForm` |
| the field macros and the modal shell | templates | as-is |
| `patch_requests_index`, `request_index_entry` | the sweep cache | the status readout. A row is keyed by its request number, which is the projcode once renamed, so a per-project read is a scan |

What neither side has is a **multi-step flow**. "Request a supplement" means add an action, then
set resources, dates and user comments, then validate and submit: five existing service calls
with no state between them, against a service with no transactions. A failure at the third step
leaves an `Incomplete` action in XRAS. Abort is a soft delete of that action, which XRAS keeps
as `isDeleted: true` (`XRAS_DEV_PROBES.md` § 6.1, scenario 1). The editor's own Add-action
modal has the same gap: it creates a bare action and stops.

### 3.4 Decisions taken (2026-09-14)

- **SAM keeps the metadata; XRAS carries the key.** The request only has to echo an identifier
  SAM already holds, and the resolved state lives in SAM, keyed by it (§ 4.2). Nothing precious,
  no projcode and no GID, is consumed before approval.
- **Routing** is the XRAS-family predicate plus a policy keyed by SAM allocation type, resolved
  by name at runtime. A control that cannot route is **disabled with its reason, never hidden**
  (§ 4.4).
- **Limits** are a hard maximum and a soft default per allocation type per resource, kept in a
  SAM table with an admin card. XRAS's `numbers[]` slot is empty at NCAR and ARC enforces
  nothing (§ 4.5).
- **One flow layer, two doors.** The PI form and the admin editor share the client, the ops, the
  audit table, the validators, the macros, the cache patch and the preflight. They do not share
  the operator modal or its templates (§ 4.3).
- **A separate lever, `XRAS_SUBMIT_ENABLED`**, distinct from `XRAS_WRITE_ENABLED`, because
  authoring a request has a different blast radius from correcting one:
  - webapp-only, never the tasks CronJob. The write lever itself is on both manifests since
    #819, because `xras_rename` is the one task write; this lever stays on the webapp's alone;
  - asserted per manifest, like the write lever;
  - when off, the control renders disabled with its reason.
- **The end-user agreement is SAMuel's gate** (Ben, 2026-10-14). The form shows the vendored
  NWSC agreement and requires its acceptance on every New and Renewal, recorded on the
  `xras_submission` row with the SHA and time (§ 4.2); XRAS's own acknowledgment row is written
  from it.
- **Most XRAS-side asks are settings we own.** Ben holds the admin role on
  `admin-ncar.xras.org`:

| Setting | Where | Use |
|---|---|---|
| Opportunity questions (`opportunityQA`) | Opportunities → *Submission Questions* → "Add a Question": Simple String, Multiple Strings, Numeric Range, Yes/No, Date; Text Field, Text Area, Calendar, Drop Down, Integer Only; active flag | a SAM-reference field is possible, but a question on an opportunity is shown to every ARC submitter too. So it stays a soft dependency (§ 4.2), not added until phase 2 needs it |
| Default resource amounts | Allocation Types → *Available Resources* → Default Resource Amounts | the ARC form's pre-fill; SAM's § 4.5 default should match it |
| Rule book | Allocation Types → *Allocation Type Rules*: Required Submission Fields, Required/Optional Documents, Maximum Requests per type, action time periods, ineligible lead statuses | the same rules `required_fields` and `required_documents_status` report: edit here, read there |
| Available units per opportunity | Opportunities → *Available Resource Numbers* | the pool, not a per-request bound; leave it blank |
| Notifications | Admin → *Notifications* (`/notification_templates`): each template sends for the rows of its Rules table (allocation type × action type × event × recipient). XRAS mails the submitter and `alloc@` on submit (Action Finalized/Submitted), and the PI with copies at the operator's Notify step (Action Approved) | the Action Approved rules for the action types SAM sends were deleted at the phase C cutover (2026-10-20) |
| End User Agreement attribute sets | Opportunities → *Submission Questions* (the `yes_no` set on five opportunities, § 4.2) | SAM owns the agreement (§ 3.5): these sets are deleted from the NCAR opportunities at the production pivot, the way phase C deleted its rules; until then SAM echoes the row |

### 3.5 Decisions taken (2026-10-27)

| Decision | |
|---|---|
| **One PR for phases 0, 1, 1b and 2** (issue #871), commits grouped per phase; phase 3 is sprint 2 (#872) | the dev loop is proven and A-D are live, so nothing is left to wait for |
| **A My Requests tab** on the user dashboard is the home of the list and every form; the user project card gets one quiet link into it (§ 4.0) | one surface to build and test |
| **Limits in full**: the table, a seed from ARC's published policy, a CrudSpec admin card (§ 4.5) | |
| **All seven open opportunities** for New from day one, so documents and grants are day-one | |
| **SAM owns the agreement, on every action.** Gentle acceptance (a checkbox beside the submit, the text one click away, no scroll gate) on Extension and Supplement too; `eula_sha` and `eula_accepted_at` on every row. XRAS's attribute sets are deprecated at the production pivot | one agreement, one record, whichever door |
| **Upload limits are hooks, dark at rollout**: `SUBMISSION_DOCUMENT_MAX_BYTES` and `SUBMISSION_DOCUMENT_MAX_PAGES`, unset means no SAM-side bound; the PDF check and XRAS's own refusal are the real bounds, the rule book's page limit is shown as advice | XRAS took a 15 MB PDF |
| **Logging is enough to diagnose, not pedantic**: one log line per flow step and XRAS's `errors[]` kept whole on the row | |
| **Grants are lookup-then-confirm**: the award number goes to the registry, the six fields XRAS validates are prefilled from `AwardRecord`, and only the unavailable ones are typed. NSF only (2a, 2026-10-29): USAspending lacks the program officer validate demands, so any other award is typed | |
| **Rollout**: the PR branch rolls to samuel-dev by workflow dispatch as often as needed; Ben applies the Postgres DDL to `sam_dev` by hand; production MySQL got it 2026-10-29 | DDL ships in two dialects (§ 4.2) |

## 4. The design

### 4.0 The home: a My Requests tab

The user dashboard gains a sixth URL-driven tab, **My Requests**, beside My Accounts. It lists
every XRAS request the user holds a role on (the sweep index, `request_numbers_by_username`)
and every submission SAM filed for them (`xras_submission`), each with its status: the sweep's
`is_pending_work` reading ("In review", "Approved, awaiting post", "Approved, in processing"; `manual` and `failed` read
"In review by CISL"), and "Applied on …" from `xras_action_log`. A stopped submission
reads "stopped at <step>" with Resume and Discard. Below the list, each project the user leads
or administers offers its actions through the routing of § 4.4, enabled or disabled with the
reason, never hidden; "New request" sits at the top. Every form of § 4.1 opens from this tab in
its own modal. The user project card carries one quiet "Request time" link into the tab; the
Manage Project page's Add, Extend and Renew stay what they are (allocation mutations, not
requests). When `XRAS_SUBMIT_ENABLED` is off the tab renders with every control disabled and
its reason.

### 4.1 The form: one to two pages, driven by the rule book

ARC's six pages are each simple, which is the problem: the work is spread across six saves and
the guidance across none of them. SAM's form is **one page for an action on an existing project,
and at most two for a New or Renewal**. It is **generated from the rule book, not hand-coded per
page**:
- `required_fields` and `required_documents_status` already name every rule per allocation
  type × action type (probes doc § 5);
- the opportunity's `rules{}` names the resources.

A section is a reveal, not a page: choosing the action or the opportunity uncovers exactly the
fields that choice requires, and nothing else.

| Action | The page asks for | What drives the reveal |
|---|---|---|
| Extension | the new end date (a real date input, ISO) and comments. The floor is the end the handler will compare against: the account's **latest** allocation by end date (`latest_allocation`, `sam/xras/handlers/_allocations.py`), which after a Renewal posts is the renewal's future window, so an earlier date is refused at the handoff (`XRAS_DEV_PROBES.md` § 6.1, scenario 7) | rule book: end date and comments required; no amounts, no document |
| Supplement | an amount per resource, pre-filled with the default and bounded by § 4.5, and comments | rule book: nothing required; the limits table. **Which window it lands on** is open: a Supplement filed after a Renewal posts applies to the renewal's future window (legacy's max-end-date rule, `XRAS_INGEST_IMPROVEMENTS.md` § 4). The form names the window it will reach, or the ingest rule changes first |
| Renewal | `/renew` copies the roster, title, keywords and field of science; the form asks for the abstract, grants on Large and Small, the agreement (below), and the Main Document or Progress Report the document rule names | the rule book and the document rules per allocation type |
| New, page 1 — *Describe* | the opportunity (everything below keys on it); title, abstract, keywords; field of science; PI = `current_user`, pre-filled, with an optional admin; affiliation → the mnemonic suggestor, failing visibly when the `user_institution` row is end-dated; the award picker, revealed when the opportunity's rule book wants grants | the opportunity's `rules{}`, `required_fields` |
| New, page 2 — *Request* | resources = `rules.resourceIdsAvailableForNewRequest` ∩ mapped, each showing its default, minimum and maximum; **the NWSC end-user agreement**, SAMuel's own vendored text shown in full and accepted on every request SAM files (New and Renewal), the way the registration gate asks it (`webapp/register/eula.py`); the document upload when a document rule names the pair; the preflight readout; submit | `rules{}`, § 4.5, the agreement (always; the XRAS attribute row is written from it when the opportunity carries one), `required_documents_status` |

What this fixes, against the baseline of § 3.2:
- the lead is pre-filled, and the role vocabulary is XRAS's own;
- amounts are defaulted and bounded, with the limit shown;
- the date input is a date input;
- a required field says so inline instead of moving focus;
- dates and grants are asked only where the rule book asks for them.

Mechanics: htmx reveals per selection, and an `HtmxFormHandler` subclass (the tier-3 handler in
`CLAUDE.md` § 9) with inline field errors. XRAS's own `validate` runs before submit, so the reveal
logic is never the only guard.

**This section is unprototyped.** The one-versus-two-page split for New is a judgment call to
revisit against a mockup; the rule-book-driven reveal is the part to keep either way.

### 4.2 The record: `xras_submission`, one row per action SAM files

Three identifiers arrive on every `/actions` payload, and they behave differently:

| | `requestNumber` | `requestId` | `actionId` |
|---|---|---|---|
| At create | **`null`**: nothing is minted | assigned, returned with `rules{}` | the first action is minted with the line; a later one is returned by `POST …/actions` |
| At submit | **minted** (`NCAR4352` appeared on the first `POST …/submit`) | unchanged | unchanged |
| After the New applies | renamed to the projcode by SAM, within a second of the reply (§ 4.6 item 3); the old number stops resolving | unchanged | unchanged |
| Across a renewal | stable: the family key | a renewal starts a **new** line | new |

One line carries many actions over its life. On XRAS-dev, line 1439571 holds a New, an
Extension and a Supplement (`XRAS_DEV_PROBES.md` § 8). So the record is **one row per action
SAM files**, with a surrogate key, because a draft exists before XRAS has assigned anything.
`action_id` is unique once known, and every flow has one from its first XRAS write. `request_id`
is indexed: it is the line the action hangs on, and the only XRAS key that exists between
create and submit.

Modeled on `XrasRequestOverride` (`sam/integration/xras.py`): `SessionMixin`, state transitions
as methods, a module-level lookup primitive. The DDL ships in **two dialects**, MySQL for the
local clone, the test container and production, Postgres for the CNPG `sam_dev` that samuel-dev
runs on; `load_postgres.py` streams every ORM table from the local MySQL clone, so the MySQL
script lands there before any `make refresh-dev` (`XRAS_SUBMISSION_SPRINT1.md` § 4).

| Column group | Columns |
|---|---|
| Keys | `xras_submission_id` (PK); `action_id` (unique, nullable until the first write); `request_id` (indexed); `request_number` (the family key, updated when observed); `project_id` (set from the start for an action on an existing project; set when a `New` applies) |
| Resolved SAM state | `user_id` (PI), `admin_user_id`, `facility_id`, `mnemonic_code_id`, `allocation_type_id`, `contract_id` (nullable, link-only), `opportunity_id`, `registration_id` (nullable; `ACCOUNT_REGISTRATION.md` § 4) |
| Agreement | `eula_sha`, `eula_accepted_at`: the git blob SHA of the vendored text and the moment the submitter accepted it, the pair `account_request` records at the registration gate. Required before submit on every action SAM files (§ 3.5) |
| Lifecycle | `state` ∈ `draft · created · populated · submitted · aborted · applied`; `created_by` (who clicked); `xa_user` (who SAM impersonated: the same person for a PI, a PI for an operator); timestamps |
| Evidence | `payload` (what we sent); `xras_snapshot` (the create response, `rules{}` included); the `xras_remediation_event` ids of each step |

**`state` holds only the steps SAM owns.** `draft` through `submitted` and `aborted` are the
flow's own steps, and resume continues from them. `applied` is stamped by the handler when the
action's post is processed. Between submit and apply, XRAS owns the status: `Submitted`,
`Under Review`, `Approved`, `Rejected`, plus Hold Off and Return for Corrections. The sweep and
the live read on the card report it; the record does not copy it. A copy would drift, because a
Return for Corrections or a withdraw moves a submitted action back to `Incomplete` without SAM
seeing it happen. A rejected or aborted submission costs one row.

The per-request ticket of post automation phase D needs nothing from this table. It keys
`external_ticket` on `request_id` and dedups its comments in `notification_log`
(`XRAS_REQUEST_TICKETS.md` § 4), and it covers ARC-authored requests, which have no row here.

**The XRAS-side echo is a soft dependency, not a need.**
- Attribute sets are ACCESS-configured per opportunity and written with
  `PUT …/actions/<aid>/opportunity_attributes` (JSON body, replace-all).
- Their answers come back on the push as `opportunityQA`: non-empty on **76 of 76** production
  `New` rows, and empty on every Extension and Supplement.
- SAM drops the field (`unknown = EXCLUDE`, `sam/schemas/forms/xras.py`).
- Five of the seven open opportunities carry the NWSC End User Agreement acknowledgment: a
  `yes_no` set, one `opportunityAttributeId` per opportunity. Data Analysis adds a textarea; the
  two Fall-2026 opportunities carry none.
- The acknowledgment stores **no value**: `attributeValue: null` on every production payload
  and on the one the probe wrote, so the row's presence is the answer.
- **The agreement itself is SAM's.** SAMuel carries the NWSC end-user agreement verbatim
  (`webapp/register/eula.md`, refreshed by `scripts/update_eula.py`) and the registration gate
  records what the visitor accepted as `eula_sha` + `eula_accepted_at`. A request SAM files
  shows the same text and records the same pair on its row (the Agreement group above); the
  XRAS attribute row is the echo written from that acceptance, never the record of it. A
  registration acceptance is per account and does not stand in for the request's.

A `simpleString` "SAM reference" attribute would round-trip and be visible to reviewers, but
the design must not need it. `userComments` also round-trips, and is the zero-ask fallback.

### 4.3 The flow layer: one implementation, two doors

```
webapp/dashboards/user/…                webapp/dashboards/allocations/xras/…
  PI request form (§ 4.1)                 admin Add-action modal
  require_project_permission              MANAGE_XRAS
  + holds a role on the line              + impersonates the PI
  xa_user = current_user                  xa_user = PI, created_by = operator
            \                                       /
             sam/manage/xras_submission.py        (new)
               request_action(session_factory, draft: ActionRequest, *, actor,
                              xa_user, eula, submit=True, door='pi')
               each step = an existing xras_remediation op through audited_write
               state in the xras_submission row; abort = soft-delete the action;
               resume = continue from state
                                |
             sam/manage/xras_remediation.py       (the ops, unchanged)
             sam/integration/xras_api/admin_client.py  (+ json= in _write)
             sam/integration/xras_api/vocabulary.py    (+ action types, stages, statuses)
```

The shared pieces are the § 3.3 table. **Deliberately not shared:**
- the operator detail modal (the stage matrix);
- `_impersonation()` and `_editor_target()`;
- `_detail_context`'s operator keys (overrides, role options, admin flags);
- the per-editor form templates, each bound to the audit modal's swap target;
- the copy ("remediation", "operator", "the card may lag until the sweep").

The admin modal is re-pointed at the flow, so "file a supplement on behalf of the PI" is the
same code path through a different door; the editor gains a populated action where it had a
bare one. `xras_remediation` is then a misnomer: rename it later, behind a shim. An **ownership
predicate**, in one place, answers "does this user hold a role on this line" from the roster
the client already returns.

### 4.4 Routing: which projects may request through XRAS

Most SAM projects came through XRAS, and some did not. An extension for one of those is handled
in SAM, never funneled through XRAS. SAM records no provenance:
- `project` has no request column;
- `xras_action_log` sees only pushes since the 2026-08-24 repoint;
- the legacy fingerprint (`allocation_transaction.user_id IS NULL` with an `XRAS Extension
  Request` comment: 1,715 projects on the 2026-09 snapshot, 677 of them active) is recall, not
  proof.

The authoritative predicate is the read the submit path needs anyway:

> **`get_request_family_by_number(projcode)` returns a line that is not `isDeleted`.**

That is exactly the condition under which XRAS accepts an action on the family:
- It holds for every project posted since the repoint, because SAM renames the family to the
  projcode (§ 4.6 item 3).
- It holds for legacy-era projects whose family NCAR staff renamed by hand.
- It answers `[]` for `SCSG0001` and two lines for `UCUB0182`.
- A projcode minted in SAM that later received a `New`-on-existing qualifies.
  (`select_service` in `sam/xras/dispatch.py` routes that New to `update`.) That is correct,
  since XRAS can review actions on it.
- A family whose every line is deleted does not qualify.
- The 12 legacy families still under `NCAR*` fail the predicate. Their projects are all
  inactive (`XRAS_CLOSED_LOOP.md` § 3.3).

On top of the predicate sits a small **policy**, keyed by SAM allocation type and resolved by
name at runtime, because having a family is necessary but not sufficient:

| Route | Allocation types | What the control renders |
|---|---|---|
| `xras` | CHAP, Small, Small (No NSF award), Data, Classroom, NSC, External Project | the request form |
| `email:<address>` | WRAP: extensions are a Wyoming committee decision, requested by mail to `wrap@uwyo.edu` | a mailto with the address and the project pre-filled. Flip the row to `xras` when WNA opts in |
| `staff` | Divisional, Staff, the reserves | "handled by CISL staff": divisional trees are renewed by staff in bulk and extended by an operator click |

The five expiration and activation mail templates tell leads three different things about how
to ask for more time. They converge on this table, and phase 1 keeps the `xras_activation`
mail's promise that a lead can "request extensions or supplements through the SAM portal".

A second gate applies to the same click: the SAM lead or admin must also hold a role on the
XRAS line, because every request-scoped write authorizes on `XA-USER`
(`XRAS_WRITE_PROBES.md` § 4.1). A user with no role on the line gets 401 on both read and write
(`XRAS_DEV_PROBES.md` § 6.1, scenario 5). Roster drift between SAM and XRAS is refused with a
named reason, never worked around by impersonating someone else. A request never mutates an
allocation, so `require_project_permission` (lead, admin, steward override) is the right
decorator. The policy that Extend and Renew themselves are not lead-available
(`webapp/api/access_control.py`) stands.

### 4.5 Limits per allocation type per resource

The ARC forms bound nothing, and users on their first project are not calibrated to the scale
of the resources. SAM already carries one number for this, `allocation_type.default_allocation_amount`:
a single default per type (Small 50,000; Divisional 100,000; CHAP and NSC 0), shown on the
Allocation Types admin form and used as a default by none.
The limit is that idea made per resource, with a floor and a ceiling.

`allocation_type_resource_limit` has these columns: `allocation_type_id`, `resource_id`,
`min_amount`, `default_amount`, `max_amount` (in the resource's own unit), `active`,
`created_by` and timestamps. It uses `SessionMixin` + `ActiveFlagMixin`, has `set()`/`clear()`,
and has a lookup primitive that returns `None` for an unconfigured pair. Rows name types and
resources; ids are resolved when read.

| | |
|---|---|
| **Hard maximum, soft default** | the form pre-fills `default_amount` and refuses an amount above `max_amount` or below `min_amount`. The server-side preflight repeats the check, so the form is not the only guard |
| **Applies to** | New, Supplement (the increment), Renewal. Extension carries no amount |
| **Does not apply to** | the admin request editor: an operator correcting a requested amount is not the case the limit exists for |
| **Unconfigured pair** | no limit; the form shows the resource without a default and says so |
| **Where it is edited** | Admin → Configuration, a CrudSpec card in the shape of Notification Addressing |

XRAS carries the same number types on the wire: each opportunity resource has `numbers[]` with
`source: allocationType` and the types Default, Minimum and Maximum. But the admin app exposes
no editor for a maximum:
- an allocation type's "Available Resources" tab offers **Default Resource Amounts** only;
- an opportunity's "Available Resource Numbers" page writes the `Available Units` pool;
- the resource page has properties and submission questions.

NCAR's data agrees: two decade-old defaults and Derecho dollar values, never a maximum. ARC
publishes the policy limits as text on its opportunities page and enforces nothing, so SAM's
table is the only enforcement. Its seed script (both dialects, sprint 1) carries ARC's
published policy where one exists and the snapshot's medians elsewhere (Ben, 2026-10-28:
the default is a nice median of what active projects of the type hold, the limit a multiple
above, and the peer-reviewed panels get a high ceiling and no default). The full table, with
the measured medians beside each proposed value, is `XRAS_SUBMISSION_SPRINT1.md` § 4. In short:
- **Small:** Derecho 500,000 default under the published 1,000,000 cap; Derecho GPU 1,500 under 2,500.
- **Exploratory and Classroom:** 500,000 and 400,000 defaults under the published 500,000 cap; GPU 1,500.
- **Casper, Casper GPU, Campaign Storage:** medians as defaults, p90 as the limit, per type.
- **Data Analysis:** Casper 1 / 1, the allocation is a yes, not a quota.
- **CHAP and NSC:** no default; ceilings of 500 million and 200 million Derecho core-hours.

Two published rules the table does not encode: requesting Derecho and Derecho GPU together
reduces each limit proportionally, and a Small or Exploratory one-time supplement may reach
twice the original. The form states them; nothing enforces them.

The same limits apply to initial requests and supplements.

### 4.6 The incoming side

Four pieces of the handoff path. Items 1 to 3 are built and live in production; item 4 belongs
to phase 2.

1. **The idempotency guard.** `_refuse_repeat` in `src/webapp/api/xras/actions.py` answers 422
   `duplicate` to a post whose `action_id` already has a `processed` or in-flight row. A
   double-clicked Post sends two posts 0.4 s apart, and the guard refuses the second
   (`XRAS_DEV_PROBES.md` § 6.1, scenario 7). It reads only SAMuel's log, so an action applied
   before the 2026-08-24 repoint can still apply twice on a repost (NRAL0051 on XRAS-dev,
   `XRAS_DEV_PROBES.md` § 4).
2. **Project resolution from SAM's own log** (`resolve_project`, `sam/xras/dispatch.py`). The
   three identifiers of § 4.2 make the processed `New` row SAM writes at the handoff
   (`request_id`, `request_number` as sent, `projcode_result`) a permanent mapping. It resolves
   in order:

   | # | Rule | Catches |
   |---|---|---|
   | 1 | `requestNumber` is a projcode | renamed families, and every legacy-era family renamed by hand |
   | 2 | a `processed` `New` or `Renewal` row has the same `request_id` | an Extension, Supplement or other action on that line before the rename: W9's #184, XRAS-dev's #7 and #8 |
   | 3 | a `processed` `New` or `Renewal` row has the same `request_number` | a Renewal filed before the rename, whose new line carries a new `requestId` but the family's number |

   Rules 2 and 3 cover ARC-originated and SAM-authored families alike, so routing needs no
   `xras_submission` row. The same lookup runs inside `select_service`, and the sweep's
   `live_pending_numbers` subtracts the numbers SAM has already pushed. A New never goes
   through it: it creates a project, or updates one by its own `select_service` rule. When no rule matches, the action parks `manual` with the reason "no SAM project for
   requestNumber=… (requestId=…)", and answers 200 with "SAM does not apply this action
   automatically; NSF NCAR staff will apply it by hand", which XRAS shows as a successful post.
3. **SAM performs the rename** (`rename_request`, `sam/manage/xras_remediation.py`;
   `XRAS_CLOSED_LOOP.md` § 3.5). `PUT /v1/requests/<request_id>/attributes?requestNumber=<projcode>`,
   under `submit` as the line's PI (else its Allocation Manager), renames the request in place:
   - the old number stops resolving, and `requestId` is unchanged;
   - actions filed afterwards arrive under the projcode and apply.

   The inbound route renames once its reply has gone (`call_on_close`, behind
   `XRAS_RENAME_ENABLED`), typically within a second. The hourly `xras_rename` task is the
   backstop. Both are on in production.

   Each rename is audited in `xras_remediation_event` and verified by re-reading the line **by
   id** (`reports/requests/<rid>`). An override keyed on the old number follows it
   (`XrasRequestOverride.rekey`). A second line still under the `NCAR####` token (a Renewal
   filed before the rename) is renamed onto the family under the projcode by the task, which is the
   intended grouping. A `New` routed to `update` already carries the projcode and is not
   renamed. Rules 2 and 3 of item 2 cover the moments before the rename and any action parked
   earlier.
4. **The `New` handler's consult point** (phase 2). It sits at the top of `NewHandler.assemble`
   (`sam/xras/handlers/new.py`), keyed on the payload's `requestId`, and pre-sets the mnemonic
   from the stored id, with the ladder as the fallback; the allocation type, contracts and area
   of interest come from the push, which carries what the form sent (built in 2b,
   `XRAS_SUBMISSION_SPRINT1.md` § 2). It only pre-sets inputs and reports nothing, so the error
   order the tests pin (title, roster, area, allocation type, mnemonic, contracts, dates,
   resources) is untouched. Every handler stamps `applied` (and a New's `project_id`) inside
   its own transaction.

## 5. Gaps and open questions

| Gap | Why it matters | Next step |
|---|---|---|
| **The form has no prototype** (§ 4.1) | the reveal rules are now measurement (the New rule book, the grant validation, § 3.1); the page split is still a judgment call | sprint 1, commit 2a; keep the rule-book-driven reveal |
| **Which window an action lands on** (§ 4.1) | `latest_allocation` (max end date) drives the extension, supplement, adjustment and update handlers and the preflight. After a Renewal posts, a Supplement on the old line applies to the renewal's future window silently, and an Extension to a date before that window's end is refused | undecided (`XRAS_INGEST_IMPROVEMENTS.md` § 4); sprint 1's form names the window it will reach and floors the date on it |
| **Write client gaps** | `_write` takes params only; create request, person-create, opportunity attributes, document upload, action delete, field of science, grants and publications are unbuilt. Action delete is the abort of § 4.3, so phase 1 needs it before the flow layer | sprint 1, commit 0a |
| **DDL is two-dialect** | samuel-dev runs Postgres, production MySQL; `load_postgres.py` streams every ORM table from the local MySQL clone | one MySQL and one Postgres script per table (§ 4.2) |
| **Large and NSC on the demo** | XRAS-dev has no open Large or NSC opportunity (its five are the University four and External), so the Main Document and the officer-bearing grants have no target there | open one of each in `xras-admin-demo` before the phase-2 proof |
| **The seed values** | proposed from the snapshot's medians and the published caps (`XRAS_SUBMISSION_SPRINT1.md` § 4) | approved 2026-10-29 (`XRAS_SUBMISSION_SPRINT1.md` § 5) |
| **Grant lookup depth** | validate wants the program officer; NSF's API carries it, USAspending does not | NSF first; decide USAspending when the step is built (§ 3.5) |
| **Placeholders SAM mints** (phase 3) | SAM classifies a placeholder by its username shape alone (`PLACEHOLDER_USERNAME_RE`, `^\S+-user-\S+$`, `sam/queries/xras_accounts.py`), and fulfills an `account_request` by email. A merge into an account under a different email leaves the sweep's row open in NUSD's queue (`XRAS_DEV_PROBES.md` § 6.1, scenario 6) | SAM names its placeholders `<x>-user-<y>`; `merge_placeholder` closes the source's rows (`XRAS_POST_AUTOMATION.md` phase D, item 6) |
| **The admin picker on a no-account New** (phase 3) | the logged-in New picks its admin with a user search (`/user/requests/htmx/admin-suggest`: any user who may file a New sees active users' names, usernames and emails). An anonymous registrant must not get that directory | decide the phase-3 admin field (typed and checked at submit, or named later by the PI) when sprint 2 builds it; never reuse the logged-in endpoint |
| **A parked action answers 200** (§ 4.6 item 2) | XRAS shows the operator a successful post, and phase D's tickets cover New and Renewal only | ask whether a parked action opens the request's ticket too, when phase D is built |
| **Actions applied before SAMuel's log** | § 4.6 items 1 and 2 read only SAMuel's log, so a repost of an action applied before the 2026-08-24 repoint applies again. The 12 legacy-era families still under `NCAR*` are dormant (`XRAS_CLOSED_LOOP.md` § 3.3) | import legacy-era actions (matched to `allocation_transaction`) only when a repost of a pre-repoint action actually happens |
| **Extension 399374 on `UHSS0001`** | approved and posted, parked as #184, not notified. The family answers `UHSS0001` after the 2026-10-14 backfill, so a fresh Extension applies | file it afresh, or withdraw it; the admin app's repost re-sends the `New`, which the guard refuses |
| **`GET /v1/projects`** | proxied to the accounting service as `/api/xras/v1/users/projects/<username>`, which neither legacy SAM nor SAMuel serves. One request in six weeks of production traffic, our own probe (`xras_action_log` #182, 2026-10-08). ACCESS's accounting service documents the response shape (`XRAS_OUTGOING_QUERIES.md` § 4.6) | ask Steve whether ARC or `xras_submit` calls it for NCAR; otherwise not built |
| **WRAP** | stays `email:wrap@uwyo.edu` until WNA opts in | flip the policy row |
| **XRAS's own mail** | the award mails are the Action Approved rules on the NCAR process's templates (§ 3.4), and they copy `alloc@`, allocation managers, Co-PIs, program officers and, for Large, NSF | settled in `XRAS_POST_AUTOMATION.md` phase C: confirm production's rules match the demo's, prove a deleted rule silences Notify on the demo, then delete the rules at cutover |
| **Approval by API** | **none exists.** ALLOCATIONS API 1.0 changes an action's state only through `POST`/`DELETE …/actions/<aid>/submit`; `reviews` and `final_review` are GET-only; `PUT …/actions/<aid>` takes `actionType`, `collaborators` and `userComments`. ACCESS's own accounting service approves in `xras_admin` too. Our key's `review` and `admin` contexts answer 401 (2026-10-13) | every loop closes with a click in `xras-admin-demo`. Ask Steve for an `admin`-context verb or an auto-approve rule on the demo instance, and whether `GET /v1/usage/by_month` would be rendered if NCAR served it |

**The test loop** is samuel-dev ↔ the XRAS demo instance (`xras-admin-demo.xsede.org` and
`xras-submit-api-demo.xsede.org`, process `NCAR`, which accepts the production key):
- **Outbound:** `helm/values-dev.yaml` points samuel-dev at the demo API with both XRAS levers
  on. `helm/tests/test-dev-render.sh` and `tests/unit/gates/test_xras_admin_client.py::TestDev`
  reject the production URL and secret path.
- **Inbound:** `sam_dev` carries production's `api_credentials` rows on every refresh, so the
  demo admin app authenticates as it does in production.
- **Records:** the environment is in `implemented/K8S_DEV_ENVIRONMENT.md`; the recipe, the
  rules and the state left on the demo are in `XRAS_CLOSED_LOOP.md` § 4 and
  `XRAS_DEV_PROBES.md` § 8.

## 6. References

| | |
|---|---|
| [`XRAS_SUBMISSION_SPRINT1.md`](XRAS_SUBMISSION_SPRINT1.md) | the sprint-1 build plan: the commit map, the file plan, the DDL in two dialects, the dev proof |
| [`XRAS_POST_AUTOMATION.md`](XRAS_POST_AUTOMATION.md) | the incoming roadmap this plan followed: approve and post does everything SAM can, and a failed post opens one ticket |
| [`XRAS_PHASE_C_AND_TICKETS.md`](XRAS_PHASE_C_AND_TICKETS.md), [`XRAS_NOTICES_CUTOVER.md`](XRAS_NOTICES_CUTOVER.md) | how phase C deleted XRAS's rules at its cutover: the shape the agreement's pivot follows |
| [`XRAS_SUBMISSION_PROBES.md`](../xras/outgoing/XRAS_SUBMISSION_PROBES.md) | the 2026-09-14 probe record: every verb in § 3.1, the NCAR vocabularies, the rule-book tables, the final state left in XRAS |
| [`XRAS_ARC_BASELINE.md`](../xras/outgoing/XRAS_ARC_BASELINE.md) | W9, 2026-09-15: the ARC form page by page, and one real New and Extension seen from ARC, the admin app, the API, SAM and mail |
| [`XRAS_DEV_PROBES.md`](../xras/outgoing/XRAS_DEV_PROBES.md) | the XRAS-dev read and write map, the posts to samuel-dev, the rename loop and the Track 2 scenarios |
| [`XRAS_CLOSED_LOOP.md`](XRAS_CLOSED_LOOP.md) | the rename (§ 4.6 item 3), the production census and backfill, and the test bed |
| [`XRAS_REQUEST_TICKETS.md`](XRAS_REQUEST_TICKETS.md) | the ticket a blocked post opens (post automation phase D) |
| [`ACCOUNT_REGISTRATION.md`](implemented/ACCOUNT_REGISTRATION.md) | the identity step of phase 3, as a product of its own |
| [`REQUEST_EDITOR.md`](../xras/outgoing/REQUEST_EDITOR.md) | the write client, tiers, levers and stage model the flow layer extends |
| [`XRAS_WRITE_PROBES.md`](../xras/outgoing/XRAS_WRITE_PROBES.md) | probe methodology, the one authorization rule, the privilege register |
| [`XRAS_OUTGOING_QUERIES.md`](../xras/outgoing/XRAS_OUTGOING_QUERIES.md) | the readable surface and the request payload shape |
| [`PROJECT_AND_ACCOUNT_LIFECYCLE.md`](../xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md) | where a project comes from; SAM never originates users |
| [`XRAS_TRIAGE_PLAYBOOK.md`](../xras/incoming/XRAS_TRIAGE_PLAYBOOK.md) | the 422 catalog § 1 answers |
| [`XRAS_PUSH_READINESS.md`](implemented/XRAS_PUSH_READINESS.md) | the preflight engine reused as the pre-submit check |
| [`XRAS_INGEST_IMPROVEMENTS.md`](XRAS_INGEST_IMPROVEMENTS.md) | ingest paper cuts, including the Supplement-after-Renewal window |
| [`XRAS_ACCOUNT_QUEUE.md`](XRAS_ACCOUNT_QUEUE.md) | the Pending Users queue the registration product generalizes |
| `https://api.xras.org/apidoc.html` | the index (86 endpoints; the same URL without `.html` is a 404 decoy). Detail pages are static under `https://api.xras.org/apidoc/1.0/` (plain `curl`), and their examples are XSEDE's, so every id is read live for NCAR |
