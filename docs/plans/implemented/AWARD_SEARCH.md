# Award search — composite NSF/USAspending search across the CLI and the New Contract form

**Status: implemented.** Branch `award_search`, six commits off `dce9a43`.
See § *Deviations* at the foot of this file for what changed against the plan
as written — the plan's §3 measurements all held, but eight implementation
details did not.

**Required reading first:** `docs/plans/implemented/SAM_ADMIN_CONTRACTS.md`
(what #403 built, and the four noise rules in `compare_contract` that this plan
reuses). Background on the award providers:
`docs/plans/implemented/CONTRACT_IMPORTING_PLAN.md`.

**Ships as one PR against `staging`**, as an ordered commit series (§8).

---

## 1. Why

The **New Contract** form has a "Look up award" mode that requires the operator
to already know the contract number. That means they have already found the
award somewhere else — almost certainly by searching NSF's website by hand —
and are retyping it into SAM. The lookup automates only the *second* half of a
two-step job.

Both upstream APIs support free-text search and we never exposed it. Building
that surface once serves three consumers:

1. `sam-search awards --search "…"` — a CLI for the composite search.
2. A **"Find an award"** step at the top of the New Contract form's lookup
   mode: search → pick → the existing lookup prefills everything.
3. Duplicate detection — annotate search hits SAM already has.

It also closes the `sam-search contracts` gap that #403 left open (§5).

```
┌─ Find an award ──────────────────────────────────┐
│ [boundary layer turbulence        ] [ Search ]   │
│  NSF   2618361  Intermittency in Wave-breaking…  │
│        Kennan · 2025-09-01 → 2028-08-31   [Use]  │
│  USAsp FA9550…  BOUNDARY LAYER TURBULENCE SENS…  │
│        (no PI/Monitor)                    [Use]  │
│  NSF   1852977  The Management and Operation…    │
│        ✓ already in SAM — contract 1841          │
└──────────────────────────────────────────────────┘
```

---

## 2. What exists today

**The award layer is lookup-only.** `AwardProvider` (`awards/base.py`) exposes
`supports()` and `fetch(contract_number)`; the registry entry point is
`resolve_award(source_name, contract_number)`. There is **no** free-text entry
point of any kind.

- `NsfAwardProvider` (`awards/nsf.py`) calls exactly one endpoint,
  `AWARD_URL = …/awards/{award_id}.json`. It is the only provider carrying a
  program officer (`poName`/`poEmail`) and the only one with
  `unavailable_fields == frozenset()`.
- `UsaSpendingProvider` (`awards/usaspending.py`) **already performs keyword
  searches internally** — `_resolve()`'s second loop calls
  `_search({'keywords': [...]})` — but `_search` is private, keywords only on
  the punctuation-stripped *award number*, and it ends
  `return results[0] if results else None`, **discarding four hits it already
  paid for** (it requests `'limit': 5`). Its module docstring documents three
  empirically-verified traps; read it before touching anything.

