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
    session,
    url_for,
)
from flask_login import current_user, login_required

from sam.integration.xras_api import XrasSourceUnavailable
from sam.queries.xras_my_requests import led_projects, my_requests
from sam.schemas.forms import (
    XrasExtensionRequestForm,
    XrasNewRequestForm,
    XrasRenewalRequestForm,
    XrasSupplementRequestForm,
)
from sam.schemas.forms.xras_submission import GRANT_INPUTS
from sam.xras.routing import action_offers, request_routing, route_for
from webapp.api.access_control import require_project_permission
from webapp.auth.impersonation import acting_username
from webapp.extensions import db
from webapp.utils.form_handler import FormError, HtmxFormHandler
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


def _config():
    from sam.integration.xras_api.config import XrasApiConfig
    return XrasApiConfig.from_environment().interactive()


def _session_factory():
    """Private sessions for the flow's rows: a request rollback must never erase an audit row."""
    from sqlalchemy.orm import Session
    return Session(db.engine)


def _index_rows():
    from sam.integration.xras_api.cache import load_requests_index
    return (load_requests_index() or {}).get('rows') or []


def _type_name(project):
    return project.allocation_type.allocation_type if project.allocation_type else None


def _reader(config):
    from sam.integration.xras_api import XrasApiClient
    return XrasApiClient.from_environment(config)


