# XRAS stress — and the schema questions it has to answer before the DBA ticket

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

⚠️ XRAS owns the retry (established in the Sprint C retrospective), so this is not about
adding a production replay path. It is about knowing the cost of an accidental double-post
and whether the audit row lets you *detect* one.

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
| 13 `xras_resource_repository_key_resource` rows, 11 active resources unmapped | `no_resource_for_key`; run `sam-admin xras --validate-mapping` |

Shapes to push: many resources on one action; deep allocation trees; several error classes
at once; boundary dates; a payload sized toward the `TEXT` ceiling.

⚠️ **PII guardrail, non-negotiable.** Generator output is real people, real awards, real
organizations. It goes to a **gitignored** directory and never enters `tests/` or a commit.
A scenario is promoted into `tests/fixtures/` only once its referents are *invented* rather
than sampled. Committed tests use the 8 scrubbed fixtures and the obfuscated 3307 database.

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
