# XRAS Sprint A — action ingestion and handlers

**Handoff doc.** Execution plan for the next XRAS sprint. The wire contract, the measured
production data and the design decisions live in
[`XRAS_REIMPLEMENTATION.md`](XRAS_REIMPLEMENTATION.md) — this document does not repeat them,
it tells you what to build and in what order. Section references like §2.4 point there.

**Prior sprint:** Phase 1 (the six GET endpoints) is complete — PR #424.

---

## What this sprint delivers

`POST /api/xras/v1/actions` — the only writing surface on the XRAS integration, and the last
functional piece of the port. 175 posts in 30 days, 108 successes and 67 failures.

Concretely: the `xras_action_log` table and its ORM model, an `XrasActionSchema` that can
load the real payload, the POST route, and the six action handlers.

**Two things ship together on purpose.** The audit row is written *before* dispatch, so an
action that explodes in a handler is still recorded and replayable. That ordering is the
whole reason the audit trail is worth building — legacy's only record is an email, and its
only replay mechanism is pasting JSON into a form.

---

## Day one — two things with external lead time

Start both before writing any code. Neither blocks the other, and neither blocks the rest of
the sprint.

### 1. Harvest real payloads

**The repo contains exactly one sample action** —
`2.0.3:src/test/resources/xras/rest/request/createActionGood.json`, 3,593 B, an `actionType:
"New"` with 3 resources, 3 roles, 1 fos, 2 panels, 1 grant. Everything else about the payload
shape is inferred from the Java POJOs.

Legacy emails the raw body on **every** action, so `hdt@ucar.edu` / `sweg-notify@ucar.edu`
hold roughly 175 real payloads from the last 30 days as `XRAS_post_action.json` attachments.
Pull them.

This gates two deliverables, which is why it is first:

- **`XrasActionSchema`** is seven nested schemas and is the single most likely thing to be
  wrong. Validating it against real traffic beats validating it against one fixture.
- **The New handler** is 21% of posts at a 30% success rate. Writing it against one sample is
  how you ship the 70% failure rate again.

It also settles the three open questions §3.5 and Phase 5.1 name: the `roleType` carried by
stale ARC placeholder identities, whether `isReconciled` / `isAccountToBeCreated` are ever
populated in practice, and the actual `beginDate` / `endDate` format — legacy compares those
with lexicographic `String.compareTo`, which is correct **only** for zero-padded ISO-8601.

⚠️ Scrub before committing anything as a fixture: real payloads carry names, emails and phone
numbers (see `roles[].person`).

### 2. Raise the prod DDL ticket

`xras_action_log` does not exist in production and **we cannot create it** — the prod writer
account holds `SELECT, INSERT, UPDATE, DELETE` and no DDL
(`scripts/repair/RUNBOOK-missing-projects.md:36-38`). It is a DBA request with its own lead
time, and it blocks nothing in this sprint because local dev and CI get the table another way
(below). Agree the DDL first (§*The table*), then file it the same day.

Alembic is not an option: `migrations/README.md` records `sam` as "not yet" managed — the
only environment is `system_status`, and standing up `migrations/sam/` means stamping ~104
unmanaged tables, explicitly deferred to the legacy-database retirement.

---

## The table

### Shape

The closest existing precedent is **`ManualTask`** (`src/sam/operational.py:25`) — the legacy
AMIE manual-fallback table, whose `client` / `state` / `data(Text)` / `timestamp` is almost
exactly `remote_actor` / `status` / `raw_payload` / `received_time`. `AllocationTransaction`
(`src/sam/accounting/allocations.py:220`) supplies the `server_default` and
self-referential-FK patterns.

