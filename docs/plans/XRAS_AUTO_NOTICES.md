# Automatic XRAS notices

**Status:** planned, not started. Design settled 2026-08-14 with Ben; nothing
built.

**Branch when executed:** fresh off `staging`. The cascade is complete — #447
(scheduled tasks + the `--dry-run` fix) and #448 (`deactivate_expired_projects`)
merged 2026-08-14, so `staging` already carries `src/scheduling/`, a `--dry-run`
that genuinely previews, and the XRAS activity card. Nothing to stack on.

---

## 1. Why

The Allocations → XRAS tab shows **"Pending Activations & Recent
Notifications"**, rebuilt during the XRAS reimplementation to be keyed on the
**action** rather than the project. Every row that maps to a notification kind
carries a manual **Notify** button. If nobody clicks it, the PI is never told
their allocation was extended or supplemented — the outcome already happened in
SAM, and the only thing missing is that anyone said so.

Automating it was explicitly deferred, and the deferral says the important part
(`docs/xras/incoming/implemented/XRAS_PRE_DEPLOY_SMOKE.md:701`):

> ☑ **Automatic sending** — **follow-on, not this branch.** Everything stays
> manual through the ledger for cutover. The message builders already take an
> action and are shaped for a handler to call.

That is right about *shape* and wrong about *location*. The builder is a private
closure inside a Flask blueprint, and `src/scheduling/` is AST-gated against
importing Flask. **Extracting it is the bulk of the work**; the task itself is
small, in the same way `deactivate_expired_projects` was.

## 2. What already works, and what does not

### Reusable today — verified by probe, not by reading

`sam/queries/xras_activation.py` imports clean with `FLASK_ACTIVE` unset (pulls
none of `click / flask / rich / kubernetes / webapp / cli`), so a task may call:

| symbol | file:line | |
|---|---|---|
| `get_xras_activity(session, *, since, until, statuses=('processed',))` | `xras_activation.py:166` | the action-keyed cohort; rows carry `tags` incl. `not_notified` |
| `get_xras_pending_recipients(session, project_ids)` | `:413` | lead + admin contacts, `{project_id: [{name,email,role}]}` |
| `xras_dedup_key(kind, projcode, action_id, address)` | `:84` | the one place the key is spelled |
| `parse_xras_dedup_key(key)` | `:98` | the correlation back to an action — no FK, on purpose |
| `XRAS_SERVICE_KINDS` | `:70` | `service` → notification kind |
| `XrasActivationEvent.create(session, …, created_by=…)` | `sam/integration/xras.py:288` | already parameterised; no `current_user` |
| `Notifier`, `NotificationLedger`, `Message`, `to_recipients` | `sam/notify/` | already driven from a task by `expiration_notices` |
| all five `xras_*.{txt,html}` templates + kinds | `sam/notify/templates/`, `kinds.py:68-129` | nothing new to write |

### Flask-entangled — the extraction

All in `src/webapp/dashboards/allocations/blueprint.py`:

| symbol | line | coupling |
|---|---|---|
| `_xras_messages(project, people, *, action=None)` | 1748 | `db.session`, `current_user.username` |
| `_action_increments(action, *, signed=False)` | 1683 | `db.session.query(...)` at 1721 |
| `_XRAS_KIND_SUBJECTS` | 1665 | none — it just lives in the wrong file |
| `_load_xras_action(action_id)` | 1676 | `db.session` |

Only **two kinds** of coupling, and both have a worked precedent:
`build_expiration_messages` (`sam/queries/expiration_notices.py:121`) takes
`requested_by` as a parameter for exactly the `current_user`-vs-`task:` split,
and every `sam/queries` function already takes a `session`.

## 3. Decisions taken

