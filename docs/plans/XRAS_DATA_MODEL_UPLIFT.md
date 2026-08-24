# XRAS Data-Model Uplift — Handoff Plan

**Status: PLANNED 2026-08-24.** Investigation complete (this doc carries its
findings); no code written yet. Two PRs, both `--base staging`, each an
ordered commit series. Fresh session starts at Track A commit 1.

## Context

PR #477 (request families) exposed a class of defect: the XRAS outgoing API
returns linked/plural structures (one `requestNumber` → a LIST of request
lines, each its own `requestId` with an `actions[]` trail) that our code
flattened to `result[0]`. A follow-up audit of both integration directions —
against live payloads (GET-only probes) and the full api.xras.org endpoint
catalog — found more members of the same class, several outright bugs, and
one wrong premise. This plan turns the actionable subset into two PRs.

The incoming side was deliberately built to match legacy SAM bug-for-bug.
That was right for cutover parity; Track A is the deliberate step past it.
The two directions must not depend on each other for any capability
(XRAS→SAM works without SAM→XRAS), but their data models are necessarily
similar — findings from one side are checked against the other throughout.

**Cutover pressure (issue #433):** XRAS repoints its base URL to
`sam.hpc.ucar.edu` imminently, making the incoming path live production
traffic. Track A commit 1 is cutover-critical (it removes a measured class
of false 422 rejections); Track B hardens the Remediations card, the triage
tool for cutover week. Issue #433's "What should we be using that we
aren't?" section is answered by this doc — worth a comment on the issue
once the PRs land (Ben posts it).

Explicitly OUT of scope: Derecho `productionBeginDate`/`productionEndDate`
staleness (consumed by another system).

## Track A — incoming uplift (PR 1)

### Commit 1 — grant/contract handling (cutover-critical, first)

**(a) Empty or digit-free `grantNumber` becomes a warning, not an error.**
Today `resolve_contract` (`src/sam/xras/extractors.py`) reports
`Cannot find contract for grant number "" ("")` — a hard action error →
422 — whenever a `grants[]` entry's number strips to empty. Legacy-verbatim,
and measured live (2026-08-24, read-only sweep replica over the sweep's own
cohort: 366 candidate actions in the 120-day preflight window):

| Preflight failures | Count | Of which |
|---|---|---|
| total `failed` | 24 | |
| `Cannot find contract` | 13 | 11 empty-number, 2 real-looking-but-unmatched (`013992`, `2423211`) |
| other (missing users, date/amount checks, ambiguous AM roles) | 11 | correct behavior, untouched |

Decoded examples: UCLR0015 carries
`grants: [{grantNumber: null, fundingAgencyId: 1, title: null}]`;
NCAR4261 the same with `title: "Graduate Research Fellowship"`. The
submitter names an agency or fellowship with no award number.

The rule (simple over precise):

1. `grantNumber` empty after strip → **warning**, no contract link. A grant
   entry with no number is, for contract-linking purposes, the same as
   `grants: []`, which is already legitimate (see the WARNING on
   `plan_contracts` in `src/sam/xras/handlers/_fields.py`). Surface
   `fundingAgency`/`title` in the warning text so operators see what was
   claimed.
2. Non-empty but **zero digits anywhere** → warning, no link (free text like
   "NSF Graduate Fellowship" cannot be an award number).
3. Anything containing a digit → current behavior unchanged: exact match,
   then the ≥6-digit-core suffix match, hard error on miss. The two live
   unmatched cores above are real numbers needing a human or a contract row;
   they must keep failing loudly.

Considered and REJECTED: treating ≤4-digit strings ("NSF Graduate Fellowship
2026") as non-numbers too. Unwarranted complexity — a year-suffixed
free-text entry fails rule 3 with the full string in the message, which is
an acceptable outcome for an unmeasured case. Do not re-litigate.

This intentionally changes the incoming 422 contract for classes 1–2: the
action now applies with a recorded warning instead of bouncing to the
xras_admin human. One sentence in the #433 thread when it ships.

**(b) Dedupe resolved contracts.** `plan_contracts`
(`src/sam/xras/handlers/_fields.py`) returns one entry per `grants[]` row;
`resolve_contract` matches both `AGS-2146709` and `2146709` to the same
`Contract` row; `project_contract` carries `UNIQUE (project_id,
contract_id)` (`src/sam/projects/contracts.py`). Two grants → one contract
raises `IntegrityError` inside `management_transaction` → the 500 arm of
`src/webapp/api/xras/actions.py`, not the reviewable 422. Dedupe by
`contract_id` in `plan_contracts`; also fix the `existing` snapshot in
`src/sam/xras/handlers/update.py` (captured before the loop, never updated
inside it). Test: two `grants[]` entries resolving to one contract → one
link, reviewable outcome.

### Commit 2 — persist `requestId` on incoming actions

`requestId` is the identity of a request LINE; `requestNumber` names the
family (the #477 axis). The schema declares it
(`src/sam/schemas/forms/xras.py`), and `xras_action_log` drops it at
persistence — the comment in `src/sam/integration/xras.py` ("requestId is
deliberately not stored") predates the family concept and is now wrong.
The corpus already holds the counterexample:
`tests/fixtures/xras/actions/new_ncar4236_failed.json` and
`new_uchi0020_ok.json` are byte-identical (same `actionId` 388865, same
`requestId` 1445244) except `requestNumber` — two audit rows, no stored
column linking them.

Work:
- Hand-applied DDL (Ben runs it; database is the schema source of truth):
  `ALTER TABLE xras_action_log ADD COLUMN request_id INT NULL AFTER
  action_id`, plus an index on `request_id`.
- ORM column in `src/sam/integration/xras.py`; rewrite the comment (keep the
  idempotency trap about `action_id`).
- `_record()` kwarg + `_parse_action` in `src/webapp/api/xras/actions.py`.
  The RoleChange (`src/webapp/api/xras/roles.py`) and unmapped ingresses
  have no `requestId` on the wire; they leave it NULL.
- Ripples: schema-validation tests
  (`tests/integration/test_schema_validation.py`); **the CI test DB is an
  LFS blob** — the column reaches CI only via `make bootstrap` + recommit
  the blob, and anonymization failures are silent, so verify the regen by
  hand; `tests/stress/scenarios.json` declares what each scenario's
  `xras_action_log` row must say — the new column lands there.

### Commit 3 — role date-windowing consistency

The handler-side roster (`src/sam/xras/roster.py`) windows every role entry
on `beginDate`/`endDate`; the worklist and modal derivations read neither,
so ended-role holders appear on account worklists. Apply the same window in
`records_from_report_requests` (`src/sam/queries/xras_accounts.py`) and
`roster_from_payload` (`src/sam/queries/xras_requests.py`). The measured
case that motivates this (a role ended 2026-07-28 against an action
beginning 2026-07-30) is recorded near the top of
`docs/xras/outgoing/XRAS_OUTGOING_QUERIES.md`.

### Commit 4 — read `roles[].person{}` for the Pending-Users worklist

The inbound schema declares the full person record
(`src/sam/schemas/forms/xras.py`: firstName, middleName, lastName, email,
phone, organization, academicStatus, isReconciled) and nothing reads it.
Feed A of the worklist (`src/sam/queries/xras_accounts.py`,
`_roster_from_action`) builds records with no person detail, so
`enrich_worklist` pays a live `GET /v1/people/{username}` per row —
budget-capped at 25 lookups — to recover 8 of the 10 `PERSON_FIELDS` the
POST body already carried. Fill `person_by_username` from the payload;
fall back to the live lookup only for fields genuinely absent from the
inbound wire (`residenceCountry`, `orcid`). Removes an outbound dependency
from a tier-A surface and the budget cliff with it.

### Commit 5 — FoS primary-selection hardening

Keep the id-path lookup exactly as is (`resolve_area_of_interest` in
`src/sam/xras/extractors.py` — `fosNum` IS an `area_of_interest_id`;
the docstring's warning is correct and hard-won). Two visibility fixes:
`primary_fos_num` falls back to `fos[0]` when no entry is flagged primary —
make that fallback visible (warning into the action's errors/audit detail);
carry `fosTypeId` through parsed detail for future validation. Non-primary
entries remain dropped (`project.area_of_interest_id` is a scalar FK) but
the drop is recorded.

### Commit 6 — `panels[]` for panel authorization

Inbound `panels[]` names the panel outright — `CHAP` appears in exactly the
6 corpus payloads where panel authorization matters — while
`auth_at_panel_meeting` (`src/sam/xras/handlers/_allocations.py`) infers it
from `'Large Allocation' in opportunityName` via the string ladder. Consult
`panels[]` (CHAP presence, `isPrimary`-aware — NOT `panels[0]`; see the
warning in `src/sam/xras/opportunity_types.py`) as the primary signal, with
the ladder as fallback; log disagreements. Related caveat to preserve: the
opportunity ladder cannot reach facility 4, so a WNA `Small` is the one
silent wrong-projcode case (#459) — this commit does not fix that, only
must not mask it.

### Follow-ons noted, NOT in this PR

- `resources[]` dedupe guard in the four resource-walking handlers (a
  repeated `resourceRepositoryKey` plans two creates; unobserved in corpus).
- `opportunityQA[]` — the NWSC End User Agreement acknowledgement, present
  on all 16 New corpus payloads, survives only inside `raw_payload`; its
  discard is pinned as a product decision in
  `tests/unit/test_xras_actions.py`. Recording it is a product call.
- `grants[]` field enrichment beyond the number (12 fields + nested
  `primaryFos{}` are dropped; a resolved Contract gets a bare link). The
  `GET /v1/funding_agencies` endpoint EXISTS and returns the agency lookup
  (~30 rows) — the earlier conclusion that funding agencies don't exist
  checked `types/all`'s `grantTypes`, which is empty; agencies are their
  own endpoint.
- `requestPeopleRoleId` / `actionResourceId` — the wire's only stable
  per-line identities on the two plural arrays; declared, unread.

## Track B — outgoing fixes (PR 2)

### Commit 1 — `allocationDateType` wire-key fix

`src/webapp/dashboards/allocations/xras/modals.py` reads `d.get('type')`
from `allocationDates[]`; the wire key is `allocationDateType` (live payload
carries only `allocationDateId`/`allocationDateType`/`beginDate`/`endDate`).
The modal's allocation-date stage label renders blank in production, and the
fixture in `tests/unit/test_xras_remediations.py` spells the key `'type'`,
so the suite structurally cannot catch it. Fix code + fixture, and add a
fixture-key gate against the documented wire keys — this is the third
occurrence of the fixture-agrees-with-the-bug pattern (after
`resourceRepositoryKey`).

### Commit 2 — family-consistent write verification

`get_request_by_number` (`src/sam/integration/xras_api/client.py`) returns
`family[0]`, and every write-verification read in
`src/sam/integration/xras_api/admin_client.py` goes through it (withdraw/
resubmit verify, role-write verify via `roster()`/`resolve_pi()`, action
update verify via `_action()`, `update_request_attributes` before/after,
`delete_request` before/after). Writes target the primary line (highest
global `actionId`, `src/webapp/dashboards/allocations/xras/_shared.py`);
verifies read line 0. On a multi-line project a successful write records
`unverified`, and delete-the-tree reports false partial failure ("Deleted 1
of 3") because the verify is `after is None` while sibling lines still
resolve.

Fix: verification reads fetch the family and select the line by the
`requestId` the write targeted; delete-verify becomes "this line is absent
from the family". Retire or rename `get_request_by_number` so `family[0]`
can never be reached implicitly again; audit all callers.

### Commit 3 — family-consistent card entry + post-write patch

`refresh_card_entry` and `recheck_readiness`
(`src/sam/manage/xras_remediation.py`) rebuild the card row from the first
family line, so the action an operator just edited may not be in the rebuilt
row; the sweep's index is first-copy-wins on `requestNumber`
(`src/scheduling/tasks/xras_sweep.py`), so a New+Renewal project contributes
one arbitrary line. Hoist the primary-line selection out of `_shared.py`
(next to `request_family` in `src/sam/queries/xras_requests.py`) and use it
in all three places.

### Commit 4 — `rules`/`validate` overlay (fail-open)

**The premise this replaces is measured wrong.** The comment in
`src/sam/queries/xras_requests.py` says the authoritative legal-moves read
(`rules{...}` on `GET /v1/requests/<rid>`) is 401 for our credential
(PRIVILEGE(#1)). Live probes (2026-08-24) show the 401 is **XA-USER-gated,
not key-gated**:

| XA-USER | context | result |
|---|---|---|
| `arcguest` | submit or report | 401 |
| the request's PI | submit or report | 200, `rules` present |

Measured payloads: an Approved New (UCUB0089, rid 1198063, PI kmussel) →
`allowedActions: ["Transfer","Supplement"]` with per-type
`availableResourceIds`, every existing action `allowedOperations: []`. A
Submitted Renewal (UMIT0073, rid 1447534, PI shuangw) → that action carries
`allowedOperations: ["Edit","Delete"]`, and
`GET .../actions/<aid>/validate` answers
`{"validation": "successful", "errors": []}` (a failing case returns error
strings such as "The Project Lead specified for this request is not allowed
to submit a new request in this opportunity").

No config decision is needed: the XA-USER choice is per-call — pass the
request's PI, which the write paths already resolve. No new env var, no
chart change. Everything here is GET-only; the write levers are untouched.

Work:
- Client methods `get_request_rules(request_id, *, xa_user)` and
  `validate_action(request_id, action_id, *, xa_user)` in
  `src/sam/integration/xras_api/client.py` (report context works with the
  PI identity).
- Remediation modal: when the rules read succeeds, gate offers on
  `existingActions[].allowedOperations` and `allowedActions` (+
  `availableResourceIds` for the resource pickers); on any failure fall
  back to today's state heuristics unchanged. Fail-open — nothing goes dark
  when XRAS degrades.
- Push-readiness screener (`src/sam/xras/preflight.py` consumers): append
  an `xras_validate` check line carrying XRAS's own error strings;
  unreachable → recorded as a gap, not a fail.
- Retire the PRIVILEGE(#1) comment (`src/sam/queries/xras_requests.py`) and
  the matching template note
  (`src/webapp/templates/dashboards/allocations/partials/_xras_remediation_actions.html`);
  document the XA-USER discovery in `docs/xras/outgoing/`.

**Phase 0 of this commit — measure before wiring:** map the
`allowedOperations` vocabulary across action states. Only `Edit`/`Delete`
are observed; how Withdraw is represented (its wire verb is
`DELETE .../submit`) is unverified. Read-only probes with the PI identity
across a handful of Submitted/Under Review/Incomplete actions settle it in
minutes. Do not gate the Withdraw offer until measured.

**Why not derive from opportunities instead (the question this answers):**
opportunity payloads alone are NOT sufficient. Their per-resource
`numbers[]` limits are sparse and stale (Cheyenne defaults from 2017; no
Min/Max rows for Derecho — only Dollar Value entries) and `resourceState`
is unreliable (every resource of every open opportunity reads
`Unavailable`). The request-level `rules` + `validate` machinery evaluates
the rule book server-side, per identity, which is the point. The
opportunity-level `rules.resourceIdsAvailableForNewRequest` (readable as
`arcguest`) is a usable secondary signal; nothing more.

## Probe recipe (read-only)

Headers per the api.xras.org docs (the doc frames live at
`https://api.xras.org/api/api_navigation`; the endpoint catalog is
`https://api.xras.org/apidoc.html`):

```
curl -s -H "XA-ALLOCATIONS-PROCESS: NCAR" -H "XA-API-KEY: $XRAS_API_KEY" \
     -H "XA-USER: <pi-username>" -H "XA-CONTEXT: report" \
     https://api.xras.org/v1/requests/<requestId>
```

`XRAS_API_KEY` comes from `.env`. GET only — the key is write-provisioned
and a person merge is irreversible. Useful references: `/v1/types/all`
(wraps its result in `response`, not `result`), `/v1/funding_agencies`,
`/v1/reports/request_numbers/<projcode>`,
`/v1/allocation_types/<atid>/action_types/<atid>/required_fields`.

## Traps

- Tests pin `XRAS_API_KEY=''` and both XRAS levers off before dotenv loads;
  outbound guards raise on any real HTTP. Drive fakes by patching the
  transport on the client *instance* (which shadows the guard) or replacing
  `from_environment` — see `tests/unit/test_outbound_guards.py`.
- `tests/stress/scenarios.json` is the declaration file for expected
  `xras_action_log` rows (Track A commits 1–2 touch it).
- CI test DB is an LFS blob (`make bootstrap` + recommit; verify the
  anonymization by hand — its failures are swallowed).
- The raw corpus `~/xras_payloads_raw/` is the only way to test SAM-side
  user predicates; the scrubbed fixtures cannot.
- Route-level tests: SAVEPOINT, not `session.rollback()` (the #474 trap).
- Fixture keys must match documented wire keys; Track B commit 1 adds the
  gate.
- Preflight and real dispatch share `resolve_contract` — Track A commit 1
  changes both surfaces with one edit; test both.

## Verification

- Track A: full XRAS-area unit suite + schema-validation tests. Replay a
  corpus New/Update through the dispatcher; assert the audit row carries
  `request_id`. Worklist renders person detail with the outbound client
  stubbed out. Rerun the readiness sweep after commit 1: the 11
  empty-grant failures become warnings (UCLR0015 and NCAR4261 by name).
- Track B: fixtures with 2-line families exercising every admin_client
  verify path (UCUB0089's shape: 3 lines, primary line not `lines[0]`);
  the corrected allocation-date fixture; live read-only smoke per the probe
  recipe (UCUB0089 family; UMIT0073 rules + validate as its PI).

## Deferred / recorded for later (from the same investigation)

Review-pipeline signals unread on every action (`returnedForCorrections`,
`states[]`, `adminComments`, `finalReviews[]`); `search_people` results cut
to 5 of 11 fields before the irreversible-merge screen (dropping
`isReconciled`); `reports/username` award detail and `opportunityId`
discarded (user modal cannot link to the opportunity modal);
`reports/allocations` as a requested-vs-awarded feed; hardcoded panel names
and role types with live endpoints available to re-verify them
(`/v1/panels`, `/v1/types/roles` — the validate-mapping pattern);
`_as_dict` first-elementing list responses in the client; two different
"last activity" definitions between the request index and the person feed.
