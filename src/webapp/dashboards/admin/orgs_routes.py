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
    has_permission, has_permission_any_facility,
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
    CreateMnemonicCodeForm, EditMnemonicCodeForm, ReassignMnemonicForm,
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

    Contracts and contract sources deliberately live on /admin/contracts
    (:func:`htmx_contracts_table`) rather than as a fifth tab here: that is
    where an operator looks for them, and it keeps ~2,200 eagerly-loaded
    contract rows out of this one cached call.
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
    """Return the mnemonic code create form fragment (loaded into modal).

    The description control searches orgs/institutions on demand, so the whole
    lists no longer ride the render — only an optional prefill (missing-side link).
    """
    return render_template(
        'dashboards/admin/fragments/create_mnemonic_code_form_htmx.html',
        prefill_description=request.args.get('description', ''),
    )


def _mnemonic_create_context():
    return {'prefill_description': ''}


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


# Mnemonic Codes console (sub-tab under Organizations)
#
# Edit and reassign are bespoke, not CrudSpec: `code` and `description` are
# UNIQUE, so both need a DB pre-check (the same reason create is hand-written),
# and reassign edits `project_code.digits` — outside the model's own row.

_MNEMONIC_TRIGGERS = modal_triggers('reloadMnemonicCodesCard')

_MNEMONIC_FILTERS = ('all', 'linked', 'orphaned', 'unused')


_MNEMONIC_SORT_KEYS = {
    'code': lambda r: r['code'],
    'description': lambda r: r['description'].casefold(),
    # Orphaned/unlinked sort together; linked rows by their first target name.
    'links': lambda r: (len(r['links_to']),
                        r['links_to'][0]['name'].casefold() if r['links_to'] else ''),
    'minted': lambda r: r['minted_total'],
}


def _sort_inventory(rows, sort_by, sort_dir):
    key = _MNEMONIC_SORT_KEYS.get(sort_by)
    if not key:
        return rows  # default: mnemonic_inventory already orders by code
    return sorted(rows, key=key, reverse=(sort_dir == 'desc'))


def _filter_inventory(rows, *, facet, q):
    if facet == 'linked':
        rows = [r for r in rows if r['links_to']]
    elif facet == 'orphaned':
        rows = [r for r in rows if r['orphaned']]
    elif facet == 'unused':
        rows = [r for r in rows if r['minted_total'] == 0]
    if q:
        needle = q.strip().casefold()
        rows = [r for r in rows
                if needle in r['code'].casefold() or needle in r['description'].casefold()]
    return rows


@bp.route('/htmx/mnemonic-codes-table')
@login_required
@require_permission_any_facility(Permission.VIEW_ORG_METADATA)
def htmx_mnemonic_codes_table():
    """The mnemonic console table: inventory + reverse-links + usage.

    Not cached (a mutable admin list; the query is cheap) so an edit shows at once.
    Missing-mnemonic prompts live only on the XRAS Remediations strip (the single
    source of truth, with tie-break context); it deep-links here via ``?create=``.
    """
    from sam.queries.mnemonic_console import mnemonic_inventory

    active_only = read_active_only(request.args)
    facet = request.args.get('filter', 'all')
    if facet not in _MNEMONIC_FILTERS:
        facet = 'all'
    q = request.args.get('q', '')

    rows = mnemonic_inventory(db.session, active_only=active_only)
    counts = {
        'all': len(rows),
        'linked': sum(1 for r in rows if r['links_to']),
        'orphaned': sum(1 for r in rows if r['orphaned']),
        'unused': sum(1 for r in rows if r['minted_total'] == 0),
    }
    shown = _sort_inventory(_filter_inventory(rows, facet=facet, q=q),
                            request.args.get('sort_by'), request.args.get('sort_dir'))
    return render_template(
        'dashboards/admin/fragments/mnemonic_codes_table_htmx.html',
        rows=shown, counts=counts, facet=facet, q=q, active_only=active_only,
        form_id='mnemonicFilterForm',
        sortable_columns=set(_MNEMONIC_SORT_KEYS),
        sort={'sort_by': request.args.get('sort_by') or 'code',
              'sort_dir': request.args.get('sort_dir') or 'asc'},
        can_edit=has_permission_any_facility(current_user, Permission.EDIT_ORG_METADATA),
        can_reassign=has_permission(current_user, Permission.SYSTEM_ADMIN),
    )


