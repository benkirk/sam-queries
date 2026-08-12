# Scheduled tasks: a ledger-backed dispatcher

**Status: PROPOSED (2026-08-09).** No code has been written. This document is the
design; it exists to settle the architecture before anyone writes YAML.

SAM has periodic work and no in-cluster place to run it. `helm/templates/` holds
seven templates and no Job, CronJob, ConfigMap, or ServiceAccount — the chart
deploys the webapp and a Redis sidecar, nothing else. Everything periodic runs from
a personal crontab on a Glade login host (`collectors/cron_scripts/crontab`,
`scripts/cron/accounting/crontab`), wrapped in `flock` + `ssh` against a conda env on
a shared filesystem. `scripts/cleanup_status_data.py` says "intended to be run daily
via cron" and nothing schedules it at all. The NRIT review flagged both
(`docs/nrit-review-2026-05/06_platform.md` D9, `08_action_register.md` P1-53, open
questions Q15 / Q42).

This plan adds **one hourly dispatcher CronJob** and a **`task_run` ledger** in
`system_status`, and wires the status-snapshot cleanup as the first real task.

The load-bearing idea is in § 2: a schedule does not answer *"is it now?"* — it
answers *"what is the most recent occurrence that should have run at or before
`now`?"*. Idempotence, self-healing after an outage, mutual exclusion against a
future daemon, and safe manual re-runs all fall out of that one choice plus a UNIQUE
constraint.

---

## 1. Roadmap

| Phase | Ships | Depends on | Touches k8s? |
|---|---|---|---|
| **P0** | Preconditions: lazy SAM connect in `sam-admin`; `cleanup_old_data(cutoff=…)` | — | no |
| **P1** | `src/scheduling/schedules.py` — pure predicate vocabulary + unit tests | — | no |
| **P2** | Alembic `0006_task_run`, `TaskRun` model, `ledger.py` | P1 | no |
| **P3** | Registry, `run_due()`, `cleanup_status_snapshots`, `sam-admin tasks` | P2 | no |
| **P4** | `helm/templates/cronjob-tasks.yaml` + render test, Docker Desktop validation | P3 | yes |
| **P5** | Production enable: ship kill-switched, soak, then remove the kill switch | P4 | yes |
| *later* | Admin dashboard card (§ 9), expiration emails (§ 12), daemon (§ 10) | P3 | no |

P1–P3 are testable with `pytest` alone and are worth landing before anyone argues
about YAML.

### Goals

1. **Idempotent by construction.** Running the dispatcher twice in one minute, or
   three times after a three-day outage, has the same effect as running it once at
   the right time.
2. **One scheduler object in the cluster**, not one per task. Adding a task is a
   Python change, not a chart change.
3. **A durable record.** CronJob pods and their logs evaporate; the ledger is what
   you read at 09:00 to answer "did the cleanup run last night?"
4. **A scheduler-agnostic core**, so a future always-on agent calls the same
   `run_due()` and the CronJob demotes to a fallback (§ 10).

**Non-goals**, listed so nobody grows them in by accident: sub-minute triggers,
DAGs, fan-out, backpressure, cluster leader election, a retry engine.

---

## 2. The core abstraction: occurrence predicates

### 2.1 The contract

```python
# src/scheduling/schedules.py  — stdlib only, no SQLAlchemy, no config imports
class Schedule(Protocol):
    def last_occurrence(self, now_utc: datetime) -> datetime | None:
        """Most recent scheduled instant at or before `now_utc`.

        Returns a NAIVE UTC datetime truncated to the second, or None if the
        schedule has no occurrence at or before `now_utc`.
        Pure: same input -> same output, no clock reads, no I/O.
        """

    def next_occurrence(self, after_utc: datetime) -> datetime | None: ...   # display only
    def describe(self) -> str: ...            # "daily at 02:15 America/Denver"
```

`last_occurrence` is the whole framework. The occurrence it returns, rendered as a
string, is the **occurrence key** — dedup key, lock key, and the ledger's business
identifier:

```python
def occurrence_key(occ: datetime) -> str:
    return occ.strftime('%Y%m%dT%H%M%SZ')     # fixed width, sorts lexically == chronologically
```

**Why this and not a boolean "is it due?"** A boolean has to be asked at exactly the
right moment, which means either a high-rate poll or a scheduler you must trust never
to miss a tick. An occurrence key is a *name for the slot*. The dispatcher asks "what
slot are we in?", tries to claim it, and either wins (runs) or loses (someone already
did it). Lateness becomes harmless. Duplicate dispatchers become harmless. A manual
re-run becomes expressible.

`next_occurrence` exists only to render a "next due" column. **Nothing in the control
flow may call it** — that is a design rule, not a style preference. A scheduler that
reasons forward has to be right about when it wakes up, and this one is deliberately
not.

### 2.2 The vocabulary

| Constructor | Meaning | Notes |
|---|---|---|
| `Hourly(minute=0)` | every hour at `:minute` | |
| `Daily(hour, minute)` | every day at HH:MM | |
| `Weekly(weekday, hour, minute)` | `weekday` 0=Mon … 6=Sun | |
| `MonthlyDay(day, hour, minute)` | day-of-month; **negative counts from the end** (`-1` = last day) | `day=29..31` in a short month clamps to the last day — documented, not skipped |
| `MonthlyBusinessDay(n, hour, minute)` | *n*-th Mon–Fri of the month; `n=1` is the motivating case. Negative counts back from month end | **No holiday calendar.** Jan 1 is a business day to this predicate. If that ever matters it becomes a table, not a guess |
| `CronExpr("7 * * * *", horizon=timedelta(hours=48))` | raw 5-field escape hatch | see below |

Every constructor takes `tz: str = "America/Denver"` (IANA name, stdlib `zoneinfo`).

The cron escape hatch is a **bounded backward scan**: start at `now` truncated to the
minute, step back one minute at a time, return the first minute matching the
expression, give up after `horizon` (default 48 h) and raise. At most 2,880 integer
comparisons — microseconds — and it buys the escape hatch with **no new runtime
dependency**. The cost is an honest restriction: *a `CronExpr` must fire at least once
every 48 hours.* Anything rarer must use a named predicate, which is better
documentation anyway. (`croniter.get_prev()` is exactly this primitive if the bound
ever binds; swapping it in is confined to one class.)

### 2.3 Timezone — the part that will bite

Three clocks are in play and they disagree:

| Clock | Value | Set by |
|---|---|---|
| SAM MySQL | naive **Mountain** | vendor schema; `helm/values.yaml` sets `TZ: "America/Denver"` for exactly this reason |
| `system_status` | naive **UTC** | `src/system_status/timeutil.py:17` `utcnow_naive()` |
| Human intent | "nightly at 2 a.m." — **Mountain**, and it should stay 2 a.m. across DST | operators |

**Decision.** Occurrences are computed in the task's declared zone (default
`America/Denver`) and canonicalized to naive UTC for the key and the ledger. The
ledger never stores a local time and never stores a tz name; the registry is the only
place a zone appears.

Two DST rules, stated because leaving them implicit is how you get a duplicate
nightly run once a year:

- **Ambiguous local times** (fall back, 01:00–02:00 MDT→MST): always resolve with
  `fold=0`, the *earlier* UTC instant. A `Daily(1, 30)` task therefore fires once on
  the fall-back day, at 07:30 UTC. During the repeated hour `last_occurrence` still
  maps the same wall clock to the same UTC instant, so the second pass claims an
  already-succeeded key and does nothing.
- **Nonexistent local times** (spring forward, 02:00–03:00): shift forward to the
  first instant that exists (02:30 → 03:00 local). `Daily(2, 15)` fires ~45 min late
  on that one day, and never silently skips a day.

Both rules are pure functions of their input and get parametrized tests against real
transition dates (§ 13). The doc-level guidance is still *prefer HH:MM outside
01:00–03:00* and you never exercise either rule. The first task is nevertheless
specified at 02:15 Denver: the rules are written down and tested, and moving snapshot
pruning off the quiet hours to dodge a tested code path is superstition.

Separately: **the dispatcher pod stays on `TZ: "America/Denver"`**, matching the
webapp. It is tempting to pin it to UTC since its first task is UTC-only — resist.
The moment the expiration task lands (§ 12) it reads SAM's naive-Mountain dates, and
a UTC pod reproduces exactly the "freshly-granted access reads back as inactive" bug
documented in `helm/values.yaml`. One TZ for every SAM pod; all UTC handling explicit
in code. Which forces the precondition below.

---

## 3. Preconditions (P0)

Two pre-existing bugs sit directly in the path. Both are independently reviewable and
should land before P3.

### 3.1 `scripts/cleanup_status_data.py:44` computes its cutoff in the wrong zone

```python
cutoff_date = datetime.now() - timedelta(days=retention_days)   # local
...
session.query(model_class).filter(model_class.timestamp < cutoff_date)   # naive UTC
```

On a Denver host the cutoff lands 6–7 h earlier than intended. Today that is benign
(it retains ~7.27 days instead of 7.0), but the sign of the error is an accident of
longitude. Refactor so the function can be called library-style and takes its cutoff
from the caller:

```python
def cleanup_old_data(retention_days=7, dry_run=False, cutoff=None, session=None):
    if cutoff is None:
        from system_status.timeutil import utcnow_naive
        cutoff = utcnow_naive() - timedelta(days=retention_days)
```

Two notes for the record. `utcnow_naive` lives at `src/system_status/timeutil.py:17`
and **nowhere else** — `sam/fmt.py` has no such symbol, despite what `CLAUDE.md` § 1
implies. And `src/sam/` imports nothing from `system_status` today; keep it that way
(§ 6.1). The script itself stays — it is documented, and running it by hand on a
workstation is legitimate; the task imports its function.

### 3.2 `sam-admin`'s group callback connects to SAM MySQL for every subcommand

`src/cli/cmds/admin.py:50-59` creates an engine and `Session` unconditionally,
exiting 1 on failure. As written, `sam-admin tasks --run-due` dies when SAM MySQL is
unreachable even though the only registered task touches Postgres — converting a SAM
outage into a `system_status` retention outage, which is precisely the coupling this
framework exists to remove.

Keep `SAMConfig.validate()` (the env-var check is cheap and catches misconfiguration);
defer the connect:

```python
# src/cli/core/context.py
def require_sam(self) -> Session:
    if self.session is None:
        engine, _ = create_sam_engine()
        self.session = Session(engine)
    return self.session
```

Every existing command reaches the session through `BaseCommand.__init__`, so the
change is: `BaseCommand.__init__` calls `ctx.require_sam()`, the group callback stops
connecting eagerly, and the tasks command does neither. One line per touchpoint, no
behaviour change for existing commands.

---

## 4. The ledger

### 4.1 Schema

New model `src/system_status/models/task_run.py`, exported from
`src/system_status/models/__init__.py`; migration
`migrations/system_status/versions/0006_task_run.py`
(`down_revision = "0005_queue_def_roster_columns"`).

The model **must** live there: `migrations/system_status/env.py` builds
`target_metadata` by importing `system_status.models`, so a model anywhere else is
invisible to autogenerate and to the drift test.

| Column | Type | Null | Notes |
|---|---|---|---|
| `task_run_id` | `Integer` PK autoincrement | no | |
| `task_name` | `String(64)` | no | registry key, e.g. `cleanup_status_snapshots` |
| `occurrence_key` | `String(24)` | no | `20260810T081500Z`, or `M20260809T143002Z` for a forced run (§ 7.2) |
| `state` | `String(16)` | no | `running` / `succeeded` / `partial` / `failed` / `skipped` |
| `trigger` | `String(16)` | no | `schedule` / `catchup` / `manual` |
| `attempt` | `SmallInteger` | no | default 1; bumped only by a stale reclaim |
| `claimed_at` | `DateTime` | no | naive UTC |
| `heartbeat_at` | `DateTime` | no | naive UTC; the lease |
| `finished_at` | `DateTime` | yes | NULL while `running` |
| `duration_ms` | `Integer` | yes | |
| `runner_id` | `String(64)` | yes | pod name, for forensics against `kubectl logs` |
| `detail` | `Text` | yes | JSON: `TaskResult.detail`, or a truncated traceback on failure |

Constraints and indexes, named by `STATUS_NAMING_CONVENTION`
(`src/system_status/base.py:26`):

- `uq_task_run_task_name_occurrence_key` — **UNIQUE (`task_name`, `occurrence_key`)**.
  This single constraint is the dedup key *and* the mutual-exclusion lock.
- `ix_task_run_task_name_claimed_at` — serves both "last run of task X" and the
  history listing.
- `ix_task_run_state` — the stale sweep.

`detail` is `Text` holding JSON rather than a JSON column type: MySQL is still the
default `STATUS_DB_DRIVER` and tests run on SQLite. Portability over queryability,
consistent with the rest of `system_status`.

### 4.2 State machine

```
                 INSERT (unique wins)
        ∅ ─────────────────────────────► running ──► succeeded
        │                                   │    └──► partial
        │  UPDATE…WHERE state='running'     ├──► failed
        │      AND heartbeat_at < cutoff    │
        └───────────── (reclaim, attempt+1) ┘

        ∅ ─────────────────────────────► skipped   (misfire / kill-switch / SKIP backfill)
```

There is deliberately **no `claimed` state distinct from `running`**. The INSERT *is*
the claim, and it happens microseconds before the task body starts in the same
process; a separate `claimed` row would be observable only during a window nobody can
query, and would add a transition that can itself fail. If the process dies between
claim and first heartbeat, the row is `running` with a stale `heartbeat_at` — which is
exactly the case the reclaim rule already handles.

### 4.3 The two locking primitives, and why they are these

Production is Postgres (`STATUS_DB_DRIVER: postgresql`,
`csg-postgres.k8s.ucar.edu`), tests are SQLite, and the config default is MySQL. So
`SELECT … FOR UPDATE SKIP LOCKED` (no SQLite), MySQL `GET_LOCK`, and Postgres advisory
locks are all out. What remains is portable and, happily, sufficient.

