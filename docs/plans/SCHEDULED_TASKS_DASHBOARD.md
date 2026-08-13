# Scheduled tasks: admin card + run-history page — implementation plan

**Status: BUILT**, 2026-08-13, as further commits on PR #444. See § *As built*
at the foot for where the implementation departed from this plan; everything
else was built as written.

## Context

PR #444 shipped the dispatcher (`src/scheduling/`), the `task_run` ledger
(`src/system_status/models/task_run.py`, Alembic `0006`) and `sam-admin tasks`.
Today the only way to answer *"did the cleanup run last night?"* is a shell.

This implements `docs/plans/SCHEDULED_TASKS_DASHBOARD.md` as further commits on
that PR. Two facts shape everything:

- **`task_run` does not exist** until Alembic `0006` is applied. It is applied
  locally, **not** on staging or production. The card must degrade, not 500 the
  Configuration tab.
- **The chart ships kill-switched** (`SAM_TASKS_DISABLED: "cleanup_status_snapshots"`,
  `helm/values.yaml:449`). The first thing this card shows in production is a
  dispatcher running hourly and deliberately doing nothing. If the card does not
  say so loudly, it looks like a healthy system.

I reviewed the source plan against the tree. It is accurate in the large — the
reuse inventory line numbers, the three traps, the `MIN_LEASE`/`ledger.py:53`
citation, the tile placement and the route table all check out. The corrections
below are what changed.

---

## Decisions taken this session

| Decision | Rationale |
|---|---|
| **Facade query half → new `src/querykit/` peer package** | Chosen over §4b's primary `sam/queries/faceted.py`. Zero new edges for all three eventual clients. `src/scheduling/` is the precedent for a peer that belongs to neither. |
| **Facade route half stays `src/webapp/utils/faceted_log.py`** | Unchanged from §4c. This half genuinely is webapp-scoped. |
| **Playwright smoke reduced to 2 scenarios** | Items 1 (card renders) + 2 (kill-switch warning). Everything else is cheaper and less fragile at the unit tier. |

### Why the query half cannot live under `webapp/`

`src/cli/xras/builders.py:14-17` already imports `summarize_xras_actions` — the
exact function §4a says the facade "must eventually absorb". After the XRAS
retrofit, `sam-admin xras --summary` would import `webapp`.

That is free *today* (`src/webapp/__init__.py` is docstring-only, no Flask) but
it is a landmine: `tests/unit/test_notify_import_graph.py` exists because this
class of coupling already produced a real `ImportError` here (`sam.fmt` → the
top-level `config`, shadowed by `webapp/config.py` when `src/webapp` lands at
`sys.path[0]`).

---

## The `src/querykit/` charter — and the docs debt it inherits

A new top-level package is invisible unless it is written down, and this repo
has already proved that: **`src/scheduling/` — added by commits on this very
branch — appears in neither structure tree.** README.md's tree (~`:555-595`)
goes `system_status/` → `cli/` → `webapp/`, and `CLAUDE.md`'s Code Organization
block skips it too. querykit would be the second omission, so this plan fixes
both.

Three places carry structure, and all three get updated:

| Where | What to add |
|---|---|
| `README.md` tree (~`:564`) | `querykit/` entry, beside `system_status/` |
| `CLAUDE.md` § Code Organization (~`:87`) | one line, pointing at the package README |
| **`src/querykit/README.md`** (new) | the charter below |

The package README is the repo's own convention for a package whose
*architecture* needs explaining — `src/cli/README.md`, `src/webapp/README.md`,
`helm/README.md`, `collectors/`. querykit qualifies precisely because the
rationale (why it is a peer and not `sam/queries/faceted.py`) is the thing that
will otherwise be lost.

**What belongs.** Dialect-neutral, model-agnostic *read* helpers over a
declarative spec: count, page, facet. Imports **only SQLAlchemy** — no SAM
models, no `system_status` models, no Flask.

**What does not.**
- Per-table `_filters()` bodies. Genuinely bespoke SQL (`ilike` across
  different columns, index-friendly `IN` forms); a DSL would cost more than it
  saves. They stay in their own modules.
- Anything importing an ORM model — that inverts the layering the package
  exists to avoid.
- Anything Flask-aware. That is `webapp/utils/faceted_log.py`, deliberately the
  other half.
- Write paths.

