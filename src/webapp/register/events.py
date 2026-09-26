"""``/register/<event_code>`` -- the event pages, mounted by ACCOUNT_INVITATIONS_ENABLED.

The open event link is the capability. A signed-in visitor gets the
self-enroll shortcut (their existing account joins the event's project);
an anonymous one gets the code-locked creation form only where that form is
mounted and open (``anonymous_form_open``), else a login redirect. Kept apart
from the creation form so production can serve events with that form absent.
"""

import logging

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user

from sam.core.users import User
from sam.manage import management_transaction
from sam.manage.account_requests import enroll_user_in_event
from sam.projects.projects import Project
from webapp.extensions import db
from webapp.limiter import limiter as _rate_limit

from .blueprint import _gate_blocks, _render_form, _render_gate
from .common import anon_tier, anonymous_form_open, ip_key, open_event, post_tier, refuse

logger = logging.getLogger(__name__)
bp = Blueprint('register_events', __name__, url_prefix='/register')


def _user_key():
    return f'user:{getattr(current_user, "user_id", None)}'


def _render_self_enroll(event, *, error=None):
    return render_template('register/self_enroll.html', event=event,
                           project=db.session.get(Project, event.project_id),
                           error=error)


@bp.route('/<event_code>')
@_rate_limit.limiter.limit(anon_tier, key_func=ip_key)
def form_for_event(event_code):
    """An unknown or closed code is refused with the reason, at 200."""
    event, refusal = open_event(event_code)
    if refusal:
        return refuse(refusal)
    if current_user.is_authenticated:
        return _render_self_enroll(event)
    if not anonymous_form_open():
        return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))
    if _gate_blocks():
        return _render_gate(event, locked_code=event.event_code)
    return _render_form(event, locked_code=event.event_code)


@bp.route('/<event_code>/enroll', methods=['POST'])
@_rate_limit.limiter.limit(post_tier, key_func=_user_key, methods=['POST'])
def self_enroll(event_code):
    """Add the signed-in user's own account to the event's project. The
    session is the identity, so no email round-trip. Enrolling others stays
    in the RBAC'd Invitations panel."""
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login',
                                next=url_for('register_events.form_for_event',
                                             event_code=event_code)))
    event, refusal = open_event(event_code)
    if refusal:
        return refuse(refusal)
    user = db.session.get(User, current_user.user_id)
    try:
        with management_transaction(db.session):
            enroll_user_in_event(db.session, event=event, user=user,
                                 source='self', by=current_user.username)
    except ValueError as exc:
        logger.warning('self-enroll %s for user %s failed: %s',
                       event.event_code, current_user.user_id, exc)
        return _render_self_enroll(event, error=str(exc))
    logger.info('self-enroll %s: user %s -> project %s',
                event.event_code, current_user.user_id, event.project_id)
    return render_template('register/enrolled.html', event=event,
                           project=db.session.get(Project, event.project_id))
