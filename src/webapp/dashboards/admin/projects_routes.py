"""
Admin dashboard — Project management routes.

Covers: Project creation (Phase A).  Edit/allocation management (Phase B).
"""

from datetime import datetime

from flask import render_template, request, redirect, url_for
from webapp.utils.htmx import htmx_success, htmx_success_message, handle_htmx_form_post
from webapp.utils.fk_validation import FKValidationError, validate_fk_existence
from flask_login import login_required, current_user

from webapp.extensions import db
from flask import abort
from webapp.utils.rbac import (
    require_permission, require_permission_any_facility,
    has_permission, has_permission_for_facility,
    Permission, user_facility_scope,
)
from webapp.api.access_control import require_project_permission
from webapp.utils.project_permissions import (
    can_edit_project_governance,
    can_edit_allocations,
)
from sam.manage import management_transaction

from .blueprint import bp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project_form_data(form=None) -> dict:
    """Load form option lists shared by create (and later edit) forms.

    When *form* is provided (a re-render after validation errors) the
    panel and alloc-type lists are pre-filtered so the selects repopulate
    without requiring an htmx round-trip.
    """
    from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup
    from sam.accounting.allocations import AllocationType
    from sam.resources.facilities import Facility, Panel
    from sam.core.organizations import MnemonicCode

    areas = (
        db.session.query(AreaOfInterest)
        .filter(AreaOfInterest.is_active)
        .order_by(AreaOfInterest.area_of_interest)
        .all()
    )
    aoi_groups = (
        db.session.query(AreaOfInterestGroup)
        .order_by(AreaOfInterestGroup.name)
        .all()
    )
    facilities_q = (
        db.session.query(Facility)
        .filter(Facility.is_active)
        .order_by(Facility.facility_name)
    )
    # Facility-scoped users only ever see (and can submit) facilities
    # they have CREATE_PROJECTS on. None → no restriction.
    allowed = user_facility_scope(current_user, Permission.CREATE_PROJECTS)
    if allowed is not None:
        facilities_q = facilities_q.filter(Facility.facility_name.in_(allowed))
    facilities = facilities_q.all()
    mnemonics = (
        db.session.query(MnemonicCode)
        .filter(MnemonicCode.is_active)
        .order_by(MnemonicCode.code)
        .all()
    )

    # Pre-populate dependent selects on error re-render
    panels_for_facility = []
    alloc_types_for_panel = []
    if form:
        fac_id_str = form.get('facility_id', '').strip()
        pan_id_str = form.get('panel_id', '').strip()
        if fac_id_str:
            try:
                panels_for_facility = (
                    db.session.query(Panel)
                    .filter(Panel.facility_id == int(fac_id_str), Panel.is_active)
                    .order_by(Panel.panel_name)
                    .all()
                )
            except (ValueError, TypeError):
                pass
        if pan_id_str:
            try:
                alloc_types_for_panel = (
                    db.session.query(AllocationType)
                    .filter(AllocationType.panel_id == int(pan_id_str), AllocationType.is_active)
                    .order_by(AllocationType.allocation_type)
                    .all()
                )
            except (ValueError, TypeError):
                pass

    return dict(
        areas=areas,
        aoi_groups=aoi_groups,
        facilities=facilities,
        mnemonics=mnemonics,
        panels_for_facility=panels_for_facility,
        alloc_types_for_panel=alloc_types_for_panel,
    )


# ---------------------------------------------------------------------------
# Create Project
# ---------------------------------------------------------------------------


@bp.route('/htmx/project-create-form')
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_project_create_form():
    """Return the project create form fragment (loaded into modal on button click)."""
    return render_template(
        'dashboards/admin/fragments/create_project_form_htmx.html',
        **_project_form_data(),
    )


@bp.route('/htmx/panels-for-facility')
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_panels_for_facility():
    """Return <option> elements for the Panel select, filtered by facility.

    Called via hx-get when the Facility select changes.
    """
    from sam.resources.facilities import Facility, Panel

    facility_id_str = request.args.get('facility_id', '').strip()
    if not facility_id_str:
        return '<option value="">— Select facility first —</option>'
    try:
        facility_id_int = int(facility_id_str)
    except (ValueError, TypeError):
        return '<option value="">— Select facility first —</option>'

    # Facility-scope gate: a user with CREATE_PROJECTS only on WNA must
    # not be able to discover NCAR panels by forging facility_id. Deny
    # at the source rather than filter the returned list silently.
    facility = db.session.get(Facility, facility_id_int)
    if facility is None:
        return '<option value="">— Select facility first —</option>'
    if not has_permission_for_facility(
        current_user, Permission.CREATE_PROJECTS, facility.facility_name,
    ):
        abort(403)

    panels = (
        db.session.query(Panel)
        .filter(Panel.facility_id == facility_id_int, Panel.is_active)
        .order_by(Panel.panel_name)
        .all()
    )

    return render_template(
        'dashboards/admin/fragments/panel_options_htmx.html',
        panels=panels,
        selected_id=None,
    )


@bp.route('/htmx/alloc-types-for-panel')
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_alloc_types_for_panel():
    """Return <option> elements for the AllocationType select, filtered by panel.

    Called via hx-get when the Panel select changes.
    """
    from sam.accounting.allocations import AllocationType
    from sam.resources.facilities import Panel

    panel_id_str = request.args.get('panel_id', '').strip()
    if not panel_id_str:
        return '<option value="">— None —</option>'
    try:
        panel_id_int = int(panel_id_str)
    except (ValueError, TypeError):
        return '<option value="">— None —</option>'

    # Resolve the panel's facility for the scope check — a scoped user
    # must not be able to harvest allocation-type options from facilities
    # outside their grant by probing panel_ids directly.
    panel = db.session.get(Panel, panel_id_int)
    if panel is None:
        return '<option value="">— None —</option>'
    if not has_permission_for_facility(
        current_user, Permission.CREATE_PROJECTS,
        panel.facility.facility_name if panel.facility else None,
    ):
        abort(403)

    alloc_types = (
        db.session.query(AllocationType)
        .filter(AllocationType.panel_id == panel_id_int, AllocationType.is_active)
        .order_by(AllocationType.allocation_type)
        .all()
    )

    return render_template(
        'dashboards/admin/fragments/alloc_type_options_htmx.html',
        alloc_types=alloc_types,
        selected_id=None,
    )


@bp.route('/htmx/org-search-for-project')
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_org_search_for_project():
    """Search organizations for the project create form FK picker.

    Returns an HTML fragment with .fk-search-result items whose click handler
    (defined in the form template) sets the hidden ``organization_id`` input.
    """
    from sam.core.organizations import Organization

    query = request.args.get('q', '').strip()
    if len(query) < 2:
        return ''

    orgs = (
        db.session.query(Organization)
        .filter(
            Organization.is_active,
            Organization.name.ilike(f'%{query}%') | Organization.acronym.ilike(f'%{query}%')
        )
        .order_by(Organization.name)
        .limit(15)
        .all()
    )

    return render_template(
        'dashboards/admin/fragments/org_search_results_fk_htmx.html',
        orgs=orgs,
    )


@bp.route('/htmx/contract-search-for-project')
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_contract_search_for_project():
    """Search contracts for the project create form FK picker.

    Returns an HTML fragment with .fk-search-result items whose click handler
    (defined in the form template) sets the hidden ``contract_id`` input.
    """
    from sam.projects.contracts import Contract

    query = request.args.get('q', '').strip()
    if len(query) < 2:
        return ''

    contracts = (
        db.session.query(Contract)
        .filter(
            Contract.contract_number.ilike(f'%{query}%') | Contract.title.ilike(f'%{query}%')
        )
        .order_by(Contract.contract_number)
        .limit(10)
        .all()
    )

    return render_template(
        'dashboards/admin/fragments/contract_search_results_fk_htmx.html',
        contracts=contracts,
    )


@bp.route('/htmx/project-search-for-parent')
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_project_search_for_parent():
    """Search projects for use as parent FK in the create form.

    Returns an HTML fragment with .fk-search-result items whose click handler
    (defined in the form template) sets the hidden ``parent_id`` input.
    """
    from sam.queries.projects import search_projects_by_code_or_title

    query = request.args.get('q', '').strip()
    if len(query) < 1:
        return ''

    projects = search_projects_by_code_or_title(
        db.session, query, active=True
    )[:10]

    return render_template(
        'dashboards/admin/fragments/project_search_results_fk_htmx.html',
        projects=projects,
    )