**Primitive A — claim a new occurrence (INSERT, catch the unique violation).**

```python
def claim(session_factory, task_name, key, *, trigger, runner_id, now) -> TaskRun | None:
    """Return the owned row, or None if someone else owns this occurrence."""
    with session_factory() as s:                  # its OWN short transaction
        s.add(TaskRun(task_name=task_name, occurrence_key=key, state='running',
                      trigger=trigger, attempt=1, claimed_at=now,
                      heartbeat_at=now, runner_id=runner_id))
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
            return None
        return _detach(s, ...)
```

The dedicated short-lived session is not stylistic. On Postgres an `IntegrityError`
aborts the whole transaction, and any subsequent statement on that connection fails
with `InFailedSqlTransaction`. Claiming on the same session the task body will use
would poison it. The ledger therefore owns its own `sessionmaker` off the
`system_status` engine, separate from the task's sessions.

**Primitive B — reclaim a stale occurrence (conditional UPDATE, check `rowcount`).**

```sql
UPDATE task_run
   SET state='running', runner_id=:me, attempt=attempt+1,
       claimed_at=:now, heartbeat_at=:now, finished_at=NULL
 WHERE task_name=:name AND occurrence_key=:key
   AND state='running' AND heartbeat_at < :cutoff
```

`rowcount == 1` means we won; `0` means someone beat us or the row recovered. A
single-statement conditional UPDATE is atomic under every isolation level on all three
backends, needs no explicit locking syntax, and is the same compare-and-swap a
`SELECT … FOR UPDATE` would give with less portability.

`ledger.py` must contain **no dialect-specific SQL whatsoever** — § 13 proposes a
boundary test that enforces it. CI runs SQLite and production runs Postgres, so a test
that greps for `SKIP LOCKED` is the only thing standing between those two facts.

### 4.4 Leases, heartbeats, retries, retention

- **Lease** = `heartbeat_at + max(3 × task.expected_runtime, 900 s)`. Past that,
  primitive B may steal the row. `expected_runtime` is declared in the registry.
- **Heartbeat.** The runner bumps `heartbeat_at` before and after each task, and —
  only for tasks declaring `long_running=True` — from a daemon thread every 60 s. No
  phase-1 task declares it; a heartbeat thread for a 20-second DELETE is ceremony.
- **Retries.** A `failed` row is terminal for its occurrence by default. This is the
  opinionated bit: **prefer self-healing tasks to a retry engine.** Tonight's cleanup
  failing costs nothing, because tomorrow night's cleanup deletes the same rows plus
  one more day. Tasks whose occurrences are genuinely non-fungible may set
  `retry=(max_attempts, backoff)`, letting primitive B also match
  `state='failed' AND attempt < max AND finished_at < now-backoff`. Nothing uses it in
  phase 1; it exists so the schema needn't change when something does.
- **Retention.** Rows older than 180 days are pruned by the cleanup task itself,
  guarded `WHERE finished_at IS NOT NULL AND finished_at < cutoff` so a run can never
  delete its own live row. The thing whose job is bounding growth also bounds the
  ledger's, which costs four lines.

---

## 5. Catch-up and misfire

Two independent per-task knobs, both defaulted so the common case is invisible:

```python
class CatchUp(Enum):
    SKIP = 'skip'   # default: only the most recent occurrence is a candidate
    ALL  = 'all'    # replay every missed occurrence, oldest first

misfire_grace: timedelta = timedelta(hours=6)
max_catchup:   int       = 7        # only meaningful under ALL
```

The dispatch decision in full:

```python
occ = task.schedule.last_occurrence(now)
if occ is None:                     -> nothing to do
if now - occ > task.misfire_grace:  -> record 'skipped' (detail: reason, lateness)
else:                               -> attempt to claim occ and run it
```

**The three-day-outage question, answered directly.** With `SKIP` and a 6 h grace, a
daily task after a three-day dispatcher outage runs **once** — for last night's slot —
and the two missed nights are written as `skipped` rows carrying
`detail={"reason":"misfire","late_by_s":…}`. The backfill walks backwards from the
current occurrence for at most 32 steps, then writes one summary row; that bound stops
a task disabled for a year from generating 365 INSERTs on the day someone re-enables
it.

Backfilling the skips costs a handful of rows and buys a real benefit:
`sam-admin tasks --history` shows the outage *as an outage*. The alternative — silence
— makes a three-day gap look identical to a task that was never registered.

`ALL` exists for tasks whose occurrences do distinct work (a per-day ingest). Nothing
uses it in phase 1. The design note that matters: under `ALL` the runner claims and
runs oldest-first and **stops at the first failure**, so a broken day cannot be
silently skipped over on the way to today.

Worked example — the real one. `cleanup_status_snapshots`,
`Daily(2, 15, tz="America/Denver")`, dispatcher hourly at `:07` UTC:

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
├── __init__.py                  # re-exports run_due, TASKS
├── schedules.py                 # PURE. stdlib only. no SQLAlchemy, no config.
├── registry.py                  # Task dataclass, @task decorator, TASKS dict
├── ledger.py                    # TaskRun claim/heartbeat/finish/prune
├── runner.py                    # run_due() — the entire scheduler-facing API
└── tasks/
    ├── __init__.py              # side-effect imports that populate TASKS
    └── cleanup_status.py        # the first real task
src/cli/tasks/                   # the Click/Rich surface, per cli/README.md
├── __init__.py  commands.py  display.py  builders.py
src/system_status/models/task_run.py     # the ORM model (Alembic must see it here)
```

**Rejected: `src/sam/scheduling/`.** `src/sam/` is the domain package for the SAM
MySQL database. It does hold cross-cutting utilities (`fmt.py`, `plugins.py`,
`provisioning.py`), so it is not *purely* models — but it currently imports nothing
from `system_status`, and the ledger would invert that. A scheduler living in the
SAM-DB package while writing to the status DB is a dependency edge somebody will
regret.

**Rejected: `src/cli/tasks/` as the home of the engine.** The CLI is a presentation
layer. A future APScheduler Deployment (§ 10) must be able to
`from scheduling.runner import run_due` without importing Click, Rich, or the
`sam-admin` group callback. `src/cli/tasks/` still exists — it holds the command
classes and display functions, per the `builders` / `commands` / `display` split that
`src/cli/README.md` mandates.

### 6.2 Declaring a task

```python
# src/scheduling/registry.py
@dataclass(frozen=True)
class Task:
    name: str                                  # ledger key; stable forever — renaming orphans history
    schedule: Schedule
    fn: Callable[[TaskContext], TaskResult]
    needs: tuple[str, ...] = ('status',)       # subset of ('sam', 'status')
    catchup: CatchUp = CatchUp.SKIP
    misfire_grace: timedelta = timedelta(hours=6)
    expected_runtime: timedelta = timedelta(seconds=60)
    max_catchup: int = 7
    long_running: bool = False
    description: str = ''

TASKS: dict[str, Task] = {}

def task(*, name, schedule, **kw):
    def deco(fn):
        if name in TASKS:
            raise ValueError(f'duplicate task {name!r}')
        TASKS[name] = Task(name=name, schedule=schedule, fn=fn,
                           description=(fn.__doc__ or '').strip(), **kw)
        return fn
    return deco