| | |
|---|---|
| **Auto-send** | everything **except `add`** — `update`, `extend`, `supplement`, `adjust`. |
| **`add` (New)** | manual only. It needs `active=True` **and** a notice — two writes. See § 7. |
| **`transfer`** | has no notification kind at all; can never auto-send, even by mistake. |
| **Delay** | a `timedelta`, default **1 day**, per service. Not a `days=`/`hours=` pair. |
| **Cadence** | a new **`BusinessHourly`** primitive — hourly, 08:00–17:00 **inclusive**, **Mon–Fri**, Mountain. 10 slots/day, 50/week. |
| **Rollout** | ships listed in `SAM_TASKS_DISABLED`; cleared in a later commit after a soak. |

### Why `timedelta`, not `days=` / `hours=`

It is already the idiom in this package — `misfire_grace=timedelta(days=7)`,
`expected_runtime=timedelta(minutes=2)` — so a policy row reads like the
decorator above it. Hours cost nothing (`timedelta(hours=6)`), there is no unit
ambiguity in the ledger `detail`, and there is no "both set / neither set"
validator to write. Runtime tuning without a deploy goes through **one** env var
in hours, read per run, exactly as `cleanup_status.retention_days()` does:

```python
#: Hours, because it is the finer unit — "24" is a day, "6" is six hours.
#: Read per run so a values.yaml change lands on the next dispatch, not the
#: next pod restart.
SAM_XRAS_NOTIFY_AFTER_HOURS
```

### Why a new `BusinessHourly`

`Hourly` would make a sub-24h threshold real but sends at 03:00. `Daily` never
sends at 03:00 but makes the threshold inert — a 6-hour setting would round up
to whenever the daily slot lands. `BusinessHourly` gives both, and removes the
overnight-mail problem *structurally* rather than by bolting a quiet-hours check
onto every task that mails people.

⚠️ **The effective delay is longer than the nominal one.** The task's `detail`
must report the actual age of what it sent, so this never reads as a stuck queue:

```
received Tue 09:15, after=1d  ->  eligible Wed 09:15  ->  sends Wed 10:00  (~1.0d)
received Fri 16:30, after=1d  ->  eligible Sat 16:30  ->  sends Mon 08:00  (~2.6d)
```

## 4. ⚠️ The surprising rule — write this one down loudest

**A card row badged `New` can auto-send.**

The badge shows `action_type` (the raw wire string); the policy keys on
`service`. Dispatch routes a `New` whose projcode **already exists** to the
`update` service (`sam/xras/dispatch.py:201-215`), and `update` is in the auto
set. That is correct — it is a renewal in all but name and the project needs no
activation — but an operator seeing "New" auto-send will reasonably conclude the
policy leaked. It gets a test that names it (§ 6).

This is also why the policy **must** key on `service`, never `action_type`:

- `action_type` is nullable (`xras.py:124`, "NULL when the body could not be parsed");
- it has aliases (`Adjust` → `Adjustment`, `xras_actions.py:111`);
- it is deliberately unconstrained, so an unrecognised value still lists;
- it includes `'Date Adjustment'`, which is not serviced at all;
- and `New` does not imply a new project, per above.

`service` is the constrained six-member vocabulary
(`add/update/extend/supplement/transfer/adjust`, `dispatch.py:96`) and is already
what decides whether a row is notifiable at all.

## 5. The work

### Commit 1 — `BusinessHourly`

Generic scheduling infrastructure in `src/scheduling/schedules.py`,
independently useful to any future task whose output a human reads. Commit 1 of
this PR per the repo's one-PR-per-track convention, but self-contained enough to
split out if it needs to land first.

**No base-class change.** `_LocalWallSchedule` already asks subclasses exactly
one question — `_candidates_on(local_date) -> [(hour, minute)]` — and its search
loop (`schedules.py:158-168`) already iterates *multiple* candidates per day,
taking the latest ≤ now. `Daily` returns one pair; this returns ten. DST, UTC
canonicalization, `last_occurrence` and `next_occurrence` are all inherited.

```python
@dataclass(frozen=True)
class BusinessHourly(_LocalWallSchedule):
    """Every hour at ``:minute``, 08:00-17:00 inclusive, Mon-Fri, in :attr:`tz`."""

    minute: int = 0
    start_hour: int = 8
    end_hour: int = 17                       # INCLUSIVE — 17:00 fires
    weekdays: Tuple[int, ...] = (0, 1, 2, 3, 4)

    def _candidates_on(self, local_date):
        if local_date.weekday() not in self.weekdays:
            return []
        return [(h, self.minute)
                for h in range(self.start_hour, self.end_hour + 1)]
```

