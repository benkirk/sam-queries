"""``/register`` -- the anonymous HPC account-registration form.

The first internet-facing write in SAM that is not a login, so it carries
the login POST's protections: CSRF (global), the per-IP login tier on every
POST plus a per-address cap, a honeypot, and plain PRG forms (no htmx on a
phone-facing page). A row is created ``submitted`` but invisible to the queue
until the address is verified by the mailed link or the mailed code; the
mail carries nothing the visitor typed. Design: docs/plans/ACCOUNT_REGISTRATION.md 3.5.
"""

import logging
from datetime import datetime, timedelta

from flask import (Blueprint, current_app, redirect, render_template, request,
                   url_for)
from flask_limiter.util import get_remote_address
from marshmallow import ValidationError

from sam.core.account_requests import AccountRequest, AccountRequestEvent
from sam.manage import management_transaction
from sam.manage.account_requests import register_request
from sam.queries.account_notices import build_verify_message
from sam.schemas.forms import RegisterForm, VerifyCodeForm
from webapp.extensions import db
from webapp.limiter import limiter as _rate_limit
from webapp.utils.htmx import institution_options
from webapp.utils.notify import get_notifier

from . import tokens

logger = logging.getLogger(__name__)
bp = Blueprint('register', __name__, url_prefix='/register')

#: The choices the form offers; free text would be a relay vector like the rest.
ACADEMIC_STATUSES = ('Faculty', 'Staff', 'Postdoc', 'Graduate Student',
                     'Undergraduate', 'Other')


def _ip_key():
    return f'ip:{get_remote_address()}'


def _email_key():
    # Raw input, so bounded to the column width before it becomes a Redis key.
    return 'email:' + (request.form.get('email') or '').strip().lower()[:255]


def _global_key():
    # A fixed key: every registration POST shares one bucket, so the tier is a
    # site-wide ceiling independent of IP or address (the relay blast-radius bound).
    return 'register-global'


def _anon_tier():
    return current_app.config['RATELIMIT_ANON']


def _post_tier():
    return current_app.config['RATELIMIT_AUTH_LOGIN']


def _email_tier():
    return current_app.config['RATELIMIT_REGISTER_EMAIL']


def _global_tier():
    return current_app.config['RATELIMIT_REGISTER_GLOBAL']


def _open_event(code):
    """``(event, refusal)``: the event when its code is accepted now, else why not."""
    if not code:
        return None, None
    try:
        normalized = AccountRequestEvent.normalize_code(code)
    except ValueError:
        return None, 'That is not a valid event code.'
    event = db.session.query(AccountRequestEvent).filter_by(event_code=normalized).first()
    if event is None:
        return None, f'{normalized} is not a known event code.'
    if not event.is_open_at(datetime.now()):
        return None, f'{event.event_code} is not accepting registrations right now.'
    return event, None


def _render_form(event=None, *, form=None, errors=(), field_errors=None, locked_code=None):
    return render_template('register/form.html', event=event, form=form or {},
                           errors=list(errors), field_errors=field_errors or {},
                           locked_code=locked_code,
                           academic_options=[(s, s) for s in ACADEMIC_STATUSES])


@bp.route('/', strict_slashes=False)
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def form():
    return _render_form()


@bp.route('/institutions')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def institutions_fragment():
    """Datalist options for the Institution field; names only, nothing the visitor typed.
    The ``_fragment`` suffix keeps it out of the e2e page sweep (e2e/conftest.py)."""
    return institution_options()


@bp.route('/<event_code>')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def form_for_event(event_code):
    """The code pre-filled and locked; an unknown or closed code is refused
    with the reason, at 200."""
    event, refusal = _open_event(event_code)
    if refusal:
        return render_template('register/refused.html', reason=refusal)
    return _render_form(event, locked_code=event.event_code)


