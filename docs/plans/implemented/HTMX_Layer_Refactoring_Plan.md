# HTMX Layer Refactoring Plan

## Context

The HTMX layer has grown into a maintenance bottleneck with 35 manual validation blocks across ~4,900 lines of route code, 55 templates with heavy boilerplate duplication, 8 search endpoints (several with duplicate names), and inline ORM queries that violate the project's centralized data-access pattern. This refactor addresses all 6 identified issues before the codebase grows further. The branch is `refactor_before_expansion`.

---

## Work Order (dependency-safe sequence)

| # | Item | Files Changed | Effort |
|---|------|--------------|--------|
| 1 | Consolidate success fragments | 6 templates → 1 + route updates | Low |
| 2 | Jinja macro for modal form boilerplate | new macro file + template updates | Low |
| 3 | Marshmallow form schemas | new `src/sam/schemas/forms/` package + 35 route blocks | Medium |
| 4 | Move inline queries to `sam.queries` | new query functions + 17 selectinload call sites | Medium |
| 5 | Consolidate user search endpoints | 4 route handlers + 4 template references | Low |
| 6 | Date parsing (subsumed by #3) | handled by Marshmallow `Date` field | —   |

---

## Item 1 — Consolidate Success Fragments

### Problem
6 near-identical `*_success_htmx.html` files in `templates/dashboards/admin/fragments/`.

### Fix

**Create** `templates/dashboards/fragments/htmx_success.html`:
```html
{# Generic HTMX success response for modal forms.
   Variables: message (str), detail (str, optional) #}
<div class="modal-body text-center py-4">
    <i class="fas fa-check-circle text-success fa-2x"></i>
    <p class="mt-2 text-success fw-semibold">{{ message }}</p>
    {% if detail %}
    <p class="text-muted mb-0">{{ detail }}</p>
    {% endif %}
</div>
```

**Update** `src/webapp/utils/htmx.py` — add a convenience wrapper:
```python
def htmx_success_message(triggers, message, detail=None):
    """Render the generic success fragment."""
    return htmx_success(
        'dashboards/fragments/htmx_success.html',
        triggers,
        message=message,
        detail=detail,
    )
```

**Remove** the 5 simple success templates (keep `add_member_success_htmx.html` — it has OOB swap logic that can't be genericized):
- `facility_edit_success_htmx.html`
- `organization_edit_success_htmx.html`
- `resource_edit_success_htmx.html`
- `exemption_success_htmx.html`
- `project_create_success_htmx.html` → pass `detail=f"{project.projcode} — {project.title}"`

**Update** the corresponding `htmx_success(...)` calls in the 4 admin route files to use `htmx_success_message(...)`.

---

## Item 2 — Jinja Macro for Modal Form Boilerplate

### Problem
Every `create_*_form_htmx.html` and `edit_*_form_htmx.html` duplicates identical `<form hx-*>`, `modal-body` error block, and `modal-footer` HTML.

### Fix

**Create** `templates/dashboards/fragments/modal_form.html`:
```jinja
{% macro htmx_form(post_url, target, submit_text, submit_icon='save', is_edit=False) %}
<form hx-post="{{ post_url }}"
      hx-target="{{ target }}"
      hx-swap="innerHTML"
      hx-disabled-elt="find button[type='submit']">

    <div class="modal-body">
        {{ caller() }}

        {% if errors %}
        <div class="alert alert-danger mt-2 mb-0">
            {% for error in errors %}
            <p class="mb-0"><i class="fas fa-exclamation-circle"></i> {{ error }}</p>
            {% endfor %}
        </div>
        {% endif %}
    </div>

    <div class="modal-footer">
        <button type="button" class="btn btn-secondary"
                data-bs-dismiss="modal">Cancel</button>
        <button type="submit" class="btn btn-success">
            <i class="fas fa-{{ submit_icon }}"></i> {{ submit_text }}
        </button>
    </div>
</form>
{% endmacro %}
```

**Update each template** to import and use the macro, replacing the duplicated wrapper HTML:
```jinja
{% from 'dashboards/fragments/modal_form.html' import htmx_form %}

{% call htmx_form(
    url_for('admin_dashboard.htmx_organization_create'),
    '#createOrganizationFormContainer',
    'Create Organization',
    submit_icon='plus'
) %}
    {# — only the unique field HTML lives here — #}
    <div class="mb-3">
        <label class="form-label">Name <span class="text-danger">*</span></label>
        <input type="text" class="form-control" name="name"
               value="{{ form.get('name', '') if form else '' }}">
    </div>
{% endcall %}
```

Applies to all 47 admin + 8 user HTMX form templates (the few with OOB swaps use the macro for the form wrapper only, with OOB divs outside the `{% call %}` block).

---

## Item 3 — Marshmallow Form Schemas

### Design Decision
Use standalone `marshmallow.Schema` (NOT `SQLAlchemyAutoSchema`) — form validation is a separate concern from ORM serialization. Keeps `src/sam/schemas/` (API) and `src/sam/schemas/forms/` (form input) cleanly decoupled.

### New Files

**`src/sam/schemas/forms/__init__.py`** — base class + exports:
```python
from marshmallow import Schema, ValidationError
import marshmallow.fields as f
import marshmallow.validate as v

class HtmxFormSchema(Schema):
    """Base for all HTMX form validation schemas.

    Usage in route:
        schema = CreateOrganizationForm()
        try:
            data = schema.load(request.form)
        except ValidationError as e:
            errors = schema.flatten_errors(e.messages)
            return render_template(..., errors=errors, form=request.form)
    """

    class Meta:
        # Empty strings are treated as missing (→ None or default)
        unknown = EXCLUDE  # ignore extra form fields like CSRF tokens

    @staticmethod
    def flatten_errors(messages: dict) -> list[str]:
        """Convert {'field': ['msg']} to ['Field: msg'] list for templates."""
        out = []
        for field, msgs in messages.items():
            label = field.replace('_', ' ').title()
            for msg in (msgs if isinstance(msgs, list) else [msgs]):
                out.append(f'{label}: {msg}')
        return out
```

**`src/sam/schemas/forms/facilities.py`**:
```python
class EditFacilityForm(HtmxFormSchema):
    description = f.Str(required=True, validate=v.Length(min=1, max=255))
    fair_share_percentage = f.Float(load_default=None,
                                     validate=v.Range(min=0, max=100))
    active = f.Bool(load_default=False)

class CreatePanelForm(HtmxFormSchema):
    name = f.Str(required=True, validate=v.Length(min=1, max=100))
    start_date = f.Date('%Y-%m-%d', required=True)
    end_date = f.Date('%Y-%m-%d', required=True)
    panel_meeting_date = f.Date('%Y-%m-%d', load_default=None)

    @validates_schema
    def validate_date_range(self, data, **kwargs):
        if data.get('end_date') and data.get('start_date'):
            if data['end_date'] <= data['start_date']:
                raise ValidationError('End date must be after start date.', 'end_date')
```

**`src/sam/schemas/forms/orgs.py`** — ~15 form schemas for all org/institution/contract entities

**`src/sam/schemas/forms/resources.py`** — ~7 form schemas for resources/machines/queues

**`src/sam/schemas/forms/projects.py`** — ~3 form schemas for project forms

**`src/sam/schemas/forms/user.py`** — ~2 form schemas for user-facing allocation/member forms

### Route Handler Pattern (before → after)

**Before** (20 lines, facilities_routes.py ~82-105):
```python
errors = []
description = request.form.get('description', '').strip()
fair_share_str = request.form.get('fair_share_percentage', '').strip()
active = bool(request.form.get('active'))
if not description:
    errors.append('Description is required.')
fair_share_percentage = None
if fair_share_str:
    try:
        fair_share_percentage = float(fair_share_str)
        if not (0 <= fair_share_percentage <= 100):
            errors.append('Fair share percentage must be between 0 and 100.')
    except ValueError:
        errors.append('Fair share percentage must be a number.')
if errors:
    return render_template(..., errors=errors, form=request.form)
```

**After** (7 lines):
```python
from sam.schemas.forms.facilities import EditFacilityForm

schema = EditFacilityForm()
try:
    data = schema.load(request.form)
except ValidationError as e:
    errors = EditFacilityForm.flatten_errors(e.messages)
    return render_template(..., errors=errors, form=request.form)
# data is now a clean dict: {'description': '...', 'fair_share_percentage': 45.0, 'active': True}
```

### Date Handling
- Use `marshmallow.fields.Date('%Y-%m-%d')` — coerces to `datetime.date`
- For end-dates needing `23:59:59` convention: use `@post_load` to call `parse_input_end_date()` from `src/webapp/api/helpers.py`
- This replaces all `datetime.strptime(s, '%Y-%m-%d')` calls in HTMX routes

---

## Item 4 — Move Inline Queries to `sam.queries`

### Problem
17 `selectinload` chains in admin route files violate the centralized data-access pattern.

### Fix

**Identify heavy query sites** (from exploration):
- `orgs_routes.py` lines ~71-88: deep `InstitutionType → institutions → users → accounts` load
- `resources_routes.py`: resource hierarchy queries
- `facilities_routes.py`: facility + panel + panel session loads

**Add new query functions** to appropriate existing modules (or new files if needed):

`src/sam/queries/lookups.py` — add simple admin lookup functions:
```python
def get_institution_type_tree(session):
    """Load all institution types with their institutions and members.
    Used by admin orgs dashboard."""
    return session.query(InstitutionType).options(
        selectinload(InstitutionType.institutions)
            .selectinload(Institution.users)
            .selectinload(UserInstitution.user)
            .lazyload(User.accounts),
        selectinload(InstitutionType.institutions)
            .selectinload(Institution.users)
            .selectinload(UserInstitution.user)
            .lazyload(User.email_addresses),
    ).order_by(InstitutionType.type).all()
```

Create `src/sam/queries/admin.py` if the inline queries don't fit cleanly into existing modules (facilities tree, resource hierarchy, etc.).

**Update** `sam/queries/__init__.py` to export new functions.

**Replace** inline queries in route files with the extracted functions:
```python
# Before (orgs_routes.py ~71-88):
institution_types = db.session.query(InstitutionType).options(
    selectinload(...)...
).order_by(InstitutionType.type).all()

# After:
from sam.queries import get_institution_type_tree
institution_types = get_institution_type_tree(db.session)
```

---

## Item 5 — Consolidate User Search Endpoints

### Problem
4 near-identical user search endpoints returning slightly different HTML:
- `admin/blueprint.py`: `/htmx/search-users-impersonate`
- `admin/resources_routes.py`: `/htmx/search-users`
- `orgs_routes.py`: `/htmx/search-users-for-org`
- `user/blueprint.py`: `/htmx/search-users`

All call `sam.queries.users.search_users_by_pattern`, differing only in the result template rendered.

### Fix

**Create** a unified endpoint in `admin/blueprint.py`:
```python
@bp.route('/htmx/search/users')
def htmx_search_users():
    """Unified user search. context param controls result template.

    ?context=fk       → user_search_results_fk_htmx.html (FK picker badges)
    ?context=member   → user_search_results_htmx.html (project member add)
    ?context=impersonate → impersonate_search_results_htmx.html
    """
    q = request.args.get('q', '').strip()
    context = request.args.get('context', 'fk')

    template_map = {
        'fk':          'dashboards/admin/fragments/user_search_results_fk_htmx.html',
        'member':      'dashboards/user/fragments/user_search_results_htmx.html',
        'impersonate': 'dashboards/admin/fragments/impersonate_search_results_htmx.html',
    }
    template = template_map.get(context, template_map['fk'])

    if len(q) < 2:
        return ''
    users = search_users_by_pattern(db.session, q)
    return render_template(template, users=users, q=q)
```

**Update** templates that reference the old search endpoints to use `/htmx/search/users?context=...`.

**Remove** the 3 duplicate endpoint handlers (keep the consolidated one).

---

## Critical Files

| File | Action |
|------|--------|
| `src/webapp/utils/htmx.py` | Add `htmx_success_message()` |
| `templates/dashboards/fragments/htmx_success.html` | **Create** |
| `templates/dashboards/fragments/modal_form.html` | **Create** macro |
| `src/sam/schemas/forms/__init__.py` | **Create** |
| `src/sam/schemas/forms/facilities.py` | **Create** |
| `src/sam/schemas/forms/orgs.py` | **Create** |
| `src/sam/schemas/forms/resources.py` | **Create** |
| `src/sam/schemas/forms/projects.py` | **Create** |
| `src/sam/schemas/forms/user.py` | **Create** |
| `src/sam/queries/lookups.py` | Extend with admin tree queries |
| `src/sam/queries/__init__.py` | Export new query functions |
| `src/webapp/dashboards/admin/facilities_routes.py` | Replace 6 validation blocks |
| `src/webapp/dashboards/admin/orgs_routes.py` | Replace 17 validation blocks + 7 inline queries |
| `src/webapp/dashboards/admin/resources_routes.py` | Replace 7 validation blocks + inline queries |
| `src/webapp/dashboards/admin/projects_routes.py` | Replace 1 validation block |
| `src/webapp/dashboards/admin/blueprint.py` | Add unified search endpoint, remove duplicates |
| `src/webapp/dashboards/user/blueprint.py` | Replace 2 validation blocks |
| All 47 admin + 8 user `*_htmx.html` form templates | Apply macro (item 2) |

---

## Verification

1. **Unit tests**: `pytest tests/ --no-cov` — all existing tests must pass (380 baseline)
2. **New tests**: Add `tests/unit/test_form_schemas.py` to test Marshmallow form schemas in isolation (valid input, required fields, date coercion, cross-field validation)
3. **Manual**: Start webapp via `docker compose up`, exercise each modal form (create + edit + error case) for: facilities, panels, organizations, institutions, resources, machines, queues, projects
4. **Search**: Verify user autocomplete still works in FK pickers, member add modal, and impersonation
5. **Success state**: Confirm success fragments render and fire `HX-Trigger` headers to close modals and reload cards
