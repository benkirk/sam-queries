"""Factories for core domain entities: User, Organization."""
import os
from typing import Optional

from sam.core.organizations import Organization
from sam.core.users import User

from ._seq import next_int, next_seq

# `organization.organization_id` has no AUTO_INCREMENT — every INSERT must
# supply an ID. Two xdist workers querying `max(organization_id)` inside
# their own SAVEPOINTs would compute the same next_id and collide on the
# primary key. Instead we carve out a high, worker-namespaced ID range
# that is well above any real organization in the snapshot (real IDs are
# in the low thousands).
_ORG_ID_BASE = 10_000_000
_ORG_ID_PER_WORKER = 100_000
_WORKER_NUM = int(os.environ.get("PYTEST_XDIST_WORKER", "gw0").removeprefix("gw") or "0")
_ORG_ID_WORKER_BASE = _ORG_ID_BASE + _WORKER_NUM * _ORG_ID_PER_WORKER


def make_organization(
    session,
    *,
    name: Optional[str] = None,
    acronym: Optional[str] = None,
    parent_org_id: Optional[int] = None,
) -> Organization:
    """Build and flush a fresh Organization row.

    Uses a worker-namespaced ID range so concurrent xdist workers cannot
    collide on the non-autoincrement primary key.
    """
    if acronym is None:
        acronym = next_seq("ORG")
    if name is None:
        name = f"Test Organization {acronym}"

    next_id = _ORG_ID_WORKER_BASE + next_int("organization_id")

    org = Organization(
        organization_id=next_id,
        name=name,
        acronym=acronym,
        parent_org_id=parent_org_id,
    )
    session.add(org)
    session.flush()
    return org


def make_user(
    session,
    *,
    username: Optional[str] = None,
    unix_uid: Optional[int] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    active: bool = True,
) -> User:
    """Build and flush a fresh User row.

    Only `username` (unique, ≤35 chars) and `unix_uid` are NOT NULL without
    defaults — everything else has a sane default or is nullable.
    """
    if username is None:
        username = next_seq("usr")
    if unix_uid is None:
        # unix_uid has no UNIQUE constraint, so plain counter is fine.
        unix_uid = 900_000 + next_int("uid")

    user = User(
        username=username,
        unix_uid=unix_uid,
        first_name=first_name or "Test",
        last_name=last_name or "User",
        active=active,
    )
    session.add(user)
    session.flush()
    return user
