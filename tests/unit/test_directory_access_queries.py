"""Unit tests for sam.queries.directory_access.

Ported verbatim from tests/unit/test_directory_access_queries.py. Structural
tests only — no hardcoded identifiers, no writes. The one `.update()` call
at line 70 is a `set.update()` (not ORM), so this entire file is pure-read.
"""
import pytest

from sam.queries.directory_access import (
    GLOBAL_LDAP_GROUP,
    GLOBAL_LDAP_GROUP_UNIX_GID,
    build_directory_access_response,
    group_populator,
    user_populator,
)


pytestmark = pytest.mark.unit


# ============================================================================
# group_populator()
# ============================================================================


class TestGroupPopulator:

    def test_returns_dict_with_groups_key(self, session):
        result = group_populator(session)
        assert isinstance(result, dict)
        for _branch_name, branch_data in result.items():
            assert 'groups' in branch_data
            assert isinstance(branch_data['groups'], dict)

    def test_at_least_one_branch(self, session):
        assert len(group_populator(session)) >= 1

    def test_group_structure(self, session):
        result = group_populator(session)
        for _branch_name, branch_data in result.items():
            for _group_name, grp in branch_data['groups'].items():
                assert 'gid' in grp
                assert 'usernames' in grp
                assert isinstance(grp['usernames'], set)

    def test_group_names_lowercase(self, session):
        result = group_populator(session)
        for _branch, branch_data in result.items():
            for group_name in branch_data['groups']:
                assert group_name == group_name.lower()

    def test_ncar_global_group_in_every_branch(self, session):
        result = group_populator(session)
        for branch_name, branch_data in result.items():
            assert GLOBAL_LDAP_GROUP in branch_data['groups'], (
                f"{GLOBAL_LDAP_GROUP!r} missing from branch {branch_name!r}"
            )

    def test_ncar_global_group_gid(self, session):
        result = group_populator(session)
        for _branch, branch_data in result.items():
            ncar = branch_data['groups'].get(GLOBAL_LDAP_GROUP)
            if ncar:
                assert ncar['gid'] == GLOBAL_LDAP_GROUP_UNIX_GID

    def test_ncar_group_contains_all_branch_usernames(self, session):
        """The ncar group should contain every username in the branch."""
        result = group_populator(session)
        for branch_name, branch_data in result.items():
            all_branch_usernames = set()
            for group_name, grp in branch_data['groups'].items():
                if group_name != GLOBAL_LDAP_GROUP:
                    all_branch_usernames.update(grp['usernames'])
            ncar_members = branch_data['groups'].get(GLOBAL_LDAP_GROUP, {}).get('usernames', set())
            missing = all_branch_usernames - ncar_members
            assert not missing, (
                f"Branch {branch_name}: {len(missing)} usernames missing from ncar: "
                f"{list(missing)[:5]}"
            )

    def test_branch_filter(self, session):
        all_result = group_populator(session)
        if not all_result:
            pytest.skip("No branches in test database")
        branch_name = list(all_result.keys())[0]
        filtered = group_populator(session, access_branch=branch_name)
        assert list(filtered.keys()) == [branch_name]

    def test_branch_filter_unknown_branch_returns_empty(self, session):
        assert group_populator(session, access_branch='nonexistent_branch_xyz') == {}

    def test_groups_nonnegative_membership(self, session):
        """Groups can have zero members; we just verify no negative counts."""
        result = group_populator(session)
        for branch_data in result.values():
            for grp in branch_data['groups'].values():
                assert len(grp['usernames']) >= 0

    def test_user_groups_key_present(self, session):
        """Every branch must have a user_groups dict."""
        result = group_populator(session)
        for branch_name, branch_data in result.items():
            assert 'user_groups' in branch_data, (
                f"user_groups missing from branch {branch_name!r}"
            )
            assert isinstance(branch_data['user_groups'], dict)

    def test_user_groups_contains_ncar(self, session):
        """Every user in the branch must appear in ncar via user_groups."""
        result = group_populator(session)
        for branch_name, branch_data in result.items():
            ncar_members = branch_data['groups'].get(GLOBAL_LDAP_GROUP, {}).get('usernames', set())
            for username in ncar_members:
                user_group_names = {
                    entry['group_name']
                    for entry in branch_data['user_groups'].get(username, [])
                }
                assert GLOBAL_LDAP_GROUP in user_group_names, (
                    f"Branch {branch_name}: {username!r} not in user_groups ncar entry"
                )

    def test_user_groups_symmetric(self, session):
        """For every (group, username), user_groups must have the reverse entry."""
        result = group_populator(session)
        for branch_name, branch_data in result.items():
            for group_name, grp in branch_data['groups'].items():
                for username in grp['usernames']:
                    entries = branch_data['user_groups'].get(username, [])
                    assert any(e['group_name'] == group_name for e in entries), (
                        f"Branch {branch_name}: user_groups[{username!r}] missing "
                        f"group {group_name!r}"
                    )


