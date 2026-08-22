# Editing XRAS Requests from SAM — handoff + Phase 0 runbook

> **This doc is written to be loaded into a fresh session with no prior
> context.** Read it top to bottom before acting.
>
> **You (the assistant) will run Phase 0 yourself.** The operator sets
> `XRAS_WRITE_ENABLED=1` in the environment for you and has approved live writes
> against three **super-stale, throwaway** requests: **NCAR0004, NCAR0099,
> NCAR0798**. These edits are reversible (the same PUT sets values back) —
> restore originals when done. Do **not** touch any other request.

---

## 0. Orientation — what this is and why

SAM (this repo, `/Users/benkirk/codes/project_samuel/devel`) has an **outbound**
XRAS integration (PRs #458/#460): an hourly `xras_sweep` reads the XRAS queue and
publishes a cache that backs the **XRAS Remediations** card on Allocations →
XRAS. The operator write surface so far can only merge people, withdraw/re-submit
actions, and edit rosters.

**The ask:** let an operator click a request (e.g. `NCAR4282`) and open a modal
with the *full* request detail (read-only first), then **edit** the request —
per-resource amounts (Casper hours), allocation dates, add/remove a resource.

**The permission boundary (read this — it frames everything):** the XRAS
endpoints we ride are the **submitter/user** surface — *users submit and edit
their own requests*. The PUT field is `amount` = the **requested** amount.
Setting the **awarded** amount (`awardedAmount` in read payloads) is an
**admin/approver** action. SAM calls under `XA-CONTEXT: submit` impersonating the
PI, so it can *plausibly* edit the **requested** amount and dates but **very
likely cannot change the awarded amount at its current permission level.**
**Phase 0 exists to measure exactly this** — API capability vs. our permission —
against throwaway requests before we build any UI.

### Confirmed XRAS endpoints (docs: `https://api.xras.org/apidoc/1.0/`)

| Purpose | Method + path | Body fields |
|---|---|---|
| Update/add a resource amount | `PUT /v1/requests/<rid>/actions/<aid>/resources/<resourceId>` | `amount` (numeric), `comments` |
| Delete a resource line | `DELETE /v1/requests/<rid>/actions/<aid>/resources/<resourceId>` | — |
| Set allocation dates | `POST /v1/requests/<rid>/actions/<aid>/allocation_dates` | `beginDate`, `endDate` |
| Update allocation dates | `PUT /v1/requests/<rid>/actions/<aid>/allocation_dates/<allocationDateId>` | `beginDate`, `endDate` |

- `resourceId` in the path is the payload's **`actionResourceId`**.
- Read payloads name the amount `awardedAmount`; the **write** field is `amount`.
- Required headers (`api.xras.org/api/request_headers`): `XA-API-KEY`,
  `XA-ALLOCATIONS-PROCESS`, `XA-CONTEXT`, `XA-USER`. Write context recognized
  values include `submit` and `admin`.

### The codebase you'll touch (all real, verified paths)

| Area | Path |
|---|---|
| Read client (GET-only) | `src/sam/integration/xras_api/client.py` — `XrasApiClient`; `get_request_by_number()` (:260) → `GET /v1/reports/request_numbers/<n>` |
| Write client | `src/sam/integration/xras_api/admin_client.py` — `XrasAdminClient`; `_write()` (:284, one attempt); `XA_ADMIN_CONTEXT='submit'` (:76); per-call `XA-USER` via `_headers()` (:229) |
| Config / levers | `src/sam/integration/xras_api/config.py` — `XrasApiConfig.from_environment()`; `write_configured` needs `XRAS_OUTGOING_ENABLED` + `XRAS_WRITE_ENABLED` + `XRAS_API_KEY` |
| Cache index (card rows) | `src/sam/integration/xras_api/cache.py` — `load_requests_index()` |
| Sweep task | `src/scheduling/tasks/xras_sweep.py` |
| Remediation routes | `src/webapp/dashboards/allocations/xras_remediation_routes.py` — helpers `_live_request()`, `_entry()`, `_impersonation()`, `_degraded()`; modal pattern |
| Remediation service | `src/sam/manage/xras_remediation.py` — audit-row-then-dispatch-then-verify |
| Audit model + vocab | `src/sam/integration/xras.py` — `XrasRemediationEvent`; `XRAS_REMEDIATION_OPERATIONS` (:439) |
| Forms | `src/sam/schemas/forms/xras_remediation.py` (export from `forms/__init__.py`) |
| Card template | `src/webapp/templates/dashboards/allocations/partials/xras_remediations_card.html` |
| Expanded row | `.../partials/xras_remediation_row.html` |
| Shared modal shell | `.../partials/audit_details_modal.html` (`#auditDetailsModal` / `#auditDetailsModalBody` / OOB `#auditDetailsModalTitle`) |
| Resource-key → SAM resource | `src/sam/integration/xras.py` `XrasResourceRepositoryKeyResource`; XRAS catalog via `xras_api/people.py get_resources()` |

**Fail-visible contract (relied on everywhere):** a write gets one attempt;
"a 200 is not success" — always **verify by re-reading**. A 4xx raises
`XrasWriteRejected` carrying XRAS's own `errors[]`; render/record it rather than
crashing. Modal GETs degrade with a **200** body (htmx won't swap a 4xx into an
open modal).

---

## 1. PHASE 0 — live API-vs-permissions spike (DO THIS FIRST)

Goal: measure what SAM's credential is actually permitted to do, on throwaway
requests, before building UI. **You run this.**

### 1a. Preconditions — verify before any write
1. Confirm the environment: `XRAS_OUTGOING_ENABLED=1`, `XRAS_WRITE_ENABLED=1`,
   `XRAS_API_KEY` set. Check via `XrasApiConfig.from_environment().write_configured`.
   If not configured, **stop** and tell the operator.
2. Confirm the three targets are the intended throwaway ones: **NCAR0004,
   NCAR0099, NCAR0798**. Touch no others.
3. **Read before you write.** For each, call
   `XrasApiClient.from_environment().get_request_by_number("NCAR0004")` and save
   the full payload (write it to the scratchpad). Record, per request:
   `requestId`, each action's `actionId`, and per resource the
   `actionResourceId` + current `awardedAmount`, and any allocation-date ids +
   current `beginDate`/`endDate`. **If the reports payload does NOT expose
   `actionResourceId` / `allocationDateId`, note it — that gates the editors —
   and do not attempt those edits.**

### 1b. Build the minimum to probe (spike code, not shipped surface)
Prefer a **standalone probe script** in the scratchpad (not committed) over
half-building the real client, so the spike can't leave dead code behind:
- Reuse `XrasApiConfig.from_environment()` for base URL + headers, and
  `XrasApiClient` for the verify-reads.
- Send writes with `requests.Session().request(METHOD, url, headers=...)` using
  `XA-CONTEXT: submit` and `XA-USER = <the request's PI>` (derive via
  `_impersonation()` / `resolve_pi`). **Unknown to resolve empirically:** whether
  `amount`/`comments`/dates go as **query params** or a **form/JSON body** — try
  query params first (matches how `_write` sends today), fall back to form body
  if XRAS 400s on missing fields. Record which worked.

### 1c. Probe matrix — run per request, verify every cell by re-read
For each of NCAR0004 / NCAR0099 / NCAR0798, and each first action:
1. **Edit a requested `amount`** — nudge one resource by a small delta
   (e.g. +1), re-read, confirm it changed. **Then set it back.**
2. **Edit allocation dates** — try `PUT .../allocation_dates/<id>` if an id
   exists, else `POST .../allocation_dates`; shift by a day, re-read, confirm,
   **set back.**
3. **Add then remove a resource line** — `PUT .../resources/<newResourceId>`
   then `DELETE` it; confirm the roster of resources returns to original.
4. **Attempt an awarded-amount change** (expected: **refused** — this confirms
   the requested-vs-awarded boundary). If it unexpectedly *succeeds*, **stop and
   flag it** — that's a bigger capability (and risk) than assumed.
5. Note the exact **XA-CONTEXT** that worked (`submit` vs needing `admin`) and
   the **params-vs-body** answer.

### 1d. Safety rails (non-negotiable)
- Only NCAR0004 / NCAR0099 / NCAR0798. Small, reversible deltas. **Restore every
  original value** after each probe (edits are reversible via the same PUT).
- One request at a time; re-read after every write.
- On any unexpected success (esp. awarded-amount), or anything you don't
  understand, **stop and report** — do not continue mutating.
- This is a live system SAM does not own. When in doubt, don't write.

### 1e. Record findings, then PAUSE
Write a **Findings** section back into this doc (§5, a table: operation ×
{permitted? context, params-vs-body, verified?, notes}) plus whether the reports
payload carries the edit ids. **Then stop and let the operator review before
building Part B UI.** The findings decide which editors ship enabled vs
disabled-with-tooltip.

---

## 2. PART A — read-only detail modal (buildable in parallel; ships first)

Mirror the existing modal-body pattern (`Roles…`/`Withdraw…` already use it).

- **Route** `xras_request_detail(request_number)` in `xras_remediation_routes.py`,
  `@login_required` + `@require_permission(Permission.MANAGE_XRAS)`. Reuse
  `_live_request()` / `_entry()` / `_impersonation()` / `_degraded()`. Outage →
  `_degraded(...)` as a 200; snapshot fallback with a "showing last sweep" note.
- **Partial** `.../partials/xras_request_detail.html`, rendered into
  `#auditDetailsModalBody`, OOB title (mirror `xras_roles_form.html:1`). Sections
  (each rich block `{% if %}`-guarded): header (status/type/dates/opportunity +
  allocation type); **Requested resources** table (label → amount via
  `fmt_number`/`alloc_unit`); abstract/title/FoS/panels/grants; **Roster +
  Actions + existing write buttons**. Extract the roster/actions/button markup
  from `xras_remediation_row.html` (lines 20–150) into a shared include so the
  row and modal can't drift.
- **Resource-key → label:** SAM `Resource.name` via
  `XrasResourceRepositoryKeyResource`; fall back to XRAS catalog
  (`people.get_resources()`); final fallback raw key.
- **Make it clickable — NOT in the Request cell** (it carries
  `collapse_toggle`; Bootstrap's capture-phase data-api fires the expand before
  any nested handler — comment lines 218–231, `test_collapse_trigger_rows.py`).
  Add a **"Details…" link in the SAM cell** (`text-end`, ~line 267, the
  non-toggle cell where the `project` badge link already lives). Also add a
  "Details…" button to the expanded-row button strip (safe — non-toggle cell).
- **Tests** (`tests/unit/test_xras_remediations.py`): add the GET to
  `TestAccessControl` (403 view-only / 200 permitted); render assertions with a
  mocked `_reader`; 200 degraded body on outage; buttons disabled when
  `write_enabled` false.

Part A's live modal independently answers unknown #3 (does the live payload carry
`actionResourceId`/`allocationDateId`).

