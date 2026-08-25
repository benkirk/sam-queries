# XRAS Data-Model Uplift — Handoff Plan

**Status: Track A SHIPPED as PR #479 (2026-08-24). Track B REPLANNED 2026-08-25
after the triage week: B1–B3 plus four triage-week additions ship as one PR on
`xras_incoming_triage`; B4 (the `rules`/`validate` overlay) is PARKED with its
premise corrected.** Findings from the 2026-08-24 audit stay here; the
2026-08-25 audit of the tree is folded in.

## Context

PR #477 (request families) exposed a class of defect: the XRAS outgoing API
returns linked/plural structures (one `requestNumber` → a LIST of request
lines, each its own `requestId` with an `actions[]` trail) that our code
flattened to `result[0]`. An audit of both integration directions — live
payloads (GET-only) and the api.xras.org endpoint catalog — found more
members of that class, several outright bugs, and one wrong premise.

The two directions must not depend on each other for any capability, but
their data models are similar — findings from one side are checked against
the other. Explicitly OUT of scope: Derecho `productionBeginDate` /
`productionEndDate` staleness (consumed by another system).

## Track A — incoming uplift — shipped (#479)

Seven commits plus the `warnings` column: numberless / digit-free grant
numbers warn instead of 422 (11 of 24 live preflight failures cleared;
`PRJ013992 BWI` and `2423211` still fail, correctly — the contract-blockers
strip now names them); resolved contracts deduped; `request_id` persisted on
`xras_action_log` (DDL applied to prod, LFS blob regenerated); role
date-windowing in the worklist and modal rosters; Feed A reads inline
`roles[].person{}`; `fos[0]` fallback warned; `panels[]` can add a panel
authorization but never withdraws one; `sam-admin xras --validate-vocabulary`.
Details: `docs/xras/incoming/implemented/` and the commit series of #479.

Follow-ons still NOT built (unobserved in corpus or a product call):
`resources[]` dedupe guard in the four resource-walking handlers;
`opportunityQA[]` recording; `grants[]` field enrichment beyond the number
(`GET /v1/funding_agencies` exists, ~30 rows); `requestPeopleRoleId` /
`actionResourceId` as per-line identities.

## Done since this plan (triage week, PRs #481/#482)

- Every write route passed a Session where the service wants a factory: the
  first production merge wrote no audit row. Fixed with a route-level gate.
- `is_pending_work` treats a `seen_in_log` action whose latest log row is
  `received`/`failed`/`manual` as pending; `log_seen_for` picks the highest
  log id (the sweep and the re-check shared two unordered loops).
- `_refresh_index_entry` re-runs the preflight; Feed A drops a failed post's
  roster once a later real post exists, and any username a verified merge
  deleted.
- The identity-merge feature: `docs/plans/XRAS_IDENTITY_MERGE.md`.

Facts measured that change premises below: XRAS `GET /v1/people/<u>` proxies
SAM's identity service (every active SAM account resolves, reconciled);
`search/people` matches name/username only, capped at 20; `isReconciled`
never flips; `email_address.email_address` is `utf8mb3_bin`.

## Track B — outgoing fixes (one PR, ordered commits)

Audited 2026-08-25: none of B1–B3 had started; the modal half of B3 was
already done by #482's `_detail_context` (primary line via `_primary_line`).

### D0 — this doc, the XA-USER record, the wrong comments

`XRAS_OUTGOING_QUERIES.md` § 4.3 records the `rules{}` finding (XA-USER-gated,
not key-gated) with the measured payloads. The three sites asserting "401 for
our credential — PRIVILEGE(#1)" (`sam/queries/xras_requests.py`,
`webapp/dashboards/allocations/xras/_shared.py`,
`partials/_xras_remediation_actions.html`) now say the read is XA-USER-scoped
and parked. The other ten `PRIVILEGE(#n)` sites are untouched.

### B1 — `allocationDateType` wire key

`modals.py` reads `d.get('type')` from `allocationDates[]`; the wire key is
`allocationDateType` (the incoming preflight already reads it right). The
modal's allocation-date stage label renders blank in production, and the
fixture in `tests/unit/test_xras_remediations.py` spells the key `'type'`, so
the suite structurally cannot catch it — the third fixture-agrees-with-the-bug
occurrence after `resourceRepositoryKey`. Fix code + fixture; add a gate that
the fixtures' and the modal's `allocationDates[]` keys are within the
documented set (`XRAS_OUTGOING_QUERIES.md` § 3.2).

### B2 + B3 — one family seam: the primary line, everywhere

