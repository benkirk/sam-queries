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
| 1 | All six handlers built and registered | `pytest -q` → 6,200 passed; `pytest -m stress -n 0` → 22 passed |
| 1b | The whole legacy surface is mapped — all eight endpoints, not the seven XRAS calls today | `pytest tests/api/test_xras_roles.py tests/api/test_xras_unmapped.py -q` |
| 2 | ✅ **Done 2026-08-10.** The audit table carries `action_id`, `service`, `outcome_reason` | `SHOW COLUMNS FROM xras_action_log` on the target DB |
| 2b | ✅ **Done 2026-08-10.** ⚠️ The DDL applied is the **current** `zz-90`/`zz-91`/`zz-92` — **exactly 7** columns must come back utf8mb4: `raw_payload`, `error_messages`, `comment`, `notified_to`, `recipient_name`, `subject`, `error` | `SELECT TABLE_NAME, COLUMN_NAME, CHARACTER_SET_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='sam' AND TABLE_NAME IN ('xras_action_log','xras_activation_event','notification_log') AND CHARACTER_SET_NAME='utf8mb4'` |
| 3 | `XRAS_ACTIONS_CAPTURE_ONLY` is `"1"` | `helm/values.yaml`, key `XRAS_ACTIONS_CAPTURE_ONLY` — and confirm it in the running pod's env before anything else |
| 4 | The replay-and-diff oracle passes | `pytest tests/unit/test_xras_oracle.py -q` |
| 5 | A notification path exists for `active = 0` projects | Sprint B's pending-activation card on the Allocations dashboard |

⚠️ **This document cites `helm/values.yaml` by key name, never by line number.** Three
refs here were repaired once (`:291` → `:316`) and were wrong again within the same
branch, because the commit that repaired them also rewrote the comment above the setting
and pushed it down sixteen lines. Both keys are unique in the file; `grep` is the stable
address and a line number is a fact with a half-life.

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
`zz-92`. Full evidence in [`DBA_PRIVILEGE_REQUEST.md`](../../plans/implemented/DBA_PRIVILEGE_REQUEST.md).

All **three** files were then applied by hand to `sam-sql.ucar.edu`, in this order —
`zz-91`'s FK references `zz-90`, so the order is load-bearing:

```bash
mysql --defaults-file=<creds> < containers/sam-sql-dev/initdb.d/zz-90-xras_action_log.sql
mysql --defaults-file=<creds> < containers/sam-sql-dev/initdb.d/zz-91-xras_activation_event.sql
mysql --defaults-file=<creds> < containers/sam-sql-dev/initdb.d/zz-92-notification_log.sql
```

**Three files, not two.** `zz-92-notification_log.sql` (Sprint D,
[`NOTIFICATION_FRAMEWORK.md`](../../plans/implemented/NOTIFICATION_FRAMEWORK.md)) belongs to the same round;
this gate listed only the two XRAS tables for a while, which is precisely the
one-table-short mistake the "one ticket" rule exists to prevent.

### 2c · `xras_opportunity_allocation_type` · ✅ **DONE 2026-08-20**

A fourth table, applied the same way and by the same grant. It is **not** part of
the cutover gate — the map is additive by design, and an empty table simply falls
through to the extractor ladder — but the DDL of record belongs here with the rest.

No `initdb.d` hook this time: that directory and its `COPY` were retired
(`containers/sam-sql-dev/Dockerfile:8-27`), so this was applied to production and
the snapshot regenerated instead. `REFERENCES` is again the load-bearing grant —
this table has an FK to `allocation_type`.

```sql
CREATE TABLE IF NOT EXISTS xras_opportunity_allocation_type (
  opportunity_id     INT          NOT NULL,
  allocation_type_id INT          NOT NULL,
  opportunity_name   VARCHAR(120)     NULL,
  PRIMARY KEY (opportunity_id),
  KEY xras_opportunity_alloc_type_at_idx (allocation_type_id),
  CONSTRAINT xras_opportunity_alloc_type_at_fk
    FOREIGN KEY (allocation_type_id)
    REFERENCES allocation_type (allocation_type_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
```

Then the nine known opportunities, seeded with **the pair the ladder already
produces** — which is what makes the map a drop-in rather than a behavior change.

