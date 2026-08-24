# XRAS Contract Blockers — surface, then assist

**Status: PROPOSED, unscheduled.** Sketched 2026-08-24 during cutover
week (`XRAS_TRIAGE_WEEK.md`); builds on the grant handling shipped in
PR #479 (`XRAS_DATA_MODEL_UPLIFT.md`, Track A commit 1). Phase 0 is a
measurement, not code; nothing past it is built until the measurement says
so.

## The gap

The Pending-Users card makes an **account** blocker obvious: one row per
username with role, source, identity state, and days waiting. A
**contract** blocker has no surface. It lives only as the 422 string
`Cannot find contract for grant number "…" ("…")` inside a failing
action's preflight messages, so an operator learns that one `contract` row
would unblock a push only by reading each failing request's tooltip.

Measured 2026-08-24: 2 of the 15 expected cutover failures are this class.

| Request | Grant number | Agency | Notes |
|---|---|---|---|
| NCAR4212 | `PRJ013992 BWI` | Other | An external project reference, not an award. The request's **only** blocker (PI has a real account) — the quickest unblock on the board, invisible as such. |
| NCAR4231 | `2423211` | NSF | A real NSF award ("PFI (MCA): Smart Disaster Response…"). Also blocked on its PI's account; 41 days waiting. |

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

### Phase 0 — measure (triage week, no code)

The `warnings` and `error_messages` audit columns make this countable from
`xras_action_log` alone: how many real posts fail on `Cannot find
contract`, which agencies they cite, how many are NSF numbers the award
search resolves. **Trigger for Phase 1:** the class recurs beyond the two
known cases, or those two prove hard to clear by hand. If neither, this
doc stays a sketch — don't build for unseen failures.

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

### Phase 2 — assist

- Pre-resolve NSF numbers through the award search
  (`_award_search_context` / `htmx_contract_award_search`, the #404
  NSF/USAspending integration) **in the sweep** — cached, off the POST
  path — so the panel row already shows title, PI and dates, and the
  create button lands on a fully prefilled form.
- Create → automatic re-check of the blocked actions (the existing
  `--recheck` / modal Replay path) so the row clears itself.
- Non-NSF agencies ("Other", like `PRJ013992 BWI`) get no prefill and a
  "not an award number?" affordance — the human verdict that it is a
  project reference, not a contract, recorded once instead of re-read
  every sweep.

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
