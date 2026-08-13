# Scheduled tasks: a ledger-backed dispatcher

**Status: P0–P4 BUILT** (PR #444, 2026-08-12). Drafted 2026-08-09, revised
2026-08-12. **P5** (clearing the production kill switch) and the § 12 stacked PR
remain open; § 9's watchdog and admin card and § 10's daemon are still *later*.

Where the build deviated from this document, the section says so — see § 3.1's
*as settled* block, § 4.2 on `trigger_type`, and § 15.

SAM has periodic work and no in-cluster place to run it. `helm/templates/` holds
eight templates and no Job, CronJob, ConfigMap, or ServiceAccount. Everything
periodic runs from a personal crontab on a Glade login host, wrapped in `flock` +
`ssh` against a conda env on a shared filesystem. `scripts/cleanup_status_data.py`
says "intended to be run daily via cron" and nothing schedules it at all. The
NRIT review flagged both (`docs/nrit-review-2026-05/06_platform.md` D9,
`08_action_register.md` P1-53, Q15 / Q42).

This plan adds **one hourly dispatcher CronJob** and a **`task_run` ledger** in
`system_status`, and wires the status-snapshot cleanup as the first task.

The load-bearing idea is § 2: a schedule does not answer *"is it now?"* — it
answers *"what is the most recent occurrence that should have run at or before
`now`?"*. Idempotence, self-healing after an outage, mutual exclusion against a
future daemon, and safe manual re-runs all fall out of that plus a UNIQUE
constraint.

### What changed since the first draft

| | |
|---|---|
| **`hpc-writer` gained DDL on `sam`** (2026-08-10, no `DROP` — `docs/plans/implemented/DBA_PRIVILEGE_REQUEST.md`) | A new SAM table is no longer a DBA round-trip. This reopened "where does the ledger live"; § 4.1 still chooses `system_status`, for reasons now about *testing* rather than privileges. |
| **The notification framework landed** (`src/sam/notify/`, `notification_log`) | Four of the old § 12's eight expiration-email preconditions are closed and a fifth is moot. Expiration notices become **the first consumer, shipped as a stacked PR** (§ 12). The proposed `notification_sent` table and its Alembic `0007` are deleted. |
| **The old § 14.6 was wrong** | It claimed a `values.yaml`-only commit to `main` would not reach `cirrus`. It does — § 13. The two-commit kill-switch soak works as written. |
| **The cleanup script is a draft, not a spec** (§ 3.1) | The first draft read as a *port* checklist. `scripts/cleanup_status_data.py` has three commits, no tests, no scheduler, and logic five months older than the span refactor it would now silently break. § 3.1 became an agenda, and the sprint settled all five items — including a **correction** to this plan's own span/CASCADE claim. Retention defaults to a year, which dissolves what the first draft called "the single most likely operational surprise in the whole plan". |
| **CIRRUS runs k8s v1.35.6** (verified 2026-08-12) | § 14's one genuine open item — whether `.spec.timeZone` (k8s ≥1.25, GA 1.27) is supported — is closed. The field is still `{{- with }}`-gated, but as belt-and-braces rather than as a risk. |

---

## 1. Roadmap

| Phase | Ships | Touches k8s? |
|---|---|---|
| **P0** ✅ | Preconditions: settled the § 3.1 retention agenda, landed `system_status/retention.py`; lazy SAM connect in `sam-admin` | no |
| **P1** ✅ | `src/scheduling/schedules.py` — pure predicate vocabulary + unit tests | no |
| **P2** ✅ | Alembic `0006_task_run`, `TaskRun` model, `ledger.py` | no |
| **P3** ✅ | Registry, `run_due()`, `cleanup_status_snapshots`, `sam-admin tasks` | no |
| **P4** ✅ | `helm/templates/cronjob-tasks.yaml` + render test | yes |
| **P5** | Production enable: **the chart already ships kill-switched**, so this is the soak plus a one-line commit clearing `SAM_TASKS_DISABLED` | yes |
| **stacked PR** | Expiration notices — the first real consumer (§ 12) | no |
| *later* | Watchdog task, admin card (§ 9), daemon (§ 10) | no |

P1–P3 are testable with `pytest` alone and are worth landing before anyone argues
about YAML.

**Goals.** (1) *Idempotent by construction* — running the dispatcher twice in one
minute, or three times after a three-day outage, has the same effect as running it
once at the right time. (2) *One scheduler object in the cluster*, not one per
task; adding a task is a Python change, not a chart change. (3) *A durable record*
— CronJob pods and their logs evaporate; the ledger is what you read at 09:00 to
answer "did the cleanup run last night?". (4) *A scheduler-agnostic core*, so a
future always-on agent calls the same `run_due()` and the CronJob demotes to a
fallback (§ 10).

**Non-goals**, listed so nobody grows them in by accident: sub-minute triggers,
DAGs, fan-out, backpressure, cluster leader election, a retry engine.

**Explicit non-targets.** The two existing crontabs are *not* migration
candidates. `collectors/cron_scripts/crontab` (2 jobs, `*/5`) and
`scripts/cron/accounting/crontab` (4 jobs, hourly + ~01:30 daily) both `ssh` into
Casper/Derecho for PBS and Glade access a k8s pod does not have. They stay where
they are. That is also why nothing here needs monthly granularity.

---

## 2. The core abstraction: occurrence predicates

### 2.1 The contract

```python
# src/scheduling/schedules.py  — stdlib only, no SQLAlchemy, no config imports
class Schedule(Protocol):
    def last_occurrence(self, now_utc: datetime) -> datetime | None:
        """Most recent scheduled instant at or before `now_utc`.

        NAIVE UTC, truncated to the second, or None if there is no such
        occurrence. Pure: same input -> same output, no clock reads, no I/O.
        """
    def next_occurrence(self, after_utc: datetime) -> datetime | None: ...   # display only
    def describe(self) -> str: ...            # "daily at 02:15 America/Denver"
```

`last_occurrence` is the whole framework. Rendered as a string it is the
**occurrence key** — dedup key, lock key, and the ledger's business identifier:
`occ.strftime('%Y%m%dT%H%M%SZ')`, fixed width so lexical order is chronological
order.

**Why this and not a boolean "is it due?"** A boolean must be asked at exactly the
right moment, which means either a high-rate poll or a scheduler you trust never
to miss a tick. An occurrence key is a *name for the slot*: the dispatcher asks
"what slot are we in?", tries to claim it, and either wins (runs) or loses
(someone already did). Lateness, duplicate dispatchers, and manual re-runs all
become expressible instead of dangerous.

`next_occurrence` exists only to render a "next due" column. **Nothing in the
control flow may call it** — a design rule, not a style preference: a scheduler
that reasons forward must be right about when it wakes up, and this one is
deliberately not.

### 2.2 The vocabulary

| Constructor | Meaning | Notes |
|---|---|---|
| `Hourly(minute=0)` | every hour at `:minute` | |
| `Daily(hour, minute)` | every day at HH:MM | |
| `Weekly(weekday, hour, minute)` | `weekday` 0=Mon … 6=Sun | |
| `MonthlyDay(day, hour, minute)` | day-of-month; **negative counts from the end** (`-1` = last day) | `day=29..31` in a short month clamps to the last day — documented, not skipped |
| `CronExpr("7 * * * *", horizon=timedelta(hours=48))` | raw 5-field escape hatch | see below |

Every constructor takes `tz: str = "America/Denver"` (IANA name, stdlib
`zoneinfo`).

`CronExpr` is a **bounded backward scan**: from `now` truncated to the minute,
step back a minute at a time, return the first match, raise after `horizon`. At
most 2,880 integer comparisons, and it buys the escape hatch with **no new
runtime dependency**. The cost is an honest restriction — *a `CronExpr` must fire
at least once every 48 hours* — and anything rarer must use a named predicate,
which is better documentation anyway. (`croniter.get_prev()` is exactly this
primitive if the bound ever binds; swapping it in is confined to one class.)

An earlier draft also specified `MonthlyBusinessDay(n, …)`. It is **cut**:
nothing in the roadmap is monthly, and it costs a class plus the "when the 1st
falls on Sat/Sun/Mon" test matrix. ~15 lines to add when something needs it, and
if a holiday calendar is ever wanted it becomes a table, not a guess.

### 2.3 Timezone — the part that will bite

Three clocks disagree: SAM MySQL is naive **Mountain** (vendor schema, which is
why `helm/values.yaml` sets `TZ: "America/Denver"`); `system_status` is naive
**UTC** (`src/system_status/timeutil.py:17`); human intent is "nightly at 2 a.m."
**Mountain**, and it should stay 2 a.m. across DST.

**Decision.** Occurrences are computed in the task's declared zone (default
`America/Denver`) and canonicalized to naive UTC for the key and the ledger. The
ledger never stores a local time or a tz name; the registry is the only place a
zone appears.

