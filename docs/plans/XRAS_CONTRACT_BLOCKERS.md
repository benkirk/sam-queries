# XRAS Contract Blockers — surface, then assist

**Status: Phase 0 MEASURED 2026-08-24 — trigger met; Phase 1 mapped below,
ready to build on `xras_incoming_triage` (2026-08-25).** Sketched during
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

## Phase 1 implementation map (verified against the tree 2026-08-24)

One PR, four ordered commits. Every path below was read, not assumed.

### Correction to the channel design

`preflight_action` (`src/sam/xras/preflight.py`) builds a **failed** verdict
with `resolved=None`: the handler's `dispatch()` raises `XrasActionRejected`
from `raise_if_any()` *before* `_resolved_summary()` runs, and only the
`rechecked` branch passes `result.resolved`. A contract blocker is by
definition a failed action, so "expose it through `_resolved_summary`" does
nothing on its own. The summary has to ride the exception.

### Commit 1 — the structured channel

| Where | Change |
|---|---|
| `src/sam/xras/extractors.py` `resolve_contract(session, grant_number, errs, *, unresolved=None)` | Optional list; on each `errs.report(...)` append `{'number', 'core', 'reason': 'missing' \| 'ambiguous', 'candidates': [...]}`. Strings and ordering untouched (`test_xras_error_coverage.py` stays green). Lift the suffix query into `contract_candidates(session, core)` so the report reuses it (two-consumers rule). |
| `src/sam/xras/handlers/_fields.py` `plan_contracts` | Returns `(contracts, warnings, unresolved)`; each unresolved entry also carries the wire `grants[]` keys `_grant_without_number` already reads (title/agency — confirm names against `~/xras_payloads_raw/`). |
| `handlers/new.py:105`, `handlers/update.py:154` | `self.contracts, contract_warnings, self.unresolved_grants = plan_contracts(...)`. |
| `handlers/base.py` `_resolved_summary` | `out['unresolved_grants'] = list(...)` when non-empty, via `getattr` like every other key. |
| `handlers/base.py` `dispatch` | `XrasActionRejected` gains `resolved: Optional[dict] = None` (`errors.py:66`); `dispatch` catches it around `raise_if_any()`, sets `exc.resolved = self._resolved_summary()`, re-raises. |
| `preflight.py` failed branch | `_verdict('failed', ..., resolved=getattr(exc, 'resolved', None))`. `verdict_to_dict` already forwards it; the snapshot cell needs nothing. |

Tests: `test_xras_extractors.py` (missing vs ambiguous entries),
`test_xras_new_handler.py::test_an_unresolvable_grant_reports` and the
update twin (`exc.value.resolved['unresolved_grants']`),
`test_xras_preflight.py` (a failed verdict carries `resolved`).

### Commit 2 — the report and the CLI

`src/sam/queries/xras_contract_report.py` — `contract_unblock_report(session,
snapshot)`, the `mnemonic_unblock_report` shape line for line: walk
`rows[].actions[].preflight` where `status == 'failed'`, read
`resolved.unresolved_grants`, **re-check each number against the current
`contract` table** (`Contract.get_by_number` then `contract_candidates`):

| re-check result | bucket |
|---|---|
| exact or single suffix hit | dropped — created since the sweep |
| no hit | `targets` (create) |
| tie | `variants` — "possible spelling variant", never a create button |

Target row: `number` (raw, never the core), `core`, `award_like`
(`extract_core_number(n) != n.strip()` — the ≥6-digit regex hit), wire
title/agency, `unblock_count`, `sample[:10]`, `pis`. Ranked by
`(-unblock_count, number)`. Envelope `kind='xras_contract_report'` with
`generated_at`, `actions_seen`, `targets`, `variants`. NOT exported from
`sam/queries/__init__.py` (same reason as the notices modules).

CLI, mirroring `--mnemonic-report` exactly: option in `cli/cmds/admin.py`
(~line 689, help group `[board]`, epilog examples ~730/781),
`XrasCommand.execute(contract_report=False)` + `_contract_report()` in
`cli/xras/commands.py:257`, `builders.build_contract_report`,
`display.display_contract_report` (rich Table: number · award-like ·
unblocks · sample · note). Tests: new `test_xras_contract_report.py` cloned
from `test_xras_mnemonic_report.py` (`_entry`/`_snapshot` helpers, using a
`resolved` cell instead of messages; drop-out via `make_contract`; tie via two
factory rows sharing a core), plus an envelope-kind case in
`test_admin_xras_cli.py`.

### Commit 3 — the card strip and the create link

- `webapp/dashboards/allocations/xras/remediation.py:123` — compute
  `contract_summary` beside `mnemonic_summary`, pass it to `_CARD`.
- `xras_remediations_card.html` after the mnemonic strip (line ~147): the same
  `alert` shape — "N failing push(es) cite M contract(s) SAM does not hold",
  first six numbers inline, `+more — sam-admin xras --contract-report`, a
  separate line for `variants`. Each number links to Admin → Contracts, the
  way the mnemonic strip links to Organizations: **no cross-page modal**
  (`HTMX_FRAGMENT_SHELL_DEPS`).
- `admin/contracts_routes.py` — the page route accepts `?create=<number>&mode=`
  and renders an `hx-get` to `htmx_contract_create_form` with
  `hx-trigger="load"`; `htmx_contract_create_form` gains `mode` (`lookup`
  default when seeded, `manual` on request). `award_like` targets link with
  `mode=lookup` (NSF prefill), the rest with `mode=manual` — that *is* the
  non-award verdict: same form, no lookup, the number already in the box.
  Additive; `CreateContractForm` unchanged.
- Route-map parity snapshot if a rule changes (`ROUTE_MAP_REGEN=1`).
- Tests: `TestContractUnblockStrip` in `test_xras_remediations.py` (publish a
  failing verdict whose `resolved` carries `unresolved_grants`; the number and
  its link render; view-only 403), and a contracts-page `?create=` render case.

### Commit 4 — docs

This file's status line; `docs/xras/incoming/XRAS_TRIAGE_PLAYBOOK.md` gains
the contract row next to the mnemonic one; `XRAS_TRIAGE_WEEK.md:154` CLI
list; CLAUDE.md needs nothing (no new pattern).

### Definition of done

`sam-admin xras --contract-report` on the prod snapshot lists NCAR4293
(`001368-00183`, manual) and NCAR4300 (`ISS 25-643`, manual) with
`unblock_count=1` each, NCAR4212 with two blockers still visible on the
board, and the strip renders on the Remediations card after the next sweep.

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
