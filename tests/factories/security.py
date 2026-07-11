"""Factories for security-domain entities: Role, ApiCredentials.

Mirrors the legacy `api_credentials` / `role_api_credentials` tables that
new SAM authenticates against on the API paths (see webapp.utils.api_auth).
"""
from typing import Optional, Sequence

import bcrypt

from sam.security.roles import ApiCredentials, Role, RoleApiCredentials

from ._seq import next_seq

# Low bcrypt cost — tests hash many throwaway passwords; 4 rounds keeps them fast.
_TEST_BCRYPT_ROUNDS = 4


def make_role(session, *, name: Optional[str] = None, description: Optional[str] = None) -> Role:
    """Build and flush a fresh Role row."""
    if name is None:
        name = next_seq("role")
    role = Role(name=name, description=description)
    session.add(role)
    session.flush()
    return role


def make_api_credentials(
    session,
    *,
    username: Optional[str] = None,
    password: str = "secret-key",
    password_hash: Optional[str] = None,
    enabled: bool = True,
    roles: Sequence[str] = (),
) -> ApiCredentials:
    """Build and flush a fresh ApiCredentials row.

    Pass `password` (plaintext) and the factory bcrypt-hashes it — the caller
    authenticates with that same plaintext. Alternatively inject a specific
    `password_hash` (e.g. a legacy ``$2a$`` hash) to exercise hash-variant
    handling; it takes precedence over `password`.

    `roles` is a list of role names; each is created as a Role and linked via
    RoleApiCredentials. `username` is capped at 11 chars by the schema, so the
    generated default (`api<worker><n>`) stays short.
    """
    if username is None:
        username = next_seq("api")  # e.g. 'api00001' — ≤ 11 chars
    if password_hash is None:
        password_hash = bcrypt.hashpw(
            password.encode("utf-8"), bcrypt.gensalt(rounds=_TEST_BCRYPT_ROUNDS)
        ).decode()

    cred = ApiCredentials(username=username, password=password_hash, enabled=enabled)
    session.add(cred)
    session.flush()

    # The dev/test DB clone truncates `api_credentials` (resetting its
    # AUTO_INCREMENT) but NOT `role_api_credentials`, so a freshly inserted row
    # can reuse an id that leftover role links still point at. Clear any such
    # rows for our new id so role assignments are deterministic (no-op in prod,
    # where the two tables are consistent). All within the test's SAVEPOINT.
    session.query(RoleApiCredentials).filter(
        RoleApiCredentials.api_credentials_id == cred.api_credentials_id
    ).delete(synchronize_session=False)
    session.expire(cred, ["role_assignments"])

    for role_name in roles:
        role = make_role(session, name=role_name)
        session.add(
            RoleApiCredentials(role_id=role.role_id, api_credentials_id=cred.api_credentials_id)
        )
    session.flush()
    return cred
