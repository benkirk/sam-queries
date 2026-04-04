"""
Directory Access query functions for SAM.

Provides group_populator() and user_populator() that reproduce the output of
the legacy Java `GET /protected/admin/sysacct/directoryaccess` endpoint.

The data is organized by access branch (hpc, hpc-data, hpc-dev) and includes:
  - unixGroups: project-based groups + explicit adhoc groups + global "ncar" group
  - unixAccounts: per-user details (uid, gid, home dir, shell, gecos)

Group sources (in order of the legacy pipeline):
  1. Implicit project groups: active projects with allocations within grace period,
     linked to access branches via account → resource → access_branch_resource
  2. Explicit adhoc groups: AdhocGroup entries whose tags match access branch names
  3. Global "ncar" group (gid=1000): every user per branch is injected as a member

Constants matching legacy Java Constants.java:
  ACCESS_GRACE_PERIOD = 90  days
  GLOBAL_LDAP_GROUP  = 'ncar'
  GLOBAL_LDAP_GROUP_UNIX_GID = 1000
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Constants (matches legacy Java Constants.java)
# ---------------------------------------------------------------------------

ACCESS_GRACE_PERIOD = 90         # days after allocation end_date
GLOBAL_LDAP_GROUP = 'ncar'
GLOBAL_LDAP_GROUP_UNIX_GID = 1000
DEFAULT_GID = 1000               # fallback when users.primary_gid is NULL
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

# Unix account data — mirrors legacy unixAccountForAccessBranchNameAndUsername
# The hpc-data branch uses GLADE* resource attributes (legacy kludge)
_SQL_UNIX_ACCOUNTS = text("""
    SELECT ab.name AS access_branch_name,
           u.username AS username,
           u.unix_uid AS uid,
           IFNULL(u.primary_gid, :default_gid) AS gid,
           IFNULL(urh.home_directory,
               CASE WHEN k.default_home_dir_base IS NULL
                    THEN CONCAT(:default_home_base, '/', u.username)
                    ELSE CONCAT(k.default_home_dir_base, '/', u.username)
               END
           ) AS home_directory,
           IFNULL(urs.path, IFNULL(drs.path, :default_shell)) AS login_shell,
           CONCAT(IFNULL(u.nickname, u.first_name), ' ', u.last_name) AS name,
           u.upid AS upid,
           CASE
               WHEN uoph.phone_number IS NOT NULL THEN uoph.phone_number
               ELSE eoph.phone_number
           END AS phone_number,
           MAX(inst.name) AS institution_name,
           MAX(org.acronym) AS organization_acronym
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
      -- "key resource" kludge: hpc-data uses GLADE resource for home/shell defaults
      JOIN (
          SELECT kr1.resource_id, kr1.resource_name, kr1.default_home_dir_base,
                 kr1.default_resource_shell_id
            FROM resources AS kr1
           UNION
          SELECT kr2.resource_id, 'hpc-data' AS resource_name,
                 kr2.default_home_dir_base, kr2.default_resource_shell_id
            FROM resources AS kr2
           WHERE kr2.resource_name LIKE 'GLADE%'
      ) AS k ON LOWER(ab.name) = LOWER(k.resource_name)
      LEFT JOIN user_resource_home AS urh ON (k.resource_id = urh.resource_id
           AND u.user_id = urh.user_id)
      LEFT JOIN (
          SELECT ilrs.resource_id, ilrs.path, ilurs.user_id
            FROM resource_shell AS ilrs
            JOIN user_resource_shell AS ilurs ON ilrs.resource_shell_id = ilurs.resource_shell_id
      ) AS urs ON (k.resource_id = urs.resource_id AND u.user_id = urs.user_id)
      LEFT JOIN resource_shell AS drs ON k.default_resource_shell_id = drs.resource_shell_id
      -- Phone numbers: UCAR Office preferred, then External Office
      LEFT JOIN phone_type AS uopht ON uopht.phone_type = 'Ucar Office'
      LEFT JOIN phone_type AS eopht ON eopht.phone_type = 'External Office'
      LEFT JOIN phone AS uoph ON (u.user_id = uoph.user_id
           AND uopht.ext_phone_type_id = uoph.ext_phone_type_id)
      LEFT JOIN phone AS eoph ON (u.user_id = eoph.user_id
           AND eopht.ext_phone_type_id = eoph.ext_phone_type_id)
      -- Institution (for external users)
      LEFT JOIN user_institution AS ui ON (u.user_id = ui.user_id AND ui.end_date IS NULL)
      LEFT JOIN institution AS inst ON ui.institution_id = inst.institution_id
      -- Organization (for UCAR staff)
      LEFT JOIN user_organization AS uo ON (u.user_id = uo.user_id AND uo.end_date IS NULL)
      LEFT JOIN organization AS org ON uo.organization_id = org.organization_id
     WHERE (:branch IS NULL OR ab.name = :branch)
     GROUP BY ab.name, u.username, u.unix_uid, u.primary_gid,
              urh.home_directory, k.default_home_dir_base,
              urs.path, drs.path, u.nickname, u.first_name, u.last_name,
              u.upid, uoph.phone_number, eoph.phone_number
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
            }
        }

    Includes three group sources (matching legacy pipeline order):
      1. Implicit project groups (projcode-based)
      2. Explicit adhoc groups (AdhocGroup + AdhocSystemAccountEntry)
      3. Global "ncar" group (every user per branch)

    Args:
        session: SQLAlchemy session
        access_branch: Optional branch name filter. None = all branches.
        grace_period_days: Days beyond allocation end_date to remain active.
    """
    params = {'branch': access_branch, 'grace_period': grace_period_days}

    # --- 1. Implicit project groups ---
    branches: Dict[str, Dict] = {}

    rows = session.execute(_SQL_PROJECT_GROUPS, params).fetchall()
    for branch_name, group_name, gid in rows:
        b = branches.setdefault(branch_name, {'groups': {}})
        b['groups'].setdefault(group_name, {'gid': gid, 'usernames': set()})

    rows = session.execute(_SQL_PROJECT_MEMBERS, params).fetchall()
    for branch_name, group_name, username in rows:
        b = branches.setdefault(branch_name, {'groups': {}})
        grp = b['groups'].setdefault(group_name, {'gid': None, 'usernames': set()})
        grp['usernames'].add(username)

    # --- 2. Explicit adhoc groups (tags → access branch) ---
    rows = session.execute(_SQL_ADHOC_GROUPS, params).fetchall()
    for branch_name, group_name, gid in rows:
        b = branches.setdefault(branch_name, {'groups': {}})
        # Adhoc groups may overlap with project groups; adhoc gid wins if set
        grp = b['groups'].setdefault(group_name, {'gid': gid, 'usernames': set()})
        if gid is not None:
            grp['gid'] = gid

    rows = session.execute(_SQL_ADHOC_MEMBERS, params).fetchall()
    for branch_name, group_name, username in rows:
        b = branches.setdefault(branch_name, {'groups': {}})
        grp = b['groups'].setdefault(group_name, {'gid': None, 'usernames': set()})
        grp['usernames'].add(username)

    # --- 3. Global "ncar" group ---
    # Collect all usernames already in this branch (from project members + adhoc members)
    for branch_name, branch_data in branches.items():
        all_branch_usernames: Set[str] = set()
        for grp_data in branch_data['groups'].values():
            all_branch_usernames.update(grp_data['usernames'])

        ncar_grp = branch_data['groups'].setdefault(GLOBAL_LDAP_GROUP, {
            'gid': GLOBAL_LDAP_GROUP_UNIX_GID,
            'usernames': set(),
        })
        ncar_grp['gid'] = GLOBAL_LDAP_GROUP_UNIX_GID
        ncar_grp['usernames'].update(all_branch_usernames)

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
    params = {
        'branch': access_branch,
        'grace_period': grace_period_days,
        'default_gid': DEFAULT_GID,
        'default_shell': DEFAULT_SHELL,
        'default_home_base': DEFAULT_HOME_BASE,
    }

    branches: Dict[str, Dict] = {}
    rows = session.execute(_SQL_UNIX_ACCOUNTS, params).fetchall()

    for row in rows:
        branch_name = row.access_branch_name
        username = row.username

        b = branches.setdefault(branch_name, {'accounts': {}})

        # Build gecos field
        name = row.name or ''
        org = ''
        if row.organization_acronym:
            org = f'UCAR/{row.organization_acronym}'
        elif row.institution_name:
            org = row.institution_name
        phone = row.phone_number or ''
        gecos = f'{name},{org},{phone}'

        b['accounts'][username] = {
            'uid': row.uid,
            'gid': row.gid,
            'home_directory': row.home_directory,
            'login_shell': row.login_shell,
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
