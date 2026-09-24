"""``/register/invite`` -- the invitee finishes a sponsor's request.

Mounted by ACCOUNT_INVITATIONS_ENABLED, independent of the public form's
switches, and with no login hook: the invitee has no account, and the signed
token (bound to ``invite_sent_at``, so a resend voids older links) is the
capability. The route sends no mail, so it cannot relay. Submitting UPDATES
the sponsor's row in place and stamps the agreement accepted on the gate.
Design: docs/plans/implemented/ACCOUNT_INVITE_LINKS.md.
"""

import logging
from datetime import datetime

from flask import Blueprint, redirect, render_template, request, session, url_for
from flask_limiter.util import get_remote_address
from marshmallow import ValidationError

from sam.core.account_requests import OPEN_STATES, AccountRequest, AccountRequestEvent
from sam.core.users import User
from sam.manage import management_transaction
from sam.projects.projects import Project
from sam.schemas.forms import RegisterForm, RegisterGateForm
from webapp.extensions import db
from webapp.limiter import limiter as _rate_limit
from webapp.utils.htmx import institution_options

from . import eula, tokens
from .common import GATE_TTL, anon_tier, ip_key, person_form_context, post_tier

logger = logging.getLogger(__name__)
bp = Blueprint('register_invite', __name__, url_prefix='/register/invite')

#: ``{'id', 'sent', 'at'}``: an accept counts for this link only.
_GATE_KEY = 'register_invite_gate'


def _token_key():
    return 'invite:' + (request.view_args or {}).get('token', '')[:64]


def _resolve(token):
    """``(row, refusal)`` for a link: the row it may complete, else why not."""
    row_id, sent, problem = tokens.read_invite_token(token)
    if problem == 'expired':
        return None, ('This invitation link has expired. Ask whoever invited you '
                      'to send a new one.')
    row = db.session.get(AccountRequest, row_id) if row_id else None
    if row is None or row.invite_sent_at is None:
        return None, 'This is not a valid invitation link.'
    if tokens.invite_stamp(row.invite_sent_at) != sent:
        return None, ('A newer invitation was sent to you. Use the link in the '
                      'most recent email.')
    if row.is_fulfilled:
        return None, 'Your NCAR HPC account already exists; nothing more is needed.'
    if row.state not in OPEN_STATES:
        return None, ('This request was closed by the account team. Contact whoever '
                      'invited you if you still need an account.')
    return row, None


def _gate_accepted_at(row):
    marker = session.get(_GATE_KEY) or {}
    if (marker.get('id') != row.account_request_id
            or marker.get('sent') != tokens.invite_stamp(row.invite_sent_at)):
        return None
    try:
        accepted = datetime.fromisoformat(marker.get('at') or '')
    except (TypeError, ValueError):
        return None
    return accepted if datetime.now() - accepted <= GATE_TTL else None


def _page_context(row):
    event = (db.session.get(AccountRequestEvent, row.event_id) if row.event_id else None)
    project = db.session.get(Project, row.project_id) if row.project_id else None
    sponsor = db.session.get(User, row.sponsor_user_id) if row.sponsor_user_id else None
    return {'invite': row, 'event': event,
            'project_code': project.projcode if project else '',
            'sponsor_name': sponsor.display_name if sponsor else ''}


def _prefill(row):
    return {key: getattr(row, key) or '' for key in AccountRequest.INVITE_FIELDS}


def _render_form(token, row, *, form=None, errors=(), field_errors=None):
    return render_template('register/form.html', form=form or _prefill(row),
                           errors=list(errors), field_errors=field_errors or {},
                           locked_code=None, event_options=[],
                           form_action=url_for('register_invite.submit', token=token),
                           institutions_url=url_for('register_invite.institutions_fragment'),
                           **_page_context(row), **person_form_context())


def _render_gate(token, row, *, errors=(), field_errors=None):
    return render_template('register/gate.html', locked_code=None, form={},
                           errors=list(errors), field_errors=field_errors or {},
                           accept_url=url_for('register_invite.accept', token=token),
                           terms_url=url_for('register_invite.terms'),
                           eula_html=eula.eula_html(), **_page_context(row))


def _refuse(reason):
    return render_template('register/invite_refused.html', reason=reason)


@bp.route('/terms')
@_rate_limit.limiter.limit(anon_tier, key_func=ip_key)
def terms():
    """The agreement on its own page, as ``register.terms`` (which may be unmounted)."""
    return render_template('register/terms.html', eula_html=eula.eula_html())


@bp.route('/institutions')
@_rate_limit.limiter.limit(anon_tier, key_func=ip_key)
def institutions_fragment():
    """Datalist options for the Institution field; names only."""
    return institution_options()


@bp.route('/complete')
@_rate_limit.limiter.limit(anon_tier, key_func=ip_key)
def complete():
    return render_template('register/invite_complete.html', already=False)


@bp.route('/<token>')
@_rate_limit.limiter.limit(anon_tier, key_func=ip_key)
def page(token):
    """The gate first, then the pre-filled form. Refusals render at 200."""
    row, refusal = _resolve(token)
    if refusal:
        return _refuse(refusal)
    if row.completed_at is not None:
        return render_template('register/invite_complete.html', already=True)
    if _gate_accepted_at(row) is None:
        return _render_gate(token, row)
    return _render_form(token, row)


@bp.route('/<token>/accept', methods=['POST'])
@_rate_limit.limiter.limit(post_tier, key_func=ip_key, methods=['POST'])
def accept(token):
    row, refusal = _resolve(token)
    if refusal:
        return _refuse(refusal)
    try:
        RegisterGateForm().load(request.form)
    except ValidationError as exc:
        field_errors, form_level = RegisterGateForm.split_errors(exc.messages)
        return _render_gate(token, row, errors=form_level, field_errors=field_errors)
    session[_GATE_KEY] = {'id': row.account_request_id,
                          'sent': tokens.invite_stamp(row.invite_sent_at),
                          'at': datetime.now().isoformat()}
    return redirect(url_for('register_invite.page', token=token))


@bp.route('/<token>', methods=['POST'])
@_rate_limit.limiter.limit(post_tier, key_func=ip_key, methods=['POST'])
@_rate_limit.limiter.limit('10 per hour', key_func=_token_key, methods=['POST'])
def submit(token):
    """Update the sponsor's row in place. No verify mail: the link proved the address."""
    row, refusal = _resolve(token)
    if refusal:
        return _refuse(refusal)
    if row.completed_at is not None:
        return render_template('register/invite_complete.html', already=True)
    accepted_at = _gate_accepted_at(row)
    if accepted_at is None:
        return _render_gate(token, row)
    raw = {k: v for k, v in request.form.items()
           if k not in ('event_code', 'purpose_note')}
    raw['email'] = row.email
    try:
        data = RegisterForm().load(raw)
    except ValidationError as exc:
        field_errors, form_level = RegisterForm.split_errors(exc.messages)
        return _render_form(token, row, form=request.form, errors=form_level,
                            field_errors=field_errors)
    fields = {k: data.get(k) for k in AccountRequest.INVITE_FIELDS}
    try:
        with management_transaction(db.session):
            row.complete_invite(fields=fields, eula_sha=eula.eula_sha(),
                                accepted_at=accepted_at, source_ip=get_remote_address())
    except ValueError as exc:
        return _render_form(token, row, form=request.form, errors=[str(exc)])
    logger.info('invite %s completed by the invitee', row.account_request_id)
    session.pop(_GATE_KEY, None)
    return redirect(url_for('register_invite.complete'))
