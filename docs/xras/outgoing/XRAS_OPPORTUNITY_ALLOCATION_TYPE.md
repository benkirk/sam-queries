# `opportunityId` → allocation type — a mapping table, built additively

**Status:** **implemented as built**, 2026-08-20, on `xras_opportunityId`.
Phase 1 shipped in full, plus the free half of Phase 2 (§ 5.2). Phase 2's CLI
audit (§ 5.1) stays deferred.
**Base:** branched from `staging` after PR #458 squash-merged as `8ae154d`.
**Origin:** the deferred § 8.2 item in
`docs/xras/outgoing/XRAS_OUTGOING_QUERIES.md`, reshaped around one hard
constraint (§ 2).

The design below stands as written except where § 0 records a deviation.

---

## 0. As built — corrections and findings

Six things the design did not know. Each was verified against the code or the
production database before the build, and each is now pinned by a test.

### The premise was never checked — and it holds

This document proposes keying a table on `opportunityId` without ever asking
whether that id is **single-valued**. It is: across all 41 payloads, nine ids
resolve to five distinct `(panel, allocation_type)` pairs, **one pair each**.
That is what makes the map coherent rather than arbitrary, and it also *is*
the seed. Pinned by
`test_opportunity_id_is_single_valued_across_the_corpus` — a future fixture
that broke it would otherwise land silently.

### `Small` is the only *silent* case — § 3 implies two

§ 3 lists both `Small` and `Education` as panel collisions. Both really are
two-panel names in the database, but the ladder's twelve declared
`SelectionParms` (`extractors.py:91-104`) never name `UW`, `WRAP` or `LCAP` —
**the whole of facility 4 is unreachable through it** — and `Education` is not
among the twelve type names at all. So a WNA `Education` request fails
resolution **loudly** (422, nothing written), while a WNA `Small` resolves
silently to `UNIV USS`.

This sharpens the argument rather than weakening it: there is exactly one
nameable silent failure, and both halves are now asserted —
`test_a_wyoming_small_request_is_the_silent_case` and
`test_a_wyoming_education_request_fails_loudly_instead`.

### `allocation_type.panel_id` is NULLABLE

`src/sam/accounting/allocations.py:496`. The obvious implementation of
§ 4.2 — `row.allocation_type.panel.panel_name` — raises `AttributeError`
mid-dispatch on a type with no panel, turning an action the ladder would have
resolved perfectly well into a 500. The lookup treats a null panel as a **miss**
and falls through. `test_a_mapped_row_whose_type_has_no_panel_falls_through`.

### The § 4.2 trap is real, but only its *first* arm

`auth_at_panel_meeting` has two arms (`handlers/_allocations.py:239-246`).
Arm 1 — the payload carries `allocationType` — runs the chain, and is the one
that had to change. Arm 2 reads the **stored** `project.allocation_type`, which
is already map-consistent because the mapped resolver is what wrote it.
Repointing arm 2 at the map would change behaviour for payloads that omit
`allocationType` entirely, which is a different question. Both are now
documented in place, and the consistency invariant is asserted across the whole
corpus by `TestPanelAuthorisationAgreesWithTheResolvedType`.

Both guards were **negative-tested**: reverting arm 1 to the pure chain, and
separately disabling the map lookup, each fail the expected tests and only
those.

### § 5.2 came free, so it shipped now

The sweep needs no `/v1/opportunities` fetch to count unmapped ids: every
`reports/requests` payload it already enumerates carries `opportunityId`
inline. So `audit_opportunity_mapping` — which § 5.1 assigns to Phase 2 — was
built now, with `audit_resource_mapping`'s injection contract intact
(`opportunity_ids=None`, `live_checked`). The deferred CLI is then pure wiring.

⚠️ The reports payload spells the sibling field **snake_case**
(`opportunity_name`) while the inbound action wire spells it `opportunityName`.
Two vocabularies for one concept meeting inside one feature — the shape of the
`key`/`resourceRepositoryKey` bug. `test_the_opportunity_id_field_is_read_and_spelled_camel_case`
pins the inbound spelling.

### § 6's DDL row is wrong twice over

