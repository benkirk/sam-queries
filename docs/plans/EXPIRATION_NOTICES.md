# Expiration Notices — the scheduled-task consumer

**Status: built; two verification passes outstanding.** Approved and implemented
2026-08-13. Written to be picked up cold; every claim carries a `file:line` so the
next reader can verify rather than re-derive.

| | |
|---|---|
| **Done** | The `open_sam` / `require_sam` split (see *Prerequisite, done*). Commits 1–10, plus a new commit 6a (`--occurrence`). Suite green at **6,856 passed, 42 skipped, 1 xfailed**; `helm/tests/test-cronjob-render.sh` OK. **Phase V run 2026-08-13** — 824 messages in 163 s, 0 failures, redirect verified across the whole population; see *Phase V results*. |
| **Also done** | **Browser smoke** 2026-08-13 — all six commit-9 assertions pass, contrast AAA in both themes; see *Browser smoke*. |
| **Outstanding** | The **legacy-key bridge**, which the dev clone could not exercise because its `notification_log` was empty. Everything else is verified. |

## Deviations, as built

Found by reading the tree during implementation. Each changed what was written;
they are recorded here rather than only in a commit message.

1. **Bands are made half-open by construction.** `get_all_expiring_allocations`
   filters `Allocation.end_date <= end_date` — *inclusive*. Passing
   `start + hi_days` straight through would make adjacent bands overlap on their
   shared boundary: invisible with one rung, a double-send to whoever lands on the
   seam with three. `band_bounds()` in the task subtracts one microsecond, and
   there are tests for both no-overlap **and** no-gap.
2. **Commit 9 reuses `get_recent_notifications` instead of a new `GROUP BY`.**
   `get_xras_activity` (`xras_activation.py:251-263`) already solved the same
   problem — one indexed fetch bounded by a projcode `IN` list, bucketed in
   Python. `get_recent_notifications` takes `projcodes=` *and* `kinds=` and orders
   `creation_time DESC`, so `delivered[0]` is the newest.
3. **The rollup returns an entry for every requested projcode**, not only the
   notified ones. The consumer is a macro shared with the user dashboard and has
   to tell "notified", "not notified" and "nobody asked" apart; making absence
   mean only the last keeps a missing key from carrying two meanings.
4. **The batch prefetch is gated on `len(messages) > 1`.** `Notifier.send()` is
   literally `send_many([m])[0]`, so an unconditional prefetch would put a bulk
   query on the path of every single-message send in the codebase — every XRAS
   activation notice.
5. **`--occurrence` shipped as a real flag** (commit 6a) rather than "temporarily
   widen a constant", which the doc had left open. Honored only under `--force`,
   where the ledger key is already `M`-prefixed and cannot claim a scheduled slot.
6. **`_drop_already_notified` tolerates `ledger=None`.** Found by a test:
   `Notifier(ledger=None)` is a documented configuration and the pre-filter
   crashed on it. It now falls through, matching `_pre_transport_guard`.
7. **`tests/factories/projects.py` gained `make_panel` / `make_allocation_type`**
   and `make_project(facility_name=...)`. Facility scoping is load-bearing here
   (§ 12 item 3) and was untestable: a factory project has `allocation_type_id`
   NULL and is invisible to every facility-scoped query, which reads as a broken
   query rather than missing fixture data.
8. **`test_task_expiration_notices.py` pins its occurrence in 2033.** The
   obfuscated snapshot holds ~22,000 real allocations ending up to 2030-12-31; a
   2026 occurrence selects ~800 of them, drowning the fixtures and taking the
   module from 2 seconds to 2 minutes.

Confirmed *not* a deviation: `tests/unit/test_expiration_notices.py` passes
**unmodified** across commit 4's extraction — its key assertion is
`startswith`/`endswith`, so the rung label slots in transparently. That is the
proof the move was pure.

## Context

Expiration notices become the **first real consumer** of two frameworks that have
both now landed: `src/sam/notify/` (mailer + `notification_log` ledger) and
`src/scheduling/` (the ledger-backed task dispatcher). Today the send is entirely
manual — Ben runs `sam-admin project --upcoming-expirations --notify` by hand,
roughly monthly, producing 500–600 individual emails. Nothing schedules it, which
`NOTIFICATION_FRAMEWORK.md:696-701` records as deliberate: *"a scheduled sender
wants the ledger proven first."* It is proven.

This work schedules that run **every Monday morning**, makes the send
path survive the volume (and the 2000+ a future outage-subscription consumer would
bring), gives Ben a per-run summary email, and surfaces "when was this last
notified" on the admin Project Expirations cards.

`SCHEDULED_TASKS.md` § 12 already sketches this. It is **input, not contract** —
this plan overrides it in five places, each flagged below.

### One doc claim is wrong — do not trust it

`NOTIFICATION_FRAMEWORK.md:908-910` states the old hardcoded `Bcc: benkirk@ucar.edu`
was *"already blind (never appeared on the wire)"* because `smtplib` strips the Bcc
header. **Ben confirms he received those BCCs through the last pre-refactor cycle
(early August 2026).** Whatever was measured, delivery was happening. The header may
have been stripped while the envelope recipient survived. This matters only as a
caution about that paragraph; the design below does not depend on it either way.

## Decisions (settled with Ben)