---

## 3. PART B — editable request surface (scoped by Phase 0 findings)

Edits the **requested** side under PI impersonation. Ship only what Phase 0
proved permitted; anything documented-but-forbidden ships disabled with a
tooltip (same treatment as the write lever being off). The PR is submitted in two
parts: **Part A (read-only) first, then Part B**.

- **B1 — `XrasAdminClient` verbs** (fold in what the Phase 0 spike learned):
  `update_action_resource(...)` → `PUT .../resources/<id>` `{amount, comments}`;
  `delete_action_resource(...)` → `DELETE .../resources/<id>`;
  `set_action_dates(...)` → `POST .../allocation_dates`;
  `update_action_dates(...)` → `PUT .../allocation_dates/<id>`. Single attempt,
  verify by re-read (three-valued `XrasWriteResult.verified`). Extend `_write` to
  carry a body (`data=`/`json=`) per the Phase-0 answer — additive; the five
  existing callers pass none.
- **B2 — audit vocab + service:** add `update_resource_amount`,
  `update_action_dates`, `remove_resource` to `XRAS_REMEDIATION_OPERATIONS`
  (`xras.py:439`); service fns in `manage/xras_remediation.py` mirroring
  `withdraw_action`/`change_role` — audit row on the private session *before*
  dispatch, `complete` after, patch the re-fetched request into cache
  (`request_index_entry`), capture `before_state`/`after_state`.