The `initdb.d` hook was not recreated. Two reasons the design could not have
known: `containers/sam-sql-dev/Dockerfile:8-27` deliberately **deleted** the
`COPY initdb.d/` line and records that an empty directory is not git-tracked,
so the directory and the COPY must be recreated together or the image build
fails — and the retired `zz-90` has drifted (`replay_of_id` vs today's
`source_action_id`), so it was a template, not a copy.

Instead the DDL was applied to production directly (`hpc-writer` holds
`CREATE, REFERENCES, INDEX, ALTER` — see the runbook) and the snapshot
regenerated, which is what was done for the previous three tables on
2026-08-10. The statements of record are in
`docs/xras/incoming/XRAS_CUTOVER_RUNBOOK.md` § 2.

⚠️ Consequently the nine seed rows are **in every regenerated snapshot**, so the
additivity tests `DELETE` inside their SAVEPOINT rather than assuming an empty
table.

### ~~Still deferred~~ · ✅ **BUILT 2026-08-20**

`sam-admin xras --validate-opportunities`. See § 8.6 for what it does and the one
place it deviates from the recipe below.

The two notes written for whoever built it both held: XRAS's `panels[]` is the
*review-panel* vocabulary (`CISL Resource Support`/`CISL RSD`), **not** SAM's
`panel` table — only `CHAP` coincides, so `/v1/opportunities`'s
`panels: [{panelId}]` is evidence for a human and never a derivation. And § 6's
"5-edit CLI recipe" is indeed 7 edit points across 4 files.

---

## 1. What you need to know first

XRAS is the ACCESS allocations broker. It **pushes** allocation decisions into
SAM at `POST /api/xras/v1/actions`; `src/sam/xras/` is the handler stack that
applies them. PR #458 added the opposite direction — a **read-only, GET-only**
client at `src/sam/integration/xras_api/` that calls `https://api.xras.org/v1/…`
— plus a dashboard worklist of accounts that must exist before a handoff can
succeed, and an hourly `xras_sweep` task.

None of that is required reading for this work. What matters here is one field
on the **inbound** wire that SAM currently ignores.

### The ladder, as it stands

`resolve_allocation_type(session, action, errs)` — `src/sam/xras/extractors.py:323`
— decides which `allocation_type` row a project gets. It runs an
**11-strategy chain** of string matching over the wire fields `allocationType`,
`opportunityName` and `requestTitle`, producing a
`SelectionParms(panel: str, allocation_type: str)`, then joins:

```python
row = (session.query(AllocationType)
       .join(Panel, AllocationType.panel_id == Panel.panel_id)
       .filter(Panel.panel_name == parms.panel)
       .filter(AllocationType.allocation_type == parms.allocation_type).first())
```

Facts worth having in front of you:

| | |
|---|---|
| Strategy signature | `_x_strategy(action) -> Optional[SelectionParms]` — **pure, sessionless, no I/O** (module docstring, `extractors.py:23-26`) |
| Pure entry point | `select_allocation_type_parms(action)` — `extractors.py:308` |
| Session-aware entry point | `resolve_allocation_type(session, action, errs)` — `extractors.py:323` |
| Consumers | `handlers/new.py:108,200` and `handlers/update.py:159,280` → `project.allocation_type_id` |
| Also | `handlers/new.py:175` → `facility_id = self.allocation_type.panel.facility_id` → `next_projcode(..., allocate=True)` |
| On failure | `errs.report(...)` then a hard **422**: `raise_if_any()` fires *before* any transaction opens, action recorded `failed`, nothing written |
| Coverage | **5 of 11** strategies are exercised by all 41 committed fixtures (`tests/unit/test_xras_extractors.py::EXPECTED`, pinned by `test_five_distinct_strategies_are_exercised`) |

### The field nobody reads

`opportunityId` is present in **41/41** fixtures and declared at
`src/sam/schemas/forms/xras.py:395` — and `grep -rn opportunityId src/` returns
that declaration and **nothing else**. It survives validation, lands in the
stored raw payload, and is ignored.

⚠️ `opportunityName` **is** read, at nine sites in `extractors.py` *and* at
`extractors.py:537` in `resolve_mnemonic_code`, where `opportunity.startswith('NCAR ')`
selects the lab mnemonic route. That last one is a **separate consumer** and
must not be disturbed by this work.

---

## 2. The constraint that shapes everything

