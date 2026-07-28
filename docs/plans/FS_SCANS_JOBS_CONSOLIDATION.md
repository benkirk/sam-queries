# fs-scans ↔ job-history consolidation

**Status (2026-07-28): IN PROGRESS on `fs_scans_job_hist_consolidation`.**
Single PR vs `staging`, ordered commit series — see [Order](#order) for the
per-commit gate. Update this line as tracks land.

The job-history navigator was deliberately modeled on the fs-scans navigator
and shipped without touching fs-scans (`JOB_HISTORY_DASHBOARD.md` →
`JOB_HISTORY_DRILLDOWN.md` → `JOBS_BY_PROJECT.md` → `JOB_HISTORY_UX_ROUND3.md`
→ `JOBS_EXPLORER_CHARTS.md`). Both are now mature and structurally isomorphic:

| | `webapp/disk_scans/` | `webapp/jobs/` |
|---|---|---|
| plugin loader | `session.py` 322 L | `session.py` 232 L |
| TTL cache | `cache.py` 253 L | `cache.py` 235 L |
| query layer | `service.py` 682 L | `service.py` 721 L |
| blueprint | `routes.py` 944 L, 14 routes | `routes.py` 1857 L, 23 routes |
| scoping | `scope.py` 102 L | (inline in routes/service) |
| modes | project / resource / user | project / machine / user |

The duplication that actually costs maintenance is *plumbing* and *mode
fan-out*, not rendering. The tempting move — one shared "Navigator framework"
— is the trap; [Non-goals](#non-goals) says why.

Target: **~1,200–1,500 lines** of `src/` removed, plus a structural change
that makes "add a tab" a one-line table entry instead of a 3× copy. Test
volume stays roughly flat (edits, not deletions).

---

## What's actually duplicated

Four classes with very different economics.

### Class 1 — verbatim cross-feature plumbing · HIGH value, LOW risk

- `disk_scans/cache.py` ≡ `jobs/cache.py` (~85% identical), and
  `sam/queries/usage_cache.py` is a third partial copy of the same
  lazy-adapter + get/compute/store skeleton. Three implementations of one
  concept.
- `disk_scans/session.py` ≡ `jobs/session.py`: `_apply_connection_settings`
  (~40 L) and `_safe_url` (~16 L) are byte-identical, each carrying a comment
  pointing at the other; the three `app.extensions` accessors differ only by
  the key string.
- `_jobs_macros.html` ≡ `_disk_scans_macros.html` — `*_disabled` / `*_error` /
  `*_empty` banners, same markup, different nouns.
- `svg-chart-links.js` — six near-identical `prefix → row attribute` branches
  (3 disk, 3 jobs).
- `_scope_project()` is defined identically in both `routes.py` files.

### Class 2 — intra-feature mode fan-out · HIGH value, MEDIUM risk

Each feature fans its 3 modes out at three layers:

- **service**: `scan_x` / `scan_x_resource` (8 fns); `search_jobs` /
  `_machine` / `_user` + the three `count_*` (6 fns) differing *only* in the
  pinning rule.
- **routes**: `_common_ctx` / `_resource_ctx` / `_user_ctx`;
  `_project_histogram` / `_machine_histogram` / `_user_histogram` (three ~18 L
  bodies differing by gate, `target_id` default, scope kwarg); 37 route bodies
  that are mostly `build ctx → url_for → closure → call shared renderer`.
- **jobs also**: four cached-aggregation wrappers repeating one
  `kwargs → pins → _compute → opts → cached_jobs_aggregation` skeleton.

This is where a class hierarchy pays.

### Class 3 — entity-kind near-duplication · MEDIUM value, MEDIUM risk

- `_render_by_user` ≈ `_render_by_project` (~90%); `jobs_by_user.html` ≈
  `jobs_by_project.html` (~95%, differ in ~8 lines).
- `disk_scans_entities.html` owner half ≈ group half (~48 L).
- `my_jobs_card.html` ≈ `status/partials/job_history.html`;
  `my_data_scans.html` ≈ `status/partials/filesystem_scans.html`.
- The hidden round-trip `<form class="d-none">` + metric-pill block appears
  3× in jobs and 3× in disk-scans.

### Class 4 — looks shared, isn't

See [Non-goals](#non-goals).

---

## Track A — shared plumbing

**A1. `sam/caching/buckets.py` — `BucketedTTLCache`**

```python
@dataclass(frozen=True)
class BucketSpec:
    name: str          # Redis prefix + Admin-card label
    ttl_key: str;  ttl_default: int
    size_key: str; size_default: int

class BucketedTTLCache:
    def __init__(self, label: str, buckets: Mapping[str, BucketSpec]) -> None
    def adapter(self, bucket: str) -> Optional[CacheBase]
    def get_or_compute(self, bucket: str, key: tuple, compute: Callable[[], Any]) -> Any
    def purge(self) -> int
    def info(self) -> list[dict]
    @property
    def prefixes(self) -> tuple[str, ...]
    @staticmethod
    def norm(value)
    def reset_for_tests(self, *, disabled: bool = True) -> None
```

Absorbs `_get_config`, `_norm`, `_adapters`/`_init_lock`, `get_cache_adapter`,
`purge_*`, `*_cache_info` from all three modules, reusing the existing
`sam.caching` primitives — no new backend code.

Each site keeps its **public names** and its key-shape logic, which is the part
that genuinely differs:

- `disk_scans/cache.py` keeps `cached_scan()` + `_scan_date_signature()` —
  content-addressed 6-tuple key (scan dates make invalidation automatic; TTL is
  a memory backstop).
- `jobs/cache.py` keeps `cached_jobs_aggregation()` + `bucket_for_window()` —
  plain TTL 3-tuple key (jobs append continuously; there is no freshness
  signature to key on).
- `sam/queries/usage_cache.py` becomes a single-bucket instance.

**A2. Cache registry.** `BucketedTTLCache` instances self-register.
`Caching.adapters()/stats()/clear()` replaces its hand-written `try/except`
import blocks with a registry walk, and `flask_adapter._FOREIGN_PREFIXES`
becomes **derived** rather than hand-maintained — today adding a bucket
silently breaks the flask adapter's Redis introspection until someone
remembers that list. Also fixes the stale `api/v1/admin.py` docstring that
omits `jobs` from the category list.

**A3. `webapp/plugins/base.py` — `PluginExtension`**

```python
class PluginExtension:
    ext_key: str          # app.extensions key
    plugin: Plugin        # sam.plugins.Plugin
    log_label: str
    def init_app(self, app) -> None            # template method
    def _warm(self, app, mod, state) -> None   # abstract: the only per-plugin part
    def is_enabled(self, app=None) -> bool
    def get_module(self, app=None)
    def get_engines(self, app=None) -> dict
    @staticmethod
    def apply_connection_settings(engine, app_name, *, statement_timeout_ms=0)
    @staticmethod
    def safe_url(engine) -> str
```

`init_fs_scans` / `init_job_history` and the module-level accessors stay as
thin wrappers — `run.py`, `utils/nav.py`, `utils/config_inspect.py` and ~10
tests import them by name. fs-scans keeps its per-database discovery +
`ThreadPoolExecutor` warm pool inside its `_warm()`; jobs keeps its per-machine
loop. Secondary payoff: the Admin → Configuration DB card gets one
introspection shape instead of two bespoke row builders, and the deferred
`statement_timeout` hardening work lands in one place.

**A4. `templates/dashboards/fragments/plugin_state.html`** —
`plugin_disabled(feature, package, note='')`, `plugin_error(feature, error)`,
`plugin_empty(message)`. The two feature macro files keep their existing macro
names as delegators so no call site changes; feature-specific macros
(`mode_badge`, `exit_status_badge`, `scans_scope_note`) stay put.

**A5. `svg-chart-links.js`** — the six prefix branches become a
`{prefix: rowAttr}` table + one loop. Same `openEntityRow` helper, same
`.tab-pane` scoping.

**A6. `_scope_project`** — one implementation in `webapp/utils/`.

---

## Track B — `Scope` objects (the object-oriented underpinning)

A thin shared protocol in `webapp/utils/scope.py` so both features keep the
same vocabulary (that shared vocabulary was the point of modeling jobs on
fs-scans), with a concrete hierarchy per feature.

```python
class NavigatorScope(ABC):
    mode: str                       # 'project' | 'resource'|'machine' | 'user'
    @abstractmethod
    def context(self) -> dict       # the template ctx (target_id, labels, badges)
```

**B1. `jobs/scope.py`** — `ProjectJobScope`, `MachineJobScope`, `UserJobScope`,
each with `apply(kwargs) -> None` injecting its pin. The security invariants
currently written as prose + per-function guards become subclass code:
`UserJobScope.apply` raises when a `user` filter is present; `MachineJobScope`
carries the "unscoped, caller must hold `VIEW_ALL_JOB_DATA`" contract in one
place, checked once instead of restated in four docstrings.
`search_jobs`/`_machine`/`_user` and the three `count_*` collapse to
`search_jobs(machine, scope, ...)` / `count_jobs(machine, scope, ...)`; the six
per-mode names are **retired**.

**B2. `disk_scans/scope.py`** — promote the existing module: add
`ProjectScanScope`, `ResourceScanScope`, `UserScanScope` on top of the existing
`resolve_scan_scope` / `resolve_scan_scope_grouped`. Each exposes
`collections_and_prefixes()`, `forced_owner_uid`, `context()` — replacing
`routes.py::_common_ctx` / `_resource_ctx` / `_user_ctx`. The four
`scan_*` / `scan_*_resource` pairs collapse to one scope-taking function each;
the eight per-mode names are **retired**.

**B3.** `jobs/service.py::_cached_aggregation(...)` — extract the skeleton
repeated by the four aggregation wrappers.

**B4.** `disk_scans/service.py` — `_owner_summary`/`_group_summary` and
`_access_history`/`_file_sizes` become one function each, parameterized by a
small entity/band spec.

**Blast radius of retiring the 14 names.** Every production caller is inside
the two `routes.py` files — the same files Track C rewrites, so those edits are
absorbed rather than added. Nothing in `dashboards/`, `api/` or `cli/` calls
them. Outside `src/` the cost is ~42 direct test call sites (23 in
`test_webapp_jobs.py`, 19 in `test_webapp_disk_scans.py`); the
`monkeypatch`-based plugin fakes patch the *plugin module*, not these
functions, so they are unaffected. The tests asserting the pinning rules
become scope-constructor tests — better placed, not lost.

---

## Track C — fragment-family registrar

**C0 (the safety gate).** `tests/unit/test_route_map_parity.py` pinned only the
five dashboard blueprints. Add `jobs` and `disk_scans` to
`DASHBOARD_BLUEPRINTS` and regen the snapshot **against unmodified code**, so
every subsequent commit is provably surface-preserving: all 37
`(endpoint, rule, methods)` triples must stay byte-identical.

**C1. `webapp/utils/fragments.py`** — modeled on the accepted `CrudSpec` /
`register_crud` precedent in `dashboards/admin/crud.py`:

```python
@dataclass(frozen=True)
class ModeSpec:
    mode: str; url_prefix: str
    decorators: tuple            # (require_project_access,) | (require_permission(P),) | ()
    scope_factory: Callable      # (url_arg) -> NavigatorScope

@dataclass(frozen=True)
class PanelSpec:
    key: str; rule: str; endpoint_suffix: str
    render: Callable             # (scope, **kwargs) -> Response
    modes: tuple[str, ...]       # jobs' by-user has no 'user'; scans' entities has no 'user'
    kwargs: dict                 # per-panel constants (dimension, bucket_header, …)

def register_panels(bp, *, modes, panels) -> None
```

Carries over CrudSpec's hard rule verbatim: **a panel needing more than the
spec expresses stays a bespoke route.** The three `explore_*` full pages and
three `/card` shells stay hand-written unless they fit cleanly.

**C2.** `jobs/routes.py`: 23 routes → two tables + the existing `_render_*`
bodies. The three `_*_histogram` helpers collapse to one. The
`_id_arg`/`target_id` derivation (repeated inline in 7 bodies) moves into
`ModeSpec`.

**C3.** `disk_scans/routes.py`: 14 routes the same way; the three near-verbatim
12-line `_scan` closures disappear (the scope object already carries what they
were forwarding).

**C4.** Fold `_render_by_user` + `_render_by_project` into one
`_render_usage_panel(entity_spec, …)` over one `jobs_usage_panel.html`; same
for the owner/group halves of `disk_scans_entities.html`. Sequenced last
because it touches templates, which the parity snapshot does not cover.

---

## Non-goals

Explicitly out of scope — recorded so a later reader doesn't "finish the job".

1. **No shared "Navigator" framework across the two features.** They are
   siblings, not instances. The rendering genuinely differs: jobs paginates and
   has period pills + facet chips; disk-scans has a limit selector, breadcrumbs
   and path descent. Unifying `jobs_card.html` (364 L, 18 macro params) with
   `disk_scans_card.html` (144 L) yields one parameter-soup macro harder to
   change than the two.
2. **Do not merge the two `service.py` modules.** Different plugins, different
   session ownership — `job_history` needs a per-call session context manager,
   `FsScanQueries` owns its own sessions internally.
3. **Do not merge `generate_jobs_histogram` with
   `generate_distribution_histogram`.** Similar silhouette, different
   semantics: bytes autoscaling + locally-derived tail vs metric selection +
   authoritative upstream remainder.
4. **Do not split `charts.py`.** 1,729 L but cohesive (one `rcParams` block,
   one palette, shared `_fig_to_svg`/`_empty_state`/`_shade_family`). Splitting
   is churn with no reduction.
5. **Do not touch the legacy-compat API blueprints**, nor the jobs/scans
   `?days=` / `active_tab` / persistence contracts — the JS markup contracts
   (`data-chart-persist-shared`, `data-jobs-days-pills`, `#jh-bar-<i>`,
   `#ah-bar-<i>`, `#disk-ent-owner-<uid>`) must survive byte-identical.

---

## Order

| # | Commit | Gate before moving on |
|---|---|---|
| 1 | this doc | — |
| 2 | **C0** — route-map parity gate for `jobs` + `disk_scans` | snapshot committed against unmodified code |
| 3 | A1+A2 — `BucketedTTLCache` + cache registry | cache + redis + flask-adapter suites |
| 4 | A3 — `PluginExtension`; migrate both `session.py` | plugin-init tests |
| 5 | A4+A6 — shared state macros, JS dispatch table, one `_scope_project` | render smoke + full suite |
| 6 | B1+B3 — `jobs/scope.py`, retire the 6 per-mode names | `test_webapp_jobs*.py` |
| 7 | B2+B4 — `disk_scans/scope.py`, retire the 8 per-mode names | `test_webapp_disk_scans.py` |
| 8 | C1 — `webapp/utils/fragments.py` registrar | parity snapshot unchanged |
| 9 | C2 — `jobs/routes.py` onto the registrar | parity snapshot unchanged |
| 10 | C3 — `disk_scans/routes.py` onto the registrar | parity snapshot unchanged |
| 11 | C4 — usage-panel + entity-table template folds | browser smoke |

C0 goes second so the 37-route surface is pinned *before* anything touches
route bodies. B precedes C because the registrar needs a scope object to hand
each render function. Stopping after commit 10 (or 7) leaves a coherent,
shippable state.

## Verification

1. **Route surface** — `pytest tests/unit/test_route_map_parity.py` with an
   unchanged snapshot after C0.
2. **Targeted suites** — `test_webapp_jobs.py`, `test_webapp_jobs_cache.py`,
   `test_webapp_jobs_charts.py`, `test_webapp_disk_scans.py`,
   `test_redis_cache.py`, `test_flask_cache_adapter.py`, `test_nav.py`,
   `tests/integration/test_status_dashboard.py`.
3. **Full suite** — `source etc/config_env.sh && pytest`.
4. **Live smoke** (`docker compose up webdev --watch` → :5050) across all three
   modes per feature — project (resource-details Compute + Disk cards),
   machine/resource (`/status/job-history`, `/status/filesystem-scans`), user
   (`/dashboards/user/jobs`, `/dashboards/user/data`) — plus both explorers.
   Check tab persistence across reload, period-pill fan-out across machine
   subtabs, bar/pie click-through, and that filter Apply keeps the active tab.
5. **Cache card** — Admin → Configuration shows all 6 buckets with today's TTLs
   (8 d / 30 min scans; 30 min / 15 min jobs; 1 h usage), and
   `sam-admin cache --refresh --category jobs|scans` still reports counts.