⚠️ Connect with a **utf8mb4 client charset**: 530902's name carries an em-dash.
⚠️ Ids are resolved by name at runtime, never pinned — and the row count is the
check. Fewer than 9 means a panel or allocation-type name has drifted.

```sql
INSERT INTO xras_opportunity_allocation_type (opportunity_id, allocation_type_id, opportunity_name)
SELECT v.oid, at.allocation_type_id, v.name
  FROM ( SELECT 530902 AS oid, 'UNIV USS' AS panel, 'Small' AS atype,
                'University small request — with NSF award' AS name
         UNION ALL SELECT 531428, 'CHAP', 'CHAP', 'University Large Request - Fall 2021'
         UNION ALL SELECT 532220, 'UNIV USS', 'Small', 'Small Allocation (University)'
         UNION ALL SELECT 532221, 'UNIV USS', 'Small (No NSF award)', 'Exploratory Allocation (University)'
         UNION ALL SELECT 532222, 'UNIV USS', 'Data', 'Data Analysis Allocation (University)'
         UNION ALL SELECT 532223, 'UNIV USS', 'Classroom', 'Classroom Allocation (University)'
         UNION ALL SELECT 533144, 'CHAP', 'CHAP', 'Large Allocation (University) - Spring 2024'
         UNION ALL SELECT 533606, 'CHAP', 'CHAP', 'Large Allocation (University) - Fall 2024'
         UNION ALL SELECT 533936, 'CHAP', 'CHAP', 'Large Allocation (University) - Spring 2025' ) v
  JOIN panel p            ON p.panel_name = v.panel
  JOIN allocation_type at ON at.panel_id = p.panel_id AND at.allocation_type = v.atype;

SELECT COUNT(*) FROM xras_opportunity_allocation_type;   -- 9
```

**Provenance, added 2026-08-20** so an automatically-derived row is
distinguishable from a human's decision:

```sql
ALTER TABLE xras_opportunity_allocation_type
  ADD COLUMN source VARCHAR(32) NOT NULL DEFAULT 'manual';
```

Four rows are `manual` because XRAS is wrong about them and no API can say so —
the unsponsored family (530296, 530315, 530900) and `NCAR - ASD Opportunity`
(531461). See the design doc § 8.5.

Adding a **new** opportunity is now nothing at all: `xras_sweep` writes it on
the next hourly run when the type map and the free-text ladder agree, and
withholds it for review when they do not. Design:
[`XRAS_OPPORTUNITY_ALLOCATION_TYPE.md`](../outgoing/XRAS_OPPORTUNITY_ALLOCATION_TYPE.md).

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

### 2d · `xras_remediation_event` · ✅ **DONE 2026-08-21 — applied to production**

The fifth table, and the first that records SAM writing **out** to XRAS rather than
XRAS writing in. Backs the Remediations card
([`../../plans/XRAS_REMEDIATIONS.md`](../../plans/XRAS_REMEDIATIONS.md)); the write
surface it audits is measured in
[`../outgoing/XRAS_WRITE_PROBES.md`](../outgoing/XRAS_WRITE_PROBES.md).

Same grant, same `initdb.d`-is-retired route as § 2c. **No foreign keys at all**,
which is a first here and deliberate: every identifier on this table — both
usernames, `request_id`, `action_id`, `role_id` — belongs to XRAS, and the merge
operation a row records *deletes* the username it names. `REFERENCES` is therefore
not needed for this one.

**Apply the committed script rather than copying out of this document** —
it carries the same statements plus the verification queries, and it is the
artifact that was tested:

```bash
mysql -u <hpc-writer> -h <host> -p sam < scripts/create_xras_remediation_event.sql
```

Verified 2026-08-21: that script applied to an empty schema produces a table
byte-identical to the one the suite runs against, and the ORM validates against
it with no column drift. For reference, the statements are:

```sql
CREATE TABLE IF NOT EXISTS xras_remediation_event (
  xras_remediation_event_id  INT UNSIGNED NOT NULL AUTO_INCREMENT,
  operation        VARCHAR(24)      NOT NULL,
  status           VARCHAR(16)      NOT NULL,
  username         VARCHAR(64)          NULL,
  target_username  VARCHAR(64)          NULL,
  request_number   VARCHAR(128)         NULL,
  request_id       INT UNSIGNED         NULL,
  action_id        INT UNSIGNED         NULL,
  role_id          INT UNSIGNED         NULL,
  role_type        VARCHAR(24)          NULL,
  xa_user          VARCHAR(64)          NULL,
  created_by       VARCHAR(35)      NOT NULL,
  creation_time    DATETIME         NOT NULL,
  completed_time   DATETIME             NULL,
  http_status      SMALLINT UNSIGNED    NULL,
  outcome_reason   VARCHAR(255)         NULL,
  comment          TEXT                 NULL,
  before_state     TEXT                 NULL,
  after_state      TEXT                 NULL,
  PRIMARY KEY (xras_remediation_event_id),
  KEY xras_remediation_event_op_time  (operation, creation_time),
  KEY xras_remediation_event_user     (username),
  KEY xras_remediation_event_request  (request_number),
  KEY xras_remediation_event_operator (created_by, creation_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;

ALTER TABLE xras_remediation_event
  MODIFY comment      TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  MODIFY before_state TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL,
  MODIFY after_state  TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NULL;
```

⚠️ **`request_number` is VARCHAR(128), NOT 30 like `xras_action_log`.** That
divergence is deliberate. The action log only sees requests being *pushed*,
which always carry a real projcode; this table sees the whole remediation
cohort, including Submitted requests whose number is still free text a PI
typed. Measured on the live cohort: `'New University Large Request - Fall 2017
UCUD0005 Zhong'` is **55 characters**, and it renders on the card with a
Withdraw button — so it is reachable, and at 30 the insert truncates or errors
under strict mode. It stays `utf8mb3` so an equality lookup against the action
log is not a mixed-charset comparison.

⚠️ **The `ALTER` is not optional and is not cosmetic.** `before_state` captures a
pre-merge person detail sheet — free text, real names, `residenceCountry` — and
`comment` is unconstrained operator prose. Applied as `utf8mb3` they silently
truncate at the first 4-byte character. Doing it later is an `ALTER` on a table
with an audit trail in it; doing it now is a property of an empty table.
`tests/integration/test_schema_validation.py` pins the resulting set at **10**
utf8mb4 columns and, on the other side, that `request_number` / `username` /
`created_by` stay `utf8mb3` so they keep joining `xras_action_log`.

Verification after applying:

```sql
SELECT COUNT(*) FROM xras_remediation_event;            -- 0
SELECT COLUMN_NAME, CHARACTER_SET_NAME
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE()
   AND TABLE_NAME = 'xras_remediation_event'
   AND CHARACTER_SET_NAME = 'utf8mb4';                  -- 3 rows
SHOW INDEX FROM xras_remediation_event;                 -- PRIMARY + 4 named keys
```

**Verified on production 2026-08-21**, and not merely by the counts the script
prints — those identify nothing. `information_schema` was read back in full and
diffed against the schema the suite runs on:

| Check | Result |
|---|---|
| 19 columns: name, type, nullability, charset, collation | ✅ byte-identical |
| `request_number` | ✅ `varchar(128)` utf8mb3 — the width the live cohort forced |
| utf8mb4 on exactly `comment` / `before_state` / `after_state` | ✅ and no others |
| 5 indexes, by column and ordinal | ✅ identical |
| Engine / table collation | ✅ InnoDB / utf8mb3_general_ci |
| Rows, foreign keys | ✅ 0 / 0 |

⚠️ The count-only queries in the script are a smoke, not a proof: "3 utf8mb4
columns" is equally true if the wrong three widened, and that particular
mistake — `request_number` drifting to utf8mb4 — would silently turn the
lookup against `xras_action_log` into a mixed-charset full scan. Read the
columns back by name.

⚠️ **CI stays red until the snapshot is regenerated — this is a hand-off, not an
oversight.** The CI test database is the committed LFS blob
(`containers/sam-sql-dev/backups/sam-obfuscated.sql.xz`), so a table applied to a
local MySQL reaches CI only after a regeneration and a re-commit of that blob.
`test_schema_validation.py` asserts an exact utf8mb4 column set and will fail
there until then.

Not done automatically, and deliberately so: the regeneration reads the **dev
database, which holds real production data**, and produces a *public* blob. The
anonymizer is what stands between those two facts, `make bootstrap` swallows its
failures with `|| true`, and the result is committed either way — so the run and
its by-hand verification belong to an operator, not to a build step.

