# Scheduled tasks: the admin card and run-history page

**Status: PROPOSED.** Drafted 2026-08-13. No code written.

This is `SCHEDULED_TASKS.md` § 9 *Tier 1*, split into its own document because it
turned out not to be the ~60-line card that section assumed. It is the third
instance of a shape the codebase already has twice, so it starts by extracting
the shared mechanism.

**Ships as further commits on PR #444**, which already carries P0–P4.

---

## 1. Where things stand

PR #444 shipped the dispatcher (`src/scheduling/`), the `task_run` ledger
(`src/system_status/models/task_run.py`, Alembic `0006`) and `sam-admin tasks`.
Today the only way to answer *"did the cleanup run last night?"* is a shell.

**The webapp has zero task awareness.** Verified: nothing under `src/webapp/`
imports `scheduling`, references `task_run`, or renders anything task-shaped. So
nothing here is a modification of existing behaviour except the deliberate
notifications migration in § 4.

Two facts that shape everything below:

- The `task_run` table **does not exist** until Alembic `0006` is applied. It is
  applied locally; it is **not** applied to staging or production yet. The card
  must therefore degrade gracefully, not 500 the Configuration tab.
- The chart **ships kill-switched** (`SAM_TASKS_DISABLED: "cleanup_status_snapshots"`
  in `helm/values.yaml`). The first thing this card will show in production is a
  dispatcher that runs hourly and deliberately does nothing. If the card does not
  say so loudly, it will look like a healthy system.

## 2. Decisions already taken

Settled with Ben on 2026-08-13. Do not re-litigate; do record any deviation.

| Decision | Rationale |
|---|---|
| **More commits on PR #444**, not a stacked PR | The card is part of shipping the dispatcher. |
| **Split gating**: page + table `VIEW_SYSTEM_CONFIG`, per-row detail modal `SYSTEM_ADMIN` | § 9 said all-SYSTEM_ADMIN, copying Notifications — but its stated reason, *"every row names a real person's email address"*, does not transfer. Task rows are task names, states, pod names. Only `detail` (tracebacks, which can name hosts and paths) warrants the higher tier. `rate_limits_routes.py` is the same-tier precedent for a `VIEW_SYSTEM_CONFIG` details page. **This is a deviation from § 9; record it in `SCHEDULED_TASKS.md` § 15.** |
| **Full parity** with the Notifications details page | Facet chips with self-exclusion, paginated table, detail modal. |
| **Extract a faceted-log facade now**; migrate notifications *and* tasks onto it | This would be the third copy. XRAS retrofitted later. |

## 3. The reuse inventory

The point of the facade is to *not* write a third copy, so start from what is
already shared. All of this is used as-is:

| Component | Where | Note |
|---|---|---|
| `facet_row(label, values, form_id, field, active, badge)` | `templates/dashboards/fragments/facet_chips.html:42` | Brings the `data-action="set-filter-submit"` contract implemented in `static/js/actions.js`. **No new JavaScript is needed for this feature.** |
| `pagination(page, total, fragment_url, target_id, form_id, sort, ...)` | `templates/dashboards/fragments/pagination.html:21` | See the trap in § 4c. |
| `status_badge(state, ...)` | `templates/dashboards/fragments/badges.html:92` | Extend its vocabulary (§ 6c); do not fork it. |
| `stat()`, `mask_badge()` | `templates/dashboards/admin/fragments/_config_macros.html:11,18` | Already imported at `configuration_card.html:21`. |
| `audit_details_modal.html` | `templates/dashboards/allocations/partials/` | The modal shell, already shared by allocations *and* notifications. |
| `htmx_modal_not_found()` | `webapp/utils/htmx.py` | |
| `gather_runtime_state()` | `webapp/utils/config_inspect.py` | The Configuration tab's single state assembler. |
| `require_permission`, `Permission`, `has_permission` | `webapp/utils/rbac.py:153,173` | `has_permission` reaches templates via `rbac_context_processor`. |

## 4. The facade (commit 1)

### 4a. Which shape wins

The pattern exists twice and they are **not** the same underneath:

