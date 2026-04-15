"""
Legacy API Parity Integration Tests

Cross-checks all three new Systems Integration APIs against their live legacy
counterparts at sam.ucar.edu to verify that legacy output is a valid subset of
the new API output.

Required environment variables (tests skip if absent):
    SAM_LEGACY_USER   HTTP Basic Auth username for sam.ucar.edu
    SAM_LEGACY_PASS   HTTP Basic Auth password for sam.ucar.edu

Optional:
    SAM_LEGACY_URL    Base URL of legacy API (default: https://sam.ucar.edu)

Production DB override (eliminates DB-drift as a source of false failures):
    If all three PROD_SAM_DB_* variables are set, this module redirects the
    new API's SAM database connection to the production server for the duration
    of the test run, then restores the original connection afterward.

    PROD_SAM_DB_USERNAME  production SAM DB username
    PROD_SAM_DB_SERVER    production SAM DB hostname
    PROD_SAM_DB_PASSWORD  production SAM DB password

    SAM_DB_REQUIRE_SSL is automatically set to true when using prod credentials.
    No other test module is affected; only this file overrides engine/app fixtures.

Usage:
    export SAM_LEGACY_USER=ssg
    export SAM_LEGACY_PASS=<password>
    # optional: point new API at prod DB to eliminate drift-related failures
    export PROD_SAM_DB_USERNAME=...
    export PROD_SAM_DB_SERVER=...
    export PROD_SAM_DB_PASSWORD=...
    source etc/config_env.sh
    pytest tests/integration/test_legacy_api_parity.py -v -n0

Design notes:
  - All API data fixtures are module-scoped: each endpoint is fetched exactly
    once per test module, not once per test function.
  - The new API can have additional fields/projects (thresholds, Expired/No Account
    statuses, projects not yet in the local DB mirror); this is expected and OK.
  - Counts and set membership are checked with tolerances to accommodate DB mirror
    sync lag between sam.ucar.edu and the local development database.
  - When PROD_SAM_DB_* vars are set the tolerance checks may be tightened in the
    future since drift is no longer a factor.
"""

import os
import pytest
import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LEGACY_BASE = os.environ.get('SAM_LEGACY_URL', 'https://sam.ucar.edu')
_LEGACY_USER = os.environ.get('SAM_LEGACY_USER', '')
_LEGACY_PASS = os.environ.get('SAM_LEGACY_PASS', '')

_LEGACY_DIR_URL     = f'{_LEGACY_BASE}/api/protected/admin/sysacct/directoryaccess'
_LEGACY_GROUP_URL   = f'{_LEGACY_BASE}/api/protected/admin/sysacct/groupstatus/{{branch}}'
_LEGACY_FSTREE_URL  = f'{_LEGACY_BASE}/api/protected/admin/ssg/fairShareTree/v3/{{resource}}'

_PROJECT_BRANCHES = ('hpc', 'hpc-data', 'hpc-dev')

# Production DB override — eliminates DB-drift as a failure source.
# All three must be present; a partial set is ignored.
_PROD_DB_USERNAME = os.environ.get('PROD_SAM_DB_USERNAME', '')
_PROD_DB_SERVER   = os.environ.get('PROD_SAM_DB_SERVER',   '')
_PROD_DB_PASSWORD = os.environ.get('PROD_SAM_DB_PASSWORD', '')
_USE_PROD_DB      = bool(_PROD_DB_USERNAME and _PROD_DB_SERVER and _PROD_DB_PASSWORD)

pytestmark = [pytest.mark.legacy_parity]

# ---------------------------------------------------------------------------
# Tolerance helpers
# ---------------------------------------------------------------------------

def _dates_within_one_day(d1: str, d2: str) -> bool:
    """
    Return True if two ISO date strings (YYYY-MM-DD) are equal or differ by exactly 1 day.

    The legacy Java system rounds allocation end dates to the first day of the following
    month (e.g., "2026-07-01"), while the new API stores the actual last day of the
    allocation period (e.g., "2026-06-30"). A ±1-day difference is therefore expected
    and should not be treated as a mismatch.
    """
    from datetime import date
    try:
        date1 = date.fromisoformat(d1)
        date2 = date.fromisoformat(d2)
        return abs((date1 - date2).days) <= 1
    except (ValueError, TypeError):
        return d1 == d2


def _normalize_gecos(gecos: str) -> str:
    """
    Normalize a gecos string for comparison by stripping whitespace from each
    comma-separated field.  The legacy system strips field-internal whitespace
    while the new implementation may produce leading/trailing spaces in phone
    or org fields.
    """
    return ','.join(part.strip() for part in gecos.split(','))


def _within_tolerance(a: float, b: float, pct: float = 5.0, abs_floor: float = 0.0) -> bool:
    """Return True if a and b are within pct% of each other (or abs_floor if both small)."""
    if a == b == 0:
        return True
    max_val = max(abs(a), abs(b))
    if max_val <= abs_floor:
        return True
    return abs(a - b) / max_val <= pct / 100.0


def _count_missing(small_set: set, large_set: set) -> int:
    """Count items in small_set that are not in large_set."""
    return len(small_set - large_set)


def _assert_subset_with_tolerance(
    small_set: set, large_set: set, label: str, max_missing: int = 10
) -> None:
    """Assert that small_set is a near-subset of large_set (up to max_missing exceptions)."""
    missing = small_set - large_set
    assert len(missing) <= max_missing, (
        f'{label}: {len(missing)} items from legacy not found in new '
        f'(tolerance {max_missing}). Missing: {sorted(missing)[:20]}'
    )


def _count_tolerance(count: int, pct: float = 5.0, floor: int = 10) -> int:
    """Return the allowed difference for a count (floor or pct%, whichever is larger)."""
    return max(floor, int(count * pct / 100.0))


