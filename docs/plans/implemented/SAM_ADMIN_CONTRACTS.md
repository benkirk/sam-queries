# `sam-admin contracts --validate` — contract data-hygiene pass

**Status: implemented.** Was **F1** in
`docs/plans/implemented/CONTRACT_IMPORTING_PLAN.md:271`, deferred out of PR #401
and PR #402 on purpose.

Read-only reporting over the `contract` table. **No writes** — it tells you what
to fix; fixing stays a human decision through the web UI.

```bash
sam-admin contracts --validate                    # 368 open contracts
sam-admin contracts --validate --all              # all 2,225
sam-admin contracts --validate --check-sources    # + network divergence
sam-admin --format json contracts --validate | jq
```

Exit 0 when clean, 2 when findings exist (matching `ProjectTreeAuditCommand`).
`--format` is a **group-level** flag: `sam-admin --format json contracts
--validate`, never `sam-admin contracts --format json`.

---

## 1. Why

Two PRs made contracts a real entity in the webapp:

- **#401** exposed `contract_monitor_user_id` and `nsf_program_id` (invisible
  before, despite 98%/99% fill rates) and added award-source prefill.
- **#402** added the detail card, the `/admin/contracts` search page, and
  cross-table linking.

Both are *interactive* surfaces: they help the operator entering the next
contract. Neither answers "what is already wrong across all 2,225 rows?" — and
the answer turns out to be *something specific and ongoing*, not a historical
artifact (§3).

## 2. What it builds on

