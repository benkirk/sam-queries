"""
Admin dashboard — Project management routes.

Covers: Project creation (Phase A).  Edit/allocation management (Phase B).
"""

import re
from datetime import datetime

from flask import render_template, request, redirect, url_for
from webapp.utils.htmx import htmx_success, htmx_success_message
from flask_login import login_required, current_user

from webapp.extensions import db
from webapp.utils.rbac import require_permission, Permission
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
    facilities = (
        db.session.query(Facility)
        .filter(Facility.is_active)
        .order_by(Facility.facility_name)
        .all()
    )
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
@require_permission(Permission.CREATE_PROJECTS)
def htmx_project_create_form():
    """Return the project create form fragment (loaded into modal on button click)."""
    return render_template(
        'dashboards/admin/fragments/create_project_form_htmx.html',
        **_project_form_data(),
    )


@bp.route('/htmx/panels-for-facility')
@login_required
@require_permission(Permission.CREATE_PROJECTS)
def htmx_panels_for_facility():
    """Return <option> elements for the Panel select, filtered by facility.

    Called via hx-get when the Facility select changes.
    """
    from sam.resources.facilities import Panel

    facility_id_str = request.args.get('facility_id', '').strip()
    if not facility_id_str:
        return '<option value="">— Select facility first —</option>'
    try:
        panels = (
            db.session.query(Panel)
            .filter(Panel.facility_id == int(facility_id_str), Panel.is_active)
            .order_by(Panel.panel_name)
            .all()
        )
    except (ValueError, TypeError):
        return '<option value="">— Select facility first —</option>'

    return render_template(
        'dashboards/admin/fragments/panel_options_htmx.html',
        panels=panels,
        selected_id=None,
    )


@bp.route('/htmx/alloc-types-for-panel')
@login_required
@require_permission(Permission.CREATE_PROJECTS)
def htmx_alloc_types_for_panel():
    """Return <option> elements for the AllocationType select, filtered by panel.

    Called via hx-get when the Panel select changes.
    """
    from sam.accounting.allocations import AllocationType

    panel_id_str = request.args.get('panel_id', '').strip()
    if not panel_id_str:
        return '<option value="">— None —</option>'
    try:
        alloc_types = (
            db.session.query(AllocationType)
            .filter(AllocationType.panel_id == int(panel_id_str), AllocationType.is_active)
            .order_by(AllocationType.allocation_type)
            .all()
        )
    except (ValueError, TypeError):
        return '<option value="">— None —</option>'

    return render_template(
        'dashboards/admin/fragments/alloc_type_options_htmx.html',
        alloc_types=alloc_types,
        selected_id=None,
    )