# ---------------------------------------------------------------------------
# Prod-DB redirect fixtures (this module only)
# ---------------------------------------------------------------------------

def _restore_env(key: str, original):
    """Restore an environment variable to its original value (or remove it)."""
    if original is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = original


@pytest.fixture(scope='module', autouse=True)
def _prod_sam_session():
    """
    If PROD_SAM_DB_* vars are all set, redirect sam.session to the production
    SAM database for this module, then restore the original connection afterward.

    autouse=True so it always runs first; engine and app fixtures depend on it
    explicitly to guarantee ordering.
    """
    import sam.session

    if not _USE_PROD_DB:
        yield
        return

    # Save originals
    orig_conn_str  = sam.session.connection_string
    orig_username  = os.environ.get('SAM_DB_USERNAME')
    orig_server    = os.environ.get('SAM_DB_SERVER')
    orig_password  = os.environ.get('SAM_DB_PASSWORD')
    orig_ssl       = os.environ.get('SAM_DB_REQUIRE_SSL')

    from config import SAMConfig

    # Point sam.session and SAMConfig at prod
    os.environ['SAM_DB_USERNAME']    = _PROD_DB_USERNAME
    os.environ['SAM_DB_SERVER']      = _PROD_DB_SERVER
    os.environ['SAM_DB_PASSWORD']    = _PROD_DB_PASSWORD
    os.environ['SAM_DB_REQUIRE_SSL'] = 'true'
    sam.session.init_sam_db_defaults()
    SAMConfig.reload()                  # unfreeze class attrs (e.g. SAM_DB_REQUIRE_SSL)

    yield  # tests run here

    # Restore
    sam.session.connection_string = orig_conn_str
    _restore_env('SAM_DB_USERNAME',    orig_username)
    _restore_env('SAM_DB_SERVER',      orig_server)
    _restore_env('SAM_DB_PASSWORD',    orig_password)
    _restore_env('SAM_DB_REQUIRE_SSL', orig_ssl)
    SAMConfig.reload()                  # re-freeze class attrs to original env values


@pytest.fixture(scope='module')
def engine(_prod_sam_session):
    """
    Module-scoped SAM engine.

    Shadows the session-scoped engine from tests/conftest.py for this module only.
    - Prod path: after _prod_sam_session has redirected sam.session, create_sam_engine()
      connects to the production database (SSL enabled).
    - Local path: falls back to create_test_engine() which uses LOCAL_SAM_DB_* vars
      (127.0.0.1), identical to the conftest session-scoped engine.
    """
    if _USE_PROD_DB:
        from sam.session import create_sam_engine
        eng, _ = create_sam_engine()
    else:
        from fixtures.test_config import create_test_engine
        eng = create_test_engine()
    yield eng
    eng.dispose()


@pytest.fixture(scope='module')
def app(test_databases, worker_db_name, _prod_sam_session):
    """
    Module-scoped Flask app.

    Shadows the session-scoped app from tests/conftest.py for this module only.
    Creates a fresh Flask app *after* _prod_sam_session has (possibly) updated
    sam.session.connection_string, so the app's SQLAlchemy pool targets the
    correct database.
    - Prod path: app connects to the production SAM database.
    - Local path: app connects to the local SAM database (same as session-scoped conftest app).
    """
    import sam.session
    import system_status.session
    from webapp.run import create_app

    if not _USE_PROD_DB:
        # Ensure sam.session is pointing at the local DB (LOCAL_SAM_DB_* vars)
        # before creating the app, matching the conftest session-scoped app exactly.
        sam.session.init_sam_db_defaults()

    # SAMConfig.reload() was already called by _prod_sam_session (or is a no-op
    # in the local path), so cfg.SAM_DB_REQUIRE_SSL is current here.

    os.environ['FLASK_CONFIG'] = 'testing'
    system_status.session.init_status_db_defaults()

    the_app = create_app()
    the_app.config['TESTING'] = True
    the_app.config['WTF_CSRF_ENABLED'] = False

    assert the_app.config['ALLOCATION_USAGE_CACHE_TTL'] == 0, (
        f"TestingConfig not loaded (got ALLOCATION_USAGE_CACHE_TTL="
        f"{the_app.config['ALLOCATION_USAGE_CACHE_TTL']!r}). "
        f"Check FLASK_CONFIG env var selection."
    )

    assert worker_db_name in the_app.config['SQLALCHEMY_BINDS']['system_status'], (
        f"Flask app not using test database '{worker_db_name}': "
        f"{the_app.config['SQLALCHEMY_BINDS']['system_status']}"
    )

    return the_app


# ---------------------------------------------------------------------------
# Module-scoped fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def legacy_creds():
    """Skip the entire module if legacy API credentials are not set."""
    if not _LEGACY_USER or not _LEGACY_PASS:
        pytest.skip(
            'SAM_LEGACY_USER and SAM_LEGACY_PASS environment variables are required '
            'for legacy API parity tests'
        )
    return (_LEGACY_USER, _LEGACY_PASS)


@pytest.fixture(scope='module')
def legacy_http(legacy_creds):
    """requests.Session pre-configured with legacy API Basic Auth."""
    s = requests.Session()
    s.auth = legacy_creds
    s.headers['Accept'] = 'application/json'
    return s


@pytest.fixture(scope='module')
def new_api_client(app, engine):
    """
    Module-scoped authenticated Flask test client.

    Looks up benkirk's user_id from the database once and sets the Flask-Login
    session — same approach as the function-scoped auth_client fixture in
    tests/conftest.py, but long-lived for the whole module.
    """
    from sam.core.users import User
    from sqlalchemy.orm import Session as SASession

    with SASession(engine) as db_session:
        user = User.get_by_username(db_session, 'benkirk')
        assert user is not None, "Test user 'benkirk' not found — needed for new API calls"
        user_id = user.user_id

    test_client = app.test_client()
    with test_client.session_transaction() as flask_sess:
        flask_sess['_user_id'] = str(user_id)
        flask_sess['_fresh'] = True

    return test_client


