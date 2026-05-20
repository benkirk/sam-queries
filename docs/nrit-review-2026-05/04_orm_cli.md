# Phase 4 — ORM + CLI (`src/sam/`, `src/cli/`, `src/sam_search_cli.py`)

> Domain core. SQLAlchemy 2.0 ORM, schemas, query helpers, CLI tooling (`sam-search`, `sam-admin`). The "library" most other subsystems depend on. ~28,500 LOC across 123 files.

## Scope

- `src/sam/` — base, core, resources, projects, accounting, activity, summaries, integration, security, operational, schemas, queries, manage, fmt, session, caching, plugins
- `src/cli/` — Click-based CLI architecture (core, user, project, allocations, cmds, accounting, notifications, templates)
- `src/sam_search_cli.py` — legacy/entry shim
- Tests: `tests/unit/test_basic_read.py`, `test_crud_operations.py`, `test_new_models.py`, `test_query_functions.py`, `test_sam_search_cli.py`, `tests/integration/test_schema_validation.py`, `test_views.py`, `tests/factories/`

## Method

Five parallel deep-dives along non-overlapping vertical slices:

1. **ORM models** — 91 model classes across 9 domain subpackages. `is_active` discipline, write-op pattern, `back_populates`, mixin layering, `NestedSetMixin` raw SQL.
2. **Library API** — `sam/queries/` (18 modules, 8,225 LOC), `sam/manage/` (5 modules, 2,114 LOC), `sam/fmt.py`, `sam/caching/`. Query-vs-model boundary, manage-layer scope, fmt compliance, usage_cache semantics.
3. **Schemas** — `sam/schemas/` 3-tier discipline (Full/List/Summary), `AllocationWithUsageSchema` correctness + performance, form schemas (cross-checked against Phase 2's form-validation findings).
4. **CLI** — `src/cli/` architecture, exit-code discipline, JSON envelope, `sam-admin`-extends-`sam-search` inheritance pattern.
5. **Test discipline** — Layer 1 fixtures + Layer 2 factories, schema-drift assertiveness, query/CLI test coverage gaps.

## Lenses applied

- Architecture (primary)
- Security (input validation in CLI / form schemas)
- Testing
- Performance (query patterns, schema fanout)

---

## Findings

### Headline

The ORM layer is the **conceptual heart of the project** — well-designed mixins, a universal `is_active` hybrid, the `update()`/`create()` co-location pattern, three-tier schemas, the `HtmxFormSchema` family, the two-tier test strategy. **The conventions are excellent and largely followed.**

But this is also where the largest concentration of bugs and convention-drift surfaced in the audit:

- **One latent correctness bug** in `Project.active_account_users` (silently drops the `start_date` guard).
- **One silent-null bug in production** — `ProjectListSchema.get_admin_username` has an empty function body and returns `None` for every project list.
- **One 500-on-orphan-projects bug** — `ProjectSchema.get_panel` doesn't guard against the documented `allocation_type=None` case.
- **One performance smell** — `AllocationWithUsageSchema` recomputes usage 4-8× per allocation dump (16-24 DB queries each); on `many=True` endpoints that's N×20 round trips.
- **One silent shadowing bug** — `analyze_renew_preconditions` defined twice in `sam/manage/renew.py`.
- **Systemic `is_active` discipline gaps** in the very models that define the hybrid (10+ violations in `Project` and `User` themselves).

None of these are security issues; all are within reach of a focused PR. The architecture is sound — the drift is at the convention-application level, which is exactly the kind of thing a directional audit can usefully surface.

### Architecture

**Strengths**

- **Universal `is_active` hybrid** is implemented correctly on every model that needs it. The 5 mixins (`TimestampMixin`, `SoftDeleteMixin`, `ActiveFlagMixin`, `DateRangeMixin`, `SessionMixin`) are clean, single-responsibility, and well-named. `normalize_end_date` is centralized and consistently applied.
- **Write-op consolidation is real** — 0 standalone `update_*(session, id, ...)` or `create_*(session, ...)` helpers remain in `src/sam/`. The migration documented in CLAUDE.md §7 happened.
- **`__eq__` / `__hash__` discipline** on Base subclasses — Project, User, Account, Resource, Machine, Queue, Allocation, AccountUser, Facility, Panel, Institution, Organization all define both with the same PK-or-`id(self)` pattern. Set-based deduplication is safe.
- **`sam/__init__.py` deliberate `__all__`** — no `from .x import *` at the package level; every name enumerated; IDE-friendly.
- **XRAS views demarcated** via `__table_args__ = {'info': {'is_view': True}}`; schema-validation tests can use the `info` dict to skip INSERT checks.
- **`sam/queries/dashboard.py` two-path strategy** (`_build_project_resources_data` vs `_build_user_projects_resources_batched`) is well-reasoned, documented, and locked in step by an equivalence test. Exemplary engineering.
- **`sam/manage/allocations.py log_allocation_transaction`** carefully handles the legacy Java enum encoding with `[TAG]` prefixing and explicit retirement notes — the kind of compatibility shim that earns its long docstring.
- **`fmt.py` matches CLAUDE.md exactly** — 4 filters, `SAM_RAW_OUTPUT` honored, `mpl_number_formatter` present, None→'—' default, naive-UTC datetime triad (`to_local_dt` / `naive_local_to_utc` / `local_tz_label`).

**Findings**

- **A1 [Med] `Base` dual-mode silent fallback** (`sam/base.py:19-40`) — identical pattern to Phase 3's `system_status/base.py:44-51` finding. Under `FLASK_ACTIVE=1`, if `from webapp.extensions import db` raises `ImportError`, falls back to standalone `declarative_base()` instead of failing loudly. Carry-over for the cross-cutting `[XC: prod-config-hardening]` roll-up — this makes 5 footguns following the same pattern.

- **A2 [Med] `Project` god-class (1,387 LOC, 38 methods).** CRUD, tree ops, allocation usage, charge aggregation, job stats, breadcrumb formatting, batch charge fetching. `_ensure_values_cte_probed` lives at module level outside the class. `batch_get_subtree_charges` and `batch_get_account_charges` are 200+ lines each and are classmethods that don't access `self` — strong candidates to move to `sam/queries/charges.py`. Maintainability rather than correctness; would suggest only if Ben agrees the file has grown organically.

- **A3 [Low] `NestedSetMixin` raw-SQL UPDATEs** (`base.py:380-397`). Two non-atomic `UPDATE ... SET tree_left = tree_left + 2` statements before assigning new coordinates. Under concurrent inserts into the same tree (especially the unscoped `Organization` path with no `tree_root`), two transactions could both compute the parent's `tree_right`, both shift, and corrupt invariants. Mitigations: `SELECT ... FOR UPDATE` on parent, or table-level lock. SAM tree mutations are presumably admin-only / low-frequency — flag for Ben's awareness.

- **A4 [Low] Code duplication: 8 models reinvent `DateRangeMixin`'s end-date normalization** (`Allocation`, `AllocationTransaction`, `Contract`, `Machine`, `Resource`, `MachineFactor`, `QueueFactor`, `WallclockExemption`). All declare their own `@validates('end_date')` calling `normalize_end_date(value)`. Most can't inherit `DateRangeMixin` directly (different column names or `start_date` nullable), but a thin `EndDateNormalizerMixin` would DRY it.

- **A5 [Low] `cli/{accounting,notifications,templates}/` undocumented in CLAUDE.md.** All three are production code (charge posting, expiration emails, Jinja templates), not stale. CLAUDE.md's CLI section lists only `user/`, `project/`, `allocations/`, `cmds/`. `cli/README.md` lists `accounting/` but not the other two.

### Bugs (correctness)

- **B1 [High] `ProjectListSchema.get_admin_username` has no body** (`sam/schemas/project.py:67-70`) — only a docstring, no `return`. **Every project in every list response gets `admin_username: null` regardless of actual admin.** Likely an editor-eaten line during refactor. Fix: `return obj.admin.username if obj.admin else None`. ⚠ This is silently shipping in production today.

- **B2 [High] `ProjectSchema.get_panel` raises 500 on orphan projects** (`sam/schemas/project.py:122`). Guards on `obj.allocation_type.panel` but not on `obj.allocation_type`. `Project.facility_name` (`projects.py:416-435`) explicitly documents `allocation_type is None` as a valid orphan state. `GET /api/v1/projects/<orphan>` will 500. Mirror the guard from `get_type` above it.

- **B3 [Med, latent] `Project.active_account_users` silently drops the `start_date` guard** (`sam/projects/projects.py:386-393`). Filters only on `au.end_date is None or au.end_date >= check_date`. `AccountUser` inherits `DateRangeMixin`, which provides `is_active_at(check_date)` doing exactly this **and** enforcing `start_date <= check_date`. The current code can surface future-dated rows. Same anti-pattern in `core/users.py:410, 433`. Fix: use `au.is_active_at(check_date)` directly.

- **B4 [Med] Duplicate function definition in `manage/renew.py`** — `analyze_renew_preconditions` at line 168 and again at line 206. Bodies are byte-identical except docstring; the second silently shadows the first. One should be deleted.

- **B5 [Med, semantic] `Organization` and `Institution` declare `deleted = Column(Boolean)` raw instead of using `SoftDeleteMixin`** (`core/organizations.py:49, 203`). Their `is_active` (from `ActiveFlagMixin`) consequently considers only `active`, not `deleted`. A `deleted=True, active=True` row is treated as "active." Whether that's correct depends on intent — open question for Ben.

### Performance

- **P1 [High] `AllocationWithUsageSchema` recomputes usage 4-8× per allocation dump** (`sam/schemas/allocation.py:291-345`). `get_used` / `get_remaining` / `get_percent_used` / `get_root_projcode` each call `_calculate_tree_usage` independently; `get_charges_by_type` / `get_adjustments` / `get_self_used` / `get_self_percent_used` each call `_calculate_usage` independently. For an HPC allocation: 8 method invocations × 2-3 DB queries each = 16-24 round trips per allocation. On `GET /api/v1/projects/<projcode>/allocations` with `many=True` and N allocations: **N×~20 round trips**. Disk-usage variant (`get_current_used_bytes/tib/snapshot_date/pct_used`) repeats `account.current_disk_usage(session)` 4×. Fix: memoize on the schema instance keyed by `obj.allocation_id`.

- **P2 [Med] `ProjectListSchema` lead/admin lookups defeat the "lightweight" contract** (`sam/schemas/project.py:60-67`). `obj.lead` is `lazy='select'` on `Project` (`projects.py:130`). `ProjectListSchema(many=True).dump(projects)` triggers N queries for `lead`. The "List" tier docstring claims minimal nesting but is N+1 in practice. Either declare as eager-load or document that callers must pre-load. Same shape in `UserSchema.get_institutions/get_organizations/get_roles` (`user.py:141-184`).

- **P3 [Med] `usage_cache.py` has no invalidation hooks** (`sam/queries/usage_cache.py`). `purge_usage_cache()` is the only invalidator and nukes everything. Nothing calls it from `manage/allocations.py`, `manage/renew.py`, `manage/extend.py`. Renew/extend/update via admin UI leaves stale rows visible for up to 3,600s. Per-project or per-allocation key-prefix invalidation would let writes invalidate surgically.

- **P4 [Med] `usage_cache.py` bypasses `app.config['CACHE_REDIS_URL']` reachability gate** (`sam/queries/usage_cache.py:81`). Reads `os.environ.get('CACHE_REDIS_URL')` directly. Webapp's `webapp/caching/__init__.py:62-91` does a PING on startup and downgrades `CACHE_TYPE` to `SimpleCache` on failure — usage_cache doesn't see that downgrade. **This sharpens P2-14 from Phase 2** (which was the same finding under a different lens).

- **P5 [Low] Unbounded `.all()` results** in `queries/projects.py:67` (`search_projects_by_title`), `:74` (`get_active_projects`), `queries/users.py:217` (`search_users_by_email`). Other search/lookup functions cap at 50 or 100; these three don't. A wildcard `%a%` against `users.email` returns thousands at NCAR scale.

- **P6 [Low] `queries/expirations.py:31` uses sentinel date `datetime(9999, 12, 31)`** for NULL-end-date coalescing in three places. Works for the next ~7,977 years. Standard SQL anti-pattern; `ORDER BY end_date IS NULL DESC, end_date DESC` would be cleaner.

### Convention drift (`[XC: convention-drift]`)

- **D1 [Med] `is_active` violations inside `Project` itself** — `projects/projects.py:227, 151, 546-548, 583` (4 sites). `cls.active == True`, `Account.deleted == False`, and raw `or_(Allocation.end_date.is_(None), ...)` where `Allocation.is_active` already encodes exactly this. The class that defines the canonical hybrid is itself the worst offender against §5.

- **D2 [Med] `User` query methods bypass `User.is_active`** — `core/users.py:191, 245, 279, 352, 362, 385` (6 sites). All spell out `cls.active == True, cls.locked == False` instead of `cls.is_active`. Same hybrid, six identical violations.

- **D3 [Med] `queries/statistics.py:89`** uses raw `Project.active == True` in `get_institution_project_count`. CLAUDE.md §5 documents `statistics.py` as the one permitted exception, but the rationale comment is on line 40 (User counters), not 89. Looks like an inadvertent extension rather than an intentional exception. Fix one line or add a comment.

- **D4 [Med] 4 pure-wrapper functions in `queries/lookups.py`** — `find_user_by_username` (line 88), `find_users_by_name` (line 93), `find_project_by_code` (line 109), `get_group_by_name` (line 118). Each delegates to a one-liner model classmethod that already exists. Per CLAUDE.md these should just be the classmethods; ~8 webapp/CLI callers would need updating.

- **D5 [Med] `queries/examples.py` is dead code that imports `from sam import *`** (4 star-imports at the top). Marked `# pragma: no cover`, not exported from `queries/__init__.py`. Move to `docs/examples/` or `notebooks/`; drop the pragma.

- **D6 [Med] `manage/allocations.py:264` and `sam.manage/__init__.py:82`** still filter with raw `Account.deleted == False`. Trivial; should use `Account.is_active`.

- **D7 [Med] `Factor` / `Formula` redundantly redeclare `is_active`** (`resources/charging.py:36-48, 85-97`). The hybrid `DateRangeMixin` already provides is identical. Deletable.

- **D8 [Med] `FacilityResource.modified_time` not from `TimestampMixin`** (`resources/facilities.py:131-132`) — declares creation/modified columns directly, reproducing 80% of the mixin inline. Trivial nit.

### CLI

- **C1 [Med] `AccountingAdminCommand` does NOT extend `AccountingSearchCommand`** (`cli/accounting/commands.py:197 + :1669`). Both inherit `BaseCommand` directly, contrary to the documented admin-extends-search pattern in `cli/README.md:99`. The accounting module is the largest single file (1,720 LOC) and the one where the pattern would matter most.

- **C2 [Med] `cli/accounting/commands.py` uses bare integer return codes** (`return 0`, `return 1`, `return 2` at 50+ sites). The rest of the package uses `EXIT_SUCCESS` / `EXIT_NOT_FOUND` / `EXIT_ERROR` from `cli.core.utils`. Functionally identical, inconsistent.

- **C3 [Med] `EXIT_KEYBOARD_INTERRUPT` (130) is defined but never used** (`cli/core/utils.py:7`). No top-level `try/except KeyboardInterrupt` in `cmds/search.py` or `cmds/admin.py`. Ctrl-C bubbles up as an unhandled exception, defeating the documented exit code.

- **C4 [Med] Inline date coercion in CLI** — `cmds/admin.py:338, 424` and `allocations/commands.py:39` use `datetime.strptime(..., '%Y-%m-%d').date()`. `cli/accounting/dates.py` is a good shared helper; these three sites should route through it.

- **C5 [Med] Mode-validation errors print to stdout** (`cmds/admin.py:93-109, 290-344, 372-430`) — they use `ctx.console` (stdout) for error messages then exit non-zero. Database errors at the same level correctly use `ctx.stderr_console`. **Error on stdout corrupts JSON pipelines** (`sam-admin --format json ... | jq` on a validation error). Standardize on stderr.

- **C6 [Med] `ProjectExpirationCommand._deactivate_projects` mutates ORM directly** (`cli/project/commands.py:248-259`) — `project.active = False; project.inactivate_time = now` then `self.session.commit()`. Per CLAUDE.md §7, write logic belongs on the model. Consider `Project.deactivate(timestamp=now)`.

- **C7 [Low] `--validate` / `--reconcile` admin commands are placeholders** — `UserAdminCommand._validate_user` (`cli/user/commands.py:162-181`) and `ProjectAdminCommand._validate_project/_reconcile_project` (`cli/project/commands.py:433-460`). Docstrings literally say "Placeholder validation logic". `--reconcile` prints "reconciled" with no work done. Either document as WIP or remove from `--help`.

- **C8 [Low] No TTY detection / `NO_COLOR` opt-out** — `Console()` is constructed bare (`cli/core/context.py:20-21`). Rich auto-detects pipes, but no explicit `--no-color` / `NO_COLOR` env handling. ANSI escapes can leak when output is tee'd.

- **C9 [Low] `cmds/admin.py:38-39` and `cmds/search.py:46-50`** use `sys.exit(1)` on DB connect failure where `sys.exit(2)` (EXIT_ERROR) is the correct semantic. Same module mixes `1` and `2` inconsistently for the same error class.

- **C10 [Low] `cli/README.md:166-168` references `sam_search_cli_original.py`** which no longer exists. Doc drift.

### Schema layer (additional, beyond B1/B2/P1/P2)

- **S1 [Med] Edit-form checkbox semantics inconsistent across forms.** `EditProjectForm` uses `partial=True` (`projects_routes.py:529`) — `EditFacilityForm`, `EditOrganizationForm`, `EditAoiForm`, `EditAoiGroupForm`, `EditNsfProgramForm`, `EditContractSourceForm`, `EditAllocationTypeForm` do **not**. For those, "unchecked checkbox = deactivate" is the contract (because `f.Bool(load_default=False)` fires when the key is stripped). Either consistently use `partial=True` for Edit, or document the deactivation contract loudly in each form's docstring.

- **S2 [Med] Missing schema tiers** — `AllocationListSchema`, `AllocationSummarySchema`, `ResourceListSchema` don't exist. List endpoints reuse the heavy `AllocationWithUsageSchema` (see P1) or `AllocationSchema`. The 3-tier doctrine is documented but partly aspirational here.

- **S3 [Low] `AllocationSchema.is_active` Method override is dead code** (`sam/schemas/allocation.py:71-75`). Overrides with `obj.is_active_at(datetime.now())`, which is exactly what the `Allocation.is_active` hybrid already returns. Drop the override.

- **S4 [Low] `CompJobSchema` manually `.isoformat()`s inside Method fields** (`sam/schemas/jobs.py:60-76`). CLAUDE.md "Datetime serialization" explicitly says no manual `.isoformat()` calls.

- **S5 [Low] ~35 Method fields hand-wrap simple `@property` accessors** (`schemas/user.py:40-49, 73-87, 119-139`; `schemas/resource.py:43-45`; `schemas/jobs.py:78-92`). `fields.Str(attribute='full_name', dump_only=True)` is the canonical idiom; Method fields earn their keep only when the result needs argument-aware computation.

### Testing

**Test count:** 1,494 explicit `def test_*` + 30 parametrize blocks → well above CLAUDE.md's documented ~1,400 (matches Phase 1's actual count of ~1,750 closer).

