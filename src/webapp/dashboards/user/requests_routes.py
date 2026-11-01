"""User dashboard -> My Requests: the user's XRAS requests, and what each project may file.

The page renders from SAM's tables and the sweep's index, never a live XRAS read; each
project's picker reads XRAS lazily when opened (the family, then the user's offers). A form
re-reads the family and re-runs the routing at every stage (edit, review, submit), so a
decision is never carried in the browser; a New re-reads the user's opportunities the same
way, and nothing reaches XRAS before its submit. Files go through
``sam.manage.xras_submission``. Design: ``docs/plans/XRAS_SUBMISSION.md`` § 4.0-4.1.
"""

from datetime import datetime, timedelta

from flask import (
    abort,
    current_app,
    flash,
    make_response,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from sam.dates import add_months, first_of_next_month, parse_wire_date
from sam.integration.xras_api import XrasSourceUnavailable
from sam.integration.xras_api.lookups import funding_agency_choices
from sam.queries.xras_my_requests import led_projects, my_requests
from sam.schemas.forms import (
    XrasExtensionRequestForm,
    XrasNewRequestForm,
    XrasRenewalRequestForm,
    XrasSupplementRequestForm,
)
from sam.schemas.forms.xras_submission import GRANT_INPUTS
from sam.xras.routing import (
    action_offers,
    inline_offers,
    offer_candidates,
    request_routing,
    route_for,
)
from webapp.api.access_control import require_project_permission
from webapp.auth.impersonation import acting_username, is_impersonating
from webapp.extensions import db
from webapp.utils.form_handler import FormError, HtmxFormHandler
from webapp.utils.htmx import register_typeahead
from webapp.utils.xras import (
    admin_client,
    interactive_config,
    read_client,
    requests_index,
    resource_keys,
    session_factory,
    type_cards,
)
from webapp.utils.rbac import Permission, has_permission

from .blueprint import _page_context, bp

_MODAL_BODY = '#xrasRequestModalBody'
_FORM = 'dashboards/user/fragments/request_form_htmx.html'
_REVIEW = 'dashboards/user/fragments/request_review_htmx.html'
_OUTCOME = 'dashboards/user/fragments/request_outcome_htmx.html'
_MESSAGE = 'dashboards/user/fragments/request_message_htmx.html'
_FORM_TYPES = {'extension': 'Extension', 'supplement': 'Supplement', 'renewal': 'Renewal'}
_SCHEMAS = {'Extension': XrasExtensionRequestForm, 'Supplement': XrasSupplementRequestForm,
            'Renewal': XrasRenewalRequestForm}


#: The module's seam for the tests; the plumbing itself is ``webapp.utils.xras``.
_config = interactive_config
_session_factory = session_factory


def _index_rows():
    return (requests_index() or {}).get('rows') or []


def _type_name(project):
    return project.allocation_type.allocation_type if project.allocation_type else None


def _decide(project, config):
    """``(decision, outage)``: the routing for the current user, reading the family only when armed."""
    family = None
    if config.submit_configured and route_for(_type_name(project)) == 'xras':
        try:
            family = read_client(config).get_request_family_by_number(project.projcode)
        except XrasSourceUnavailable as exc:
            current_app.logger.warning('my requests family read %s: %s', project.projcode, exc)
            return None, 'XRAS is not answering, so this project\'s requests cannot be offered now.'
    return request_routing(db.session, project, family, current_user.username,
                           armed=config.submit_configured), None


@bp.route('/requests')
@login_required
def my_requests_page():
    """My Requests: the status of every request, then one picker per led or administered project."""
    ctx = _page_context()
    user = ctx['user']
    projects = led_projects(db.session, user)
    config = _config()
    return render_template(
        'dashboards/user/my_requests.html',
        requests=my_requests(db.session, user.username, _index_rows()),
        projects=[(p, route_for(_type_name(p))) for p in projects],
        open_projcode=(request.args.get('projcode') or '').strip().upper() or None,
        submit_configured=config.submit_configured, new_enabled=config.submit_allows('New'),
        submit_allows=config.submit_allows,
        can_manage_xras=has_permission(current_user, Permission.MANAGE_XRAS),
        **ctx)


@bp.route('/requests/<projcode>/actions')
@login_required
@require_project_permission(Permission.EDIT_PROJECTS)
def my_requests_actions(project):
    """One project's offers, each enabled or disabled with its reason."""

    config = _config()
    decision, outage = _decide(project, config)
    available = []
    if decision is not None and decision.allowed:
        try:
            available = admin_client(config).available_action_requests(
                current_user.username)
        except XrasSourceUnavailable as exc:
            current_app.logger.warning('my requests offers read %s: %s',
                                       current_user.username, exc)
            available = None
    return render_template(
        'dashboards/user/fragments/request_actions_htmx.html',
        project=project, decision=decision, outage=outage,
        form_types={v: k for k, v in _FORM_TYPES.items()},
        offers=action_offers(decision, available, config.submit_allows) if decision else [])


# the Extension, Supplement and Renewal forms

@bp.route('/requests/htmx/offers')
@login_required
def my_requests_offers():
    """Every project row's request buttons in one response (out-of-band swaps), at most one XRAS read."""
    config = _config()
    if not config.submit_configured:
        return ''
    projects = led_projects(db.session, current_user)
    index_rows = _index_rows()
    available = None
    # The read feeds the Renewal check alone: a lead of staff-routed projects, or a site with
    # Renewal off, gets Extend and Supplement with no XRAS round trip.
    if config.submit_allows('Renewal') and offer_candidates(
            projects, current_user.username, index_rows, armed=True):
        try:
            available = admin_client(config).available_action_requests(current_user.username)
        except XrasSourceUnavailable as exc:
            current_app.logger.warning('my requests inline offers: %s', exc)
    offers = inline_offers(db.session, projects, current_user.username, index_rows,
                           available, config.submit_allows, armed=True)
    return render_template('dashboards/user/fragments/request_offers_htmx.html',
                           projects=projects, offers=offers,
                           form_types={v: k for k, v in _FORM_TYPES.items()})


@bp.route('/requests/<number>/notes')
@login_required
def my_request_notes(number):
    """The comments XRAS holds on one action of a row the user sees, read live on first open."""
    from sam.integration.xras_api.comments import find_action, normalize_comment
    action_id = request.args.get('action_id', type=int)
    # The read client does not carry the PI as XA-USER, so SAM decides who may read the line.
    if action_id is None or not any(
            r['request_number'] == number and r['action_id'] == action_id
            for r in my_requests(db.session, current_user.username, _index_rows())):
        abort(404)
    try:
        action = find_action(read_client(_config()).get_request_family_by_number(number),
                             action_id) or {}
    except XrasSourceUnavailable as exc:
        current_app.logger.warning('my requests notes %s/%s: %s', number, action_id, exc)
        return render_template('dashboards/user/fragments/request_notes_htmx.html', outage=True)
    return render_template('dashboards/user/fragments/request_notes_htmx.html', outage=False,
                           approver=normalize_comment(action.get('adminComments')),
                           yours=normalize_comment(action.get('userComments')))


def message_fragment(title, message, variant='warning'):
    """A refusal or notice in the request dialog's body."""
    return render_template(_MESSAGE, title=title, message=message, variant=variant)


def _refuse_unless_armed(config, action_type):
    """``FormError`` unless this session may file *action_type* here."""
    if is_impersonating():
        raise FormError('You are viewing SAM as another user. They file their own '
                        'requests, under their own agreement.')
    if not config.submit_allows(action_type):
        raise FormError(f'{action_type} requests through SAM are not enabled here.')


def _read_upload(field, label):
    """``(filename, content, why not)`` for the PDF posted as *field*."""
    from sam.xras.submission_preflight import document_refusal
    upload = request.files.get(field)
    if upload is None or not upload.filename:
        return None, None, f'Attach the {label}.'
    content = upload.read()
    return upload.filename, content, document_refusal(upload.filename, content)


class _RequestForm(HtmxFormHandler):
    """One request through edit -> review -> submit; every stage re-checks the gate live."""

    template = _FORM
    error_prefix = 'The request could not be filed'

    def __init__(self, *, project, action_type):
        super().__init__(project=project, action_type=action_type)
        self.schema_cls = _SCHEMAS[action_type]
        self._ctx = None

    def form_input(self):
        if self.action_type == 'Extension':
            return request.form
        raw = request.form.to_dict()
        raw['amounts'] = {k[len('amount_'):]: v for k, v in request.form.items()
                          if k.startswith('amount_')}
        return raw

    def gate(self):
        """The live context for this project and type; ``FormError`` when it may not file."""
        if self._ctx is not None:
            return self._ctx
        from webapp.register.eula import eula_sha
        config = _config()
        _refuse_unless_armed(config, self.action_type)
        decision, outage = _decide(self.project, config)
        if outage:
            raise FormError(outage)
        if not decision.allowed:
            raise FormError(decision.message)
        self._ctx = dict(project=self.project, action_type=self.action_type,
                         decision=decision, eula_sha=eula_sha(), grant_inputs=list(GRANT_INPUTS),
                         grant_agencies=funding_agency_choices(),
                         post_url=request.path, **self._type_context(config, decision))
        return self._ctx

    def _type_context(self, config, decision):
        from sam.xras.submission_preflight import extension_floor, line_resources
        floor = extension_floor(self.project)
        if self.action_type == 'Extension':
            return {'floor': floor, 'end_min': floor + timedelta(days=1) if floor else None,
                    'resources': [], 'resource_keys': None}
        if self.action_type == 'Supplement':
            return {'floor': None, **self._with_limits(config, line_resources(decision.line))}
        return self._renewal_context(config, decision, floor)

    def _renewal_context(self, config, decision, floor):
        """XRAS's renewal offer for the line (opportunity, resources, award period) and its rule book."""
        from sam.integration.xras_api.rule_book import rule_book
        from sam.xras.submission_preflight import line_resources
        try:
            offers = admin_client(config).available_action_requests(
                current_user.username)
        except XrasSourceUnavailable:
            raise FormError('XRAS is not answering, so the renewal cannot be offered now.')
        offer = next((o for o in offers if o['action_type'] == 'Renewal'
                      and o['request_number'] == decision.request_number), None)
        if offer is None:
            raise FormError('XRAS offers no renewal for this project right now.')
        rules = rule_book(read_client(config), offer['allocation_type_id'], 'Renewal',
                          allocation_type_name=offer['allocation_type'])
        begin = floor + timedelta(days=1) if floor else datetime.now().date()
        months = offer.get('award_months') or 12
        end = add_months(begin, months) - timedelta(days=1)
        held = {r['resource_id'] for r in line_resources(decision.line)}
        resources = [dict(r, prefill=r['resource_id'] in held) for r in offer['resources']]
        return {'floor': floor, 'offer': offer, 'rules': rules,
                'documents': [d for d in rules.documents if d.required],
                'default_begin': begin, 'default_end': end,
                'line_abstract': (decision.line or {}).get('abstract') or '',
                'grant_defaults': self._line_grant(decision.line),
                **self._with_limits(config, resources)}

    @staticmethod
    def _line_grant(line):
        """The grant fieldset's values from the line's first numbered grant; XRAS spells the agency by name."""
        grant = next((g for g in (line or {}).get('grants') or ()
                      if isinstance(g, dict) and (g.get('grantNumber') or '').strip()), None)
        if grant is None:
            return None
        agencies = {name: agency_id for agency_id, name in funding_agency_choices()}
        values = {name: grant.get(wire) for name, wire in GRANT_INPUTS.items()
                  if name != 'grant_agency_id'}
        values['grant_agency_id'] = agencies.get(grant.get('fundingAgency'))
        for name in ('grant_begin_date', 'grant_end_date'):
            values[name] = parse_wire_date(values[name])
        return {k: v for k, v in values.items() if v}

    def _with_limits(self, config, resources, type_name=None):
        """*resources* with their SAM resource and type limit, through the catalog key."""
        from sam.xras.submission_preflight import resources_with_limits
        keys = resource_keys(config)
        name = type_name or (_type_name(self.project) if self.project else None)
        resources_with_limits(db.session, resources, keys, name)
        return {'resources': resources, 'resource_keys': keys}

    def context(self):
        try:
            return self.gate()
        except FormError:
            return dict(project=self.project, action_type=self.action_type, decision=None,
                        resources=[], floor=None, eula_sha=None, post_url=request.path)

    def draft(self, data):
        from sam.manage.xras_submission import ActionRequest, ResourceAmount, SamRefs
        ctx, project = self._ctx, self.project
        decision = ctx['decision']
        common = dict(action_type=self.action_type, request_number=decision.request_number,
                      request_id=decision.request_id, user_comments=data.get('user_comments'),
                      sam=SamRefs(project_id=project.project_id,
                                  allocation_type_id=project.allocation_type_id))
        if self.action_type == 'Extension':
            return ActionRequest(dates=(None, data['end_date']), **common)
        resources = tuple(ResourceAmount(rid, amount) for rid, amount in data['amounts'].items())
        if self.action_type == 'Supplement':
            return ActionRequest(resources=resources, **common)
        return ActionRequest(opportunity_id=ctx['offer']['opportunity_id'],
                             eua_attribute_id=ctx['offer'].get('eua_attribute_id'),
                             abstract=data['abstract'], resources=resources,
                             dates=(data['begin_date'], data['end_date']),
                             grants=self._grants(data),
                             documents=tuple(data.get('documents') or ()), **common)

    def clean(self, data):
        from marshmallow import ValidationError
        ctx = self.gate()
        if data['stage'] == 'edit':
            return data
        if self.action_type == 'Extension':
            if ctx['floor'] and data['end_date'] <= ctx['floor']:
                raise ValidationError({'end_date': [
                    f'Choose a date after {ctx["floor"].isoformat()}, the project\'s '
                    'current end.']})
        else:
            if ctx.get('rules') and ctx['rules'].wants_grants:
                errors = self._grant_errors(data, ctx['rules'])
                if errors:
                    raise ValidationError(errors)
            self._check_limits(data, ctx, _type_name(self.project))
        return self._assemble(data, ctx)

    def _check_limits(self, data, ctx, type_name):
        """Every amount names a resource XRAS offers here, within SAM's limits for *type_name*."""
        from marshmallow import ValidationError
        from sam.xras.submission_preflight import limit_refusals
        by_id = {r['resource_id']: r for r in ctx['resources']}
        if any(rid not in by_id for rid in data['amounts']):
            raise FormError('XRAS does not offer one of those resources here; reopen the form.')
        named = {by_id[rid]['sam_name']: amt for rid, amt in data['amounts'].items()
                 if by_id[rid]['sam_name']}
        refused = limit_refusals(db.session, type_name, named)
        errors = {f'amount_{rid}': [refused[by_id[rid]['sam_name']]]
                  for rid in data['amounts'] if by_id[rid]['sam_name'] in refused}
        if errors:
            raise ValidationError(errors)

    def _assemble(self, data, ctx):
        """Review and submit share this tail: the documents, the draft, the verdict, back to review."""
        if data['stage'] == 'submit' and ctx.get('documents'):
            data['documents'], data['document_error'] = self._attached(ctx['documents'])
        data['draft'] = self.draft(data)
        data['verdict'] = self._verdict(data, ctx)
        if data['stage'] == 'submit':
            back = self._back_to_review(data)
            if back:
                data.update(stage='review', review_error=back)
        return data

    def _verdict(self, data, ctx):
        from sam.xras.submission_preflight import preflight_draft
        offer = ctx.get('offer')
        return preflight_draft(
            db.session, ctx['decision'].line, data['draft'], resource_keys=ctx['resource_keys'],
            opportunities={offer['opportunity_id']: {'allocationType': offer['allocation_type']}}
            if offer else None)

    @staticmethod
    def _grant_errors(data, rules):
        """``{field: [message]}`` for the grant fields the rule book requires and the form lacks."""
        if not data['grant_number']:
            return {'grant_number': ['This request needs a supporting grant.']}
        wire_to_input = {w: n for n, w in GRANT_INPUTS.items() if n != 'grant_agency_id'}
        errors = {wire_to_input[w]: ['Required for the grant.'] for w in rules.grant_fields
                  if w in wire_to_input and data.get(wire_to_input[w]) is None}
        agency = data.get('grant_agency_id')
        if agency is not None and agency not in dict(funding_agency_choices()):
            errors['grant_agency_id'] = ['Choose one of the agencies listed.']
        return errors

    @staticmethod
    def _grants(data):
        from sam.integration.xras_api.vocabulary import NSF_FUNDING_AGENCY_ID
        return ((dict({'fundingAgencyId': NSF_FUNDING_AGENCY_ID}, **data['grant']),)
                if data.get('grant') else ())

    @staticmethod
    def _attached(rules):
        """``(documents, why not)`` for the uploads the rule book requires."""
        from sam.manage.xras_submission import SubmissionDocument
        documents = []
        for rule in rules:
            filename, content, why = _read_upload(f'document_{rule.code}', rule.label)
            if why:
                return [], why
            documents.append(SubmissionDocument(rule.code, filename, content, title=rule.label))
        return documents, None

    def _back_to_review(self, data):
        """Why a submit returns to the review instead of filing, or ``None``."""
        if data['verdict'].status == 'failed':
            return 'SAM could not apply this once approved. Go back and change it.'
        if data.get('document_error'):
            return data['document_error']
        from webapp.register.eula import agreement_problem
        return agreement_problem(data['eula_sha'], data['agree'], shown_sha=self._ctx['eula_sha'])

    def perform(self, data):
        if data['stage'] != 'submit':
            return data
        from sam.manage import xras_submission as flow
        from webapp.register.eula import accepted
        try:
            data['outcome'] = self._file(flow, data['draft'], accepted(data['eula_sha']))
        except flow.SubmissionRefused as exc:
            raise FormError(str(exc))
        return data

    @staticmethod
    def _sessions():
        """The flow's session factory; the module seam the tests patch."""
        return _session_factory

    def _file(self, flow, draft, eula):
        """Open the submission through the PI door: the signed-in user files as themself."""
        return flow.request_action(self._sessions(), draft, actor=acting_username(),
                                   xa_user=current_user.username, eula=eula, door='pi')

    def on_success(self, data):
        ctx = dict(self._ctx, form=request.form, data=data)
        if data['stage'] == 'edit':
            return render_template(_FORM, errors=[], field_errors={}, **ctx)
        if data['stage'] == 'review':
            return render_template(_REVIEW, **ctx)
        return render_template(_OUTCOME, outcome=data['outcome'], **ctx)


@bp.route('/requests/<projcode>/<any(extension, supplement, renewal):kind>')
@login_required
@require_project_permission(Permission.EDIT_PROJECTS)
def my_request_form(project, kind):
    """The request form, into ``#xrasRequestModalBody``; a refusal renders as a message."""
    handler = _RequestForm(project=project, action_type=_FORM_TYPES[kind])
    try:
        ctx = handler.gate()
    except FormError as exc:
        return message_fragment(f'{handler.action_type} unavailable', ' '.join(exc.errors))
    return render_template(_FORM, errors=[], field_errors={}, form=None, **ctx)


@bp.route('/requests/<projcode>/<any(extension, supplement, renewal):kind>', methods=['POST'])
@login_required
@require_project_permission(Permission.EDIT_PROJECTS)
def my_request_post(project, kind):
    return _RequestForm(project=project, action_type=_FORM_TYPES[kind]).handle()


# the New request: describe -> request -> review -> submit

_NEW_TYPE = 'dashboards/user/fragments/request_new_type_htmx.html'
_NEW_DESCRIBE = 'dashboards/user/fragments/request_new_htmx.html'
_NEW_AMOUNTS = 'dashboards/user/fragments/request_new_amounts_htmx.html'
_GRANT = 'dashboards/user/fragments/request_grant_htmx.html'
#: The page-one inputs a later page carries in hidden fields, and whose errors land on page one.
_DESCRIBE_FIELDS = ('opportunity_id', 'title', 'abstract', 'keywords', 'fos_type_id',
                    'admin_user_id', 'mnemonic_code_id', 'roster') + tuple(GRANT_INPUTS)
#: People a New may name on page one; the rest join once the project exists.
ROSTER_MAX = 20


def _new_opportunities(config):
    """The open opportunities as the current user sees them; ``FormError`` on an outage."""
    try:
        return admin_client(config).new_request_opportunities(
            current_user.username)
    except XrasSourceUnavailable as exc:
        current_app.logger.warning('my requests opportunities %s: %s', current_user.username, exc)
        raise FormError('XRAS is not answering, so a new request cannot be started now.')


def _fos_options():
    from sam.integration.xras_api.lookups import get_fos_types
    try:
        rows = get_fos_types() or []
    except XrasSourceUnavailable:
        raise FormError('XRAS is not answering, so a new request cannot be started now.')
    return sorted((r for r in rows if r.get('isSelectable') is not False),
                  key=lambda r: r.get('fosName') or '')


def _opportunity_context(config, opportunity, pi, institution_name=None):
    """What page one reveals for *opportunity*: the rule book, the PI's mnemonic, the grant."""
    from sam.integration.xras_api.rule_book import rule_book
    from sam.xras.submission_preflight import new_request_sam_state
    if opportunity is None:
        return {'opportunity': None, 'rules': None, 'sam_state': None}
    rules = rule_book(read_client(config), opportunity['allocation_type_id'], 'New',
                      allocation_type_name=opportunity['allocation_type'])
    return {'opportunity': opportunity, 'rules': rules,
            'sam_state': new_request_sam_state(db.session, opportunity, pi,
                                               institution_name=institution_name)}


def _chosen(opportunities, opportunity_id):
    return next((o for o in opportunities if o['opportunity_id'] == opportunity_id), None)


def _mnemonic_label(mnemonic_code_id):
    from sam.core.organizations import MnemonicCode
    code = db.session.get(MnemonicCode, mnemonic_code_id) if mnemonic_code_id else None
    return code.code if code is not None else ''


def _user_label(user_id):
    from sam.core.users import User
    user = db.session.get(User, user_id) if user_id else None
    return f'{user.username} ({user.display_name})' if user is not None else ''


class NewRequestForm(_RequestForm):
    """A New through type -> describe -> request -> review -> submit; nothing reaches XRAS
    before submit. The type stage (``type_stage``, ``choose_type``) is the signed-in door's:
    the visitor's type is fixed by its token.

    Who files is a seam (``_arm``, ``_opportunities``, ``_chosen_id``, ``_pi``, ``_pi_username``,
    ``_institution_name``, ``_check_admin``, ``_expected_messages``, ``_file``, ``_post_url``,
    ``_type_url``, ``_pi_email``, ``roster_private``, ``names_sam_users``, ``request_urls``):
    the visitor's New
    (``webapp/register/allocation_request.py``) overrides those, and ``draft`` to stamp its
    registration on the ``SamRefs``.
    """

    schema_cls = XrasNewRequestForm
    template = _NEW_DESCRIBE
    #: Template URLs a door replaces; empty means the signed-in user's endpoints.
    request_urls: dict = {}
    #: True where the review must not say which roster addresses SAM or XRAS knows.
    roster_private = False
    #: False where the form must not search SAM's users (the project admin, the roster picker).
    names_sam_users = True

    def __init__(self):
        HtmxFormHandler.__init__(self, project=None, action_type='New')
        self._ctx = None

    def _arm(self, config):
        _refuse_unless_armed(config, 'New')

    def _opportunities(self, config):
        return _new_opportunities(config)

    def _chosen_id(self):
        return request.values.get('opportunity_id', type=int)

    def _pi(self):
        from sam.core.users import User
        return User.get_by_username(db.session, current_user.username)

    def _pi_username(self):
        return current_user.username

    def _institution_name(self):
        """Where the code letters come from when there is no SAM user; the ladder needs none."""
        return None

    def _pi_email(self):
        """The PI's address where no SAM user names them, so the roster can skip it."""
        return None

    def _post_url(self):
        return url_for('user_dashboard.my_request_new_post')

    def _type_url(self):
        """Where "Change type" returns to; ``None`` where the type is fixed."""
        return url_for('user_dashboard.my_request_new_fragment')

    def type_stage(self, errors=()):
        """The first stage: every published type as a card, the PI's audiences first (one read)."""
        from sam.xras.audiences import user_audiences
        config = _config()
        self._arm(config)
        try:
            opportunities, outage = self._opportunities(config), False
        except FormError:
            opportunities, outage = [], True
        mine = user_audiences(self._pi())
        cards = type_cards(opportunities, config, audiences=None)
        eligible = [c for c in cards if c.audience is None or c.audience in mine]
        others = [c for c in cards if not (c.audience is None or c.audience in mine)]
        return render_template(_NEW_TYPE, eligible=eligible, others=others, outage=outage,
                               post_url=self._post_url(), errors=list(errors))

    def choose_type(self):
        """``stage=type``: the describe page with the chosen open type fixed, or the stage again."""
        ctx = self.gate()
        if ctx['opportunity'] is None or not ctx['opportunity']['can_submit']:
            return self.type_stage(errors=['Choose one of the open types.'])
        return render_template(_NEW_DESCRIBE, errors=[], field_errors={}, form=None, **ctx)

    def gate(self):
        if self._ctx is not None:
            return self._ctx
        from webapp.register.eula import eula_sha
        config = _config()
        self._arm(config)
        opportunities = self._opportunities(config)
        chosen = _chosen(opportunities, self._chosen_id())
        fos = _fos_options()
        self._ctx = dict(project=None, action_type='New', decision=None,
                         eula_sha=eula_sha(), post_url=self._post_url(),
                         request_urls=self.request_urls, roster_private=self.roster_private,
                         names_sam_users=self.names_sam_users, type_url=self._type_url(),
                         opportunities=opportunities, fos_options=fos,
                         fos_choices=[(r['fosTypeId'], r.get('fosName')) for r in fos],
                         chosen_mnemonic=_mnemonic_label(request.values.get(
                             'mnemonic_code_id', type=int)),
                         chosen_admin=_user_label(request.values.get('admin_user_id', type=int)),
                         grant_agencies=funding_agency_choices(),
                         carry_fields=_DESCRIBE_FIELDS,
                         **_opportunity_context(config, chosen, self._pi(),
                                                self._institution_name()))
        self._ctx.update(self._amounts_context(config))
        return self._ctx

    def _amounts_context(self, config):
        """Page two: the opportunity's resources with their limits, and the default window."""
        opportunity, state = self._ctx['opportunity'], self._ctx['sam_state']
        if opportunity is None:
            return {'resources': [], 'resource_keys': None, 'documents': []}
        begin = first_of_next_month(datetime.now().date())
        end = add_months(begin, opportunity.get('award_months') or 12) - timedelta(days=1)
        type_name = state['allocation_type'].allocation_type if state['allocation_type'] else None
        resources = [dict(r) for r in opportunity['resources']]
        return {'default_begin': begin, 'default_end': end, 'sam_type_name': type_name,
                'documents': [d for d in self._ctx['rules'].documents if d.required],
                **self._with_limits(config, resources, type_name)}

    def context(self):
        try:
            return self.gate()
        except FormError:
            return dict(project=None, action_type='New', decision=None, opportunities=[],
                        names_sam_users=self.names_sam_users, type_url=self._type_url(),
                        fos_options=[], fos_choices=[],
                        opportunity=None, rules=None, sam_state=None, chosen_mnemonic='',
                        chosen_admin='',
                        carry_fields=_DESCRIBE_FIELDS, resources=[], documents=[],
                        eula_sha=None, post_url=self._post_url(),
                        request_urls=self.request_urls)

    def render_errors(self, errors, field_errors=None):
        stage = request.form.get('stage')
        page_one = stage in ('describe', 'request') or any(
            k in _DESCRIBE_FIELDS for k in field_errors or {})
        self.template = _NEW_DESCRIBE if page_one else _NEW_AMOUNTS
        return super().render_errors(errors, field_errors)

    def clean(self, data):
        from marshmallow import ValidationError
        ctx = self.gate()
        data['admin_username'] = None
        if data['stage'] == 'describe':
            return data
        errors = self._describe_errors(data, ctx)
        if errors:
            raise ValidationError(errors)
        if data['stage'] == 'request':
            return data
        self._check_limits(data, ctx, ctx.get('sam_type_name'))
        return self._assemble(data, ctx)

    def _describe_errors(self, data, ctx):
        """Page one's checks against XRAS and SAM, as ``{field: [message]}``."""
        from sam.core.organizations import MnemonicCode
        errors = {}
        opportunity = ctx['opportunity']
        if opportunity is None or opportunity['opportunity_id'] != data['opportunity_id']:
            errors['opportunity_id'] = ['Choose one of the open opportunities.']
            return errors
        if not opportunity['can_submit']:
            errors['opportunity_id'] = [opportunity.get('reason')
                                        or 'XRAS does not take a new request from you here.']
        if data['fos_type_id'] not in {r.get('fosTypeId') for r in ctx['fos_options']}:
            errors['fos_type_id'] = ['Choose the field of science.']
        self._check_admin(data, errors)
        if data['mnemonic_code_id']:
            code = db.session.get(MnemonicCode, data['mnemonic_code_id'])
            if code is None or not code.is_active:
                errors['mnemonic_code_id'] = ['Choose an active code.']
        elif ctx['sam_state']['mnemonic'] is None:
            errors['mnemonic_code_id'] = ['Choose the letters the project code starts with.']
        if ctx['rules'].wants_grants:
            errors.update(self._grant_errors(data, ctx['rules']))
        errors.update(self._roster_errors(data))
        return errors

    @staticmethod
    def _roster_errors(data):
        """Parse the roster's ``Name <email>`` lines onto ``data['roster_entries']``."""
        from sam.manage.account_requests import parse_roster
        entries, problems = parse_roster(data.get('roster') or '')
        if len(entries) > ROSTER_MAX:
            problems.append(f'Name at most {ROSTER_MAX} people here; add the rest once the '
                            'project starts.')
        data['roster_entries'] = entries
        return {'roster': problems} if problems else {}

    def _roster(self, data):
        """The draft's roster: ``username`` for an active SAM user XRAS knows, else ``None``.

        The PI and the admin are dropped; so is the visitor's own address.
        """
        from sam.queries.xras_accounts import active_holders
        entries = data.get('roster_entries') or ()
        holders = active_holders(db.session, [e['email'] for e in entries])
        skip = {u.casefold() for u in (self._pi_username(), data['admin_username']) if u}
        own = (self._pi_email() or '').strip().lower()
        roster = []
        for entry in entries:
            username = holders.get(entry['email'])
            if entry['email'] == own or (username and username.casefold() in skip):
                continue
            known = username and self._in_xras(username, unsure=False)
            roster.append(dict(entry, username=username if known else None))
        return tuple(roster)

    def _check_admin(self, data, errors):
        """The optional project admin: an active SAM user with an XRAS person, not the PI."""
        from sam.core.users import User
        if not data['admin_user_id']:
            return
        admin = db.session.get(User, data['admin_user_id'])
        if admin is None or not admin.is_active:
            errors['admin_user_id'] = ['Choose an active SAM user.']
        elif admin.username.casefold() == current_user.username.casefold():
            errors['admin_user_id'] = ['You are the PI; name someone else, or no one.']
        elif not self._in_xras(admin.username):
            errors['admin_user_id'] = [f'{admin.username} has no XRAS account yet.']
        else:
            data['admin_username'] = admin.username

    @staticmethod
    def _in_xras(username, unsure=True):
        """Whether XRAS knows *username*; *unsure* answers an outage (the admin's add_role backstops)."""
        from sam.integration.xras_api.base import XrasWriteRejected
        try:
            return admin_client(_config()).get_person(username) is not None
        except XrasWriteRejected:
            return False
        except XrasSourceUnavailable:
            return unsure

    def _verdict(self, data, ctx):
        """The preflight on the draft; a code the PI chose stands in for the ladder's miss."""
        from dataclasses import replace
        from sam.xras.submission_preflight import new_request_line, preflight_draft
        fos = next((r for r in ctx['fos_options'] if r.get('fosTypeId') == data['fos_type_id']),
                   None)
        opportunity = ctx['opportunity']
        line = new_request_line(data['draft'], pi_username=self._pi_username(),
                                opportunity=opportunity, fos=fos,
                                agency_names=dict(funding_agency_choices()))
        verdict = preflight_draft(
            db.session, line, data['draft'], resource_keys=ctx['resource_keys'],
            opportunities={opportunity['opportunity_id']: {
                'allocationType': opportunity['allocation_type']}})
        # NOTE: the review runs before the row exists, so the handler cannot consult it yet;
        # a code the PI chose stands in for the ladder's miss, as the handler will use it.
        skip = set(ctx['sam_state']['mnemonic_errors']) if data['mnemonic_code_id'] else set()
        skip |= set(self._expected_messages(verdict.messages))
        if verdict.status == 'failed' and skip:
            left = tuple(m for m in verdict.messages if m not in skip)
            verdict = replace(verdict, messages=left,
                              status='failed' if left else 'rechecked', would_succeed=not left)
        return verdict

    def _expected_messages(self, messages):
        """Verdict messages this door expects and the review should not count; none here."""
        return ()

    def draft(self, data):
        from sam.manage.xras_submission import ActionRequest, ResourceAmount, SamRefs
        ctx = self._ctx
        opportunity, state = ctx['opportunity'], ctx['sam_state']
        allocation_type = state['allocation_type']
        mnemonic_id = data['mnemonic_code_id'] or (
            state['mnemonic'].mnemonic_code_id if state['mnemonic'] else None)
        return ActionRequest(
            action_type='New', opportunity_id=opportunity['opportunity_id'],
            title=data['title'], abstract=data['abstract'], keywords=data['keywords'],
            fos_type_id=data['fos_type_id'], admin_username=data['admin_username'],
            roster=self._roster(data),
            resources=tuple(ResourceAmount(rid, amt) for rid, amt in data['amounts'].items()),
            dates=(data['begin_date'], data['end_date']), grants=self._grants(data),
            documents=tuple(data.get('documents') or ()),
            eua_attribute_id=opportunity.get('eua_attribute_id'),
            sam=SamRefs(
                allocation_type_id=allocation_type.allocation_type_id if allocation_type else None,
                facility_id=allocation_type.panel.facility_id
                if allocation_type and allocation_type.panel else None,
                mnemonic_code_id=mnemonic_id))

    def on_success(self, data):
        ctx = dict(self._ctx, form=request.form, data=data, errors=[], field_errors={})
        if data['stage'] == 'describe':
            return render_template(_NEW_DESCRIBE, **ctx)
        if data['stage'] == 'request':
            return render_template(_NEW_AMOUNTS, **ctx)
        if data['stage'] == 'review':
            return render_template(_REVIEW, **ctx)
        return render_template(_OUTCOME, outcome=data['outcome'], **ctx)


@bp.route('/requests/new')
@login_required
def my_request_new_fragment():
    """A New's type stage, into ``#xrasRequestModalBody``; a refusal renders as a message."""
    try:
        return NewRequestForm().type_stage()
    except FormError as exc:
        return message_fragment('New request unavailable', ' '.join(exc.errors))


@bp.route('/requests/new', methods=['POST'])
@login_required
def my_request_new_post():
    """``stage=type`` picks the type (no schema: the choice is the whole input); then the form."""
    handler = NewRequestForm()
    if request.form.get('stage') != 'type':
        return handler.handle()
    try:
        return handler.choose_type()
    except FormError as exc:
        return message_fragment('New request unavailable', ' '.join(exc.errors))


def mnemonic_code_search(q, active_only):
    """The code-letter picker's search; codes and their institutions are public knowledge."""
    from sam.queries.mnemonic_console import search_mnemonic_codes
    return search_mnemonic_codes(db.session, q, limit=10)


def _search_admins(q, active_only):
    from sam.queries.users import search_users_by_pattern
    if not _config().submit_allows('New'):
        return []
    return search_users_by_pattern(db.session, q, limit=15, active_only=True,
                                   exclude_user_ids=[current_user.user_id])


register_typeahead(
    bp, rule='/requests/htmx/admin-suggest', endpoint='my_requests_admin_suggest',
    permission=None,                    # any user who may file a New picks its admin
    search=_search_admins,
    template='dashboards/user/fragments/request_admin_results_htmx.html',
    ctx_key='users')

register_typeahead(
    bp, rule='/requests/htmx/roster-suggest', endpoint='my_requests_roster_suggest',
    permission=None,                    # the same search, as roster lines; never the visitor's
    search=_search_admins,
    template='dashboards/user/fragments/request_roster_results_htmx.html',
    ctx_key='users')


register_typeahead(
    bp, rule='/requests/htmx/mnemonic-suggest', endpoint='my_requests_mnemonic_suggest',
    permission=None,                    # any user may pick the letters for a New
    search=mnemonic_code_search,
    template='dashboards/allocations/partials/mnemonic_code_search_results_fk_htmx.html',
    ctx_key='codes')


@bp.route('/requests/htmx/award-lookup')
@login_required
def my_requests_award_lookup():
    """The grant fieldset, filled from NSF's record, or USAspending's for another agency."""
    return award_lookup_fragment()


def award_lookup_fragment(request_urls=None):
    """The award lookup's body, shared with the visitor's New; *request_urls* re-points the fieldset."""
    from sam.integration.awards.base import AwardSourceUnavailable
    from sam.integration.awards.client import AwardHttpClient
    from sam.integration.awards.registry import build_providers, resolve_award
    from sam.integration.xras_api.vocabulary import NSF_FUNDING_AGENCY_ID
    number = (request.args.get('grant_number') or '').strip()
    agency = request.args.get('grant_agency_id', type=int) or NSF_FUNDING_AGENCY_ID
    required = [w for w in (request.args.get('grant_required') or '').split(',') if w]
    nsf = agency == NSF_FUNDING_AGENCY_ID
    source = 'NSF' if nsf else 'USAspending'
    found, note = {}, None
    try:
        # Exact numbers only away from NSF: USAspending's keyword fallback can return another award.
        # USAspending answered the cluster in 1-16 s (2026-10-31): ten seconds in all, as the
        # help says.
        record = (resolve_award('NSF', number) if nsf else resolve_award(
            None, number, exact=True,
            providers=build_providers(AwardHttpClient(timeout=10, max_retries=1, budget=10)))
        ) if number else None
    except AwardSourceUnavailable:
        record, note = None, ('NSF is not answering. Type the grant\'s details, or try again.' if nsf
                              else 'USAspending did not answer within ten seconds. Type the '
                                   'grant\'s details, or try again.')
    if record is not None:
        found = {'grant_title': record.title, 'grant_begin_date': record.start_date,
                 'grant_end_date': record.end_date,
                 'grant_pi_name': record.pi.name if record.pi else None,
                 'grant_officer_name': record.monitor.name if record.monitor else None,
                 'grant_officer_email': record.monitor.email if record.monitor else None}
        note = (f'Filled from {source}. Check each field, and type any {source} left blank.' if nsf
                else 'Filled from USAspending: the title and dates. It has no PI or program '
                     'officer; type them.')
    elif number and note is None:
        note = (f'NSF has no award {number}. Check the number, or type the details.' if nsf
                else f'USAspending has no federal award numbered exactly {number}. Check the '
                     'number, or type the details.')
    # No record: keep what the user typed. A found record replaces it whole, so no stale PI
    # rides along.
    kept = found if record is not None else {k: request.args.get(k) for k in GRANT_INPUTS}
    values = {**{k: v for k, v in kept.items() if v},
              'grant_agency_id': agency, 'grant_number': number}
    return render_template(_GRANT, form=values, required=required, note=note, field_errors={},
                           grant_agencies=funding_agency_choices(),
                           request_urls=request_urls or {})


# a stopped row: resume or discard

def _owned_submission(submission_id):
    from sam.integration.xras_submission import XrasSubmission
    row = db.session.get(XrasSubmission, submission_id)
    if row is None:
        abort(404)
    if current_user.username not in (row.created_by, row.xa_user) \
            and not has_permission(current_user, Permission.MANAGE_XRAS):
        abort(403)
    return row


def _door(row):
    """The PI door for the row's own PI; an operator who opened it, or any manager, the admin door."""
    if current_user.username == row.xa_user:
        return 'pi'
    return 'admin'


def _refresh():
    """Reload the page: both cards change, and the picker's pending reason with them."""
    resp = make_response('', 204)
    resp.headers['HX-Refresh'] = 'true'
    return resp


_RESUME = 'dashboards/user/fragments/request_resume_htmx.html'


def _resume_documents(row):
    """``(documents, error, notes)`` from the uploads for the documents *row* still lacks."""
    import hashlib
    from sam.manage.xras_submission import SubmissionDocument
    from sam.queries.xras_my_requests import missing_documents
    documents, notes = [], []
    for wanted in missing_documents(db.session, row):
        label = wanted.get('title') or wanted['document_type']
        _, content, why = _read_upload(f"document_{wanted['document_type']}", label)
        if why:
            return [], why, []
        if wanted.get('sha256') and hashlib.sha256(content).hexdigest() != wanted['sha256']:
            notes.append(f'The {label} is a different file than the first time.')
        # The stored name keeps the payload match (ActionRequest.from_payload, by filename).
        documents.append(SubmissionDocument(wanted['document_type'], wanted['filename'],
                                            content, title=wanted.get('title')))
    return documents, None, notes


@bp.route('/requests/<int:submission_id>/resume')
@login_required
def my_request_resume_fragment(submission_id):
    """The documents a stopped row needs again, into ``#xrasRequestModalBody``."""
    from sam.queries.xras_my_requests import missing_documents
    row = _owned_submission(submission_id)
    return render_template(_RESUME, row=row, missing=missing_documents(db.session, row),
                           errors=[])


@bp.route('/requests/<int:submission_id>/resume', methods=['POST'])
@login_required
def my_request_resume(submission_id):
    from sam.manage import xras_submission as flow
    from sam.queries.xras_my_requests import missing_documents
    row = _owned_submission(submission_id)
    door = _door(row)
    documents, notes = (), []
    if missing_documents(db.session, row):
        documents, error, notes = _resume_documents(row)
        if error:
            return render_template(_RESUME, row=row,
                                   missing=missing_documents(db.session, row), errors=[error])
    try:
        flow.resume_submission(_session_factory, submission_id, actor=acting_username(),
                               door=door, documents=tuple(documents))
    except (flow.SubmissionRefused, LookupError, XrasSourceUnavailable) as exc:
        current_app.logger.warning('my requests resume %s: %s', submission_id, exc)
        flash(str(exc), 'warning')
    for note in notes:
        flash(note, 'info')
    return _refresh()


@bp.route('/requests/<int:submission_id>/abort', methods=['POST'])
@login_required
def my_request_abort(submission_id):
    from sam.manage import xras_submission as flow
    row = _owned_submission(submission_id)
    try:
        flow.abort_submission(_session_factory, submission_id, actor=acting_username(),
                              door=_door(row))
    except (flow.SubmissionRefused, LookupError, XrasSourceUnavailable) as exc:
        current_app.logger.warning('my requests abort %s: %s', submission_id, exc)
        flash(str(exc), 'warning')
    return _refresh()


@bp.route('/requests/agreement')
@login_required
def my_requests_agreement():
    """The agreement every request is filed under, on a page of its own (the forms link it)."""
    from webapp.register.eula import eula_html
    return render_template('register/terms.html', eula_html=eula_html())
