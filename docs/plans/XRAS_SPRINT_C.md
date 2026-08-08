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

   ⚠️ **Filing early buys lead time, not payloads.** An earlier draft of this section
   claimed the ticket alone would start capturing production posts. It will not: XRAS
   posts to legacy's URL, and nothing reaches this endpoint until XRAS repoints — which
   *is* the cutover. The table arrives empty and stays empty until then. Growing the
   corpus beforehand would need a dual-post arrangement, and that is **ruled out**. The
   corpus is 8 for the duration of this sprint; see § *What the corpus still does not
   cover* and the synthetic follow-on.

2. **Confirm the 400/422 contract change with `allocations@access-ci.org`** (§ 2.5).
   Legacy answers 200/500; this port answers 400 for a malformed body and 422 with the
   accumulated error list. **Broker retry behaviour on 4xx is unknown** and it is the
   riskiest open unknown on the cutover path. One caller has ever hit this surface —
   `18.223.62.77`, User-Agent `Ruby` — so this is a single-party conversation (§ 1.1).

---

## Settle this before handler one

**The actor question — ✅ settled, commit 1.** Legacy writes
`allocation_transaction.user_id = NULL` for XRAS, and **25,048 production rows carry
it** (measured 2026-08-07). `log_allocation_transaction`
(`src/sam/manage/allocations.py`) now types `user_id: Optional[int]` and documents that
`None` means an integration actor.

The mechanism in legacy is incidental rather than deliberate, which is worth knowing
before anyone "fixes" it: `username` is never set on any of the extend / supplement /
adjust / add-allocation commands, so `userRepository.get(null)` issues
`WHERE username = null`, returns null, and the column lands NULL. Same outcome, no
intent. **Do not invent a service account to avoid the NULL** — a synthetic user id is
indistinguishable from a real person in every report that joins this column, and it
would break diffing our rows against the years of legacy rows beside them.

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

Flip each handler's slice out of capture mode as it lands. That keeps `POST /actions`
continuously deployable and lets each handler be exercised in isolation locally — but
note it is a *development* sequence, not a rollout one: all six ship enabled in a
single deploy (§ *Enablement*).

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

### As built

`src/sam/xras/dispatch.py`. `dispatch_action(session, action, *, enabled)` returns a
frozen `DispatchResult(status, service, projcode, reason, warnings)` or raises
`XrasActionRejected`. The 422 path is deliberately **not** a result value: assemble →
check once → execute means a rejection happens before any transaction opens, so it is
an exception the route maps.

Both seams are wired — `actions.py`'s hardcoded `_finish(status='manual')` is gone, and
`replay.py` goes through the same dispatcher. Handlers register with
`register(service, handler)`; **the registry ships empty**, so every action still parks
as `manual` exactly as before. That is what lets commits 5–10 land one handler each
without touching the route again.

`DispatchResult.service` is recorded even on the `manual` arm. "Nothing matched" and
"the handler is disabled" and "the handler is not built yet" are indistinguishable in
`xras_action_log` otherwise, and they call for three different responses at 3am.

**Corpus dispatch, verified against the snapshot.** Five of the eight projcodes are
present in the obfuscated test database, so these are real lookups:

| Payload | `actionType` | Project in snapshot | → |
|---|---|---|---|
| `extension_ucub0166_ok` / `extension_ufsu0023_failed` | Extension | ✅ | `extend` |
| `supplement_ubrn0027_ok` / `supplement_ucub0182_ok` | Supplement | ✅ | `supplement` |
| `adjustment_uwis0064_manual` | Adjustment | ✅ | `adjust` — **a service legacy could never reach** |
| `new_ncar4232_failed` / `new_ncar4253_ok` | New | ✗ (request token) | `add` |
| `new_uwis0071_existing_ok` | New | ✗ here, ✅ in prod | `add` here, `update` there |

That last row is trap 2, and it is tested by *creating* the project mid-test and
re-dispatching the same payload: one payload, two answers, the only variable being
whether the row exists.

⚠️ **The existence check must not filter on `active`.** XRAS-created projects arrive
`active = 0` by design (`InactivateNewProject`), so an active-only check would route a
re-posted New action to the **Add** handler and mint a second project for the same
request. `Project.get_by_projcode` does not filter, which is why it is the right
callable here.

---

## Per-handler semantics, from the Java

Read from source at tag 2.0.3. This is the porting specification; § 3.3 of the
reference doc is the summary.

### Extension

```java
project.getAccounts().stream()
    .filter(a -> a.isActive() && a.hasAllocations())
    .map(Account::getLatestAllocation)
    .filter(this::isAllocationEndDateExtendable)     // reports and drops on shrink
    .map(al -> buildExtendAllocationCommand(al, comment))
```

- **`resources[]` is ignored entirely** — per *active account*, one allocation each.
  `Account.isActive()` is `project.isActive() && resource.isCommissioned(now) &&
  !creationTime.after(now)`.
- `Account.getLatestAllocation()` returns the max-end-date allocation, but an
  allocation with a **null end date short-circuits and is returned immediately**. And
  `Allocation.getEndDate()` is decommission-clamped: `min(stored end, resource
  decommission)`, with a null stored end reading through as the decommission date.
