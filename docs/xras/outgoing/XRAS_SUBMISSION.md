# Authoring XRAS requests from SAM — closing the loop

**Status: design, not built.** Today a project's allocation life begins at ARC
(`arc.ucar.edu/xras_submit`), is reviewed in XRAS, and reaches SAM only at
approval (`incoming/`). SAM writes back only as remediation (`REQUEST_EDITOR.md`).
This document is the case for the third arc — **SAM → XRAS (review) → SAM
(apply)** — and the shape it should take. Everything measured here was measured
on 2026-09-10 against production XRAS and the production `xras_action_log`
(17 days of live traffic since the 2026-08-24 repoint).

---

## 1. Why author what we later process

Every `New` handoff today is resolved **blind**, at approval time, by a handler
that has only the wire payload: PI username → `User`, PI affiliation → mnemonic
(an eleven-strategy ladder in `sam/xras/extractors.py`), `opportunityId` →
allocation type → facility, grant number → contract, resource key → resource.
The triage playbook's 422 catalog is the list of ways that goes wrong, and every
fix ends with "ask ACCESS to push the button again".

If SAM authors the request, each of those resolutions happens **while the
submitter is in front of us**, weeks before approval, and is *stored*:

| Incoming failure class (`XRAS_TRIAGE_PLAYBOOK.md`) | With a SAM-authored request |
|---|---|
| Unreconciled ARC placeholder PI — 55% of `New` failures | PI is `current_user`. `XA-USER` must resolve at the allocations process anyway (XRAS proxies our `/api/xras/v1/people`), so the identity is reconciled by construction |
| Mnemonic unresolvable, or valid-but-wrong — 24% | Resolved with the submitter present, using the suggestor (`sam/queries/mnemonic_console.py`); the `mnemonic_code_id` is stored, **not minted** |
| WNA `Small` minted in the UNIV series, silently | Facility and `allocation_type_id` are explicit form choices, stored |
| "Cannot find contract" | Award picked through the awards search (`sam-search awards`); `contract_id` stored |
| Unmapped resource key | The form offers `rules.resourceIdsAvailableForNewRequest` ∩ `xras_resource_repository_key_resource` only |
| Handoff fails → re-push | The pre-submit preflight is the **existing** `sam/xras/preflight.py`: `synthesize_action` → `dispatch_action(validate_only=True)` in a SAVEPOINT. Feed it the draft instead of a `reports/requests` row |

The handler's job on approval shrinks from *resolve* to *look up and apply*.
ARC-originated requests keep the ladder — the lookup is a short-circuit with a
fallback, the same shape as `lookup_request_override` at the top of
`resolve_mnemonic_code` and in `plan_contracts`.

## 2. SAM keeps the metadata; XRAS carries the key

The instinct is to push metadata *into* XRAS so it comes back on the handoff.
The better design is the inverse: the request only has to echo an identifier
SAM already holds, and the resolved state lives in SAM, keyed by it. Two
identifiers arrive on every `/actions` payload; they behave differently, and
the difference was measured, not assumed:

| | `requestNumber` | `requestId` |
|---|---|---|
| At `POST /v1/requests` | XRAS assigns `NCAR####` | XRAS assigns; returned in the create response |
| At handoff | **rewritten in place to the projcode**: `UPSU0087` (minted 2026-08-24, before SAM echoed a projcode at all) resolves under the projcode and `NCAR4277` no longer resolves | **unchanged** — `1445869` before and after |
| Promptness | `UPUR0036` was rewritten within hours of its 2026-09-10 handoff; `NCAR4212` → `NRAL0056` (2026-09-09) still answers to `NCAR4212` a day later. Prompt, not guaranteed — a manual step is the likely reading (§ 8 Q1) | n/a |
| Across a renewal | stable — the family key | a renewal spawns a **new** line/id |

So the SAM-side record (§ 4) keys on **`request_id`** for the create → approval
window and carries `request_number` as the family key it becomes. This is the
opposite of `xras_request_override`, which is keyed on `request_number` because
it lives *after* the rewrite; both are right for their window.

**The XRAS-side channel exists, and we already receive it.** Attribute sets are
ACCESS-configured per opportunity and written with
`PUT …/actions/<aid>/opportunity_attributes` (replace-all semantics). Their
answers come back on the push as `opportunityQA` — non-empty on **76 of 76**
production `New` rows, every status, and empty on every Extension and
Supplement. SAM drops the field (`unknown = EXCLUDE`, `sam/schemas/forms/xras.py`).
Five of the seven open opportunities carry one set today — the NWSC End User
Agreement acknowledgment (`attributeSetTypeId 500006`); Data Analysis carries a
second (`500007`); the two Fall-2026 opportunities carry none. A `simpleString`
"SAM reference" attribute, if ACCESS adds one, is therefore a measured
round-trip — belt-and-braces for `requestId`, and visible to reviewers. It is a
soft dependency; the design must not need it. `userComments` also round-trips
and is the zero-ask fallback.

## 3. The authoring surface (apidoc 1.0, read 2026-09-10)

