# `sam-admin contracts --validate` — contract data-hygiene pass

**Status:** not started. Flagged as **F1** in
`docs/plans/CONTRACT_IMPORTING_PLAN.md`, deferred out of PR #401
and PR #402 on purpose.

**Restart cold from this file.** Everything below was measured against the dev
`sam` database (2,225 contracts) rather than assumed; re-run the queries in
§3 before trusting the counts, since the live figures move as contracts are
entered.

---

## 1. Why

Two PRs made contracts a real entity in the webapp:

- **#401** exposed `contract_monitor_user_id` and `nsf_program_id` (invisible
  before, despite 98%/99% fill rates) and added award-source prefill.
- **#402** added the detail card, the `/admin/contracts` search page, and
  cross-table linking.

Both are *interactive* surfaces: they help the operator entering the next
contract. Neither answers "what is already wrong across all 2,225 rows?" —
and the answer turns out to be *something specific and ongoing*, not a
historical artifact (§3).

This is the read-only reporting pass. **No writes.** It tells you what to fix;
fixing stays a human decision through the web UI.

## 2. What it builds on (this is the part that changed)

The original F1 note predates the award-source framework. That framework now
exists, is merged, and does most of the hard part. `sam.integration.awards`
exports:

| Symbol | Use here |
|---|---|
| `resolve_award(source_name, number) -> AwardRecord \| None` | the `--check-sources` divergence check |
| `AwardSourceUnavailable` | transport failure, distinct from "no such award" |
| `resolve_person(session, PersonRef) -> User \| None` | map an agency monitor to a SAM user |
| `nsf_award_id(number) -> str \| None` | **the award-id parse check is this function** |
| `award_id_candidates(number)` | USAspending id normalisation |
| `AwardRecord.unavailable_fields` | skip person checks for USAspending |

So the network check is `resolve_award()` plus a field-by-field comparison.
Do not re-implement any of it, and do not import from `sql/queries/nsf_awards.py`
(a standalone script tree; `nsf_award_id` is deliberately duplicated there).

The awards TTL cache (`sam.integration.awards.cache`, 8-day default) works in a
CLI process: `_config_int` falls back to `os.environ` outside a Flask app, and
Redis is used when `CACHE_REDIS_URL` is set. **Set it** when running
`--check-sources` and the CLI shares the webapp's warm cache instead of
building a per-process one that dies at exit.

## 3. The checks, with measured counts

Two scopes matter. F1 originally said "open contracts (`Contract.is_active`)";
that turns out to change the picture a lot.

| Check | All 2,225 | Open 368 |
|---|---|---|
| **Program is a funding-account string** (`^\d{8}[A-Z]{2}`) | 66 | **57** |
| Monitor == PI | 22 | 10 |
| URL missing | 225 | 12 |
| Missing monitor | 42 | **0** |
| Missing program | 20 | **0** |
| NSF number with unparseable award id | 4 | 0 |
| Orphan `nsf_program` rows (0 contracts) | 53 | — |

**Two findings worth carrying into the design:**

**(a) The headline check is the funding-account program, and it is an active
bug.** 57 of 368 open contracts — **15%** — point at an `nsf_program` row whose
name is NSF's `primaryProgram` (a funding account) rather than `fundProgramName`.
The rows in use by open contracts are the *recent fiscal years*:

```
01002526DB NSF RESEARCH & RELATED ACTIVIT   23    <- FY25-26
01002425DB NSF RESEARCH & RELATED ACTIVIT   14
01002324RB NSF RESEARCH & RELATED ACTIVIT    7
01002324DB NSF RESEARCH & RELATED ACTIVIT    6
01002223DB NSF RESEARCH & RELATED ACTIVIT    5
01002627DB NSF RESEARCH & RELATED ACTIVIT    2    <- FY26-27
```

Someone is still pasting the wrong field. #401's create form now maps
`fundProgramName` correctly, so new lookups are safe — but manual entry is not,
and this check is what catches it.

**(b) "Missing monitor" and "missing program" find nothing on open contracts.**
Both are 0. Scoped to open, those two checks are permanently vacuous and would
be noise. Either widen the default scope or report them as clean rather than
listing them — see the decision in §6.

Two smaller notes for the parse check: contract numbers are free text, and real
values include `PRJ014003 BAHAMAS S-TIMBA` and
`USDA Prime Award No. 2013-67003-20652`. `nsf_award_id()` correctly returns
`None` for the first and (a false positive) `20652` for the second — the check
should report "not parseable" only for `contract_source = 'NSF'`, where all 4
offenders live.

