# XRAS Sprint C — the handlers, and the oracle that proves them

> **STATUS: shipped.** All six services are registered, the dispatcher is wired into
> both seams, and every error string is exercised or declared unreachable. Suite at
> **5,213 passed** (baseline 4,708 at sprint start). Each major section now carries an
> **As built** subsection recording what the code does and where it diverged; § *Deviations*
> at the end is the consolidated list. The prose above those subsections is the original
> plan, kept because the reasoning is still the best explanation of *why* — but where the
> two disagree, **the As-built text and § Deviations are correct**.
>
> | Commit | |
> |---|---|
> | 1 | error vocabulary + integration actor |
> | 2–3 | extractors, roster |
> | 4 | dispatcher + triage lever |
> | 5–10 | Extension, Supplement, Adjustment, New, Update, Transfer |
> | 11a–c | oracle + mapping gate, error-coverage matrix, replay decoupling |
> | 12 | this record |
>
> **What remains before cutover is not code** — the four gates in § *Run these in
> parallel* and § *Deferred to deploy time*.

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

**As built**, the "one at a time" sequencing needed no configuration at all: the handler
registry ships empty and each handler registers itself on import, so an unbuilt service
takes the manual arm unchanged. The per-type flag stayed what § *Enablement* says it is —
a triage lever for after cutover, not a rollout mechanism.

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

#### As built

`src/sam/xras/handlers/supplement.py`, plus two new primitives in
`sam/manage/allocations.py`.

**`supplement_allocation()` — the additive primitive that did not exist.** Only
`update_allocation(amount=…)` was available, and it *sets*. Confirmed against the
production row shape: all 3,203 integration-written SUPPLEMENT rows carry NULL
`alloc_start_date` and `alloc_end_date`, 2,752 carry NULL `requested_amount`, zero are
`propagated`, and 1,264 carry `auth_at_panel_mtg` — so that flag is not vestigial.

**`log_integration_transaction()` — one place for the integration row shape.** After
Extension it was clear three handlers would each need to un-snapshot the same columns.
It delegates to `log_allocation_transaction` (keeping one insert site, one
`LEGACY_TYPE_MAP` translation, one place a future audit hook would go) and then states
every informational column explicitly as a keyword. `extend_account_allocation` was
refactored onto it in the same commit, so there is one implementation rather than two
that drift.

**A structural asymmetry with Extension worth naming.** Extension ignores `resources[]`
and filters accounts hard; Supplement walks `resources[]` and looks accounts up
**unfiltered** (`Project.getAccount(name)` scans all of them). So a supplement lands on
an account whose resource is decommissioned or whose project is inactive, where an
extension would skip it. Both behaviours are legacy's and both are tested.

**The name join is safe, and now provably so.** `Account.isForResource` matches on
resource *name*, case-insensitively — which could pick the wrong account if two
resources differed only by case. They cannot: `resources_name_unique_idx` is a unique
index on a case-insensitive collation, so the database refuses the second row. Asserted
in a test, because the whole argument for keeping legacy's name join rests on it.

Kept bug-for-bug: the create branch derives its window from **today** plus the latest
non-past contract (else allocation) end, and **never reads `actionBeginDate` or
`actionEndDate`** — a Supplement that creates an allocation gets dates XRAS did not ask
for. Reproduced because the alternative is inventing a policy, and 100% of Supplement
traffic succeeds under the current rule. Non-positive amounts are still dropped
silently, but now with a `logger.warning` so triage can see them.

Divergence: the blank/unparseable `awardedAmount` NPE is guarded, keeping
`Awarded amount missing` in the 422 where legacy loses every accumulated diagnostic to
a bare `NullPointerException`.

### Adjustment

A near-verbatim copy of Supplement with `buildAdjustAllocationCommand` swapped in —
**including the `> 0` guard**, which means legacy's adjust handler silently drops
negatives, the one thing an adjustment is for. Combined with defect 4 (it tests
`"Adjust"` while the wire says `"Adjustment"`) it has never serviced a single action.

