"""``/register`` -- the anonymous HPC account-registration form.

The first internet-facing write in SAM that is not a login, so it carries
the login POST's protections: CSRF (global), the per-IP login tier on every
POST plus a per-address cap, a honeypot, and plain PRG forms (no htmx on a
phone-facing page). A row is created ``submitted`` but invisible to the queue
until the address is verified by the mailed link or the mailed code; the
mail carries nothing the visitor typed. Design: docs/plans/implemented/ACCOUNT_REGISTRATION.md 3.5.
"""

import logging
from datetime import datetime, timedelta

from flask import (Blueprint, current_app, redirect, render_template, request,
                   session, url_for)
from flask_limiter.util import get_remote_address
from flask_login import current_user
from marshmallow import ValidationError

from sam.core.account_requests import AccountRequest
from sam.manage import management_transaction
from sam.manage.account_requests import register_request
from sam.queries.account_notices import build_verify_message
from sam.schemas.forms import RegisterForm, RegisterGateForm, VerifyCodeForm
from webapp.dashboards.event_lifecycle import upcoming_events_data
from webapp.extensions import db
from webapp.limiter import limiter as _rate_limit
from webapp.utils import human_check
from webapp.utils.htmx import institution_options
from webapp.utils.notify import get_notifier

from . import eula, tokens
from .handoff_mail import send_ticket
from .common import (ACADEMIC_STATUSES, GATE_TTL, anon_tier as _anon_tier,  # noqa: F401
                     ip_key as _ip_key, open_event as _open_event, person_form_context,
                     post_tier as _post_tier, refuse as _refuse)

logger = logging.getLogger(__name__)
bp = Blueprint('register', __name__, url_prefix='/register')

@bp.before_request
def _login_gate():
    """ACCOUNT_REGISTRATION_LOGIN_REQUIRED: every route here, the mailed
    verify link included, sends a visitor to login first (they come back
    via ``next``). One hook rather than eight decorators, so the public form
    is one config flip away."""
    if (current_app.config.get('ACCOUNT_REGISTRATION_LOGIN_REQUIRED', False)
            and not current_user.is_authenticated):
        return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))
    return None


def _email_key():
    # Raw input, so bounded to the column width before it becomes a Redis key.
    return 'email:' + (request.form.get('email') or '').strip().lower()[:255]


def _global_key():
    # A fixed key: every registration POST shares one bucket, so the tier is a
    # site-wide ceiling independent of IP or address (the relay blast-radius bound).
    return 'register-global'


def _email_tier():
    return current_app.config['RATELIMIT_REGISTER_EMAIL']


def _global_tier():
    return current_app.config['RATELIMIT_REGISTER_GLOBAL']


def _event_options():
    """The publicly listed open events; an unlisted event is reachable by link only."""
    return [(e['event_code'], f"{e['name']} ({e['event_code']})")
            for e in upcoming_events_data() if not e.get('invite_only')]


def _render_form(event=None, *, form=None, errors=(), field_errors=None, locked_code=None):
    return render_template('register/form.html', event=event, form=form or {},
                           errors=list(errors), field_errors=field_errors or {},
                           locked_code=locked_code,
                           human_check=human_check.provider(),
                           human_check_site_key=current_app.config.get('HUMAN_CHECK_SITE_KEY', ''),
                           event_options=[] if locked_code else _event_options(),
                           form_action=url_for('register.submit'),
                           institutions_url=url_for('register.institutions_fragment'),
                           **person_form_context())


def _rerender(raw, **kwargs):
    """An error re-render. A link-locked form stays locked: an unlisted code
    is not among the select's options and would otherwise drop silently."""
    event = _open_event(raw.get('event_code'))[0] if raw.get('event_locked') else None
    return _render_form(event, form=raw,
                        locked_code=event.event_code if event else None, **kwargs)


#: The gate marker, set on accept, lets the open form through for one form-fill
#: window. Session-backed (signed on SECRET_KEY), so a direct POST to the form
#: without an accept in this session is bounced -- the gate is enforced, not
#: merely hidden. Design: docs/plans/implemented/ACCOUNT_REGISTRATION.md.
_GATE_KEY = 'register_gate_at'
_GATE_TTL = GATE_TTL


def _gate_accepted_at():
    """This session's gate acceptance while still inside the window, else None."""
    try:
        accepted = datetime.fromisoformat(session.get(_GATE_KEY) or '')
    except (TypeError, ValueError):
        return None
    return accepted if datetime.now() - accepted <= _GATE_TTL else None


