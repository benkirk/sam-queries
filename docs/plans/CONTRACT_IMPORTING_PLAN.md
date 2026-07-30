# Contracts: expose Monitor/NSF Program, add pluggable award-source prefill

## Context

The Create Contract form is a generated `CrudSpec` exposing 7 fields. Two real `contract`
columns are invisible everywhere in the app — **`contract_monitor_user_id`** (the Program
Manager) and **`nsf_program_id`**. They are absent from the create template, from
`CreateContractForm`, and from `Contract.create()`'s signature entirely, and
`Contract.update()`'s docstring declares PI/monitor/source/number read-only. Yet the data
matters: **2,183 of 2,225 contracts (98%) have a monitor**, 2,205 (99%) have a program,
and the DB already carries indexes (`contract_contract_monitor_user_fk`,
`contract_nsf_program_fk`) for columns the UI never surfaces.

**97.2% of contracts are NSF** (2,162 of 2,225). Recent creation (2021–2026) runs ~130
NSF/yr against ~7 non-NSF/yr.

### NSF API fit — verified against live API and DB

`api.nsf.gov/services/v1/awards/{id}.json` needs no key and maps near-1:1 onto our
columns. Round-tripping `AGS-1852977` reproduced our stored row exactly, including URL
format and program name — and our stored title carries the same "Atmoshperic" typo as
NSF's, so this data was originally sourced from these awards.

Measured on a random 25 NSF contracts plus a 12-contract field diff:

- **25/25** awards found. `contract_number` is `DIV-7digit` for 2,109 of 2,162 NSF
  contracts; NSF keys on the bare 7-digit tail, and `divAbbr + '-' + id` reconstructs ours.
- `end_date` 12/12, `title` 11/12, `contract_number` 11/12, `start_date` 10/12.
- PI resolves to a SAM user **16/25 by email, 11/12 with surname fallback**.
- Program officer resolves **16/25 by email**. They already exist as real SAM users —
  387 distinct, 293 with an `nsf.gov` address.
- `fundProgramName` already in `nsf_program` (case-insensitive): **8/25**.

Two data-quality findings this work prevents recurring:

1. **SAM's Monitor is stale vs NSF in ~1/3 of sampled contracts** (`OCE-2242033`: SAM says
   Baris Uz, NSF says Sean Kennan). One row has Monitor == PI, a plain entry fallback.
2. **6 `nsf_program` rows are funding-account strings** — `01002324DB NSF RESEARCH &
   RELATED ACTIVIT` is NSF's `primaryProgram`, not `fundProgramName`. **66 contracts**
   point at them, concentrated in recent entries.

### Multi-source research — the verdict

Empirically surveyed grants.gov, USAspending, NIH RePORTER, SBIR.gov, OSTI, DOE PAMS,
NASA TechPort/NSPIRES, DTIC, NOAA, DOI, DOT/TRB, USAID, OpenAlex, Crossref, ORCID.

| Source | Coverage | Award data | PI | Program officer |
|---|---|---|---|---|
| **api.nsf.gov** | NSF only (97.2% of ours) | full | ✅ +email | ✅ **+email** |
| **USAspending** | **all federal** (the other 8 agencies) | number, dates, description, URL, CFDA | ❌ | ❌ |
| NIH RePORTER | NIH/AHRQ/FDA/VA | full | ✅ name | ✅ name |
| NASA TechPort | NASA tech projects | **no award number** → unjoinable | ✅ +email | ✅ +email |
| SBIR bulk CSV | SBIR/STTR only | native format | ✅ +email | ❌ |
| grants.gov | **opportunities, not awards** | ✗ | ❌ | ❌ |

- **grants.gov is the wrong data class** — it indexes solicitations you apply *to*
  (`PD-24-7790 "Atmosphere Cluster"`, open/close dates). It resolved none of our award
  numbers; `AGS-1852977` returned 12 hits that were pure keyword-tokenization noise.
  It cannot be "just used."
- **Federal RePORTER**, the one cross-agency system with PI data, has been **dead since
  March 2022** (host no longer resolves), with no successor.
