# XRAS companion API (`/api/v1/xras`)

SAMuel's own routes about XRAS requests, for tools that work the XRAS queue (today the ARC
Allocation Recommender). Not the frozen inbound `/api/xras/v1` blueprint, which is XRAS's
contract with SAM.

| Route | Auth | Permission |
|---|---|---|
| `POST /api/v1/xras/notes` | API key only | `post_xras_notes` |
| `GET /api/v1/xras/award-audit/<projcode>` | API key or session | `view_xras_award_audit` |

Under `RBAC_SOURCE=db` a key needs the permission through a `samuel_role_grant` row
(`sam-admin rbac --grant apikey:NAME --permission P`); a key without it is 403. The notes
route's body and design: [`ARC_RECOMMENDER_NOTES.md`](../plans/implemented/ARC_RECOMMENDER_NOTES.md) § 3.

## `GET /api/v1/xras/award-audit/<projcode>`

What XRAS awarded against what SAM allocated, for one SAM project: the same audit and envelope
as `sam-admin xras --award-audit --request PROJCODE` (expired awards included). Read-only.
Logic: `src/sam/queries/xras_award_audit.py`; route: `src/webapp/api/v1/xras_award_audit.py`;
output schema: `src/sam/schemas/xras_award_audit.py`.

**Cost**: one XRAS read per call (`reports/request_numbers/<projcode>`, the request family;
never the paged `reports/requests` report), plus the resource catalog from the day-long
`xras_resources` cache. The family is not cached: each call sees XRAS as it is now.

| Status | Body |
|---|---|
| 200 | the envelope below |
| 401 | no credentials |
| 403 | the caller lacks `view_xras_award_audit` (checked before the projcode is looked up) |
| 404 | `{"error": "Project X not found"}`: SAM has no such projcode |
| 503 | `{"error": "...", "reason": R}`: `not_configured` (the XRAS levers are off), `unreachable`, or `no_resource_catalog` |

A projcode SAM knows but XRAS does not answers 200 with `checked: 0`.

```json
{
  "kind": "xras_award_audit",
  "projcode": "UXYZ0001",
  "as_of": "2026-11-23",
  "include_expired": true,
  "complete": true,
  "checked": 2,
  "mismatched": 1,
  "retired_skipped": 0,
  "counts": {"project_missing": 0, "resource_unmapped": 0, "allocation_missing": 0,
             "amount_differs": 1, "begin_differs": 0, "end_differs": 0,
             "renamed_pending": 0, "project_inactive": 0, "approximate": 0, "sam_only": 0},
  "rows": [
    {"projcode": "UXYZ0001", "xras_resource": "Derecho", "sam_resource": "Derecho",
     "issues": ["amount_differs"], "xras_amount": 500000.0, "sam_amount": 1000000.0,
     "xras_begin": "2024-08-19", "xras_end": "2029-06-30",
     "sam_begin": "2024-08-19", "sam_end": "2029-06-30",
     "allocation_id": 123456, "sam_projcode": "UXYZ0001", "units": "Core-hours",
     "action_ids": [400001, 400002]}
  ]
}
```

### Envelope fields

| Field | Meaning |
|---|---|
| `kind` | always `xras_award_audit` |
| `projcode` | the SAM projcode audited (stored spelling) |
| `as_of` | the date awards were scoped and compared on |
| `include_expired` | always `true` on this route |
| `complete` | always `true` on this route (one family, no page budget) |
| `checked` | number of `rows` |
| `mismatched` | rows carrying at least one mismatch kind |
| `retired_skipped` | awards on a decommissioned SAM resource, counted and not listed |
| `counts` | every issue kind below, with its row count (zeros included) |
| `rows` | one per XRAS award per resource, plus `sam_only` rows |

### Row fields

| Field | Meaning |
|---|---|
| `projcode` | the XRAS request number |
| `sam_projcode` | the SAM project it landed on; differs only with `renamed_pending`; `null` with `project_missing` |
| `xras_resource` / `sam_resource` | XRAS resource label / mapped SAM resource name (`null` when unmapped, or on a `sam_only` row) |
| `issues` | list of kinds, below; empty means it matches |
| `xras_amount` / `sam_amount` | awarded / allocated, in `units` |
| `xras_begin`, `xras_end`, `sam_begin`, `sam_end` | ISO dates or `null` |
| `allocation_id` | the SAM allocation compared against |
| `units` | XRAS `resourceUnits` |
| `action_ids` | the approved XRAS actions folded into the award |

Mismatch kinds: `project_missing`, `resource_unmapped`, `allocation_missing`, `amount_differs`
(more than 0.01 apart), `begin_differs` (after the commission-date clamp), `end_differs`.
Context kinds, not a mismatch by themselves: `renamed_pending`, `project_inactive`,
`approximate` (a Transfer was folded in), `sam_only` (a SAM allocation live today on an
XRAS-mapped resource that no award names).

Adding a field is additive; renaming or removing one is a contract change for the recommender.
`tests/api/test_xras_award_audit_api.py` pins the schema to `AwardAudit.to_dict()`.