`sam.integration.awards` (merged with #401) does the hard part of
`--check-sources`:

| Symbol | Use here |
|---|---|
| `resolve_award(source_name, number) -> AwardRecord \| None` | the divergence check |
| `AwardSourceUnavailable` | transport failure, distinct from "no such award" |
| `resolve_person(session, PersonRef) -> User \| None` | map an agency monitor to a SAM user |
| `nsf_award_id(number) -> str \| None` | **the award-id parse check is this function** |
| `AwardRecord.unavailable_fields` | skip person checks for USAspending |

Do not re-implement any of it, and do not import from `sql/queries/nsf_awards.py`
(a standalone script tree; `nsf_award_id` is deliberately duplicated there).

The awards TTL cache works in a CLI process: `_config_int` falls back to
`os.environ` outside a Flask app, and Redis is used when `CACHE_REDIS_URL` is
set. **Set it** and the CLI shares the webapp's warm cache instead of building a
per-process one that dies at exit — the in-process fallback holds
`AWARD_LOOKUP_CACHE_SIZE` (256) entries, fewer than the 368 open contracts, so a
full run partially evicts itself.

## 3. The checks, with measured counts

Re-verified against the dev `sam` database on 2026-07-30. Re-run §3.1 before
trusting these; live figures move as contracts are entered.

| Check | Severity | Open 368 | All 2,225 |
|---|---|---|---|
| `funding_account_program` — program name matches `^\d{8}[A-Z]{2}` | high | **57** | 66 |
| `monitor_is_pi` | high | 10 | 22 |
| `missing_monitor` — *NSF-scoped* | medium | 0 | 42 |
| `missing_program` — *NSF-scoped*, incl. placeholder names | medium | 0 | 5 |
| `unparseable_award_id` — *NSF-scoped* | medium | 0 | 4 |
| `url_missing` | low | 12 | 225 |
| `nsf_program` rows that are funding accounts | — | 6 rows → all 57 above | |

**(a) The headline check is the funding-account program, and it is an active
bug.** 57 of 368 open contracts — **15%** — point at an `nsf_program` row whose
name is NSF's `primaryProgram` (a funding account) rather than `fundProgramName`.
The six rows in use are the *recent fiscal years*:

```
241  01002526DB NSF RESEARCH & RELATED ACTIVIT   23 open   <- FY25-26
229  01002425DB NSF RESEARCH & RELATED ACTIVIT   14 open
223  01002324RB NSF RESEARCH & RELATED ACTIVIT    7 open
239  01002324DB NSF RESEARCH & RELATED ACTIVIT    6 open
240  01002223DB NSF RESEARCH & RELATED ACTIVIT    5 open
242  01002627DB NSF RESEARCH & RELATED ACTIVIT    2 open   <- FY26-27
```

Someone is still pasting the wrong field. #401's create form now maps
`fundProgramName` correctly, so provider-assisted entry is safe — but manual
entry is not, and this check is what catches it. The lookup-table section is
reported *because* it is more actionable: renaming six rows fixes 57 contracts.

**(b) Three checks are NSF-scoped, and that scoping is load-bearing.** 18 of the
20 contracts with no program are non-NSF (DOE 8, NASA 3, AFOSR 2, …), where
`Contract.create`'s own docstring says a program is not expected — unscoped, the
check would be 90% false positives. `missing_monitor` is all-NSF anyway (NSF is
the only surveyed source carrying a program officer), so scoping it is free and
states the intent. For `unparseable_award_id`, scoping is what makes
`nsf_award_id()`'s false positive on `USDA Prime Award No. 2013-67003-20652`
(returns `20652`) harmless; all 4 real offenders are NSF-source.

**(c) `nsf_program_id=107` is literally named `NONE`** — a placeholder standing
in for NULL, used by 10 contracts. It is reported as a missing program rather
than as its own category. Its 5 open users are all non-NSF, which is why
`missing_program` is 0 on the open scope.

**(d) Two checks are vacuous on the open scope** (`missing_monitor`,
`missing_program`, plus `unparseable_award_id`). They still print a green
`✅ 0 of 368 …` line — an absent section reads as "not run".

**(e) Orphan `nsf_program` rows are deliberately not reported.** 53 of 239 rows
reference no contract (2 already inactive); no contract is wrong because of
them, and listing them would bury the six rows that matter.

### 3.1 Reproduce

```sql
-- scope: swap the WHERE for `1=1` to see all contracts
WITH open_c AS (
  SELECT c.*, s.contract_source FROM contract c
  JOIN contract_source s USING(contract_source_id)
  WHERE c.start_date <= NOW() AND (c.end_date IS NULL OR c.end_date >= NOW()))
SELECT 'funding-acct program' k, COUNT(*) v FROM open_c o
  JOIN nsf_program p USING(nsf_program_id)
  WHERE p.nsf_program_name REGEXP '^[0-9]{8}[A-Z]{2}'
UNION ALL SELECT 'monitor == PI', COUNT(*) FROM open_c
  WHERE contract_monitor_user_id = principal_investigator_user_id
UNION ALL SELECT 'missing monitor', COUNT(*) FROM open_c
  WHERE contract_source = 'NSF' AND contract_monitor_user_id IS NULL
UNION ALL SELECT 'missing program', COUNT(*) FROM open_c
  WHERE contract_source = 'NSF' AND nsf_program_id IS NULL
UNION ALL SELECT 'url missing', COUNT(*) FROM open_c WHERE url IS NULL OR TRIM(url) = '';
```

Note the shipped checks run in **Python** over one eager-loaded result set, not
as SQL predicates — the funding-account rule is a regex and `REGEXP` is
MySQL-specific (see `docs/plans/POSTGRES_MIGRATION.md`); the whole table is
2,225 rows, so one loaded pass beats six round trips.

## 4. What was built

```
src/sam/schemas/contract.py          ContractSummarySchema
src/sam/queries/contract_audit.py    audit_contracts() / audit_nsf_programs()
src/sam/integration/awards/audit.py  compare_contract()  (network)
src/sam/queries/admin.py             get_contracts_with_pi(with_source=)
src/cli/contracts/                   __init__ / builders / commands / display
src/cli/cmds/admin.py                @cli.command() contracts
```

`ContractSummarySchema` fills a real gap — `sam/schemas/` had no contract
schemas. It dumps fine outside a Flask app context: `BaseSchema.Meta.sqla_session`
is consulted by `load()`, never `dump()`. It is imported lazily inside
`builders.py` because `sam.schemas` pulls in `webapp.extensions`. The List/Full
tiers are not built; add them when a contracts API endpoint needs them.

### `ProjectSchema.get_contracts` now uses it

Done in the same change, on the call that the API is pre-deployment with no
external consumers. It previously emitted hand-padded f-strings
(`f"{source} {number:<20} {title}"`); it now returns a list of
`ContractSummarySchema` objects. `obj.contracts` holds `ProjectContract`
association rows, so it hops through `.contract`.

**This introduced an N+1 and the fix has a trap worth knowing.** The summary
schema flattens four relationships per contract, so on the worst project
(7 contracts) the field went from 11 queries to 69 during `jsonify()`
traversal. `_warm_contract_graph()` in `webapp/api/v1/projects.py` preloads
them in one `selectin` pass — but **the caller must hold the returned list**.
The SQLAlchemy identity map is weakly referenced, so discarding it lets the
warmed objects be collected and reloaded unloaded: measured 78 queries, i.e.
no improvement at all. Held, the whole route is 33.

No perf baseline covered this route, so `make perf` could not have caught any
of it. `test_project_detail_api_route` + the `project_detail_api_route`
baseline (45) now guard it; reverting the helper's return value re-measures 93
and fails the test.

`ContractsAuditCommand` extends `BaseCommand` directly (like the accounting
commands) and mirrors `ProjectTreeAuditCommand`'s JSON/Rich fork.
**`sam-admin user --validate` is the anti-model**: its `_validate_user` is a
self-declared placeholder that prints Rich with no builder, no display function
and no JSON path, *after* the parent already emitted the envelope — which is why
`tests/integration/test_cli_json_output.py:229-240` has to `raw_decode` from the
first brace. `test_admin_contracts_envelope_is_pure_json` is the deliberate
contrast.

**JSON envelope**: top-level `kind: "contract_audit"`, then `scope`,
`contracts_audited`, `checked_sources`, `total_findings`, `checks`,
`program_findings`, `source_check`. Every check appears with a `count` even at
zero. Payload complete regardless of `-v`.

> `ExporterRegistry` / the `Exporter` ABC described in `CLAUDE.md` and
> `src/cli/README.md` exist only in the peer **hpc-usage-queries** repo. This
> repo has a two-branch `if self.ctx.output_format == 'json':` per command plus
> `output_json()`. File exporters (`dat`/`csv`/`md`) would be net-new work.

## 5. `--check-sources` — the network check

Opt-in because it is slow and external, and it is the only check that finds
*stale* values rather than missing ones: the original research measured SAM's
Monitor as stale versus NSF in roughly 1 of 3 sampled contracts (`OCE-2242033`:
SAM says Baris Uz, NSF says Sean Kennan).

Compares `title`, `start_date`, `end_date`, `contract_number`, program name,
and — where the provider supplies them — PI and Monitor.

**Four rules keep it from being noise. Each one costs real findings if dropped:**

1. **`unavailable_fields` is skipped.** USAspending has no program-officer
   concept (FFATA/DATA Act does not collect one), so a blank Monitor there is
   structural, not divergence.
2. **`resolve_person()` returning `None` is a hint, not a divergence.** It means
   the agency's person is not a SAM user (314 of 387 monitors exist purely as
   contract contacts). The raw `PersonRef.label` is carried so an operator can
   search or create. Only a *resolved* user differing from the stored FK is a
   divergence.
