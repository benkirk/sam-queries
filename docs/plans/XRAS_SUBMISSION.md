# Reimplementing XRAS submission from SAMuel

**Status: design, not built; the wire surface and the ARC baseline are measured.**
A project's allocation life begins at ARC (`arc.ucar.edu/xras_submit`), is reviewed in
XRAS, and reaches SAM at approval (`docs/xras/incoming/`). This document designs the
return path — **SAM → XRAS (review) → SAM (apply)** — so a request can be authored from
SAMuel while ARC keeps working beside it. Every XRAS verb below was driven live on
2026-09-14 and is recorded, with ids, in
[`XRAS_SUBMISSION_PROBES.md`](../xras/outgoing/XRAS_SUBMISSION_PROBES.md); the
existing ARC workflow was driven once for real on 2026-09-15 and is recorded in
[`XRAS_ARC_BASELINE.md`](../xras/outgoing/XRAS_ARC_BASELINE.md), which left
production project `UHSS0001` behind as the phase-1 test bed. Traffic figures are
from the production `xras_action_log` on 2026-09-10. The identity step of phase 3 is
its own product, [`ACCOUNT_REGISTRATION.md`](ACCOUNT_REGISTRATION.md).

---

## 1. Goal

**Reimplement the XRAS submission workflows from SAMuel, coexisting with ARC.** XRAS
stays the system of review: the admin app, the approve → post → notify steps and the
handoff to SAM are untouched, and ARC keeps submitting. SAM becomes a second door onto
the same XRAS request object — a PI already on the SAM dashboard files the request
there; a PI who prefers ARC is unaffected. W9 measured that nothing ARC shows depends
on SAM, so the two doors cannot interfere.

The reason to build the second door is what the first one costs. Every `New` handoff
is resolved **blind**, at approval time, by a handler that has only the wire payload:
PI username → `User`, PI affiliation → mnemonic (an eleven-strategy ladder in
`sam/xras/extractors.py`), `opportunityId` → allocation type → facility, grant number
→ contract, resource key → resource. The triage playbook's 422 catalog is the list of
ways that goes wrong, and every fix ends with "ask ACCESS to push the button again".
If SAM authors the request, each resolution happens **while the submitter is in front
of us**, weeks before approval, and is stored:

| Incoming failure class (`XRAS_TRIAGE_PLAYBOOK.md`) | With a SAM-authored request |
|---|---|
| Unreconciled ARC placeholder PI — 55% of `New` failures | PI is `current_user`; `XA-USER` must resolve at the allocations process anyway (XRAS proxies our `/api/xras/v1/people`). Phase 3 keeps the property with a placeholder SAM itself mints and merges |
| Mnemonic unresolvable, or valid-but-wrong — 24% | resolved with the submitter present, using the suggestor (`sam/queries/mnemonic_console.py`); `mnemonic_code_id` stored, **not minted** |
| WNA `Small` minted in the UNIV series, silently | facility and `allocation_type_id` are explicit form choices, stored |
| "Cannot find contract" | award picked through the awards search (`sam-search awards`); `contract_id` stored |
| Unmapped resource key | the form offers the opportunity's `rules.resourceIdsAvailableForNewRequest` ∩ `xras_resource_repository_key_resource` only |
| Requested amounts uncalibrated to the resource | a per-type, per-resource default and bounds (§ 4.5); the ARC forms bound nothing |
| Handoff fails → re-push | the pre-submit preflight is the **existing** `sam/xras/preflight.py`, fed the draft instead of a `reports/requests` row |

The handler's job on approval shrinks from *resolve* to *look up and apply*.
**Coexistence rule:** ARC-originated requests keep the ladder; a SAM-originated one is
a short-circuit with the ladder as fallback, the same shape as
`lookup_request_override` at the top of `resolve_mnemonic_code` and in
`plan_contracts`.

## 2. Phases

Three user-facing phases, in the order each becomes provable, on one layer of
plumbing.