**The DB layer has no contract search.** Grepping all of `src/sam/queries/`
finds only getters for contracts (`get_contracts_with_pi`,
`get_contract_detail`, `get_nsf_program_contracts`,
`get_nsf_programs_with_contracts`, plus #403's `audit_contracts`). The only
contract text search in the tree is in the webapp, written **twice**:
`_search_contracts` (`admin/orgs_routes.py`, limit 20) and
`_search_contracts_for_project` (`admin/projects_routes.py`, limit 10), both
`contract_number.ilike(f'%{q}%') | title.ilike(f'%{q}%')`.

**The form already has the right seam.**
`templates/dashboards/admin/fragments/create_contract_form_htmx.html` has a
two-mode radio (`contract_mode`: manual / lookup) and a `#contractLookupRow`
div that `static/js/form-helpers.js:applyContractMode` shows only in lookup
mode. Inside it sits the "Fetch award" button:

```jinja
<div id="contractLookupRow" style="display:none;">
    <button type="button" class="btn btn-sm btn-outline-primary"
            hx-get="{{ url_for('admin_dashboard.htmx_contract_award_lookup') }}"
            hx-target="#createContractFields"
            hx-swap="innerHTML"
            hx-include="closest form"
            hx-indicator="#createContractLookupSpinner">
```

Critically, `contract_number` (`id='createContractNumber'`) and
`contract_source_id` (`id='createContractSource'`) live in the **parent form,
outside `#createContractFields`**. The template docstring says why: they are
the lookup *inputs*, and a prefill swap must not disturb them. That is exactly
what a search result needs to write into.

> Anchors in this document are **symbol names**, deliberately — line numbers
> drift. Everything named here was verified present on `staging` at `dce9a43`.

---

## 3. Measured facts, and how to re-verify

Measured 2026-07-30 against the live APIs. **Re-run these before trusting
them** — they are the load-bearing claims of the whole plan.

| | endpoint | latency | returns per hit |
|---|---|---|---|
| NSF | `awards.json?keyword=` | 0.58 s | **everything `_to_record` reads** — `id`, `title`, `startDate`, `expDate`, `fundProgramName`, `poName`/`poEmail`, `piFirstName`/`piLastName`, `divAbbr` |
| USAspending | `search/spending_by_award/` | 0.51 s | `Award ID`, `Description`, `Start Date`, `End Date`, `generated_internal_id` |

**One request per provider — there is no N+1.** That is what makes this cheap
rather than expensive, and it is not obvious from the code:

- NSF **ignores `printFields`** and returns the full record set regardless.
- USAspending's `Description` **is available on the search endpoint**. The
  existing `SEARCH_FIELDS` omits it, which makes it look detail-only. It is
  not. Add it and no per-row detail fetch is needed.

USAspending needs two calls (the two award-type groups — "trap 2" in its module
docstring; mixing the groups errors or returns nothing), so a full composite
search is ~3 requests, **~1.6 s**. That is fine behind an explicit button with
a spinner and is why the trigger is **not** a keystroke typeahead.

**The one real asymmetry:** USAspending's `program_name` comes from
`cfda_info`, which *is* detail-only. So **search returns summaries, lookup
returns full records.** This is why a search hit still chains into the existing
lookup rather than prefilling directly — the chain is what recovers the program
name (and, for NSF, is simply free).

```bash
# NSF free-text
python - <<'PY'
import json, urllib.request, urllib.parse
q = urllib.parse.urlencode({'keyword': 'boundary layer turbulence', 'rpp': '5'})
with urllib.request.urlopen(f"https://api.nsf.gov/services/v1/awards.json?{q}", timeout=15) as r:
    d = json.load(r)
a = d['response']['award']
print(len(a), sorted(a[0]))          # expect ~60 keys incl. poName, fundProgramName
PY

# USAspending free-text, Description inline
python - <<'PY'
import json, urllib.request
body = {"filters": {"keywords": ["boundary layer turbulence"],
                    "award_type_codes": ["02","03","04","05"]},
        "fields": ["Award ID","Description","Start Date","End Date","generated_internal_id"],
        "limit": 3, "page": 1}
rq = urllib.request.Request("https://api.usaspending.gov/api/v2/search/spending_by_award/",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
with urllib.request.urlopen(rq, timeout=20) as r:
    print(json.dumps(json.load(r)['results'][0], indent=2))
PY
```

DB-side sizing (dev `sam`, same date): 2,225 contracts / 368 open; `title LIKE
'%climate%'` → 107 all, 18 open; 1,261 distinct PIs, busiest has 13; 2,209 of
2,225 contracts have ≥1 linked project.

---

## 4. Part 1 — Award search core (`src/sam/integration/awards/`)

### `base.py` — one new method on the ABC

```python
def search(self, query: str, limit: int = 10) -> List[AwardRecord]:
    """Free-text search. Providers that cannot search return []."""
    return []
```

Concrete with a `[]` default, **not** `@abstractmethod`: `fetch` is the
provider contract, search is an optional capability, and a default keeps a
future provider from having to fake one.

### `nsf.py` — `NsfAwardProvider.search()`

One GET to `awards.json?keyword=<q>&rpp=<limit>`, mapping each hit through the
**existing** `_to_record(award, award_id)`. Search hits carry every field that
mapper reads, so there is no second mapper and therefore nothing to drift.
`supports()` is unchanged and irrelevant here — search is not number-scoped.

### `usaspending.py` — `search()` plus a two-line fix

Change `_search` to return the full `results` list; `_resolve` takes `[0]` at
its two call sites. That is the entire refactor — the type-group loop,
`SEARCH_FIELDS`, and all trap-handling stay as they are. Add `'Description'` to
`SEARCH_FIELDS`. Then `search()` runs the keyword filter once per type group,
concatenates, and maps via a new `_to_search_record(hit)`: `title =
Description[:TITLE_MAX_LENGTH]`, dates from the search row, `url` from
`AWARD_PAGE_URL.format(generated_internal_id=…)`, `program_name=None`, same
`unavailable_fields={'pi','monitor'}`.

> **A deliberate inconsistency — document it in both mappers or it will get
> "fixed".** `_to_record` sets `contract_number=None` on purpose: USAspending
> reports a punctuation-stripped id, and rewriting the operator's number to a
> form no other system uses would be wrong. `_to_search_record` **must** set it
> from `Award ID` — in search-driven creation there is no operator input yet,
> and without a number the form's "Use" button has nothing to seed. The policy
> is *"don't overwrite what the operator owns"*, not *"never emit a number"*.

### `registry.py` — `search_awards()`

```python
def search_awards(query, limit=10, sources=None) -> Tuple[List[AwardRecord], List[Dict]]
```

Fans out across `providers()`, returning `(records, errors)`. **One dead
provider must not kill the search**: an `AwardSourceUnavailable` from NSF
becomes an `errors` entry while USAspending's hits still return. This is the
same stance `--check-sources` takes, and the reason `resolve_award`'s
raise-vs-`None` split exists — "NSF has no award X" and "NSF is down" are
different answers and must never be conflated. Serial is fine at ~1.6 s; note
concurrency is available if a third provider ever lands.

### `cache.py` — a second bucket

The lookup key is `(provider_name, contract_number)`; searches need their own
namespace. Add a bucket rather than overloading it:

```python
'search': BucketSpec(name='awards_search',
                     ttl_key='AWARD_SEARCH_CACHE_TTL', ttl_default=86400,
                     size_key='AWARD_SEARCH_CACHE_SIZE', size_default=256)
```

Shorter TTL (1 day vs the lookup's 8) because a search is a *view over a
changing corpus* while an award record is near-immutable. `BucketedTTLCache` is
multi-bucket by construction — `purge()`, `info()` and `live_adapters()` all
iterate `self.buckets` — so the Admin Configuration card and `sam-admin cache
--refresh --category awards` pick the new bucket up with **no further wiring**
(the module is already in `_BUCKETED_CACHE_MODULES`).

### `audit.py` — one optional parameter

`compare_contract(session, contract, record=None)`. The CLI's `awards <number>`
has already fetched the record; without this it fetches twice. (The cache makes
the second call nearly free, but threading it is clearer than relying on that.)

---

## 5. Part 2 — DB contract search + CLI

### `src/sam/projects/contracts.py` — two classmethods

Matching `User.search_users` / `Project.search_by_pattern`:

```python
Contract.get_by_number(session, contract_number)      # exact; the column is unique-indexed
Contract.search_by_pattern(session, pattern=None, *, active_only=True,
                           source=None, pi=None, monitor=None,
                           program=None, limit=50)
```

All four filter families were confirmed wanted: number/title pattern,
active-only default, `--pi`/`--monitor` by username, `--source`/`--program` by
name.

> **Wildcard semantics — decide deliberately; do NOT copy `user --search`.**
> `sam-search user --search` advertises *"use % for wildcard, _ for single
> char"* and then does `pattern.replace('%','').replace('_','')` before handing
> the bare term to `User.search_users`, which wraps it `%term%`. **The
> documented semantics are not the actual semantics** — `ben%`, `ben` and
> `%ben%` all produce an identical query.
>
> Use the one self-consistent idiom in the tree, `_apply_filter` in
> `sam/queries/charges.py` — `col.like(val) if '%' in val else col == val` —
> generalised to treat the term as a LIKE pattern iff it contains `%` **or**
> `_` (the charges version checks only `%`), else substring-match. Document it
> in `--help`. Fixing `user --search` is **out of scope**: different command,
> different consumers.

**Deduplicate the webapp's third copy.** Point both `_search_contracts` and
`_search_contracts_for_project` at the classmethod, exactly as
`_search_projects_for_parent` already delegates to
`search_projects_by_code_or_title`. **Preserve their differing limits (20 / 10)
and `active_only` defaults as arguments** — `_search_contracts`' docstring
explains that its checkbox defaults off because an active-only default would
hide 83% of the data.

### CLI

```
src/cli/core/base.py     + BaseContractCommand (get_contract by number)
src/cli/contracts/       + ContractSearchCommand, ContractPatternSearchCommand
src/cli/awards/          NEW package: __init__ / builders / commands / display
src/cli/cmds/search.py   + two @cli.command()s
```

```bash
sam-search contracts AGS-1852977          # DB detail
sam-search contracts --search climate     # DB pattern + filters
sam-search awards AGS-1852977             # ask the providers, cross-ref SAM
sam-search awards --search turbulence     # composite free-text
```

`BaseContractCommand` is justified now that a single-entity lookup exists.
**`ContractsAuditCommand` stays on `BaseCommand`** — it is scope-wide and that
reasoning is unchanged by this PR; do not retrofit `AdminCommand(SearchCommand)`
onto it. `awards` gets its own package: different data source, and the
three-layer split (`builders` → `commands` → `display`) is per domain.

Use the `sum(inputs) != 1` mode-guard idiom from the `user`/`project`
subcommands (red error + `click.get_current_context().get_help()` + exit 1),
**not** `sam-admin contracts`' per-flag dependency guards.

Envelope `kind`s: `contract`, `contract_search_results`, `award`,
`award_search_results`. Per-contract rows reuse `ContractSummarySchema` via the
existing `_contract_dict()` in `cli/contracts/builders.py`. The award envelope
must carry `provenance` per record, render `unavailable_fields` as an explicit
positive note ("USAspending cannot supply PI/Monitor — enter manually"), and
for the lookup path an `in_sam` block.

**Exit codes — three outcomes, never conflated**, exactly as
`htmx_contract_award_lookup` models them: found → `EXIT_SUCCESS`; no such award
/ no matching contract → `EXIT_NOT_FOUND`; source unreachable → `EXIT_ERROR`.
A `--format json` not-found still emits its envelope and exits 1.

`sam-search awards <number>` cross-references SAM (`Contract.get_by_number` →
`compare_contract`), inheriting #403's `suspect_match` guard. That matters:
`sam-search awards 014421 --source DOD` resolves to a 2009 award titled
**"MEALS"**, and the output must say so rather than presenting it as this
contract's data.

`AwardHttpClient.DEFAULT_TIMEOUT` is 10 s, deliberately short because it runs
inside an htmx round-trip. A CLI has no worker to hold — pass a longer
`timeout=` to the constructor from the CLI rather than raising the default and
slowing the webapp's failure path.

---

## 6. Part 3 — "Find an award" in the New Contract form

The search step goes **inside `#contractLookupRow`, above the existing "Fetch
award" button**. Manual-entry mode is untouched.

### New route — hand-written, not `register_typeahead`

`register_typeahead` takes any callable `(q, active_only) -> list` and never
touches the session, so an award search *fits* the signature. Do not use it:
exceptions become 500s (no `AwardSourceUnavailable` handling) and its template
contract is only `{q, <ctx_key>}`, with no room for per-provider error notes.
Its own docstring blesses the exception — *"Endpoints whose branching is the
feature stay hand-written."*

`GET /admin/htmx/contract-award-search`, gated
`@require_permission(Permission.CREATE_ORG_METADATA)` (same as the lookup
route). Reads `q` — matching `fk_search_field`'s `name="q"` +
`hx-include="this"` convention, and already popped by
`htmx_contract_award_lookup` so it cannot leak into the form dict. Scopes to
the chosen source when one is selected. Renders a new
`contract_award_search_results_htmx.html`. Mirror
`htmx_contract_award_lookup`'s error handling: an unreachable source renders an
inline note, never a 500.

`hx-indicator` on the Search button, reusing the
`#createContractLookupSpinner` idiom — ~1.6 s needs visible feedback.

### Result rows: annotate, then chain

Model on `contract_search_results_htmx.html` (a `<button>` row carrying its own
htmx attributes), **not** `contract_search_results_fk_htmx.html` — this
navigates rather than filling a hidden FK.

Each row shows provenance, number, title, dates, and the program officer where
NSF supplies one. **Annotate rows whose number already exists in SAM** via one
pass over the result numbers using Part 2's classmethod — Part 2 paying for
itself. `_ContractCreateHandler.clean` already catches a duplicate number and
names the conflicting contract, so this is the same protection surfaced one
round-trip earlier, before the operator fills the form.

**"Use" writes the two parent-form inputs, then fires the existing Fetch
button.** This needs a few lines of JS: the operator must *see* the number that
was selected, and the eventual POST reads that field — a pure `hx-vals` chain
would re-render `#createContractFields` while leaving `contract_number` visibly
empty and posting nothing. Register a CSP-safe `data-action` in
`form-helpers.js`, mirroring the existing `search-suggested-person` action
(which sets an input's value then `htmx.trigger`s it):

```js
registerAction('use-award', function (btn) {
    setValue('createContractNumber', btn.dataset.awardNumber);
    if (btn.dataset.sourceId) setValue('createContractSource', btn.dataset.sourceId);
    htmx.trigger(document.getElementById('contractFetchAward'), 'click');
});
```

No inline handlers — `actions.js` delegates `data-action` at document level,
and CSP is enforced. Give the existing Fetch button an `id`
(`contractFetchAward`); it has none today.

**The existing lookup route stays completely untouched.** That is the payoff of
the two-step design, and it is also what recovers the program name that
USAspending search results structurally lack.

**Source inference: NSF only.** `provenance == 'NSF Awards API'` → resolve the
`ContractSource` row named `NSF` **at runtime by name**, never a hardcoded id.
USAspending spans many agencies and its `Awarding Agency` string ("Department
of Defense") does not match our source names ("DOD"), so leave Source for the
operator rather than guessing.

**Route-map parity is the gate.** Adding this route fails
`tests/unit/test_route_map_parity.py` until regenerated:
`ROUTE_MAP_REGEN=1 pytest tests/unit/test_route_map_parity.py`, then commit the
diff to `tests/unit/snapshots/dashboard_route_map.json`.

Watch `_ContractCreateHandler.perform`, which does `kwargs = {k: v for k, v in
data.items() if k != 'contract_mode'}` → `Contract.create(**kwargs)`. Any new
posted field must not reach it; `CreateContractForm` is `unknown=EXCLUDE` so a
stray `q` is dropped, but verify rather than assume.

---

## 7. Decisions already taken — do not relitigate

| Decision | Why |
|---|---|
| Two subcommands (`contracts`, `awards`), not one with modes | Different data sources; clean `kind` envelopes and independent exit-code semantics |
| Explicit Search button, not keystroke typeahead | ~1.6 s and 3 external requests per search; a typeahead would also pollute the shared cache with prefix queries and is impolite to two public APIs |
| Select → fill number → auto-chain the existing lookup | One click, existing route untouched, and it is the only way to get USAspending's detail-only program name |
| Search returns summaries; lookup returns full records | Forced by USAspending's `cfda_info` being detail-only. Documented, not a gap to paper over |
| `contract_number` set on search records, `None` on fetch records | Different situations: seeding an empty form vs overwriting operator input |
| Separate cache bucket with a 1-day TTL | Searches are views over a changing corpus; award records are near-immutable |
| Both wildcard chars honoured, documented honestly | The existing `user --search` contract is a lie; do not propagate it |
| One PR, ordered commit series | Matches #403; the webUI step is the payoff that shows why the core looks the way it does |

---

## 8. Commit series

Each commit should pass its own tests standalone — #403 verified this by
checking out each commit and running its test file; do the same.

1. **Award search core** — `base.search()`, both providers, `search_awards()`,
   the `_search` multi-result fix, the cache bucket, `compare_contract(record=)`
2. **`Contract.get_by_number` / `search_by_pattern`** + webapp delegation
3. **`sam-search contracts`** — `BaseContractCommand`, command classes, wiring
4. **`sam-search awards`** — the `cli/awards/` package + cross-reference
5. **"Find an award" form step** — route, template, `use-award` action,
   route-map snapshot regen
6. **Docs** — move this file to `docs/plans/implemented/`, update
   `src/cli/README.md`'s directory tree, note the new commands in `CLAUDE.md`'s
   CLI section

> Do **not** put `[skip ci]` on the last commit of the series. CI keys off the
> head commit message, so a trailing docs-only `[skip ci]` silently suppresses
> the entire PR's test matrix — this happened on #403 and had to be amended off.

---

## 9. Verification

**Tests — no network in any of them.**

- `tests/unit/test_contract_search.py` — `get_by_number` / `search_by_pattern`
  against factory rows. `make_contract` takes `monitor=` / `nsf_program=` /
  `url=` and `make_nsf_program` exists, both added by #403. Cover each filter,
  the active-only default, and **both wildcard branches** — that is where the
  deliberate divergence from `user --search` lives.
- `tests/unit/test_award_search.py` — provider `.search()` against a
  `MagicMock` client, reusing the `_provider(nsf=..., **returns)` helper in
  `tests/unit/test_award_providers.py`. Assert NSF search maps through the
  *same* `_to_record` as fetch; that `_to_search_record` **does** set
  `contract_number` while `_to_record` does not; and that `_search` returning
  many no longer collapses to one.
- `search_awards()` — one provider raising `AwardSourceUnavailable` still
  returns the other's hits plus an `errors` entry.
- `tests/unit/test_cli_contracts_search.py` — CliRunner through
  `cli.cmds.search`, with the two-patch fixture (`sam.session.create_sam_engine`
  + **`cli.cmds.search.Session`** — a *different import site* from the admin
  tests, which patch `cli.cmds.admin.Session`). Without it the CLI opens its own
  connection and escapes the test's SAVEPOINT. Mode guards, exit codes,
  whole-stream `json.loads`.
- Webapp, per house convention (HTTP layer covers auth / validation / render
  smoke): the new route 403s without `CREATE_ORG_METADATA`, renders results with
  `search_awards` stubbed, renders an inline note on `AwardSourceUnavailable`
  rather than 500ing, returns empty below `min_len`, and flags an
  already-in-SAM row.
- `tests/integration/test_cli_json_output.py` — add `contract_search_results` /
  `award_search_results` cases asserting the **entire** stream parses.

**Manual, against dev:**

```bash
sam-search contracts AGS-1852977 --list-projects
sam-search contracts --search climate             # 18 open / 107 with --all
sam-search contracts --pi poulsen                 # 13 contracts
sam-search awards 014421 --source DOD             # expect suspect_match, title "MEALS"
sam-search awards --search "boundary layer turbulence"
sam-search --format json awards --search turbulence | jq -e '.kind == "award_search_results"'
```

Then `docker compose up webdev --watch` → Admin ▸ Contracts ▸ **New Contract**
▸ "Look up award": search `turbulence`; confirm mixed NSF/USAspending results
with an already-in-SAM annotation; click **Use** on an NSF row and verify the
number and source populate visibly and the existing Fetch chains into a full
prefill — **including Monitor and program, which the search result alone cannot
supply.** That last point is the whole reason for the chain; if it does not
happen, the wiring is wrong.

**Suite:** full `pytest`; `make perf`; and the route-map regen in §6.

> If any of this introduces a new N+1 during template or JSON rendering, note
> that `make perf` only catches routes that have a baseline — `GET
> /api/v1/projects/<projcode>` had none until #403 added one. Add a baseline
> alongside any new expensive route rather than assuming coverage. And if the
> fix is a warm-the-graph pass, **the caller must hold the returned list**: the
> SQLAlchemy identity map is weakly referenced, and discarding it silently
> un-warms everything (#403 measured 78 queries vs 12 on exactly that mistake).

---

## 10. Out of scope

- **Fixing `sam-search user --search`'s wildcard bug** — real, but a different
  command with its own consumers.
- **`sam-admin contracts <number> --validate`** (single-contract validate,
  which `BaseContractCommand` would make easy) — additive, not asked for.
- **Any write path.** The form still creates the contract; nothing
  auto-creates from an award record.
- **A third award provider.** The registry seam supports one; nothing here
  needs it.
- **F2** — moving external contacts out of `users` (414 users exist purely as
  contract contacts). A schema conversation:
  `docs/plans/implemented/CONTRACT_IMPORTING_PLAN.md` § F2.

---

## 11. Deviations from the plan as written

Everything in §3 was re-verified against the live APIs and the dev DB on
2026-07-30 before implementation and **all of it held** — NSF 0.58 s,
USAspending 0.47 s, `Description` inline, 2,225/368/1,261/2,209 contracts,
busiest PI 13, `--pi poulsen` 13, `contract_number` uniquely indexed with zero
duplicates. Two measurements came out *better* than claimed:

- **NSF search and fetch return identical 62-key sets** — diffed both ways,
  empty in both directions. Reusing `_to_record` is therefore lossless, not
  merely sufficient.
- The composite search is **~1.1–1.5 s**, not ~1.6 s.

Eight implementation details in §§4–6 were wrong or underspecified:

1. **`setValue` does not exist** in `static/js/form-helpers.js`. The §6
   snippet was aspirational; `use-award` assigns `.value` directly.
2. **The class is `NSFProgram`**, not `NsfProgram` (§6).
3. **`AwardHttpClient` had no injection seam.** §5 says the CLI should "pass a
   longer `timeout=` to the constructor", but providers are module-level
   singletons built at import with default clients. Added
   `registry.build_providers(client=None)` and a `providers=` parameter on
   `search_awards`.
4. **`supports()` cannot scope a search** — it requires a contract number.
   §4's `sources=` needed its own rule, added as `registry.search_providers()`:
   naming a provider's source narrows to it, anything else falls back to the
   generics, mirroring `UsaSpendingProvider.supports()` returning False for NSF.
5. **`_apply_filter` (`sam/queries/charges.py`) is not the idiom §5
   describes.** Its else-branch is exact equality, not substring, and it uses
   `like` not `ilike`. The shipped rule is a deliberate divergence on both
   axes, documented as such in `search_by_pattern`'s docstring.
6. **`title` and `contract_number` are `utf8mb3_bin`, i.e. case-sensitive.**
   §9's manual-test expectation of "18 open / 107 with `--all`" for `climate`
   was measured with a plain `LIKE`. The real figures under `ilike` — which
   the webapp already used — are **78 open / 512 all**. `ilike` here is
   load-bearing, not cosmetic.
7. **`contract_source_id` is a `<select>`**, not a hidden input, so
   `use-award` guards on the option existing before assigning.
8. **`selectinload`/`joinedload` are not exported** by `sam/base.py`'s star
   import (`or_` and `and_` are), and `aliased` and `User` are not either —
   `search_by_pattern` imports them locally.

Also worth recording:

- **`_search_contracts_for_project` ignored its `active_only` argument.** The
  delegation preserves that behaviour explicitly (`active_only=False`) with a
  docstring saying why, rather than silently acquiring the classmethod's
  `True` default and hiding 83% of the picker's rows.
- **`tests/unit/test_flask_cache_adapter.py` enumerates adapter names**, so
  the new `awards_search` bucket had to be added there. Not mentioned in §9,
  and the same class of gate as the route-map snapshot.
- **`_contract_dict` was promoted to `contract_dict`** so `cli/awards/` can
  reuse it without a cross-package private import.
- **Source inference for the CLI.** §6 covers it for the form, but
  `sam-search awards <number>` needed its own: with no `--source`,
  `providers_for` never reaches NSF, so a bare lookup of an NSF number
  reported "not found". The source is now taken from SAM's contract when
  there is one, and NSF is retried when the number parses as an award id.

**Pre-existing issue, deliberately not fixed** (agreed with Ben before
starting): running a *small subset* of test files under the default `-n auto`
reproducibly hits `pymysql.err.OperationalError: (1213, 'Deadlock found')`
during factory INSERTs — 3/3 runs on clean `staging`, while the same files
pass serially. The full suite is unaffected (3,872 passed). Use `-n 0` for
targeted runs.

## 12. Verification results

- Full suite: **3,872 passed, 30 skipped, 1 xfailed** in 92 s (was 3,745 at
  #403). `make perf`: 21 passed, no new N+1 — no new expensive route was
  added, since the search route's cost is network, not queries.
- Route-map snapshot regenerated: exactly one route added.
- Live-checked against `webdev` with the real APIs: the search endpoint
  returned 20 rows in 1.5 s, and the already-in-SAM annotation correctly
  matched award `2535750` to its existing contract.
- `sam-search awards 014421 --source DOD` reproduces the documented
  `suspect_match` on the 2009 award titled **"MEALS"**.

---

## 13. Round 2 — `/admin/contracts` (same PR)

Added after the first round landed, once the award search had proved itself
inside the create modal.

### 13.1 The CI failure the first round shipped

The first round's `tests/unit/test_award_search.py` was **Redis-blind** and CI
caught it: 3 failed / 3,869 passed. Worth recording because the class of bug
recurs.

`ci-staging.yaml`'s `Test Suite` job runs pytest *inside the compose `webapp`
container*, where `compose.yaml` force-sets `CACHE_REDIS_URL`. So the awards
cache resolved to `RedisTTLAdapter`, and the fixture's `_adapters.clear()`
dropped only the in-process adapter reference — the Redis keyspace survived.
Every `TestSearchAwards` case searches the same term, so they shared the key
`('NSF Awards API', 'q', 10)` and read each other's records. A dev machine has
no `CACHE_REDIS_URL`, gets a `TTLCacheAdapter`, and clearing the memo really
does yield a fresh cache — hence green locally, red in CI.

The fix is the idiom `test_webapp_jobs_cache.py` and `test_webapp_disk_scans.py`
already used, whose docstring names the trap verbatim: `reset_for_tests()` to
pin buckets off, plus `monkeypatch.delenv('CACHE_REDIS_URL')` — the latter also
covering the quieter problem that **xdist workers share one Redis**, so no
per-test cleanup is race-free.

> **Rule going forward:** any test asserting on cache behaviour must be run
> `CACHE_REDIS_URL=… pytest …` at least once. It is the only way to catch this
> locally, and CI runs that way by default.

### 13.2 What round 2 built

- **"Find Candidate Contracts"** on `/admin/contracts`, alongside a renamed
  **"Search Existing Contracts"**. Both cards collapse, open by default, via
  the `.collapse-toggle` header idiom (pure CSS chevron, no JS).
- `_award_search_context()` factors the provider call and in-SAM annotation out
  of `htmx_contract_award_search`; the modal rows and the page cards are two
  templates over one implementation.
- **Create-from-award seeds the form server-side.** `htmx_contract_create_form`
  gained optional `contract_number` / `contract_source_id`; a seeded form opens
  in lookup mode with `hx-trigger="load, click"` on the Fetch button, so the
  existing lookup fires immediately. Chosen over JS because the form does not
  exist when the button is clicked, so JS would have to sequence itself against
  the htmx swap. A bare New Contract still never calls an agency (asserted).
- **The contracts table moved** off the Organizations card onto
  `/admin/contracts`. NSF Programs deliberately stayed — it is not contract
  data in the same sense, and moving it would have been a larger diff for no
  operator benefit.

### 13.3 The move was a large, unplanned perf win

`admin_orgs_card_route` went from a **measured 311 queries to 20**. Rendering
2,225 contracts was almost the entire cost of that card; the plan had expected
"strictly fewer queries" and a stale baseline, not a 15× drop. Both baselines
were reset, and `admin_contracts_table_route` (measured 25) got its own —
`make perf` only catches routes that have one.

### 13.4 Silent-failure hazards handled

Each of these fails *quietly* — no error, just nothing happening:

1. Contract and contract-source CRUD shared `_ORG_TRIGGERS`, and
   `_reloadAdminCard` opens with `if (!section) return;`. On `/admin/contracts`
   that event matches nothing, so mutations would have refreshed nothing.
   Added `reloadContractsCard` + listener; both specs point at it.
2. `admin-cards.js` gated the collapse-chevron wiring on
   `#organizationsTabsContent`; re-gated on the fragment's own `#contractsTable`.
3. A stray `q` from the in-form search box reaching `Contract.create(**kwargs)`
   — covered by a schema test rather than assumed from `unknown=EXCLUDE`.

### 13.5 Round 2 verification

- Full suite **3,888 passed** / 30 skipped / 1 xfailed, run **both with and
  without `CACHE_REDIS_URL`**. `make perf` 22 passed.
- Route-map snapshot: exactly two routes added.
- Live against `webdev` with the real APIs: 20 candidate cards in 1.3 s with
  one already-in-SAM hit rendering *View contract* instead of a create button;
  the seeded form pre-filled and auto-fetching; the moved table showing 368
  active contracts; the organizations card down to four tabs.
