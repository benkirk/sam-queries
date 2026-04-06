"""
Unit tests for directory_access query functions.

Tests group_populator(), user_populator(), and build_directory_access_response()
directly against the database, verifying counts, structure, and key behaviors
like the global "ncar" group injection.
"""

import pytest
from sam.queries.directory_access import (
    group_populator,
    user_populator,
    build_directory_access_response,
    GLOBAL_LDAP_GROUP,
    GLOBAL_LDAP_GROUP_UNIX_GID,
    ACCESS_GRACE_PERIOD,
)


class TestGroupPopulator:
    """Tests for group_populator()."""

    def test_returns_dict_with_groups_key(self, session):
        result = group_populator(session)
        assert isinstance(result, dict)
        for branch_name, branch_data in result.items():
            assert 'groups' in branch_data
            assert isinstance(branch_data['groups'], dict)

    def test_at_least_one_branch(self, session):
        result = group_populator(session)
        assert len(result) >= 1, 'Expected at least one access branch'

    def test_group_structure(self, session):
        result = group_populator(session)
        for branch_name, branch_data in result.items():
            for group_name, grp in branch_data['groups'].items():
                assert 'gid' in grp
                assert 'usernames' in grp
                assert isinstance(grp['usernames'], set)

    def test_group_names_lowercase(self, session):
        result = group_populator(session)
        for branch_name, branch_data in result.items():
            for group_name in branch_data['groups']:
                assert group_name == group_name.lower(), \
                    f'Group name not lowercase: {group_name}'

    def test_ncar_global_group_in_every_branch(self, session):
        result = group_populator(session)
        for branch_name, branch_data in result.items():
            assert GLOBAL_LDAP_GROUP in branch_data['groups'], \
                f"'{GLOBAL_LDAP_GROUP}' group missing from branch '{branch_name}'"

    def test_ncar_global_group_gid(self, session):
        result = group_populator(session)
        for branch_name, branch_data in result.items():
            ncar = branch_data['groups'].get(GLOBAL_LDAP_GROUP)
            if ncar:
                assert ncar['gid'] == GLOBAL_LDAP_GROUP_UNIX_GID, \
                    f"ncar gid wrong in branch {branch_name}: {ncar['gid']}"

    def test_ncar_group_contains_all_branch_usernames(self, session):
        """ncar group should contain every username in the branch."""
        result = group_populator(session)
        for branch_name, branch_data in result.items():
            all_branch_usernames = set()
            for group_name, grp in branch_data['groups'].items():
                if group_name != GLOBAL_LDAP_GROUP:
                    all_branch_usernames.update(grp['usernames'])
            ncar_members = branch_data['groups'].get(GLOBAL_LDAP_GROUP, {}).get('usernames', set())
            # Every non-ncar member should be in ncar
            missing = all_branch_usernames - ncar_members
            assert not missing, \
                f"Branch {branch_name}: {len(missing)} usernames not in ncar group: {list(missing)[:5]}"

    def test_branch_filter(self, session):
        all_result = group_populator(session)
        if not all_result:
            pytest.skip('No branches in test database')
        branch_name = list(all_result.keys())[0]
        filtered = group_populator(session, access_branch=branch_name)
        assert list(filtered.keys()) == [branch_name]

    def test_branch_filter_unknown_branch_returns_empty(self, session):
        result = group_populator(session, access_branch='nonexistent_branch_xyz')
        assert result == {}

    def test_groups_have_members_or_exist(self, session):
        """Groups can have zero members (exist without membership) — just verify no negatives."""
        result = group_populator(session)
        for branch_data in result.values():
            for grp in branch_data['groups'].values():
                assert len(grp['usernames']) >= 0

    def test_user_groups_key_present(self, session):
        """Every branch must have a user_groups dict."""
        result = group_populator(session)
        for branch_name, branch_data in result.items():
            assert 'user_groups' in branch_data, \
                f"user_groups missing from branch {branch_name!r}"
            assert isinstance(branch_data['user_groups'], dict)

    def test_user_groups_contains_ncar(self, session):
        """Every user in a branch must appear in the ncar group via user_groups."""
        result = group_populator(session)
        for branch_name, branch_data in result.items():
            ncar_members = branch_data['groups'].get(GLOBAL_LDAP_GROUP, {}).get('usernames', set())
            for username in ncar_members:
                user_group_names = {
                    entry['group_name']
                    for entry in branch_data['user_groups'].get(username, [])
                }
                assert GLOBAL_LDAP_GROUP in user_group_names, \
                    f"Branch {branch_name!r}: {username!r} not in user_groups ncar entry"

    def test_user_groups_symmetric(self, session):
        """For every group/username pair in groups, user_groups must have the reverse entry."""
        result = group_populator(session)
        for branch_name, branch_data in result.items():
            for group_name, grp in branch_data['groups'].items():
                for username in grp['usernames']:
                    entries = branch_data['user_groups'].get(username, [])
                    matched = any(e['group_name'] == group_name for e in entries)
                    assert matched, (
                        f"Branch {branch_name!r}: user_groups[{username!r}] missing "
                        f"group {group_name!r}"
                    )


