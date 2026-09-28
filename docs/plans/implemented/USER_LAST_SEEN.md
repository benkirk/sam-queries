# User last-seen ledger (`system_status.user_last_seen`)

**Status:** shipped in #642 (main via #643, image `sha-4168d5e`, 2026-09-27); live in
production with all four sources, backfilled to 2011. The approved plan is kept
below as written; this header records what was built, how it rolled out, and what
is left.

## As built -- deviations from the plan

| Plan said | Built | Why |
|---|---|---|
| Webapp sightings via a `flask_login.user_logged_in` signal handler | `_record_login_seen()` called at the two real login sites (stub POST and OIDC callback) in `src/webapp/auth/blueprint.py`, on its own status-bind session | Admin impersonation (`dashboards/admin/blueprint.py`) and dev auto-login also call `login_user`; a signal would record an admin impersonating X as X being seen |
| Schema sketch had `system_id NULL` for webapp | `system_id NOT NULL`; webapp logins use a `samuel` row in `systems` | The plan's own recommendation; avoids NULL-in-UNIQUE duplicates on MySQL and Postgres |
| Seed source rows in the migration | No seed rows; `get_or_create_source()` at first use | Matches the other status lookups, and keeps the migration pure DDL so it could land ahead of the code |
| `--dry-run` by default | `--dry-run` is an opt-in flag | That is the actual `sam-admin` convention (`project --reconcile-lead-admin --dry-run`) |
| Explicit machine -> system dict | One rule: `machine.lower().removesuffix('-gpu')` (`sam/queries/last_seen_backfill.py`); the dry run prints every mapping for review | Prod has case variants (`Caldera`/`caldera`, `Geyser`/`geyser`) the rule merges for free; nothing needed an override |
| Payload fields optional in the snapshot schemas | `login_users` / `users` are popped before the snapshot schema loads and validated by their own schemas (`LoginUsersSchema`, `JupyterHubUserSchema`) | The snapshot schemas use `load_instance=True`; the new fields are not model columns |
| (not in plan) | JupyterHub `last_activity` is clamped to the snapshot time | A skewed future timestamp would pin `last_seen` ahead forever under a monotonic upsert |
| (not in plan) | Ledger writes run in a savepoint on ingest; a failure is logged and the snapshot still commits | The ledger is secondary to the status snapshot |

## Rollout, as it happened (2026-09-27)

1. Local MySQL `system_status` upgraded to 0007; backfill (6,749 rows, rerun
   idempotent) plus live Derecho, Casper and JupyterHub collector posts to webdev.
   Upgrade/downgrade/upgrade and the upsert semantics were also verified on scratch
   MySQL (3307) and Postgres (5434) databases.
2. samuel-dev (`system_status_dev`), then prod (`system_status`) upgraded by hand
   **before** the PR, so the merge was code only. Both already grant `pguser`
   through default privileges; no manual GRANT was needed.
3. #642 merged to staging, promoted in #643. Prod collectors were already sending
   `login_users` / `users` at the first post-deploy tick (23:20Z).
4. Prod backfill via `sam-admin last-seen --backfill` (dry run first): about 20 s,
   13 systems, 22,015 rows, 10,122 distinct users, oldest first_seen 2011-10-06
   (janus). Last-seen buckets at the time: 978 within a day, 2,290 within a year,
   2,119 at 1-3 years, 4,735 older than 3 years.
5. A prod webapp login wrote the `(webapp, samuel)` row.

Useful facts learned on the way:

- `make migrate-status-*` re-sources `.env` and clobbers exported `STATUS_DB_*`;
  run `alembic -c migrations/system_status/alembic.ini ...` directly for dev/prod.
- A fresh MySQL 8 status database needs `COLLATE utf8mb4_unicode_ci`, or migration
  0002 fails on a collation mix. Pre-existing, unrelated to 0007.
- `alembic check` reports column-comment drift on `login_node_status` and
  `user_proj_queue_status` on MySQL and Postgres. Pre-existing, unrelated to 0007.
- `pbs` is the source kind for every backfilled system, including the LSF/Slurm
  era (Yellowstone, Geyser, Caldera): read it as "ran a batch job".
