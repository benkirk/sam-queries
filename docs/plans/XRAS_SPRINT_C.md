# XRAS Sprint C — the handlers, and the oracle that proves them

**Handoff doc.** Written for a cold start: you should be able to execute this without
the session that produced it. The wire contract, the measured production data and the
design decisions live in [`XRAS_REIMPLEMENTATION.md`](XRAS_REIMPLEMENTATION.md) — §
references below point there. The as-built records of the two prior sprints are
[`XRAS_SPRINT_A.md`](XRAS_SPRINT_A.md) (ingestion) and
[`XRAS_SPRINT_B.md`](XRAS_SPRINT_B.md) (the operator surface).

**Prior sprints.** Phases 1, 2 and 4 are done on PR #424, branch
`xras_reimplementation`: the six GET endpoints, `POST /actions` in capture mode with
`xras_action_log`, and the 4th Allocations tab with replay, `sam-admin xras` and the
activation worklist.

**This sprint is not blocked on anything.** Not on samples — the corpus is eight real
payloads and no handler lacks one. Not on the DDL — dev and CI have both tables from
tracked init scripts. Not on SMTP. The two items with external lead time (§ *Run these
in parallel*) sit beside this sprint, not in front of it.

---

## What this sprint delivers

The dispatcher and the six handler paths, so that `POST /actions` stops parking every
action as `manual` and starts doing the work legacy does — **one action type at a time**,
each proven against a real payload before the next is enabled.

And one thing that is not a handler: **the oracle**. There is no parity harness for a
write path and there cannot be a live one — both stacks share a single production
database, so "run both and compare" would apply every action twice. Build the
replay-and-diff check *with* the first handler. A handler with no oracle is not done.

---

## Run these in parallel — they are not this sprint's work, and they gate cutover

Neither is code. Both have external lead time. Start them the day this sprint starts.

1. **File the DBA ticket** for `xras_action_log` **and** `xras_activation_event` — one
   ticket, both init scripts (`containers/sam-sql-dev/initdb.d/zz-90-*.sql` and
   `zz-91-*.sql`). A second ticket costs another round of the same lead time, which is
   why Sprint B settled the second table's DDL before filing. Staging needs both run by
   hand once: `infrastructure/scripts/init-rds.sh` restores the raw `.xz` with no initdb
   hook. ⚠️ The `purge_xras_action_log` rule in `containers/sam-sql-dev/anonymize_sam_db.py`
   must be in place before the next snapshot regeneration — `raw_payload` is a verbatim
   POST body full of PII and the obfuscated dump is a committed public LFS blob.

   Landing this alone is worth doing on its own: it lets production posts be **recorded**
   while this sprint is still being written, and every recorded post is a harvested
   payload from the authoritative source. That is the cheapest remaining way to close the
   corpus gaps in § *What the corpus still does not cover*.

2. **Confirm the 400/422 contract change with `allocations@access-ci.org`** (§ 2.5).
   Legacy answers 200/500; this port answers 400 for a malformed body and 422 with the
   accumulated error list. **Broker retry behaviour on 4xx is unknown** and it is the
   riskiest open unknown on the cutover path. One caller has ever hit this surface —
   `18.223.62.77`, User-Agent `Ruby` — so this is a single-party conversation (§ 1.1).

---

## Settle this before handler one

**The actor question.** Legacy writes `allocation_transaction.user_id = NULL` for XRAS.
`log_allocation_transaction` (`src/sam/manage/allocations.py:69`) declares `user_id: int`
positionally, but the column is nullable (`src/sam/accounting/allocations.py:232`) and
nothing in the body validates or dereferences it — so `None` writes `NULL` and matches
legacy today. **Widen the hint to `Optional[int]` and document that `None` means an
integration actor.** Every handler and every parity diff depends on this, and changing it
midway invalidates diffs already taken.

