"""Pieces shared by the ``register``, ``register_events`` and ``register_invite`` blueprints.

Three blueprints because they are mounted by two switches: the creation form
by ACCOUNT_REGISTRATION_ENABLED, the event pages and the invitation link by
ACCOUNT_INVITATIONS_ENABLED. They render the same templates, so the form
context, the rate-limit keys, the gate window and the event lookup live here.
"""

from datetime import datetime, timedelta

from flask import current_app, render_template, url_for
from flask_limiter.util import get_remote_address

from sam.core.account_requests import AccountRequestEvent
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


def refuse(reason):
    """The refused-code page; offers the creation form only where it is mounted."""
    return render_template('register/refused.html', reason=reason,
                           form_url=url_for('register.form') if creation_form_mounted() else None)