| Phase | Who | What | Proves | Depends on | State |
|---|---|---|---|---|---|
| **0 — plumbing** | — | the JSON body path in the write client's `_write`; the vocabulary module; the `xras_submission` table and the flow layer (§ 4.2, § 4.3); the ownership predicate; the routing policy (§ 4.4); the limits table and its admin card (§ 4.5); `XRAS_SUBMIT_ENABLED`; the ingest idempotency guard (§ 4.6); the ask to Steve (§ 5) | nothing by itself | nothing external | designed |
| **1 — existing projects** | the lead or admin, from the project card | Supplement and Extension; a status readout beside the control (the sweep cache entry for the projcode with `is_pending_work`, plus `get_recent_xras_actions`; `manual` and `failed` read as "being reviewed by CISL", and nothing names a notified address); the admin Add-action modal re-pointed at the flow; the routed answer for projects that cannot use XRAS. **1b — Renewal:** `/renew` on the primary line, then abstract, grants on Large and Small, and the document the rule book names, through the base64 upload | the flow layer, the audit trail and the lever. The incoming side is unchanged, because the request is the shape ARC would have sent | 0 | designed; every verb measured; test bed `UHSS0001` |
| **2 — new request, logged in** | any SAM user | the form of § 4.1: opportunity, affiliation (fail-visible when the `user_institution` row is end-dated — the case that minted `CGD` for a CU Boulder PI), mnemonic suggestor, awards picker, resources bounded by the opportunity's `rules` and amounts by § 4.5, the explicit PI role, the EUA row, documents where the rule book demands them, preflight, submit; the `New` handler's consult point | the first phase that changes what arrives at the handoff, and the first that needs an approval to prove | 0; the test instance (§ 5) for the approval leg | designed; the form is unprototyped |
| **3 — new request, no account** | someone without a SAM login | `ACCOUNT_REGISTRATION.md` phase 2 first. The request is created under an XRAS person SAM mints (`POST /v1/people/<u>` with `isReconciled = false` — a new verb, unprobed); the `xras_submission` row carries `registration_id`; the account's arrival merges the placeholder into the real username with `merge_placeholder`, re-pointing the PI role | closes the 55% row of § 1 | 2 in production; `ACCOUNT_REGISTRATION.md` phase 2 | designed |

Phase 1 can be exercised against production XRAS today: small, reversible requests
under `benkirk`, each paired with its inverse, the one submitted request rejected by
Ben in the admin app. Phase 2 cannot — proving it needs an approval, and an approval
on production XRAS posts to production SAM (§ 5).

## 3. What we have done

### 3.1 The wire surface, measured

All under our key's `submit` context, impersonating the PI; `review`/`admin` are 401
for every identity, so only the *Requested* stage is ours. Reads for verify-by-reread
go through the `report` context, the dual-context arrangement `XrasAdminClient`
already uses.

| Step | Endpoint | Measured behavior |
|---|---|---|
| Create | `POST /v1/requests?opportunityId&requestType[&requestNumber&grantTypeId]` | the full object **with `rules{}`**, status `Incomplete`, **the first action already minted**, `requestNumber` **null**, and the creator as **Allocation Manager, not PI** |
| PI | `POST /v1/requests/<rid>/roles/PI/<username>` | required; one person may hold PI and Allocation Manager. Validate and submit must run as the PI — the same call passes as the PI and fails as the Allocation Manager |
| Text | `PUT /v1/requests/<rid>/attributes` | `title shortTitle abstract keywords isSupportedByGrants` |
| Action | `POST /v1/requests/<rid>/actions?actionType[&userComments]` → `{actionId}` | accepted on `Approved`, `Incomplete` and `Submitted` lines. `DELETE …/actions/<aid>` is a **soft delete**: the row stays with `isDeleted: true`, the line's own `isDeleted` is derived from its actions, and a deleted action can hold the family's highest `actionId` — anything that walks `actions[]` must filter it |
| Renew | `POST /v1/requests/<rid>/renew?opportunityId` | a new line with `rules{}`; **copies roster, title, keywords and field of science**, not abstract, grants, resources, dates or documents. Create-with-`requestNumber` also spawns a line but copies nothing — use `/renew` |
| Resources, dates | `PUT …/actions/<aid>/resources/<resourceId>?amount`; `POST …/allocation_dates?beginDate&endDate` → `{allocationDateId}` | query params; the resource id is the resource *type* id |
| Field of science, grants, publications | `PUT …/fos/<fosTypeId>?isPrimary`; `POST …/grants`; `POST …/publications` (JSON) | fos measured; grants and publications authorized in the editor spike, unbuilt |
| Opportunity attributes | `PUT …/opportunity_attributes` — **JSON body** | the EUA row (§ 4.2) |
| Documents | `POST …/documents` — **JSON with a base64 body**; `GET …/required_documents_status` | XRAS parses the bytes (a hand-written PDF is a 400, a rendered one is a 200 with `documentId`); the status endpoint names exactly the unmet rule. No multipart transport is needed |
| Rule book | `GET /v1/allocation_types/<at>/action_types/<act>/required_fields` | `<at>` is `opportunities[].allocationTypeInfo.allocationTypeId`; tables in the probes doc § 5 |
| Preflight, submit | `GET …/validate`; `POST …/submit` | validate names exactly the unmet rules; a first submit lands **`Submitted`** (a re-submit lands `Under Review`) and mints the number; the submit body is `null` |

Every step is single-attempt with verify-by-reread (`XRAS_WRITE_PROBES.md` § 4.4: a
200 proves nothing), and the audit row is committed **before** the write leaves
(`sam/manage/xras_remediation.py`, `_editor_op`). Two entries for the privilege
register in `XRAS_WRITE_PROBES.md` § 7: the create response is captured because it is
the only `rules{}` read our key gets (`GET /v1/requests/<rid>` is 401), and the
Requested stage is the only stage a SAM submission can populate.

