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

> **The hold is over.** This sprint is done and § *Schema deltas* is closed, which is
> exactly the condition § *Definition of done* item 3 set. **File the ticket now, with
> both init scripts** — `zz-90-xras_action_log.sql` and `zz-91-xras_activation_event.sql`
> — and run them by hand on staging. The reasoning below is kept because it is why the
> ticket carries two tables instead of one, and why the DDL it carries was amended four
> times before it left.

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

**The handlers (Phase 3).** They are the next sprint. **They are no longer sample-blocked** — a
second forward from Travis Fair and Haris Brka (2026-08-07) took the corpus from 4 to 8:

| Handler | Share | Samples in hand |
|---|---:|---|
| Extension | 60% | ✅ one success + one failure |
| New | 21% | ✅ two successes + one failure |
| Supplement | 15% | ✅ two successes |
| Update | 3% | ✅ one (`new_uwis0071_existing_ok.json`) |
| Adjustment | 0% | ✅ one — the manual-fallback case |
| Transfer | 0% | ❌ zero (routes to manual regardless, §5) |

Two corrections that batch forced, both carried into `XRAS_REIMPLEMENTATION.md`:

- **"Update" is not an `actionType`.** It is the handler legacy selects for `New` or `Renewal`
  when the project already exists, so New and Update are one dispatch decision.
- **XRAS sends `Adjustment`, legacy compares against `Adjust`** — so legacy's Adjustment handler
  has never fired (defect 4, §9). SAM treats the spellings as synonyms via
  `XRAS_ACTION_TYPE_ALIASES`; the audit column still stores the wire value verbatim.

Also out: SMTP (above), the GET cutover steps 1-3 (independent — Phase 1 is done and can
deploy in parallel), and the `POST /actions` cutover itself, which additionally needs the
400/422 error-contract change confirmed with `allocations@access-ci.org` (§9).

Still open on the wire contract: **no co-PI role has ever appeared in a sampled payload**,
so whether `roleType` is `'Co-PI'` or `'CoPi'` is unknown — still true across all eight.
`Transfer`, `Renewal` and `Advance` also remain unsampled. One bulk forward closes them.

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

## Schema deltas

**This section is the deliverable.** When the sprint ends it becomes the DDL in the DBA
ticket. Every entry is either a change that was made or a change that was deliberately
*not* made, with the reason — a considered "no" is as load-bearing as a "yes", because the
next person will otherwise re-open it.

Amended **up front**, not at the end: the table was empty and re-seeding is a curl loop, so
amending after seeding would have meant seeding twice. `zz-90-xras_action_log.sql` and
`XrasActionLog` moved in the same commit; `test_schema_validation.py` is green (21 passed).

### Applied

| # | Change | Reason |
|---|---|---|
| 1 | **`http_status SMALLINT UNSIGNED` added** | `status='failed'` covers both a malformed body (400) and a schema rejection (422). Triage needs to tell them apart, it is a filter axis on the operator page, and it stops being derivable from `status` the moment handlers add their own validation failures. Set on all four route paths. |
| 2 | **`KEY xras_action_log_triage (status, action_type)` added** | The natural triage axis and the table's default filter — "failed New actions" *is* the 55% failure cohort (§1.3). The standalone `status` index is **kept**, not replaced: the summary strip and `sam-admin xras --summary` group by status alone. |
| 3 | **`received_time DEFAULT CURRENT_TIMESTAMP` dropped** | The default resolves in the *MySQL server's* timezone (UTC in the containers) while SAM is naive-Mountain — exactly the 6-hour inversion Sprint A fixed by stamping from the app clock. Keeping it as a "safety net" was backwards: it made a hand-written `INSERT` that forgets the column lie quietly instead of failing loudly. |

### Considered and deliberately declined