@bp.route('/htmx/org-search-for-project')
@login_required
@require_permission(Permission.CREATE_PROJECTS)
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
@require_permission(Permission.CREATE_PROJECTS)
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
@require_permission(Permission.CREATE_PROJECTS)
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
@require_permission(Permission.CREATE_PROJECTS)
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
@require_permission(Permission.CREATE_PROJECTS)
def htmx_project_create():
    """Validate form and create a new project."""
    from datetime import datetime
    from sam.projects.projects import Project
    from sam.projects.areas import AreaOfInterest
    from sam.projects.contracts import Contract, ProjectContract
    from sam.core.users import User
    from sam.core.organizations import Organization, ProjectOrganization
    from sam.resources.facilities import Facility, Panel

    errors = []

    # ── Raw field extraction ──
    projcode_raw = request.form.get('projcode', '').strip().upper()
    title = request.form.get('title', '').strip()
    abstract = request.form.get('abstract', '').strip() or None
    facility_id_str = request.form.get('facility_id', '').strip()
    panel_id_str = request.form.get('panel_id', '').strip()
    lead_id_str = request.form.get('project_lead_user_id', '').strip()
    admin_id_str = request.form.get('project_admin_user_id', '').strip()
    aoi_id_str = request.form.get('area_of_interest_id', '').strip()
    alloc_type_id_str = request.form.get('allocation_type_id', '').strip()
    parent_id_str = request.form.get('parent_id', '').strip()
    contract_id_str = request.form.get('contract_id', '').strip()
    org_id_str = request.form.get('organization_id', '').strip()
    charging_exempt = request.form.get('charging_exempt') == 'on'
    unix_gid_str = request.form.get('unix_gid', '').strip()
    ext_alias = request.form.get('ext_alias', '').strip() or None

    # ── Validation ──
    if not facility_id_str:
        errors.append('Facility is required.')
    else:
        try:
            if not db.session.get(Facility, int(facility_id_str)):
                errors.append('Selected facility does not exist.')
        except ValueError:
            errors.append('Invalid facility.')

    if not panel_id_str:
        errors.append('Panel is required.')
    else:
        try:
            if not db.session.get(Panel, int(panel_id_str)):
                errors.append('Selected panel does not exist.')
        except ValueError:
            errors.append('Invalid panel.')

    if not projcode_raw:
        errors.append('Project code is required.')
    elif not re.fullmatch(r'[A-Z0-9]{2,30}', projcode_raw):
        errors.append('Project code must be 2–30 uppercase letters/digits.')
    elif Project.get_by_projcode(db.session, projcode_raw):
        errors.append(f'Project code "{projcode_raw}" is already in use.')

    if not title:
        errors.append('Title is required.')
    elif len(title) > 255:
        errors.append('Title must be 255 characters or fewer.')

    project_lead_user_id = None
    if not lead_id_str:
        errors.append('Project lead is required.')
    else:
        try:
            project_lead_user_id = int(lead_id_str)
            if not db.session.get(User, project_lead_user_id):
                errors.append('Selected project lead does not exist.')
        except ValueError:
            errors.append('Invalid project lead.')

    project_admin_user_id = None
    if admin_id_str:
        try:
            project_admin_user_id = int(admin_id_str)
            if not db.session.get(User, project_admin_user_id):
                errors.append('Selected project admin does not exist.')
        except ValueError:
            errors.append('Invalid project admin.')

    area_of_interest_id = None
    if not aoi_id_str:
        errors.append('Area of interest is required.')
    else:
        try:
            area_of_interest_id = int(aoi_id_str)
            if not db.session.get(AreaOfInterest, area_of_interest_id):
                errors.append('Selected area of interest does not exist.')
        except ValueError:
            errors.append('Invalid area of interest.')

    allocation_type_id = None
    if alloc_type_id_str:
        try:
            allocation_type_id = int(alloc_type_id_str)
        except ValueError:
            errors.append('Invalid allocation type.')

    parent_id = None
    if parent_id_str:
        try:
            parent_id = int(parent_id_str)
            if not db.session.get(Project, parent_id):
                errors.append('Selected parent project does not exist.')
        except ValueError:
            errors.append('Invalid parent project.')

    contract_id = None
    if contract_id_str:
        try:
            contract_id = int(contract_id_str)
            if not db.session.get(Contract, contract_id):
                errors.append('Selected contract does not exist.')
        except ValueError:
            errors.append('Invalid contract.')

    organization_id = None
    if org_id_str:
        try:
            organization_id = int(org_id_str)
            if not db.session.get(Organization, organization_id):
                errors.append('Selected organization does not exist.')
        except ValueError:
            errors.append('Invalid organization.')

    unix_gid = None
    if unix_gid_str:
        try:
            unix_gid = int(unix_gid_str)
        except ValueError:
            errors.append('Unix GID must be a number.')

    def _reload_form(extra_errors=None):
        return render_template(
            'dashboards/admin/fragments/create_project_form_htmx.html',
            **_project_form_data(form=request.form),
            errors=(extra_errors or []) + errors,
            form=request.form,
        )

    if errors:
        return _reload_form()

    try:
        with management_transaction(db.session):
            project = Project.create(
                db.session,
                projcode=projcode_raw,
                title=title,
                abstract=abstract,
                project_lead_user_id=project_lead_user_id,
                project_admin_user_id=project_admin_user_id,
                area_of_interest_id=area_of_interest_id,
                allocation_type_id=allocation_type_id,
                parent_id=parent_id,
                charging_exempt=charging_exempt,
                unix_gid=unix_gid,
                ext_alias=ext_alias,
            )
            if contract_id:
                db.session.add(ProjectContract(
                    project_id=project.project_id,
                    contract_id=contract_id,
                ))
            if organization_id:
                db.session.add(ProjectOrganization(
                    project_id=project.project_id,
                    organization_id=organization_id,
                    start_date=datetime.now(),
                ))
    except Exception as e:
        return _reload_form([f'Error creating project: {e}'])

    return htmx_success_message(
        {'closeActiveModal': {}, 'loadNewProject': project.projcode},
        'Project created successfully.',
        detail=f'{project.projcode} — {project.title}',
    )


# ---------------------------------------------------------------------------
# Edit Project (Phase B)
# ---------------------------------------------------------------------------

@bp.route('/project/<projcode>/edit')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def edit_project_page(projcode):
    """Full edit-project page (not a modal).

    Renders a three-tab page: Details | Allocations | Members.
    The Allocations tab is lazy-loaded on first click.
    """
    from sam.projects.projects import Project
    from sam.queries.dashboard import get_project_dashboard_data

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return redirect(url_for('admin_dashboard.index'))

    project_data = get_project_dashboard_data(db.session, projcode)

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

    return render_template(
        'dashboards/admin/edit_project.html',
        project=project,
        project_data=project_data,
        current_facility_id=current_facility_id,
        current_panel_id=current_panel_id,
        **form_data,
    )


