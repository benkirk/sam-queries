# XRAS submission probes — the authoring surface, measured

**Status: read set run 2026-09-14 against production `api.xras.org`; write set
operator-approved call by call the same day.** This is the probe plan of
[`XRAS_SUBMISSION.md`](../../plans/XRAS_SUBMISSION.md) § 7, executed. It extends
[`XRAS_WRITE_PROBES.md`](XRAS_WRITE_PROBES.md) (merge / withdraw / re-submit /
roster) and the 2026-08-22 editor spike in [`REQUEST_EDITOR.md`](REQUEST_EDITOR.md)
§ 3 to the verbs a SAM-authored request needs: add an action to an approved
family, create a request, set its attributes, and drive it to `Under Review`.

Same methodology and the same recording rule as `XRAS_WRITE_PROBES.md` § 1: the
status code is the finding; 200 proves nothing changed; status codes, ids, field
names and state transitions go in this file, response bodies do not.

---

## 1. Setup

```bash
# Read XRAS_API_KEY from ../.env; never echo it, never curl -v.
BASE=https://api.xras.org
H=( -sS --max-time 40 -w '\n[HTTP %{http_code}]\n'
    -H "XA-API-KEY: $XRAS_API_KEY" -H "XA-ALLOCATIONS-PROCESS: NCAR" )
R=( "${H[@]}" -H 'XA-CONTEXT: report' -H 'XA-USER: arcguest' )     # reads
S=( "${H[@]}" -H 'XA-CONTEXT: submit' -H 'XA-USER: benkirk'  )     # writes as PI
```

| Target | Why |
|---|---|
| **NCAR0007** — requestId `1167091`, PI `msmart` | the 2015 `Approved` test family from `XRAS_WRITE_PROBES.md` P3/P4: one `New/Approved` (30576) and one `Supplement/Incomplete` (30578). Not a SAM projcode. The add-action probe runs here because it needs an *approved* family and mints nothing new |
| **a fresh request under `benkirk`** in opportunity `532221` (Exploratory) | the create → attributes → submit round trip. `benkirk` holds no PI role anywhere in XRAS today (R1), so the request is unambiguously ours. Closed by Ben rejecting it in `admin-ncar.xras.org` |

---

## 2. Read set (no writes)

| # | Call | Result |
|---|---|---|
| R1 | `GET /v1/reports/username/benkirk` (report) | 200. Shape `{panels[], requestRoles[{roleName, requests[]}]}`. `benkirk` sits on two panels and holds **User** on one NCAR-facility request; **no Project Lead / Submitter / Project Admin role anywhere** |
| R1b | `GET /v1/projects` (submit, `benkirk`) | **404 with SAM's own envelope** — `no route for GET /api/xras/v1/users/projects/benkirk`. XRAS proxies this route to the accounting service, i.e. back to us (`XRAS_WRITE_PROBES.md` § 4.6). Not an origin oracle, and not a port miss: legacy Java SAM never implemented the `/v1/users/…` family either (`../incoming/XRAS_REIMPLEMENTATION.md` § 2.1), and the catch-all had seen no unmapped call in 58 days. **This probe left the first `status='unmapped'` row in production `xras_action_log`** (2026-09-14, requester `benkirk`) — a watch that reports it is seeing this, not XRAS |
| R2 | `GET /v1/permissions/benkirk` | 200: `administrator` (3), `review-impersonator` (4), `read-admin` (5). Person-level grants; the key's context ceiling (`submit`/`report`) still governs the API |
| R3 | `GET /v1/types/all` (submit) | 200. NCAR vocabularies below (§ 5) |
| R4 | `GET /v1/opportunities` (submit) | 200, **7** open. Every one `canSubmitNewRequest: true`. The XRAS allocation type is on the opportunity (`allocationTypeInfo.allocationTypeId`), not on its resources. Table in § 5 |
| R5 | `GET /v1/allocation_types/<at>/action_types/<act>/required_fields` — 5 types × {Supplement, Extension, Renewal} | 200 on all 15, both contexts. Table in § 5 |
| R6 | `GET /v1/reports/request_numbers/SCSG0001` / `…/UCUB0182` (report) | `[]` for SCSG0001 (never in XRAS); two lines for UCUB0182 — a `New/Approved` line carrying New + Extension + Supplement actions, and a `Renewal/Incomplete` line with **`isDeleted: true`**. The origin predicate, demonstrated both ways |

---

## 3. Write set

Every write single-attempt, verified by re-read, paired with its inverse. Recorded
after each call.

