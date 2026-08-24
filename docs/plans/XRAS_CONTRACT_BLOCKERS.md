# XRAS Contract Blockers — surface, then assist

**Status: Phase 0 MEASURED 2026-08-24 — trigger met; Phase 1 scheduled for
the post-cutover branch.** Sketched during cutover week
(`XRAS_TRIAGE_WEEK.md`); builds on the grant handling shipped in PR #479
(`XRAS_DATA_MODEL_UPLIFT.md`, Track A commit 1).

## The gap

The Pending-Users card makes an **account** blocker obvious: one row per
username with role, source, identity state, and days waiting. A
**contract** blocker has no surface. It lives only as the 422 string
`Cannot find contract for grant number "…" ("…")` inside a failing
action's preflight messages, so an operator learns that one `contract` row
would unblock a push only by reading each failing request's tooltip.

Measured 2026-08-24 (cutover day), over every New request not yet in SAM —
23 Submitted plus the approved-but-unposted: **6 of 32 carry a grant number,
and 0 of the 6 resolve** against `contract` (core-number `ilike`, the
handler's own rule).

| Request | Stage | Grant number | Other blockers |
|---|---|---|---|
| NCAR4212 | Approved | `PRJ013992 BWI` | the mnemonic failure too — **two** blockers, not one (the preflight lists both) |
| NCAR4231 | Approved | `2423211` (a real NSF award) | placeholder PI |
| NCAR4280 | Submitted | `2624974` | two placeholder identities |
| NCAR4287 | Submitted | `2331527` | inactive PI |
| **NCAR4293** | Submitted | `001368-00183` | **none** |
| **NCAR4300** | Submitted | `ISS 25-643` | **none** |

The last two are the case this doc exists for: clean rosters, both under the
"NCAR External Projects" opportunity, whose approvals are quick — the day
they are approved, a missing `contract` row is the *only* thing between them
and a 200, and nothing today says so. Four of the six numbers are project
references rather than NSF awards, which is why the human verdict below is
Phase 1, not Phase 2.

## The shape: the mnemonic report, for contracts

`sam/queries/xras_mnemonic_report.py` is the exemplar. It pivots over the
push-readiness snapshot (never a second scan), names the data fix, ranks
it by the pushes it unblocks, and surfaces through
`sam-admin xras --mnemonic-report` plus the org-card linker. Contract
blockers are the same shape: *contracts to create, ranked by the pushes
each would unblock*, with a one-click path to the existing New Contract
form.

## Prerequisite: a structured channel, not string parsing

The mnemonic report matches 422 strings by **equality** against the
`sam/xras/errors.py` formatters — error strings are XRAS's wire contract,
"not an interface for us to scrape" (the rule `sam/queries/xras_accounts.py`
states for the roster). `cannot_find_contract(grant, core)` embeds variable
text, so equality cannot work and sub-parsing is exactly what the rule
forbids. So:

- `plan_contracts` (`src/sam/xras/handlers/_fields.py`) already knows
  which numbers failed to resolve. The handler records them —
  `self.unresolved_grants = [{number, core, agency, title}]`, read from
  the same `grants[]` entry — and `_resolved_summary`
  (`src/sam/xras/handlers/base.py`) exposes them, exactly as it exposes
  the resolved allocation type, panel, mnemonic series, and research area.
- From there the value rides existing plumbing untouched:
  `preflight_action` → `Verdict.resolved` → `verdict_to_dict` → the
  readiness snapshot's per-action `preflight` cell → CLI report and card.
  Zero string parsing anywhere.

## Phases

### Phase 0 — measure (done 2026-08-24)

Measured on the forward pipeline (the table above) rather than the audit
log: extensions ignore `grants[]` and the one New posted on cutover day had
none, so the log alone would have said "0". **Trigger for Phase 1 was "the
class recurs beyond the two known cases" — it did, twice, before any post
exercised the path.** Re-count from `xras_action_log` (`error_messages`,
`warnings`) once posts accumulate; the sweep preflight is the ongoing
source.

### Phase 1 — surface

- The structured channel above.
- `contract_unblock_report(session, snapshot)` in `src/sam/queries/`,
  mirroring `mnemonic_unblock_report`: grouped by grant number — agency,
  grant title, PI, requests blocked, oldest waiting; confirmed still
  unresolved against the current `contract` table (a row created since the
  sweep drops out, the way a mapped org drops out of the mnemonic report).
- `sam-admin xras --contract-report` (JSON envelope
  `xras_contract_report`), same conventions as `--mnemonic-report`.
- A "Contract blockers" panel beside the mnemonic linker on the
  Remediations card; each row links to the existing
  `htmx_contract_create_form` (`src/webapp/dashboards/admin/contracts_routes.py`)
  with the grant number carried as the award-search query.
- **The "not an award number?" verdict, here rather than Phase 2.** Four of
  the six measured numbers (`PRJ013992 BWI`, `ISS 25-643`, `001368-00183`,
  and the External-Projects pattern generally) are project references, so
  NSF prefill cannot help them; what clears them is a `contract` row created
  as a non-award reference, recorded once. The row must exist either way —
  the handler links, never creates — so the affordance is "create as a
  non-award contract with this reference", and the report stops re-listing
  it the next sweep.

### Phase 2 — assist

- Pre-resolve NSF numbers through the award search
  (`_award_search_context` / `htmx_contract_award_search`, the #404
  NSF/USAspending integration) **in the sweep** — cached, off the POST
  path — so the panel row already shows title, PI and dates, and the
  create button lands on a fully prefilled form.
- Create → automatic re-check of the blocked actions (the existing
  `--recheck` / modal Replay path) so the row clears itself.
- Non-NSF numbers get no prefill; the non-award verdict is already Phase 1.

### Rejected — fully automatic contract creation

At dispatch time it puts an outbound call (NSF API) in the XRAS-facing
POST path: a 10 s budget, outbound closed in tests, and an availability
coupling between two third parties. In the sweep it writes reference data
nobody reviewed. The opportunity-map precedent
(`propose_opportunity_mapping`: propose, a human confirms, the sweep never
overwrites a human's row) is the house answer — **auto-propose, one-click
confirm**. Recorded here so it is not re-litigated.

## Traps for whoever builds it

- `contract` text columns are `utf8mb3_bin` — case-sensitive; every
  comparison in `resolve_contract` uses `ilike` for that reason.
- The ≥6-digit core regex (`extract_core_number`) misses 5-digit numbers
  by design; the report must show the raw number, not the core.
- The suffix match is legacy's tie-prone path (`1049089` / `PLR-1049089`
  exist in production). The create button must not mint a second row for
  a number that would then tie — the create form's FK and uniqueness
  checks are the guard, and the report should flag a number whose core
  already suffix-matches an existing row as "possible spelling variant",
  not "missing".
- A grant with digits but no contract stays a hard 422 by design (the
  ≤4-digit carve-out was rejected in #479); this feature changes what the
  operator *sees*, not what the handler *decides*.