**Vocabulary is process-scoped**; the apidoc's examples are XSEDE's. NCAR request
types are New and Renewal only; action types carry ids (Supplement 500020, Extension
500017, Renewal 500021, New 500019, Adjustment 500168, Date Adjustment 500334);
allocation-date stages are Requested 1, Suggested 3, Approved 2; a request is
`Incomplete · Submitted · Under Review · Approved · Rejected` and a rejected action is
`Declined`. These belong in `sam/integration/xras_api/vocabulary.py`, one module,
beside the role types.

**The rule book is exact.** A Supplement requires **nothing**; an Extension requires
an end date and user comments; a Renewal requires title and abstract, and grants on
Large and Small. Documents are a separate rule: a Main Document for New and Renewal
on Large and NSC, a Progress Report for Renewal on Small and Educational, an Advisor
Letter on Exploratory — and **no document rule names Supplement or Extension**. Over
400 approved requests the corpus agrees (Extension 0/28 carry one). That line is
what phases 1 and 1b are cut along.

**What the PI sees.** An API-created request is a first-class object in ARC:

- the draft is listed under its title with a completeness bar and an Edit link;
- the submitted request under its number with View, Edit request and **Delete
  request** — the PI can delete from ARC what our key cannot delete through the API;
- in the admin app it reaches the dashboard within a minute, with Hold Off and Return
  for Corrections as the operator's first choices, and a Process tab whose "Finalize
  and Post" table records the post and the notify as two separate steps (probes doc
  § 3.5).

### 3.2 The ARC baseline (W9, 2026-09-15)

The existing workflow, driven once for real on a throwaway Exploratory request under
`benkirk`: ARC → XRAS admin app → SAM, every surface read at every step
(`XRAS_ARC_BASELINE.md`). Result: XRAS `1449367` / `NCAR4354` → SAM project
**`UHSS0001`** (`xras_action_log` #183; mnemonic `HSS` via the organization branch,
since `benkirk` has no institution row), active, lead `benkirk`, Derecho 1 core-hour
2026-10-01 → 2027-09-30. Kept as the phase-1 test bed.

The ARC form is six pages, each simple:

| # | Page | Required | What loses people |
|---|---|---|---|
| 1 | Request Information: title, abstract, keywords | title, abstract | native HTML5 `required`: an empty submit moves focus and says nothing |
| 2 | Fields of Science: one dropdown, one primary | one row | — |
| 3 | Related Personnel: Project Lead picker, Project Admin (disabled, pre-filled with the submitter), User picker, **Create User** | a Project Lead | no pre-filled lead; a "Project Lead / Project Admin" vocabulary that differs from XRAS's own; *Create User* mints the untracked placeholders of § 1 |
| 4 | Additional Questions: the NWSC End User Agreement checkbox | the checkbox | the slug is `data_analysis` on every opportunity |
| 5 | Available Resources: four rows, amount + comment | **none** | every amount blank submits; the published limits appear nowhere |
| 6 | Documents: the Advisor letter, Submit | the letter | uploaded only at submit |

ARC never asks for allocation dates (the operator picks them at approval) or grants
on this opportunity. The Extension form is one page — end date and comments — whose
date picker parses **mm/dd/yy**, so an ISO date becomes year 31, XRAS accepts it, and
the admin app offers to approve it; ARC also answered that submit with a 504 after
the action existed.

Four findings that shape the design:

- **The `requestNumber` rewrite is neither the post nor the notify** (§ 4.2).
- **An action filed in the gap parks.** The Extension filed from ARC 17 minutes after
  the New arrived as `requestNumber: NCAR4354` and sits `manual` as #184 (§ 4.6).
- **The admin dashboard clears at the post**, before the notify; the rows that linger
  for days are approved and not yet posted.
- **Extend comes from the approval.** ARC offers View, Extension, Supplement and
  Transfer the moment the operator approves, and Start a Renewal after the notify. A
  pending extension is not surfaced in ARC's list at all. Phase 1's control on the
  SAM project card is the same affordance keyed the other way round.

### 3.3 What SAM already has

Below its routes the admin request editor is caller-agnostic, and that is where the
overlap with a PI-facing form lives. The operator assumptions sit in the twenty-eight
routes and their `MANAGE_XRAS`/`ADMIN_XRAS` decorators, in `_impersonation()`
("`xa_user` is somebody else, the PI preferred"), in the operator-only keys of the
detail modal's context, and in the copy.