- **B3 — forms** (`schemas/forms/xras_remediation.py`, `HtmxFormSchema`):
  `XrasResourceAmountForm` (`amount` Decimal ≥0 required, `comment`);
  `XrasActionDatesForm` (`begin_date`, `end_date`, `comment`; reuse
  `assert_date_range()`/`normalize_end_date()`). Ids come from the URL.
- **B4 — routes + handlers:** GET modal-form + POST write per op, each a
  `_XrasRemediationHandler` subclass (its `exception_map` renders
  `XrasWriteRejected.errors[]`). Gated on `xras_write_configured()`; disabled
  buttons carry the "writes off" reason. Add Edit-amount/Edit-dates/Remove/Add
  buttons to the resources table in `xras_request_detail.html`, targeting
  `#auditDetailsModalBody` (re-render in place after a write like
  `_XrasRoleAddHandler`).
- **B5 — tests:** `test_xras_admin_client.py` (new verbs: method+path+body,
  single-attempt, verify-by-reread, lever gates off-by-default);
  `test_xras_remediation_service.py` (audit survives, cache patch);
  `test_xras_remediations.py` (access control, disabled when lever off,
  validation).

---

## 4. Suite to run

```
pytest tests/unit/test_xras_remediations.py tests/unit/test_xras_admin_client.py \
       tests/unit/test_xras_remediation_service.py \
       tests/unit/test_xras_remediation_event.py \
       tests/unit/test_collapse_trigger_rows.py -v
```