def _gate_blocks():
    """True when the gate is on and this session has no recent accept."""
    if not current_app.config.get('ACCOUNT_REGISTRATION_GATE_ENABLED', False):
        return False
    return _gate_accepted_at() is None


def _render_gate(event=None, *, locked_code=None, form=None, errors=(), field_errors=None):
    return render_template('register/gate.html', event=event, locked_code=locked_code,
                           form=form or {}, errors=list(errors),
                           field_errors=field_errors or {},
                           accept_url=url_for('register.accept'),
                           terms_url=url_for('register.terms'),
                           eula_html=eula.eula_html())


@bp.route('/', strict_slashes=False)
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def form():
    if _gate_blocks():
        return _render_gate()
    return _render_form()


@bp.route('/terms')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def terms():
    """The agreement on a page of its own (the gate links it in a new tab).
    Read-only: accepting still happens on the gate."""
    return render_template('register/terms.html', eula_html=eula.eula_html())


@bp.route('/institutions')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def institutions_fragment():
    """Datalist options for the Institution field; names only, nothing the visitor typed.
    The ``_fragment`` suffix keeps it out of the e2e page sweep (e2e/conftest.py)."""
    return institution_options()


@bp.route('/accept', methods=['POST'], strict_slashes=False)
@_rate_limit.limiter.limit(_post_tier, key_func=_ip_key, methods=['POST'])
def accept():
    """The accept-first gate: the terms acceptance. On success the open form
    is reachable for one fill window (re-checked in submit)."""
    event, refusal = _open_event(request.form.get('event_code'))
    if refusal:
        return _refuse(refusal)
    locked = event.event_code if event else None
    try:
        RegisterGateForm().load(request.form)
    except ValidationError as exc:
        field_errors, form_level = RegisterGateForm.split_errors(exc.messages)
        return _render_gate(event, locked_code=locked, form=request.form,
                            errors=form_level, field_errors=field_errors)
    session[_GATE_KEY] = datetime.now().isoformat()
    # The event pages are a sibling blueprint (events.py) that may be unmounted.
    if locked and 'register_events' in current_app.blueprints:
        return redirect(url_for('register_events.form_for_event', event_code=locked))
    return redirect(url_for('register.form'))


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
    # The gate is enforced here, not just in the UI: the open inputs cannot be
    # submitted until the terms were accepted in this session.
    if _gate_blocks():
        event, _refusal = _open_event(raw.get('event_code'))
        return _render_gate(event, locked_code=(event.event_code if event else None))
    try:
        data = RegisterForm().load(raw)
    except ValidationError as exc:
        field_errors, form_level = RegisterForm.split_errors(exc.messages)
        return _rerender(raw, errors=form_level, field_errors=field_errors)

    event, refusal = _open_event(data.get('event_code'))
    if refusal:
        return _rerender(raw, errors=[refusal])
    if event is None and not data.get('purpose_note'):
        return _rerender(raw, field_errors={
            'purpose_note': ['Tell us briefly what you need the account for.']})
    # Last before the write, so a typo elsewhere never spends the one-use token.
    human_error = human_check.verify(raw)
    if human_error:
        return _rerender(raw, errors=[human_error])

    now = datetime.now()
    ttl = int(current_app.config.get('ACCOUNT_VERIFY_TTL_HOURS', 48))
    code = tokens.new_code()
    accepted_at = _gate_accepted_at()
    with management_transaction(db.session):
        row = register_request(db.session, event=event, clock=now,
                               source_ip=get_remote_address(),
                               eula_sha=eula.eula_sha() if accepted_at else None,
                               eula_accepted_at=accepted_at, **{
            k: data.get(k) for k in ('email', 'first_name', 'last_name', 'middle_name',
                                     'organization', 'academic_status',
                                     'residence_country', 'orcid', 'phone', 'purpose_note')})
        row.set_verification(tokens.code_hash(row.account_request_id, code),
                             now + timedelta(hours=ttl))

    message = build_verify_message(
        row, verify_url=url_for('register.verify', token=tokens.link_token(row.account_request_id),
                                _external=True),
        code=code, expires_hours=ttl, event_name=event.name if event else None,
        eula_text=eula.eula_text(), eula_html=eula.eula_html())
    result = get_notifier().send(message)
    logger.info('registration %s for %s: verification mail %s',
                row.account_request_id, row.email, result.status)
    session.pop(_GATE_KEY, None)  # best effort: a cookie session cannot revoke
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
    send_ticket(row)
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
        send_ticket(row)
    return redirect(url_for('register.verified'))


@bp.route('/verified')
@_rate_limit.limiter.limit(_anon_tier, key_func=_ip_key)
def verified():
    return render_template('register/verified.html')