From the operator, verbatim:

> Suppose XRAS_OUTGOING is disabled or entirely absent, we still need to be able
> to ingest incoming records. So I would like to see a path where
> "opportunityId → allocation type" is implementable but purely additive — that
> is, it adds fidelity when outgoing XRAS is plumbed, but does not break
> anything in its absence.

**One rule satisfies it: ingestion reads a local table and never calls out.**
The outgoing API is used only to *populate and audit* that table, offline.

| | outgoing absent / disabled | outgoing plumbed |
|---|---|---|
| Ingest | table empty → lookup returns `None` → **today's ladder, unchanged** | table hit → deterministic pair |
| Discovery | rows added by hand (there are 9) | audit CLI proposes rows, flags unknown ids |

This is not a novel shape. It is exactly `xras_resource_repository_key_resource`
(`src/sam/integration/xras.py:9`): a local table mapping an XRAS key to a SAM
entity, populated out-of-band, read at ingest by `handlers/_fields.py:144`,
audited two-sidedly by `audit_resource_mapping`. **SAM has no precedent for an
external API writing a `sam` table, and this design deliberately does not create
one.**

---

## 3. Why bother — the panel collision

The thin part of the ladder is the **panel**, not the type. Each strategy
hardcodes a panel, but the pair is genuinely ambiguous in production:

| `allocation_type` | panels it exists on |
|---|---|
| `Small` | **UNIV USS** (id 8) **and UW** (id 3) |
| `Education` | **UNIV USS** (id 9, inactive) **and UW** (id 18) |

**Operator context:** University of Wyoming (WNA) does not submit through XRAS
today, but **may in future**. So this is a latent risk to design around, not a
live bug — and three properties make it worth pre-empting:

1. **It fails silently.** Every other mapping gap here shouts: an unmapped
   resource key 422s the action and writes nothing. This one does not. The
   ladder returns `('UNIV USS', 'Small')`, the join **succeeds** — that is a
   perfectly valid row — and the action is *processed*. The only symptom is a
   WNA project holding a UNIV projcode, drawn from the wrong facility's series
   by `new.py:175`. Projcodes are not undoable.
2. **The deferral criterion cannot produce evidence.** § 8.2 says "assess after
   the first triage week under live dispatch". Triage week will be 100%
   `(University)` traffic and will report the same five exercised strategies and
   zero collisions the 41-payload audit already reports. A historical audit
   returns "no problem" every time, right up until the day WNA onboards.
   Waiting cannot generate the signal that would justify acting.
3. **The map buys lead time on exactly that day.** A new WNA opportunity gets a
   new `opportunityId`, visible in the sweep's enumeration **before any action is
   pushed** — the same "reachable ahead of the push" property PR #458 already
   ships for accounts. Map one row; the first WNA request lands correctly
   instead of being discovered later by its projcode.

### The domain is tiny

**9** distinct `opportunityId` across all 41 payloads; **5** opportunities open
today. This is a lookup table, not an algorithm.

Observed (from the committed, scrubbed corpus):

| opportunityId | wire `allocationType` | opportunity name |
|---|---|---|
| 532221 ×14 | Exploratory | Exploratory Allocation (University) |
| 532220 ×11 | Small | Small Allocation (University) |
| 532222 ×5 | Data Analysis | Data Analysis Allocation (University) |
| 532223 ×4 | Educational | Classroom Allocation (University) |
| 533144 ×3 | Large | Large Allocation (University) - Spring 2024 |
| 533606, 531428, 533936 ×1 | Large | …Fall 2024 / University Large Request - Fall 2021 / …Spring 2025 |
| 530902 ×1 | Small | University small request — with NSF award |

`GET /v1/opportunities` (live, verified 2026-08-20) returns 5 open
opportunities carrying `opportunityId`, `opportunityName`,
`displayOpportunityName`, `allocationType`, `panels: [{panelId, isPrimary}]`
and `allocationTypeInfo: {allocationTypeId, allocationType, description}` —
i.e. **XRAS already knows the answer the ladder is guessing at**, including the
panel.

⚠️ Do **not** key the map on the wire `allocationType` string. Its vocabulary
differs from SAM's and it is not unique (`schemas/forms/xras.py` says so
explicitly). Key on `opportunityId`.

