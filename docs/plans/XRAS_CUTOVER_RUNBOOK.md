# XRAS cutover runbook

**Operational checklist. Everything here is a command to run or a person to ask —
no code is left.** The design, the measurements and the reasoning live in
[`XRAS_REIMPLEMENTATION.md`](XRAS_REIMPLEMENTATION.md); this is the day-of sequence.

> ## ⚠️ The one fact that shapes everything below
>
> **Cutover is abrupt.** XRAS holds **one** base URL, not seven. When it repoints from
> `sam.ucar.edu` to `sam.hpc.ucar.edu`, the six GET endpoints *and* `POST /actions` move
> together, all six handlers go live at once, and there is **no observation window** —
> nothing about the new stack is exercised by production traffic until all of it is.
>
> **Rollback is not unilateral.** It is another repoint, which means another round-trip
> with ACCESS rather than a flip we control. Budget for that when deciding what "ready"
> means.
>
> Dual-posting (XRAS posting to both stacks, ours in capture mode) would have restored an
> observation window. **It is ruled out. Do not re-propose it.**

---

## Preconditions — everything that must already be true

| # | Precondition | How to prove it |
|---|---|---|
| 1 | All six handlers built and registered | `pytest -q` → 5,280 passed; `pytest -m stress -n 0` → 21 passed |
| 1b | The whole legacy surface is mapped — all eight endpoints, not the seven XRAS calls today | `pytest tests/api/test_xras_roles.py tests/api/test_xras_unmapped.py -q` |
| 2 | ✅ **Done 2026-08-10.** The audit table carries `action_id`, `service`, `outcome_reason` | `SHOW COLUMNS FROM xras_action_log` on the target DB |
| 2b | ✅ **Done 2026-08-10.** ⚠️ The DDL applied is the **current** `zz-90`/`zz-91`/`zz-92` — **exactly 7** columns must come back utf8mb4: `raw_payload`, `error_messages`, `comment`, `notified_to`, `recipient_name`, `subject`, `error` | `SELECT TABLE_NAME, COLUMN_NAME, CHARACTER_SET_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='sam' AND TABLE_NAME IN ('xras_action_log','xras_activation_event','notification_log') AND CHARACTER_SET_NAME='utf8mb4'` |
| 3 | `XRAS_ACTIONS_CAPTURE_ONLY` is `"1"` | `helm/values.yaml:291` — and confirm it in the running pod's env before anything else |
| 4 | The replay-and-diff oracle passes | `pytest tests/unit/test_xras_oracle.py -q` |
| 5 | A notification path exists for `active = 0` projects | Sprint B's pending-activation card on the Allocations dashboard |

⚠️ **Precondition 3 is the interlock and it is the one to physically check**, not assume.
While it is `"1"` the endpoint authenticates, parses and audits every post and dispatches
nothing. Legacy is still the system of record; dispatching before the repoint would
double-apply actions legacy has already applied, against live allocations, with no undo.

