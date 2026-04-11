"""
Admin dashboard — Project management routes.

Covers: Project creation (Phase A).  Edit/allocation management (Phase B).
"""

import re

from flask import render_template, request
from webapp.utils.htmx import htmx_success
from flask_login import login_required

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

    return htmx_success(
        'dashboards/admin/fragments/project_create_success_htmx.html',
        {'closeActiveModal': {}, 'loadNewProject': project.projcode},
        project=project,
    )
