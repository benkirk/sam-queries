"""
Admin dashboard — Organization management routes.

Covers: Organizations, Institutions, Institution Types, Areas of Interest,
AOI Groups, Contract Sources, Contracts, NSF Programs.
"""

from flask import render_template, request
from webapp.utils.htmx import htmx_success_message
from flask_login import login_required
from datetime import datetime
from marshmallow import ValidationError

from webapp.extensions import db, cache
from webapp.utils.rbac import require_permission, Permission
from sam.manage import management_transaction
from sam.core.users import User
from sam.queries.admin import (
    get_organizations_with_members,
    get_institution_type_tree,
    get_institutions_with_members,
    get_aoi_groups_with_areas,
    get_areas_of_interest_with_projects,
    get_contracts_with_pi,
    get_nsf_programs_with_contracts,
)
from sam.schemas.forms.orgs import (
    EditOrganizationForm, CreateOrganizationForm,
    EditInstitutionTypeForm, CreateInstitutionTypeForm,
    EditInstitutionForm, CreateInstitutionForm,
    CreateMnemonicCodeForm,
    EditAoiGroupForm, CreateAoiGroupForm,
    EditAoiForm, CreateAoiForm,
    EditContractSourceForm, CreateContractSourceForm,
    EditContractForm, CreateContractForm,
    EditNsfProgramForm, CreateNsfProgramForm,
)

from .blueprint import bp


# ── Organization Card ──────────────────────────────────────────────────────