| # | Call | Inverse | Result |
|---|---|---|---|
| W7 | `POST /v1/requests/1167091/actions?actionType=Supplement` as `msmart` | `DELETE /v1/requests/1167091/actions/399241` | ✅ **200 `{result:{actionId}}`** — the body carries the id. Re-read: `399241 Supplement/Incomplete` beside the two 2015 actions. Delete → 200; re-read shows the row **kept, `isDeleted: true`** (soft delete). § 3.1 |
| W1 | `POST /v1/requests?opportunityId=532221&requestType=New` as `benkirk` | reject in `admin-ncar` (W5) | ✅ 200. `requestId 1449311`, **`requestNumber: null`**, `Incomplete`, `rules{}` present (`allowedOperations [Edit, Delete]`, `allowedActions []`, `allowedActionsRes []`, `existingActions[{399242 New, [Edit, Delete]}]`). **The first `New` action is minted by the create.** **The creator lands as Allocation Manager (14), not PI.** § 3.2 |
| W2 | on W1: `PUT …/attributes` (title, abstract, keywords, `isSupportedByGrants=false`); `PUT …/actions/399242/resources/530902?amount=1`; `POST …/allocation_dates?beginDate&endDate`; `PUT …/fos/500003?isPrimary=true`; `PUT …/opportunity_attributes` (JSON); `POST …/roles/PI/benkirk` | — | all 200. `allocation_dates` → `{allocationDateId 250430}`; roles → `{roleId 590267}` — `benkirk` now PI **and** Allocation Manager, both accepted. Re-read via `GET /v1/reports/requests/1449311` (report) echoes title, keywords, fos, the `Requested`-stage resource line, the dates. **The EUA value did not stick**: `attributeValue: null` after both `"Yes"` and `"true"`. § 3.3 |
| W3 | `GET …/required_documents_status`; `GET …/validate`; `POST …/documents` (JSON, base64) | — | documents status: one rule, `Advisor_Letter` (22) `requirementMet: false`; validate failed with exactly that one error — everything else W2 wrote satisfied the rule book. Upload of a hand-built minimal PDF → **400 `document is not a PDF file`** (XRAS parses the bytes); a `cupsfilter`-generated PDF → **200 `{documentId 319659}`**; status flips to `Requirement Met` and **validate passes**. No multipart needed. |
| W4 | `POST …/actions?actionType=Supplement` on the W1 draft; `DELETE` it | delete | 200 `{actionId 399243}`; delete 200; re-read: kept, `isDeleted: true` — same soft-delete as W7 |
| W5 | `POST …/actions/399242/submit` as `benkirk` → Ben rejects in `admin-ncar.xras.org` | — | 200, `result: null` (as P3). Re-read: **`requestNumber` minted at submit — `NCAR4352`**; status **`Submitted`** (not `Under Review`, where P3's *re*-submit landed), `states: [Conflicts Verified, Reviewers Assigned]`, `submitDate` set. After the reject: request **`Rejected`**, action **`Declined`** (the action-level word differs from the request-level one), and the operator's comment came back on the action's `adminComments` — the same field the `xras_notices` approver's note reads. § 3.4 |
| W6a | `POST /v1/requests/1449311/renew?opportunityId=532221` as `benkirk` | `DELETE /v1/requests/1449312/actions/399244` | 200 on a merely `Submitted` request (`rules.allowedActions` was `[]` — the list is not the gate). New line `1449312 Renewal/Incomplete` with one `Renewal` action (399244), `rules{}` present. **Copied:** roster (PI + Allocation Manager), title, keywords, fos. **Not copied:** abstract, grants, resources, dates, attributes, documents. Delete → line `isDeleted: true` |
| W6b | `POST /v1/requests?opportunityId=532221&requestType=Renewal&requestNumber=NCAR4352` as `benkirk` | `DELETE /v1/requests/1449313/actions/399245` | 200; line `1449313 Renewal/Incomplete`, action 399245, `rules{}`. **Copies nothing** — roster is the creator as Allocation Manager only, no title. Delete → `isDeleted: true` |

### 3.1 Add-action is the phase-1 verb, and delete is soft

`POST …/actions` answers `{result:{actionId}}` — the id the doc's § 7 asked about. It is
accepted on an `Approved` family (W7), an `Incomplete` draft (W4) and, by W6, on a
`Submitted` one. `DELETE …/actions/<aid>` is a **soft delete**: the row stays in
`reports/*` with `isDeleted: true`, and the request line's own `isDeleted` is derived —
it read `true` on both 2015 test families before this session (every action deleted;
they were retired after `XRAS_WRITE_PROBES.md`), flipped to `false` the moment W7
added a live action, and back to `true` when that action was deleted. Consequences
for SAM: `xras_sweep.py:543` already drops deleted lines; `preflight.py:98` drops
deleted actions; anything new that walks `actions[]` must too, because a deleted
action can hold the family's highest `actionId`.

### 3.2 Create mints the first action, no number, and the wrong role

The create response is the one `rules{}` read we get, and it shows the auto-minted
`New` action. Two things the design assumed are false: `requestNumber` is **null**
until submit (W5 minted it), and `XA-USER` becomes the **Allocation Manager**, so the
PI must be added explicitly (`POST …/roles/PI/<u>`, W2 — the same person may hold
both). P2's finding applies: validate/submit must run as the PI.

### 3.3 Every sub-resource write is params, except two that are JSON

Attributes, resource lines, dates, fos and roles are query params. Opportunity
attributes and documents take a JSON body — `_write` in `admin_client.py` has no
`json=` path today (`REQUEST_EDITOR.md` § 5 overstates this). The EUA `single_sel`
attribute stores **no value**: `attributeValue: null` after `"Yes"` and `"true"`, and
every approved production `New` payload carries the same `null` with `answer` = the
attribute's own label (`tests/fixtures/xras/actions/new_*.json` `opportunityQA`).
Presence of the row is the acknowledgment; there is nothing to encode.

### 3.4 Documents: the JSON path works, and XRAS parses the bytes

A 400-byte hand-written PDF was refused (`document is not a PDF file`); a
`cupsfilter`-rendered one was accepted and returned `documentId`. For an
Exploratory New the only document rule is the Advisor Letter, and validate names
exactly the unmet rule and nothing else — the rule-book tables in § 5 are the
authoritative form spec.

---

## 4. Final state

| Target | Start | End |
|---|---|---|
| NCAR0007 `1167091` | request `isDeleted: true`; 30578 `Supplement/Incomplete` (deleted), 30576 `New/Approved` (deleted) | identical, plus **399241** `Supplement/Incomplete` soft-deleted |
| NCAR0001 `1166819` | read only (control) | untouched |
| **NCAR4352** `1449311` (created by W1) | — | **`Rejected`** (action 399242 `Declined`, rejected by Ben in `admin-ncar.xras.org` with a comment); PI + Allocation Manager `benkirk`; title `SAM submission probe 2026-09-14 - please reject`; 399243 soft-deleted; one Advisor Letter document (319659, a one-line PDF) |
| NCAR4352 `1449312`, `1449313` (W6 renewals) | — | both `isDeleted: true` (sole action deleted) |
| `benkirk` roles | User on one NCAR request | plus PI 13 + Allocation Manager 14 on NCAR4352 |

Nothing here is a SAM projcode; the sweep windows on `Approved`, so none of it can
reach the Pending card or the accounts worklist. `Rejected` is terminal, and the
family stays readable under `NCAR4352` as the specimen for § 8 Q3 of the design doc.

---

## 5. Reference tables (from R3–R5)

**Opportunity → XRAS allocation type** (`allocationTypeInfo.allocationTypeId`),
and the End User Agreement attribute each carries (`attributeSets[].attributes[]
.opportunityAttributeId`, set type `yes_no` 500006, relation `single_sel` 500004):

| opportunityId | Opportunity | allocationTypeId | EUA attribute |
|---|---|---|---|
| 532220 | Small Allocation (University) | 500024 Small | 539637 |
| 532221 | Exploratory Allocation (University) | 500847 Exploratory | 539638 |
| 532222 | Data Analysis Allocation (University) | 500848 Data Analysis | 539639 (+ textarea 537062, set 534586) |
| 532223 | Classroom Allocation (University) | 500026 Educational | 539640 |
| 533276 | NCAR External Projects | 501276 | 539641 |
| 535388 | Large Allocation (University) - Fall 2026 | 500023 Large | none |
| 535487 | NCAR - NSC Allocation Request - Fall 2026 | 500088 NCAR Strategic Computing | none |

**Required fields per action type** (`required: true` leaves only):

| Allocation type | Supplement 500020 | Extension 500017 | Renewal 500021 |
|---|---|---|---|
| Large 500023 | none | `actions.allocationDates.endDate`, `actions.userComments` | `title`, `abstract`, `grants` |
| Small 500024 | none | same | `title`, `abstract`, `grants` |
| Educational 500026 | none | same | `title`, `abstract` |
| Exploratory 500847 | none | same | `title`, `abstract` |
| NCAR Strategic Computing 500088 | none | none | `title`, `abstract` |

Documents are a separate rule book (`types/all` → `documentTypes[]`): a Main
Document is required for New and Renewal at 500023 and 500088, a Progress Report
for Renewal at 500024 and 500026, Supporting Information at 500026, an Advisor
Letter at 500847. **No document rule names Supplement or Extension.**

**NCAR vocabularies** (process-scoped; the apidoc examples are XSEDE's):

| | |
|---|---|
| `requestTypes` | New 6, Renewal 7 — nothing else |
| `actionTypes` | New 500019, Renewal 500021, Supplement 500020, Extension 500017, Adjustment 500168, Date Adjustment 500334, Transfer 500018, Advance 500015, Appeal 500016, Final Report 500022 |
| `allocationDateTypes` | Requested 1, Suggested 3, Approved 2 |
| `requestStatusTypes` | Submitted 1, Approved 3, Rejected 4, Incomplete 500002, Under Review |
| `attributeSetTypes` | multiple_strings 500008, numeric_range 500005, simple_string 500007, yes_no 500006, date 500010 |
| `roleTypes` | PI 13 "Project Lead", Allocation Manager 14 "Project Admin", User 19 |
| `fosTypes` | 39 entries, e.g. Advanced Scientific Computing 500003 |
