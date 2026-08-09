"""Factories for core domain entities: User, Organization, GidAllocation,
AdhocGroup, MnemonicCode."""
import os
import string
from datetime import datetime, timedelta
from typing import Optional

from sam.core.groups import AdhocGroup, GidAllocation
from sam.core.organizations import (
    Institution,
    MnemonicCode,
    Organization,
    UserInstitution,
    UserOrganization,
)
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


# `institution.institution_id` has the same no-AUTO_INCREMENT problem as
# organization (see above), so it gets its own worker-namespaced range.
_INST_ID_BASE = 20_000_000
_INST_ID_WORKER_BASE = _INST_ID_BASE + _WORKER_NUM * _ORG_ID_PER_WORKER


def make_institution(
    session,
    *,
    name: Optional[str] = None,
    acronym: Optional[str] = None,
    deleted: bool = False,
) -> Institution:
    """Build and flush a fresh Institution row.

    `acronym` is NOT NULL; `institution_id` must be supplied explicitly and is
    drawn from a worker-namespaced range so xdist workers cannot collide.
    """
    if acronym is None:
        acronym = next_seq("INST")
    if name is None:
        name = f"Test Institution {acronym}"

    inst = Institution(
        institution_id=_INST_ID_WORKER_BASE + next_int("institution_id"),
        name=name,
        acronym=acronym,
        deleted=deleted,
    )
    session.add(inst)
    session.flush()
    return inst


def make_user_institution(
    session,
    *,
    user,
    institution=None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> UserInstitution:
    """Link a user to an institution over a date window.

    `start_date` is NOT NULL and defaults to a year ago; `end_date` of None
    means an open-ended (currently effective) affiliation.
    """
    if institution is None:
        institution = make_institution(session)
    if start_date is None:
        start_date = datetime.now() - timedelta(days=365)

    ui = UserInstitution(
        user_id=user.user_id,
        institution_id=institution.institution_id,
        start_date=start_date,
        end_date=end_date,
    )
    session.add(ui)
    session.flush()
    return ui


def make_user_organization(
    session,
    *,
    user,
    organization=None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> UserOrganization:
    """Link a user to an organization over a date window (see
    `make_user_institution` for the date conventions)."""
    if organization is None:
        organization = make_organization(session)
    if start_date is None:
        start_date = datetime.now() - timedelta(days=365)

    uo = UserOrganization(
        user_id=user.user_id,
        organization_id=organization.organization_id,
        start_date=start_date,
        end_date=end_date,
    )
    session.add(uo)
    session.flush()
    return uo


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


# gid_allocation blocks must not overlap each other. Each xdist worker
# gets a disjoint 1M-wide slice of the GID number-line, well above any
# range a production block could plausibly occupy.
_GID_BLOCK_BASE = 90_000_000
_GID_BLOCK_PER_WORKER = 1_000_000
_GID_BLOCK_DEFAULT_SIZE = 1_000
_GID_BLOCK_WORKER_BASE = _GID_BLOCK_BASE + _WORKER_NUM * _GID_BLOCK_PER_WORKER


def make_gid_allocation(
    session,
    *,
    size: int = _GID_BLOCK_DEFAULT_SIZE,
    start_gid: Optional[int] = None,
    next_gid: Optional[int] = None,
    end_gid: Optional[int] = None,
) -> GidAllocation:
    """Build and flush a fresh gid_allocation row.

    Carves out a worker-namespaced GID block. By default the block is
    pristine (``next_gid IS NULL``). Pass ``next_gid`` to start partway
    through the block, or ``end_gid`` to override the computed end.
    """
    if start_gid is None:
        # Each call gets a fresh, non-overlapping `size`-wide slot.
        slot = next_int("gid_block_slot")
        start_gid = _GID_BLOCK_WORKER_BASE + slot * size
    if end_gid is None:
        end_gid = start_gid + size - 1

    block = GidAllocation(
        start_gid=start_gid,
        next_gid=next_gid,
        end_gid=end_gid,
    )
    session.add(block)
    session.flush()
    return block


def make_mnemonic_code(
    session,
    *,
    code: Optional[str] = None,
    description: Optional[str] = None,
    active: bool = True,
) -> MnemonicCode:
    """Build and flush a fresh MnemonicCode row.

    ``code`` is a UNIQUE 3-char column, too short for the usual worker-namespaced
    ``next_seq`` strings. Generated codes are ``Q`` plus a **two-character base36
    slice of a 1,296-wide space, partitioned across the xdist workers actually
    running**. The ``Q`` prefix keeps them disjoint from real snapshot mnemonics,
    which are meaningful abbreviations — verified: zero ``Q``-prefixed rows exist.

    ⚠️ **The old scheme was ``Q<worker><counter>``: one char each, so 36 codes per
    worker — and that made capacity depend on how many cores the machine has.** With
    12 workers locally each worker ran few enough tests to stay under 36; CI's
    4-worker runner gave each worker ~3x the tests and blew the limit with
    ``exhausted its 36x36 namespace``. It passed on every developer laptop and failed
    on the smallest machine in the fleet, which is the wrong way round.

    Partitioning by ``PYTEST_XDIST_WORKER_COUNT`` inverts that: **fewer workers means
    a larger share each**, which is exactly when each worker needs more. Four workers
    get 324 codes apiece; twelve get 108; serial (``-n 0``) gets all 1,296.
    """
    if code is None:
        alphabet = string.digits + string.ascii_uppercase
        span = len(alphabet) ** 2                      # 1,296 two-char combinations
        workers = max(1, int(os.environ.get('PYTEST_XDIST_WORKER_COUNT', '1')))
        share = span // workers
        n = next_int("mnemonic_code")
        if n >= share:
            raise RuntimeError(
                f'make_mnemonic_code exhausted worker {_WORKER_NUM}\'s share of the '
                f'code space ({share} codes across {workers} worker(s)). Either a '
                f'test is looping, or the suite genuinely needs more than {span} '
                f'mnemonics and the column is too narrow for that.')
        slot = _WORKER_NUM * share + n
        code = f'Q{alphabet[slot // len(alphabet)]}{alphabet[slot % len(alphabet)]}'
    if description is None:
        description = f"Test mnemonic {next_seq('mnemo_desc')}"

    mnemo = MnemonicCode(code=code, description=description, active=active)
    session.add(mnemo)
    session.flush()
    return mnemo


# adhoc_group.unix_gid is UNIQUE; real snapshot GIDs top out well below
# 100k, and make_gid_allocation blocks live in their own range — carve a
# separate worker-namespaced range for standalone group rows.
_ADHOC_GID_BASE = 900_000
_ADHOC_GID_PER_WORKER = 10_000


def make_adhoc_group(
    session,
    *,
    group_name: Optional[str] = None,
    unix_gid: Optional[int] = None,
    active: bool = True,
) -> AdhocGroup:
    """Build and flush a fresh AdhocGroup row (worker-namespaced name/gid)."""
    if group_name is None:
        group_name = next_seq("grp")
    if unix_gid is None:
        unix_gid = (_ADHOC_GID_BASE + _WORKER_NUM * _ADHOC_GID_PER_WORKER
                    + next_int("adhoc_gid"))

    group = AdhocGroup(group_name=group_name, unix_gid=unix_gid, active=active)
    session.add(group)
    session.flush()
    return group