# --- Directory Access data ------------------------------------------------

@pytest.fixture(scope='module')
def legacy_dir_data(legacy_http):
    """Fetch legacy /sysacct/directoryaccess (all branches) once."""
    resp = legacy_http.get(_LEGACY_DIR_URL, timeout=120)
    assert resp.status_code == 200, f'Legacy directoryaccess returned {resp.status_code}'
    data = resp.json()
    assert 'accessBranchDirectories' in data, 'Legacy directoryaccess missing accessBranchDirectories'
    return data


@pytest.fixture(scope='module')
def new_dir_data(new_api_client):
    """Fetch new /api/v1/directory_access/ (all branches) once."""
    resp = new_api_client.get('/api/v1/directory_access/')
    assert resp.status_code == 200, f'New directory_access returned {resp.status_code}'
    data = resp.get_json()
    assert 'accessBranchDirectories' in data
    return data


def _branch_index(data: dict) -> dict:
    """Index accessBranchDirectories by branch name."""
    return {b['accessBranchName']: b for b in data['accessBranchDirectories']}


# --- Project Access data --------------------------------------------------

@pytest.fixture(scope='module')
def legacy_project_data(legacy_http):
    """Fetch legacy groupstatus for each branch once; return dict {branch: [projects]}."""
    result = {}
    for branch in _PROJECT_BRANCHES:
        url = _LEGACY_GROUP_URL.format(branch=branch)
        resp = legacy_http.get(url, timeout=60)
        assert resp.status_code == 200, f'Legacy groupstatus/{branch} returned {resp.status_code}'
        result[branch] = resp.json()
    return result


@pytest.fixture(scope='module')
def new_project_data(new_api_client):
    """Fetch new /api/v1/project_access/ (all branches) once."""
    resp = new_api_client.get('/api/v1/project_access/')
    assert resp.status_code == 200, f'New project_access returned {resp.status_code}'
    return resp.get_json()


# --- FairShare Tree data --------------------------------------------------

def _collect_resource_names(fstree_data: dict) -> list:
    """Extract unique resource names from a fstree all-resources response."""
    names = set()
    for fac in fstree_data.get('facilities', []):
        for at in fac.get('allocationTypes', []):
            for proj in at.get('projects', []):
                for res in proj.get('resources', []):
                    names.add(res['name'])
    return sorted(names)


@pytest.fixture(scope='module')
def new_fstree_data(new_api_client):
    """Fetch new /api/v1/fstree_access/ (all resources) once."""
    resp = new_api_client.get('/api/v1/fstree_access/')
    assert resp.status_code == 200, f'New fstree_access returned {resp.status_code}'
    data = resp.get_json()
    assert data.get('name') == 'fairShareTree'
    return data


@pytest.fixture(scope='module')
def legacy_fstree_data(legacy_http, new_fstree_data):
    """
    Fetch legacy fairShareTree for every resource found in the new API response.
    Returns dict {resource_name: fstree_data}.
    """
    resource_names = _collect_resource_names(new_fstree_data)
    result = {}
    for resource in resource_names:
        url = _LEGACY_FSTREE_URL.format(resource=requests.utils.quote(resource, safe=''))
        resp = legacy_http.get(url, timeout=60)
        if resp.status_code in (404, 500):
            # 404: resource not in legacy; 500: legacy Java error for retired/inactive
            # resources (e.g., Cheyenne). Skip both silently.
            continue
        assert resp.status_code == 200, f'Legacy fstree/{resource} returned {resp.status_code}'
        data = resp.json()
        if data.get('name') == 'fairShareTree':
            result[resource] = data
    assert result, 'No legacy fstree data fetched — check credentials and legacy URL'
    return result


def _build_fstree_index(fstree_data: dict) -> dict:
    """
    Build a nested index for efficient lookup:
        {facility_name: {alloc_type_name: {project_code: {resource_name: resource_node}}}}
    """
    idx = {}
    for fac in fstree_data.get('facilities', []):
        fname = fac['name']
        idx.setdefault(fname, {})
        for at in fac.get('allocationTypes', []):
            atname = at['name']
            idx[fname].setdefault(atname, {})
            for proj in at.get('projects', []):
                pcode = proj['projectCode']
                idx[fname][atname].setdefault(pcode, {})
                for res in proj.get('resources', []):
                    idx[fname][atname][pcode][res['name']] = res
    return idx


# ===========================================================================
# Test Class 1: Directory Access Parity
# ===========================================================================

