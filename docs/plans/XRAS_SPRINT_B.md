# XRAS Sprint B — the operator surface, replay, and freezing the schema

**Handoff doc.** Written for a cold start: you should be able to execute this without the
session that produced it. The wire contract, the measured production data and the
design decisions live in [`XRAS_REIMPLEMENTATION.md`](XRAS_REIMPLEMENTATION.md); the
as-built record of the previous sprint is [`XRAS_SPRINT_A.md`](XRAS_SPRINT_A.md).
Section references like §3.1 point at the reference doc. This document does not repeat
them — it tells you what to build and in what order.

**Prior sprints.** Phase 1 (six GET endpoints) and Phase 2-capture
(`xras_action_log` + `XrasActionSchema` + `POST /actions` in capture mode) are both
done, on PR #424, branch `xras_reimplementation`.

---

## What this sprint delivers

The operator surface for XRAS actions: a 4th top-level page on the Allocations
dashboard, a replay path, `Permission.MANAGE_XRAS`, and `sam-admin xras`.

**And one thing that is not a feature: a settled schema.** That is the real deliverable,
and the reason this sprint comes before the handlers.

---

## The decision that reorders everything: hold the DBA ticket

`xras_action_log` does not exist in production. The prod writer holds
`SELECT, INSERT, UPDATE, DELETE` and **no DDL**
(`scripts/repair/RUNBOOK-missing-projects.md:36-38`), and Alembic manages only
`system_status` (`migrations/README.md`), so creating it is a DBA request.

**Sprint A's advice was to file that ticket on day one. That advice is now withdrawn —
do not file it until this sprint is done.** The original reasoning was "it has external
lead time and blocks nothing," which was true but assumed the schema was settled. It
isn't: the table was designed from inference in a single sitting, nothing has ever
rendered it, and once production has it every change costs another ticket.

Dev and CI get the table from a tracked, self-retiring init script
(`containers/sam-sql-dev/initdb.d/zz-90-xras_action_log.sql`), so **amending the DDL is
free right now** — edit the file, then:

```bash
docker compose --profile test down -v && make docker-build && make docker-up
```

⚠️ `make docker-down` is `docker compose --profile test down` with **no `-v`**
(`Makefile`), so it will not re-run init scripts. This is the single most likely thing
to go wrong.

This is not hypothetical. Deltas already visible before any UI exists:

| Candidate | Why |
|---|---|
| **`action_type` is not indexed** | Indexes are `received_time`, `status`, `request_number`, `replay_of_id` (verified against the live table). But the natural triage axis is `(status, action_type)` — "failed New actions" *is* the 55% failure cohort (§1.3). Probably wants a composite index. |
| **No `http_status` column** | We answer 400 / 422 / 200 and do not record which. Triage wants it as a filter, and it stops being derivable from `status` once handlers add nuance. |
| **`error_messages` is newline-joined `TEXT`** | Fine to render as a list. But if the UI wants per-error structure — which field, which validator, which are retryable — that is a child table or a JSON column, and deciding while it is free beats deciding after. |
| **`received_time DEFAULT CURRENT_TIMESTAMP` is vestigial** | The app always sets it, and that default is exactly what caused a 6-hour timestamp inversion in Sprint A (it resolves in the *MySQL server's* timezone, which is UTC in the containers). Consider dropping it so nobody re-introduces the bug via hand-written SQL. |
| **Nothing records what a handler changed** | The row has `projcode_result` and nothing else. Once handlers land, "which allocations did this action touch" is the first question an operator asks. May want a link to `allocation_transaction`. |

**Keep a running list.** Add a `## Schema deltas` section to this file as you go and
record every one, with the reason. When the sprint ends, that list becomes the DDL in
the ticket. Do not amend the DDL silently — the init script and the ORM model must move
together or schema validation fails, which is the point of it failing.

---

## Day one — seed the table, because it is empty

Capture mode only records posts arriving at **our** endpoint, and XRAS still posts to
legacy. So `xras_action_log` is empty except for what you put there, and there is
nothing to build a dashboard against.

Seed it by replaying the four scrubbed payloads in `tests/fixtures/xras/actions/`
through the capture endpoint:

```bash
docker compose up webdev --watch
for f in tests/fixtures/xras/actions/*.json; do
  curl -s -H 'Content-Type: application/json' \
       -H "XA-REQUESTER: $SAM_XRAS_USER" -H "XA-API-KEY: $SAM_XRAS_PASS" \
       --data @"$f" http://localhost:5050/api/xras/v1/actions; echo " <- $f"
done
```

`SAM_XRAS_USER` / `SAM_XRAS_PASS` are already in the environment via
`source etc/config_env.sh`.

Two things follow from this:

- **Build Unit 3 (replay) early**, not last. Seeding *is* replay, so the feature you
  need for development is the feature you are shipping. Getting it wrong is cheapest now.
- **Ask Travis Fair for more samples.** Four payloads render four rows. `hdt@ucar.edu`
  is the only source (see §2.4 — `sweg-notify` holds none, and `actionJson` is never
  logged at any level), and both success and failure mails carry the attachment.
  Volume makes the dashboard's pagination, filtering and sorting real rather than
  theoretical.

### Three of five states are producible today

`status` is `received | processed | manual | failed | replayed`.

| State | Producible now? | How |
|---|---|---|
| `received` | ✅ | any successful capture-mode post |
| `failed` | ✅ | malformed JSON → 400; schema rejection → 422 |
| `manual` | ✅ | set `XRAS_ACTIONS_CAPTURE_ONLY=0` — with no handlers registered, every type parks |
| `processed` | ❌ | needs a handler (Phase 3) |
| `replayed` | ❌ | needs Unit 3 |

Design the UI for all five. Treat `processed` as **unvalidated** until handlers land, and
expect it to be the state that reveals the missing "what changed" column above.

---

## Unit 1 — `Permission.MANAGE_XRAS`

`src/webapp/utils/rbac.py`. Add the enum member next to the other `MANAGE_*` entries
(`Permission` is at `:39`; `MANAGE_SYSTEM_STATUS = "manage_system_status"` at `:151` is
the closest model — snake_case value, comment explaining scope).

⚠️ **The `ALL_*` aggregates are lexical.** `ALL_VIEW` / `ALL_EDIT` / `ALL_CREATE` /
`ALL_DELETE` are built by `_perms_with_action('view')` etc. at `:170-182`, which
prefix-matches the enum *name*. There is no `ALL_MANAGE`, so `MANAGE_XRAS` is picked up
by **nothing** and must be granted explicitly. That is the behaviour you want — an
integration-admin capability should not be swept in by a naming coincidence — but it
means you have to remember. Grant it in:

- `GROUP_PERMISSIONS` (`:240`) for the admin group(s) that field XRAS failures, and/or
- `_ALLOCATION_ADMIN` (`:221`) if it belongs to the allocation-admin tier, and/or
- `USER_PERMISSION_OVERRIDES` (`:276`) for named individuals.

Do **not** add it to `USER_FACILITY_PERMISSIONS` (`:311`) unless XRAS triage is genuinely
facility-scoped. It is not: an action arrives before we know its facility, and a
malformed body has none at all. Decide this deliberately and write down why.

Read-only viewing versus replay are different authorities. Consider whether viewing the
log needs `MANAGE_XRAS` at all or whether `VIEW_ALLOCATIONS` suffices, with
`MANAGE_XRAS` gating only replay. The dashboard is an audit surface; the replay button
is a write.

---

## Unit 2 — the 4th Allocations page

The Allocations dashboard's top-level navigation is **one route per page**, not Bootstrap
tabs. Verified layout:

```
src/webapp/dashboards/allocations/blueprint.py
    @bp.route('/')             index()        → redirects to the default (Projects)
    @bp.route('/projects')     projects()     ← default page
    @bp.route('/transactions') transactions()
    @bp.route('/adjustments')  adjustments()
src/webapp/templates/dashboards/allocations/
    base_allocations.html      ← the page_tabs() nav, :21-24
    projects.html  transactions.html  adjustments.html
    fragments/  partials/
```

So: add `@bp.route('/xras')`, an `xras.html` extending `base_allocations.html`, and a
fourth entry in that `page_tabs([...])` list:

```jinja
{'endpoint': 'allocations_dashboard.xras', 'label': 'XRAS', 'icon': 'fas fa-...'},
```

`page_tabs` is `dashboards/fragments/page_tabs.html:20` and resolves the active tab from
the endpoint, so there is no `active_tab` to thread.

Gate it like its siblings — `@login_required` plus a permission decorator; the existing
three use `@require_permission_any_facility(Permission.VIEW_PROJECTS)`. See Unit 1 on
which permission is right here.

**Table content**, driven by triage rather than completeness: `received_time`,
`action_type`, `request_number`, `status`, the error list, `projcode_result`, and a
replay affordance. Filter on status and action type; default to newest first. Follow the
existing fragment pattern — `transactions_fragment` / `adjustments_fragment` in the same
blueprint are the closest precedent, including their detail-row endpoints
(`transaction_details/<id>`).

⚠️ **Route-map parity is the gate.** `tests/unit/test_route_map_parity.py` pins every
dashboard `(endpoint, rule, methods)` triple to `tests/unit/snapshots/`. Regenerate with
`ROUTE_MAP_REGEN=1 pytest tests/unit/test_route_map_parity.py` and commit the diff **in
the same commit** as the route change.

Use `sam.fmt` filters for every date and number (`fmt_date`, `fmt_number`, `fmt_ago`) —
no `strftime` or `'{:,}'.format` in templates.

### The free stopgap that already exists

`XrasActionLog` is exported from `sam/__init__.py`, so `add_default_models.py`
auto-registered a Flask-Admin view at `/database/default_views/xras_action_log` —
paginated list, detail view, `can_delete = False`. It is behind `FLASK_ADMIN_ENABLED`
(off in prod). Worth telling whoever fields XRAS failures that it exists, and worth
looking at before designing Unit 2: it will show you what the raw columns feel like.

---

## Unit 3 — replay

Build this early (see Day one). Replay takes a stored `raw_payload`, re-dispatches it,
and records the outcome as a **new** row with `replay_of_id` pointing at the original.

- `replay_of_id` is a self-FK and the relationship is already on the model
  (`replay_of` / `replays`), so the audit chain is a first-class structure rather than a
  convention. `AllocationTransaction.related_transaction_id` is the precedent.
- Set `processed_by` (`varchar(35)`, `users.username` width) to the human who clicked.
  This is the one column that distinguishes an operator action from an integration one.
- **`raw_payload` is byte-exact on purpose.** MySQL `JSON` was rejected precisely because
  it normalises — see the `XrasActionLog` docstring for the measurements. Replay must
  re-post the stored bytes verbatim, not a re-serialisation.
- While handlers do not exist, replay of anything lands in `manual`. That is a valid and
  testable outcome; do not special-case it away.

⚠️ Reuse the audit-write helpers in `webapp/api/xras/actions.py` (`_record` / `_finish`)
rather than writing new ones. They commit on their **own connection**, outside
`management_transaction`, which rolls the whole session back on exception — an audit row
enrolled in that transaction vanishes exactly when it matters. This also means API-tier
tests must clean up explicitly, because the suite's per-test SAVEPOINT cannot undo a
write on another connection; `action_log` in `tests/api/test_xras_access.py` is the
fixture to copy, and its docstring explains the gap-lock deadlock that the obvious
cleanup causes under `-n auto`.

---

## Unit 4 — `sam-admin xras`

`src/cli/cmds/admin.py` is `click`-based: one decorated function per command (`user`,
`project`, `contracts`, `accounting`, `cache`), each delegating to a command class in
`src/cli/<domain>/commands.py`. Add `src/cli/xras/` following that shape and a `xras`
command in `admin.py`.

Useful surface, in rough priority: list/filter recent actions, show one action with its
payload, replay one by id, and a summary rollup by status and action type. `cache` is the
closest precedent for a command that is mostly a thin client over existing machinery.

Conventions are non-negotiable and shared with `hpc-usage-queries` — exit codes
0/1/2/130, the JSON envelope (top-level `kind`, ISO-8601 dates, `float(Decimal)`, sorted
sets, `indent=2`, `sort_keys=False`), and the `ExporterRegistry` interface. `src/cli/README.md`
has the recipe. Use `rich` for human output, `sam.fmt` for all formatting.

---

## Notification — why this sprint is on the cutover path

XRAS projects arrive `active = 0` **by design** (§3.1 row 1) and a human activates them;
21 of 23 have been. The trigger today is legacy's success email, and the webapp has no
mailer at all — zero `MAIL_*` / `flask_mail` / `smtplib` hits under `src/webapp/` or
`src/sam/` (§5).

Cutover step 4 (`POST /actions` moving to us) needs *some* notification path to exist
first. **A "pending activation" view in this dashboard can be it** — which is what keeps
SMTP deferred indefinitely rather than becoming a prerequisite. Build that view.

If you would rather have real email, the lift is a move plus a config wire-up, not a
build: `src/cli/notifications/email.py` is stdlib `smtplib` + Jinja2 with no Flask
coupling, and `src/config.py` already carries `MAIL_*`. But it is optional, and this
sprint is the reason it stays optional.

---

## Out of scope

**The handlers (Phase 3).** They are the next sprint, and they are partly sample-blocked:

| Handler | Share | Samples in hand |
|---|---:|---|
| Extension | 60% | ✅ one success + one failure |
| New | 21% | ✅ one success + one failure |
| Supplement | 15% | ❌ **zero** |
| Update | 3% | ❌ **zero** |
| Adjust / Transfer | 0% | ❌ zero (Transfer routes to manual regardless, §5) |

Also out: SMTP (above), the GET cutover steps 1-3 (independent — Phase 1 is done and can
deploy in parallel), and the `POST /actions` cutover itself, which additionally needs the
400/422 error-contract change confirmed with `allocations@access-ci.org` (§9).

Still open on the wire contract: **no co-PI role has ever appeared in a sampled payload**,
so whether `roleType` is `'Co-PI'` or `'CoPi'` is unknown. One bulk forward closes it.

---

## Verification

- `pytest tests/unit/test_route_map_parity.py` — regenerated snapshot committed alongside
  the route change
- `pytest tests/integration/test_schema_validation.py` — still passes after **every** DDL
  amendment; the init script and the ORM model must move together
- `pytest tests/api/test_xras_access.py` — extend with the replay surface: a replay writes
  a new row, `replay_of_id` points at the original, `processed_by` records the human, and
  `MANAGE_XRAS` is enforced. Copy the `action_log` fixture; do not invent new cleanup.
- `pytest tests/unit/test_xras_actions.py` — unchanged, but it is the canary: it asserts
  the measured wire contract, so a failure means a schema or schema-module edit broke
  fidelity
- full suite green before the PR — baseline at the end of Sprint A was **4481 passed,
  36 skipped, 1 xfailed**; re-measure rather than trusting it
- **visual:** seed the table per Day one, then confirm the page renders all five states,
  that filtering and sorting work, and that the replay affordance is gated
- `sam-admin xras --help` and one real invocation per subcommand, in both `rich` and
  `--format json`

## Definition of done

1. The dashboard renders and triages real seeded rows, replay works end to end, and
   `MANAGE_XRAS` gates the write.
2. **The `## Schema deltas` section of this file is complete**, the DDL amended, schema
   validation green.
3. **Then, and only then, file the DBA ticket** — with the final DDL. Note that staging
   also needs it run by hand once: `infrastructure/scripts/init-rds.sh` pipes the
   raw `.xz` straight into `mysql` (`:75-77`) with no initdb-hook equivalent, so nothing
   applies the `zz-90-` DDL there.

---

## Cold-start orientation

What Sprint A left you, and where:

| Thing | Where |
|---|---|
| Reference doc (wire contract, prod data, divergences) | `docs/plans/XRAS_REIMPLEMENTATION.md` |
| Sprint A as-built (the 20 measured contract corrections) | `docs/plans/XRAS_SPRINT_A.md` § *Track 0* |
| Retired Sprint A handoff | `docs/plans/implemented/XRAS_ACTION_INGESTION.md` |
| Table DDL (amend here) | `containers/sam-sql-dev/initdb.d/zz-90-xras_action_log.sql` |
| ORM model | `src/sam/integration/xras.py` — `XrasActionLog` |
| Schema (7 nested) | `src/sam/schemas/forms/xras.py` |
| Route + audit helpers | `src/webapp/api/xras/actions.py` |
| Capture kill-switch | `XRAS_ACTIONS_CAPTURE_ONLY` in `src/webapp/config.py` |
| Real payloads (scrubbed, committed) | `tests/fixtures/xras/actions/` — 4 files |
| Raw payloads (PII, **not** in git) | `~/xras_payloads_raw/` |
| Scrubber | `scripts/xras/scrub_payload.py` |
| Snapshot purge rule | `containers/sam-sql-dev/anonymize_sam_db.py` — `purge_xras_action_log` |
| Legacy Java source | `~/codes/sam` (tag `2.0.3`; `git diff 2.0.3..HEAD` over the xras paths is empty) |

Two traps that cost time in Sprint A and will cost it again:

- **`compose.yaml` sets no `TZ`** while `helm/values.yaml:230` sets `America/Denver`, so
  every local and CI container runs UTC against ~123 `datetime.now()` call sites. A
  host-run `pytest` hides it because CI runs pytest *inside* the container. When a table
  has two timestamps, stamp both from the app clock; never let one default to the DB's
  `CURRENT_TIMESTAMP`. Fixing this properly is its own change.
- **Never write the skip-ci markers in a commit message or PR body**, including inside
  backticks or a quoted table. GitHub scans the whole text and creates *no* check suite,
  which is indistinguishable from an outage — and squash merges build the message from
  the PR title *and* body. It has cost this repo twice. See the *Skipping CI* section of
  `CLAUDE.md`.