```

A decorator rather than a list of dataclasses, because the schedule belongs next to
the function it schedules — the alternative puts the two halves of every task in
different files and guarantees drift. Discoverability is recovered the way
`src/system_status/models/__init__.py` already does it: an explicit list of
side-effect imports in `scheduling/tasks/__init__.py`, the one place to grep for
"what tasks exist".

The first task:

```python
# src/scheduling/tasks/cleanup_status.py
@task(name='cleanup_status_snapshots',
      schedule=Daily(2, 15, tz='America/Denver'),
      needs=('status',),
      expected_runtime=timedelta(minutes=2))
def cleanup_status_snapshots(ctx: TaskContext) -> TaskResult:
    """Prune system_status snapshot rows older than the retention window."""
    retention = int(os.getenv('STATUS_RETENTION_DAYS', '7'))
    cutoff = ctx.occurrence - timedelta(days=retention)      # NOT utcnow(): keyed to the slot
    counts = cleanup_old_data(cutoff=cutoff, dry_run=ctx.dry_run, session=ctx.status_session)
    pruned = prune_task_runs(ctx.status_session, older_than=ctx.occurrence - timedelta(days=180))
    return TaskResult(detail={'deleted': counts, 'task_run_pruned': pruned})
```

Note `cutoff = ctx.occurrence - retention`, not `now - retention`. **A task computes
from its occurrence, never from the wall clock.** That is what makes a late run
produce the same result as a punctual one, which is what makes the scheme
deterministic and testable. It belongs in the module docstring, because it is the
single easiest thing for a future task author to get wrong.

### 6.3 What a task receives and returns

```python
@dataclass(frozen=True)
class TaskContext:
    now: datetime            # naive UTC, the dispatch instant
    occurrence: datetime     # naive UTC, the slot being filled   <-- compute from THIS
    dry_run: bool
    logger: logging.Logger   # namespaced 'sam.tasks.<name>'
    _sessions: _LazySessions # .sam_session / .status_session, opened on first access

@dataclass
class TaskResult:
    detail: dict = field(default_factory=dict)   # must be JSON-serializable
    message: str = ''
    partial_failures: int = 0                    # >0 -> ledger state 'partial'
```

- Return normally → `succeeded`, or `partial` if `partial_failures > 0`.
- Raise → `failed`, with `detail = {"error": repr(exc), "traceback": tb[-4000:]}`.
  The runner catches `Exception`, not `BaseException`: a `KeyboardInterrupt` /
  `SystemExit` must propagate so the pod's `activeDeadlineSeconds` kill leaves the row
  `running`-and-stale for the reclaim path rather than mislabelling it.
- `partial` is a real state rather than a flag inside `detail` because the expiration
  task will genuinely produce "23 of 25 emails sent", and an operator scanning a status
  column should not have to open JSON to notice.

Sessions are lazy and driven by `needs`, so a status-only task never touches SAM MySQL
— which is what § 3.2 makes possible.

### 6.4 The runner

```python
# src/scheduling/runner.py — the ENTIRE scheduler-facing API
def run_due(*, now: datetime,               # injected; never read from the clock in here
            only: str | None = None,
            force: bool = False,
            dry_run: bool = False,
            registry: dict[str, Task] = TASKS,
            ledger: Ledger) -> DispatchReport
```

Per task, in registry order:

1. **Kill switch.** `SAM_TASKS_DISABLED` (comma-separated names) → record `skipped`
   with `detail={"reason":"disabled"}`, continue. GitOps-flippable in `values.yaml`
   without a code deploy; used by the P5 rollout.
2. **Stale sweep** for this task (primitive B) — reclaims a crashed pod's row, at most
   one per dispatch.
3. **Dueness** per § 5. Misfires → `skipped`.
4. **Claim** (primitive A). Lost → continue *silently*; this is the normal case for
   every dispatch after the first in a slot and must not log at WARNING, or the logs
   are 23/24 noise.
5. **Run** inside `try/except`, timed; open only the sessions `needs` asks for; commit
   or roll back the task's own sessions independently of the ledger session.
6. **Finish**: `state`, `finished_at`, `duration_ms`, `detail`, one commit.

Tasks run **strictly serially** in one process. With three tasks and a two-minute
worst case, concurrency is unjustifiable complexity; if it ever becomes justifiable,
the ledger already makes parallel dispatchers safe, so the answer is "two CronJobs with
disjoint `--only` sets", not a thread pool.

---

## 7. CLI surface

`sam-admin tasks`, per `src/cli/README.md` § *Adding New Commands*:
`TasksListCommand` / `TasksDispatchCommand` / `TasksHistoryCommand` in
`src/cli/tasks/commands.py` extending `BaseCommand`; builders returning plain dicts;
`display_*` functions taking dicts only.

| Flag | Mode | Behaviour |
|---|---|---|
| *(none)* / `--list` | query | registry + latest ledger row per task |
| `--run-due` | dispatch | the CronJob entry point |
| `--run <name>` | dispatch | one task, ignoring dueness |
| `--history` | query | recent rows; `--task <name>`, `--limit N` (default 20) |
| `--dry-run` | modifier | requires a dispatch mode; **no ledger writes** |
| `--force` | modifier | requires `--run`; manual occurrence key |
| `--task <name>` | modifier | filters `--history` |

Modes are mutually exclusive; violating that is exit 2 with a message, following the
`--notify requires --upcoming-expirations` precedent at `src/cli/cmds/admin.py:117-135`.

### 7.1 `--dry-run` writes no ledger row at all

A dry run that claimed the slot would prevent the real run — the worst possible
failure mode for a safety flag. It prints what it *would* claim; the JSON envelope
reports `"would_claim": [...]`.

### 7.2 `--force` writes a manual key

`occurrence_key = "M" + now.strftime('%Y%m%dT%H%M%SZ')`, `trigger='manual'`. The
leading `M` means it cannot collide with a scheduled key, so it never satisfies a
scheduled slot — and that consequence must be documented at the flag: *a forced 10:00
run of the cleanup does not stop tonight's 02:15 run.* The alternative — deleting or
superseding the scheduled row — makes history lie, and history is the entire product
here. Without `--force`, `--run <name>` fills the real slot and refuses (exit 2) if
that slot already `succeeded`.

### 7.3 Output

```
                        Scheduled tasks
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━┓
┃ Task                      ┃ Schedule          ┃ Last occurrence  ┃ State     ┃ Age     ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━┩
│ cleanup_status_snapshots  │ daily 02:15 MT    │ 2026-08-09 08:15 │ succeeded │ 6h 12m  │
└───────────────────────────┴───────────────────┴──────────────────┴───────────┴─────────┘
```

JSON envelopes carry a top-level `kind` per convention: `task_list`, `task_dispatch`,
`task_history`.

```json
{"kind": "task_dispatch", "now": "2026-08-09T14:07:03",
 "results": [{"task": "cleanup_status_snapshots", "occurrence": "2026-08-09T08:15:00",
              "outcome": "already_claimed"}],
 "counts": {"succeeded": 0, "failed": 0, "skipped": 0, "already_claimed": 1}}
