# Authoring XRAS requests from SAM — closing the loop

**Status: design, not built; the authoring surface is measured.** A project's
allocation life begins at ARC (`arc.ucar.edu/xras_submit`), is reviewed in XRAS, and
reaches SAM at approval (`docs/xras/incoming/`). SAM writes to XRAS only as remediation
(`docs/xras/outgoing/REQUEST_EDITOR.md`). This document designs the third arc —
**SAM → XRAS (review) → SAM (apply)** — in three phases: actions on existing projects
from the project lead's own dashboard, new requests by logged-in users with sensible
per-resource limits, and requests by people who do not yet have an account. Every
claim about an XRAS verb below was driven live on 2026-09-14 and is recorded, with
ids, in [`XRAS_SUBMISSION_PROBES.md`](../xras/outgoing/XRAS_SUBMISSION_PROBES.md);
the traffic figures are from the production `xras_action_log` on 2026-09-10. The
identity step of phase 3 is its own product, [`ACCOUNT_REGISTRATION.md`](ACCOUNT_REGISTRATION.md).

---

## 1. Why author what we later process

Every `New` handoff is resolved **blind**, at approval time, by a handler that has
only the wire payload: PI username → `User`, PI affiliation → mnemonic (an
eleven-strategy ladder in `sam/xras/extractors.py`), `opportunityId` → allocation type
→ facility, grant number → contract, resource key → resource. The triage playbook's
422 catalog is the list of ways that goes wrong, and every fix ends with "ask ACCESS
to push the button again".

If SAM authors the request, each of those resolutions happens **while the submitter
is in front of us**, weeks before approval, and is *stored*:

| Incoming failure class (`XRAS_TRIAGE_PLAYBOOK.md`) | With a SAM-authored request |
|---|---|
| Unreconciled ARC placeholder PI — 55% of `New` failures | PI is `current_user`. `XA-USER` must resolve at the allocations process anyway (XRAS proxies our `/api/xras/v1/people`), so the identity is reconciled by construction; phase 3 keeps the property with a placeholder SAM itself mints and merges |
| Mnemonic unresolvable, or valid-but-wrong — 24% | Resolved with the submitter present, using the suggestor (`sam/queries/mnemonic_console.py`); the `mnemonic_code_id` is stored, **not minted** |
| WNA `Small` minted in the UNIV series, silently | Facility and `allocation_type_id` are explicit form choices, stored |
| "Cannot find contract" | Award picked through the awards search (`sam-search awards`); `contract_id` stored |
| Unmapped resource key | The form offers the opportunity's `rules.resourceIdsAvailableForNewRequest` ∩ `xras_resource_repository_key_resource` only |
| Requested amounts uncalibrated to the resource | The form pre-fills a per-allocation-type, per-resource default and refuses amounts outside the configured range (§ 5) — the ARC forms impose no such bound |
| Handoff fails → re-push | The pre-submit preflight is the **existing** `sam/xras/preflight.py`: `synthesize_action` → `dispatch_action(validate_only=True)` in a SAVEPOINT. Feed it the draft instead of a `reports/requests` row |

The handler's job on approval shrinks from *resolve* to *look up and apply*.
ARC-originated requests keep the ladder — the lookup is a short-circuit with a
fallback, the same shape as `lookup_request_override` at the top of
`resolve_mnemonic_code` and in `plan_contracts`.

## 2. SAM keeps the metadata; XRAS carries the key

The instinct is to push metadata *into* XRAS so it comes back on the handoff. The
better design is the inverse: the request only has to echo an identifier SAM already
holds, and the resolved state lives in SAM, keyed by it. Two identifiers arrive on
every `/actions` payload; they behave differently, and the difference is measured:

| | `requestNumber` | `requestId` |
|---|---|---|
| At `POST /v1/requests` | **`null`** — nothing is minted at create | XRAS assigns; returned in the create response with `rules{}` |
| At submit | **minted** (`NCAR4352` appeared on the first `POST …/submit`) | unchanged |
| At handoff | **rewritten in place to the projcode**: `UPSU0087` resolves under the projcode and `NCAR4277` stops resolving | **unchanged** — `1445869` before and after |
| Promptness of the rewrite | hours for `UPUR0036`; a day and counting for `NCAR4212` → `NRAL0056`. A manual step is the likely reading (§ 9 Q1) | n/a |
| Across a renewal | stable — the family key | a renewal spawns a **new** line/id |