No config or schema-migration changes. The write path stays behind the existing
`XRAS_WRITE_ENABLED` lever (webapp-only; never set for scheduled tasks).

---

## 5. Findings — Phase 0 run 2026-08-22 (COMPLETE)

Ran the full matrix against **NCAR0798** (Submitted), **NCAR0099** (Approved),
**NCAR0004** (Approved, read-only). Every write was reversible; **all three
requests verified back to their exact original state** at the end.

### Verdict: every operation we need is PERMITTED at our permission level.

| Operation | Endpoint | Result | Notes |
|---|---|---|---|
| Edit requested amount | `PUT .../resources/<resourceId>` `{amount, comments}` | ✅ **200, verified** | In-place update when a Requested line exists (NCAR0798 555→556→555). |
| Add a resource line | same PUT, when no Requested line exists | ✅ **200, verified** | Created a Requested line on NCAR0099 530181 (had only Recommended+Approved). |
| Remove a resource line | `DELETE .../resources/<resourceId>` | ✅ **200, verified** | **Requested-stage only** — removed my Requested line on NCAR0099, left Recommended+Approved intact. |
| Set allocation dates | `POST .../allocation_dates` `{beginDate,endDate}` | ✅ **200, verified** | Response returns `{"allocationDateId": N}`; created a `Requested`-type date. |
| Update allocation dates | `PUT .../allocation_dates/<allocationDateId>` | ✅ **200, verified** | NCAR0798 date range updated in place. |
| Delete allocation dates | `DELETE .../allocation_dates/<allocationDateId>` | ✅ **200, verified** | Restored NCAR0798 to `allocationDates: []`. |
| Change the **awarded** amount | (no submit-surface endpoint) | ⛔ **out of reach — by design** | See boundary below. |

### The key mechanic — the "stage" model (this reshapes Part B)

Each resource/date carries a **`type` stage**: **`Requested` → `Recommended` →
`Approved`**. **The `submit` context writes ONLY the `Requested` stage.** It never
touches `Recommended`/`Approved`. So on an **Approved** request, editing the
"amount" does **not** change the awarded allocation — it edits/creates the
*Requested* figure beside it. This is exactly the requested-vs-awarded permission
boundary: **SAM (as the PI, `submit` context) edits what was *requested*; only an
approver changes what was *awarded*.** Confirmed empirically, not refused-with-error
but structurally scoped.

**Implication for the UI:** the editor operates on the **Requested** figures.
On an already-**Approved** request that is mostly cosmetic (the award governs),
so Part B should (a) show all three stages read-only in the detail modal, and
(b) only offer editing where it is meaningful — primarily `Submitted` /
`Under Review` requests — and clearly label that it edits the *requested* value,
not the award. Editing amounts on an Approved request silently spawns a stray
`Requested` line (that's what happened on NCAR0099 and had to be cleaned up).

### Mechanics confirmed (fold into `XrasAdminClient`)

- **Transport = query params** for every write (`?amount=…&comments=…`,
  `?beginDate=…&endDate=…`). Never needed form/JSON. → extend `_write` to send
  `params=` for these verbs (it already supports `params`; **no body needed**).
- **Context = `submit`**, **`XA-USER` = the request's PI** (resolve via
  `resolve_pi(roster_from_payload(...))`). `XA-API-KEY` + `XA-ALLOCATIONS-PROCESS`
  as usual.
