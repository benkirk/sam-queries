# XRAS Request Editor — full-request editor handoff

> Written for a **fresh session with no context**. This is the authoritative
> **forward plan**: it widens the earlier "edit amounts/dates" scope into a
> **full request editor**. The companion runbook
> `docs/xras/outgoing/EDIT_REQUESTS.md` is the **raw test log** for Phases 0 and
> 0.5 (its findings sections record exactly what was measured live).
>
> **Status:** Phase 0 (submit-surface capability) and Phase 0.5 (admin-context
> ceiling) are **both COMPLETE** — findings folded into §1, §2 and §11 below.
> **Part A (read-only detail modal) is BUILT** and **Part B (the amount + date
> editors) is BUILT** — both on branch `xras_details` / PR #463. Part B ships
> the **Phase-0-verified core**: per-resource amounts (edit/add-or-update,
> remove) and allocation dates (set/update/remove), Requested stage live under
> `submit`, Approved stage fail-visible behind `xras_admin_context_available`
> (default off). The live write path was smoke-tested reversibly against
> NCAR0798 through the new client verb (555→556→555, verified, restored). The
> **broader Tier-M editors** — see the split below. **Part C** (`ADMIN_XRAS`
> destructive verbs) is **not started**; its RBAC decision is settled in §4.
>
> **Part B2a (text metadata editors) is BUILT** on the same PR: request
> attributes (`title`/`shortTitle`/`abstract`, `PUT /attributes`) and action
> `userComments` (`PUT /actions/<aid>`) — `submit` context, query params,
> verify-by-reread. Phase 0.75 (`EDIT_REQUESTS.md §8`) proved the whole metadata
> surface authorizes under our key; B2a ships only the fields the reports feed
> reads back (so they can be verified). ⚠️ **XRAS strips trailing whitespace on
> stored text** (measured), so the verify compares whitespace-normalized. Live
> smoke on NCAR0798 (incl. a long abstract) verified + restored.
>
> **Deferred to Part B2b:** grants (POST/PUT/DELETE), publications
> (POST **JSON body**/DELETE), FoS (needs a new `/v1/types/fos` catalog + picker,
> `isPrimary`). **Beyond B2:** resource/opportunity attributes (unprobed),
> documents (multipart upload). `keywords`/`collaborators`/`grantTypeId`/
> `actionType` excluded (unverifiable or too consequential).
>
> **Live writes:** operator sets `XRAS_WRITE_ENABLED=1`. Throwaway requests
> approved for mutation: **NCAR0004, NCAR0099, NCAR0798** (all edits reversible;
> restore originals). Touch no others.

---

## 0. Goal

Turn the XRAS Remediations request modal into a **full-fledged editor for any
existing request** — everything a `MANAGE_XRAS` operator can reach through the
XRAS API — stopping one step short of creating *new* requests (deferred).
Destructive lifecycle verbs are supported but gated behind a **new `ADMIN_XRAS`**
RBAC tier, not `MANAGE_XRAS`.

Why: Allocations → XRAS is a remediation console for a system SAM does not own
but must keep consistent with. Read-only detail + a couple of edits isn't enough;
operators need to fix requests in place. MANAGE_XRAS is already tightly held (the
`nusd` allocation-admin bundle), so a broad editor is appropriate.

---

## 1. Phase 0 findings (measured 2026-08-22 — recap; full log in EDIT_REQUESTS.md §5)

- **Stage model.** Every resource and allocation-date carries a `type` stage:
  **Requested → Recommended → Approved**. Under `XA-CONTEXT: submit` impersonating
  the PI, writes affect **only the Requested stage** — confirmed, not by error but
  structurally. On an Approved request, editing the "amount" as PI creates/updates
  a *Requested* line beside the untouched Approved award.
- **Verified working (submit context, query params, XA-USER = PI):** edit/add
  resource amount (`PUT .../resources/<resourceId>` `{amount,comments}`), remove
  (`DELETE`, Requested-only), set/update/delete allocation dates
  (`POST|PUT|DELETE .../allocation_dates[/<id>]`; POST returns `allocationDateId`).
- **Transport = query params** for all writes (never a body). **Context = submit**,
  **XA-USER = PI** (`resolve_pi(roster_from_payload(...))`).
- **`resourceId` = resource *type* id** (not a per-line id); at most one Requested
  line per resource, so it's unambiguous. `allocationDateId` surfaces in the
  reports payload once a date exists.