| Candidate | Decision |
|---|---|
| **Restructure `error_messages`** (child table / JSON, per-field or retryable flags) | **No.** The 422 body is a flat, ordered list of strings and that list *is* the wire contract — XRAS admins read it directly. Per-error structure would invent a shape XRAS never sends, and the ordering (which legacy gets from a `LinkedHashSet`) is the only structure that carries meaning. Rendered as a `<ul>` by splitting on `\n`. |
| **A column recording what a handler changed** (e.g. a link to `allocation_transaction`) | **Deferred, with the escape hatch named.** Nothing writes it yet, and designing the link before any handler exists is designing against nothing — it would encode a guess about handler shape at exactly the moment we have least information. The out is the same one the `XrasActionLog` docstring already names for `payload_json`: a nullable additive column, backfillable from `raw_payload` + `projcode_result`, not a migration. Revisit in the handler sprint, when there is a real write to point at. |
| **Widen `remote_actor` to hold a username** | **No.** A replay row keeps the *original's* `remote_actor` — the bytes still originated at XRAS — and records the human in `processed_by` (`varchar(35)`, `users.username` width, already present). `remote_actor` stays `varchar(11)` and stays honest about meaning "which API credential posted these bytes". |
| **A `status` ENUM instead of `varchar(16)`** | **No.** The rest of this schema uses `varchar` for such columns, and the five values are still moving — `processed` is unvalidated until handlers land. An ENUM change is a DBA ticket; a string is not. |

### Applied — the second table

**`xras_activation_event`** is now built, not merely decided. The pending-activation
card was read-only and fully derived; giving it Notify / Activate / Dismiss /
Comments needed state SAM recorded nowhere, and a second DBA request costs another
round of external lead time — so the schema was settled while the design was fresh
and then implemented in the same PR, which is what proved the shape before
production commits to it.

Full DDL, the rejected alternatives, and the timestamp rule that makes it both the
anti-spam and the re-open mechanism: **[`implemented/XRAS_SPRINT_B_FOLLOWUP.md`](implemented/XRAS_SPRINT_B_FOLLOWUP.md)**.

One change from the DDL recorded there, made while building:

| # | Change | Reason |
|---|---|---|
| 4 | **`event_type` vocabulary gained a fifth value, `restored`** | Undo for a dismissal. The hide rule made a dismissed project invisible until a new XRAS action arrived, which can be never — leaving Flask-Admin (off in prod) or a DBA as the only recovery from a misclick. In an append-only table the undo is a *superseding event*, not a DELETE, so the mistake and its correction both stay on the record. **Free**: the column is a bare `VARCHAR(16)` with no `ENUM` and no `CHECK` by design, so the application constant is the only enforcement point and a fifth value costs no DDL. The hide rule gains one term: `latest('dismissed') > MAX(latest_action, latest('restored'))`. |

⚠️ **The ticket carries both init scripts** — `zz-90-xras_action_log.sql` and
`zz-91-xras_activation_event.sql`. Filing only the first is the mistake this
section exists to prevent.

### Not a schema delta, but found while doing this

**`make docker-build` never rebuilt `mysql-test`.** The target was `docker compose build`
with no `--profile test`, while `docker-up` *does* pass it — so the profile-gated
`mysql-test` service started from whatever image it was last built from (here: one from
July 11, predating `initdb.d/` entirely). `down -v` does not help; it drops the volume, not
the image.

The failure mode is nasty because it is silent and asymmetric: the amended DDL appeared in
`mysql` and not in `mysql-test`, i.e. everywhere *except* where pytest looks. **Fixed in the
`Makefile`** — `docker-build` now passes `--profile test`, so build and up are symmetric.

This also corrects the recipe in § *The decision that reorders everything* above, which
cannot work as written on a machine whose `mysql-test` image predates the change:

```bash
docker compose --profile test down -v && make docker-build && make docker-up
```

is now correct, but only because `make docker-build` changed. Verify with
`docker compose exec mysql-test ls /docker-entrypoint-initdb.d/` if in doubt — the
`zz-90-` file must be present in **both** containers.

---

## Porting notes — what another site would have to change

