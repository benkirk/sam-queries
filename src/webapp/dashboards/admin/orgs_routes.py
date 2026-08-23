"""
Admin dashboard — Organization management routes.

Covers: Organizations, Institutions, Institution Types, Areas of Interest,
AOI Groups.

Contracts, contract sources and NSF programs moved to `contracts_routes.py`
once they outgrew being org metadata — see that module's docstring.

The CRUD quintets (edit-form/edit/create-form/create/delete) are generated
from `_ORG_CRUD_SPECS` at the bottom of this module via `register_crud` —
only the card/table fragments and the one route that genuinely deviates from
the pattern remain hand-written: mnemonic-code create (DB-uniqueness checks).
"""

import logging

from flask import render_template, request
from flask_login import current_user, login_required
from datetime import datetime
from functools import partial
from sqlalchemy import func

from webapp.utils.htmx import (
    htmx_not_found,
    htmx_success_message,
    modal_triggers,
    read_active_only,
    register_typeahead,
)
from webapp.extensions import db, cache, user_aware_cache_key
from webapp.utils.rbac import (
    has_permission_any_facility,
    require_permission, require_permission_any_facility, Permission,
)
from sam.manage import management_transaction
from sam.core.organizations import Institution, InstitutionType, Organization
from sam.core.users import User
from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup
from sam.queries.admin import (
    get_organizations_with_members,
    get_institution_type_tree,
    get_institutions_with_members,
    get_countries_with_institutions,
    get_aoi_groups_with_areas,
    get_areas_of_interest_with_projects,
    get_contract_detail,
    get_contracts_with_pi,
    get_nsf_program_contracts,
    get_nsf_programs_with_contracts,
)
from sam.schemas.forms.orgs import (
    EditOrganizationForm, CreateOrganizationForm,
    EditInstitutionTypeForm, CreateInstitutionTypeForm,
    EditInstitutionForm, CreateInstitutionForm,
    CreateMnemonicCodeForm,
    EditAoiGroupForm, CreateAoiGroupForm,
    EditAoiForm, CreateAoiForm,
)

from .blueprint import bp
from .crud import CrudSpec, register_crud


logger = logging.getLogger(__name__)

_ORG_TRIGGERS = modal_triggers('reloadOrganizationsCard')


# shared dropdown loaders


def _active_parent_orgs():
    return (
        db.session.query(Organization)
        .filter(Organization.is_active)
        .order_by(Organization.name)
        .all()
    )


def _all_institution_types():
    return db.session.query(InstitutionType).order_by(InstitutionType.type).all()


def _all_active_aoi_groups():
    return (
        db.session.query(AreaOfInterestGroup)
        .filter(AreaOfInterestGroup.is_active)
        .order_by(AreaOfInterestGroup.name)
        .all()
    )


def _all_aoi_groups():
    return db.session.query(AreaOfInterestGroup).order_by(AreaOfInterestGroup.name).all()


# Organization Card


@bp.route('/htmx/organizations-card')
@login_required
@require_permission_any_facility(Permission.VIEW_ORG_METADATA)
@cache.cached(make_cache_key=user_aware_cache_key)
def htmx_organizations_card():
    """
    Return the Organization card body fragment with four tabs:
    Organizations, Institutions, Areas of Interest, NSF Programs.
    Lazy-loaded when the Organization collapsible section is first expanded.

    Contracts and contract sources used to be a fifth tab here; they now live
    on /admin/contracts (:func:`htmx_contracts_table`), which is where an
    operator looks for them. That also takes ~2,200 eagerly-loaded contract
    rows out of this one cached call.
    """
    from sam.core.organizations import MnemonicCode

    active_only = read_active_only(request.args)
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
        nsf_programs=nsf_programs,
        org_to_mnemonic=org_to_mnemonic,
        is_admin=True,
        now=now,
        active_only=active_only,
        # Gate the PI/Monitor links on the user_card route's own permission
        # so a click can never 403. Safe under @cache.cached because the key
        # is user-aware.
        can_view_users=has_permission_any_facility(
            current_user, Permission.VIEW_USERS),
    )