- **Read model:** `GET /v1/reports/request_numbers/<n>` returns rich detail:
  `abstract`, `title`, `shortTitle`, `keywords`, `fos[]`, `grants[]`,
  `publications[]`, `roles[]` (nested person), and per action `resources[]`
  (with `type` stage), `allocationDates[]`, `userComments`, `collaborators`,
  `opportunityAttributes`, `resourceAttributes`, `documents`.

---

## 2. Phase 0.5 — admin-context probe: COMPLETE — see §11

**Result:** the write ceiling is the **API key**, not the user. Our key grants
`submit`+`report` only; `review`/`admin` return **401** for every identity tried
(including `benkirk`, who personally holds XRAS `administrator`). So the
**Approved/Recommended stages are not editable with this key** — a **new
admin/review-provisioned XRAS key** is the follow-on. Per the operator's decision
we still **build** the Approved editors now, fail-visible behind a default-off
flag. Full evidence and consequences in §11.

---

## 3. The editable surface (full inventory → capability tiers)

All under `/v1/requests/<rid>/...`. Context is `submit` (PI) unless a stage/field
requires `admin` (TBD by Phase 0.5). Fields per the apidoc.

### Tier M — `MANAGE_XRAS` (the full non-destructive editor)
| Group | Endpoint(s) | Fields |
|---|---|---|
| Resource amount | `PUT|DELETE .../actions/<aid>/resources/<resourceId>` | `amount`, `comments` (+ Approved stage via admin, if permitted) |
| Allocation dates | `POST .../actions/<aid>/allocation_dates`; `PUT|DELETE .../allocation_dates/<id>` | `beginDate`, `endDate` |
| Request attributes | `PUT .../attributes` | `abstract`, `title`, `shortTitle`, `keywords`, `grantTypeId`, `isSupportedByGrants` |
| Action fields | `PUT .../actions/<aid>` | `actionType`, `collaborators`, `userComments` |
| Roster / roles | `POST|DELETE|PUT .../roles/...` (partly built) | add/remove/update people |
| Fields of science | `PUT|DELETE .../fos/<fosTypeId>` | FoS set |
| Grants | `POST .../grants`; `PUT|DELETE .../grants/<grantId>` | grant metadata |
| Publications | `POST .../publications`; `DELETE .../publications/<id>` | publications |
| Resource attributes | `PUT|DELETE .../actions/<aid>/resource_attributes[/<id>]` | per-resource Q&A |
| Opportunity attributes | `PUT|DELETE .../actions/<aid>/opportunity_attributes[/<id>]` | per-opportunity Q&A |
| Documents | `POST|DELETE .../actions/<aid>/documents[/<id>]` | attachments |
| Submit / withdraw action | `POST|DELETE .../actions/<aid>/submit` (built) | state transition |

### Tier A — `ADMIN_XRAS` (new; destructive lifecycle — NOT for MANAGE_XRAS)
| Op | Endpoint | Note |
|---|---|---|
| Delete whole request | `DELETE /v1/requests/<rid>` | irreversible in XRAS |
| Renew request | `POST /v1/requests/<rid>/renew` | spawns a renewal |
| Add an action | `POST /v1/requests/<rid>/actions` | near new-request territory |

### Deferred (out of scope this round)
`POST /v1/requests` (create new request), `POST /v1/requests/<rid>/actions/<aid>/documents` upload UX beyond a link, merge (already built separately).

---

## 4. RBAC — add `ADMIN_XRAS` (`src/webapp/utils/rbac.py`)

- Add `ADMIN_XRAS = "admin_xras"` to `class Permission`. The `admin_` prefix is
  **not** matched by any `ALL_*` aggregate (they match `view_`/`edit_`/`create_`/
  `delete_`), so it fails closed and must be granted explicitly — same property
  that protects `MANAGE_XRAS`.
- **DECIDED (operator, 2026-08-22): `ADMIN_XRAS` rides with `SYSTEM_ADMIN`, NOT
  `_ALLOCATION_ADMIN`.** Destructive XRAS lifecycle verbs are a system-admin
  capability, not an allocation-admin one — so `MANAGE_XRAS` stays in
  `_ALLOCATION_ADMIN` (nusd/csg keep the non-destructive editor, Part B) but
  `ADMIN_XRAS` is granted only wherever `SYSTEM_ADMIN` is. Today that is the
  `benkirk` full-perms override (`USER_PERMISSION_OVERRIDES`, `[p for p in
  Permission]`), which picks up the new member for free; no group bundle holds
  `SYSTEM_ADMIN` (it gates rate-limits, tasks-detail, notifications rows, config
  writes). **Do NOT add `ADMIN_XRAS` to `_ALLOCATION_ADMIN`.** If a system-admin
  group bundle is ever introduced, `ADMIN_XRAS` belongs there beside
  `SYSTEM_ADMIN`. Conceptually: `VIEW_XRAS ⊆ MANAGE_XRAS` (allocation-admin) and
  `ADMIN_XRAS` sits with `SYSTEM_ADMIN`, above it.
