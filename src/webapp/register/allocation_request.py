"""``/request`` -- a New allocation request from someone with no SAM account.

Step 1 is the overview: every allocation type a visitor may file, with its limits, documents
and review cadence, before anything starts. Then the terms, the person (``RegisterForm`` with
/register's protections: CSRF, the per-IP, per-address and global POST tiers, the honeypot,
the human check) and the email confirmation, which files no NUSD ticket: the account waits
for an approved request. A confirmed row's signed draft token admits the request form.
Mounted with ACCOUNT_REGISTRATION_ENABLED; every route 404s unless the visitor lever is on.
Design: docs/plans/XRAS_SUBMISSION_SPRINT2.md.
"""

import logging
from datetime import datetime, timedelta

from flask import (Blueprint, abort, current_app, redirect, render_template, request,
                   session, url_for)
from flask_limiter.util import get_remote_address
from flask_login import current_user
from marshmallow import ValidationError

from sam.core.account_requests import PURPOSE_SUBMISSION, AccountRequest
from sam.integration.xras_api import XrasSourceUnavailable
from sam.manage import management_transaction
from sam.manage.account_requests import register_request
from sam.queries.account_notices import build_verify_message
from sam.schemas.forms import RegisterForm, RegisterGateForm, VerifyCodeForm
from webapp.extensions import db
from webapp.limiter import limiter as _rate_limit
from webapp.utils import human_check
from webapp.utils.htmx import institution_options
from webapp.utils.notify import get_notifier
from webapp.dashboards.user.requests_routes import _NewRequestForm, award_lookup_fragment
from webapp.utils.form_handler import FormError
from webapp.utils.xras import (admin_client, interactive_config, read_client, resource_keys,
                               session_factory)

from . import eula, tokens
from .blueprint import _GATE_KEY, _email_key, _email_tier, _gate_accepted_at, _gate_blocks, _global_key, _global_tier
from .common import anon_tier as _anon_tier, ip_key as _ip_key, person_form_context, post_tier as _post_tier

logger = logging.getLogger(__name__)
bp = Blueprint('allocation_request', __name__, url_prefix='/request')

_PERSON = ('email', 'first_name', 'last_name', 'middle_name', 'organization',
           'academic_status', 'residence_country', 'orcid', 'phone')


@bp.before_request
def _gates():
    """The visitor lever (404 when off), then the registration preview's login gate."""
    if not interactive_config().visitor_submit_configured:
        abort(404)
    if (current_app.config.get('ACCOUNT_REGISTRATION_LOGIN_REQUIRED', False)
            and not current_user.is_authenticated):
        return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))
    return None


def _verify_ttl() -> int:
    return int(current_app.config.get('ACCOUNT_VERIFY_TTL_HOURS', 48)) * 3600


def _draft_ttl() -> int:
    return int(current_app.config.get('REQUEST_DRAFT_TTL_DAYS', 30)) * 86400


def _opportunities(config):
    """``(views, outage)``: the open opportunities in the report context."""
    try:
        return admin_client(config).new_request_opportunities(None), False
    except XrasSourceUnavailable as exc:
        logger.warning('request overview: XRAS not answering: %s', exc)
        return [], True


def _cards(config):
    from sam.integration.xras_api.rule_book import rule_book
    from sam.xras.allocation_overview import allocation_type_overview
    opportunities, outage = _opportunities(config)
    reader = read_client(config) if opportunities else None
    cards = allocation_type_overview(
        db.session, opportunities,
        rules_for=lambda type_id, name: rule_book(reader, type_id, 'New',
                                                  allocation_type_name=name),
        resource_keys=resource_keys(config) if opportunities else None)
    return cards, outage


