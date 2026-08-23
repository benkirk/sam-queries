# XRAS push-readiness — a SAM-side preflight of what XRAS has not pushed yet

**Status: planned 2026-08-23, unbuilt.** Promoted out of
[`XRAS_INGEST_IMPROVEMENTS.md`](XRAS_INGEST_IMPROVEMENTS.md) § 2.1 once research showed it
was not "generalize the existing preflight" but a new capability: nothing on the outgoing
side preflights anything today, and the sweep discards the fields a preflight needs.

Companion pages: [`../xras/incoming/XRAS_TRIAGE_PLAYBOOK.md`](../xras/incoming/XRAS_TRIAGE_PLAYBOOK.md)
(the 422 catalog this predicts), [`../xras/outgoing/XRAS_OUTGOING_QUERIES.md`](../xras/outgoing/XRAS_OUTGOING_QUERIES.md)
§ 3.3 (the reports payload this reads) and § 7.4 (the sweep this rides),
[`XRAS_REMEDIATIONS.md`](XRAS_REMEDIATIONS.md) § 6 (the snapshot-patch idiom the
re-check button reuses).

---

## 1 · What it is

A SAM-side, **never-writes** verdict per XRAS **action** the sweep can see — New, Renewal,
Extension, Supplement, Adjustment, on requests of any status: *"if XRAS pushed this
today, would `POST /api/xras/v1/actions` land?"* Computed by synthesizing the inbound action
from the `reports/requests` payload and running
`dispatch_action(session, synthetic, validate_only=True)` — the same call `--recheck` and
the Pending Users card make, which returns before `management_transaction` is ever opened
(`sam/xras/handlers/base.py::run`). The verdict carries the same ordered 422 list a real
push would get, so the playbook's catalog applies *before* the 422 exists, while the fix
window is open and nobody at XRAS has burned a push.

**Tier B input, tier A compute** (the brainstorm's tiers): the payloads come from the sweep
or a live GET and degrade to "unchecked"; the verdict itself is the ingest code path.

Vocabulary reuses re-check's, plus one: `rechecked` (would land) · `failed` (would fail,
with the ordered error list) · `manual` (nothing would run — `Transfer`, `Date
Adjustment`, no service) · **`unchecked`** (could not synthesize, or the preflight raised).
Never guess green: a missing field is a gap, not a pass.

⚠️ **Not XRAS's own `validate`.** `XrasAdminClient.validate_action` (the re-submit modal)
asks XRAS *"would you accept this submission?"* under an impersonated user. This asks SAM
*"would your ingest accept the push?"*. Both are preflights; the docs name which.

---

## 2 · Cohort, stage and push state

**The unit is the action, not the request.** An Extension on an existing project lives on a
request whose `requestNumber` *is* the projcode, and the sweep's `pending_push` set-difference
(`numbers − known projcodes`, `xras_sweep.py::_build_requests_index`) drops it — correctly,
for the *account worklist* that filter serves. The payload is nonetheless in hand: the
primary pass enumerates Approved requests in the period-of-performance window, the extra
passes pull all Submitted and Under Review. Decision (Ben, 2026-08-23): **preflight every
action the sweep enumerates**, any request status.

