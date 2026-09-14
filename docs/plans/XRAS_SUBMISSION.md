# Authoring XRAS requests from SAM — closing the loop

**Status: design, not built; authoring surface probed 2026-09-14.** Today a
project's allocation life begins at ARC (`arc.ucar.edu/xras_submit`), is reviewed
in XRAS, and reaches SAM only at approval (`docs/xras/incoming/`). SAM writes
back only as remediation (`docs/xras/outgoing/REQUEST_EDITOR.md`). This document
is the case for the third arc — **SAM → XRAS (review) → SAM (apply)** — and the
shape it should take. The traffic figures are from 2026-09-10 (production
`xras_action_log`, 17 days after the 2026-08-24 repoint); every claim about the
authoring verbs is from
[`XRAS_SUBMISSION_PROBES.md`](../xras/outgoing/XRAS_SUBMISSION_PROBES.md), where
the full create → submit → renew round trip was driven under `benkirk` on
2026-09-14 and every finding is keyed by probe id (R1–R6, W1–W7).

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
| At `POST /v1/requests` | **`null`** — nothing is minted at create (W1) | XRAS assigns; returned in the create response with `rules{}` |
| At submit | **minted**: `NCAR4352` appeared on the first `POST …/submit` (W5) | unchanged |
| At handoff | **rewritten in place to the projcode**: `UPSU0087` (minted 2026-08-24, before SAM echoed a projcode at all) resolves under the projcode and `NCAR4277` no longer resolves | **unchanged** — `1445869` before and after |
| Promptness | `UPUR0036` was rewritten within hours of its 2026-09-10 handoff; `NCAR4212` → `NRAL0056` (2026-09-09) still answers to `NCAR4212` a day later. Prompt, not guaranteed — a manual step is the likely reading (§ 8 Q1) | n/a |
| Across a renewal | stable — the family key | a renewal spawns a **new** line/id (W6) |

So the SAM-side record (§ 4) keys on **`request_id`** for the create → approval
window and carries `request_number` as the family key it becomes. This is the
opposite of `xras_request_override`, which is keyed on `request_number` because
it lives *after* the rewrite; both are right for their window. `request_id` is
the only key that exists between create and submit.

**The XRAS-side channel exists, and we already receive it.** Attribute sets are
ACCESS-configured per opportunity and written with
`PUT …/actions/<aid>/opportunity_attributes` (JSON body, replace-all semantics).
Their answers come back on the push as `opportunityQA` — non-empty on **76 of 76**
production `New` rows, every status, and empty on every Extension and
Supplement. SAM drops the field (`unknown = EXCLUDE`, `sam/schemas/forms/xras.py`).
Five of the seven open opportunities carry the NWSC End User Agreement
acknowledgment (a `yes_no` set, `attributeSetTypeId 500006`, relation
`single_sel`, one `opportunityAttributeId` per opportunity — the table in the
probes doc § 5); Data Analysis carries a second, a textarea (`500007`); the two
Fall-2026 opportunities carry none. The acknowledgment stores **no value** —
every production payload carries `attributeValue: null` with `answer` set to the
attribute's own label, and so does the one W2 wrote after `"Yes"` and `"true"`
were both accepted and discarded; the row's presence is the answer, so there is
nothing to encode and nothing to get wrong. A `simpleString` "SAM reference"
attribute, if ACCESS adds one, is therefore a measured round-trip —
belt-and-braces for `requestId`, and visible to reviewers. It is a soft
dependency; the design must not need it. `userComments` also round-trips and is
the zero-ask fallback.

## 3. The authoring surface (apidoc 1.0; probed 2026-09-14)

All under our key's `submit` context, impersonating the PI; `review`/`admin`
are 401 for every identity, so only the *Requested* stage is ours. Reads for
verify-by-reread go through the `report` context, the dual-context arrangement
`XrasAdminClient` already uses. Every row below was driven live; the probe ids
refer to `XRAS_SUBMISSION_PROBES.md` § 3.