def _decide(project, config):
    """``(decision, outage)``: the routing for the current user, reading the family only when armed."""
    family = None
    if config.submit_configured and route_for(_type_name(project)) == 'xras':
        try:
            family = _reader(config).get_request_family_by_number(project.projcode)
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
    from sam.integration.xras_api.admin_client import XrasAdminClient

    config = _config()
    decision, outage = _decide(project, config)
    available = []
    if decision is not None and decision.allowed:
        try:
            available = XrasAdminClient.from_environment(config).available_action_requests(
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

def _message(title, message, variant='warning'):
    return render_template(_MESSAGE, title=title, message=message, variant=variant)


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
        if session.get('impersonator_id'):
            raise FormError('You are viewing SAM as another user. They file their own '
                            'requests, under their own agreement.')
        if not config.submit_allows(self.action_type):
            raise FormError(f'{self.action_type} requests through SAM are not enabled here.')
        decision, outage = _decide(self.project, config)
        if outage:
            raise FormError(outage)
        if not decision.allowed:
            raise FormError(decision.message)
        self._ctx = dict(project=self.project, action_type=self.action_type,
                         decision=decision, eula_sha=eula_sha(),
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
        from sam.integration.xras_api.admin_client import XrasAdminClient
        from sam.integration.xras_api.rule_book import rule_book
        from sam.xras.submission_preflight import line_resources
        try:
            offers = XrasAdminClient.from_environment(config).available_action_requests(
                current_user.username)
        except XrasSourceUnavailable:
            raise FormError('XRAS is not answering, so the renewal cannot be offered now.')
        offer = next((o for o in offers if o['action_type'] == 'Renewal'
                      and o['request_number'] == decision.request_number), None)
        if offer is None:
            raise FormError('XRAS offers no renewal for this project right now.')
        rules = rule_book(_reader(config), offer['allocation_type_id'], 'Renewal',
                          allocation_type_name=offer['allocation_type'])
        begin = floor + timedelta(days=1) if floor else datetime.now().date()
        months = offer.get('award_months') or 12
        end = _add_months(begin, months) - timedelta(days=1)
        held = {r['resource_id'] for r in line_resources(decision.line)}
        resources = [dict(r, prefill=r['resource_id'] in held) for r in offer['resources']]
        return {'floor': floor, 'offer': offer, 'rules': rules,
                'documents': [d for d in rules.documents if d.required],
                'default_begin': begin, 'default_end': end,
                'line_abstract': (decision.line or {}).get('abstract') or '',
                **self._with_limits(config, resources)}

    def _with_limits(self, config, resources, type_name=None):
        """*resources* with their SAM resource and type limit, through the catalog key."""
        from sam.accounting.allocation_type_limits import limits_for_allocation_type
        from sam.integration.xras_api.people import resource_key_map
        from sam.xras.submission_preflight import sam_resources_by_xras_id
        try:
            catalog = _reader(config).get_resources()
        except XrasSourceUnavailable:
            catalog = None
        keys = resource_key_map(catalog) if catalog else None
        sam = sam_resources_by_xras_id(db.session, [r['resource_id'] for r in resources], keys)
        name = type_name or (_type_name(self.project) if self.project else None)
        limits = limits_for_allocation_type(db.session, name) if name else {}
        for r in resources:
            resource = sam.get(r['resource_id'])
            r['sam_name'] = resource.resource_name if resource is not None else None
            r['limit'] = limits.get(r['sam_name']) if r['sam_name'] else None
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
        from sam.xras.submission_preflight import limit_refusals, preflight_draft
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
            by_id = {r['resource_id']: r for r in ctx['resources']}
            unknown = [rid for rid in data['amounts'] if rid not in by_id]
            if unknown:
                raise FormError('XRAS does not offer one of those resources here; '
                                'reopen the form.')
            named = {by_id[rid]['sam_name']: amt for rid, amt in data['amounts'].items()
                     if by_id[rid]['sam_name']}
            refused = limit_refusals(db.session, _type_name(self.project), named)
            errors = {f'amount_{rid}': [refused[by_id[rid]['sam_name']]]
                      for rid in data['amounts'] if by_id[rid]['sam_name'] in refused}
            if errors:
                raise ValidationError(errors)
        if data['stage'] == 'submit' and ctx.get('documents'):
            data['documents'], data['document_error'] = self._attached(ctx['documents'])
        data['draft'] = self.draft(data)
        offer = ctx.get('offer')
        data['verdict'] = preflight_draft(
            db.session, ctx['decision'].line, data['draft'], resource_keys=ctx['resource_keys'],
            opportunities={offer['opportunity_id']: {'allocationType': offer['allocation_type']}}
            if offer else None)
        if data['stage'] == 'submit':
            back = self._back_to_review(data)
            if back:
                data.update(stage='review', review_error=back)
        return data

    @staticmethod
    def _grant_errors(data, rules):
        """``{field: [message]}`` for the grant fields the rule book requires and the form lacks."""
        if not data['grant_number']:
            return {'grant_number': ['This request needs a supporting grant.']}
        wire_to_input = {w: n for n, w in GRANT_INPUTS.items()}
        return {wire_to_input[w]: ['Required for the grant.'] for w in rules.grant_fields
                if w in wire_to_input and data.get(wire_to_input[w]) is None}

    @staticmethod
    def _grants(data):
        from sam.integration.xras_api.vocabulary import NSF_FUNDING_AGENCY_ID
        return ((dict(data['grant'], fundingAgencyId=NSF_FUNDING_AGENCY_ID),)
                if data.get('grant') else ())

    @staticmethod
    def _attached(rules):
        """``(documents, why not)`` for the uploads the rule book requires."""
        from sam.manage.xras_submission import SubmissionDocument
        from sam.xras.submission_preflight import document_refusal
        documents = []
        for rule in rules:
            upload = request.files.get(f'document_{rule.code}')
            if upload is None or not upload.filename:
                return [], f'Attach the {rule.label}.'
            content = upload.read()
            why = document_refusal(upload.filename, content)
            if why:
                return [], why
            documents.append(SubmissionDocument(rule.code, upload.filename, content,
                                                title=rule.label))
        return documents, None

    def _back_to_review(self, data):
        """Why a submit returns to the review instead of filing, or ``None``."""
        if data['verdict'].status == 'failed':
            return 'SAM could not apply this once approved. Go back and change it.'
        if data.get('document_error'):
            return data['document_error']
        if data['eula_sha'] != self._ctx['eula_sha']:
            return 'The terms changed since this page opened. Read them and accept again.'
        if not data['agree']:
            return 'Accept the terms of use to file this request.'
        return None

    def perform(self, data):
        if data['stage'] != 'submit':
            return data
        from sam.manage import xras_submission as flow
        try:
            data['outcome'] = flow.request_action(
                _session_factory, data['draft'], actor=acting_username(),
                xa_user=current_user.username,
                eula=(data['eula_sha'], datetime.now()), door='pi')
        except flow.SubmissionRefused as exc:
            raise FormError(str(exc))
        return data

    def on_success(self, data):
        ctx = dict(self._ctx, form=request.form, data=data)
        if data['stage'] == 'edit':
            return render_template(_FORM, errors=[], field_errors={}, **ctx)
        if data['stage'] == 'review':
            return render_template(_REVIEW, **ctx)
        return render_template(_OUTCOME, outcome=data['outcome'], **ctx)


def _add_months(day, months):
    """*day* moved *months* ahead, clamped to the target month's last day."""
    import calendar
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return day.replace(year=year, month=month,
                       day=min(day.day, calendar.monthrange(year, month)[1]))


def _form_type(kind):
    action_type = _FORM_TYPES.get(kind)
    if action_type is None:
        abort(404)
    return action_type


@bp.route('/requests/<projcode>/<any(extension, supplement, renewal):kind>')
@login_required
@require_project_permission(Permission.EDIT_PROJECTS)
def my_request_form(project, kind):
    """The request form, into ``#xrasRequestModalBody``; a refusal renders as a message."""
    handler = _RequestForm(project=project, action_type=_form_type(kind))
    try:
        ctx = handler.gate()
    except FormError as exc:
        return _message(f'{handler.action_type} unavailable', ' '.join(exc.errors))
    return render_template(_FORM, errors=[], field_errors={}, form=None, **ctx)


@bp.route('/requests/<projcode>/<any(extension, supplement, renewal):kind>', methods=['POST'])
@login_required
@require_project_permission(Permission.EDIT_PROJECTS)
def my_request_post(project, kind):
    return _RequestForm(project=project, action_type=_form_type(kind)).handle()


# the New request: describe -> request -> review -> submit

_NEW_DESCRIBE = 'dashboards/user/fragments/request_new_htmx.html'
_NEW_OPPORTUNITY = 'dashboards/user/fragments/request_new_opportunity_htmx.html'
_NEW_AMOUNTS = 'dashboards/user/fragments/request_new_amounts_htmx.html'
_GRANT = 'dashboards/user/fragments/request_grant_htmx.html'
#: The page-one inputs a later page carries in hidden fields, and whose errors land on page one.
_DESCRIBE_FIELDS = ('opportunity_id', 'title', 'abstract', 'keywords', 'fos_type_id',
                    'admin_username', 'mnemonic_code_id') + tuple(GRANT_INPUTS)


def _new_opportunities(config):
    """The open opportunities as the current user sees them; ``FormError`` on an outage."""
    from sam.integration.xras_api.admin_client import XrasAdminClient
    try:
        return XrasAdminClient.from_environment(config).new_request_opportunities(
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


def _opportunity_context(config, opportunity):
    """What page one reveals for *opportunity*: the rule book, the PI's mnemonic, the grant."""
    from sam.core.users import User
    from sam.integration.xras_api.rule_book import rule_book
    from sam.xras.submission_preflight import new_request_sam_state
    if opportunity is None:
        return {'opportunity': None, 'rules': None, 'sam_state': None}
    rules = rule_book(_reader(config), opportunity['allocation_type_id'], 'New',
                      allocation_type_name=opportunity['allocation_type'])
    pi = User.get_by_username(db.session, current_user.username)
    return {'opportunity': opportunity, 'rules': rules,
            'sam_state': new_request_sam_state(db.session, opportunity, pi)}


def _chosen(opportunities, opportunity_id):
    return next((o for o in opportunities if o['opportunity_id'] == opportunity_id), None)


def _mnemonic_label(mnemonic_code_id):
    from sam.core.organizations import MnemonicCode
    code = db.session.get(MnemonicCode, mnemonic_code_id) if mnemonic_code_id else None
    return code.code if code is not None else ''


def _first_of_next_month(day):
    return (day.replace(day=1) + timedelta(days=32)).replace(day=1)


class _NewRequestForm(_RequestForm):
    """A New through describe -> request -> review -> submit; nothing reaches XRAS before submit."""

    schema_cls = XrasNewRequestForm
    template = _NEW_DESCRIBE

    def __init__(self):
        HtmxFormHandler.__init__(self, project=None, action_type='New')
        self._ctx = None

    def form_input(self):
        raw = request.form.to_dict()
        raw['amounts'] = {k[len('amount_'):]: v for k, v in request.form.items()
                          if k.startswith('amount_')}
        return raw

    def gate(self):
        if self._ctx is not None:
            return self._ctx
        from webapp.register.eula import eula_sha
        config = _config()
        if session.get('impersonator_id'):
            raise FormError('You are viewing SAM as another user. They file their own '
                            'requests, under their own agreement.')
        if not config.submit_allows('New'):
            raise FormError('New requests through SAM are not enabled here.')
        opportunities = _new_opportunities(config)
        chosen = _chosen(opportunities, request.values.get('opportunity_id', type=int))
        fos = _fos_options()
        self._ctx = dict(project=None, action_type='New', decision=None,
                         eula_sha=eula_sha(), post_url=url_for('user_dashboard.my_request_new_post'),
                         opportunities=opportunities, fos_options=fos,
                         opportunity_options=[(o['opportunity_id'], o['opportunity_name']
                                               if o['can_submit'] else
                                               f"{o['opportunity_name']} (not open to you)")
                                              for o in opportunities],
                         fos_choices=[(r['fosTypeId'], r.get('fosName')) for r in fos],
                         chosen_mnemonic=_mnemonic_label(request.values.get(
                             'mnemonic_code_id', type=int)),
                         carry_fields=_DESCRIBE_FIELDS,
                         **_opportunity_context(config, chosen))
        self._ctx.update(self._amounts_context(config))
        return self._ctx

    def _amounts_context(self, config):
        """Page two: the opportunity's resources with their limits, and the default window."""
        opportunity, state = self._ctx['opportunity'], self._ctx['sam_state']
        if opportunity is None:
            return {'resources': [], 'resource_keys': None, 'documents': []}
        begin = _first_of_next_month(datetime.now().date())
        end = _add_months(begin, opportunity.get('award_months') or 12) - timedelta(days=1)
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
                        opportunity_options=[], fos_options=[], fos_choices=[],
                        opportunity=None, rules=None, sam_state=None, chosen_mnemonic='',
                        carry_fields=_DESCRIBE_FIELDS, resources=[], documents=[],
                        eula_sha=None, post_url=url_for('user_dashboard.my_request_new_post'))

    def render_errors(self, errors, field_errors=None):
        stage = request.form.get('stage')
        page_one = stage in ('describe', 'request') or any(
            k in _DESCRIBE_FIELDS for k in field_errors or {})
        self.template = _NEW_DESCRIBE if page_one else _NEW_AMOUNTS
        return super().render_errors(errors, field_errors)

    def clean(self, data):
        from marshmallow import ValidationError
        ctx = self.gate()
        if data['stage'] == 'describe':
            return data
        errors = self._describe_errors(data, ctx)
        if errors:
            raise ValidationError(errors)
        if data['stage'] == 'request':
            return data
        by_id = {r['resource_id']: r for r in ctx['resources']}
        if any(rid not in by_id for rid in data['amounts']):
            raise FormError('XRAS does not offer one of those resources here; start again.')
        self._check_limits(data, by_id, ctx.get('sam_type_name'))
        if data['stage'] == 'submit' and ctx['documents']:
            data['documents'], data['document_error'] = self._attached(ctx['documents'])
        data['draft'] = self.draft(data)
        data['verdict'] = self._verdict(data, ctx)
        if data['stage'] == 'submit':
            back = self._back_to_review(data)
            if back:
                data.update(stage='review', review_error=back)
        return data

    def _describe_errors(self, data, ctx):
        """Page one's checks against XRAS and SAM, as ``{field: [message]}``."""
        from sam.core.organizations import MnemonicCode
        from sam.core.users import User
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
        admin = data['admin_username']
        if admin:
            found = User.get_by_username(db.session, admin)
            if found is None or not found.is_active:
                errors['admin_username'] = [f'SAM has no active user {admin}.']
            elif admin.casefold() == current_user.username.casefold():
                errors['admin_username'] = ['You are the PI; name someone else, or no one.']
            elif not self._in_xras(admin):
                errors['admin_username'] = [f'{admin} has no XRAS account yet.']
        if data['mnemonic_code_id']:
            code = db.session.get(MnemonicCode, data['mnemonic_code_id'])
            if code is None or not code.is_active:
                errors['mnemonic_code_id'] = ['Choose an active code.']
        elif ctx['sam_state']['mnemonic'] is None:
            errors['mnemonic_code_id'] = ['Choose the letters the project code starts with.']
        if ctx['rules'].wants_grants:
            errors.update(self._grant_errors(data, ctx['rules']))
        return errors

    @staticmethod
    def _in_xras(username):
        from sam.integration.xras_api.admin_client import XrasAdminClient
        from sam.integration.xras_api.base import XrasWriteRejected
        try:
            return XrasAdminClient.from_environment(_config()).get_person(username) is not None
        except XrasWriteRejected:
            return False
        except XrasSourceUnavailable:
            return True        # the add_role step is the backstop

    def _check_limits(self, data, by_id, type_name):
        from marshmallow import ValidationError
        from sam.xras.submission_preflight import limit_refusals
        named = {by_id[rid]['sam_name']: amt for rid, amt in data['amounts'].items()
                 if by_id[rid]['sam_name']}
        refused = limit_refusals(db.session, type_name, named)
        errors = {f'amount_{rid}': [refused[by_id[rid]['sam_name']]]
                  for rid in data['amounts'] if by_id[rid]['sam_name'] in refused}
        if errors:
            raise ValidationError(errors)

    def _verdict(self, data, ctx):
        """The preflight on the draft; a code the PI chose stands in for the ladder's miss."""
        from dataclasses import replace
        from sam.xras.submission_preflight import new_request_line, preflight_draft
        fos = next((r for r in ctx['fos_options'] if r.get('fosTypeId') == data['fos_type_id']),
                   None)
        opportunity = ctx['opportunity']
        line = new_request_line(data['draft'], pi_username=current_user.username,
                                opportunity=opportunity, fos=fos)
        verdict = preflight_draft(
            db.session, line, data['draft'], resource_keys=ctx['resource_keys'],
            opportunities={opportunity['opportunity_id']: {
                'allocationType': opportunity['allocation_type']}})
        # NOTE: the review runs before the row exists, so the handler cannot consult it yet;
        # a code the PI chose stands in for the ladder's miss, as the handler will use it.
        skip = set(ctx['sam_state']['mnemonic_errors']) if data['mnemonic_code_id'] else set()
        if verdict.status == 'failed' and skip:
            left = tuple(m for m in verdict.messages if m not in skip)
            verdict = replace(verdict, messages=left,
                              status='failed' if left else 'rechecked', would_succeed=not left)
        return verdict

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
    """Page one of a New, into ``#xrasRequestModalBody``; a refusal renders as a message."""
    handler = _NewRequestForm()
    try:
        ctx = handler.gate()
    except FormError as exc:
        return _message('New request unavailable', ' '.join(exc.errors))
    return render_template(_NEW_DESCRIBE, errors=[], field_errors={}, form=None, **ctx)


@bp.route('/requests/new', methods=['POST'])
@login_required
def my_request_new_post():
    return _NewRequestForm().handle()


@bp.route('/requests/new/opportunity')
@login_required
def my_request_new_opportunity_fragment():
    """Page one's reveal for the chosen opportunity: its rule book, the code letters, the grant."""
    try:
        ctx = _NewRequestForm().gate()
    except FormError as exc:
        return render_template(_NEW_OPPORTUNITY, outage=' '.join(exc.errors), form=None,
                               field_errors={}, opportunity=None, oob=True)
    return render_template(_NEW_OPPORTUNITY, form=None, field_errors={}, oob=True, **ctx)


@bp.route('/requests/htmx/mnemonic-suggest')
@login_required
def my_requests_mnemonic_suggest():
    from sam.queries.mnemonic_console import search_mnemonic_codes
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return ''
    return render_template(
        'dashboards/allocations/partials/mnemonic_code_search_results_fk_htmx.html',
        q=q, codes=search_mnemonic_codes(db.session, q, limit=10))


@bp.route('/requests/htmx/award-lookup')
@login_required
def my_requests_award_lookup():
    """The grant fieldset, filled from NSF's record of the number typed."""
    from sam.integration.awards.base import AwardSourceUnavailable
    from sam.integration.awards.registry import resolve_award
    number = (request.args.get('grant_number') or '').strip()
    required = [w for w in (request.args.get('grant_required') or '').split(',') if w]
    found, note = {}, None
    try:
        record = resolve_award('NSF', number) if number else None
    except AwardSourceUnavailable:
        record, note = None, 'NSF is not answering. Type the grant\'s details, or try again.'
    if record is not None:
        found = {'grant_title': record.title, 'grant_begin_date': record.start_date,
                 'grant_end_date': record.end_date,
                 'grant_pi_name': record.pi.name if record.pi else None,
                 'grant_officer_name': record.monitor.name if record.monitor else None,
                 'grant_officer_email': record.monitor.email if record.monitor else None}
        note = 'Filled from NSF. Check each field, and type any NSF left blank.'
    elif number and note is None:
        note = f'NSF has no award {number}. Check the number, or type the details.'
    values = {'grant_number': number, **{k: v for k, v in found.items() if v}}
    return render_template(_GRANT, form=values, required=required, note=note, field_errors={})


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
    from sam.xras.submission_preflight import document_refusal
    documents, notes = [], []
    for wanted in missing_documents(db.session, row):
        label = wanted.get('title') or wanted['document_type']
        upload = request.files.get(f"document_{wanted['document_type']}")
        if upload is None or not upload.filename:
            return [], f'Attach the {label}.', []
        content = upload.read()
        why = document_refusal(upload.filename, content)
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
                           error=None)


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
                                   missing=missing_documents(db.session, row), error=error)
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