**The replay invariant.** Every `allocation_transaction` write must keep
`replay(history) == amount`. These handlers are about to become the largest writer of
those rows, and the failure mode is a manual row double-counted against an auto-logged
one. Assert it in the oracle, not only in review.

---

## Build order, and why

Easy-path-first, so the pipeline is proven before the 30%-success path is attempted.
Shares and success rates are measured over 175 posts, 2026-07-07 → 2026-08-05 (§ 1.2,
§ 1.3):

| # | Handler | Share | Legacy success | Why here |
|---|---|---:|---:|---|
| 1 | **Extension** | 60% | 98.5% | Highest volume, nearly perfect, and the smallest surface — its only input is `actionEndDate` |
| 2 | **Supplement** | 15% | 100% | Extension's mirror: a populated `resources[]` and an additive amount |
| 3 | **Adjustment** | 0%* | — | No known-good outcome exists; review it hardest (see below) |
| 4 | **Update** | 3% | — | `New`/`Renewal` against an *existing* project — one dispatch decision with New, not two |
| 5 | **New** | 21% | **30%** | The hardest, and the one carrying all the pain |
| 6 | Transfer | 0% | — | **Not built.** Routes to the manual fallback with an audit row |

\* Zero in the measured window because legacy has **never serviced one** — defect 4:
XRAS sends `Adjustment`, legacy compares `Adjust`, so every Adjustment has only ever
produced a manual-fallback email. **This handler is the one to review hardest**: it is
the only one that will begin servicing traffic a human has always handled, with no
production outcome to diff against.

Flip each handler's slice out of capture mode as it lands, so `POST /actions` stays
continuously deployable.

---

## The dispatcher

The seam is `src/webapp/api/xras/actions.py:251` — currently a hardcoded
`_finish(log_id, status='manual')` with a comment saying so. `replay.py:152` has the
identical arm and becomes live with no change of its own.

**Legacy dispatches on the pair `(actionType, does the project exist)`**, first match
wins, registration order Add → Update → Supplement → Adjust → Transfer → Extend
(`~/codes/sam/src/main/java/edu/ucar/cisl/sam/action/ActionConfig.java:505-511`):

| Service | Selector |
|---|---|
| `AddProjectActionService` | `New` **and not** `exists(projcode)` |
| `UpdateProjectActionService` | (`New` **or** `Renewal`) **and** `exists` |
| `ExtendProjectActionService` | `Extension` and `exists` |
| `SupplementProjectActionService` | `Supplement` and `exists` |
| `TransferAllocationActionService` | `Transfer` and `exists` |
| `AdjustProjectActionService` | `Adjust` and `exists` — never fires |
| *no match* | `BadRequestException` → manual fallback → bare **200** |

Three traps the corpus proved, each of which would produce a wrong dispatch:

1. **"Update" is not an `actionType`.** It is a handler. The wire vocabulary is
   `New, Renewal, Extension, Supplement, Transfer, Adjustment, Advance`
   (`action/domain/model/Action.java:6`), encoded as `XRAS_ACTION_TYPES` in
   `src/sam/queries/xras_actions.py`.
2. **`New` does not imply a request token.** `new_uwis0071_existing_ok.json` is an
   `actionType: 'New'` whose `requestNumber` is the projcode of a project that already
   existed — legacy routed it to `UpdateProjectActionService` and emitted the "Existing
   XRAS project updated" subject. **Only the database can tell the two apart**, so
   resolve the projcode first, then branch. New and Update are one decision.
3. **`requestType` is useless for dispatch.** All eight sampled payloads carry
   `requestType: 'New'`, including both Extensions, both Supplements and the Adjustment.

Accept **both** `Adjust` and `Adjustment`. The alias already exists for the query layer —
`XRAS_ACTION_TYPE_ALIASES` and `canonical_action_type()`,
`src/sam/queries/xras_actions.py:72` — reuse it rather than adding a second spelling map.

---

## Samples in hand