So the SAM-side record keys on **`request_id`** for the create → approval window —
the only key that exists between create and submit — and carries `request_number` as
the family key it becomes. This is the opposite of `xras_request_override`, keyed on
`request_number` because it lives *after* the rewrite; both are right for their window.

### 2.1 `xras_submission` — the record

Modeled on `XrasRequestOverride` (`sam/integration/xras.py`): `SessionMixin`, state
transitions as methods, a module-level lookup primitive.

| Column group | Columns |
|---|---|
| Keys | `request_id` (PK for the pending window), `action_id`, `request_number` (family key, updated when observed), `project_id` (existing-project actions from the start; a `New` once applied) |
| Resolved SAM state | `user_id` (PI), `admin_user_id`, `facility_id`, `mnemonic_code_id`, `allocation_type_id`, `contract_id` (nullable — link-only), `opportunity_id`, `registration_id` (nullable; `ACCOUNT_REGISTRATION.md` § 4) |
| Lifecycle | `state` ∈ `draft · created · populated · validated · submitted · under_review · approved · applied · aborted · rejected`; `created_by` (who clicked), `xa_user` (who SAM impersonated — the same person for a PI, a PI for an operator), timestamps |
| Evidence | `payload` (what we sent), `xras_snapshot` (the create response, `rules{}` included — the only `rules{}` read our key gets, since `GET /v1/requests/<rid>` is 401), the `xras_remediation_event` ids of each step |