Two DST rules, stated because leaving them implicit is how you get a duplicate
nightly run once a year:

- **Ambiguous local times** (fall back, 01:00–02:00 MDT→MST): resolve with
  `fold=0`, the *earlier* UTC instant. `Daily(1, 30)` fires once on that day, at
  07:30 UTC — during the repeated hour `last_occurrence` maps the same wall clock
  to the same UTC instant, so the second pass claims an already-succeeded key and
  does nothing.
- **Nonexistent local times** (spring forward, 02:00–03:00): shift forward to the
  first instant that exists (02:30 → 03:00 local). `Daily(2, 15)` fires ~45 min
  late that one day, and never silently skips a day.

Both are pure functions and get parametrized tests against real transition dates
(§ 13). Guidance is still *prefer HH:MM outside 01:00–03:00*, and you never
exercise either rule. The first task is nevertheless at 02:15 Denver: the rules
are written down and tested, and moving snapshot pruning off the quiet hours to
dodge a tested code path is superstition.

Separately: **the dispatcher pod stays on `TZ: "America/Denver"`**, matching the
webapp. It is tempting to pin it to UTC since its first task is UTC-only —
resist. The moment the expiration task lands (§ 12) it reads SAM's naive-Mountain
dates, and a UTC pod reproduces exactly the "freshly-granted access reads back as
inactive" bug documented in `helm/values.yaml`.

⚠️ `utcnow_naive` lives in `src/system_status/timeutil.py` and **nowhere else**.
`sam/fmt.py` has no such symbol, despite what `CLAUDE.md` § 1 and
`helm/values.yaml:229` both imply. Fix those two references while in the area.

---

## 3. Preconditions (P0)

### 3.1 Retention: reevaluate the policy, then port the script