# ============================================================================
# user_populator()
# ============================================================================


class TestUserPopulator:

    def test_returns_dict_with_accounts_key(self, session):
        result = user_populator(session)
        assert isinstance(result, dict)
        for _branch_name, branch_data in result.items():
            assert 'accounts' in branch_data
            assert isinstance(branch_data['accounts'], dict)

    def test_at_least_one_branch(self, session):
        assert len(user_populator(session)) >= 1

    def test_account_structure(self, session):
        result = user_populator(session)
        for _branch_name, branch_data in result.items():
            for _username, acct in list(branch_data['accounts'].items())[:5]:
                for field in ('uid', 'gid', 'home_directory', 'login_shell', 'name', 'gecos'):
                    assert field in acct

    def test_uid_positive(self, session):
        result = user_populator(session)
        for branch_data in result.values():
            for username, acct in list(branch_data['accounts'].items())[:10]:
                assert isinstance(acct['uid'], int)
                assert acct['uid'] > 0, f"{username} uid={acct['uid']}"

    def test_gid_has_fallback(self, session):
        """gid should always be set (1000 default when primary_gid is NULL)."""
        result = user_populator(session)
        for branch_data in result.values():
            for username, acct in branch_data['accounts'].items():
                assert acct['gid'] is not None, f"{username} gid is None"
                assert acct['gid'] > 0

    def test_home_directory_contains_username(self, session):
        result = user_populator(session)
        for branch_data in result.values():
            for username, acct in list(branch_data['accounts'].items())[:10]:
                assert username in acct['home_directory'], (
                    f"{username}: home_directory={acct['home_directory']!r}"
                )

    def test_login_shell_is_absolute_path(self, session):
        result = user_populator(session)
        for branch_data in result.values():
            for username, acct in list(branch_data['accounts'].items())[:10]:
                assert acct['login_shell'].startswith('/'), (
                    f"{username}: login_shell={acct['login_shell']!r}"
                )

    def test_gecos_has_two_commas(self, session):
        result = user_populator(session)
        for branch_data in result.values():
            for username, acct in list(branch_data['accounts'].items())[:20]:
                gecos = acct['gecos']
                assert gecos.count(',') >= 2, f"{username}: gecos={gecos!r}"

    def test_branch_filter(self, session):
        all_result = user_populator(session)
        if not all_result:
            pytest.skip("No branches in test database")
        branch_name = list(all_result.keys())[0]
        filtered = user_populator(session, access_branch=branch_name)
        assert list(filtered.keys()) == [branch_name]

    def test_branch_filter_unknown_returns_empty(self, session):
        assert user_populator(session, access_branch='nonexistent_branch_xyz') == {}


# ============================================================================
# build_directory_access_response()
# ============================================================================


class TestBuildDirectoryAccessResponse:

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
            for field in ('accessBranchName', 'unixGroups', 'unixAccounts'):
                assert field in branch

    def test_unix_group_schema(self, session):
        groups = group_populator(session)
        accounts = user_populator(session)
        result = build_directory_access_response(groups, accounts)
        for branch in result['accessBranchDirectories']:
            for grp in branch['unixGroups'][:5]:
                for field in ('accessBranchName', 'groupName', 'gid', 'usernames'):
                    assert field in grp
                assert isinstance(grp['usernames'], list)

    def test_unix_account_schema(self, session):
        groups = group_populator(session)
        accounts = user_populator(session)
        result = build_directory_access_response(groups, accounts)
        for branch in result['accessBranchDirectories']:
            for acct in branch['unixAccounts'][:5]:
                for field in ('accessBranchName', 'username', 'uid', 'gid',
                              'homeDirectory', 'loginShell', 'name', 'upid', 'gecos'):
                    assert field in acct

    def test_branches_sorted_alphabetically(self, session):
        groups = group_populator(session)
        accounts = user_populator(session)
        result = build_directory_access_response(groups, accounts)
        names = [b['accessBranchName'] for b in result['accessBranchDirectories']]
        assert names == sorted(names)

    def test_usernames_sorted_within_group(self, session):
        groups = group_populator(session)
        accounts = user_populator(session)
        result = build_directory_access_response(groups, accounts)
        for branch in result['accessBranchDirectories']:
            for grp in branch['unixGroups'][:10]:
                assert grp['usernames'] == sorted(grp['usernames']), (
                    f"Usernames not sorted in group {grp['groupName']}"
                )

    def test_accounts_sorted_by_username(self, session):
        groups = group_populator(session)
        accounts = user_populator(session)
        result = build_directory_access_response(groups, accounts)
        for branch in result['accessBranchDirectories']:
            usernames = [a['username'] for a in branch['unixAccounts']]
            assert usernames == sorted(usernames)

    def test_empty_inputs_return_empty_directories(self):
        assert build_directory_access_response({}, {}) == {'accessBranchDirectories': []}
