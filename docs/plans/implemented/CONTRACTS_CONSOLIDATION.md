# Contracts consolidation — post-hoc cleanup of #401–#404

**Status:** implemented. One PR vs `staging`, branch `contracts_refactor`.

## Why

PRs #401–#404 landed on 2026-07-30, each planned independently, and together
turned `contract` from an invisible table into a first-class entity:

| PR | Size | What it added |
|---|---|---|
| #401 | +2810/-57 | `sam.integration.awards` provider seam; Monitor + NSF Program columns; two-mode create form |
| #402 | +1141/-9 | Contract detail card, `/admin/contracts` page, cross-table linking |
| #403 | +2262/-11 | `sam-admin contracts --validate` data-hygiene pass |
| #404 | +4795/-313 | Composite NSF/USAspending free-text search; round 2 rehomed the contracts table |

~10,850 insertions across 98 files. Nobody knew at #401 that #404 would exist,
so this is the pass that asks whether the seams chosen early still fit.

**They did.** The provider registry absorbed a whole second capability without
being reshaped; there is one `search_awards()` behind three consumers; the two
webapp award routes already shared `_award_search_context`; every new template
is CSP-clean; `read_active_only` / `fk_search_field` / `register_typeahead` are
used per convention; and there are no TODO/FIXME markers in the new code.

What *had* accumulated was four defects and a set of "same concept, four
spellings" duplications — the predictable cost of building one concept four
times in four increments.

## What was fixed

**Defects**

1. `AWARD_SEARCH_CACHE_TTL` / `_SIZE` were referenced by `awards/cache.py` but
   never defined in `config.py`. The search bucket was therefore **live during
   tests** (`TestingConfig` zeroed only the lookup pair) — the leak
   `AWARD_SEARCH.md` §13.1 records, which had been patched at the fixture layer
   instead. The knobs were also undeployable: `_config_int` reads
   `current_app.config` inside the webapp, which never saw the env var.
2. `cached_lookup` did not normalise its key while `cached_search` casefolds, so
   `ags-1852977` and `AGS-1852977` occupied two entries in an 8-day bucket.
3. `HtmxFormHandler.render_errors` passed `form=request.form` as an explicit
   keyword *alongside* `**self.context()`, so any handler supplying its own
   `form` would `TypeError`. `_ContractCreateHandler` carried a local override
   to survive it. Context now wins; the override is gone; a regression test
   pins that a failed create still renders the FK-picker badges.
4. `sam/schemas/contract.py`'s docstring described the `ProjectSchema`
   repoint as a deferred follow-up when the same PR had done it — the only
   place the `GET /api/v1/projects/<projcode>` response-shape break was
   documented, and it said the break hadn't happened.

**Consolidation**

5. **One contract-number normaliser.** Four rules existed; two were the same
   job. `normalize_contract_number()` now lives on the model that owns the
   column, and `get_by_number` gained a whitespace-insensitive fallback that
   runs only after the indexed exact match misses — so `OCE-1419584` finds the
   row stored as `OCE- 1419584`. The two *provider* manglers stay separate and
   now document why.
6. **`Contract.existing_by_number()`** replaces line-for-line copies of the
   "already in SAM" query in `orgs_routes.py` and `cli/awards/commands.py`.
7. **`dashboards/fragments/contract_bits.html`** — the user link, program link,
   and status badge, which had drifted ("not started" raw markup in one new
   template vs "Not started" via the macro in its sibling, and a PI rendered as
   plain text in the one place a username wasn't clickable).
8. **`cli/core/display_utils.py`** — `truncate` / `date_cell` / `text`, which
   existed twice with divergent behaviour, plus one shared
   `UNAVAILABLE_FIELD_LABELS` map (`'PI'` vs `'the PI'`).
9. **`contracts_routes.py`** extracted from `orgs_routes.py` (1,138 → 448 + 754).
   Pure motion, gated by `test_route_map_parity.py` passing unregenerated.

## Deliberate — do NOT "fix" these

- **The registry's two tiering rules** (`registry.py` `providers_for` vs
  `search_providers`). Restated on purpose, documented in place, and they agree
  for every current provider. A third provider is what would diverge them.
  *If you add one*, check both.
- **`_to_record` vs `_to_search_record`** setting `contract_number` to opposite
  values in `usaspending.py`. The policy is "don't overwrite what the operator
  owns"; a comment explains it and a test pins it.
- **Two exit-code conventions** across `sam-admin contracts` (2 = findings
  exist) and `sam-search contracts` (2 = something broke). Each matches the
  command family it is invoked from.
- **The hand-written award-search routes** rejecting `register_typeahead` — the
  helper has no error channel, so an `AwardSourceUnavailable` would 500.
- **`registry.providers()`** was flagged as dead but is kept: it is a public
  accessor with a legitimate test caller, and deleting it would only push that
  test onto `_PROVIDERS`.
- **`project_card.html`'s dated contract badge** ("starts …"/"expired …") is
  #399's user-dashboard treatment, not a stale copy of the admin badge.
- **F2 — external contacts out of `users`.** 414 users exist purely as contract
  contacts. A schema/coexistence conversation with legacy Java SAM, not a
  refactor. `resolve_person()` remains the single seam.
  See `CONTRACT_IMPORTING_PLAN.md` § F2.

## Known, deferred

Real but deliberately out of scope for this PR:

- **`htmx_contracts_table` is `@cache.cached` (300 s) with no invalidation**,
  while create/edit/expire fire `reloadContractsCard` → a re-fetch of that exact
  route. Inherited from the old org-card pattern, but #404 put the write form
  and the cached table on one page. The repo convention after a mutation is
  `cache.delete_memoized(...)` (`api/v1/queue.py`, `fstree_access.py`).
- **`htmx_contract_program_create`** is a POST that loads no schema, hand-rolling
  the `Length(min=1)` check `CreateNsfProgramForm` already performs (§9
  violation). It also fires no triggers, so a program created there never
  refreshes the Organizations card's NSF Programs tab.
- **`--check-sources` loads the contract set twice** (`queries/contract_audit.py`
  + `cli/contracts/commands.py` both call `get_contracts_with_pi`).
  `ContractSearchCommand` queries one row twice to turn a number into an id.
- **`webapp/api/v1/projects.py:_warm_contract_graph`** is a fourth copy of the
  contract eager-load chain, in the route layer, load-bearing on a `# noqa: F841`
  local. Belongs in `sam/queries/admin.py` with its three siblings.
- **The two award-result templates** share ~40 lines of badge/error/period
  markup behind one `_award_search_context`; only the affordance differs.
- **`show-user-details` (6 sites) vs `show-detail-modal` (10 sites)** — the
  latter is a strict generalisation; `actions.js` already says "could fold in
  later".
- **`src/cli/README.md` and `CLAUDE.md` claim this repo shares
  `ExporterRegistry`** with hpc-usage-queries; `grep` finds zero Python hits.
  A pre-existing doc bug in both repos' shared contract.

## Verification

- Full suite, run **with and without `CACHE_REDIS_URL`** — the axis CI catches.
- `test_route_map_parity.py` **without snapshot regen** — the gate for the split.
- `make perf` — guards the extraction and the `_warm_contract_graph` baselines.
- CLI smoke incl. `sam-search contracts 'OCE-1419584'` finding a spaced row.
- Browser smoke on `/admin/contracts`.