**Strengths**

- **Two-tier discipline genuinely clean** — no helper in `tests/factories/` reads Layer 1 fixtures; Layer 1 fixtures return IDs that tests re-`session.get()` into their own session. Tests compose both layers freely without blurring them.
- **`make_project` correctly calls `_ns_place_in_tree()`** (`tests/factories/projects.py:125`) — a subtle NestedSetMixin trap the inline comment calls out.
- **Schema-drift tests promoted from informational → assertive** for FK existence (`test_foreign_keys_exist`) and UNIQUE index drift (`test_unique_constraints_match`).
- **Disk-capacity-vs-burn coverage is unusually thorough** — three layers pin the same invariant (data → query → display).
- **Test skips are well-justified** — every `pytest.skip` (154 occurrences) I sampled has a clear inline reason.

**Findings**

- **T1 [Med] Two documented query functions are untested.** `get_projects_by_allocation_end_date` / `get_projects_with_expired_allocations` — featured in CLAUDE.md (return-shape tuples spelled out explicitly as a "common pitfall to unpack") but `test_query_functions.py` has zero coverage. CLI smoke tests at `test_sam_search_cli.py:226-234` exercise them indirectly. ~4 targeted tests would close the gap.

- **T2 [Med] CLI tests cover exit codes 0 and 1, never 2 or 130.** CLAUDE.md documents `2 = error, 130 = keyboard interrupt`. The unit file should assert at least one error path produces exit_code 2. **Combines with C3 above:** if the KeyboardInterrupt handler is added (130), it needs a test.

