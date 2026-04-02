"""
Admin dashboard — Organization management routes.

Covers: Organizations, Institutions, Institution Types, Areas of Interest,
AOI Groups, Contract Sources, Contracts, NSF Programs.
"""

from flask import render_template, request
from flask_login import login_required
from datetime import datetime
from webapp.api.helpers import parse_input_end_date

from webapp.extensions import db, cache
from webapp.utils.rbac import require_permission, Permission
from sam.manage import management_transaction
from sam.core.users import User

from .blueprint import bp


# ── Organization Card ──────────────────────────────────────────────────────


@bp.route('/htmx/organizations-card')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
@cache.cached(timeout=300, query_string=True)
def htmx_organizations_card():
    """
    Return the Organization card body fragment with seven tabs:
    Organizations, Institutions, AOI Groups, Areas of Interest,
    Contract Sources, Contracts, NSF Programs.
    Lazy-loaded when the Organization collapsible section is first expanded.
    """
    from sam.core.organizations import Organization, Institution, InstitutionType, UserInstitution, MnemonicCode
    from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup
    from sam.projects.contracts import Contract, ContractSource, NSFProgram
    from sam.projects.projects import Project
    from sqlalchemy.orm import subqueryload, selectinload, lazyload

    active_only = request.args.get('active_only') == '1'
    now = datetime.now()

    org_q = db.session.query(Organization).options(
        subqueryload(Organization.children),
        selectinload(Organization.users),
    )
    if active_only:
        org_q = org_q.filter(Organization.is_active)
    organizations = org_q.all()

    # Build DFS-ordered flat tree: [(org, depth, has_children)]
    _children = {}
    for _o in organizations:
        _pid = _o.parent_org_id
        _children.setdefault(_pid, []).append(_o)
    for _pid in _children:
        _children[_pid].sort(key=lambda o: o.acronym or '')

    def _dfs(_pid, _depth):
        result = []
        for _o in _children.get(_pid, []):
            _has_ch = bool(_children.get(_o.organization_id))
            result.append((_o, _depth, _has_ch))
            result.extend(_dfs(_o.organization_id, _depth + 1))
        return result

    org_tree = _dfs(None, 0)

    institution_types = db.session.query(InstitutionType).options(
        selectinload(InstitutionType.institutions)
            .selectinload(Institution.users)
            .selectinload(UserInstitution.user)
            .lazyload(User.accounts),
        selectinload(InstitutionType.institutions)
            .selectinload(Institution.users)
            .selectinload(UserInstitution.user)
            .lazyload(User.email_addresses),
    ).order_by(InstitutionType.type).all()
    institutions = db.session.query(Institution).options(
        selectinload(Institution.users)
            .selectinload(UserInstitution.user)
            .lazyload(User.accounts),
        selectinload(Institution.users)
            .selectinload(UserInstitution.user)
            .lazyload(User.email_addresses),
    ).order_by(Institution.name).all()

    aoi_group_q = db.session.query(AreaOfInterestGroup).options(
        selectinload(AreaOfInterestGroup.areas),
    ).order_by(AreaOfInterestGroup.name)
    if active_only:
        aoi_group_q = aoi_group_q.filter(AreaOfInterestGroup.is_active)
    aoi_groups = aoi_group_q.all()

    aoi_q = db.session.query(AreaOfInterest).options(
        selectinload(AreaOfInterest.projects).lazyload(Project.accounts),
    ).order_by(AreaOfInterest.area_of_interest)
    if active_only:
        aoi_q = aoi_q.filter(AreaOfInterest.is_active)
    aois = aoi_q.all()

    cs_q = db.session.query(ContractSource).order_by(ContractSource.contract_source)
    if active_only:
        cs_q = cs_q.filter(ContractSource.is_active)
    contract_sources = cs_q.all()

    contract_q = db.session.query(Contract).options(
        selectinload(Contract.principal_investigator)
            .lazyload(User.accounts),
        selectinload(Contract.principal_investigator)
            .lazyload(User.email_addresses),
    ).order_by(Contract.contract_number)
    if active_only:
        contract_q = contract_q.filter(Contract.is_active)
    contracts = contract_q.all()

    nsf_q = db.session.query(NSFProgram).options(
        selectinload(NSFProgram.contracts),
    ).order_by(NSFProgram.nsf_program_name)
    if active_only:
        nsf_q = nsf_q.filter(NSFProgram.is_active)
    nsf_programs = nsf_q.all()

    _mc_lookup = MnemonicCode.build_lookup(db.session)
    inst_to_mnemonic = {
        inst.institution_id: MnemonicCode.resolve_for_institution(inst, _mc_lookup)
        for inst in institutions
    }
    org_to_mnemonic = {
        org.organization_id: MnemonicCode.resolve_for_organization(org, _mc_lookup)
        for org in organizations
    }

    return render_template(
        'dashboards/admin/fragments/organization_card.html',
        organizations=organizations,
        org_tree=org_tree,
        institution_types=institution_types,
        institutions=institutions,
        aoi_groups=aoi_groups,
        aois=aois,
        contract_sources=contract_sources,
        contracts=contracts,
        nsf_programs=nsf_programs,
        inst_to_mnemonic=inst_to_mnemonic,
        org_to_mnemonic=org_to_mnemonic,
        is_admin=True,
        now=now,
        active_only=active_only,
    )