| | XRAS actions | Notifications |
|---|---|---|
| Query module | `sam/queries/xras_actions.py` | `sam/queries/notifications.py` |
| Route | `dashboards/allocations/blueprint.py` (~`:1290-1340`) | `dashboards/admin/notifications_routes.py` |
| Filters | **scalar** (`status=`, `action_type=`) | **list** (`statuses=`, `kinds=`) |
| Facets | `summarize_xras_actions` does double duty as summary *and* facet, called twice with different args omitted | dedicated `facet_notifications(session, dimension, **filters)` |
| Extras | sorting, action-type canonicalization, `_annotate_project_existence` | none |

**Notifications is the winner** — the dedicated facet function with an explicit
dimension→column map is the clearer mechanism, and its single `_filters()`
builder is what stops the table, the count and the facets from drifting apart.
XRAS's extras are real requirements that the facade must eventually absorb, which
is why XRAS is **not** migrated in this PR.

### 4b. `src/sam/queries/faceted.py` (new)

```python
@dataclass(frozen=True)
class LogSpec:
    model:          type
    id_column:      Any                    # PK, for count()
    order_columns:  tuple                  # newest-first
    dimensions:     Mapping[str, Any]      # 'status' -> NotificationLog.status
    owned_filter:   Mapping[str, str]      # 'status' -> 'statuses'
    build_filters:  Callable[..., list]    # the per-table _filters()

def count_rows(session, spec, **filters) -> int
def page_rows(session, spec, *, limit, offset, **filters) -> list
def facet_counts(session, spec, dimension, **filters) -> dict[str, int]
```

`facet_counts` implements **self-exclusion** once — drop the dimension's own
filter via `owned_filter`, honour every other one. Both existing implementations
explain this rule at length in comments; it should be one function with one
explanation. (Scope a dimension by itself and every unselected value drops to
zero the moment one is picked, so the chips stop being switchers and become dead
ends.)

Per-table `_filters()` bodies **stay in their own modules**. They are genuinely
bespoke SQL — `ilike` search across different columns, index-friendly equality
forms — and hiding them behind a DSL would cost more than it saves.

