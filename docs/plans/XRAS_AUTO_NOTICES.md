# Automatic XRAS notices — implementation plan

## Context

The Allocations → XRAS tab's "Pending Activations & Recent Notifications" card
carries a manual **Notify** button per action. If nobody clicks it, the PI is
never told their allocation was renewed, extended, supplemented or adjusted —
the outcome already happened in SAM, and the only missing step is that anyone
said so.

`docs/plans/XRAS_AUTO_NOTICES.md` is the settled design (2026-08-14, with Ben).
Every `file:line` in it was re-verified against the tree and **all of them are
correct**; the deviations below are additions and one staleness, not
corrections.

This executes it on `xras_auto_notices_plan` as an ordered commit series in one
PR against `staging`, shipping **disabled** — appended to the staged-enable
enumeration #450 landed.

### What #450 changed under the doc

The doc says `SAM_TASKS_DISABLED` is "currently `cleanup_status_snapshots`".
It is now `"deactivate_expired_projects,expiration_notices"`, and the switch
went from a one-off soak mechanism to a **permanent staged-enable enumeration**
(`helm/values.yaml:458-482`). Two consequences:

- It is **fail-open**. `disabled_tasks()` is an exact-match set; a task added to
  `src/scheduling/tasks/` dispatches on the next hourly wake unless its name is
  in that list **in the same change**. This is the single most important line of
  this PR.
- `helm/tests/test-cronjob-render.sh:142-152` now **derives** the expected value
  out of `values.yaml`, so appending a name needs no edit there. The doc's
  instruction to add a per-manifest assertion for the switch is already
  satisfied generically.

---

## Decisions taken (beyond the doc)

| | |
|---|---|
| **Lookback** | `LOOKBACK = timedelta(days=14)`. The doc left `since=` unspecified. Prod has no backlog, so this is a theoretical bound — it exists so the predicate can never reach further back than one outage's worth of recovery. |
| **Summary mail** | **None.** `expiration_notices` mails one per run (52/yr); this runs 50×/week and most runs send nothing. The ledger row and the admin card are the record. |
| **Send cap** | New `SAM_TASKS_XRAS_MAX`, default **50**, own reader. Sized to real traffic, so an inverted `notified` check trips it instead of mailing the lookback window. |
| **Slot minute** | `minute=0`, not the doc's `20`. The CronJob wakes at `:07`, so a `:00` slot is dispatched ~7 min later; `:20` would add ~47 min of latency to every notice. |
| **Determinism** | `until=slot` as well as `since=` — a run reclaimed an hour late must select the cohort its slot would have, not a wider one. |

---

## The work

### Commit 1 — `scheduling: a business-hours hourly schedule`

`src/scheduling/schedules.py`. No base-class change: `_LocalWallSchedule`
(`:122-191`) already asks exactly one question, `_candidates_on(local_date) ->
[(hour, minute)]`, and its search loop (`:158-168`) already takes the latest of
*several* candidates per day. `Daily` returns one pair; this returns ten.

```python
@dataclass(frozen=True)
class BusinessHourly(_LocalWallSchedule):
    minute: int = 0
    start_hour: int = 8
    end_hour: int = 17                      # INCLUSIVE — 17:00 fires
    weekdays: Tuple[int, ...] = (0, 1, 2, 3, 4)
```

- `__post_init__` validation in the house style (`ValueError(f'… got {x}')`):
  hour ranges, `start_hour <= end_hour`, weekday membership, `minute` 0–59.
- `describe()` → `hourly 08:00-17:00 Mon-Fri America/Denver`. That string is what
  `sam-admin tasks --list` and `webapp/utils/config_inspect.py:710` print.
- **Do not redeclare `tz`** — it is `kw_only=True` on the base for the reason at
  `schedules.py:131-134`.
- Export from `src/scheduling/__init__.py` (import block **and** `__all__`).

⚠️ **The docstring must say the DST immunity is a property of the window, not
the class.** `Hourly`'s docstring (`:196-209`) explains it is UTC-only *because*
a local-wall hourly schedule "loses one slot each fall … and risks merging one
each spring". `BusinessHourly` **is** that schedule; it escapes only because US
transitions happen at 02:00 local, which 08:00–17:00 never contains. Narrowing
`start_hour` below 03:00 walks straight into it.