**The admission rule, so it does not become a junk drawer:** a helper moves in
on its **third** real caller, not its second. That is the same reasoning that
justifies the package at all ("this would be the third copy", §4a) and the same
discipline `CLAUDE.md` already states for `CrudSpec` — *an entity needing more
than the spec expresses stays bespoke; don't grow the spec for one case.*

**Known growth, in likely order:**
1. The **XRAS retrofit** (§4a, explicitly deferred out of this PR). It is the
   real test of the design: XRAS brings a sort whitelist
   (`XRAS_ACTION_SORT_COLUMNS`), alias canonicalization
   (`canonical_action_type` / `expand_action_types`), a correlated
   `recheck_count` subquery and `_annotate_project_existence`. Some of that is
   facade material; some is bespoke. Deciding which is the retrofit's job, not
   this PR's.
2. The **jobs-explorer facets** (`_jobs_facet_chips.html`) are a third partial
   implementation of the chip pattern — a candidate once XRAS proves the shape.

`querykit` is the name to reach for **only** if a fourth client makes the
generalization real. Two clients is a coincidence; three is the pattern this
package was extracted to hold.

**Gate it.** Add a subprocess import-graph test modelled on
`tests/unit/test_notify_import_graph.py`: importing `querykit` must not pull in
`flask`, `sam`, or `system_status`. Without that, "imports only SQLAlchemy" is a
comment, not a contract — and comments are exactly what drifted here before.

---

## Corrections to the source plan

Fold these into `docs/plans/SCHEDULED_TASKS_DASHBOARD.md` as part of commit 5.

**1. §8's `tests/factories/scheduling.py` is the wrong home — and its testing
premise does not apply.**
`tests/factories/` is SAM-bind-only (ten modules, none touching
`system_status`). Status rows are built by module-private `_make_*` helpers
against the `status_session` fixture (`tests/conftest.py:436`) — see
`tests/unit/test_status_retention.py:44-70` and
`test_user_proj_queues_timeseries.py:19-51` for the shape.

Crucially, `status_session` is a **per-worker SQLite tempfile with per-test
DELETE isolation**, not the SAVEPOINT-bound SAM `session`. Its docstring says
tests may `commit()`. So §8's closing note — assert on `gather_runtime_state`
because committing would escape the SAVEPOINT into the shared xdist database —
**is a SAM-bind constraint that does not bind here**. The tasks card can commit
real rows and assert on rendered HTML, which is strictly stronger.
→ Write `_make_task_run` in the test module; do not add a factory.

**2. Reuse `scheduling.runner.disabled_tasks()` (`runner.py:46-49`).**
The plan says config_inspect reads "the `SAM_TASKS_DISABLED` list" without
naming the existing accessor. `sam-admin tasks --list` already calls it
(`cli/tasks/commands.py:49`). Re-parsing `os.environ` would be a second
mechanism — the same discipline the plan already applies to `MIN_LEASE`.

**3. Reuse `TASK_STATES` / `TASK_TRIGGERS` (`task_run.py:39-42`).**
Both vocabularies already exist as module constants. Use them for facet
zero-fill rather than re-declaring. `CARD_STATES` remains a legitimately
shorter, separate tuple — exactly mirroring `CARD_STATUSES` vs
`NOTIFICATION_STATUSES` in `sam/queries/notifications.py:25`.
Note `'catchup'` is declared but never written by the current runner, so its
chip will read 0 permanently. That is correct under the zero-fill doctrine (an
absent bucket reads as "not measured"), not a bug.

**4. §4d's `pagination()` adoption has two mechanical prerequisites the plan
does not mention.**
- The macro dereferences `sort['sort_by']` unconditionally
  (`pagination.html:33-36`), so notifications must pass a
  `{'sort_by': None, 'sort_dir': 'desc'}` stub. Notifications has no sort
  whitelist at all.
- The macro emits its own `"No matching rows."` at `total == 0`
  (`pagination.html:27-28`), while `notifications_log.html:98-103` already
  renders an in-table `"No delivery attempts match these filters."`. Gate the
  call so an empty result does not show both.
- `has_more` becomes dead once `total` drives the macro; remove it from the
  route.

**5. §4c's `parse_window` must return `days` inside the page dict.**
Notifications' page dict is `{'n', 'per_page', 'days'}` — `page.days` is read by
`notifications_log.html:28` for the "in the last N days" clause. A
`(since, page)` return that drops the third key breaks the headline.