```

### 7.4 Exit codes

| Code | When |
|---|---|
| `EXIT_SUCCESS` 0 | nothing due; all due tasks succeeded; every slot already claimed by a peer |
| `EXIT_NOT_FOUND` 1 | `--run <unknown>` / `--history --task <unknown>` |
| `EXIT_ERROR` 2 | ≥1 task `failed` or `partial`, or a bad flag combination |

This follows the *audit* convention in `src/cli/README.md` § *Exit Codes* —
`EXIT_ERROR` overloaded to mean "findings exist" so CI can gate on it. Here the "CI"
is Kubernetes: a nonzero exit makes the Job `Failed`, which is the only free alerting
channel this deployment has.

### 7.5 One documented carve-out

`src/cli/README.md` says `--format json` combined with side-effecting flags is
rejected (`json_unsupported_for_writes`, exit 2). `--run-due` is inherently
side-effecting, and JSON is exactly what a log-scraped dispatcher should emit. So
`--format json --run-due` is **allowed**, and `src/cli/README.md` gets one sentence
recording the carve-out. The rule exists to stop someone accidentally emailing while
scripting a *report*; here the side effect **is** the command. The guard stays in
force for `--notify` (§ 12.6), where the original hazard is real.

---

## 8. The Helm CronJob

New `helm/templates/cronjob-tasks.yaml`. The chart has no `_helpers.tpl` and inlines
labels per template; follow that rather than introducing a helper for one new file.

```yaml
{{- if .Values.tasks.enabled }}
apiVersion: batch/v1
kind: CronJob
metadata:
  name: {{ .Values.tasks.name }}
  namespace: {{ .Release.Namespace }}
  labels:
    app: {{ .Values.webapp.name }}
    group: {{ .Values.webapp.group }}
    component: tasks
spec:
  schedule: {{ .Values.tasks.schedule | quote }}
  {{- with .Values.tasks.timeZone }}
  # k8s >= 1.25 (GA 1.27). Pinned to UTC so the CronJob CONTROLLER never sees a
  # DST gap or fold. Task dueness is decided in Python (America/Denver), not
  # here — this field only controls how often we knock on the door.
  timeZone: {{ . | quote }}
  {{- end }}
  suspend: {{ .Values.tasks.suspend | default false }}
  concurrencyPolicy: {{ .Values.tasks.concurrencyPolicy | default "Forbid" }}
  startingDeadlineSeconds: {{ .Values.tasks.startingDeadlineSeconds | default 600 }}
  successfulJobsHistoryLimit: {{ .Values.tasks.successfulJobsHistoryLimit | default 3 }}
  failedJobsHistoryLimit: {{ .Values.tasks.failedJobsHistoryLimit | default 5 }}
  jobTemplate:
    spec:
      backoffLimit: {{ .Values.tasks.backoffLimit | default 0 }}
      activeDeadlineSeconds: {{ .Values.tasks.activeDeadlineSeconds | default 3000 }}
      template:
        metadata:
          labels:
            app: {{ .Values.webapp.name }}
            component: tasks
        spec:
          restartPolicy: Never
          # The dispatcher never talks to the k8s API. Same reasoning as
          # deployment.yaml:30 — don't hand a code-exec foothold a token.
          automountServiceAccountToken: false
          {{- with .Values.webapp.securityContext.pod }}
          securityContext:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          containers:
          - name: tasks
            # DELIBERATELY .Values.webapp.container.image, with no tasks-specific
            # image key. CI (.github/workflows/build-images-cirrus-deploy.yaml,
            # update-helm job) rewrites lines matching
            # `image: ghcr.io/<repo>/webapp:` — a separate key would either be
            # missed (pinned forever at :main) or silently co-rewritten, giving
            # two sources of truth for one image. sam-admin and /code/scripts/*.py
            # are already in this image; only the command differs.
            image: {{ .Values.webapp.container.image }}
            imagePullPolicy: IfNotPresent
            command: ["sam-admin"]
            args:
              - "--format"
              - "json"
              - "tasks"
              {{- range .Values.tasks.args }}
              - {{ . | quote }}
              {{- end }}
            {{- with .Values.webapp.securityContext.container }}
            securityContext:
              {{- toYaml . | nindent 14 }}
            {{- end }}
            env:
              # Mountain, matching the webapp: SAM MySQL is naive-local and a
              # future task reads it. system_status handling is explicitly UTC
              # in code (system_status/timeutil.py). See the values.yaml TZ comment.
              - name: TZ
                value: {{ .Values.webapp.env.TZ | default "America/Denver" | quote }}
              - name: SAM_DB_SERVER
                value: {{ .Values.webapp.env.SAM_DB_SERVER | quote }}
              - name: SAM_DB_REQUIRE_SSL
                value: {{ .Values.webapp.env.SAM_DB_REQUIRE_SSL | quote }}
              - name: STATUS_DB_DRIVER
                value: {{ .Values.webapp.env.STATUS_DB_DRIVER | quote }}
              - name: STATUS_DB_SERVER
                value: {{ .Values.webapp.env.STATUS_DB_SERVER | quote }}
              {{- range $k, $v := .Values.tasks.env }}
              - name: {{ $k }}
                value: {{ $v | quote }}
              {{- end }}
              - name: RUNNER_ID           # -> TaskRun.runner_id, ties a row to `kubectl logs`
                valueFrom:
                  fieldRef: {fieldPath: metadata.name}
              # The same namespace-scoped Secrets the Deployment consumes; the six
              # existing ExternalSecrets already materialize them. No new ESO
              # resource, no new OpenBao path.
              {{- if .Values.webapp.dbCredentials.enabled }}
              - name: STATUS_DB_USERNAME
                valueFrom:
                  secretKeyRef: {name: {{ .Values.webapp.name }}-db-credentials, key: username}
              - name: STATUS_DB_PASSWORD
                valueFrom:
                  secretKeyRef: {name: {{ .Values.webapp.name }}-db-credentials, key: password}
              {{- end }}
              {{- if .Values.webapp.samDbCredentials.enabled }}
              - name: SAM_DB_USERNAME
                valueFrom:
                  secretKeyRef: {name: {{ .Values.webapp.name }}-sam-db-credentials, key: username}
              - name: SAM_DB_PASSWORD
                valueFrom:
                  secretKeyRef: {name: {{ .Values.webapp.name }}-sam-db-credentials, key: password}
              {{- end }}
            resources:
              requests:
                memory: {{ .Values.tasks.requests.memory }}
                cpu: {{ .Values.tasks.requests.cpu }}
              limits:
                memory: {{ .Values.tasks.limits.memory }}
                cpu: {{ .Values.tasks.limits.cpu }}
{{- end }}
```

### Why each knob has the value it has

| Field | Value | Reason |
|---|---|---|
| `schedule` | `"7 * * * *"` | Hourly, offset from `:00` to dodge the cluster-wide top-of-hour herd and disjoint from the collectors' `*/5`. The exact minute is arbitrary and safe to change — the ledger keys off the *task's* schedule, not the CronJob's |
| `timeZone` | `"Etc/UTC"` | The controller must never see a DST gap or fold. Task-level DST is handled in Python, where it is tested. `{{- with }}`-gated so a pre-1.25 cluster can drop it via `tasks.timeZone: ""` |
| `concurrencyPolicy` | `Forbid` | Belt. The ledger is the suspenders and the one that actually holds — `Forbid` protects only against overlap *within one CronJob object*, not against a future daemon |
| `startingDeadlineSeconds` | `600` | Miss the window by >10 min and skip; the next hour catches up for free. Never leave this unset on a CronJob that might be suspended — >100 missed schedules wedges the controller permanently. Never set it below ~10 s either, or controller resync jitter starts eating legitimate fires |
| `successful/failedJobsHistoryLimit` | `3` / `5` | Failures are worth keeping longer than successes; both small, matching the chart's `revisionHistoryLimit: 3` instinct about ArgoCD clutter |
| `backoffLimit` | `0` | No pod-level retry. The next hourly dispatch *is* the retry, and the ledger decides whether re-running is even correct. An immediate retry just re-hits whatever was broken five seconds ago |
| `activeDeadlineSeconds` | `3000` (50 min) | Strictly less than the 60-minute interval, so a wedged run can never coexist with its successor. A kill here leaves a stale `running` row, which the § 4.4 lease reclaims |
| `restartPolicy` | `Never` | With `backoffLimit: 0`, `OnFailure` would be a contradiction; `Never` also preserves the failed pod for `kubectl logs` |
| resources | req `100m` / `256Mi`, lim `1` / `1Gi` | The webapp's 4 cpu / 4 GiB sizes ~9 gthread gunicorn workers. This process imports the CLI, opens two connections, and issues DELETEs. The floor is Python import overhead of the fat image (matplotlib is not on the CLI path); 1 GiB absorbs a surprise |

### `values.yaml`

```yaml
# Scheduled-task dispatcher. ONE CronJob for ALL tasks — each task's schedule
# lives in Python (src/scheduling/tasks/), and the task_run ledger in
# system_status makes a late or duplicate dispatch a no-op.
# Design: docs/plans/SCHEDULED_TASKS.md
tasks:
  enabled: true
  name: samuel-tasks
  schedule: "7 * * * *"
  timeZone: "Etc/UTC"
  suspend: false
  concurrencyPolicy: Forbid
  startingDeadlineSeconds: 600
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 5
  backoffLimit: 0
  activeDeadlineSeconds: 3000
  args: ["--run-due"]
  env:
    STATUS_RETENTION_DAYS: "7"
    # Comma-separated task names to skip without a code deploy. Used during the
    # initial rollout to prove creds/DNS/image before anything deletes.
    SAM_TASKS_DISABLED: ""
  requests: {memory: 256Mi, cpu: 100m}
  limits:   {memory: 1Gi,   cpu: "1"}