#### As built

`src/sam/xras/handlers/adjustment.py` + `adjust_allocation()` in
`sam/manage/allocations.py`. The per-resource pieces are **imported** from
`supplement.py` rather than copied — same key resolution, amount parsing, unfiltered
account lookup and create branch. Three things differ, all from the Java: the
transaction type, the absence of `auth_at_panel_mtg` (`buildAdjustAllocationCommand`
never sets it), and the sign.

⚠️ **This is the one handler with no production outcome to diff against.** Everything
in it is reasoned from the source rather than confirmed against behaviour. Two
divergences carry the risk:

1. **Negatives are honoured** — the `> 0` gate is removed. Nothing depends on it,
   because nothing has ever run.
2. **An adjustment that would take an allocation below zero is rejected**, via one
   added string. Legacy has no such guard (`verifyValidateState` checks only the end
   date) but legacy also never applies one. A below-zero `amount` makes every
   `remaining = allocated − used` nonsense; the guard can only reject, never corrupt;
   and a rejected Adjustment goes to a human, which is where 100% of them go today.
   Reducing an allocation to **exactly** zero is allowed — an award withdrawn is a real
   case.

⚠️ **Formatting note that is not cosmetic.** The new string uses `,.2f`, not `g`. With
`g`'s six significant digits an adjustment of **-1,000,001** against an allocation of
1,000,000 rendered as `-1e+06` — a message asserting that a number *equal* to the
balance would take it below zero. Caught by a test; pinned by another. `sam.fmt` is
also wrong here: it compacts above 100,000 (`68.6M`), which is right for a dashboard
and wrong for a value someone must reconcile against a wire payload.
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

#### As built

`src/sam/xras/handlers/new.py`. Ported against the admin create-project route rather
than the Java, per the plan — `next_projcode(allocate=True)` → `allocate_next_gid` →
`Project.create` → `ProjectContract.create` / `ProjectOrganization.create`.

**Everything is resolved and reported before anything is written**, so the seven
distinct failure classes an XRAS admin can hit arrive in one 422 rather than one per
resubmission. Both scarce resources — the projcode counter and the GID pool — are drawn
**inside** the transaction, after `raise_if_any()`, so a rejected action consumes
neither. Tested.

Two divergences:

1. **An end date at or before the resource's commission date reports instead of
   raising.** Legacy throws `IllegalStateException` from
   `DefaultAddAllocationToProjectCommand`, which is not observer-reported and becomes a
   500 with no diagnostic. Same refusal, one an operator can act on. The string keeps
   its missing space before `(`.
2. **Projcode/GID exhaustion raises `XrasProjectCreationFailed`, not
   `XrasActionRejected`.** Nothing about the *request* is wrong, so a 422 telling XRAS
   to fix its payload would be a lie. Both conditions need a human with database
   access, not a resubmission.

The early-start commission clamp stays **silent**, as legacy's is, and is isolated in
the handler rather than pushed into `create_allocation` — the operator-facing flows
should keep rejecting a bad start rather than quietly moving it.

⚠️ **Two concurrency findings from the test suite, both worth keeping.**

The first is a property of the handler, not of the tests: `allocate_next_gid` takes
`with_for_update()` on the lowest-`startGid` block and the handler then holds that row
lock across `Project.create`, whose `_ns_place_in_tree` issues a **table-wide**
`UPDATE project SET tree_left = …` to shift siblings. Twelve xdist workers doing that
concurrently deadlock reliably. In production this is a non-issue — one webapp process,
one action at a time — but it is worth knowing that the New path holds a global lock for
the duration of a project creation. The tests stub the pool; `test_gid_allocation.py`
still covers it for real.

The second was mine: the fixture used a fixed `mnemonic_code.description`, which carries
a unique index, so every worker inserted the same value concurrently — a second
deadlock, surfacing as a fixture error rather than a test failure. The soft-link pair is
now worker-unique. **Any future fixture that seeds a soft link needs the same
treatment**, because the org name and the mnemonic description must be *equal* to link
and *unique* to insert.

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