Plus `__post_init__` validation (hour ranges, `start_hour <= end_hour`, weekday
values, `minute` 0–59) and a `describe()` rendering as
`hourly 08:00-17:00 Mon-Fri America/Denver` — that string is what
`sam-admin tasks --list` prints and what a registration test pins.

⚠️ **The DST subtlety, which is the interesting part of this class.** `Hourly`'s
docstring (`schedules.py:195-209`) explains it is UTC-only *because* a local-wall
hourly schedule "loses one slot each fall (the repeated hour folds onto one
instant) and risks merging one each spring". **`BusinessHourly` is that
schedule** — the warning applies to the class in general. It does not bite for
the default window only because US transitions happen at **02:00 local**, which
08:00–17:00 never contains.

So state in the docstring that **the immunity is a property of the window, not
the class**, and prove it with tests on both transition dates plus the inverse
case (a 01:00–05:00 window *does* hit the fold). Someone narrowing `start_hour`
below 03:00 walks straight into the `Hourly` warning, at which point
`_to_utc_naive`'s fold/gap rules (`schedules.py:70-106`) become load-bearing.

**Misfire needs no special handling** — checked against the runner. The 17:00
slot does not accumulate misfire rows overnight, because `run_due` tests "already
settled" *before* lateness (`runner.py:130-161`, an ordering its own comments
call load-bearing for exactly this shape). A 17:00 slot that ran reads
`already_claimed` at 23:07, not `misfire`. The default 6 h grace is fine.

### Commit 2 — extract the builder

New module **`src/sam/queries/xras_notices.py`**, mirroring
`sam/queries/expiration_notices.py`. Move `_xras_messages`, `_action_increments`,
`_XRAS_KIND_SUBJECTS` and the `_load_xras_action` helper out of the blueprint:

```python
def build_xras_messages(session, project, people, *, action=None,
                        requested_by: str) -> List[Message]:
```

- `db.session` → the `session` parameter (three call sites).
- `current_user.username` → `requested_by=`.
- Rewire `xras_notify_form` (`blueprint.py:1817`) and `xras_notify` (`:1897`) to
  call it, so the route and the task cannot drift on the dedup key — the same
  rationale `expiration_notices.py:3-8` gives for its own extraction.

❌ **Do NOT add it to `sam/queries/__init__.py`.** That file imports its
submodules eagerly, so listing a module that imports `sam.notify` puts
`sam.notify.base` into the graph of every `from sam.queries import ...` and
breaks `tests/unit/test_notify_import_graph.py`. Import it by full dotted path.

⚠️ The trap is that `xras_activation.py` **is** exported (`__init__.py:124`) —
safely, because it does not import `sam.notify`. The two modules look alike and
must be treated differently.

### Commit 3 — the task, shipped kill-switched

**`src/scheduling/tasks/xras_notices.py`**, plus the import line and `__all__`
entry in `scheduling/tasks/__init__.py` — the whole registration surface, since
`TASKS` is populated purely by import side effect.

```python
SCHEDULE = BusinessHourly(minute=20, tz='America/Denver')

@dataclass(frozen=True)
class AutoNotice:
    service: str
    after: timedelta

#: Fail-closed: a service absent here is NEVER auto-sent. `add` is absent on
#: purpose (§ 7); `transfer` has no notification kind at all.
AUTO_NOTICES = (
    AutoNotice('update',     after=timedelta(days=1)),
    AutoNotice('extend',     after=timedelta(days=1)),
    AutoNotice('supplement', after=timedelta(days=1)),
    AutoNotice('adjust',     after=timedelta(days=1)),
)
```

Body, following `scheduling/tasks/expiration_notices.py`:

1. All `sam` imports **deferred inside the function** — `scheduling/` is imported
   by the CLI's `--list` path, which must not pay for jinja2 and the ORM to
   print a table.
2. Cohort from `get_xras_activity(session, since=…)`, filtered to rows whose
   `service` is in `AUTO_NOTICES`, `notifiable`, not `notified`, not
   `dismissed`, and `received_time <= slot - after`.
3. **Compute from `ctx.occurrence`**, converted with `to_local_naive`.
   `received_time` is naive-**Mountain** from the app clock, never a DB default —
   `xras.py:112-116` is emphatic about this, because MySQL's `CURRENT_TIMESTAMP`
   resolves in UTC in the containers.
4. Pre-filter with `ledger.already_sent_many()` before building, as
   `expiration_notices._drop_already_notified` does. Left to `Notifier`, every
   quiet hour would write `suppressed` rows into the table the admin card reads.
5. `Notifier` with a ledger on its **own** session (`_new_sam_session`), never
   `ctx.sam_session` — mail cannot be un-sent by the rollback `close_sessions`
   performs on failure.
6. `requested_by='task:xras_notices'`. Not `getpass.getuser()`, which in that pod
   is the runtime UID or a `KeyError`.
7. Raise if `NOTIFY_ENABLED` is false, as `expiration_notices` does — otherwise a
   chart mistake reports `succeeded` and mails nobody.
8. Write `XrasActivationEvent.create(..., event_type='notified',
   created_by='task:xras_notices', notified_to=…, xras_action_log_id=…)` on
   `ctx.sam_session`; the runner commits it. **Not** for the card's badge —
   `row['notified']` derives from `notification_log` via the parsed dedup key
   (`xras_activation.py:254-263, :307`) and works without it — but so the history
   modal does not show a notice appearing from nowhere.
9. **No `ctx.dry_run` branch for the DB writes** (the runner rolls them back),
   but the send **must** branch: use `notifier.preview()`. A rollback undoes
   rows, not mail. This is precisely the case `TaskContext.dry_run`'s note warns
   about.
10. `detail` always carries the window, per-service counts, and
    selected / suppressed / sent — so "0 sent, succeeded", the normal result for
    most hours, is distinguishable from a query that quietly stopped matching.

**Helm**: add `xras_notices` to `SAM_TASKS_DISABLED` in `helm/values.yaml`
(currently `"cleanup_status_snapshots"`), and assert it **per-manifest**
(`-s templates/cronjob-tasks.yaml`) in `helm/tests/test-cronjob-render.sh` — a
whole-render grep passes on the Deployment's copy and proves nothing.

### Commit 4 — tests

- **`BusinessHourly`** (`tests/unit/test_schedule_predicates.py`): the ten slots,
  the Mon–Fri skip, `describe()`, validation rejects a bad window,
  `last_occurrence` at 07:00 returns *yesterday's* 17:00, and — the one that
  matters — **no slot lost or duplicated across either DST transition**, plus the
  inverse case showing a 01:00–05:00 window *does* hit the fold. That pair is
  what records the immunity as window-scoped rather than inherent.
- **Builder**: subject per kind, `added` only for supplement, `changes` only for
  adjust, dedup-key shape, `requested_by` honoured.
- **Policy**: `add` never selected; `transfer` never selected; a row younger than
  `after` not selected; one exactly at the boundary selected.
- **The `New`-badge case**, explicitly: an action with `action_type='New'` and
  `service='update'` **is** selected. § 4 is the surprising rule, so it needs a
  test that names it.
- **The Friday case**, end to end: received Friday afternoon with `after=1d` is
  not sent Saturday and *is* sent at Monday 08:00.
- **Task wiring**: registration, the schedule, threshold from `ctx.occurrence`
  rather than the wall clock, dry run previews and sends nothing.
- **Import purity**: add `sam.queries.xras_notices` to the parameterised
  transitive test at `tests/unit/test_task_ledger.py:426`.

### Follow-up commit — clear the switch

After a soak watching `skipped` rows, remove `xras_notices` from
`SAM_TASKS_DISABLED`. That soak is the entire reason the switch exists.

