"""Host provisioning cross-checks.

On a provisioned NCAR host (Casper/Derecho) the local NSS layer resolves users
and groups against LDAP — the same source `getent` reads. This module compares
what SAM (the database) believes against what the host actually provisions,
using the Python `pwd` / `grp` / `os.getgrouplist` primitives (no shelling out).

Everything here is read-only and side-effect free: no Rich, no DB writes. The
CLI builders call the ``check_*`` functions and hand the resulting plain dicts
to display / JSON. When the host cannot answer (not provisioned, user/group
absent), lookups degrade to ``None`` / ``[]`` rather than raising.

The single primitive behind both checks is :func:`user_group_gids`, which wraps
``os.getgrouplist``. Unlike ``grp.getgrnam().gr_mem`` it includes a group that
is the user's *primary* group, so "is user U in project group G" is answered
correctly whether G is a primary or supplementary group.
"""

import os
import pwd
import grp

# Shells that mean "this account cannot log in" — flagged for an active user.
NOLOGIN_SHELLS = frozenset({
    '/sbin/nologin',
    '/usr/sbin/nologin',
    '/bin/false',
    '/usr/bin/false',
    '/bin/true',
})


def is_provisioned_host() -> bool:
    """Whether host provisioning data is meaningful here (the gate).

    ``SAM_CHECK_PROVISIONING`` (if set) wins outright — ``true/1/yes`` forces
    the check on, anything else forces it off. Otherwise the gate follows the
    presence of ``NCAR_HOST``, which is defined on provisioned NCAR systems.
    """
    override = os.getenv('SAM_CHECK_PROVISIONING')
    if override is not None:
        return override.strip().lower() in ('true', '1', 'yes')
    return bool(os.getenv('NCAR_HOST'))


# ---------------------------------------------------------------- NSS primitives

def _getpwnam(username: str):
    """`pwd.getpwnam`, returning None instead of raising KeyError."""
    try:
        return pwd.getpwnam(username)
    except KeyError:
        return None


def _getgrgid(gid):
    """`grp.getgrgid`, returning None instead of raising KeyError."""
    if gid is None:
        return None
    try:
        return grp.getgrgid(gid)
    except KeyError:
        return None


def _getgrnam(name: str):
    """`grp.getgrnam`, returning None instead of raising KeyError."""
    try:
        return grp.getgrnam(name)
    except KeyError:
        return None


def user_group_gids(username: str, pw_gid: int) -> set:
    """Full set of GIDs (primary + supplementary) the host assigns to a user.

    Wraps ``os.getgrouplist`` so a project group counts whether it is the
    user's primary or a supplementary group. Returns an empty set if the host
    cannot resolve the user.
    """
    try:
        return set(os.getgrouplist(username, pw_gid))
    except (KeyError, OSError):
        return set()


# ----------------------------------------------------------------- user check

def check_user_provisioning(user, projects) -> dict:
    """Compare a SAM user against host provisioning.

    Args:
        user: SAM ``User`` (uses ``username``, ``unix_uid``).
        projects: iterable of SAM ``Project`` the user should have host group
            membership for (typically ``user.active_projects()``). Each needs
            ``projcode`` and ``unix_gid``.

    Returns a plain dict::

        {recognized, uid, uid_matches, home, home_exists, shell, shell_ok,
         missing_project_groups: [{projcode, unix_gid}], ok}

    ``ok`` is True when the host recognizes the user, the uid matches, the
    account can log in, and every project group is present. ``home_exists`` /
    ``shell_ok`` surface softer warnings and do not gate ``ok``.
    """
    pw = _getpwnam(user.username)
    if pw is None:
        return {
            'recognized': False,
            'uid': None,
            'uid_matches': None,
            'home': None,
            'home_exists': None,
            'shell': None,
            'shell_ok': None,
            'missing_project_groups': [],
            'ok': False,
        }

    gids = user_group_gids(user.username, pw.pw_gid)
    missing = [
        {'projcode': p.projcode, 'unix_gid': p.unix_gid}
        for p in projects
        if p.unix_gid is not None and p.unix_gid not in gids
    ]
    missing.sort(key=lambda m: m['projcode'])

    uid_matches = (user.unix_uid == pw.pw_uid)
    shell_ok = pw.pw_shell not in NOLOGIN_SHELLS
    home_exists = bool(pw.pw_dir) and os.path.isdir(pw.pw_dir)

    return {
        'recognized': True,
        'uid': pw.pw_uid,
        'uid_matches': uid_matches,
        'home': pw.pw_dir,
        'home_exists': home_exists,
        'shell': pw.pw_shell,
        'shell_ok': shell_ok,
        'missing_project_groups': missing,
        'ok': uid_matches and not missing,
    }


# --------------------------------------------------------------- project check

def check_project_provisioning(project) -> dict:
    """Compare a SAM project's roster against its host group.

    Args:
        project: SAM ``Project`` (uses ``projcode``, ``unix_gid``, ``users``).

    Returns a plain dict::

        {group_exists, gid, group_name, name_matches, os_member_count,
         sam_member_count, missing_from_group: [usernames],
         extra_in_group: [usernames], ok}

    ``missing_from_group`` = SAM roster members whose host group set lacks the
    project's gid (per-user ``getgrouplist``, so a primary-group membership
    counts). ``extra_in_group`` = group members with no active SAM membership
    (ghosts). ``ok`` is True when the group exists and both lists are empty.
    """
    group = _getgrgid(project.unix_gid)
    # Fall back to name lookup if the gid is unset/unresolvable. Host group
    # names are the lowercased projcode (scsg0001 ↔ SCSG0001), and getgrnam is
    # case-sensitive, so look up the lowercased form.
    if group is None:
        group = _getgrnam(project.projcode.lower())

    if group is None:
        return {
            'group_exists': False,
            'gid': project.unix_gid,
            'group_name': None,
            'name_matches': None,
            'os_member_count': 0,
            'sam_member_count': len(project.users),
            'missing_from_group': [],
            'extra_in_group': [],
            'ok': False,
        }

    sam_users = {u.username: u for u in project.users}
    os_members = set(group.gr_mem)

    missing_from_group = sorted(
        username for username, u in sam_users.items()
        if group.gr_gid not in user_group_gids(username, _primary_gid(u))
    )
    extra_in_group = sorted(os_members - set(sam_users))

    return {
        'group_exists': True,
        'gid': group.gr_gid,
        'group_name': group.gr_name,
        # Host group names are the lowercased projcode (scsg0001 ↔ SCSG0001).
        'name_matches': group.gr_name.lower() == project.projcode.lower(),
        'os_member_count': len(os_members),
        'sam_member_count': len(sam_users),
        'missing_from_group': missing_from_group,
        'extra_in_group': extra_in_group,
        'ok': not missing_from_group and not extra_in_group,
    }


def _primary_gid(user) -> int:
    """Host primary gid for a SAM user, needed as the ``getgrouplist`` seed.

    Falls back to the SAM ``unix_uid`` (a harmless non-match) if the host does
    not recognize the user, so ``getgrouplist`` still returns the empty set.
    """
    pw = _getpwnam(user.username)
    return pw.pw_gid if pw else (user.unix_uid or 0)