def _load_mnemonic_or_404(mnemonic_code_id):
    from sam.core.organizations import MnemonicCode
    return db.session.get(MnemonicCode, mnemonic_code_id)


@bp.route('/htmx/mnemonic-code-edit-form/<int:mnemonic_code_id>')
@login_required
@require_permission(Permission.EDIT_ORG_METADATA)
def htmx_mnemonic_code_edit_form(mnemonic_code_id):
    """Edit form fragment — description + active only (the 3-letter code is fixed)."""
    mc = _load_mnemonic_or_404(mnemonic_code_id)
    if not mc:
        return htmx_not_found('Mnemonic code not found.')
    return render_template(
        'dashboards/admin/fragments/edit_mnemonic_code_form_htmx.html', mc=mc)


@bp.route('/htmx/mnemonic-code-edit/<int:mnemonic_code_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_ORG_METADATA)
def htmx_mnemonic_code_edit(mnemonic_code_id):
    """Apply a description/active edit, with a DB-uniqueness pre-check on description."""
    from sam.core.organizations import MnemonicCode
    from marshmallow import ValidationError

    mc = _load_mnemonic_or_404(mnemonic_code_id)
    if not mc:
        return htmx_not_found('Mnemonic code not found.')

    def _reload_form(extra_errors=None):
        return render_template(
            'dashboards/admin/fragments/edit_mnemonic_code_form_htmx.html',
            mc=mc, form=request.form, errors=extra_errors or [])

    try:
        data = EditMnemonicCodeForm().load(request.form)
    except ValidationError as e:
        return _reload_form(EditMnemonicCodeForm.flatten_errors(e.messages))

    active = 'active' in request.form
    clash = (db.session.query(MnemonicCode)
             .filter(MnemonicCode.description == data['description'],
                     MnemonicCode.mnemonic_code_id != mnemonic_code_id).first())
    if clash:
        return _reload_form([f'Description "{data["description"]}" is already in use '
                             f'by mnemonic code "{clash.code}".'])

    try:
        with management_transaction(db.session):
            mc.update(description=data['description'], active=active)
    except Exception as e:
        return _reload_form([f'Error updating mnemonic code: {e}'])
    return htmx_success_message(_MNEMONIC_TRIGGERS, 'Saved successfully.')


def _mnemonic_reassign_context(mc):
    """Facilities dropdown + this code's per-facility high-water marks + the
    suggested discontinuity floor for each (so the operator need not pick one)."""
    from sam.queries.mnemonic_console import suggest_discontinuity
    from sam.resources.facilities import Facility, ProjectCode

    facilities = (db.session.query(Facility)
                  .filter(Facility.is_active, Facility.code.isnot(None))
                  .order_by(Facility.facility_name).all())
    last_by_facility = dict(
        db.session.query(ProjectCode.facility_id, ProjectCode.digits)
        .filter(ProjectCode.mnemonic_code_id == mc.mnemonic_code_id))
    suggested_by_facility = {f.facility_id: suggest_discontinuity(last_by_facility.get(f.facility_id, 0))
                             for f in facilities}
    return {'facilities': facilities, 'last_by_facility': last_by_facility,
            'suggested_by_facility': suggested_by_facility}


@bp.route('/htmx/mnemonic-code-reassign-form/<int:mnemonic_code_id>')
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def htmx_mnemonic_code_reassign_form(mnemonic_code_id):
    """Reassign form — repoint description + insert a digit-band discontinuity."""
    mc = _load_mnemonic_or_404(mnemonic_code_id)
    if not mc:
        return htmx_not_found('Mnemonic code not found.')
    return render_template(
        'dashboards/admin/fragments/reassign_mnemonic_form_htmx.html',
        mc=mc, **_mnemonic_reassign_context(mc))


@bp.route('/htmx/mnemonic-code-reassign-preview/<int:mnemonic_code_id>')
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def htmx_mnemonic_code_reassign_preview(mnemonic_code_id):
    """Live 'next code' preview for the reassign form (no side effects)."""
    from sam.projects.projects import formulate_projcode
    from sam.resources.facilities import Facility, ProjectCode

    mc = _load_mnemonic_or_404(mnemonic_code_id)
    facility = db.session.get(Facility, request.args.get('facility_id', type=int))
    next_start = request.args.get('next_start', type=int)
    preview = last_issued = None
    if mc and facility and facility.code:
        pc = db.session.get(ProjectCode, (facility.facility_id, mc.mnemonic_code_id))
        last_issued = pc.digits if pc else 0
        effective = max(next_start or 0, last_issued + 1)
        preview = formulate_projcode(facility.code, mc.code, effective)
    return render_template(
        'dashboards/admin/fragments/mnemonic_reassign_preview_htmx.html',
        preview=preview, last_issued=last_issued,
        facility=facility, next_start=next_start)


@bp.route('/htmx/mnemonic-code-reassign/<int:mnemonic_code_id>', methods=['POST'])
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def htmx_mnemonic_code_reassign(mnemonic_code_id):
    """Repoint the description and raise one facility's counter, in one transaction."""
    from sam.core.organizations import MnemonicCode
    from sam.resources.facilities import Facility, ProjectCode
    from marshmallow import ValidationError

    mc = _load_mnemonic_or_404(mnemonic_code_id)
    if not mc:
        return htmx_not_found('Mnemonic code not found.')

    def _reload_form(extra_errors=None):
        return render_template(
            'dashboards/admin/fragments/reassign_mnemonic_form_htmx.html',
            mc=mc, form=request.form, errors=extra_errors or [],
            **_mnemonic_reassign_context(mc))

    try:
        data = ReassignMnemonicForm().load(request.form)
    except ValidationError as e:
        return _reload_form(ReassignMnemonicForm.flatten_errors(e.messages))

    facility = db.session.get(Facility, data['facility_id'])
    if not facility or not facility.code:
        return _reload_form(['Select a facility with a projcode prefix.'])
    clash = (db.session.query(MnemonicCode)
             .filter(MnemonicCode.description == data['description'],
                     MnemonicCode.mnemonic_code_id != mnemonic_code_id).first())
    if clash:
        return _reload_form([f'Description "{data["description"]}" is already in use '
                             f'by mnemonic code "{clash.code}".'])

    try:
        with management_transaction(db.session):
            mc.update(description=data['description'])
            ProjectCode.set_number_floor(db.session, facility.facility_id,
                                         mc.mnemonic_code_id, data['next_start'])
    except Exception as e:
        return _reload_form([f'Error reassigning mnemonic code: {e}'])
    return htmx_success_message(_MNEMONIC_TRIGGERS, 'Reassigned successfully.')


# Description picker: entity typeahead + live match indicator (all three forms)


def _search_mnemonic_targets(q, active_only):
    from sam.queries.mnemonic_console import search_targets
    return search_targets(db.session, q,
                          exclude_code=request.args.get('exclude_code') or None)


register_typeahead(
    bp, rule='/htmx/search-mnemonic-targets',
    endpoint='htmx_search_mnemonic_targets',
    permission=Permission.VIEW_ORG_METADATA, any_facility=True,
    search=_search_mnemonic_targets,
    template='dashboards/admin/fragments/mnemonic_target_search_results_htmx.html',
    ctx_key='targets')


@bp.route('/htmx/mnemonic-match-check')
@login_required
@require_permission_any_facility(Permission.VIEW_ORG_METADATA)
def htmx_mnemonic_match_check():
    """Live indicator: which entity the description routes to, or a code collision."""
    from sam.queries.mnemonic_console import claiming_code, describes_live_entity

    description = request.args.get('description', '')
    exclude = request.args.get('exclude_code') or None
    return render_template(
        'dashboards/admin/fragments/mnemonic_match_status_htmx.html',
        description=description.strip(),
        match=describes_live_entity(db.session, description),
        claimed_by=claiming_code(db.session, description, exclude_code=exclude))


@bp.route('/htmx/mnemonic-suggest-codes')
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_mnemonic_suggest_codes():
    """Collision-free code chips for the create form's current description."""
    from sam.queries.mnemonic_console import suggest_codes
    return render_template(
        'dashboards/admin/fragments/mnemonic_suggested_codes_htmx.html',
        codes=suggest_codes(db.session, request.args.get('description', '')))


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