## 6. Verification

```bash
source etc/config_env.sh
export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'
pytest tests/unit/test_schedule_predicates.py tests/unit/test_task_xras_notices.py \
       tests/unit/test_xras_notify.py tests/unit/test_task_ledger.py \
       tests/unit/test_notify_import_graph.py -v
pytest
```

`sam-admin tasks --list` should render the row as
`hourly 08:00-17:00 Mon-Fri America/Denver` with a **Next due** inside the
window — a cheap end-to-end check that the schedule is right, and one worth
running on a Friday evening and again on a Sunday.

Against the dev clone, **`--dry-run` genuinely previews** (the #447 follow-up
fix), which is most of the verification story here:

```bash
sam-admin tasks --run xras_notices --force --dry-run
#   -> every action + recipient it would mail; no notification_log row, no mail
sam-admin tasks --run xras_notices --force --dry-run --occurrence <ISO>
```

Browser: post synthetic actions with `scripts/xras/smoke_payloads.py` (a
generator rather than committed JSON, because the interesting payloads name a
projcode that does not exist until the New is processed), leave them unclicked,
run the task, and confirm the card flips to notified and the history modal shows
`task:xras_notices`.

## 7. Phase 2 (later) — automatic activation for New

`add` is excluded because it is **two** writes, not one: `active=True` *and* the
notice. Recorded now so the shape is not re-derived:

- The activation notice says *"is now active"*, and smoke measured one going out
  **64 s before** the activation actually happened. Order matters: activate,
  then notify.
- The decision taken then was **warn in the modal, do not block** — an operator
  may be about to activate. An unattended task has no operator, so it must
  genuinely activate first.
- `xras_activate` already exists as a route (`blueprint.py:1988`) and would need
  the same extraction treatment as the builder.
- Governance gating (`can_edit_project_governance`) has no meaning for a task.
  Decide deliberately whether an automatic activation may bypass what a human
  operator is gated on — do not let it fall out of the implementation.

## 8. Traps

- ❌ **Never key the policy on `action_type`.** § 4.
- ❌ **Never export the new builder from `sam/queries/__init__.py`.** § 5.
- ✅ **Manual and automatic sends mint identical dedup keys**
  (`{kind}:{projcode}:{action_id}:{address}`), so they can never double-mail —
  the ledger suppresses whichever is second. This is the main safety property,
  and the reason no extra locking or claiming is needed around the card.
- ⚠️ `Notifier._prefetch_suppressed` bails when `len(messages) < 2`, so a
  one-recipient batch falls back to `already_sent` one row at a time. Harmless;
  the pre-filter in § 5 step 4 is what keeps the volume down.
- ⚠️ The AST gate (`test_task_ledger.py:417`) walks the **full** AST including
  function bodies, so deferred imports do not dodge it. It bans top-level
  `click / flask / rich / kubernetes`; the transitive test additionally bans
  `webapp` and `cli`.
- ⚠️ **`BusinessHourly`'s DST immunity is a property of the window, not the
  class.** Narrowing `start_hour` below 03:00 re-opens exactly what `Hourly`'s
  docstring warns about.
- ⚠️ `TestingConfig` pins `XRAS_ACTIONS_CAPTURE_ONLY`, read at class-body time —
  a developer with `=0` in `.env` sees ten unrelated capture tests fail.

## 9. Related

- `docs/plans/implemented/SCHEDULED_TASKS.md` — the dispatcher, § 6.2 on
  declaring a task, and the amended note on what `--dry-run` now does.
- `docs/plans/implemented/EXPIRATION_NOTICES.md` — the first `sam.notify`
  consumer on a schedule; the closest working model for this task.
- `docs/xras/incoming/implemented/XRAS_PRE_DEPLOY_SMOKE.md` — where automatic
  sending was deferred, and the Round 2 findings on wording per kind.
- `docs/plans/implemented/NOTIFICATION_FRAMEWORK.md` — `Notifier`, the ledger,
  and the fail-closed posture.