def _open(opportunity_id):
    """``(opportunity, outage)``: the open opportunity a visitor may file under *opportunity_id*."""
    from sam.accounting.allocation_type_guide import public_guides
    opportunities, outage = _opportunities(interactive_config())
    public = {g.xras_allocation_type_id for g in public_guides(db.session)}
    return next((o for o in opportunities
                 if o.get('opportunity_id') == opportunity_id and o.get('can_submit')
                 and o.get('allocation_type_id') in public), None), outage


def open_opportunity(opportunity_id):
    """The open opportunity a visitor may file under *opportunity_id*, or None."""
    return _open(opportunity_id)[0]


def _step(n, label):
    return {'num': n, 'of': 5, 'label': label}


@bp.route('/', strict_slashes=False)
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def overview():
    """Step 1: every allocation type a visitor may file, laid out before anything starts."""
    cards, outage = _cards(interactive_config())
    return render_template('register/request_overview.html', cards=cards, outage=outage,
                           step=_step(1, 'Choose an allocation'))


@bp.route('/terms')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def terms():
    return render_template('register/terms.html', eula_html=eula.eula_html())


@bp.route('/institutions')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def institutions_fragment():
    return institution_options()


def _render_gate(opportunity, *, form=None, errors=(), field_errors=None):
    return render_template('register/request_gate.html', opportunity=opportunity,
                           form=form or {}, errors=list(errors),
                           field_errors=field_errors or {},
                           hidden={'opportunity_id': opportunity['opportunity_id']},
                           accept_url=url_for('allocation_request.accept'),
                           terms_url=url_for('allocation_request.terms'),
                           eula_html=eula.eula_html(), step=_step(2, 'Terms of use'))


def _render_person(opportunity, *, form=None, errors=(), field_errors=None):
    return render_template('register/request_person.html', opportunity=opportunity,
                           form=form or {}, errors=list(errors),
                           field_errors=field_errors or {}, hide_purpose=True,
                           event=None, locked_code=None, event_options=[],
                           human_check=human_check.provider(),
                           human_check_site_key=current_app.config.get('HUMAN_CHECK_SITE_KEY', ''),
                           form_action=url_for('allocation_request.submit',
                                               opportunity_id=opportunity['opportunity_id']),
                           institutions_url=url_for('allocation_request.institutions_fragment'),
                           submit_label='Confirm my email address',
                           step=_step(3, 'About you'), **person_form_context())


def _chosen_or_refuse(opportunity_id):
    opportunity = open_opportunity(opportunity_id)
    if opportunity is None:
        return None, render_template('register/request_closed.html',
                                     overview_url=url_for('allocation_request.overview'))
    return opportunity, None


@bp.route('/start/<int:opportunity_id>')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def start(opportunity_id):
    opportunity, refusal = _chosen_or_refuse(opportunity_id)
    if refusal:
        return refusal
    return _render_gate(opportunity) if _gate_blocks() else _render_person(opportunity)


@bp.route('/accept', methods=['POST'], strict_slashes=False)
@_rate_limit.limiter.limit(_post_tier, key_func=_ip_key, methods=['POST'])
def accept():
    try:
        opportunity_id = int(request.form.get('opportunity_id') or 0)
    except ValueError:
        opportunity_id = 0
    opportunity, refusal = _chosen_or_refuse(opportunity_id)
    if refusal:
        return refusal
    try:
        RegisterGateForm().load(request.form)
    except ValidationError as exc:
        field_errors, form_level = RegisterGateForm.split_errors(exc.messages)
        return _render_gate(opportunity, form=request.form, errors=form_level,
                            field_errors=field_errors)
    session[_GATE_KEY] = datetime.now().isoformat()
    return redirect(url_for('allocation_request.start', opportunity_id=opportunity_id))


