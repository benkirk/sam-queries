"""
Admin dashboard — Organization management routes.

Covers: Organizations, Institutions, Institution Types, Areas of Interest,
AOI Groups, Contract Sources, Contracts, NSF Programs.
"""

from flask import render_template, request
from flask_login import login_required
from datetime import datetime

from webapp.utils.htmx import (
    handle_htmx_form_post,
    handle_htmx_soft_delete,
    htmx_not_found,
    htmx_success_message,
)
from webapp.extensions import db, cache, user_aware_cache_key
from webapp.utils.rbac import (
    require_permission, require_permission_any_facility, Permission,
)
from sam.manage import management_transaction
from sam.core.users import User
from sam.queries.admin import (
    get_organizations_with_members,
    get_institution_type_tree,
    get_institutions_with_members,
    get_countries_with_institutions,
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


_ORG_TRIGGERS = {'closeActiveModal': {}, 'reloadOrganizationsCard': {}}


# ─── shared dropdown loaders ────────────────────────────────────────────────


def _active_parent_orgs():
    from sam.core.organizations import Organization
    return (
        db.session.query(Organization)
        .filter(Organization.is_active)
        .order_by(Organization.name)
        .all()
    )


def _all_institution_types():
    from sam.core.organizations import InstitutionType
    return db.session.query(InstitutionType).order_by(InstitutionType.type).all()


def _all_active_aoi_groups():
    from sam.projects.areas import AreaOfInterestGroup
    return (
        db.session.query(AreaOfInterestGroup)
        .filter(AreaOfInterestGroup.is_active)
        .order_by(AreaOfInterestGroup.name)
        .all()
    )


def _all_aoi_groups():
    from sam.projects.areas import AreaOfInterestGroup
    return db.session.query(AreaOfInterestGroup).order_by(AreaOfInterestGroup.name).all()


def _active_contract_sources():
    from sam.projects.contracts import ContractSource
    return (
        db.session.query(ContractSource)
        .filter(ContractSource.is_active)
        .order_by(ContractSource.contract_source)
        .all()
    )


# ── Organization Card ──────────────────────────────────────────────────────


@bp.route('/htmx/organizations-card')
@login_required
@require_permission_any_facility(Permission.VIEW_ORG_METADATA)
@cache.cached(make_cache_key=user_aware_cache_key)
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

    aoi_groups = get_aoi_groups_with_areas(db.session, active_only=active_only)
    aois = get_areas_of_interest_with_projects(db.session, active_only=active_only)

    cs_q = db.session.query(ContractSource).order_by(ContractSource.contract_source)
    if active_only:
        cs_q = cs_q.filter(ContractSource.is_active)
    contract_sources = cs_q.all()

    contracts = get_contracts_with_pi(db.session, active_only=active_only)
    nsf_programs = get_nsf_programs_with_contracts(db.session, active_only=active_only)

    _mc_lookup = MnemonicCode.build_lookup(db.session)
    org_to_mnemonic = {
        org.organization_id: MnemonicCode.resolve_for_organization(org, _mc_lookup)
        for org in organizations
    }

    return render_template(
        'dashboards/admin/fragments/organization_card.html',
        organizations=organizations,
        org_tree=org_tree,
        aoi_groups=aoi_groups,
        aois=aois,
        contract_sources=contract_sources,
        contracts=contracts,
        nsf_programs=nsf_programs,
        org_to_mnemonic=org_to_mnemonic,
        is_admin=True,
        now=now,
        active_only=active_only,
    )


@bp.route('/htmx/institutions-fragment')
@login_required
@require_permission_any_facility(Permission.VIEW_ORG_METADATA)
@cache.cached(make_cache_key=user_aware_cache_key)
def htmx_institutions_fragment():
    """HTMX fragment: filterable, nested table of institutions by institution type.

    Query params:
      - ``country_id``, ``state_prov_id``: geography filters (blank → None;
        ``state_prov_id`` is ignored unless ``country_id`` is set).
      - ``active_only``: institution-level filter (from the outer
        Organizations card). Keep only institutions with ≥1 currently-active
        ``UserInstitution`` linked to an active ``User``.
      - ``show_users_projects``: when set, eager-load users + their lead /
        admin projects and render the ``# Users`` / ``# Projects`` columns
        + expand row. When off we do no user/project work.
      - ``active_users_projects``: when set (and U&P shown), filter the
        chip lists to active users / active projects. Institutions with
        zero visible users AND zero visible projects are dropped.
    """
    from sam.core.organizations import MnemonicCode
    from sam.geography import StateProv

    def _int_or_none(val):
        val = (val or '').strip()
        if not val:
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    country_id = _int_or_none(request.args.get('country_id'))
    state_prov_id = _int_or_none(request.args.get('state_prov_id')) if country_id else None
    active_only = request.args.get('active_only') == '1'
    show_users_projects = request.args.get('show_users_projects') == '1'
    active_users_projects = request.args.get('active_users_projects') == '1'

    institutions = get_institutions_with_members(
        db.session,
        country_id=country_id,
        state_prov_id=state_prov_id,
        active_only=active_only,
        include_projects=show_users_projects,
    )

    # Per-institution chip lists — only built when the U&P view is on.
    user_chips = {}
    project_chips = {}
    if show_users_projects:
        for inst in institutions:
            user_chips[inst.institution_id] = sorted(
                [(ui.user.username, bool(ui.user.is_active)) for ui in inst.users],
                key=lambda t: t[0].lower(),
            )
            seen = {}
            for ui in inst.users:
                for p in list(ui.user.led_projects) + list(ui.user.admin_projects):
                    # Dedupe by projcode; "active" wins if any link is active.
                    is_active = bool(p.is_active)
                    prev = seen.get(p.projcode)
                    if prev is None or (is_active and not prev):
                        seen[p.projcode] = is_active
            project_chips[inst.institution_id] = sorted(
                seen.items(), key=lambda t: t[0].lower()
            )

        # Drop institutions whose visible-users AND visible-projects are both
        # empty under the current active_users_projects filter.
        def _has_visible(chips):
            if active_users_projects:
                return any(is_active for _, is_active in chips)
            return bool(chips)

        institutions = [
            inst for inst in institutions
            if _has_visible(user_chips[inst.institution_id])
            or _has_visible(project_chips[inst.institution_id])
        ]

    # Group (possibly filtered) institutions by institution_type.
    all_types = get_institution_type_tree(db.session)
    by_type_id = {}
    for inst in institutions:
        by_type_id.setdefault(inst.institution_type_id, []).append(inst)
    institution_types_grouped = [
        (it, by_type_id[it.institution_type_id])
        for it in all_types
        if it.institution_type_id in by_type_id
    ]

    _mc_lookup = MnemonicCode.build_lookup(db.session)
    inst_to_mnemonic = {
        inst.institution_id: MnemonicCode.resolve_for_institution(inst, _mc_lookup)
        for inst in institutions
    }

    countries = get_countries_with_institutions(db.session)
    state_provs = (
        db.session.query(StateProv)
        .filter_by(ext_country_id=country_id)
        .order_by(StateProv.name)
        .all()
        if country_id else []
    )

    return render_template(
        'dashboards/admin/fragments/institutions_table.html',
        institution_types_grouped=institution_types_grouped,
        total_institutions=len(institutions),
        inst_to_mnemonic=inst_to_mnemonic,
        user_chips=user_chips,
        project_chips=project_chips,
        countries=countries,
        state_provs=state_provs,
        country_id=country_id,
        state_prov_id=state_prov_id,
        active_only=active_only,
        show_users_projects=show_users_projects,
        active_users_projects=active_users_projects,
        is_admin=True,
    )


# ── Organization Edit ──────────────────────────────────────────────────────


@bp.route('/htmx/organization-edit-form/<int:org_id>')
@login_required
@require_permission(Permission.EDIT_ORG_METADATA)
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
@require_permission(Permission.EDIT_ORG_METADATA)
def htmx_organization_edit(org_id):
    """Update an organization."""
    from sam.core.organizations import Organization

    org = db.session.get(Organization, org_id)
    if not org:
        return htmx_not_found('Organization')

    return handle_htmx_form_post(
        schema_cls=EditOrganizationForm,
        template='dashboards/admin/fragments/edit_organization_form_htmx.html',
        success_triggers=_ORG_TRIGGERS,
        error_prefix='Error updating organization',
        extra_context={'org': org},
        do_action=lambda data: org.update(
            name=data['name'], acronym=data['acronym'],
            description=data['description'],
            active=data['active'],
        ),
    )


# ── Organization Create ────────────────────────────────────────────────────


@bp.route('/htmx/organization-create-form')
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_organization_create_form():
    """Return the organization create form fragment (loaded into modal)."""
    return render_template(
        'dashboards/admin/fragments/create_organization_form_htmx.html',
        parent_orgs=_active_parent_orgs(),
    )


@bp.route('/htmx/organization-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_organization_create():
    """Create a new organization."""
    from sam.core.organizations import Organization

    return handle_htmx_form_post(
        schema_cls=CreateOrganizationForm,
        template='dashboards/admin/fragments/create_organization_form_htmx.html',
        success_triggers=_ORG_TRIGGERS,
        error_prefix='Error creating organization',
        context_fn=lambda: {'parent_orgs': _active_parent_orgs()},
        do_action=lambda data: Organization.create(
            db.session,
            name=data['name'],
            acronym=data['acronym'],
            description=data['description'],
            parent_org_id=data['parent_org_id'],
        ),
    )


# ── Organization Delete ────────────────────────────────────────────────────


@bp.route('/htmx/organization-delete/<int:org_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_ORG_METADATA)
def htmx_organization_delete(org_id):
    """Soft-delete (deactivate) an organization."""
    from sam.core.organizations import Organization

    org = db.session.get(Organization, org_id)
    if not org:
        return htmx_not_found('Organization')
    return handle_htmx_soft_delete(org, name='Organization')


# ── Institution Type Edit ──────────────────────────────────────────────────


@bp.route('/htmx/institution-type-edit-form/<int:institution_type_id>')
@login_required
@require_permission(Permission.EDIT_ORG_METADATA)
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
@require_permission(Permission.EDIT_ORG_METADATA)
def htmx_institution_type_edit(institution_type_id):
    """Update an institution type."""
    from sam.core.organizations import InstitutionType

    inst_type = db.session.get(InstitutionType, institution_type_id)
    if not inst_type:
        return htmx_not_found('Institution type')

    return handle_htmx_form_post(
        schema_cls=EditInstitutionTypeForm,
        template='dashboards/admin/fragments/edit_institution_type_form_htmx.html',
        success_triggers=_ORG_TRIGGERS,
        error_prefix='Error updating institution type',
        extra_context={'inst_type': inst_type},
        do_action=lambda data: inst_type.update(type=data['type']),
    )


# ── Institution Type Create ────────────────────────────────────────────────


@bp.route('/htmx/institution-type-create-form')
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_institution_type_create_form():
    """Return the institution type create form fragment (loaded into modal)."""
    return render_template('dashboards/admin/fragments/create_institution_type_form_htmx.html')


@bp.route('/htmx/institution-type-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_institution_type_create():
    """Create a new institution type."""
    from sam.core.organizations import InstitutionType

    return handle_htmx_form_post(
        schema_cls=CreateInstitutionTypeForm,
        template='dashboards/admin/fragments/create_institution_type_form_htmx.html',
        success_triggers=_ORG_TRIGGERS,
        error_prefix='Error creating institution type',
        do_action=lambda data: InstitutionType.create(db.session, type=data['type']),
    )


# ── Institution Edit ───────────────────────────────────────────────────────


@bp.route('/htmx/institution-edit-form/<int:inst_id>')
@login_required
@require_permission(Permission.EDIT_ORG_METADATA)
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
@require_permission(Permission.EDIT_ORG_METADATA)
def htmx_institution_edit(inst_id):
    """Update an institution."""
    from sam.core.organizations import Institution

    institution = db.session.get(Institution, inst_id)
    if not institution:
        return htmx_not_found('Institution')

    return handle_htmx_form_post(
        schema_cls=EditInstitutionForm,
        template='dashboards/admin/fragments/edit_institution_form_htmx.html',
        success_triggers=_ORG_TRIGGERS,
        error_prefix='Error updating institution',
        extra_context={'institution': institution},
        do_action=lambda data: institution.update(
            name=data['name'],
            acronym=data['acronym'],
            nsf_org_code=data['nsf_org_code'],
            address=data['address'],
            city=data['city'],
            zip=data['zip'],
            code=data['code'],
        ),
    )


# ── Institution Create ─────────────────────────────────────────────────────


@bp.route('/htmx/institution-create-form')
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_institution_create_form():
    """Return the institution create form fragment (loaded into modal)."""
    return render_template(
        'dashboards/admin/fragments/create_institution_form_htmx.html',
        institution_types=_all_institution_types(),
    )


@bp.route('/htmx/institution-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_institution_create():
    """Create a new institution."""
    from sam.core.organizations import Institution

    return handle_htmx_form_post(
        schema_cls=CreateInstitutionForm,
        template='dashboards/admin/fragments/create_institution_form_htmx.html',
        success_triggers=_ORG_TRIGGERS,
        error_prefix='Error creating institution',
        context_fn=lambda: {'institution_types': _all_institution_types()},
        do_action=lambda data: Institution.create(
            db.session,
            name=data['name'],
            acronym=data['acronym'],
            nsf_org_code=data['nsf_org_code'],
            city=data['city'],
            code=data['code'],
            institution_type_id=data['institution_type_id'],
        ),
    )


# ── Mnemonic Code Create ───────────────────────────────────────────────────


@bp.route('/htmx/mnemonic-code-create-form')
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
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


def _mnemonic_create_context():
    from sam.core.organizations import Institution, Organization
    return {
        'institutions': db.session.query(Institution).order_by(Institution.name).all(),
        'organizations': (
            db.session.query(Organization)
            .filter(Organization.is_active)
            .order_by(Organization.name)
            .all()
        ),
        'prefill_description': '',
    }


@bp.route('/htmx/mnemonic-code-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_mnemonic_code_create():
    """Create a new mnemonic code.

    Uses an inline DB-uniqueness check after schema validation, so the
    helper-based handler gets bypassed for that step.
    """
    from sam.core.organizations import MnemonicCode
    from marshmallow import ValidationError

    def _reload_form(extra_errors=None):
        ctx = _mnemonic_create_context()
        ctx['errors'] = extra_errors or []
        ctx['form'] = request.form
        return render_template(
            'dashboards/admin/fragments/create_mnemonic_code_form_htmx.html',
            **ctx,
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

    return htmx_success_message(_ORG_TRIGGERS, 'Saved successfully.')


# ── AOI Group Edit ─────────────────────────────────────────────────────────


@bp.route('/htmx/aoi-group-edit-form/<int:group_id>')
@login_required
@require_permission(Permission.EDIT_ORG_METADATA)
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
@require_permission(Permission.EDIT_ORG_METADATA)
def htmx_aoi_group_edit(group_id):
    """Update an AOI group."""
    from sam.projects.areas import AreaOfInterestGroup

    group = db.session.get(AreaOfInterestGroup, group_id)
    if not group:
        return htmx_not_found('AOI group')

    return handle_htmx_form_post(
        schema_cls=EditAoiGroupForm,
        template='dashboards/admin/fragments/edit_aoi_group_form_htmx.html',
        success_triggers=_ORG_TRIGGERS,
        error_prefix='Error updating AOI group',
        extra_context={'group': group},
        do_action=lambda data: group.update(name=data['name'], active=data['active']),
    )


# ── AOI Group Create ───────────────────────────────────────────────────────


@bp.route('/htmx/aoi-group-create-form')
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_aoi_group_create_form():
    """Return the AOI group create form fragment."""
    return render_template('dashboards/admin/fragments/create_aoi_group_form_htmx.html')


@bp.route('/htmx/aoi-group-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_aoi_group_create():
    """Create a new AOI group."""
    from sam.projects.areas import AreaOfInterestGroup

    return handle_htmx_form_post(
        schema_cls=CreateAoiGroupForm,
        template='dashboards/admin/fragments/create_aoi_group_form_htmx.html',
        success_triggers=_ORG_TRIGGERS,
        error_prefix='Error creating AOI group',
        do_action=lambda data: AreaOfInterestGroup.create(db.session, name=data['name']),
    )


# ── AOI Group Delete ───────────────────────────────────────────────────────


@bp.route('/htmx/aoi-group-delete/<int:group_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_ORG_METADATA)
def htmx_aoi_group_delete(group_id):
    """Soft-delete (deactivate) an AOI group."""
    from sam.projects.areas import AreaOfInterestGroup

    group = db.session.get(AreaOfInterestGroup, group_id)
    if not group:
        return htmx_not_found('AOI group')
    return handle_htmx_soft_delete(group, name='AOI group')


# ── AOI Edit ───────────────────────────────────────────────────────────────


@bp.route('/htmx/aoi-edit-form/<int:aoi_id>')
@login_required
@require_permission(Permission.EDIT_ORG_METADATA)
def htmx_aoi_edit_form(aoi_id):
    """Return the AOI edit form fragment (loaded into modal)."""
    from sam.projects.areas import AreaOfInterest

    aoi = db.session.get(AreaOfInterest, aoi_id)
    if not aoi:
        return '<div class="alert alert-warning">Area of interest not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_aoi_form_htmx.html',
        aoi=aoi,
        aoi_groups=_all_aoi_groups(),
    )


@bp.route('/htmx/aoi-edit/<int:aoi_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_ORG_METADATA)
def htmx_aoi_edit(aoi_id):
    """Update an area of interest."""
    from sam.projects.areas import AreaOfInterest

    aoi = db.session.get(AreaOfInterest, aoi_id)
    if not aoi:
        return htmx_not_found('Area of interest')

    return handle_htmx_form_post(
        schema_cls=EditAoiForm,
        template='dashboards/admin/fragments/edit_aoi_form_htmx.html',
        success_triggers=_ORG_TRIGGERS,
        error_prefix='Error updating area of interest',
        extra_context={'aoi': aoi},
        context_fn=lambda: {'aoi_groups': _all_aoi_groups()},
        do_action=lambda data: aoi.update(
            area_of_interest=data['area_of_interest'],
            area_of_interest_group_id=data['area_of_interest_group_id'],
            active=data['active'],
        ),
    )


# ── AOI Create ─────────────────────────────────────────────────────────────


@bp.route('/htmx/aoi-create-form')
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_aoi_create_form():
    """Return the AOI create form fragment."""
    return render_template(
        'dashboards/admin/fragments/create_aoi_form_htmx.html',
        aoi_groups=_all_active_aoi_groups(),
    )


@bp.route('/htmx/aoi-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_aoi_create():
    """Create a new area of interest."""
    from sam.projects.areas import AreaOfInterest

    return handle_htmx_form_post(
        schema_cls=CreateAoiForm,
        template='dashboards/admin/fragments/create_aoi_form_htmx.html',
        success_triggers=_ORG_TRIGGERS,
        error_prefix='Error creating area of interest',
        context_fn=lambda: {'aoi_groups': _all_active_aoi_groups()},
        do_action=lambda data: AreaOfInterest.create(
            db.session,
            area_of_interest=data['area_of_interest'],
            area_of_interest_group_id=data['area_of_interest_group_id'],
        ),
    )


# ── AOI Delete ─────────────────────────────────────────────────────────────


@bp.route('/htmx/aoi-delete/<int:aoi_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_ORG_METADATA)
def htmx_aoi_delete(aoi_id):
    """Soft-delete (deactivate) an area of interest."""
    from sam.projects.areas import AreaOfInterest

    aoi = db.session.get(AreaOfInterest, aoi_id)
    if not aoi:
        return htmx_not_found('Area of interest')
    return handle_htmx_soft_delete(aoi, name='Area of interest')


# ── Contract Source Edit ───────────────────────────────────────────────────


@bp.route('/htmx/contract-source-edit-form/<int:source_id>')
@login_required
@require_permission(Permission.EDIT_ORG_METADATA)
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
@require_permission(Permission.EDIT_ORG_METADATA)
def htmx_contract_source_edit(source_id):
    """Update a contract source."""
    from sam.projects.contracts import ContractSource

    source = db.session.get(ContractSource, source_id)
    if not source:
        return htmx_not_found('Contract source')

    return handle_htmx_form_post(
        schema_cls=EditContractSourceForm,
        template='dashboards/admin/fragments/edit_contract_source_form_htmx.html',
        success_triggers=_ORG_TRIGGERS,
        error_prefix='Error updating contract source',
        extra_context={'source': source},
        do_action=lambda data: source.update(
            contract_source=data['contract_source'], active=data['active'],
        ),
    )


# ── Contract Source Create ─────────────────────────────────────────────────


@bp.route('/htmx/contract-source-create-form')
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_contract_source_create_form():
    """Return the contract source create form fragment."""
    return render_template('dashboards/admin/fragments/create_contract_source_form_htmx.html')


@bp.route('/htmx/contract-source-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_contract_source_create():
    """Create a new contract source."""
    from sam.projects.contracts import ContractSource

    return handle_htmx_form_post(
        schema_cls=CreateContractSourceForm,
        template='dashboards/admin/fragments/create_contract_source_form_htmx.html',
        success_triggers=_ORG_TRIGGERS,
        error_prefix='Error creating contract source',
        do_action=lambda data: ContractSource.create(
            db.session, contract_source=data['contract_source'],
        ),
    )


# ── Contract Source Delete ─────────────────────────────────────────────────


@bp.route('/htmx/contract-source-delete/<int:source_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_ORG_METADATA)
def htmx_contract_source_delete(source_id):
    """Soft-delete (deactivate) a contract source."""
    from sam.projects.contracts import ContractSource

    source = db.session.get(ContractSource, source_id)
    if not source:
        return htmx_not_found('Contract source')
    return handle_htmx_soft_delete(source, name='Contract source')


# ── Contract Edit ──────────────────────────────────────────────────────────


@bp.route('/htmx/contract-edit-form/<int:contract_id>')
@login_required
@require_permission(Permission.EDIT_ORG_METADATA)
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
@require_permission(Permission.EDIT_ORG_METADATA)
def htmx_contract_edit(contract_id):
    """Update a contract."""
    from sam.projects.contracts import Contract

    contract = db.session.get(Contract, contract_id)
    if not contract:
        return htmx_not_found('Contract')

    return handle_htmx_form_post(
        schema_cls=EditContractForm,
        template='dashboards/admin/fragments/edit_contract_form_htmx.html',
        success_triggers=_ORG_TRIGGERS,
        error_prefix='Error updating contract',
        extra_context={'contract': contract},
        do_action=lambda data: contract.update(
            title=data['title'],
            url=data['url'],
            start_date=datetime.combine(data['start_date'], datetime.min.time()),
            end_date=data['end_date'],
        ),
    )


# ── Contract Create ────────────────────────────────────────────────────────


@bp.route('/htmx/contract-create-form')
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_contract_create_form():
    """Return the contract create form fragment."""
    return render_template(
        'dashboards/admin/fragments/create_contract_form_htmx.html',
        contract_sources=_active_contract_sources(),
        today=datetime.now().strftime('%Y-%m-%d'),
    )


@bp.route('/htmx/contract-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_contract_create():
    """Create a new contract."""
    from sam.projects.contracts import Contract

    return handle_htmx_form_post(
        schema_cls=CreateContractForm,
        template='dashboards/admin/fragments/create_contract_form_htmx.html',
        success_triggers=_ORG_TRIGGERS,
        error_prefix='Error creating contract',
        context_fn=lambda: {
            'contract_sources': _active_contract_sources(),
            'today': datetime.now().strftime('%Y-%m-%d'),
        },
        do_action=lambda data: Contract.create(
            db.session,
            contract_number=data['contract_number'],
            title=data['title'],
            url=data['url'],
            start_date=datetime.combine(data['start_date'], datetime.min.time()),
            end_date=data['end_date'],
            contract_source_id=data['contract_source_id'],
            principal_investigator_user_id=data['principal_investigator_user_id'],
        ),
    )


# ── Contract Delete ────────────────────────────────────────────────────────


@bp.route('/htmx/contract-delete/<int:contract_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_ORG_METADATA)
def htmx_contract_delete(contract_id):
    """Soft-delete (expire) a contract."""
    from sam.projects.contracts import Contract

    contract = db.session.get(Contract, contract_id)
    if not contract:
        return htmx_not_found('Contract')

    # Contract uses end_date rather than the active flag for retirement.
    try:
        with management_transaction(db.session):
            contract.update(end_date=datetime.now())
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return ''


# ── NSF Program Edit ───────────────────────────────────────────────────────


@bp.route('/htmx/nsf-program-edit-form/<int:nsf_program_id>')
@login_required
@require_permission(Permission.EDIT_ORG_METADATA)
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
@require_permission(Permission.EDIT_ORG_METADATA)
def htmx_nsf_program_edit(nsf_program_id):
    """Update an NSF program."""
    from sam.projects.contracts import NSFProgram

    program = db.session.get(NSFProgram, nsf_program_id)
    if not program:
        return htmx_not_found('NSF program')

    return handle_htmx_form_post(
        schema_cls=EditNsfProgramForm,
        template='dashboards/admin/fragments/edit_nsf_program_form_htmx.html',
        success_triggers=_ORG_TRIGGERS,
        error_prefix='Error updating NSF program',
        extra_context={'program': program},
        do_action=lambda data: program.update(
            nsf_program_name=data['nsf_program_name'], active=data['active'],
        ),
    )


# ── NSF Program Create ─────────────────────────────────────────────────────


@bp.route('/htmx/nsf-program-create-form')
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_nsf_program_create_form():
    """Return the NSF program create form fragment."""
    return render_template('dashboards/admin/fragments/create_nsf_program_form_htmx.html')


@bp.route('/htmx/nsf-program-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_nsf_program_create():
    """Create a new NSF program."""
    from sam.projects.contracts import NSFProgram

    return handle_htmx_form_post(
        schema_cls=CreateNsfProgramForm,
        template='dashboards/admin/fragments/create_nsf_program_form_htmx.html',
        success_triggers=_ORG_TRIGGERS,
        error_prefix='Error creating NSF program',
        do_action=lambda data: NSFProgram.create(
            db.session, nsf_program_name=data['nsf_program_name'],
        ),
    )


# ── NSF Program Delete ─────────────────────────────────────────────────────


@bp.route('/htmx/nsf-program-delete/<int:nsf_program_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_ORG_METADATA)
def htmx_nsf_program_delete(nsf_program_id):
    """Soft-delete (deactivate) an NSF program."""
    from sam.projects.contracts import NSFProgram

    program = db.session.get(NSFProgram, nsf_program_id)
    if not program:
        return htmx_not_found('NSF program')
    return handle_htmx_soft_delete(program, name='NSF program')


# ── User search for FK fields ──────────────────────────────────────────────
# Note: user search is handled by the unified admin_dashboard.htmx_search_users
# endpoint (admin/blueprint.py) with context='fk'.
