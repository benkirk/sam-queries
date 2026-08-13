# Expiration Notices — the scheduled-task consumer

**Status: designed, not built.** Approved 2026-08-13. Written to be picked up
cold; every claim carries a `file:line` so the next reader can verify rather
than re-derive.

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

`Context.require_sam()` calls `sys.exit(1)` on a connection failure
(`cli/core/context.py:80`). `runner._execute` catches `Exception`, **not**
`BaseException` — deliberately, so a pod's `activeDeadlineSeconds` kill leaves the
row `running` for reclaim rather than mislabeling it `failed` (`runner.py:200-213`).
A `SystemExit` raised inside a task body therefore escapes `run_due`, escapes
`TasksCommand.execute`, and **terminates the dispatcher process** — skipping every
later task and stranding a `running` row.

Secondary: a fresh `Context` binds `console = Console()` on **stdout**, and the
CronJob runs `sam-admin --format json tasks --run-due`, whose stdout is a JSON
envelope operators pipe to `jq`. `notification_progress` (`display.py:529`) plus the
two `display_*` calls would interleave rich tables and ANSI into it.

Extraction also satisfies § 12 item 4's actual *requirement* — that the
`json_mode and notify` guard (`commands.py:125-133`) stay a CLI-flag check — more
cleanly, because `execute()` is not touched at all.

### Why weekly, and why Monday

An earlier draft used a `FirstWeekdayOfMonth` predicate. It was dropped, and the
reasoning is worth keeping because it inverts several of this plan's other decisions.

**"First weekday" is not reliably Monday.** Over 2026–2045 it lands on Monday only
**42%** of months and on **Friday 14%** — and a 600-recipient notice sent Friday
morning is read Monday anyway, with any replies landing while the sender is away.

**Weekly is strictly better on four axes**, measured rather than assumed:

| | first weekday of month | `Weekly(0, 9, 0)` |
|---|---|---|
| Day of week | Mon 42%, Fri 14% | always Monday |
| Gap between runs | 28–33 days | 7 days |
| Messages per run | ~600 burst | ~600 once, then **~105** |
| A missed run | loses a whole month | self-healing — see below |

- **Self-healing.** With a 40-day band and 7-day runs, one expiration is selected on
  **5–6 consecutive runs**. Any single skipped or failed week is recovered by the next
  one, with dedup preventing a double-send. This is what retires the "monthly means one
  shot" risk entirely, and it makes federal-holiday Mondays (~5–6 a year) a non-issue.
- **It deletes a commit.** `Weekly(weekday=0, hour, minute, tz)` already exists
  (`schedules.py:248-273`). No new predicate, no weekend-rollover rule, no DST test
  matrix, no argument about `_MAX_LOOKBACK_DAYS`.
- **Steady-state volume drops ~6×.** The batching work in commits 1–3 is still right —
  for the first run, and for the future 2000+ outage-subscription consumer — but it
  stops being the load-bearing risk it was under a monthly burst.

**The cost, and it is real:** roughly 85% of each week's selection has already been
notified. The task therefore **must drop suppressed messages itself** rather than
letting `Notifier` record them — see commit 6 step 3. Left to the framework, 48
near-no-op weeks a year would each write ~500 `suppressed` rows: ~26,000 rows a year
polluting `notification_log`, the new badge, and the admin Notifications card.

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

## Phase V — local validation, before any k8s work

Runs from the `devel` checkout against the **development database**, using the real
expiration list already in it. This is the step that proves templates, audience,
throughput, chunking and the summary email with real mail in a real inbox. Ben has a
skip-mailbox filter set up for the volume.

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

- **Inbox**: message count matches the reported audience; every message carries an
  `X-SAM-Original-To` header (`transports/smtp.py:33`) naming the intended recipient;
  UNIV and WNA recipients got the right template variant; the summary email arrived
  and its numbers match the ledger.
- **Ledger**: `SELECT status, COUNT(*) FROM notification_log WHERE kind='expiration'
  GROUP BY status` — expect all `redirected`, zero `failed`, zero stuck `queued`.
- **Throughput**: wall-clock of the run. This is the number that decides whether
  `expected_runtime=20min` and `activeDeadlineSeconds=3000` are right. **Record it in
  this doc.**
- **Re-run V3 immediately.** Every message must come back `suppressed` and the inbox
  must stay quiet — that is the dedup proof, and it is the same 602-sent-then-602-
  suppressed check `NOTIFICATION_FRAMEWORK.md` used.
- **The legacy-key bridge.** The dev DB carries `notification_log` rows from Ben's
  real pre-refactor CLI runs, in the *old* key format — which makes it the only place
  the bridge can be tested against genuine data. Confirm the overlap cohort (projects
  notified in the last manual run whose end dates still fall in the 40-day window)
  comes back `suppressed` rather than sending a second time. If that cohort is empty
  in the dev snapshot, say so explicitly rather than recording a pass.

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
`NOTIFY_ENABLED` actually reached the pod**. Badge: load `/admin/projects` →
Expirations, confirm it renders for a project with `notification_log` rows and is
absent on the user dashboard.

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
  in ~600 first-run recipients is likely, and a CronJob that goes red trains people to
  ignore it. Weekly makes this *more* frequent than monthly would have, which cuts both
  ways: more chances to go red, but a red week is also cheap because the next run
  self-heals. Ship strictly as the framework intends; if red becomes routine, add a
  failure-rate threshold in the task rather than weakening `TaskResult`. The summary
  email softens it — Ben learns of failures by mail regardless of Job color.
- **Modifying a just-shipped framework.** `sam/notify/` has an import-graph gate and
  its own suite; commits 1–3 must be additive with defaults reproducing current
  behavior exactly.
- **`expected_runtime` is being used as a tuning knob.** It is documented as "drives
  the lease, not a timeout". The honest fix is `TaskContext.heartbeat()` —
  `Task.long_running` already exists and is unused (`registry.py:70`). Not built here;
  the drift test and a comment naming the reason are the interim.

## Open questions for whoever picks this up

1. When is the ladder worth enabling? The weekly cadence already supports it — 7-day
   bands tile exactly — so it is a product decision, not an engineering one: do PIs
   want a 60/30/7 sequence, or is one notice at ~35 days the right amount of mail?
   Enabling it multiplies steady-state volume by roughly the number of rungs.
2. Phase V's measured throughput decides whether `expected_runtime=20min` is right.
   Note Phase V measures the **first-run burst** (~600), not steady state (~105), which
   is the correct thing to size the lease against. Record the number here once known.
3. `sam/queries/expiration_notices.py` is the weakest naming call in this plan —
   it builds rather than queries. `sam/notifications/expiration.py` is defensible.
   It must not go inside `sam/notify/`.
