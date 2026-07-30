"""
Admin dashboard — Organization management routes.

Covers: Organizations, Institutions, Institution Types, Areas of Interest,
AOI Groups, Contract Sources, Contracts, NSF Programs.

The CRUD quintets (edit-form/edit/create-form/create/delete) are generated
from `_ORG_CRUD_SPECS` at the bottom of this module via `register_crud` —
only the card/table fragments and the routes that genuinely deviate from
the pattern remain hand-written: mnemonic-code create (DB-uniqueness
checks), contract create (award-source prefill, FK-existence and
uniqueness checks), and contract delete (retires by end_date).
"""

import logging

from flask import render_template, request
from flask_login import current_user, login_required
from datetime import datetime
from functools import partial
from sqlalchemy import func

from webapp.utils.form_handler import FormError, HtmxFormHandler
from webapp.utils.fk_validation import validate_fk_existence
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
from sam.projects.contracts import Contract, ContractSource, NSFProgram
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
    EditContractSourceForm, CreateContractSourceForm,
    EditContractForm, CreateContractForm,
    EditNsfProgramForm, CreateNsfProgramForm,
)

from .blueprint import bp
from .crud import CrudSpec, register_crud


logger = logging.getLogger(__name__)

_ORG_TRIGGERS = modal_triggers('reloadOrganizationsCard')


# ─── shared dropdown loaders ────────────────────────────────────────────────


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


def _active_contract_sources():
    return (
        db.session.query(ContractSource)
        .filter(ContractSource.is_active)
        .order_by(ContractSource.contract_source)
        .all()
    )


def _search_nsf_programs_fk(q, active_only):
    """Typeahead search behind the contract forms' NSF Program picker.

    Active-only, per the FK-picker convention: the search offers programs
    you may *assign*. A contract already pointing at a deactivated program
    still shows it, because the picker's badge comes from the contract row
    rather than from this search.
    """
    query = db.session.query(NSFProgram).filter(
        NSFProgram.nsf_program_name.ilike(f'%{q}%'))
    if active_only:
        query = query.filter(NSFProgram.is_active)
    return query.order_by(NSFProgram.nsf_program_name).limit(15).all()