- **T3 [Med] `test_column_types_match` and `test_database_columns_in_orm` are informational, not assertive** (`tests/integration/test_schema_validation.py:240-285`). They print warnings but don't fail. The documented Boolean → BIT(1) historical bug **would not be caught** because Boolean accepts both BIT and TINYINT in `TYPE_MAPPINGS`. CLAUDE.md claims "Schema validation tests will catch future drift" — that's a partial claim. Either promote to assertive or document the informational intent.

- **T4 [Med] `test_crud_operations.py` instantiates models directly even where `create()` exists.** Most `TestCreate*` / `TestUpdate*` tests build `Allocation(...)` / `EmailAddress(...)` / `Phone(...)` by hand. `Allocation.create()` validation (`amount > 0`) is only exercised implicitly via the factory. Add explicit tests like `test_allocation_create_rejects_zero_amount`.

- **T5 [Med] `make_organization` factory has a fragile non-autoincrement workaround** (`tests/factories/core.py:16-19`). Carves out `[10_000_000, 10_000_000 + n*100_000)` per xdist worker via process-local counter. If any worker creates >100,000 orgs in a session it collides with the next worker's slice. Unlikely in practice but worth documenting or asserting.

- **T6 [Low] Stale `new_tests/conftest.py` path references** in `tests/factories/__init__.py:2`, `test_schema_validation.py:10`, `test_query_functions.py:8`, `test_webapp_smoke.py:12`, `test_factories.py:1`. From a directory rename; harmless but a dead pointer.