- Shrink test is `getEndDate().before(allocation.getEndDate())` — **strictly** before,
  so equal end dates pass and emit a **no-op** extend command. That is a candidate
  explanation for the "2 successful posts that mutated nothing" in § 1.2.
- A reported shrink drops *that* allocation but the stream continues; the accumulated
  error then aborts the whole action at `throwExceptionIfErrors`. One bad account kills
  the extension.
- Comment is `action.getClass().getSimpleName() + " Extension Request"` — a Java class
  name leaking into the database. **Hardcode `XrasAction Extension Request`**;
  production has 1,416 rows of it and 8,552 of the pre-2025-10 `XRAS Extension Request`.
- Command order: `validateNewEndDate` (pre-order walk of the whole child subtree, each
  node must pass) → resolve user (always null) → `disinherit()` → `extend()`, which
  walks the subtree writing one `EXTENSION` row per node, **skipping nodes whose end
  date already equals the new one**.
- Row shape: `transaction_type=EXTENSION`, `alloc_end_date` set, `transaction_amount`
  / `requested_amount` / `alloc_start_date` **NULL**, `user_id` NULL, `propagated`
  false.

#### As built

`src/sam/xras/handlers/extension.py` + `extend_account_allocation` in
`sam/manage/extend.py`. Registered via `sam/xras/handlers/__init__.py`, which
`webapp/api/xras/actions.py` imports for the side effect.

**The assembler composes one factory, so the Extension path validates almost nothing.**
`ExtendProjectAssembler` wires only `ExtendProjectAllocationActionCommandsFactory` — no
project factory, no roster factory. So an Extension can emit **none** of `Missing
title`, `Missing pi role`, `PI %s is not in database` or `Username %s is missing`. Its
entire input is `actionEndDate`. Worth stating because the corpus makes it look
otherwise: both Extensions carry a populated `roles[]` that nothing reads. This is
stronger than the § *roster* note about `resources: []` producing zero add-user
commands — the factory is never constructed at all.

**Three findings that changed the implementation:**

1. ⚠️ **`!creationTime.after(now)` is not ported.** It compares two clocks that are not
   the same clock: `account.creation_time` carries `server_default=CURRENT_TIMESTAMP`
   and resolves in the **MySQL server's** timezone (UTC in dev/CI) while `now` is
   naive-Mountain. Measured against the test container the same second: `NOW()` = 12:45,
   `datetime.now()` = 06:45 — **six hours**. The conjunct can only ever *exclude*, so
   honouring it under skew makes an Extension posted within six hours of a New silently
   skip the account it should extend, report `processed`, and write nothing. Dropping it
   is a no-op wherever the clocks agree. Same family as the `received_time` default this
   repo already removed for the same reason.
2. **The NULL row shape is the table's convention, not just legacy's.** Of the 10,504
   EXTENSION rows *not* written by the XRAS/AMIE integrations, **10,489 also carry NULL**
   `transaction_amount`, `requested_amount` and `alloc_start_date` — 20,603 of 20,618
   rows overall. So this is not "bug-compatibility", it is the column convention.
   `log_allocation_transaction` snapshots those columns unconditionally, so
   `extend_account_allocation` nulls them on the row it just wrote. Deliberately *not*
   fixed in the helper: that would also change the operator-facing Extend Allocation
   flow's audit output. Reasonable follow-up; the measurement above is the argument.
3. **`Allocation.extend_allocation` could not be reused** — it writes the snapshot
   shape, sets `propagated` on child nodes (production has **zero** propagated XRAS
   rows), has no equal-end-date skip, and takes a non-optional `user_id`.

**`Account.is_active` is the wrong predicate here** and this is the case where the
house rule (§ 5) gives the wrong answer: SAM's hybrid on that model is `SoftDeleteMixin`
("not deleted"), while legacy means `project.isActive() && resource.isCommissioned(now)`.
Composed explicitly from the other models' documented predicates, with the soft-delete
check kept *as well* — a declared divergence, unobservable (zero deleted accounts of
17,989), but extending a deleted account would be wrong regardless.

**Detach writes an audit row; legacy's `disinherit()` does not.** Production holds
**zero** DETACH rows against 2,390 inheriting allocations. Routed through
`detach_allocation`, which emits `transaction_type='ADJUSTMENT'` with a `[DETACH]` tag
and `transaction_amount = 0.0`. Declared divergence — SAM's audit trail is the product.

**Testing hazard, recorded because it bit once.** A registered handler commits through
`management_transaction` on the route's own connection, *outside* the suite's per-test
SAVEPOINT. The first run after registering this handler leaked three EXTENSION rows and
three mutated `end_date`s into the shared test database (found, repaired, verified).
Two guards now exist: unit tests patch `management_transaction` to flush instead of
commit, and API tests that are not about a handler take the `no_handlers` fixture. **Any
capture-off API test added from here needs that fixture.**

### Supplement

Per requested resource; existing allocation looked up via `Project.getAccount(name)`
(a plain scan over **all** accounts, active or not).

```java
if (allocation == null)                       return buildAddAllocationCommand(resource);
else if (getTransactionAmount(resource) > 0)  return buildSupplementAllocationCommand(...);
return null;                                   // <= 0 silently dropped
```

- **`awardedAmount` is the INCREMENT**, not the new total — `SUPPLEMENT` does
  `allocation.addAmount(transaction_amount)`. This is the single most important
  porting semantic here.
