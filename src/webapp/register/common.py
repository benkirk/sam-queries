"""Pieces shared by the ``register`` and ``register_invite`` blueprints.

Two blueprints because they are mounted by different switches: the public
form by ACCOUNT_REGISTRATION_ENABLED, the invitation link by
ACCOUNT_INVITATIONS_ENABLED. Both render the same templates, so the form
context, the rate-limit keys and the gate window live here.
"""

from datetime import timedelta

from flask import current_app
from flask_limiter.util import get_remote_address

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