@bp.route('/', methods=['POST'], strict_slashes=False)
@_rate_limit.limiter.limit(_post_tier, key_func=_ip_key, methods=['POST'])
@_rate_limit.limiter.limit(_email_tier, key_func=_email_key, methods=['POST'])
@_rate_limit.limiter.limit(_global_tier, key_func=_global_key, methods=['POST'])
def submit():
    raw = request.form
    # The honeypot: a bot fills every field. Pretend success and write nothing.
    if (raw.get('website') or '').strip():
        logger.warning('registration honeypot tripped from %s', get_remote_address())
        return redirect(url_for('register.pending', token=tokens.page_token(0)))
    try:
        data = RegisterForm().load(raw)
    except ValidationError as exc:
        field_errors, form_level = RegisterForm.split_errors(exc.messages)
        return _render_form(form=raw, errors=form_level, field_errors=field_errors)

    event, refusal = _open_event(data.get('event_code'))
    if refusal:
        return _render_form(form=raw, errors=[refusal])
    if event is None and not data.get('purpose_note'):
        return _render_form(form=raw, field_errors={
            'purpose_note': ['Tell us briefly what you need the account for.']})

    now = datetime.now()
    ttl = int(current_app.config.get('ACCOUNT_VERIFY_TTL_HOURS', 48))
    code = tokens.new_code()
    with management_transaction(db.session):
        row = register_request(db.session, event=event, clock=now,
                               source_ip=get_remote_address(), **{
            k: data.get(k) for k in ('email', 'first_name', 'last_name', 'middle_name',
                                     'organization', 'academic_status',
                                     'residence_country', 'orcid', 'phone',
                                     'desired_username', 'purpose_note')})
        row.set_verification(tokens.code_hash(row.account_request_id, code),
                             now + timedelta(hours=ttl))

    message = build_verify_message(
        row, verify_url=url_for('register.verify', token=tokens.link_token(row.account_request_id),
                                _external=True),
        code=code, expires_hours=ttl, event_name=event.name if event else None)
    result = get_notifier().send(message)
    logger.info('registration %s for %s: verification mail %s',
                row.account_request_id, row.email, result.status)
    return redirect(url_for('register.pending', token=tokens.page_token(row.account_request_id),
                            sent=int(result.status in ('sent', 'redirected'))))


def _row_for_page(token):
    row_id = tokens.read_page_token(token)
    return db.session.get(AccountRequest, row_id) if row_id else None


@bp.route('/pending/<token>')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def pending(token):
    """"Check your mail": the code form. Never names the address for a bad
    token (a guessed URL learns nothing)."""
    row = _row_for_page(token)
    if row is None:
        return render_template('register/expired.html'), 200
    if row.is_verified:
        return redirect(url_for('register.verified'))
    return render_template('register/pending.html', row=row, token=token,
                           mail_sent=request.args.get('sent', '1') == '1',
                           errors=[], form={})


@bp.route('/pending/<token>', methods=['POST'])
@_rate_limit.limiter.limit('10 per hour', key_func=lambda: f'verify:{request.view_args.get("token", "")[:64]}',
                           methods=['POST'])
def pending_code(token):
    """The typed six-digit code."""
    row = _row_for_page(token)
    if row is None:
        return render_template('register/expired.html'), 200
    try:
        data = VerifyCodeForm().load(request.form)
    except ValidationError:
        return render_template('register/pending.html', row=row, token=token,
                               mail_sent=True, form=request.form,
                               errors=['Enter the six-digit code from the mail.'])
    if not tokens.code_matches(row, data['code']):
        return render_template('register/pending.html', row=row, token=token,
                               mail_sent=True, form=request.form,
                               errors=['That code is wrong or has expired.'])
    with management_transaction(db.session):
        row.mark_verified('self')
    return redirect(url_for('register.verified'))


@bp.route('/verify/<token>')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def verify(token):
    """The mailed link. A bad or expired token gets the same page as an
    expired code: submit again."""
    row_id = tokens.read_link_token(token)
    row = db.session.get(AccountRequest, row_id) if row_id else None
    if row is None:
        return render_template('register/expired.html'), 200
    if not row.is_verified:
        with management_transaction(db.session):
            row.mark_verified('self')
    return redirect(url_for('register.verified'))


@bp.route('/verified')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def verified():
    return render_template('register/verified.html')