XRAS's own vocabulary is `Incomplete · Submitted · Under Review · Approved ·
Rejected` for the request and `Declined` for a rejected action; `state` maps onto it
and adds the SAM-side steps.

Consult point: the top of `NewHandler.assemble` (`sam/xras/handlers/new.py`), keyed
on the payload's `requestId`, pre-setting the mnemonic, allocation type, contracts
and area of interest from the stored FKs with the ladder as fallback — three branches
in the shape of the two `lookup_request_override` branches, or one
`_consult_submission()` first statement, either way before the panel check so the
422 ordering the tests pin is untouched. `NewHandler.execute` stamps `applied` and
`project_id` after `Project.create`. Nothing precious — no projcode, no GID — is
consumed before approval; a rejected or aborted submission costs one row.

That row is also the idempotency key the ingest path lacks: `actionId` is enforced by
nothing today and four of the six handlers double-apply on a re-post
(`XRAS_INGEST_IMPROVEMENTS.md` § 3). The guard — park a post whose `action_id` has a
prior `processed` sibling as `manual`, naming the earlier row — is a prerequisite of
phase 2 with value of its own, and lands first.

**The XRAS-side echo.** Attribute sets are ACCESS-configured per opportunity and
written with `PUT …/actions/<aid>/opportunity_attributes` (JSON body, replace-all).
Their answers come back on the push as `opportunityQA` — non-empty on **76 of 76**
production `New` rows and empty on every Extension and Supplement — and SAM drops
the field (`unknown = EXCLUDE`, `sam/schemas/forms/xras.py`). Five of the seven open
opportunities carry the NWSC End User Agreement acknowledgment (a `yes_no` set, one
`opportunityAttributeId` per opportunity; Data Analysis adds a textarea; the two
Fall-2026 opportunities carry none). The acknowledgment stores **no value**:
`attributeValue: null` on every production payload and on the one the probe wrote,
so the row's presence is the answer and there is nothing to encode. A `simpleString`
"SAM reference" attribute, if ACCESS adds one, is therefore a measured round-trip and
visible to reviewers; it is a soft dependency the design must not need.
`userComments` also round-trips and is the zero-ask fallback.

## 3. The authoring surface, measured

All under our key's `submit` context, impersonating the PI; `review`/`admin` are
401 for every identity, so only the *Requested* stage is ours. Reads for
verify-by-reread go through the `report` context, the dual-context arrangement
`XrasAdminClient` already uses.

| Step | Endpoint | Measured behavior |
|---|---|---|
| Create | `POST /v1/requests?opportunityId&requestType[&requestNumber&grantTypeId]` | the full object **with `rules{}`**, status `Incomplete`, **the first action already minted**, `requestNumber` **null**, and the creator as **Allocation Manager, not PI** |
| PI | `POST /v1/requests/<rid>/roles/PI/<username>` | required; one person may hold PI and Allocation Manager. Validate and submit must run as the PI — the same call passes as the PI and fails as the Allocation Manager |
| Text | `PUT /v1/requests/<rid>/attributes` | `title shortTitle abstract keywords isSupportedByGrants` |
| Action | `POST /v1/requests/<rid>/actions?actionType[&userComments]` → `{actionId}` | accepted on `Approved`, `Incomplete` and `Submitted` lines. `DELETE …/actions/<aid>` is a **soft delete**: the row stays with `isDeleted: true`, the line's own `isDeleted` is derived from its actions, and a deleted action can hold the family's highest `actionId` — anything that walks `actions[]` must filter it |
| Renew | `POST /v1/requests/<rid>/renew?opportunityId` | a new line with `rules{}`; **copies roster, title, keywords and field of science**, not abstract, grants, resources, dates or documents. Create-with-`requestNumber` also spawns a line but copies nothing — use `/renew` |
| Resources, dates | `PUT …/actions/<aid>/resources/<resourceId>?amount`; `POST …/allocation_dates?beginDate&endDate` → `{allocationDateId}` | query params; the resource id is the resource *type* id |
| Field of science, grants, publications | `PUT …/fos/<fosTypeId>?isPrimary`; `POST …/grants`; `POST …/publications` (JSON) | fos measured; grants and publications authorized in the editor spike, unbuilt |
| Opportunity attributes | `PUT …/opportunity_attributes` — **JSON body** | the EUA row (§ 2) |
| Documents | `POST …/documents` — **JSON with a base64 body**; `GET …/required_documents_status` | XRAS parses the bytes (a hand-written PDF is a 400, a rendered one is a 200 with `documentId`); the status endpoint names exactly the unmet rule. No multipart transport is needed |
| Rule book | `GET /v1/allocation_types/<at>/action_types/<act>/required_fields` | `<at>` is `opportunities[].allocationTypeInfo.allocationTypeId`; tables in the probes doc § 5 |
| Preflight, submit | `GET …/validate`; `POST …/submit` | validate names exactly the unmet rules; a first submit lands **`Submitted`** (a re-submit lands `Under Review`) and mints the number; the submit body is `null` |

The write client (`sam/integration/xras_api/admin_client.py`) carries every verb
above except person-create, opportunity attributes and documents; the last two need
a JSON-body path in `_write`, which is params-only. Every step is single-attempt with
verify-by-reread (`XRAS_WRITE_PROBES.md` § 4.4: a 200 proves nothing), and the audit
row is committed **before** the write leaves (`sam/manage/xras_remediation.py`,
`_editor_op`). The create response is captured because it is the only `rules{}` read
available, and the Requested stage is the only stage a SAM submission can populate —
two entries for the privilege register in `XRAS_WRITE_PROBES.md` § 7.

**Vocabulary is process-scoped**; the apidoc's examples are XSEDE's. NCAR request
types are New and Renewal only; action types carry ids (Supplement 500020, Extension
500017, Renewal 500021, New 500019, Adjustment 500168, Date Adjustment 500334);
allocation-date stages are Requested 1, Suggested 3, Approved 2. These, the stage
map and the status strings belong in `sam/integration/xras_api/vocabulary.py`, one
module, beside the role types.

**Required fields and documents.** The rule book is exact: a Supplement requires
**nothing**; an Extension requires an end date and user comments; a Renewal requires
title and abstract, and grants on Large and Small. Documents are a separate rule: a
Main Document for New and Renewal on Large and NSC, a Progress Report for Renewal on
Small and Educational, an Advisor Letter on Exploratory — and **no document rule names
Supplement or Extension**. Over 400 approved requests the corpus agrees (Extension
0/28 carry one). That line is what phases 1 and 1b are cut along.

## 4. Routing: which projects may request through XRAS

Most SAM projects came through XRAS; some did not, and an extension for one of those
is handled in SAM, never funneled through XRAS. SAM records no provenance — `project`
has no request column, `xras_action_log` sees only pushes since the 2026-08-24
repoint, and the legacy fingerprint (`allocation_transaction.user_id IS NULL` with an
`XRAS Extension Request` comment: 1,715 projects on the 2026-09 snapshot, 677 of them
active) is recall, not proof. The authoritative predicate is the read the submit
path needs anyway:

> **`get_request_family_by_number(projcode)` returns a line that is not `isDeleted`.**

That is exactly the condition under which XRAS accepts an action on the family, and
it holds for legacy-era projects because the handoff rewrites `requestNumber` to the
projcode and the family stays under it. It answers `[]` for `SCSG0001` and two lines
for `UCUB0182`. A projcode minted in SAM that later received a `New`-on-existing
(`select_service` in `sam/xras/dispatch.py` routes that to `update`) has a family and
qualifies — correct, since XRAS can review actions on it. A family whose every line is
deleted does not.

On top of the predicate sits a small **policy**, keyed by SAM allocation type and
resolved by name at runtime, because having a family is necessary but not
sufficient:

| Route | Allocation types | What the control renders |
|---|---|---|
| `xras` | CHAP, Small, Small (No NSF award), Data, Classroom, NSC, External Project | the request form |
| `email:<address>` | WRAP — extensions are a Wyoming committee decision, requested by mail to `wrap@uwyo.edu` | a mailto with the address and the project pre-filled; flip the row to `xras` when WNA opts in |
| `staff` | Divisional, Staff, the reserves | "handled by CISL staff" — divisional trees are renewed by staff in bulk and extended by an operator click |

The control is **disabled with its reason**, never hidden. The five expiration and
activation mail templates already tell leads three different things about how to ask
for more time; they converge on this table, and the `xras_activation` mail's promise
that a lead can "request extensions or supplements through the SAM portal" is kept by
phase 1.

A second gate on the same click: the SAM lead or admin must also hold a role on the
XRAS line, because every request-scoped write authorizes on `XA-USER`
(`XRAS_WRITE_PROBES.md` § 4.1). Roster drift between SAM and XRAS is refused with a
named reason, never worked around by impersonating someone else. A request never
mutates an allocation, so `require_project_permission` (lead, admin, steward
override) is the right decorator; the policy that Extend and Renew themselves are not
lead-available (`webapp/api/access_control.py`) stands.

## 5. Limits per allocation type per resource

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

XRAS has a native slot for the same numbers — each opportunity resource carries
`numbers[]` with `source: allocationType` and types Default, Minimum and Maximum —
and NCAR populates it with two decade-old defaults and Derecho dollar values, never a
maximum. Mirroring our table into it is an ask to ACCESS (§ 9 Q7), not a dependency:
SAM enforces its own.

## 6. The flow layer: one implementation, two doors

The admin request editor and a user-facing submit form overlap almost entirely, and
the overlap is already in the right place. Below its routes the editor is
caller-agnostic: the write client takes `xa_user` and `context` per call; the fifteen
service functions in `sam/manage/xras_remediation.py` take `operator` and the PI as
parameters, open the audit row before the write and patch the sweep cache after; the
form schemas assert only the shape of a body; the field macros and the modal shell
are generic. The operator assumptions live in the twenty-eight routes and their
`MANAGE_XRAS`/`ADMIN_XRAS` decorators, in `_impersonation()` ("`xa_user` is somebody
else, the PI preferred"), in the operator-only keys of the detail modal's context,
and in the copy.

What neither side has is a **multi-step flow**. "Request a supplement" is add-action
→ resources → dates → user comments → validate → submit: five existing service calls
with no state between them, against a service with no transactions — a failure at
step three leaves an `Incomplete` action in XRAS. The editor's own Add-action modal
has the same gap; it creates a bare action and stops.

```
webapp/dashboards/user/…                webapp/dashboards/allocations/xras/…
  PI request form (composite)             admin Add-action modal
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