- `yslogin` (117 users) was kept as its own system rather than folded into
  `yellowstone`.

## Future work

Nothing below is committed to; it is the list to pick from.

- **Read side**: sketched below. The user card section and the Admin → Users & Groups
  → Last seen page are built (`webapp/dashboards/admin/last_seen_routes.py`, the join
  in `sam/queries/last_seen_review.py`). Recency is a chip strip of buckets (<30d,
  30d–1y, 1–3y, >3y, never) rather than window pills, and times show in UTC because
  backfilled rows hold a Mountain date at 00:00. Still open: the CLI half and the xlsx
  export.
- **Deactivation.** Feed the ledger into account-deactivation review, e.g. alongside
  the `deactivate_expired` task, as evidence rather than a trigger.
- **Webapp "last used", not just "last login".** SSO sessions last days. Scrape the
  usernames the webapp already writes to stdout on a schedule and apply them to the
  same `(webapp, samuel)` source through `record_seen`; no per-request hook.
- **samuel-dev collector posts.** Dev's ledger only gets webapp rows until
  collectors post to dev (dev has its own `collector` key; `RBAC_SOURCE=db` also
  needs the `api_collector` grant there).
- **Finer backfill.** Where jobhist (hpc-usage-queries) is installed, replace the
  day-granular backfilled `last_seen` with real job end times.
- **Committed dialect test.** The MySQL/Postgres upsert branches were verified on
  scratch databases, not in CI; a test against the mysql-test / postgres-test
  containers would pin them.
- **Housekeeping found on the way.** A small migration to sync the pre-existing
  column comments `alembic check` reports, and a fix for migration 0002 on a fresh
  MySQL 8 database.

### Sketch: read side (CLI + UI)

This is a sketch, not a plan. The main constraint: the ledger lives in `system_status`
and users live in SAM, so every view makes two queries joined by username in Python,
never a cross-database SQL join. Both directions are cheap: the ledger is about 22k
rows, and `ix_user_last_seen_source_id_last_seen` serves the "since T" filter.

**CLI**

- `sam-search user X` (and `--verbose`): one "Last seen" line giving the most recent
  source and time, e.g. `2026-09-27 23:40 UTC (webapp)`. `--verbose` shows the full
  per-source table that `sam-admin last-seen X` prints today. It uses
  `get_last_seen()`, and the JSON envelope gains a `last_seen` list.
- `sam-search user --not-seen-since 3y` / `--seen-since 30d`: active SAM users
  whose MAX `last_seen` across sources is older (or newer) than the window. "Never
  seen" (no ledger row) counts as older. It pairs naturally with `--abandoned`,
  e.g. `--abandoned --not-seen-since 1y` is the strong-candidate list. Duration
  parsing can reuse `_parse_last_spec` (`src/cli/accounting/dates.py`), which
  `accounting --last 7d` uses; it accepts days only today, so it would need `y`/`m`
  added.
- Optional `--source login|pbs|jupyterhub|webapp` to narrow either of the above.
- `sam-admin last-seen` already exists; it stays the operator and backfill surface.

**User card / modal** (`admin_dashboard.user_card`, which serves both the Users &
Groups card and `#userDetailsModal`, so one change covers both)

- A compact "Last seen" block: one row per source kind, showing system, `fmt_ago`
  and the date, with the newest first. It shows "never seen" when there are no rows.
  Retired systems (cheyenne, yellowstone) sit in the same list, since that history
  is the point.
- Gate it on `VIEW_USERS`, the same as the card. It reads the `system_status` bind,
  so a status DB outage must degrade to "unavailable" rather than breaking the card.

**Admin list view** (Admin → Users & Groups, a new card or tab)

- A "Last seen" table of users sorted by last seen: username (opening the user
  modal), name, SAM active/locked, most recent source, and last seen (`fmt_ago`).
  Optionally show the number of active projects, so a dormant user who still holds a
  project stands out.
- Filters: a window pill (`30d / 1y / 3y / never`), a source kind, and "active SAM
  users only" (default on, via `read_active_only`). It uses the shared filter and
  search-box macros, and `?` params make it deep-linkable.
