"""Directory Access query functions.

``group_populator()`` and ``user_populator()`` reproduce the legacy Java
``GET /protected/admin/sysacct/directoryaccess``, organized by access branch
(hpc, hpc-data, hpc-dev) into ``unixGroups`` and ``unixAccounts``.

Group sources, in legacy pipeline order:

1. **Implicit project groups** -- active projects with allocations within the
   grace period, linked to branches via account -> resource ->
   access_branch_resource. These rows, and only these, establish the branch's
   *account set*: the usernames that get a ``unixAccounts`` entry.
2. **Explicit adhoc groups** -- created unconditionally, but each
   ``AdhocSystemAccountEntry`` is admitted only if that username is already in
   the account set.
3. **Global "ncar" group** (gid 1000) -- the account set, verbatim.

WARNING: step 2's gate reproduces ``SystemDirectory.flatLoadDependentAccount()``
and is load-bearing. Without it, a service account listed in an adhoc group but
holding no project membership (``tomcat`` in ``sage``) is emitted as a group
member with no ``unixAccounts`` entry -- a dangling reference for the
downstream LDAP provisioner. "ncar" is injected BEFORE the adhoc stage for the
same reason, so adhoc membership cannot leak into it.

Constants match legacy ``Constants.java``: grace period 90 days, common group
``ncar``, gid 1000.
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from sam.core.groups import DEFAULT_COMMON_GROUP, DEFAULT_COMMON_GROUP_GID


# ---------------------------------------------------------------------------
# Constants (matches legacy Java Constants.java)
# ---------------------------------------------------------------------------

ACCESS_GRACE_PERIOD = 90         # days after allocation end_date
DEFAULT_SHELL = '/bin/tcsh'
DEFAULT_HOME_BASE = '/home'


# ---------------------------------------------------------------------------
# SQL Queries
# ---------------------------------------------------------------------------

# sysAcctGroups equivalent — implicit project groups (no members needed, just existence)
_SQL_PROJECT_GROUPS = text("""
    SELECT ab.name AS access_branch_name,
           LOWER(p.projcode) AS group_name,
           p.unix_gid AS gid
      FROM account AS a
      JOIN project AS p ON (a.project_id = p.project_id AND p.active IS TRUE)
      JOIN resources AS r ON (a.resource_id = r.resource_id AND r.configurable IS TRUE)
      JOIN access_branch_resource AS abr ON r.resource_id = abr.resource_id
      JOIN access_branch AS ab ON abr.access_branch_id = ab.access_branch_id
      JOIN allocation AS al ON (a.account_id = al.account_id
           AND (al.end_date + INTERVAL :grace_period DAY) > NOW())
     WHERE (:branch IS NULL OR ab.name = :branch)
     GROUP BY ab.name, p.projcode, p.unix_gid
""")

# sysAcctMembers equivalent — project group members
_SQL_PROJECT_MEMBERS = text("""
    SELECT ab.name AS access_branch_name,
           LOWER(p.projcode) AS group_name,
           u.username AS username
      FROM account AS a
      JOIN project AS p ON (a.project_id = p.project_id AND p.active IS TRUE)
      JOIN resources AS r ON (a.resource_id = r.resource_id AND r.configurable IS TRUE)
      JOIN access_branch_resource AS abr ON r.resource_id = abr.resource_id
      JOIN access_branch AS ab ON abr.access_branch_id = ab.access_branch_id
      JOIN allocation AS al ON (a.account_id = al.account_id
           AND (al.end_date + INTERVAL :grace_period DAY) > NOW())
      JOIN account_user AS au ON (a.account_id = au.account_id
           AND au.start_date <= NOW()
           AND (au.end_date IS NULL OR au.end_date > NOW()))
      JOIN users AS u ON (au.user_id = u.user_id AND u.active IS TRUE)
     WHERE (:branch IS NULL OR ab.name = :branch)
     GROUP BY ab.name, p.projcode, u.username
""")

# Adhoc groups — active groups with their branch tags
_SQL_ADHOC_GROUPS = text("""
    SELECT ab.name AS access_branch_name,
           ag.group_name AS group_name,
           ag.unix_gid AS gid
      FROM adhoc_group AS ag
      JOIN adhoc_group_tag AS agt ON ag.group_id = agt.group_id
      JOIN access_branch AS ab ON LOWER(ab.name) = LOWER(agt.tag)
     WHERE ag.active IS TRUE
       AND (:branch IS NULL OR LOWER(ab.name) = LOWER(:branch))
     GROUP BY ab.name, ag.group_name, ag.unix_gid