All under our key's `submit` context, impersonating the PI; `review`/`admin`
are 401 for every identity, so only the *Requested* stage is ours. Reads for
verify-by-reread go through the `report` context, the dual-context arrangement
`XrasAdminClient` already uses.

| Step | Endpoint | Client state |
|---|---|---|
| Create | `POST /v1/requests?opportunityId&requestType&grantTypeId` → the full object **including `rules{}`** (the block `GET /v1/requests/<rid>` refuses us), status `Incomplete` | new verb; capture the response — the one time we see `rules{}` |
| Text | `PUT /v1/requests/<rid>/attributes` — `title shortTitle abstract keywords`, fixed fields only | built |
| Roles | `POST /v1/requests/<rid>/roles/<roleType string>/<username>` | built, probed |
| Action | `POST /v1/requests/<rid>/actions?actionType&userComments` | built (`add_action`), unprobed |
| Renew | `POST /v1/requests/<rid>/renew` | built (`renew_request`), unprobed |
| Resources, dates | `PUT …/actions/<aid>/resources/<resourceId>`, `POST …/allocation_dates` | built |
| Grants, FoS, publications | `POST …/grants`, `PUT …/fos/<fosTypeId>`, `POST …/publications` (JSON body) | probed-authorized, not built |
| Custom fields | `PUT …/opportunity_attributes`, `…/resource_attributes` | new verb |
| Documents | `POST …/documents` (multipart) + `GET …/required_documents_status` | new transport |
| Rule book | `GET /v1/allocation_types/<at>/action_types/<act>/required_fields` | new read |
| Preflight, submit | `GET …/validate` (verdict is a function of `XA-USER`), `POST …/submit` → `Under Review` | built, probed |

Sequence: create → attributes → roles (PI first; it becomes `XA-USER` for the
rest) → action → resources → dates → grants/FoS → opportunity attributes (the
EUA is `isRequired`) → `required_fields` + `validate` → `submit`. Every step is
single-attempt with verify-by-reread (`XRAS_WRITE_PROBES.md` § 4.4: a 200 proves
nothing), and the audit row is committed **before** the write leaves
(`sam/manage/xras_remediation.py`, `_editor_op`).

**Documents, measured over 400 approved requests:** `New` 234/398 actions carry
one, `Renewal` 2/2, `Supplement` 4/33, `Extension` **0/28**, Adjustment 0/9. So
Extension never needs the multipart transport, Supplement almost never, and
Renewal behaves like a New. Phase 1 (§ 5) is chosen to stay on the
documents-free side of that line.

## 4. `xras_submission` — the SAM-side record

Modeled on `XrasRequestOverride` (`sam/integration/xras.py`): `SessionMixin` +
`ActiveFlagMixin`, `set()`/`clear()`, a module-level lookup primitive.

| Column group | Columns |
|---|---|
| Keys | `request_id` (PK for the pending window), `request_number` (family key, updated when the rewrite is observed), `action_id` |
| Resolved SAM state | `user_id` (PI), `admin_user_id`, `facility_id`, `mnemonic_code_id`, `allocation_type_id`, `contract_id` (nullable — contract is link-only), `opportunity_id` |
| Lifecycle | `state` ∈ `draft · created · submitted · under_review · approved · applied · withdrawn · rejected`, `created_by`, `xa_user`, timestamps |
| Evidence | `payload` (what we sent), `xras_snapshot` (the create response, `rules{}` included) |

Consult point: the top of the `New` handler's resolution, keyed on the payload's
`requestId`; on a hit the handler takes the stored FKs and mints from them.
Nothing precious — no projcode, no GID — is consumed before approval: the
`project_code` counter and `GidAllocation` are touched only where they are
today, inside the `New` handler's transaction. A rejected or withdrawn
submission costs one row.

⚠️ **Idempotency lands here.** `actionId` is enforced by nothing today and four
of the six handlers double-apply on a re-post (`XRAS_INGEST_IMPROVEMENTS.md`
§ 3). A submission row that records `applied` with the `action_id` is the
natural key the re-apply path has been waiting for.

## 5. Phasing

**Phase 1 — existing-project actions.** Supplement, Extension and Renewal from
the project page, for the lead or admin (`require_project_permission`). The
projcode *is* the `requestNumber`; the PI is known; nothing is minted; no
documents (§ 3). It exercises create-action → resources → dates → validate →
submit end to end with verbs already written. A form schema in its own module
under `sam/schemas/forms/`; an `HtmxFormHandler` subclass; a new
lever **`XRAS_SUBMIT_ENABLED`**, distinct from `XRAS_WRITE_ENABLED` because
authoring a request is a different blast radius from correcting one — webapp
only, never the tasks CronJob, asserted per manifest like the write lever.

**Phase 2 — a new request by a logged-in PI.** The wizard: affiliation
(fail-visible when the `user_institution` row is end-dated — the case that
minted `CGD` for a CU Boulder PI), mnemonic suggestor, awards picker,
opportunity and resources bounded by `rules`, EUA → attribute, preflight,
submit. Depends on the § 7 probes.

