"""
Admin dashboard — Contract, contract-source, and NSF-program routes.

Split out of `orgs_routes.py`, where contracts had grown to roughly 60% of the
module and half its endpoints. They are no longer org metadata in the way
institutions and areas of interest are: they have their own page
(/admin/contracts), their own detail card, their own reload event
(`_CONTRACT_TRIGGERS`), and an award-source integration behind them.

NSF programs come along because every one of their surfaces is reached from a
contract — the drill-down modal, the create form's picker, the lookup's
"create and select" hint. The Organizations card keeps its NSF Programs *tab*;
it renders from `get_nsf_programs_with_contracts` and the shared macros.

The CRUD quintets are generated from `_CONTRACT_CRUD_SPECS` at the bottom via
`register_crud`; hand-written routes are the ones that genuinely deviate —
contract create (award prefill, FK-existence and uniqueness checks) and
contract delete (retires by end_date, not an active flag).
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
from sam.core.users import User
from sam.projects.contracts import Contract, ContractSource, NSFProgram
#: AwardRecord.unavailable_fields -> what the operator sees in the form.
#: Shared with `sam-search awards`, which renders the same sentence.
from sam.integration.awards import UNAVAILABLE_FIELD_LABELS as _UNAVAILABLE_LABELS
from sam.queries.admin import (
    get_contract_detail,
    get_contracts_with_pi,
    get_nsf_program_contracts,
)
from sam.schemas.forms.orgs import (
    EditContractSourceForm, CreateContractSourceForm,
    EditContractForm, CreateContractForm,
    EditNsfProgramForm, CreateNsfProgramForm,
)

from .blueprint import bp
from .crud import CrudSpec, register_crud


logger = logging.getLogger(__name__)

#: Contracts and contract sources render on /admin/contracts, not on the
#: Organizations card, so their mutations must reload a different section.
#: `_reloadAdminCard` starts with `if (!section) return;`, so firing the
#: wrong event here fails *silently* — the modal closes and nothing refreshes.
_CONTRACT_TRIGGERS = modal_triggers('reloadContractsCard')


#: Public because the /admin/contracts page route in `blueprint.py` renders
#: the Source filter server-side and so needs this at page-render time. The
#: import there stays function-local: `blueprint.py` imports the route modules
#: at its tail, so a module-scope import would cycle.
def active_contract_sources():
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


def _search_contracts(q, active_only):
    """Search box behind /admin/contracts.

    Unlike the FK-picker sibling ``_search_contracts_for_project``, this one
    **honours** ``active_only`` — that one has no checkbox, this one does.
    The checkbox defaults off: only 368 of 2,225 contracts are currently
    active, so an active-only default would hide 83% of the data behind a
    control most operators would not think to clear.

    ``with_details`` is what keeps the eager loads this page's result rows
    need (source badge, PI line); the FK picker asks for neither.
    """
    return Contract.search_by_pattern(
        db.session, q, active_only=active_only, limit=20, with_details=True)


register_typeahead(
    bp, rule='/htmx/search/contracts', endpoint='htmx_search_contracts',
    permission=Permission.VIEW_CONTRACTS, any_facility=True,
    search=_search_contracts,
    template='dashboards/admin/fragments/contract_search_results_htmx.html',
    ctx_key='contracts',
)


register_typeahead(
    bp, rule='/htmx/search/nsf-programs', endpoint='htmx_search_nsf_programs',
    permission=Permission.VIEW_CONTRACTS,
    search=_search_nsf_programs_fk,
    template='dashboards/admin/fragments/nsf_program_search_results_fk_htmx.html',
    ctx_key='nsf_programs',
    # No checkbox behind this picker, so the param never arrives — see §10.
    active_only_default=True,
)


# ── Contract detail card ──────────────────────────────────────────────────
#
# Same shape as user_card / project_card / group_card in blueprint.py, but it
# lives here with the rest of the contract surface. Two hosts swap the same
# HTML: the #contractCardContainer region on /admin/contracts, and
# #contractDetailsModalBody wherever a contract number is clickable.


@bp.route('/contract/<int:contract_id>')
@login_required
@require_permission_any_facility(Permission.VIEW_CONTRACTS)
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
@require_permission_any_facility(Permission.VIEW_CONTRACTS)
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
        'contract_sources': active_contract_sources(),
        'today': datetime.now().strftime('%Y-%m-%d'),
        'form': _with_picker_labels(form),
    }
    ctx.update(extra)
    return ctx


@bp.route('/htmx/contracts-table')
@login_required
@require_permission_any_facility(Permission.VIEW_CONTRACTS)
@cache.cached(make_cache_key=user_aware_cache_key)
def htmx_contracts_table():
    """The All Contracts table on /admin/contracts, grouped by funding source.

    Moved out of ``htmx_organizations_card``'s Contracts tab. Same query and
    same grouping; it simply lives where an operator looks for it.

    The table defaults to active-only, unlike the search box above it on the
    same page: that one is a query you have already narrowed by typing,
    whereas this is a browse table over 2,225 rows.

    That default is expressed in the **template** — the checkbox ships
    `checked` and the section's initial `hx-get` carries `?active_only=1` —
    NOT as `default=True` here. htmx omits an unchecked checkbox entirely, so
    absent has to mean off; reading it as on makes the toggle a no-op
    (measured: unchecking re-fetched without the param and still returned the
    369 active rows). See CLAUDE.md § 10.
    """
    active_only = read_active_only(request.args)

    cs_q = db.session.query(ContractSource).order_by(ContractSource.contract_source)
    if active_only:
        cs_q = cs_q.filter(ContractSource.is_active)

    return render_template(
        'dashboards/admin/fragments/contracts_table_htmx.html',
        contract_sources=cs_q.all(),
        contracts=get_contracts_with_pi(db.session, active_only=active_only),
        active_only=active_only,
        # Gate the PI/Monitor links on the user_card route's own permission
        # so a click can never 403. Safe under @cache.cached because the key
        # is user-aware.
        can_view_users=has_permission_any_facility(
            current_user, Permission.VIEW_USERS),
    )


@bp.route('/htmx/contract-create-form')
@login_required
@require_permission(Permission.CREATE_CONTRACTS)
def htmx_contract_create_form():
    """Render the Create Contract form, optionally seeded from an award.

    "Create contract from this award" on /admin/contracts passes
    ``contract_number`` (and, for NSF, ``contract_source_id``). Seeding
    happens **server-side**, through the ``form`` dict the template already
    consumes, rather than by having JS fill inputs after the swap: the form
    does not exist yet when the button is clicked, so a JS approach would
    have to sequence itself against the htmx response.

    A seeded form opens in lookup mode and auto-fires the existing award
    lookup (``seeded=True`` → ``hx-trigger="load"`` on the Fetch button), so
    one click yields a full prefill **including Monitor and program**, which
    the search result itself structurally cannot carry.
    """
    number = (request.args.get('contract_number') or '').strip()
    source_id = (request.args.get('contract_source_id') or '').strip()

    if not number:
        return render_template(CREATE_CONTRACT_TEMPLATE,
                               **_contract_create_context())

    form = {'contract_number': number, 'contract_mode': 'lookup'}
    if source_id.isdigit():
        form['contract_source_id'] = source_id

    return render_template(
        CREATE_CONTRACT_TEMPLATE,
        **_contract_create_context(form, seeded=True))


#: Minimum query length before the award search bothers the public APIs.
AWARD_SEARCH_MIN_LEN = 3

#: Per provider, so the composite can return more. Ten each is one screenful.
AWARD_SEARCH_LIMIT = 10

CONTRACT_AWARD_SEARCH_TEMPLATE = (
    'dashboards/admin/fragments/contract_award_search_results_htmx.html')

CONTRACT_AWARD_CANDIDATES_TEMPLATE = (
    'dashboards/admin/fragments/contract_award_candidates_htmx.html')


def _award_search_context(query, source_id):
    """Search the award providers and annotate hits SAM already has.

    Shared by the two renderers below — the compact rows inside the create
    modal and the cards on /admin/contracts. One place makes the provider
    call and does the in-SAM annotation; each route picks a template.

    Returns a context dict ready to splat into ``render_template``. Errors are
    never raised: an unreachable source becomes ``search_error`` (nothing
    usable came back) or ``partial_errors`` (some did), because a 500 inside
    an htmx fragment is a blank hole in the page.
    """
    from sam.integration.awards import search_awards

    source = db.session.get(ContractSource, int(source_id)) \
        if str(source_id or '').isdigit() else None
    source_name = source.contract_source if source else None

    try:
        records, errors = search_awards(
            query, limit=AWARD_SEARCH_LIMIT,
            sources=[source_name] if source_name else None)
    except Exception as exc:                      # pragma: no cover - defensive
        # search_awards already downgrades per-provider failures into
        # `errors`; anything escaping it is a bug, but an inline note still
        # beats a 500 inside the form.
        logger.warning('award search for %r failed: %s', query, exc)
        return {'q': query, 'results': [],
                'search_error': 'The award search could not be completed. '
                                'Enter the details manually, or try again '
                                'shortly.'}

    if errors and not records:
        # Every provider down reads as "no such award" unless we say so.
        return {'q': query, 'results': [],
                'search_error': ' '.join(
                    f'{e["provenance"]} could not be reached.'
                    for e in errors)
                + ' Enter the details manually, or try again shortly.'}

    return {'q': query,
            'results': _annotate_known(records),
            'nsf_source_id': _nsf_source_id(),
            'partial_errors': errors}


@bp.route('/htmx/contract-award-search')
@login_required
@require_permission(Permission.CREATE_CONTRACTS)
def htmx_contract_award_search():
    """Free-text search across the award providers, above the Fetch button.

    Hand-written rather than :func:`register_typeahead`, whose own docstring
    blesses the exception ("endpoints whose branching is the feature stay
    hand-written"). It *fits* that signature — the search callable never
    touches the session — but it would turn an
    :class:`AwardSourceUnavailable` into a 500, and its template contract is
    only ``{q, <ctx_key>}``, with nowhere to put a per-provider error note.

    Results are **summaries**: USAspending's program name comes from a
    detail-only endpoint. Picking a row therefore seeds the number and fires
    the existing lookup rather than prefilling directly — that chain is what
    recovers the program name, and for NSF it is free.

    Rows whose number SAM already has are annotated, which is the same
    protection ``_ContractCreateHandler.clean`` gives on submit, one
    round-trip earlier.
    """
    query = (request.args.get('q') or '').strip()
    if len(query) < AWARD_SEARCH_MIN_LEN:
        return ''

    return render_template(
        CONTRACT_AWARD_SEARCH_TEMPLATE,
        **_award_search_context(query, request.args.get('contract_source_id')))


@bp.route('/htmx/contract-award-candidates')
@login_required
@require_permission(Permission.CREATE_CONTRACTS)
def htmx_contract_award_candidates():
    """"Find Candidate Contracts" on /admin/contracts — the same search, as cards.

    The page-level sibling of :func:`htmx_contract_award_search`. Gated on
    ``CREATE_CONTRACTS`` rather than the page's own ``VIEW_CONTRACTS``
    because every row here leads to creating a contract, and because it
    spends two public APIs' quota per press.

    A card whose number SAM already has offers "View contract" instead of a
    create button: the existing contract is one click away on this very page,
    which is strictly more useful than the create modal's disabled state.
    """
    query = (request.args.get('q') or '').strip()
    if len(query) < AWARD_SEARCH_MIN_LEN:
        return ''

    return render_template(
        CONTRACT_AWARD_CANDIDATES_TEMPLATE,
        **_award_search_context(query, request.args.get('contract_source_id')))


def _annotate_known(records):
    """Pair each record with the SAM contract of the same number, if any."""
    from sam.projects.contracts import Contract, normalize_contract_number

    known = Contract.existing_by_number(
        db.session, [r.contract_number for r in records])

    return [{'record': r,
             'in_sam': known.get(normalize_contract_number(r.contract_number))}
            for r in records]


def _nsf_source_id():
    """The ``contract_source`` row named NSF, resolved by name at runtime.

    Never a hardcoded id — lookup-table PKs differ between environments.
    Returns ``None`` if NSF is absent or inactive, in which case the Use
    button simply leaves Source for the operator.

    NSF only: USAspending spans many agencies and its ``Awarding Agency``
    string ("Department of Defense") does not match our source names ("DOD"),
    so guessing there would be worse than not guessing.
    """
    source = (db.session.query(ContractSource)
              .filter(ContractSource.contract_source == 'NSF',
                      ContractSource.is_active)
              .first())
    return source.contract_source_id if source else None


@bp.route('/htmx/contract-award-lookup')
@login_required
@require_permission(Permission.CREATE_CONTRACTS)
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
@require_permission(Permission.CREATE_CONTRACTS)
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
        clash = Contract.get_by_number(db.session, number)
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

    def triggers(self, result):
        return _CONTRACT_TRIGGERS

    def detail(self, result):
        return f'{result.contract_number} — {result.title[:60]}'


@bp.route('/htmx/contract-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_CONTRACTS)
def htmx_contract_create():
    """Create a new contract."""
    return _ContractCreateHandler().handle()


# ── Contract Delete (bespoke: retires by end_date, not active flag) ────────


@bp.route('/htmx/contract-delete/<int:contract_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_CONTRACTS)
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
# Endpoints, URL rules, templates and not-found messages are unchanged by the
# split from orgs_routes (pinned by the route-map parity snapshot). The
# permissions are not: the whole contract surface moved off *ORG_METADATA onto
# its own *CONTRACTS family, so contract administration can be granted
# without conferring write on organizations, institutions and AOIs.

_contract_spec = partial(
    CrudSpec,
    triggers=_CONTRACT_TRIGGERS,
    edit_permission=Permission.EDIT_CONTRACTS,
    create_permission=Permission.CREATE_CONTRACTS,
    delete_permission=Permission.DELETE_CONTRACTS,
)

_CONTRACT_CRUD_SPECS = (
    _contract_spec(
        slug='contract-source', name='Contract source',
        model=ContractSource, id_param='source_id', context_key='source',
        edit_schema=EditContractSourceForm, create_schema=CreateContractSourceForm,
        edit_fields=('contract_source', 'active'),
        create_fields=('contract_source',),
    ),
    _contract_spec(
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
    _contract_spec(
        slug='nsf-program', name='NSF program', noun='NSF program',
        model=NSFProgram, id_param='nsf_program_id', context_key='program',
        edit_schema=EditNsfProgramForm, create_schema=CreateNsfProgramForm,
        edit_fields=('nsf_program_name', 'active'),
        create_fields=('nsf_program_name',),
    ),
)

for _spec in _CONTRACT_CRUD_SPECS:
    register_crud(bp, _spec)