#### As built

`src/sam/xras/handlers/update.py`. Assembler order **UpdateProject → AddContract →
UpdateAllocation×N → AddUser×N** — no mnemonic (the projcode exists) and, critically,
**no inactivation step**, which is the source of bug 1.

Three legacy bugs, three different treatments:

| Bug | Treatment |
|---|---|
| Silently re-activates an inactive project | **Not ported.** `active` is simply absent from the `Project.update` call, and a warning fires. An XRAS project is inactive because a human has not approved it; approving it as a side effect of a Supplement is wrong |
| Never updates lead or admin | **Fixed.** Plainly a bug — an always-true guard plus missing braces |
| `UNDO AUTO/DEFAULT` compensating adjustment | **Not ported.** Detected and warned, with the log line naming defect 5. Zero UNDO rows in production, either spelling |

The **contingent-resource** short-circuit *is* ported: wire `resources[].comments ==
"AUTO_DEFAULT_ALLOCATION_TRANSACTION"` means "move the date, leave the amount". It
compares `.name()` on both sides and genuinely works, unlike the undo — the two are
easy to conflate and a test pins that the marker is the `.name()` spelling, not
`AUTO/DEFAULT`.

Two things a reader will otherwise get wrong:

* **The shrink error is not Extension's string.** Update interpolates a *resource name*
  and omits the word "is"; Extension interpolates a *date* and includes it. Which one
  an operator sees is how they tell which path rejected them.
* **Update-driven extends carry the resource comment**, not
  `XrasAction Extension Request`.

`is_allocation_overlapping` keeps legacy's "either date null → no overlap" behaviour,
which routes the resource to ADD. Legacy then dereferences the same null on the
commission clamp and throws; unreachable here because assembly reports the bad date
first, but the guard stays explicit rather than implied.

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

   **As built** — `src/sam/xras/handlers/transfer.py` is a *registered* handler that
   returns `manual` with a reason, not an absent one. The difference matters at 3am:
   an unregistered service reports `no handler is registered for 'transfer'`, which
   reads like something is broken, while this records that the action was recognised
   and deliberately deferred, and that legacy does service it. A test pins that the two
   messages do not read the same.

   Three reasons it is not built, in order of weight: **zero production traffic** in the
   175 measured posts and none in the corpus, so there is no payload to port against and
   no outcome to diff against; the one primitive that looks like a fit is a different
   operation sharing a name; and it is the only action type that moves allocation
   **between projects**, so a wrong implementation is wrong in two places at once and
   the error is a real balance rather than a date.

   All five Transfer error strings are implemented and pinned regardless — including
   `Transfer requires one source resource (negative amount)`, the third arity string
   missing from § 3.4 — so a future implementation starts from verified bytes. The
   query to watch is `status='manual' AND action_type='Transfer'`.

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
| `Adjustment of %s for %s would take the allocation below zero (currently %s)` | a negative Adjustment larger than the allocation | `verifyValidateState` checks only the end date, so nothing stops it — but nothing has ever hit it either, because that handler has never serviced an action |

And one legacy string reused in a place legacy does not emit it:

| String | New site | Why |
|---|---|---|
| `Could not determine Mnemonic code for internal PI via organization` | the **lab** route (`opportunityName` starts `'NCAR '`) | `UserLabStrategy` alone has no error arm — it returns `null` in silence, and the project is created with no mnemonic. A lab is an organization, and a projcode cannot be minted without a code |

### Coverage — as built

`tests/unit/test_xras_error_coverage.py`. **Every one of the 34 builders is either
exercised by a synthetic payload or declared unreachable with a reason**, and the two
declarations are asserted against the module, so a new string with neither fails the
suite. That is the structural difference from the "~15 hand-written fixtures" the plan
scoped: a fixture pile decays silently, a checked declaration does not.

