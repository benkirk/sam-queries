# XRAS Remediations — an operator write surface on Allocations → XRAS

**Status: Phases 0-5 BUILT 2026-08-21 on `xras_write_exploration`. Phase 6 (arming) NOT done,
deliberately.** This document is now a record of what was built and why, plus the two operator
steps that remain.

✅ **Phase 0 (the live probe) is done** — results in
[`XRAS_WRITE_PROBES.md`](../xras/outgoing/XRAS_WRITE_PROBES.md), and § 3, § 5.1, § 5.3, § 5.4,
§ 7.3 and § 11 below are updated with what it settled. Read the probe doc before touching the
client: the API cannot be cheaply re-probed, half the calls are destructive, and two of the
plan's original assumptions turned out to be wrong.

---

## 1. Context

`xras_sweep` + the Feed-B "XRAS Requests Awaiting a Handoff" tab were built read-only, as a
look-ahead at incoming users. The 2026-08-20 live probe
([`XRAS_WRITE_FIXUPS.md`](../xras/outgoing/XRAS_WRITE_FIXUPS.md)) proved the outbound
`XRAS_API_KEY` is write-provisioned for a small, useful subset of the XRAS admin surface: person
**merge** ✅ (destructive, user-agnostic), action **withdraw** ✅ (XA-USER/PI-scoped),
un-reconcile ❌ (200 + silently ignored), whole-request delete ❌ (401). Fetching
`api.xras.org/apidoc.html` (2026-08-21 — static pages, plain curl, no key needed) revealed a much
larger *documented* write surface: re-submit, role add/remove/update, allocation dates, resource
amounts, a validate preflight, and a per-request `rules{allowedOperations}` legality read.

This plan builds an **"XRAS Remediations" card** on Allocations → XRAS — a scoped *subset* of the
external XRAS admin dashboard (never a replacement) for operator fixups: resolving
erroneously-reconciled placeholders by merge, withdrawing stale or pending submissions,
re-submitting, and roster fixups. `MANAGE_XRAS`-only, conditional on outgoing API config, never
automated.

## 2. Decisions (Ben, 2026-08-21)

1. **V1 ops — all four**: merge, withdraw, re-submit, role add/remove. **All four are now
   proven live** (Phase 0, 2026-08-21).
2. **Status scope**: Approved + Submitted + Under Review; status is a facet.
3. **Placement**: new card between the worklist tabs and the action log, own facet-chip form,
   honoring the shared window pills. Action log untouched in v1. NOT a 4th worklist tab.
4. **Merge entry — both surfaces**: per-row on Accounts Needed (rows with placeholder ∧
   is_reconciled) AND from the Remediations card; one shared modal keyed by username.
5. Merge is **permissive** (any target via the standard user picker) with a fragility warning —
   operator discernment, never automation. Feed B stays as-is for now.
6. **Interactivity is a requirement**: a write must render its effect in the same interaction —
   the hourly sweep is not the refresh path (§ 6b, the surgical snapshot patch).

### Established engineering calls

- **Sibling class, not subclass/relaxation**: writes need `XA-CONTEXT: submit` + per-call
  `XA-USER`; the read client hardcodes `report` (under which the write surface 401s, and vice
  versa for the reports family). The GET-only pins on `XrasApiClient`
  (`tests/unit/test_xras_api_client.py:167-188`, `hasattr`/`getsource`-scoped) stay untouched and
  meaningful.
- **`XRAS_WRITE_ENABLED`** lever, fail-closed, pinned `"0"` in helm `webapp.env` (webapp-only —
  tasks never write to XRAS). Note: `XRAS_OUTGOING_ENABLED` is pinned `"1"` in prod now (this
  corrects `XRAS_WRITE_FIXUPS.md` § 6.2, written before the sweep went live).
- **Verify-after-write in the client** — never trust a 200 (the isReconciled lesson). Zero retries
  on write verbs; ambiguity after a timeout is settled by the verify re-GET, which runs regardless.
- **Audit rows survive SAM-side rollback**: attempt-row-before-dispatch + completion update, both
  on private sessions (the `webapp/api/xras/recheck.py` / `NotificationLedger` session-factory
  idiom), never the request session. A 200 from XRAS is irreversible; the record must not be.
- A typo'd merge target could mint a new XRAS identity (apidoc: *"merge a username into an
  existing/new username"*) — SAM **fails closed**: server-side `get_person(target)` must resolve
  before any merge call is made.

## 3. API surface (from api.xras.org/apidoc.html; ✅ proven live / ⚠️ documented, untested / ❌ closed)