@bp.route('/htmx/project-next-projcode')
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_project_next_projcode():
    """Compute and return a preview of the next available projcode.

    Called via hx-get when the user changes the Facility or Mnemonic selects
    in "auto-generate" mode.  Returns a plain-text projcode string or an empty
    string if the combination is incomplete / invalid.
    """
    from sam.projects.projects import next_projcode

    facility_id_str = request.args.get('facility_id', '').strip()
    mnemonic_id_str = request.args.get('mnemonic_code_id', '').strip()

    if not facility_id_str or not mnemonic_id_str:
        return ''

    try:
        code = next_projcode(
            db.session,
            facility_id=int(facility_id_str),
            mnemonic_code_id=int(mnemonic_id_str),
        )
        return code
    except (ValueError, TypeError, Exception):
        return ''


@bp.route('/htmx/project-create', methods=['POST'])
@login_required
@require_permission_any_facility(Permission.CREATE_PROJECTS)
def htmx_project_create():
    """Validate form and create a new project."""
    from sam.projects.projects import Project
    from sam.projects.areas import AreaOfInterest
    from sam.projects.contracts import Contract, ProjectContract
    from sam.core.users import User
    from sam.core.organizations import Organization, ProjectOrganization
    from sam.resources.facilities import Facility, Panel
    from sam.accounting.allocations import AllocationType
    from sam.schemas.forms import CreateProjectForm

    def _do_action(data):
        validate_fk_existence(
            db.session,
            (Facility, data['facility_id'], 'facility'),
            (Panel, data['panel_id'], 'panel'),
            (User, data['project_lead_user_id'], 'project lead'),
            (User, data.get('project_admin_user_id'), 'project admin'),
            (AreaOfInterest, data['area_of_interest_id'], 'area of interest'),
            (AllocationType, data.get('allocation_type_id'), 'allocation type'),
            (Project, data.get('parent_id'), 'parent project'),
            (Contract, data.get('contract_id'), 'contract'),
            (Organization, data.get('organization_id'), 'organization'),
        )
        # Facility-scope gate: the decorator-level CREATE_PROJECTS check
        # only knows whether the user holds the permission anywhere.
        # Scoped users must additionally be creating inside a facility
        # they have been granted. FK existence is already validated
        # above, so the lookup is guaranteed to resolve.
        chosen_facility = db.session.get(Facility, data['facility_id'])
        if not has_permission_for_facility(
            current_user, Permission.CREATE_PROJECTS, chosen_facility.facility_name,
        ):
            abort(403)
        if Project.get_by_projcode(db.session, data['projcode']):
            raise FKValidationError(
                [f'Project code "{data["projcode"]}" is already in use.']
            )

        # facility_id / panel_id are existence-only; Project.create() derives
        # the effective facility/panel from allocation_type_id.
        project_kwargs = {
            k: v for k, v in data.items()
            if k not in ('facility_id', 'panel_id',
                         'contract_id', 'organization_id')
        }
        project = Project.create(db.session, **project_kwargs)
        if data.get('contract_id'):
            ProjectContract.create(
                db.session,
                project_id=project.project_id,
                contract_id=data['contract_id'],
            )
        if data.get('organization_id'):
            ProjectOrganization.create(
                db.session,
                project_id=project.project_id,
                organization_id=data['organization_id'],
            )
        return project

    return handle_htmx_form_post(
        schema_cls=CreateProjectForm,
        template='dashboards/admin/fragments/create_project_form_htmx.html',
        context_fn=lambda: _project_form_data(form=request.form),
        success_triggers=lambda project: {
            'closeActiveModal': {},
            'loadNewProject': project.projcode,
        },
        success_message='Project created successfully.',
        success_detail=lambda project: f'{project.projcode} — {project.title}',
        error_prefix='Error creating project',
        do_action=_do_action,
    )


# ---------------------------------------------------------------------------
# Edit Project (Phase B)
# ---------------------------------------------------------------------------

@bp.route('/project/<projcode>/edit')
@login_required
@require_project_permission(Permission.EDIT_PROJECTS)
def edit_project_page(project):
    """Full edit-project page (not a modal).

    Renders a three-tab page: Details | Allocations | Members.
    The Allocations tab is lazy-loaded on first click.

    Access: system EDIT_PROJECTS, or project lead, or project admin
    (``can_access_edit_project_page``). Non-admin stewards see every
    tab but a limited edit surface gated by ``can_edit_governance``.
    """
    from sam.queries.dashboard import get_project_dashboard_data

    project_data = get_project_dashboard_data(db.session, project.projcode)

    # Reverse-lookup facility_id / panel_id for cascading dropdown pre-population.
    current_facility_id = None
    current_panel_id = None
    if project.allocation_type and project.allocation_type.panel:
        current_panel_id = project.allocation_type.panel_id
        if project.allocation_type.panel.facility:
            current_facility_id = project.allocation_type.panel.facility_id

    # Feed the current facility_id / panel_id into _project_form_data so it
    # pre-loads the cascading Panel and Allocation Type option lists — the same
    # mechanism used by the create form on validation-error re-renders.
    pre_fill = {}
    if current_facility_id:
        pre_fill['facility_id'] = str(current_facility_id)
    if current_panel_id:
        pre_fill['panel_id'] = str(current_panel_id)
    form_data = _project_form_data(form=pre_fill or None)

    can_edit_governance = can_edit_project_governance(current_user, project)
    from webapp.utils.rbac import has_permission_any_facility
    can_access_admin = has_permission_any_facility(current_user, Permission.ACCESS_ADMIN_DASHBOARD)

    return render_template(
        'dashboards/admin/edit_project.html',
        project=project,
        project_data=project_data,
        current_facility_id=current_facility_id,
        current_panel_id=current_panel_id,
        can_edit_governance=can_edit_governance,
        can_access_admin=can_access_admin,
        **form_data,
    )


GOVERNANCE_FIELDS = frozenset({
    'facility_id', 'panel_id', 'allocation_type_id',
    'project_lead_user_id', 'project_admin_user_id',
    'active', 'charging_exempt', 'ext_alias',
})


@bp.route('/htmx/project-update/<projcode>', methods=['POST'])
@login_required
@require_project_permission(Permission.EDIT_PROJECTS)
def htmx_project_update(project):
    """Validate and apply project metadata updates.

    Access: system EDIT_PROJECTS, or project lead/admin. Non-admin
    stewards can only change metadata fields (title / abstract /
    area_of_interest_id); governance-field submissions are stripped
    server-side before validation.
    """
    from sam.projects.areas import AreaOfInterest
    from sam.accounting.allocations import AllocationType
    from sam.core.users import User
    from sam.schemas.forms import EditProjectForm
    from marshmallow import ValidationError

    current_facility_id = None
    current_panel_id = None
    if project.allocation_type and project.allocation_type.panel:
        current_panel_id = project.allocation_type.panel_id
        if project.allocation_type.panel.facility:
            current_facility_id = project.allocation_type.panel.facility_id

    # Governance fields are admin-only. When a non-admin steward submits,
    # drop those keys before marshmallow sees them. Defense-in-depth: the
    # template renders them as read-only text for non-admins (so browsers
    # don't submit them), but a crafted curl request could include them.
    if can_edit_project_governance(current_user, project):
        form_input = request.form
    else:
        form_input = {k: v for k, v in request.form.items()
                      if k not in GOVERNANCE_FIELDS}

    def _render_with_errors(errs):
        return render_template(
            'dashboards/admin/fragments/edit_project_details_htmx.html',
            project=project,
            current_facility_id=current_facility_id,
            current_panel_id=current_panel_id,
            can_edit_governance=can_edit_project_governance(current_user, project),
            errors=errs,
            form=request.form,
            **_project_form_data(form=request.form),
        )

    try:
        data = EditProjectForm().load(form_input, partial=True)
    except ValidationError as e:
        return _render_with_errors(EditProjectForm.flatten_errors(e.messages))

    try:
        with management_transaction(db.session):
            validate_fk_existence(
                db.session,
                (User, data.get('project_lead_user_id'), 'project lead'),
                (User, data.get('project_admin_user_id'), 'project admin'),
                (AreaOfInterest, data.get('area_of_interest_id'), 'area of interest'),
                (AllocationType, data.get('allocation_type_id'), 'allocation type'),
            )
            project.update(**data)
    except FKValidationError as e:
        return _render_with_errors(e.errors)
    except Exception as e:  # noqa: BLE001
        return _render_with_errors([f'Error updating project: {e}'])

    return htmx_success_message(
        {'reloadEditProjectDetails': project.projcode},
        'Project updated successfully.',
        detail=f'{project.projcode} — {project.title}',
    )