| Reused as-is | Deliberately not shared |
|---|---|
| the write client and its verify-by-reread | the operator detail modal (the stage matrix) |
| the service ops and `_editor_op` | `_impersonation()` and `_editor_target()` |
| `xras_remediation_event` — `created_by` and `xa_user` coincide for a PI, which the columns allow | `_detail_context`'s operator keys (overrides, role options, admin flags) |
| the field validators (amount `≥ 0`, `end ≥ begin`, the comment caps) composed into one `ActionRequestForm` | the per-editor form templates, each bound to the audit modal's swap target |
| the field macros and the modal shell | the copy ("remediation", "operator", "the card may lag until the sweep") |
| `patch_requests_index` and `request_index_entry` — the cache row is keyed by `request_number == projcode`, so a per-project read is a scan | |
| the preflight, fed a draft through a report-shaped shim | |

The admin modal is re-pointed at the flow so "file a supplement on behalf of the PI"
is the same code path with a different door — the editor gains a populated action
where it had a bare one. `xras_remediation` is by then a misnomer; rename it later,
behind a shim, not now.

Two small pieces of plumbing complete the picture. An **ownership predicate**, in one
place, answers "does this user hold a role on this line" from the roster the client
already returns. A **lever, `XRAS_SUBMIT_ENABLED`**, distinct from `XRAS_WRITE_ENABLED`
because authoring a request is a different blast radius from correcting one:
webapp-only, never the tasks CronJob, asserted per manifest like the write lever, and
rendering the control disabled with its reason when off.