```

### `values-local.yaml`

```yaml
tasks:
  enabled: false          # Docker Desktop: nothing should silently DELETE local data.
  # For a manual smoke test (see § 13):
  #   helm upgrade samuel . -f values.yaml -f values-local.yaml \
  #     --set tasks.enabled=true --set tasks.schedule='*/5 * * * *'
```

---

## 9. Observability

Proportionate to the scope: the ledger *is* the observability story, and the job is to
give it two or three cheap read paths.

**Tier 0 — ships with P3/P4. Required.**

- `sam-admin tasks --list` / `--history`.
- Container stdout: one structured line per task per dispatch. This is the durable
  sink — the chart already leans on CIRRUS's stdout retention for audit records
  (`AUDIT_LOG_STDOUT: "1"` and its comment).
- A failed Job object, retained by `failedJobsHistoryLimit: 5`, visible in
  `kubectl get jobs` and in ArgoCD's tree.

**Tier 1 — ~60 lines, later, and worth it.** A read-only "Scheduled tasks" card on the
existing Admin → Configuration tab: a route beside `htmx_server_card` in
`src/webapp/dashboards/admin/configuration_routes.py`, a fragment under
`dashboards/admin/fragments/`, gated on the same permission. That tab is documented as
read-only, so a "run now" button belongs elsewhere. The query is one
`ORDER BY claimed_at DESC LIMIT 1` per registered task — with three tasks, loop; do
not write a window function.

**Deferred, named so nobody reinvents them:**

- `/api/v1/health/tasks` returning `{"stale": [...]}` for external monitoring — there
  is no external monitor to consume it. Build the consumer first.
- **A watchdog task.** The only reliable executor in this system is the dispatcher
  itself, so the natural staleness alarm is a registered task: `tasks_watchdog`,
  hourly, exits 2 (→ failed Job) if any task's last `succeeded` is older than 2× its
  interval. It cannot detect its own death — but a dead dispatcher stops producing Job
  objects entirely, which is a different and more visible failure. ~30 lines, no new
  infrastructure. **Recommended as the second task ever written.**

---

## 10. The daemon migration path

The entire scheduler-facing surface is one function:

```python
run_due(now=..., ledger=..., registry=TASKS) -> DispatchReport
```

`schedules.py` is pure and clock-free. `registry.py` knows nothing about how it is
invoked. `ledger.py` talks only to `system_status`. Nothing in `src/scheduling/`
imports Click, Flask, or `kubernetes`. A future always-on agent is therefore:

```python
# hypothetical src/scheduling/daemon.py — NOT part of this plan
while True:
    run_due(now=utcnow_naive(), ledger=ledger)
    time.sleep(60)