Reproduce all of the above:

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
UNION ALL SELECT 'missing monitor', COUNT(*) FROM open_c WHERE contract_monitor_user_id IS NULL
UNION ALL SELECT 'missing program', COUNT(*) FROM open_c WHERE nsf_program_id IS NULL
UNION ALL SELECT 'url missing', COUNT(*) FROM open_c WHERE url IS NULL OR TRIM(url) = '';
```

## 4. Command shape

New `src/cli/contracts/` package mirroring `src/cli/user/` — the CLI's
**three-layer split is mandatory** (`src/cli/README.md` § Design Principles):

```
src/cli/contracts/
├── __init__.py    # empty; importers use the full path
├── builders.py    # ORM -> plain dicts, 'kind' as the first key. No Rich.
├── commands.py    # ContractsAuditCommand(BaseCommand)
└── display.py     # display_*(dict) -> Rich. Dicts only, never ORM objects.
```

The same dict feeds `display_*()` and `output_json()`; that is what keeps
`--format json` complete without a second code path. Add a thin
`BaseContractCommand` to `src/cli/core/base.py` beside `BaseUserCommand` /
`BaseProjectCommand` if a shared ORM-lookup helper is wanted; those base
classes are lookup helpers only, nothing more.

### Copy `ProjectTreeAuditCommand`, not `UserAdminCommand`

**`sam-admin user --validate` is the wrong model.** Its `_validate_user` is a
self-declared placeholder (`src/cli/user/commands.py:170`) that prints Rich
directly with **no builder, no display function, and no JSON path** — an
integration test already documents the leak
(`tests/integration/test_cli_json_output.py:229-231`: "UserAdminCommand prints
rich validation chatter even in JSON mode").

The conforming precedent is **`ProjectTreeAuditCommand`**
(`src/cli/project/commands.py:417-452`) — the one audit-style command that does
both output formats and derives its exit code from findings:

```python
if self.ctx.output_format == 'json':
    output_json({'kind': 'tree_audit', 'resource': resource_name,
                 'violations': violations, 'invalid_dates': [...]})
else:
    self.console.print(f"[dim]Auditing project allocation trees{scope}...[/dim]\n")
    display_tree_audit(self.ctx, violations, bad_dates)