Interim: apply the DDL by hand to any local MySQL that needs it, including the
test container on 3307 —

```bash
mysql -u root -h 127.0.0.1 -P 3307 -proot sam < path/to/the/DDL/above
```

⚠️ **The snapshot purge must land with the table.**
`containers/sam-sql-dev/anonymize_sam_db.py::purge_xras_remediation_event` empties
it before the obfuscated dump, because `before_state` is the worst payload on any
of these tables and the dump is a **public Git LFS blob**. `make bootstrap`
swallows anonymization failures with `|| true` and dumps anyway, so **verify the
purge by hand** on every regeneration — a silent skip ships the PII.

### 3 · Parity against the deployed host · ✅ **PASSED 2026-08-19 — 13/13 byte-identical**

```bash
python utils/parity/check_legacy_apis.py --api xras \
       --new-base-url https://sam.hpc.ucar.edu --xras-user benkirk --timeout 120
```

Uses the `ROLE_XRAS` credential (`SAM_XRAS_USER` / `SAM_XRAS_PASS`, already in `.env`).
This is the **GET-side** cutover verification and it is independent of gate 4.

```
== xras == 13/13 checks passed (65.2s)
  ✓ people roster: 3,844,518 B identical      ✓ dates/requests single: 120 B identical
  ✓ people/{username}: 170 B identical        ✓ dates/requests multi:  306 B identical
  ✓ people/{username} 404: 58 B identical     ✓ requests/request/{SCSG0001,SCSG0002,UCIS0004}
  ✓ requests/user/benkirk: 6 masters          ✓ requests/role/{pi,co_pi,allocation_manager}
```

⚠️ **Three flags are load-bearing, and the bare command is a weaker run than it looks.**

- **`--new-base-url`** — the script's default is `samuel.k8s.ucar.edu`. Same app, but the
  cutover host is the one to prove.
- **`--xras-user`** — without it the script *samples* the roster for a user with requests,
  and on a real run **all eight sampled users had none**. It warns and carries on, and the
  result is `8/8 passed` with `requests/request/*` exercised only against an unknown
  number and `dates/requests` **not probed at all**. A green 8/8 and a green 13/13 look
  identical at a glance. Read the check list, not the count.
- **`--timeout 120`** — legacy answers `requests/request/SCSG0001` in ~6.8 s (the new
  stack: ~0.67 s) and dropped the connection outright on one run:
  `RemoteDisconnected('Remote end closed connection without response')`. That is legacy
  being slow, not a parity failure — both hosts return the same 30,296 bytes. Retry
  before investigating.

- **Done when** the run is byte-clean across all six endpoints — i.e. **13** checks, with
  `dates/requests` and a populated `requests/request/{n}` among them.
- ⚠️ Re-run it if anything changes `xras_resource_repository_key_resource`.
  `resourceRepositoryKey` is *omitted* when a resource is unmapped, so **adding a mapping
  row changes GET response bytes** and invalidates a previous clean run. That is the most
  likely triage-week fix, so expect to re-run this during the week.

✅ **#458 and #459 do NOT invalidate this run** — checked rather than assumed, because the
default assumption for two large XRAS PRs is that they do. Three independent reasons:
`src/webapp/api/xras/` is untouched by both merges; `src/sam/xras/roster.py`'s nine new
lines are a comment block (why exact `String.equals` role matching is correct) with no
executable change; and neither PR adds a `xras_resource_repository_key_resource` row —
there is no DDL or seed in either diff. `xras_opportunity_allocation_type` is read only by
`sam/xras/extractors.py`, i.e. the inbound **POST** path, so it cannot move GET bytes.

The trigger above stays exactly as sharp: it is a *mapping row*, not "an XRAS change".

### 4 · The 400/422 contract · ✅ **ANSWERED 2026-08-11**

Legacy answers 500 for both a malformed body and a failed validation, and a bare 200 for
an action it silently parked. We answer 400 / 422 / 200 and distinguish all of them.

Steven Peckins (XRAS, UIUC) answered on the *Planning for NCAR XRAS->SAM reintegration*
thread. Verbatim, because each clause retires something:

> Your changes on the POST /v1/actions endpoint are fine; XRAS won't notice. **POSTs are
> not automatically retried. They are triggered by a human — a user in xras_admin pushes
> a button.** Any status other than 200/OK is considered an error. XRAS itself does not
> treat any non-200 error status specially, but it's fine to use different statuses. The
> response body is saved and made available in xras_admin for the admin to see, so it's
> nice to include something informative.

- ✅ **The retry loop cannot happen.** This was the riskiest open unknown on the cutover
  path; it is closed. A 422 is seen by the person who pushed the button.
- ✅ **The 400/422 split is approved.** No change back.
- ✅ **The structured error list is an improvement, not a risk.** Steve quoted what legacy
  currently returns — `{"message":"Unhandled SAM exception processing XRAS request
  (timestamp 1771966790970)","result":null}` — against ACCESS's own accounting service,
  which explains *why* an action failed. Our accumulated 422 list is exactly that fix.

✅ **The one thing this opened is now closed — 2026-08-19.** The response body is shown
to a human, and a **parked** action answered `message: 'OK'` — byte-identical to a
success. So an admin who posted a `Date Adjustment` (or a Transfer, or anything disabled
by the triage lever) was told it worked. Legacy does the same, so it was never a
regression or a blocker; but `Date Adjustment` parks and is 4 of the 41 corpus payloads,
which makes the silent-success arm the *common* one, and Steve explicitly invited an
informative body.

**Decided: the `manual` arm now answers a distinct message; `processed` still answers
`'OK'`.** Two constraints shaped it:

- **The status stays 200.** Steve: *"Any status other than 200/OK is considered an
  error."* A parked action is not an error, so 202 — the tidy REST answer — would have
  been read as a failure.
- **The body is a module constant, not `DispatchResult.reason`.** Three of the four
  parking causes name internal machinery (`XRAS_ACTIONS_ENABLED`, an unregistered
  handler) that an ACCESS admin can neither act on nor should see. That detail stays in
  `xras_action_log.outcome_reason`, which is what our own operator surfaces read.

Outside the parity gate, which covers the six **GET** endpoints only.

⚠️ **Also raised, and not yet triaged.** Steve reports anomalies in the *GET*
`/v1/requests/*` responses: NCAR does not return `xrasActionId` or `xrasActionResourceId`,
which xras_admin uses to correlate actions and render links back to them, and *"even the
dates were out of whack with what XRAS had."* We ported those responses byte-for-byte, so
we inherited this exactly. It is **post-cutover work by construction** — closing it moves
response bytes and would invalidate the gate 3 parity run. He is digging up details.

💡 **Offered and DECLINED: a test instance.** Steve offered *"a test instance of
xras_admin; we could set that up against your new accounting service, if it would be
helpful."*

**Not taken for this cutover — decision 2026-08-11.** The cutover stays abrupt and every
precondition in this document still stands as written: the § *One fact* banner above is
unchanged, and the pre-cutover evidence remains the 41-payload corpus and the oracle.
Nothing here depends on a test instance, and nobody should arrive on the day wondering
whether one was set up.

**Noted for future us.** If a later XRAS-facing change wants a rehearsal — the GET
serializer work in the paragraph above is the obvious candidate, since it deliberately
moves response bytes — this offer is the way to get one, and it costs a
conversation rather than a code change.

⚠️ **This is not dual-posting and does not reopen it.** Dual-posting is ruled out (§ *One
fact*, and `XRAS_REIMPLEMENTATION.md` § 6 Phase 5.5): it means production traffic
reaching two systems. A test instance is a harness driven by us against a stack XRAS
production never touches. Declining the harness does not weaken that ruling, and taking
it later would not either.

### 5 · The repoint — this is the cutover

Coordinated with ACCESS. Two things happen, and the order matters:

1. **XRAS repoints** its base URL to `sam.hpc.ucar.edu`.
2. **`XRAS_ACTIONS_CAPTURE_ONLY` flips to `"0"`** in `helm/values.yaml`, and deploy.

Flipping *before* the repoint is harmless (nothing is arriving). Flipping *after* means
every post in the gap is captured as `received`. Flipping **early on a stack XRAS is
already posting to** is the double-apply, and is the thing this interlock exists to
prevent.

✅ **Done 2026-08-19: step 2 landed first, deliberately, ahead of the repoint.** The
runbook originally read as if the two steps were simultaneous. They are not symmetric,
and the asymmetry is worth stating because it is the opposite of the intuition that a
safety interlock should come off last:

> A post that arrives while `CAPTURE_ONLY` is `"1"` is stranded. **`--recheck` cannot
> apply it** — `dispatch_action(..., validate_only=True)` returns before
> `management_transaction` opens, structurally — so the only recovery is asking the XRAS
> admin to push the button again, per action. Meanwhile flipping early costs nothing at
> all: XRAS is still posting to `sam.ucar.edu`, so the dispatching arm sees no traffic
> until the repoint.

So the gap has a real price in one direction and none in the other. Verify the log holds
no `received` rows before flipping — if it does, XRAS has already repointed and those
posts need to be re-sent.

⚠️ Order the *checks* accordingly: `sam-admin xras --summary` first, flip second.

---

## Triage week

➡️ **The full version is [`XRAS_TRIAGE_PLAYBOOK.md`](XRAS_TRIAGE_PLAYBOOK.md)** — how to
classify a row, the 422 catalog with the data fix for each, and what the levers cost.
What follows is the short form.

The watch surface, in the order you will reach for it:

| Surface | What it answers |
|---|---|
| XRAS → **Pending Activations & Notifications** | Everything received, filterable, raw payload behind `MANAGE_XRAS` |
| XRAS → **Pending Users** | Who needs a SAM account before a handoff can land, both feeds unioned with a **Source** badge: *Received push* (usernames on received actions — the 55% failure class) and *Pending request* (approved XRAS requests *not yet pushed*, before the action arrives). Received pushes sort first |
| `sam-admin xras --summary` | Status counts at a glance |
| `sam-admin xras --status failed` | The 422s, with their error lists |
| `sam-admin xras --status manual` | What was parked, and now **why** |
| `sam-admin xras --accounts [--enrich]` | The account worklist on the CLI |
| `sam-admin xras --validate-mapping` / `--validate-opportunities` | The two mapping tables, both sides |

⚠️ **Before the repoint, every Pending Users row is a *Pending request*; the first
*Received push* row is the first sign XRAS has repointed**, not a bug. Received pushes read
`xras_action_log`, at 0 rows until XRAS repoints; pending requests reach `api.xras.org`
directly and were answering in production on 2026-08-20 (22 requests, 18 accounts needed).

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
XRAS_ACTIONS_ENABLED: "Extension,Supplement"   # helm/values.yaml
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
  appears in that list.
  ⚠️ Since #458 the audit is **two-sided** and exits non-zero on **two** states, not one:
  a dangling key (a broken FK on our side) *and* `xras_only_keys`, a key XRAS offers that
  SAM cannot resolve — the one that actually breaks an award. Measured 2026-08-20:
  **13/13 of the keys XRAS offers resolve**, zero dangling, so that failure cannot fire
  against today's catalog. Unconfigured or unreachable API degrades to the local half and
  says so; do not read a one-sided report as a clean two-sided one.
- **89% of active organizations have no mnemonic soft link** (153 of 171), and 80% of
  institutions. This is the root of New's 24% mnemonic failure class. A data fix, and it
  would move New's success rate more than any code.
- **Transfer parks by design**, with `outcome_reason` saying so. Zero production traffic.
- **Wire shapes never seen in production**: `Co-PI` vs `CoPi` is **closed** — membership
  ignores `roleType` entirely, so the spelling cannot matter, tested across three. And
  now measured: 41 payloads across ~35 projects carry exactly `PI` / `Allocation Manager`
  / `User` and no co-PI at all. `Renewal` and `Advance` are exercised synthetically; the
  2026-08-11 forward supplied three `requestType: 'Renewal'` payloads, which is **not**
  the same thing and does not reach the Renewal arm.
- ⚠️ **`Date Adjustment` is a real action type and it parks.** Four samples in the
  2026-08-11 forward; unknown before that, because it only ever appears in the
  manual-fallback subject line. Legacy has no serviceable for it either, so parking is
  parity-correct and a human applies it exactly as today. It is listed in
  `XRAS_ACTION_TYPES` so it is filterable on the XRAS tab from the first row.
  **Expect these in triage week and do not treat them as failures.** Whether to service
  it is a question for ACCESS: the payloads are Extension-shaped, but they carry an
  `actionBeginDate` that Extension ignores, and a separate action type most likely
  exists to move dates in directions Extension rejects.
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