@bp.route('/htmx/project-allocation-tree/<projcode>')
@login_required
@require_project_permission(Permission.EDIT_PROJECTS)
def htmx_project_allocation_tree(project):
    """Lazy-loaded allocation tree for the Edit Project Allocations tab.

    Builds a {projcode: {resource_name: resource_dict}} lookup for all active
    nodes in the project tree, groups resources by resource type into tabs, and
    renders accordion cards within each tab.

    Accepts an optional ?active_at=YYYY-MM-DD query parameter to show
    allocations as they were (or will be) on a given date.  Defaults to today.
    """
    from collections import OrderedDict
    from datetime import datetime
    from sam.queries.dashboard import _build_project_resources_data

    # Parse optional active_at date; default to today.
    active_at_str = request.args.get('active_at', '').strip()
    try:
        active_at = datetime.strptime(active_at_str, '%Y-%m-%d') if active_at_str else None
    except ValueError:
        active_at = None
    now_str = datetime.now().strftime('%Y-%m-%d')
    active_at_str = active_at.strftime('%Y-%m-%d') if active_at else now_str

    root = project.get_root() if hasattr(project, 'get_root') else project

    # Always show active projects only in the allocation tree.
    all_nodes = [n for n in ([root] + root.get_descendants()) if n.active]
    resources_by_projcode = {}
    for node in all_nodes:
        node_resources = _build_project_resources_data(node, active_at=active_at)
        resources_by_projcode[node.projcode] = {
            r['resource_name']: r for r in node_resources
        }

    # Build resource_type lookup from the data already loaded.
    resource_type_lookup = {}  # {resource_name: resource_type_string}
    for res_dict in resources_by_projcode.values():
        for rname, rdata in res_dict.items():
            if rname not in resource_type_lookup:
                resource_type_lookup[rname] = rdata.get('resource_type', 'HPC')

    # Group resources into display tabs (ordered; skip empty tabs).
    _TAB_CONFIG = [
        ('HPC / DAV',    ['HPC', 'DAV']),
        ('Disk',         ['DISK']),
        ('Archive',      ['ARCHIVE']),
        ('Data Access',  ['DATA ACCESS']),
    ]
    resources_by_tab = OrderedDict()
    for tab_label, rtypes in _TAB_CONFIG:
        names = sorted(
            rname for rname, rtype in resource_type_lookup.items()
            if rtype in rtypes
        )
        if names:
            resources_by_tab[tab_label] = {
                'names': names,
                'rtypes': rtypes,
                'rtypes_str': ','.join(rtypes),
            }

    # Exchange eligibility: a resource is eligible when at least two
    # distinct DESCENDANT projects (NOT the edit-page project itself)
    # hold a dedicated (non-inheriting) allocation for it. The root is
    # never a valid exchange endpoint — see ``_exchange_candidates``.
    # Computed from the data already loaded above; no extra DB trips.
    can_exchange = can_edit_allocations(current_user, project)
    descendant_projcodes = {
        p.projcode for p in project.get_descendants(include_self=False)
        if p.active
    }
    exchange_eligible_resources = set()
    if can_exchange:
        per_resource_counts = {}  # resource_name → count of dedicated allocs among descendants
        for pc in descendant_projcodes:
            for rname, rdata in resources_by_projcode.get(pc, {}).items():
                if rdata.get('allocation_id') and not rdata.get('is_inheriting'):
                    per_resource_counts[rname] = per_resource_counts.get(rname, 0) + 1
        exchange_eligible_resources = {
            rname for rname, count in per_resource_counts.items() if count >= 2
        }

    # Resolve resource_id by name so the Exchange button's URL can target
    # /htmx/exchange-allocation-form/<projcode>/<resource_id>. Only needed
    # when exchange eligibility is non-empty.
    resource_id_by_name = {}
    if exchange_eligible_resources:
        from sam.resources.resources import Resource
        resource_id_by_name = {
            r.resource_name: r.resource_id
            for r in db.session.query(Resource)
            .filter(Resource.resource_name.in_(exchange_eligible_resources))
            .all()
        }

    return render_template(
        'dashboards/admin/fragments/project_allocation_tree_htmx.html',
        root=root,
        projcode=project.projcode,
        resources_by_tab=resources_by_tab,
        resources_by_projcode=resources_by_projcode,
        active_at=active_at_str,
        now_str=now_str,
        can_edit_governance=can_edit_project_governance(current_user, project),
        can_exchange=can_exchange,
        exchange_eligible_resources=exchange_eligible_resources,
        resource_id_by_name=resource_id_by_name,
    )