# ── Organization Edit ──────────────────────────────────────────────────────


@bp.route('/htmx/organization-edit-form/<int:org_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_organization_edit_form(org_id):
    """Return the organization edit form fragment (loaded into modal)."""
    from sam.core.organizations import Organization

    org = db.session.get(Organization, org_id)
    if not org:
        return '<div class="alert alert-warning">Organization not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_organization_form_htmx.html',
        org=org,
    )


@bp.route('/htmx/organization-edit/<int:org_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_organization_edit(org_id):
    """Update an organization."""
    from sam.core.organizations import Organization

    org = db.session.get(Organization, org_id)
    if not org:
        return '<div class="alert alert-danger">Organization not found</div>', 404

    errors = []
    name = request.form.get('name', '').strip()
    acronym = request.form.get('acronym', '').strip()
    description = request.form.get('description', '').strip()
    active = bool(request.form.get('active'))

    if not name:
        errors.append('Name is required.')
    if not acronym:
        errors.append('Acronym is required.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_organization_form_htmx.html',
            org=org, errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            org.update(
                name=name, acronym=acronym,
                description=description or None,
                active=active,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_organization_form_htmx.html',
            org=org, errors=[f'Error updating organization: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


# ── Organization Create ────────────────────────────────────────────────────


@bp.route('/htmx/organization-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_organization_create_form():
    """Return the organization create form fragment (loaded into modal)."""
    from sam.core.organizations import Organization

    # Offer existing active orgs as optional parent
    parent_orgs = (
        db.session.query(Organization)
        .filter(Organization.is_active)
        .order_by(Organization.name)
        .all()
    )

    return render_template(
        'dashboards/admin/fragments/create_organization_form_htmx.html',
        parent_orgs=parent_orgs,
    )


@bp.route('/htmx/organization-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_organization_create():
    """Create a new organization."""
    from sam.core.organizations import Organization

    errors = []
    name = request.form.get('name', '').strip()
    acronym = request.form.get('acronym', '').strip()
    description = request.form.get('description', '').strip()
    parent_id_str = request.form.get('parent_org_id', '').strip()

    if not name:
        errors.append('Name is required.')
    if not acronym:
        errors.append('Acronym is required.')

    parent_org_id = None
    if parent_id_str:
        try:
            parent_org_id = int(parent_id_str)
        except ValueError:
            errors.append('Invalid parent organization.')

    def _reload_form(extra_errors=None):
        parent_orgs = (
            db.session.query(Organization)
            .filter(Organization.is_active)
            .order_by(Organization.name)
            .all()
        )
        return render_template(
            'dashboards/admin/fragments/create_organization_form_htmx.html',
            parent_orgs=parent_orgs,
            errors=(extra_errors or []) + errors,
            form=request.form,
        )

    if errors:
        return _reload_form()

    try:
        with management_transaction(db.session):
            Organization.create(
                db.session,
                name=name,
                acronym=acronym,
                description=description or None,
                parent_org_id=parent_org_id,
            )
    except Exception as e:
        return _reload_form([f'Error creating organization: {e}'])

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


# ── Organization Delete ────────────────────────────────────────────────────


@bp.route('/htmx/organization-delete/<int:org_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_organization_delete(org_id):
    """Soft-delete (deactivate) an organization."""
    from sam.core.organizations import Organization

    org = db.session.get(Organization, org_id)
    if not org:
        return '<div class="alert alert-danger">Organization not found</div>', 404

    try:
        with management_transaction(db.session):
            org.update(active=False)
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return ''


# ── Institution Type Edit ──────────────────────────────────────────────────


@bp.route('/htmx/institution-type-edit-form/<int:institution_type_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_institution_type_edit_form(institution_type_id):
    """Return the institution type edit form fragment (loaded into modal)."""
    from sam.core.organizations import InstitutionType

    inst_type = db.session.get(InstitutionType, institution_type_id)
    if not inst_type:
        return '<div class="alert alert-warning">Institution type not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_institution_type_form_htmx.html',
        inst_type=inst_type,
    )


@bp.route('/htmx/institution-type-edit/<int:institution_type_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_institution_type_edit(institution_type_id):
    """Update an institution type."""
    from sam.core.organizations import InstitutionType

    inst_type = db.session.get(InstitutionType, institution_type_id)
    if not inst_type:
        return '<div class="alert alert-danger">Institution type not found</div>', 404

    errors = []
    type_name = request.form.get('type', '').strip()

    if not type_name:
        errors.append('Type name is required.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_institution_type_form_htmx.html',
            inst_type=inst_type, errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            inst_type.update(type=type_name)
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_institution_type_form_htmx.html',
            inst_type=inst_type, errors=[f'Error updating institution type: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


# ── Institution Type Create ────────────────────────────────────────────────


@bp.route('/htmx/institution-type-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_institution_type_create_form():
    """Return the institution type create form fragment (loaded into modal)."""
    return render_template('dashboards/admin/fragments/create_institution_type_form_htmx.html')


@bp.route('/htmx/institution-type-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_institution_type_create():
    """Create a new institution type."""
    from sam.core.organizations import InstitutionType

    errors = []
    type_name = request.form.get('type', '').strip()

    if not type_name:
        errors.append('Type name is required.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/create_institution_type_form_htmx.html',
            errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            InstitutionType.create(db.session, type=type_name)
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/create_institution_type_form_htmx.html',
            errors=[f'Error creating institution type: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


# ── Institution Edit ───────────────────────────────────────────────────────


@bp.route('/htmx/institution-edit-form/<int:inst_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_institution_edit_form(inst_id):
    """Return the institution edit form fragment (loaded into modal)."""
    from sam.core.organizations import Institution

    institution = db.session.get(Institution, inst_id)
    if not institution:
        return '<div class="alert alert-warning">Institution not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_institution_form_htmx.html',
        institution=institution,
    )


@bp.route('/htmx/institution-edit/<int:inst_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_institution_edit(inst_id):
    """Update an institution."""
    from sam.core.organizations import Institution

    institution = db.session.get(Institution, inst_id)
    if not institution:
        return '<div class="alert alert-danger">Institution not found</div>', 404

    errors = []
    name = request.form.get('name', '').strip()
    acronym = request.form.get('acronym', '').strip()

    if not name:
        errors.append('Name is required.')
    if not acronym:
        errors.append('Acronym is required.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_institution_form_htmx.html',
            institution=institution, errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            institution.update(
                name=name,
                acronym=acronym,
                nsf_org_code=request.form.get('nsf_org_code', '').strip() or None,
                address=request.form.get('address', '').strip() or None,
                city=request.form.get('city', '').strip() or None,
                zip=request.form.get('zip', '').strip() or None,
                code=request.form.get('code', '').strip() or None,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_institution_form_htmx.html',
            institution=institution, errors=[f'Error updating institution: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


# ── Institution Create ─────────────────────────────────────────────────────


@bp.route('/htmx/institution-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_institution_create_form():
    """Return the institution create form fragment (loaded into modal)."""
    from sam.core.organizations import InstitutionType

    institution_types = db.session.query(InstitutionType).order_by(InstitutionType.type).all()

    return render_template(
        'dashboards/admin/fragments/create_institution_form_htmx.html',
        institution_types=institution_types,
    )


@bp.route('/htmx/institution-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_institution_create():
    """Create a new institution."""
    from sam.core.organizations import Institution, InstitutionType

    errors = []
    name = request.form.get('name', '').strip()
    acronym = request.form.get('acronym', '').strip()
    nsf_org_code = request.form.get('nsf_org_code', '').strip()
    city = request.form.get('city', '').strip()
    code = request.form.get('code', '').strip()
    institution_type_id_str = request.form.get('institution_type_id', '').strip()

    if not name:
        errors.append('Name is required.')
    if not acronym:
        errors.append('Acronym is required.')

    institution_type_id = None
    if institution_type_id_str:
        try:
            institution_type_id = int(institution_type_id_str)
        except ValueError:
            errors.append('Invalid institution type selection.')

    def _reload_form(extra_errors=None):
        institution_types = db.session.query(InstitutionType).order_by(InstitutionType.type).all()
        return render_template(
            'dashboards/admin/fragments/create_institution_form_htmx.html',
            institution_types=institution_types,
            errors=(extra_errors or []) + errors,
            form=request.form,
        )

    if errors:
        return _reload_form()

    try:
        with management_transaction(db.session):
            Institution.create(
                db.session,
                name=name,
                acronym=acronym,
                nsf_org_code=nsf_org_code or None,
                city=city or None,
                code=code or None,
                institution_type_id=institution_type_id,
            )
    except Exception as e:
        return _reload_form([f'Error creating institution: {e}'])

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


# ── Mnemonic Code Create ───────────────────────────────────────────────────


@bp.route('/htmx/mnemonic-code-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_mnemonic_code_create_form():
    """Return the mnemonic code create form fragment (loaded into modal)."""
    from sam.core.organizations import Institution, Organization

    prefill_description = request.args.get('description', '')

    institutions = (
        db.session.query(Institution)
        .order_by(Institution.name)
        .all()
    )
    organizations = (
        db.session.query(Organization)
        .filter(Organization.is_active)
        .order_by(Organization.name)
        .all()
    )
    return render_template(
        'dashboards/admin/fragments/create_mnemonic_code_form_htmx.html',
        institutions=institutions,
        organizations=organizations,
        prefill_description=prefill_description,
    )


@bp.route('/htmx/mnemonic-code-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_mnemonic_code_create():
    """Create a new mnemonic code."""
    import re
    from sam.core.organizations import Institution, MnemonicCode, Organization

    errors = []
    code = request.form.get('code', '').strip().upper()
    description = request.form.get('description', '').strip()

    if not code:
        errors.append('Code is required.')
    elif not re.fullmatch(r'[A-Z]{3}', code):
        errors.append('Code must be exactly 3 uppercase letters (A–Z).')
    elif db.session.query(MnemonicCode).filter_by(code=code).first():
        errors.append(f'Code "{code}" already exists.')

    if not description:
        errors.append('Description is required.')
    elif db.session.query(MnemonicCode).filter_by(description=description).first():
        errors.append(f'Description "{description}" is already in use by another mnemonic code.')

    def _reload_form(extra_errors=None):
        institutions = db.session.query(Institution).order_by(Institution.name).all()
        organizations = (
            db.session.query(Organization)
            .filter(Organization.is_active)
            .order_by(Organization.name)
            .all()
        )
        return render_template(
            'dashboards/admin/fragments/create_mnemonic_code_form_htmx.html',
            institutions=institutions,
            organizations=organizations,
            prefill_description='',
            errors=(extra_errors or []) + errors,
            form=request.form,
        )

    if errors:
        return _reload_form()

    try:
        with management_transaction(db.session):
            MnemonicCode.create(db.session, code=code, description=description)
    except Exception as e:
        return _reload_form([f'Error creating mnemonic code: {e}'])

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


# ── AOI Group Edit ─────────────────────────────────────────────────────────


@bp.route('/htmx/aoi-group-edit-form/<int:group_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_aoi_group_edit_form(group_id):
    """Return the AOI group edit form fragment (loaded into modal)."""
    from sam.projects.areas import AreaOfInterestGroup

    group = db.session.get(AreaOfInterestGroup, group_id)
    if not group:
        return '<div class="alert alert-warning">AOI group not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_aoi_group_form_htmx.html',
        group=group,
    )


@bp.route('/htmx/aoi-group-edit/<int:group_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_aoi_group_edit(group_id):
    """Update an AOI group."""
    from sam.projects.areas import AreaOfInterestGroup

    group = db.session.get(AreaOfInterestGroup, group_id)
    if not group:
        return '<div class="alert alert-danger">AOI group not found</div>', 404

    errors = []
    name = request.form.get('name', '').strip()
    active = bool(request.form.get('active'))

    if not name:
        errors.append('Name is required.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_aoi_group_form_htmx.html',
            group=group, errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            group.update(name=name, active=active)
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_aoi_group_form_htmx.html',
            group=group, errors=[f'Error updating AOI group: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


# ── AOI Group Create ───────────────────────────────────────────────────────


@bp.route('/htmx/aoi-group-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_aoi_group_create_form():
    """Return the AOI group create form fragment."""
    return render_template('dashboards/admin/fragments/create_aoi_group_form_htmx.html')


@bp.route('/htmx/aoi-group-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_aoi_group_create():
    """Create a new AOI group."""
    from sam.projects.areas import AreaOfInterestGroup

    errors = []
    name = request.form.get('name', '').strip()

    if not name:
        errors.append('Name is required.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/create_aoi_group_form_htmx.html',
            errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            AreaOfInterestGroup.create(db.session, name=name)
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/create_aoi_group_form_htmx.html',
            errors=[f'Error creating AOI group: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


# ── AOI Group Delete ───────────────────────────────────────────────────────


@bp.route('/htmx/aoi-group-delete/<int:group_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_aoi_group_delete(group_id):
    """Soft-delete (deactivate) an AOI group."""
    from sam.projects.areas import AreaOfInterestGroup

    group = db.session.get(AreaOfInterestGroup, group_id)
    if not group:
        return '<div class="alert alert-danger">AOI group not found</div>', 404

    try:
        with management_transaction(db.session):
            group.update(active=False)
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return ''


# ── AOI Edit ───────────────────────────────────────────────────────────────


@bp.route('/htmx/aoi-edit-form/<int:aoi_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_aoi_edit_form(aoi_id):
    """Return the AOI edit form fragment (loaded into modal)."""
    from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup

    aoi = db.session.get(AreaOfInterest, aoi_id)
    if not aoi:
        return '<div class="alert alert-warning">Area of interest not found</div>'

    aoi_groups = db.session.query(AreaOfInterestGroup).order_by(AreaOfInterestGroup.name).all()

    return render_template(
        'dashboards/admin/fragments/edit_aoi_form_htmx.html',
        aoi=aoi,
        aoi_groups=aoi_groups,
    )


@bp.route('/htmx/aoi-edit/<int:aoi_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_aoi_edit(aoi_id):
    """Update an area of interest."""
    from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup

    aoi = db.session.get(AreaOfInterest, aoi_id)
    if not aoi:
        return '<div class="alert alert-danger">Area of interest not found</div>', 404

    errors = []
    area_of_interest = request.form.get('area_of_interest', '').strip()
    group_id_str = request.form.get('area_of_interest_group_id', '').strip()
    active = bool(request.form.get('active'))

    if not area_of_interest:
        errors.append('Name is required.')

    group_id = None
    if group_id_str:
        try:
            group_id = int(group_id_str)
        except ValueError:
            errors.append('Invalid group selection.')
    else:
        errors.append('Group is required.')

    if errors:
        aoi_groups = db.session.query(AreaOfInterestGroup).order_by(AreaOfInterestGroup.name).all()
        return render_template(
            'dashboards/admin/fragments/edit_aoi_form_htmx.html',
            aoi=aoi, aoi_groups=aoi_groups, errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            aoi.update(
                area_of_interest=area_of_interest,
                area_of_interest_group_id=group_id,
                active=active,
            )
    except Exception as e:
        aoi_groups = db.session.query(AreaOfInterestGroup).order_by(AreaOfInterestGroup.name).all()
        return render_template(
            'dashboards/admin/fragments/edit_aoi_form_htmx.html',
            aoi=aoi, aoi_groups=aoi_groups,
            errors=[f'Error updating area of interest: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


# ── AOI Create ─────────────────────────────────────────────────────────────


@bp.route('/htmx/aoi-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_aoi_create_form():
    """Return the AOI create form fragment."""
    from sam.projects.areas import AreaOfInterestGroup

    aoi_groups = db.session.query(AreaOfInterestGroup).filter(
        AreaOfInterestGroup.is_active
    ).order_by(AreaOfInterestGroup.name).all()

    return render_template(
        'dashboards/admin/fragments/create_aoi_form_htmx.html',
        aoi_groups=aoi_groups,
    )


@bp.route('/htmx/aoi-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_aoi_create():
    """Create a new area of interest."""
    from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup

    errors = []
    area_of_interest = request.form.get('area_of_interest', '').strip()
    group_id_str = request.form.get('area_of_interest_group_id', '').strip()

    if not area_of_interest:
        errors.append('Name is required.')

    group_id = None
    if group_id_str:
        try:
            group_id = int(group_id_str)
        except ValueError:
            errors.append('Invalid group selection.')
    else:
        errors.append('Group is required.')

    def _reload_form(extra_errors=None):
        aoi_groups = db.session.query(AreaOfInterestGroup).filter(
            AreaOfInterestGroup.is_active
        ).order_by(AreaOfInterestGroup.name).all()
        return render_template(
            'dashboards/admin/fragments/create_aoi_form_htmx.html',
            aoi_groups=aoi_groups,
            errors=(extra_errors or []) + errors,
            form=request.form,
        )

    if errors:
        return _reload_form()

    try:
        with management_transaction(db.session):
            AreaOfInterest.create(
                db.session,
                area_of_interest=area_of_interest,
                area_of_interest_group_id=group_id,
            )
    except Exception as e:
        return _reload_form([f'Error creating area of interest: {e}'])

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


# ── AOI Delete ─────────────────────────────────────────────────────────────


@bp.route('/htmx/aoi-delete/<int:aoi_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_aoi_delete(aoi_id):
    """Soft-delete (deactivate) an area of interest."""
    from sam.projects.areas import AreaOfInterest

    aoi = db.session.get(AreaOfInterest, aoi_id)
    if not aoi:
        return '<div class="alert alert-danger">Area of interest not found</div>', 404

    try:
        with management_transaction(db.session):
            aoi.update(active=False)
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return ''


# ── Contract Source Edit ───────────────────────────────────────────────────


@bp.route('/htmx/contract-source-edit-form/<int:source_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_contract_source_edit_form(source_id):
    """Return the contract source edit form fragment (loaded into modal)."""
    from sam.projects.contracts import ContractSource

    source = db.session.get(ContractSource, source_id)
    if not source:
        return '<div class="alert alert-warning">Contract source not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_contract_source_form_htmx.html',
        source=source,
    )


@bp.route('/htmx/contract-source-edit/<int:source_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_contract_source_edit(source_id):
    """Update a contract source."""
    from sam.projects.contracts import ContractSource

    source = db.session.get(ContractSource, source_id)
    if not source:
        return '<div class="alert alert-danger">Contract source not found</div>', 404

    errors = []
    contract_source = request.form.get('contract_source', '').strip()
    active = bool(request.form.get('active'))

    if not contract_source:
        errors.append('Source name is required.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_contract_source_form_htmx.html',
            source=source, errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            source.update(contract_source=contract_source, active=active)
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_contract_source_form_htmx.html',
            source=source, errors=[f'Error updating contract source: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


# ── Contract Source Create ─────────────────────────────────────────────────


@bp.route('/htmx/contract-source-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_contract_source_create_form():
    """Return the contract source create form fragment."""
    return render_template('dashboards/admin/fragments/create_contract_source_form_htmx.html')


@bp.route('/htmx/contract-source-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_contract_source_create():
    """Create a new contract source."""
    from sam.projects.contracts import ContractSource

    errors = []
    contract_source = request.form.get('contract_source', '').strip()

    if not contract_source:
        errors.append('Source name is required.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/create_contract_source_form_htmx.html',
            errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            ContractSource.create(db.session, contract_source=contract_source)
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/create_contract_source_form_htmx.html',
            errors=[f'Error creating contract source: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


# ── Contract Source Delete ─────────────────────────────────────────────────


@bp.route('/htmx/contract-source-delete/<int:source_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_contract_source_delete(source_id):
    """Soft-delete (deactivate) a contract source."""
    from sam.projects.contracts import ContractSource

    source = db.session.get(ContractSource, source_id)
    if not source:
        return '<div class="alert alert-danger">Contract source not found</div>', 404

    try:
        with management_transaction(db.session):
            source.update(active=False)
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return ''


# ── Contract Edit ──────────────────────────────────────────────────────────


@bp.route('/htmx/contract-edit-form/<int:contract_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_contract_edit_form(contract_id):
    """Return the contract edit form fragment (loaded into modal)."""
    from sam.projects.contracts import Contract

    contract = db.session.get(Contract, contract_id)
    if not contract:
        return '<div class="alert alert-warning">Contract not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_contract_form_htmx.html',
        contract=contract,
    )


@bp.route('/htmx/contract-edit/<int:contract_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_contract_edit(contract_id):
    """Update a contract."""
    from sam.projects.contracts import Contract

    contract = db.session.get(Contract, contract_id)
    if not contract:
        return '<div class="alert alert-danger">Contract not found</div>', 404

    errors = []
    title = request.form.get('title', '').strip()
    url = request.form.get('url', '').strip()
    start_str = request.form.get('start_date', '').strip()
    end_str = request.form.get('end_date', '').strip()

    if not title:
        errors.append('Title is required.')

    start_date = None
    if start_str:
        try:
            start_date = datetime.strptime(start_str, '%Y-%m-%d')
        except ValueError:
            errors.append('Invalid start date format.')
    else:
        errors.append('Start date is required.')

    end_date = None
    if end_str:
        try:
            end_date = parse_input_end_date(end_str)
            effective_start = start_date or contract.start_date
            if effective_start and end_date <= effective_start:
                errors.append('End date must be after start date.')
        except ValueError:
            errors.append('Invalid end date format.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_contract_form_htmx.html',
            contract=contract, errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            contract.update(
                title=title,
                url=url or None,
                start_date=start_date,
                end_date=end_date,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_contract_form_htmx.html',
            contract=contract, errors=[f'Error updating contract: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


# ── Contract Create ────────────────────────────────────────────────────────


@bp.route('/htmx/contract-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_contract_create_form():
    """Return the contract create form fragment."""
    from sam.projects.contracts import ContractSource

    contract_sources = db.session.query(ContractSource).filter(
        ContractSource.is_active
    ).order_by(ContractSource.contract_source).all()

    return render_template(
        'dashboards/admin/fragments/create_contract_form_htmx.html',
        contract_sources=contract_sources,
        today=datetime.now().strftime('%Y-%m-%d'),
    )


@bp.route('/htmx/contract-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_contract_create():
    """Create a new contract."""
    from sam.projects.contracts import Contract, ContractSource

    errors = []
    contract_number = request.form.get('contract_number', '').strip()
    title = request.form.get('title', '').strip()
    url = request.form.get('url', '').strip()
    start_str = request.form.get('start_date', '').strip()
    end_str = request.form.get('end_date', '').strip()
    source_id_str = request.form.get('contract_source_id', '').strip()
    pi_user_id_str = request.form.get('principal_investigator_user_id', '').strip()

    if not contract_number:
        errors.append('Contract number is required.')
    if not title:
        errors.append('Title is required.')
    if not pi_user_id_str:
        errors.append('Principal investigator is required.')

    contract_source_id = None
    if source_id_str:
        try:
            contract_source_id = int(source_id_str)
        except ValueError:
            errors.append('Invalid contract source selection.')
    else:
        errors.append('Contract source is required.')

    pi_user_id = None
    if pi_user_id_str:
        try:
            pi_user_id = int(pi_user_id_str)
        except ValueError:
            errors.append('Invalid principal investigator selection.')

    start_date = None
    if start_str:
        try:
            start_date = datetime.strptime(start_str, '%Y-%m-%d')
        except ValueError:
            errors.append('Invalid start date format.')
    else:
        errors.append('Start date is required.')

    end_date = None
    if end_str:
        try:
            end_date = parse_input_end_date(end_str)
            if start_date and end_date <= start_date:
                errors.append('End date must be after start date.')
        except ValueError:
            errors.append('Invalid end date format.')

    def _reload_form(extra_errors=None):
        contract_sources = db.session.query(ContractSource).filter(
            ContractSource.is_active
        ).order_by(ContractSource.contract_source).all()
        return render_template(
            'dashboards/admin/fragments/create_contract_form_htmx.html',
            contract_sources=contract_sources,
            today=datetime.now().strftime('%Y-%m-%d'),
            errors=(extra_errors or []) + errors,
            form=request.form,
        )

    if errors:
        return _reload_form()

    try:
        with management_transaction(db.session):
            Contract.create(
                db.session,
                contract_number=contract_number,
                title=title,
                url=url or None,
                start_date=start_date,
                end_date=end_date,
                contract_source_id=contract_source_id,
                principal_investigator_user_id=pi_user_id,
            )
    except Exception as e:
        return _reload_form([f'Error creating contract: {e}'])

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


# ── Contract Delete ────────────────────────────────────────────────────────


@bp.route('/htmx/contract-delete/<int:contract_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_contract_delete(contract_id):
    """Soft-delete (expire) a contract."""
    from sam.projects.contracts import Contract

    contract = db.session.get(Contract, contract_id)
    if not contract:
        return '<div class="alert alert-danger">Contract not found</div>', 404

    try:
        with management_transaction(db.session):
            contract.update(end_date=datetime.now())
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return ''


# ── NSF Program Edit ───────────────────────────────────────────────────────


@bp.route('/htmx/nsf-program-edit-form/<int:nsf_program_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_nsf_program_edit_form(nsf_program_id):
    """Return the NSF program edit form fragment (loaded into modal)."""
    from sam.projects.contracts import NSFProgram

    program = db.session.get(NSFProgram, nsf_program_id)
    if not program:
        return '<div class="alert alert-warning">NSF program not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_nsf_program_form_htmx.html',
        program=program,
    )


@bp.route('/htmx/nsf-program-edit/<int:nsf_program_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_nsf_program_edit(nsf_program_id):
    """Update an NSF program."""
    from sam.projects.contracts import NSFProgram

    program = db.session.get(NSFProgram, nsf_program_id)
    if not program:
        return '<div class="alert alert-danger">NSF program not found</div>', 404

    errors = []
    nsf_program_name = request.form.get('nsf_program_name', '').strip()
    active = bool(request.form.get('active'))

    if not nsf_program_name:
        errors.append('Program name is required.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_nsf_program_form_htmx.html',
            program=program, errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            program.update(nsf_program_name=nsf_program_name, active=active)
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_nsf_program_form_htmx.html',
            program=program, errors=[f'Error updating NSF program: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


# ── NSF Program Create ─────────────────────────────────────────────────────


@bp.route('/htmx/nsf-program-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_nsf_program_create_form():
    """Return the NSF program create form fragment."""
    return render_template('dashboards/admin/fragments/create_nsf_program_form_htmx.html')


@bp.route('/htmx/nsf-program-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_nsf_program_create():
    """Create a new NSF program."""
    from sam.projects.contracts import NSFProgram

    errors = []
    nsf_program_name = request.form.get('nsf_program_name', '').strip()

    if not nsf_program_name:
        errors.append('Program name is required.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/create_nsf_program_form_htmx.html',
            errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            NSFProgram.create(db.session, nsf_program_name=nsf_program_name)
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/create_nsf_program_form_htmx.html',
            errors=[f'Error creating NSF program: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/organization_edit_success_htmx.html')


# ── NSF Program Delete ─────────────────────────────────────────────────────


@bp.route('/htmx/nsf-program-delete/<int:nsf_program_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_nsf_program_delete(nsf_program_id):
    """Soft-delete (deactivate) an NSF program."""
    from sam.projects.contracts import NSFProgram

    program = db.session.get(NSFProgram, nsf_program_id)
    if not program:
        return '<div class="alert alert-danger">NSF program not found</div>', 404

    try:
        with management_transaction(db.session):
            program.update(active=False)
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return ''


# ── User search for FK fields ──────────────────────────────────────────────


@bp.route('/htmx/search-users-for-org')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_search_users_for_org():
    """
    Search users for FK fields in org-domain forms (e.g. contract PI).
    Returns an HTML fragment with a list of matching users.
    """
    from sam.queries.users import search_users_by_pattern

    query = request.args.get('q', '').strip()
    if len(query) < 2:
        return ''

    users = search_users_by_pattern(db.session, query, limit=15, active_only=True)

    return render_template(
        'dashboards/admin/fragments/user_search_results_fk_htmx.html',
        users=users,
    )