| | count |
|---|---:|
| Reachable and exercised | **28** |
| Declared unreachable — Transfer, not serviced | 5 |
| Declared unreachable — `no_resource_for_name` | 1 |

`no_resource_for_name` is worth naming because it is the one *structural* divergence in
the vocabulary: it is the **roster** path's resource lookup. Legacy fans the roster out
per `resources[]` entry and resolves each by *name*; SAM's `add_user_to_project` is
project-scoped and adds a member to every account at once, so there is no per-resource
name lookup to fail. The allocation path's key variant (`no_resource_for_key`) is the
one that fires.

The matrix also pins two behaviours nothing else could:

* **three resources each missing an amount produce exactly one**
  `Awarded amount missing` — the accumulator's dedup, in the place most likely to be
  got wrong, and only a synthetic payload can produce the shape;
* **seven distinct problems across seven categories arrive in one 422** — identity,
  classification, resource, amount, contract, mnemonic and roster together. That is
  assemble → check once, demonstrated rather than asserted.

⚠️ **The matrix caught its own payload once**, which is worth recording: a test meant to
produce `Unable to determine allocation type from action data` instead succeeded,
because its `requestTitle` read *"Nothing matching CSL or External here"* — and
`ExternalStrategy` full-matches `(.* )?External( .*)?` against the **title** as well as
the opportunity name. A title merely *mentioning* the word resolves the whole action to
`External Projects`. Noted in the test, because it is an easy trap for anyone writing a
payload by hand.

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

### As built

`tests/unit/test_xras_oracle.py`. Starts from the **bytes on disk**, loads them through
`XrasActionSchema`, and lets the *dispatcher* choose — covering the three seams no
handler suite can:

1. **bytes → schema → dispatcher → handler → rows** in one pass;
2. the **§ 1.2 action-mix correlation** as per-post row-shape claims (Extension writes
   one `EXTENSION` per touched allocation — the shape behind "3.3 allocations per post";
   Supplement one per *requested resource*; New one allocation per resource), including
   § 1.2's *"2 successful posts that mutated nothing"*, reproduced via the
   equal-end-date skip;
3. the **replay invariant swept over every allocation an action touched**, asserted as a
   *delta* rather than against `amount` — the factories seed no `NEW` row, so absolute
   equality would test the fixtures rather than the handler.

Referents are substituted, never sampled: the corpus usernames and most projcodes were
scrubbed independently of the snapshot. **The wire shape stays the real bytes**; only
`requestNumber` / `roles[]` / `resources[]` move.

Two things the run itself taught:

* **UCUB0166's `actionEndDate` is 2026-12-31 exactly**, so seeding an allocation with
  that end made the extension a legitimate **no-op** — the equal-end-date skip firing
  correctly, and a test expecting three rows failing for the right reason. The seed
  default now precedes every corpus end date, with a note, because the coincidence is
  easy to reintroduce.
* **`new_ncar4253_ok.json` carries `grants: ['EAR-2425607']`**, so the New path's
  contract resolution is exercised end to end — and the action *fails* without that
  contract. Not a fixture detail: a New action whose grant SAM does not hold is one of
  the measured production failure classes.

The `committing` fixture patches `management_transaction` in **all five** handler
modules. Each imports it by name, so patching one would silently let the others commit
past the per-test SAVEPOINT onto the shared xdist database — the hazard that already
bit once in commit 5.

**`--validate-mapping` is built** and reproduces the documented gap exactly: the same 11
active unmapped resources, plus 6 mappings pointing at decommissioned kit (Cheyenne,
GLADE fs1, Geyser_Caldera, HPSS, Janus, Yellowstone). It exits `EXIT_NOT_FOUND` on an
active gap so it can gate a deploy script, and `EXIT_SUCCESS` on a merely-stale mapping,
which is untidy rather than broken. A test pins the documented set, so closing a gap
fails loudly — that is the signal the parity run needs repeating.