Asked during review: is the `NCAR####` "not a project yet" placeholder hardcoded?
**No.** An audit of `src/` found *zero* behavioural coupling to the pattern — no
regex, no `startswith`, no `LIKE 'NCAR%'`, no validator, no generator. Every
mention was a docstring, plus one `placeholder=` attribute.

The reason matters, because it constrains any future "improvement":
`get_recent_xras_actions` decides whether a `request_number` is a projcode by
**asking the database** (`_annotate_project_existence`, one `IN` query per page),
and it has to. Measured against real data, projcodes are `AAAA####`
(`UCUB0166`, `UBOI0007`, `NACD0009`) and `NCAR4232` is the *same eight-character
shape* — so no prefix or shape rule can separate a request token from a projcode,
and a site holding a projcode beginning `NCAR` would have it misclassified
outright. `test_a_projcode_shaped_like_a_request_token_is_still_a_project` fails
against any prefix-matching implementation; that is its whole job.

What *is* named, in `sam/queries/xras_actions.py` beside the other vocabulary
constants:

```python
XRAS_REQUEST_TOKEN_PREFIXES = ('NCAR',)   # a family; startswith takes a tuple
XRAS_REQUEST_TOKEN_EXAMPLE  = 'NCAR4253'
```

**Display only.** They drive the filter box's placeholder, the seed script's
sample body, and one triage distinction the DB lookup cannot make on its own:
an unresolvable request number that *looks like* a token is a New action whose
project does not exist yet (normal), while one that does not is an Extension
naming a projcode SAM has never had — deleted, renamed, or a mis-sent payload,
and worth a second look. Those two used to render identically.

### The real site-locks are elsewhere, and they are not in Sprint B's code

Recording, not fixing — this is Phase 1 territory and its own change. The
`/api/xras/v1/people` roster in **`sam/queries/xras_access.py`** is markedly more
site-bound than anything above:

| Where | What |
|---|---|
| `:76`, `:111-117`, `:174-175` | `'UCAR/NCAR:'` sentinel + `_PRIMARY_ORG_NAMES` + the parentage walk hardcode UCAR's organisational hierarchy |
| `:93,95` | `AND NOT (ea1.email_address LIKE '%ucar.edu%')` — a site's mail domain buried in raw SQL, and **not currently flagged as site-specific anywhere** |
| `:68-73` | `'Ucar Office'` / `'External Office'` phone-type literals |
| `:102` | `WHERE u.login_type_id = 1` — a magic reference-data id (documented at `:51` as deliberate legacy fidelity) |

Portable as-is, for the record: `XRAS_ROLE = 'ROLE_XRAS'` is the XRAS *protocol's*
authority string rather than a site value; `remote_actor` is derived from
`api_credentials.username` at request time, never hardcoded; and resource mapping
is table-driven through `xras_resource_repository_key_resource`.

---

## Deviations from this plan, as built

Recorded per the house rule that a plan is input, not contract. Each of these was a
deliberate departure, not a slip.

### 1. Two permissions, not one

The plan asked whether viewing needs `MANAGE_XRAS` at all. Answer: **`VIEW_XRAS` +
`MANAGE_XRAS`**, split on *what the data is* rather than on read-vs-write:

| | Covers |
|---|---|
| `VIEW_XRAS` | the page, the table, the filters, the error lists |
| `MANAGE_XRAS` | the **raw payload** and the replay button |

The payload is the request body verbatim and carries participant names, emails, phone
numbers and grant-officer contacts. Gating it with the audit view would have handed PII
to everyone holding `ALL_VIEW`; gating the whole page behind `MANAGE_XRAS` would have
hidden an audit surface from the people who read every other audit table.

Two consequences worth knowing:

- **`VIEW_XRAS` is deliberately swept into `ALL_VIEW`** (the aggregates prefix-match
  `p.value`), so `nusd`, `csg`, `ssg` and `mcjones` get the page for free. That is the
  intent, not an accident.