**6. The badge vocabularies are Jinja, and only XRAS is gated.**
`_STATUS_VARIANTS` / `_STATUS_LABELS` / `_STATUS_TOOLTIPS` are module-level
`{%- set -%}` blocks in `badges.html:21-73`, not Python. I confirmed §6c
exactly: `failed` is present (`bg-danger`, right for tasks); `running`,
`succeeded`, `partial`, `skipped` are all absent; and TRAP 3 is real —
`manual` is present at `:29` meaning *"Parked for a human: no handler services
this action type."*

`badges.html:13-14` records that `test_xras_dashboard.py` asserts every
`XRAS_ACTION_STATUSES` member has an entry in all three dicts. There is **no
equivalent gate for `NOTIFICATION_STATUSES`**. Add one for `TASK_STATES`; close
the notifications gap in the same test while there.

**7. §7's "read the ORM row directly" diverges from the `system_status/queries/`
house style,** which returns `List[Dict]` shaped for templates and never ORM
objects (`user_proj_queues.py:101-128`). Keep ORM rows — that is the facade's
shape, inherited from notifications — and record the deviation in the module
docstring.

**8. §11's "6,534 passed" baseline needs re-verification** before it is quoted
as a gate; suite counts drift.

**Not a problem, checked:** all three new routes sit under `/admin/htmx/`, so
`e2e/conftest.py:46-84` excludes them from the browser page sweep — the same as
`notifications` and `rate-limits`. No console/dark-mode sweep entries appear.

---

## Commit series

Appended to `scheduled_tasks`, PR #444.

### Commit 1 — `refactor(queries): extract the faceted-log facade`

**New `src/querykit/faceted.py`** (+ `__init__.py`). Imports only SQLAlchemy —
no SAM models, no Flask. Docstring states the layer-neutral contract.

```python
@dataclass(frozen=True)
class LogSpec:
    model, id_column, order_columns, dimensions, owned_filter, build_filters

def count_rows(session, spec, **filters) -> int
def page_rows(session, spec, *, limit, offset, **filters) -> list
def facet_counts(session, spec, dimension, **filters) -> dict[str, int]
```

`facet_counts` implements **self-exclusion** once — drop the dimension's own
filter via `owned_filter`, honour every other one. That doctrine is currently
written out four times (`notifications.py:148-155`, `xras_actions.py:558-565`,
`allocations/blueprint.py:1304-1315`, `facet_chips.html:14-17`); this becomes
the one explanation.

Per-table `_filters()` bodies **stay in their own modules** — genuinely bespoke
SQL (`ilike` across different columns, index-friendly `IN` forms).

**New `src/webapp/utils/faceted_log.py`** — `parse_window(args, *, default_days,
per_page, max_days=365)` → `(since, page)` with `page = {'n','per_page','days'}`
(correction 5); `build_facet_strip(counts, vocabulary)` → zero-filled in
vocabulary order with out-of-vocabulary values **appended, not reshuffled**.
That append is load-bearing: `allocations/blueprint.py:1329-1332` records that
re-deriving from the declared tuple alone was a bug that dropped OOV statuses
while the headline total still counted them.

**Migrate notifications.** `sam/queries/notifications.py` keeps every public
name and signature as thin wrappers. `notifications_routes.py` adopts
`parse_window` / `build_facet_strip`.

**One declared visual change:** `notifications_log.html:109-131` drops its
hand-rolled Newer/Older pager for the shared `pagination()` macro, honouring
correction 4. Call it out in the commit message.

> **The gate — these three files must pass completely UNEDITED:**
> ```
> tests/unit/test_notifications_queries.py
> tests/unit/test_admin_notifications_card.py
> tests/unit/test_admin_notifications_page.py
> ```
> Checked in advance: none of them assert on pager markup. The single
> `assert b'Showing'` (`test_admin_notifications_page.py:107`) is satisfied by
> the headline strip at `notifications_log.html:25`, which is untouched — and
> the macro emits its own "Showing X–Y of Z" too.

**New** `tests/unit/test_faceted_queries.py` — self-exclusion, zero-fill,
OOV append, page/offset arithmetic. Plus the subprocess import-graph gate
(`querykit` must not import flask / sam / system_status).

**Docs, in this commit — the package is born here, so no commit ever leaves the
tree stale:** `src/querykit/README.md` (the charter above), the README.md tree
entry (~`:564`), the `CLAUDE.md` § Code Organization line (~`:87`).

Add `src/querykit` to `[tool.coverage.run] source` (`pyproject.toml:110`).

### Commit 2 — `feat(status): read-side queries over task_run`