- **T7 [Low] Raw `User.active == True` in `tests/conftest.py:359`** and `test_basic_read.py:156, 246` — convention violation per §5. Trivial cleanup.

- **T8 [Low] Two CLI tests pin `benkirk` in content assertions** (`test_sam_search_cli.py:99-102`). Rationale is solid (obfuscated snapshot, `_` stripping in `cli/user/commands.py:37`), but if `benkirk` ever ceases to be the preserved test user the test will silently start asserting wrong content. Add `assert User.get_by_username(session, 'benkirk')` as an explicit precondition.

- **T9 [Low] `test_views.py:96-106` swallows `XrasRequestView` GROUP BY error** with bare `except Exception: pass`. If the underlying issue is ever fixed, tests continue passing despite no longer exercising the view. Narrow to `except OperationalError` + TODO comment.

- **T10 [Low] `test_redis_cache.py:75` uses `time.sleep(1.1)`** — documented case (fakeredis honors real-time TTLs), only one in the suite, low priority. Flagged because CLAUDE.md test-smell guidance lists `time.sleep` as a smell.

- **T11 [Low] `_session_for_setup()` (`tests/conftest.py:496-499`) opens a session without explicit transaction.** Works because reads auto-commit on close, but a misplaced write would bypass the SAVEPOINT safety net. Defensive: use `engine.connect()` + `text()` directly for read-only intent.

