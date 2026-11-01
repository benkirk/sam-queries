"""Pieces shared by the ``register``, ``register_events``, ``register_invite`` and
``allocation_request`` blueprints.

Separate blueprints because they are mounted by different switches: the creation form by
ACCOUNT_REGISTRATION_ENABLED, the event pages and the invitation link by
ACCOUNT_INVITATIONS_ENABLED, the visitor's request by the former plus its own lever. They
render the same templates, so the form context, the rate-limit keys and tiers, the gate
and the event lookup live here.
"""

from datetime import datetime, timedelta

from flask import current_app, render_template, request, session, url_for
from flask_limiter.util import get_remote_address

from sam.core.account_requests import AccountRequestEvent
from . import eula
from sam.queries.admin import country_names
from webapp.extensions import db

#: The choices the form offers; free text would be a relay vector like the rest.
ACADEMIC_STATUSES = ('Faculty', 'Staff', 'Postdoc', 'Graduate Student',
                     'Undergraduate', 'Other')

# Generous: an expiry at submit bounces to the gate and the typed form is lost.
GATE_TTL = timedelta(hours=2)


def person_form_context() -> dict:
    """The select and datalist options every rendering of ``form.html`` needs."""
    return {'country_options': country_names(db.session),
            'academic_options': [(s, s) for s in ACADEMIC_STATUSES]}


#: The gate marker, set on accept, lets the open form through for one form-fill window.
#: Session-backed (signed on SECRET_KEY), so a direct POST without an accept in this session
#: is bounced: the gate is enforced, not merely hidden. /register and /request share it: one
#: EULA, so accepting on either satisfies both. Design: docs/plans/implemented/ACCOUNT_REGISTRATION.md.
GATE_KEY = 'register_gate_at'


def gate_accepted_at():
    """This session's gate acceptance while still inside the window, else None."""
    try:
        accepted = datetime.fromisoformat(session.get(GATE_KEY) or '')
    except (TypeError, ValueError):
        return None
    return accepted if datetime.now() - accepted <= GATE_TTL else None


def gate_blocks():
    """True when the gate is on and this session has no recent accept."""
    if not current_app.config.get('ACCOUNT_REGISTRATION_GATE_ENABLED', False):
        return False
    return gate_accepted_at() is None


def email_key():
    # Raw input, so bounded to the column width before it becomes a Redis key.
    return 'email:' + (request.form.get('email') or '').strip().lower()[:255]


def global_key():
    # One fixed bucket for every anonymous registration POST, /register and /request alike:
    # the tier is a site-wide ceiling independent of IP or address (the relay blast-radius bound).
    return 'register-global'


def email_tier():
    return current_app.config['RATELIMIT_REGISTER_EMAIL']


def global_tier():
    return current_app.config['RATELIMIT_REGISTER_GLOBAL']


def ip_key():
    return f'ip:{get_remote_address()}'


def anon_tier():
    return current_app.config['RATELIMIT_ANON']


def post_tier():
    return current_app.config['RATELIMIT_AUTH_LOGIN']


def open_event(code):
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
    if event.invite_only:
        return None, (f'{event.event_code} is by invitation only; use the link in '
                      f'your invitation email.')
    if not event.is_open_at(datetime.now()):
        return None, f'{event.event_code} is not accepting registrations right now.'
    return event, None


def creation_form_mounted() -> bool:
    return 'register' in current_app.blueprints


def anonymous_form_open() -> bool:
    """True when an anonymous visitor may use the creation form: mounted and not login-gated."""
    return (creation_form_mounted()
            and not current_app.config.get('ACCOUNT_REGISTRATION_LOGIN_REQUIRED', False))


def terms_page():
    """The agreement on a page of its own, read-only: accepting happens on the gate."""
    return render_template('register/terms.html', eula_html=eula.eula_html())


def expired(*, again_url=None, again_label=None):
    """The expired-token page, 200: it names no address (a guessed URL learns nothing)."""
    return render_template('register/expired.html', again_url=again_url,
                           again_label=again_label), 200


def refuse(reason):
    """The refused-code page; offers the creation form only where it is mounted."""
    return render_template('register/refused.html', reason=reason,
                           form_url=url_for('register.form') if creation_form_mounted() else None)