**`--dir` is built** on `scripts/xras/seed_dev_actions.py`, and prints a loud warning
when pointed anywhere but the committed fixtures: an arbitrary directory is unscrubbed,
and the difference decides whether anything derived from the run may be committed.

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

### Replay is decoupled from the flag — a reversal, with the premise that changed

~~Replay honours the capture flag~~ — **superseded. Replay now *never* dispatches.**

Sprint B tied the two together and argued: *"The kill switch stays the single safety
interlock. A second, replay-specific override would mean two things to reason about and
one of them would eventually be wrong."* That was right while nothing dispatched at all.
With handlers live the conclusion inverts: coupling them means **the flag that turns on
production ingestion is the same flag that arms the replay button**. At cutover
`XRAS_ACTIONS_CAPTURE_ONLY` flips off and a replay silently becomes a live re-apply.

And a replay of a **successful** action is a double-apply on four of the six handlers:

| Handler | Replaying a success |
|---|---|
| Supplement, Adjustment | **additive** — a replayed 250,000-hour supplement becomes 500,000 |
| New | does **not** re-create the project; it now exists, so `(New, exists)` → **Update** → supplements the allocation it just created |
| Update | applies the whole per-resource decision again |
| Extension | near-idempotent, and only because of the equal-end-date skip |
| Transfer | parks, as always |

**XRAS owns the retry.** A failed post is parked on their side and re-sent from there
once the data is fixed, so a replay that applied would race a resend with no idempotency
key between them — `actionId` is in every payload but is not a column, only bytes inside
`raw_payload`.

What remains is the half that was always the valuable one: replay re-parses and
re-validates stored bytes against the **current** schema code, writes nothing, and
records `replayed`. That is a permanent regression check of today's code against the
harvested corpus.

Two tests guard it: one asserts `replayed` with capture **off** and the real handler
registry live, one asserts both flag settings give the same outcome. If a production
remediation path is ever wanted it needs the idempotency key and an agreement with XRAS
about who owns resend — not a flag flip.

---

## Revisit, now that handlers exist — DECIDED: still declined, for a better reason

The one schema column Sprint B declined **with the escape hatch named**: a link from
`xras_action_log` to what a handler changed. It was declined because nothing wrote
anything yet. Handlers now write, so the decision is due — DoD item 6, and it must land
before the DBA ticket.

**Decision: decline again.** The reason has changed and is now structural rather than
provisional: **the relationship is one-to-many.** A single Extension writes an average of
**3.3** `allocation_transaction` rows (§ 1.2); New writes 2.7; an Update can write three
for a single resource. A nullable FK column on `xras_action_log` cannot express that, so
the column as scoped was the *wrong shape*, not merely premature. Representing it
properly needs either a join table — a new table, a larger ticket — or a column on
`allocation_transaction`, which is a hot table that **legacy Java also writes to**, and
that is a materially bigger blast radius than this sprint should take days before
cutover.

**What replaces it** is the same correlation that built § 1.2, and it is precise enough
because of a convention this sprint deliberately preserved:

```sql
-- everything one action wrote
SELECT t.* FROM allocation_transaction t
  JOIN allocation a  USING (allocation_id)
  JOIN account    ac USING (account_id)
  JOIN project    p  USING (project_id)
 WHERE p.projcode = :projcode_result          -- from xras_action_log
   AND t.user_id IS NULL                      -- the integration-actor convention
   AND t.creation_time BETWEEN :processed_time - INTERVAL 60 SECOND
                           AND :processed_time + INTERVAL 60 SECOND;
```

`user_id IS NULL` is what makes this work: only integrations write it (25,048 rows), and
`processed_time` bounds the window to seconds. Two XRAS actions against the *same*
project inside the same minute would be ambiguous — no production pair has ever been
observed, and triage week is when we would find out.