```sql
CREATE TABLE IF NOT EXISTS xras_action_log (
    xras_action_log_id  INT UNSIGNED NOT NULL AUTO_INCREMENT,
    received_time       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    remote_actor        VARCHAR(11)  NOT NULL,          -- api_credentials.username width
    action_type         VARCHAR(32),                    -- NULL when the payload won't parse
    request_number      VARCHAR(30),                    -- == projcode; project.projcode width
    raw_payload         TEXT         NOT NULL,          -- the body, verbatim, before parsing
    status              VARCHAR(16)  NOT NULL,          -- received|processed|manual|failed|replayed
    error_messages      TEXT,                           -- the ordered list, one per line
    projcode_result     VARCHAR(30),
    processed_time      DATETIME,
    processed_by        VARCHAR(35),                    -- users.username width
    replay_of_id        INT UNSIGNED,                   -- self-FK, NULL for original posts
    PRIMARY KEY (xras_action_log_id),
    KEY xras_action_log_received  (received_time),
    KEY xras_action_log_status    (status),
    KEY xras_action_log_request   (request_number),
    CONSTRAINT xras_action_log_replay_fk
        FOREIGN KEY (replay_of_id) REFERENCES xras_action_log (xras_action_log_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3;
```

Notes on the choices, so they can be argued with rather than guessed at:

- **`raw_payload` is `TEXT`, not `JSON`.** No SAM model uses `Column(JSON)` — every
  payload-ish column in the schema is `Text` (`operational.py:48`, `xras_views.py:158`, …),
  and `sam/base.py` does not export `JSON`. The one `Column(JSON)` in the repo is in
  `system_status`, a different database. The real sample is 2,605 B compact, so `TEXT`'s
  64 KB is ample; if you want headroom for an unusually large roster, `MEDIUMTEXT` costs
  nothing.
- **`action_type` is nullable** because a body that fails to parse still gets a row, and we
  will not know its type.
- **`replay_of_id`** makes replay a first-class relationship rather than a convention.
  `AllocationTransaction.related_transaction_id` is the precedent.
- Charset `utf8mb3` matches the rest of the schema — check `SHOW CREATE TABLE project` before
  filing the ticket and match whatever it actually says.

### Getting it into dev and CI

**This is the one genuinely new problem in the sprint**, so do not improvise it.

The obfuscated snapshot both `mysql` and `mysql-test` restore from is dumped from production,
so it will not contain this table. And **adding the ORM model without the table fails two
tests** — `test_all_tables_exist_in_database` (`tests/integration/test_schema_validation.py:213`)
and `test_all_models_have_tables` (`:422`) apply the same filter independently. There is **no
allowlist or skip mechanism** for "model exists, table pending"; the only exclusion is
`info={'is_view': True}`, which would be a lie and would silently drop the table from index
and FK drift checking too.

**Do this:** two small tracked changes, adding a post-restore init script to the image.

```
containers/sam-sql-dev/initdb.d/zz-90-xras_action_log.sql    (new — USE `sam`; + the DDL above)
containers/sam-sql-dev/Dockerfile                            (+ COPY initdb.d/ /docker-entrypoint-initdb.d/)
```

Why it works, and why the alternatives are worse:

- `mysql` and `mysql-test` **build from the same image**
  (`compose.yaml:219-221`, `:264-266`), so **one change serves local dev and CI**. CI needs no
  workflow edit — it builds fresh and tears down with `down -v` on every run.
- The stock `mysql:9` entrypoint globs `/docker-entrypoint-initdb.d/*` in collation order and
  the only existing entry is `init-db.sh`, so a `zz-` prefix guarantees the DDL runs **after**
  the `xzcat | mysql` restore.
- `IF NOT EXISTS` makes it **self-retiring**. Once the DBA creates the table in production and
  the snapshot is next regenerated, the restore already contains `xras_action_log` and this
  file becomes a harmless no-op you can delete whenever. Nothing to remember to undo.
- Schema validation then passes **honestly** — the table really exists, and its indexes and
  column types get checked like every other table's.

⚠️ **Publish this with it, because it is the thing people will get wrong.** `make docker-down`
is `docker compose --profile test down` — **no `-v`** (`Makefile:188`) — so it will not
re-run init scripts. Picking up the new table needs:

```bash
docker compose --profile test down -v && make docker-build && make docker-up
```

Rejected alternatives, for the record: appending the DDL to `sam-obfuscated.sql.xz` writes a
new ~20 MB Git LFS object per schema tweak and is unreviewable in a diff; a full
`make bootstrap` needs prod access over VPN plus the anonymisation run, and churns the data
under every fixture-dependent test.

### Two follow-ups this does not cover

- **Staging.** `infrastructure/scripts/init-rds.sh:14` restores the raw `.xz` straight to RDS
  with no initdb hook, so staging needs the DDL run by hand once.