```

deployed as a 1-replica Deployment with `strategy: Recreate`. **During the migration
both can run**: CronJob and daemon both wake, both compute the same occurrence key, and
the UNIQUE constraint picks one winner per occurrence. That is the migration story —
run both for a week, watch `runner_id` show the daemon winning every slot, then set
`tasks.suspend: true` and keep the CronJob as a values-flippable fallback.

What a *true* always-on agent needs that this framework deliberately does not provide,
and should not be stretched to:

| Capability | Why not here |
|---|---|
| Sub-minute / event-driven triggers | This is polling at hour granularity by design |
| Long-running work with progress + cancellation | `TaskResult` is a single return value; there is no cancel channel |
| Inter-task dependencies / DAGs / fan-out | Tasks run serially in registry order and know nothing of each other |
| Queues, priorities, backpressure, worker pools | One process, one task at a time |
| Leader election | The ledger gives per-occurrence mutual exclusion, **not** a cluster leader. Two daemons both wake and both attempt; one wins per slot. Correct, but not a leader — do not let anyone assume it is |
| Retry with exponential backoff as a first-class concept | § 4.4 has a deliberately anaemic hook. Real retry semantics are a queue's job |
| Externally-triggered runs ("run now" from the UI) | The CLI is the only trigger surface |

If three or more of those become requirements, the answer is a real workflow engine,
not evolution of this one. Writing that sentence down now is cheaper than arguing
about it in eighteen months.

---

## 11. Rejected alternatives

| Alternative | Why not |
|---|---|
| **High-rate polling (`*/5 * * * *`)** | 288 pods/day, 288 connect cycles against `csg-postgres`'s 100-slot cap, 288 Job objects churning through `historyLimit` — for zero benefit. The ledger already makes lateness harmless, so a finer poll buys only *punctuality*, which nothing here needs. Hourly is already 24× more often than the only real task requires |
| **One CronJob per task** | N templates and N cron strings that must stay in sync with N Python schedule declarations: two sources of truth for every schedule, drifting silently. Every new task becomes a chart change plus an ArgoCD sync, which is precisely the friction being removed. And the ledger loses its point |
| **APScheduler inside gunicorn** | 2 replicas × ~9 gthread workers = 18 schedulers → 18 duplicate fires per slot. The ledger would make that *correct*, which is why this is a genuine option — it is rejected on **coupling**, not correctness. Batch DELETEs and (later) SMTP loops would run inside request-serving processes, compete for the same 4-cpu request, and be killed mid-task by every rolling update. A `maxSurge: 1` rollout during a task is a guaranteed stale-lease event |
| **A dedicated always-on Deployment now** | A pod running 24/7 for ~2 minutes of work per day, plus liveness-probe design, plus restart semantics, plus a second thing ArgoCD can show as Degraded. § 10 keeps the door open at a cost of zero today |
| **Argo Workflows `CronWorkflow`** | The cluster runs ArgoCD, not Argo Workflows — a different product. New CRDs, new controller, new RBAC, and a second scheduling language to keep in sync with the Python one |
| **GitHub Actions `schedule:`** | No network path from GitHub-hosted runners to `csg-postgres.k8s.ucar.edu` or `sam-sql.ucar.edu` (VPN-only); would mean egressing DB credentials to GitHub or standing up a self-hosted runner. GH cron is also routinely 5–30 min late, and the maintenance workflows already carry a personal-PAT single point of failure — do not add load-bearing weight to it |
| **Keep it in the Glade crontab** | A personal crontab in one person's account referencing `/glade/{u/home,work}/benkirk/repos/...`, already flagged as an SPOF by the NRIT review. The cleanup task is not in it today anyway |
| **cron / supervisord inside the webapp container** | Two PID-1 candidates, `restartPolicy` semantics that fight the Deployment, and the duplicate-fire problem of the APScheduler option with worse observability |
| **Reuse `sam.operational.Synchronizer`** (`src/sam/operational.py:9`) | Four reasons, any one sufficient. **(1) Wrong database** — it is in the vendor-owned SAM MySQL schema, which has no Alembic coverage in this repo (migrations manage `system_status` only), so a new column there has no in-repo migration path. **(2) Wrong shape** — `(name, last_run)` is a last-run *pointer*, not an occurrence ledger; it cannot express dedup, state, attempt, duration, or history. **(3) No lock primitive** — with no unique constraint on an occurrence, two dispatchers both read `last_run`, both decide "due", and both run. **(4) It is legacy furniture** — one row (`'pdb'`) from the vendor product, no writers anywhere in this codebase; repurposing it overloads an artifact whose original semantics we neither own nor can verify. The right in-repo precedent is `DiskChargeSummaryStatus` (`src/sam/summaries/disk_summaries.py:80`) — one row per key, upserted by `mark_disk_snapshot_current()` — and `task_run` is exactly that pattern generalized from a date key to an occurrence key, with state and history added |

---

## 12. What the expiration-email task needs first

Explicitly out of scope. This section is the checklist that must clear before
`@task(name='project_expiration_notices', …)` may be written.

**1. Milestones, not a rolling window.** `ProjectExpirationCommand.execute`
(`src/cli/project/commands.py:141-144`, and again at `:155-160` for the notify path)
hard-codes `now → now + 32 days`, recomputed every invocation. Scheduling that daily
emails every lead every day for 32 days. The redesign: a fixed ladder —
`MILESTONES = (60, 30, 14, 7, 1)` days before `allocation.end_date` — where the task,
for occurrence date D, selects the `(allocation, milestone)` pairs whose milestone date
**equals** D. That converts a window into a point event, which is what makes dedup
tractable *and* what gives § 5's catch-up a well-defined set of missed notices.

**2. Dedup goes in a new table, not `task_run`.** Different cardinality and different
retention: `task_run` is one row per (task, occurrence) and is pruned at 180 days; a
send record is one row per (allocation, recipient, milestone) and **must never be
pruned**, because you cannot un-send an email. New table in `system_status`
(Alembic `0007`):

```
notification_sent
  notification_sent_id  Integer PK
  channel      String(16)    # 'email'
  template     String(32)    # 'expiration' | 'expiration-WNA' | ...
  subject_key  String(64)    # 'alloc:1234567' — a STRING, not an FK:
                             #   allocations live in SAM/MySQL, this row in status/Postgres
  milestone    String(16)    # 'd30'
  recipient    String(255)
  sent_at      DateTime
  task_run_id  Integer FK -> task_run  (nullable; NULL for manual sends)
  UNIQUE (channel, subject_key, milestone, recipient)