⚠️ **The escape hatch, stated so it stays available:** if the correlation proves
insufficient during triage week, the right shape is a **join table**
(`xras_action_transaction`), not the single column Sprint B declined. Reopening it costs
a DBA ticket either way; choosing the wrong shape to save one would cost two.

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

## Definition of done — as shipped

| # | Item | Status |
|---|---|---|
| 1 | Dispatcher exists; each handler writes `status='processed'` with `projcode_result` | ✅ `sam/xras/dispatch.py`; `_finish(status='processed', …)` finally has callers |
| 2 | Every handler has a replay-and-diff test; the allocation-replay invariant asserted | ✅ per handler, plus the cross-handler sweep in `test_xras_oracle.py` |
| 3 | `Optional[int]` on `log_allocation_transaction`, documented | ✅ commit 1 |
| 4 | Per-type enablement + `helm/values.yaml`; global kill switch retained | ✅ `XRAS_ACTIONS_ENABLED`; both flags now in helm, neither was before |
| 5 | § 3.4's error strings verified against the Java source | ✅ 34 builders, each citing its emitter; § 3.4 was wrong in seven places and is marked superseded |
| 6 | The `xras_action_log` → `allocation_transaction` column decided in writing **before** the DBA ticket | ✅ declined — see § *Revisit, now that handlers exist* |
| 7 | A `## Deviations` section recording where the repo's patterns won over this plan | ✅ below |

---

## Deviations

This document is input, not contract. Where the repo, the Java, or the data disagreed
with it, the repo/Java/data won and the disagreement is recorded here.

### Where the plan was wrong

| Plan said | Reality | Where |
|---|---|---|
| Prefer `fos_aoi` over legacy's id decode for FOS→AOI | **Wrong, and silently so.** The id spaces are disjoint: `fos_aoi.fos_id` holds 5-digit AMIE/XSEDE codes, XRAS sends `1`–`40` = the `area_of_interest` **primary key** space. Routing through the mapping table would file every XRAS project under the wrong research area with no error | commit 2; `XRAS_REIMPLEMENTATION.md` § *Data* corrected |
| `allocationType` is inert on the POST path | **Wrong.** It is the first input to the eleven-strategy chain, read three different ways. A Sprint A test docstring said otherwise and is corrected | commit 2 |
| The `UNDO AUTO/DEFAULT` compensation must be ported ("33 rows in two years") | **Dead code.** Writers use `.name()`, the detector compares `.getValue()`; production holds **zero** UNDO rows of either spelling. The 33 rows are what the *writer* produces | legacy defect 5 |
| ~15 hand-written synthetic fixtures for the high-value branches | Replaced by a **checked coverage matrix**: all 34 builders exercised or declared unreachable with a reason, asserted against the module. A fixture pile decays silently; a declaration cannot | commit 11b |
| Replay honours `XRAS_ACTIONS_CAPTURE_ONLY` (Sprint B) | **Reversed.** Coupling them means the flag enabling production ingestion also arms the replay button. Replay now never dispatches | commit 11c |

### Where legacy was wrong, and we diverged

| Legacy | Here | Why |
|---|---|---|
| Blank `awardedAmount` unboxes a null `Float` → NPE inside assembly, destroying every accumulated diagnostic | Guard; keep `Awarded amount missing` in the 422 | The diagnostic is the entire point of the 422 |
| Adjust drops negatives (`> 0` guard) and never fires anyway (defect 4) | Negatives honoured; both spellings dispatch | The purpose of the action type |
| Nothing stops an adjustment taking an allocation below zero | Rejected, with one added string | Makes `remaining = allocated − used` nonsense; can only reject, never corrupt |
| `getUsernameByRoleType` takes the first on duplicate PI (defect 1) | Date-window filter, reject if >1 survives | Array order deciding who leads a project is a coin flip |
| Roster and role-assignment disagree on begin dates (defect 3) | Both ported; **warn** on disagreement | Silently fixing it removes the only evidence it occurs |
| Update silently re-activates an inactive project | Not ported; warn | Inactive means a human has not approved it |
| Update never updates lead/admin (always-true guard + missing braces) | Fixed | Plainly a bug |
| `disinherit()` severs the parent link with no audit row (zero DETACH rows in production) | `detach_allocation`, which writes one | SAM's audit trail is the product |
| Contract suffix collision → `NonUniqueResultException` → 500, no diagnostic | Exact → unique suffix → report, naming the candidates | Three cores collide in production today |
| `UserLabStrategy` returns null silently when a lab has no soft link | Report the internal-organization string | A projcode cannot be minted without a code |
| An end date at/before commissioning raises `IllegalStateException` → 500 | Report it | Same refusal, one an operator can act on |