| Piece | Where | Reused how |
|---|---|---|
| the write client — every verb of § 3.1 except person-create, opportunity attributes and documents; `xa_user` and `context` per call | `sam/integration/xras_api/admin_client.py` | as-is, plus a JSON-body path in `_write`, which is params-only |
| fifteen service ops taking `operator` and the PI as parameters; the audit row opened before the write, the sweep cache patched after | `sam/manage/xras_remediation.py`, `_editor_op` | each flow step is one op |
| `xras_remediation_event` | the audit table | `created_by` and `xa_user` coincide for a PI, which the columns allow |
| the preflight: `synthesize_action` → `dispatch_action(validate_only=True)` in a SAVEPOINT | `sam/xras/preflight.py` | fed the draft through a report-shaped shim |
| the mnemonic suggestor | `sam/queries/mnemonic_console.py` | the affiliation step |
| the awards search | `sam-search awards` | the contract step |
| the field validators (amount `≥ 0`, `end ≥ begin`, the comment caps) | `sam/schemas/forms/xras.py` | composed into one `ActionRequestForm` |
| the field macros and the modal shell | templates | as-is |
| `patch_requests_index`, `request_index_entry` | the sweep cache | the status readout; the row is keyed by `request_number == projcode`, so a per-project read is a scan |

What neither side has is a **multi-step flow**. "Request a supplement" is add-action
→ resources → dates → user comments → validate → submit: five existing service calls
with no state between them, against a service with no transactions — a failure at
step three leaves an `Incomplete` action in XRAS. The editor's own Add-action modal
has the same gap; it creates a bare action and stops.

### 3.4 Decisions taken (2026-09-14)

- **SAM keeps the metadata; XRAS carries the key.** The request only has to echo an
  identifier SAM already holds; the resolved state lives in SAM, keyed by it (§ 4.2).
  Nothing precious — no projcode, no GID — is consumed before approval.
- **Routing** is the XRAS-family predicate plus a policy keyed by SAM allocation type,
  resolved by name at runtime. A control that cannot route is **disabled with its
  reason, never hidden** (§ 4.4).
- **Limits** are a hard maximum and a soft default per allocation type per resource,
  in a SAM table with an admin card, because XRAS's `numbers[]` slot is empty at NCAR
  and ARC enforces nothing (§ 4.5).
- **One flow layer, two doors.** The PI form and the admin editor share the client,
  the ops, the audit table, the validators, the macros, the cache patch and the
  preflight — not the operator modal or its templates (§ 4.3).
- **A separate lever, `XRAS_SUBMIT_ENABLED`**, distinct from `XRAS_WRITE_ENABLED`
  because authoring a request is a different blast radius from correcting one:
  webapp-only, never the tasks CronJob, asserted per manifest like the write lever,
  and rendering the control disabled with its reason when off.
- **Most XRAS-side asks are settings we own.** Ben holds the admin role on
  `admin-ncar.xras.org`:

| Setting | Where | Use |
|---|---|---|
| Opportunity questions (`opportunityQA`) | Opportunities → *Submission Questions* → "Add a Question": Simple String, Multiple Strings, Numeric Range, Yes/No, Date; Text Field, Text Area, Calendar, Drop Down, Integer Only; active flag | a SAM-reference field is possible, but a question on an opportunity is rendered to every ARC submitter too, so it stays a soft dependency (§ 4.2) and is not added until phase 2 needs it |
| Default resource amounts | Allocation Types → *Available Resources* → Default Resource Amounts | the ARC form's pre-fill; SAM's § 4.5 default should match it |
| Rule book | Allocation Types → *Allocation Type Rules*: Required Submission Fields, Required/Optional Documents, Maximum Requests per type, action time periods, ineligible lead statuses | the same rules `required_fields` and `required_documents_status` report; edit here, read there |
| Available units per opportunity | Opportunities → *Available Resource Numbers* | the pool, not a per-request bound; leave blank |
| Notifications | XRAS mails the submitter (to the XRAS person's address) and the `alloc@` staff list on submit, and again at the operator's "notify" step after the post; both are XRAS-side settings | once phase 1 ships, switching them off makes SAM's `xras_*` notices the only mail — an optional coordination step (§ 5), not a prerequisite |

## 4. The design

### 4.1 The form: one to two pages, driven by the rule book

ARC's six pages are each simple, which is the problem: the work is spread across six
saves and the guidance across none of them. SAM's form is **one page for an action on
an existing project and at most two for a New or Renewal**, and it is **generated
from the rule book, not hand-coded per page**. `required_fields` and
`required_documents_status` already name every rule per allocation type × action type
(probes doc § 5), and the opportunity's `rules{}` names the resources. A section is a
reveal, not a page: choosing the action or the opportunity uncovers exactly the fields
that choice requires, and nothing else.

| Action | The page asks for | What drives the reveal |
|---|---|---|
| Extension | the new end date (a real date input, ISO, no earlier than the current end) and comments | rule book: end date and comments required; no amounts, no document |
| Supplement | an amount per resource, pre-filled with the default and bounded by § 4.5, and comments | rule book: nothing required; the limits table |
| Renewal | `/renew` copies roster, title, keywords and field of science; the form asks for the abstract, grants on Large and Small, and the Main Document or Progress Report the document rule names | the rule book and the document rules per allocation type |
| New, page 1 — *Describe* | the opportunity (everything below keys on it); title, abstract, keywords; field of science; PI = `current_user`, pre-filled, with an optional admin; affiliation → the mnemonic suggestor, fail-visible when the `user_institution` row is end-dated; the award picker, revealed when the opportunity's rule book wants grants | the opportunity's `rules{}`, `required_fields` |
| New, page 2 — *Request* | resources = `rules.resourceIdsAvailableForNewRequest` ∩ mapped, each showing its default, minimum and maximum; the EUA checkbox when the opportunity carries the attribute; the document upload when a document rule names the pair; the preflight readout; submit | `rules{}`, § 4.5, `opportunity_attributes`, `required_documents_status` |

What this fixes, against the baseline of § 3.2: the lead is pre-filled and the role
vocabulary is XRAS's own; amounts are defaulted and bounded with the limit shown; the
date input is a date input; a required field says so inline instead of moving focus;
dates and grants are asked only where the rule book asks for them. Mechanics: htmx
reveals per selection, an `HtmxFormHandler` subclass (the tier-3 handler in
`CLAUDE.md` § 9) with inline field errors, and XRAS's own `validate` before submit so
the reveal logic is never the only guard.

**This section is unbuilt and unprototyped.** The one-versus-two-page split for New
is a judgment call to revisit against a mockup; the rule-book-driven reveal is the
part to keep whichever way that goes.

### 4.2 The record: `xras_submission`, keyed on `request_id`

Two identifiers arrive on every `/actions` payload; they behave differently, and the
difference is measured:

| | `requestNumber` | `requestId` |
|---|---|---|
| At `POST /v1/requests` | **`null`** — nothing is minted at create | XRAS assigns; returned in the create response with `rules{}` |
| At submit | **minted** (`NCAR4352` appeared on the first `POST …/submit`) | unchanged |
| At handoff | **rewritten in place to the projcode**: `UPSU0087` resolves under the projcode and `NCAR4277` stops resolving | **unchanged** — `1445869` before and after |
| Across a renewal | stable — the family key | a renewal spawns a **new** line/id |

The rewrite runs on XRAS's own clock: hours for `UPUR0036`, a day and counting for
`NCAR4212` → `NRAL0056`, and W9 rules out both admin steps — the family still
answered only to `NCAR4354` 17 min after the post and 11 min after the notify, with
both keys re-read within seconds of each step and every five minutes after, and the
award letter XRAS mails at the notify names `NCAR4354`. So the SAM-side record keys
on **`request_id`** for the create → approval window — the only key that exists
between create and submit — and carries `request_number` as the family key it
becomes. This is the opposite of `xras_request_override`, keyed on `request_number`
because it lives *after* the rewrite; both are right for their window.

Modeled on `XrasRequestOverride` (`sam/integration/xras.py`): `SessionMixin`, state
transitions as methods, a module-level lookup primitive.

| Column group | Columns |
|---|---|
| Keys | `request_id` (PK for the pending window), `action_id`, `request_number` (family key, updated when observed), `project_id` (existing-project actions from the start; a `New` once applied) |
| Resolved SAM state | `user_id` (PI), `admin_user_id`, `facility_id`, `mnemonic_code_id`, `allocation_type_id`, `contract_id` (nullable — link-only), `opportunity_id`, `registration_id` (nullable; `ACCOUNT_REGISTRATION.md` § 4) |
| Lifecycle | `state` ∈ `draft · created · populated · validated · submitted · under_review · approved · applied · aborted · rejected`; `created_by` (who clicked), `xa_user` (who SAM impersonated — the same person for a PI, a PI for an operator), timestamps |
| Evidence | `payload` (what we sent), `xras_snapshot` (the create response, `rules{}` included), the `xras_remediation_event` ids of each step |

XRAS's own statuses (§ 3.1) map onto `state`, which adds the SAM-side steps. A
rejected or aborted submission costs one row.

**The XRAS-side echo is a soft dependency, not a need.** Attribute sets are
ACCESS-configured per opportunity and written with
`PUT …/actions/<aid>/opportunity_attributes` (JSON body, replace-all). Their answers
come back on the push as `opportunityQA` — non-empty on **76 of 76** production `New`
rows and empty on every Extension and Supplement — and SAM drops the field
(`unknown = EXCLUDE`, `sam/schemas/forms/xras.py`). Five of the seven open
opportunities carry the NWSC End User Agreement acknowledgment (a `yes_no` set, one
`opportunityAttributeId` per opportunity; Data Analysis adds a textarea; the two
Fall-2026 opportunities carry none), and it stores **no value**: `attributeValue:
null` on every production payload and on the one the probe wrote, so the row's
presence is the answer. A `simpleString` "SAM reference" attribute would round-trip
and be visible to reviewers, but the design must not need it. `userComments` also
round-trips and is the zero-ask fallback.

### 4.3 The flow layer: one implementation, two doors

```
webapp/dashboards/user/…                webapp/dashboards/allocations/xras/…
  PI request form (§ 4.1)                 admin Add-action modal
  require_project_permission              MANAGE_XRAS
  + holds a role on the line              + impersonates the PI
  xa_user = current_user                  xa_user = PI, created_by = operator
            \                                       /
             sam/manage/xras_submission.py        (new)
               request_action(session_factory, *, line, action_type, resources,
                              dates, comments, actor, xa_user, submit=True)
               each step = an existing xras_remediation op through _editor_op
               state in the xras_submission row; abort = soft-delete the action;
               resume = continue from state
                                |
             sam/manage/xras_remediation.py       (the ops, unchanged)
             sam/integration/xras_api/admin_client.py  (+ json= in _write)
             sam/integration/xras_api/vocabulary.py    (+ action types, stages, statuses)
```

The pieces shared are the § 3.3 table. **Deliberately not shared:** the operator
detail modal (the stage matrix); `_impersonation()` and `_editor_target()`;
`_detail_context`'s operator keys (overrides, role options, admin flags); the
per-editor form templates, each bound to the audit modal's swap target; and the copy
("remediation", "operator", "the card may lag until the sweep").

The admin modal is re-pointed at the flow so "file a supplement on behalf of the PI"
is the same code path with a different door — the editor gains a populated action
where it had a bare one. `xras_remediation` is by then a misnomer; rename it later,
behind a shim, not now. An **ownership predicate**, in one place, answers "does this
user hold a role on this line" from the roster the client already returns.

### 4.4 Routing: which projects may request through XRAS

Most SAM projects came through XRAS; some did not, and an extension for one of those
is handled in SAM, never funneled through XRAS. SAM records no provenance — `project`
has no request column, `xras_action_log` sees only pushes since the 2026-08-24
repoint, and the legacy fingerprint (`allocation_transaction.user_id IS NULL` with an
`XRAS Extension Request` comment: 1,715 projects on the 2026-09 snapshot, 677 of them
active) is recall, not proof. The authoritative predicate is the read the submit path
needs anyway:

> **`get_request_family_by_number(projcode)` returns a line that is not `isDeleted`.**

That is exactly the condition under which XRAS accepts an action on the family, and
it holds for legacy-era projects because the handoff rewrites `requestNumber` to the
projcode and the family stays under it. It answers `[]` for `SCSG0001` and two lines
for `UCUB0182`. A projcode minted in SAM that later received a `New`-on-existing
(`select_service` in `sam/xras/dispatch.py` routes that to `update`) has a family and
qualifies — correct, since XRAS can review actions on it. A family whose every line is
deleted does not.

On top of the predicate sits a small **policy**, keyed by SAM allocation type and
resolved by name at runtime, because having a family is necessary but not sufficient:

| Route | Allocation types | What the control renders |
|---|---|---|
| `xras` | CHAP, Small, Small (No NSF award), Data, Classroom, NSC, External Project | the request form |
| `email:<address>` | WRAP — extensions are a Wyoming committee decision, requested by mail to `wrap@uwyo.edu` | a mailto with the address and the project pre-filled; flip the row to `xras` when WNA opts in |
| `staff` | Divisional, Staff, the reserves | "handled by CISL staff" — divisional trees are renewed by staff in bulk and extended by an operator click |

The five expiration and activation mail templates tell leads three different things
about how to ask for more time; they converge on this table, and the
`xras_activation` mail's promise that a lead can "request extensions or supplements
through the SAM portal" is kept by phase 1.

A second gate on the same click: the SAM lead or admin must also hold a role on the
XRAS line, because every request-scoped write authorizes on `XA-USER`
(`XRAS_WRITE_PROBES.md` § 4.1). Roster drift between SAM and XRAS is refused with a
named reason, never worked around by impersonating someone else. A request never
mutates an allocation, so `require_project_permission` (lead, admin, steward override)
is the right decorator; the policy that Extend and Renew themselves are not
lead-available (`webapp/api/access_control.py`) stands.

### 4.5 Limits per allocation type per resource

The ARC forms bound nothing, and users on their first project are not calibrated to
the scope of the resources. SAM already carries one number for this,
`allocation_type.default_allocation_amount` — a single default per type (Small
50,000; Divisional 100,000; CHAP and NSC 0), read by no form. The limit is that idea
made per resource, with a floor and a ceiling.

`allocation_type_resource_limit`: `allocation_type_id`, `resource_id`, `min_amount`,
`default_amount`, `max_amount` (in the resource's own unit), `active`, `created_by`,
timestamps; `SessionMixin` + `ActiveFlagMixin`, `set()`/`clear()`, a lookup primitive
that returns `None` for an unconfigured pair. Rows name types and resources; ids are
resolved when read.

| | |
|---|---|
| **Hard maximum, soft default** | the form pre-fills `default_amount`, refuses an amount above `max_amount` or below `min_amount`, and the server-side preflight repeats the check so the form is not the only guard |
| **Applies to** | New, Supplement (the increment), Renewal. Extension carries no amount |
| **Does not apply to** | the admin request editor — an operator correcting a requested amount is not the case the limit exists for |
| **Unconfigured pair** | no limit; the form shows the resource without a default and says so |
| **Where it is edited** | Admin → Configuration, a card in the shape of Notification Addressing |

XRAS carries the same number types on the wire — each opportunity resource has
`numbers[]` with `source: allocationType` and types Default, Minimum and Maximum — but
the admin app exposes no editor for a maximum: an allocation type's "Available
Resources" tab offers **Default Resource Amounts** only, an opportunity's "Available
Resource Numbers" page writes the `Available Units` pool, and the resource page has
properties and submission questions. NCAR's data agrees (two decade-old defaults and
Derecho dollar values, never a maximum). ARC publishes the policy limits as text on
its opportunities page and enforces nothing. So SAM's table is the only enforcement,
and its seed values are ARC's published policy: Small — Derecho 1,000,000 core-hours,
Derecho GPU 2,500 GPU-hours; Exploratory and Classroom — 500,000 and 1,500; Large —
none; Data Analysis — Casper only; the same for initial and supplement.

### 4.6 The incoming side: consult points and the idempotency guard

Three changes to the handoff path, each small, in the order they land:

1. **The idempotency guard, first.** `actionId` is enforced by nothing today and four
   of the six handlers double-apply on a re-post (`XRAS_INGEST_IMPROVEMENTS.md` § 3).
   Park a post whose `action_id` has a prior `processed` sibling as `manual`, naming
   the earlier row. A prerequisite of phase 2 with value of its own.
2. **A `requestId` fallback in `select_service`** (`sam/xras/dispatch.py`). W9 posted
   an Extension 17 minutes after its New and it arrived as `requestNumber: NCAR4354`
   — the rewrite had not run — so no service matched and the row parked. The fallback
   is one lookup: no project under `requestNumber`, but a `processed` `New` row in
   `xras_action_log` with the same `requestId` → route to that row's
   `projcode_result`. It needs no new table and covers ARC-originated families; the
   `xras_submission` row is the same lookup for SAM-authored ones. Belongs with the
   guard, in `XRAS_INGEST_IMPROVEMENTS.md`.
3. **The `New` handler's consult point** (phase 2): the top of `NewHandler.assemble`
   (`sam/xras/handlers/new.py`), keyed on the payload's `requestId`, pre-setting the
   mnemonic, allocation type, contracts and area of interest from the stored FKs with
   the ladder as fallback — three branches in the shape of the two
   `lookup_request_override` branches, or one `_consult_submission()` first
   statement, either way before the panel check so the 422 ordering the tests pin is
   untouched. `NewHandler.execute` stamps `applied` and `project_id` after
   `Project.create`.

## 5. Gaps and open questions

| Gap | Why it matters | Next step |
|---|---|---|
| **The form has no prototype** (§ 4.1) | the one-vs-two-page split and the reveal rules are design, not measurement | mock it before the phase-2 plumbing; keep the rule-book-driven reveal |
| **No clean test loop** | the verbs were proved on production XRAS with requests that never pass `Submitted`, which the sweep never sees. Proving the handoff (`opportunityQA` and the `requestId` echo arriving on `POST /api/xras/v1/actions`, the `New` handler consulting the submission row, the rewrite) needs an approval, and an approval on production XRAS posts to production SAM | the samuel-dev ↔ XRAS test instance loop below; the ask first, since it costs a conversation. Until it exists, anything that needs an approval is labeled unproven |
| **The rewrite's mechanism and cadence** | if it is a periodic job, its cadence bounds how soon after a New an action on the family can be filed from ARC without parking | ask Steve (W9 rules out the post and the notify); the § 4.6 fallback is needed regardless |
| **`GET /v1/projects`** | proxied to the accounting service as `/api/xras/v1/users/projects/<username>`, which neither legacy SAM nor this one serves | ask Steve whether anything in the NCAR process calls it |
| **Write client gaps** | no JSON-body path in `_write`; person-create, opportunity attributes and documents unbuilt | phase 0 |
| **`POST /v1/people/<u>`** | phase 3's placeholder person is an unprobed verb | probe when phase 3 is next |
| **Guard and fallback not landed** | § 4.6 items 1 and 2 | `XRAS_INGEST_IMPROVEMENTS.md` |
| **Extension 399374 on `UHSS0001`** | approved and posted, parked as #184, not notified | Repost to Accounting Service once the rewrite lands, or once the fallback does |
| **WRAP** | stays `email:wrap@uwyo.edu` until WNA opts in | flip the policy row |
| **XRAS's own mail** | submit and notify mails are XRAS-side settings (§ 3.4); once phase 1 ships, SAM's `xras_*` notices can be the only mail | optional coordination, not a prerequisite |

**The clean loop is samuel-dev ↔ the XRAS test instance.** SAM has the
internet-reachable development deployment (`https://samuel-dev.k8s.ucar.edu`,
`K8S_DEV_ENVIRONMENT.md`), and Steve offered "a test instance of xras_admin … against
your new accounting service" on 2026-08-11 (`docs/xras/incoming/XRAS_CUTOVER_RUNBOOK.md`).
Priced from the chart:

| Side | Change |
|---|---|
| Ask to Steve | the test instance's API base URL and allocations-process name; a key with the same `submit` + `report` grant; the test `xras_admin` pointed at `https://samuel-dev.k8s.ucar.edu/api/xras/v1` |
| Outbound (samuel-dev → test XRAS) | `helm/values-dev.yaml`: `XRAS_API_BASE`, `XRAS_ALLOCATIONS_PROCESS`, `XRAS_OUTGOING_ENABLED=1`, `XRAS_WRITE_ENABLED=1`, `xrasApiCredentials.enabled=true` with its own OpenBao path (`csg/xras-dev-api-key`). `helm/tests/test-dev-render.sh` asserts the levers off and the key absent, and rejects turning them on; those assertions become "not production XRAS" (base URL and secret path differ from `helm/values.yaml`). `scripts/deploy_dev.sh` refuses to deploy unless `XRAS_ACTIONS_CAPTURE_ONLY` renders `1`; `scripts/lib/cirrus_common.sh` sets `XRAS_ES_EXPECTED=0` for dev. All four follow |
| Inbound (test XRAS → samuel-dev) | nothing in the chart pins a caller. `sam_dev` ships `api_credentials` **empty** (`containers/sam-sql-dev/config.yaml`), so a `ROLE_XRAS` row must be seeded and survive `make refresh-dev` (`scripts/gen_api_key.py --username samuel --sql` emits it; `scripts/xras/seed_dev_actions.py` is host-guarded to localhost); `XRAS_ACTIONS_CAPTURE_ONLY=0` |

The order is the ask, then the chart change, then the seeding.

## 6. References

| | |
|---|---|
| [`XRAS_SUBMISSION_PROBES.md`](../xras/outgoing/XRAS_SUBMISSION_PROBES.md) | the 2026-09-14 probe record: every verb in § 3.1, the NCAR vocabularies, the rule-book tables, the final state left in XRAS |
| [`XRAS_ARC_BASELINE.md`](../xras/outgoing/XRAS_ARC_BASELINE.md) | W9, 2026-09-15: the ARC form page by page, one real New and Extension seen from ARC, the admin app, the API, SAM and mail; the measurements § 3.2 and § 4.2 cite |
| [`ACCOUNT_REGISTRATION.md`](ACCOUNT_REGISTRATION.md) | the identity step of phase 3, as a product of its own |
| [`REQUEST_EDITOR.md`](../xras/outgoing/REQUEST_EDITOR.md) | the write client, tiers, levers and stage model the flow layer extends |
| [`XRAS_WRITE_PROBES.md`](../xras/outgoing/XRAS_WRITE_PROBES.md) | probe methodology, the one authorization rule, the privilege register |
| [`XRAS_OUTGOING_QUERIES.md`](../xras/outgoing/XRAS_OUTGOING_QUERIES.md) | the readable surface and the request payload shape |
| [`PROJECT_AND_ACCOUNT_LIFECYCLE.md`](../xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md) | where a project comes from; SAM never creates users |
| [`XRAS_TRIAGE_PLAYBOOK.md`](../xras/incoming/XRAS_TRIAGE_PLAYBOOK.md) | the 422 catalog § 1 answers |
| [`XRAS_PUSH_READINESS.md`](implemented/XRAS_PUSH_READINESS.md) | the preflight engine reused as the pre-submit check |
| [`XRAS_INGEST_IMPROVEMENTS.md`](XRAS_INGEST_IMPROVEMENTS.md) | where the idempotency guard and the `select_service` fallback of § 4.6 land |
| [`XRAS_ACCOUNT_QUEUE.md`](XRAS_ACCOUNT_QUEUE.md) | the Pending Users queue the registration product generalizes |
| `https://api.xras.org/apidoc.html` | the index (86 endpoints; the same URL without `.html` is a 404 decoy); detail pages are static under `https://api.xras.org/apidoc/1.0/`, plain `curl`, and their examples are XSEDE's, so every id is read live for NCAR |