@bp.route('/htmx/institutions-fragment')
@login_required
@require_permission_any_facility(Permission.VIEW_ORG_METADATA)
@cache.cached(make_cache_key=user_aware_cache_key)
def htmx_institutions_fragment():
    """HTMX fragment: filterable, nested table of institutions by institution type.

    Query params:
      - ``country_id``, ``state_prov_id``: geography filters (blank -> None;
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
    active_only = read_active_only(request.args)
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


# Mnemonic Code Create (bespoke: DB-uniqueness checks)


@bp.route('/htmx/mnemonic-code-create-form')
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_mnemonic_code_create_form():
    """Return the mnemonic code create form fragment (loaded into modal)."""
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


# CRUD quintets — generated from specs
#
# Endpoints, URL rules, templates, permissions, and not-found messages are
# identical to the hand-written routes these replace (pinned by
# tests/unit/test_admin_orgs_crud.py and the route-map parity snapshot).

_org_spec = partial(
    CrudSpec,
    triggers=_ORG_TRIGGERS,
    edit_permission=Permission.EDIT_ORG_METADATA,
    create_permission=Permission.CREATE_ORG_METADATA,
    delete_permission=Permission.DELETE_ORG_METADATA,
)


_ORG_CRUD_SPECS = (
    _org_spec(
        slug='organization', name='Organization',
        model=Organization, id_param='org_id', context_key='org',
        edit_schema=EditOrganizationForm, create_schema=CreateOrganizationForm,
        edit_fields=('name', 'acronym', 'description', 'active'),
        create_fields=('name', 'acronym', 'description', 'parent_org_id'),
        create_context=lambda: {'parent_orgs': _active_parent_orgs()},
    ),
    _org_spec(
        slug='institution-type', name='Institution type',
        model=InstitutionType, id_param='institution_type_id',
        context_key='inst_type',
        edit_schema=EditInstitutionTypeForm, create_schema=CreateInstitutionTypeForm,
        edit_fields=('type',), create_fields=('type',),
        actions=('edit', 'create'),
    ),
    _org_spec(
        slug='institution', name='Institution',
        model=Institution, id_param='inst_id', context_key='institution',
        edit_schema=EditInstitutionForm, create_schema=CreateInstitutionForm,
        edit_fields=('name', 'acronym', 'nsf_org_code', 'address',
                     'city', 'zip', 'code'),
        create_fields=('name', 'acronym', 'nsf_org_code', 'city', 'code',
                       'institution_type_id'),
        create_context=lambda: {'institution_types': _all_institution_types()},
        actions=('edit', 'create'),
    ),
    _org_spec(
        slug='aoi-group', name='AOI group', noun='AOI group',
        model=AreaOfInterestGroup, id_param='group_id', context_key='group',
        edit_schema=EditAoiGroupForm, create_schema=CreateAoiGroupForm,
        edit_fields=('name', 'active'), create_fields=('name',),
    ),
    _org_spec(
        slug='aoi', name='Area of interest',
        model=AreaOfInterest, id_param='aoi_id', context_key='aoi',
        edit_schema=EditAoiForm, create_schema=CreateAoiForm,
        edit_fields=('area_of_interest', 'area_of_interest_group_id', 'active'),
        edit_context=lambda: {'aoi_groups': _all_aoi_groups()},
        create_fields=('area_of_interest', 'area_of_interest_group_id'),
        create_context=lambda: {'aoi_groups': _all_active_aoi_groups()},
    ),
)

for _spec in _ORG_CRUD_SPECS:
    register_crud(bp, _spec)


# User search for FK fields
# Note: user search is handled by the unified admin_dashboard.htmx_search_users
# endpoint (admin/blueprint.py) with context='fk'.