**Misfire needs no special handling** — verified against `runner.py:135-152`:
the already-settled check runs *before* the lateness check, so a 17:00 slot that
ran reads `already_claimed` at 23:07, never `misfire`.

Tests (same commit): add `BusinessHourly()` to `ALL_SCHEDULES`
(`tests/unit/test_schedule_predicates.py:78-89`, which drives 7 parametrized
contract tests), plus a `TestBusinessHourly` class — the ten slots, the Mon–Fri
skip, `describe()`, validation rejects a bad window, `last_occurrence` at 07:00
returns *yesterday's* 17:00 — and **the DST pair**: no slot lost or duplicated
across `2026-11-01` (fall back) or `2027-03-14` (spring forward), plus the
inverse case showing a `start_hour=1, end_hour=5` window *does* hit the fold.
Reuse `sweep()` / `assert_evenly_spaced()` from that module (`:31-75`).

### Commit 2 — `sam: extract the XRAS message builder out of the blueprint`

New **`src/sam/queries/xras_notices.py`**, mirroring
`src/sam/queries/expiration_notices.py` — including its module docstring's
"two consumers must not disagree about the dedup key" framing and its
`⚠️ Not exported from sam/queries/__init__.py` warning.

Move out of `src/webapp/dashboards/allocations/blueprint.py`:

| from | to |
|---|---|
| `_XRAS_KIND_SUBJECTS` (`:1665`) | `XRAS_KIND_SUBJECTS` |
| `_load_xras_action` (`:1676`) | `load_xras_action(session, action_id)` |
| `_action_increments` (`:1683`) | `action_increments(session, action, *, signed=False)` |
| `_xras_messages` (`:1748`) | `build_xras_messages(session, project, people, *, action=None, requested_by: str)` |

Only two kinds of coupling, both with a worked precedent:

- `db.session` → the `session` parameter. **Exactly three sites**: `:1680`,
  `:1721`, `:1764`.
- `current_user.username` → `requested_by=`, a **required keyword-only** arg
  with no default, exactly as `build_expiration_messages`
  (`expiration_notices.py:121-127`) threads it.

Rewire `xras_notify_form` (`:1817`) and `xras_notify` (`:1897`) to call it with
`db.session` and `requested_by=current_user.username`, so the route and the task
cannot drift on the dedup key.

❌ **Do NOT add it to `sam/queries/__init__.py`.** That file imports submodules
eagerly, and this one imports `sam.notify` at module top level; listing it puts
`sam.notify.base` into the graph of every `from sam.queries import …`.
⚠️ The trap: `xras_activation.py` **is** exported (`__init__.py:124-135`) —
safely, because it does not import `sam.notify`. The two look alike.

**Proof the move was pure:** `tests/unit/test_xras_notify.py` (413 lines,
exercises the builder through the routes) and `tests/unit/test_xras_dashboard.py`
must pass **unedited**. No route, URL or route-map entry changes.

### Commit 3 — `scheduling: hoist the mail guards shared by notice tasks`

`EmailCapExceeded` and `NotificationsDisabled`
(`tasks/expiration_notices.py:73-100`) are generic and safety-critical — the
`task_detail` merge in `runner._execute` (`:255-257`) and the fail-closed
`NOTIFY_ENABLED` check both depend on them. Move to
`src/scheduling/tasks/mail_guards.py`, re-export from `expiration_notices` so
callers and tests are untouched. `tests/unit/test_task_expiration_notices.py`
passing unedited is the proof.

### Commit 4 — `scheduling: hourly XRAS auto-notices, shipped kill-switched`

**`src/scheduling/tasks/xras_notices.py`** plus the import line **and** `__all__`
entry in `scheduling/tasks/__init__.py` — the whole registration surface, since
`TASKS` is populated purely by import side effect.