Eight scrubbed production payloads in `tests/fixtures/xras/actions/`. Nothing except
Transfer is sample-blocked, and Transfer routes to manual regardless.

| Handler | Fixtures |
|---|---|
| Extension | `extension_ucub0166_ok.json` (success), `extension_ufsu0023_failed.json` (failure, with legacy's exact error string) |
| Supplement | `supplement_ubrn0027_ok.json`, `supplement_ucub0182_ok.json` |
| Adjustment | `adjustment_uwis0064_manual.json` — the `Adjustment`-vs-`Adjust` case |
| New | `new_ncar4253_ok.json` (success), `new_ncar4232_failed.json` (the 55% failure mode: an unreconciled ARC placeholder identity) |
| Update | `new_uwis0071_existing_ok.json` — `New` against an existing project |

Measured shape facts that change handler code, from § 2.4 and the schema docstrings in
`src/sam/schemas/forms/xras.py`:

- **`resources[]` is empty on both Extensions**, success and failure. An Extension handler
  cannot derive its targets from the payload; its only input is `actionEndDate`.
- **Supplement is the opposite** — populated `resources[]`, and `awardedAmount` is the
  **increment, not the new total**.
- **`isReconciled` is `true` even for the unreconciled identity whose absence caused the
  failure.** It is XRAS's view of its own state. Keep it inert.
- **`allocationType`** (`Small`, `Large`, `Educational`, `Exploratory`, `Data Analysis`)
  is inert on this path and its vocabulary does not match SAM's `allocation_type` table,
  where `Small` is not even unique. Do not build a mapping from it without deciding to.
- **`roleType` is `'PI'` / `'Allocation Manager'` / `'User'`**, space separated — a
  *different* vocabulary from the `Pi`/`CoPi`/`AllocationManager` keys of
  `GET /v1/requests/role/…`.
- **A `roleType` is not unique.** UWIS0071 carries two `PI` entries separated only by
  their date windows, resolving to different facilities. Legacy's pick-first
  `getUsernameByRoleType()` gets this wrong. **Filter on the date window, and reject only
  if that still leaves more than one** — rejecting outright is wrong, because UWIS0071 is
  legitimate traffic.

---

## Write primitives

### What exists — compose these

| Need | Callable |
|---|---|
| transaction boundary | `management_transaction(session)` — `src/sam/manage/transaction.py:12` |
| audit row | `log_allocation_transaction(...)` — `src/sam/manage/allocations.py:69` |
| create an allocation (gets-or-creates the Account) | `create_allocation(...)` — `manage/allocations.py:197` |
| update an allocation | `update_allocation(...)` — `manage/allocations.py:271` |
| membership | `add_user_to_project(...)` — `src/sam/manage/__init__.py:53` ⚠️ raises if the project has no accounts, so it must run **after** allocations |
| project | `Project.create` — `src/sam/projects/projects.py:233`; `Project.update` `:306`; `Project.reactivate` `:367` |
| projcode | `next_projcode(session, facility_id, mnemonic_code_id, allocate=True)` — `projects.py:1724` |
| unix gid | `GidAllocation.allocate_next_gid` — `src/sam/core/groups.py:292` |
| account | `Account.get_or_create` — `src/sam/accounting/accounts.py:111` |
| contract | `Contract.existing_by_number` — `src/sam/projects/contracts.py:249`; `ProjectContract.create` `:468` |
| mnemonic | `MnemonicCode.resolve_for_organization` — `src/sam/core/organizations.py:481` |

**The New handler already has a reference implementation**: the admin create-project
route, `src/webapp/dashboards/admin/projects_routes.py:600-687`, runs
`next_projcode(allocate=True)` → `allocate_next_gid` → `Project.create` →
`ProjectContract.create` → `ProjectOrganization.create` inside one
`management_transaction`. Port against it rather than against the Java.

⚠️ `management_transaction` does **no** implicit audit logging
(`src/sam/manage/transaction.py`). Every allocation mutation goes through
`log_allocation_transaction` explicitly.

### What does not exist and must be written

1. **A strict, account-scoped extend.** `extend_project_allocations`
   (`src/sam/manage/extend.py:40`) is project-*tree*-scoped and **silently skips shrinks**
   — `if source_root.end_date >= new_end: continue`. Legacy extends every active
   account's latest allocation and **errors** when the new end would shrink one. Add a
   strict variant or a mode; do not loosen the existing caller.
   Regression oracle: UFSU0023 (`actionEndDate` 2027-09-30 against an existing end of
   2033-07-31) must 422 with
   `Action end date is before existing allocation end date (2033-07-31)`.
   Legacy's comment string on the transaction is `XrasAction Extension Request`.
2. **An additive supplement.** Only `update_allocation(amount=…)` exists, which sets
   rather than adds. Legacy creates the allocation when the resource has none (start
   today, end = latest contract/allocation end), supplements when the increment is `> 0`,
   and log-warns on `≤ 0`.
3. **The allocation-type rule table.** § 3.2's eleven ordered strategies, transcribed as
   data — a `(panel, allocation_type)` pair table. ⚠️ **Never resolve by name alone**:
   `Small` appears twice in `allocation_type` and so does `Education`. Pair the rules with
   names and resolve the ids at runtime — never hardcode a lookup-table PK.
4. **The `AUTO_DEFAULT_ALLOCATION_TRANSACTION` undo compensation** the Update path needs
   — a compensating `UNDO AUTO/DEFAULT` adjustment. 33 such rows in two years.
5. **Nothing for Transfer.** `exchange_allocations` (`manage/allocations.py:416`) does not
   fit: it is 1→1, same-resource, and raises rather than clamping, where legacy's transfer
   is one negative source to N positive destinations summing to zero with the source
   clamped to available. Transfer routes to manual this sprint.

---

## Errors

**Accumulate, never short-circuit.** Legacy gathers every problem into an ordered
`LinkedHashSet` and raises once, which is what lets an XRAS admin fix a request in one
pass instead of five — they read the 422 body directly in their "Accounting Service
Posts" panel. The schema layer already does this (`_flatten` in `actions.py:151`); the
handler layer must accumulate its own and merge.

§ 3.4 lists ~23 exact error strings. ⚠️ Sprint A flagged that section as needing **a pass
against the Java source** before it is trusted: there are two different end-date
validators, and `ProjectActionCommandFactoryBase:58` has a dangling trailing colon-space
that a naive transcription would drop. Do that pass first — the strings are the contract
the oracle diffs against.

---

## The oracle

Replay the corpus against a test database and diff the resulting rows against what legacy
did for the same action.

- **The action-mix correlation in § 1.2 is the oracle for successes.** It was built by
  correlating success lines in `sam-xras-actions.log` against
  `allocation_transaction.creation_time` (±60 s) and `project.creation_time`, and it says
  exactly how many rows of which type each post produced.
- **UFSU0023 and NCAR4232 are the oracle for failures** — both have known-correct legacy
  outcomes with exact error strings to diff a 422 against.
- Assert the replay invariant (`replay(history) == amount`) over every allocation the
  handler touched.
- `utils/parity/` is GET-only today (`XrasClient` has no `post_action`), so this lives in
  the test tier rather than the parity harness. That is the right home: it needs a
  database it is allowed to mutate.

**Also unbuilt and cheap: `sam-admin xras --validate-mapping`**, named as a pre-cutover
run. 11 active SAM resources are unmapped in `xras_resource_repository_key_resource`
(Boreas, Destor, GLADE user, GLADE work, Gust, Gust GPU, hpc, hpc-dev, HPC_Futures_Lab,
Laramie, Quasar); closing a gap **moves response bytes**, because `resourceRepositoryKey`
is omitted when unmapped.

---

## Enablement

⚠️ **`XRAS_ACTIONS_CAPTURE_ONLY` is a global boolean** (`src/webapp/config.py:62`) and is
set in **neither** `helm/values.yaml` nor `compose.yaml` — production runs on the code
default. Flipping it today would enable all six paths at once, which is the opposite of
the rollout this sprint is built around.

Add per-type enablement (an allowlist, e.g. `XRAS_ACTIONS_ENABLED=Extension,Supplement`)
and a `helm/values.yaml` entry. Keep the global kill switch: it is the single safety
interlock, and **replay honours it deliberately** — a replay that dispatched while
capture was on would re-apply an action legacy has already applied, a double-apply
against live allocations one click away with no undo (`XRAS_SPRINT_B.md` § *Deviations*
item 3).

---

## Revisit, now that handlers exist

The one schema column Sprint B declined **with the escape hatch named**: a link from
`xras_action_log` to what a handler changed (e.g. an `allocation_transaction` reference).
It was declined because nothing wrote anything yet. It is a nullable additive column,
backfillable from `raw_payload` + `projcode_result` — not a migration — but adding it
after the DBA ticket lands costs another ticket. Decide during this sprint, not after.
See `XRAS_SPRINT_B.md` § *Schema deltas* → *Considered and deliberately declined*.

---

## What the corpus still does not cover

- **No co-PI role has ever appeared** in any of the eight payloads, so its `roleType`
  spelling is unknown. This one touches the New/Update roster build.
- **`Transfer`, `Renewal` and `Advance` have zero samples.** Transfer routes to manual, so
  it is not blocking; `Renewal` shares the Update path.

Both close the same two ways: a bulk forward from `hdt@ucar.edu` (the query and the ask
are in `XRAS_SPRINT_A.md` § 3b — include the manual-fallback subject, which is how the
Adjustment payload nearly went unnoticed), or production capture once the DDL lands.

---

## The ceiling this sprint cannot raise

New is 21% of posts at a **30%** legacy success rate, and six causes account for all 67
failures. Most of them are data, not code (§ 1.3, § 9):

- **`user_organization` is frozen** — no rows since 2026-07-09; 4,563 active users with no
  current organization; 2,092 rows pointing at a dangling `organization_id = 0`. Root
  cause of the 24% mnemonic failure class.
- **The mnemonic fuzzy match is broken**: `code LIKE '%name%'` against a `varchar(3)`
  column — **150 of 171** active organizations match nothing.
- **`person.organization` is free text** with inconsistent case and appended role
  suffixes. A curated or fuzzier mapping would move New's success rate more than any code
  in this sprint. That is a decision to take, not a port detail.
- **Organization 158 ("UCAR Community Programs") matches two mnemonic codes** and throws
  for any PI in it (defect 2).
- **Contract suffix collisions are live** — cores `1049089`, `1744587`, `2146709`. Legacy's
  `LIKE '%core'` + `uniqueResult()` guarantees a 500. Resolve exact → unique suffix →
  report. ⚠️ `contract` is `utf8mb3_bin` and therefore case-sensitive: use `ilike`.

**Decide deliberately whether to port these bug-for-bug.** § 9 says surface them as
reviewable 422s rather than silently copying or silently fixing them — a failure an
operator can act on is worth more than one that looks like success.

---

## Definition of done

1. The dispatcher exists, and each enabled handler writes `status='processed'` with
   `projcode_result` set. `_finish(status='processed', …)` is already written and has
   never had a caller.
2. Every enabled handler has a replay-and-diff test against its fixture, and the
   allocation-replay invariant is asserted.
3. `Optional[int]` widened on `log_allocation_transaction`, documented.
4. Per-type enablement plus a `helm/values.yaml` entry; the global kill switch retained.
5. § 3.4's error strings verified against the Java source.
6. The `xras_action_log` → `allocation_transaction` column decided (build or decline in
   writing) **before** the DBA ticket is applied.
7. A `## Deviations` section in this file recording where the repo's patterns won over
   this plan — as Sprint B did. This document is input, not contract.