| Step | Endpoint | Measured |
|---|---|---|
| Create | `POST /v1/requests?opportunityId&requestType[&requestNumber&grantTypeId]` → the full object **including `rules{}`** (the block `GET /v1/requests/<rid>` refuses us), status `Incomplete`, **the first action already minted**, `requestNumber` **null**, creator = **Allocation Manager** | W1 ✅ — new verb; capture the response, the one time we see `rules{}` |
| Text | `PUT /v1/requests/<rid>/attributes` — `title shortTitle abstract keywords isSupportedByGrants` | W2 ✅ (built) |
| Roles | `POST /v1/requests/<rid>/roles/PI/<username>` — required, because create does not make the PI; one person may hold PI and Allocation Manager | W2 ✅ (built) |
| Action | `POST /v1/requests/<rid>/actions?actionType[&userComments]` → `{actionId}`; `DELETE …/actions/<aid>` is a **soft delete** | W7/W4 ✅ (`add_action` built; delete new) |
| Renew | `POST /v1/requests/<rid>/renew?opportunityId` → new line with `rules{}`, **copies roster, title, keywords, fos**; create-with-`requestNumber` also spawns a line but copies **nothing** — use `/renew` | W6 ✅ (`renew_request` built) |
| Resources, dates | `PUT …/actions/<aid>/resources/<resourceId>?amount`, `POST …/allocation_dates?beginDate&endDate` → `{allocationDateId}` | W2 ✅ (built) |
| Grants, FoS, publications | `POST …/grants`, `PUT …/fos/<fosTypeId>?isPrimary`, `POST …/publications` (JSON body) | fos W2 ✅; grants/publications authorized (editor spike), not built |
| Custom fields | `PUT …/opportunity_attributes` — **JSON body**; the EUA stores no value, the row is the answer | W2 ✅ — new verb, needs a `json=` path in `_write` |
| Documents | `POST …/documents` — **JSON with a base64 body works**, XRAS parses the bytes (a malformed PDF is a 400); `GET …/required_documents_status` names the unmet rule | W3 ✅ — new verb, same `json=` path; no multipart |
| Rule book | `GET /v1/allocation_types/<at>/action_types/<act>/required_fields`; `<at>` is `opportunities[].allocationTypeInfo.allocationTypeId` | R5 ✅ — new read; results tabled in the probes doc § 5 |
| Preflight, submit | `GET …/validate` (verdict is a function of `XA-USER`; names exactly the unmet rules), `POST …/submit` → **`Submitted`** on a first submit (`Under Review` was the *re*-submit of P3), and the number is minted here | W3/W5 ✅ (built) |