```python
SCHEDULE = BusinessHourly(minute=0, tz='America/Denver')
LOOKBACK = timedelta(days=14)
DEFAULT_XRAS_MAX = 50

@dataclass(frozen=True)
class AutoNotice:
    service: str
    after: timedelta

#: Fail-closed: a service absent here is NEVER auto-sent. `add` is absent on
#: purpose (Phase 2); `transfer` has no notification kind at all.
AUTO_NOTICES = (
    AutoNotice('update',     after=timedelta(days=1)),
    AutoNotice('extend',     after=timedelta(days=1)),
    AutoNotice('supplement', after=timedelta(days=1)),
    AutoNotice('adjust',     after=timedelta(days=1)),
)
```

Two env readers in the `cleanup_status.retention_days()` shape (`:36-51`) —
injectable `env` param, blank/unparseable/out-of-range → default, **read per
run** so a `values.yaml` change lands on the next dispatch, not the next pod
restart: `notify_after()` from `SAM_XRAS_NOTIFY_AFTER_HOURS` (overrides every
row's `after`) and `xras_email_max()` from `SAM_TASKS_XRAS_MAX`.

Decorator: `needs=('sam',)`, `misfire_grace` left at the 6 h default (a missed
slot costs nothing — the window is rolling, so the next slot covers it), and
`expected_runtime=timedelta(minutes=20)` so `lease_for()` (3600 s) **exceeds**
`activeDeadlineSeconds` (3000 s). Carry the same drift assertion
`test_task_expiration_notices.py:146-173` uses.

Body, following `tasks/expiration_notices.py`:

1. **All `sam` imports deferred inside the function** — `scheduling/` is imported
   by the CLI's `--list` path, which must not pay for jinja2 and the ORM.
2. `slot = to_local_naive(ctx.occurrence, ZoneInfo(SCHEDULE.tz))`. **No midnight
   truncation** (cf. `deactivate_expired.py:94-97`): this is a rolling threshold,
   not a band. `received_time` is naive-**Mountain** from the app clock — never a
   DB default, and `sam/integration/xras.py:111-116` is emphatic about why.
3. `get_xras_activity(session, since=slot - LOOKBACK, until=slot)` — default
   `statuses=('processed',)`.
4. Select rows where `service` is in the policy **and** `notifiable` **and**
   `not notified` **and** `not dismissed` **and**
   `received_time <= slot - after[service]`.
5. `get_xras_pending_recipients(session, project_ids)` once for the whole cohort;
   `build_xras_messages(..., requested_by='task:xras_notices')` per action.
6. Pre-filter with `ledger.already_sent_many()` **before** building, as
   `_drop_already_notified` does — left to `Notifier`, every quiet hour writes
   `suppressed` rows into the table the admin card reads.
7. `Notifier` with a ledger on its **own** session (`_new_sam_session`), never
   `ctx.sam_session`: mail cannot be un-sent by the rollback `close_sessions`
   performs on failure.
8. Guards **before any transport**: raise `NotificationsDisabled` if
   `NOTIFY_ENABLED` is false; raise `EmailCapExceeded` past `xras_email_max()`.
9. **`ctx.dry_run` must branch the send** — `notifier.preview()`, which writes no
   ledger row. A rollback undoes rows, not mail; this is the case
   `TaskContext.dry_run`'s note (`registry.py:126-137`) warns about. The DB
   writes need no branch.
10. `XrasActivationEvent.create(session, …, event_type='notified',
    created_by='task:xras_notices', notified_to=…, xras_action_log_id=…)` on
    `ctx.sam_session` — **only for actions where something was actually
    delivered**, mirroring the route's send-first-record-second rule
    (`blueprint.py:1952-1975`). Not for the card's badge (`row['notified']`
    derives from `notification_log` via the parsed dedup key and works without
    it) but so the history modal does not show a notice from nowhere.
    `created_by` is `VARCHAR(35)`; the sentinel is 17 chars.
11. `detail` always carries the window bounds, the per-service counts,
    selected / suppressed / audience / sent / failed, **and the age of the
    oldest thing it sent** — the effective delay is longer than the nominal one
    (Fri 16:30 + 1 d sends Mon 08:00, ≈2.6 d) and must never read as a stuck
    queue. "0 sent, succeeded" is the normal hourly result and must be
    distinguishable from a query that stopped matching.
12. `partial_failures=len(failed)`.

