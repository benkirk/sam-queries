"""Factories for security-domain entities: Role, ApiCredentials, and the
samuel_role_* catalog rows.

Mirrors the legacy `api_credentials` / `role_api_credentials` tables that
new SAM authenticates against on the API paths (see webapp.utils.api_auth).
"""
from typing import Optional, Sequence

import bcrypt

from sam.security.access import AccessBranch, AccessBranchResource
from sam.security.permissions import Permission
from sam.security.roles import ApiCredentials, Role, RoleApiCredentials
from sam.security.samuel_roles import SamuelRole, SamuelRoleGrant

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

    for role_name in roles:
        role = make_role(session, name=role_name)
        session.add(
            RoleApiCredentials(role_id=role.role_id, api_credentials_id=cred.api_credentials_id)
        )
    session.flush()
    return cred


def make_samuel_role(session, *, name: Optional[str] = None,
                     permissions: Sequence[Permission] = (Permission.VIEW_USERS,),
                     extends: Optional[SamuelRole] = None, by: str = 'factory',
                     description: Optional[str] = None) -> SamuelRole:
    """Build and flush a samuel_role row with its permission rows."""
    if name is None:
        name = next_seq("srole")
    return SamuelRole.create(session, name=name, permissions=permissions, by=by,
                             description=description, extends=extends)


def make_samuel_grant(session, *, subject_type: str = 'user',
                      subject_name: Optional[str] = None,
                      role: Optional[SamuelRole] = None,
                      permission: Optional[Permission] = None,
                      facility_name: Optional[str] = None, by: str = 'factory',
                      note: Optional[str] = None) -> SamuelRoleGrant:
    """Build and flush a grant; makes a role when neither role nor permission is given."""
    if subject_name is None:
        subject_name = next_seq("subj")
    if role is None and permission is None:
        role = make_samuel_role(session)
    return SamuelRoleGrant.create(session, subject_type=subject_type,
                                  subject_name=subject_name, by=by, role=role,
                                  permission=permission, facility_name=facility_name,
                                  note=note)


def make_access_branch(session, name: Optional[str] = None, resources: Sequence = ()) -> AccessBranch:
    """An access branch linked to *resources*."""
    branch = AccessBranch(name=name or next_seq('branch'))
    session.add(branch)
    session.flush()
    for r in resources:
        session.add(AccessBranchResource(access_branch_id=branch.access_branch_id, resource_id=r.resource_id))
    session.flush()
    return branch
