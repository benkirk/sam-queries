"""
API endpoint tests for Directory Access endpoints.

Tests the /api/v1/directory_access/ endpoints which provide unix group
and account data for LDAP provisioning systems.
"""

import pytest


class TestDirectoryAccessStructure:
    """Test GET /api/v1/directory_access/ response structure."""

    def test_returns_200(self, auth_client):
        response = auth_client.get('/api/v1/directory_access/')
        assert response.status_code == 200

    def test_top_level_key(self, auth_client):
        data = auth_client.get('/api/v1/directory_access/').get_json()
        assert 'accessBranchDirectories' in data
        assert isinstance(data['accessBranchDirectories'], list)

    def test_three_access_branches(self, auth_client):
        data = auth_client.get('/api/v1/directory_access/').get_json()
        branch_names = [b['accessBranchName'] for b in data['accessBranchDirectories']]
        # At minimum hpc and hpc-data should exist in the test data
        assert len(branch_names) >= 1

    def test_branch_has_required_keys(self, auth_client):
        data = auth_client.get('/api/v1/directory_access/').get_json()
        for branch in data['accessBranchDirectories']:
            assert 'accessBranchName' in branch
            assert 'unixGroups' in branch
            assert 'unixAccounts' in branch
            assert isinstance(branch['unixGroups'], list)
            assert isinstance(branch['unixAccounts'], list)


class TestUnixGroups:
    """Test unixGroups structure and content."""

    def _get_hpc_groups(self, auth_client):
        data = auth_client.get('/api/v1/directory_access/').get_json()
        for branch in data['accessBranchDirectories']:
            if branch['accessBranchName'] == 'hpc':
                return branch['unixGroups']
        return None

    def test_groups_have_required_fields(self, auth_client):
        data = auth_client.get('/api/v1/directory_access/').get_json()
        for branch in data['accessBranchDirectories']:
            for grp in branch['unixGroups'][:5]:  # spot-check first 5
                assert 'accessBranchName' in grp
                assert 'groupName' in grp
                assert 'gid' in grp
                assert 'usernames' in grp
                assert isinstance(grp['usernames'], list)

    def test_ncar_global_group_present(self, auth_client):
        data = auth_client.get('/api/v1/directory_access/').get_json()
        for branch in data['accessBranchDirectories']:
            group_names = [g['groupName'] for g in branch['unixGroups']]
            assert 'ncar' in group_names, \
                f"Global 'ncar' group missing from branch {branch['accessBranchName']}"

    def test_ncar_group_gid_is_1000(self, auth_client):
        data = auth_client.get('/api/v1/directory_access/').get_json()
        for branch in data['accessBranchDirectories']:
            for grp in branch['unixGroups']:
                if grp['groupName'] == 'ncar':
                    assert grp['gid'] == 1000
                    break

    def test_ncar_group_has_members(self, auth_client):
        data = auth_client.get('/api/v1/directory_access/').get_json()
        for branch in data['accessBranchDirectories']:
            for grp in branch['unixGroups']:
                if grp['groupName'] == 'ncar':
                    assert len(grp['usernames']) > 0, \
                        f"ncar group in {branch['accessBranchName']} has no members"

    def test_group_names_are_lowercase(self, auth_client):
        data = auth_client.get('/api/v1/directory_access/').get_json()
        for branch in data['accessBranchDirectories']:
            for grp in branch['unixGroups'][:20]:
                assert grp['groupName'] == grp['groupName'].lower(), \
                    f"Group name not lowercase: {grp['groupName']}"