**New `src/system_status/queries/task_runs.py`**, beside `lookups.py` and
`user_proj_queues.py`, built on `querykit`:

```python
CARD_STATES = ('succeeded', 'partial', 'failed', 'skipped')
DEFAULT_WINDOW_HOURS = 24
SPEC = LogSpec(model=TaskRun, dimensions={'task_name','state','trigger_type'}, ...)

def summarize_task_runs(session, *, since=None, window_hours=..., stale_lease_seconds=...)
def count_stale_running(session, *, stale_lease_seconds=...)   # deliberately NOT windowed
def _filters(*, since, task_names, states, triggers, search)
```

> ⚠️ **TRAP 1 — the clock.** `sam/queries/notifications.py` uses
> `datetime.now()` because `notification_log` is naive-**Mountain**. `task_run`
> is naive-**UTC** (`task_run.py:102`). Every window here uses
> `system_status.timeutil.utcnow_naive()` (`timeutil.py:17-19`). Copying the
> import verbatim shifts every count by 6–7 hours — the same bug
> `SCHEDULED_TASKS.md` §3.1 found in the old cleanup script.

Default the lease from `scheduling.ledger.MIN_LEASE` (`ledger.py:53`) so the
card and the reclaim rule cannot disagree about "stale".

**New** `tests/unit/test_task_run_queries.py` — assert the window derives from
`utcnow_naive`, not local time; zero-filled `CARD_STATES`; unwindowed
`count_stale_running`. Module-private `_make_task_run` helper per correction 1.

### Commit 3 — `feat(webapp): Scheduled tasks card on Admin → Configuration`

No new route — the Configuration tab is a single lazy fragment rendered from
`gather_runtime_state()`, so a card is a state key plus an HTML block.

**`webapp/utils/config_inspect.py`** — add a `scheduled_tasks` block beside the
notifications one (`:642-688`) and a key in the returned dict (`:702-713`).
Contents: registered tasks (from `scheduling.registry.TASKS` after importing
`scheduling.tasks` for its side effects), `disabled_tasks()` per correction 2,
last dispatch (`max(claimed_at)`), `window_hours`, the four `CARD_STATES`
counts, `stale_running`.

> ⚠️ **TRAP 2 — the degrade.** Copy the notifications `try/except` verbatim
> **including the `db.session.rollback()` in the handler** (`:663-675`).
> `task_run` does not exist until `0006` is applied, so staging and production
> **will** render this card before the table exists. Without the fallback the
> whole tab 500s; without the rollback, any later `db.session` use in the same
> request raises `PendingRollbackError` instead of its own error. Every key the
> template reads outside the `unavailable` short-circuit must be present in the
> fallback — a real bug the notifications block hit and fixed (its
> `'window_hours': None` comment at `:685-686`).

**Tile** — new `<div class="col-12 col-xl-6">` in
`templates/dashboards/admin/fragments/configuration_card.html`, between the
Notifications tile (ends `:341`) and Rate limiting (`:343`). Plain HTML in a
`row g-3`; no registry to update.

`Details »` is a plain `<a href>`, **not** `SYSTEM_ADMIN`-gated — the page it
targets is `VIEW_SYSTEM_CONFIG`. `rate_limits` is the precedent, with the
reasoning already in a comment at `configuration_card.html:348-350`.

**The kill switch gets the `redirect_to` treatment** — a yellow `alert-warning`,
because a box quietly swallowing its work is the identical hazard and it ships
non-empty. A second `alert-warning` covers non-zero `stale_running`.

**Badges** — add `running` / `succeeded` / `partial` / `skipped` to the three
dicts in `badges.html:21-73`, matching the colours `cli/tasks/display.py:14-29`
already chose (cyan / green / yellow / red / dim) so terminal and web teach one
vocabulary.

> ⚠️ **TRAP 3 — the collision.** Render `trigger_type` as a **plain**
> `<span class="badge bg-secondary">`, the way notifications renders `kind` —
> *not* through `status_badge`. `manual` is already in `_STATUS_VARIANTS:29`
> with an XRAS meaning and its own tooltip, and would silently mislabel a
> manually-triggered run.

**New** `tests/unit/test_admin_scheduled_tasks_card.py` — modelled on
`test_admin_notifications_card.py`. Tile renders; kill-switch warning when
`SAM_TASKS_DISABLED` is set; stale alert; a `TestUnavailableTable` class
asserting the tab still 200s when `summarize_task_runs` raises (TRAP 2), via
the monkeypatch-the-query-module pattern at
`test_admin_notifications_card.py:141-151`. Per correction 1, assert on real
committed rows and rendered HTML.