@bp.route('/htmx/project-update/<projcode>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_project_update(projcode):
    """Validate and apply project metadata updates."""
    from sam.projects.projects import Project
    from sam.projects.areas import AreaOfInterest
    from sam.accounting.allocations import AllocationType
    from sam.core.users import User
    from sam.schemas.forms import EditProjectForm
    from marshmallow import ValidationError

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found.</div>', 404

    errors = []

    # Pre-process form data: drop empty strings so marshmallow's Int/load_default
    # treats absent/empty fields uniformly. Inject explicit booleans for unchecked
    # checkboxes (which are absent from request.form when unchecked).
    data = {k: v for k, v in request.form.items() if v != ''}
    data['charging_exempt'] = 'charging_exempt' in request.form
    data['active'] = 'active' in request.form

    try:
        form_data = EditProjectForm().load(data)
    except ValidationError as e:
        errors.extend(EditProjectForm.flatten_errors(e.messages))
        form_data = {}

    # Required-field checks not expressible in the partial schema
    if not form_data.get('title'):
        errors.append('Title is required.')
    if not form_data.get('project_lead_user_id'):
        errors.append('Project lead is required.')
    if not form_data.get('area_of_interest_id'):
        errors.append('Area of interest is required.')

    # FK existence checks — require DB access, intentionally kept in the route
    lead_id = form_data.get('project_lead_user_id')
    if lead_id and not db.session.get(User, lead_id):
        errors.append('Selected project lead does not exist.')
    admin_id = form_data.get('project_admin_user_id')
    if admin_id and not db.session.get(User, admin_id):
        errors.append('Selected project admin does not exist.')
    aoi_id = form_data.get('area_of_interest_id')
    if aoi_id and not db.session.get(AreaOfInterest, aoi_id):
        errors.append('Selected area of interest does not exist.')
    alloc_type_id = form_data.get('allocation_type_id')
    if alloc_type_id and not db.session.get(AllocationType, alloc_type_id):
        errors.append('Selected allocation type does not exist.')

    def _reload_edit_form(extra_errors=None):
        current_facility_id = None
        current_panel_id = None
        if project.allocation_type and project.allocation_type.panel:
            current_panel_id = project.allocation_type.panel_id
            if project.allocation_type.panel.facility:
                current_facility_id = project.allocation_type.panel.facility_id
        return render_template(
            'dashboards/admin/fragments/edit_project_details_htmx.html',
            project=project,
            current_facility_id=current_facility_id,
            current_panel_id=current_panel_id,
            errors=(extra_errors or []) + errors,
            form=request.form,
            **_project_form_data(form=request.form),
        )

    if errors:
        return _reload_edit_form()

    # Project.update() writes only non-None kwargs, so passing form_data as **kwargs
    # correctly preserves fields the user didn't touch.
    try:
        with management_transaction(db.session):
            project.update(**form_data)
    except Exception as e:
        return _reload_edit_form([f'Error updating project: {e}'])

    return htmx_success_message(
        {'reloadEditProjectDetails': projcode},
        'Project updated successfully.',
        detail=f'{project.projcode} — {project.title}',
    )


@bp.route('/htmx/project-allocation-tree/<projcode>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_project_allocation_tree(projcode):
    """Lazy-loaded allocation tree for the Edit Project Allocations tab.

    Builds a {projcode: {resource_name: resource_dict}} lookup for all active
    nodes in the project tree, groups resources by resource type into tabs, and
    renders accordion cards within each tab.

    Accepts an optional ?active_at=YYYY-MM-DD query parameter to show
    allocations as they were (or will be) on a given date.  Defaults to today.
    """
    from collections import OrderedDict
    from datetime import datetime
    from sam.projects.projects import Project
    from sam.queries.dashboard import _build_project_resources_data

    # Parse optional active_at date; default to today.
    active_at_str = request.args.get('active_at', '').strip()
    try:
        active_at = datetime.strptime(active_at_str, '%Y-%m-%d') if active_at_str else None
    except ValueError:
        active_at = None
    now_str = datetime.now().strftime('%Y-%m-%d')
    active_at_str = active_at.strftime('%Y-%m-%d') if active_at else now_str

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-warning">Project not found.</div>'

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

    return render_template(
        'dashboards/admin/fragments/project_allocation_tree_htmx.html',
        root=root,
        projcode=projcode,
        resources_by_tab=resources_by_tab,
        resources_by_projcode=resources_by_projcode,
        active_at=active_at_str,
        now_str=now_str,
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
@require_permission(Permission.EDIT_PROJECTS)
def htmx_project_linked_elements(projcode):
    """Render the linked-elements section for an edit-project page."""
    from sam.projects.projects import Project

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-warning">Project not found.</div>'

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