- **PII.** The `raw_payload` scrubbing rule must land in
  `containers/sam-sql-dev/anonymize_sam_db.py` **before** anyone next runs `make bootstrap`.
  Do it in this sprint (step 7) rather than leaving it as a trap.

---

## The ORM model

`src/sam/integration/xras.py`, alongside `XrasResourceRepositoryKeyResource`. Follow that
file's conventions: `from ..base import *`, the `bh-`/`eh-` and `bm-`/`em-` banners,
`__table_args__` as a tuple of `Index(...)`, `__str__` + `__repr__`.

Then export it from `src/sam/__init__.py:181-183`, the "Integration and security" block.

That single import line **auto-registers a Flask-Admin view** — `add_default_models.py`
enumerates `Base.registry.mappers` and registers a `SAMModelView` for anything without a
`__bind_key__`. You get, for free: a paginated list at
`/database/default_views/xras_action_log`, detail view, `can_delete = False` (append-only by
default, which suits an audit table), and auto-excluded timestamp columns on the form. That
is a usable operator surface until Sprint B builds the real one — worth telling whoever fields
XRAS failures that it exists.

Add the model to `tests/integration/test_schema_validation.py` coverage — which is automatic
once the table exists, so this is really "confirm it passes", not "write a case".

---

## The schema

`src/sam/schemas/forms/xras.py`, exported from `forms/__init__.py`.

### Do not inherit from `HtmxFormSchema`

It is the wrong base class here, for two concrete reasons:

- It is **`ImmutableMultiDict`-shaped** — its `_strip_empty_strings` pre-load has a `getlist`
  branch for form posts and a plain-dict branch that is a **shallow** filter. It will not
  recurse into six nested arrays.
- That shallow filter also strips legitimately-empty strings at the top level, which for a
  JSON body is data loss, not convenience.

**Use a plain `marshmallow.Schema`**, following
`src/sam/schemas/charges.py:91` (`BaseChargeSummaryInputSchema`) — the existing family for
API JSON bodies rather than form posts. Set `unknown = EXCLUDE` explicitly; that family does
not, and §2.4 requires it (`@JsonIgnoreProperties(ignoreUnknown = true)` on every legacy
POJO).

### Nesting has one precedent, and you want half of it

Nothing in `src/sam/schemas/` loads nested objects — the deepest existing structure is
`fields.List(fields.Int())`. The only nested-load precedent in the repo is
`src/system_status/schemas/status.py:168-172`:

```python
queues = fields.Nested(QueueSchema, many=True, required=False, load_default=[])
```

Copy that shape. **Do not copy `load_instance = True`** — those schemas are
`SQLAlchemyAutoSchema` and return ORM objects. An audit-log ingest wants a plain dict it can
persist verbatim before anything is interpreted.

### Seven schemas, and the tolerances that matter

`XrasActionSchema` plus `Resource`, `Role`, `Person`, `Fos`, `Panel`, `Grant` — and note
`grants[].primaryFos` is a nested object inside a nested object, so `Grant` needs its own
`fields.Nested(FosSchema)`.

Three tolerances from §2.4, each measured against the real sample:

1. **Numbers arrive in string fields.** `fos[].fosTypeId` is `500005` (int) and `awardPeriod`
   is `36` (int), both declared `String` in Java. Jackson coerces silently; marshmallow will
   not. Accept both.
2. **Nulls are everywhere.** In the one sample, `requestShortTitle`, `roles[].endDate`,
   `roles[].isAccountToBeCreated`, `person.middleName`, `person.academicStatus`,
   `grants[].percentageAward`, `grants[].subAwardNumber`, `grants[].isPending` and
   `resources[].comments` are all `null`. Be liberal with `allow_none=True`; absent strings
   default to `""`, absent lists to `[]`, absent boxed numerics to `None`.
3. **The forgiving boolean applies to exactly one field** — `roles[].isAccountToBeCreated`.
   `null→false`, integer→`!= 0`, `t/true/y/yes`→true, `f/false/n/no/""`→false, anything else
   errors. Every other boolean uses defaults. Do not generalise it.