Extend the `badges.html` vocabulary gate for `TASK_STATES` (correction 6).

### Commit 4 — `feat(webapp): scheduled-task run history page`

**New `src/webapp/dashboards/admin/tasks_routes.py`**, mirroring
`notifications_routes.py` (159 lines) but via the facade. Register by appending
to the import list at `blueprint.py:1091`.

| Endpoint | Rule | Gate |
|---|---|---|
| `scheduled_tasks` | `/admin/htmx/tasks` | `VIEW_SYSTEM_CONFIG` |
| `scheduled_tasks_log` | `/admin/htmx/tasks/log` | `VIEW_SYSTEM_CONFIG` |
| `task_run_detail` | `/admin/htmx/tasks/<int:task_run_id>` | **`SYSTEM_ADMIN`** |

Templates: `dashboards/admin/scheduled_tasks.html` (shell),
`fragments/scheduled_tasks_log.html`, `fragments/task_run_detail_modal.html`.
Reuse the `audit_details_modal.html` shell by `{% include %}`, exactly as
`notifications.html:142` does.

Columns: Claimed / Task / Occurrence / State / Trigger / Try / Took / Runner /
Actions. The Actions button renders only
`{% if has_permission(Permission.SYSTEM_ADMIN) %}`, so the lower tier is never
offered a control that 403s.

- `detail` is a **JSON string** in the column. `TaskLedger._as_dict` decodes it,
  but these routes read the ORM row directly, so the modal must `json.loads`
  tolerantly (fall back to the raw string — still evidence) and render in a
  `<pre>`. Mirror the tolerant decode at `ledger.py:317-340`.
- Miss → `htmx_modal_not_found('Task run')`, which returns **200** not 404 on
  purpose: htmx will not swap a 4xx.
- **Read-only. No "run now" button.** The Configuration tab is a read surface
  and `sam-admin tasks --run` exists.

**New** `tests/unit/test_admin_scheduled_tasks_page.py` — modelled on
`test_admin_notifications_page.py`, whose `config_only_client` fixture
(`:18-35`) strips `SYSTEM_ADMIN` while keeping `VIEW_SYSTEM_CONFIG` by patching
`get_user_permissions`. **That fixture is the point**: page and log 200 at the
lower tier, detail modal 403.

Regenerate the route-map snapshot **in this same commit**:

```bash
ROUTE_MAP_REGEN=1 pytest tests/unit/test_route_map_parity.py
```

### Commit 5 — `docs: record the Tier 1 admin card`

Mark §9 Tier 1 built in `SCHEDULED_TASKS.md`, add the gating deviation and the
`src/querykit/` choice to its §15, fold corrections 1–8 into
`SCHEDULED_TASKS_DASHBOARD.md`, update the PR body.

**Also settle the pre-existing debt this branch created:** add `src/scheduling/`
to both structure trees (README.md ~`:564`, `CLAUDE.md` § Code Organization).
It shipped in P0–P4 and is documented in neither. Cheap to fix while both files
are already open, and leaving it undocumented is what made the querykit question
worth asking in the first place.

---

## Playwright smoke

**New `e2e/test_scheduled_tasks_card.py` — two scenarios:**

1. **The card renders** on Admin → Configuration, and `Details »` navigates.
2. **The kill-switch warning is visible** — the single most important pixel,
   because production ships with `cleanup_status_snapshots` disabled and the
   card must not look healthy while nothing is being deleted.

House style: import `visit` from `conftest` (flat, no package), assert with
`page.locator(...).count()` plus a message naming what regressed.

```bash
docker compose up webdev --watch                  # http://localhost:5050
make e2e SAM_E2E_BASE_URL=http://localhost:5050
```

One-time: `pip install -e ".[e2e]" && playwright install chromium`.

⚠️ `webapp` (:7050) and `webdev` (:5050) share one Redis db — flush before any
A/B. `--watch` syncs on *change*, so touch a file if the watcher started late.

---

## Verification