---

## 4. Phase 1 — needs no outgoing API at all

This phase is the entire safety argument. It is independently shippable.

### 4.1 Table + ORM

`xras_opportunity_allocation_type`, modelled on `XrasResourceRepositoryKeyResource`:

- `opportunity_id` — PK, from the wire
- `allocation_type_id` — FK → `allocation_type.allocation_type_id`, NOT NULL
- `opportunity_name` — snapshot, for humans reading the table

**FK to `allocation_type_id`, not the `(panel, type)` string pair.** § 8.2 says
"keep the two-column join"; an FK satisfies that constraint *more* strongly — it
resolves the ambiguity by construction and cannot drift when a type is renamed.

### 4.2 One shared lookup

In `extractors.py`:

```python
def select_allocation_type_mapped(session, action) -> Optional[SelectionParms]:
    """Map hit → SelectionParms from the FK'd row; miss → the pure ladder."""
```

A hit yields `SelectionParms(row.allocation_type.panel.panel_name,
row.allocation_type.allocation_type)`. A miss falls through to the existing
`select_allocation_type_parms(action)`, untouched.

> ⚠️ **THE TRAP — both call sites must use it.**
> `auth_at_panel_meeting(session, action)` (`handlers/_allocations.py:222`)
> calls the **pure** entry point directly and tests
> `parms.allocation_type in {'CSL','CHAP'}` (`_PANEL_AUTHORISED`,
> `_allocations.py:59`) to set `auth_at_panel_mtg` on written
> **allocation_transaction** rows. It is called from `new.py:122`,
> `update.py:168`, `supplement.py:65`, `adjustment.py:88`.
>
> Wiring only `resolve_allocation_type` would let a project's allocation type
> come from the map while its transactions' panel-authorisation flag still came
> from the ladder — inconsistent rows, written, silently. It already takes a
> session, so it can share the same lookup. Do that.

### 4.3 Seed, then prove equivalence

Seed the 9 known ids with **the pair the ladder already produces today**, then
assert the corpus is unchanged. That is what makes this a drop-in rather than a
behaviour change: divergence becomes a later, deliberate, visible edit.

---

## 5. Phase 2 — additive, needs outgoing

1. **`sam-admin xras --validate-opportunities`**, mirroring `--validate-mapping`
   exactly — *including its injection contract*: the CLI fetches
   `/v1/opportunities`, the query function takes `opportunity_ids=None` and has
   **zero network knowledge**, and a `live_checked` flag distinguishes "XRAS
   sends nothing we lack" from "we never asked". Reports ids seen on the wire
   with no map row, and proposes a row using XRAS's own `allocationType` +
   `panels[]` as evidence.
2. **`xras_sweep` counts unmapped opportunity ids** in its `TaskResult.detail`.
   It **does not write** — keep its read-only posture.

Copy the exit-code discipline from `src/cli/xras/commands.py:71` — only
genuinely broken states are non-zero. `audit_resource_mapping` originally
failed on "unmapped by design" and *"made the command unusable as the deploy
gate its own docstring claimed it could be: it would have failed every time,
forever."*

---

## 6. Files

| | |
|---|---|
| `src/sam/integration/xras.py` | new model beside `XrasResourceRepositoryKeyResource` |
| `src/sam/__init__.py` | stage-9 import **and** `__all__` — registration is what the schema tests iterate |
| `src/sam/xras/extractors.py` | `select_allocation_type_mapped`; `resolve_allocation_type` consults it |
| `src/sam/xras/handlers/_allocations.py` | `auth_at_panel_meeting` uses the same lookup (§ 4.2 trap) |
| `src/sam/queries/xras_actions.py` | `audit_opportunity_mapping(session, *, opportunity_ids=None)` beside `audit_resource_mapping` |
| `src/cli/cmds/admin.py`, `src/cli/xras/{commands,builders,display}.py` | the 5-edit CLI-mode recipe (option + `execute` kwarg + `_mode()` + `build_*` + `display_*`) |
| `containers/sam-sql-dev/initdb.d/zz-9x-*.sql` | dev/CI DDL hook |

### DDL reality

`sam` has **no migrations** — Alembic covers only `system_status`
(`migrations/README.md`). A new ORM model with no table fails
`tests/integration/test_schema_validation.py::TestModelCoverage::test_all_models_have_tables`.