- **`resourceId` in the path is the resource *type* id** (Yellowstone=530181,
  Cheyenne=530201), NOT a unique per-line id. There is no per-line
  `actionResourceId` in the reports feed. The submitter addresses a resource by
  `(action, resourceId, Requested-stage)` — which is unambiguous because there is
  at most one Requested line per resource per action.
- **`allocationDateId`** is returned by the POST and appears in the reports
  payload once a date exists (empty array when none) — so the editors can target
  updates/deletes.
- Amounts serialize as strings ("555.0"); send plain numeric strings.
- Empty `comments=` clears the field back to `null`.
- Success bodies are `{"message":null,"result":...}`; **still verify by
  re-reading** — a 200 here was always truthful in testing, but keep the contract.

### Answered unknowns
1. `submit` + PI **is** authorized — for the *Requested* stage of amounts, dates,
   add, and remove. Awarded stage is untouchable (by design, not by error).
2. **Params, not body.**
3. Reports payload exposes `resourceId` (type id) + `type` stage + `amount`, and
   `allocationDateId` when dates exist — **enough to drive the editors.**

### Probe artifacts
Throwaway probe + saved before-payloads live in the session scratchpad
(`xras_phase0/`), not committed.

---

## 6. Recommended adjustments to Parts A/B (from Phase 0)

- **Detail modal (Part A):** render resources grouped by `type` stage
  (Requested / Recommended / Approved) so an operator sees requested-vs-awarded at
  a glance; show `allocationDates` similarly.
- **Editors (Part B):** edit the **Requested** stage only; gate the edit
  affordance to states where it's meaningful (`Submitted`/`Under Review`), and on
  `Approved` requests either hide it or show a clear "edits the requested value,
  not the award" warning. Client verbs send **query params**, `submit` context,
  `XA-USER`=PI. Audit vocab: `update_resource_amount`, `add_resource`,
  `remove_resource`, `set_action_dates`, `update_action_dates`,
  `remove_action_dates`.

---

## 7. Phase 0.5 findings — admin-context ceiling (2026-08-22, COMPLETE)

Ran to answer: can we edit the **Approved/Recommended** stages (needs `review`/
`admin` context)? **No — not with this API key.**

**The ceiling is the API key, not the user.** `XA-API-KEY` grants `submit`+`report`
only; `review`/`admin` are refused. Effective authority = *key context grant* ∩
*user role/permission*.

| `GET /v1/permissions/<u>` (read) | result |
|---|---|
| `arcguest` (our `XA-USER`), `lam`, `sorbjan`, `harrter` (PIs) | `[]` — none. Submit authorizes via holding a **role** on the request, not a permission. |
| `benkirk` | `administrator`, `review-impersonator`, `read-admin` — an XRAS admin **as a person**. |

| write attempt (`PUT .../resources/530181`, NCAR0099 Approved) | result |
|---|---|
| ctx=`admin`, user=`arcguest` | **401** |
| ctx=`admin`/`review`, user=`sorbjan` (PI) | **401** |
| ctx=`admin`/`review`/`submit`, user=`benkirk` (XRAS admin) | **401** |

Even `benkirk`, personally an XRAS `administrator`, cannot write the Approved
stage through **our** key. The Approved line stayed 10.0 throughout (nothing to
restore). All three throwaway requests re-verified pristine after both phases.

**Consequence:**
- **Editable with this key:** the **Requested** stage only (amounts, dates,
  add/remove resource, request attributes, action fields, roster, provenance) via
  `submit`, `XA-USER` = a role-holder (the PI).
- **Not editable now:** Approved/Recommended and anything needing `review`/`admin`.
  **Follow-on:** obtain a **new XRAS API key provisioned for `admin`/`review`**
  from XRAS/ACCESS ops — a per-user permission grant (like benkirk's) is *not*
  enough; it must be the key.
- Build decision: build the Approved editors anyway, fail-visible behind a
  default-off `xras_admin_context_available` flag; the client's per-call
  `context=` argument flips them on when the elevated key lands.

**Forward plan:** see `docs/xras/outgoing/REQUEST_EDITOR.md` — the full-request
editor, the `MANAGE_XRAS` vs new `ADMIN_XRAS` tiering, and the build sequencing.