```bash
source etc/config_env.sh
export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'

# Commit 1's gate — must pass with NO edits to these files
pytest tests/unit/test_notifications_queries.py \
       tests/unit/test_admin_notifications_card.py \
       tests/unit/test_admin_notifications_page.py -v

pytest tests/unit/test_faceted_queries.py tests/unit/test_task_run_queries.py \
       tests/unit/test_admin_scheduled_tasks_card.py \
       tests/unit/test_admin_scheduled_tasks_page.py \
       tests/unit/test_route_map_parity.py -v

# Guard the new peer package's import graph, modelled on
# tests/unit/test_notify_import_graph.py (clean subprocess):
#   importing querykit must not pull in flask or sam models

pytest              # full suite — re-establish the baseline, do not quote 6,534 unchecked
make helm-test      # unchanged by this work, but cheap
```

Confirm the local `system_status` DB is actually at `0006` before expecting the
page to draw — the stamped revision is **not** discoverable from files:

```bash
make migrate-status-current
sam-admin tasks --run cleanup_status_snapshots --force
sam-admin tasks --history
```

---

## Not in scope

The XRAS retrofit onto the facade; `/api/v1/health/tasks`; the `tasks_watchdog`
task; and P5 (clearing the production kill switch). The first three are named in
`SCHEDULED_TASKS.md` §9; the last is Ben's.

---

## As built

Six commits, not five — the order changed and one was added.

### The commit order swapped

The plan had the card (3) before the page (4). Built the other way round: the
tile's `Details »` is a plain `url_for('admin_dashboard.scheduled_tasks')`, so
a card commit landing first would raise `BuildError` and 500 the whole
Configuration tab. The dependency runs card → page, so the page ships first.

A sixth commit registers the fragment's modal-shell dependency —
`test_modal_shell_contract.py`'s ratchet fired on `scheduled_tasks_log.html`
reaching for `auditDetailsModal`, exactly as designed. The contract holds: the
fragment is only ever loaded by `scheduled_tasks.html`, which includes the
shell itself.

### Deviations from the plan text

| Deviation | Why |
|---|---|
| **`summarize_task_runs` also returns `last_dispatch_age`** | `fmt_ago` takes a `timedelta`, not a `datetime`. Computing it in the query module keeps the subtraction against `utcnow_naive()`; a template differencing this naive-UTC column against the local clock would report an hourly dispatcher as ~7 hours stale. |
| **`last_dispatch` is unwindowed**, like `count_stale_running` | Not called out in the plan. A windowed "when did the dispatcher last wake" reads as *never* once the answer falls off the edge — the same failure mode windowing `stale_running` would cause. |
| **`observed_task_names` reads the table, not `scheduling.registry.TASKS`** | A task deleted from the registry still has history worth filtering to, and the registry is not what the rows say. |
| **Runner column is `nowrap`** | Found by the browser smoke, not by any test. The Task column's `width:99%` squeezed Runner to its minimum and a pod name broke at all three hyphens, tripling every row's height. There is no `audit-table` CSS rule anywhere — the class is purely semantic — so this was default table behaviour, not a style regression. |
| **The badge vocabulary gate was generalized** rather than duplicated | `test_xras_dashboard.py::TestStatusVocabularyIsRenderable` now parametrizes over `XRAS_ACTION_STATUSES`, `NOTIFICATION_STATUSES` and `TASK_STATES`. The notifications half was a pre-existing gap: `badges.html` is one flat namespace holding three domains and only XRAS was asserted against it. |

### Corrections to the plan's own corrections

- **Correction 8 was unnecessary.** The plan doubted the "6,534 passed"
  baseline; it was essentially right. The suite now finishes at **6,629 passed
  / 42 skipped / 1 xfailed**, and the ~95 new tests here account for the
  difference. New baseline: **6,629**.
- **`parse_window`'s `days` key** (correction 5) was real and load-bearing —
  `notifications_log.html` reads `page.days` for its headline.
- **The `pagination()` prerequisites** (correction 4) were both real: the macro
  dereferences `sort['sort_by']` unconditionally, and it emits its own
  "No matching rows." at zero, which would have double-rendered against the
  in-table empty state. Both handled; verified in the browser.

### The extraction gate held

The three notifications test files passed **completely unedited** — 64 tests —
which is the proof that migrating them onto `querykit` changed no behaviour.

### Verified in a real browser

Facet self-exclusion (selecting **Failed** leaves the other state chips live
while Trigger correctly collapses to `manual 1` with the rest dimmed at zero),
all five run-state badges distinct, `trigger_type` as a plain badge rather than
the XRAS `manual` status badge, the shared pager reading "Showing 51–62 of 62",
JSON detail pretty-printed, an unparseable traceback shown as-is, and the
migrated notifications log still rendering with a single empty state.