The precedent is the retired `zz-90` initdb hook: recover it with
`git show 24965d9:containers/sam-sql-dev/initdb.d/zz-90-xras_action_log.sql`.
It is a self-retiring dev/CI hook that lets the schema tests pass honestly until
the DBA applies the DDL to production and the snapshot is regenerated.

Applied-DDL procedure of record: `docs/xras/incoming/XRAS_CUTOVER_RUNBOOK.md`
§ 2. `hpc-writer` holds `CREATE, ALTER, INDEX, REFERENCES` on `sam.*`
(deliberately **no `DROP`** — get the DDL right the first time).
⚠️ `REFERENCES` is the non-obvious grant MySQL 8 needs on the **parent** of an
FK; this table has one.

---

## 7. Verification

- **The additivity guarantee, as a test.** With the table **empty**, the full
  `EXPECTED` corpus in `tests/unit/test_xras_extractors.py` (41 entries, 5
  distinct pairs) is unchanged and `test_five_distinct_strategies_are_exercised`
  still passes. This is the literal statement of "does not break anything in its
  absence" — write it first.
- **The equivalence guarantee.** With the 9 rows seeded, that same corpus is
  *still* unchanged.
- **The WNA case** — the one that cannot be observed in production until it is
  too late. A synthetic UW-panel opportunity with wire `allocationType: 'Small'`:
  assert the ladder resolves it to `UNIV USS` *and succeeds* (documenting the
  divergence, not just the fix), the map resolves it to `UW`, and the resulting
  `panel.facility_id` differs — since that is what reaches `next_projcode`.
- **Consistency.** `auth_at_panel_meeting` agrees with `resolve_allocation_type`
  for both a mapped and an unmapped action.
- **Schema tier.** A per-table guard modelled on
  `tests/integration/test_schema_validation.py:561` (the existing
  `test_xras_resource_repository_key_resource_schema`, which exists because that
  model *was wrong once, with 5 columns instead of 2*).
- **Wire vocabulary.** Add the new read to `tests/unit/test_xras_wire_vocabulary.py`.
  ⚠️ Read `handlers/_fields.py:150-163` first: the resource resolver read
  `'key'` instead of `'resourceRepositoryKey'` for an entire sprint, so **every
  resource on every action** silently reported `No resource found in SAM
  corresponding to key ` with nothing after it. It survived because every test
  built its own `{'key': ...}` fixtures. Copy that test, not just the resolver.