3. **Contract numbers are normalised before comparison.** `NsfAwardProvider`
   rebuilds the number as `{divAbbr}-{award_id}`, so a raw compare flags every
   hand-entered `OCE- 1419584` / `AGS - 2410913`.
4. **`url` is never compared.** NSF emits the modern `show-award?AWD_ID=` form
   while ~1,895 legacy bulk-loaded rows carry the old scheme-less `showAward?…`.
   The provider URL is offered as a hint only where SAM has none.

**Plus a fifth found during implementation — the `suspect_match` guard.**
`UsaSpendingProvider` escalates to a *keyword* search when the exact-id lookup
misses, and short internal numbers find something plausible every time: SAM's
`014421` (a DOD 4DWX contract) resolves to a 2009 award titled **"MEALS"**, and
`105935` to an unrelated 2011 NSF CI award. `AwardRecord` carries no confidence
signal, so the consumer has to notice. The rule is blunt on purpose — title
*and* both dates all disagree — because a genuine stale field moves one or two,
never all three. Such a record is reported as `status='suspect_match'` with a
`source_summary` for eyeballing and **no divergences**, under a "verify the
contract number, do not copy these values" heading. Without it the report tells
an operator to overwrite a correct title with a stranger's.

**Cost, measured on open contracts:** 354 NSF (all with parseable ids) + 14
non-NSF. NSF is one GET each; USAspending is up to 4 search POSTs plus a detail
GET, so ~424 requests worst case — roughly 2 minutes at the default
`--sleep 0.3`. `rich.progress.track` is used with `disable=json_mode`.
`AwardSourceUnavailable` becomes one "unchecked" contract, never an aborted run.
`--limit N` narrows while developing.

## 6. Verification

- **`tests/unit/test_contract_audit.py`** (38) — check functions against
  factory-built rows. `audit_contracts()` has no scope argument to isolate
  behind, so every assertion filters findings to the contract the test created
  rather than counting globally; that also survives snapshot refreshes.
- **`tests/unit/test_award_audit.py`** (22) — `compare_contract`, one class per
  noise rule. `resolve_award` stubbed at `sam.integration.awards.resolve_award`;
  `audit.py` reaches through the module object rather than binding the name at
  import precisely so that target works. No network.
- **`tests/unit/test_cli_contracts.py`** (17) — Click wiring, flag guards, JSON
  envelope. The two-patch `mock_db_session` fixture is mandatory (patch
  `Session` at its import site, `cli.cmds.admin.Session`) or the CLI opens its
  own connection and escapes the test's SAVEPOINT.
- **`tests/integration/test_cli_json_output.py`** — one added case asserting the
  *entire* stream parses.
- `tests/factories/projects.py` gained `make_nsf_program` and `monitor=` /
  `nsf_program=` / `url=` kwargs on `make_contract`; every check turns on
  exactly those columns and none were settable before.

Full suite green (3,745 passed / 30 skipped / 1 xfailed). No route or template
changes, so no snapshot regeneration.

## 7. Out of scope

- **Any write path.** No auto-fix, no "apply NSF's monitor".
- **`sam-search contracts`** — would restore the `AdminCommand(SearchCommand)`
  inheritance and give the CLI parity with #402's search page. Cheap now that
  the builders exist.
- **F2** (moving external contacts out of `users` — 414 users exist purely as
  contract contacts). A schema conversation:
  `docs/plans/implemented/CONTRACT_IMPORTING_PLAN.md` § F2.