- **The payload gate is enforced in the route, not only the template.** A
  `VIEW_XRAS`-only response never contains `raw_payload` at all, because the route only
  asks the query layer for it when the viewer holds `MANAGE_XRAS`. A template-only gate
  leaves the bytes in the HTML for anyone who opens view-source.
  `test_view_only_user_gets_the_locked_notice_and_no_bytes` pins it.

Nothing was added to `USER_FACILITY_PERMISSIONS`, with the reason written in a comment
there: an XRAS action is **not facility-scopable**. It arrives before we know its
facility (a New action has no project yet, only a `requestNumber`) and a malformed body
has none at all. Routes therefore use plain `require_permission`, so a facility-scoped
manager gets a clean 403 rather than a partial, misleading view.

### 2. Seeding is not replay, so Unit 3 did not have to come first

The plan argued replay must be built first because "seeding *is* replay". It is not —
seeding posts fixture payloads through the capture endpoint, which is a **fresh post**
and needs no new code. Order actually built: schema → permissions → queries → page →
replay → CLI.

What seeding *did* need was a credential, which the plan missed entirely — see item 6.

### 3. Replay honours `XRAS_ACTIONS_CAPTURE_ONLY` instead of bypassing it

The plan said replay of anything currently lands in `manual`, implying replay dispatches
regardless of capture mode. **Rejected**, and this is the most consequential deviation.

Capture mode is on because **legacy is still the system of record** until cutover step 4
— it is already applying these actions. A replay that dispatched while capture was on
would apply an action legacy has already applied: a double-apply against live
allocations, one button click away, with no undo.

So under capture mode a replay re-parses and re-validates the stored bytes against the
*current* schema code and lands `replayed` (or `failed`, with a fresh error list). That
is not a consolation prize — it is a **regression check of the schema against the
harvested corpus**, which is what the corpus exists for, and it is exactly what
`sam-admin xras --replay 6` demonstrated by re-rejecting a payload that had failed
validation weeks earlier. With `XRAS_ACTIONS_CAPTURE_ONLY=0` replay dispatches and lands
`processed` / `manual` / `failed` like a fresh post.

The kill switch stays the single safety interlock; a replay-specific override would mean
two things to reason about and one of them would eventually be wrong.

**The original row is never stamped.** Setting the parent's status to `replayed` would
destroy its own outcome, which *is* the audit record. "Has been replayed" is derived from
the `replays` relationship, which was already first-class.

### 4. `nav.py` also needs the tab

The plan listed only `page_tabs` in `base_allocations.html`. `src/webapp/utils/nav.py`
is a second, independent registry driving the navbar dropdown, the mobile offcanvas and
breadcrumbs. Both were updated, each with its own visibility predicate.

### 5. The row-detail interaction, and the trap under it

Errors expand **inline**; a separate button opens the shared audit modal. The load-bearing
detail: **the collapse toggle is on the Errors `<td>` alone, never the `<tr>`.**

Bootstrap registers its collapse data-api with `EventHandler.on(document, …)`, which runs
in the **capture** phase — so a row-level toggle fires *before* any nested button's
handler, and `data-stop-propagation` is powerless against it by construction. This row
carries four interactive controls (two project-modal buttons, details, replay), so a
`<tr>` toggle would have swallowed every one of them. Rows with no errors get no toggle
at all, because a chevron that expands to nothing reads as broken.

### 6. Two things the plan did not know about

**`make docker-build` never rebuilt `mysql-test`** — see § *Schema deltas*. Fixed in the
`Makefile`. This one is nasty because it is silent and asymmetric: the amended DDL
appeared in `mysql` and not in `mysql-test`, i.e. everywhere except where pytest looks.

**The day-one seeding recipe cannot work as written.** `POST /actions` authenticates
against `api_credentials` rows carrying `ROLE_XRAS`, and the obfuscated snapshot ships
that table **empty** — credentials are scrubbed, correctly. Config-based `API_KEYS_*`
cannot substitute: a config-sourced key resolves to `roles=[]` and `xras_api_required`
demands `ROLE_XRAS`, so it authenticates and then 403s. The curl loop in § *Day one*
therefore returns 401 on a fresh stack.