```

Same insert-wins primitive as § 4.3.

**3. Pick a side of at-least-once / at-most-once, in writing.** Commit the dedup row
*before* SMTP → at-most-once (a crash between commit and send loses that notice).
Commit *after* → at-least-once (a crash after send re-sends). **Choose at-most-once.**
A duplicate blast to hundreds of PIs is the failure that gets the tool switched off
permanently; a single dropped rung is recoverable, because there are five rungs and the
ladder is visible in `notification_sent`. Per-recipient SMTP failures go into
`TaskResult.partial_failures` → ledger state `partial` → exit 2 → failed Job → someone
looks.

**4. The hard-coded BCC.** `src/cli/notifications/email.py:127` and `:138` set
`msg['Bcc'] = 'benkirk@ucar.edu'` unconditionally. Under a scheduler that is hundreds
of copies a night to one mailbox. It becomes `MAIL_BCC` (env, default empty, read via
`Context` like the other mail settings). While in there: delete the commented-out
recipient override at `src/cli/project/commands.py:374-376` — a commented-out line that
redirects all mail to one person is a loaded gun in a file a scheduler is about to call.

**5. A hard send cap.** `SAM_TASKS_EMAIL_MAX` (default 250). Exceeded → the task fails
*before* sending anything, with the count in `detail`. This guards the failure mode
where a milestone bug turns "the 30-day cohort" into "every allocation ever".

**6. Keep the JSON/write guard at the CLI layer.** `execute()` currently rejects
`json_mode and notify` at `src/cli/project/commands.py:124-131`. The task will call the
command class directly with a `Context` it constructs itself
(`output_format='rich'`), so the guard must stay where it is — a CLI-flag check — and
must not migrate down into the command class, where it would block the task. § 7.5's
carve-out is about the *dispatcher's* own output, not about `--notify`.

**7. Explicit facility scoping.** `default=['UNIV', 'WNA']` is a Click default on
`--facilities` (`src/cli/cmds/admin.py:102`). The task must pass its facilities
explicitly rather than inherit a CLI default someone might reasonably change.

**8. Open question, and probably the long pole: can the namespace reach SMTP?** The
webapp has never sent mail from Kubernetes — every expiration email to date has gone out
from a workstation or Glade. Egress from the CIRRUS namespace to `ndir.ucar.edu:25`
needs confirming (the chart's only NetworkPolicy today is `redis-networkpolicy.yaml`, so
egress is likely open, but "likely" is not a deployment plan). Verify with a one-off pod
**before** writing any of items 1–7.

**Rollout, when it comes:** register the task with `dry_run` forced on for one full
ladder cycle. It writes real `succeeded` ledger rows whose `detail` carries the
would-send recipient list, so you can diff a week of intended sends against reality
before a single message leaves the cluster.

---

## 13. Phasing and verification

### Test matrix

| Layer | File | How |
|---|---|---|
| Predicates | `tests/unit/test_schedule_predicates.py` | Pure functions, no fixtures, no DB. Parametrized over: spring-forward (2027-03-14) and fall-back (2026-11-01) in `America/Denver`; month-end for `MonthlyDay(-1)` and `MonthlyDay(31)` across Feb 28 / Feb 29 / 30- and 31-day months; `MonthlyBusinessDay(1)` when the 1st is Sat, Sun, Mon; `CronExpr` agreement with `Hourly`/`Daily` over 1,000 instants; `CronExpr` raising past its horizon. Property tests: `last_occurrence(last_occurrence(t)) == last_occurrence(t)` and `last_occurrence(t) <= t` |
| Ledger | `tests/unit/test_task_ledger.py` | The existing SQLite status tier (`status_session` / `status_db_url` in `tests/conftest.py`). Two competing claims on one key → exactly one `TaskRun`, loser gets `None`. Stale reclaim: hand-age `heartbeat_at`, assert `attempt == 2` and still one row. A fresh row is **not** reclaimable. `prune_task_runs` never touches `finished_at IS NULL` |
| Portability guard | same file | A boundary test in the style of `tests/unit/test_chart_module_boundaries.py`: read `src/scheduling/ledger.py` and assert it contains none of `FOR UPDATE`, `SKIP LOCKED`, `GET_LOCK`, `pg_advisory`, `ON CONFLICT`, `INSERT IGNORE`, `ON DUPLICATE KEY`. CI runs SQLite; production is Postgres; this test is the only thing standing between those two facts |
| Migration | *free* | `tests/integration/test_alembic_migrations.py` already asserts that `upgrade head` matches `StatusBase.metadata` exactly and that `head → base → head` round-trips. Adding `0006_task_run` plus the model gets both guarantees with no new test — and will *fail* if model and migration disagree, which is the point |
| Runner | `tests/unit/test_task_runner.py` | `run_due(now=…)` takes the clock as a parameter and never calls `utcnow_naive()` internally (only the CLI boundary does). So: register throwaway tasks against a fake registry, drive a simulated week, assert exactly 7 `succeeded` rows for a daily task; simulate a 3-day outage and assert 1 `succeeded` + 2 `skipped`; assert `dry_run` writes zero rows; assert a raising task yields `failed` with a traceback in `detail` |
| CLI | `tests/unit/test_cli_tasks.py` | CliRunner, per `tests/unit/test_sam_search_cli.py`. Exit codes for each mode, `kind` present in every JSON envelope, mutually-exclusive-flag rejections, `--run unknown` → 1 |
| Chart | `helm/tests/test-cronjob-render.sh` | Modeled directly on `test-oidc-render.sh` — same `assert_contains` / `assert_not_contains` helpers, same two renders |

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

Add both scripts to whatever runs `helm/tests/` — today that is manual, and a one-line
`make helm-test` target running every `helm/tests/*.sh` is the obvious tidy-up while
touching this.

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

That proves image, command, secrets, DNS, and ledger writes with the destructive task
still disabled. Then drop `SAM_TASKS_DISABLED`, re-run, and confirm a `succeeded` row
with a `deleted` breakdown in `detail`.

### Production rollout (P5)

The `main` → CI → `cirrus` → ArgoCD path is the only route
(`docs/CIRRUS_PUBLISHING.md`); there is no direct `helm upgrade` against CIRRUS.

1. Merge P1–P4 to `main` with `values.yaml` carrying
   **`SAM_TASKS_DISABLED: "cleanup_status_snapshots"`**. CI pins the image,
   force-pushes `cirrus`, ArgoCD syncs. For 24 h the dispatcher runs hourly, writes
   `skipped` rows, and deletes nothing. This proves credentials, DNS, image,
   securityContext, and Postgres reachability from a pod that is *not* the webapp —
   with zero blast radius. It is the entire reason the kill switch exists.
2. Verify: `sam-admin tasks --history` shows 24 `skipped` rows with distinct
   `runner_id`s.
3. Second commit: `SAM_TASKS_DISABLED: ""`. **Open question — see § 14.6.**
4. Watch the first real 02:15 MT run. Expect `deleted` counts in the hundreds of
   thousands on the first pass (nothing has ever pruned this) and a long
   `duration_ms`. Consider a first manual run with `STATUS_RETENTION_DAYS: "90"`,
   stepping down over a few nights, rather than letting the first automated DELETE take
   a multi-year backlog in one transaction against `csg-postgres`. **This is the single
   most likely operational surprise in the whole plan.**

---

## 14. Flags on the chosen design

Recorded so they are decisions rather than accidents.

1. **Hourly dispatch caps the vocabulary's usefulness at hourly granularity.**
   `Daily(2, 15)` fires somewhere in `[02:15, 03:07)` Mountain. Fine for pruning and
   fine for email; "at 09:00 sharp" is not expressible. If a task ever needs
   punctuality, the answer is § 10's daemon, not a faster cron.
2. **`system_status` is not the natural home for a SAM-wide ledger.** The expiration
   task is about SAM data, and its `notification_sent` rows will sit in Postgres
   pointing at MySQL keys with no FK. The decision is still right — it is the only
   Alembic-managed DB in the repo, and SAM's schema is vendor-owned — but it means
   "which database is this fact in?" gets a less obvious answer over time. Worth one
   sentence in `CLAUDE.md`.
3. **The dispatcher is a `sam-admin` subcommand**, so it inherits the group callback's
   SAM-DB coupling. § 3.2 is a hard precondition, not a nicety.
4. **Nothing alerts.** A failed Job is visible only to someone looking at ArgoCD or
   `kubectl`. The § 9 watchdog is the cheapest fix and should be the second task
   written.
5. **The first production DELETE is unbounded** — see rollout step 4.
6. **A values-only change may not reach `cirrus`.** CI's `update-helm` job is gated on
   `needs.setup.outputs.webapp_built == 'true'`, so a commit to `main` that touches only
   `values.yaml` does not by itself repin and force-push `cirrus`. Confirm the intended
   mechanism for values-only production changes before relying on rollout step 3; if
   there isn't one, steps 1 and 3 must be a single commit and the kill-switch soak has to
   happen on Docker Desktop instead.
7. **CronJob `timeZone` requires k8s ≥ 1.25 (GA 1.27)** and the CIRRUS version was not
   verified while writing this. The field is `{{- with }}`-gated so it can be dropped,
   but confirm before P4.