""")

# Adhoc group members via AdhocSystemAccountEntry
_SQL_ADHOC_MEMBERS = text("""
    SELECT ase.access_branch_name AS access_branch_name,
           ag.group_name AS group_name,
           ase.username AS username
      FROM adhoc_system_account_entry AS ase
      JOIN adhoc_group AS ag ON ase.group_id = ag.group_id AND ag.active IS TRUE
     WHERE (:branch IS NULL OR ase.access_branch_name = :branch)
""")

# Account membership + user identity. Split-and-assemble (see module docstring /
# group_populator): the legacy single mega-join fanned each user out ~14x over
# accounts x allocations x phone x institution x organization, then collapsed
# ~135k rows with a temporary-table GROUP BY that dominated the ~7s cost. This
# query keeps ONLY the account-chain membership -> one row per (branch, user);
# phone/institution/organization and the home/shell "key resource" logic are
# fetched as small bulk lookups below and merged in Python. Home/shell were
# pulled OUT of SQL deliberately: joining the key-resource/override tables inside
# this query gave the optimizer a plan that exploded to >1M rows on some
# instances (plan-fragile). DISTINCT here is stable at ~0.2s on every instance.
_SQL_MEMBERSHIP = text("""
    SELECT DISTINCT ab.name AS branch,
           u.user_id AS user_id,
           u.username AS username,
           u.unix_uid AS uid,
           u.primary_gid AS primary_gid,
           u.nickname AS nickname,
           u.first_name AS first_name,
           u.last_name AS last_name,
           u.upid AS upid
      FROM account AS a
      JOIN project AS p ON (a.project_id = p.project_id AND p.active IS TRUE)
      JOIN resources AS r ON (a.resource_id = r.resource_id AND r.configurable IS TRUE)
      JOIN access_branch_resource AS abr ON r.resource_id = abr.resource_id
      JOIN access_branch AS ab ON abr.access_branch_id = ab.access_branch_id
      JOIN allocation AS al ON (a.account_id = al.account_id
           AND (al.end_date + INTERVAL :grace_period DAY) > NOW())
      JOIN account_user AS au ON (a.account_id = au.account_id
           AND au.start_date <= NOW()
           AND (au.end_date IS NULL OR au.end_date > NOW()))
      JOIN users AS u ON (au.user_id = u.user_id AND u.active IS TRUE)
     WHERE (:branch IS NULL OR ab.name = :branch)
""")

# Home/shell "key resource" kludge, decomposed. The branch->key-resource map is a
# JOIN in legacy (hpc-data maps to 3 GLADE rows); an account is emitted only if
# its branch has a key resource, so a branch absent here has no accounts. Legacy
# collapsed the per-key-resource fan-out non-deterministically (row-order
# last-wins); we take MAX() in Python, which reproduces the observed output on
# real data (hpc-data resolves to /home — the GLADE base is not applied; that
# legacy behavior is preserved, not "fixed").
_SQL_KEY_RESOURCE = text("""
    SELECT ab.name AS branch, k.resource_id AS resource_id,
           k.default_home_dir_base AS home_base,
           k.default_resource_shell_id AS shell_default_id
      FROM access_branch AS ab
      JOIN (
          SELECT resource_id, resource_name, default_home_dir_base, default_resource_shell_id
            FROM resources
           UNION
          SELECT resource_id, 'hpc-data', default_home_dir_base, default_resource_shell_id
            FROM resources WHERE resource_name LIKE 'GLADE%'
      ) AS k ON LOWER(ab.name) = LOWER(k.resource_name)
     WHERE (:branch IS NULL OR ab.name = :branch)
""")

_SQL_HOME_OVERRIDES = text("""
    SELECT resource_id, user_id, home_directory FROM user_resource_home
""")

_SQL_SHELL_OVERRIDES = text("""
    SELECT rs.resource_id, urs.user_id, rs.path
      FROM resource_shell AS rs
      JOIN user_resource_shell AS urs ON rs.resource_shell_id = urs.resource_shell_id