**Chart, same commit:**

- `helm/values.yaml`: append `xras_notices` to `SAM_TASKS_DISABLED` (`:482`) and
  add it to the Live/Disabled roster comment (`:462-464`); add
  `SAM_TASKS_XRAS_MAX: "50"` to `tasks.env`.
- `helm/tests/test-cronjob-render.sh`: add a `cron_out` (`-s
  templates/cronjob-tasks.yaml`) assertion for `SAM_TASKS_XRAS_MAX`. The switch
  assertions need no edit — they derive the value.
- `docs/README-k8s.md`: update the per-environment table (`:250`) and the
  kill-switch prose (`:325-360`), both of which enumerate the disabled tasks by
  name.

### ⚠️ The surprising rule, which gets its own named test

**A card row badged `New` can auto-send.** The badge shows `action_type`; the
policy keys on `service`, and `dispatch.select_service` (`:201-204`) routes a
`New` whose projcode **already exists** to `update`, which is in the auto set.
That is correct — it is a renewal in all but name — but an operator seeing "New"
auto-send will reasonably conclude the policy leaked.

This is also why the policy **must** key on `service`: `action_type` is nullable
(`xras.py:123`), has aliases (`Adjust` → `Adjustment`), is deliberately
unconstrained, includes `'Date Adjustment'` which is not serviced at all, and
does not imply a new project.

### Commit 5 — tests

Model: `tests/unit/test_task_expiration_notices.py` (692 lines). Reuse its
fixture shape verbatim — `transport` (`NullTransport`), a `ledger` whose factory
yields the test session with `commit` rebound to `flush`, a `wire` fixture
monkeypatching **`sam.notify.Notifier`**, and a `ctx` factory building a real
`TaskContext` with a derived `occurrence_key`. Pin the occurrence in **2033**,
for the reason that file documents: a present-day occurrence drowns the fixtures
in real snapshot rows. Build rows with `tests/factories/xras.py`
(`make_xras_action`, `make_xras_activation_event`) plus `make_project` /
`make_user`; `test_xras_action_queries.py:104-128` has the `NotificationLog`
helper for the already-notified cases.

- **Builder** (`tests/unit/test_xras_notices_builder.py`): subject per kind,
  `added` only for supplement, `changes` only (and signed) for adjust, dedup-key
  shape, `requested_by` honored.
- **Policy**: `add` never selected; `transfer` never selected; a row younger than
  `after` not selected; one exactly at the boundary selected; a dismissed row not
  selected; a row outside `LOOKBACK` not selected.
- **The `New`-badge case, named**: `action_type='New'`, `service='update'` **is**
  selected.
- **The Friday case, end to end**: received Friday afternoon with `after=1d` is
  not sent Saturday and *is* sent at Monday 08:00.
- **Task wiring**: registration, schedule identity, `needs`, the lease/deadline
  drift assertion, threshold from `ctx.occurrence` rather than the wall clock,
  dry run previews and sends nothing and writes no ledger row.
- **Guards**: cap trips before any transport; `NOTIFY_ENABLED` false raises.
- **Event**: `created_by == 'task:xras_notices'`, written only when something was
  delivered.
- **Import purity**: add `sam.queries.xras_notices` **and**
  `sam.queries.xras_activation` to the parametrized transitive test at
  `tests/unit/test_task_ledger.py:426`. (The per-file AST gate at `:417` walks
  `rglob('*.py')` and picks the new task up automatically.)

### Follow-up commit, **not in this PR**

Remove `xras_notices` from `SAM_TASKS_DISABLED` after a soak watching `skipped`
rows. That soak is the entire reason the switch exists.

---

## Deviations from `docs/plans/XRAS_AUTO_NOTICES.md`

Recorded here rather than only in commit messages; fold them into the doc when
the branch lands.

1. **§5 helm instruction is stale** — the switch value and its per-manifest
   assertion changed shape in #450 (above).
2. **`docs/README-k8s.md` is a third consumer** the doc does not mention; it
   enumerates disabled tasks by name in two places.