**Deferred — anonymous submission.** `XA-USER` must exist at the allocations
process, and SAM never creates users (`PROJECT_AND_ACCOUNT_LIFECYCLE.md` § 2).
An anonymous submitter is therefore exactly the placeholder-identity class this
design eliminates. Options to weigh when it is reached: require an NCAR account
first (enrollment is upstream); a SAM-side pre-registration that becomes a
submission once the user exists; or create the XRAS placeholder deliberately
(`POST /v1/people` with `XA-USER-RECONCILED: false`) and merge on account
creation with the tool that already exists. Walk ARC's flow before choosing.

**Independent follow-on — stop discarding `opportunityQA`.** Declare it in
`XrasActionSchema` and persist or display it. The schema is `EXCLUDE`, so it is
not parity-affecting, but the resource-mapping lesson applies: re-run the parity
check after any wire-shape change.

## 6. Where the risk sits

XRAS has a testbed. SAM does **not** have an internet-reachable development
deployment with a hostname — development is Docker on a workstation — so the
fully clean loop (SAM-dev ↔ XRAS-testbed, no production data at either end) is
not available. The accepted posture for this phase is **small, reversible bogus
requests under `benkirk` against production XRAS**, each paired with its
inverse and closed by Ben himself. While one exists it will surface on SAM's own
Pending Requests card and accounts worklist; log it so nobody triages it.

If that becomes too constraining — anything that needs an approval to flow back
into SAM, such as proving the `opportunityQA` round trip end to end — the
fallback is a k8s-hosted SAM development environment coexisting with
production. That is a large, parallel effort and enabling well beyond XRAS; it
is recorded here as a parked prerequisite with its trigger, not started.

## 7. Probe plan

Run as `XRAS_WRITE_PROBES.md` was: status codes and shapes recorded, bodies
not; every write paired with its inverse; a final-state table.

| Probe | Settles |
|---|---|
| `POST /v1/requests` as `benkirk` against an open opportunity | permitted under `submit`? `rules{}` in the body? `requestNumber` assigned `NCAR####`? |
| `POST …/actions` | does the body carry `actionId`? (the apidoc is silent) |
| `POST …/renew` on a real family | the shape of the spawned line — what phase 1 Renewal rides on |
| `PUT …/opportunity_attributes` with the EUA id | reread shows it? |
| `validate` as PI → `submit` → `withdraw` | the full round trip ends `Incomplete` |

## 8. Questions for ACCESS

1. Who or what rewrites `requestNumber` to the projcode after handoff? It
   happened for `UPSU0087` before SAM echoed a projcode (`219a2a5a`), and has
   not happened for `NCAR4212`/`NRAL0056` a day after. If XRAS now reads
   `result.projcode` from our `/actions` reply, that is half the loop already.
2. Will ACCESS add a `simpleString` attribute to NCAR opportunities for a SAM
   reference, and is that per opportunity or process-wide?
3. Does an API-created request appear in ARC for the PI to see and edit, or
   only in the admin app?
4. What would pointing a SAM environment at the XRAS testbed take —
   credentials, allocations-process name, and whether it can call back to a SAM
   host of our choosing? This prices the § 6 fallback.

## 9. Privilege register additions

Extend the `PRIVILEGE(#n)` register in `XRAS_WRITE_PROBES.md` § 7 when the
build lands: **#12** — the create response is captured because it is the only
`rules{}` read available (retired by read access to `GET /v1/requests/<rid>`);
**#13** — the Requested stage is the only stage a SAM submission can populate,
so the awarded figures are always XRAS's (retired by an `admin`-context key).

## 10. References

| | |
|---|---|
| [`REQUEST_EDITOR.md`](REQUEST_EDITOR.md) | the write client, tiers, levers and stage model this extends |
| [`XRAS_WRITE_PROBES.md`](XRAS_WRITE_PROBES.md) | probe methodology, the one authorization rule, the privilege register |
| [`XRAS_OUTGOING_QUERIES.md`](XRAS_OUTGOING_QUERIES.md) | the readable surface and the request payload shape |
| [`../PROJECT_AND_ACCOUNT_LIFECYCLE.md`](../PROJECT_AND_ACCOUNT_LIFECYCLE.md) | where a project comes from; SAM never creates users |
| [`../incoming/XRAS_TRIAGE_PLAYBOOK.md`](../incoming/XRAS_TRIAGE_PLAYBOOK.md) | the 422 catalog § 1 answers |
| [`../../plans/implemented/XRAS_PUSH_READINESS.md`](../../plans/implemented/XRAS_PUSH_READINESS.md) | the preflight engine reused as the pre-submit check |
| [`../../plans/XRAS_INGEST_IMPROVEMENTS.md`](../../plans/XRAS_INGEST_IMPROVEMENTS.md) | the idempotency prerequisite § 4 lands |
| `https://api.xras.org/apidoc/1.0/` | the authoring endpoints: `https://api.xras.org/apidoc/1.0/requests/create.html`, `…/requests/post_action.html`, `…/action_opportunity_attributes/put_action_opportunity_attributes.html`, `…/rule_book_action_required_fields/index.html` |