""")

_SQL_SHELL_DEFAULTS = text("""
    SELECT resource_shell_id, path FROM resource_shell
""")

# Per-user phone (no account fan-out): UCAR Office preferred, then External
# Office. Legacy fanned duplicate phone rows and kept a non-deterministic
# last-wins; MIN() makes it deterministic and reproduces the legacy choice on
# real data (a "+1 303…" entry sorts before a bare "303-…" duplicate).
_SQL_USER_PHONE = text("""
    SELECT ph.user_id,
           MIN(CASE WHEN pt.phone_type = 'Ucar Office'     THEN TRIM(ph.phone_number) END) AS ucar_phone,
           MIN(CASE WHEN pt.phone_type = 'External Office' THEN TRIM(ph.phone_number) END) AS ext_phone
      FROM phone AS ph
      JOIN phone_type AS pt ON ph.ext_phone_type_id = pt.ext_phone_type_id
     WHERE pt.phone_type IN ('Ucar Office', 'External Office')
     GROUP BY ph.user_id
""")

# Per-user institution / organization (MAX matches the legacy tie-break).
_SQL_USER_INSTITUTION = text("""
    SELECT ui.user_id, MAX(inst.name) AS institution_name
      FROM user_institution AS ui
      JOIN institution AS inst ON ui.institution_id = inst.institution_id
     WHERE ui.end_date IS NULL
     GROUP BY ui.user_id
""")

_SQL_USER_ORGANIZATION = text("""
    SELECT uo.user_id, MAX(org.acronym) AS organization_acronym
      FROM user_organization AS uo
      JOIN organization AS org ON uo.organization_id = org.organization_id
     WHERE uo.end_date IS NULL
     GROUP BY uo.user_id
