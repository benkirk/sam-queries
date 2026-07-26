# Webapp Boilerplate Reduction & OO Refactor

**Status: IN PROGRESS — started 2026-07-26.**
Branches: PR 1 = `webapp_quick_wins`, PR 2 = `oo_refactor` (stacked on PR 1). Both target `staging`.

## Progress

- [x] Step 0 — this doc committed; baseline pytest green (2916 passed)
- **PR 1 — peripheral quick wins (≈ −1,300 LOC)**
  - [x] 1.1 dead-code deletion (−164; also removed test-only predicates has_any_permission/has_all_permissions/has_any_role)
  - [x] 1.2 stale docs (−702)
  - [x] 1.3 charts.py `_fig_to_svg` + `_empty_state` (−13 net, leak fix)
  - [x] 1.4 Flask-Admin spec-loop (−358) — approved by Ben after page-output
        snapshot proof: 947 URL rules + 106 view registrations + 105/106
        normalized page hashes byte-identical; the one differing page
        (`projects`, custom ProjectAdmin, untouched) differs between two
        runs of identical code (inherent render nondeterminism)
  - [x] 1.4b (Ben's suggestion) auto-detect models from the SAM declarative
        registry (excluding __bind_key__ models); slug derivation reproduces
        all 93 legacy endpoints exactly; recovered 6 models the manual list
        had drifted past (Country, GidAllocation, ManualTask, Product,
        StateProv, Synchronizer); crawl proof: 0 removed, 6 added (all 200),
        existing page bodies byte-identical nav-stripped
  - [x] 1.5 `invalidate_queue_cache()` ownership + `allowed_facility_names()` (admin site kept active_only=False — divergence surfaced for Ben)
  - [x] 1.6 GET-side date parsing (end-of-day note in commit) + `sort_link` macro
  - [x] PR 1 opened — #375 (webapp_quick_wins → staging, −1,105 net)
  - [x] Playwright smoke of the manual checklist — all 4 items pass; results
        posted as a comment on #375 (menu=99 w/ 6 recovered; charts+empty-state
        byte-identical; queue edit→API fresh both directions, DB restored;
        sort/paginate green on shared macro)
- PAUSE after PR 1 opens, before any Phase-2 (PR 2) work — per Ben 2026-07-26
- **PR 2 — form-layer OO refactor (≈ −1,100..1,400 LOC)**
  - [x] 2.1 `HtmxFormHandler` + adapter + parity tests (2938 passed, zero
        existing-test edits; route-map snapshot = 239 dashboard routes)
  - [ ] 2.2 `CrudSpec` registrar + orgs migration (8 entities)
  - [ ] 2.3 facilities + resources registrar migrations
  - [ ] 2.4 31 hand-rolled shape-C handlers → handler subclasses
  - [ ] 2.5 §9 conformance (exemption schemas + shape-D migrations)
  - [ ] 2.6 typeahead factory
  - [ ] PR 2 opened

## Context

Investigation (2026-07-26): after the #118→#160→#182→#238→#336→#358 refactor arc, the
webapp (~26k LOC Python) retains ~2,400 lines of mechanical boilerplate, concentrated in:

1. **31 hand-rolled form-POST handlers** (2,165 lines, avg 69.8) that could not adopt the
   10-kwarg `handle_htmx_form_post` (31 sites already use it) because of variation axes a
   function can't express: partial loads, PUT gating, ORM-dependent cross-field checks,
   domain-exception mapping, custom success responses, post-commit hooks. This is where
   OO genuinely helps — a template-method class, matching the CLI `BaseCommand` house style.
2. **~12–14 simple admin CRUD entities** each repeating an ~80-line route quintet
   (edit-form GET / edit POST / create-form GET / create POST / DELETE) → declarative registrar.
3. Peripheral wins: dead code (verified 0 callers), stale docs, Flask-Admin's 93 empty
   generated classes (`utils/wrap_classes.py` is a one-off bootstrap scaffold, not a live
   pipeline), charts.py figure-lifecycle duplication.