- `pytest tests/unit tests/integration tests/api` (7,233 passing on #458's head).

---

## 8. Cost, and the honest counter-argument

The ladder **works on 41/41 payloads today**, and will keep working for as long
as XRAS traffic stays all-University. This fixes no present failure.

What it buys: (a) it disarms a wrong-facility projcode that fails *silently* the
day WNA onboards, (b) it makes a new opportunity a one-row data fix instead of a
ladder edit plus a deploy — the reason to have it **before** triage week, since
triage would otherwise produce code patches under time pressure — and (c) a
two-sided audit of the one wire field SAM currently ignores entirely.

Against that: a hand-applied DDL, a new ORM model, and a second place where
allocation type is decided. The `auth_at_panel_mtg` coupling is the sharp edge.

**Scope note.** Phase 1 is the whole safety argument and needs no outgoing API.
Phase 2 is convenience. If the DDL is the blocker, Phase 1 alone is still worth
doing. They are separable on purpose.

---

## 8.5 Auto-detection — BUILT 2026-08-20

The operator posts roughly **four opportunities a year** (University Large x2,
NSC x2). Each used to be a hand-written `INSERT`. It no longer is: `xras_sweep`
maps a new opportunity by itself, and writes nothing it cannot corroborate.

### What the full enumeration showed

Probed live, 21 pages, 4,088 requests: **42 distinct opportunities**, not the 9
the scrubbed corpus contains. They collapse to **eight** stable
`(allocationTypeId, primary panelId)` pairs, and two of those carry the bulk:

| `allocationTypeId` | primary `panelId` | XRAS type | SAM `(panel, allocation_type)` |
|---|---|---|---|
| 500023 | 500022 (CISL HPC Allocation Panel) | Large | CHAP / CHAP |
| 500088 | 500045 (NSC Allocation Panel) | NCAR Strategic Computing | NCAR-ARP / NSC |
| 500026 | 500021 (CISL Resource Support) | Educational | UNIV USS / Classroom |
| 500024 | 500021 | Small | UNIV USS / Small |
| 500847 | 500021 | Exploratory | UNIV USS / Small (No NSF award) |
| 500848 | 500021 | Data Analysis | UNIV USS / Data |
| 501276 | 500046 (Admin Panel) | NCAR External Projects | External Projects / External Project |
| 500023 | **500045** | Large *(2018-era NSC)* | NCAR-ARP / NSC |

Every University Large since 2021 shares one pair; so does every NSC request.
**The operator's whole annual churn reuses two rows that have been stable for
five years** — which is why the reference half is eight entries rather than a
row per opportunity, and why it is a **constant** (`sam/xras/opportunity_types.py`)
rather than a table: it changes at code cadence, and a constant can be
test-asserted to name real `allocation_type` rows.

⚠️ That last row is why the key is the **pair**. `500023` on the CHAP panel is
CHAP; on the NSC panel it is NSC. Keying on `allocationTypeId` alone would have
filed a 2018 NSC request under CHAP.

### ⚠️ The premise this document opened with is wrong

Section 3 says `/v1/opportunities` means "XRAS already knows the answer the
ladder is guessing at". It does not. The mapping is **not injective onto SAM's
types**, in two directions:

| | ladder | XRAS |
|---|---|---|
| the unsponsored family (4 ids) | UNIV USS / **Small (No NSF award)** | UNIV USS / **Classroom** |
| `NCAR - ASD Opportunity` | **ASD-NCAR** (facility 7) | **NCAR-ARP / NSC** (facility 1) |

XRAS files unsponsored requests under `Educational`, the same id as
Classroom/Training; and it gives ASD NSC's own type *and* panel, so the two are
indistinguishable from the API at all. Both differences change the **facility**,
which is what reaches `next_projcode`. The operator adjudicated both: the ladder
is right, and all four are pinned `source='manual'`.

### The rule: two derivations must agree

`propose_opportunity_mapping` (`sam/queries/xras_actions.py`) derives the pair
twice — once from the constant, once from the free-text ladder — and the sweep
writes **only when they match**. Disagreement, an unknown pair, or a ladder that
declines is reported and withheld.

This is not belt-and-braces. It:

- **caught all four bad cases without knowing about any of them** — including
  two the design never anticipated (530296, 530315), found by the rule rather
  than by hand;
- **withholds the first Wyoming opportunity**, since the ladder cannot produce
  `UW` — which is also the alerting this section originally asked for;
- makes an error in the constant **self-limiting**: a wrong entry disagrees with
  the ladder and is withheld rather than written;
- held under a **partially-loaded database** — observed during a snapshot
  rebuild, where the `allocation_type` rows briefly vanished and all 41
  candidates were withheld with `missing_allocation_type`, writing nothing.

The value auto-writing adds is **durability**, not new correctness: the rows it
writes are ones the ladder already got right, made explicit so an opportunity
rename cannot break them. Correctness stays with the human-confirmed rows.

### What it writes, and what it cannot

| | |
|---|---|
| **Provenance** | `source` — `manual` or `task:xras_sweep`. The one schema change: `ALTER ... ADD COLUMN source VARCHAR(32) NOT NULL DEFAULT 'manual'`, recorded in the runbook. |
| **Never overwrites** | inserts only where no row exists, checked against the database rather than against `source`, so a `manual` row is safe from any future writer. |
| **Cap** | `SAM_TASKS_XRAS_MAP_MAX`, default 20 — a blast-radius bound. Steady state is zero or one a quarter. |
| **Ingest is untouched** | the handler path still reads one local table and never calls out. Writing happens out of band; if the sweep stops, the map stops growing and the ladder covers the gap. |
| **`--dry-run` is a full rehearsal** | `TaskContext.close_sessions` rolls back rather than commits, so the report is exactly what a real run would have done. Use it before any large backfill. |

Measured preview against the live API with the four manual rows in place:
**42 seen, 29 unmapped, 29 agreeing, 0 needing review, 0 unknown** — written 20
then 9 across two hourly runs.

⚠️ **The helper must stay above the `@task` decorator.** A module-level function
defined between `@task(...)` and `def xras_sweep` is registered as the task
body — silently, because the name is a decorator argument, and invisibly to
every unit test that calls `mod.xras_sweep` directly. It fails only at dispatch.
That happened; `test_the_decorator_is_bound_to_the_task_body` is the guard.

### 8.6 `sam-admin xras --validate-opportunities` · ✅ **BUILT 2026-08-20**

CLI wiring over `audit_opportunity_mapping` and `propose_opportunity_mapping`, as
predicted: option, `execute` kwarg, mode method, `_live_opportunities()`, builder,
display. Both query functions keep their injection contract — the CLI fetches, they
take ids or payloads and hold zero network knowledge, and `live_checked`
distinguishes "nothing unmapped out there" from "we never asked".

**Two decisions the recipe did not settle.**

⚠️ **It reads `GET /v1/opportunities` — the OPEN list — not
`/v1/opportunities/list/:ids`**, which is the opposite of what this section said
before it was built. The reasoning it was written with is sound for *backfill* and
wrong for a **check**:

- The historical tail is not this command's job. `xras_sweep` already resolves
  closed and Terminating ids by batch from `reports/requests`, hourly, and writes
  the agreeing ones. Duplicating that in the CLI means a 21-page, 60-90 s
  enumeration behind an interactive flag — and the ids it would report are ones no
  future action can cite, because the opportunity is closed.
- The open list is the only place a **brand-new** opportunity appears. By
  construction `reports/requests` cannot mention one nobody has submitted against
  yet — and that is exactly the row that would silently mis-resolve, because there
  is no request yet to notice it on. The lead indicator is the point.

The display says which scope it used, so a one-sided or open-only report cannot be
read as a stronger claim than it is.

⚠️ **The proposal runs over the UNMAPPED subset only**, mirroring
`_map_new_opportunities`. Run over everything and the four `source='manual'` rows —
the ones a human settled precisely *because* the derivations disagree — reappear in
`review` on every invocation. A bucket that is never empty is a bucket an operator
stops reading, and this is the one bucket that must be read.

**Exit code: non-zero on `dangling_ids` only.** Not on `unmapped_ids` (an empty
table is a healthy table — the ladder resolves everything, as it always did) and
not on `review` (never empty, by design). This is the same trap `--validate-mapping`
fell into once and corrected; both guards are negative-tested.

## 9. Do not

- ❌ Call the XRAS API from the ingest path. That is the circularity this design
  exists to avoid, and it would make ingestion depend on `XRAS_OUTGOING_ENABLED`
  and on a remote host being up.
- ❌ Put the lookup inside a strategy. The chain is pure and sessionless by
  construction; a DB read belongs in `resolve_allocation_type` or a
  session-taking pre-step.
- ❌ Key the map on the wire `allocationType` string (§ 3).
- ❌ Displace `opportunityName` — `resolve_mnemonic_code` (`extractors.py:537`)
  reads it for the lab mnemonic route.
- ❌ Delete or rewrite the ladder. It is the fallback, and it is what an empty
  table falls through to.
- ❌ Let the sweep write the table — **as shipped**. § 8.5.2 revisits this
  deliberately, and narrows it: the rule that must never bend is that the
  *ingest path* does not call out. A sweep that writes rows derived from
  XRAS's own declaration adds no runtime dependency to ingestion.

## 10. Reference

| | |
|---|---|
| `docs/xras/outgoing/XRAS_OUTGOING_QUERIES.md` | § 8.2 is the original deferral; § 0 records PR #458 as built |
| `src/sam/xras/extractors.py` | the ladder, the two entry points, `resolve_mnemonic_code` |
| `src/sam/integration/xras.py` | `XrasResourceRepositoryKeyResource` — the model to copy |
| `src/sam/xras/handlers/_fields.py:144` | `resolve_resource` — the ingest-side read to copy |
| `src/sam/queries/xras_actions.py:652` | `audit_resource_mapping` — the two-sided audit to copy |
| `docs/xras/incoming/XRAS_CUTOVER_RUNBOOK.md` § 2 | applied-DDL procedure and the `hpc-writer` grant |