@bp.route('/start/<int:opportunity_id>', methods=['POST'])
@_rate_limit.limiter.limit(_post_tier, key_func=_ip_key, methods=['POST'])
@_rate_limit.limiter.limit(_email_tier, key_func=_email_key, methods=['POST'])
@_rate_limit.limiter.limit(_global_tier, key_func=_global_key, methods=['POST'])
def submit(opportunity_id):
    raw = request.form
    if (raw.get('website') or '').strip():
        logger.warning('request honeypot tripped from %s', get_remote_address())
        return redirect(url_for('allocation_request.pending',
                                token=tokens.request_token(tokens.SALT_REQUEST_PAGE, 0, 0)))
    opportunity, refusal = _chosen_or_refuse(opportunity_id)
    if refusal:
        return refusal
    if _gate_blocks():
        return _render_gate(opportunity)
    try:
        data = RegisterForm().load(raw)
    except ValidationError as exc:
        field_errors, form_level = RegisterForm.split_errors(exc.messages)
        return _render_person(opportunity, form=raw, errors=form_level,
                              field_errors=field_errors)
    human_error = human_check.verify(raw)
    if human_error:
        return _render_person(opportunity, form=raw, errors=[human_error])

    now = datetime.now()
    ttl = int(current_app.config.get('ACCOUNT_VERIFY_TTL_HOURS', 48))
    code = tokens.new_code()
    accepted_at = _gate_accepted_at()
    with management_transaction(db.session):
        row = register_request(db.session, purpose=PURPOSE_SUBMISSION, clock=now,
                               source_ip=get_remote_address(),
                               purpose_note=f"New request: {opportunity['opportunity_name']}",
                               eula_sha=eula.eula_sha() if accepted_at else None,
                               eula_accepted_at=accepted_at,
                               **{k: data.get(k) for k in _PERSON})
        row.set_verification(tokens.code_hash(row.account_request_id, code),
                             now + timedelta(hours=ttl))
    link = tokens.request_token(tokens.SALT_REQUEST_LINK, row.account_request_id, opportunity_id)
    message = build_verify_message(
        row, verify_url=url_for('allocation_request.verify', token=link, _external=True),
        code=code, expires_hours=ttl, eula_text=eula.eula_text(), eula_html=eula.eula_html())
    result = get_notifier().send(message)
    logger.info('request registration %s for %s: verification mail %s',
                row.account_request_id, row.email, result.status)
    session.pop(_GATE_KEY, None)
    page = tokens.request_token(tokens.SALT_REQUEST_PAGE, row.account_request_id, opportunity_id)
    return redirect(url_for('allocation_request.pending', token=page,
                            sent=int(result.status in ('sent', 'redirected'))))


def _row(token, salt, max_age):
    read = tokens.read_request_token(token, salt, max_age=max_age)
    if read is None:
        return None, None
    row_id, opportunity_id, _ = read
    row = db.session.get(AccountRequest, row_id) if row_id else None
    if row is None or row.purpose != PURPOSE_SUBMISSION:
        return None, None
    return row, opportunity_id


def _expired():
    return render_template('register/expired.html', again_label='start again',
                           again_url=url_for('allocation_request.overview')), 200


def _to_draft(row, opportunity_id):
    """The verified row's way into the request form.

    The row names its XRAS placeholder here, before any write, whoever verified it (the
    visitor, or an operator's Verify on a deployment with mail off), so a retry or a resume
    can never mint a second one.
    """
    from sam.queries.xras_accounts import sam_placeholder_username
    if not row.xras_username:
        with management_transaction(db.session):
            row.attach_placeholder(sam_placeholder_username(row.account_request_id))
    token = tokens.request_token(tokens.SALT_REQUEST_DRAFT, row.account_request_id,
                                 opportunity_id, verified_at=row.verified_at)
    return redirect(url_for('allocation_request.draft', token=token))


def _confirm(row, opportunity_id):
    """Verified by the visitor: no NUSD ticket, which waits for an approved request."""
    if not row.is_verified:
        # Whole seconds: the draft token binds the stamp, and DATETIME would round.
        with management_transaction(db.session):
            row.mark_verified('self', clock=datetime.now().replace(microsecond=0))
    return _to_draft(row, opportunity_id)