class TestDirectoryAccessParity:
    """
    Verify that the new /api/v1/directory_access/ is a superset of the legacy
    /api/protected/admin/sysacct/directoryaccess response.

    Both endpoints share the same response schema:
        {"accessBranchDirectories": [{accessBranchName, unixGroups, unixAccounts}]}
    """

    def test_same_branch_names(self, legacy_dir_data, new_dir_data):
        """Every branch in the legacy response must appear in the new response."""
        legacy_branches = {b['accessBranchName'] for b in legacy_dir_data['accessBranchDirectories']}
        new_branches    = {b['accessBranchName'] for b in new_dir_data['accessBranchDirectories']}
        missing = legacy_branches - new_branches
        assert not missing, f'Branches in legacy but not new: {missing}'

    def test_group_count_comparable(self, legacy_dir_data, new_dir_data):
        """Per-branch unix group count should be within tolerance (DB sync lag)."""
        legacy_idx = _branch_index(legacy_dir_data)
        new_idx    = _branch_index(new_dir_data)
        for branch, legacy_branch in legacy_idx.items():
            if branch not in new_idx:
                continue
            lcount = len(legacy_branch['unixGroups'])
            ncount = len(new_idx[branch]['unixGroups'])
            tol = _count_tolerance(lcount)
            assert abs(lcount - ncount) <= tol, (
                f'{branch}: legacy has {lcount} groups, new has {ncount} '
                f'(tolerance ±{tol})'
            )

    def test_group_names_subset(self, legacy_dir_data, new_dir_data):
        """Every legacy group name must appear in the new response (≤10 missing allowed)."""
        legacy_idx = _branch_index(legacy_dir_data)
        new_idx    = _branch_index(new_dir_data)
        for branch, legacy_branch in legacy_idx.items():
            if branch not in new_idx:
                continue
            legacy_names = {g['groupName'] for g in legacy_branch['unixGroups']}
            new_names    = {g['groupName'] for g in new_idx[branch]['unixGroups']}
            _assert_subset_with_tolerance(
                legacy_names, new_names,
                label=f'{branch} group names', max_missing=10
            )

    def test_group_gids_match(self, legacy_dir_data, new_dir_data):
        """For every group present in both responses, the GID must be identical."""
        legacy_idx = _branch_index(legacy_dir_data)
        new_idx    = _branch_index(new_dir_data)
        mismatches = []
        for branch, legacy_branch in legacy_idx.items():
            if branch not in new_idx:
                continue
            new_groups = {g['groupName']: g['gid'] for g in new_idx[branch]['unixGroups']}
            for grp in legacy_branch['unixGroups']:
                name = grp['groupName']
                if name in new_groups and grp['gid'] != new_groups[name]:
                    mismatches.append(
                        f'{branch}/{name}: legacy GID={grp["gid"]}, new GID={new_groups[name]}'
                    )
        assert not mismatches, f'GID mismatches ({len(mismatches)}):\n' + '\n'.join(mismatches[:20])

    def test_group_members_subset(self, legacy_dir_data, new_dir_data):
        """
        For each group present in both responses, every legacy member username
        must appear in the new group membership (≤5 missing per group allowed).
        """
        legacy_idx = _branch_index(legacy_dir_data)
        new_idx    = _branch_index(new_dir_data)
        failures = []
        for branch, legacy_branch in legacy_idx.items():
            if branch not in new_idx:
                continue
            new_groups = {g['groupName']: set(g['usernames']) for g in new_idx[branch]['unixGroups']}
            for grp in legacy_branch['unixGroups']:
                name = grp['groupName']
                if name not in new_groups:
                    continue
                legacy_members = set(grp['usernames'])
                missing = legacy_members - new_groups[name]
                if len(missing) > 5:
                    failures.append(
                        f'{branch}/{name}: {len(missing)} legacy members missing from new '
                        f'(tolerance 5). Missing: {sorted(missing)[:10]}'
                    )
        assert not failures, f'Group membership subset failures:\n' + '\n'.join(failures[:10])

    def test_account_count_comparable(self, legacy_dir_data, new_dir_data):
        """Per-branch unix account count should be within tolerance."""
        legacy_idx = _branch_index(legacy_dir_data)
        new_idx    = _branch_index(new_dir_data)
        for branch, legacy_branch in legacy_idx.items():
            if branch not in new_idx:
                continue
            lcount = len(legacy_branch['unixAccounts'])
            ncount = len(new_idx[branch]['unixAccounts'])
            tol = _count_tolerance(lcount)
            assert abs(lcount - ncount) <= tol, (
                f'{branch}: legacy has {lcount} accounts, new has {ncount} '
                f'(tolerance ±{tol})'
            )

    def test_account_usernames_subset(self, legacy_dir_data, new_dir_data):
        """Every legacy username must appear in the new response (≤10 missing allowed)."""
        legacy_idx = _branch_index(legacy_dir_data)
        new_idx    = _branch_index(new_dir_data)
        for branch, legacy_branch in legacy_idx.items():
            if branch not in new_idx:
                continue
            legacy_users = {a['username'] for a in legacy_branch['unixAccounts']}
            new_users    = {a['username'] for a in new_idx[branch]['unixAccounts']}
            _assert_subset_with_tolerance(
                legacy_users, new_users,
                label=f'{branch} usernames', max_missing=10
            )

    def test_uid_matches_for_shared_users(self, legacy_dir_data, new_dir_data):
        """For users present in both responses, uid must be identical."""
        legacy_idx = _branch_index(legacy_dir_data)
        new_idx    = _branch_index(new_dir_data)
        mismatches = []
        for branch, legacy_branch in legacy_idx.items():
            if branch not in new_idx:
                continue
            new_users = {a['username']: a for a in new_idx[branch]['unixAccounts']}
            for acct in legacy_branch['unixAccounts']:
                uname = acct['username']
                if uname in new_users and acct['uid'] != new_users[uname]['uid']:
                    mismatches.append(
                        f'{branch}/{uname}: legacy uid={acct["uid"]}, new uid={new_users[uname]["uid"]}'
                    )
        assert not mismatches, f'UID mismatches:\n' + '\n'.join(mismatches[:20])

    def test_gid_matches_for_shared_users(self, legacy_dir_data, new_dir_data):
        """For users present in both responses, primary gid must be identical."""
        legacy_idx = _branch_index(legacy_dir_data)
        new_idx    = _branch_index(new_dir_data)
        mismatches = []
        for branch, legacy_branch in legacy_idx.items():
            if branch not in new_idx:
                continue
            new_users = {a['username']: a for a in new_idx[branch]['unixAccounts']}
            for acct in legacy_branch['unixAccounts']:
                uname = acct['username']
                if uname in new_users and acct['gid'] != new_users[uname]['gid']:
                    mismatches.append(
                        f'{branch}/{uname}: legacy gid={acct["gid"]}, new gid={new_users[uname]["gid"]}'
                    )
        assert not mismatches, f'GID mismatches:\n' + '\n'.join(mismatches[:20])

    def test_home_directory_matches(self, legacy_dir_data, new_dir_data):
        """For users present in both responses, homeDirectory must be identical."""
        legacy_idx = _branch_index(legacy_dir_data)
        new_idx    = _branch_index(new_dir_data)
        mismatches = []
        for branch, legacy_branch in legacy_idx.items():
            if branch not in new_idx:
                continue
            new_users = {a['username']: a for a in new_idx[branch]['unixAccounts']}
            for acct in legacy_branch['unixAccounts']:
                uname = acct['username']
                if uname not in new_users:
                    continue
                if acct['homeDirectory'] != new_users[uname]['homeDirectory']:
                    mismatches.append(
                        f'{branch}/{uname}: legacy={acct["homeDirectory"]!r}, '
                        f'new={new_users[uname]["homeDirectory"]!r}'
                    )
        assert not mismatches, f'homeDirectory mismatches:\n' + '\n'.join(mismatches[:20])

    def test_login_shell_matches(self, legacy_dir_data, new_dir_data):
        """For users present in both responses, loginShell must be identical."""
        legacy_idx = _branch_index(legacy_dir_data)
        new_idx    = _branch_index(new_dir_data)
        mismatches = []
        for branch, legacy_branch in legacy_idx.items():
            if branch not in new_idx:
                continue
            new_users = {a['username']: a for a in new_idx[branch]['unixAccounts']}
            for acct in legacy_branch['unixAccounts']:
                uname = acct['username']
                if uname not in new_users:
                    continue
                if acct['loginShell'] != new_users[uname]['loginShell']:
                    mismatches.append(
                        f'{branch}/{uname}: legacy={acct["loginShell"]!r}, '
                        f'new={new_users[uname]["loginShell"]!r}'
                    )
        assert not mismatches, f'loginShell mismatches:\n' + '\n'.join(mismatches[:20])

    def test_gecos_matches(self, legacy_dir_data, new_dir_data):
        """For users present in both responses, gecos field must be identical."""
        legacy_idx = _branch_index(legacy_dir_data)
        new_idx    = _branch_index(new_dir_data)
        mismatches = []
        for branch, legacy_branch in legacy_idx.items():
            if branch not in new_idx:
                continue
            new_users = {a['username']: a for a in new_idx[branch]['unixAccounts']}
            for acct in legacy_branch['unixAccounts']:
                uname = acct['username']
                if uname not in new_users:
                    continue
                if _normalize_gecos(acct['gecos']) != _normalize_gecos(new_users[uname]['gecos']):
                    mismatches.append(
                        f'{branch}/{uname}: legacy={acct["gecos"]!r}, '
                        f'new={new_users[uname]["gecos"]!r}'
                    )
        assert not mismatches, f'gecos mismatches:\n' + '\n'.join(mismatches[:20])