Headers on every call: `XA-API-KEY`, `XA-ALLOCATIONS-PROCESS`, `XA-USER`, `XA-CONTEXT`. Our key's
context ceiling: submit ✅ report ✅ review ❌ admin ❌. NCAR role type ids: **13 PI, 14
Allocation Manager, 19 User** (no co-PI in the NCAR process).

| Op | Endpoint | Status |
|---|---|---|
| Merge person | `POST /v1/people/<u>/merge/<new>` | ✅ destructive; does NOT copy phone/residenceCountry/organization to the target |
| Person read (verify path) | `GET /v1/people/<u>` under `submit` | ✅ P0 — the admin client verifies its own merges; unknown → clean 404 |
| Withdraw action | `DELETE /v1/requests/<rid>/actions/<aid>/submit` | ✅ XA-USER must hold a role on the request |
| Re-submit action | `POST .../submit` | ✅ P3 — role-scoped; → **`Under Review`**; the 200 body is `null`, so verify is mandatory |
| Validate (read) | `GET .../validate` → `{validation, errors[]}` | ✅ P2 — role-scoped, and ⚠️ **the verdict depends on which XA-USER you impersonate** |
| Role add / remove | `POST /v1/requests/<rid>/roles/<roleType **string**>/<u>` → `{roleId}`; `DELETE /v1/requests/<rid>/roles/<roleId>` | ✅ P7 — role-scoped; send **no** person params (all optional), which defuses the isReconciled trap |
| Role types | `GET /v1/types/roles` | ✅ NCAR: **13 PI** ("Project Lead"), **14 Allocation Manager** ("Project Admin"), **19 User**. No co-PI. Render `displayRoleType` |
| Roles by projcode | `POST/DELETE /v1/roles/<requestNumber>/<roleTypeId>/<u>` | ⚠️ provisioned (401 vs 404 proves it) but **cannot resolve our test request numbers** — untestable without writing to a live project. Not used |
| Allocation dates / resource amounts / update action | `POST/PUT/DELETE .../allocation_dates`, `PUT .../resources/<id>`, `PUT .../actions/<aid>` | ⚠️ deliberately out of v1 scope |
| Legal-moves read | `GET /v1/requests/<rid>` → `rules{...}` | ❌ **401 route-wide**, every context × every XA-USER. Offer legality comes from the snapshot + `validate` instead |
| Un-reconcile | `POST /v1/people/<u>` isReconciled=false | ❌ green-and-inert — never wire a button to it |
| Delete request | `DELETE /v1/requests/<rid>` | ❌ 401 for every XA-USER |

⚠️ **The two role families encode `roleType` differently** — `/v1/roles/...` wants the integer id,
`/v1/requests/.../roles/...` wants the string name and 400s on the id. Carry both representations.

⚠️ **One authorization rule covers the whole request-scoped surface**: `XA-USER` must hold a role on
*that* request, else 401. `arcguest` (the config default) is never sufficient. Person ops (merge,
`/v1/people`) are user-agnostic. Prefer the **PI** (roleTypeId 13) as the impersonated user — P2
proved the PI and the Allocation Manager are not interchangeable.

The users-on-resources loopback (`GET/POST /v1/roles/<n>/Users`) proxies to *"the accounting
service"* — which for NCAR is SAM's own inbound API. GET is a possible future connectivity
diagnostic; POST is a loop we never drive. Out of v1.

## 4. Phase 0 — live probe ✅ **DONE 2026-08-21**

Full runbook, results and net-zero proof:
[`docs/xras/outgoing/XRAS_WRITE_PROBES.md`](../xras/outgoing/XRAS_WRITE_PROBES.md). Targets were
**NCAR0001** (requestId 1166819; PI `dhart`, Allocation Manager `bjsmith`) and **NCAR0007**
(1167091; Supplement action 30578), neither of which is a `project.projcode` in SAM. Every write
was paired with its inverse and verified by re-read; both targets ended exactly as found.

What it changed, in one place:

1. **`rules{}` is closed** (401 route-wide) — the biggest change. Offer legality is derived from
   the sweep snapshot's `actionStatus` plus the `validate` preflight; `get_request()` is gone.
2. **Validate is impersonation-dependent** — the same action validates for the PI and fails for
   the Allocation Manager. Impersonate the PI; show the verdict *with* the user it was evaluated
   as; never cache a verdict across users.
3. **The role family flipped** to the requests-keyed routes with a **string** roleType and no
   person params; removal is keyed on the returned `roleId`.