register_typeahead(
    bp, rule='/htmx/search/nsf-programs', endpoint='htmx_search_nsf_programs',
    permission=Permission.VIEW_ORG_METADATA,
    search=_search_nsf_programs_fk,
    template='dashboards/admin/fragments/nsf_program_search_results_fk_htmx.html',
    ctx_key='nsf_programs',
    # No checkbox behind this picker, so the param never arrives — see §10.
    active_only_default=True,
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


# ── Mnemonic Code Create (bespoke: DB-uniqueness checks) ───────────────────


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


# ── Contract detail card ──────────────────────────────────────────────────
#
# Same shape as user_card / project_card / group_card in blueprint.py, but it
# lives here with the rest of the contract surface. Two hosts swap the same
# HTML: the #contractCardContainer region on /admin/contracts, and
# #contractDetailsModalBody wherever a contract number is clickable.


@bp.route('/contract/<int:contract_id>')
@login_required
@require_permission_any_facility(Permission.VIEW_ORG_METADATA)
def contract_card(contract_id):
    """HTML fragment for a single contract's detail card.

    Keyed by id, not number: contract numbers are free text (``USDA Prime
    Award No. 2013-67003-20652``, ``OCE- 1419584``) and cannot key a URL
    path the way ``username`` and ``projcode`` do.
    """
    contract = get_contract_detail(db.session, contract_id)
    if not contract:
        # Warning div at 200, matching the other *_card routes: htmx swaps
        # it into the card region or modal body rather than erroring.
        return '<div class="alert alert-warning">Contract not found</div>'

    # Active projects first, then by code. ProjectContract carries no date
    # window of its own, so there is nothing else meaningful to sort on.
    linked_projects = sorted(
        contract.projects,
        key=lambda pc: (not pc.project.active, pc.project.projcode),
    )

    return render_template(
        'dashboards/admin/fragments/contract_card.html',
        contract=contract,
        linked_projects=linked_projects,
        # Gate the cross-entity links on the *target* routes' permissions so
        # a click can never 403 (the rule jobs_usage_panel.html states).
        can_view_users=has_permission_any_facility(
            current_user, Permission.VIEW_USERS),
        can_view_projects=has_permission_any_facility(
            current_user, Permission.VIEW_PROJECTS),
    )


@bp.route('/nsf-program/<int:nsf_program_id>/contracts')
@login_required
@require_permission_any_facility(Permission.VIEW_ORG_METADATA)
def nsf_program_contracts(nsf_program_id):
    """The contracts under one NSF program, for the drill-down modal.

    NSFProgram carries only a name and an active flag, so it gets no detail
    card of its own — this list is the one question the data supports, and
    it gives the previously-dead "# Contracts" count somewhere to go.
    """
    program, contracts = get_nsf_program_contracts(db.session, nsf_program_id)
    if program is None:
        return '<div class="alert alert-warning">NSF program not found</div>'

    return render_template(
        'dashboards/admin/fragments/nsf_program_contracts_htmx.html',
        program=program,
        contracts=contracts,
        can_view_users=has_permission_any_facility(
            current_user, Permission.VIEW_USERS),
    )


# ── Contract Create (bespoke: award prefill + FK/uniqueness checks) ───────
#
# The generated CrudSpec create closure calls Model.create() directly with no
# hook, so it can express neither the FK-existence checks the monitor/program
# columns need nor a contract_number uniqueness pre-check (the column carries
# a unique index, and an IntegrityError surfaces as an unreadable 500-ish
# error string). Both halves are therefore hand-written here, keeping the
# endpoint names and URL rules the spec would have generated so the card's
# url_for() calls and tests/unit/test_admin_orgs_crud.py are untouched.
#
# The form has two modes. They are *presentational*: "look up" adds a Fetch
# button that prefills the field block via htmx, and Create then submits
# exactly what the operator sees. No award lookup happens in the POST path —
# a slow agency API must never sit inside a write transaction, and the
# operator's edits must always win over a possibly-stale prefill.

CREATE_CONTRACT_TEMPLATE = 'dashboards/admin/fragments/create_contract_form_htmx.html'
CREATE_CONTRACT_FIELDS_TEMPLATE = 'dashboards/admin/fragments/create_contract_fields_htmx.html'

#: AwardRecord.unavailable_fields -> what the operator sees in the form.
_UNAVAILABLE_LABELS = {'pi': 'the PI', 'monitor': 'the Monitor'}


def _user_label(user):
    return f'{user.display_name} ({user.username})' if user else ''


#: FK-picker field -> (model, label callable). ``fk_search_field`` renders its
#: badge from ``form.get(name ~ '_display')``, but nothing in the DOM posts
#: that key — so every re-render (validation error, prefill, program create)
#: would otherwise show a selected row as a blank badge.
_PICKER_LABELS = (
    ('principal_investigator_user_id', User, _user_label),
    ('contract_monitor_user_id', User, _user_label),
    ('nsf_program_id', NSFProgram, lambda p: p.nsf_program_name if p else ''),
)


def _clear_picker(form, field):
    """Empty an FK picker, badge label included."""
    form[field] = ''
    form[f'{field}_display'] = ''


def _with_picker_labels(form):
    """Copy *form*, adding the ``<field>_display`` keys the FK pickers need."""
    data = dict(form or {})
    for field, model, label in _PICKER_LABELS:
        raw = str(data.get(field) or '').strip()
        if not raw.isdigit():
            continue
        data.setdefault(f'{field}_display', label(db.session.get(model, int(raw))))
    return data


def _contract_create_context(form=None, **extra):
    """Render context shared by initial render, prefill, and error re-render."""
    ctx = {
        'contract_sources': _active_contract_sources(),
        'today': datetime.now().strftime('%Y-%m-%d'),
        'form': _with_picker_labels(form),
    }
    ctx.update(extra)
    return ctx


@bp.route('/htmx/contract-create-form')
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_contract_create_form():
    """Render the Create Contract form."""
    return render_template(CREATE_CONTRACT_TEMPLATE, **_contract_create_context())


@bp.route('/htmx/contract-award-lookup')
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_contract_award_lookup():
    """Prefill the contract field block from the funding source's API.

    Seeded via ``hx-include`` from everything already typed, so a miss
    re-renders the operator's own input rather than wiping it. Returns 204
    (htmx: no swap) only when there is nothing to look up at all.

    Suggest, don't impose: a PI or monitor that resolves to a SAM user
    pre-selects the picker; one that does not is rendered as a read-only
    hint carrying the agency's raw name and email, with an explicit
    apply-or-search affordance. Same for a program name absent from
    ``nsf_program``. We never invent a user.
    """
    from sam.integration.awards import (
        AwardSourceUnavailable, resolve_award, resolve_person,
    )

    form = dict(request.args)
    form.pop('q', None)          # the FK pickers' search boxes; not form data
    number = (form.get('contract_number') or '').strip()
    if not number:
        return '', 204

    source = db.session.get(ContractSource, int(form['contract_source_id'])) \
        if str(form.get('contract_source_id') or '').isdigit() else None
    source_name = source.contract_source if source else None

    def _render(**extra):
        return render_template(CREATE_CONTRACT_FIELDS_TEMPLATE,
                               **_contract_create_context(form, **extra))

    try:
        record = resolve_award(source_name, number)
    except AwardSourceUnavailable as exc:
        logger.warning('award lookup for %r failed: %s', number, exc)
        return _render(lookup_error=(
            f'{source_name or "The award source"} could not be reached. '
            f'Enter the details manually, or try again shortly.'))

    if record is None:
        return _render(lookup_error=(
            f'No award matching "{number}" was found'
            + (f' at {source_name}.' if source_name else '.')))

    extra = {'provenance': record.provenance,
             'unavailable_labels': [_UNAVAILABLE_LABELS.get(f, f)
                                    for f in sorted(record.unavailable_fields)]}

    if record.contract_number:
        form['contract_number'] = record.contract_number
    for field, value in (('title', record.title),
                         ('url', record.url)):
        if value:
            form[field] = value
    if record.start_date:
        form['start_date'] = record.start_date.isoformat()
    if record.end_date:
        form['end_date'] = record.end_date.isoformat()

    # When the source names someone we cannot map, CLEAR the field rather
    # than leave it. "Never destroy input" governs a *failed* lookup; here
    # the record has an opinion we simply could not resolve, and keeping a
    # previous award's pick next to "no matching SAM user" would be wrong.
    # A source with no opinion at all (USAspending has no people) falls
    # through untouched — the operator has to fill those in by hand anyway.
    for field, person, hint_key in (
            ('principal_investigator_user_id', record.pi, 'pi_hint'),
            ('contract_monitor_user_id', record.monitor, 'monitor_hint')):
        if not person:
            continue
        user = resolve_person(db.session, person)
        if user is not None:
            form[field] = str(user.user_id)
            form[f'{field}_display'] = _user_label(user)
        else:
            extra[hint_key] = person
            _clear_picker(form, field)

    if record.program_name:
        program = (
            db.session.query(NSFProgram)
            .filter(func.lower(NSFProgram.nsf_program_name)
                    == record.program_name.lower())
            .first()
        )
        if program is not None:
            form['nsf_program_id'] = str(program.nsf_program_id)
        else:
            extra['program_hint'] = record.program_name
            _clear_picker(form, 'nsf_program_id')

    return _render(**extra)


@bp.route('/htmx/contract-program-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_contract_program_create():
    """Create an NSF program from the create-contract form and select it.

    The opt-in half of the "unknown program" hint: the lookup only ever
    *offers* the agency's program name, and this is what happens when the
    operator accepts it. Re-renders just the select so the rest of the form
    is untouched.
    """
    name = (request.form.get('nsf_program_name') or '').strip()
    selected = (request.form.get('nsf_program_id') or '').strip()
    error = None

    if not name:
        error = 'No program name to create.'
    else:
        existing = (
            db.session.query(NSFProgram)
            .filter(func.lower(NSFProgram.nsf_program_name) == name.lower())
            .first()
        )
        if existing is not None:
            selected = str(existing.nsf_program_id)
        else:
            try:
                with management_transaction(db.session):
                    program = NSFProgram.create(db.session, nsf_program_name=name)
                selected = str(program.nsf_program_id)
            except Exception as exc:      # noqa: BLE001 — surface to the user
                error = f'Could not create NSF program: {exc}'

    return render_template(
        'dashboards/admin/fragments/contract_nsf_program_field_htmx.html',
        form=_with_picker_labels({'nsf_program_id': selected}),
        program_hint=name if error else None,
        program_error=error,
    )


class _ContractCreateHandler(HtmxFormHandler):
    """Create Contract.

    Beyond the generated quintet: FK-existence checks (§9), a
    ``contract_number`` uniqueness pre-check, and stripping the
    presentational ``contract_mode`` before the ORM call.
    """

    schema_cls = CreateContractForm
    template = CREATE_CONTRACT_TEMPLATE
    error_prefix = 'Error creating contract'
    success_message = 'Contract created successfully.'

    def clean(self, data):
        validate_fk_existence(
            db.session,
            (ContractSource, data.get('contract_source_id'), 'contract source'),
            (User, data.get('principal_investigator_user_id'),
             'principal investigator'),
            (User, data.get('contract_monitor_user_id'), 'contract monitor'),
            (NSFProgram, data.get('nsf_program_id'), 'NSF program'),
        )

        # contract_number carries a unique index; catching it here gives the
        # operator the conflicting contract instead of an IntegrityError.
        number = (data.get('contract_number') or '').strip()
        clash = (db.session.query(Contract)
                 .filter(Contract.contract_number == number).first())
        if clash is not None:
            raise FormError(
                f'Contract number "{number}" already exists '
                f'({clash.title[:60]}).')
        return data

    def perform(self, data):
        kwargs = {k: v for k, v in data.items() if k != 'contract_mode'}
        kwargs['start_date'] = datetime.combine(kwargs['start_date'],
                                                datetime.min.time())
        return Contract.create(db.session, **kwargs)

    def context(self):
        return _contract_create_context(request.form)

    def render_errors(self, errors, field_errors=None):
        # context() already supplies the augmented `form`, so do not let the
        # base class overwrite it with the raw request.form (which carries no
        # `_display` keys and would blank the picker badges).
        return render_template(self.template, errors=errors,
                               field_errors=field_errors or {},
                               **self.context())

    def triggers(self, result):
        return _ORG_TRIGGERS

    def detail(self, result):
        return f'{result.contract_number} — {result.title[:60]}'


@bp.route('/htmx/contract-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_ORG_METADATA)
def htmx_contract_create():
    """Create a new contract."""
    return _ContractCreateHandler().handle()


# ── Contract Delete (bespoke: retires by end_date, not active flag) ────────


@bp.route('/htmx/contract-delete/<int:contract_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_ORG_METADATA)
def htmx_contract_delete(contract_id):
    """Soft-delete (expire) a contract."""
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


# ── CRUD quintets — generated from specs ───────────────────────────────────
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
    _org_spec(
        slug='contract-source', name='Contract source',
        model=ContractSource, id_param='source_id', context_key='source',
        edit_schema=EditContractSourceForm, create_schema=CreateContractSourceForm,
        edit_fields=('contract_source', 'active'),
        create_fields=('contract_source',),
    ),
    _org_spec(
        slug='contract', name='Contract',
        model=Contract, id_param='contract_id', context_key='contract',
        edit_schema=EditContractForm, create_schema=CreateContractForm,
        # NB: the kwargs lambdas enumerate keys explicitly, so a new schema
        # field is silently dropped unless it is added here too.
        edit_kwargs=lambda data: dict(
            title=data['title'],
            url=data['url'],
            start_date=datetime.combine(data['start_date'], datetime.min.time()),
            end_date=data['end_date'],
            contract_monitor_user_id=data['contract_monitor_user_id'],
            nsf_program_id=data['nsf_program_id'],
        ),
        # create is bespoke (htmx_contract_create_form / htmx_contract_create):
        # it needs FK-existence checks and a contract_number uniqueness
        # pre-check, neither of which the generated closure can express.
        # delete is bespoke too (htmx_contract_delete — retires by end_date).
        actions=('edit',),
    ),
    _org_spec(
        slug='nsf-program', name='NSF program', noun='NSF program',
        model=NSFProgram, id_param='nsf_program_id', context_key='program',
        edit_schema=EditNsfProgramForm, create_schema=CreateNsfProgramForm,
        edit_fields=('nsf_program_name', 'active'),
        create_fields=('nsf_program_name',),
    ),
)

for _spec in _ORG_CRUD_SPECS:
    register_crud(bp, _spec)


# ── User search for FK fields ──────────────────────────────────────────────
# Note: user search is handled by the unified admin_dashboard.htmx_search_users
# endpoint (admin/blueprint.py) with context='fk'.