return EXIT_ERROR if (violations or bad_dates) else EXIT_SUCCESS
```

Its Rich half, `display_tree_audit()` (`src/cli/project/display.py:437`), is the
layout to mirror: a yellow `⚠️ N …` header, one
`Table(box=box.SIMPLE, show_header=True)` per finding class, per-item detail
gated on `ctx.verbose`, and a green `✅` all-clear in the else branch.

`ContractsAuditCommand` **extends `BaseCommand` directly**. The
`UserAdminCommand(UserSearchCommand)` inheritance exists only because
`sam-admin user <name> --validate` prints the user first; there is no
`sam-search contracts` to extend. Precedent for extending `BaseCommand`
directly: the accounting commands, and the DB-wide `--audit-trees` dispatch
that short-circuits before the projcode requirement
(`src/cli/cmds/admin.py:141-144`) — the same shape a scope-wide contracts audit
needs.

Wire into `src/cli/cmds/admin.py` beside the existing `--validate` flags
(`pyproject.toml:54` already registers `sam-admin`, so no packaging change):

```bash
sam-admin contracts --validate                  # rich report
sam-admin contracts --validate --check-sources  # + network divergence
sam-admin --format json contracts --validate | jq
```

Note `--format` is a **group-level** flag: `sam-admin --format json contracts
--validate`, never `sam-admin contracts --format json`.

**JSON envelope** (`src/cli/README.md` § Output Formats): top-level
`kind` — use `"contract_audit"` — ISO-8601 dates, `Decimal` → float,
sets → sorted lists, `indent=2`, `sort_keys=False`. Payload always complete
regardless of `-v`. Emit it through `output_json()` (`src/cli/core/output.py`),
which owns the `_SAMEncoder` type coercions. Note `rich.progress.track` is
auto-disabled in JSON mode, which matters for §5.

> **Do not go looking for `ExporterRegistry` / the `Exporter` ABC.** `CLAUDE.md`
> and `src/cli/README.md` describe them, but they exist only in the peer
> **hpc-usage-queries** repo — this repo has no such machinery, just a
> two-branch `if self.ctx.output_format == 'json':` per command plus
> `output_json()`. File exporters (`dat`/`csv`/`md`) would be net-new work or a
> port; they are not needed for this command.

## 5. `--check-sources` — the network check

Opt-in because it is slow and external. It is also the check that pays for the
whole command: the original research measured SAM's Monitor as **stale versus
NSF in roughly 1 of 3 sampled contracts** (`OCE-2242033`: SAM says Baris Uz,
NSF says Sean Kennan). Nothing else surfaces that.

Compare per contract: `title`, `start_date`, `end_date`, `contract_number`,
`nsf_program` name, and — NSF only — PI and Monitor via `resolve_person()`.
Skip person comparison when `'pi' in record.unavailable_fields` (USAspending
structurally has no people; not a divergence).

**Cost, measured on open contracts:** 354 NSF (all with parseable ids) + 14
non-NSF. NSF is one GET each. USAspending is up to 4 search POSTs (two
award-type groups, then a keyword fallback) plus one detail GET, so ~424
requests worst case. Budget:

- A **throttle** — `sql/queries/nsf_awards.py` uses `sleep_between=0.3`; at
  that rate NSF alone is ~2 minutes. Make it a flag.
- A **progress bar** via `rich.progress.track` (auto-disabled in JSON mode).
- `AwardSourceUnavailable` must **not** abort the run — count it as
  "unchecked" per contract and report the total at the end. A dead API is not
  a data-hygiene finding.
- Recommend `CACHE_REDIS_URL` so a second run is nearly free.

## 6. Decisions to take before writing code

1. ~~**Exit code when findings exist.**~~ **Settled — use `EXIT_ERROR` (2).**
   Both the placeholder `user --validate` *and* the real, conforming
   `ProjectTreeAuditCommand` return 2 when findings exist, and `EXIT_NOT_FOUND`
   (1) is reserved for "identifier doesn't exist"
   (`src/cli/user/commands.py:32-37`). Two independent precedents agree; match
   them and do not invent a `--strict` flag. (These codes are mirrored by
   `jobhist` in hpc-usage-queries — changing them is a two-repo change.)
2. **Default scope.** Open-only makes two checks permanently vacuous (§3b).
   **Recommendation: default open, `--all` to widen**, and print a one-line
   "0 of 368 open contracts" for a clean check rather than omitting it — an
   absent section reads as "not run".
3. **Is a `sam-search contracts` worth adding first?** It would restore the
   house `AdminCommand(SearchCommand)` inheritance and give the CLI parity
   with the webapp's new search page. Out of scope for F1, but cheap once
   `builders.py` exists.
4. **Orphan `nsf_program` rows (53).** A finding about the lookup table, not
   about any contract. Include as a separate section, or leave out?

## 7. Verification

Split the tests the way `--audit-trees` already is — query logic direct, CLI
wiring through `CliRunner`:

- **`tests/unit/test_contract_audit.py`** — the check functions against
  factory-built rows (`tests/factories/projects.py` has `make_contract`,
  `make_contract_source`), session fixture only, no CliRunner. Model:
  `tests/unit/test_tree_audit.py`.
- **`tests/unit/test_cli_contracts.py`** — end-to-end through the Click group.
  Model: `tests/unit/test_admin_cache_cli.py`, the only unit test driving
  `cli.cmds.admin` with `CliRunner`. **Its two-patch session fixture is
  mandatory** — without it the CLI opens its own connection and escapes the
  test's SAVEPOINT:

  ```python
  @pytest.fixture
  def mock_db_session(session):
      with patch('sam.session.create_sam_engine') as mock_engine, \
           patch('cli.cmds.admin.Session') as mock_session_cls:   # import site!
          mock_engine.return_value = (MagicMock(), None)
          mock_session_cls.return_value = session
          yield session
  ```

  Assert `result.exit_code == N, result.output` (the second arg is idiomatic
  here), and invoke JSON as `runner.invoke(cli, ['--format', 'json',
  'contracts', '--validate'])`.
- **Stub `resolve_award`** for the `--check-sources` tests exactly as
  `tests/unit/test_award_providers.py` does — no network in tests.
- Snapshot-independence: use factories for exact-value assertions, and skip
  rather than fail when the snapshot has no row of a given shape (the pattern
  in `tests/unit/test_admin_contract_card.py`).
- Manual: `sam-admin contracts --validate` against dev should report ~57
  funding-account-program contracts and ~10 monitor==PI. Then
  `--check-sources` on a narrowed set first (add a `--limit` while developing)
  before running all 368.
- `pytest` full suite; no route-map or template changes here, so no snapshot
  regen.

## 8. Out of scope

- **Any write path.** No auto-fix, no "apply NSF's monitor". The webapp's edit
  form is where corrections happen.
- **F2** (moving external contacts out of `users` — 414 users exist purely as
  contract contacts). Separate, and a schema conversation:
  `docs/plans/CONTRACT_IMPORTING_PLAN.md` § F2.