Sequence: create → `roles/PI` (it becomes `XA-USER` for the rest) → attributes
→ resources → dates → fos/grants → opportunity attributes → documents if the
rule book says so → `validate` → `submit`. Every step is single-attempt with
verify-by-reread (`XRAS_WRITE_PROBES.md` § 4.4: a 200 proves nothing — the
submit's body is `null`), and the audit row is committed **before** the write
leaves (`sam/manage/xras_remediation.py`, `_editor_op`).

**Documents.** Over 400 approved requests: `New` 234/398 actions carry one,
`Renewal` 2/2, `Supplement` 4/33, `Extension` **0/28**, Adjustment 0/9. The rule
book agrees and is exact: no document rule names Supplement or Extension; a Main
Document is required for New **and Renewal** on Large (500023) and NSC (500088),
a Progress Report for Renewal on Small (500024) and Educational (500026), an
Advisor Letter on Exploratory (500847). So Renewal is *not* documents-free, and
phase 1 (§ 5) is Supplement + Extension.

## 4. `xras_submission` — the SAM-side record

Modeled on `XrasRequestOverride` (`sam/integration/xras.py`): `SessionMixin` +
`ActiveFlagMixin`, `set()`/`clear()`, a module-level lookup primitive.

| Column group | Columns |
|---|---|
| Keys | `request_id` (PK for the pending window), `request_number` (family key, updated when the rewrite is observed), `action_id` |
| Resolved SAM state | `user_id` (PI), `admin_user_id`, `facility_id`, `mnemonic_code_id`, `allocation_type_id`, `contract_id` (nullable — contract is link-only), `opportunity_id` |
| Lifecycle | `state` ∈ `draft · created · submitted · under_review · approved · applied · withdrawn · rejected` (XRAS's own vocabulary is `Incomplete · Submitted · Under Review · Approved · Rejected`), `created_by`, `xa_user`, timestamps |
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

**Phase 1 — Supplement and Extension on an existing project.** From the project
page, for the lead or admin (`require_project_permission`). The projcode *is*
the `requestNumber`; the PI is known; nothing is minted; no documents and — for
Supplement — no required fields at all (§ 3). Every Extension and Supplement in
the fixture corpus is an action on the family's **existing** line, so the flow
is `add_action` on `primary_line(family)` (`sam/queries/xras_requests.py`) →
resources → dates (+ `userComments` for Extension, which the rule book requires)
→ validate → submit, with verbs already written and now probed. A form schema in
its own module under `sam/schemas/forms/`; an `HtmxFormHandler` subclass; a new
lever **`XRAS_SUBMIT_ENABLED`**, distinct from `XRAS_WRITE_ENABLED` because
authoring a request is a different blast radius from correcting one — webapp
only, never the tasks CronJob, asserted per manifest like the write lever.

**Phase 1 gate — XRAS-origin projects only.** Most SAM projects came through
XRAS; some did not (staff-created in Admin → Projects, or older than the
integration), and an Extension or Supplement for one of those is handled in
SAM, never funneled through XRAS. SAM records no provenance anywhere:

| Candidate predicate | Verdict |
|---|---|
| a column on `project` (`ext_alias`, `project_number`, …) | none carries a request id; `ext_alias` is unused by the integration |
| `xras_action_log` (`action_names_project(Project.projcode)` in `sam/queries/xras_actions.py`) | sound, but sees only pushes since the 2026-08-24 repoint — nothing before |
| `xras_activation_event.project_id` | a narrower subset of the same cohort |
| legacy fingerprint: `allocation_transaction.user_id IS NULL` with an `XRAS Extension Request` / `XrasAction Extension Request` comment | 1,715 projects on the 2026-09 snapshot, 677 of them active; high recall, but `user_id IS NULL` means "any integration actor", so suggestive, not proof |
| **`get_request_family_by_number(projcode)` returns a line that is not `isDeleted`** (`sam/integration/xras_api/client.py`) | **authoritative** — exactly the condition under which XRAS will accept an action, and it holds for legacy-era projects because the handoff rewrites `requestNumber` to the projcode and the family stays under it forever. Demonstrated by R6: `SCSG0001` → `[]`, `UCUB0182` → two lines |

The gate is therefore the last row, which is the read the submit path needs
anyway (it wants the primary line's `requestId` and the XRAS PI for `XA-USER`).
Render the control from the sweep cache (`xras_sweep`'s index, built by
`request_index_entry` in `sam/queries/xras_requests.py`); re-read live on click;
a project with no family gets the control **disabled with the reason** ("not an
XRAS project — its extensions are handled in SAM"), never hidden. Two subtleties
to keep: a projcode minted in SAM that later received a `New`-on-existing
(`select_service` in `sam/xras/dispatch.py` routes that to `update`) *has* a
family and qualifies — that is correct, since XRAS can review actions on it, not
a leak; and a family whose every line is `isDeleted` (both 2015 test requests,
W7) does not. Second gate on the same click: the SAM lead/admin must also hold a
role on the line, because every request-scoped write authorizes on `XA-USER`
(`XRAS_WRITE_PROBES.md` § 4.1); roster drift between SAM and XRAS is refused
with a named reason, never worked around by impersonating someone else. No
provenance column now. If a durable link is wanted later, the shape mirrors
`xras_request_override`: `project_id + request_number + request_id +
opportunity_id + first action_id + source ∈ {action_log, api_backfill}`,
backfilled from `reports/request_numbers/<projcode>` through the existing
`request_family()` parsing — a loop over projcodes, not new code.

**Phase 1b — Renewal.** `POST /v1/requests/<rid>/renew` on the primary line
(it copies roster, title, keywords and fos; the create-with-`requestNumber`
form copies nothing), then the same steps as a New: abstract, grants on Large
and Small, and the Main Document / Progress Report the rule book requires — the
JSON base64 upload, once `_write` has a `json=` path.

**Phase 2 — a new request by a logged-in PI.** The wizard: affiliation
(fail-visible when the `user_institution` row is end-dated — the case that
minted `CGD` for a CU Boulder PI), mnemonic suggestor, awards picker,
opportunity and resources bounded by the opportunity's
`rules.resourceIdsAvailableForNewRequest`, the explicit `roles/PI` write (create
makes the submitter an Allocation Manager, not the PI), EUA → attribute,
documents where the rule book demands them, preflight, submit. The verbs are all
probed; what remains is the form.

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

## 6. Where the risk sits, and the clean loop

The authoring verbs are now measured (§ 3), on **small, reversible requests
under `benkirk` against production XRAS**, each paired with its inverse and the
one that had to be submitted rejected by Ben in the admin app. That posture
remains right for a verb probe: nothing in it can reach SAM's Pending card or
accounts worklist, because the sweep windows on `Approved`, and a request that
never gets past `Submitted` is invisible to SAM by construction.

It is wrong for the next step. Proving the handoff — `opportunityQA` and the
`requestId` echo arriving on `POST /api/xras/v1/actions`, the `New` handler
consulting `xras_submission`, the `requestNumber` rewrite — needs an approval,
and an approval on production XRAS posts to production SAM. The clean loop is
**samuel-dev ↔ the XRAS test instance**: SAM now has an internet-reachable
development deployment (`https://samuel-dev.k8s.ucar.edu`,
`K8S_DEV_ENVIRONMENT.md`), and Steve offered "a test instance of xras_admin … we
could set that up against your new accounting service" on 2026-08-11
(`docs/xras/incoming/XRAS_CUTOVER_RUNBOOK.md`, declined for the cutover and
noted for exactly this). What it takes, priced from the chart as it is:

| Side | Change |
|---|---|
| Ask to Steve | the test instance's API base URL and allocations-process name; a key with the same `submit` + `report` grant; the test `xras_admin` pointed at `https://samuel-dev.k8s.ucar.edu/api/xras/v1` |
| Outbound (samuel-dev → test XRAS) | `helm/values-dev.yaml`: `XRAS_API_BASE`, `XRAS_ALLOCATIONS_PROCESS`, `XRAS_OUTGOING_ENABLED=1`, `XRAS_WRITE_ENABLED=1`, `xrasApiCredentials.enabled=true` with its own OpenBao path (`csg/xras-dev-api-key`). `helm/tests/test-dev-render.sh` today asserts the levers **off** and the key **absent**, and rejects any attempt to turn them on; those assertions become "not production XRAS" (base URL and secret path differ from `values.yaml`). `scripts/deploy_dev.sh` refuses to deploy unless `XRAS_ACTIONS_CAPTURE_ONLY` renders `1`; `scripts/lib/cirrus_common.sh` sets `XRAS_ES_EXPECTED=0` for dev. All four follow |
| Inbound (test XRAS → samuel-dev) | nothing in the chart pins a caller. `sam_dev` ships `api_credentials` **empty** (`containers/sam-sql-dev/config.yaml`), so a `ROLE_XRAS` row must be seeded and must survive `make refresh-dev` (`scripts/gen_api_key.py --username samuel --sql` emits it; `scripts/xras/seed_dev_actions.py` is host-guarded to localhost); `XRAS_ACTIONS_CAPTURE_ONLY=0` |

Until that exists, anything that needs an approval stays un-proven and is
labeled so in § 5. The order of work is the ask first — it costs a conversation
— then the chart PR, then the seeding, and only then the `xras_submission`
table and the handler consult point.

## 7. Probes

Executed and recorded in
[`XRAS_SUBMISSION_PROBES.md`](../xras/outgoing/XRAS_SUBMISSION_PROBES.md): the
read set (roles, permissions, vocabularies, opportunities → allocation types,
the rule book for five allocation types × three action types, the origin
predicate on a non-XRAS and an XRAS projcode) and the write set (add-action on
an approved family; create → roles → attributes → resources → dates → fos → EUA
→ document → validate → submit; both renewal mechanisms). Read that before adding
a verb to the client; the final-state table there is the record of what was
left in production XRAS.

## 8. Questions for ACCESS

1. Who or what rewrites `requestNumber` to the projcode after handoff? It
   happened for `UPSU0087` before SAM echoed a projcode (`219a2a5a`), and has
   not happened for `NCAR4212`/`NRAL0056` a day after. If XRAS now reads
   `result.projcode` from our `/actions` reply, that is half the loop already.
2. Will ACCESS add a `simpleString` attribute to NCAR opportunities for a SAM
   reference, and is that per opportunity or process-wide?
3. Does an API-created request appear in ARC for the PI to see and edit, or
   only in the admin app? (NCAR4352 is the specimen — it was created and
   submitted entirely through the API.)
4. The § 6 ask: test-instance base URL, process name, a key, and the test
   `xras_admin` pointed at samuel-dev.
5. `GET /v1/projects` is proxied to the accounting service as
   `/api/xras/v1/users/projects/<username>`, which SAM does not serve (R1b). Is
   anything calling it, and what did the legacy Java API return?

## 9. Privilege register additions

Extend the `PRIVILEGE(#n)` register in `XRAS_WRITE_PROBES.md` § 7 when the
build lands: **#12** — the create response is captured because it is the only
`rules{}` read available (retired by read access to `GET /v1/requests/<rid>`);
**#13** — the Requested stage is the only stage a SAM submission can populate,
so the awarded figures are always XRAS's (retired by an `admin`-context key).

## 10. References

| | |
|---|---|
| [`XRAS_SUBMISSION_PROBES.md`](../xras/outgoing/XRAS_SUBMISSION_PROBES.md) | the 2026-09-14 probe record: every verb in § 3, the NCAR vocabularies, the rule-book tables |
| [`REQUEST_EDITOR.md`](../xras/outgoing/REQUEST_EDITOR.md) | the write client, tiers, levers and stage model this extends |
| [`XRAS_WRITE_PROBES.md`](../xras/outgoing/XRAS_WRITE_PROBES.md) | probe methodology, the one authorization rule, the privilege register |
| [`XRAS_OUTGOING_QUERIES.md`](../xras/outgoing/XRAS_OUTGOING_QUERIES.md) | the readable surface and the request payload shape |
| [`PROJECT_AND_ACCOUNT_LIFECYCLE.md`](../xras/PROJECT_AND_ACCOUNT_LIFECYCLE.md) | where a project comes from; SAM never creates users |
| [`XRAS_TRIAGE_PLAYBOOK.md`](../xras/incoming/XRAS_TRIAGE_PLAYBOOK.md) | the 422 catalog § 1 answers |
| [`XRAS_PUSH_READINESS.md`](implemented/XRAS_PUSH_READINESS.md) | the preflight engine reused as the pre-submit check |
| [`XRAS_INGEST_IMPROVEMENTS.md`](XRAS_INGEST_IMPROVEMENTS.md) | the idempotency prerequisite § 4 lands |
| `https://api.xras.org/apidoc.html` | the index (86 endpoints; the same URL without `.html` is a 404 decoy); detail pages are static under `https://api.xras.org/apidoc/1.0/`, e.g. `https://api.xras.org/apidoc/1.0/requests/create.html`, `https://api.xras.org/apidoc/1.0/requests/renew_request.html`, `https://api.xras.org/apidoc/1.0/requests/post_action_document.html`, `https://api.xras.org/apidoc/1.0/rule_book_action_required_fields/index.html`. Plain `curl`; the examples are XSEDE's, so every id must be read live for NCAR |
