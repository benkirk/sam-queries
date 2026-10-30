"""Session impersonation: who is really at the keyboard while ``current_user`` is someone else.

``admin_dashboard.impersonate`` logs the admin in as the target and keeps the admin's id under
one session key; every read of that key goes through here. The key is dropped at logout and
at every login, so a browser never inherits an impersonation from an earlier session.
"""

from typing import Optional

from flask import session
from flask_login import current_user

SESSION_KEY = 'impersonator_id'


def impersonator_id() -> Optional[int]:
    return session.get(SESSION_KEY)


def is_impersonating() -> bool:
    return bool(session.get(SESSION_KEY))


def begin(impersonator_user_id: int) -> None:
    session[SESSION_KEY] = impersonator_user_id


def end() -> None:
    session.pop(SESSION_KEY, None)


def impersonator():
    """The admin's ``User`` row behind an impersonated session, or ``None``."""
    from sam.core.users import User
    from webapp.extensions import db
    uid = impersonator_id()
    return db.session.get(User, uid) if uid else None