`get_request_by_number` returns `family[0]`, and every write-verification read
in `admin_client.py` goes through it (`_actions`, `roster`/`resolve_pi`,
`update_request_attributes` before/after, `delete_request` before/after — the
delete verify is `after is None`, so a three-line project reports "Deleted 1
of 3"). Writes target the primary line (highest global `actionId`); verifies
read line 0. `_refresh_index_entry` and `recheck_readiness` rebuild the card
entry from line 0 too, and the sweep's index is first-copy-wins on
`requestNumber` — where "first" is the **highest `requestId`** (descending
page order), not the highest `actionId`, so a New+Renewal project can index
the wrong line.

Fix: hoist `primary_line(lines)` (and `line_by_id`) into
`sam/queries/xras_requests.py`; a client `get_request_line(number, *,
request_id=None)`; **delete `get_request_by_number`** and repoint its callers
(a grep gate keeps it gone). Verifies select the line the write targeted by
`request_id`; delete-verify becomes "that line is absent from the family".
Refresh, re-check, and the sweep index all use the primary line — the same
one the modal shows.

### B5 — the affiliation class (incoming; observed twice)

A merged PI with no current `user_institution` and no `user_organization`
fails the mnemonic route with legacy's lab-route string, *Could not determine
Mnemonic code for internal PI via organization* — misleading for an external
PI (NCAR4262: the Miami row was end-dated 2026-06-24 by the upstream
affiliation sync; NCAR4261 next). A new builder,
`no_current_affiliation_for_pi`, names the real gap; declared divergence, like
the lab-route one. SAM has no write surface for `user_institution`; the
playbook § 3 recipe says where the fix lives. The mnemonic report matches
strings by equality, so the new message stays out of the org-linking strip.

### B6 — `isReconciled` on the merge screen

The client returns the full person shape; `_merge_candidates` drops
`isReconciled` on the one screen where an irreversible target is chosen. Carry
it and badge it in the card's vocabulary.

### B7 — roles-modal warning nuance

"An unknown username creates a new XRAS identity" is true only for names that
are not SAM accounts — every SAM username resolves. Qualify the copy; confirm
the picker is the SAM user picker.

### B4 — PARKED: `rules`/`validate` overlay (premise corrected)

The comment this replaces said `rules{}` is 401 for our credential. Measured
2026-08-24: it is **XA-USER-gated** — 401 as `arcguest`, 200 as the request's
PI in both contexts (payloads in `XRAS_OUTGOING_QUERIES.md` § 4.3). No config
lever is needed; the XA-USER is per call and the write paths already resolve
the PI. Parked until an operator needs authoritative offers; the design when
it is built:

- Client `get_request_rules(request_id, *, xa_user)` and a read-context
  `validate_action`; gate offers on `existingActions[].allowedOperations` /
  `allowedActions` (+ `availableResourceIds`); on any failure fall back to
  today's state heuristics — fail-open, nothing goes dark.
- Preflight gains an `xras_validate` check line carrying XRAS's own strings;
  unreachable is a gap, not a fail.
- **Phase 0 first, read-only:** map the `allowedOperations` vocabulary across
  Submitted / Under Review / Incomplete as the PI identity. Only `Edit`/`Delete`
  are observed; how Withdraw is represented (its wire verb is
  `DELETE .../submit`) is unverified. Do not gate Withdraw until measured.
- Opportunity payloads are NOT a substitute: `numbers[]` limits are sparse and
  stale, `resourceState` reads `Unavailable` for everything; only
  `rules.resourceIdsAvailableForNewRequest` is a usable secondary signal.

## Probe recipe (read-only)

```
curl -s -H "XA-ALLOCATIONS-PROCESS: NCAR" -H "XA-API-KEY: $XRAS_API_KEY" \
     -H "XA-USER: <pi-username>" -H "XA-CONTEXT: report" \
     https://api.xras.org/v1/requests/<requestId>
```

GET only — the key is write-provisioned and a person merge is irreversible.
`/v1/types/all` wraps its result in `response`, not `result`.

## Traps

- Tests pin `XRAS_API_KEY=''` and both levers off before dotenv loads; outbound
  guards raise on any real HTTP — patch the transport on the client instance
  or replace `from_environment`.
- Fixture keys must match documented wire keys; B1 adds the gate the
  remediation fixtures lacked.
- `tests/unit/test_xras_error_coverage.py` is a declaration matrix: B5's new
  builder needs a `SCENARIOS` entry or the matrix fails.
- Route-level tests: SAVEPOINT, not `session.rollback()` (the #474 trap).

## Verification

- Full XRAS unit/api/task suites + `test_docs.py` after each commit.
- B2+B3 live, read-only from a pod: `get_request_line('UCUB0089')` is the line
  with the highest `actionId` (three lines; primary not `lines[0]`), and
  `line_by_id` resolves each; the next sweep indexes a multi-line project on
  that line.
- B5 against the prod snapshot: `sam-admin xras --readiness` shows NCAR4262
  with the affiliation message, not the internal-PI one.
- B1/B6 in a browser: the allocation-date stage label renders; merge
  candidates carry the identity badge.

## Deferred (from the 2026-08-24 investigation, still open)

Review-pipeline signals unread (`returnedForCorrections`, `states[]`,
`adminComments`, `finalReviews[]`); `reports/username` award detail and
`opportunityId` dropped by `person_roles_from_payload` (the user modal cannot
link the opportunity modal); `reports/allocations` as a requested-vs-awarded
feed; `_as_dict` first-elementing list responses in the client
(`get_person`, `get_person_roles`, `get_opportunity`); two "last activity"
definitions between the request index and the person feed.