- Paginate on the status-DB side, then fetch those usernames' SAM rows in one
  `IN (...)`. "Never seen" runs the other way (SAM users minus ledger usernames), so
  it may want its own query path.
- An xlsx export through `sam/export/` is the natural next step for deactivation
  review.
- Load `wire-dashboard-feature` before building either UI piece.

---

## The plan (as approved)

### Context
Ben wants a cheap way to answer "when was this user last seen, and where?", covering
webapp login, login nodes, PBS and JupyterHub. The main uses are dormant-account
detection (next to `sam-search user --abandoned` and deactivation) and support
triage. Nothing like this exists today. SAM `users` has no last_login column, and
we don't change the SAM schema. The only per-user activity we store is the
`user_proj_queue_status` spans, and those are pruned after 365 days.

### Verdict on the proposal
Two tables, a normalized lookup plus a latest-only ledger with a unique key on (user, source),
is the right shape. I'd change four things:

1. **Name it `user_last_seen`, not `..._history`.** It keeps one row per (user, source),
   overwritten in place. It is not a history. Real history already exists in jobhist
   and the UPQ spans.
2. **Make the composite key the primary key.** Use `PRIMARY KEY (user_id, source_id)`
   with no surrogate id. The PK is the uniqueness constraint and also the lookup
   index for "everything about user X".
3. **Scope a source by system.** Seeing someone on the Casper login nodes and seeing
   them on the Derecho login nodes are different facts. A source is `(kind, system)`,
   and `system` reuses the existing `systems` lookup (`models/lookups.py:21`).
4. **Reuse `status_users` (`UserDef`)** for the user FK. The status DB already keys
   users by username string, with no FK to SAM, and joins to SAM by name
   (`lookups.py:109`).

### Schema (Alembic `0007_user_last_seen`, style of `0006_task_run.py`)
```
access_sources
  source_id    INT PK autoincrement
  kind         VARCHAR(32)  NOT NULL   -- 'webapp' | 'login' | 'pbs' | 'jupyterhub'
  system_id    INT NULL FK systems     -- NULL for webapp
  UNIQUE (kind, system_id)             -- uq_access_sources_kind_system

user_last_seen
  user_id      INT FK status_users   \
  source_id    INT FK access_sources /  PRIMARY KEY (user_id, source_id)
  first_seen   DATETIME NOT NULL       -- naive UTC; costs nothing, answers "new user?"
  last_seen    DATETIME NOT NULL       -- naive UTC, monotonic (see writes)
  INDEX (source_id, last_seen)         -- "who used X since T", "dormant on X"
```
- Leave out a hit counter. It would turn every observation into a real row change
  and its meaning depends on how often we collect. Leave out an IP/host column too;
  if that turns out to be needed, it's a history concern.
- `NULL` in the `UNIQUE(kind, system_id)` key makes duplicates possible on MySQL and
  Postgres (NULL ≠ NULL). The fix is to seed the rows in the migration and resolve
  them through a get-or-create keyed in Python. Alternatively, give webapp its own
  `systems` row, `samuel`, and make `system_id` NOT NULL. **Recommend the second
  option**: it's simpler and has no NULL trap.
- **Never purged.** A user last seen 3 years ago is exactly the answer this table
  exists to give. Its size is bounded by users × sources, so there is nothing to prune.
  - Keep both this table and `status_users` out of `retention.SNAPSHOT_TABLES` and
    `RETENTION_DAYS`.
  - Add a gate test asserting neither table ever enters them. Retention is written
    as a list someone will someday "complete", so the gate is what stops that.
  - Say "never purged" in the model docstring, pointing at the gate.

### Writes: one bulk monotonic upsert helper
There's no upsert helper in the codebase today. Add one in `system_status/queries/`
that dispatches through `sam/sqlcompat.py:dialect_name`:
- Postgres / SQLite (the test tier): `insert().on_conflict_do_update(...,
  where=t.c.last_seen < excluded.c.last_seen)`
- MySQL: `on_duplicate_key_update(last_seen=func.greatest(t.c.last_seen, inserted.last_seen))`