| | |
|---|---|
| **Cohort** | every action on every enumerated request with `actionStatus != Declined`, whose `entryDate`/`submitDate` falls within a per-action lookback (`SAM_TASKS_XRAS_PREFLIGHT_DAYS`, default ~120; junk/zero refused per the `xras_email_max` idiom; the window is reported in `detail`). The lookback bounds cost and noise — 1,640 in-window requests × all their actions is thousands, the recent slice is tens to low hundreds |
| **Routing** | `select_service` decides by `(actionType, project exists)` exactly as a push would — New on a token → `add`, New/Renewal on a projcode → `update`, Extension → `extend`, Supplement → `supplement`, Adjustment → `adjust`; `Transfer` / `Date Adjustment` → `manual`, by design |
| **`stage`** | `Approved` when the action is approved (amounts and dates from the Approved lines), else `Recommended` → `Requested` fallback. Pre-approval verdicts are **real** for the checks that actually fail — roster, mnemonic, contract, AOI, allocation type, resource key, dates vs commission — and provisional for amounts. That is weeks of lead time (the account queue's "buy lead time" item, without any write) |
| **`push_state`** | layered, **never hidden on a heuristic**: `seen_in_log` (the action's `action_id` appears in `xras_action_log` — exact, but only for pushes after the repoint) · `applied_inferred` (SAM state already reflects it — **exact for Extension**: every target allocation already ends at the action end date, the handler's own equal-end-date skip, reported as a distinct flag; **heuristic for Supplement/Adjustment**: an `allocation_transaction` of that amount on that account after the action's entry date — `transaction_comment` is only the wire comment, there is no XRAS stamp) · `pending` (a New whose request token has no project, or no evidence of a push) · `unknown`. The board hides `seen_in_log` by default and nothing else |
| **Lever** | the sweep passes `enabled=None`. The tasks env does not carry `XRAS_ACTIONS_ENABLED`, and the question is "would the data land", not "is the type parked this week". Surfaces render the lever state separately |

**Cannot check**, and says so: projcode-pool / GID exhaustion (execute-time only), and
anything XRAS will put on the wire that differs from the reports view — hence § 7.

**Probed 2026-08-23 — the reports payload does NOT expose push state.** Read-only, report
context, the newest 400 Approved requests (455 actions) plus one `request_numbers/<n>`
read, and the public apidoc (`requests/by_id`, `reports/*`, every `types/*` page). Action
keys are exactly `actionId, actionStatus, actionType, adminComments, allocationDates,
collaborators, documents, entryDate, finalReview, finalReviews, isDeleted,
opportunityAttributes, resourceAttributes, resources, states, userComments` (the
per-number route adds `returnedForCorrections`); `states[]` is always the two review
states, `finalReview` is always empty, `finalReviews[]` always `[]`,
`opportunityAttributes.attributeValue` is free-text data-use answers, and `adminComments`
is reviewer guidance ("save processed data to campaign store" — not a push record). So
the inference layer above stands, and a field from XRAS would need a product change, not a
probe. Two side facts the probe settled: **`actionStatus` carries `Submitted` and `Under
Review` at the action level** (besides Approved / Declined), so "not yet approved" is
readable per action without consulting `requestStatus`; and the Approved-with-no-project
set in that slice was eight 2026 `New` tokens (`NCAR42xx`) — a population that, given
New's ~30% success rate, mixes *never pushed* with *pushed to legacy and failed*, which is
exactly why `pending` needs `seen_in_log` to split it after the repoint.

---

## 3 · Phase 0 — fix the shared preflight helper (tier A, ships first, small)

`sam/queries/xras_accounts.py::_validate` is what the Pending Users card and
`sam-admin xras --accounts` call for Feed-A rows. Two latent faults, both verified:

1. **No handler registration on the CLI/sweep path.** Handlers register by import side
   effect, fired only by `webapp/api/xras/actions.py`. In a fresh interpreter, after
   `import sam.queries.xras_accounts`, `sam.xras.dispatch._HANDLERS == {}`. So on the CLI
   every dispatch parks as `manual` ("no handler registered") …
2. … and **`manual` is reported as success**: `_validate` discards the `DispatchResult` and
   returns `(True, ())` for anything that did not raise. Net: `sam-admin xras --accounts`
   says "would succeed" for every Feed-A row. The playbook recommends that command.

Fix: deferred `import sam.xras.handlers` inside the helper (test: `registered_services()`
is non-empty after one call from a module that imported nothing else); add
`preflight_status` to `ActionRef` (`rechecked` / `failed` / `manual` / `unchecked`) and
define `would_succeed = status == 'rechecked'`, carrying `manual`'s reason in
`reject_messages`. Also: Feed-B `ActionRef.action_type` is set from `requestType`
(`records_from_report_requests`), which `schemas/forms/xras.py` documents as useless — read
the action's `actionType`. Tests in `tests/unit/test_xras_accounts_query.py`; one-line note
on the playbook's `--accounts` row once fixed.

---

## 4 · Phase 1 — `src/sam/xras/preflight.py`

Pure synthesis plus one dispatch call. Flask-free; `sam.xras.handlers` imported deferred
inside the function that dispatches.

```python
@dataclass(frozen=True)
class Synthesis:
    action: Optional[dict]          # the inbound-wire dict, or None
    gaps: Tuple[str, ...]           # why it is incomplete, machine-readable
    action_id: Optional[int]
    action_type: Optional[str]
    stage: str                      # Approved | Recommended | Requested

@dataclass(frozen=True)
class Verdict:
    status: str                     # rechecked | failed | manual | unchecked
    would_succeed: bool             # status == 'rechecked'
    messages: Tuple[str, ...]       # the ordered 422 list, verbatim — display-only
    gaps: Tuple[str, ...]
    service: Optional[str]
    warnings: Tuple[str, ...]
    action_id: Optional[int]
    action_type: Optional[str]
    action_status: Optional[str]
    request_status: Optional[str]
    stage: str
    push_state: str                 # seen_in_log | applied_inferred | pending | unknown
    push_detail: Optional[dict]
    checked_at: datetime
    resolved: Optional[dict]        # see below; None until DispatchResult carries it

def iter_candidate_actions(report_payload, *, since: date) -> Iterator[dict]
def synthesize_action(report_payload, action, *, resource_keys: Mapping[int, int],
                      opportunities: Mapping[int, dict]) -> Synthesis
def preflight_action(session, report_payload, action, *, resource_keys, opportunities,
                     enabled=None, log_seen: Mapping[int, dict] = {}) -> Verdict
def infer_applied(session, synthesis) -> Optional[dict]
```

**The field map** — the inbound vocabulary is what the parse ladder reads
(`sam/xras/wire.py::get_field` call sites; `XrasActionSchema` in `sam/schemas/forms/xras.py`),
the outgoing one is `XRAS_OUTGOING_QUERIES.md` § 3.3:

| Inbound (assembly reads) | From the reports payload | Gap when absent |
|---|---|---|
| `requestNumber`, `requestId` | same | — |
| `actionId`, `actionType` | the action itself | — |
| `requestTitle` / `requestAbstract` | `title` / `abstract` | — |
| `opportunityId` | same | — |
| `opportunityName` | `opportunity_name` (snake_case — the trap `xras_requests.py` already notes) or `opportunityName` | — |
| `allocationType` | `opportunities[opportunityId]['allocationType']` — **absent from reports rows**; the `opportunityId` map is consulted first, so a map hit makes this moot | `opportunity_unresolved` |
| `actionBeginDate` / `actionEndDate` | the action's `allocationDates[]` at the best available stage (`Approved` → `Requested`) → `%Y-%m-%d` (`handlers/_fields.py::parse_action_*_date`); fallback top-level `beginDate`/`endDate` | `no_allocation_dates` |
| `resources[].resourceRepositoryKey` (int) | `resource_keys[actions[].resources[].resourceId]` — **different id space**; the join is `GET /v1/resources` (`XrasApiClient.get_resources`, carries both ids) | `resource_id_unmapped:<id>` |
| `resources[].awardedAmount` (str) | `str(amount)` of the line at the best stage (`Approved` → `Recommended` → `Requested`), recorded in `stage`. Empty on Extension is **normal** — it is empty on the real wire too | — |
| `resources[].comments` | `comments` | — |
| `roles[]` flat `{roleType, username, beginDate, endDate}` | flatten `roles[].person.username` × `roles[].roles[].role` — reuse `iter_roster_entries` (`xras_accounts.py`) | — |
| `fos[]` (`fosNum`, `isPrimary`) | same | — |
| `grants[].grantNumber` | `grants[]` — inner shape **unprobed** | `grants_shape_unknown` |

Load the synthetic dict through `XrasActionSchema().load()` so it is proven to fit the wire
schema (the path Feed A takes); `get_field` reads plain dicts, so the loaded result goes
straight to `dispatch_action`.

**`preflight_action`**: deferred `import sam.xras.handlers`; `dispatch_action(session,
action, enabled=enabled, validate_only=True)`; `XrasActionRejected` → `failed` with
`exc.messages`; `DispatchResult.status` → `rechecked` / `manual` (reason into `messages`);
any other exception → `unchecked` (logged, never raised — a row, not the run);
`session.rollback()` afterwards, recheck's belt-and-braces. `push_state` resolves in order:
`seen_in_log` if `action_id in log_seen` (carry `{status, received_time, log_id}`) →
`applied_inferred` per `infer_applied` → `pending` for a New whose token has no project →
`unknown`. A green Supplement verdict on an `applied_inferred` action is still green
(additive) — the push state carries the meaning, which is why it is never dropped.

**`infer_applied`**: Extension — every target allocation's effective end ≥ the action end
date, computed with the handler's own helpers (`latest_allocation`, `effective_end_date`,
`account_is_active`); exact. Supplement/Adjustment — an `allocation_transaction` on that
account whose `transaction_amount` equals `awardedAmount`, dated after the action's
`entryDate`; returned flagged `heuristic=True`. New — never.

**Optional, small, recommended**: `DispatchResult` gains `resolved: Optional[dict]`,
populated by `run()` on the validate_only path from handler attributes when present —
`allocation_type` name, panel, facility code, mnemonic code, and whether the type came from
the `opportunityId` map or the ladder. This is what un-silences the playbook's § 3.9a
*pre-push*: the board shows "would mint in series `<facility><mnemonic>…`" before a
projcode is burned.

**Tests** — `tests/unit/test_xras_preflight.py`: hand-built reports fixtures per § 3.3 (plus
the `REPORT_REQUEST` stub in `test_xras_accounts_query.py`) — one New request (token, no
project), one existing project carrying an Approved Extension and a not-yet-approved
Supplement on a request that is Under Review (Requested-stage amounts only);
`iter_candidate_actions` skips Declined and out-of-window; the synthesized dict is
byte-exact per action; stage fallback recorded; each gap triggers; verdict mapping incl.
`manual`; handlers registered by the call; `resource_keys`/`opportunities` injected (no
network); `infer_applied` — Extension exact (Layer-2 `make_allocation` already at the end
date ⇒ `applied_inferred`, a later end ⇒ not), Supplement flagged heuristic; `log_seen` wins
over inference. Follow-up: a **scrubbed** live `reports/request_numbers/<n>` fixture — PII
rules, Ben pulls it.

---

## 5 · Phase 2 — sweep integration (`src/scheduling/tasks/xras_sweep.py`)

- Over **all** enumerated payloads (`payloads + extra_payloads`, before the cohort filters):
  `candidates = [(payload, action) ... for action in iter_candidate_actions(payload,
  since=occurrence − PREFLIGHT_DAYS)]`; one `xras_action_log.action_id IN (...)` query over
  the candidates' ids → `log_seen`; then `preflight_action(...)` per candidate with
  `resource_keys` from `get_resources()` (cached 1 d) and `opportunities` from
  `get_opportunities(<distinct cohort ids>)`. Both lookups are injected — `None` turns every
  resource / opportunity into a gap; the pure function never touches the network. A raising
  preflight costs that action (`unchecked`), never the run. Preflights run before the two
  publishes.
- The Approved ∩ `pending_push` filter **stays for the account worklist** — its purpose is
  unchanged. The Remediations index cohort grows to include Approved in-window requests that
  carry at least one candidate action (an existing project with a fresh Extension is exactly
  what the operator wants to see); those rows render `pending_push=False`, already a
  rendered state (the "project" badge).
- Stamp verdicts into both snapshots: `request_index_entry(payload, pending_push=…,
  preflights=[...])` (additive kwarg — `actions[]` entries gain `preflight`) and the Feed-B
  worklist rows' `actions[]` (`would_succeed` / `reject_messages` / `preflight_status` via
  `ActionRef`, per the request's candidate actions).
- `detail` gains `preflight: {window_days, candidates, rechecked, failed, manual, unchecked,
  by_push_state{…}, by_stage{…}}` and `preflight_calibration` (§ 7). "0 checked, succeeded"
  must be distinguishable from "did not look".
- Cost: bounded by the lookback. Extension/Supplement assembly is a handful of SELECTs,
  New ≈ 10–30. Measure on the first run; tune `PREFLIGHT_DAYS`.
- Tests: extend `tests/unit/test_task_xras_sweep.py` (mock client returns fixture payloads
  + catalog; entries carry `preflight`; a raising preflight costs one row); the
  `test_task_ledger.py` portability walk still passes — no Flask reachable from the task.

---

## 6 · Phase 3 — surfaces

| Surface | What changes |
|---|---|
| **Remediations card** (`partials/xras_remediations_card.html`, `MANAGE_XRAS`) | per-request flags column gets a roll-up badge (worst candidate action: `would fail (N)` > `would park` > `unchecked` > `would land`); the actions subtable gets two cells per action — verdict (tooltip = messages or gaps, with `stage` when not Approved) and push state (`pushed <date> · <status>` / `applied?` / `pending` / `—`). New facet chips for verdict and push state; default hides `seen_in_log` |
| **Request modal** (`xras/modals.py`, `xras_request_detail.html`) | a "SAM pre-flight" section per action: messages (decorated by the § 1.3 remedy hints when they land), gaps, `resolved` summary, push evidence; `MANAGE_XRAS` only — the synthesized payload, collapsed |
| **Pending Users tab** (`partials/xras_accounts_card.html`, `VIEW_XRAS`) | the expansion subtable already carries the Pre-flight column for received-push rows ("not checked / would succeed / would fail (N)"); this fills it for the *Pending request* rows, which currently read "not checked" |
| **Re-check now** (`MANAGE_XRAS`) | POST → live `get_request_by_number()` → preflight the request's candidate actions → `patch_requests_index()` (the `XRAS_REMEDIATIONS.md` § 6 coherence idiom) + patch the worklist row → `refreshXrasTab`. Degrades **200** with a reason when the read client is unavailable — htmx will not swap a 4xx into an open modal |
| **CLI** | `sam-admin xras --readiness [--format json]` → `kind: xras_readiness`, reads the published snapshot (no network); rows sorted red → amber → green; an empty board exits 0 |

Gates: route-map snapshot regen (`ROUTE_MAP_REGEN=1`), RBAC tests (403 for view-only on
the POST), a template gate that the Pending Users subtable carries the column for
pending-request rows, the CLI envelope test.

---

## 7 · Phase 4 — calibration (triage week is the measurement)

When a real POST for a preflighted `action_id` lands, compare predicted vs actual
(`xras_action_log.status` / `error_messages`). The sweep records
`preflight_calibration = {compared, agree, sample[]}`; the request modal shows *"Predicted:
would fail (3) · Actual: failed 422 (3)"* and, for `MANAGE_XRAS`, the synthesized payload
beside `raw_payload`. After a week of real pushes this says whether the field map above
needs correcting — the synthesizer is a guess about what XRAS puts on the wire, and this
is the only way to grade it.

---

## 8 · Verification

Local: `docker compose up webdev --watch` → `docker compose exec webdev sam-admin tasks --run
xras_sweep --force` (needs `XRAS_OUTGOING_ENABLED=1` + key; otherwise the skip path) →
Allocations → XRAS: Remediations badges + modal section; the Pending Users Pre-flight
column on pending-request rows; Re-check now patching in the same interaction;
`sam-admin xras --readiness`. Ben
runs `pytest` by hand. Prod: read-only by construction; watch `detail.preflight` on the
first sweeps, then `preflight_calibration` once pushes arrive.

## 9 · Not in this plan

The digest line (§ 2.3 of the brainstorm) carrying "N would bounce"; any write to XRAS.
(A push-state field from XRAS was probed on 2026-08-23 and does not exist — § 2; asking
for one is a product ask, outside this plan.)