@bp.route('/pending/<token>')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def pending(token):
    row, opportunity_id = _row(token, tokens.SALT_REQUEST_PAGE, _verify_ttl())
    if row is None:
        return _expired()
    if row.is_verified:
        return _to_draft(row, opportunity_id)
    return render_template('register/request_pending.html', row=row, token=token,
                           mail_sent=request.args.get('sent', '1') == '1', errors=[], form={},
                           code_action=url_for('allocation_request.pending_code', token=token),
                           again_url=url_for('allocation_request.overview'),
                           again_label='start again', step=_step(4, 'Confirm your email'))


@bp.route('/pending/<token>', methods=['POST'])
@_rate_limit.limiter.limit('10 per hour', methods=['POST'],
                           key_func=lambda: f'request-verify:{request.view_args.get("token", "")[:64]}')
def pending_code(token):
    row, opportunity_id = _row(token, tokens.SALT_REQUEST_PAGE, _verify_ttl())
    if row is None:
        return _expired()
    context = dict(row=row, token=token, mail_sent=True, form=request.form,
                   code_action=url_for('allocation_request.pending_code', token=token),
                   again_url=url_for('allocation_request.overview'), again_label='start again',
                   step=_step(4, 'Confirm your email'))
    try:
        data = VerifyCodeForm().load(request.form)
    except ValidationError:
        return render_template('register/request_pending.html',
                               errors=['Enter the six-digit code from the mail.'], **context)
    if not tokens.code_matches(row, data['code']):
        return render_template('register/request_pending.html',
                               errors=['That code is wrong or has expired.'], **context)
    return _confirm(row, opportunity_id)


@bp.route('/verify/<token>')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def verify(token):
    row, opportunity_id = _row(token, tokens.SALT_REQUEST_LINK, _verify_ttl())
    if row is None:
        return _expired()
    return _confirm(row, opportunity_id)


def draft_row(token):
    """``(row, opportunity_id)`` for a draft token still bound to the row's verification, else Nones."""
    read = tokens.read_request_token(token, tokens.SALT_REQUEST_DRAFT, max_age=_draft_ttl())
    if read is None:
        return None, None
    row_id, opportunity_id, stamp = read
    row = db.session.get(AccountRequest, row_id)
    if (row is None or row.purpose != PURPOSE_SUBMISSION or not row.is_verified
            or stamp != tokens.invite_stamp(row.verified_at)):
        return None, None
    return row, opportunity_id


class _VisitorNewRequest(_NewRequestForm):
    """A confirmed visitor's New: the opportunity fixed by the token, no SAM user and no admin.

    The PI is the placeholder the row names; the flow mints it in XRAS as its first step.
    """

    template = 'dashboards/user/fragments/request_new_htmx.html'
    roster_private = True

    def __init__(self, row, opportunity_id, token):
        super().__init__()
        self.row, self.opportunity_id, self.token = row, opportunity_id, token
        self.request_urls = {
            'fixed_opportunity': True,
            'mnemonic_suggest': url_for('allocation_request.mnemonic_suggest', token=token),
            'award_lookup': url_for('allocation_request.award_lookup', token=token),
            'terms': url_for('allocation_request.terms'),
            'done': url_for('allocation_request.status', token=token),
            'where': "this request's status page"}

    def _arm(self, config):
        if not config.visitor_submit_configured:
            raise FormError('New requests from this page are not enabled here.')

    def _opportunities(self, config):
        opportunity, outage = _open(self.opportunity_id)
        if outage:
            raise FormError('XRAS is not answering, so the request cannot continue now.')
        if opportunity is None:
            raise FormError('That allocation is no longer open. Choose another from the start.')
        return [opportunity]

    def _chosen_id(self):
        return self.opportunity_id

    def _pi(self):
        return None

    def _pi_username(self):
        return self.row.xras_username

    def _institution_name(self):
        return self.row.organization

    def _pi_email(self):
        return self.row.email

    def _post_url(self):
        return url_for('allocation_request.draft_post', token=self.token)

    def _check_admin(self, data, errors):
        data['admin_username'] = None

    def _expected_messages(self, messages):
        """XRAS will not know the PI until the account exists: its 422 is the approval's step."""
        state = (self._ctx or {}).get('sam_state') or {}
        expected = set(state.get('mnemonic_errors') or ()) if state.get('mnemonic') else set()
        return [m for m in messages if self._pi_username() in m or m in expected]

    def draft(self, data):
        from dataclasses import replace
        draft = super().draft(data)
        return replace(draft, sam=replace(draft.sam, registration_id=self.row.account_request_id))

    def _file(self, flow, draft, eula):
        return flow.request_action(session_factory, draft,
                                   actor=f'registration:{self.row.account_request_id}',
                                   xa_user=self.row.xras_username, eula=eula, door='visitor')


