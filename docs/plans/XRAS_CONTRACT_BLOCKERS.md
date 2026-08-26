# XRAS Contract Blockers — surface, then assist

**Status: Phase 1 BUILT 2026-08-24 on PR #482 (`xras_incoming_triage`),
smoked against the local sweep in a browser and by `e2e/`.** Sketched during
cutover week (`XRAS_TRIAGE_WEEK.md`); builds on the grant handling shipped in
PR #479 (`XRAS_DATA_MODEL_UPLIFT.md`, Track A commit 1).

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
and a 200, and nothing today says so. Three of the six numbers carry no
six-digit run at all (the handler's award shape) and a fourth is not NSF's,
which is why the human verdict below is Phase 1, not Phase 2.

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
- **The "not an award number?" verdict, here rather than Phase 2.** Most of
  the measured numbers (`ISS 25-643`, `001368-00183`, `PRJ013992 BWI`, and
  the External-Projects pattern generally) are not NSF awards, so NSF
  prefill cannot help them; what clears them is a `contract` row created
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

## Phase 1 implementation map (refined 2026-08-24, building on PR #482)

One PR, four ordered commits. Every path below was read, not assumed.

### What reading the tree corrected

| Item | Finding |
|---|---|
| The channel | `preflight_action` builds a **failed** verdict with `resolved=None`: `BaseHandler.run()` raises `XrasActionRejected` from `raise_if_any()` before `_resolved_summary()` runs. A contract blocker is a failed action, so the summary rides the exception (`exc.resolved`). |
| Wire `grants[]` keys | Every corpus grant carries `fundingAgency`, `grantNumber`, `title`, `piName`, `beginDate`, `endDate`, `isPending` (and more). The wire already holds a create form's title and dates — the non-award verdict gets a *seeded* manual-mode form, no lookup. |
| `award_like` | The handler's own regex (`has_core_number`, a ≥6-digit run). `PRJ013992 BWI` **is** award-shaped by that rule (`013992`); `ISS 25-643` and `001368-00183` are not. Three of the six measured, not four, are references. |
| Contract sources | Prod: `NSF`, `NASA`, `DOI`, `DOD`, `DOT`, `USAID`, `LANL`, `Other`, plus institution-named rows. Only NSF has a lookup provider. `suggested_source='NSF'` when the agency is NSF *and* the number is award-shaped; else the operator picks. |
| Create link | Page hop to Admin → Contracts (the mnemonic strip's shape); `?create=…` auto-opens the seeded modal. CSP is `script-src 'self'`, so a data attribute plus a `DOMContentLoaded` hook in `form-helpers.js`, reusing the `project-details-modal` pair. |
| Readiness partial | `_xras_readiness_why.html` printed "Would mint in series …" on `pf.resolved.series`; now gated on `pf.would_succeed`, since failed verdicts carry `resolved`. |
| The sweep's grants carry no agency **name** | The GET shape (`reports/requests`) has `fundingAgencyId`, the POST shape `fundingAgency`; the synthesizer passes `grants[]` through verbatim and the GET client has no agency vocabulary. So on the sweep path `suggested_source` is `None` and every link opens in **manual** mode with title and dates seeded — the operator flips to Lookup when it is NSF. Resolving the id (admin `types/all`) in the sweep is a Phase 2 item. |
| Only assembled actions reach the channel | A Submitted request has no approved dates, so the preflight is `incomplete` and never runs `plan_contracts`. NCAR4293/NCAR4300 surface the hour they are approved — the hourly sweep — not before. |

### Commit 1 — the structured channel (`sam/xras`)

- `extractors.py`: `has_core_number()`; `contract_candidates(session, core)`
  lifted out of `resolve_contract` so the report runs the identical query;
  `resolve_contract(..., unresolved=None)` appends
  `{number, core, reason: missing|ambiguous, candidates}` per reported failure.
  Strings and order untouched.
- `handlers/_fields.py` `plan_contracts` → `(contracts, warnings, unresolved)`,
  each entry enriched with `agency`, `title`, `pi_name`, `begin_date`,
  `end_date`, `is_pending`. The wire names are a literal tuple in the loop —
  the vocabulary gate resolves them from there.
- `handlers/new.py`, `handlers/update.py`: `self.unresolved_grants`;
  `base.py` `_resolved_summary` exposes it; `run()` sets `exc.resolved`;
  `errors.py` gives the exception the attribute; `preflight.py` forwards it
  on the failed branch. `verdict_to_dict` already carries `resolved`.
- `webapp/api/xras/actions.py` reads only `exc.messages` — wire bytes unchanged.

### Commit 2 — the report and the CLI

`sam/queries/xras_contract_report.py` — `contract_unblock_report(session,
snapshot)`, the `mnemonic_unblock_report` shape: walk failed rows' failed
actions, read `resolved.unresolved_grants`, **re-check every number against
the current table** (`Contract.get_by_number`, then `contract_candidates`):
exact or single suffix hit → dropped (created since the sweep); none →
`targets`; tie → `variants` ("possible spelling variant", never a create
link). Target: `number` (raw), `core`, `award_like`, `agency`,
`suggested_source`, `title`, `pi_name`, `begin_date`, `end_date`,
`unblock_count`, `sample`, `pis`, `oldest_activity`. Envelope
`xras_contract_report`. Not exported from `sam/queries/__init__.py`.

`sam-admin xras --contract-report`, mirroring `--mnemonic-report` exactly
(option, `XrasCommand._contract_report`, `builders.build_contract_report`,
`display.display_contract_report`).

### Commit 3 — the card strip and the seeded create link

- `remediation.py` computes `contract_summary` beside `mnemonic_summary`; the
  card renders the same `alert` strip after the mnemonic one, each number
  linking to `admin_dashboard.contracts?create=<number>&mode=…&title=…&
  start_date=…&end_date=…[&contract_source_id]` — `lookup` for
  NSF-suggested numbers, `manual` (seeded) for the rest. The NSF source id is
  resolved by name from `active_contract_sources()`, never hardcoded.
- `admin/blueprint.py` `contracts()` passes `auto_create_url` when `?create=`
  is present and the user holds `CREATE_CONTRACTS`; `contracts.html` emits
  `data-auto-open-create`; `form-helpers.js` opens the modal on load.
- `htmx_contract_create_form` reads `mode`, `title`, `start_date`, `end_date`;
  `seeded` (prefill) and `lookup` (auto-fetch) become two flags.
  `CreateContractForm` unchanged. No new route.

### Commit 4 — docs

Status line here; `docs/xras/incoming/XRAS_TRIAGE_PLAYBOOK.md` contract row
beside the mnemonic one; `XRAS_TRIAGE_WEEK.md` CLI list.

### Local smoke, before any dispatch

The Redis snapshot is empty after a container rebuild. Re-sweep, then walk it
in a browser against webdev, then the e2e suite:

```bash
docker compose exec webdev sam-admin tasks --run xras_sweep --force
docker compose exec webdev sam-admin --format json xras --readiness | jq   # resolved.unresolved_grants present
make e2e SAM_E2E_BASE_URL=http://localhost:5050
```

`e2e/test_xras_remediations_card.py` gains a guarded `TestContractStrip`
(skips when nothing published; asserts no username, request number or count).

### Definition of done

Met locally 2026-08-24 on a fresh sweep: `--contract-report` listed three
targets (NCAR4231, NCAR4280, NCAR4212 — the approved ones; the two Submitted
requests join on approval), the strip rendered with each number linking to
Admin → Contracts, and the link opened the New Contract modal in manual mode
with number, title and both dates seeded, zero console errors. The
`e2e/` case passed unskipped. Prod: the same after the branch dispatch and
the next hourly sweep.

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
- `_xras_readiness_why.html:46` prints "Would mint <series>" whenever
  `pf.resolved.series` exists. Once failed verdicts carry `resolved`, gate
  that line on `pf.would_succeed` or a failed New says it would mint a
  projcode.
- A grant with digits but no contract stays a hard 422 by design (the
  ≤4-digit carve-out was rejected in #479); this feature changes what the
  operator *sees*, not what the handler *decides*.
