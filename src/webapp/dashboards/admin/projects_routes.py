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
    from sam.resources.facilities import Panel

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found.</div>', 404

    errors = []

    # --- Field extraction ---
    title = request.form.get('title', '').strip()
    abstract = request.form.get('abstract', '').strip() or None
    lead_id_str = request.form.get('project_lead_user_id', '').strip()
    admin_id_str = request.form.get('project_admin_user_id', '').strip()
    aoi_id_str = request.form.get('area_of_interest_id', '').strip()
    alloc_type_id_str = request.form.get('allocation_type_id', '').strip()
    unix_gid_str = request.form.get('unix_gid', '').strip()
    ext_alias = request.form.get('ext_alias', '').strip() or None

    # Boolean fields: unchecked checkboxes send nothing, so default to False.
    charging_exempt = 'charging_exempt' in request.form
    active = 'active' in request.form

    # --- Validation ---
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
            if not db.session.get(AllocationType, allocation_type_id):
                errors.append('Selected allocation type does not exist.')
        except ValueError:
            errors.append('Invalid allocation type.')

    unix_gid = None
    if unix_gid_str:
        try:
            unix_gid = int(unix_gid_str)
        except ValueError:
            errors.append('Unix GID must be a number.')

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

    try:
        with management_transaction(db.session):
            project.update(
                title=title,
                abstract=abstract,
                area_of_interest_id=area_of_interest_id,
                allocation_type_id=allocation_type_id,
                charging_exempt=charging_exempt,
                project_lead_user_id=project_lead_user_id,
                project_admin_user_id=project_admin_user_id,
                unix_gid=unix_gid,
                ext_alias=ext_alias,
                active=active,
            )
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
    """
    from collections import OrderedDict
    from sam.projects.projects import Project
    from sam.queries.dashboard import _build_project_resources_data

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-warning">Project not found.</div>'

    root = project.get_root() if hasattr(project, 'get_root') else project

    # Always show active projects only in the allocation tree.
    all_nodes = [n for n in ([root] + root.get_descendants()) if n.active]
    resources_by_projcode = {}
    for node in all_nodes:
        node_resources = _build_project_resources_data(node)
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
    )


@bp.route('/htmx/add-allocation-form/<projcode>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_add_allocation_form(projcode):
    """Return the add-allocation sub-form (loaded into modal on button click)."""
    from sam.projects.projects import Project
    from sam.resources.resources import Resource

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-warning">Project not found.</div>'

    # Resources already linked to this project (via any account, even deleted).
    linked_resource_ids = {
        acct.resource_id for acct in project.accounts
    }

    # Optional resource-type filter passed from the allocation tab button.
    # e.g. ?rtypes=HPC,DAV  limits the dropdown to those resource types.
    rtypes_str = request.args.get('rtypes', '').strip()
    rtypes_filter = [r.strip() for r in rtypes_str.split(',') if r.strip()]

    # Offer all active resources not yet linked (filtered by type if requested).
    available_resources = (
        db.session.query(Resource)
        .filter(Resource.is_active)
        .order_by(Resource.resource_name)
        .all()
    )
    available_resources = [
        r for r in available_resources
        if r.resource_id not in linked_resource_ids
        and (not rtypes_filter
             or (r.resource_type and r.resource_type.resource_type in rtypes_filter))
    ]

    return render_template(
        'dashboards/admin/fragments/add_allocation_form_htmx.html',
        project=project,
        available_resources=available_resources,
        rtypes_str=rtypes_str,
        today=datetime.now().strftime('%Y-%m-%d'),
    )


@bp.route('/htmx/add-allocation/<projcode>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_add_allocation(projcode):
    """Create a new account + allocation for the project."""
    from sam.projects.projects import Project
    from sam.resources.resources import Resource
    from sam.manage.allocations import create_allocation

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found.</div>', 404

    errors = []

    resource_id_str = request.form.get('resource_id', '').strip()
    amount_str = request.form.get('amount', '').strip()
    start_date_str = request.form.get('start_date', '').strip()
    end_date_str = request.form.get('end_date', '').strip()
    description = request.form.get('description', '').strip() or None

    resource = None
    if not resource_id_str:
        errors.append('Resource is required.')
    else:
        try:
            resource = db.session.get(Resource, int(resource_id_str))
            if not resource:
                errors.append('Selected resource does not exist.')
        except ValueError:
            errors.append('Invalid resource.')

    amount = None
    if not amount_str:
        errors.append('Amount is required.')
    else:
        try:
            amount = float(amount_str)
            if amount <= 0:
                errors.append('Amount must be greater than zero.')
        except ValueError:
            errors.append('Amount must be a number.')

    start_date = end_date = None
    if not start_date_str:
        errors.append('Start date is required.')
    else:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        except ValueError:
            errors.append('Invalid start date.')

    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(
                hour=23, minute=59, second=59)
            if start_date and end_date < start_date:
                errors.append('End date must be on or after start date.')
        except ValueError:
            errors.append('Invalid end date.')

    # Preserve resource-type filter from the hidden form field on re-render.
    rtypes_str = request.form.get('rtypes_str', '').strip()
    rtypes_filter = [r.strip() for r in rtypes_str.split(',') if r.strip()]

    def _reload_add_form(extra_errors=None):
        from sam.resources.resources import Resource as R
        linked_ids = {a.resource_id for a in project.accounts}
        available = (
            db.session.query(R)
            .filter(R.is_active)
            .order_by(R.resource_name)
            .all()
        )
        available = [
            r for r in available
            if r.resource_id not in linked_ids
            and (not rtypes_filter
                 or (r.resource_type and r.resource_type.resource_type in rtypes_filter))
        ]
        return render_template(
            'dashboards/admin/fragments/add_allocation_form_htmx.html',
            project=project,
            available_resources=available,
            rtypes_str=rtypes_str,
            today=datetime.now().strftime('%Y-%m-%d'),
            errors=(extra_errors or []) + errors,
            form=request.form,
        )

    if errors:
        return _reload_add_form()

    try:
        with management_transaction(db.session):
            create_allocation(
                db.session,
                project_id=project.project_id,
                resource_id=resource.resource_id,
                amount=amount,
                start_date=start_date,
                end_date=end_date,
                description=description,
                user_id=current_user.user_id,
            )
    except Exception as e:
        return _reload_add_form([f'Error creating allocation: {e}'])

    return htmx_success_message(
        {'closeActiveModal': {}, 'reloadAllocationTree': projcode},
        'Allocation created successfully.',
        detail=f'{project.projcode} — {resource.resource_name}',
    )


@bp.route('/htmx/edit-allocation-form/<int:alloc_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_edit_allocation_form(alloc_id):
    """Return the edit-allocation form fragment (loaded into modal)."""
    from sam.accounting.allocations import Allocation

    allocation = db.session.get(Allocation, alloc_id)
    if not allocation:
        return '<div class="alert alert-warning">Allocation not found.</div>'

    projcode = allocation.account.project.projcode if allocation.account else ''

    return render_template(
        'dashboards/admin/fragments/edit_allocation_form_htmx.html',
        allocation=allocation,
        projcode=projcode,
    )


@bp.route('/htmx/edit-allocation/<int:alloc_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_edit_allocation(alloc_id):
    """Validate and apply allocation edits with cascade + audit logging."""
    from sam.accounting.allocations import Allocation, InheritingAllocationException
    from sam.manage.allocations import update_allocation

    allocation = db.session.get(Allocation, alloc_id)
    if not allocation:
        return '<div class="alert alert-danger">Allocation not found.</div>', 404

    projcode = allocation.account.project.projcode if allocation.account else ''

    errors = []

    amount_str = request.form.get('amount', '').strip()
    start_date_str = request.form.get('start_date', '').strip()
    end_date_str = request.form.get('end_date', '').strip()
    description = request.form.get('description', '').strip() or None

    updates = {}

    if amount_str:
        try:
            updates['amount'] = float(amount_str)
            if updates['amount'] <= 0:
                errors.append('Amount must be greater than zero.')
        except ValueError:
            errors.append('Amount must be a number.')

    if start_date_str:
        try:
            updates['start_date'] = datetime.strptime(start_date_str, '%Y-%m-%d')
        except ValueError:
            errors.append('Invalid start date.')

    if end_date_str:
        try:
            updates['end_date'] = datetime.strptime(end_date_str, '%Y-%m-%d').replace(
                hour=23, minute=59, second=59)
        except ValueError:
            errors.append('Invalid end date.')
    elif 'end_date' in request.form:
        # Explicit empty string → clear end date (open-ended).
        updates['end_date'] = None

    if 'description' in request.form:
        updates['description'] = description

    def _reload_edit_form(extra_errors=None):
        return render_template(
            'dashboards/admin/fragments/edit_allocation_form_htmx.html',
            allocation=allocation,
            projcode=projcode,
            errors=(extra_errors or []) + errors,
            form=request.form,
        )

    if errors:
        return _reload_edit_form()

    if not updates:
        return _reload_edit_form(['No changes provided.'])

    try:
        with management_transaction(db.session):
            update_allocation(
                db.session,
                alloc_id,
                current_user.user_id,
                **updates,
            )
    except InheritingAllocationException:
        return _reload_edit_form([
            'Cannot directly edit an inherited (child) allocation. '
            'Edit the parent allocation instead — changes will cascade automatically.'
        ])
    except Exception as e:
        return _reload_edit_form([f'Error updating allocation: {e}'])

    return htmx_success_message(
        {'closeActiveModal': {}, 'reloadAllocationTree': projcode},
        'Allocation updated successfully.',
    )