⚠️ `isReconciled` and `isAccountToBeCreated` are **inert** in legacy — parsed and never read
by any business logic. Parse them (they are contract), but do not wire them to behaviour
without deciding to, because a role meaning "provision this new person" currently does
nothing.

---

## The route

`POST /api/xras/v1/actions`, in `src/webapp/api/xras/` alongside the Phase 1 modules.

```python
@bp.route('/actions', methods=['POST'])
@csrf.exempt              # token-auth caller has no cookie to carry a CSRF token
@xras_api_required()
def post_action():
    ...
```

- **`@csrf.exempt` is required.** CSRFProtect covers all POSTs. The status-collector ingest
  (`src/webapp/api/v1/status.py:227-230`) is the precedent, with the rationale inline.
- **Use `xras_api_required`**, the Phase 1 alias — *not* `api_key_required`, whose
  `_auth_challenge` emits `{'error': …}` **with** a `WWW-Authenticate` header and is not
  byte-compatible with legacy's 41-byte 401 (§2.2).

**Order of operations, and the one part that is not negotiable:**

```
parse body ──failure──> 400, and STILL write a row (status='failed', action_type=NULL)
    │
    ├─ persist xras_action_log row  (status='received')   ← BEFORE dispatch
    │
    └─ dispatch to handler
           ├─ success   → status='processed', projcode_result, processed_time  → 200
           ├─ validation errors → status='failed', error_messages              → 422
           └─ no serviceable    → status='manual'                              → 200
```

Persisting before dispatch is what makes replay possible when a handler explodes. A row
written only on success is a success log, not an audit trail.

⚠️ **The log row must survive a handler rollback.** Handlers run inside
`management_transaction(db.session)`, which rolls back the whole session on exception
(`src/sam/manage/transaction.py:31-36`). If the audit row is in that same transaction it
disappears exactly when it matters most. Commit the row first, or write it on a separate
session/connection — decide deliberately and test it, because the failing case is the one
that matters and it will not show up in a happy-path test.

**Status codes** are §2.5: 400 on malformed JSON (legacy 500s), 422 with the real ordered
error list (legacy 500s with an opaque timestamp), 200 on success, 200 + `manual` for an
unhandled action type. The 422 is the headline improvement of this project — XRAS admins read
the response body directly in their "Accounting Service Posts" panel.

⚠️ Blueprint-local error handlers already exist from Phase 1 (`api/xras/__init__.py`); the
shared `register_error_handlers` has no 422 or 500 handler. Extend the local ones.

⚠️ Map **both** URL forms defensively (§2.1) — all 175 real posts go to bare `/v1/actions`,
but the ACCESS spec documents `/v1/actions/<actionId>/<requestId>/<actionType>`. If the broker
is ever corrected to match its own docs, every post 404s.

---

## The handlers

§3.1 has the full selector semantics; §3.2 the 11-strategy allocation-type extractor; §3.3 the
mnemonic / AOI / contract extractors; §3.4 the exact error strings.

### Solve the actor question first — it is smaller than the plan implied

Legacy writes `allocation_transaction.user_id = NULL` for XRAS.
`log_allocation_transaction` (`src/sam/manage/allocations.py:69`) declares `user_id: int`
positionally, which looks like a blocker. It is not: the column is **nullable**
(`src/sam/accounting/allocations.py:232`) and nothing in the function body validates or
dereferences it. **Passing `None` writes `NULL` today**, matching legacy exactly.

So the decision is: widen the type hint to `Optional[int]`, document that `None` means an
integration actor, and move on. No service user, no schema change. Do it before the first
handler, because every handler and every parity diff against legacy rows depends on it.

⚠️ While you are in there: `management_transaction` does **no** implicit audit logging. It is
six lines of commit-on-success / rollback-on-error. Audit rows exist because manage functions
*explicitly* call `log_allocation_transaction` — the context manager only makes the write and
its audit row atomic. Do not assume a handler gets logging for free.

### Build order — easy path first

The production data says the highest-volume handler is nearly perfect and the hardest one
carries all the pain (§1.3). Prove the pipeline on the easy path, then invest:

| Order | Handler | Share | Success | Why here |
|---|---|---:|---:|---|
| 1 | **Extension** | 60% | 98.5% | Highest volume, simplest semantics. Establishes dispatch, the actor convention, and the audit row end-to-end |
| 2 | **Supplement** | 15% | 100% | Same allocation primitives, one more branch |
| 3 | **Adjust** | 0% | — | A Supplement variant; marginal cost is small and it closes a spec obligation |
| 4 | **Update** (New/Renewal on an existing project) | 3% | — | Adds the `AUTO_DEFAULT_ALLOCATION_TRANSACTION` undo kludge; must tolerate the no-op case (2 of 109 posts changed nothing) |
| 5 | **New** | 21% | **30%** | Last. The extractor chain, mnemonic, contracts, GID, projcode — and the only one where the harvested payloads really pay off |
| — | **Transfer** | 0% | — | Route to manual fallback with an audit row. `exchange_allocations` does not fit its semantics (§5) |

Two reusable pieces worth not rebuilding:

- **New** should be ported against `src/webapp/dashboards/admin/projects_routes.py:600-687`,
  which already performs the exact sequence inside one `management_transaction`:
  `next_projcode(..., allocate=True)` → `allocate_next_gid` → `Project.create` →
  `ProjectContract.create` → `ProjectOrganization.create`.
- **Extension** needs an account-scoped variant or strict mode:
  `extend_project_allocations` is project-tree-scoped and **silently skips** shrinks, where
  legacy Extension errors on them (§5).

⚠️ Errors **accumulate, they do not short-circuit** (§3.1). Legacy gathers every problem into
an ordered `LinkedHashSet` and raises once with the full list. Reproduce that — reporting
every problem in one response is what lets an operator fix a request in one pass instead of
five, and it is what the 422 carries.

⚠️ Never leave `project.unix_gid` NULL — it is NULL for 0 of 5,795 production rows, and legacy
allocates GIDs locally for XRAS projects from pool `99000–99999` (§4.1).

⚠️ XRAS-created projects are set **inactive** on purpose (§3.1 row 1). Keep that. The success
email is the human activation trigger today; see *Notification* below.

---

## Notification, and why SMTP can wait

XRAS projects arrive `active = 0` and a human activates them — 21 of 23 have been. The
trigger is legacy's success email, and the webapp has no mailer at all (§5).

**No gap opens during this sprint.** Legacy keeps sending those emails right up until
`POST /actions` actually cuts over, which is cutover step 4 — after this sprint *and* after
Sprint B. The requirement is only that *some* notification path exists before step 4, and
Sprint B's dashboard can be it (a "pending activation" view).

So SMTP is a genuine option rather than a prerequisite. Recording it here so it is not
rediscovered at cutover.

---

## Verification

- `pytest tests/integration/test_schema_validation.py` — the new table validates honestly,
  including its indexes and column types
- `pytest tests/api/test_xras_access.py` — extend the Phase 1 file with the POST surface:
  400 on malformed JSON, 422 carrying the ordered error list, 200 on success, 200 + `manual`
  on an unhandled type, `ROLE_XRAS` enforcement on POST, and **an audit row written on every
  one of those paths** — including the handler-rollback case
- `pytest tests/unit/test_xras_actions.py` — each handler against factories, plus the
  harvested payloads
- full suite green before the PR (baseline: 4,404 passed / 36 skipped / 1 xfailed)
- **local end-to-end:**
  ```bash
  docker compose --profile test down -v && make docker-build && make docker-up
  docker compose up webdev --watch
  curl -u samuel:"$SAM_XRAS_PASS" -H 'Content-Type: application/json' \
       --data @createActionGood.json localhost:5050/api/xras/v1/actions
  ```
  then confirm the `xras_action_log` row, and that the auto-registered Flask-Admin view at
  `/database/default_views/xras_action_log` renders it
- replay each harvested payload and diff the resulting DB state against what legacy did for
  the same action — the 30-day action-mix correlation in §1.2 is the oracle

---

## Out of scope

Sprint B (the 4th Allocations tab, `sam-admin xras`, replay UI, `Permission.MANAGE_XRAS`),
SMTP, the GET cutover steps 1–3 (independent of this sprint — Phase 1 is done and can deploy
in parallel), and the `POST /actions` cutover itself, which additionally needs the 400/422
error-contract change confirmed with `allocations@access-ci.org` (§9).