@bp.route('/htmx/organizations-card')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
@cache.cached(query_string=True)
def htmx_organizations_card():
    """
    Return the Organization card body fragment with seven tabs:
    Organizations, Institutions, AOI Groups, Areas of Interest,
    Contract Sources, Contracts, NSF Programs.
    Lazy-loaded when the Organization collapsible section is first expanded.
    """
    from sam.core.organizations import Organization, MnemonicCode
    from sam.projects.contracts import ContractSource

    active_only = request.args.get('active_only') == '1'
    now = datetime.now()

    organizations = get_organizations_with_members(db.session, active_only=active_only)

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

    institution_types = get_institution_type_tree(db.session)
    institutions = get_institutions_with_members(db.session)
    aoi_groups = get_aoi_groups_with_areas(db.session, active_only=active_only)
    aois = get_areas_of_interest_with_projects(db.session, active_only=active_only)

    cs_q = db.session.query(ContractSource).order_by(ContractSource.contract_source)
    if active_only:
        cs_q = cs_q.filter(ContractSource.is_active)
    contract_sources = cs_q.all()

    contracts = get_contracts_with_pi(db.session, active_only=active_only)
    nsf_programs = get_nsf_programs_with_contracts(db.session, active_only=active_only)

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

    try:
        data = EditOrganizationForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/edit_organization_form_htmx.html',
            org=org, errors=EditOrganizationForm.flatten_errors(e.messages), form=request.form,
        )

    try:
        with management_transaction(db.session):
            org.update(
                name=data['name'], acronym=data['acronym'],
                description=data['description'],
                active=data['active'],
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_organization_form_htmx.html',
            org=org, errors=[f'Error updating organization: {e}'], form=request.form,
        )

    return htmx_success_message({'closeActiveModal': {}, 'reloadOrganizationsCard': {}}, 'Saved successfully.')


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
            errors=extra_errors or [],
            form=request.form,
        )

    try:
        data = CreateOrganizationForm().load(request.form)
    except ValidationError as e:
        return _reload_form(CreateOrganizationForm.flatten_errors(e.messages))

    try:
        with management_transaction(db.session):
            Organization.create(
                db.session,
                name=data['name'],
                acronym=data['acronym'],
                description=data['description'],
                parent_org_id=data['parent_org_id'],
            )
    except Exception as e:
        return _reload_form([f'Error creating organization: {e}'])

    return htmx_success_message({'closeActiveModal': {}, 'reloadOrganizationsCard': {}}, 'Saved successfully.')


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

    try:
        data = EditInstitutionTypeForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/edit_institution_type_form_htmx.html',
            inst_type=inst_type, errors=EditInstitutionTypeForm.flatten_errors(e.messages), form=request.form,
        )

    try:
        with management_transaction(db.session):
            inst_type.update(type=data['type'])
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_institution_type_form_htmx.html',
            inst_type=inst_type, errors=[f'Error updating institution type: {e}'], form=request.form,
        )

    return htmx_success_message({'closeActiveModal': {}, 'reloadOrganizationsCard': {}}, 'Saved successfully.')


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

    try:
        data = CreateInstitutionTypeForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/create_institution_type_form_htmx.html',
            errors=CreateInstitutionTypeForm.flatten_errors(e.messages), form=request.form,
        )

    try:
        with management_transaction(db.session):
            InstitutionType.create(db.session, type=data['type'])
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/create_institution_type_form_htmx.html',
            errors=[f'Error creating institution type: {e}'], form=request.form,
        )

    return htmx_success_message({'closeActiveModal': {}, 'reloadOrganizationsCard': {}}, 'Saved successfully.')


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

    try:
        data = EditInstitutionForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/edit_institution_form_htmx.html',
            institution=institution, errors=EditInstitutionForm.flatten_errors(e.messages), form=request.form,
        )

    try:
        with management_transaction(db.session):
            institution.update(
                name=data['name'],
                acronym=data['acronym'],
                nsf_org_code=data['nsf_org_code'],
                address=data['address'],
                city=data['city'],
                zip=data['zip'],
                code=data['code'],
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_institution_form_htmx.html',
            institution=institution, errors=[f'Error updating institution: {e}'], form=request.form,
        )

    return htmx_success_message({'closeActiveModal': {}, 'reloadOrganizationsCard': {}}, 'Saved successfully.')


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

    def _reload_form(extra_errors=None):
        institution_types = db.session.query(InstitutionType).order_by(InstitutionType.type).all()
        return render_template(
            'dashboards/admin/fragments/create_institution_form_htmx.html',
            institution_types=institution_types,
            errors=extra_errors or [],
            form=request.form,
        )

    try:
        data = CreateInstitutionForm().load(request.form)
    except ValidationError as e:
        return _reload_form(CreateInstitutionForm.flatten_errors(e.messages))

    try:
        with management_transaction(db.session):
            Institution.create(
                db.session,
                name=data['name'],
                acronym=data['acronym'],
                nsf_org_code=data['nsf_org_code'],
                city=data['city'],
                code=data['code'],
                institution_type_id=data['institution_type_id'],
            )
    except Exception as e:
        return _reload_form([f'Error creating institution: {e}'])

    return htmx_success_message({'closeActiveModal': {}, 'reloadOrganizationsCard': {}}, 'Saved successfully.')


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
            errors=extra_errors or [],
            form=request.form,
        )

    try:
        data = CreateMnemonicCodeForm().load(request.form)
    except ValidationError as e:
        return _reload_form(CreateMnemonicCodeForm.flatten_errors(e.messages))

    # DB uniqueness checks (require session, can't be in Marshmallow)
    errors = []
    if db.session.query(MnemonicCode).filter_by(code=data['code']).first():
        errors.append(f'Code "{data["code"]}" already exists.')
    if db.session.query(MnemonicCode).filter_by(description=data['description']).first():
        errors.append(f'Description "{data["description"]}" is already in use by another mnemonic code.')
    if errors:
        return _reload_form(errors)

    try:
        with management_transaction(db.session):
            MnemonicCode.create(db.session, code=data['code'], description=data['description'])
    except Exception as e:
        return _reload_form([f'Error creating mnemonic code: {e}'])

    return htmx_success_message({'closeActiveModal': {}, 'reloadOrganizationsCard': {}}, 'Saved successfully.')


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

    try:
        data = EditAoiGroupForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/edit_aoi_group_form_htmx.html',
            group=group, errors=EditAoiGroupForm.flatten_errors(e.messages), form=request.form,
        )

    try:
        with management_transaction(db.session):
            group.update(name=data['name'], active=data['active'])
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_aoi_group_form_htmx.html',
            group=group, errors=[f'Error updating AOI group: {e}'], form=request.form,
        )

    return htmx_success_message({'closeActiveModal': {}, 'reloadOrganizationsCard': {}}, 'Saved successfully.')


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

    try:
        data = CreateAoiGroupForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/create_aoi_group_form_htmx.html',
            errors=CreateAoiGroupForm.flatten_errors(e.messages), form=request.form,
        )

    try:
        with management_transaction(db.session):
            AreaOfInterestGroup.create(db.session, name=data['name'])
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/create_aoi_group_form_htmx.html',
            errors=[f'Error creating AOI group: {e}'], form=request.form,
        )

    return htmx_success_message({'closeActiveModal': {}, 'reloadOrganizationsCard': {}}, 'Saved successfully.')


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

    try:
        data = EditAoiForm().load(request.form)
    except ValidationError as e:
        aoi_groups = db.session.query(AreaOfInterestGroup).order_by(AreaOfInterestGroup.name).all()
        return render_template(
            'dashboards/admin/fragments/edit_aoi_form_htmx.html',
            aoi=aoi, aoi_groups=aoi_groups, errors=EditAoiForm.flatten_errors(e.messages), form=request.form,
        )

    try:
        with management_transaction(db.session):
            aoi.update(
                area_of_interest=data['area_of_interest'],
                area_of_interest_group_id=data['area_of_interest_group_id'],
                active=data['active'],
            )
    except Exception as e:
        aoi_groups = db.session.query(AreaOfInterestGroup).order_by(AreaOfInterestGroup.name).all()
        return render_template(
            'dashboards/admin/fragments/edit_aoi_form_htmx.html',
            aoi=aoi, aoi_groups=aoi_groups,
            errors=[f'Error updating area of interest: {e}'], form=request.form,
        )

    return htmx_success_message({'closeActiveModal': {}, 'reloadOrganizationsCard': {}}, 'Saved successfully.')


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

    def _reload_form(extra_errors=None):
        aoi_groups = db.session.query(AreaOfInterestGroup).filter(
            AreaOfInterestGroup.is_active
        ).order_by(AreaOfInterestGroup.name).all()
        return render_template(
            'dashboards/admin/fragments/create_aoi_form_htmx.html',
            aoi_groups=aoi_groups,
            errors=extra_errors or [],
            form=request.form,
        )

    try:
        data = CreateAoiForm().load(request.form)
    except ValidationError as e:
        return _reload_form(CreateAoiForm.flatten_errors(e.messages))

    try:
        with management_transaction(db.session):
            AreaOfInterest.create(
                db.session,
                area_of_interest=data['area_of_interest'],
                area_of_interest_group_id=data['area_of_interest_group_id'],
            )
    except Exception as e:
        return _reload_form([f'Error creating area of interest: {e}'])

    return htmx_success_message({'closeActiveModal': {}, 'reloadOrganizationsCard': {}}, 'Saved successfully.')


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

    try:
        data = EditContractSourceForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/edit_contract_source_form_htmx.html',
            source=source, errors=EditContractSourceForm.flatten_errors(e.messages), form=request.form,
        )

    try:
        with management_transaction(db.session):
            source.update(contract_source=data['contract_source'], active=data['active'])
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_contract_source_form_htmx.html',
            source=source, errors=[f'Error updating contract source: {e}'], form=request.form,
        )

    return htmx_success_message({'closeActiveModal': {}, 'reloadOrganizationsCard': {}}, 'Saved successfully.')


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

    try:
        data = CreateContractSourceForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/create_contract_source_form_htmx.html',
            errors=CreateContractSourceForm.flatten_errors(e.messages), form=request.form,
        )

    try:
        with management_transaction(db.session):
            ContractSource.create(db.session, contract_source=data['contract_source'])
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/create_contract_source_form_htmx.html',
            errors=[f'Error creating contract source: {e}'], form=request.form,
        )

    return htmx_success_message({'closeActiveModal': {}, 'reloadOrganizationsCard': {}}, 'Saved successfully.')


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

    try:
        data = EditContractForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/edit_contract_form_htmx.html',
            contract=contract, errors=EditContractForm.flatten_errors(e.messages), form=request.form,
        )

    try:
        with management_transaction(db.session):
            contract.update(
                title=data['title'],
                url=data['url'],
                start_date=datetime.combine(data['start_date'], datetime.min.time()),
                end_date=data['end_date'],
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_contract_form_htmx.html',
            contract=contract, errors=[f'Error updating contract: {e}'], form=request.form,
        )

    return htmx_success_message({'closeActiveModal': {}, 'reloadOrganizationsCard': {}}, 'Saved successfully.')


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

    def _reload_form(extra_errors=None):
        contract_sources = db.session.query(ContractSource).filter(
            ContractSource.is_active
        ).order_by(ContractSource.contract_source).all()
        return render_template(
            'dashboards/admin/fragments/create_contract_form_htmx.html',
            contract_sources=contract_sources,
            today=datetime.now().strftime('%Y-%m-%d'),
            errors=extra_errors or [],
            form=request.form,
        )

    try:
        data = CreateContractForm().load(request.form)
    except ValidationError as e:
        return _reload_form(CreateContractForm.flatten_errors(e.messages))

    try:
        with management_transaction(db.session):
            Contract.create(
                db.session,
                contract_number=data['contract_number'],
                title=data['title'],
                url=data['url'],
                start_date=datetime.combine(data['start_date'], datetime.min.time()),
                end_date=data['end_date'],
                contract_source_id=data['contract_source_id'],
                principal_investigator_user_id=data['principal_investigator_user_id'],
            )
    except Exception as e:
        return _reload_form([f'Error creating contract: {e}'])

    return htmx_success_message({'closeActiveModal': {}, 'reloadOrganizationsCard': {}}, 'Saved successfully.')


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

    try:
        data = EditNsfProgramForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/edit_nsf_program_form_htmx.html',
            program=program, errors=EditNsfProgramForm.flatten_errors(e.messages), form=request.form,
        )

    try:
        with management_transaction(db.session):
            program.update(nsf_program_name=data['nsf_program_name'], active=data['active'])
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_nsf_program_form_htmx.html',
            program=program, errors=[f'Error updating NSF program: {e}'], form=request.form,
        )

    return htmx_success_message({'closeActiveModal': {}, 'reloadOrganizationsCard': {}}, 'Saved successfully.')


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

    try:
        data = CreateNsfProgramForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/create_nsf_program_form_htmx.html',
            errors=CreateNsfProgramForm.flatten_errors(e.messages), form=request.form,
        )

    try:
        with management_transaction(db.session):
            NSFProgram.create(db.session, nsf_program_name=data['nsf_program_name'])
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/create_nsf_program_form_htmx.html',
            errors=[f'Error creating NSF program: {e}'], form=request.form,
        )

    return htmx_success_message({'closeActiveModal': {}, 'reloadOrganizationsCard': {}}, 'Saved successfully.')


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
# Note: user search is handled by the unified admin_dashboard.htmx_search_users
# endpoint (admin/blueprint.py) with context='fk'.