3. **`since=`/`until=` specified** — 14-day lookback, upper bound at the slot.
4. **No summary mail** (the doc is silent; the model task sends one per run).
5. **Own cap** `SAM_TASKS_XRAS_MAX=50` rather than the shared 2500.
6. **`minute=0`, not 20** — the `:07` dispatcher makes `:20` cost ~47 min.
7. **Guards hoisted** to `tasks/mail_guards.py` (commit 3) instead of being
   duplicated or cross-imported between task modules.
8. **`expected_runtime` sized for the lease**, with the drift assertion — the doc
   does not name a value.
9. **Partial-notification edge case, decided:** `get_xras_activity`'s `notified`
   is true if **any** recipient of that action was reached, so an action where
   the lead got mail and the admin did not is treated as notified and stays
   manual. Conservative direction — never double-mail — and it matches what the
   card shows. Worth a comment in the selection predicate.
10. **Not in scope, noted:** `xras_notify_form` calls `ledger.already_sent` once
    per recipient (`blueprint.py:1867-1871`) where `already_sent_many` exists.
    Harmless at modal scale; leave it.

---

## Verification

```bash
source etc/config_env.sh
export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'

# Commit 2's purity proof — these must pass UNEDITED
pytest tests/unit/test_xras_notify.py tests/unit/test_xras_dashboard.py -v
# Commit 3's purity proof
pytest tests/unit/test_task_expiration_notices.py -v

pytest tests/unit/test_schedule_predicates.py tests/unit/test_task_xras_notices.py \
       tests/unit/test_xras_notices_builder.py tests/unit/test_task_ledger.py \
       tests/unit/test_notify_import_graph.py tests/unit/test_route_map_parity.py -v
pytest
bash helm/tests/test-cronjob-render.sh
```

**The schedule, end to end.** `sam-admin tasks --list` should render the row as
`hourly 08:00-17:00 Mon-Fri America/Denver` with a **Next due** inside the
window — worth running once on a Friday evening and again on a Sunday, when the
answer should be Monday 08:00.

**The task, against the dev clone.** `--dry-run` genuinely previews since the
#447 follow-up fix, which is most of the story here:

```bash
sam-admin tasks --run xras_notices --force --dry-run
#   -> every action + recipient it would mail; no notification_log row, no mail
sam-admin tasks --run xras_notices --force --dry-run --occurrence 2026-08-14T09:00
```

Replay is honored **only** under `--force`, where the key is `M`-prefixed and
cannot claim a real slot — so `--occurrence` is how you ask "what would the
Friday-evening slot have sent?" without waiting.

**Browser.** Post synthetic actions with `scripts/xras/smoke_payloads.py`, leave
them unclicked, run the task, and confirm the card flips to notified and the
history modal shows `task:xras_notices`.

**Chart, before the PR:** `helm template … -s templates/cronjob-tasks.yaml`
must show `xras_notices` inside `SAM_TASKS_DISABLED` and carry
`SAM_TASKS_XRAS_MAX`. A whole-render grep passes on the Deployment's copy and
proves nothing.

## Traps

- ❌ **Never key the policy on `action_type`.**
- ❌ **Never export the new builder from `sam/queries/__init__.py`.**
- ✅ Manual and automatic sends mint **identical** dedup keys
  (`{kind}:{projcode}:{action_id}:{address}`), so they can never double-mail —
  the ledger suppresses whichever is second. This is the main safety property
  and the reason no locking or claiming is needed around the card.
- ⚠️ `TestingConfig` pins `XRAS_ACTIONS_CAPTURE_ONLY` at class-body time — a
  developer with `=0` in `.env` sees ten unrelated capture tests fail.
- ⚠️ `tests/unit/test_admin_scheduled_tasks_card.py:6` still names the pre-#450
  switch value in its docstring. Fix in passing.
- ⚠️ Skip-CI tokens: do not write them in the PR title or body — GitHub builds
  the squash message from both.

## Phase 2 (later) — automatic activation for `add`

Recorded in the design doc § 7 and unchanged: `add` is excluded because it is
**two** writes (`active=True` *and* the notice, in that order — smoke measured a
notice going out 64 s before the activation). It also needs a deliberate
decision about whether an unattended task may bypass
`can_edit_project_governance`, which a human operator is gated on.
