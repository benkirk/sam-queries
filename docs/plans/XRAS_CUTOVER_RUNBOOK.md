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
| 1 | All six handlers built and registered | `pytest -q` → 5,233 passed; `pytest -m stress` → 17 passed |
| 2 | The audit table carries `action_id`, `service`, `outcome_reason` | `SHOW COLUMNS FROM xras_action_log` on the target DB |
| 3 | `XRAS_ACTIONS_CAPTURE_ONLY` is `"1"` | `helm/values.yaml:291` — and confirm it in the running pod's env before anything else |
| 4 | The replay-and-diff oracle passes | `pytest tests/unit/test_xras_oracle.py -q` |
| 5 | A notification path exists for `active = 0` projects | Sprint B's pending-activation card on the Allocations dashboard |

⚠️ **Precondition 3 is the interlock and it is the one to physically check**, not assume.
While it is `"1"` the endpoint authenticates, parses and audits every post and dispatches
nothing. Legacy is still the system of record; dispatching before the repoint would
double-apply actions legacy has already applied, against live allocations, with no undo.

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

### 2 · The DBA ticket · ⏳ external

`xras_action_log` + `xras_activation_event` in production, and **run by hand on staging**
— `infrastructure/scripts/init-rds.sh` restores the raw `.xz` with no initdb hook.

**One ticket carries both tables.** A second costs another round of lead time.

The ticket is a **transcription of the current init scripts**, not a design question:

- `containers/sam-sql-dev/initdb.d/zz-90-xras_action_log.sql`
- `containers/sam-sql-dev/initdb.d/zz-91-xras_activation_event.sql`

⚠️ **File the current file.** `zz-90` gained three columns in C.1b — `action_id`,
`service`, `outcome_reason` — each with written evidence in
[`XRAS_STRESS_AND_SCHEMA.md`](XRAS_STRESS_AND_SCHEMA.md) § *Verdicts*. An older copy of the
DDL is missing them, and adding a column later is a second ticket.

- **Done when** both tables exist in prod and staging with all columns and indexes.
- ⚠️ **Landing this starts capturing nothing.** XRAS still posts to legacy's URL; nothing
  reaches this endpoint until the repoint. An early ticket buys lead time, not payloads.

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
- **SMTP is not implemented**, deliberately. Legacy keeps mailing until the repoint;
  Sprint B's pending-activation card is the accepted substitute trigger. It becomes real
  work *after* cutover — see `XRAS_REIMPLEMENTATION.md` § 0.2.

## Housekeeping, after the dust settles

- ⚠️ Before the **next snapshot regeneration**, confirm `purge_xras_action_log` in
  `containers/sam-sql-dev/anonymize_sam_db.py` still covers the three new columns.
  `raw_payload` is a verbatim POST body full of PII and the obfuscated dump is a committed
  public LFS blob.
- `zz-90` / `zz-91` are **self-retiring**: once prod has the tables and the snapshot is
  regenerated, the restore already contains them and `IF NOT EXISTS` makes the scripts
  no-ops. Delete them whenever.
- `compose.yaml` sets no `TZ` while `helm/values.yaml` sets `America/Denver`, so local dev
  and CI run UTC against ~123 `datetime.now()` call sites. Its own change, its own run.
