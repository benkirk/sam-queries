# Editing XRAS requests from SAM — the request editor

The **XRAS Remediations** card on *Allocations → XRAS* opens a per-request modal
that is a **scoped editor for an existing XRAS request**: read-only detail, plus
in-place editors for resource amounts, allocation dates, and request/action text,
plus a destructive admin tier. It is a subset of the external XRAS admin app, never
a replacement — SAM does not own XRAS but must keep it consistent with the systems
it does own, and an operator needs to fix a request in place rather than bouncing
between two consoles.

Read this alongside `XRAS_OUTGOING_QUERIES.md` (the read side and the sweep) and
`XRAS_WRITE_PROBES.md` / `XRAS_WRITE_FIXUPS.md` (the original merge / withdraw /
re-submit / roster surface this editor extends).

---

## 1. Permission tiers

| Permission | Grants | Held by |
|---|---|---|
| `VIEW_XRAS` | the action log — an audit surface | every operator bundle (via `ALL_VIEW`) |
| `MANAGE_XRAS` | the Remediations card and the **full non-destructive editor** (merge, withdraw / re-submit, roster, amounts, dates, request attributes, action fields) | `_ALLOCATION_ADMIN` (`nusd`, `csg`) |
| `ADMIN_XRAS` | the **destructive lifecycle** — delete a request, renew it, add an action | rides with `SYSTEM_ADMIN`, **not** `_ALLOCATION_ADMIN` (today only the full-admin override) |

`MANAGE_XRAS` and `ADMIN_XRAS` both use the `manage_`/`admin_` prefix, which is
matched by **no** `ALL_*` aggregate (those match `view_`/`edit_`/`create_`/
`delete_`), so both fail closed and must be granted explicitly. A `MANAGE_XRAS`
operator gets the whole editor but never the destructive verbs; those render only
for an `ADMIN_XRAS` holder and the routes 403 otherwise.

## 2. Levers (all fail-closed, `sam/integration/xras_api/config.py`)