**It gates `POST /v1/roles` too**, and that is deliberate: the lead reassignment is the
*other* XRAS write, and without the gate it would be the one that applies the moment the
base URL repoints while every action is still being captured. Under capture-only it
answers 200 (legacy's success shape), records `status='received'`, and changes nothing.

---

## The sequence

Five gates, strictly ordered. Two run on someone else's schedule — **start those in
parallel with gate 1, not after it**.

### 1 · Merge and deploy

```
PR #424 → staging  →  a second PR: staging → main  →  CIRRUS/k8s
```

Nothing else can start: gate 3 needs a deployed host to point at, and the DDL is only
useful once code that reads those columns is running.

- **Done when** `sam.hpc.ucar.edu` serves the six GETs and `POST /actions` answers the
  41-byte 401 unauthenticated.
- ⚠️ ECS-staging is **not** CIRRUS-k8s. Helm changes reach production via `main` → cirrus.

### 2 · The three tables · ✅ **DONE 2026-08-10 — no longer a DBA ticket**

**`hpc-writer` was granted DDL**, so this stopped being an external gate:

```sql
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, REFERENCES, INDEX, ALTER ON `sam`.* TO 'hpc-writer'@'%';
```

Deliberately **no `DROP`**, so the worst case stays additive. `REFERENCES` is the
non-obvious half and the reason a ticket saying "we need CREATE TABLE" would have
failed: MySQL 8.0 requires it on the **parent** of every foreign key, and `zz-90` has a
self-referential FK while `zz-91` points at `project`. `CREATE` alone yields only
`zz-92`. Full evidence in [`DBA_PRIVILEGE_REQUEST.md`](DBA_PRIVILEGE_REQUEST.md).

All **three** files were then applied by hand to `sam-sql.ucar.edu`, in this order —
`zz-91`'s FK references `zz-90`, so the order is load-bearing:

```bash
mysql --defaults-file=<creds> < containers/sam-sql-dev/initdb.d/zz-90-xras_action_log.sql
mysql --defaults-file=<creds> < containers/sam-sql-dev/initdb.d/zz-91-xras_activation_event.sql
mysql --defaults-file=<creds> < containers/sam-sql-dev/initdb.d/zz-92-notification_log.sql
```

**Three files, not two.** `zz-92-notification_log.sql` (Sprint D,
[`NOTIFICATION_FRAMEWORK.md`](NOTIFICATION_FRAMEWORK.md)) belongs to the same round;
this gate listed only the two XRAS tables for a while, which is precisely the
one-table-short mistake the "one ticket" rule exists to prevent.

Verified on production, all five matching:

| Check | Expected | Got |
|---|---|---|
| Tables | 3, InnoDB, `utf8mb3_general_ci`, 0 rows | ✅ |
| `xras_action_log` C.1b columns | `action_id`, `service`, `outcome_reason` | ✅ |
| utf8mb4 columns (precondition 2b) | exactly 7 | ✅ |
| FK constraints | 3 | ✅ |
| Index coverage (`information_schema.STATISTICS`) | 27 rows | ✅ |

⚠️ **Landing this started capturing nothing.** XRAS still posts to legacy's URL; nothing
reaches this endpoint until the repoint. Early tables buy lead time, not payloads.

⚠️ **ECS-staging's RDS does not have them.** `infrastructure/scripts/init-rds.sh`
restores the snapshot — which *does* now contain all three — but it is a one-time
bootstrap after `terraform apply`, so an existing instance never picks them up. Until
that DB is given the DDL, the XRAS tab and Admin → Notifications 500 there. CIRRUS/k8s
is the deployment target; ECS-staging is a check-the-render environment.

### 3 · Parity against the deployed host

```bash
python utils/parity/check_legacy_apis.py --api xras   # against sam.hpc.ucar.edu
```

Uses our own `samuel` credential. This is the **GET-side** cutover verification and it is
independent of gate 4.

- **Done when** the run is byte-clean across all six endpoints.
- ⚠️ Re-run it if anything changes `xras_resource_repository_key_resource`.
  `resourceRepositoryKey` is *omitted* when a resource is unmapped, so **adding a mapping
  row changes GET response bytes** and invalidates a previous clean run.

### 4 · The 400/422 contract · ⏳ external

An email to `allocations@access-ci.org`, not code. **Start it in the same week as gate 2.**

Legacy answers 500 for both a malformed body and a failed validation, and a bare 200 for
an action it silently parked. We answer 400 / 422 / 200 and distinguish all of them.

⚠️ **Broker retry behaviour on 4xx is unknown, and it is the riskiest open unknown on the
cutover path.** If XRAS retries a 422 indefinitely, a single bad payload becomes a loop.

- **Done when** ACCESS confirms what their broker does with a 4xx.

### 5 · The repoint — this is the cutover

Coordinated with ACCESS. Two things happen, and the order matters:

1. **XRAS repoints** its base URL to `sam.hpc.ucar.edu`.
2. **`XRAS_ACTIONS_CAPTURE_ONLY` flips to `"0"`** in `helm/values.yaml:291`, and deploy.

Flipping *before* the repoint is harmless (nothing is arriving). Flipping *after* means
every post in the gap is captured as `received` and must be replayed by hand — recoverable,
but work. Flipping **early on a stack XRAS is already posting to** is the double-apply, and
is the thing this interlock exists to prevent.

---

## Triage week

The watch surface, in the order you will reach for it:

| Surface | What it answers |
|---|---|
| Allocations → XRAS page | Everything, filterable, with the raw payload behind `MANAGE_XRAS` |
| `sam-admin xras --summary` | Status counts at a glance |
| `sam-admin xras --status failed` | The 422s, with their error lists |
| `sam-admin xras --status manual` | What was parked, and now **why** |

**The three columns C.1b added are what make a row triageable**, so use them:

- `outcome_reason` — *"why did this park?"* Four causes used to produce byte-identical
  rows. A NULL `service` means nothing matched at all; a populated one means a service was
  selected and then something stopped it.
- `action_id` — *"have I seen this action before?"* A point lookup. Three posts sharing one
  `action_id` are a duplicate, not three awards.
- `service` — which handler ran, recorded on the failed and manual arms too.

**Health signals** (§ 4.4) — these are *normal*, so do not chase them:

- a **~30% 404 rate** on `/people/{username}` is the baseline, not an incident
- the roster response is **~3.84 MB ±0.2%**
- New actions historically succeed **~30%** of the time; the causes are data
  (unreconciled ARC identities, the mnemonic soft-link gap), not this code

### If something goes wrong

**Park one action type by config, without a revert:**

```yaml
XRAS_ACTIONS_ENABLED: "Extension,Supplement"   # helm/values.yaml:295
```

Narrow it to whatever should keep running. The excluded types take the audited `manual`
path — visible, recorded, applied by a human — rather than being dropped. An unknown token
is logged and **dropped**, which fails safe: a typo leaves that type *disabled*.

**Stop dispatching entirely:** `XRAS_ACTIONS_CAPTURE_ONLY: "1"`. Everything is still
captured and replayable; nothing is applied.

**Full rollback** is a repoint, i.e. another round-trip with ACCESS. Not ours to do alone.

### What one action wrote

There is no FK from `xras_action_log` to `allocation_transaction` — the relationship is
one-to-many (an Extension averages 3.3 rows) so a column was the wrong shape. Correlate
instead:

```sql
SELECT t.* FROM allocation_transaction t
  JOIN allocation a  USING (allocation_id)
  JOIN account    ac USING (account_id)
  JOIN project    p  USING (project_id)
 WHERE p.projcode = :projcode_result          -- from xras_action_log
   AND t.user_id IS NULL                      -- the integration-actor convention
   AND t.creation_time BETWEEN :processed_time - INTERVAL 60 SECOND
                           AND :processed_time + INTERVAL 60 SECOND;
```

Measured: **zero ambiguous buckets** across 451 XRAS `(projcode, minute)` buckets. (Twelve
ambiguous buckets exist in the table overall, all 2015–2016 manual writes, and they are
unreachable through this query because `processed_time` only exists from 2025-10.)

If the correlation proves insufficient, the right shape is a join table
(`xras_action_transaction`) — **not** a single column.

---

## Known-open, and accepted

None of these gate the cutover. They are the things you may see and should not treat as
new.

- **11 active resources have no XRAS mapping.** ✅ **Expected** — not every internal
  resource is offered for allocation through XRAS. `sam-admin xras --validate-mapping` is
  a *diagnostic*, not a gate: it matters only if a resource that **should** be allocatable
  appears in that list. It exits non-zero only on a dangling key.
- **89% of active organizations have no mnemonic soft link** (153 of 171), and 80% of
  institutions. This is the root of New's 24% mnemonic failure class. A data fix, and it
  would move New's success rate more than any code.
- **Transfer parks by design**, with `outcome_reason` saying so. Zero production traffic.
- **Wire shapes never seen in production**: `Co-PI` vs `CoPi` is **closed** — membership
  ignores `roleType` entirely, so the spelling cannot matter, tested across three.
  `Renewal` and `Advance` are exercised synthetically.
- **The audit table's charset is split, on purpose.** `raw_payload`, `error_messages`
  and `xras_activation_event.comment` / `.notified_to` are **utf8mb4**; every
  identifier column is **utf8mb3**. utf8mb3 cannot hold a 4-byte character at all, and
  under `STRICT_TRANS_TABLES` that raises `1366` and **loses the audit row** — so the
  columns carrying human text (project titles, abstracts, operator notes) had to move.
  The identifiers could not follow: `request_number` and `projcode_result` join against
  `project.projcode`, and a mixed-charset comparison stops using the index. They are
  guarded by `_fit()` replacing astral characters with `U+FFFD` instead, so an emoji in
  `actionType` shows as `New�` in the table and verbatim in `raw_payload`.
- **`POST /v1/roles` answers 404/409 where legacy answers 400.** Ported late, after an audit
  of the deployed WAR found it missing. Legacy's own ladder is dead code — every validation
  failure there falls through to 400 with a leaked `ValidationException:` string (§2.1) — so
  there is no contract this breaks and no client that has seen anything else. Zero traffic in
  58 days of access logs. If a 409 appears in triage it means "project or user is inactive",
  and the `message` says which.
- **A new `unmapped` status appears on the XRAS tab** whenever XRAS calls a path we do not
  implement. It is **not** a failure and not a parked action: it means the broker asked for
  something new. One row is worth reading; a run of them is a conversation with ACCESS.
- **SMTP is implemented** (Sprint D, `NOTIFICATION_FRAMEWORK.md`). The pending-activation
  card's Notify button now previews and sends the handoff mail legacy sent on activation,
  and records every attempt in `notification_log`.
  ⚠️ It is **fail-closed**: `NOTIFY_ENABLED` defaults to `0` everywhere, and production
  sets it explicitly in `helm/values.yaml`. If it is unset at cutover, nothing is
  delivered and every attempt is recorded `suppressed` — visible on Admin → Configuration
  → Notifications, and warned about at startup. Check that card on the day.

## Housekeeping, after the dust settles

- ⚠️ Before the **next snapshot regeneration**, confirm `purge_xras_action_log` in
  `containers/sam-sql-dev/anonymize_sam_db.py` still covers the three new columns.
  `raw_payload` is a verbatim POST body full of PII and the obfuscated dump is a committed
  public LFS blob.
- ✅ `zz-90` / `zz-91` / `zz-92` were **self-retiring, and are now deleted** (2026-08-10),
  along with the `COPY initdb.d/` in `containers/sam-sql-dev/Dockerfile` — an empty
  directory is untracked by git, so leaving the COPY behind would fail the image build.
  Prod has the tables and the committed snapshot carries all three (0 rows, purged by
  the anonymizer), so dev and CI get them from the restore.

  ⚠️ The snapshot is now the **sole** carrier of the utf8mb3/utf8mb4 split, which the
  ORM does not encode. That is why
  `tests/integration/test_schema_validation.py::TestCharsetSplit` was added in the same
  commit: it pins all seven utf8mb4 columns *and* the three identifier columns that must
  stay utf8mb3, and it runs in the default tier rather than the gated stress one. If a
  future snapshot regeneration loses the split, that test is the only thing that will
  say so before an audit row goes missing in production.
- `compose.yaml` sets no `TZ` while `helm/values.yaml` sets `America/Denver`, so local dev
  and CI run UTC against ~123 `datetime.now()` call sites. Its own change, its own run.