⚠️ **`scripts/cleanup_status_data.py` is a draft, not a specification. Do not
port its semantics faithfully.** It arrived in the first status-dashboard PR
(`ca47464`, #42); its last functional change was `a46de78`, a **2025-11-30**
directory reorg. Three commits, all structural. It has no tests, and nothing in
the tree invokes it — NRIT asked outright whether it runs in production and could
not find a scheduler (`docs/nrit-review-2026-05/03_status.md:63`, O1 / Q15 /
`08_action_register.md:79` P1-17). It is a hastily written, rarely run utility that has very likely never
executed against `csg-postgres` at all.

That matters because P0 is the moment its behavior becomes **automatic and
nightly**. Read the script as *evidence about the tables* — it is the only place
anyone has enumerated them — and then decide what the task should do.

**What it does today, and why each part is a question rather than a given:**

| Behavior | Why it is not settled |
|---|---|
| Cutoff is `datetime.now()` (`:44`), **local**, compared against `timestamp`, which is naive **UTC** (`system_status/base.py:76`) | Straight bug: 6–7 h early on a Denver host. Fix regardless of everything below. |
| 7-day retention | Wrong for the product — the status dashboards support long lookback, so a week under-serves the UI that reads it. |
| Prunes the seven snapshot tables **and** resolved `system_outages` **and** past `resource_reservations`, in one transaction | Those two are curated, human-authored incident records, not samples. Whether they belong in a *snapshot*-retention task is a policy question nobody has answered. |
| Outage predicate is `status == 'resolved' AND end_time < cutoff`, but `end_time` is **nullable** (`models/outages.py:57`) | A resolved outage nobody closed out leaks forever. Meanwhile `ResourceReservation.end_time` is **NOT NULL** (`models/outages.py:111`) and is pruned with no status check at all. Two curated tables, two inconsistent, unexamined predicates. |
| `user_proj_queue_status` prunes transitively via `ondelete='CASCADE'` to `derecho_status` / `casper_status` | **The span refactor invalidated this.** Post-#248 the parent FK points at the snapshot at *first_seen* and is never rewritten (`models/user_proj_queues.py:28-31`), so a span first seen 400 days ago and extended yesterday **dies with its parent**. A faithful port silently deletes live data. The script predates that cutover (2026-05-10) by five months. |
| `.count()` then `.delete()` per table; the entire body is `print()` | Two scans per table, and the script itself prints a warning for the case where the two disagree. A task needs a return value and a logger, so the I/O layer is a rewrite either way. |

The CASCADE row is the reason this section exists. It is invisible unless someone
is told to look, and the first draft of this plan told them the opposite.

⚠️ **But see the correction below** — the hazard is real and the blast radius is
much smaller than that row implies. Read both before acting on either.

#### The agenda, as settled (2026-08-12)

All five were decided during the implementation sprint and are **built**.

1. **Delete, or downsample?** → **Delete.** Rollups of old snapshots are a
   legitimate feature and a different plan.
2. **Do outages and reservations belong in this task?** → **No.** Snapshot tables
   only. `system_outages` and `resource_reservations` are curated,
   human-authored incident records; a scheduled job does not delete hand-written
   history. `tests/unit/test_status_retention.py` asserts both survive a prune,
   `end_time IS NULL` rows included.
3. **What horizon, per table?** → **One global 365-day knob**;
   `RETENTION_DAYS = {}` stays empty. `csg-postgres` was not reachable from the
   sprint, and a guessed per-table number that looks authoritative is worse than
   one obvious global. The first production run's `deleted` breakdown is the
   measurement. (Local MySQL, ~277k rows, is a smoke target and not a sizing
   sample: it holds **zero** `user_proj_queue_status` rows.)
4. **How are spans pruned?** → **The CASCADE *semantic* is kept; the CASCADE
   *mechanism* is not.** See the correction below.
5. **What is the contract?** → Built as specified: `{table: rows}` returned,
   `cutoff=`/`session=` injected, `dry_run`, bounded `chunk_size`, `logging`
   rather than `print`.

#### Correction: the span hazard is bounded by days, not years

The CASCADE row above says "a span first seen 400 days ago and extended
yesterday **dies with its parent**". True in principle. Its **probability is
essentially zero**, and the first draft of this section did not say so.

A span is not a job. It is a run of *unchanging counters* for one
`(user, project_code, queue)` tuple, and `user_proj_queue_ingest.py:137-158`
extends one only when the tuple was present at the **immediately preceding
tick** with all ten counters identical — never across a gap of more than
`MAX_SPAN_GAP` (20 minutes, `:38`). So a span's length is bounded by how long a
user's queue footprint stays perfectly static, which walltime bounds to days. A
365-day span would need ~115,000 consecutive identical ticks.

At a 365-day horizon, therefore, the only spans a CASCADE prune could lose are
ones straddling a cutoff that is itself a year old. Nothing recent is at risk.

**The mechanism still had to go, for a different reason: it is untestable
here.** Nothing in this repo sets `PRAGMA foreign_keys=ON`, so SQLite — the
entire status test tier — does not enforce `ondelete='CASCADE'` at all, and a
bulk `query.delete()` bypasses SQLAlchemy's ORM-level `cascade='all,
delete-orphan'` too. Relying on CASCADE would mean the behaviour under test and
the behaviour in production were different mechanisms.

`timestamp` **is** the span's first_seen, so an explicit
`DELETE ... WHERE timestamp < cutoff` reproduces the documented semantic
exactly, portably — and additionally reaps spans whose parent FKs are both
NULL, which CASCADE never could. `SNAPSHOT_TABLES` is hand-ordered
children-before-parents so the result never depends on FK enforcement, and a
test pins that ordering.

**What is already decided, and stands.** The policy lives in exactly one place —
a new `src/system_status/retention.py`, which is also where `cleanup_old_data()`
moves:

```python
#: The one retention knob. Overridden by $STATUS_RETENTION_DAYS.
DEFAULT_RETENTION_DAYS = 365

#: Per-table overrides — empty until agenda item 3 is measured. Add rows HERE,
#: not a second constant somewhere else.
RETENTION_DAYS: dict[str, int] = {}

def cleanup_old_data(retention_days=DEFAULT_RETENTION_DAYS, dry_run=False,
                     cutoff=None, session=None, chunk_size=10_000):
    if cutoff is None:
        cutoff = utcnow_naive() - timedelta(days=retention_days)
```

The 365-day default is deliberately conservative. A never-pruned production
database would otherwise meet its first automated run as a single multi-year
`DELETE` against `csg-postgres`; at a year, only data already older than a year is
in scope, so the hazard never arises and narrowing later is a one-line
`values.yaml` change reviewable on its own. `chunk_size` is hygiene rather than
mitigation: bounded batches, no long lock.

Every consumer reads the constant — the script's `argparse` default, the function
signature, the task. `scripts/cleanup_status_data.py` **stays**, because running a
prune by hand is legitimate, but becomes a thin wrapper owning no policy, and
stops being something a task must import across a `sys.path` hack.

**Three references go stale with it**, and a P0 change should sweep them:
`README.md:538` advertises "7-day retention"; `scripts/README.md:174,216`
documents the script; and `tests/conftest.py:424` says its per-test cleanup
"mirrors the iteration pattern" of the script — so it duplicates the table list
that agenda item 2 may change.

For the record: `src/sam/` imports nothing from `system_status` today. Keep it
that way (§ 6.1).

### 3.2 `sam-admin`'s group callback connects to SAM MySQL for every subcommand

`src/cli/cmds/admin.py:43-61` creates an engine and `Session` unconditionally,
exiting 1 on failure. As written, `sam-admin tasks --run-due` dies when SAM MySQL
is unreachable even though the only registered task touches Postgres — converting
a SAM outage into a `system_status` retention outage, precisely the coupling this
framework exists to remove.

Keep `SAMConfig.validate()` (cheap, catches misconfiguration); defer the connect
behind `Context.require_sam()`, which lazily builds the engine on first use.
`BaseCommand.__init__` calls it, the group callback stops connecting eagerly, and
the tasks command does neither. One line per touchpoint, no behavior change for
existing commands.

---

## 4. The ledger

### 4.1 Where it lives, and why not SAM MySQL

New model `src/system_status/models/task_run.py`; migration
`migrations/system_status/versions/0006_task_run.py`
(`down_revision = "0005_queue_def_roster_columns"`, still the head).

The model **must** be added to **both** the import list and `__all__` in
`src/system_status/models/__init__.py`: `migrations/system_status/env.py` builds
`target_metadata` by importing that package and there is no auto-discovery — a
model missing from it is invisible to autogenerate and to the drift test.

Since 2026-08-10 `hpc-writer` can create tables in `sam` directly, and
`notification_log` set the precedent of a framework table living there. The ledger
still belongs in `system_status`, for three reasons in order of weight:

1. **The test tier.** `system_status` tables are created by
   `db.create_all(bind_key='system_status')` against a per-worker SQLite tempfile
   — a new table exists in CI the moment the model does. A new **SAM** table only
   reaches CI after `make bootstrap` regenerates the LFS test-DB blob and someone
   recommits it, a path that has silently half-failed before. The ledger is the
   most test-heavy component here (competing claims, stale reclaim, prune), so
   putting it where tests are free is decisive.
2. **Free schema coverage.** `tests/integration/test_alembic_migrations.py`
   already asserts `upgrade head == StatusBase.metadata` and a `head → base →
   head` round-trip; `0006` inherits both with no new test. SAM has no in-repo
   migration path at all.
3. **Failure isolation.** With the ledger in Postgres a SAM MySQL outage cannot
   take down `system_status` retention — the coupling § 3.2 exists to remove.

The honest cost: two ledgers in two databases. Tolerable, because they want no
foreign key between them anyway — `notification_log` is documented as deliberately
FK-free, carrying a generic `entity_type`/`entity_id` instead.

### 4.2 Schema

| Column | Type | Null | Notes |
|---|---|---|---|
| `task_run_id` | `Integer` PK autoincrement | no | |
| `task_name` | `String(64)` | no | registry key, e.g. `cleanup_status_snapshots` |
| `occurrence_key` | `String(24)` | no | `20260810T081500Z`, or `M20260809T143002Z` for a forced run (§ 7) |
| `state` | `String(16)` | no | `running` / `succeeded` / `partial` / `failed` / `skipped` |
| `trigger_type` | `String(16)` | no | `schedule` / `catchup` / `manual`. **Renamed from `trigger` during the build** — that is a reserved word in both MySQL and Postgres. SQLAlchemy quotes it, so the app worked, but `SELECT task_name, state, trigger FROM task_run` fails with a syntax error naming the wrong token, and hand-written SQL against this table is a first-class use case. The JSON wire format keeps `trigger`. |
| `attempt` | `SmallInteger` | no | default 1; bumped only by a stale reclaim |
| `claimed_at` | `DateTime` | no | naive UTC |
| `heartbeat_at` | `DateTime` | no | naive UTC; the lease |
| `finished_at` | `DateTime` | yes | NULL while `running` |
| `duration_ms` | `Integer` | yes | |
| `runner_id` | `String(64)` | yes | pod name, ties a row to `kubectl logs` |
| `detail` | `Text` | yes | JSON: `TaskResult.detail`, or a truncated traceback |

Constraints named by `STATUS_NAMING_CONVENTION` (`src/system_status/base.py:26`):
`uq_task_run_task_name_occurrence_key` — **UNIQUE (`task_name`,
`occurrence_key`)**, which is the dedup key *and* the mutual-exclusion lock;
`ix_task_run_task_name_claimed_at` for "last run of X" and the history listing;
`ix_task_run_state` for the stale sweep.

`detail` is `Text` holding JSON rather than a JSON column type: MySQL is still the
default `STATUS_DB_DRIVER` and tests run on SQLite. Portability over
queryability, consistent with the rest of `system_status`.

### 4.3 State machine

```
                 INSERT (unique wins)
        ∅ ─────────────────────────────► running ──► succeeded
        │                                   │    └──► partial
        │  UPDATE…WHERE state='running'     ├──► failed
        │      AND heartbeat_at < cutoff    │
        └───────────── (reclaim, attempt+1) ┘

        ∅ ─────────────────────────────► skipped   (misfire / kill-switch / SKIP backfill)
```

There is deliberately **no `claimed` state distinct from `running`**. The INSERT
*is* the claim, microseconds before the task body starts in the same process; a
separate `claimed` row would be observable only during a window nobody can query,
and would add a transition that can itself fail. If the process dies between claim
and first heartbeat, the row is `running` with a stale `heartbeat_at` — exactly
what the reclaim rule handles.

### 4.4 The two locking primitives, and why they are these

Production is Postgres (`csg-postgres.k8s.ucar.edu`), tests are SQLite, the config
default is MySQL. So `SELECT … FOR UPDATE SKIP LOCKED` (no SQLite), MySQL
`GET_LOCK`, and Postgres advisory locks are all out. What remains is portable and,
happily, sufficient.

**Primitive A — claim a new occurrence: INSERT, catch the unique violation.**

```python
def claim(session_factory, task_name, key, *, trigger, runner_id, now) -> TaskRun | None:
    with session_factory() as s:                  # its OWN short transaction
        s.add(TaskRun(task_name=task_name, occurrence_key=key, state='running',
                      trigger=trigger, attempt=1, claimed_at=now,
                      heartbeat_at=now, runner_id=runner_id))
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
            return None                           # someone else owns this slot
        return _detach(s, ...)
```

The dedicated short-lived session is not stylistic. On Postgres an
`IntegrityError` aborts the whole transaction and every subsequent statement on
that connection fails with `InFailedSqlTransaction`; claiming on the session the
task body will use would poison it. This is exactly the discipline
`NotificationLedger` already uses — constructed with a `session_factory`, every
method opening and committing its own session, never enrolling in the caller's
transaction. See `src/sam/notify/ledger.py` and
`docs/plans/implemented/NOTIFICATION_FRAMEWORK.md` § 5; copy the shape.

**Primitive B — reclaim a stale occurrence: conditional UPDATE, check `rowcount`.**

```sql
UPDATE task_run
   SET state='running', runner_id=:me, attempt=attempt+1,
       claimed_at=:now, heartbeat_at=:now, finished_at=NULL
 WHERE task_name=:name AND occurrence_key=:key
   AND state='running' AND heartbeat_at < :cutoff
```

`rowcount == 1` means we won; `0` means someone beat us or the row recovered. A
single-statement conditional UPDATE is atomic under every isolation level on all
three backends, needs no explicit locking syntax, and is the same compare-and-swap
`SELECT … FOR UPDATE` would give with less portability.

`ledger.py` must contain **no dialect-specific SQL whatsoever** — § 13 enforces
this with a boundary test. CI runs SQLite and production runs Postgres; that test
is the only thing standing between those two facts.

### 4.5 Leases, heartbeats, retries, retention

- **Lease** = `heartbeat_at + max(3 × task.expected_runtime, 900 s)`. Past that,
  primitive B may steal the row.
- **Heartbeat.** The runner bumps `heartbeat_at` before and after each task, and —
  only for tasks declaring `long_running=True` — from a daemon thread every 60 s.
  No phase-1 task declares it; a heartbeat thread for a 20-second DELETE is
  ceremony.
- **Retries.** A `failed` row is terminal for its occurrence by default — the
  opinionated bit: **prefer self-healing tasks to a retry engine.** Tonight's
  cleanup failing costs nothing, because tomorrow night's deletes the same rows
  plus one more day. Tasks whose occurrences are genuinely non-fungible may set
  `retry=(max_attempts, backoff)`, letting primitive B also match
  `state='failed' AND attempt < max AND finished_at < now-backoff`. Nothing uses it
  in phase 1; it exists so the schema needn't change when something does.
- **Retention.** Rows older than 180 days are pruned by the cleanup task itself,
  guarded `WHERE finished_at IS NOT NULL AND finished_at < cutoff` so a run can
  never delete its own live row — four lines, and the thing whose job is bounding
  growth also bounds the ledger's.

---

## 5. Catch-up and misfire

Two per-task knobs, defaulted so the common case is invisible: `catchup` is
`CatchUp.SKIP` (only the most recent occurrence is a candidate) or `CatchUp.ALL`
(replay every missed occurrence, oldest first); `misfire_grace` is 6 hours;
`max_catchup` is 7 and only means anything under `ALL`.

```python
occ = task.schedule.last_occurrence(now)
if occ is None:                     -> nothing to do
if now - occ > task.misfire_grace:  -> record 'skipped' (detail: reason, lateness)
else:                               -> attempt to claim occ and run it
```

**The three-day-outage question, answered directly.** With `SKIP` and a 6 h grace,
a daily task after a three-day dispatcher outage runs **once** — for last night's
slot — and the two missed nights are written as `skipped` rows carrying
`detail={"reason":"misfire","late_by_s":…}`. The backfill walks backwards at most
32 steps then writes one summary row, so a task disabled for a year cannot
generate 365 INSERTs on the day someone re-enables it.

Backfilling the skips costs a handful of rows and buys a real benefit:
`sam-admin tasks --history` shows the outage *as an outage*. The alternative —
silence — makes a three-day gap look identical to a task that was never
registered.

`ALL` exists for tasks whose occurrences do distinct work (a per-day ingest).
Nothing uses it in phase 1. The design note that matters: under `ALL` the runner
runs oldest-first and **stops at the first failure**, so a broken day cannot be
silently skipped over on the way to today.

Worked example — `cleanup_status_snapshots`, `Daily(2, 15, tz="America/Denver")`,
dispatcher hourly at `:07` UTC:

| Event | Dispatch at | `last_occurrence` | Outcome |
|---|---|---|---|
| Normal | 09:07 UTC | 08:15 UTC (= 02:15 MDT) | claim → run → `succeeded` |
| Same day, later | 10:07 UTC | 08:15 UTC | claim fails (unique) → no-op |
| Cluster down 08:00–13:00 | 13:07 UTC | 08:15 UTC, 4.9 h late | within grace → claim → run |
| Cluster down 08:00–16:00 | 16:07 UTC | 08:15 UTC, 7.9 h late | past grace → `skipped`; tonight's run deletes one extra day |

---

## 6. Registry and runner

### 6.1 Where the code lives — and why not the two obvious places

```
src/scheduling/                  # NEW top-level package, peer of sam/ system_status/ cli/
├── schedules.py                 # PURE. stdlib only. no SQLAlchemy, no config.
├── registry.py                  # Task dataclass, @task decorator, TASKS dict
├── ledger.py                    # TaskRun claim/heartbeat/finish/prune
├── runner.py                    # run_due() — the entire scheduler-facing API
└── tasks/
    ├── __init__.py              # side-effect imports that populate TASKS
    └── cleanup_status.py        # the first real task
src/cli/tasks/                   # the Click/Rich surface: commands.py display.py builders.py
src/system_status/models/task_run.py     # the ORM model (Alembic must see it here)
src/system_status/retention.py           # the retention policy + cleanup_old_data (§ 3.1)
```

**Rejected: `src/sam/scheduling/`.** `src/sam/` is the domain package for SAM
MySQL. It does hold cross-cutting utilities (`fmt.py`, `plugins.py`), so it is not
*purely* models — but it currently imports nothing from `system_status`, and the
ledger would invert that. A scheduler living in the SAM-DB package while writing
to the status DB is a dependency edge somebody will regret.

**Rejected: `src/cli/tasks/` as the home of the engine.** The CLI is a
presentation layer, and a future daemon (§ 10) must be able to
`from scheduling.runner import run_due` without importing Click, Rich, or the
`sam-admin` group callback. `src/cli/tasks/` still exists — it holds the command
classes and display functions per `src/cli/README.md`'s
`builders`/`commands`/`display` split.

### 6.2 Declaring a task

`Task` is a frozen dataclass: `name` (the ledger key — **stable forever**, since
renaming orphans history), `schedule`, `fn`, plus defaulted `needs=('status',)`
(subset of `('sam','status')`), `catchup`, `misfire_grace`, `expected_runtime`,
`max_catchup`, `long_running`, `description`. A `@task(name=…, schedule=…, **kw)`
decorator registers into a module-level `TASKS` dict and raises on a duplicate
name.

A decorator rather than a list of dataclasses, because the schedule belongs next
to the function it schedules; the alternative puts the two halves of every task in
different files and guarantees drift. Discoverability is recovered the way
`src/system_status/models/__init__.py` already does it: an explicit list of
side-effect imports in `scheduling/tasks/__init__.py`, the one place to grep for
"what tasks exist".

```python
# src/scheduling/tasks/cleanup_status.py
@task(name='cleanup_status_snapshots',
      schedule=Daily(2, 15, tz='America/Denver'),
      needs=('status',),
      expected_runtime=timedelta(minutes=2))
def cleanup_status_snapshots(ctx: TaskContext) -> TaskResult:
    """Prune system_status snapshot rows older than the retention window."""
    retention = int(os.getenv('STATUS_RETENTION_DAYS', DEFAULT_RETENTION_DAYS))
    cutoff = ctx.occurrence - timedelta(days=retention)   # NOT utcnow(): keyed to the slot
    counts = cleanup_old_data(cutoff=cutoff, dry_run=ctx.dry_run, session=ctx.status_session)
    pruned = prune_task_runs(ctx.status_session, older_than=ctx.occurrence - timedelta(days=180))
    return TaskResult(detail={'deleted': counts, 'task_run_pruned': pruned})
```

⚠️ The `cleanup_old_data(...)` call above is a **sketch against P0's output**, not
a settled API — § 3.1's agenda decides which tables it touches and how spans are
pruned, and that may change the signature.

Note `cutoff = ctx.occurrence - retention`, not `now - retention`. **A task
computes from its occurrence, never from the wall clock.** That is what makes a
late run produce the same result as a punctual one, which is what makes the scheme
deterministic and testable. Put it in the module docstring — it is the single
easiest thing for a future task author to get wrong.

### 6.3 What a task receives and returns

`TaskContext` carries `now` (naive UTC, the dispatch instant), `occurrence` (naive
UTC, the slot being filled — **compute from this**), `dry_run`, a
`sam.tasks.<name>`-namespaced `logger`, and lazy `.sam_session` / `.status_session`
accessors opened on first use and driven by `needs`. That laziness is what § 3.2
makes possible: a status-only task never touches SAM MySQL.

`TaskResult` carries a JSON-serializable `detail` dict, an optional `message`, and
`partial_failures: int`.

- Return normally → `succeeded`, or `partial` if `partial_failures > 0`.
- Raise → `failed`, `detail = {"error": repr(exc), "traceback": tb[-4000:]}`. The
  runner catches `Exception`, **not** `BaseException`: a `KeyboardInterrupt` /
  `SystemExit` must propagate so the pod's `activeDeadlineSeconds` kill leaves the
  row `running`-and-stale for the reclaim path rather than mislabeling it.
- `partial` is a real state rather than a flag inside `detail` because the
  expiration task will genuinely produce "23 of 25 emails sent", and an operator
  scanning a status column should not have to open JSON to notice.

### 6.4 The runner

`run_due(*, now, only=None, force=False, dry_run=False, registry=TASKS, ledger)`
is the *entire* scheduler-facing API. `now` is injected and never read from the
clock inside — that is what makes § 13's simulated-week tests possible.

Per task, in registry order:

1. **Kill switch.** `SAM_TASKS_DISABLED` (comma-separated names) → record
   `skipped` with `detail={"reason":"disabled"}`. GitOps-flippable in
   `values.yaml` without a code deploy; the P5 rollout depends on it.
2. **Stale sweep** for this task (primitive B) — at most one reclaim per dispatch.
3. **Dueness** per § 5. Misfires → `skipped`.
4. **Claim** (primitive A). Lost → continue *silently*; this is the normal case
   for every dispatch after the first in a slot and must not log at WARNING, or
   the logs are 23/24 noise.
5. **Run** inside `try/except`, timed; open only the sessions `needs` asks for;
   commit or roll back the task's own sessions independently of the ledger's.
6. **Finish**: `state`, `finished_at`, `duration_ms`, `detail`, one commit.

Tasks run **strictly serially** in one process. With three tasks and a two-minute
worst case, concurrency is unjustifiable complexity; if it ever becomes
justifiable, the ledger already makes parallel dispatchers safe, so the answer is
"two CronJobs with disjoint `--only` sets", not a thread pool.

---

## 7. CLI surface

`sam-admin tasks`, per `src/cli/README.md` § *Adding New Commands*:
`TasksListCommand` / `TasksDispatchCommand` / `TasksHistoryCommand` in
`src/cli/tasks/commands.py` extending `BaseCommand`; builders returning plain
dicts; `display_*` functions taking dicts only.

| Flag | Mode | Behavior |
|---|---|---|
| *(none)* / `--list` | query | registry + latest ledger row per task |
| `--run-due` | dispatch | the CronJob entry point |
| `--run <name>` | dispatch | one task, ignoring dueness |
| `--history` | query | recent rows; `--task <name>`, `--limit N` (default 20) |
| `--dry-run` | modifier | requires a dispatch mode; **no ledger writes** |
| `--force` | modifier | requires `--run`; manual occurrence key |

Modes are mutually exclusive; violating that is exit 2 with a message, following
the `--notify requires --upcoming-expirations` precedent in `cmds/admin.py`.

**`--dry-run` writes no ledger row at all.** A dry run that claimed the slot would
prevent the real run — the worst possible failure mode for a safety flag. It
prints what it *would* claim; the JSON envelope reports `"would_claim": [...]`.
The notify framework made the same call for `preview()`, for the same reason.

**`--force` writes a manual key**: `"M" + now.strftime('%Y%m%dT%H%M%SZ')`,
`trigger='manual'`. The leading `M` cannot collide with a scheduled key, so a
forced run never satisfies a scheduled slot — and that must be documented at the
flag: *a forced 10:00 run of the cleanup does not stop tonight's 02:15 run.* The
alternative — deleting or superseding the scheduled row — makes history lie, and
history is the entire product here. Without `--force`, `--run <name>` fills the
real slot and refuses (exit 2) if that slot already `succeeded`.

**Output.** Rich table columns are Task / Schedule / Last occurrence / State /
Age. JSON envelopes carry a top-level `kind` per convention — `task_list`,
`task_dispatch`, `task_history`:

```json
{"kind": "task_dispatch", "now": "2026-08-09T14:07:03",
 "results": [{"task": "cleanup_status_snapshots", "occurrence": "2026-08-09T08:15:00",
              "outcome": "already_claimed"}],
 "counts": {"succeeded": 0, "failed": 0, "skipped": 0, "already_claimed": 1}}
```

**Exit codes.** `0` — nothing due, all due tasks succeeded, or every slot already
claimed by a peer. `1` — `--run <unknown>` / `--history --task <unknown>`. `2` —
≥1 task `failed` or `partial`, or a bad flag combination. This follows the *audit*
convention in `src/cli/README.md` § *Exit Codes*, where `EXIT_ERROR` is overloaded
to mean "findings exist" so CI can gate on it. Here the "CI" is Kubernetes: a
nonzero exit makes the Job `Failed`, the only free alerting channel this
deployment has.

**One documented carve-out.** `src/cli/README.md:72` says `--format json` combined
with side-effecting flags is rejected (`json_unsupported_for_writes`, exit 2).
`--run-due` is inherently side-effecting and JSON is exactly what a log-scraped
dispatcher should emit, so `--format json --run-due` is **allowed** and the README
gets one sentence recording it. The rule exists to stop someone accidentally
emailing while scripting a *report*; here the side effect **is** the command. The
guard stays in force for `--notify` (§ 12), where the original hazard is real.

---

## 8. The Helm CronJob

New `helm/templates/cronjob-tasks.yaml`, gated `{{- if .Values.tasks.enabled }}`.
The chart has no `_helpers.tpl` and inlines labels per template; follow that rather
than introducing a helper for one file. Reuse `webapp.securityContext.pod` /
`.container` verbatim via the same `{{- with }}` idiom as `deployment.yaml:31-36`.

The fields that encode a decision:

```yaml
spec:
  schedule: "7 * * * *"
  timeZone: "Etc/UTC"                  # {{- with }}-gated; see the table
  concurrencyPolicy: Forbid
  startingDeadlineSeconds: 600
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 5
  jobTemplate:
    spec:
      backoffLimit: 0
      activeDeadlineSeconds: 3000
      template:
        spec:
          restartPolicy: Never
          automountServiceAccountToken: false   # same reasoning as deployment.yaml:29
          containers:
          - name: tasks
            image: {{ .Values.webapp.container.image }}     # ← NOT a tasks-specific key
            command: ["sam-admin"]
            args: ["--format", "json", "tasks", "--run-due"]
            env:
              - name: TZ                        # Mountain, matching the webapp (§ 2.3)
              - name: RUNNER_ID                 # fieldRef metadata.name -> TaskRun.runner_id
              # SAM_DB_*, STATUS_DB_* from webapp.env; credentials from the SAME
              # namespace Secrets the Deployment consumes (samuel-db-credentials,
              # samuel-sam-db-credentials). No new ExternalSecret, no new OpenBao path.
```

⚠️ **The image key is load-bearing.** CI
(`.github/workflows/build-images-cirrus-deploy.yaml`, `update-helm`)
`sed`-rewrites lines matching `image: ghcr.io/<repo>/webapp:`. A tasks-specific key
would either be missed (pinned forever at `:main`) or silently co-rewritten,
giving two sources of truth for one image. `sam-admin` is already in this image;
only the command differs.

| Field | Value | Reason |
|---|---|---|
| `schedule` | `"7 * * * *"` | Offset from `:00` to dodge the top-of-hour herd and disjoint from the collectors' `*/5`. The exact minute is arbitrary — the ledger keys off the *task's* schedule, not the CronJob's |
| `timeZone` | `"Etc/UTC"` | The controller must never see a DST gap or fold; task-level DST is handled in Python, where it is tested. `{{- with }}`-gated so a pre-1.25 cluster can drop it |
| `concurrencyPolicy` | `Forbid` | Belt. The ledger is the suspenders and the one that actually holds — `Forbid` protects only *within one CronJob object*, not against a future daemon |
| `startingDeadlineSeconds` | `600` | Miss by >10 min and skip; the next hour catches up free. Never leave it unset on a CronJob that might be suspended — >100 missed schedules wedges the controller permanently — nor below ~10 s, or resync jitter eats legitimate fires |
| `successful`/`failedJobsHistoryLimit` | `3` / `5` | Failures are worth keeping longer than successes; both small, matching the chart's `revisionHistoryLimit: 3` instinct about ArgoCD clutter |
| `backoffLimit` | `0` | The next hourly dispatch *is* the retry, and the ledger decides whether re-running is even correct. An immediate retry just re-hits whatever broke five seconds ago |
| `activeDeadlineSeconds` | `3000` (50 min) | Strictly under the 60-minute interval, so a wedged run cannot coexist with its successor. A kill here leaves a stale `running` row, which the § 4.5 lease reclaims |
| `restartPolicy` | `Never` | With `backoffLimit: 0`, `OnFailure` is a contradiction; `Never` also preserves the failed pod for `kubectl logs` |
| resources | req `100m`/`256Mi`, lim `1`/`1Gi` | The floor is Python import overhead of the fat image (matplotlib is not on the CLI path); 1 GiB absorbs a surprise |

`values.yaml` gains a `tasks:` block holding every field in the table above plus
`enabled: true`, `name: samuel-tasks`, `suspend: false`, `args: ["--run-due"]`,
the resource requests/limits, and two env keys:

```yaml
  env:
    # One year. The status dashboards support long lookback, and a year-long
    # cutoff also means the first production run is not a multi-year DELETE.
    # The default lives in src/system_status/retention.py; this overrides it.
    STATUS_RETENTION_DAYS: "365"
    # Comma-separated task names to skip without a code deploy. Used during the
    # initial rollout to prove creds/DNS/image before anything deletes.
    SAM_TASKS_DISABLED: ""
```

The block's header comment should say what it is: *one CronJob for all tasks; each
task's schedule lives in `src/scheduling/tasks/`, and the ledger makes a late or
duplicate dispatch a no-op.*

`values-local.yaml` sets `tasks.enabled: false` — on Docker Desktop nothing should
silently DELETE local data. Enable for a smoke test with
`--set tasks.enabled=true --set tasks.schedule='*/5 * * * *'`.

---

## 9. Observability

The ledger *is* the observability story; the job is to give it two or three cheap
read paths.

**Tier 0 — ships with P3/P4. Required.** `sam-admin tasks --list` / `--history`;
one structured stdout line per task per dispatch (the durable sink — the chart
already leans on CIRRUS's stdout retention for audit records, see
`AUDIT_LOG_STDOUT`); and a failed Job object retained by
`failedJobsHistoryLimit: 5`.

**Tier 1 — BUILT** (PR #444, commits following P4). Planned in its own
document, `SCHEDULED_TASKS_DASHBOARD.md`, whose § *As built* records the
deviations. The "~60 lines" below turned out to be wrong: this is the *third*
instance of a shape the codebase already has twice (XRAS actions,
notifications), so it started by extracting a faceted-log facade —
**`src/querykit/`**, a new top-level peer package.

Two decisions there **supersede the text below** — the details page is
`VIEW_SYSTEM_CONFIG` with only the traceback-bearing modal at `SYSTEM_ADMIN`,
and the page reaches full parity with the Notifications one (facet chips,
pagination, modal). What shipped:

- `src/querykit/` — `LogSpec` + `count_rows` / `page_rows` / `facet_counts`,
  SQLAlchemy-only, with an import-graph gate. Notifications migrated onto it.
- `src/webapp/utils/faceted_log.py` — `parse_window` / `build_facet_strip`.
- `src/system_status/queries/task_runs.py` — the card counts and facet rollups.
- The card on Admin → Configuration, and `/admin/htmx/tasks{,/log,/<id>}`.

The original sketch:

A read-only "Scheduled tasks" card on
the Admin → Configuration tab, copying the **Notifications card** exactly
(`templates/dashboards/admin/fragments/configuration_card.html:266-341`): body
gated on `VIEW_SYSTEM_CONFIG` showing **counts only**, with a `Details »` link
wrapped in `{% if has_permission(Permission.SYSTEM_ADMIN) %}` so operators are
never shown a link that would 403. Route beside `htmx_server_card` in
`dashboards/admin/configuration_routes.py`, fragment under
`templates/dashboards/admin/fragments/`. That tab is documented read-only, so a
"run now" button belongs elsewhere. The query is one
`ORDER BY claimed_at DESC LIMIT 1` per task — with three tasks, loop; do not write
a window function.

**Deferred, named so nobody reinvents them.** `/api/v1/health/tasks` returning
`{"stale": [...]}` — there is no external monitor to consume it, so build the
consumer first. And **a watchdog task**: the only reliable executor here is the
dispatcher itself, so the natural staleness alarm is a registered task —
`tasks_watchdog`, hourly, exits 2 (→ failed Job) if any task's last `succeeded` is
older than 2× its interval. It cannot detect its own death, but a dead dispatcher
stops producing Job objects entirely, a different and more visible failure. ~30
lines, no new infrastructure. **Recommended as the second task ever written**, and
once § 12 lands it should also surface `NotificationLedger.stuck_queued()`, which
today has no alert beyond the admin card.

---

## 10. The daemon migration path

The entire scheduler-facing surface is `run_due(now=…, ledger=…, registry=TASKS)`.
`schedules.py` is pure and clock-free; `registry.py` knows nothing about how it is
invoked; `ledger.py` talks only to `system_status`; nothing in `src/scheduling/`
imports Click, Flask, or `kubernetes`. A future always-on agent is therefore
`while True: run_due(now=utcnow_naive(), ledger=ledger); time.sleep(60)`, deployed
as a 1-replica Deployment with `strategy: Recreate`.

**During the migration both can run**: CronJob and daemon both wake, both compute
the same occurrence key, and the UNIQUE constraint picks one winner per
occurrence. Run both for a week, watch `runner_id` show the daemon winning every
slot, then set `tasks.suspend: true` and keep the CronJob as a values-flippable
fallback.

What a *true* always-on agent needs that this framework deliberately does not
provide, and should not be stretched to:

| Capability | Why not here |
|---|---|
| Sub-minute / event-driven triggers | Polling at hour granularity by design |
| Long-running work with progress + cancellation | `TaskResult` is a single return value; there is no cancel channel |
| Inter-task dependencies / DAGs / fan-out | Tasks run serially in registry order and know nothing of each other |
| Queues, priorities, backpressure, worker pools | One process, one task at a time |
| Leader election | The ledger gives per-occurrence mutual exclusion, **not** a cluster leader. Two daemons both wake and both attempt; one wins per slot. Correct, but not a leader — do not let anyone assume it is |
| Retry with exponential backoff as a first-class concept | § 4.5 has a deliberately anemic hook. Real retry semantics are a queue's job |
| Externally-triggered runs ("run now" from the UI) | The CLI is the only trigger surface |

If three or more of those become requirements, the answer is a real workflow
engine, not evolution of this one. Writing that down now is cheaper than arguing
about it in eighteen months.

---

## 11. Rejected alternatives

| Alternative | Why not |
|---|---|
| **High-rate polling (`*/5 * * * *`)** | 288 pods/day and 288 connect cycles against `csg-postgres`'s 100-slot cap, for zero benefit — the ledger already makes lateness harmless, so a finer poll buys only punctuality, which nothing here needs |
| **One CronJob per task** | Two sources of truth per schedule (a cron string and a Python declaration), drifting silently, and every new task becomes a chart change plus an ArgoCD sync — precisely the friction being removed |
| **APScheduler inside gunicorn** | 18 workers = 18 schedulers = 18 duplicate fires per slot. The ledger would make that *correct*, which is why it is a genuine option — it is rejected on **coupling**: batch DELETEs and SMTP loops would run inside request-serving processes and be killed mid-task by every rolling update |
| **A dedicated always-on Deployment now** | A pod running 24/7 for ~2 minutes of work a day, plus liveness probes, restart semantics, and a second thing ArgoCD can show as Degraded. § 10 keeps the door open at zero cost today |
| **Argo Workflows `CronWorkflow`** | The cluster runs ArgoCD, not Argo Workflows — a different product. New CRDs, controller, RBAC, and a second scheduling language to keep in sync |
| **GitHub Actions `schedule:`** | No network path from GitHub runners to `csg-postgres` or `sam-sql` (VPN-only), so it means egressing DB credentials or standing up a self-hosted runner. GH cron is also routinely 5–30 min late |
| **Keep it in the Glade crontab** | A personal crontab under `/glade/…/benkirk/repos/…`, already flagged as an SPOF by the NRIT review. The cleanup task is not in it today anyway |
| **cron / supervisord in the webapp container** | Two PID-1 candidates, `restartPolicy` semantics that fight the Deployment, and APScheduler's duplicate-fire problem with worse observability |
| **Reuse `sam.operational.Synchronizer`** | Four reasons, any one sufficient. **(1) Wrong database** — vendor-owned SAM MySQL, no Alembic coverage here. **(2) Wrong shape** — `(name, last_run)` is a pointer, not a ledger; it cannot express dedup, state, attempt, duration, or history. **(3) No lock primitive** — two dispatchers both read `last_run`, both decide "due", both run. **(4) Legacy furniture** — one vendor row, no writers in this codebase. The right precedent is `DiskChargeSummaryStatus` (`src/sam/summaries/disk_summaries.py:80`); `task_run` is that pattern generalized from a date key to an occurrence key |

---

## 12. First consumer: expiration notices (a stacked PR)

The notification framework (`src/sam/notify/`,
`docs/plans/implemented/NOTIFICATION_FRAMEWORK.md`) landed after this plan's first
draft and closed most of what was an eight-item blocker list. Expiration notices
are now the **first real consumer of the dispatcher**, shipped as a PR stacked on
P5 rather than folded into it.

**Closed by the notify framework — do not re-derive these:**

| Old precondition | Status |
|---|---|
| A dedup table | **Dead.** `notification_log` exists in SAM MySQL and the CLI already writes it — `kind`, `channel`, `template`, `entity_type`/`entity_id`, `projcode`, `recipient`, `dedup_key`, `status`, timestamps, `requested_by` |
| At-most-once vs at-least-once | **Decided.** The `queued` row is written *before* the transport call and the send is refused if that write fails; `NOTIFY_QUEUED_STALE_SECONDS` (300) converts an orphaned `queued` row back into a retryable state. At-most-once within the horizon, at-least-once beyond it — the boundary is a knob |
| The hardcoded BCC | **Done.** `src/cli/notifications/email.py` is deleted; it is `NOTIFY_BCC` on `NotifyConfig`, envelope-only, empty by default |
| "Can the namespace reach SMTP?" — the declared long pole | **Closed.** Measured 2026-08-09 from a pod in `sam-queries`: STARTTLS negotiates, no `AUTH` needed, egress IP passes `ucar.edu` SPF, the only NetworkPolicy is redis-scoped, and one real message reached an inbox. `NOTIFICATION_FRAMEWORK.md` § 9 |

**And the scheduling hazard is already defused.** The dedup key is
`expiration:{projcode}:{latest_end_date}:{recipient}`
(`src/cli/project/commands.py:454`), so **a daily schedule against the current
rolling 32-day window is already safe** — each recipient gets exactly one notice
per expiration date, ever, and a new end date mints a new key. The old draft
treated this as a blocking redesign; it is not.

**No `UNIQUE(dedup_key)` on `notification_log`.** Considered and rejected as
incompatible with the table. `record()` writes the key on *every* row including
`suppressed` ones, so a normal re-run legitimately produces duplicates — the
framework's measured run was 602 `sent`, then 602 `suppressed` on the same keys —
meaning production already holds duplicates and the `ALTER` would simply fail.
Forced through, it would also break retries, since `failed` is deliberately not a
suppressing status and a retry is a new row on the same key. It is unnecessary
anyway: `task_run`'s UNIQUE gives per-occurrence mutual exclusion and tasks run
serially, so two processes can never be inside the send loop at once. If a daemon
ever lands and that stops being true, the shape is a generated `dedup_claim`
column — `NULL` unless status is `sent`/`redirected`, UNIQUE on that, since MySQL
has no partial indexes — not a unique index on `dedup_key` itself.

**What the stacked PR still owes:**

1. **Milestones instead of a rolling window** — optional, but better.
   `ProjectExpirationCommand.execute` hard-codes `now → now + 32 days`
   (`src/cli/project/commands.py:143` and `:159`). A fixed ladder
   (`MILESTONES = (60, 30, 14, 7, 1)` days before `allocation.end_date`, selecting
   pairs whose milestone date *equals* the occurrence date) turns a window into a
   point event, which is what gives § 5's catch-up a well-defined set of missed
   notices. Adding rungs means **adding the milestone to the dedup key**, or the
   first rung suppresses all the others.
2. **A hard send cap.** `SAM_TASKS_EMAIL_MAX` (default 250). Exceeded → the task
   fails *before* sending anything, with the count in `detail`. Nothing like this
   exists today, and it guards the failure mode where a milestone bug turns "the
   30-day cohort" into "every allocation ever".
3. **Explicit facility scoping.** `default=['UNIV', 'WNA']` is a Click default on
   `--facilities` (`src/cli/cmds/admin.py:106`). The task must pass its facilities
   explicitly rather than inherit a CLI default someone might reasonably change.
4. **Keep the JSON/write guard where it is.** `execute()` rejects
   `json_mode and notify` at `src/cli/project/commands.py:125-133`. The task will
   call the command class directly with a `Context` it constructs
   (`output_format='rich'`), so the guard must stay a CLI-flag check and must not
   migrate down into the command class, where it would block the task. § 7's
   carve-out is about the *dispatcher's* own output, not about `--notify`.

**Rollout, when it comes:** `NOTIFY_ENABLED` is fail-closed and
`NOTIFY_REDIRECT_TO` exists, so the safe first cycle is a redirected run — real
ledger rows, real templates, every message to one mailbox — for one full ladder
cycle before anything reaches a PI.

---

## 13. Verification

| Layer | File | How |
|---|---|---|
| Predicates | `tests/unit/test_schedule_predicates.py` | Pure functions, no fixtures, no DB. Parametrized over spring-forward (2027-03-14) and fall-back (2026-11-01) in `America/Denver`; `MonthlyDay(-1)` / `MonthlyDay(31)` across Feb 28 / Feb 29 / 30- and 31-day months; `CronExpr` agreement with `Hourly`/`Daily` over 1,000 instants, and raising past its horizon. Properties: `last_occurrence` is idempotent and `<= t` |
| Ledger | `tests/unit/test_task_ledger.py` | The existing SQLite status tier (`status_session`). Two competing claims on one key → exactly one `TaskRun`, loser gets `None`. Stale reclaim: hand-age `heartbeat_at`, assert `attempt == 2` and still one row. A fresh row is **not** reclaimable. `prune_task_runs` never touches `finished_at IS NULL` |
| Portability guard | same file | A boundary test in the style of `tests/unit/test_chart_module_boundaries.py` (AST-walks imports, including inside functions): `ledger.py` contains none of `FOR UPDATE`, `SKIP LOCKED`, `GET_LOCK`, `pg_advisory`, `ON CONFLICT`, `INSERT IGNORE`, `ON DUPLICATE KEY`; `schedules.py` imports no SQLAlchemy and no config |
| Migration | *free* | `tests/integration/test_alembic_migrations.py` already asserts `upgrade head` matches `StatusBase.metadata` and that `head → base → head` round-trips — and *fails* if model and migration disagree, which is the point |
| Runner | `tests/unit/test_task_runner.py` | `run_due(now=…)` takes the clock as a parameter, so: throwaway tasks against a fake registry, a simulated week → exactly 7 `succeeded` for a daily task; a simulated 3-day outage → 1 `succeeded` + 2 `skipped`; `dry_run` writes zero rows; a raising task yields `failed` with a traceback in `detail` |
| Retention | `tests/unit/test_status_retention.py` | `cleanup_old_data(cutoff=…)` is injectable: the cutoff is honored exactly, `dry_run` deletes nothing, chunking terminates, and the default is 365 in every consumer (script, signature, task). Then one test per § 3.1 decision, so the agenda leaves evidence: **a span with `timestamp` older than the cutoff but `last_seen` inside it survives** (item 4 — this is the test that would have caught the naive port), and whichever tables item 2 excludes are asserted untouched, outage `end_time IS NULL` rows included |
| CLI | `tests/unit/test_cli_tasks.py` | CliRunner, per `test_sam_search_cli.py`. Exit codes per mode, `kind` in every JSON envelope, mutually-exclusive-flag rejections, `--run unknown` → 1 |
| Chart | `helm/tests/test-cronjob-render.sh` | Modeled on `test-oidc-render.sh` — same `assert_contains` helpers, same two renders |

The chart assertions worth writing, specifically:

```bash
# The pinning invariant — a tasks-specific image key would never be repinned by CI.
webapp_image=$(grep -E '^\s+image: ghcr.io/.*/webapp:' "$CHART_DIR/values.yaml" | awk '{print $2}')
assert_contains "$prod_out" "image: ${webapp_image}" "CronJob must reuse webapp.container.image"
test "$(printf '%s' "$prod_out" | grep -c 'image: ghcr.io/.*/webapp:')" -eq 2 \
  || { red "FAIL: expected exactly 2 webapp image refs (Deployment + CronJob)"; exit 1; }

assert_contains     "$prod_out"  "kind: CronJob"
assert_contains     "$prod_out"  'command: ["sam-admin"]'
assert_contains     "$prod_out"  "concurrencyPolicy: Forbid"
assert_contains     "$prod_out"  "automountServiceAccountToken: false"
assert_contains     "$prod_out"  "samuel-db-credentials"      # STATUS_DB_* secretKeyRef
assert_contains     "$prod_out"  "runAsUser: 1000"            # inherited hardening
assert_not_contains "$local_out" "kind: CronJob"              # local default is disabled
```

Add both scripts to whatever runs `helm/tests/` — today that is manual, and a
one-line `make helm-test` target running every `helm/tests/*.sh` is the obvious
tidy-up while touching this.

### Docker Desktop validation, before CIRRUS

```bash
bash helm/local-secrets.sh samuel-dev
helm upgrade --install samuel ./helm -f helm/values.yaml -f helm/values-local.yaml \
  -n samuel-dev \
  --set tasks.enabled=true --set tasks.schedule='*/5 * * * *' \
  --set 'tasks.env.SAM_TASKS_DISABLED=cleanup_status_snapshots'

kubectl create job -n samuel-dev --from=cronjob/samuel-tasks tasks-manual-1   # don't wait 5 minutes
kubectl logs -n samuel-dev job/tasks-manual-1
kubectl exec -n samuel-dev deploy/samuel -- sam-admin tasks --list
kubectl exec -n samuel-dev deploy/samuel -- sam-admin --format json tasks --history | jq
```

That proves image, command, secrets, DNS, and ledger writes with the destructive
task still disabled. Then drop `SAM_TASKS_DISABLED`, re-run, and confirm a
`succeeded` row with a `deleted` breakdown in `detail`.

### Production rollout (P5)

The `main` → CI → `cirrus` → ArgoCD path is the only route
(`docs/CIRRUS_PUBLISHING.md`); there is no direct `helm upgrade` against CIRRUS.

1. Merge P1–P4 to `main` with `values.yaml` carrying
   **`SAM_TASKS_DISABLED: "cleanup_status_snapshots"`**. For 24 h the dispatcher
   runs hourly, writes `skipped` rows, and deletes nothing. This proves
   credentials, DNS, image, securityContext, and Postgres reachability from a pod
   that is *not* the webapp — with zero blast radius. It is the entire reason the
   kill switch exists.
2. Verify: `sam-admin tasks --history` shows 24 `skipped` rows with distinct
   `runner_id`s.
3. Second commit: `SAM_TASKS_DISABLED: ""`. **This works as a separate commit.**
   An earlier draft worried a values-only change might not reach `cirrus` because
   `update-helm` is gated on `webapp_built == 'true'`. That is wrong: the
   workflow's `main` push trigger has no `paths:` filter, `DEFAULT[webapp]="true"`
   makes `webapp_built` true on every push, and `update-helm` checks out the whole
   triggering tree. `webapp_built` is only `false` on a `workflow_dispatch` whose
   `images` input explicitly omits `webapp`.
4. Watch the first real 02:15 MT run. With `STATUS_RETENTION_DAYS: "365"` it
   deletes only what is already older than a year, so this is an ordinary run
   rather than the multi-year transaction the 7-day default would have produced.
   Record the `deleted` breakdown and `duration_ms` in the PR; they are the
   baseline for deciding whether the window should ever narrow.

---

## 14. Flags on the chosen design

1. **Hourly dispatch caps the vocabulary's usefulness at hourly granularity.**
   `Daily(2, 15)` fires somewhere in `[02:15, 03:07)` Mountain. Fine for pruning
   and for email; "at 09:00 sharp" is not expressible. If a task ever needs
   punctuality, the answer is § 10's daemon, not a faster cron.
2. **Nothing alerts.** A failed Job is visible only to someone looking at ArgoCD or
   `kubectl`. The § 9 watchdog is the cheapest fix and should be the second task
   written.

Two flags from the first draft are resolved rather than carried: *"which database
is this fact in?"* is answered in § 4.1 with a reason that will still hold in a
year, and *"the first production DELETE is unbounded"* is dissolved by the 365-day
default in § 3.1. A third was simply wrong and is corrected in § 13 step 3.

The last open item is **closed**: `.spec.timeZone` requires k8s ≥ 1.25 (GA
1.27), and CIRRUS/nwc1 runs **v1.35.6** (verified 2026-08-12). The field stays
`{{- with }}`-gated so `tasks.timeZone: ""` can drop it, but as belt-and-braces.

---

## 15. Deviations, as built (P0–P4, PR #444)

Where the implementation departed from this document. Everything else was built
as written.

| Deviation | Why |
|---|---|
| **`task_run.trigger` → `trigger_type`** (§ 4.2) | Reserved word in MySQL *and* Postgres. Found by running the plan's own "read the ledger at 09:00" workflow against a real database and getting a syntax error. Free now; a migration after prod. |
| **Spans pruned by an explicit `timestamp < cutoff` DELETE, not CASCADE** (§ 3.1) | Same semantic, but CASCADE is unenforceable on SQLite (no `PRAGMA foreign_keys`) and bypassed by bulk `delete()`, so the tested mechanism and the production one would have differed. |
| **`Hourly` takes no `tz`** (§ 2.2 said every constructor takes one) | It computes on the UTC clock. Every zone SAM uses is a whole-hour offset so the instants are identical, while a local-wall hourly schedule loses a slot each fall. It *refuses* a `tz` rather than ignoring one. |
| **`run_due` checks the ledger before checking lateness** (§ 5's order) | A daily 02:15 task with a 6 h grace is "late" for eighteen hours a day. Checking lateness first declared a misfire — and re-walked the backfill — on every dispatch from 15:07 for a slot that had already succeeded that morning. Caught by a test. |
| **`sam-admin tasks` is one command with mode flags**, not three command classes (§ 7) | Matches `xras`, the newest sibling, and `cmds/admin.py` has no nested groups. `src/cli/README.md` allows either. |
| **`make helm-test` globs `helm/tests/*.sh`** (§ 13 suggested the target; CI named one script) | Naming scripts individually is how the second render test gets written and then silently never runs. |
| **A stdout `print` had to be removed first** (not in the plan at all) | `system_status.session` printed a redacted connection string at import, which corrupted every `--format json` envelope. The CronJob is log-scraped, so this was a hard blocker. |
| **Per-table `RETENTION_DAYS` left empty** (§ 3.1 item 3 wanted numbers) | `csg-postgres` was not reachable. The mechanism is built and tested; the first production run's `deleted` breakdown is the measurement. |

### Tier 1 — the admin card (§ 9)

| Deviation | Why |
|---|---|
| **Details page is `VIEW_SYSTEM_CONFIG`; only the detail modal is `SYSTEM_ADMIN`** (§ 9 said all-`SYSTEM_ADMIN`) | § 9 copied the Notifications gating, but its stated reason — *"every row names a real person's email address"* — does not transfer. Task rows carry task names, states and pod names. Only `detail` (tracebacks, which can name hosts and paths) warrants the higher tier. `rate_limits_routes.py` is the same-tier precedent. |
| **The facade lives in a new top-level `src/querykit/`**, not `sam/queries/faceted.py` | `sam/` and `system_status/` import nothing from each other; putting it in `sam/queries/` would create the reverse of the edge § 6.1 protects. And not under `webapp/` either: `src/cli/xras/builders.py` already imports `summarize_xras_actions`, the function the facade absorbs on the XRAS retrofit, so that would put `webapp` in `sam-admin xras`'s import graph. A peer creates zero edges. |
| **The notifications log adopted the shared `pagination()` macro** (not in the plan) | It hand-rolled a Newer/Older pager while `pagination.html` already existed and XRAS used it. Declared as a visible change rather than smuggled in — and noticing it is why the tasks page was not a copy-paste of `notifications_log.html`, which would have triplicated the weaker pager. |
| **No `tests/factories/scheduling.py`** (the dashboard plan § 8 wanted one) | `tests/factories/` targets the SAM bind only. Status rows use module-private `_make_*` helpers against `status_session` — and because that bind is a per-worker SQLite tempfile with per-test DELETE isolation, those tests may `commit()`, so they assert on rendered HTML rather than on a state dict. The SAVEPOINT constraint that shaped the notifications card tests does not apply here. |
| **`badges.html`'s vocabulary gate now covers all three domains** | Only `XRAS_ACTION_STATUSES` was asserted against it; `NOTIFICATION_STATUSES` had no gate at all. Adding `TASK_STATES` without closing that gap would have left the same trap for the next domain. |
| **Browser smoke is 2 scenarios, not 6** (dashboard plan § 9 listed six) | The `unavailable` degrade needs a table-less database and the e2e tier has no monkeypatch seam; the 403 boundary is cheaper at the unit tier. Card-renders and kill-switch-warning are the two that are genuinely about pixels. |
