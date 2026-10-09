"""Shared fixtures for the LDAP sync API (``/api/protected/admin``).

A ``make_api_credentials`` row is invisible to a route (routes read ``db.session``
on their own connection), so the DB-key loader is monkeypatched, exactly as
``xras_helpers.xras_keys`` does.
"""

import bcrypt
import pytest

from xras_helpers import basic_auth, reset_db_key_cache  # noqa: F401

__all__ = ['ADMIN_PW', 'PREFIX', 'admin_auth', 'ldapsync_keys', 'ldapsync_client',
           'reset_db_key_cache']

ADMIN_PW = 'ldapsync-test-pw'
PREFIX = '/api/protected/admin'


def admin_auth(username: str = 'admin') -> dict:
    """Headers for *username*; ``admin`` holds ROLE_API_ADMIN, ``nobody`` does not."""
    return {'Authorization': basic_auth(username, ADMIN_PW)}


@pytest.fixture
def ldapsync_keys(monkeypatch):
    from webapp.utils import api_auth

    hashed = bcrypt.hashpw(ADMIN_PW.encode(), bcrypt.gensalt(rounds=4)).decode()
    monkeypatch.setattr(
        api_auth, '_get_db_api_keys',
        lambda: {
            'admin': {'hash': hashed, 'roles': ['ROLE_API_ADMIN']},
            'nobody': {'hash': hashed, 'roles': ['ROLE_XRAS']},
        },
    )


@pytest.fixture
def ldapsync_client(client, ldapsync_keys):
    """Test client with the key map installed; import ``ldapsync_keys`` alongside it."""
    return client