Two rules for the helper:
- **Monotonic.** A late or replayed collector POST, or a backfill, can never move
  `last_seen` backwards (`GREATEST`) or move `first_seen` forwards (`LEAST`).
- **Batch writes.** Every source goes through one bulk statement per ingest.
  `first_seen` is set only on insert.

The timestamp is **the observation's own time**, not the server's `now()`:
- the snapshot `timestamp` for PBS and login nodes;
- JupyterHub's per-user `last_activity`, which the hub API already returns.

Volume is about 1–2k rows per 5-minute tick, which is trivial.

### Where each source comes from
| source | change needed |
|---|---|
| pbs (derecho, casper) | None on the collector side. Derive it in `_ingest_system_status` (`webapp/api/v1/status.py:144`) from the `user_project_queues` rows already parsed from `Job_Owner`. |
| jupyterhub | The collector already builds the username set (`collectors/jupyterhub/collector.py:95`). Add a `users: [{name, last_activity}]` field to the payload and its schema. |
| login (derecho, casper) | Use `ps`, not `who`, so it covers processes without a tty or utmp entry (see below). Add a users list next to the existing count (`collectors/lib/ssh_utils.py:111`). The collector unions the users from every login node into one set per system. |
| webapp | A `flask_login.user_logged_in` signal handler catches both the OIDC path and the stub path (`auth/blueprint.py:88,185`). It must fail open: a status-DB hiccup can never block a login. |