---

## Cross-cutting tags raised

- `[XC: prod-config-hardening]` — `sam/base.py:19-40` makes 5 fall-open footguns following the same pattern (carry-over: Phase 2 DISABLE_AUTH/AUTH_PROVIDER/RATELIMIT_STORAGE_URI, Phase 3 system_status base, Phase 4 sam base).
- `[XC: convention-drift]` — 10+ `is_active` violations inside the very models that define the hybrid (`Project`, `User`); 4 pure-wrapper functions in `queries/lookups.py`; bare integer exit codes in `cli/accounting/`; inline date coercion across 3 CLI sites.
- `[XC: docs-drift]` — `cli/{accounting,notifications,templates}/` undocumented in CLAUDE.md; `cli/README.md` references non-existent `sam_search_cli_original.py`; tests reference non-existent `new_tests/conftest.py`.
- `[XC: testing]` — Two schema-drift tests are informational despite CLAUDE.md claiming they catch drift; two documented query functions have zero coverage; CLI tests don't exercise exit codes 2 or 130.
- `[XC: perf]` — `AllocationWithUsageSchema` N×20 fanout; `ProjectListSchema` N+1; `usage_cache` lacks invalidation on writes.

## Open questions for Ben

1. **`ProjectListSchema.get_admin_username` (B1)** — has `admin_username` been silently null in production list responses for a while, or is this a recent regression? A quick spot-check of `/api/v1/projects/` against a project with a known admin would confirm.
2. **`AllocationWithUsageSchema` memoization (P1)** — would you accept a memoization pass that caches `_calculate_tree_usage` and `_calculate_usage` on the schema instance per `allocation_id`? Expected 5-10× speedup on `many=True` endpoints.
3. **`Organization`/`Institution` `deleted` semantics (B5)** — should their `is_active` consider `deleted`? Right now a `deleted=True, active=True` row is "active." Bug or intentional?
4. **`NestedSetMixin` concurrency story (A3)** — are project/org tree mutations gated behind any application-level lock, or assumed admin-only / serialized in practice?
5. **`Project` god-class (A2)** — would a refactor splitting batch charge methods into `sam/queries/charges.py` be welcome, or do you prefer keeping them as Project classmethods for discoverability?
6. **`usage_cache` invalidation gap (P3)** — 1-hour stale-data window after renew/extend/update via admin UI. Acceptable by design (writes are infrequent), or worth surgical invalidation hooks?
7. **`AccountingAdminCommand` inheritance (C1)** — intentional break from the admin-extends-search pattern, or aspirational? Either is reasonable; the docs should reflect reality.
8. **`--validate` / `--reconcile` placeholders (C7)** — awaiting real logic, or should they be hidden from `--help` until ready?
9. **Edit-form checkbox semantics (S1)** — is "unchecked = deactivate" the intended contract for the 7 Edit forms that don't use `partial=True`? Worth a two-sentence docstring in `HtmxFormSchema` either way.
10. **Informational-only schema-drift tests (T3)** — intentional human diagnostic, or should they fail? CLAUDE.md overstates strictness today.