**Declined after investigation (do not revisit without new evidence):** disk_scans matrix
(already factored via `_render_*` helpers; scope resolution IS the security boundary), API
envelope/pagination unification (deliberate per-endpoint caps; consumer contract risk),
rbac/access_control decorator meta-factory (security-critical explicitness wins),
derecho()/casper() and page-route loops, big-bang function-local import hoist,
`if active_only:` filter idiom.

**Decisions (Ben, 2026-07-26):**
- Both tracks, one PR each, ordered commit series within each PR.
- **Unify error rendering on inline field errors** (`split_errors`) during migration —
  completes the PR #336 follow-up; deliberate UI change across ~29 forms.
- Additive `invalidate_queue_cache()` in `api/v1/queue.py` approved (legacy-compat file;
  zero response bytes change).
- Tests may be run in-session for this work.

**Constraints:** 5 legacy-compat API files otherwise untouched (`directory_access.py`,
`project_access.py`, `fstree_access.py`, `queue.py`, `wallclock_exemption.py`); outage
datetime-local JS (#337), page_tabs per-page lists (#359), FK-picker active-only defaults
preserved; every write stays inside `management_transaction` (implicit audit); forms stay
standalone marshmallow schemas.

---

## PR 1 — Peripheral quick wins (branch `webapp_quick_wins`)

### 1.1 Dead-code deletion (verified 0 callers in src/ + tests/)
- `src/webapp/api/helpers.py:197-248`: delete `success_response`/`error_response` (their
  success envelope contradicts the deployed flat contract asserted in
  `tests/api/test_project_endpoints.py:173`, `test_user_endpoints.py:91`).
- `src/webapp/utils/rbac.py:570-613`: delete `require_any_permission`, `require_role`.
- Delete `src/webapp/clients/` (only a stale `__pycache__`).

### 1.2 Stale docs
- Delete `src/webapp/DESIGN.md`, `src/webapp/REFACTORING_PLAN.md` (git history preserves).
- Fix `src/webapp/README.md` stale claims (Bootstrap 4 → current stack, tree); add
  function-local-import policy paragraph (module-top default; local import needs a reason
  comment: cycle / ORM-init order per `access_control.py:338` / optional dep).
- Fix stale status line in `docs/plans/implemented/FORMAT_DISPLAY.md`.

### 1.3 charts.py figure lifecycle (≈ −45; fixes figure leak on savefig exception)
Add `_fig_to_svg(fig)` (savefig in `try`, `plt.close(fig)` in `finally`) and
`_empty_state(msg, extra_classes='')`. Replace 11 savefig tails (lines ~274, 380, 504,
639, 797, 862, 930, 997, 1047, 1168, 1473) and 14 empty-state literals — **messages
byte-identical** (`tests/unit/test_disk_usage_chart.py:35` asserts substrings; the line
~553 empty-state needs `extra_classes='py-4'`). Leave the 7 cache-key fns.

### 1.4 Flask-Admin spec-loop (≈ −370)
- `admin/default_model_views.py:301-676`: delete the 93 `class XDefaultAdmin(SAMModelView):
  pass` (verified pass-only, referenced only by `add_default_models.py`). Keep `SAMModelView`.
- `admin/add_default_models.py`: `_DEFAULT_MODELS = [(Model, 'endpoint_snake'), ...]`
  (literal endpoints, no derivation) + `_CUSTOM_VIEWS = {}` promotion dict + loop
  instantiating `SAMModelView` directly (Flask-Admin needs distinct endpoints, not classes).
- Extend `tests/unit/test_admin_defaults.py`: 93 views registered + sampled endpoints intact.

### 1.5 Ownership fixes (≈ −30)
- `api/v1/queue.py`: add `invalidate_queue_cache()` next to the memoized fns; `refresh_cache()`
  calls it. Delete `resources_routes.py:37-54 _invalidate_queue_api_cache`; repoint its 4
  call sites (`resources_routes.py:586, 658, 682, 860`).
- `utils/rbac.py`: add `allowed_facility_names(user, permission, *, active_only=True)` next
  to `user_facility_scope`. Replace 3 copies: `admin/blueprint.py:78-85` (pass
  `active_only=False` — preserves current behavior; **surface the is_active divergence in
  the PR description as a probable latent bug for Ben to rule on**),
  `allocations/blueprint.py:271-281`, `:351-361`.

### 1.6 GET-side consolidations (≈ −30)
- Adopt `parse_input_start_date`/`parse_input_end_date` (`api/helpers.py:45-61`) at
  dashboard GET strptime sites — esp. 4 identical 12-line blocks in
  `user/blueprint.py:438, 690, 823, 924`. Skip POST handlers (PR 2) and legacy files.
- Extract the identical twin `sort_link` macros
  (`allocations/partials/transactions_table.html:9` ≡ `adjustments_table.html:9`).

**Verify per commit:** pytest; grep gates (deleted symbols gone). PR-level manual:
`FLASK_ADMIN_ENABLED=1` click-through, charts eyeball incl. empty-data case, queue edit →
`GET /api/v1/queue` freshness.

---

## PR 2 — Form-layer OO refactor (branch `oo_refactor`, stacked on PR 1)

Design: **hybrid** — new template-method class core; `handle_htmx_form_post` keeps its
exact signature as a thin adapter (zero churn for its 31 call sites). Rejected: more
kwargs (18-kwarg function, lambda nests); migrating all 62 to classes (churn on the 31
already-good sites).

### 2.1 Infrastructure
- New `src/webapp/utils/form_handler.py` (~170 lines): `HtmxFormHandler` + `FormError`.
  Lifecycle: `form_input() → load() → clean() → [management_transaction: perform()] →
  after_commit() → on_success()`; all error paths funnel to `render_errors()`
  (`errors=form_level, field_errors=..., form=request.form, **context()`).
  Class attrs: `schema_cls, template, partial, error_prefix, success_message,
  success_redirect, exception_map`. `__init__(**entities)` stores route-loaded ORM objects.
  **split_errors only** (unify decision — no `error_style` axis).
- Rewrite `handle_htmx_form_post` body as `_KwargFormHandler` adapter; add optional
  `after_commit=None` kwarg. Add `modal_triggers(*reload_events)` helper.
- Tests: `tests/unit/test_form_handler.py` (hook matrix) + **route-map parity test**
  snapshotting `(endpoint, rule, methods)` for dashboard blueprints.
- Gate: full pytest green with zero existing-test edits.

### 2.2 CRUD registrar + orgs (≈ −450)
- New `src/webapp/dashboards/admin/crud.py` (~220 lines): frozen `CrudSpec` dataclass +
  `register_crud(bp, spec)` — endpoints/rules **identical to current names** so template
  `url_for` calls are untouched. Spec: slug, name, model, `id_param` (explicit — kwarg
  names vary), context_key, schemas, field projections, `edit_kwargs`/`create_kwargs`,
  `edit_context`/`create_context`, triggers, per-action permissions, `after_commit`,
  `actions`, endpoint_base. POSTs delegate to `handle_htmx_form_post`. Preserve not-found
  asymmetry (warning-div/200 GET form; `htmx_not_found` 404 POST/DELETE). Views get
  `__name__ = endpoint`. **Docstring rule: entity needing more than the spec stays bespoke.**
- Characterization/smoke tests first (CRUD routes have near-zero direct coverage).
- Migrate `orgs_routes.py`: 8 of 9 entities (mnemonic-code bespoke — GID logic).

### 2.3 facilities + resources (≈ −250)
- facilities: facility, allocation-type; add trivial panel-edit schema to
  `forms/facilities.py` first; panel-session stays bespoke (documented ORM cross-field check).
- resources: resource, resource-type, machine, queue (`after_commit=invalidate_queue_cache`
  — deletes the `attempted={}` sentinel at :559-587), disk-root where it fits.

### 2.4 Migrate 31 shape-C handlers (≈ −550..650)
Order: `project_members.py` → `user/blueprint.py` → `allocations/blueprint.py` →
`admin/projects_routes.py` (largest handlers last, most review care). Each → small
`HtmxFormHandler` subclass + 2-line route. Worked examples validated:
`_ProjectUpdateHandler` 71→51, `_EditAllocationHandler` 101→68, `_AddMemberHandler` 70→41.
- **Intended UI change:** ~29 forms move to inline field errors. Per-template check:
  fragment uses `form_fields.html` macros (accept `field_errors` since #336).
- Characterization tests per file before migrating.
- Note in commit messages: edit-allocation latent 500 fixed (projects_routes.py:2104-2114
  KeyError on non-numeric amount → error re-render); some handlers gain
  `'{error_prefix}: {e}'` over bare `str(e)`.
- Manual empty-string pre-processing (8 handlers) drops — `_strip_empty_strings` covers it.

### 2.5 §9 conformance (≈ −180..220)
- New `src/sam/schemas/forms/operational.py`: `CreateWallclockExemptionForm`,
  `AdminCreateWallclockExemptionForm`, `EditWallclockExemptionForm` (end-vs-start check
  stays in handler `clean()` — needs ORM); export from `forms/__init__.py`.
- Migrate exemption trio (`admin/blueprint.py:843, :977, :1099`) to handler subclasses
  sharing `_resources_with_queues()` (currently copy-pasted ×4).
- `ChangeProjectAdminForm` in `forms/user.py` for `project_members.py:181` — schema only,
  stays a function.
- Sweep remaining ~5 non-conformant POST handlers (grep strptime/float/int on POST paths);
  form-shaped → class, small alert-response → schema only. **Do not touch outage JS.**

### 2.6 Typeahead factory (≈ −100)
`register_typeahead(bp, *, slug, endpoint, permission, search, template, ctx_key, min_len,
limit, active_only_default)` in `utils/htmx.py`; migrate ~5–6 of 9 endpoints. The 4-way
context multiplexer (`admin/blueprint.py:657`) and facility-scoped project search (:772)
stay bespoke.

**Verify per commit:** full pytest; route-map parity green; grep gates trend to 0
(`except ValidationError` in dashboards/ 31→0; strptime/float ladders in POST handlers →0;
`_reload`/`_render_with_errors` closures 14→0); `tests/perf/test_route_query_counts.py`
baseline unchanged. PR-level manual: click-through per affected card — valid + invalid
submissions, inline field errors render, modals close, cards reload.

## Appendix — Phase-2 design detail (restart-safe; validated against real code)

### HtmxFormHandler core (`src/webapp/utils/form_handler.py`, new, ~170 ln)

```python
class FormError(Exception):
    """User-facing rejection from clean()/perform(); carries error strings."""
    def __init__(self, *errors):
        self.errors = [str(e) for e in errors]
        super().__init__('; '.join(self.errors))

class HtmxFormHandler:
    # declarative config (override per subclass)
    schema_cls = None; template = None
    partial = False
    error_prefix = 'Error'; success_message = 'Saved successfully.'
    success_redirect = None          # str | callable(result)
    exception_map = ()               # ((ExcType, msg_or_callable), ...)

    def __init__(self, **entities):  # route-loaded ORM objects (decorator-injected or looked up)
        for k, v in entities.items(): setattr(self, k, v)

    # hooks: form_input() -> request.form (pre-schema mutation);
    # load(raw) -> schema_cls().load(raw, partial=self.partial);
    # clean(data) -> data (PUT gating vs request.form, ORM cross-field checks; may raise FormError);
    # perform(data) (the write; runs INSIDE management_transaction; required);
    # after_commit(result) (cache invalidation);
    # context() -> {} (error re-render context: entity, dropdowns);
    # triggers(result) -> {} ; detail(result) -> None;
    # on_success(result) -> htmx_success_message(triggers, message, detail) or HX-Redirect
    #   when success_redirect set (flash + make_response('' , 200) + HX-Redirect header);
    # render_errors(errors, field_errors=None) -> render_template(template,
    #   errors=..., field_errors=..., form=request.form, **self.context())

    def handle(self):
        try:
            payload = self.clean(self.load(self.form_input()))
        except ValidationError as e:
            field_errors, form_level = self.schema_cls.split_errors(e.messages)
            return self.render_errors(form_level, field_errors)   # split ONLY (Ben's unify decision)
        except FormError as e:
            return self.render_errors(e.errors)
        try:
            with management_transaction(db.session):
                result = self.perform(payload)
        except (FormError, FKValidationError) as e:
            return self.render_errors(e.errors)
        except Exception as e:
            for exc_type, msg in self.exception_map:
                if isinstance(e, exc_type):
                    return self.render_errors([msg(e) if callable(msg) else msg])
            return self.render_errors([f'{self.error_prefix}: {e}'])
        self.after_commit(result)
        return self.on_success(result)
```

### Adapter (`handle_htmx_form_post` keeps EXACT signature; body → `_KwargFormHandler`)
Mapping: `do_action→perform`, `extra_context`+`context_fn`→`context()`,
`success_triggers`→`triggers()` (callable-or-dict), `success_detail`→`detail()`,
`success_message/success_redirect/error_prefix` → same-name attrs,
NEW optional kwarg `after_commit=None` (kills the `attempted={}` sentinel wrapper in
resources_routes.py queue-edit, currently ~:540-570 post-PR1). Gate: full pytest green
with ZERO existing-test edits. Also add `modal_triggers(*reload_events)` to utils/htmx.py
(fresh dict per call) replacing 4 `_X_TRIGGERS` constants + ~20 inline literals as touched.

### CrudSpec registrar (`src/webapp/dashboards/admin/crud.py`, new, ~220 ln)
Frozen dataclass fields: `slug, name, model, id_param (explicit — url kwarg names vary:
org_id/inst_id/group_id/...), context_key, edit_schema, create_schema,
edit_fields=(), create_fields=(), edit_kwargs=None, create_kwargs=None,
edit_context=None (obj->dict), create_context=None (()->dict, re-run on error re-render),
triggers={}, edit_permission/create_permission/delete_permission,
after_commit=None, actions=('edit','create','delete'), endpoint_base=None (default slug→snake)`.
`register_crud(bp, spec)` emits via `bp.add_url_rule` with endpoints IDENTICAL to current
view-fn names (`htmx_{base}_edit_form`, `_edit`, `_create_form`, `_create`, `_delete`) and
identical rule strings (`/htmx/{slug}-edit-form/<int:{id_param}>` etc.) — template url_for
untouched. Permissions applied as `login_required(require_permission(P)(view))`.
Preserve not-found asymmetry EXACTLY: edit-form GET → warning-div/200; POST/DELETE →
htmx_not_found (danger/404). POST bodies delegate to handle_htmx_form_post.
Set `view.__name__ = endpoint`. Docstring hard rule: needs-more-than-spec ⇒ stays bespoke.
Eligible: orgs 8/9 (mnemonic-code bespoke, GID logic), facilities 2-3/4 (panel-edit needs
trivial schema first in forms/facilities.py; panel-session bespoke — documented ORM
cross-field check), resources 4-5 (queue uses after_commit=invalidate_queue_cache from
api/v1/queue.py — landed in PR 1).

### Worked migration examples (validated; pre-PR1 line refs, re-grep after rebase)
- `htmx_project_update` projects_routes.py:770 (71→~51): partial=True; form_input strips
  GOVERNANCE_FIELDS unless can_edit_project_governance; perform = validate_fk_existence
  (4 FK tuples) + project.update(**data); triggers {'reloadEditProjectDetails': projcode}.
- `htmx_edit_allocation` projects_routes.py:2078 (101→~68): partial=True; clean() gates
  updates on request.form presence (amount/start_date truthy; end_date/description by key
  presence — empty end_date CLEARS), raises FormError('No changes provided.') if empty;
  perform: optional detach_allocation on break_inheritance=='true' then
  update_allocation(db.session, id, current_user.user_id, **updates); exception_map
  InheritingAllocationException → long detach-first message; context() has cheap-on-error
  frontier/parent_info. FIXES latent 500: old code KeyError'd on non-numeric amount
  (form_data={} then updates['amount']=form_data['amount']) — note in commit msg.
- `htmx_add_member` project_members.py:80 (70→~41): clean() looks up User by username,
  FormError if missing (stores self.member); perform → add_user_to_project;
  on_success OVERRIDE → htmx_success custom template + OOB _render_members_table,
  {'closeModal': 'addMemberModal'}.
- Micro-deltas to note per migrated handler: generic exceptions gain `'{prefix}: {e}'`
  where a few rendered bare str(e); 8 handlers' manual empty-string pre-drop is redundant
  (HtmxFormSchema._strip_empty_strings) and vanishes; error UI switches flatten→split
  (INTENDED, Ben approved) — check each template imports form_fields.html macros
  (they render field_errors since #336).

### Commit 2.5 schema specs (sam/schemas/forms/operational.py, new)
- CreateWallclockExemptionForm: queue_id Int req, start_date Date req, end_date via
  normalize_end_date req, time_limit_hours Float Range(min=0, min_inclusive=False),
  comment optional; date order via assert_date_range.
- AdminCreateWallclockExemptionForm(Create...): + user_id Int req (FK-picker variant).
- EditWallclockExemptionForm: end_date, time_limit_hours, comment; end-vs-ORM-start_date
  check lives in handler clean(). Migrate exemption trio admin/blueprint.py:843/:977/:1099
  (~278→~110+60); shared _resources_with_queues() (currently copy-pasted 4×).
- ChangeProjectAdminForm (forms/user.py) for project_members.py:181 — schema only,
  handler STAYS a function (28 ln, alert/table response).
- Sweep: `grep -rn 'strptime\|float(\|int(request' src/webapp/dashboards` on POST/PUT
  handlers only. DO NOT touch outage datetime-local JS (#337).

### Commit 2.6 typeahead factory
`register_typeahead(bp, *, slug, endpoint, permission, search, template, ctx_key,
min_len=2, limit=20, active_only_default=False)` in utils/htmx.py; migrate ~5-6 of 9
(groups, organizations ×2, impersonate alias...). Bespoke stay: 4-way context multiplexer
admin/blueprint.py:~657 and facility-scoped project search :~772.

### Phase-2 verification gates
- Route-map parity test (snapshot (endpoint, rule, methods) of dashboard blueprints) added
  in 2.1, green every commit. Characterization tests FIRST per migrated file (valid post /
  schema-error / domain-error / success-trigger header) — htmx_project_update, admin
  htmx_edit_allocation, htmx_add_member have authz-only coverage today.
- Grep gates → 0 by end: `except ValidationError` in dashboards/ (31); local `_reload`/
  `_render_with_errors` closures (14); strptime/float ladders in POST handlers.
- tests/perf/test_route_query_counts.py baseline unchanged.
- Test invocation: `source etc/config_env.sh && export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam' && pytest -q` (~85s; container samuel-mysql-test on 3307 already running). Full-suite tally after PR 1: 2915 passed / 25 skipped / 1 xfailed.

### Session logistics
- Branch: rebase `oo_refactor` onto `webapp_quick_wins` (PR 2 needs invalidate_queue_cache)
  or onto staging once #375 merges. PRs → staging. One PR, ordered commits (Ben's rule).
- Admin page-crawl harness (for any future admin-surface change):
  scratchpad/capture_admin_snapshot.py (run from a copy at tests/unit/zz_*.py so conftest
  fixtures resolve; ADMIN_SNAPSHOT_OUT env var; normalizes CSRF+nonce+navbar; `projects`
  custom view is run-to-run nondeterministic — exclude its hash).
- Open reviewer questions on PR #375: admin facility multi-select active_only=False
  (latent-bug suspicion); end-of-day end_date semantics on user-dashboard GET ranges.

## Net effect
≈ −2,400 to −2,700 LOC; one ~170-line class expressing the form lifecycle; one ~220-line
declarative CRUD registrar; #336 inline-field-error follow-up completed; one latent 500
fixed; dead code and false documentation removed. The function-based pieces that already
work (`handle_htmx_form_post` call sites, disk_scans, security decorators) deliberately
left alone.
