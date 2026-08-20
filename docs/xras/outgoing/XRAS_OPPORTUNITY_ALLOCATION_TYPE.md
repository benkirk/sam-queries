# `opportunityId` → allocation type — a mapping table, built additively

**Status:** designed, **not implemented**. Sketched 2026-08-20 with the operator.
**Base:** stacks on **PR #458** (`probing_xras` → `staging`, 19 commits, all CI
green). Branch from that head; do not branch from `staging`.
**Origin:** the deferred § 8.2 item in
`docs/xras/outgoing/XRAS_OUTGOING_QUERIES.md`, reshaped around one hard
constraint (§ 2).

This is a handoff document. It assumes **no prior context** — an implementation
session should be able to start cold from here.

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
- ❌ Let the sweep write the table.

## 10. Reference

| | |
|---|---|
| `docs/xras/outgoing/XRAS_OUTGOING_QUERIES.md` | § 8.2 is the original deferral; § 0 records PR #458 as built |
| `src/sam/xras/extractors.py` | the ladder, the two entry points, `resolve_mnemonic_code` |
| `src/sam/integration/xras.py` | `XrasResourceRepositoryKeyResource` — the model to copy |
| `src/sam/xras/handlers/_fields.py:144` | `resolve_resource` — the ingest-side read to copy |
| `src/sam/queries/xras_actions.py:652` | `audit_resource_mapping` — the two-sided audit to copy |
| `docs/xras/incoming/XRAS_CUTOVER_RUNBOOK.md` § 2 | applied-DDL procedure and the `hpc-writer` grant |