| Env var | Governs | Default |
|---|---|---|
| `XRAS_OUTGOING_ENABLED` | reads (the sweep, the modals' live reads) | off |
| `XRAS_WRITE_ENABLED` | **all writes** | off (on `helm/values.yaml` never; webapp-only, never tasks) |
| `XRAS_ADMIN_CONTEXT_ENABLED` | the Approved/Recommended-**stage** editors (see §4) | off |

When the write lever is off, the editors **render disabled with a reason** rather
than hiding — a control that vanishes teaches nobody that a switch exists. Every
write is additionally refused at the service, belt-and-braces.

## 3. The editable surface

All under `/v1/requests/<rid>/...`, `XA-CONTEXT: submit`, `XA-USER` = the request's
PI (`resolve_pi`). Transport is **query params** except where noted.

| Group | Endpoint(s) | Tier | Notes |
|---|---|---|---|
| Resource amount | `PUT|DELETE .../actions/<aid>/resources/<resourceId>` | MANAGE | add-or-update / remove a **stage** line; `resourceId` is the resource *type* id |
| Allocation dates | `POST .../allocation_dates`; `PUT|DELETE .../allocation_dates/<id>` | MANAGE | `POST` returns `allocationDateId` |
| Request attributes | `PUT .../attributes` | MANAGE | `title` / `shortTitle` / `abstract` |
| Action fields | `PUT .../actions/<aid>` | MANAGE | `userComments` |
| Delete request | `DELETE /v1/requests/<rid>` | **ADMIN** | irreversible in XRAS |
| Renew request | `POST /v1/requests/<rid>/renew` | **ADMIN** | spawns a renewal |
| Add action | `POST /v1/requests/<rid>/actions` | **ADMIN** | closed action-type picker |

**Deferred** (probed and authorized under our key, not yet built — a *Part B2b*):
grants (`POST|PUT|DELETE .../grants`), publications (`POST` **JSON body** / `DELETE
.../publications/<id>`), fields of science (`PUT|DELETE .../fos/<fosTypeId>`, needs
`isPrimary` and a new `/v1/types/fos` catalog + picker). **Beyond that:** per-action
resource/opportunity attributes (unprobed) and documents (multipart upload).

**Deliberately excluded:** `keywords` and `collaborators` — the reports read-back
does not echo them, so they cannot be verified (see §5), and every write here
verifies. `grantTypeId` / `isSupportedByGrants` / `actionType` — consequential
classification, left to an explicit later decision.

## 4. The stage model, and why the award is not editable here

Every resource and allocation-date carries a `type` **stage**: `Requested →
Recommended → Approved`. Under `XA-CONTEXT: submit` impersonating the PI, writes
affect **only the Requested stage** — measured, not by error but structurally. On
an Approved request, "editing the amount" creates or updates the *Requested* figure
beside the untouched award. So the editor operates on **what was requested**; only
an approver changes **what was awarded**.

**The write ceiling is the API key, not the user.** Our `XA-API-KEY` grants
`submit` + `report` contexts only; `review`/`admin` return **401 for every
identity** — including a person who personally holds XRAS `administrator`.
Effective authority = *key context grant* ∩ *user role/permission*. So the
**Approved / Recommended stages are not editable with this key.**

The Approved-stage editors are nonetheless **built, fail-visible**: they render
disabled (`XRAS_ADMIN_CONTEXT_ENABLED` off, "requires an elevated XRAS key"). The
client parameterises `XA-CONTEXT` **per call** (`_headers(xa_user, context)`), so
the day XRAS/ACCESS issues an `admin`/`review`-provisioned key, flipping the lever
and passing `context='admin'` turns them live with no rework. A per-*user*
permission grant is not enough — it must be the key.

## 5. Wire mechanics (measured, not from the apidoc)

- **Transport is query params** for every write **except publications**, which
  wants a **JSON body** (params → 400 "JSON parse error"). `_write` supports both.
- **`resourceId` is the resource *type* id** (not a per-line id); there is at most
  one line per resource per stage, so `(action, resourceId, stage)` is unambiguous.
- **Sub-resource creates return their id** in `result` (`allocationDateId`,
  `grantId`, `publicationId`) — captured for the audit row and the delete path.
- **XRAS normalizes trailing whitespace** on stored text (a 1020-char abstract read
  back at 1019, identically via params and a body). Verify-by-reread therefore
  compares **whitespace-stripped**, or a good long-text write reports `unverified`.
- **FoS** needs `isPrimary` on the `PUT`; it is a set-membership toggle on
  `fosTypeId` (no returned id).

## 6. The fail-visible contract (relied on everywhere)

A write gets **one attempt** — a retried delete could delete twice, a retried
submit could double-fire a workflow. **A 200 is not success**; every verb
**verifies by re-reading** and returns a three-valued verdict
(`verified` / `unverified` / `rejected` / `error` — `XrasWriteResult`). The audit
row (`xras_remediation_event`) is committed `attempted` on a **private session
before** the write leaves, so a record survives a crash mid-call; it is closed on a
fresh session after. Modal GETs degrade with a **200** body — htmx will not swap a
4xx into an open modal. A verified write patches the affected request back into the
card snapshot so the operator sees their change immediately.

⚠️ The **ADMIN tier verbs were not live-probed** (a delete cannot be tested without
deleting; renew/add-action pollute the request), so they ship fail-visible and
unprobed: a 401 surfaces in the modal. They are gated on `ADMIN_XRAS`, the write
lever, and `hx-confirm`.

## 7. Code map

| Layer | Path |
|---|---|
| Read client (GET) | `sam/integration/xras_api/client.py` — `get_request_by_number` → `reports/request_numbers/<n>` |
| Write client | `sam/integration/xras_api/admin_client.py` — per-call `context`, single-attempt `_write`, verify-by-reread |
| Config / levers | `sam/integration/xras_api/config.py` — `write_configured`, `admin_context_available` |
| Service (audit + cache) | `sam/manage/xras_remediation.py` — `_editor_op`, audit-before-dispatch |
| Audit model + vocab | `sam/integration/xras.py` — `XrasRemediationEvent`, `XRAS_REMEDIATION_OPERATIONS` (`operation` is `VARCHAR(24)` — keep op names ≤ 24) |
| Forms | `sam/schemas/forms/xras_remediation.py` |
| RBAC | `webapp/utils/rbac.py` — `MANAGE_XRAS` / `ADMIN_XRAS` (§1) |
| Routes + handlers | `webapp/dashboards/allocations/xras/remediation.py` (editors + handlers) · `webapp/dashboards/allocations/xras/modals.py` (the read-detail modal they hang off) |
| Templates | `.../partials/xras_request_detail.html`, `_xras_remediation_actions.html`, and the per-editor `xras_*_form.html` |

## 8. Follow-ons

1. **An `admin`/`review`-provisioned XRAS key** — unblocks Approved/Recommended
   editing (§4); flip `XRAS_ADMIN_CONTEXT_ENABLED` and the built editors go live.
2. **Part B2b** — grants, publications, FoS editors (surface authorized in §3).
3. **Beyond** — resource/opportunity attributes, document upload.