Packaged as **`scripts/xras/seed_dev_actions.py`**, which provisions the credential
idempotently and then posts:

```bash
source etc/config_env.sh
docker compose up webdev --watch          # in another terminal
python scripts/xras/seed_dev_actions.py --errors --pending-demo
```

`--errors` also posts a malformed body (400) and a rejected one (422), so four of the
five states exist without hand-crafting anything. `--pending-demo` deactivates one
XRAS-touched project so the pending-activation card has a row — both real Extension
payloads name projects that are `active = 1` in the snapshot, so the card is correctly,
but unhelpfully, empty otherwise.

### 7. The pending-activation card cannot retro-discover legacy XRAS projects

**There is no provenance marker on `project`.** Nothing in `sam/projects/` records where
a project came from, and `XrasActionView` is an *outbound* reporting view derived from
allocations, not a record of inbound posts. So the card's rule is necessarily "a project
named by some `xras_action_log` row — via `projcode_result` or `request_number` — that is
currently inactive".

It therefore sees only projects **this log** knows about: empty today, growing as the log
grows, and never retroactively covering the 23 historical XRAS projects legacy created.
The empty state says so in as many words, because "no rows" must not read as "all clear"
while capture mode is on.

### 8. `request_number` is not always a projcode, and the UI has to know

`request_number` is the projcode for Extension/Supplement/Update and an `NCAR####` token
for New. Linking every one of them to a project modal would 404 on precisely the 21% of
traffic with the worst failure rate. `get_recent_xras_actions` therefore resolves
`request_is_project` / `result_is_project` in one extra `IN` query per page, and the
template links only what resolves.

### 9. Small fixes made along the way

- **`_record` width guards.** On the 422 path `action_type` / `request_number` come
  straight off an *unvalidated* payload dict, so an over-long or non-string value would
  turn the audit write into a 500 — losing precisely the row the table exists to keep.
  Now coerced and truncated (`_fit`).
- **`replay.py` imports `actions` as a module, not by name.** `from .actions import
  _record` binds at import time and would sail straight past the `action_log` fixture's
  monkeypatch, leaking committed rows into the shared xdist database. Every call goes
  through the module attribute.
- **The `action_log` fixture teardown deletes in descending id order**, one PK-targeted
  statement each. `replay_of_id` is a self-FK, so a single `IN (...)` delete gives InnoDB
  no ordering guarantee and fails with `1451 Cannot delete or update a parent row`. A
  replay is always inserted after the row it replays, so descending id removes every
  child before its parent, to any chain depth — while staying PK-targeted, because a
  range predicate would gap-lock and deadlock under `-n auto`.
- **The CLI redirects `create_app()`'s startup chatter to stderr.** `--replay` needs a
  Flask app context (`_record` commits on its own connection; `_capture_only` reads app
  config), and app construction prints diagnostics to stdout, which is the CLI's result
  channel.

### 10. The activation worklist — deviations from `XRAS_SPRINT_B_FOLLOWUP.md`

That handoff doc is input, not contract. Six departures, each deliberate:

1. **Notify is record-only, and says so in a dialog.** The doc recommended a
   `mailto:` (confirm before building); Ben chose neither `mailto:` nor SMTP. The
   button does the entire timestamp half — which is what the badge, the staleness
   rule and "Notify again" derive from — and answers with an explicit
   **"Email delivery is not implemented"** modal listing the recipients, rather
   than a success toast that would imply mail moved. **SMTP is a separate
   follow-on PR**; when it lands the same button sends server-side and the schema
   does not change.