- **Program officer is structurally unavailable outside NSF.** It is a pre-award
  administrative attribute in each agency's grants-management system; FFATA/DATA Act —
  what feeds USAspending — does not collect it. NASA TechPort has `Project_Manager` with
  `@nasa.gov` emails but no award number of any kind, so it cannot be joined. DOE PAMS and
  TRB RiP have it behind ASP.NET/HTML with no API — rejected as brittle scrapes.
- We have **zero NIH/HHS contracts**, so the one other API with a program officer is moot.

So the framework is a **2-provider world**, and Monitor stays manual outside NSF.

### Decisions taken

| Decision | Choice |
|---|---|
| Providers | Ship the seam with **both** NSF (full) and USAspending (partial federal fallback) |
| Provenance | Shown in the form only; nothing persisted (no schema change) |
| Unmatched PI / officer | Suggest, don't impose — hint + manual pick; **never auto-create a `User`** |
| Scope | Create form + expose Monitor & NSF Program on edit/list; CLI hygiene pass as a follow-on PR |
| Unknown NSF program | Preselect if matched, else an explicit "create and select" |
| NSF client location | New package under `src/sam/integration/`; `sql/queries/nsf_awards.py` untouched |

### Explicitly rejected

- `institution.nsf_org_code` — values are legacy 7-char NSF codes (`0010512`); the awards
  API returns `ueiNumber`/`parentUeiNumber` instead (NSF retired org codes for UEI). No
  field to map; only 330/1,378 populated. Leave alone.
- Funding amounts (`estimatedTotalAmt`) — `contract` has no amount column and the DB is
  the schema source of truth. Flag for a future schema conversation; add no columns here.
- NASA TechPort fuzzy title-join, DOE PAMS / TRB RiP scraping, OpenAlex/Crossref fallback
  (for DOE, measured as null or a name-less shell — latency and a second normalization
  headache for zero usable people).

---

## Unit 1 — Award-source framework, `src/sam/integration/awards/`

New package beside the existing `src/sam/integration/`. Transport modelled on
`collectors/lib/api_client.py:23-95` (persistent `requests.Session`, explicit timeout,
`max_retries=3` with `2 ** attempt` backoff, no retry on 4xx) — **not** the offline
script's bare `urllib` call.

```
base.py       AwardRecord, PersonRef, AwardProvider (ABC), AwardSourceUnavailable
nsf.py        NsfAwardProvider
usaspending.py UsaSpendingProvider
registry.py   resolve_award(source_name, contract_number) -> AwardRecord | None
people.py     resolve_person(session, name, email) -> User | None
```

`AwardRecord` is a frozen dataclass carrying `provenance` (provider name), the mappable
fields, and **`unavailable_fields: frozenset[str]`** — the explicit list of things this
provider structurally cannot supply, so the UI can say "Monitor must be entered manually"
rather than silently leaving a blank.

`AwardProvider` ABC: `name`, `supports(source_name, contract_number) -> bool`,
`fetch(contract_number) -> AwardRecord | None` (`None` = no such award; raise
`AwardSourceUnavailable` for transport failure — the caller must distinguish these).

`registry.resolve_award` tries source-specific providers first, then generic ones.

**`NsfAwardProvider`** — `supports` when source is NSF and the number yields an award id.
Award id = digits after the last hyphen (same rule as `sql/queries/nsf_awards.py:56`;
keep the implementations independent, do not import across `sql/`). Mapping:
`contract_number` ← `divAbbr + '-' + id`; `title`; `startDate`/`expDate` (`MM/DD/YYYY`);
`url` ← `…showAward?AWD_ID=<id>&HistoricalAwards=false` (matches our stored format
exactly); `program_name` ← `fundProgramName`; PI ← `piEmail` + `piFirstName/piLastName`;
monitor ← `poEmail` + `poName`. `unavailable_fields` is empty.

**`UsaSpendingProvider`** — federal fallback. Two-step: POST
`/api/v2/search/spending_by_award/` with a **candidate array** of `award_ids`, then GET
`/api/v2/awards/<generated_internal_id>/`. Three verified traps, each a silent-zero-hit
source if missed:

1. **IDs are punctuation-stripped, inconsistently.** `DE-SC0012671` → `DESC0012671`;
   `DE-FC02-97ER62402` → `DEFC0297ER62402`; `FA9550-14-C-0035` → `FA955014C0035`;
   `80NSSC19K0855` unchanged. Submit `{raw, alnum-only}` as a candidate set.
2. **`award_type_codes` must come from a single group** — mixing assistance (`02`–`05`)
   and contract (`A`–`D`) codes errors/returns nothing. Issue both queries.
3. **Suffixed variants** — `NA18NWS4620043` misses on exact match but `keywords` finds
   `NA18NWS4620043B`. Fall back to a keyword prefix search.

Verified agency coverage — **NASA needs no normalization at all**: `80NSSC19K0855`
(UCAR, 2019-04-16 → 2024-04-15), `80NSSC21K1522` (UCLA), `80NSSC23K1055` (UCAR) all
resolve unchanged, which covers 8 of our 9 recent NASA contracts. DOE/AFOSR/NOAA need the
candidate set above.

Also: coverage is **FY2008+** — the legacy NASA form `NNG04EA00C` (2004) returns no hit,
confirmed. Report those as "not found" rather than an error. And there is no title
field — `description` is ALL-CAPS FPDS text, sometimes a whole abstract, against our
`title varchar(255) NOT NULL`. Treat it as a *suggestion only*, truncated, never
auto-committed. `url` ← `https://www.usaspending.gov/award/<generated_internal_id>/`.
`program_name` ← CFDA number + title. `unavailable_fields = {'pi', 'monitor'}`.

**`resolve_person`** stays SAM-side, not in a provider: match email against
`email_address` case-insensitively across **all** rows — *not* `is_primary`, which is
unset for many of these users and cost me a false 0/12 during research — then fall back to
first+last name. Return the `User` or `None` plus the source's raw name/email so the UI
can render the hint on a miss.

Caching: `sam.caching.BucketedTTLCache` + a `BucketSpec` keyed `provider:number`, env key
`AWARD_LOOKUP_CACHE_TTL`, long default (award records are near-immutable — closer to the
8-day `FS_SCANS_CACHE_TTL` than the 30-minute jobs TTL). Add the bucket to the
`--category` choices in `src/cli/cmds/admin.py:519`, and zero the TTL in the testing
config as `src/webapp/config.py:326` does for `ALLOCATION_USAGE_CACHE_TTL`.

## Unit 2 — Widen model, schema, edit path

- `Contract.create()` (`src/sam/projects/contracts.py:94`) gains
  `contract_monitor_user_id=None`, `nsf_program_id=None` — it never sets them today, so
  passing them currently raises `TypeError`.
- `Contract.update()` (`:46`) gains the same two; correct its `:57-58` docstring — they
  become editable, PI/source/number stay read-only.
- `CreateContractForm` / `EditContractForm` (`src/sam/schemas/forms/orgs.py:104,117`) gain
  both as `f.Int(load_default=None)`.
- The contract `_org_spec` (`src/webapp/dashboards/admin/orgs_routes.py:468-492`)
  `edit_kwargs` lambda must pass the new keys — **the lambdas enumerate keys explicitly,
  so a new schema field is otherwise silently dropped.**
- `edit_contract_form_htmx.html` gains a Monitor `fk_search_field` + NSF Program
  `select_field`; update its "Read-only / Editable" header comment.
- FK existence via `validate_fk_existence` (`webapp/utils/fk_validation.py`) per §9.

## Unit 3 — Two-mode create form

**The house pattern is not a tab strip.** `create_project_form_htmx.html:54-69` uses a
Bootstrap `btn-check` radio group whose value (`projcode_mode`) is itself a submitted
field — that is what the server branches on. Mirror it with `contract_mode`
(`manual` | `lookup`); visually it reads as the two tabs you described.

Carried over verbatim from that form:

- `data-action-change="contract-mode"` registered in `static/js/form-helpers.js` — **not**
  an inline `onchange`; CSP forbids it and `tests/unit/test_template_csp_lint.py` enforces it.
- `autocomplete="off"` on the radios; re-derive `checked` from `form` so an error
  re-render lands on the submitted mode.
- Re-init under `htmx.onLoad` gated on a marker element unique to this fragment.
- **Hidden sections still submit their fields** — `display:none` does not exclude them, so
  the mode field is authoritative, never DOM presence.

`register_crud` **must stop generating create for contracts**: drop `'create'` from the
spec's `actions` tuple and hand-write `htmx_contract_create` beside it, exactly as
`htmx_contract_delete` (`orgs_routes.py:385`) already sits next to its own spec — required
by the hard rule at `crud.py:12-15`. Keep the endpoint name and URL rule identical so the
template's `url_for` and htmx attributes are untouched.

Implement as an `HtmxFormHandler` subclass (tier 3 per §9): mode branching + an external
call + exception mapping exceeds what `handle_htmx_form_post` expresses. Do the lookup in
`clean()` — outside `management_transaction`, so a slow or failed call never holds a DB
transaction open. Raise `FormError` for "no award found" and, distinctly, for
`AwardSourceUnavailable`. Both modes converge on one payload before a single
`Contract.create()`; strip `contract_mode` before the ORM call.

**Prefill mechanism** — the `htmx_project_parent_prefill` idiom
(`projects_routes.py:478-512`): factor the field block into
`create_contract_fields_htmx.html`, wrap it in a div with `hx-target="this"`, and
re-render server-side from a synthesized `form` dict so initial render, error re-render,
and prefill are one code path. `fk_search_field` pre-populates from `form.get(name)` +
`form.get(name ~ '_display')` (`form_fields.html:367-368`). **Return `204` when the
provider has nothing**, so operator input is never destroyed.

**Provenance + suggest-don't-impose:** label each prefilled group with the provider it
came from, and render `unavailable_fields` as an explicit "USAspending cannot supply
PI/Monitor — enter manually" note. For an unresolved person, follow
`project_lead_hint_htmx.html`: show the source's name/email as a hint with an
apply-or-search affordance — "No match → no suggestion, never a guess". Same for an
unknown program, with a create-and-select button. Reuse
`admin_dashboard.htmx_search_users?context=fk` for the Monitor picker with a distinct
`id_prefix` — **no new typeahead endpoint needed**.

Re-validate server-side at POST; the prefill is display-only and may be stale, the stance
`projects_routes.py:600-605` takes for the projcode preview.

## Unit 4 — Display

`organization_card.html` contract rows (`:294-320`) show number, title, PI, start, end.
Add **Monitor** and **NSF Program** columns, and eager-load them in
`get_contracts_with_pi` (`src/sam/queries/admin.py:167`), which loads only
`principal_investigator` today — otherwise this is an N+1 across ~2,200 rows.

Contracts are child rows under the **Contract Sources** tab, not their own tab; the NSF
Programs tab shows only name + count. Keep that structure — widening columns is the fix.

---

## Follow-ons — flagged, NOT in this branch

### F1. `sam-admin contracts --validate` (data-hygiene pass)

Separate PR after the above. Read-only reporting over open contracts
(`Contract.is_active`), no writes, exit codes 0/1/2/130. New `src/cli/contracts/` package
(`commands.py` + `display.py`) mirroring `src/cli/user/`, wired into
`src/cli/cmds/admin.py` beside the existing `--validate` flags (`:64`, `:85`); `rich`
output per house convention. Named `--validate` to match the established `user` /
`project` flags rather than `--verify`.

Checks, each evidenced above: missing monitor or program; monitor == PI;
`nsf_program_name` matching `^\d{8}[A-Z]{2}` (66 contracts); `contract_number` not
parseable to an award id; and — behind an opt-in `--check-sources` flag, since it hits the
network — divergence from the provider for title/dates/monitor/program. That last check is
what systematically surfaces the ~1/3 stale-Monitor rate measured above.