4. **Re-submit works** and lands in `Under Review`, with a `null` body — verify is mandatory.

Standing prod fixtures for the merge feature, both **untouched** and still wanted as test data (do
NOT merge without re-confirming with Ben): `mding-user-efmlx` (single email-exact target `mding`;
re-confirmed resolving with `isReconciled: true`) and `kquagraine-user-89o84` (**two** candidates:
`ktquagra`@tamu.edu/TAMU = correct; `kwesiq`@gmail/NCAR = decoy — the case that kills name-based
automation; the safe key is email/org, never name).

## 5. Backend

### 5.1 Client — new `src/sam/integration/xras_api/admin_client.py`

`XA_ADMIN_CONTEXT = 'submit'` module const (mirror of the read client's `'report'`; not a knob).
Session headers as the read client but submit context; `XA-USER` defaults to `config.api_user` and
is overridden per call via per-request headers (never session mutation).

```python
class XrasAdminClient:
    from_environment()                      # raises XrasWriteNotConfigured unless write_configured
    _get(path, *, params, xa_user)          # submit-context GET; 5xx retry like the read client; 404 -> None
    # NOTE: no get_request() — GET /v1/requests/<rid> is 401 route-wide (P1). Roster/action
    # state comes from the READ client's get_request_by_number() (reports family).
    _write(method, path, *, params, xa_user)  # ONE attempt, no retry; 4xx -> XrasWriteRejected;
                                              # transport error recorded, verify still runs
    get_person(username)                    # submit-context read; the verify path (P0)
    validate_action(request_id, action_id, *, xa_user)   # role-scoped; verdict is per-xa_user
    merge_person(source, target)            # pre-capture both; verify: source re-GET -> None AND target resolves
    withdraw_action(request_id, action_id, *, xa_user)   # verify: action -> Incomplete; requestStatus before/after
    submit_action(request_id, action_id, *, xa_user, preflight=True)  # refuse on validate errors unless preflight=False
    add_role(request_id, role_type_name, username, *, xa_user)   # STRING roleType; no person params; -> {roleId}
    remove_role(request_id, role_id, *, xa_user)                 # keyed on roleId, NOT username
```

All writes return a frozen `XrasWriteResult(operation, method, path, xa_user, http_status,
message, before, after, verified, verify_detail, write_error)`. A failed verify is
`verified=False`, NOT an exception — the audit row and the operator both need the payload.

- `config.py`: field `write_enabled` (reads `XRAS_WRITE_ENABLED`, default False), property
  `write_configured` (`enabled AND write_enabled AND api_key`), `summary()` gains `write_enabled`,
  module predicate `xras_write_configured()`.
- `base.py`: `XrasWriteNotConfigured(XrasApiNotConfigured)`, `XrasWriteRejected(XrasSourceUnavailable)`.
- `cache.py`: `invalidate_person(username)` (pop casefolded key) — called for BOTH source and
  target after a merge, else the 4-hour `xras_people` TTL serves a deleted placeholder to the very
  card the merge just fixed.
- Package `__init__.py` exports the new names; rewrite its "Read-only, GET-only" docstring for the
  two-client split.

### 5.2 Audit table — `xras_remediation_event` (new; no FKs, deliberately — all identifiers are XRAS-side)

One table covering person-ops AND request-ops. Do NOT overload the spec'd `xras_account_event`
(`XRAS_OUTGOING_QUERIES.md` § 7.6 — username-keyed state-derive semantics, reserved for its own
feature). Columns: id PK · `operation` VARCHAR(24)
(`merge_person|withdraw_action|submit_action|add_role|remove_role`) · `status` VARCHAR(16)
(`attempted|verified|unverified|rejected|error`) · `username` · `target_username` ·
`request_number` · `request_id` INT · `action_id` INT · `xa_user` (impersonated; NULL =
user-agnostic) · `created_by` (operator, never `task:*`) · `creation_time` / `completed_time`
DATETIME (app clock, no DB default) · `http_status` · `outcome_reason` VARCHAR(255) · `comment`
TEXT (operator's reason — required for withdraw, optional elsewhere) · `before_state` /
`after_state` TEXT (JSON captures, incl. pre-merge person detail with residenceCountry — merge does
not copy it). Table utf8mb3; ALTER `before_state`/`after_state`/`comment` to utf8mb4 (the
charset-split rule — identifiers stay utf8mb3). Indexes: (operation, creation_time), username,
request_number, (created_by, creation_time).

ORM in `src/sam/integration/xras.py`: vocab tuples + `XrasRemediationEvent` with `create()`
(flush-no-commit, vocab-validated, `created_by[:35]`) and `complete()` classmethods; export from
`src/sam/__init__.py` (auto-registers the Flask-Admin view).

New-table checklist (the proven `XRAS_CUTOVER_RUNBOOK.md` § 2c sequence — `initdb.d/` is retired):
hand-DDL on the local dev DB → ORM + exports → schema-validation case + `UTF8MB4_COLUMNS`
additions → prod DDL via the hpc-writer grant (no DROP — get it right the first time) → record the
DDL as `XRAS_CUTOVER_RUNBOOK.md` § 2d → `purge_xras_remediation_event` in
`containers/sam-sql-dev/anonymize_sam_db.py` (before_state carries PII; **verify the purge by
hand** — anonymization failures are silent) → regenerate the obfuscated snapshot → factory
`make_xras_remediation_event` in `tests/factories/xras.py`.

### 5.3 Service — new `src/sam/manage/xras_remediation.py` (Flask-free, deferred imports)

```python
merge_placeholder(session_factory, *, source_username, target_username, operator, comment=None, client=None)
withdraw_action(session_factory, *, request_number, request_id, action_id, pi_username, operator, comment, client=None)
resubmit_action(session_factory, *, request_number, request_id, action_id, pi_username, operator, client=None)
change_role(session_factory, *, add, request_number, request_id, role_type_id, username, xa_user, operator, client=None)
# each -> RemediationOutcome(event_id, result, status, error)
```

Flow per op: build client (`XrasWriteNotConfigured` propagates) → `create()` attempt row on a
private session, commit → dispatch (verify inside) → `complete()` on a fresh private session →
merge only: `invalidate_person()` ×2 → the § 6b coherence patch. Role-type mapping lives here, exported for the form
schema, and must carry **three** representations per role because the API uses all three:
`roleTypeId` (13/14/19, for the projcode family and roster matching), `roleType` (`PI` /
`Allocation Manager` / `User` — the **string the write route takes**), and `displayRoleType`
(`Project Lead` / `Project Admin` / `User` — what the UI renders, matching the XRAS admin app).
Also here: `resolve_pi(roster)` → the roleTypeId-13 username, the default `xa_user` for every
request-scoped op. Webapp
routes call only these functions with `session_factory=lambda: Session(db.engine)` — never the
client raw. Read side: new `src/sam/queries/xras_remediations.py`
`list_remediation_events(session, *, operation=None, request_number=None, username=None,
since=None, limit=50)` — NOT exported from `sam/queries/__init__.py` (the eager-import trap).

### 5.4 Sweep broadening — `src/scheduling/tasks/xras_sweep.py` + `xras_api/cache.py`

- Two extra paged passes, hardcoded `('Submitted', 'Under Review')`, own budget
  (`EXTRA_STATUS_MAX_PAGES = 5`; the cohorts are small), per-status `budget_exhausted` in detail.
  `SAM_TASKS_XRAS_SWEEP_STATUS` keeps governing only the primary pass — a typo'd chart value must
  not silently drop the remediation feed.
- Index cohort: **(Approved ∩ pending_push) + all Submitted + all Under Review**, with NO
  period-of-performance window — stale NCAR0001-class requests fall outside that window, and they
  are the point. Roughly ~100 requests. *Assumption for Ben (§ 9.1): already-pushed Approved
  requests are excluded — their handoff happened; the escape hatch is the action-log
  request_number filter + a live modal.*
- Per-request dict (built by the shared § 6b helper): request_number, request_id, status,
  request_type, submit_date, begin/end dates, pending_push, opportunity id/name,
  pi{name, username}, roster [{role_type_id, role_type, role_id, username, name, placeholder,
  is_reconciled}] (no full person dicts — the route gates PII like the existing cards).
  ⚠️ The reports roster **nests**: `roles[].person` plus `roles[].roles[]` carrying
  `{roleId, role, roleTypeId, beginDate, endDate, isAccountToBeCreated}` — reading
  `role['roleType']` on the outer object returns `None`. `role_id` is load-bearing: role
  removal is keyed on it, not on the username, actions [{action_id, action_type,
  action_status, submit_date, amounts}].
- Publish as a **second key** in the `xras_pending` bucket: `store_requests_index()` /
  `load_requests_index()` (same backend-name return contract as `store_pending_worklist` — the
  one-shot-pod Redis-vs-local lesson applies identically; TTL 86400). The `worklist` key and shape
  stay untouched, so the two feeds fail independently — a failed index write must not cost the
  account worklist. Not a compatibility boundary: sweep and webapp ship in one image, so neither
  shape is frozen and `sam-admin cache --refresh` settles any disagreement. Ledger `detail` gains
  counts only (60 kB cap).

## 6. Post-write interactivity — cache coherence without a full sweep

The card renders from the hourly sweep snapshot, but a write demands an immediate visual response —
a just-withdrawn action must not show "Approved" for another hour, and re-running the 60-90 s
enumeration per write is not an option. **After every verified write the service patches the
snapshot surgically:**

- **Shared derivation** (the two-consumers rule): extract the sweep's per-request index-entry
  builder into an importable helper (e.g.
  `sam/queries/xras_requests.py::request_index_entry(payload)`), so the sweep's bulk pass and the
  post-write patch produce byte-identical entries from one function.
- **Request ops** (withdraw / re-submit / roles): the service ends with a best-effort
  `_refresh_index_entry(request_number)` — live `get_request_by_number()` (read client; carries
  roles + persons + full actions), rebuild the entry, read-modify-write the `requests_index`
  payload under the cache adapter's lock, stamping `refreshed_at` on the entry. A row patched into
  an out-of-cohort state (e.g. a whole request flipping to Incomplete) **stays visible** with its
  new status and an "updated since sweep" tell until the next sweep drops it naturally — the
  operator must see the effect, not a vanishing row.
- **Merge**: patch every index entry whose roster carries the placeholder username (re-fetch those
  requests — typically 1-2), AND drop/patch the username's row in the Feed-B `worklist` key so the
  Pending Requests tab agrees. `invalidate_person()` ×2 covers the people bucket; Accounts Needed
  classifies live on render, so that row clears itself.
- **Failure is non-fatal**: on `XrasSourceUnavailable` the patch is skipped and logged; the modal's
  success message appends "the card may lag until the next hourly sweep." The write itself was
  already verified and audited.
- **UI**: `refreshXrasTab` (already fired by every success) re-renders from the patched snapshot —
  the card updates in the same interaction, no new JS. Entries with `refreshed_at` newer than
  `generated_at` render a small "live" tick.

## 7. UI (webapp)

### 7.1 Page shell — edit `templates/dashboards/allocations/xras.html`

Both additions wrapped in `{% if has_permission(Permission.MANAGE_XRAS) %}` (double-gated with the
routes):
1. Hidden facet form `#xras-remediation-filters` after `#xras-pending-filters`: multi hidden
   selects `status`, `opportunity`, `push`, `request_number` — every facet dimension the card
   offers needs a matching control here or its chips are silently inert (xras.html:88-94).
2. Card container `#alloc-xras-remediations` between `.tab-content` and `.filter-sidebar`:
   `hx-trigger="load, refreshXrasTab from:body, submit from:#xras-window-filters, submit
   from:#xras-remediation-filters"`, `hx-include` both forms. A card, not a 4th tab —
   `#xrasWorklistTabs` stays at 3 (e2e pin).

### 7.2 Fragment + card — new `partials/xras_remediations_card.html`

Route `xras_remediations_fragment` (GET, MANAGE_XRAS): `load_requests_index()`; window filter on
`submit_date` (keep dateless rows — missing information is not evidence of age); self-excluding
facets over the windowed set; chip filters; regroup by opportunity. Context carries
`write_enabled = xras_write_configured()` — lever off renders **disabled** buttons with an
explanatory title, not a hidden card.

- **Four empty states**: not configured / no sweep yet / snapshot predates the index (the
  first-deploy hour) / nothing to remediate. Freshness line + budget-exhausted warning as Feed B.
- **Nested table**: opportunity header rows (name + id + count badge) → button-free request rows
  (`<tr>` collapse toggle, same as the sibling cards — sidesteps the Bootstrap capture-phase
  data-api gotcha): request_number, status badge (card-local vocabulary — the shared
  `status_badge` vocabulary is SAM ingest statuses), PI, submitted, period, push badge, summary
  chips (N actions; a "placeholder roster" warning when any role is placeholder ∧ is_reconciled).
- **Expansion**: a button strip (Roles…, "Show in action log" via `set-filter-submit` writing
  request_number into `#xras-filters` — zero new JS); roster subtable with per-placeholder-row
  "Resolve identity (merge in XRAS)…"; actions subtable with per-action Withdraw… / Re-submit…
  offers (snapshot-derived; the modal's live read is the authority on legality).
- Header honesty: "N of M request(s)" + the amber "outside the date filter" badge (the Feed-B
  idiom — an older stale request is the MORE urgent one; the window may never hide silently).
- One warning strip, said once: every button writes to production XRAS; operator-confirmed,
  verified by re-read, recorded to the remediation audit log.

### 7.3 Modals — all into `#auditDetailsModalBody`; new routes module `dashboards/allocations/xras_remediation_routes.py`

Registered on the existing `bp`, imported at the bottom of `blueprint.py` (which is 2,500 lines;
this family is ~600 more). Shared `_XrasRemediationHandler(HtmxFormHandler)` with an
`exception_map` translating `XrasWriteNotConfigured` / `XrasWriteRejected` /
`XrasSourceUnavailable` into operator copy; triggers = `_XRAS_MODAL_TRIGGERS`. The webapp session
does no writes (audit lives on the service's private connections); the
idle-transaction-across-HTTP cost inside `perform()` is accepted and documented in the base-class
docstring. Every GET form route catches `XrasSourceUnavailable` → a **200** degraded body
("remediation requires a live read; nothing can proceed from cached data") — htmx will not swap a
4xx into an open modal.

| Modal | GET (live reads) | POST | Notes |
|---|---|---|---|
| **Merge** `xras_merge_form/<username>` | `get_person` (404 → "already merged away; stale echo"); candidates = `search_people` (email first, then surname — its first real caller; today dead code at `client.py:186`) ∩ SAM users, ranked email→org→name | `_XrasMergeHandler`: `clean()` = exactly-one-of candidate-radio / fk-picker override, target ≠ source, **target must resolve in XRAS** (fail-closed) | `XRAS_WRITE_FIXUPS.md` § 3 copy verbatim; radios `required`, NO default when >1; the picker override carries the fragility warning (the kquagraine decoy case); `btn-danger` "Merge and delete placeholder" |
| **Withdraw** `xras_withdraw_form/<request_number>/<int:action_id>` | `get_request_by_number` only (state + roster + PI). **No `rules{}`** — it is 401 route-wide; the offer keys on `actionStatus` | `_XrasWithdrawHandler` + `XrasRemediationReasonForm` (comment required — the dismiss precedent) | Addendum copy: de-approves to a draft, reversible, rewrites history; names the impersonated PI AND the recorded operator |
| **Re-submit** `xras_resubmit_form/...` | validate preflight rendered before the button, **labelled with the impersonated user** (the verdict is per-user); errors → disabled button | hand-rolled bodiless POST (no fields — a schema would be furniture); `XrasWriteRejected` re-renders with `errors[]` | inverse-of-withdraw framing |
| **Roles** `xras_roles_form/<request_number>` | live roster | add: `_XrasRoleAddHandler` + `XrasRoleForm` (role_type OneOf imported from the service, submitted as the **string**; user via fk-picker); remove keys on the roster's `role_id`; `on_success()` re-renders the roster and does NOT close the modal (roster fixes come in batches). remove: bodiless POST + `hx-confirm` danger | warning: an unknown username creates a new XRAS identity |

Form schemas: new `src/sam/schemas/forms/xras_remediation.py` — `XrasMergeForm`,
`XrasRemediationReasonForm`, `XrasRoleForm`.

Ten new routes (fragment + 4 GET forms + 5 POSTs), all
`@login_required @require_permission(Permission.MANAGE_XRAS)`; route-map snapshot regenerated
(`ROUTE_MAP_REGEN=1`) with the diff showing exactly the additions.

### 7.4 Accounts Needed entry — edit `partials/xras_accounts_card.html`

"Resolve identity (merge in XRAS)…" button inside the **expansion panel** above
`person_detail(row.person)`, visible when `may_manage ∧ row.placeholder ∧ row.is_reconciled`
(keeps the `<tr>` toggle and the e2e data-bs-target-off-the-row pin intact). Same merge modal.
Passive tell: the "identified" badge gains warning styling for that conjunction ("identified but
never merged — XRAS keeps sending the throwaway username").

**JavaScript: none.** Everything rides `set-filter-submit`, `fk-picker.js`, `htmx-config.js`
(samConfirmModal via `hx-confirm` + `data-confirm-*`, closeActiveModal, showToast), and
`dashboard-init.js` (refreshXrasTab).

## 8. Tests (house convention: HTTP tests = auth/validation/render smoke; write happy paths at the service layer)

- New `tests/unit/test_xras_admin_client.py` — lever default-off; three-way `write_configured`;
  sibling-not-subclass; `XA-CONTEXT == 'submit'`; per-call XA-USER; writes single-attempt (mock
  503 → `call_count == 1`); 4xx → `XrasWriteRejected`; per-method verify semantics (merge
  source-still-200 ⇒ `verified=False`); the two helm drift tests
  (`test_the_write_lever_ships_off`, `test_the_tasks_env_never_arms_writes`).
- New `tests/unit/test_xras_remediation_service.py` — attempt row survives a client explosion;
  completion status mapping; cache invalidation on merge; coherence patch produces the same entry
  shape as the sweep (shared-builder assertion), reaches both cache keys on merge, leaves the
  snapshot intact on failure; operator recorded.
- New `tests/unit/test_xras_remediations.py` (routes) — 403s for non-admin AND view-only (the
  `get_user_permissions` monkeypatch fixture); the view-only shell contains neither form nor
  container; facet/control parity; four empty states distinct; window honesty; modal GET smoke +
  degraded 200s; the merge two-candidate case has no checked radio; POST validation paths;
  HX-Trigger contents (role-add keeps the modal open).
- New `tests/unit/test_xras_remediation_forms.py`, `tests/unit/test_xras_remediations_query.py`,
  `e2e/test_xras_remediations_card.py` (DOM order, tab count still 3, chips; no PII assertions —
  unscrubbed-corpus rules).
- Edited: `test_schema_validation.py` (UTF8MB4_COLUMNS + table case), `test_task_xras_sweep.py`
  (extra passes, second key, a **refactor guard** on the worklist payload shape — not a
  compatibility pin), a `tests/stress` audit-survival case,
  factories, route-map snapshot. `test_xras_api_client.py` stays untouched (pins verified
  class-scoped).

## 9. Phases / PR structure

One PR vs staging with an ordered commit series, after the probe:

0. ✅ **Probe session — DONE 2026-08-21**: `XRAS_WRITE_PROBES.md` written, P0-P7 run,
   `XRAS_WRITE_FIXUPS.md` § 2 verdict table extended, and this plan's provisional signatures
   replaced with what the probes settled.
1. ✅ **Config + client** — `write_enabled`, `admin_client.py`, exceptions,
   `invalidate_person`, helm key + `TestingConfig` pin, 47 tests. Inert without callers.
2. ✅ **Audit table** — the full § 5.2 checklist. Production DDL applied
   **2026-08-21** and verified column-by-column against the tested schema
   (`XRAS_CUTOVER_RUNBOOK.md` § 2d). ⏳ One operator step remains: the
   obfuscated-snapshot regeneration that carries the table into CI. Until it
   lands `test_schema_validation.py` passes locally and fails in CI, because
   the CI database is the committed LFS blob.
3. ✅ **Service + queries + coherence patch** — `sam/manage/xras_remediation.py`,
   `sam/queries/xras_remediations.py`, and `sam/queries/xras_requests.py` (the shared index-entry
   builder). 41 tests.
4. ✅ **Sweep broadening + second cache key** — two extra passes, per-status budgets, the
   `requests_index` key, and a refactor guard on the worklist payload's shape. 11 tests.
5. ✅ **UI** — 10 routes in `xras_remediation_routes.py`, the card, three modals, three form
   schemas, the Accounts Needed merge entry, route-map regen, modal-shell pin, e2e file. 44 tests.
6. ⏳ **Arm** — flip `XRAS_WRITE_ENABLED` in `values.yaml` + its drift test, same commit. **Not
   done**: the whole design is fail-closed until this is a deliberate, reviewed act, and Ben owns
   deploy mechanics. § 10 has the verification steps.

⚠️ **Across commits 1, 3 and 5**: every site that exists only because of the key's privilege
ceiling carries a `PRIVILEGE(#n)` comment keyed to
[`XRAS_WRITE_PROBES.md`](../xras/outgoing/XRAS_WRITE_PROBES.md) § 7, so
`grep -rn 'PRIVILEGE(#' src/` is a live index rather than a doc that quietly goes stale. The PR
body's **Follow-ons** paragraph names § 7 and its top three rows.

### What was built differently from this plan

Three deviations, each because the repo or the probe said otherwise:

1. **No `get_request()` on the admin client, and no `rules{}` anywhere** — probe P1 found that
   route 401s for our credential in every context. Offer legality is derived from the snapshot's
   `actionStatus` plus the `validate` preflight (§ 3).
2. **The merge override is a plain text input, not an FK picker.** A picker over SAM users returns
   a *SAM* username; the merge target is an *XRAS* identity, and the two are not the same
   namespace. The handler resolves whatever is typed against XRAS and fails closed — the API
   *creates* an unknown target rather than refusing.
3. **Roles use the requests-keyed family with a string `roleType`** (P7), not the projcode-keyed
   numeric one this plan preferred. That family cannot resolve our test request numbers, and the
   reason the plan preferred it — avoiding the `isReconciled` create trap — is answered by *not
   sending person parameters*, which this route allows.

One addition the plan did not call for: a **fifth empty state**. When the shared date window hides
every swept row, the card says so and says how many. The default lookback hides precisely the
stale requests this card exists for, so a generic "no matches" would read as "no work".

## 10. Verification

- `pytest` (Ben runs by hand); schema-validation after the model lands; route-map parity regen
  committed.
- Local smoke: `docker compose up webdev --watch` → stub-login as an admin tier → Allocations →
  XRAS: the card renders all four empty states (toggle `XRAS_OUTGOING_ENABLED` / the lever; flush
  Redis first — webapp+webdev share Redis db 0); `docker compose exec webdev sam-admin tasks --run
  xras_sweep --force` to populate the index; verify chips/window/badges; modals render with the
  lever off (disabled buttons) and on.
- Write-path smoke (prod, operator-approved): merge `mding-user-efmlx` → `mding` (Case B, the
  ready-to-fix fixture) from the UI; a withdraw/re-submit round-trip on NCAR0007's Supplement
  30578; confirm audit rows, cache invalidation, **the surgical snapshot patch rendering the new
  state in the same interaction** (no sweep run between write and render), and the Accounts Needed
  row clearing.

## 11. Open questions / assumptions for Ben

1. **Index cohort**: already-pushed Approved requests are excluded (their handoff happened). OK?
2. ✅ **Closed by Phase 0.** Role-op signatures are settled (requests-keyed family, string
   roleType, roleId for removal). The stale NCAR0007 Supplement validated *successfully*, so the
   "withdraw is reversible" copy needs no qualifier.
3. ✅ **Closed by Phase 0** — `benkirk` was added and removed on NCAR0001 via the requests-keyed
   family. ⚠️ Still open, and deliberately not probed: the **projcode-keyed** role family
   (`/v1/roles/<requestNumber>/...`) is provisioned but cannot resolve legacy test request
   numbers, so verifying it would mean writing to a live NCAR project. Not needed for v1.
4. **Key privilege / the refactor register.** A scoped write key (`XRAS_WRITE_FIXUPS.md` § 8.1)
   stays on the XRAS ask register; nothing here blocks on it. ⚠️ Separately, **eleven places in
   this design are shaped by what our key may *not* do** — inventoried in
   [`XRAS_WRITE_PROBES.md`](../xras/outgoing/XRAS_WRITE_PROBES.md) § 7, with the code deleted by
   each. The highest-value ask there is a **read** grant, not a write one: `GET /v1/requests/<rid>`
   alone retires the four structural contortions (`rules{}` inference, the admin client's second
   report-context `reader`, the `request_id` + `request_number` double parameter, and the nested
   roster flattening). Carry § 7 as a named **follow-on in the PR body**, not just in the docs —
   these read as ordinary code within a release and nobody remembers what they compensate for.
5. Feed-B redundancy: revisit dropping "Requests Awaiting a Handoff" only after this ships.

## 12. References

| | |
|---|---|
| [`../xras/outgoing/XRAS_WRITE_FIXUPS.md`](../xras/outgoing/XRAS_WRITE_FIXUPS.md) | The research this builds on: proven write surface, merge decision tree, withdraw addendum, structural rules |
| [`../xras/outgoing/XRAS_OUTGOING_QUERIES.md`](../xras/outgoing/XRAS_OUTGOING_QUERIES.md) | The readable surface, the sweep design, § 7.6 (the `xras_account_event` spec this deliberately does not reuse) |
| [`../xras/incoming/XRAS_CUTOVER_RUNBOOK.md`](../xras/incoming/XRAS_CUTOVER_RUNBOOK.md) | § 2/2c — the DDL-of-record home and the new-table process precedent |
| `https://api.xras.org/apidoc.html` + `apidoc/1.0/*` | The authoritative API docs — static pages, fetchable with plain curl (`/apidoc` without `.html` is a 404 decoy) |
| [`../xras/outgoing/XRAS_WRITE_PROBES.md`](../xras/outgoing/XRAS_WRITE_PROBES.md) | **Phase 0, done.** Every verb measured against production, the one authorization rule, the role-type encoding trap, the net-zero proof |
| Memory: `xras-write-capability`, `xras-raw-payload-corpus` | The condensed live-probe record; the fixture-data ground rules |