@bp.route('/draft/<token>')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def draft(token):
    """Step 5: the request itself, for a confirmed visitor; the form loads into its panel."""
    row, opportunity_id = draft_row(token)
    if row is None:
        return _expired()
    return render_template('register/request_draft.html', row=row,
                           opportunity=open_opportunity(opportunity_id),
                           form_url=url_for('allocation_request.draft_fragment', token=token),
                           overview_url=url_for('allocation_request.overview'),
                           step=_step(5, 'Your request'))


@bp.route('/draft/<token>/form')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def draft_fragment(token):
    """Page one of the visitor's New, into the panel; a refusal renders as a message."""
    row, opportunity_id = draft_row(token)
    if row is None:
        abort(404)
    handler = _VisitorNewRequest(row, opportunity_id, token)
    try:
        ctx = handler.gate()
    except FormError as exc:
        return render_template('dashboards/user/fragments/request_message_htmx.html',
                               title='The request cannot continue',
                               message=' '.join(exc.errors), variant='warning')
    return render_template(handler.template, errors=[], field_errors={}, form=None, **ctx)


@bp.route('/draft/<token>', methods=['POST'])
@_rate_limit.limiter.limit(_post_tier, key_func=_ip_key, methods=['POST'])
def draft_post(token):
    row, opportunity_id = draft_row(token)
    if row is None:
        return _expired()
    return _VisitorNewRequest(row, opportunity_id, token).handle()


@bp.route('/draft/<token>/mnemonic-suggest')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def mnemonic_suggest(token):
    """The code-letter picker's results; codes and their institutions are public knowledge."""
    from sam.queries.mnemonic_console import search_mnemonic_codes
    if draft_row(token)[0] is None:
        abort(404)
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return ''
    codes = search_mnemonic_codes(db.session, q, limit=10)
    return render_template('dashboards/allocations/partials/mnemonic_code_search_results_fk_htmx.html',
                           codes=codes, q=q)


@bp.route('/draft/<token>/award-lookup')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def award_lookup(token):
    if draft_row(token)[0] is None:
        abort(404)
    return award_lookup_fragment(request_urls={
        'award_lookup': url_for('allocation_request.award_lookup', token=token)})


@bp.route('/status/<token>')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def status(token):
    """Where a visitor's request stands: the confirmation, then the filing SAM made."""
    from sam.integration.xras_submission import XrasSubmission
    row, opportunity_id = draft_row(token)
    if row is None:
        return _expired()
    submission = (db.session.query(XrasSubmission)
                  .filter(XrasSubmission.registration_id == row.account_request_id)
                  .order_by(XrasSubmission.xras_submission_id.desc()).first())
    from sam.projects.projects import Project
    project = (db.session.get(Project, submission.project_id)
               if submission is not None and submission.project_id else None)
    return render_template('register/request_status.html', row=row, submission=submission,
                           project=project,
                           draft_url=url_for('allocation_request.draft', token=token),
                           step=_step(5, 'Your request'))
