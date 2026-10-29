"""User dashboard -> My Requests: the user's XRAS requests, and what each project may file.

The page renders from SAM's tables and the sweep's index, never a live XRAS read; each
project's picker reads XRAS lazily when opened (the family, then the user's offers). A form
re-reads the family and re-runs the routing at every stage (edit, review, submit), so a
decision is never carried in the browser. Files go through ``sam.manage.xras_submission``.
Design: ``docs/plans/XRAS_SUBMISSION.md`` § 4.0, routing § 4.4.
"""

from datetime import datetime, timedelta

from flask import abort, current_app, flash, make_response, render_template, request, session
from flask_login import current_user, login_required

from sam.integration.xras_api import XrasSourceUnavailable
from sam.queries.xras_my_requests import led_projects, my_requests
from sam.schemas.forms import (
    XrasExtensionRequestForm,
    XrasRenewalRequestForm,
    XrasSupplementRequestForm,
)
from sam.xras.routing import action_offers, request_routing, route_for
from webapp.api.access_control import require_project_permission
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
    return render_template(
        'dashboards/user/my_requests.html',
        requests=my_requests(db.session, user.username, _index_rows()),
        projects=[(p, route_for(_type_name(p))) for p in projects],
        open_projcode=(request.args.get('projcode') or '').strip().upper() or None,
        submit_configured=_config().submit_configured,
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
        if rules.wants_grants:
            raise FormError('This renewal needs supporting grants, which My Requests cannot '
                            'take yet. Renew in ARC for now.')
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

    def _with_limits(self, config, resources):
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
        limits = limits_for_allocation_type(db.session, _type_name(self.project))
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
        username = current_user.username
        try:
            data['outcome'] = flow.request_action(
                _session_factory, data['draft'], actor=username, xa_user=username,
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


def _refresh():
    """Reload the page: both cards change, and the picker's pending reason with them."""
    resp = make_response('', 204)
    resp.headers['HX-Refresh'] = 'true'
    return resp


@bp.route('/requests/<int:submission_id>/resume', methods=['POST'])
@login_required
def my_request_resume(submission_id):
    from sam.manage import xras_submission as flow
    row = _owned_submission(submission_id)
    door = 'pi' if current_user.username == row.xa_user else 'admin'
    try:
        flow.resume_submission(_session_factory, submission_id,
                               actor=current_user.username, door=door)
    except (flow.SubmissionRefused, LookupError, XrasSourceUnavailable) as exc:
        current_app.logger.warning('my requests resume %s: %s', submission_id, exc)
        flash(str(exc), 'warning')
    return _refresh()


@bp.route('/requests/<int:submission_id>/abort', methods=['POST'])
@login_required
def my_request_abort(submission_id):
    from sam.manage import xras_submission as flow
    _owned_submission(submission_id)
    try:
        flow.abort_submission(_session_factory, submission_id, actor=current_user.username)
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