**Login nodes via `ps`:** `who` reads utmp, so it misses VS Code Remote, non-interactive
`ssh host cmd`, scp/sftp, and detached tmux or screen sessions. `ps` sees all of them.
- Command: `ps -eo uid=,user:32= | sort -u`. The widened `user:32` column matters:
  the default `user` column prints a numeric uid for names longer than 8 characters.
  Keep rows with `uid >= 1000` (tune this to NCAR's system-account range) to drop
  root, daemons and service accounts.
- Leave the existing `who`-based count in `login_node_status` untouched, because
  changing its source would redefine a metric that's already charted. The user set
  is a new payload field.
- Semantics: this measures *presence* ("has a live process"), not activity. A tmux
  session left running for weeks keeps `last_seen` at now. That's acceptable for
  dormancy detection, because a user with live processes isn't dormant.
- Payload size: the distinct users per system, a few hundred usernames every 5
  minutes. Cost: one extra `ps` per node over the SSH connection the collector
  already opens.

**Webapp, v1 = login only.** This records *login*, not *use*, and SSO sessions can
last days. That is acceptable for now. A later phase can recover "last used" by
periodically scraping the usernames the webapp already writes to stdout, feeding the
same `(webapp, samuel)` source through the same monotonic upsert. That needs no
per-request hook in the app.

### Backfill from PBS history (part of v1)
Rollout should start with years of dates, not an empty table.

**Source: SAM's three job charge summaries.** `comp_charge_summary`,
`dav_charge_summary` and `hpc_charge_summary` (`sam/summaries/comp_summaries.py:27`,
`dav_summaries.py:11`, `hpc_summaries.py:9`) have the same shape: one row per user,
machine and day, with `username`, `act_username`, `machine` and `activity_date`.
Together they reach back through Cheyenne and earlier.
- Run one aggregate per table: `SELECT COALESCE(username, act_username), machine,
  MIN(activity_date), MAX(activity_date) GROUP BY 1, 2`.
- Union the three results in Python, merging MIN/MAX per (user, machine). Tables
  overlap where a machine was charged through more than one of them. The monotonic
  upsert would merge them correctly anyway, but merging first keeps it to one write
  per key.
- Use three separate queries, not one `UNION ALL`, so each shows its own row count
  and timing in the dry-run report.
- Skip rows with no username at all, and report how many were skipped.
- These tables are always present, unlike jobhist (the hpc-usage-queries plugin),
  which is optional and has a shorter horizon.

**Retired machines get their own `systems` row**, e.g. `cheyenne`.
- Their last-seen dates are the whole point: a user whose last job ran on Cheyenne
  in 2023 is exactly the dormant user we want to find.
- Adding these rows is safe. I checked that nothing enumerates `systems`; every
  reader filters by name.
- The mapping from SAM `machine` name to status `systems.name` is an explicit dict.
  Unmapped machines are reported, not silently dropped.

**Precision.** Summaries are per day, in Mountain time. Store the date at 00:00, and
note in the docstring that backfilled rows are day-granular. Live observations
supersede them through the monotonic upsert.

**Running it.**
- One-shot, idempotent command: `sam-admin` or a status CLI subcommand, `--dry-run`
  by default in the house style.
- It reads SAM, then writes the status DB through the same upsert helper. Because
  the upsert is monotonic, running it again, or after live collection has started,
  never moves `last_seen` backwards.
- `first_seen` takes the *earlier* of the stored and incoming values, so a backfill
  run after rollout still lowers it correctly. The upsert helper therefore needs
  `LEAST` on `first_seen` as well as `GREATEST` on `last_seen`.
- The GROUP BY is a full scan of large tables, so run it off-hours. It is one-time.

**Optional refinement:** where jobhist is installed, a second pass can replace
day-granular `last_seen` with real job end times. That can come later.

### Read side (first consumer, keep it small)
- `get_last_seen(username)` returns one row per source.
- `sam-search user X` could show a "Last seen" line. The `--abandoned` logic could
  use the MAX across sources later. The UI is a follow-on, not part of v1.

### Rollout order
The migration is purely additive: two new tables plus seed rows. The code running
today never touches them, so the schema can land ahead of the code.

1. **Local.** Run `alembic upgrade head` → `downgrade -1` → `upgrade head` on the
   local status DB, on **both** MySQL and Postgres (:5434), because the upsert
   branches differ by dialect.
   - Then run `alembic check`. An empty autogenerate diff proves the models and the
   migration agree. It matters more than usual here: the schema is frozen once prod
   has it.
   - Then point the local collectors at webdev (:5050) and watch rows land.
2. **samuel-dev, then prod.** Run `upgrade head --sql` as a dry run first, per
   `migrations/system_status/PROD_BOOTSTRAP.md`, then the real upgrade.
   - Ben runs these, and I stop and prompt at each step.
   - Old code keeps running unaffected.
3. **Open the PR, merge, deploy.** The merge is code only. What starts working right
   away:
   - PBS, derived from the UPQ payload collectors already send;
   - webapp login;
   - the backfill command, which is run once.
4. **Update the collectors later.** JupyterHub and login-node users light up when
   the collectors are updated.
   - The new payload fields are optional in the schema.
   - `BaseSchema` has `unknown=EXCLUDE` (`system_status/schemas/__init__.py:30`), so
     a new collector posting to an old server gets its extra fields silently ignored
     rather than a 400. The order is forgiving in both directions.
   - One detail: the schemas use `load_instance=True`. The new `users` field must be
     a plain, non-model field that the ingest code pulls out itself.

Trade-off of migrating before review: if review changes the schema, the fix is a
`0008` migration, not an edit to `0007`. Settle the (small) schema before step 2.

### Critical files
- `src/system_status/models/lookups.py` (new source lookup), with a new `models/user_last_seen.py`
- `migrations/system_status/versions/0007_user_last_seen.py`
- `src/system_status/queries/` (upsert helper), `src/sam/sqlcompat.py` (dialect dispatch)
- `src/webapp/api/v1/status.py`, `src/system_status/schemas/status.py`
- `collectors/jupyterhub/collector.py`, `collectors/lib/ssh_utils.py`, `collectors/lib/base_collector.py`
- `src/webapp/auth/` (signal handler)

### Verification
- Unit tests for the upsert on SQLite: insert, then newer, then older. `last_seen`
  never goes back, `first_seen` stays fixed, and a batch with a duplicate
  (user, source) pair works.
- Run the same test against the MySQL test container and against Postgres (:5434).
  That is where the dialect branches actually differ.
- An ingest test POSTs a fixture derecho payload and asserts that PBS and login rows appear.
- A login test checks that the signal writes a row, and that a raising status session
  still logs the user in.
- `alembic upgrade head` / `downgrade` on MySQL and Postgres.