class TestUserPopulator:
    """Tests for user_populator()."""

    def test_returns_dict_with_accounts_key(self, session):
        result = user_populator(session)
        assert isinstance(result, dict)
        for branch_name, branch_data in result.items():
            assert 'accounts' in branch_data
            assert isinstance(branch_data['accounts'], dict)

    def test_at_least_one_branch(self, session):
        result = user_populator(session)
        assert len(result) >= 1

    def test_account_structure(self, session):
        result = user_populator(session)
        for branch_name, branch_data in result.items():
            for username, acct in list(branch_data['accounts'].items())[:5]:
                assert 'uid' in acct
                assert 'gid' in acct
                assert 'home_directory' in acct
                assert 'login_shell' in acct
                assert 'name' in acct
                assert 'gecos' in acct

    def test_uid_positive(self, session):
        result = user_populator(session)
        for branch_data in result.values():
            for username, acct in list(branch_data['accounts'].items())[:10]:
                assert isinstance(acct['uid'], int)
                assert acct['uid'] > 0, f'{username} uid={acct["uid"]}'

    def test_gid_has_fallback(self, session):
        """gid should always be set (1000 default when primary_gid is NULL)."""
        result = user_populator(session)
        for branch_data in result.values():
            for username, acct in branch_data['accounts'].items():
                assert acct['gid'] is not None, f'{username} gid is None'
                assert acct['gid'] > 0

    def test_home_directory_contains_username(self, session):
        result = user_populator(session)
        for branch_data in result.values():
            for username, acct in list(branch_data['accounts'].items())[:10]:
                assert username in acct['home_directory'], \
                    f'{username}: home_directory={acct["home_directory"]!r}'

    def test_login_shell_is_absolute_path(self, session):
        result = user_populator(session)
        for branch_data in result.values():
            for username, acct in list(branch_data['accounts'].items())[:10]:
                assert acct['login_shell'].startswith('/'), \
                    f'{username}: login_shell={acct["login_shell"]!r}'

    def test_gecos_has_two_commas(self, session):
        result = user_populator(session)
        for branch_data in result.values():
            for username, acct in list(branch_data['accounts'].items())[:20]:
                gecos = acct['gecos']
                assert gecos.count(',') >= 2, \
                    f'{username}: gecos={gecos!r}'

    def test_branch_filter(self, session):
        all_result = user_populator(session)
        if not all_result:
            pytest.skip('No branches in test database')
        branch_name = list(all_result.keys())[0]
        filtered = user_populator(session, access_branch=branch_name)
        assert list(filtered.keys()) == [branch_name]

    def test_branch_filter_unknown_returns_empty(self, session):
        result = user_populator(session, access_branch='nonexistent_branch_xyz')
        assert result == {}


class TestBuildDirectoryAccessResponse:
    """Tests for build_directory_access_response()."""

    def test_response_structure(self, session):
        groups = group_populator(session)
        accounts = user_populator(session)
        result = build_directory_access_response(groups, accounts)

        assert 'accessBranchDirectories' in result
        assert isinstance(result['accessBranchDirectories'], list)

    def test_each_directory_has_required_keys(self, session):
        groups = group_populator(session)
        accounts = user_populator(session)
        result = build_directory_access_response(groups, accounts)

        for branch in result['accessBranchDirectories']:
            assert 'accessBranchName' in branch
            assert 'unixGroups' in branch
            assert 'unixAccounts' in branch

    def test_unix_group_schema(self, session):
        groups = group_populator(session)
        accounts = user_populator(session)
        result = build_directory_access_response(groups, accounts)

        for branch in result['accessBranchDirectories']:
            for grp in branch['unixGroups'][:5]:
                assert 'accessBranchName' in grp
                assert 'groupName' in grp
                assert 'gid' in grp
                assert 'usernames' in grp
                assert isinstance(grp['usernames'], list)

    def test_unix_account_schema(self, session):
        groups = group_populator(session)
        accounts = user_populator(session)
        result = build_directory_access_response(groups, accounts)

        for branch in result['accessBranchDirectories']:
            for acct in branch['unixAccounts'][:5]:
                assert 'accessBranchName' in acct
                assert 'username' in acct
                assert 'uid' in acct
                assert 'gid' in acct
                assert 'homeDirectory' in acct
                assert 'loginShell' in acct
                assert 'name' in acct
                assert 'upid' in acct
                assert 'gecos' in acct

    def test_branches_sorted_alphabetically(self, session):
        groups = group_populator(session)
        accounts = user_populator(session)
        result = build_directory_access_response(groups, accounts)

        branch_names = [b['accessBranchName'] for b in result['accessBranchDirectories']]
        assert branch_names == sorted(branch_names)

    def test_usernames_sorted_within_group(self, session):
        groups = group_populator(session)
        accounts = user_populator(session)
        result = build_directory_access_response(groups, accounts)

        for branch in result['accessBranchDirectories']:
            for grp in branch['unixGroups'][:10]:
                usernames = grp['usernames']
                assert usernames == sorted(usernames), \
                    f"Usernames not sorted in group {grp['groupName']}: {usernames[:5]}"

    def test_accounts_sorted_by_username(self, session):
        groups = group_populator(session)
        accounts = user_populator(session)
        result = build_directory_access_response(groups, accounts)

        for branch in result['accessBranchDirectories']:
            usernames = [a['username'] for a in branch['unixAccounts']]
            assert usernames == sorted(usernames)

    def test_empty_inputs_return_empty_directories(self):
        result = build_directory_access_response({}, {})
        assert result == {'accessBranchDirectories': []}