### Where SAM's conventions won over faithfulness

- **`!creationTime.after(now)` is not ported.** It compares two clocks that are not the
  same clock — `creation_time` resolves in MySQL's timezone (UTC in dev/CI), `now` is
  naive-Mountain. Measured: **six hours** apart. Honouring it would make an Extension
  posted within six hours of a New silently skip the account it should extend.
- **`User.is_active` is `active AND NOT locked`**; Java's is `active` alone. House rule
  § 5. Unobservable — production has zero locked users of 28,371.
- **`Account.is_active` is deliberately *not* used** in the Extension handler: SAM's
  hybrid there means "not deleted", legacy means active-project-and-commissioned-resource.
  The one place the house rule gives the wrong answer, composed explicitly instead.
- **The roster deduplicates**; legacy does not.
- **Absent is treated as JSON null throughout.** Jackson defaults every wire string to
  `""` and behaves differently on absent vs null; marshmallow gives `None` for both. One
  rule, stated once, rather than an accident per field.

### Structural choices not in the plan

- **`log_integration_transaction`** — after Extension it was clear three handlers would
  each need to un-snapshot the same columns. One helper, one insert site, one place the
  `LEGACY_TYPE_MAP` translation lives. `extend_account_allocation` was refactored onto it
  in the same commit rather than leaving two implementations to drift.
- **Transfer is a *registered* handler returning `manual`**, not an absent one. An
  unregistered service reports `no handler is registered`, which reads like a bug; this
  records a decision on the audit row.
- **Commit 11 split into 11a/11b/11c** (oracle / coverage matrix / replay decoupling) —
  three different concerns that the plan bundled.
- **The per-handler enablement dance was unnecessary.** The plan expected each handler to
  be flipped out of capture mode as it landed; an empty handler registry gave the same
  isolation with no config involved.

### Testing hazards discovered, and now guarded

1. **A registered handler commits.** `management_transaction` runs on the route's own
   connection, *outside* the suite's per-test SAVEPOINT. The first run after registering
   the Extension handler leaked three EXTENSION rows and three mutated `end_date`s into
   the shared test database — found, repaired, verified. Guards: unit tests patch
   `management_transaction` (in **all five** handler modules, since each imports it by
   name), and API tests not about a handler take `no_handlers`.
2. **A fixed `mnemonic_code.description` deadlocks under xdist.** It carries a unique
   index, so twelve workers inserting the same value contend on duplicate-key gap locks.
   The org name and the mnemonic description must be *equal* to soft-link and *unique* to
   insert — any future fixture seeding one needs both.
3. **`allocate_next_gid` holds a global lock across a project creation.** It takes
   `with_for_update()` on the lowest block, and `Project.create`'s `_ns_place_in_tree`
   then issues a table-wide sibling shift. A non-issue in production — one process, one
   action at a time — but it deadlocks a parallel suite, so the New tests stub the pool.

### Unreachable by construction

`no_resource_for_name` is the one legacy string this port structurally cannot emit. It is
the **roster** path's resource lookup: legacy fans the roster out per `resources[]` entry
and resolves each by name, while SAM's `add_user_to_project` is project-scoped and adds a
member to every account at once. The allocation path's key variant fires instead. Declared
and tested as unreachable rather than left as a silent gap.