- Routes: non-destructive editor routes use `@require_permission(Permission.MANAGE_XRAS)`;
  destructive routes use `@require_permission(Permission.ADMIN_XRAS)`. Buttons for
  destructive verbs render only when `has_permission(current_user, ADMIN_XRAS)`.
- Tests: extend the RBAC matrix — a MANAGE_XRAS-only user gets 403 on destructive
  routes; update `tests/unit/test_rbac*.py` and any permission snapshot.

---

## 5. Client — extend `XrasAdminClient` (`src/sam/integration/xras_api/admin_client.py`)

- **Parameterize `XA-CONTEXT` per call.** Today it's hardcoded `submit`
  (`XA_ADMIN_CONTEXT='submit'`, :76). Add a `context` argument to `_write`/verbs
  (`submit` default; `admin` for Approved-stage and privileged fields). Keep the
  reader on `report`.
- **Transport:** send fields as **query params** (`params=`) — `_write` already
  supports `params`; no body needed. Add the new verbs, each single-attempt +
  verify-by-reread (three-valued `XrasWriteResult.verified`), mirroring the five
  existing:
  - resources: `update_resource_amount`, `remove_resource` (have shapes from Phase 0)
  - dates: `set_action_dates`, `update_action_dates`, `remove_action_dates`
  - attributes: `update_request_attributes(**fields)` → `PUT .../attributes`
  - action: `update_action(**fields)` → `PUT .../actions/<aid>`
  - fos/grants/publications/resource_attributes/opportunity_attributes/documents
  - destructive (Tier A): `delete_request`, `renew_request`, `add_action`
- Verify-by-reread compares the changed field back via `reader.get_request_by_number`.
  A 4xx raises `XrasWriteRejected` carrying XRAS's `errors[]` (render it).

## 6. Service + audit vocab (`src/sam/manage/xras_remediation.py`, `xras.py`)

- Extend `XRAS_REMEDIATION_OPERATIONS` (`xras.py:439`) with the new operations
  (`update_resource_amount`, `add_resource`, `remove_resource`, `set_action_dates`,
  `update_action_dates`, `remove_action_dates`, `update_request_attributes`,
  `update_action`, `update_fos`, `update_grant`, `update_publication`,
  `update_resource_attribute`, `update_opportunity_attribute`, `manage_document`,
  and Tier-A `delete_request`, `renew_request`, `add_action`). Cover in
  `test_xras_remediation_event.py`.
- One service fn per op, mirroring `withdraw_action`/`change_role`: write the
  `attempted` audit row on the private session **before** dispatch, `complete`
  after, capture `before_state`/`after_state`, then patch the re-fetched request
  into cache (`request_index_entry`). Records the acting context (submit/admin).

## 7. Forms (`src/sam/schemas/forms/xras_remediation.py`, `HtmxFormSchema`)

One schema per editable section (export from `forms/__init__.py`):
`XrasResourceAmountForm` (`amount` Decimal≥0, `comment`), `XrasActionDatesForm`
(`begin_date`, `end_date`; reuse `assert_date_range()`/`normalize_end_date()`),
`XrasRequestAttributesForm` (`abstract`, `title`, `short_title`, `keywords`,
`is_supported_by_grants`), `XrasActionFieldsForm` (`user_comments`,
`collaborators`), and thin schemas for fos/grant/publication/attribute rows. IDs
come from the URL. PUT-gating: update dicts gated on keys present in the original
`request.form`.

## 8. Routes + modal UI

- **Part A (read-only) first** — unchanged from prior plan and unblocked:
  `xras_request_detail(request_number)` route + `xras_request_detail.html` partial
  into the shared `#auditDetailsModal`; "Details…" link in the **non-toggle SAM
  cell** of `xras_remediations_card.html` (never the collapse-toggle Request cell —
  `test_collapse_trigger_rows.py`). Render resources/dates **grouped by stage**
  (Requested/Recommended/Approved) so requested-vs-awarded is visible; show all
  the rich sections read-only.