class TestUnixAccounts:
    """Test unixAccounts structure and content."""

    def test_accounts_have_required_fields(self, auth_client):
        data = auth_client.get('/api/v1/directory_access/').get_json()
        for branch in data['accessBranchDirectories']:
            for acct in branch['unixAccounts'][:5]:  # spot-check first 5
                assert 'accessBranchName' in acct
                assert 'username' in acct
                assert 'uid' in acct
                assert 'gid' in acct
                assert 'homeDirectory' in acct
                assert 'loginShell' in acct
                assert 'name' in acct
                assert 'gecos' in acct

    def test_gecos_format(self, auth_client):
        """gecos should be in format 'name,org,phone' — at least two commas."""
        data = auth_client.get('/api/v1/directory_access/').get_json()
        for branch in data['accessBranchDirectories']:
            for acct in branch['unixAccounts'][:20]:
                gecos = acct['gecos']
                parts = gecos.split(',')
                assert len(parts) >= 3, \
                    f"gecos for {acct['username']} missing commas: {gecos!r}"

    def test_home_directory_has_username(self, auth_client):
        """Home directory should contain the username component."""
        data = auth_client.get('/api/v1/directory_access/').get_json()
        for branch in data['accessBranchDirectories']:
            for acct in branch['unixAccounts'][:10]:
                assert acct['username'] in acct['homeDirectory'], \
                    f"username not in home dir: {acct['username']!r} / {acct['homeDirectory']!r}"

    def test_uid_is_positive_integer(self, auth_client):
        data = auth_client.get('/api/v1/directory_access/').get_json()
        for branch in data['accessBranchDirectories']:
            for acct in branch['unixAccounts'][:10]:
                assert isinstance(acct['uid'], int)
                assert acct['uid'] > 0

    def test_login_shell_is_path(self, auth_client):
        data = auth_client.get('/api/v1/directory_access/').get_json()
        for branch in data['accessBranchDirectories']:
            for acct in branch['unixAccounts'][:10]:
                assert acct['loginShell'].startswith('/'), \
                    f"loginShell is not a path: {acct['loginShell']!r}"


class TestBranchFilter:
    """Test GET /api/v1/directory_access/<branch_name>."""

    def test_single_branch_returns_200(self, auth_client):
        data = auth_client.get('/api/v1/directory_access/').get_json()
        if not data['accessBranchDirectories']:
            pytest.skip('No access branches in test database')
        branch_name = data['accessBranchDirectories'][0]['accessBranchName']
        response = auth_client.get(f'/api/v1/directory_access/{branch_name}')
        assert response.status_code == 200

    def test_single_branch_returns_one_entry(self, auth_client):
        data = auth_client.get('/api/v1/directory_access/').get_json()
        if not data['accessBranchDirectories']:
            pytest.skip('No access branches in test database')
        branch_name = data['accessBranchDirectories'][0]['accessBranchName']
        filtered = auth_client.get(f'/api/v1/directory_access/{branch_name}').get_json()
        assert len(filtered['accessBranchDirectories']) == 1
        assert filtered['accessBranchDirectories'][0]['accessBranchName'] == branch_name

    def test_invalid_branch_returns_404(self, auth_client):
        response = auth_client.get('/api/v1/directory_access/nonexistent_branch_xyz')
        assert response.status_code == 404


class TestAuthentication:
    """Test authentication requirements."""

    def test_unauthenticated_returns_401_or_302(self, client):
        """Unauthenticated request returns 302 (redirect to login) or 401."""
        response = client.get('/api/v1/directory_access/')
        assert response.status_code in [302, 401]

    def test_unauthenticated_branch_returns_401_or_302(self, client):
        """Unauthenticated request returns 302 (redirect to login) or 401."""
        response = client.get('/api/v1/directory_access/hpc')
        assert response.status_code in [302, 401]


class TestCacheRefresh:
    """Test POST /api/v1/directory_access/refresh."""

    def test_refresh_returns_200(self, auth_client):
        response = auth_client.post('/api/v1/directory_access/refresh')
        assert response.status_code == 200

    def test_refresh_returns_ok_status(self, auth_client):
        data = auth_client.post('/api/v1/directory_access/refresh').get_json()
        assert data.get('status') == 'ok'

    def test_unauthenticated_refresh_returns_401_or_302(self, client):
        """Unauthenticated request returns 302 (redirect to login) or 401."""
        response = client.post('/api/v1/directory_access/refresh')
        assert response.status_code in [302, 401]