# ===========================================================================
# Test Class 2: Project Access Parity
# ===========================================================================

class TestProjectAccessParity:
    """
    Verify that the new /api/v1/project_access/ is a superset of the legacy
    /api/protected/admin/sysacct/groupstatus/{branch} responses.

    Key difference: legacy emits only ACTIVE and DEAD; new adds EXPIRING and EXPIRED.
    Status mapping used in comparisons:
        new ACTIVE / EXPIRING / EXPIRED  →  legacy ACTIVE
        new DEAD                         →  legacy DEAD
    """

    _LIVE_STATUSES = frozenset({'ACTIVE', 'EXPIRING', 'EXPIRED'})

    def test_all_branches_covered(self, legacy_project_data, new_project_data):
        """New response must include every branch we queried in legacy."""
        for branch in legacy_project_data:
            assert branch in new_project_data, (
                f'Branch {branch!r} returned by legacy but absent from new project_access'
            )

    def test_project_count_comparable(self, legacy_project_data, new_project_data):
        """Per-branch project count should be within tolerance."""
        for branch, legacy_projects in legacy_project_data.items():
            if branch not in new_project_data:
                continue
            lcount = len(legacy_projects)
            ncount = len(new_project_data[branch])
            tol = _count_tolerance(lcount)
            assert abs(lcount - ncount) <= tol, (
                f'{branch}: legacy has {lcount} projects, new has {ncount} '
                f'(tolerance ±{tol})'
            )

    def test_project_names_subset(self, legacy_project_data, new_project_data):
        """Every legacy groupName must appear in the new response (≤10 missing allowed)."""
        for branch, legacy_projects in legacy_project_data.items():
            if branch not in new_project_data:
                continue
            legacy_names = {p['groupName'] for p in legacy_projects}
            new_names    = {p['groupName'] for p in new_project_data[branch]}
            _assert_subset_with_tolerance(
                legacy_names, new_names,
                label=f'{branch} project names', max_missing=10
            )

    def test_dead_projects_consistent(self, legacy_project_data, new_project_data):
        """
        Projects marked DEAD in the new API should also be DEAD in legacy.
        Allows ≤5 inconsistencies per branch (DB mirror lag).
        """
        for branch, legacy_projects in legacy_project_data.items():
            if branch not in new_project_data:
                continue
            legacy_by_name = {p['groupName']: p for p in legacy_projects}
            inconsistencies = []
            for proj in new_project_data[branch]:
                name = proj['groupName']
                if proj['status'] != 'DEAD':
                    continue
                if name not in legacy_by_name:
                    continue  # new project not in legacy — OK
                legacy_status = legacy_by_name[name].get('status', '')
                if legacy_status != 'DEAD':
                    inconsistencies.append(
                        f'{name}: new=DEAD, legacy={legacy_status!r}'
                    )
            assert len(inconsistencies) <= 5, (
                f'{branch}: {len(inconsistencies)} DEAD projects in new but ACTIVE in legacy '
                f'(tolerance 5):\n' + '\n'.join(inconsistencies[:10])
            )

    def test_active_projects_consistent(self, legacy_project_data, new_project_data):
        """
        Projects marked ACTIVE/EXPIRING/EXPIRED in the new API should be ACTIVE in legacy.
        Allows ≤5 inconsistencies per branch.
        """
        for branch, legacy_projects in legacy_project_data.items():
            if branch not in new_project_data:
                continue
            legacy_by_name = {p['groupName']: p for p in legacy_projects}
            inconsistencies = []
            for proj in new_project_data[branch]:
                name = proj['groupName']
                if proj['status'] not in self._LIVE_STATUSES:
                    continue
                if name not in legacy_by_name:
                    continue  # new project not in legacy — OK
                legacy_status = legacy_by_name[name].get('status', '')
                if legacy_status == 'DEAD':
                    inconsistencies.append(
                        f'{name}: new={proj["status"]}, legacy=DEAD'
                    )
            assert len(inconsistencies) <= 5, (
                f'{branch}: {len(inconsistencies)} live projects in new but DEAD in legacy '
                f'(tolerance 5):\n' + '\n'.join(inconsistencies[:10])
            )

    def test_expiration_dates_match(self, legacy_project_data, new_project_data):
        """
        For matching projects, expiration date must agree within ±1 day.

        The legacy Java system normalizes allocation end dates to the first of the
        following month; the new API stores the actual last day of the allocation
        period.  A 1-day difference is therefore expected and accepted.
        """
        for branch, legacy_projects in legacy_project_data.items():
            if branch not in new_project_data:
                continue
            new_by_name = {p['groupName']: p for p in new_project_data[branch]}
            mismatches = []
            for proj in legacy_projects:
                name = proj['groupName']
                if name not in new_by_name:
                    continue
                legacy_exp = proj.get('expiration')
                new_exp    = new_by_name[name].get('expiration')
                if legacy_exp and new_exp and not _dates_within_one_day(legacy_exp, new_exp):
                    mismatches.append(
                        f'{branch}/{name}: legacy={legacy_exp!r}, new={new_exp!r}'
                    )
            assert not mismatches, f'Expiration date mismatches (>1 day):\n' + '\n'.join(mismatches[:20])

    def test_resource_group_statuses_subset(self, legacy_project_data, new_project_data):
        """
        For matching projects, every legacy resourceGroupStatus entry must appear
        in the new response with the same resource name and end date (±1 day).

        The same ±1-day date tolerance applies here as in test_expiration_dates_match
        (legacy rounds to first of following month; new uses actual last day).
        """
        for branch, legacy_projects in legacy_project_data.items():
            if branch not in new_project_data:
                continue
            new_by_name = {p['groupName']: p for p in new_project_data[branch]}
            failures = []
            for proj in legacy_projects:
                name = proj['groupName']
                if name not in new_by_name:
                    continue
                new_rgs = {
                    r['resourceName']: r['endDate']
                    for r in new_by_name[name].get('resourceGroupStatuses', [])
                }
                for rgs in proj.get('resourceGroupStatuses', []):
                    rname = rgs['resourceName']
                    if rname not in new_rgs:
                        failures.append(f'{branch}/{name}: resource {rname!r} missing from new')
                    elif not _dates_within_one_day(rgs['endDate'], new_rgs[rname]):
                        failures.append(
                            f'{branch}/{name}/{rname}: legacy endDate={rgs["endDate"]!r}, '
                            f'new endDate={new_rgs[rname]!r} (>1 day difference)'
                        )
            assert len(failures) <= 10, (
                f'resourceGroupStatuses mismatches ({len(failures)}):\n'
                + '\n'.join(failures[:20])
            )