> ⚠️ **The one architectural consequence, flag it at review.**
> `src/sam/` and `src/system_status/` import nothing from each other today
> (verified: no import in either direction). `SCHEDULED_TASKS.md` § 6.1 protects
> the `sam → system_status` direction specifically. Having
> `system_status/queries/task_runs.py` import this module creates the **reverse**
> edge, which is new.
>
> It is defensible only because the module contains **no SAM models and imports
> only SQLAlchemy** — the same layer-neutral contract `src/sam/plugins.py:6`
> already states ("Any layer — CLI, webapp, system_status — can import from
> here"). Say so in the module docstring.
>
> **The alternative, if that edge is unwelcome:** put the facade in a new
> top-level `src/querykit/` peer package (peer of `sam/`, `system_status/`,
> `scheduling/`). Costs a package for ~120 lines; buys zero new edges.
> `src/scheduling/` is the recent precedent for adding a peer. Ask before
> choosing differently from the above.

### 4c. `src/webapp/utils/faceted_log.py` (new)

The two genuinely repeated route-level chores, ~40 lines:

- `parse_window(args, *, default_days, per_page, max_days=365)` → `(since, page)`,
  clamping days to 1..365 and page ≥ 1.
- `build_facet_strip(counts, vocabulary)` → zero-filled rows in vocabulary order,
  with **out-of-vocabulary values appended rather than reshuffling**. Both
  existing implementations get this right and explain why: a strip is something
  an operator scans by position, an absent bucket reads as "not measured" rather
  than "none", and a stray value is a bug worth surfacing rather than hiding.

### 4d. Migrate notifications — and prove the move was pure

`sam/queries/notifications.py` keeps **every public name and signature**,
becoming thin wrappers over the facade. `notifications_routes.py` adopts
`parse_window` / `build_facet_strip`.

> **The gate: these three files must pass completely UNEDITED.**
> ```
> tests/unit/test_notifications_queries.py
> tests/unit/test_admin_notifications_card.py
> tests/unit/test_admin_notifications_page.py
> ```
> If any needs a change, the extraction changed behaviour and needs another look
> before going further.

**One declared visual change.** `notifications_log.html:109-130` hand-rolls a
Newer/Older pager, while `pagination.html:21` already exists and XRAS uses it
(`xras_table.html:1,231`) — it renders windowed numbered links and
"Showing 1–50 of 312". Notifications adopts the shared macro here. This *is* a
visible change to a shipped page: call it out in the commit message rather than
smuggling it in. (Noticing this is also why the tasks page must not be a
copy-paste of `notifications_log.html` — a naive copy would have triplicated the
weaker pager.)

## 5. The query layer for tasks (commit 2)

New `src/system_status/queries/task_runs.py`, beside `lookups.py` and
`user_proj_queues.py`, built on the facade:

```python
CARD_STATES = ('succeeded', 'partial', 'failed', 'skipped')   # card row order
DEFAULT_WINDOW_HOURS = 24
SPEC = LogSpec(model=TaskRun, dimensions={'task_name', 'state', 'trigger_type'}, ...)

def summarize_task_runs(session, *, since=None, window_hours=..., stale_lease_seconds=...)
def count_stale_running(session, *, stale_lease_seconds=...)   # deliberately NOT windowed
def _filters(*, since, task_names, states, triggers, search)
```

> ⚠️ **TRAP 1 — the clock.** `sam/queries/notifications.py` computes windows with
> `datetime.now()` because `notification_log` lives in SAM MySQL and is
> naive-**Mountain**. `task_run` is naive-**UTC**. Every window here must use
> `system_status.timeutil.utcnow_naive()`. Copying the import verbatim shifts
> every count by 6–7 hours — exactly the bug `SCHEDULED_TASKS.md` § 3.1 found in
> the old cleanup script, in exactly the same way.

`count_stale_running` is the analogue of `count_stuck_queued` and is unwindowed
for the same stated reason: a run stuck three days ago matters more than one
stuck an hour ago, and windowing it lets the oldest breakage age quietly off the
card. Default its lease from `scheduling.ledger.MIN_LEASE` (`ledger.py:53`) so
the card and the reclaim rule cannot disagree about what "stale" means — the same
one-mechanism discipline `queued_stale_seconds` keeps.

## 6. The card (commit 3) — no new route

The Configuration tab is a **single** lazy-loaded fragment
(`htmx_configuration_card` → `configuration_card.html`) rendered from
`gather_runtime_state()`. A card is therefore a state key plus an HTML block; it
needs no route of its own.

### 6a. State

`webapp/utils/config_inspect.py` — add a `scheduled_tasks` block beside the
notifications one (`:642-688`) and a key in the returned dict (`:702-713`).

Contents: registered tasks (from `scheduling.registry.TASKS` after importing
`scheduling.tasks` for its side effects), the `SAM_TASKS_DISABLED` list, last
dispatch (`max(claimed_at)`), `window_hours`, the four `CARD_STATES` counts, and
`stale_running`.

> ⚠️ **TRAP 2 — the degrade.** Copy the notifications `try/except` **verbatim,
> including the `db.session.rollback()` in the handler** (`:663-688`). `task_run`
> does not exist until `0006` is applied, so staging and production **will**
> render this card before the table exists. Without the fallback the whole
> Configuration tab 500s; without the rollback, any later `db.session` use in the
> same request raises `PendingRollbackError` instead of its own error. Every key
> the template reads outside the `unavailable` short-circuit must be present in
> the fallback dict — that is a real bug the notifications block hit and fixed
> (see its `'window_hours': None` comment).

### 6b. Tile

A new `<div class="col-12 col-xl-6">` in `configuration_card.html`, between the
Notifications tile (comment opens `:266`, tile ends `:341`) and Rate limiting
(`:343`). That keeps the half-width pairs even and the full-width Audit card
last. Cards are plain HTML in a `row g-3` (`:32`); there is no registry or
ordering constant to update.

`Details »` is a plain `<a href>`, **not** wrapped in a `SYSTEM_ADMIN` check —
the page it targets is `VIEW_SYSTEM_CONFIG` (§ 2).

**The kill switch gets the `redirect_to` treatment.** Notifications renders a
yellow `alert-warning` for `redirect_to` because a staging box quietly swallowing
mail is the failure mode that line exists to prevent. `SAM_TASKS_DISABLED` is the
identical hazard, and it ships non-empty. Same treatment, same reasoning. A
second `alert-warning` covers non-zero `stale_running`.

### 6c. Badges

`status_badge` (`badges.html:92`) knows `failed` (`bg-danger`, correct for tasks
too) but **not** `running`, `succeeded`, `partial` or `skipped` — all four fall
back to `bg-secondary`, rendering a success and a skip identically. Add them to
`_STATUS_VARIANTS` (`:21`), `_STATUS_LABELS` and `_STATUS_TOOLTIPS`, matching the
colours `src/cli/tasks/display.py:16-22` already chose (`running` cyan,
`succeeded` green, `partial` yellow, `failed` red, `skipped` dim) so the terminal
and the web teach one vocabulary.

> ⚠️ **TRAP 3 — the collision.** Render `trigger_type` as a **plain**
> `<span class="badge bg-secondary">`, the way notifications renders `kind` —
> *not* through `status_badge`. The value `manual` already exists in
> `_STATUS_VARIANTS` with an XRAS meaning and its own tooltip, and would silently
> mislabel a manually-triggered run.

## 7. The details page (commit 4)

New `src/webapp/dashboards/admin/tasks_routes.py`, mirroring
`notifications_routes.py` (159 lines) but via the facade. Register by appending
to the import list at `blueprint.py:1091`.

| Endpoint | Rule | Gate |
|---|---|---|
| `scheduled_tasks` | `/admin/htmx/tasks` | `VIEW_SYSTEM_CONFIG` |
| `scheduled_tasks_log` | `/admin/htmx/tasks/log` | `VIEW_SYSTEM_CONFIG` |
| `task_run_detail` | `/admin/htmx/tasks/<int:task_run_id>` | **`SYSTEM_ADMIN`** |

Templates: `dashboards/admin/scheduled_tasks.html` (page shell),
`fragments/scheduled_tasks_log.html`, `fragments/task_run_detail_modal.html`.

Columns: Claimed / Task / Occurrence / State / Trigger / Try / Took / Runner /
Actions. The Actions button renders only
`{% if has_permission(Permission.SYSTEM_ADMIN) %}`, so the lower tier is never
offered a control that 403s — the courtesy the Notifications tile pays with its
`Details »` link.

Two details:

- `detail` is a **JSON string** in the column. `TaskLedger._as_dict` decodes it,
  but these routes read the ORM row directly, so the modal must `json.loads`
  tolerantly (fall back to the raw string — it is still evidence) and render in
  a `<pre>`.
- **The page is read-only. No "run now" button.** § 9 is explicit that the
  Configuration tab is a read surface, and `sam-admin tasks --run` exists.

## 8. Tests and snapshots

| File | Covers |
|---|---|
| `tests/unit/test_faceted_queries.py` | The facade directly: self-exclusion, zero-fill, out-of-vocabulary append, page/offset arithmetic. |
| `tests/unit/test_task_run_queries.py` | **Assert the window derives from `utcnow_naive`, not local time** (TRAP 1); zero-filled `CARD_STATES`; unwindowed `count_stale_running`. |
| `tests/unit/test_admin_scheduled_tasks_card.py` | Model: `test_admin_notifications_card.py`. Tile renders; kill-switch warning when `SAM_TASKS_DISABLED` is set; stale alert; a `TestUnavailableTable` class asserting the tab still 200s when `summarize_task_runs` raises (TRAP 2). |
| `tests/unit/test_admin_scheduled_tasks_page.py` | Model: `test_admin_notifications_page.py`, whose `config_only_client` fixture (`:19-35`) strips `SYSTEM_ADMIN` while keeping `VIEW_SYSTEM_CONFIG`. **That fixture is the point**: page and log 200 at the lower tier, detail modal 403. |
| `tests/factories/scheduling.py` | `make_task_run`, beside `tests/factories/notify.py`. |

Route-map parity pins every dashboard `(endpoint, rule, methods)` triple; three
new endpoints will fail it until regenerated. Regenerate and **commit the diff in
the same commit as the routes**:

```bash
ROUTE_MAP_REGEN=1 pytest tests/unit/test_route_map_parity.py
```

A note the card tests must respect: `test_admin_notifications_card.py:57-79`
asserts on `gather_runtime_state(app, db)` output rather than on HTML whenever
the assertion needs committed rows, because committing escapes the per-test
SAVEPOINT into the shared xdist database. Follow that.

## 9. Playwright smoke

The e2e tier lives in `e2e/` and is deliberately independent of the conda
environment — nothing under it imports `sam`/`webapp`/`system_status`
(see the `e2e:` target in the `Makefile`).

- `base_url` (`e2e/conftest.py:159`) resolves `--base-url` → `SAM_E2E_BASE_URL` →
  `http://localhost:7050`.
- `storage_state` (`:174`) logs in **once per session** through the real stub
  login form as `ADMIN_USERNAME` (`:25`, default `benkirk`, override with
  `SAM_E2E_USER`). It deliberately fills the form rather than clicking a Quick
  Login button — those only exist under `DevelopmentConfig`. Per-test login would
  trip `RATELIMIT_AUTH_LOGIN` ('5 per minute').

Bring the stack up and run against the dev server:

```bash
docker compose up webdev --watch                       # http://localhost:5050
make e2e SAM_E2E_BASE_URL=http://localhost:5050
```

One-time: `pip install -e ".[e2e]" && playwright install chromium`.

Add `e2e/test_scheduled_tasks_card.py`. The states worth driving in a real
browser are the ones that will actually ship:

1. **The card renders** on Admin → Configuration, and `Details »` navigates.
2. **The kill-switch warning** — the single most important pixel here, because
   production ships with `cleanup_status_snapshots` disabled and the card must
   not look healthy while nothing is being deleted.
3. **The empty table** — before any run, the log fragment shows its empty state
   rather than a broken grid.
4. **The `unavailable` degrade** — the Configuration tab still renders when
   `task_run` is absent. Reproduce by pointing `STATUS_DB_NAME` at a database
   without the table.
5. **The 403 boundary** — the detail modal refuses a `VIEW_SYSTEM_CONFIG`-only
   user. (Unit-testable too, and cheaper there; drive it in the browser only if
   the chip/modal interaction is worth seeing.)
6. **Facet chips and pagination actually work** — this is the payoff of reusing
   `facet_row` and `pagination()`, and the one thing unit tests cannot show.

⚠️ Two local-stack gotchas: `webapp` (:7050) and `webdev` (:5050) **share the
same Redis cache db**, so flush it before any A/B comparison; and `--watch`
syncs on *change*, so touch a file if the watcher started late.

## 10. Commit series

Appended to `scheduled_tasks`, PR #444.

1. `refactor(queries): extract the faceted-log facade` — the two new modules,
   notifications migrated, its three test files unedited, the shared
   `pagination()` adoption declared.
2. `feat(status): read-side queries over task_run`
3. `feat(webapp): Scheduled tasks card on Admin → Configuration`
4. `feat(webapp): scheduled-task run history page` — routes, templates,
   route-map snapshot.
5. `docs: record the Tier 1 admin card` — mark § 9 Tier 1 built in
   `SCHEDULED_TASKS.md`, add the gating deviation to its § 15, update the PR body.

## 11. Verification

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

pytest              # full suite — 6,534 passed / 42 skipped / 1 xfailed is the baseline
make helm-test      # unchanged by this work, but cheap
```

The local `system_status` MySQL is at `0006` and holds one real `task_run` row
from the CLI smoke test, so the page has something to draw. To generate more:

```bash
sam-admin tasks --run cleanup_status_snapshots --force
sam-admin tasks --history
```

## 12. Notes and non-goals

- **No caching.** The Configuration cards are a live runtime snapshot and
  deliberately carry no `@cache.cached`; `configuration_card.html:25-30` says so
  on the page. Do not add one.
- **`db.session`, not `TaskLedger`.** `TaskRun.__bind_key__ = "system_status"`
  routes it automatically. `TaskLedger` takes a `session_factory` and closes the
  session per call, so handing it `db.session` would close the scoped session.
  (`TaskLedger`'s read helpers — `latest`, `history`, `stale_running` — remain the
  CLI's path and are not used by the webapp.)
- Task rows carry no PII, but `runner_id` is a pod name and `detail` can hold a
  traceback naming hosts, paths and connection strings. That is the whole reason
  the modal sits a tier above the table.
- **Not in scope**: the XRAS retrofit onto the facade; `/api/v1/health/tasks`;
  the `tasks_watchdog` task; and P5 (clearing the production kill switch). The
  first three are named in `SCHEDULED_TASKS.md` § 9; the last is Ben's.