2. **`Project.reactivate()`, not a symmetric `update()`.** The doc floated making
   `update()` stamp `inactivate_time` on `active=False` and clear it on
   `active=True`. That is actively wrong here: the admin Details tab loads with
   `partial=True`, and a partial load **skips `load_default` entirely** (verified
   against the installed marshmallow), so `active` reaches the update dict only
   when the checkbox is *checked*. A symmetric `update()` would therefore clear
   `inactivate_time` on every save of any already-active project — destroying a
   historical stamp on an unrelated edit — while the stamping half never fired
   from the web at all. The CLI assigns the column directly and Flask-Admin
   bypasses `update()`, so it would not have been an invariant either.
   `test_update_active_true_leaves_the_stamp_alone` is the guard rail.
3. **A fifth event type, `restored`, plus a "Show dismissed" toggle** — see
   § *Schema deltas*. ⚠️ The toggle lives in `xras.html`, **outside**
   `#alloc-xras-pending`: the card fragment renders a table *or* an empty state,
   so a toggle inside it would vanish exactly when every row was dismissed, which
   is the one case it exists for. The container carries
   `hx-include="#xras-pending-controls"` so `refreshXrasTab` preserves it.
4. **`sam/schemas/forms/xras_activation.py` is its own module.** `forms/xras.py`
   is the one module in that package that is deliberately *not* `HtmxFormSchema`,
   with a 39-line docstring explaining why. Putting a snake_case form schema there
   would put two families with opposite base classes and opposite empty-string
   semantics behind one name.
5. **Writes run inside `management_transaction`** — the opposite of `replay.py`
   one screen away, and each route docstring says so. A replay's audit row must
   survive a handler rollback ("we received this even though processing it blew
   up"); an activation event is the inverse — it records a *decision*, and since
   the card's state is derived from these events, an `activated` row that outlived
   its own effect would make the card lie.
6. **`xras_history` is gated on `MANAGE_XRAS`, not `VIEW_XRAS`.** Its timeline
   surfaces `notified_to` — project lead/admin contact detail, the same category
   the raw-payload gate exists for. Recipients are likewise fetched in the *route*
   only for `MANAGE_XRAS`, so a `VIEW_XRAS` response never carries an address.

**Two defects only the browser pass caught**, both invisible to the unit tier and
both worth knowing:

- **`fmt_ago` takes a `timedelta`, not a `datetime`.** Passing a timestamp raises
  `AttributeError: 'datetime.datetime' object has no attribute 'total_seconds'`
  *at render time*. The unit tier could not see it — the pending-fragment render
  test runs against a snapshot with no activation events, so the badge that calls
  the filter never drew. Ages are now computed in the query layer as deltas
  (`notified_age`, and `age` per timeline event) rather than subtracting in Jinja.
  Note a just-written event can read *slightly negative*: `creation_time` carries
  microseconds and MySQL DATETIME **rounds** rather than truncating, so the row
  can land up to ~0.5 s ahead of a `datetime.now()` taken moments later.
  `fmt.ago` clamps at zero, and a test pins the tolerance.
- **`{% from ... import %}` without `with context` silently swallows form errors.**
  The `form_fields.html` macros read `field_errors` and `form` out of the template
  context; a plain import gives them neither, so a rejected submission re-rendered
  looking untouched — no message, no preserved input. Every other call site in the
  repo already says `with context`. The regression test asserts on the *rendered*
  `invalid-feedback` block, because matching the word "required" alone is a false
  positive: `required=True` puts a literal `required` attribute on the textarea.

### 11. Filter macro: a sibling, not an extension

`xras_filters` is a new macro rather than four more optional parameters on
`audit_filters`. The two share the panel chrome **verbatim** — the same
`.filter-sidebar-*` header, collapse behaviour, `form-reset-submit` reset and control
styling — but not one field: `audit_filters` offers Project / Responsible user /
Resources / Facilities, none of which an XRAS action has.

That chrome block is now copy-pasted in **five** places. It is a fair candidate for a
`filter_panel_shell` macro; extracting it is a five-template refactor and was
deliberately left out of a feature change.

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