# ===========================================================================
# Test Class 3: FairShare Tree Parity
# ===========================================================================

class TestFstreeAccessParity:
    """
    Cross-check the new /api/v1/fstree_access/ against the legacy
    /api/protected/admin/ssg/fairShareTree/v3/{resource} responses.

    Scope difference: the legacy fstree includes historical projects (all accounts,
    including those with expired or inactive allocations) while the new API shows
    only currently-active allocations.  As a result the legacy response has
    significantly MORE projects than the new one — a 3x+ ratio is normal for busy
    resources like Casper or Derecho.

    Correct subset direction: new projects ⊆ legacy projects (new is the filtered
    subset; legacy is the historical archive).

    For projects present in BOTH responses, financial amounts and user rosters are
    compared.  Numeric values (adjustedUsage, balance) are checked within 5%
    tolerance to accommodate DB mirror sync lag.
    """

    def test_facility_names_match(self, legacy_fstree_data, new_fstree_data):
        """
        For each resource, the set of facilities with active projects in the new
        API must also appear in the legacy response for that resource.

        The new all-resources response contains all facilities globally; we filter
        to only facilities that have at least one project with the specific resource
        before comparing against the per-resource legacy response.
        """
        for resource, legacy_data in legacy_fstree_data.items():
            # Collect facility names that have at least one project with this resource in new
            new_fac_for_resource = set()
            for fac in new_fstree_data.get('facilities', []):
                for at in fac.get('allocationTypes', []):
                    for proj in at.get('projects', []):
                        for res in proj.get('resources', []):
                            if res['name'] == resource:
                                new_fac_for_resource.add(fac['name'])
                                break

            legacy_facilities = {f['name'] for f in legacy_data.get('facilities', [])}
            # New (resource-specific) facilities must all exist in legacy for this resource
            _assert_subset_with_tolerance(
                new_fac_for_resource, legacy_facilities,
                label=f'{resource} new→legacy facility names', max_missing=2
            )

    def test_allocation_types_in_legacy(self, legacy_fstree_data, new_fstree_data):
        """
        Every allocation type present in the new API must also appear in the
        legacy response.  (Legacy may have additional empty alloc types whose
        active projects have all expired — that is expected and ignored.)
        """
        # Build legacy alloc-type set per facility (across all resources)
        legacy_at_by_fac: dict = {}
        for resource, legacy_data in legacy_fstree_data.items():
            for fac in legacy_data.get('facilities', []):
                legacy_at_by_fac.setdefault(fac['name'], set())
                for at in fac.get('allocationTypes', []):
                    legacy_at_by_fac[fac['name']].add(at['name'])

        failures = []
        for fac in new_fstree_data.get('facilities', []):
            fname = fac['name']
            legacy_ats = legacy_at_by_fac.get(fname, set())
            for at in fac.get('allocationTypes', []):
                if at['name'] not in legacy_ats:
                    failures.append(f'{fname}: new alloc type {at["name"]!r} not in legacy')

        assert len(failures) <= 3, (
            f'New allocation types missing from legacy ({len(failures)}):\n'
            + '\n'.join(failures[:10])
        )

    def test_new_project_count_le_legacy(self, legacy_fstree_data, new_fstree_data):
        """
        For each resource, the new API project count should be ≤ legacy count
        (new is the active-only subset of the historical legacy tree).
        """
        def _count_projects(data, resource_filter=None):
            count = 0
            for fac in data.get('facilities', []):
                for at in fac.get('allocationTypes', []):
                    for proj in at.get('projects', []):
                        if resource_filter is None:
                            count += 1
                        else:
                            for res in proj.get('resources', []):
                                if res['name'] == resource_filter:
                                    count += 1
                                    break
            return count

        for resource, legacy_data in legacy_fstree_data.items():
            lcount = _count_projects(legacy_data)
            ncount = _count_projects(new_fstree_data, resource_filter=resource)
            # New should have equal or fewer projects (active-only filter)
            # Allow new to exceed legacy by at most 10 (DB mirror lag may add a few)
            assert ncount <= lcount + 10, (
                f'{resource}: new has {ncount} projects, legacy has {lcount} — '
                f'new should not greatly exceed legacy (new is active-only subset)'
            )

    def test_new_project_codes_in_legacy(self, legacy_fstree_data, new_fstree_data):
        """
        Every project code in the new API must also appear in the legacy fstree
        (≤10 missing per resource allowed for DB mirror lag).

        This verifies the new API has not invented spurious project codes.
        """
        # Build a flat set of project codes per resource from legacy
        legacy_codes_by_resource: dict = {}
        for resource, legacy_data in legacy_fstree_data.items():
            codes: set = set()
            for fac in legacy_data.get('facilities', []):
                for at in fac.get('allocationTypes', []):
                    for proj in at.get('projects', []):
                        codes.add(proj['projectCode'])
            legacy_codes_by_resource[resource] = codes

        for fac in new_fstree_data.get('facilities', []):
            for at in fac.get('allocationTypes', []):
                for proj in at.get('projects', []):
                    pcode = proj['projectCode']
                    for res in proj.get('resources', []):
                        rname = res['name']
                        if rname not in legacy_codes_by_resource:
                            continue  # legacy didn't return this resource (e.g., 404/500)
                        if pcode not in legacy_codes_by_resource[rname]:
                            # Count per resource
                            legacy_codes_by_resource.setdefault(f'__missing_{rname}', set()).add(pcode)

        failures = []
        for key, codes in legacy_codes_by_resource.items():
            if key.startswith('__missing_'):
                resource = key[len('__missing_'):]
                if len(codes) > 10:
                    failures.append(
                        f'{resource}: {len(codes)} new project codes not found in legacy '
                        f'(tolerance 10). Sample: {sorted(codes)[:5]}'
                    )

        assert not failures, '\n'.join(failures)

    def test_allocation_amounts_match(self, legacy_fstree_data, new_fstree_data):
        """
        For matching project+resource nodes, allocationAmount must be identical.
        This is static data and should never differ.
        """
        new_idx = _build_fstree_index(new_fstree_data)
        mismatches = []

        for resource, legacy_data in legacy_fstree_data.items():
            for fac in legacy_data.get('facilities', []):
                for at in fac.get('allocationTypes', []):
                    for proj in at.get('projects', []):
                        pcode = proj['projectCode']
                        for res in proj.get('resources', []):
                            rname = res['name']
                            new_res = new_idx.get(fac['name'], {}).get(at['name'], {}).get(pcode, {}).get(rname)
                            if new_res is None:
                                continue
                            legacy_amt = res.get('allocationAmount', 0)
                            new_amt    = new_res.get('allocationAmount', 0)
                            if legacy_amt != new_amt:
                                mismatches.append(
                                    f'{resource}/{pcode}/{rname}: '
                                    f'legacy allocationAmount={legacy_amt}, new={new_amt}'
                                )
        assert not mismatches, (
            f'allocationAmount mismatches ({len(mismatches)}):\n'
            + '\n'.join(mismatches[:20])
        )

    def test_adjusted_usage_within_tolerance(self, legacy_fstree_data, new_fstree_data):
        """
        For matching project+resource nodes, adjustedUsage should be within 5%
        (or ≤500 AU absolute for small allocations). Differences are expected
        due to DB mirror sync lag — charges accumulate daily.

        Up to 2% of matched nodes are allowed to be out of tolerance; a much higher
        failure rate would indicate a systematic problem rather than normal lag.
        """
        new_idx = _build_fstree_index(new_fstree_data)
        failures = []
        compared = 0

        for resource, legacy_data in legacy_fstree_data.items():
            for fac in legacy_data.get('facilities', []):
                for at in fac.get('allocationTypes', []):
                    for proj in at.get('projects', []):
                        pcode = proj['projectCode']
                        for res in proj.get('resources', []):
                            rname = res['name']
                            new_res = new_idx.get(fac['name'], {}).get(at['name'], {}).get(pcode, {}).get(rname)
                            if new_res is None:
                                continue
                            compared += 1
                            legacy_usage = res.get('adjustedUsage', 0)
                            new_usage    = new_res.get('adjustedUsage', 0)
                            if not _within_tolerance(legacy_usage, new_usage, pct=5.0, abs_floor=500):
                                failures.append(
                                    f'{resource}/{pcode}/{rname}: '
                                    f'legacy adjustedUsage={legacy_usage}, new={new_usage}'
                                )

        # Allow up to 2% of compared nodes (floor: 10) to differ — DB mirror lag
        max_failures = max(10, int(compared * 0.02))
        assert len(failures) <= max_failures, (
            f'adjustedUsage out of 5% tolerance for {len(failures)}/{compared} matched nodes '
            f'(limit {max_failures} = 2%). High failure rate suggests excessive DB mirror lag:\n'
            + '\n'.join(failures[:20])
        )

    def test_balance_consistent(self, legacy_fstree_data, new_fstree_data):
        """
        In the new API, balance must equal allocationAmount − adjustedUsage.
        Also checks that legacy balance is consistent with its own fields.
        """
        new_idx = _build_fstree_index(new_fstree_data)
        failures = []

        # Check internal consistency of new API
        for fac in new_fstree_data.get('facilities', []):
            for at in fac.get('allocationTypes', []):
                for proj in at.get('projects', []):
                    for res in proj.get('resources', []):
                        amt     = res.get('allocationAmount', 0)
                        usage   = res.get('adjustedUsage', 0)
                        balance = res.get('balance', 0)
                        expected = amt - usage
                        if abs(balance - expected) > 1:  # allow 1 AU rounding
                            failures.append(
                                f'new/{proj["projectCode"]}/{res["name"]}: '
                                f'balance={balance}, allocationAmount-adjustedUsage={expected}'
                            )

        # Check internal consistency of each legacy resource response
        for resource, legacy_data in legacy_fstree_data.items():
            for fac in legacy_data.get('facilities', []):
                for at in fac.get('allocationTypes', []):
                    for proj in at.get('projects', []):
                        for res in proj.get('resources', []):
                            amt     = res.get('allocationAmount', 0)
                            usage   = res.get('adjustedUsage', 0)
                            balance = res.get('balance', 0)
                            expected = amt - usage
                            if abs(balance - expected) > 1:
                                failures.append(
                                    f'legacy/{resource}/{proj["projectCode"]}/{res["name"]}: '
                                    f'balance={balance}, allocationAmount-adjustedUsage={expected}'
                                )

        assert len(failures) <= 5, (
            f'balance inconsistencies ({len(failures)}):\n' + '\n'.join(failures[:20])
        )

    def test_users_subset(self, legacy_fstree_data, new_fstree_data):
        """
        For each project+resource in both responses, every legacy username must
        appear in the new users list (≤3 missing per project+resource allowed).
        """
        new_idx = _build_fstree_index(new_fstree_data)
        failures = []

        for resource, legacy_data in legacy_fstree_data.items():
            for fac in legacy_data.get('facilities', []):
                for at in fac.get('allocationTypes', []):
                    for proj in at.get('projects', []):
                        pcode = proj['projectCode']
                        for res in proj.get('resources', []):
                            rname = res['name']
                            new_res = new_idx.get(fac['name'], {}).get(at['name'], {}).get(pcode, {}).get(rname)
                            if new_res is None:
                                continue
                            legacy_users = {u['username'] for u in res.get('users', [])}
                            new_users    = {u['username'] for u in new_res.get('users', [])}
                            missing = legacy_users - new_users
                            if len(missing) > 3:
                                failures.append(
                                    f'{resource}/{pcode}/{rname}: '
                                    f'{len(missing)} legacy users missing from new '
                                    f'(tolerance 3): {sorted(missing)[:5]}'
                                )
        assert len(failures) <= 10, (
            f'User subset failures across {len(failures)} project+resource nodes:\n'
            + '\n'.join(failures[:20])
        )

    def test_account_status_comparable(self, legacy_fstree_data, new_fstree_data):
        """
        For matching project+resource nodes, account status should be broadly
        consistent: if legacy is non-Normal, new should also be non-Normal.
        New may have additional non-Normal values (Expired, No Account) that
        legacy does not.
        """
        new_idx = _build_fstree_index(new_fstree_data)
        inconsistencies = []

        for resource, legacy_data in legacy_fstree_data.items():
            for fac in legacy_data.get('facilities', []):
                for at in fac.get('allocationTypes', []):
                    for proj in at.get('projects', []):
                        pcode = proj['projectCode']
                        for res in proj.get('resources', []):
                            rname = res['name']
                            new_res = new_idx.get(fac['name'], {}).get(at['name'], {}).get(pcode, {}).get(rname)
                            if new_res is None:
                                continue
                            legacy_status = res.get('accountStatus', 'Normal')
                            new_status    = new_res.get('accountStatus', 'Normal')
                            # If legacy says non-Normal, new should also be non-Normal
                            if legacy_status != 'Normal' and new_status == 'Normal':
                                inconsistencies.append(
                                    f'{resource}/{pcode}/{rname}: '
                                    f'legacy={legacy_status!r}, new=Normal'
                                )

        assert len(inconsistencies) <= 5, (
            f'accountStatus inconsistencies ({len(inconsistencies)}): '
            f'legacy non-Normal but new Normal:\n'
            + '\n'.join(inconsistencies[:20])
        )