| | |
|---|---|
| Schedule | **Every Monday**, 09:00 `America/Denver` — `Weekly(0, 9, 0)` |
| Lookahead window | Fixed **40 days** (not the CLI's 32) |
| Runaway guard | `SAM_TASKS_EMAIL_MAX`, default **2500**, fail *before* sending |
| Batching | In `sam/notify/` (`Notifier`), so future kinds inherit it |
| Rollout | Live immediately after local validation — no redirected *production* soak |
| Badge | Age + recipient count, XRAS-activity-card pattern |
| Ben's copy | **One summary email per run**, not a per-message BCC |

On the last row: Ben chose the summary knowing he had been receiving per-message
BCCs. `NOTIFY_BCC=benkirk@ucar.edu` remains a one-line `values.yaml` change if he
later wants both — but note it is *global*, so it would also BCC him on every XRAS
activation notice the webapp sends.

## Overrides of § 12

1. **The milestone ladder ships as machinery with a single rung** (§ 12 item 1). No
   ladder is *acted on* yet, but the rung label goes into the dedup key from day one,
   because adding it later would change every key and force a one-time re-notify of
   everyone. Today's single rung reproduces current behavior. See *The ladder, shipped
   inert* below.
2. **40-day window, not 32.** Under a weekly cadence the window is no longer a
   *coverage* constraint — runs are 7 days apart, so any window over 7 days tiles. It
   is a **lead-time** choice: 40 days gives every PI a consistent 33–40 days' notice,
   where today's monthly 32-day window gives anywhere from 1 to 32 depending on where
   in the month the run happens to land.
3. **Cap 2500, not 250** (§ 12 item 2). 250 is below the observed volume — as
   specified the task would fail every month.
4. **Extract the message builder; do NOT construct a `Context`** (§ 12 item 4). See
   directly below.
5. **Batching and the summary email** are new work § 12 does not mention.

### Why § 12 item 4 must be rejected

A fresh `Context` binds `console = Console()` on **stdout**, and the CronJob runs
`sam-admin --format json tasks --run-due`, whose stdout is a JSON envelope operators
pipe to `jq`. `notification_progress` (`display.py:529`) plus the two `display_*`
calls would interleave rich tables and ANSI into it. The task also needs to own its
window (`execute()` hardcodes `now + 32 days` at `:143,159`), its `requested_by`, and
its facility scoping — all of which `execute()` decides for it.

Extraction also satisfies § 12 item 4's actual *requirement* — that the
`json_mode and notify` guard (`commands.py:125-133`) stay a CLI-flag check — more
cleanly, because `execute()` is not touched at all.

> **Correction.** An earlier draft of this section led with a different argument:
> that `Context.require_sam()`'s `sys.exit(1)` would let a `SystemExit` escape
> `run_due` and kill the dispatcher, and that extraction avoided it because the task
> "never constructs a `Context`". **The hazard was real; the escape was not.**
> `cli/tasks/commands.py` hands the runner `ctx.require_sam` as its
> `sam_session_factory`, so *any* task with `needs=('sam',)` reaches that `sys.exit`
> through `ctx.sam_session` — extraction or no extraction. It was latent only because
> `cleanup_status_snapshots` is status-only, and `expiration_notices` would have been
> the first task to arm it.
>
> **Fixed separately and already landed** — see *Prerequisite, done* below. The
> conclusion here is unchanged, but it now rests on the reasons above rather than on
> a premise that did not hold.

### Prerequisite, done: the SAM connect no longer exits

`Context` now has two accessors (`src/cli/core/context.py`):

- **`open_sam()`** — connects or raises `SamConnectionError`. Holds the caching.
- **`require_sam()`** — a thin CLI wrapper that prints the red message and exits 1,
  behaviorally identical to before for every subcommand.

`cli/tasks/commands.py` passes **`open_sam`**, so a SAM outage now fails the one task
that wanted the session — recorded `failed` with the error in `detail` — and the
dispatcher continues to the next. Covered by `tests/unit/test_cli_context.py` (9
tests, including a source-level guard on the wiring) and
`TestASessionFactoryThatFails` in `tests/unit/test_task_runner.py`.

The exit code stays **1** rather than `EXIT_ERROR` (2), which a connection failure
arguably deserves. Changing it would touch 10+ `sys.exit(command.execute(...))` call
sites with no top-level handler, and the codes are a contract kept in lockstep with
`hpc-usage-queries`. Deliberate, and recorded in the `require_sam` docstring.

### Why weekly, and why Monday

An earlier draft used a `FirstWeekdayOfMonth` predicate. It was dropped, and the
reasoning is worth keeping because it inverts several of this plan's other decisions.

**"First weekday" is not reliably Monday.** Over 2026–2045 it lands on Monday only
**42%** of months and on **Friday 14%** — and a 600-recipient notice sent Friday
morning is read Monday anyway, with any replies landing while the sender is away.

**Weekly is better on three axes:**

| | first weekday of month | `Weekly(0, 9, 0)` |
|---|---|---|
| Day of week | Mon 42%, Fri 14% | always Monday |
| Gap between runs | 28–33 days | 7 days |
| A missed run | loses a whole month | self-healing — see below |

- **Self-healing.** With a 40-day band and 7-day runs, one expiration is selected on
  **5–6 consecutive runs**. Any single skipped or failed week is recovered by the next
  one, with dedup preventing a double-send. This retires the "monthly means one shot"
  risk entirely, and makes federal-holiday Mondays (~5–6 a year) a non-issue.
- **It deletes a commit.** `Weekly(weekday=0, hour, minute, tz)` already exists
  (`schedules.py:248-273`). No new predicate, no weekend-rollover rule, no DST test
  matrix, no argument about `_MAX_LOOKBACK_DAYS`.
- **Each run is bounded to at most one month-end cluster** (see below). A monthly
  cadence, whose newly-selected band is 28–35 days wide, can catch two.

### Volume does NOT smooth — end dates are spiky

An earlier draft of this plan claimed weekly cadence would cut per-run volume ~6×, to
~105. **That was wrong**, and it was wrong because it assumed expirations are spread
evenly across the calendar. They are not. Measured against the obfuscated snapshot:

- **97% of allocations end on the last day of a month** (21,524 of 22,204).
- Months are themselves clustered: **September 6,575** (federal FY end), **December
  3,535** (calendar year), and a July/August bulge of 1,950/1,875.

Because a weekly run only sends to the cohort *newly* entering the 40-day window — a
7-day band, `[run+33, run+40)` — and month-ends are ~30 days apart, **each run catches
at most one cluster, and most runs catch none.** A full year of Mondays, UNIV+WNA,
active projects:

| | emails |
|---|---|
| Loaded runs | **12 a year**, one per month-end |
| Peak | **535** (2026-11-23, catching the Dec 31 cluster) |
| Typical loaded run | ~250 |
| The other ~40 runs | 0–15 |

Sanity check against reality: simulating Ben's actual early-August 2026 run at the
CLI's 32-day window yields **471 recipients**, matching his recollection of 500–600.

Three consequences, all load-bearing:

1. **The batching work in commits 1–3 stays fully justified.** Peak per-run volume is
   ~535 — essentially unchanged from today. Weekly moved the burst; it did not remove it.
2. **`SAM_TASKS_EMAIL_MAX = 2500` is a sane guard** — ~4.7× the measured peak. Far
   enough above normal operation never to fire, close enough to catch an
   order-of-magnitude selection bug.
3. **Phase V must be timed to hit a loaded week**, or it validates nothing. See there.

**And the cost of the quiet weeks:** on a loaded run roughly 85% of the *selection* is
already-notified, and on a quiet run essentially all of it is. The task therefore
**must drop suppressed messages itself** rather than letting `Notifier` record them —
see commit 6 step 3. Left to the framework, the ~40 quiet weeks a year would each
write hundreds of `suppressed` rows — tens of thousands annually — polluting
`notification_log`, the new badge, and the admin Notifications card.

### The ladder, shipped inert

A rung is a **band** of days-before-expiry, not a point. Bands tile the runway, so
each project-expiration falls in exactly one band per run — and **bands must be at
least as wide as the gap between runs**, or expirations fall between them. That is the
same failure mode as the 32-day window this plan already fixes, which is why a weekly
cadence is what makes a 7-day rung expressible at all.

```python
@dataclass(frozen=True)
class Milestone:
    label: str      # goes in the dedup key and the template context
    lo_days: int    # inclusive
    hi_days: int    # exclusive

#: Today: one rung spanning the whole 40-day runway — one notice per expiration,
#: at 33-40 days out. Enabling the ladder below needs NO key migration, because
#: `label` is already in the key.
MILESTONES = (Milestone('expiring', 0, 40),)

# Future, for reference. Bands are 7 days wide because runs are 7 days apart;
# re-tile them if the schedule ever changes.
# MILESTONES = (Milestone('60d', 56, 63),
#               Milestone('30d', 28, 35),
#               Milestone('7d',   7, 14))
```

The task iterates rungs, querying each band's window and tagging each message with
its rung. The dedup key becomes:

```
expiration:{projcode}:{latest_end_date}:{label}:{recipient}
```

**Why this must land now, not later.** § 12 item 1 warns that adding rungs means
adding the milestone to the dedup key *"or the first rung suppresses all the
others."* Retrofitting the label changes **every** key, so the run after the change
would re-notify everyone once. Paying that cost is avoidable only by paying it never.

**The one-time migration this creates.** Ben's manual runs wrote keys in the *old*
format. The first scheduled run would not match them, so the overlap cohort — projects
already notified this month whose end dates still fall in the new window — would get a
second notice. Handle it in the task, not the framework:

```python
# Bridge: the CLI wrote `expiration:{projcode}:{date}:{recipient}` before rung
# labels existed. Treat a legacy hit as suppressing. REMOVABLE after the first
# scheduled cycle — by then every live key is in the new format.
suppressed = ledger.already_sent_many(new_keys + legacy_keys)
```

~10 lines, one extra key per message in a query that is already batched, and a
docstring naming the cycle after which it can go.

---

## Build, in commit order

Each commit is independently revertable and leaves the suite green.

### 1. `notify: one query for a batch's suppression check`

`sam/notify/ledger.py`. Extract the predicate at `:185-189` into a private
`_suppression_conditions(*, horizon, since)` so the two callers cannot drift, then add:

```python
DEDUP_CHUNK = 500
def already_sent_many(self, dedup_keys, *, since=None,
                      chunk_size=DEDUP_CHUNK) -> set[str]:
```

- `already_sent` keeps its own `.limit(1)` fast path and `try/except` — do **not**
  reimplement it as `bool(already_sent_many([k]))`.
- Drop falsy keys (mirrors `:175-176`); dedupe preserving order.
- Empty input returns `set()` **without** calling `session_factory`.
- Compute `horizon` **once** per call, not per key.
- **One session** for the whole call, chunks looped inside — chunking is a driver
  artifact, not a second operation (`ledger.py:60-68`).
- **Fail open, per chunk**: on exception return what completed, not `set()`.

### 2. `notify: reconnect every chunk_size messages in send_many`

`sam/notify/service.py`. Extract `:135-155` verbatim into `_send_chunk(...)`, then
loop. `send_many(..., chunk_size: Optional[int] = None)`, keyword-only.

- `open()`/`close()` and their `try/finally` move **inside** the loop — a `finally`
  wrapped around the loop would leak a connection between chunks.
- `chunk_size=None` → one chunk covering all pending → byte-identical to today.
  `test_notify_service.py::test_one_open_per_batch_not_per_message` is the gate and
  must pass unchanged.
- The `open()`-failure branch now fails only *its* chunk; the next gets a fresh
  connect. That is the mid-run recovery this is for. Cost: a hard-down relay makes
  ⌈N/chunk⌉ attempts (~100 s at 2500/250 × `mail_timeout=10`). Document it.
- Catch only `TransportError` from `open()`, as today. `on_result` block untouched.

### 3. `notify: prefetch a batch's suppression before the guard phase`

Wire commit 1 into `service.py:129-132`; `_pre_transport_guard` gains a keyword-only
`suppressed_keys=None`, so its single-message contract is unchanged for every other
caller and `send()`-of-one never pays for a prefetch.

No semantic change: the existing comprehension already evaluates every guard before
any delivery, so two messages sharing a `dedup_key` in one batch already both pass.
Add an explicit test so nobody "fixes" that later.

**Constraint across 1–3:** `sam/notify/__init__.py` uses PEP 562 lazy `__getattr__`
(`:94-102`) so importing the ORM never pulls in jinja2/transports —
`tests/unit/test_notify_import_graph.py` is the gate. Add no eager imports. Keep
`record()` fail-closed and `already_sent` fail-open; do not blur them.

### 4. `sam: extract the expiration message builder out of the CLI command`

New `src/sam/queries/expiration_notices.py`:

```python
def build_expiration_messages(expiring, *, requested_by, milestone,
                              additional_recipients=None) -> List[Message]:
```

Move `commands.py:330-457` **verbatim** — the grouping, `get_detailed_allocation_usage()`,
the latest-expiration/grace math, facility resolution, `ResourceTypeName.allocation_unit`,
the roster → admin → lead precedence-by-overwrite, and the WNA subject variant. Keep
the comments; they encode measured decisions. Ordering stays deterministic.

**The one non-verbatim change** is the dedup key at `commands.py:451-455`, which gains
the rung label: `expiration:{projcode}:{latest_expiration_date}:{milestone.label}:{recipient}`.
`Milestone` and `MILESTONES` live in this module too — the builder is the only thing
that needs to know a rung exists. Expose `legacy_dedup_key(...)` here as well, so the
pre-filter in commit 6 has one place to read the old format from.

Because this changes the key the **CLI** writes too, `--force` semantics and the
CLI's own suppression shift to the new format on the same commit — which is correct,
since CLI and task must not disagree about what has been sent.

`_send_notifications` shrinks to ~25 lines and delegates.
`tests/unit/test_expiration_notices.py` exercises it end-to-end and must pass
**untouched** — that is the proof the move was pure.

Also add an additive `now: Optional[datetime] = None` kwarg to
`get_all_expiring_allocations` (`sam/queries/expirations.py:297`), which currently
does `now = datetime.now()` at `:337`. Without it a late dispatch renders "expires in
37 days" where a punctual one said 38.

*Naming:* `sam/queries/` is documented as read-side, and this builds. It still
belongs there — beside `expirations.py` whose exact tuple it consumes, and
`notifications.py`. The one place it must **not** go is inside `sam/notify/`.

### 5. `scheduling: let a failing task attach its own detail to the ledger row`

Three lines in `runner._execute`'s except branch (`:206-210`): merge a
`getattr(exc, 'task_detail', None)` dict into `detail`. No behavior change for
`cleanup_status`, and it is what lets the cap report `{'audience': n, 'cap': c}` as
structured data rather than a substring of `repr(exc)`.

### 6. `scheduling: weekly expiration notices`

New `src/scheduling/tasks/expiration_notices.py` **plus registration in
`scheduling/tasks/__init__.py`** — without the side-effect import the task simply
never runs and nothing errors.

⚠️ **This is the first task with `needs=('sam',)`.** `cleanup_status_snapshots` is
status-only, so nothing has ever exercised the SAM session factory. That path is now
safe (see *Prerequisite, done*) — `ctx.sam_session` routes through `open_sam`, and a
SAM outage yields a `failed` ledger row while the dispatcher continues — but this task
is the first thing to depend on it, so treat a change there as touching this feature.

```python
@task(name='expiration_notices', schedule=Weekly(0, 9, 0, tz='America/Denver'),  # Mon 09:00
      needs=('sam', 'status'),
      expected_runtime=timedelta(minutes=20),   # lease sizing, see below
      misfire_grace=timedelta(hours=24),
      description='Email upcoming allocation-expiration notices')
```

Body, in order:

1. **Window from `ctx.occurrence`, never the clock** — the repo's stated #1
   task-authoring hazard (`cleanup_status.py:3-8`). Convert to the schedule's local
   zone and truncate to local **midnight**: `ctx.occurrence` is naive **UTC** while
   `Allocation.end_date` is naive **Mountain**, so comparing them raw is a 6–7 h skew;
   truncating to the date makes a punctual and a 20-hour-late run select the same set.
   Promote `_to_local_naive` (`schedules.py:109`) to public rather than re-deriving
   zoneinfo math in the task.
2. **Iterate `MILESTONES`.** For each rung, query its band —
   `[start + lo_days, start + hi_days)` — with `now=start` injected (commit 4's
   kwarg) and facilities passed **explicitly** as `('UNIV','WNA')`, never inherited
   from the Click default at `cli/cmds/admin.py:106` (§ 12 item 3). With today's
   single rung this is one query over `[start, start+40)` — exactly the current
   behavior. The loop is what makes adding rungs a one-tuple edit.
3. **Build** per rung via `build_expiration_messages(..., milestone=rung,
   requested_by='task:expiration_notices')`, then concatenate.
   `getpass.getuser()` in this pod is the runtime UID or a `KeyError` — either way a
   lie in a column the admin card renders as "who asked".
   Then **pre-filter**: ask `already_sent_many` for the new key form *and* the legacy
   form, and drop every message suppressed under either.

   This is **permanent, not a migration step** — it is what makes 48 near-no-op weeks
   a year cheap. ~85% of each week's selection has already been notified; left to
   `Notifier`, each of those would write a `suppressed` ledger row (~26,000 a year).
   Dropping them here means zero rows and an `audience: 0` result on a quiet week. The
   count still gets reported in `TaskResult.detail`, so nothing is lost but the noise.

   The *legacy* half of the key list — the pre-rung-label format Ben's manual runs
   wrote — is the only removable part, after one full cycle.
4. **Cap, before any transport is touched.** Raise `EmailCapExceeded` carrying
   `task_detail={'audience': n, 'cap': c, 'aborted_before_sending': True}`. It must
   *raise* — `TaskResult` has no failed state. Do **not** use `partial_failures`,
   which reports `partial`, meaning "some sent"; here zero were.
5. **Refuse to run mail-disabled.** If `notifier.config.enabled` is false, raise.
   Without this a chart mistake writes ~600 `suppressed` rows, reports `succeeded`,
   and exits 0.
6. **Send** with `chunk_size=250`. The ledger gets its **own** sessions off the engine
   (`Notifier(ledger=NotificationLedger(lambda: Session(engine)))`), not
   `ctx.sam_session` — mail cannot be un-sent by the rollback `close_sessions` would do
   on failure. No `rich` progress bar; the task writes only to `ctx.logger`.
   `ctx.dry_run` → `preview()` per message, writing no ledger row.
7. **Return** `TaskResult(detail={audience, projects, window, sent, suppressed, failed,
   failed_recipients[:50]}, partial_failures=len(failed))`.

**Lease sizing is load-bearing.** `TaskContext` (`registry.py:110-177`) exposes no
ledger handle, so this task **cannot heartbeat**. The lease is
`max(3×expected_runtime, 900s)` (`ledger.py:65-69`) and must exceed the CronJob's
`activeDeadlineSeconds: 3000` (`values.yaml:426`), or a still-running send becomes
reclaimable and every PI gets a second copy. Hence 20 min → 3600 s. Add a drift test
that parses `activeDeadlineSeconds` out of `values.yaml` and asserts the inequality.

`misfire_grace=24h` (not the 6 h default): a late run is byte-identical because the
window comes from `ctx.occurrence`, so there is no reason to refuse one. Under a weekly
cadence a missed slot is not fatal either way — the 40-day band re-selects the same
expirations next Monday — but 24 h absorbs an ordinary maintenance window without
writing a `skipped` row that looks like a problem.

Document the kill-recovery path in the module docstring: killed mid-send → row stays
`running` → next hourly dispatch reclaims the stale lease → the re-run's
`already_sent_many` suppresses everyone already `sent`, so only the remainder goes.

### 7. `notify: per-run summary email`

A new non-facility-aware kind in `sam/notify/kinds.py` plus text/HTML templates in
`sam/notify/templates/`. Recipient from an env var, set in Helm to
`benkirk@ucar.edu`; `dedup_key` keyed on the occurrence so a re-run does not
double-send. Content: counts by status, audience size, per-project recipient counts,
every failure. **Sent on the cap trip too**, before the task fails — otherwise the one
run Ben most needs to hear about is the one that emails him nothing.

---

## Verification tooling — enable these on the session that does the work

Two MCP tools materially change how this plan is verified. **Neither was available in
the session that authored it**; both are enabled per-session, so whoever executes must
resume with them on. Without them the two weakest steps in this plan — "check the
inbox" and "check the badge renders" — degrade to eyeballing, which is exactly where a
600-message send and a shared Jinja macro hide their failures.

| Tool | Used by | Turns this into an assertion |
|---|---|---|
| **Google MCP** (mail) | Phase V | count reconciliation, the `X-SAM-Original-To` safety check, template-variant sampling, and "zero new mail on the re-run" |
| **Playwright** | commit 9 | badge present/absent/failure states, tooltip contents, and computed contrast in both themes |

Both carry constraints worth stating up front:

- **Google MCP reads a real mailbox** holding production-derived PII. Scope queries to
  `from:sam-admin@ucar.edu` in the run window; report counts and header presence, never
  bodies or addresses. See the warning at the end of Phase V.
- **Playwright runs against local `webdev` (:5050) with stub Quick Login, not prod.**
  Production OIDC needs Ben and a second factor, so a prod browser smoke is a handoff,
  not something an agent completes — do not report prod login as verified from a local
  or mocked run.

## Phase V — local validation, before any k8s work

Runs from the `devel` checkout against the **development database**, using the real
expiration list already in it. This is the step that proves templates, audience,
throughput, chunking and the summary email with real mail in a real inbox. Ben has a
skip-mailbox filter set up for the volume.

### ⚠️ Pick a loaded week, or this proves nothing

Expirations cluster on month-ends, so ~40 of 52 Mondays send 0–15 emails. Running
Phase V on one of those exercises the query and the ledger and **nothing else** — no
chunking, no throughput, no realistic summary email.

Before running, find a date whose `[run+33, run+40)` band contains a month-end cluster:

```sql
SELECT DATE(a.end_date) d, COUNT(DISTINCT p.project_id) projects
FROM allocation a
  JOIN account ac ON ac.account_id = a.account_id
  JOIN project p  ON p.project_id = ac.project_id AND p.active = 1
WHERE a.deleted = 0 AND a.end_date >= CURDATE()
GROUP BY d ORDER BY projects DESC LIMIT 5;
```

Then drive the task at an occurrence 33–40 days ahead of that date. **Settled and
built: `--occurrence` is a real flag** (commit 6a), not a temporary constant edit:

```bash
sam-admin tasks --run expiration_notices --force --occurrence 2026-11-23T09:00
```

It is honored only alongside `--force`, where the ledger key is `M`-prefixed and
so cannot satisfy or displace a real scheduled slot. Record below which week was
exercised and how many messages it produced.

### Phase V results — run 2026-08-13, against the 3306 dev clone

All runs redirected to one mailbox. Occurrences driven with `--occurrence`.

| Occurrence | projects | selected | suppressed | sent | wall clock |
|---|---|---|---|---|---|
| 2027-10-04 (1-project probe) | 1 | 3 | 0 | 3 | 1.4 s |
| **2026-11-23 (loaded)** | **212** | **824** | 0 | **824** | **163 s** |
| 2026-11-30 (the week after) | 212 | 824 | **823** | **1** | 14.5 s |
| 2027-11-01 (empty band) | 0 | 0 | 0 | 0 | 0.5 s |

**Throughput: ~5.05 messages/second**, 824 messages in 163 s, 0 failures.

**The volume model above understated the peak.** It predicted ~535 by reasoning
about a 7-day *newly-entering* band; the shipped single rung spans the whole
40-day runway, so a loaded run catches **two** month-end clusters (Nov 30 and
Dec 31) — 824 messages, not 535. The cap at 2500 is still ~3× headroom, and
`expected_runtime=20 min` is still right: actual worst case is ~3 minutes, and
20 min is barely above the 16.7 min floor that keeps the lease above
`activeDeadlineSeconds`. Nothing to change; the *reasoning* in that section
needs the correction, not the numbers it drove.

**The pre-filter, measured.** The 2026-11-30 run is the whole argument in one
line: 824 selected, 823 dropped before the framework saw them, and
`notification_log` went 827 → **828**. Without it that row count would have
gone to 1,651 — and that is one week. It also made the run 11× faster.

**"0 sent" is legibly two different things**, exactly as § Risks required:
2026-11-30 reports `selected: 824, suppressed: 823`; 2027-11-01 reports
`selected: 0`. Same headline, unmistakable in `detail`.

### ⚠️ The relay defers hard after a burst — measured

SAM's side of all 829 messages completed cleanly: every one got a `250` from
`ndir.ucar.edu` and a `sent`/`redirected` ledger row, `failed = 0`,
`queued_stuck = 0`.

**Onward delivery lags badly, then catches up.** The first ~15 minutes' worth
arrived promptly; everything after 16:30:35 queued. The 824-run's own summary
was handed off at 16:30:36 and did not appear for roughly **20 minutes** — and
during that window the backlog trickled in **out of `Date` order**, which is the
tell that a queue is draining rather than that messages were lost. It did
arrive, intact and correct.

The cutoff is temporal, not per-kind: the 16:26 summary rendered from the
identical template arrived immediately. So this is the receiving side
rate-limiting a sender that just pushed 824 messages at **one** mailbox, with
`ndir` queueing and retrying behind it.

Two consequences worth keeping:

- **It is largely an artifact of the redirect.** In production those 824
  messages go to ~689 distinct addresses across many domains, so per-recipient
  and per-sender-pair limits do not concentrate the way they do when everything
  lands on one Gmail account. Do not size production expectations from this.
- **But "the task succeeded" genuinely does not mean "the mail arrived."** The
  ledger records handoff to the relay, which is the most it can honestly know.
  The per-run summary inherits that limit — and, being sent *last*, it is the
  message most likely to be stuck behind the batch it describes. An operator
  waiting on the summary as proof a run worked may wait a long time; the ledger
  and `sam-admin tasks --history` are the faster answer.

### Browser smoke — run 2026-08-13 against webdev :5050

All six assertions from commit 9 pass. Data was seeded by running the task at
an occurrence covering the page's own 31-day window with
`NOTIFY_TRANSPORT=null` — which writes ledger rows and sends nothing, exactly
what a UI fixture wants — plus one hand-inserted `failed` row
(`requested_by='playwright-smoke-fixture'`, delete it when done).

| # | Assertion | Result |
|---|---|---|
| 1 | badge renders with age + recipient count | 100 of 102 cards; title reads `Last expiration notice 2026-08-13 — delivered to 3 recipient(s)` |
| 2 | never-notified is an explicit state | 2 cards, `Not notified`, not a blank and not an `—` |
| 3 | failure badge | `1 failed`, alongside the notified badge on the same card |
| 4 | **absent on the user dashboard** | 5 cards, 19 badges, **0** notification badges, **0** envelope icons |
| 5 | computed WCAG contrast, both themes | see below — all pass **AAA**, not just AA |
| 6 | Notifications card + `Details »` | both render; facets reconcile |

Contrast, composited against the real ancestor chain rather than read off the
token values:

| badge | light | dark |
|---|---|---|
| `Notified … ago` | 10.35 | 7.67 |
| `Not notified` | 10.51 | 7.84 |
| `N failed` | 10.22 | 7.15 |

The admin surfaces reconcile with the ledger: `Redirected 1,732 + Sent 6 +
Failed 1 = 1,739` = the `email` channel total = `expiration 1,733 +
task_summary 6`. **`Suppressed` reads 0** — the pre-filter's whole purpose,
visible on the card it was protecting. The Scheduled Tasks card lists
`expiration_notices — weekly on Monday at 09:00 America/Denver`.

⚠️ **A local-dev-only timezone trap, found here.** The badge rendered
"Notified 6 hours ago" for rows written minutes earlier. Cause: `notified_age`
is `datetime.now() - creation_time`, and the *writer* was the CLI on the host
(MDT) while the *reader* was the webdev container, which has **no `TZ` set** in
`compose.yaml` and therefore runs UTC. The gap is exactly the offset.

Not a production bug — the webapp Deployment gets `TZ: "America/Denver"`
(`values.yaml:231`) and the CronJob inherits it (`cronjob-tasks.yaml:104-105`),
so writer and reader agree there. But it is a real trap for anyone smoke-testing
locally, and it is worth knowing that the badge inherits SAM's naive-Mountain
convention: its age is only meaningful when writer and reader share a zone.
Setting `TZ` on the compose services would fix local dev, but it would shift
*every* naive-datetime display there, so it is left as a separate decision.

### Commit 9's data path, against the real rows

Not the browser smoke (that still needs Playwright), but the query behind it,
run against the 828 rows Phase V produced:

- `get_expiration_notice_status()` over **214 projcodes in 97 ms** — one bulk
  query for a whole page, which is what commit 9 promised.
- Every entry carried the right `delivered_count`; `fmt.ago` rendered
  `'8 minutes'` from the returned timedelta.
- A projcode with no notices returned the full never-notified shape, so the
  template's three states are all reachable.
- Admin card: `redirected 828, sent 5, failed 0, queued_stuck 0`.

### What Phase V could NOT verify

- **The legacy-key bridge.** `notification_log` in this clone was **empty** (0
  rows) before these runs — Ben's pre-refactor manual sends are not in it. The
  overlap cohort the bridge exists for does not exist here, so the bridge is
  covered only by its unit test. **Recorded as untested against real data**,
  per this section's own instruction not to record a pass.
- **A full message-by-message inbox count.** 829 were handed to the relay and
  the tail was still draining at session end — 3 of the 5 summaries had arrived
  (the 3-sent, the 824-sent and the quiet-week), with the pre-filter run's
  "1 sent" summary and a late probe still queued. Everything that arrived
  reconciled; see below.

  Both delivered summaries render the § Risks distinction in a real inbox, not
  just in `TaskResult`: the loaded run reports `Selected 824`, the quiet week
  reports `Selected 0`, under the same "0 sent"-shaped headline.
- **Deliverability to real PIs**, obviously — every message was redirected.

### ⚠️ Two defects in the recipe below, found by running it

1. **V2 blocks V3.** `NOTIFY_TRANSPORT=null` still writes ledger rows, and with
   `NOTIFY_REDIRECT_TO` set their status is **`redirected`** — a *suppressing*
   status. So the null-transport rehearsal silently suppresses the real send it
   is meant to precede, and V3 reports `audience: 0` for reasons that look like
   a bug. Worse, the dedup key has no occurrence in it, so choosing a different
   `--occurrence` does not help: the same project + end date mints the same key.
   Either run V2 against a *different* cohort than V3, or delete its rows
   (`DELETE FROM notification_log WHERE requested_by='task:expiration_notices'`)
   before V3.
2. **"No message may lack `X-SAM-Original-To`" is too broad.** The per-run
   summary is addressed to the redirect target itself, so `resolve_recipient`
   correctly no-ops and the header is absent. The assertion must be scoped to
   `kind='expiration'`.

Both are recipe bugs, not product bugs. The far stronger form of the safety
check turned out to be **in the ledger, not the headers** — it covers the whole
population rather than a sample:

```sql
SELECT COUNT(DISTINCT recipient)                    -- must be 1
     , COUNT(DISTINCT intended_recipient)           -- how many real people were spared
     , SUM(intended_recipient IS NULL)              -- must be 0: redirect not applied
     , SUM(recipient = intended_recipient)          -- must be 0: escaped to its subject
FROM notification_log WHERE kind='expiration';
```

Measured on the 2026-11-23 run: `1, 689, 0, 0`.

### Three-way count reconciliation — passed

The plan asked for messages-received == ledger == `TaskResult`. All three agree
on the loaded run, and the summary email's per-project breakdown gives a fourth,
independent cross-check:

| Source | Sent | Projects | WNA messages |
|---|---|---|---|
| `TaskResult.detail` | 824 | 212 | — |
| `notification_log` | 824 | 212 | 196 across 17 projects |
| Summary email body | 824 | 212 | its 17 `WYOM` lines sum to **196** |

The WNA figure is the satisfying one: the ledger's `template` column recorded
`expiration-WNA.txt` exactly 196 times, the facility join says 17 WNA projects,
and the summary — built from an entirely separate code path, the message list —
lists 17 `WYOM` projects whose recipient counts sum to 196. Mailbox spot-checks
confirmed correct `To:`, `X-SAM-Original-To` present and differing, the redirect
banner in both MIME parts, matching text/HTML variants on a UNIV and a WNA
sample, and SPF/DKIM/DMARC all passing.

### ⚠️ Two hard preconditions

1. **`NOTIFY_REDIRECT_TO` must be set.** The dev DB at **3306 holds real production
   data** after a bare `make clone` — real PI addresses. Without the redirect, a
   laptop mails 600 real people. `NOTIFY_ENABLED` is fail-closed precisely so this
   cannot happen by accident; do not defeat both at once.
2. **Never point this at the production database.** `redirected` is a **suppressing
   status** (`ledger.py:44`), and the dedup key is built from the *intended* recipient
   **before** the redirect is applied (`service.py:186-191`) — by design, so a staging
   run cannot collapse onto one key. The consequence: a redirected run writes rows that
   would **suppress the real send for every one of those recipients**. Against a local
   copy that is harmless. Against prod it silently cancels next month's notices.

### Steps

```bash
source etc/config_env.sh          # dev DB; confirm it is NOT prod before proceeding

# V1 — content only. No ledger rows, no sockets. Reviews subject/body/audience.
sam-admin project --upcoming-expirations --notify --dry-run

# V2 — the task itself, null transport. Exercises window math, the cap, the
#      ledger, and TaskResult, while smtplib is guaranteed untouched.
NOTIFY_ENABLED=1 NOTIFY_TRANSPORT=null \
  sam-admin tasks --run expiration_notices
sam-admin --format json tasks --history | jq

# V3 — the real thing, every message redirected to Ben. Real SMTP, real volume,
#      real ledger rows (status='redirected'), real summary email.
NOTIFY_ENABLED=1 NOTIFY_TRANSPORT=smtp \
NOTIFY_REDIRECT_TO=benkirk@ucar.edu \
  sam-admin tasks --run expiration_notices
```

`sam-admin tasks --run <name>` bypasses the misfire grace and keys the ledger row with
an `M`-prefixed manual occurrence key, so it cannot collide with a scheduled slot.

### What to check afterwards

The inbox checks below are **assertions, not eyeballing** — run this phase from a
`claude --resume` session with the Google MCP enabled (see *Verification tooling*).
Scope every mailbox query to `from:sam-admin@ucar.edu` within the run window.

- **Ledger**: `SELECT status, COUNT(*) FROM notification_log WHERE kind='expiration'
  GROUP BY status` — expect all `redirected`, zero `failed`, zero stuck `queued`.
- **Count reconciliation**: messages received == `redirected` in the ledger ==
  `TaskResult.detail`'s count. Three independent sources; all three must agree.
- **The safety assertion — no message may lack `X-SAM-Original-To`.** That header
  (`transports/smtp.py:33`) is set *only* on the redirect path, so a message without
  it is one that went to its real recipient. The check is not "most have it"; it is
  **zero without it**. Assert the `To:` differs from the header value too.
- **Template variants**: sample one UNIV and one WNA recipient (read the intended
  address off `X-SAM-Original-To`) and confirm each got the right variant — and that
  the HTML part matches the text part's variant. They are resolved together on
  purpose; a WNA recipient with UNIV HTML is the specific bug that pairing prevents.
- **The summary email** arrived, and its counts reconcile with the ledger.
- **Throughput**: wall-clock of the run, on a **loaded** week. This is the number that
  decides whether `expected_runtime=20min` and `activeDeadlineSeconds=3000` are right.
  **Record it in this doc**, along with which week was exercised.
- **Re-run V3 immediately.** Every message must come back `suppressed` and **zero new
  mail** must arrive — the dedup proof, and the same 602-sent-then-602-suppressed check
  `NOTIFICATION_FRAMEWORK.md` used. Asserting "no new messages since timestamp T" is
  exactly the check a human skims past and a tool does reliably.
- **The legacy-key bridge.** The dev DB carries `notification_log` rows from Ben's
  real pre-refactor CLI runs, in the *old* key format — the only place the bridge can
  be tested against genuine data. Confirm the overlap cohort (projects notified in the
  last manual run whose end dates still fall in the 40-day window) comes back
  `suppressed` rather than sending twice. If that cohort is empty in the dev snapshot,
  **say so explicitly rather than recording a pass.**

⚠️ **These are real messages about real people.** The dev DB carries production data,
so bodies contain PI names, project codes and usage, and `X-SAM-Original-To` carries
real addresses. Report counts, header presence and variant names. Do **not** paste
message bodies or recipient addresses into this doc, a commit message, or a summary —
the same rule that keeps anything derived from port 3306 out of the repo.

---

## Build, continued

### 8. `helm: notify config and the send cap for the tasks CronJob`

**The highest-severity item in this plan.** `cronjob-tasks.yaml:95-121` renders only
`.Values.tasks.env` plus a hand-listed set (`TZ`, `RUNNER_ID`, the two DB blocks). It
does **not** inherit `.Values.webapp.env`, where `NOTIFY_ENABLED: "1"`,
`NOTIFY_TRANSPORT` and all `MAIL_*` live (`values.yaml:245-257`). As things stand the
first production run mails nobody, silently.

- Add `NOTIFY_ENABLED`/`NOTIFY_TRANSPORT`/`MAIL_*` to the CronJob using the template's
  existing cross-reference pattern (`{{ .Values.webapp.env.X | quote }}`), not a
  duplicated block, which would drift.
- `tasks.env`: `SAM_TASKS_EMAIL_MAX: "2500"`, summary recipient.
- Extend `helm/tests/test-cronjob-render.sh` to assert `NOTIFY_ENABLED` renders.
- Leave `SAM_TASKS_DISABLED` naming only `cleanup_status_snapshots` (`:449`).

Ship **enabled** — but note the weekly cadence removes the accidental grace period a
monthly schedule would have given: the first natural fire is **within 7 days**, not up
to a month. That is exactly why Phase V runs first and is not optional. If the deploy
lands on a Monday morning, consider `SAM_TASKS_DISABLED` for a few hours rather than
discovering a chart problem live. `sam-admin tasks --run expiration_notices` remains
the manual trigger.

### 9. `webapp: last-notified badge on Project Expirations`

- **Query** — new helper in `sam/queries/notifications.py` following
  `get_xras_activity()` (`xras_activation.py:251-316`): newest *delivered* expiration
  notice per projcode plus delivered/failed counts, as one `GROUP BY projcode` with
  conditional aggregation over `kind='expiration'`, using the existing
  `notification_log_projcode` index. Return `notified_age` as a **timedelta** —
  `fmt.ago` (`sam/fmt.py:289`) takes a delta, and keeping `datetime.now()` out of Jinja
  is what makes it testable (`xras_activation.py:307-311` says so explicitly).
- **Route** — attach in `_build_expiration_project_data()`
  (`webapp/dashboards/admin/blueprint.py:382-405`) as **one bulk query for all
  projcodes**; that function is already N+1 on `get_project_dashboard_data`.
- **Template** — the right-hand badge group of `render_project_card`
  (`dashboards/user/partials/project_card.html:369-390`), markup copied from
  `xras_activity_card.html:151-163`. Gate on the key's presence so the shared macro
  renders nothing on the user dashboard, which never sets it.
- Neither `/admin/projects` nor `/admin/expirations` is cached.

**Accepted imprecision:** the badge shows the newest expiration notice for the project
regardless of *which* expiration date it referred to, so a project notified about a
prior year reads "Notified 400 days ago". The tooltip carries the absolute date and
recipient count, making a stale one self-evident. Matching `dedup_key`'s embedded date
against the card's currently-computed expiration is more precise and more fragile.

**Browser smoke (Playwright, against `webdev` on :5050 with Quick Login):**

1. `/admin/projects` → Expirations tab → for a project with `notification_log` rows,
   the badge renders with `Notified <n> <unit> ago` and its `title` names the
   recipient count.
2. A project with **no** rows shows the `Not notified` state — not a blank, and not
   an `—` from `fmt_ago`'s null path.
3. A project with failed deliveries also shows the failure badge.
4. **The badge is absent on the user dashboard.** `render_project_card` is shared, and
   only the admin path sets the key. This is the regression a shared macro invites and
   the single most valuable assertion here.
5. **Computed contrast, both themes.** The badge uses `bg-success-subtle` /
   `text-success-emphasis`; assert the *computed* WCAG ratio in the browser rather than
   judging it by eye, in light and in dark. Dark mode is where subtle-background badge
   pairs have failed before.
6. While the browser is up and Phase V has left real rows behind, smoke Admin →
   Configuration → Notifications and its `Details »` page, which now have data to render.

`webapp` (:7050) and `webdev` (:5050) share Redis db 0. Neither `/admin/projects` nor
`/admin/expirations` is cached, so this is low-risk here — but flush before any A/B
comparison, or one port serves the other's cached fragments.

### 10. Docs

Corrections to `SCHEDULED_TASKS.md` § 12 items 2 and 4 (both overridden); `CLAUDE.md`
§ Notifications — the task, the two new `Notifier` knobs, and the
`NOTIFY_*`-must-reach-the-CronJob trap.

---

## Verification

```bash
source etc/config_env.sh
docker compose --profile test up -d mysql-test
export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'

pytest tests/unit/test_notify_import_graph.py          # the eager-import gate
pytest tests/unit/test_expiration_notices.py           # must pass UNTOUCHED after commit 4
pytest tests/unit/test_notify_*.py tests/unit/test_task_*.py -v
pytest                                                 # full suite
bash helm/tests/test-cronjob-render.sh
```

No new schedule predicate means **no new predicate tests** — `Weekly` is already
covered by `tests/unit/test_schedule_predicates.py`, DST matrix included.

New tests, by tier: `already_sent`/`already_sent_many` **agreement matrix** over all
six status/age cases as the anti-drift gate; chunked-transport open/close counts and a
transport failing `open()` only on the second chunk; task-tier window determinism
(dispatch at `occ+5m` and `occ+20h` → identical window), cap trip, `NOTIFY_ENABLED`
unset → `failed`, and capsys asserting the task writes nothing to stdout.

Cadence-specific — these are the ones the weekly decision makes load-bearing:

- **The quiet week.** Two consecutive Monday dispatches over an unchanged dataset:
  the first sends N, the second sends **0** and writes **0** `notification_log` rows
  of any status, reporting `audience: 0`. This is the regression gate on the
  pre-filter; without it the second run writes ~N `suppressed` rows.
- **Self-healing.** Skip a Monday (simulate a `skipped` misfire), dispatch the next:
  every expiration the skipped run would have caught is still selected and sent.
- **Tiling invariant.** A synthetic three-rung `MILESTONES` produces **disjoint**
  audiences with distinct dedup keys and no project in two bands; and a deliberately
  under-wide band (narrower than the 7-day run gap) is caught by an assertion at
  registration rather than silently dropping notices.
- **Legacy bridge.** A message whose old-format key is already `sent` is suppressed.

In-cluster, after Phase V and commit 8, per § 13's recipe: `kubectl create job
--from=cronjob/samuel-tasks`, check logs, confirm a `succeeded` row **and that
`NOTIFY_ENABLED` actually reached the pod**.

The badge's browser smoke is specified with commit 9; the mailbox assertions with
Phase V. Both need MCP tools this session did not have — see *Verification tooling*.

## Risks

- **The chart.** A missed `NOTIFY_*` makes the first run a silent no-op that reports
  success. Mitigated twice — the `config.enabled` check in the task (commit 6 step 5)
  and the helm-test assertion — because it is invisible otherwise.
- **The suppressed-row flood, if the pre-filter is ever removed.** Under a weekly
  cadence the task's own drop step (commit 6 step 3) is what keeps 48 quiet weeks
  quiet. Delete it as "redundant with `Notifier`'s own dedup" and `notification_log`
  gains ~26,000 rows a year, which the last-notified badge and the admin Notifications
  card both read. The comment there must say *why* it is not redundant.
- **A PI still gets exactly one notice per expiration, for now.** One rung is
  configured. The *machinery* for 60/30/7 is in place and the key already carries the
  rung label, so enabling the ladder is a one-tuple edit — no key migration, no forced
  re-notify, and the weekly cadence already supports 7-day bands. Until then, anyone
  expecting staged reminders will not get them.
- **The key format changes on commit 4**, for the CLI as well as the task. The legacy
  half of the pre-filter covers the overlap cohort; if it is omitted or dropped too
  early, those recipients get one duplicate notice. Not harmful, but it will be
  noticed. Keep it until one full cycle has run.
- **`partial` → exit 2 → a red Job on one bounced address.** A hard bounce somewhere
  in a ~250–535 loaded run is likely, and a CronJob that goes red trains people to
  ignore it. Because volume is spiky, this concentrates on the 12 loaded runs a year —
  the quiet weeks will be reliably green, which makes a red one *more* informative, not
  less. Ship strictly as the framework intends; if red becomes routine, add a
  failure-rate threshold in the task rather than weakening `TaskResult`. The summary
  email softens it — Ben learns of failures by mail regardless of Job color.
- **A quiet week can mask a broken selection.** ~40 runs a year legitimately send
  nothing, so "0 emails, succeeded" is the *normal* result and cannot be distinguished
  from a query that silently stopped matching. The `detail` must therefore always carry
  the window bounds and the pre-filter counts (selected / suppressed / sent), so a run
  that selected 0 rows is visibly different from one that selected 300 and suppressed
  them all. Worth a glance at the summary email's numbers after any change to the
  window or facility scoping.
- **Modifying a just-shipped framework.** `sam/notify/` has an import-graph gate and
  its own suite; commits 1–3 must be additive with defaults reproducing current
  behavior exactly.
- **`expiration_notices` is the first `needs=('sam',)` task.** The SAM session factory
  now raises rather than exits, but nothing else in the tree depends on that yet, so
  `TestASessionFactoryThatFails` in `tests/unit/test_task_runner.py` plus the
  source-level wiring guard in `test_cli_context.py` are the only things holding it.
  If someone "tidies" `tasks/commands.py` back to `require_sam`, a SAM outage silently
  becomes a dead dispatcher again — the guard test exists to make that loud.
- **`expected_runtime` is being used as a tuning knob.** It is documented as "drives
  the lease, not a timeout". The honest fix is `TaskContext.heartbeat()` —
  `Task.long_running` already exists and is unused (`registry.py:70`). Not built here;
  the drift test and a comment naming the reason are the interim.

## Open questions

1. When is the ladder worth enabling? The weekly cadence already supports it — 7-day
   bands tile exactly — so it is a product decision, not an engineering one: do PIs
   want a 60/30/7 sequence, or is one notice at ~35 days the right amount of mail?
   Enabling it multiplies steady-state volume by roughly the number of rungs.
   **Mechanically it is now a one-tuple edit** to `MILESTONES` in
   `sam/queries/expiration_notices.py`, with no key migration and no forced
   re-notify; `TestTheLadderTiles` exercises a synthetic three-rung configuration.
2. Phase V's measured throughput decides whether `expected_runtime=20min` is right.
   Size it against a **loaded** week (~535 at peak), not a quiet one. Record the
   number here once known. **Still open.**
3. ~~`sam/queries/expiration_notices.py` is the weakest naming call in this plan.~~
   **Settled**: it stays in `sam/queries/`, beside `expirations.py` whose exact
   tuple it consumes and `notifications.py` which reads back what it caused. It is
   deliberately **not** exported from `sam/queries/__init__.py`, which imports its
   submodules eagerly — listing it would put `sam.notify.base` into the import
   graph of every `from sam.queries import ...`.
4. **When can the legacy-key bridge go?** After one full cycle, at which point
   every live key carries a rung label. `legacy_dedup_key()` and the second half
   of the key list in `_drop_already_notified` are the only things to delete; the
   pre-filter itself is permanent. Phase V is where the bridge gets its only test
   against genuine pre-refactor rows.
