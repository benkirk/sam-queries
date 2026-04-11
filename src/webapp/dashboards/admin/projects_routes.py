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

def _project_form_data() -> dict:
    """Load form option lists shared by create (and later edit) forms."""
    from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup
    from sam.accounting.allocations import AllocationType
    from sam.resources.facilities import Facility
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
    alloc_types = (
        db.session.query(AllocationType)
        .order_by(AllocationType.allocation_type)
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
    return dict(
        areas=areas,
        aoi_groups=aoi_groups,
        alloc_types=alloc_types,
        facilities=facilities,
        mnemonics=mnemonics,
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
    from sam.projects.projects import Project
    from sam.projects.areas import AreaOfInterest
    from sam.core.users import User

    errors = []

    # ── Raw field extraction ──
    projcode_raw = request.form.get('projcode', '').strip().upper()
    title = request.form.get('title', '').strip()
    abstract = request.form.get('abstract', '').strip() or None
    lead_id_str = request.form.get('project_lead_user_id', '').strip()
    admin_id_str = request.form.get('project_admin_user_id', '').strip()
    aoi_id_str = request.form.get('area_of_interest_id', '').strip()
    alloc_type_id_str = request.form.get('allocation_type_id', '').strip()
    parent_id_str = request.form.get('parent_id', '').strip()
    charging_exempt = request.form.get('charging_exempt') == 'on'
    unix_gid_str = request.form.get('unix_gid', '').strip()
    ext_alias = request.form.get('ext_alias', '').strip() or None

    # ── Validation ──
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

    unix_gid = None
    if unix_gid_str:
        try:
            unix_gid = int(unix_gid_str)
        except ValueError:
            errors.append('Unix GID must be a number.')

    def _reload_form(extra_errors=None):
        return render_template(
            'dashboards/admin/fragments/create_project_form_htmx.html',
            **_project_form_data(),
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
    except Exception as e:
        return _reload_form([f'Error creating project: {e}'])

    return htmx_success(
        'dashboards/admin/fragments/project_create_success_htmx.html',
        {'closeActiveModal': {}, 'loadNewProject': {'projcode': project.projcode}},
        project=project,
    )