@bp.route('/htmx/add-allocation-form/<projcode>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_add_allocation_form(projcode):
    """Return the add-allocation sub-form (loaded into modal on button click)."""
    import calendar
    from sam.projects.projects import Project
    from sam.resources.resources import Resource

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-warning">Project not found.</div>'

    # Resources already linked to this project (via any account, even deleted).
    linked_resource_ids = {
        acct.resource_id for acct in project.accounts
    }

    # Offer all active resources not yet linked to this project.
    available_resources = (
        db.session.query(Resource)
        .filter(Resource.is_active)
        .order_by(Resource.resource_name)
        .all()
    )
    available_resources = [r for r in available_resources
                           if r.resource_id not in linked_resource_ids]

    active_descendants = [d for d in project.get_descendants() if d.active]

    # Default end date = last day of the same month, one year out.
    # (E.g. today 2026-04-13 → default end 2027-04-30.) User can override.
    now = datetime.now()
    target_year = now.year + 1
    last_day = calendar.monthrange(target_year, now.month)[1]
    default_end_date = f'{target_year:04d}-{now.month:02d}-{last_day:02d}'

    return render_template(
        'dashboards/admin/fragments/add_allocation_form_htmx.html',
        project=project,
        available_resources=available_resources,
        today=now.strftime('%Y-%m-%d'),
        default_end_date=default_end_date,
        project_has_children=project.has_children,
        child_count=len(active_descendants),
    )


@bp.route('/htmx/add-allocation/<projcode>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_add_allocation(projcode):
    """Create a new account + allocation for the project."""
    from sam.projects.projects import Project
    from sam.resources.resources import Resource
    from sam.manage.allocations import create_allocation
    from sam.schemas.forms import AddAllocationForm
    from marshmallow import ValidationError

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found.</div>', 404

    errors = []

    # Pre-process: drop empty strings (marshmallow would reject '' for Int/Float/Date);
    # inject explicit False for unchecked checkbox.
    data = {k: v for k, v in request.form.items() if v != ''}
    data['apply_to_subprojects'] = 'apply_to_subprojects' in request.form

    try:
        form_data = AddAllocationForm().load(data)
    except ValidationError as e:
        errors.extend(AddAllocationForm.flatten_errors(e.messages))
        form_data = {}

    # FK existence check — requires DB access, stays in the route
    resource = None
    if form_data.get('resource_id'):
        resource = db.session.get(Resource, form_data['resource_id'])
        if not resource:
            errors.append('Selected resource does not exist.')

    # Coerce start_date (date) → datetime; end_date is already datetime-or-None via post_load
    start_date = (
        datetime.combine(form_data['start_date'], datetime.min.time())
        if form_data.get('start_date') else None
    )
    end_date = form_data.get('end_date')
    amount = form_data.get('amount')
    description = form_data.get('description')
    apply_to_subprojects = form_data.get('apply_to_subprojects', False)

    def _reload_add_form(extra_errors=None):
        import calendar
        from sam.resources.resources import Resource as R
        linked_ids = {a.resource_id for a in project.accounts}
        available = (
            db.session.query(R)
            .filter(R.is_active)
            .order_by(R.resource_name)
            .all()
        )
        available = [r for r in available if r.resource_id not in linked_ids]
        active_desc = [d for d in project.get_descendants() if d.active]
        now = datetime.now()
        last_day = calendar.monthrange(now.year + 1, now.month)[1]
        default_end = f'{now.year + 1:04d}-{now.month:02d}-{last_day:02d}'
        return render_template(
            'dashboards/admin/fragments/add_allocation_form_htmx.html',
            project=project,
            available_resources=available,
            today=now.strftime('%Y-%m-%d'),
            default_end_date=default_end,
            errors=(extra_errors or []) + errors,
            form=request.form,
            project_has_children=project.has_children,
            child_count=len(active_desc),
        )

    if errors:
        return _reload_add_form()

    try:
        from sam.manage.allocations import propagate_allocation_to_subprojects
        with management_transaction(db.session):
            parent_alloc = create_allocation(
                db.session,
                project_id=project.project_id,
                resource_id=resource.resource_id,
                amount=amount,
                start_date=start_date,
                end_date=end_date,
                description=description,
                user_id=current_user.user_id,
            )
            if apply_to_subprojects and project.has_children:
                descendants = [d for d in project.get_descendants() if d.active]
                child_created, child_skipped = propagate_allocation_to_subprojects(
                    db.session, parent_alloc, descendants,
                    user_id=current_user.user_id, skip_existing=True,
                )
            else:
                child_created, child_skipped = [], []
    except Exception as e:
        return _reload_add_form([f'Error creating allocation: {e}'])

    detail = f'{project.projcode} — {resource.resource_name}'
    if child_created or child_skipped:
        detail += (
            f'. Propagated to {len(child_created)} sub-project(s)'
            + (f'; {len(child_skipped)} already had an allocation (skipped).' if child_skipped else '.')
        )

    return htmx_success_message(
        {'closeActiveModal': {}, 'reloadAllocationTree': projcode},
        'Allocation created successfully.',
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Exchange allocations (Edit Project → Allocations tab)
# ---------------------------------------------------------------------------

def _exchange_candidates(project, resource_id, active_at=None):
    """Return list of dedicated allocation candidates within ``project``'s
    subtree for ``resource_id``, restricted to allocations active at
    ``active_at`` (defaults to now).

    The edit-page project itself is EXCLUDED — exchange is strictly a
    rebalancing between descendants. Moving amount *to* the root would
    not change anything (descendants inherit from it); moving amount
    *from* the root would affect children whose allocations are
    independent of it. Either way, the root is not a valid endpoint.

    Each entry is a dict: {allocation_id, amount, used, projcode,
    project_id, resource_name}. Only non-inheriting, non-deleted
    allocations on accounts owned by active descendant projects AND
    active at the reference date are included. The result is sorted by
    projcode.

    Matching ``active_at`` is essential so the dropdown shows exactly the
    allocations rendered in the tree — otherwise expired/future
    allocations for the same (project, resource) create duplicate entries.
    """
    from sam.accounting.allocations import Allocation
    from sam.accounting.accounts import Account
    from sam.resources.resources import Resource
    from sqlalchemy import or_ as sa_or

    resource = db.session.get(Resource, resource_id)
    if not resource:
        return [], None

    subtree = {
        p.project_id: p for p in project.get_descendants(include_self=False)
        if p.active
    }
    if not subtree:
        return [], resource

    check_date = active_at or datetime.now()

    rows = (
        db.session.query(Allocation, Account)
        .join(Account, Allocation.account_id == Account.account_id)
        .filter(
            Account.project_id.in_(subtree.keys()),
            Account.resource_id == resource_id,
            Account.deleted == False,  # noqa: E712
            Allocation.deleted == False,  # noqa: E712
            Allocation.parent_allocation_id.is_(None),
            Allocation.start_date <= check_date,
            sa_or(
                Allocation.end_date.is_(None),
                Allocation.end_date >= check_date,
            ),
        )
        .all()
    )

    candidates = []
    for alloc, acct in rows:
        proj = subtree.get(acct.project_id)
        if proj is None:
            continue
        # Per-project 'used' for the FROM overdraft preview / server check.
        usage = proj.get_detailed_allocation_usage(
            resource_name=resource.resource_name,
            active_at=active_at,
        )
        used = usage.get(resource.resource_name, {}).get('used', 0.0) if usage else 0.0
        candidates.append({
            'allocation_id': alloc.allocation_id,
            'amount': alloc.amount,
            'used': used,
            'projcode': proj.projcode,
            'project_id': proj.project_id,
            'resource_name': resource.resource_name,
            'title': proj.title or '',
        })

    candidates.sort(key=lambda c: c['projcode'])
    return candidates, resource


@bp.route('/htmx/exchange-allocation-form/<projcode>/<int:resource_id>')
@login_required
@require_project_permission(Permission.EDIT_ALLOCATIONS)
def htmx_exchange_allocation_form(project, resource_id):
    """Render the exchange-allocation modal form for a (project-subtree, resource) pair.

    Honors the ``active_at=YYYY-MM-DD`` query parameter carried in from the
    Allocations tab's date picker — restricts candidates to allocations
    active at that date, matching what's displayed in the tree.
    """
    active_at = _parse_active_at_arg(request.args.get('active_at', ''))
    candidates, resource = _exchange_candidates(project, resource_id, active_at=active_at)
    if resource is None:
        return '<div class="modal-body"><div class="alert alert-warning">Resource not found.</div></div>'
    if len(candidates) < 2:
        return (
            '<div class="modal-body">'
            '<div class="alert alert-info">'
            '<i class="fas fa-info-circle"></i> '
            'Exchange requires at least two dedicated allocations for this resource '
            'within the project subtree. Inherited (shared) allocations do not count.'
            '</div></div>'
        )
    return render_template(
        'dashboards/admin/fragments/exchange_allocation_form_htmx.html',
        project=project,
        resource=resource,
        candidates=candidates,
        active_at=active_at.strftime('%Y-%m-%d'),
    )


@bp.route('/htmx/exchange-allocation/<projcode>', methods=['POST'])
@login_required
@require_project_permission(Permission.EDIT_ALLOCATIONS)
def htmx_exchange_allocation(project):
    """Validate and apply an allocation exchange within the project's subtree."""
    from sam.accounting.allocations import Allocation, InheritingAllocationException
    from sam.manage.allocations import exchange_allocations
    from sam.schemas.forms import ExchangeAllocationForm
    from marshmallow import ValidationError

    errors = []
    resource_id_raw = request.form.get('resource_id', '').strip()
    try:
        resource_id = int(resource_id_raw)
    except (TypeError, ValueError):
        resource_id = None

    active_at = _parse_active_at_arg(request.form.get('active_at', ''))

    try:
        form_data = ExchangeAllocationForm().load(request.form)
    except ValidationError as e:
        errors.extend(ExchangeAllocationForm.flatten_errors(e.messages))
        form_data = {}

    def _reload_exchange_form(extra_errors=None):
        candidates, resource = (
            _exchange_candidates(project, resource_id, active_at=active_at)
            if resource_id else ([], None)
        )
        return render_template(
            'dashboards/admin/fragments/exchange_allocation_form_htmx.html',
            project=project,
            resource=resource,
            candidates=candidates,
            active_at=active_at.strftime('%Y-%m-%d'),
            errors=(extra_errors or []) + errors,
            form=request.form,
        )

    if resource_id is None:
        return _reload_exchange_form(['Resource is required.'])

    if errors:
        return _reload_exchange_form()

    from_id = form_data['from_allocation_id']
    to_id = form_data['to_allocation_id']
    amount = form_data['amount']

    # Restrict endpoints to the edit-page project's subtree — prevents
    # forged allocation IDs from outside the authorized scope.
    candidates, resource = _exchange_candidates(project, resource_id, active_at=active_at)
    by_id = {c['allocation_id']: c for c in candidates}
    from_cand = by_id.get(from_id)
    to_cand = by_id.get(to_id)
    if from_cand is None or to_cand is None:
        return _reload_exchange_form([
            'Selected allocation is not in this project subtree for the chosen resource.'
        ])

    # Strict overdraft: cannot push FROM remaining below zero.
    from_remaining = from_cand['amount'] - from_cand['used']
    if amount > from_remaining:
        return _reload_exchange_form([
            f"Exchange amount ({amount:g}) exceeds FROM remaining balance "
            f"({from_remaining:g})."
        ])

    try:
        with management_transaction(db.session):
            exchange_allocations(
                db.session,
                from_allocation_id=from_id,
                to_allocation_id=to_id,
                amount=amount,
                user_id=current_user.user_id,
            )
    except InheritingAllocationException as e:
        return _reload_exchange_form([str(e)])
    except ValueError as e:
        return _reload_exchange_form([str(e)])
    except Exception as e:
        return _reload_exchange_form([f'Error exchanging allocations: {e}'])

    detail = (
        f"{resource.resource_name}: -{amount:g} {from_cand['projcode']} / "
        f"+{amount:g} {to_cand['projcode']}"
    )
    return htmx_success_message(
        {'closeActiveModal': {}, 'reloadAllocationTree': project.projcode},
        'Allocation exchanged successfully.',
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Renew allocations (Edit Project → Allocations tab)
# ---------------------------------------------------------------------------

def _parse_active_at_arg(arg: str) -> datetime:
    """Parse the ?active_at=YYYY-MM-DD query arg; default to today on empty/invalid."""
    arg = (arg or '').strip()
    if arg:
        try:
            return datetime.strptime(arg, '%Y-%m-%d')
        except ValueError:
            pass
    return datetime.now()


def _snap_to_end_of_month(d):
    """Snap *d* to the nearest natural month-end.

    Admins write allocation end dates as 'end-of-month', not 'May 2nd' or
    'Jan 1st'. Computed dates from period arithmetic can land a day or
    two off a month boundary — this normalizes them:

      - day 1  →  last day of the previous month  (May 1 → Apr 30).
      - any other day → last day of the same month (Apr 15 → Apr 30,
        Oct 29 → Oct 31, Oct 31 → Oct 31 no-op).

    The day-1 case matters for Renew when an N-year source + an N-year
    shift lands exactly on the next period's first day (e.g. a Jan 1 →
    Dec 31 source shifted 2 years gives Jan 1, which should be Dec 31).
    """
    import calendar
    from datetime import timedelta
    if d.day == 1:
        return d - timedelta(days=1)
    last_day = calendar.monthrange(d.year, d.month)[1]
    return d.replace(day=last_day)


def _propose_renew_dates(source_allocs):
    """Return (new_start, new_end) as 'YYYY-MM-DD' strings for the form defaults.

    Contiguous renewal: new_start = latest source end_date + 1 day;
    new_end = new_start + (source_end - source_start), snapped to the
    last day of that month. When multiple source allocations are selected,
    we anchor on the one with the latest end date and preserve its period
    length — this naturally handles the common fiscal-year case (e.g.
    Oct 1 → Sep 30 → Oct 1 → Sep 30 next year).

    Falls back to ("today", "today + 1 year, last day of month") if the set
    is empty or lacks end dates (open-ended allocations).
    """
    from datetime import timedelta

    dated = [a for a in source_allocs if a.end_date is not None]
    if dated:
        anchor = max(dated, key=lambda a: a.end_date)
        new_start = anchor.end_date + timedelta(days=1)
        period = anchor.end_date - anchor.start_date
        new_end = _snap_to_end_of_month(new_start + period)
        return new_start.strftime('%Y-%m-%d'), new_end.strftime('%Y-%m-%d')

    now = datetime.now()
    fallback = _snap_to_end_of_month(now.replace(year=now.year + 1))
    return (
        now.strftime('%Y-%m-%d'),
        fallback.strftime('%Y-%m-%d'),
    )


def _build_renew_candidates(project, source_active_at):
    """Build the per-resource candidate rows for the Renew form.

    Returns a list of dicts (one per root-project allocation active at
    ``source_active_at``), each containing display fields the template
    needs: resource name/type, amount, tree size, and the source
    allocation id for submission.
    """
    from sam.manage.renew import (
        find_source_allocations_at,
        find_renewable_descendants,
    )

    sources = find_source_allocations_at(
        db.session, project, source_active_at
    )

    candidates = []
    for src in sources:
        resource = src.account.resource
        # Count descendants that had any (inheriting OR standalone) source
        # allocation for this resource at source_active_at — these are the
        # projects renewal will create new rows on.
        child_projects = find_renewable_descendants(
            project, resource.resource_id, source_active_at
        )
        candidates.append({
            'source_alloc': src,
            'resource_id': resource.resource_id,
            'resource_name': resource.resource_name,
            'resource_type': (
                resource.resource_type.resource_type
                if resource.resource_type else ''
            ),
            'amount': src.amount,
            'start_date': src.start_date,
            'end_date': src.end_date,
            'descendant_count': len(child_projects),
        })
    candidates.sort(key=lambda c: c['resource_name'])
    return candidates


@bp.route('/htmx/renew-allocations-form/<projcode>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_renew_allocations_form(projcode):
    """Return the Renew Allocations modal form fragment.

    Query params:
        active_at (YYYY-MM-DD): which allocations to renew. Defaults to today.
    """
    from sam.projects.projects import Project

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-warning">Project not found.</div>'

    # Renew always operates from the root of the project tree.
    root = project.get_root() if hasattr(project, 'get_root') else project

    source_active_at = _parse_active_at_arg(request.args.get('active_at', ''))
    candidates = _build_renew_candidates(root, source_active_at)

    default_start, default_end = _propose_renew_dates(
        [c['source_alloc'] for c in candidates]
    )

    return render_template(
        'dashboards/admin/fragments/renew_allocations_form_htmx.html',
        project=project,
        root=root,
        candidates=candidates,
        source_active_at=source_active_at.strftime('%Y-%m-%d'),
        default_start=default_start,
        default_end=default_end,
    )


@bp.route('/htmx/renew-allocations/<projcode>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_renew_allocations(projcode):
    """Create renewed allocations for the selected resources."""
    from sam.projects.projects import Project
    from sam.resources.resources import Resource
    from sam.manage.renew import renew_project_allocations, analyze_renew_preconditions
    from sam.schemas.forms import RenewAllocationsForm
    from marshmallow import ValidationError

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found.</div>', 404

    root = project.get_root() if hasattr(project, 'get_root') else project

    errors = []

    # Pre-process: drop empty strings; collect multi-valued resource_ids list.
    data = {k: v for k, v in request.form.items() if v != ''}
    data['resource_ids'] = [
        int(v) for v in request.form.getlist('resource_ids') if v
    ]
    # Per-resource scale inputs: scale_<resource_id> → float. Missing/blank
    # entries default to 1.0 inside renew_project_allocations().
    data['scales'] = {
        int(k.removeprefix('scale_')): v
        for k, v in request.form.items()
        if k.startswith('scale_') and v.strip()
    }
    # Strip the flattened scale_* keys so marshmallow's unknown=EXCLUDE isn't
    # invoked on inputs we've already collapsed into the 'scales' dict.
    for k in list(data):
        if k.startswith('scale_'):
            del data[k]
    # Inject explicit False for the replace_existing checkbox when unchecked.
    data['replace_existing'] = 'replace_existing' in request.form

    try:
        form_data = RenewAllocationsForm().load(data)
    except ValidationError as e:
        errors.extend(RenewAllocationsForm.flatten_errors(e.messages))
        form_data = {}

    def _reload_renew_form(extra_errors=None):
        source_active_at = form_data.get('source_active_at') \
            or _parse_active_at_arg(request.form.get('source_active_at', '')).date()
        # Normalize to datetime
        source_dt = (
            datetime.combine(source_active_at, datetime.min.time())
            if hasattr(source_active_at, 'year') else datetime.now()
        )
        candidates = _build_renew_candidates(root, source_dt)
        return render_template(
            'dashboards/admin/fragments/renew_allocations_form_htmx.html',
            project=project,
            root=root,
            candidates=candidates,
            source_active_at=source_dt.strftime('%Y-%m-%d'),
            default_start=request.form.get('new_start_date', ''),
            default_end=request.form.get('new_end_date', ''),
            errors=(extra_errors or []) + errors,
            form=request.form,
        )

    if errors:
        return _reload_renew_form()

    new_start = datetime.combine(
        form_data['new_start_date'], datetime.min.time()
    )
    new_end = form_data['new_end_date']   # already datetime via post_load
    source_dt = datetime.combine(
        form_data['source_active_at'], datetime.min.time()
    )

    replace_existing = form_data.get('replace_existing', False)

    # Pre-flight: classify each requested resource so we can produce accurate
    # error messages and (when needed) prompt the admin to set replace_existing.
    preconditions = analyze_renew_preconditions(
        db.session,
        root_project_id=root.project_id,
        source_active_at=source_dt,
        new_start=new_start,
        new_end=new_end,
        resource_ids=form_data['resource_ids'],
    )
    resource_name = {
        r.resource_id: r.resource_name
        for r in db.session.query(Resource).filter(
            Resource.resource_id.in_(form_data['resource_ids'])
        )
    }

    no_source_ids = [rid for rid, s in preconditions.items() if s == 'no_source']
    overlap_ids   = [rid for rid, s in preconditions.items() if s == 'overlap']

    # Bail early with a specific error when NOTHING can be renewed.
    if not any(s == 'ok' for s in preconditions.values()) and not (replace_existing and overlap_ids):
        msgs = []
        if overlap_ids:
            names = ', '.join(sorted(resource_name.get(r, f'#{r}') for r in overlap_ids))
            msgs.append(
                f'Already has allocations overlapping '
                f'{new_start.strftime("%Y-%m-%d")} → {new_end.strftime("%Y-%m-%d")}: {names}. '
                f'Tick "Replace existing" to supersede them.'
            )
        if no_source_ids:
            names = ', '.join(sorted(resource_name.get(r, f'#{r}') for r in no_source_ids))
            msgs.append(
                f'No active root allocation at '
                f'{source_dt.strftime("%Y-%m-%d")} for: {names}.'
            )
        return _reload_renew_form(msgs)

    try:
        with management_transaction(db.session):
            created = renew_project_allocations(
                db.session,
                root_project_id=root.project_id,
                source_active_at=source_dt,
                new_start=new_start,
                new_end=new_end,
                resource_ids=form_data['resource_ids'],
                scales=form_data.get('scales') or {},
                user_id=current_user.user_id,
                replace_existing=replace_existing,
            )
    except Exception as e:
        return _reload_renew_form([f'Error renewing allocations: {e}'])

    if not created:
        # Defensive fallback — preconditions said 'ok' for at least one, but
        # nothing was created. Shouldn't happen but keep a sane message.
        return _reload_renew_form([
            'No allocations were renewed. Please review the form and try again.'
        ])

    detail_parts = [
        f'{root.projcode}: renewed {len(created)} allocation(s) for '
        f'{new_start.strftime("%Y-%m-%d")} → {new_end.strftime("%Y-%m-%d")}'
    ]
    if replace_existing and overlap_ids:
        names = ', '.join(sorted(resource_name.get(r, f'#{r}') for r in overlap_ids))
        detail_parts.append(f'replaced overlapping allocations for: {names}')
    if no_source_ids:
        names = ', '.join(sorted(resource_name.get(r, f'#{r}') for r in no_source_ids))
        detail_parts.append(f'skipped (no source at {source_dt.strftime("%Y-%m-%d")}): {names}')
    return htmx_success_message(
        {'closeActiveModal': {}, 'reloadAllocationTree': projcode},
        'Allocations renewed successfully.',
        detail='; '.join(detail_parts),
    )


# ---------------------------------------------------------------------------
# Extend allocations (Edit Project → Allocations tab)
# ---------------------------------------------------------------------------

def _propose_extend_end(source_allocs):
    """Return ``YYYY-MM-DD`` string: a proposed new end date for Extend.

    Anchors on the latest-ending dated source, then adds the source's
    own period length (end - start), snapped to the last day of that
    month. So a 1-year allocation proposes a 1-year push; a 6-month
    allocation proposes 6 months. Open-ended sources are ignored.
    Returns '' if no dated source is available.
    """
    dated = [a for a in source_allocs if a.end_date is not None]
    if not dated:
        return ''
    anchor = max(dated, key=lambda a: a.end_date)
    period = anchor.end_date - anchor.start_date
    return _snap_to_end_of_month(anchor.end_date + period).strftime('%Y-%m-%d')


def _build_extend_candidates(project, source_active_at):
    """Build the per-resource candidate rows for the Extend form.

    Mirrors ``_build_renew_candidates`` but emits the fields the Extend
    template needs (no start_date) and tags open-ended sources so the
    template can render them as disabled checkboxes.
    """
    from sam.manage.renew import (
        find_source_allocations_at,
        find_renewable_descendants,
    )

    sources = find_source_allocations_at(
        db.session, project, source_active_at
    )

    candidates = []
    for src in sources:
        resource = src.account.resource
        child_projects = find_renewable_descendants(
            project, resource.resource_id, source_active_at
        )
        candidates.append({
            'source_alloc': src,
            'resource_id': resource.resource_id,
            'resource_name': resource.resource_name,
            'resource_type': (
                resource.resource_type.resource_type
                if resource.resource_type else ''
            ),
            'amount': src.amount,
            'end_date': src.end_date,
            'is_open_ended': src.end_date is None,
            'descendant_count': len(child_projects),
        })
    candidates.sort(key=lambda c: c['resource_name'])
    return candidates


@bp.route('/htmx/extend-allocations-form/<projcode>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_extend_allocations_form(projcode):
    """Return the Extend Allocations modal form fragment."""
    from sam.projects.projects import Project

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-warning">Project not found.</div>'

    root = project.get_root() if hasattr(project, 'get_root') else project

    source_active_at = _parse_active_at_arg(request.args.get('active_at', ''))
    candidates = _build_extend_candidates(root, source_active_at)

    default_end = _propose_extend_end(
        [c['source_alloc'] for c in candidates if not c['is_open_ended']]
    )

    return render_template(
        'dashboards/admin/fragments/extend_allocations_form_htmx.html',
        project=project,
        root=root,
        candidates=candidates,
        source_active_at=source_active_at.strftime('%Y-%m-%d'),
        default_end=default_end,
    )


@bp.route('/htmx/extend-allocations/<projcode>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_extend_allocations(projcode):
    """Push end_date forward on the selected allocations."""
    from sam.projects.projects import Project
    from sam.manage.extend import extend_project_allocations
    from sam.schemas.forms import ExtendAllocationsForm
    from marshmallow import ValidationError

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found.</div>', 404

    root = project.get_root() if hasattr(project, 'get_root') else project

    errors = []

    data = {k: v for k, v in request.form.items() if v != ''}
    data['resource_ids'] = [
        int(v) for v in request.form.getlist('resource_ids') if v
    ]

    try:
        form_data = ExtendAllocationsForm().load(data)
    except ValidationError as e:
        errors.extend(ExtendAllocationsForm.flatten_errors(e.messages))
        form_data = {}

    def _reload_extend_form(extra_errors=None):
        source_active_at = form_data.get('source_active_at') \
            or _parse_active_at_arg(request.form.get('source_active_at', '')).date()
        source_dt = (
            datetime.combine(source_active_at, datetime.min.time())
            if hasattr(source_active_at, 'year') else datetime.now()
        )
        candidates = _build_extend_candidates(root, source_dt)
        return render_template(
            'dashboards/admin/fragments/extend_allocations_form_htmx.html',
            project=project,
            root=root,
            candidates=candidates,
            source_active_at=source_dt.strftime('%Y-%m-%d'),
            default_end=request.form.get('new_end_date', ''),
            errors=(extra_errors or []) + errors,
            form=request.form,
        )

    if errors:
        return _reload_extend_form()

    new_end = form_data['new_end_date']   # datetime via post_load
    source_dt = datetime.combine(
        form_data['source_active_at'], datetime.min.time()
    )

    # Block shortening: new_end must strictly exceed every selected
    # resource's current end date at the source.
    from sam.manage.renew import find_source_alloc_at
    latest_current_end = None
    for rid in form_data['resource_ids']:
        src = find_source_alloc_at(root, rid, source_dt)
        if src is None or src.end_date is None:
            continue
        if latest_current_end is None or src.end_date > latest_current_end:
            latest_current_end = src.end_date
    if latest_current_end is not None and new_end <= latest_current_end:
        return _reload_extend_form([
            f'New end date must be later than the current latest end date '
            f'({latest_current_end.strftime("%Y-%m-%d")}).'
        ])

    try:
        with management_transaction(db.session):
            updated = extend_project_allocations(
                db.session,
                root_project_id=root.project_id,
                source_active_at=source_dt,
                new_end=new_end,
                resource_ids=form_data['resource_ids'],
                user_id=current_user.user_id,
            )
    except Exception as e:
        return _reload_extend_form([f'Error extending allocations: {e}'])

    if not updated:
        return _reload_extend_form([
            'No allocations were extended. Either the selected resources '
            'are open-ended or already end on/after the requested date.'
        ])

    detail = (
        f'{root.projcode}: extended {len(updated)} allocation(s) to '
        f'{new_end.strftime("%Y-%m-%d")}'
    )
    return htmx_success_message(
        {'closeActiveModal': {}, 'reloadAllocationTree': projcode},
        'Allocations extended successfully.',
        detail=detail,
    )


@bp.route('/htmx/edit-allocation-form/<int:alloc_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_edit_allocation_form(alloc_id):
    """Return the edit-allocation form fragment (loaded into modal)."""
    from sam.accounting.allocations import Allocation
    from sam.manage.allocations import get_partitioned_descendant_sum, date_ranges_overlap
    from sam.accounting.accounts import Account

    allocation = db.session.get(Allocation, alloc_id)
    if not allocation:
        return '<div class="alert alert-warning">Allocation not found.</div>'

    projcode = allocation.account.project.projcode if allocation.account else ''

    # Flaw 1 fix: sum standalone (non-inherited) descendant allocations only
    partitioned_sum = get_partitioned_descendant_sum(db.session, allocation)

    # Parent info for inheriting allocations
    parent_info = None
    if allocation.is_inheriting and allocation.parent:
        p = allocation.parent
        parent_proj = p.account.project if p.account else None
        parent_info = {
            'allocation_id': p.allocation_id,
            'amount': p.amount,
            'projcode': parent_proj.projcode if parent_proj and parent_proj.active else None,
        }

    # Flaw 3 fix: count descendants that have NO allocation for this resource at all
    # (not just those missing from allocation.children — detached ones are excluded)
    unlinked_descendants_count = 0
    if not allocation.is_inheriting and allocation.account:
        project = allocation.account.project
        resource_id = allocation.account.resource_id
        if project and project.has_children:
            def _has_any_alloc(proj_id):
                acct = Account.get_by_project_and_resource(db.session, proj_id, resource_id)
                return acct is not None and any(not a.deleted for a in acct.allocations)
            unlinked_descendants_count = sum(
                1 for d in project.get_descendants()
                if d.active and not _has_any_alloc(d.project_id)
            )

    # Relink candidate: standalone child allocation whose project has a parent
    # project with a compatible (overlapping, non-inheriting) allocation for the
    # same resource. When multiple candidates overlap, prefer the latest start_date.
    relink_candidate = None
    if not allocation.is_inheriting and allocation.account:
        proj = allocation.account.project
        if proj and proj.parent_id and proj.parent and proj.parent.active:
            parent_acct = Account.get_by_project_and_resource(
                db.session, proj.parent.project_id, allocation.account.resource_id
            )
            if parent_acct:
                # Parent allocation may itself be inheriting — the deep-tree
                # invariant points at the immediate project-parent's allocation,
                # not the root. A grandchild correctly links to an inheriting
                # middle-tier parent.
                candidates = [
                    a for a in parent_acct.allocations
                    if not a.deleted
                    and date_ranges_overlap(a, allocation)
                ]
                if candidates:
                    best = max(candidates, key=lambda a: a.start_date or datetime.min)
                    relink_candidate = {
                        'allocation_id': best.allocation_id,
                        'amount': best.amount,
                        'start_date': best.start_date,
                        'end_date': best.end_date,
                        'projcode': proj.parent.projcode,
                    }

    return render_template(
        'dashboards/admin/fragments/edit_allocation_form_htmx.html',
        allocation=allocation,
        projcode=projcode,
        partitioned_sum=partitioned_sum,
        parent_info=parent_info,
        unlinked_descendants_count=unlinked_descendants_count,
        relink_candidate=relink_candidate,
    )


@bp.route('/htmx/edit-allocation/<int:alloc_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_edit_allocation(alloc_id):
    """Validate and apply allocation edits with cascade + audit logging."""
    from sam.accounting.allocations import Allocation, InheritingAllocationException
    from sam.manage.allocations import update_allocation, detach_allocation
    from sam.manage.allocations import get_partitioned_descendant_sum
    from sam.schemas.forms import EditAllocationForm
    from marshmallow import ValidationError

    allocation = db.session.get(Allocation, alloc_id)
    if not allocation:
        return '<div class="alert alert-danger">Allocation not found.</div>', 404

    projcode = allocation.account.project.projcode if allocation.account else ''

    break_inheritance = request.form.get('break_inheritance') == 'true'

    errors = []

    # Pre-process form data: drop empty amount/start_date (marshmallow would
    # reject '' for Float/Date). end_date='' is a valid "clear to open-ended"
    # signal — let the schema's post_load convert it to None.
    data = dict(request.form)
    for k in ('amount', 'start_date'):
        if data.get(k) == '':
            data.pop(k, None)

    try:
        form_data = EditAllocationForm().load(data, partial=True)
    except ValidationError as e:
        errors.extend(EditAllocationForm.flatten_errors(e.messages))
        form_data = {}

    # Build updates dict — gate on original form presence so unspecified fields
    # aren't overwritten and empty-string end_date correctly clears.
    updates = {}
    if request.form.get('amount'):
        updates['amount'] = form_data['amount']
    if request.form.get('start_date'):
        updates['start_date'] = datetime.combine(
            form_data['start_date'], datetime.min.time()
        )
    if 'end_date' in request.form:
        updates['end_date'] = form_data.get('end_date')  # datetime or None
    if 'description' in request.form:
        updates['description'] = form_data.get('description')

    def _reload_edit_form(extra_errors=None):
        # Recompute context for re-render
        partitioned_sum = get_partitioned_descendant_sum(db.session, allocation)
        p_info = None
        if allocation.is_inheriting and allocation.parent:
            p = allocation.parent
            parent_proj = p.account.project if p.account else None
            p_info = {
                'allocation_id': p.allocation_id,
                'amount': p.amount,
                'projcode': parent_proj.projcode if parent_proj and parent_proj.active else None,
            }
        return render_template(
            'dashboards/admin/fragments/edit_allocation_form_htmx.html',
            allocation=allocation,
            projcode=projcode,
            partitioned_sum=partitioned_sum,
            parent_info=p_info,
            unlinked_descendants_count=0,  # skip expensive recompute on error re-renders
            relink_candidate=None,         # skip recompute on error re-renders
            errors=(extra_errors or []) + errors,
            form=request.form,
        )

    if errors:
        return _reload_edit_form()

    if not updates:
        return _reload_edit_form(['No changes provided.'])

    try:
        with management_transaction(db.session):
            if allocation.is_inheriting and break_inheritance:
                # DETACH then EDIT: two audit records — intentional.
                # detach_allocation() calls session.flush() so is_inheriting is
                # False in the identity map before update_allocation() runs.
                detach_allocation(db.session, alloc_id, current_user.user_id)
                update_allocation(db.session, alloc_id, current_user.user_id, **updates)
            else:
                update_allocation(db.session, alloc_id, current_user.user_id, **updates)
    except InheritingAllocationException:
        return _reload_edit_form([
            'Cannot directly edit an inherited allocation. '
            'Check "I understand — break inheritance" to detach it first, '
            'or edit the parent allocation to cascade changes automatically.'
        ])
    except Exception as e:
        return _reload_edit_form([f'Error updating allocation: {e}'])

    return htmx_success_message(
        {'closeActiveModal': {}, 'reloadAllocationTree': projcode},
        'Allocation updated successfully.',
    )


@bp.route('/htmx/detach-allocation/<int:alloc_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_detach_allocation(alloc_id):
    """Break parent_allocation_id link without editing other fields."""
    from sam.accounting.allocations import Allocation
    from sam.manage.allocations import detach_allocation

    allocation = db.session.get(Allocation, alloc_id)
    if not allocation:
        return '<div class="alert alert-danger">Allocation not found.</div>', 404
    projcode = allocation.account.project.projcode if allocation.account else ''
    try:
        with management_transaction(db.session):
            detach_allocation(db.session, alloc_id, current_user.user_id)
    except ValueError as e:
        return f'<div class="alert alert-danger">{e}</div>', 400
    return htmx_success_message(
        {'closeActiveModal': {}, 'reloadAllocationTree': projcode},
        'Allocation detached successfully.',
    )


@bp.route('/htmx/link-allocation-to-parent/<int:alloc_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_link_allocation_to_parent(alloc_id):
    """Re-link a standalone child allocation to its parent-project allocation."""
    from sam.accounting.allocations import Allocation
    from sam.manage.allocations import link_allocation_to_parent

    allocation = db.session.get(Allocation, alloc_id)
    if not allocation:
        return '<div class="alert alert-danger">Allocation not found.</div>', 404
    projcode = allocation.account.project.projcode if allocation.account else ''

    try:
        parent_allocation_id = int(request.form.get('parent_allocation_id', '0'))
    except (TypeError, ValueError):
        return '<div class="alert alert-danger">Invalid parent allocation id.</div>', 400
    if parent_allocation_id <= 0:
        return '<div class="alert alert-danger">Missing parent allocation id.</div>', 400

    try:
        with management_transaction(db.session):
            link_allocation_to_parent(
                db.session, alloc_id, parent_allocation_id, current_user.user_id
            )
    except ValueError as e:
        return f'<div class="alert alert-danger">{e}</div>', 400

    return htmx_success_message(
        {'closeActiveModal': {}, 'reloadAllocationTree': projcode},
        'Allocation re-linked to parent successfully.',
    )


@bp.route('/htmx/propagate-allocation-to-remaining/<int:alloc_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_propagate_to_remaining(alloc_id):
    """Create child allocations for active descendants that don't yet have one."""
    from sam.accounting.allocations import Allocation
    from sam.accounting.accounts import Account
    from sam.manage.allocations import propagate_allocation_to_subprojects

    allocation = db.session.get(Allocation, alloc_id)
    if not allocation or allocation.is_inheriting:
        return '<div class="alert alert-danger">Invalid allocation.</div>', 400
    project = allocation.account.project
    resource_id = allocation.account.resource_id

    # Flaw 3 fix: exclude descendants that already have ANY allocation for this resource
    # (not just those linked via allocation.children — detached ones are excluded correctly)
    def _has_any_alloc(proj_id):
        acct = Account.get_by_project_and_resource(db.session, proj_id, resource_id)
        return acct is not None and any(not a.deleted for a in acct.allocations)

    descendants = [
        d for d in project.get_descendants()
        if d.active and not _has_any_alloc(d.project_id)
    ]
    try:
        with management_transaction(db.session):
            created, skipped = propagate_allocation_to_subprojects(
                db.session, allocation, descendants,
                user_id=current_user.user_id, skip_existing=True,
            )
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 400
    return htmx_success_message(
        {'closeActiveModal': {}, 'reloadAllocationTree': project.projcode},
        f'Created {len(created)} child allocation(s).'
        + (f' {len(skipped)} skipped (already existed).' if skipped else ''),
    )


# ---------------------------------------------------------------------------
# Linked Elements (ProjectOrganization, ProjectContract, ProjectDirectory)
# ---------------------------------------------------------------------------

_ORG_LINK_FACILITIES = {'NCAR', 'CISL', 'CSL', 'ASD'}


def _linked_elements_context(project):
    """Build the template context dict for the linked-elements fragment."""
    facility_name = None
    try:
        facility_name = project.allocation_type.panel.facility.facility_name
    except AttributeError:
        pass

    return dict(
        project=project,
        allows_org_links=(facility_name in _ORG_LINK_FACILITIES),
        active_organizations=[po for po in project.organizations if po.is_active],
        contracts=project.contracts,
        active_directories=[pd for pd in project.directories if pd.is_active],
        can_edit_governance=can_edit_project_governance(current_user, project),
        errors=[],
    )


def _render_linked_elements(project, errors=None):
    ctx = _linked_elements_context(project)
    if errors:
        ctx['errors'] = errors
    return render_template(
        'dashboards/admin/fragments/project_linked_elements_htmx.html',
        **ctx,
    )


@bp.route('/htmx/project/<projcode>/linked-elements')
@login_required
@require_project_permission(Permission.EDIT_PROJECTS)
def htmx_project_linked_elements(project):
    """Render the linked-elements section for an edit-project page.

    Access: system EDIT_PROJECTS, or project lead/admin. Add / remove
    actions inside the fragment remain gated by the admin-only
    ``can_edit_governance`` flag.
    """
    return _render_linked_elements(project)


@bp.route('/htmx/project/<projcode>/organizations/add', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_add_project_organization(projcode):
    """Link an organization to a project (NCAR facility only)."""
    from marshmallow import ValidationError
    from sam.schemas.forms.projects import AddLinkedOrganizationForm
    from sam.projects.projects import Project
    from sam.core.organizations import Organization, ProjectOrganization

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found.</div>', 404

    # Facility gate — caller shouldn't reach this for non-NCAR, but guard anyway
    try:
        facility_name = project.allocation_type.panel.facility.facility_name
    except AttributeError:
        facility_name = None
    if facility_name not in _ORG_LINK_FACILITIES:
        return _render_linked_elements(project, errors=['Organization links are not available for this facility.'])

    try:
        form_data = AddLinkedOrganizationForm().load(request.form)
    except ValidationError as e:
        return _render_linked_elements(project, errors=AddLinkedOrganizationForm.flatten_errors(e.messages))

    org_id = form_data['organization_id']
    org = db.session.get(Organization, org_id)
    if not org:
        return _render_linked_elements(project, errors=['Organization not found.'])

    # Prevent duplicate active links
    existing = [po for po in project.organizations if po.organization_id == org_id and po.is_active]
    if existing:
        return _render_linked_elements(project, errors=[f'"{org.name}" is already linked to this project.'])

    try:
        with management_transaction(db.session):
            ProjectOrganization.create(
                db.session,
                project_id=project.project_id,
                organization_id=org_id,
            )
    except Exception as e:
        return _render_linked_elements(project, errors=[f'Error adding organization: {e}'])

    db.session.refresh(project)
    return _render_linked_elements(project)


@bp.route('/htmx/project/<projcode>/organizations/<int:po_id>/remove', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_remove_project_organization(projcode, po_id):
    """Deactivate a project-organization link (sets end_date to now)."""
    from sam.projects.projects import Project
    from sam.core.organizations import ProjectOrganization

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found.</div>', 404

    po = db.session.get(ProjectOrganization, po_id)
    if not po or po.project_id != project.project_id:
        return _render_linked_elements(project, errors=['Organization link not found.'])

    try:
        with management_transaction(db.session):
            po.deactivate()
    except Exception as e:
        return _render_linked_elements(project, errors=[f'Error removing organization: {e}'])

    db.session.refresh(project)
    return _render_linked_elements(project)


@bp.route('/htmx/project/<projcode>/contracts/add', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_add_project_contract(projcode):
    """Link a contract to a project."""
    from marshmallow import ValidationError
    from sam.schemas.forms.projects import AddLinkedContractForm
    from sam.projects.projects import Project
    from sam.projects.contracts import Contract, ProjectContract

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found.</div>', 404

    try:
        form_data = AddLinkedContractForm().load(request.form)
    except ValidationError as e:
        return _render_linked_elements(project, errors=AddLinkedContractForm.flatten_errors(e.messages))

    contract_id = form_data['contract_id']
    contract = db.session.get(Contract, contract_id)
    if not contract:
        return _render_linked_elements(project, errors=['Contract not found.'])

    # Prevent duplicate links
    existing = [pc for pc in project.contracts if pc.contract_id == contract_id]
    if existing:
        return _render_linked_elements(project, errors=[f'Contract "{contract.contract_number}" is already linked to this project.'])

    try:
        with management_transaction(db.session):
            ProjectContract.create(
                db.session,
                project_id=project.project_id,
                contract_id=contract_id,
            )
    except Exception as e:
        return _render_linked_elements(project, errors=[f'Error adding contract: {e}'])

    db.session.refresh(project)
    return _render_linked_elements(project)


@bp.route('/htmx/project/<projcode>/contracts/<int:pc_id>/remove', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_remove_project_contract(projcode, pc_id):
    """Remove a project-contract link.

    If this is the sole project using the contract, also deactivates the
    Contract record (sets end_date = now).  Otherwise only removes the
    ProjectContract join row.
    """
    from sam.projects.projects import Project
    from sam.projects.contracts import ProjectContract

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found.</div>', 404

    pc = db.session.get(ProjectContract, pc_id)
    if not pc or pc.project_id != project.project_id:
        return _render_linked_elements(project, errors=['Contract link not found.'])

    contract = pc.contract
    other_links = [p for p in contract.projects if p.project_contract_id != pc_id]

    try:
        with management_transaction(db.session):
            db.session.delete(pc)
            if not other_links:
                # Sole project using this contract — deactivate the contract too
                contract.update(end_date=datetime.now())
    except Exception as e:
        return _render_linked_elements(project, errors=[f'Error removing contract: {e}'])

    db.session.refresh(project)
    return _render_linked_elements(project)


@bp.route('/htmx/project/<projcode>/directories/add', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_add_project_directory(projcode):
    """Associate a filesystem directory with a project."""
    from marshmallow import ValidationError
    from sam.schemas.forms.projects import AddLinkedDirectoryForm
    from sam.projects.projects import Project, ProjectDirectory

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found.</div>', 404

    try:
        form_data = AddLinkedDirectoryForm().load(request.form)
    except ValidationError as e:
        return _render_linked_elements(project, errors=AddLinkedDirectoryForm.flatten_errors(e.messages))

    directory_name = form_data['directory_name']

    # Prevent duplicate active entries
    existing = [pd for pd in project.directories if pd.directory_name == directory_name and pd.is_active]
    if existing:
        return _render_linked_elements(project, errors=[f'Directory "{directory_name}" is already linked to this project.'])

    try:
        with management_transaction(db.session):
            ProjectDirectory.create(
                db.session,
                project_id=project.project_id,
                directory_name=directory_name,
            )
    except Exception as e:
        return _render_linked_elements(project, errors=[f'Error adding directory: {e}'])

    db.session.refresh(project)
    return _render_linked_elements(project)


@bp.route('/htmx/project/<projcode>/directories/<int:pd_id>/remove', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_remove_project_directory(projcode, pd_id):
    """Deactivate a project directory association (sets end_date to now)."""
    from sam.projects.projects import Project, ProjectDirectory

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found.</div>', 404

    pd = db.session.get(ProjectDirectory, pd_id)
    if not pd or pd.project_id != project.project_id:
        return _render_linked_elements(project, errors=['Directory not found.'])

    try:
        with management_transaction(db.session):
            pd.deactivate()
    except Exception as e:
        return _render_linked_elements(project, errors=[f'Error removing directory: {e}'])

    db.session.refresh(project)
    return _render_linked_elements(project)
