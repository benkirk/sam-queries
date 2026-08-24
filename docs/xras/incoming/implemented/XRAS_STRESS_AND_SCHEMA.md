# XRAS stress — and the schema questions it has to answer before the DBA ticket

> ✅ **Built, and the columns have landed.** `tests/stress/`, 17 scenarios behind
> `-m stress`, on `xras_reimplementation` (PR #424). The verdicts are in
> [`## Verdicts`](#verdicts); **`action_id`, `service` and `outcome_reason` are now
> in `zz-90`, the ORM, the route and the query layer.** The DBA ticket is a
> transcription of that DDL — nothing about it is still open.
>
> Headline: the stress work found a **live correctness bug** before it found any
> schema gap. `resolve_resource` read `resources[].key`, a field XRAS has never sent —
> see [`## The bug the framing found`](#the-bug-the-framing-found).

**Handoff doc.** Written for a cold start. Every claim carries a `file:line` or a
measurement; re-verify rather than trust.

**Companion:** [`XRAS_HANDLER_REFACTOR.md`](XRAS_HANDLER_REFACTOR.md) — ✅ **shipped**, so
this work inherits **one** `management_transaction` patch point instead of five, held
there by `tests/unit/test_xras_transaction_seam.py`. Two of the gaps below moved while it
landed; they are marked inline.

**Prior work:** Sprint C ([`XRAS_SPRINT_C.md`](XRAS_SPRINT_C.md)) — all six handlers, the
oracle, and a coverage matrix over all 34 error strings.

---

## The organising principle

Not "stress the handlers". **Stress the handlers and assert on the audit row.**

Every scenario asserts that the `xras_action_log` row *alone* is enough for an operator to
triage from. Where it is not, that is a missing column — and the whole point of doing this
now is that **the DBA ticket is unfiled**. Once filed, a column costs another round of
external lead time. This is the last cheap window.

Sprint C's tests assert on handler return values and on `allocation_transaction` rows.
Almost nothing asserts on the audit row, which is the only artefact that survives into
triage week.

---

## Three gaps already visible, before running anything

| Gap | Evidence |
|---|---|
| **No `action_id` column** | The wire carries `actionId` and `requestId` (`src/sam/schemas/forms/xras.py:346,351`) and neither is a column. They survive only as bytes inside `raw_payload`; the sole consumer anywhere is a log interpolation at `sam/xras/roster.py:299`. `webapp/api/xras/replay.py:44-53` names this absence as the reason replay can never dispatch: *"a replay that applied would race a resend with no idempotency key between them"* |
| **No `service` / `reason` column** | `DispatchResult` carries both and the audit row still holds neither. ⚠️ **Partly closed by C.1a**: `_finish` now records `projcode_result` on the manual arm too, so a parked Transfer names its project. `service` and `reason` remain log-only, and k8s app logs are ephemeral (see the durable-audit memory), so the distinction is still lost within days |
| **`error_messages` is unguarded** | `TEXT` (64 KB), written as an unbounded `'\n'.join(...)` at `actions.py:135` and `:157`. Under strict mode an oversized value **fails the audit write** — precisely the failure `_fit()` exists to prevent for `action_type` / `request_number` (`actions.py:86-97,133-134`). The audit write is the one thing this table cannot afford to lose |

### The four parking causes are byte-identical today

| cause | `dispatch.py` | `service` value | distinguishable in the row? |
|---|---|---|---|
| nothing matched | `:234-236` | `None` | **no** |
| type disabled by `XRAS_ACTIONS_ENABLED` | `:238-244` | e.g. `'extend'` | **no** |
| no handler registered | `:246-249` | e.g. `'adjust'` | **no** |
| Transfer deliberately unbuilt | `handlers/transfer.py:74-77` | `'transfer'` + reason | **no** |

All four produce `status='manual'`, `http_status=200`, `error_messages` NULL,
`projcode_result` NULL, `processed_time` set. `dispatch.py:109-111` explains why the
distinction is worth having: *"knowing that an Extension parked because Extension was
disabled, rather than because nothing matched, is the difference between a two-minute
triage and a long one."* The only in-table discriminator is `(status='manual',
action_type)`, which works for Transfer alone because Transfer has a dedicated action
type.

### Smaller findings, same audit

- ~~`projcode_result` has **no `_fit()` guard**~~ ✅ **closed by C.1a** — the guard moved
  into `_finish` itself, so both terminal arms are covered.
- `processed_by`'s `[:35]` slice lives in `replay.py:125,133,145,153`, **not** in
  `_record` itself.
- `http_status` is `Integer` in the ORM (`sam/integration/xras.py:135`) and
  `SMALLINT UNSIGNED` in the DDL — a type mismatch, cosmetic but worth closing.
- `raw_payload` is `TEXT` (65,535 bytes). Largest observed corpus payload is **4,819
  bytes** — ~13× headroom. Confirm rather than assume under the combinatorial scenarios.
- Six query filters exist with no production caller: `remote_actor`, `processed_by`,
  `http_status`, `projcode`, `has_errors`, `replays_only`
  (`sam/queries/xras_actions.py:155-220`). Dead surface, not a gap — but if stress shows
  triage needs one, it is already there.

---

## The four scopes

### 1. Audit-row triage assertions

The half that produces the schema answer. For every terminal state — `received`,
`processed`, `manual`, `failed`, `replayed` — assert the row identifies **what** happened
and **why**, with no access to the app log.

Write these as questions an operator asks at 3am, and let the failures name the columns:

- "Why did this park?" → currently unanswerable. → `service` / `outcome_reason`
- "Is this the same action I saw an hour ago?" → currently unanswerable. → `action_id`
- "What did it change?" → answerable via the § 1.2 correlation query recorded in
  `XRAS_SPRINT_C.md` § *Revisit, now that handlers exist*. **Confirm that query works
  under stress**, because declining the link column rests on it.
- "Was anything odd but non-fatal?" → ⚠️ **half-closed by C.1a.** `DispatchResult.warnings`
  used to be discarded entirely; `_dispatch` now logs it against `log_id`. Still not in the
  row, so still unanswerable from the table alone. → `warnings`

### 2. Repeat-post / idempotency

Post the same action 2–3×. This settles whether `action_id` must be a column, and measures
the real blast radius.

The expected shape, from `replay.py`'s docstring — **verify it rather than trust it**:

| handler | replaying a success |
|---|---|
| Supplement, Adjustment | additive — 250,000 becomes 500,000 |
| New | does *not* re-create; the project now exists, so `(New, exists)` → **Update** → supplements what it just created |
| Update | applies the whole per-resource decision again |
| Extension | near-idempotent, via the equal-end-date skip only |

⚠️ XRAS owns the retry (established in the Sprint C retrospective, and **confirmed by
ACCESS 2026-08-11** — *"POSTs are not automatically retried. They are triggered by a
human"*, Steven Peckins, XRAS/UIUC), so this is not about adding a production replay
path. It is about knowing the cost of an accidental double-post and whether the audit
row lets you *detect* one.

That detection question got sharper with the 41-payload corpus: `actionId` 388865
arrives **twice with different bodies**, once failing and once applying. So a repeated
`action_id` does not imply a duplicate post, and triage must not assume it does.

### 3. Combinatorial payloads

`scripts/xras/synthesize_payload.py` — the DB-aware generator declared as a Sprint C
follow-on. Reads the **dev clone** (port 3306, real prod sample after `make clone`),
substitutes real referents into a real scrubbed fixture so the wire shape stays authentic.

Referents the clone is known to hold (verified during Sprint C planning):

| referent | why it matters |
|---|---|
| three colliding contract cores — `1049089`/`PLR-1049089`, `OPP-1744587`/`PLR-1744587`, `2146709`/`AGS-2146709` | the `ambiguous_contract` divergence; legacy 500s here |
| organization 158, "UCAR Community Programs" | matches two mnemonics — legacy defect 2 |
| 341 `mnemonic_code` rows vs ~171 active orgs | the broken `varchar(3)` fuzzy match — the 24% failure class |
| 13 `xras_resource_repository_key_resource` rows, 11 active resources unmapped | `no_resource_for_key`. ⚠️ Later established as **expected** — those resources are not offered through XRAS |

Shapes to push: many resources on one action; deep allocation trees; several error classes
at once; boundary dates; a payload sized toward the `TEXT` ceiling.

⚠️ **PII guardrail, non-negotiable.** Generator output is real people, real awards, real
organizations. It goes to a **gitignored** directory and never enters `tests/` or a commit.
A scenario is promoted into `tests/fixtures/` only once its referents are *invented* rather
than sampled. Committed tests use the scrubbed fixtures (**41** as of 2026-08-11) and the
obfuscated 3307 database. `.gitignore` now carries `xras_payloads_raw/` and `*_payloads_raw/`,
so the staging convention is enforced rather than merely written down.

### 4. Unsampled wire shapes

The four things the corpus has never shown us, which otherwise arrive for the first time in
production:

- **`Co-PI` vs `CoPi`** — the spelling is still unknown. Surviving both is real risk
  reduction. Note the roster ignores `roleType` entirely
  (`XRAS_SPRINT_C.md` § *The roster*), so the risk is on role *assignment*, not membership.
- **`Renewal`** — shares the Update path, never sampled.
- **`Advance`** — a declared wire type with no legacy service; currently falls through to
  `manual`. Confirm that is what happens and that the row says so.
- **`Transfer`** — parks by design; confirm the reason survives to wherever it lands.

Both close permanently the same two ways: a bulk forward from `hdt@ucar.edu`
(`XRAS_SPRINT_A.md` § 3b has the ask — include the manual-fallback subject, which is how
the Adjustment payload nearly went unnoticed), or production capture after cutover.

> **Update 2026-08-11 — the bulk forward arrived; the list did not shrink to zero, and it
> gained a fifth entry.** Corpus 8 → 41.
>
> - **`Co-PI`** — still unsampled, and now *measured*: 41 payloads, ~35 projects,
>   `roleType` vocabulary exactly `PI` (45) / `Allocation Manager` (43) / `User` (13).
>   This site does not send one, which agrees with the GET side
>   (`webapp/api/xras/requests.py:244` documents `co_pi` as valid and always empty).
>   The synthetic scenario stays and is the right coverage.
> - **`Renewal`** — still unsampled as an `actionType`. ⚠️ Three payloads carry
>   `requestType: 'Renewal'`, which looks like the sample and is not: `requestType`
>   dispatches nothing, and those three route to extend / supplement / park.
> - **`Advance`**, **`Transfer`** — unchanged, still zero.
> - ⚠️ **New: `Date Adjustment`**, ×4. A wire `actionType` in no document and no Java
>   enum, which parks with no serviceable — exactly as legacy does. It is now the most
>   common thing on the manual-fallback path. `park_unknown_action_type` covers it.
>
> Only production capture can close what is left.

---

## Harness shape

- **`tests/stress/`**, gated behind a new `-m stress` marker, mirroring the existing perf
  tier — `pytest.ini:37` (`-m "not perf"`) and `:39-44` (markers). Add `stress` to both.
- **`scenarios.json`** manifest mirroring `tests/perf/baselines.json`, consumed by a
  parametrized fixture the way `tests/perf/conftest.py:17-25` does. Each scenario declares
  its expected outcome — `processed` / `manual` / 422-with-exact-strings — **and what the
  audit row must say**.
- ✅ **One patch point.** `management_transaction` is imported only by
  `sam/xras/handlers/base.py`, and `tests/unit/test_xras_transaction_seam.py` enforces
  that with a runtime globals scan plus a `session.commit` spy. Patch `base`, not the
  handler modules — and note the spy pattern, which catches a commit reached by *any*
  route and is the right shape for a harness that writes to the shared database.
- ⚠️ **Scenarios run through the HTTP route, not `dispatch_action`.** The audit row is the
  thing under test, and it is written by `_record` / `_finish` on their own connection,
  outside the handler transaction (`actions.py:99-160`). That means the per-test SAVEPOINT
  does **not** roll these rows back — they need explicit cleanup.
  `tests/api/test_xras_access.py`'s `action_log` fixture is the precedent, and read its
  docstring: it deletes by captured primary key rather than an `id > watermark` range,
  because the range predicate takes an open-ended gap lock and deadlocks reliably under
  `-n auto`.
- Handlers commit. See `XRAS_SPRINT_C.md` § *Extension* → *Testing hazard* for the leak
  that already happened, and run the leak check after every stress run:

  ```sql
  SELECT COUNT(*) FROM allocation_transaction WHERE DATE(creation_time) = CURDATE();
  ```

---

## Candidate columns

Each to be confirmed or dropped by the evidence, then written up.

| candidate | case for | case against |
|---|---|---|
| `action_id INT UNSIGNED NULL` + index | The idempotency key. Precondition for any future production remediation path; makes duplicate posts detectable | XRAS owns the retry, so nothing needs it *today* |
| `service VARCHAR(16) NULL` | Separates four parking causes that are byte-identical | `(status, action_type)` half-covers it |
| `outcome_reason VARCHAR(255) NULL` | The human-readable *why*, currently only in an ephemeral log | Could be folded into `error_messages` — but that column means "the 422 body", and overloading it would corrupt a wire contract |
| `warnings TEXT NULL` | Where defect-3 disagreements land instead of being dropped | Could go to the app log only, if triage week shows nobody looks |

**Code-side fixes regardless of the ticket**, all in `webapp/api/xras/actions.py`:

1. Bound the `error_messages` join (`:135`, `:157`) — an oversized value currently **fails
   the audit write**. ⚠️ Still the most consequential item here.
2. ~~`_fit()` on `projcode_result`~~ ✅ done in C.1a, inside `_finish`.
3. `http_status` → `SmallInteger` in the ORM to match the DDL.
4. Move `processed_by`'s width slice into `_record` rather than four call sites in
   `replay.py` — the same shape as item 2, so follow that fix.

---

## Output

**A written verdict per candidate column, with its evidence**, added to this file. Then the
ticket is filed carrying whatever survives, into **both** init scripts —
`containers/sam-sql-dev/initdb.d/zz-90-*.sql` and `zz-91-*.sql`, one ticket, as Sprint B
established. Staging needs both run by hand: `infrastructure/scripts/init-rds.sh` restores
the raw `.xz` with no initdb hook.

⚠️ Before the next snapshot regeneration, confirm the `purge_xras_action_log` rule in
`containers/sam-sql-dev/anonymize_sam_db.py` still covers any column added — `raw_payload`
is a verbatim POST body full of PII and the obfuscated dump is a committed public LFS blob.

---

## Definition of done

1. `pytest -q -m stress` runs every scenario in `scenarios.json`.
2. **Every scenario asserts on the `xras_action_log` row**, not only on the return value.
3. Scenarios that *cannot* be triaged from the row are listed as schema evidence rather
   than skipped or weakened.
4. The § 1.2 correlation query is confirmed to work under load — the decision to decline
   the action→transaction link column rests on it.
5. `sam-admin xras --validate-mapping` is clean, or its gaps are a filed decision.
6. A written verdict per candidate column in this file.
7. The leak check returns 0 after a full stress run.
8. A `## Deviations` section — this document is input, not contract.

---

## The bug the framing found

Before any schema gap, the stress work found a live correctness bug — and it found it
for exactly the reason this document argued for driving scenarios through the **route**.

`resolve_resource` read `resources[].key`. **No XRAS payload has ever carried that
field.** All six resource-bearing corpus fixtures send `resourceRepositoryKey`, the
schema declares it under that name, and unknown keys are dropped on load. So through
the real pipeline the key was always `None`, and every resource on every Supplement,
Adjustment, New and Update reported

```
No resource found in SAM corresponding to key
```

with nothing after it. Roughly **36% of production traffic** — Supplement is currently
100% successful in legacy — failing on day one of an abrupt cutover, with an error
message that does not say what is wrong.

**Why a whole sprint of tests missed it.** Every test built its own `resources[]`
entries as `{'key': ...}` — five handler modules, the error-coverage matrix, the seam
test, and most of all the oracle's `_retarget`, whose docstring reads *"Shape
untouched"* while replacing the one field that mattered. The corpus was loaded through
the real schema and then had its resources overwritten with the invented shape. Every
layer that could have caught it substituted the wrong shape first.

**The fix is the check, not the field name.**
`tests/unit/test_xras_wire_vocabulary.py` asserts that every wire field name the
handlers read is a field some XRAS schema declares — an AST walk over `get_field(...)`
and `self.get(...)` literals against the union of all seven schemas. It named the bug on
first run: `{'key': ['_fields.py:159']}`. Same shape as the error-string coverage
matrix: declare the vocabulary, then prove code and declaration agree.

---

## Verdicts

Each candidate confirmed or dropped against evidence produced by the tier, not against
the argument that proposed it.

### ✅ `action_id INT UNSIGNED NULL` — **built**, with an index

`test_repeat_post_supplement` posts the same action three times and gets three rows that
are **identical in every column an operator can filter on**. `actionId` was on the wire
of all three and survives only as bytes inside `raw_payload`, so telling a duplicate
from a legitimate second award means parsing JSON out of a `TEXT` column.

The cost side is measured rather than asserted, and it is asymmetric:

| handler | share of traffic | a double post costs |
|---|---|---|
| Extension | 60% | **nothing** — the equal-end-date skip writes no row |
| Supplement | 15% | **a full increment** — 250,000 posted three times leaves 750,000 added |

XRAS owns the retry, so nothing *needs* this today. That is an argument about
prevention, and this column is about **detection** — which is the thing triage week
will actually want, and the thing no code change can add afterwards without a second
ticket. It is also the precondition for any future remediation path: `replay.py`
already names its absence as the reason replay can never dispatch.

### ✅ `service VARCHAR(16) NULL` — **built**

`test_a_disabled_park_and_an_unmatched_park_are_byte_identical` asserts the equality
directly, over the six columns the dashboard filters on. Four causes park an action —
nothing matched, the type is disabled by the triage lever, no handler is registered,
Transfer by design — and only Transfer is distinguishable, and only because it owns a
dedicated `action_type`.

The lever case is the sharp one: an operator who narrows `XRAS_ACTIONS_ENABLED` at 3am
and then cannot confirm from the table that it took effect is flying blind during the
incident the lever exists for.

`DispatchResult` has carried `service` since Sprint C. Nothing but the column is missing.

### ✅ `outcome_reason VARCHAR(255) NULL` — **built**

`NOT_IMPLEMENTED_REASON` in `handlers/transfer.py` is 200 characters written
specifically *"for whoever reads it at 3am with no context: what happened, that it was
intended, and what to do."* It reaches the app log and stops. k8s app logs are
ephemeral — see the durable-audit decision — so within days the row is all that is left.

**Not** folded into `error_messages`, which means "the 422 body XRAS received" and is a
wire contract. Overloading it would corrupt the one column an XRAS administrator reads
directly.

### ❌ `warnings TEXT NULL` — **decline**, revisit after triage week

Three pieces of evidence, and they point the same way:

1. **It is already durable enough.** `roster.py` logs each defect-3 disagreement as it
   is found, and C.1a added a second log line in `_dispatch` carrying them against
   `log_id` — the handle an operator actually has.
2. **The type is wrong to fossilise.** `DispatchResult.warnings` carries bare
   **usernames**, not sentences (`roster.py` returns `tuple(sorted(assigned - members))`).
   A column would freeze "tuple of something" into the schema before anyone has decided
   whether the roster renders the sentence or the consumer does.
3. **Zero observed instances.** No corpus payload triggers it and no production
   evidence exists, because the condition is legacy's defect 3 and legacy leaves no
   record of it.

If triage week shows operators reaching for it, `outcome_reason` above is a reasonable
home for a rendered summary and costs nothing extra at that point.

**REVERSED 2026-08-24** (`docs/plans/XRAS_DATA_MODEL_UPLIFT.md` Track A): two of the
three legs fell. Warnings are now rendered sentences, not bare usernames — the
incoming-hardening series added the unlinkable-grant and unflagged-primary-fos
warnings — and the observed-instances count went from zero to 11 requests in the live
readiness cohort that will emit the grant warning on their real POST at cutover.
`warnings TEXT CHARACTER SET utf8mb4 NULL` shipped in the same DDL trip as
`request_id` (utf8mb4 because grant titles are user free text); `_finish` writes it
through the same message-boundary bounding as `error_messages` on the processed,
manual, and rechecked arms. `error_messages` stays the untouched 422 wire contract.

### ➖ `raw_payload` / `error_messages` — **no schema change**, fixed in code

Both were unbounded into `TEXT` (65,535 bytes). Under `STRICT_TRANS_TABLES` — confirmed
on — an oversized value does not truncate, it raises `1406 Data too long`. **Reproduced
live before the guard existed**: the INSERT failed and the audit row was lost entirely,
which is the one failure this table cannot afford.

Which path can reach an oversized error list took the corrected wire field name to see:

| path | ratio | reachable? |
|---|---|---|
| Supplement | **1.00×** | no — a failed key resolution `continue`s before the amount is read, so one message per resource against an entry of near-identical length; the body hits its limit first |
| New | **1.79×** | **yes** — `_plan_allocations` calls `resolve_resource` *and* `transaction_amount` unconditionally, so one resource yields two messages. 59,090 bytes of body → 105,999 of messages |

Widening the columns to `MEDIUMTEXT` was considered and rejected: it moves the cliff
rather than removing it, and the guard is needed at *any* width. Both measurements are
pinned in `scenarios.json` and the contrast is its own test.

### ➖ `http_status` — **no ticket**, ORM corrected

`Integer` in the ORM against `SMALLINT UNSIGNED` in the DDL. Harmless in MySQL, but the
kind of drift that makes a width guard computed from the ORM quietly wrong. Now
`SmallInteger`, pinned by a test that compares every declared width against the live DDL.

---

## The correlation query is confirmed — and the Sprint C note about it was wrong

DoD item 4. Sprint C declined the `xras_action_log` → `allocation_transaction` link
column on the grounds that the relationship is one-to-many, and replaced it with a
correlation keyed on `user_id IS NULL` + `processed_time ± 60s`. That note said *"no
production pair has ever been observed"*.

Measured against the snapshot:

| | |
|---|---|
| integration-written rows (`user_id IS NULL`) | **24,825** |
| distinct `(projcode, minute)` buckets | **10,347** |
| ambiguous buckets — same project, same minute, >1 distinct comment | **12** |

So ambiguous pairs **do** exist. But all twelve are from **2015–2016**, every one a
blank-comment row beside a hand-typed one (`ev134500`, `WRAP 06-2016`, `CHAP`) — manual
writes from the pre-XRAS era, not two XRAS actions colliding.

Scoped to XRAS's own rows:

| | |
|---|---|
| XRAS-written rows | **1,416**, 2025-10-23 → 2026-07-17 |
| `(projcode, minute)` buckets | **451** |
| ambiguous | **0** |

**The query holds**, and the twelve legacy buckets are unreachable through it by
construction: `processed_time` comes from `xras_action_log`, whose earliest row is
2025-10. The decision to decline the link column stands, and its escape hatch — a
`xras_action_transaction` join table rather than a single column — stays available.

---

## `sam-admin xras --validate-mapping` — clean, once "clean" is defined correctly

DoD item 5, and the answer is **not a gap** — which took asking rather than measuring.

Run against the test snapshot: 13 mapping rows, 11 active resources with no mapping
(`Gust`, `Gust GPU`, `GLADE user`, `GLADE work`, `Boreas`, `Destor`,
`HPC_Futures_Lab`, `Laramie`, `Quasar`, `hpc`, `hpc-dev`), 6 mappings pointing at
decommissioned kit.

⚠️ **The 11 are expected. Not every internal resource is offered for allocation
through XRAS**, so most of them have no mapping *by design* — this document previously
recorded them as an open data gap and a pre-cutover gate, and that was wrong. Corrected
on Ben's confirmation, 2026-08-08.

So what is `--validate-mapping` for? The **opposite** case: a resource that *should* be
allocatable through XRAS showing up in that list. That is the data fix behind
`No resource found in SAM corresponding to key %s`, and the report is how you find it —
it is a diagnostic, not a checklist item to clear to zero.

The byte-ordering note still holds *if* a mapping is ever added: `resourceRepositoryKey`
is omitted when unmapped, so adding one changes GET response bytes and must precede a
parity run. It is no longer a gate, because nothing is queued to be added.

---

## Deviations

### Scope 3 — the combinatorial payload generator — not built

`scripts/xras/synthesize_payload.py` was to read the **dev clone** (port 3306, real prod
sample) and substitute real referents into scrubbed fixtures.

Not built, deliberately, and the reason is that its premise was overtaken. Its purpose
was to reach shapes the corpus does not cover; the ones that mattered —
oversize/amplification, repeat posts, the four parks, `Renewal`, `Advance`, `Co-PI` —
were all reachable from **synthetic** payloads with no PII and no clone dependency, and
are now in `tests/stress/`. What the generator would have added over those is the
*ambiguous-contract* and *mnemonic-collision* classes, which are extractor concerns
already covered by `test_xras_extractors.py` and `test_xras_error_coverage.py`.

Against that, the cost is real: output that is PII by construction, a gitignored
directory, a standing rule that nothing derived from 3306 is ever committed, and a
generator whose own correctness nobody checks. Worth building **if** triage week
produces a failure class the synthetic scenarios cannot reproduce — not before.

### The `error_messages` amplification finding moved mid-flight

The original estimate was ~1.35× on the Supplement path, which would have made the
oversize scenario a Supplement. That was computed against `{'key': ...}` — the field
name that turned out not to exist. With the real field, `resourceRepositoryKey` is long
enough that Supplement measures **1.00×** and cannot reach the condition at all; the
reachable path is New, at 1.79×, for a structural reason (both resolvers called
unconditionally). The scenario is a New, and the Supplement contrast is its own test so
the finding cannot silently rot.

### Route scenarios cannot use factories

Discovered by a scenario that quietly became a different scenario: the route reads
Flask-SQLAlchemy's `db.session` on its own connection and sees only **committed** rows,
so a factory-made project is invisible and every dispatch parks as "no service matched".
Route scenarios use committed snapshot projects; the questions that need exact values —
the double-post arithmetic — are asked at `dispatch_action` with factories instead. Both
fixtures say so at the point of use.

### `action_log` was promoted rather than copied

It moved to `tests/xras_audit.py`, imported by both `tests/api/` and `tests/stress/`.
Its two hazards — the gap-lock deadlock and the self-FK delete order — are exactly the
kind that must not drift between two copies.

### One scenario is green *because* the gap exists

`test_a_disabled_park_and_an_unmatched_park_are_byte_identical` asserts an equality that
documents a deficiency. It must go **red** the day `service` lands, at which point it
becomes the test that proves the column works. Flagged in the test itself, because a
green assertion that encodes a problem is easy to mistake for a good result.

---

## Definition of done — as built

| # | | |
|---|---|---|
| 1 | `pytest -m stress` runs every scenario in `scenarios.json` | ✅ 17 scenarios; a test with no manifest entry fails loudly |
| 2 | Every scenario asserts on the `xras_action_log` row | ✅ except the three deliberately asked at handler level, which say why |
| 3 | Untriageable scenarios listed as schema evidence | ✅ via the manifest's `verdict` field |
| 4 | The correlation query confirmed | ✅ and the Sprint C note corrected — 12 ambiguous buckets exist, all pre-XRAS |
| 5 | `--validate-mapping` clean or filed | ✅ **not a gap** — the 11 unmapped resources are not offered through XRAS by design; the report is a diagnostic, not a gate |
| 6 | A written verdict per candidate column | ✅ 3 recommended, 1 declined, 2 closed in code |
| 7 | Leak check returns 0 after a full stress run | ✅ `allocation_transaction`, `xras_action_log` and `project` all 0, under `-n auto` |
| 8 | A `## Deviations` section | ✅ above |

**The ticket carries three columns**, and they are **already written** into
`containers/sam-sql-dev/initdb.d/zz-90-*.sql`, the ORM, `_record`/`_finish` and the
query layer:

```sql
ALTER TABLE xras_action_log
  ADD COLUMN action_id      INT UNSIGNED  NULL AFTER request_number,
  ADD COLUMN service        VARCHAR(16)   NULL AFTER action_id,
  ADD COLUMN outcome_reason VARCHAR(255)  NULL AFTER service,
  ADD KEY xras_action_log_action (action_id);
```

That statement **is** the ticket — it was applied verbatim to both local containers,
and the init script was separately replayed into a scratch database and its
`information_schema` output diffed against the result. Identical, column order and
index included.

⚠️ `zz-90` is `CREATE TABLE IF NOT EXISTS`, so it only reaches **fresh** containers.
A running dev/test container needs the `ALTER` above, or a `down -v` → rebuild → up
cycle. ✅ **Verified end to end**: after `down -v`, rebuild, up and `make clone`, both
`mysql` (3306) and `mysql-test` (3307) carry all three columns and the
`xras_action_log_action` index, created by the init script with no manual step — and
the full suite, the stress tier and the perf tier are green against them.
CI needs neither: init scripts run *after* the snapshot restore, which is how this
table reached dev and CI in the first place — no LFS blob regeneration.

Staging still needs it run by hand: `infrastructure/scripts/init-rds.sh` restores the
raw `.xz` with no initdb hook. `zz-91` (`xras_activation_event`) is unchanged and
still ships in the same ticket, as Sprint B established.

⚠️ Before the next snapshot regeneration, confirm `purge_xras_action_log` in
`containers/sam-sql-dev/anonymize_sam_db.py` covers the three new columns. `action_id`
is not PII; `service` is a closed vocabulary; `outcome_reason` is free text written by
SAM rather than by XRAS and should be safe — check rather than assume.

---

## The dev-clone census — the cheap thing that replaced the generator

Read-only, run against port 3306 after the verdicts were written, to test whether the
referent figures the plan rests on had moved. **They had not** — and every figure was
then re-measured after a full `down -v` → rebuild → `make clone` cycle and came back
**identical**, so these are stable properties of the data rather than a snapshot
artefact. Nothing here is committed beyond these aggregates.

| | plan said | measured | |
|---|---|---|---|
| `mnemonic_code` rows | 341 | **341** | unchanged |
| active organizations | ~171 | **171** | unchanged |
| colliding contract cores | 3 — `1049089`, `1744587`, `2146709` | **3, exactly those** | unchanged |
| `xras_resource_repository_key_resource` rows | 13 | **13** | unchanged |
| active resources with no mapping | 11 | **11** | unchanged |

Two figures the plan never had:

- **153 of 171 active organizations (89%) have no mnemonic soft link**, and 1,104 of
  1,381 institutions (80%). This is the root of the 24% New-action failure class, and
  it is wide open. ⚠️ Do not read 89% as a failure *rate* — most organizations never
  appear as a PI's affiliation on XRAS traffic. It is a coverage figure, and the fix
  is data, not code.
- **A single project can hold 22 accounts** (mean 3.11; 72 projects above 10). That is
  the upper bound on how many allocations one Extension touches — the corpus average
  is 3.3 transactions per Extension. The handler loops linearly so there is no
  combinatorial risk, but it is the widest real fan-out and the stress tier does not
  currently exercise it.

**This is what the generator would have been for**, and it took a handful of `SELECT`s
rather than a script, a gitignored output directory and a standing PII rule. Every
class it would have sampled is either unchanged or already covered by a dedicated
test — `test_ambiguous_contract`, `test_mnemonic_internal_failed`,
`test_mnemonic_external_failed`, `test_no_affiliation_for_pi`, all driving real
dispatch. Re-run the census after a `make clone` if the figures ever matter again.