## 7. Phases

| Phase | Content | Depends on |
|---|---|---|
| **0 — plumbing** | the JSON path in `_write`; vocabulary consolidation; the `xras_submission` table and the flow layer; the ownership predicate; the routing policy (§ 4); the limits table and its card (§ 5); `XRAS_SUBMIT_ENABLED`; the ingest idempotency guard; the ask to Steve (§ 8) | nothing external |
| **1 — existing projects** | Supplement and Extension from the project card for the lead or admin; the status readout beside it (the sweep cache entry for the projcode with `is_pending_work`, plus `get_recent_xras_actions` — `manual` and `failed` read as "being reviewed by CISL", and nothing that names a notified address); the admin Add-action modal on the flow; the routed answer for projects that cannot use XRAS | 0 |
| **1b — Renewal** | `/renew` on the primary line, then the New-shaped steps: abstract, grants on Large and Small, the Main Document or Progress Report the rule book requires through the base64 upload | 0, 1 |
| **2 — a new request by a logged-in user** | the wizard: affiliation (fail-visible when the `user_institution` row is end-dated — the case that minted `CGD` for a CU Boulder PI), mnemonic suggestor, awards picker, opportunity and resources bounded by the opportunity's `rules`, amounts bounded by § 5, the explicit PI role, the EUA row, documents where the rule book demands them, preflight, submit; the `New` handler's consult point | 0; the test instance for the approval leg |
| **3 — a request by someone without an account** | `ACCOUNT_REGISTRATION.md` phase 2 first. Then: the submission is created under an XRAS person SAM mints (`POST /v1/people/<u>` with `isReconciled = false` — a new verb, to be probed), the `xras_submission` row carries `registration_id`, and the account's arrival merges the placeholder into the real username with `merge_placeholder`, re-pointing the PI role | 2 in production; `ACCOUNT_REGISTRATION.md` phase 2 |

Phase 1 sends a PI's request into XRAS in the same shape ARC would, so the incoming
side is unchanged; phase 2 is the first that changes what arrives (the consult point)
and the first that needs an approval to prove.

## 8. Test posture and the clean loop

The verbs are measured on **small, reversible requests under `benkirk` against
production XRAS**, each paired with its inverse, the one submitted request rejected
by Ben in the admin app. That posture is right for a verb probe — the sweep windows
on `Approved`, so a request that never gets past `Submitted` is invisible to SAM — and
wrong for the next step. Proving the handoff (`opportunityQA` and the `requestId`
echo arriving on `POST /api/xras/v1/actions`, the `New` handler consulting the
submission row, the `requestNumber` rewrite) needs an approval, and an approval on
production XRAS posts to production SAM.

The clean loop is **samuel-dev ↔ the XRAS test instance**. SAM has the
internet-reachable development deployment (`https://samuel-dev.k8s.ucar.edu`,
`K8S_DEV_ENVIRONMENT.md`), and Steve offered "a test instance of xras_admin … against
your new accounting service" on 2026-08-11 (`docs/xras/incoming/XRAS_CUTOVER_RUNBOOK.md`).
Priced from the chart:

| Side | Change |
|---|---|
| Ask to Steve | the test instance's API base URL and allocations-process name; a key with the same `submit` + `report` grant; the test `xras_admin` pointed at `https://samuel-dev.k8s.ucar.edu/api/xras/v1` |
| Outbound (samuel-dev → test XRAS) | `helm/values-dev.yaml`: `XRAS_API_BASE`, `XRAS_ALLOCATIONS_PROCESS`, `XRAS_OUTGOING_ENABLED=1`, `XRAS_WRITE_ENABLED=1`, `xrasApiCredentials.enabled=true` with its own OpenBao path (`csg/xras-dev-api-key`). `helm/tests/test-dev-render.sh` asserts the levers off and the key absent, and rejects turning them on; those assertions become "not production XRAS" (base URL and secret path differ from `helm/values.yaml`). `scripts/deploy_dev.sh` refuses to deploy unless `XRAS_ACTIONS_CAPTURE_ONLY` renders `1`; `scripts/lib/cirrus_common.sh` sets `XRAS_ES_EXPECTED=0` for dev. All four follow |
| Inbound (test XRAS → samuel-dev) | nothing in the chart pins a caller. `sam_dev` ships `api_credentials` **empty** (`containers/sam-sql-dev/config.yaml`), so a `ROLE_XRAS` row must be seeded and survive `make refresh-dev` (`scripts/gen_api_key.py --username samuel --sql` emits it; `scripts/xras/seed_dev_actions.py` is host-guarded to localhost); `XRAS_ACTIONS_CAPTURE_ONLY=0` |

The order is the ask first — it costs a conversation — then the chart change, then
the seeding. Until it exists, anything that needs an approval is labeled unproven.

## 9. Questions for ACCESS and Steve

1. Who or what rewrites `requestNumber` to the projcode after handoff? It happened
   for `UPSU0087` before SAM echoed a projcode, and had not happened for
   `NCAR4212`/`NRAL0056` a day after. If XRAS reads `result.projcode` from our
   `/actions` reply, that is half the loop already.
2. Will ACCESS add a `simpleString` attribute to NCAR opportunities for a SAM
   reference, and is that per opportunity or process-wide?
3. Does an API-created request appear in ARC for the PI to see and edit, or only in
   the admin app? `NCAR4352` is the specimen — created and submitted entirely through
   the API.
4. The § 8 ask: test-instance base URL, process name, a key, and the test
   `xras_admin` pointed at samuel-dev.
5. `GET /v1/projects` is proxied to the accounting service as
   `/api/xras/v1/users/projects/<username>`, which neither legacy SAM nor this one
   serves. Does anything in the NCAR process call it?
6. Does XRAS Submit mail the submitter on submit and on decision? If so, SAM sends
   no "received" notice of its own.
7. Would ACCESS populate `numbers[].Maximum Amount` on NCAR opportunity resources from
   the § 5 table, so ARC-originated requests get the same bound?

## 10. References

| | |
|---|---|
| [`XRAS_SUBMISSION_PROBES.md`](../xras/outgoing/XRAS_SUBMISSION_PROBES.md) | the 2026-09-14 probe record: every verb in § 3, the NCAR vocabularies, the rule-book tables, the final state left in XRAS |
| [`ACCOUNT_REGISTRATION.md`](ACCOUNT_REGISTRATION.md) | the identity step of phase 3, as a product of its own |
| [`REQUEST_EDITOR.md`](../xras/outgoing/REQUEST_EDITOR.md) | the write client, tiers, levers and stage model the flow layer extends |
| [`XRAS_WRITE_PROBES.md`](../xras/outgoing/XRAS_WRITE_PROBES.md) | probe methodology, the one authorization rule, the privilege register |
| [`XRAS_OUTGOING_QUERIES.md`](../xras/outgoing/XRAS_OUTGOING_QUERIES.md) | the readable surface and the request payload shape |
| [`PROJECT_AND_ACCOUNT_LIFECYCLE.md`](../xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md) | where a project comes from; SAM never creates users |
| [`XRAS_TRIAGE_PLAYBOOK.md`](../xras/incoming/XRAS_TRIAGE_PLAYBOOK.md) | the 422 catalog § 1 answers |
| [`XRAS_PUSH_READINESS.md`](implemented/XRAS_PUSH_READINESS.md) | the preflight engine reused as the pre-submit check |
| [`XRAS_INGEST_IMPROVEMENTS.md`](XRAS_INGEST_IMPROVEMENTS.md) | the idempotency guard § 2.1 lands |
| [`XRAS_ACCOUNT_QUEUE.md`](XRAS_ACCOUNT_QUEUE.md) | the Pending Users queue the registration product generalizes |
| `https://api.xras.org/apidoc.html` | the index (86 endpoints; the same URL without `.html` is a 404 decoy); detail pages are static under `https://api.xras.org/apidoc/1.0/`, plain `curl`, and their examples are XSEDE's, so every id is read live for NCAR |