- ⚠️ The `> 0` test **unboxes a null `Float`** when the amount is blank or unparseable,
  throwing an NPE *inside* assembly — so `throwExceptionIfErrors` never runs and the
  operator gets a bare `NullPointerException` instead of `Awarded amount missing`. We
  guard and keep the diagnostic (declared divergence).
- Create branch dates: **start = today at 00:00**, not the action's begin date. End =
  latest **contract** end if not before today, else latest **allocation** end if not
  before today, else report `All contract and allocation end dates are null or past…`.
- Row shape on supplement: `SUPPLEMENT`, `transaction_amount` = increment,
  `requested_amount` NULL, comment = normalized `resources[].comments` or NULL,
  `auth_at_panel_mtg` per the CSL/CHAP rule, dates NULL, `user_id` NULL.

### Adjustment

A near-verbatim copy of Supplement with `buildAdjustAllocationCommand` swapped in —
**including the `> 0` guard**, which means legacy's adjust handler silently drops
negatives, the one thing an adjustment is for. Combined with defect 4 (it tests
`"Adjust"` while the wire says `"Adjustment"`) it has never serviced a single action.
We accept both spellings **and** honour negatives. Row shape: `ADJUSTMENT`, signed
`transaction_amount`, everything else NULL.

### New

`AddProjectAssembler` marks its order `// the order below is important!!`:
**AddProject → AddContract → AddAllocation×N → AddUser×N → InactivateNewProject.**

The aggregation overwrites each collaborator's `projcode` with the *generated* one
after step 1 — during assembly they all hold the XRAS **request number**.

Why the order cannot be rearranged:
- `Project.addAllocation` throws `Cannot add allocation to inactive project %s`, and
  `Account.isAssignable()` requires `project.isActive()` — so the project is created
  **active** and inactivated only at the end.
- Accounts are created as a side effect of adding an allocation, and user assignment
  requires an account (`Assignment to project {0} cannot be made until project has an
  account on resource {1}.`). Allocations must precede users. Our
  `add_user_to_project` has the same constraint for the same reason.

Field sources: title = `cleanText(requestTitle, 255)`; lead = PI; admin = AM (optional,
no error when absent); AOI from primary `fosNum`; charge type **always** `NONEXEMPT`;
`active` **always** true; allocation type via the extractor chain; mnemonic via the
extractor; org acronym from the *lead's* `getBestOrganization()`; `extAlias` always null.

Allocation dates use the **action's own** begin/end (unlike Supplement), end
EOD-clamped. Commission clamping happens downstream in the command: an early start is
**silently clamped forward**; an end at-or-before commission is **fatal**.

### Update

Per resource, and a single resource can emit **three** commands in this order:

| condition | result |
|---|---|
| no allocation, **or** existing does not overlap the action window | **ADD** (using the action's dates) |
| overlaps, existing EOD end **after** action end | **ERROR** `Action end date before existing allocation end date for %s` |
| overlaps, existing end **before** action end | **EXTEND**… |
| …then unless `resources[].comments == "AUTO_DEFAULT_ALLOCATION_TRANSACTION"` | (undo — dead, see defect 5) then **SUPPLEMENT** (`>0`) or **ADJUST** (`<0`) |

⚠️ `isAllocationOverlapping` requires both action dates non-null, so a blank action
date routes to ADD — and then NPEs on the commission clamp. Guard it.

Update-driven extends use the **resource comment**, not `XrasAction Extension Request`.

Two legacy bugs on this path we fix: it **silently re-activates an inactive project**
(`setActive(getActive())` with `getActive()` hardcoded true, and no `InactivateNewProject`
to undo it), and it **never actually updates lead or admin** — the guard compares the
fetched user's username against the lookup key, which is always equal, and
`setLeadUser` is missing braces so only its first statement is guarded.

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
3. **The allocation-type rule table** — see § *Allocation-type resolution* below for the
   verified strategies. ⚠️ **Never resolve by name alone**: `Small` appears twice in
   `allocation_type` and so does `Education`. Resolve the `(panel, type)` pair to an id
   at runtime; never hardcode a lookup-table PK.
4. ~~**The `AUTO_DEFAULT_ALLOCATION_TRANSACTION` undo compensation.**~~ **Not needed —
   the path is dead in legacy.** See § *Legacy defect 5* below.
5. **Nothing for Transfer.** `exchange_allocations` (`manage/allocations.py:416`) does not
   fit: it is 1→1, same-resource, and raises rather than clamping, where legacy's transfer
   is one negative source to N positive destinations summing to zero with the source
   clamped to available. Transfer routes to manual this sprint.

---

## Allocation-type resolution — eleven strategies, first non-null wins

`allocationtype/AllocationTypeIdExtractor:10-22`. Resolves to a **`(panel, type)` pair**,
then `findByPanelAndType`. Order verified against the source:

| # | Strategy | Match rule | → `(panel, type)` |
|---|---|---|---|
| 1 | ACCESS | if `allocationType` non-null → **exact lookup by SAM type name** (see trap below); else lowercase `opportunityName` contains `discover` → Discover; contains `explore` or equals `staff allocations` → Explore | `("ACCESS","Discover ACCESS")` / `("ACCESS","Explore ACCESS")` |
| 2 | NSC | `opportunityName.startsWith("NCAR - NSC Allocation Request")` | `("NCAR-ARP","NSC")` |
| 3 | External | full-match `(.* )?External( .*)?` against **any** of `requestTitle`, `opportunityName`, `allocationType` | `("External Projects","External Project")` |
| 4 | CSL | full-match `\s*CSL(\|[\W].*)` against `requestTitle` only | `("CSLAP","CSL")` |
| 5 | Large | `allocationType == "Large"` or `opportunityName` contains `Large Allocation` | `("CHAP","CHAP")` |
| 6 | SmallNonNSF | contains `no NSF award` \| `unsponsored` \| `Exploratory Allocation` | `("UNIV USS","Small (No NSF award)")` |
| 7 | SmallNSF | contains `w/ NSF` \| `with NSF` \| `Small Allocation` | `("UNIV USS","Small")` |
| 8 | Classroom | contains `Classroom/Training` \| `Classroom Allocation` | `("UNIV USS","Classroom")` |
| 9 | DataAnalysis | contains `Data Analysis Allocation` | `("UNIV USS","Data")` |
| 10 | ASD-UNIV | lowercase `opportunityName.startsWith("univ - asd opportunity")` | `("ASD-CHAP","ASD-UNIV")` |
| 11 | ASD-NCAR | lowercase `opportunityName.startsWith("ncar - asd opportunity")` | `("ASD-NCAR","ASD-NCAR")` |

All null → `Unable to determine allocation type from action data`. Pair resolved but
absent from `allocation_type` → `No AllocationType for SelectionParms{…}`.

⚠️ **Three traps, each of which silently produces the wrong answer.**

1. **The CSL regex in § 3.2 is markdown-mangled.** It renders as `\s*CSL(\|[\W].*)`,
   which reads as a *literal pipe*. The source is
   `Pattern.compile("\\s*" + "CSL" + "(|[\\W].*)")` — an **alternation with an empty
   left branch**: "CSL alone, or CSL followed by a non-word char and anything". Java
   `.matches()` is full-match, so Python needs
   `re.fullmatch(r"\s*CSL(|[\W].*)", request_title)`.
2. **Strategy 1 short-circuits on the literal `"Small"`.** `AllocationType.lookup` is
   keyed on SAM's *type names*, so a wire `allocationType: "Small"` resolves
   immediately to `("UNIV USS","Small")` and never reaches strategies 2–11. `"Large"`
   does **not** (its type name is `CHAP`), and `"Educational"` / `"Exploratory"` /
   `"Data Analysis"` all miss and fall through. § 3.2's "may return null and fall
   through" only half-covers this. `Small` is the second most common resulting type,
   so this is a live path, not a corner.
3. **Java's POJO defaults hide null-safety bugs.** `XrasAction.allocationType` defaults
   to `""`, never Java-null, and `ACCESSStrategy` / `LargeStrategy` dereference it
   unguarded. Our schema admits `None` (the wire sends JSON `null`), so Python would
   raise where Java does not. Normalise `None` and `""` identically at the boundary.

Production frequency of the resulting types (automated creations, 12 months) — order
test coverage by it: `Small (No NSF award)` 146 · `Small` 87 · `Data` 79 ·
`Classroom` 52 · `CHAP` 30 · `NSC` 16 · `Discover ACCESS` 15 · `Explore ACCESS` 10 ·
`External Project` 4.

### As built — and the chain is verified against production, not just the Java

`src/sam/xras/extractors.py`. `select_allocation_type_parms()` is pure (no session);
`resolve_allocation_type()` resolves the pair to a row and reports.

**Six of the eight corpus payloads resolve to the exact `(panel, type)` the real
project carries today.** That closes the question the table above could only answer by
inspection:

| Payload | Wire `allocationType` | Strategy | Resolved | Production |
|---|---|---|---|---|
| UCUB0166 / UWIS0064 / UWIS0071 | `Small` | 1, exact | `UNIV USS` / `Small` | ✅ same |
| UFSU0023 | `Large` | 5 | `CHAP` / `CHAP` | ✅ same |
| UCUB0182 | `Exploratory` | 6 | `UNIV USS` / `Small (No NSF award)` | ✅ same |
| UBRN0027 | `Data Analysis` | 9 | `UNIV USS` / `Data` | ✅ same |
| NCAR4253 | `Small` | 1, exact | `UNIV USS` / `Small` | absent from sample |
| NCAR4232 | `Educational` | 8 | `UNIV USS` / `Classroom` | never created (fail fixture) |

So the corpus exercises **five of eleven** strategies. The other six — NSC, External,
CSL, SmallNSF, ASD-UNIV, ASD-NCAR — are pinned by unit tests only, and that is
recorded in the deviations section rather than left implicit.

⚠️ One divergence, in `_clean()`. Java distinguishes an *absent* `allocationType`
(Jackson default `""` → exact-lookup branch, which can only miss) from an explicit
JSON `null` (→ the `opportunityName` branch that detects Discover/Explore ACCESS).
marshmallow gives `None` for both and the distinction is not recoverable. We take the
`null` behaviour, which is strictly the more capable one — the only payloads affected
are ACCESS-instance ones omitting the key, where legacy resolves nothing at all.

**A second consumer of the same resolution:** `getAuthAtPanelMeeting()` is `true` iff
the resolved type is `CSL` or `CHAP` — but note the branch is **inverted** from what
you would expect (`ProjectAllocationActionCommandsFactoryBase:96-114`): when the
payload carries an `allocationType` it runs the strategy chain; when it does **not**,
it reads the *existing project's* stored type and looks that up by name.

---

## The other three extractors — as built

All in `src/sam/xras/extractors.py`, all reporting into `ActionErrors` rather than
raising, per § *The extractors report rather than propagate*. Pinned by
`tests/unit/test_xras_extractors.py` (97 tests).

### Area of interest — ⚠️ the plan's `FosAoi` route was wrong

**`fosNum` is an `area_of_interest_id`, not an `fos_aoi.fos_id`.** Legacy's
`areaOfInterestRepository.findOne(fosInt)` is a Spring Data *primary-key* lookup;
`fos_aoi` is not on this path and cannot be — its `fos_id` values are 5-digit
AMIE/XSEDE codes (`10202`, `10501`, …) while XRAS sends `1`–`40`. Settled against
production three ways: the id spaces are disjoint (asserted in a test), every corpus
payload's primary `fosNum` equals the `area_of_interest_id` its real project carries,
and every `fosName` XRAS sends is SAM's `area_of_interest` string verbatim. Reading
this through `fos_aoi` would have mis-filed every XRAS project's research area
silently, with no error. `XRAS_REIMPLEMENTATION.md` § *Data* is corrected.

Non-numeric `fosNum` falls back to a name lookup, mirroring the `NumberFormatException`
arm. Empty `fos: []` → `No FieldOfScience (fos) objects`.

### Contract — the collision now reports instead of 500ing

Three steps, of which the middle one is legacy:

1. Exact match on the **full grant number** first (`Contract.get_by_number`,
   whitespace-insensitive). Strictly better than legacy and never wrong.
2. Else the ≥6-digit core-number suffix match, `ilike '%core'` — the column is
   `utf8mb3_bin`, so a plain `LIKE` undercounts. Exactly one row → that row.
3. A tie → **report, naming the candidates**. Legacy closes the same query with
   Hibernate's `uniqueResult()`, which raises `NonUniqueResultException` — *not* an
   `AttributeExtractionException`, so it escapes the observer and becomes a 500 with no
   diagnostic. Three cores collide in production today (§ *Data*).

This adds the one string to the vocabulary this sprint has added:
`Ambiguous contract for grant number "%s" ("%s"): matches %s`. It never replaces a
legacy message — it appears only where legacy emitted nothing at all.

### Mnemonic — 24% of failures, and one silent legacy hole

Three routes in legacy's order: `opportunityName` starting `'NCAR '` → the **lab**
strategy (walk the PI's org parentage to level 3); else an institution → `"Name, City"`
then `"Name"`; else the organization name. Reuses the existing
`MnemonicCode.build_lookup` / `resolve_for_*` ports so the code XRAS picks and the one
the admin create-project form suggests cannot drift.

**Declared divergence:** `UserLabStrategy` has no error arm — it returns `null` in
silence, so an NCAR-opportunity PI whose lab has no soft link yields a project with no
mnemonic and a failure that surfaces later and less legibly. We report
`Could not determine Mnemonic code for internal PI via organization`. A lab is an
organization, and a projcode cannot be minted without a code.

`pi_username` is passed in rather than read off the action, so this module does not
depend on `sam.xras.roster`; legacy reads `action.getPiUsername()`, the same value.
⚠️ `ProjectActionCommandFactoryBase:110`'s `action.getMnemonicCode()` short-circuit is
dead on the XRAS path — `XrasAction.getMnemonicCode()` is a hardcoded `return null`.
It is the AMIE actions sharing the base class that supply one. Nothing to port.

The parentage walk gets a cycle guard: `parent_org_id` is a self-FK with nothing
stopping a loop, and legacy's `while (org != null)` would hang the request thread.

---

## The roster — `roles[]` read twice, differently

`XrasAction.java`. Two readings of one array, and conflating them is the easiest way
to get membership wrong.

| Reading | Method | Filter | Result |
|---|---|---|---|
| **Role assignment** | `getPiUsername()` / `getAllocationManagerUsername()` | `roleType` **must** equal `PI` or `Allocation Manager`, plus a date window | project **lead** / **admin** |
| **Roster** | `getUsernames()` | **`roleType` never examined** — date window only | **every** entry becomes a member |

`ActionRoleName` has exactly two constants, `PI("PI")` and
`ALLOCATION_MANAGER("Allocation Manager")` — space-separated, case-sensitive, and a
*different vocabulary* from the `Pi`/`CoPi`/`AllocationManager` keys of
`GET /v1/requests/role/…`. So a `Co-PI` or `User` is invisible to role assignment but
**is still added to the project**.

The predicates, verified:

```java
// roster — getUsernames()
if (roleBeginDate.compareTo(actionDate) > 0) continue;                       // strictly excluded
if (endDate != null && endDate.compareTo(actionDate) < 0) continue;

// role assignment — getUsernameByRoleType()
if (roleBeginDate > actionDate && currDate <= roleBeginDate && currDate <= actionDate)
    continue;                                                                 // excluded only if ALSO future
if (endDate != null && endDate.compareTo(actionDate) < 0) continue;           // identical to roster
```

- **The end-date rule is identical on both readings.** Only the begin-date rule differs
  — § 3.5 says so and it is correct, but its snippet omits the roster's end-date line,
  which invites the wrong conclusion.
- The role-assignment begin rule is a triple conjunct: a future-dated role is ignored
  **only while the action itself is also still in the future**. Once the action begin
  date has passed, a future-dated role is *accepted*. That is defect 3: such a person
  becomes project lead but is excluded from the roster, so they lead a project they
  have no account on. **Port both, and warn when they disagree.**
- All comparisons are lexicographic `String.compareTo`, correct only because the wire
  is zero-padded `yyyy-MM-dd`. ⚠️ Jackson defaults these fields to `""`, so an empty
  `endDate` compares *less than* any real date and the role is skipped — a Python port
  parsing dates would behave differently.
- `getUsernames()` **does not dedupe**; one human holding two roles appears twice.
  `Account.assign` is idempotent, so this is harmless — but our port dedupes anyway.
- `getUsernameByRoleType` returns the **first** survivor: defect 1. Our rule is filter
  on the date window, reject only if more than one still survives.

`AddUserToProjectActionCommandsFactory.create()` fans the roster out **per resource** —
one command per `resources[]` entry, each carrying every username. ⚠️ With
`resources: []` — **both Extensions in the corpus** — zero add-user commands are
produced even though the roster is non-empty.

### As built

`src/sam/xras/roster.py`. `resolve_roster()` returns a frozen `Roster`
(`pi_username`, `admin_username`, `member_usernames`, `warnings`) and reports into
`ActionErrors` in legacy's order: PI → Allocation Manager → members. The three
predicates are separately callable and **pure** — `roster_usernames()`,
`role_candidates()`, `role_assignment_disagreements()` — so the date arithmetic is
tested without a session, which matters because the corpus usernames were scrubbed
independently of the obfuscated snapshot and resolve to no rows.

`today` is an injectable argument, not a call to `datetime.now()`. The role-assignment
rule reads the current date, so a test that let it float would pass or fail depending
on when it ran.

**Corpus roster shapes**, computed and pinned:

| Payload | Members | Note |
|---|---:|---|
| `adjustment_uwis0064_manual` | **0** | ⚠️ defect 3, live — see below |
| `extension_ucub0166_ok` / `extension_ufsu0023_failed` | 2 | but `resources: []`, so nothing consumes them |
| `new_ncar4232_failed` | 2 | 3 roles, 2 distinct humans, one of them a `User` |
| `new_ncar4253_ok` / `supplement_ucub0182_ok` | 2 | |
| `new_uwis0071_existing_ok` | 1 | 3 roles: one PI expired, the other two are one human |
| `supplement_ubrn0027_ok` | 1 | PI and Allocation Manager are the same person |

Two facts worth having before the cutover, both asserted: **no corpus payload names an
ambiguous role** (so none would be rejected by the defect-1 rule), and **every corpus
payload names a PI** (so `Missing pi role` is reserved for a genuinely malformed
request).

**Defect 3 is live in the corpus, not hypothetical.** `adjustment_uwis0064_manual`
carries roles beginning 2025-08-06 against an `actionBeginDate` of 2021-08-15. The
roster excludes both people; both role assignments resolve. Legacy would set a lead and
an admin on a project neither has an account on. Carried as a `Roster.warning` and a
`logger.warning`, not repaired — it is the only evidence anyone has that this occurs.

The asymmetry that makes the disagreement one-directional is asserted over a grid of
begin/end/action/today combinations rather than argued: role assignment's begin-date
exclusion is the roster's conjoined with two further conditions, so
`members ⊆ assigned` always.

Two divergences beyond the plan's list:

1. **`_wire_str` treats absent as JSON null**, the same rule
   `sam.xras.extractors._clean` uses. Java's `XrasRole` defaults every string to `""`,
   so an *absent* `endDate` compares less than any real date and Java skips the role
   entirely. All eight corpus payloads send `endDate` as JSON `null`, never absent and
   never `""`, so the observed wire is unaffected — and "no end date" plainly means
   current. `username` and `roleType` keep the `""` reading, because
   `Username␣␣is missing` is the exact bytes legacy emits for a role with no person.
2. **`User.is_active` is `active AND NOT locked`**; Java's `isActive()` is `active`
   alone. House rule § 5 says use the hybrid. Measured: production has **zero** locked
   users out of 28,371, so the divergence is unobservable today.

---

## Legacy defect 5 — the AUTO/DEFAULT undo has never fired

Sprint C's plan and § 3.1 both said the Update path must reproduce a compensating
`UNDO AUTO/DEFAULT` adjustment, "33 such rows in two years". **It does not, because the
mechanism is broken in legacy and has never once executed.**

`ActionTag` is a two-valued enum where `name()` and `getValue()` differ:

```java
AUTO_DEFAULT_ALLOCATION_TRANSACTION("AUTO/DEFAULT"),
UNDO_AUTO_DEFAULT_ALLOCATION_TRANSACTION("UNDO AUTO/DEFAULT");
```

Writers use `.name()` (`AllocationTransaction:23`, `UpdateProjectAllocationActionCommandsFactory:62,110`,
`AMIEAction:112`); the detector `isAutoDefaultAllocation` compares `.getValue()`
(`ProjectAllocationActionCommandsFactoryBase:153`). They never match.

Settled against production data (2026-08-07):

```sql
SELECT transaction_comment, COUNT(*) FROM allocation_transaction
 WHERE transaction_comment LIKE '%AUTO%DEFAULT%' GROUP BY 1;
```

| `transaction_comment` | rows |
|---|---:|
| `AUTO_DEFAULT_ALLOCATION_TRANSACTION` | 33 |
| `AUTO/DEFAULT` | 17 |
| *any `UNDO` spelling* | **0** |

The "33 rows" the plan cited are the `.name()` form — what the *writer* produces, not
what the detector looks for. The 17 `AUTO/DEFAULT` rows are an older writer, so the
detector is reachable only against pre-existing legacy data, and **no compensating
adjustment has ever been written in either spelling.**

**Decision: do not port it.** Detect the tag and log a warning so the situation is
visible if it ever arises; take no compensating action. Porting dead code invents
behaviour nobody has observed, on the Update path, against live allocations. The
separate *contingent-resource* short-circuit — wire `resources[].comments ==
"AUTO_DEFAULT_ALLOCATION_TRANSACTION"` meaning extension-only — compares `.name()`
on both sides and **does** work; that one is ported.

---

## The error vocabulary

> **This section supersedes `XRAS_REIMPLEMENTATION.md` § 3.4**, which was written from
> the POJOs before anyone read the emitters and is **wrong or incomplete in seven
> places**. The pass against the Java source Sprint A asked for is done; the results
> are below and are implemented in `src/sam/xras/errors.py`, one named builder per
> message with its emitter cited, pinned byte-for-byte by
> `tests/unit/test_xras_errors.py`.

### Assemble → check once → execute

Legacy builds the entire command list first — pure, no writes — reporting problems
into a `LinkedHashSet` on `ProcessingAction`, then calls `throwExceptionIfErrors` and
only then executes (`AbstractServiceableProjectActionService.addOrUpdate`, whole
handler in `@Transactional(REQUIRES_NEW)`). **Nothing is written if assembly reported
anything.** Port that shape: build a plan, accumulate, raise 422, and do not open
`management_transaction` until validation has passed.

The container is insertion-ordered **and deduplicating**, and both halves are
load-bearing. Three resources each missing `awardedAmount` yield **one** `Awarded
amount missing`; `AddAllocationToProjectActionCommandsFactory` calls `getResourceName`
twice per resource, so an unmapped key reports twice and collapses to one line. A
Python `list` diverges on every multi-resource failure. `ActionErrors` provides
`dict.fromkeys` semantics.

### The 33 strings

Java paths relative to `~/codes/sam/src/main/java/edu/ucar/cisl/sam/`. ␣ marks
significant whitespace. **Bold** rows are absent from or mangled in § 3.4.

| Exact string | Emitter |
|---|---|
| `Missing title` | `action/command/ProjectActionCommandFactoryBase:28` |
| `Missing pi role` | `…ProjectActionCommandFactoryBase:39` |
| `PI %s is not in database` | `…:43` — no trailing punctuation |
| `PI %s is not an active user:␣` | `…:45` — ⚠️ trailing colon-space |
| `Allocation Manager %s is not in database:␣` | `…:58` — ⚠️ trailing colon-space |
| `Allocation Manager %s is not active␣` | `…:60` — ⚠️ trailing bare space |
| `Username %s is missing` | `action/command/AddUserToProjectActionCommandsFactory:55` |
| `Username %s is inactive` | `…:57` |
| `No resource found in SAM corresponding to key %s` | `action/command/ProjectAllocationActionCommandsFactoryBase:38` — allocation path, `resource.getKey()` |
| `No resource found in SAM corresponding to name %s` | `AddUserToProjectActionCommandsFactory:81` — roster path, `getResourceName()` |
| `Awarded amount missing` | `ProjectAllocationActionCommandsFactoryBase:55` |
| **`Could not convert awarded amount "%s"␣␣to float`** | `…:66` — ⚠️ **two spaces** before `to float` |
| **`Missing begin date for allocation(s)`** | `…:85` — § 3.4 collapses this and the next into one slashed string |
| **`Missing end date for allocation(s)`** | `…:85` |
| **`Could not convert begin date for allocation(s)`** | `…:91` — absent from § 3.4 |
| **`Could not convert end date for allocation(s)`** | `…:91` — absent from § 3.4 |
| **`Action end date is before existing allocation end date (%s)`** | `ExtendProjectAllocationActionCommandsFactory:42` — **Extension** path, `%s` is a `yyyy-MM-dd` date |
| **`Action end date before existing allocation end date for %s`** | `UpdateProjectAllocationActionCommandsFactory:52` — **Update** path, `%s` is a resource name, and no "is" |
| `All contract and allocation end dates are null or past for project [%s]` | `SupplementProjectAllocationActionCommandsFactory:73`, duplicated at `AdjustProjectAllocationActionCommandsFactory:72` |
| `Cannot find contract for grant number "%s" ("%s")` | `action/domain/model/ContractNumberExtractor:21` — grant number, then extracted core |
| `Could not determine Mnemonic code for external PI via institution` | `…/mnemoniccode/MnemonicCodeExtractor:39` |
| `Could not determine Mnemonic code for internal PI via organization` | `…:47` — **24% of all legacy XRAS failures** |
| `Could not produce affiliation data for PI %s` | `…:56` |
| **`No FieldOfScience (fos) objects`** | `action/domain/model/AreaOfInterestExtractor:14` — fires on `fos: []`; absent from § 3.4 |
| `AreaOfInterest (FOS) id is not in database: %s` | `…:25` |
| `Unable to determine allocation type from action data` | `…/allocationtype/AllocationTypeIdExtractor:31` |
| **`No AllocationType for SelectionParms{panel='%s', type='%s'}`** | `…:37` — pair resolved but no row; absent from § 3.4 |
| `Transfer supports only one source (negative amount)` | `TransferProjectAllocationActionCommandsFactory:57` |
| **`Transfer requires one source resource (negative amount)`** | `…:67` — a *third* arity string; absent from § 3.4 |
| `Transfer requires at least one destination resource (positive amount)` | `…:71` |
| `Transfer source project:resource (%s:%s) has no allocation` | `…:93` |
| `Transfer destination credit (%f) exceeds source allowed debit (%f)` | `…:102` — ⚠️ Java `%f` is **six decimal places** (`1000.000000`) |
| `Request not serviced, no appropriate service found` | `action/service/ProjectActionServiceSelector` — `BadRequestException`, never reaches a client |

Two more that are *not* observer-reported and therefore never reach the accumulated
422 in legacy — they escape as exceptions and become a 500 — but which our handlers
must decide about explicitly:

| String | Emitter | Note |
|---|---|---|
| `End date of allocation (%s) must be after commission date of resource(%s).` | `DefaultAddAllocationToProjectCommand:63` | ⚠️ **no space** before `(` in `resource(%s)`. `IllegalStateException` |
| `Cannot add allocation to inactive project %s` | `project/domain/model/Project:251` | why `InactivateNewProject` runs last |

### Strings this port adds

Adding to an operator-facing vocabulary is a contract change, so each one is listed
with the case it covers and the reason legacy has nothing there. Neither replaces a
legacy message; both appear only where legacy emitted **nothing at all**.

| String | Covers | What legacy does |
|---|---|---|
| `Ambiguous contract for grant number "%s" ("%s"): matches %s` | two contracts share a ≥6-digit core | `uniqueResult()` raises `NonUniqueResultException`, which is not an `AttributeExtractionException` → escapes the observer → **500, no diagnostic** |
| `Multiple %s roles are in range for this action: %s` | two current `PI` or `Allocation Manager` roles | `getUsernameByRoleType` returns the **first** survivor and discards the rest, so array order decides who leads the project (defect 1) |

And one legacy string reused in a place legacy does not emit it:

| String | New site | Why |
|---|---|---|
| `Could not determine Mnemonic code for internal PI via organization` | the **lab** route (`opportunityName` starts `'NCAR '`) | `UserLabStrategy` alone has no error arm — it returns `null` in silence, and the project is created with no mnemonic. A lab is an organization, and a projcode cannot be minted without a code |

### The extractors report rather than propagate

`ProjectActionCommandFactoryBase` catches `AttributeExtractionException` from the AOI
(`:79-81`), allocation-type (`:103-105`) and mnemonic (`:115-117`) extractors and funnels
`e.getMessage()` into the observer, so those accumulate with everything else rather
than aborting. Reproduce that: an unresolvable mnemonic and a missing title arrive in
the same 422.

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

~~⚠️ `XRAS_ACTIONS_CAPTURE_ONLY` is set in neither `helm/values.yaml` nor
`compose.yaml`~~ — **done.** Both flags now carry helm entries with the reasoning
inline: `XRAS_ACTIONS_CAPTURE_ONLY: "1"` (the interlock, flipped only in step with the
repoint) and `XRAS_ACTIONS_ENABLED: "all"` (the lever). `compose.yaml` names no feature
flags at all — they arrive via `.env` — so it is not a target.

`XRAS_ACTIONS_ENABLED` accepts `all` (default), `none`, or a comma-separated list in
either Adjust spelling. **An unknown token is logged and dropped**, which fails safe in
the direction that matters: a typo like `Extention` leaves Extension *disabled*, so its
actions park as `manual` for a human rather than being written by a handler nobody
meant to enable. Refusing to start would be worse — this is the lever reached for
during an incident and it must not be able to take the app down.

**It keys on action type, not handler.** That is what the operator has in hand:
`xras_action_log.action_type` is the column they are reading when they decide to pull
it. The consequence, tested and accepted: disabling `New` disables *both* the Add and
the Update handler. "Stop processing New actions" is the thing being asked for.

**The two flags are not the same flag**, and a test pins the precedence:
`XRAS_ACTIONS_CAPTURE_ONLY` outranks any allowlist setting. While legacy is the system
of record, no value of `XRAS_ACTIONS_ENABLED` may cause a dispatch.

**What the allowlist is for, given the deployment shape.** Cutover is a single repoint
and **all six handlers go live at once** — so the allowlist is *not* a rollout
mechanism. Its job is triage: when one action type misbehaves in the week after
cutover, it can be parked back on the manual-fallback path — which is what legacy does
with an unserviceable action anyway — by config, without a code deploy. Sized for a
3am decision, not a release plan.

During development it does double duty: it is how a handler is exercised in isolation
on the local stack while the others still park.

**Replay honours the capture flag deliberately** — a replay that dispatched while
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