### F2. External contacts should not live in `users` (ORM/schema change)

Today `contract.principal_investigator_user_id` (NOT NULL) and
`contract_monitor_user_id` are both FKs into `users`, so every NSF program officer
becomes a row in UCAR's identity table. Measured:

- **1,608** users are a contract PI or Monitor; **881** are inactive.
- **414** are *purely external* — no `account_user` membership, never a project
  lead/admin. They exist solely as contract contacts.
- Of the **387** distinct Monitors, **314 (81%)** are purely external.

The better long-term shape is a lightweight `external_contact` table (name, email, org,
role) that is not entangled with UPID / `unix_uid` / POSIX groups / IDMS sync / RBAC —
none of which mean anything for an NSF program director.

**Why it is not in scope here:** SAM's database is the schema source of truth and is
shared with legacy SAM, whose Java app and the legacy-compat API blueprints
(`directory_access.py`, `project_access.py`, …) read these tables. Repointing the
contract FKs is a coexistence problem that requires retiring or migrating those consumers
first — a sequencing conversation, not a code change.

**This plan deliberately keeps that seam clean.** `resolve_person()` (Unit 1) is the only
place that maps a source's person to a SAM `User`, and providers carry the raw
name/email independently of any SAM row. Swapping the target entity later is a change to
one function's return type plus two FK columns, not a rewrite of the prefill path.

---

## Verification

**Tests** (author and run; no network in tests — stub `AwardProvider.fetch`):

- `tests/unit/test_award_providers.py` — NSF: id parsing across the real production
  formats (`AGS-1852977`, bare `2317820`, `GRFP-2009067341`, `OCE- 1419584` with a space,
  `AGS - 2410913`); mapping of a canned payload; `MM/DD/YYYY` parsing; 23:59:59 `end_date`
  normalization. USAspending: the candidate-set normalizer against all four verified
  shapes, both `award_type_codes` groups, and the `NA18NWS4620043` → `…B` keyword
  fallback. Both: `AwardSourceUnavailable` vs `None`, and `unavailable_fields` contents.
- `tests/unit/test_award_people.py` — email match across non-primary rows, surname
  fallback, and the unmatched path returning the raw name/email for the hint.
- `tests/unit/test_contract_create_modes.py` — per-mode schema validation; the bespoke
  route in both modes; `FormError` on unknown award and on source-unavailable;
  `contract_mode` stripped before `Contract.create()`; monitor + program persist.
- **Regen the route-map snapshot** — Unit 3 adds routes:
  `ROUTE_MAP_REGEN=1 pytest tests/unit/test_route_map_parity.py`, commit the diff.

```bash
source etc/config_env.sh
export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'
pytest tests/unit/test_award_providers.py tests/unit/test_award_people.py \
       tests/unit/test_contract_create_modes.py -v
pytest tests/integration/test_schema_validation.py   # Contract.create/update changed
pytest
```

**Manual** — `docker compose up webdev --watch`, http://localhost:5050, Quick Login
`benkirk`, `/admin/organizations` → Contracts:

1. Lookup mode, `AGS-1852977` → title "The Management and Operation…", 2018-10-01 →
   2028-09-30, program `NCAR-Nat Center Atmosph Resear`, PI `barron`, Monitor `cblack`.
   All verified present in the live API today.
2. `OCE-2622251` — PI does not resolve by email; the hint shows NSF's name and the picker
   stays empty rather than guessing.
3. `DEB-2224743` — `fundProgramName` is `LONG TERM ECOLOGICAL RESEARCH…`, absent from
   `nsf_program`; the create-and-select affordance appears.
4. Source DOE, `DE-SC0012671` → USAspending fills dates (2014-08-15 → 2020-10-14) and
   URL, and shows the explicit "PI/Monitor manual" note.
5. `AGS-9999999` → `FormError`, form preserved, nothing wiped.
6. Switch modes back and forth → no field loss; mode round-trips through a validation error.
7. Edit an existing contract: Monitor + NSF Program visible and editable.
8. Contracts list shows both new columns; watch the SQL log to confirm no N+1.
