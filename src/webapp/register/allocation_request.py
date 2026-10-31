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
from webapp.utils.xras import admin_client, interactive_config, read_client, resource_keys

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


def open_opportunity(opportunity_id):
    """The open opportunity a visitor may file under *opportunity_id*, or None."""
    from sam.accounting.allocation_type_guide import public_guides
    opportunities, _ = _opportunities(interactive_config())
    public = {g.xras_allocation_type_id for g in public_guides(db.session)}
    return next((o for o in opportunities
                 if o.get('opportunity_id') == opportunity_id and o.get('can_submit')
                 and o.get('allocation_type_id') in public), None)


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
    """The verified row's way into the request form."""
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


@bp.route('/draft/<token>')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def draft(token):
    """Step 5: the request itself, for a confirmed visitor."""
    row, opportunity_id = draft_row(token)
    if row is None:
        return _expired()
    opportunity = open_opportunity(opportunity_id)
    return render_template('register/request_draft.html', row=row, opportunity=opportunity,
                           overview_url=url_for('allocation_request.overview'),
                           step=_step(5, 'Your request'))