""")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def group_populator(
    session: Session,
    access_branch: Optional[str] = None,
    grace_period_days: int = ACCESS_GRACE_PERIOD,
) -> Dict[str, Dict]:
    """
    Build the per-access-branch group directory.

    Returns a dict keyed by access_branch_name, each value containing:
        {
            "groups": {
                group_name: {"gid": int, "usernames": set[str]}
            },
            "user_groups": {
                username: [{"group_name": str, "gid": int}, ...]
            }
        }

    The ``user_groups`` key is the symmetric inverse of ``groups``, enabling
    O(1) lookup of all groups a given username belongs to within a branch.

    Includes three group sources (matching legacy pipeline order):
      1. Implicit project groups (projcode-based) — these member rows define
         the branch's account set
      2. Explicit adhoc groups (AdhocGroup + AdhocSystemAccountEntry), whose
         members are admitted only if already in the branch's account set
      3. Global "ncar" group — the branch's account set

    Every username in the returned ``groups`` is therefore guaranteed to have
    a matching entry in :func:`user_populator`'s accounts for the same branch.

    Args:
        session: SQLAlchemy session
        access_branch: Optional branch name filter. None = all branches.
        grace_period_days: Days beyond allocation end_date to remain active.
    """
    params = {'branch': access_branch, 'grace_period': grace_period_days}

    # --- 1. Implicit project groups ---
    branches: Dict[str, Dict] = {}

    # Per-branch account set — the legacy SystemDirectory.accounts equivalent.
    # Only *member* rows contribute (legacy flatLoad(branch, group, username)
    # loads the group membership AND the account); _SQL_PROJECT_GROUPS creates
    # groups without accounts and must not feed this.
    branch_accounts: Dict[str, Set[str]] = {}

    rows = session.execute(_SQL_PROJECT_GROUPS, params).fetchall()
    for branch_name, group_name, gid in rows:
        b = branches.setdefault(branch_name, {'groups': {}})
        b['groups'].setdefault(group_name, {'gid': gid, 'usernames': set()})

    rows = session.execute(_SQL_PROJECT_MEMBERS, params).fetchall()
    for branch_name, group_name, username in rows:
        b = branches.setdefault(branch_name, {'groups': {}})
        grp = b['groups'].setdefault(group_name, {'gid': None, 'usernames': set()})
        grp['usernames'].add(username)
        branch_accounts.setdefault(branch_name, set()).add(username)

    # --- 2. Explicit adhoc groups (tags -> access branch) ---
    rows = session.execute(_SQL_ADHOC_GROUPS, params).fetchall()
    for branch_name, group_name, gid in rows:
        b = branches.setdefault(branch_name, {'groups': {}})
        # Adhoc groups may overlap with project groups; adhoc gid wins if set
        grp = b['groups'].setdefault(group_name, {'gid': gid, 'usernames': set()})
        if gid is not None:
            grp['gid'] = gid

    rows = session.execute(_SQL_ADHOC_MEMBERS, params).fetchall()
    for branch_name, group_name, username in rows:
        # Dependent-account gate — legacy SystemDirectory.flatLoadDependentAccount().
        # adhoc_system_account_entry.username is a bare string with no FK and no
        # active check, so an entry can name a user who has no account on this
        # branch (or none at all). Legacy drops those silently; so do we.
        if username not in branch_accounts.get(branch_name, ()):
            continue
        b = branches.setdefault(branch_name, {'groups': {}})
        grp = b['groups'].setdefault(group_name, {'gid': None, 'usernames': set()})
        grp['usernames'].add(username)

    # --- 3. Global "ncar" group ---
    # Exactly the branch's account set. Legacy injects this *before* the adhoc
    # stage (iterating SystemDirectory.accounts), so branches with adhoc groups
    # but no accounts get no "ncar" group at all.
    for branch_name, accounts in branch_accounts.items():
        if not accounts:
            continue
        ncar_grp = branches[branch_name]['groups'].setdefault(DEFAULT_COMMON_GROUP, {
            'gid': DEFAULT_COMMON_GROUP_GID,
            'usernames': set(),
        })
        ncar_grp['gid'] = DEFAULT_COMMON_GROUP_GID
        ncar_grp['usernames'].update(accounts)

    # --- 4. Symmetric username -> groups index ---
    # Invert the groups dict so callers can quickly look up a user's memberships.
    for branch_name, branch_data in branches.items():
        user_groups: Dict[str, List[Dict]] = {}
        for group_name, grp in branch_data['groups'].items():
            for username in grp['usernames']:
                user_groups.setdefault(username, []).append({
                    'group_name': group_name,
                    'gid': grp['gid'],
                })
        branch_data['user_groups'] = user_groups

    return branches


def user_populator(
    session: Session,
    access_branch: Optional[str] = None,
    grace_period_days: int = ACCESS_GRACE_PERIOD,
) -> Dict[str, Dict]:
    """
    Build the per-access-branch unix account directory.

    Returns a dict keyed by access_branch_name, each value containing:
        {
            "accounts": {
                username: {
                    "uid": int,
                    "gid": int,
                    "home_directory": str,
                    "login_shell": str,
                    "name": str,
                    "upid": int | None,
                    "gecos": str,
                }
            }
        }

    gecos format: "{name},{org},{phone}" where:
      - org = "UCAR/{acronym}" for internal staff, institution name for external, "" if neither
      - phone = UCAR Office phone preferred over External Office, "" if none

    Args:
        session: SQLAlchemy session
        access_branch: Optional branch name filter. None = all branches.
        grace_period_days: Days beyond allocation end_date to remain active.
    """
    params = {'branch': access_branch, 'grace_period': grace_period_days}

    # Per-user attribute lookups (one row per user, no account fan-out).
    phones: Dict[int, Optional[str]] = {}
    for user_id, ucar_phone, ext_phone in session.execute(_SQL_USER_PHONE):
        phones[user_id] = ucar_phone if ucar_phone is not None else ext_phone
    institutions = {uid: name for uid, name in session.execute(_SQL_USER_INSTITUTION)}
    organizations = {uid: acr for uid, acr in session.execute(_SQL_USER_ORGANIZATION)}

    # Home/shell "key resource" inputs (small tables), computed in Python.
    key_resources: Dict[str, List[Tuple]] = {}
    for branch, resource_id, home_base, shell_default_id in session.execute(
            _SQL_KEY_RESOURCE, {'branch': access_branch}):
        key_resources.setdefault(branch, []).append((resource_id, home_base, shell_default_id))
    home_overrides = {(rid, uid): home
                      for rid, uid, home in session.execute(_SQL_HOME_OVERRIDES)}
    shell_overrides = {(rid, uid): path
                       for rid, uid, path in session.execute(_SQL_SHELL_OVERRIDES)}
    shell_defaults = {rsid: path for rsid, path in session.execute(_SQL_SHELL_DEFAULTS)}

    def _home(branch: str, user_id: int, username: str) -> Optional[str]:
        # MAX over the branch's key resources (legacy collapsed the fan-out).
        candidates = []
        for resource_id, home_base, _shell in key_resources.get(branch, ()):
            home = home_overrides.get((resource_id, user_id))
            if home is None:
                home = (f'{home_base}/{username}' if home_base is not None
                        else f'{DEFAULT_HOME_BASE}/{username}')
            candidates.append(home)
        return max(candidates) if candidates else None

    def _shell(branch: str, user_id: int) -> Optional[str]:
        candidates = []
        for resource_id, _home_base, shell_default_id in key_resources.get(branch, ()):
            path = shell_overrides.get((resource_id, user_id))
            if path is None:
                path = shell_defaults.get(shell_default_id) if shell_default_id is not None else None
                if path is None:
                    path = DEFAULT_SHELL
            candidates.append(path)
        return max(candidates) if candidates else None

    branches: Dict[str, Dict] = {}
    for row in session.execute(_SQL_MEMBERSHIP, params):
        # Legacy INNER-joins the key resource, so a branch with none has no accounts.
        if row.branch not in key_resources:
            continue
        b = branches.setdefault(row.branch, {'accounts': {}})

        # name = CONCAT(IFNULL(nickname, first_name), ' ', last_name); MySQL CONCAT
        # yields NULL if any part is NULL, which the legacy code mapped to ''.
        first_part = row.nickname if row.nickname is not None else row.first_name
        if first_part is None or row.last_name is None:
            name = ''
        else:
            name = f'{first_part} {row.last_name}'

        # gecos = "{name},{org},{phone}"; org = UCAR/{acronym} for staff, else
        # institution name for external, else "".
        acronym = organizations.get(row.user_id)
        institution = institutions.get(row.user_id)
        if acronym:
            org = f'UCAR/{acronym}'
        elif institution:
            org = institution
        else:
            org = ''
        phone = phones.get(row.user_id) or ''
        gecos = f'{name},{org},{phone}'

        b['accounts'][row.username] = {
            'uid': row.uid,
            'gid': row.primary_gid if row.primary_gid is not None else DEFAULT_COMMON_GROUP_GID,
            'home_directory': _home(row.branch, row.user_id, row.username),
            'login_shell': _shell(row.branch, row.user_id),
            'name': name,
            'upid': row.upid,
            'gecos': gecos,
        }

    return branches


def build_directory_access_response(
    branch_groups: Dict[str, Dict],
    branch_accounts: Dict[str, Dict],
) -> dict:
    """
    Assemble the final JSON response matching the legacy DirectoryAccess format.

    Args:
        branch_groups: Output of group_populator()
        branch_accounts: Output of user_populator()

    Returns:
        dict with "accessBranchDirectories" list, each entry containing
        "accessBranchName", "unixGroups" list, and "unixAccounts" list.
    """
    all_branches = sorted(set(list(branch_groups.keys()) + list(branch_accounts.keys())))

    directories = []
    for branch_name in all_branches:
        groups_data = branch_groups.get(branch_name, {}).get('groups', {})
        accounts_data = branch_accounts.get(branch_name, {}).get('accounts', {})

        unix_groups = []
        for group_name, grp in sorted(groups_data.items()):
            unix_groups.append({
                'accessBranchName': branch_name,
                'groupName': group_name,
                'gid': grp['gid'],
                'usernames': sorted(grp['usernames']),
            })

        unix_accounts = []
        for username, acct in sorted(accounts_data.items()):
            unix_accounts.append({
                'accessBranchName': branch_name,
                'username': username,
                'uid': acct['uid'],
                'gid': acct['gid'],
                'homeDirectory': acct['home_directory'],
                'loginShell': acct['login_shell'],
                'name': acct['name'],
                'upid': acct['upid'],
                'gecos': acct['gecos'],
            })

        directories.append({
            'accessBranchName': branch_name,
            'unixGroups': unix_groups,
            'unixAccounts': unix_accounts,
        })

    return {'accessBranchDirectories': directories}