- **Part B (MANAGE_XRAS editors)** — each section in the modal gets an inline
  "Edit…" affordance → GET modal-form + POST write, each a
  `_XrasRemediationHandler` subclass (its `exception_map` renders
  `XrasWriteRejected.errors[]`), re-rendering the section in place after a write
  (like `_XrasRoleAddHandler`). Gated on `xras_write_configured()`; disabled with a
  reason when the write lever is off. Amount editors expose the **Requested** stage
  always, and the **Approved** stage when Phase 0.5 says we're privileged (else the
  Approved editor renders disabled with "requires elevated XRAS key").
- **Part C (ADMIN_XRAS destructive)** — delete-request / renew / add-action
  buttons, `@require_permission(ADMIN_XRAS)`, `hx-confirm`, rendered only for
  ADMIN_XRAS holders.

## 9. Build sequencing (one PR, ordered commit series)

1. Part A read-only modal (+ stage-grouped rendering) — ships first, needs no writes.
2. Phase 0.5 admin probe → record findings (§11), decide Approved-editing live vs flagged.
3. Part B editors (client verbs + service + forms + routes), Requested stage + Approved (fail-visible).
4. `ADMIN_XRAS` RBAC + Part C destructive verbs.
Each behind the existing `XRAS_WRITE_ENABLED` lever (webapp-only; never for tasks).

## 10. Tests

`test_xras_admin_client.py` (new verbs: method+path+params+context, single-attempt,
verify-by-reread, lever gates off); `test_xras_remediation_service.py` (audit
survives, cache patch, per-op); `test_xras_remediations.py` (access control incl.
MANAGE_XRAS vs ADMIN_XRAS 403 split, disabled-when-lever-off, validation);
`test_xras_remediation_event.py` (new vocab); `test_collapse_trigger_rows.py`;
RBAC tests for `ADMIN_XRAS`. Run:
```
pytest tests/unit/test_xras_remediations.py tests/unit/test_xras_admin_client.py \
       tests/unit/test_xras_remediation_service.py tests/unit/test_xras_remediation_event.py \
       tests/unit/test_collapse_trigger_rows.py tests/unit/test_rbac_permissions.py -v
```

## 11. Phase 0.5 findings — admin probe COMPLETE (2026-08-22)

**The write ceiling is the API *key*, not the user.** Our `XA-API-KEY` is
provisioned for `submit` + `report` contexts only; `review`/`admin` are not
granted to it. Effective authority = *key context grant* ∩ *user role/permission*.

Evidence (all against throwaway NCAR0099, Approved 530181 = 10.0; every attempt
left it 10.0 — nothing to restore):

| `GET /v1/permissions/<u>` | result |
|---|---|
| `arcguest` (our XA-USER), `lam`, `sorbjan`, `harrter` (PIs) | `[]` — no named permissions; submit worked via holding a **role** on the request, not a permission |
| `benkirk` | `administrator`, `review-impersonator`, `read-admin` — a real XRAS admin **as a person** |

| write attempt | result |
|---|---|
| `PUT .../resources/530181` ctx=`admin`, user=`arcguest` | **401** |
| ctx=`admin`/`review`, user=`sorbjan` (PI) | **401** |
| ctx=`admin`/`review`/`submit`, user=`benkirk` (XRAS admin) | **401** |

So even `benkirk`, who personally holds XRAS `administrator`, cannot write the
Approved stage through **our** key. The `permissions/<user>` endpoint reflects the
*person's* XRAS permissions, but our key caps the reachable contexts.

**Conclusion / consequence for the build:**
- **Editable now (this key):** the **Requested** stage only — amounts, dates,
  add/remove resource, request attributes, action fields, roster, provenance —
  all via `submit`, `XA-USER` = a **role-holder on the request** (the PI).
- **Not editable now:** Approved/Recommended stages and anything needing
  `review`/`admin` context. **Follow-on:** request a **new XRAS API key
  provisioned for `admin`/`review`** from XRAS/ACCESS ops (a user-permission grant
  like benkirk's is insufficient — it must be the key).
- **Per the operator's decision, still build the Approved-stage editors now**,
  fail-visible: they render (gated behind `XRAS_WRITE_ENABLED` and, for Approved,
  a `xras_admin_context_available` flag defaulting **false**) and surface XRAS's
  401 until the elevated key lands. The client's `context=` argument (§5) is what
  flips on that day — no rework.
- The submit surface authorizes on **request role**, not on `permissions[]`, so
  the impersonation logic (`resolve_pi`) stays exactly as Phase 0 proved.
