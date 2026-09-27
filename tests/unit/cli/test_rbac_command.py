"""CliRunner tests for `sam-admin rbac`: seed, seed-keys, keys, effective, diff, grant, revoke."""

import json

import pytest

from cli.cmds.admin import cli
from sam.security.samuel_roles import SamuelRole, SamuelRoleGrant

from factories import make_api_credentials


# The seed writes fixed role names into a unique index; see `serial_file_lock`.
@pytest.fixture(autouse=True)
def _one_worker_at_a_time(serial_file_lock):
    with serial_file_lock('samuel_roles_fixed_names'):
        yield


@pytest.fixture(autouse=True)
def _no_config_keys(monkeypatch):
    for k in list(__import__('os').environ):
        if k.startswith('API_KEYS_'):
            monkeypatch.delenv(k)


def test_seed_then_diff_is_clean_and_seed_is_a_no_op(runner, mock_db_session):
    result = runner.invoke(cli, ['rbac', '--seed'])
    assert result.exit_code == 0, result.output
    assert 'Seeded' in result.output
    result = runner.invoke(cli, ['--format', 'json', 'rbac', '--diff'])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload['kind'] == 'rbac_diff'
    assert payload['roles'] == {} and not payload['grants_only_in_db']
    result = runner.invoke(cli, ['rbac', '--seed'])
    assert result.exit_code == 0
    assert 'Already seeded' in result.output


def test_listing_and_effective(runner, mock_db_session):
    runner.invoke(cli, ['rbac', '--seed'])
    result = runner.invoke(cli, ['--format', 'json', 'rbac'])
    assert result.exit_code == 0, result.output
    listing = json.loads(result.output)
    assert {r['name'] for r in listing['roles']} >= {'csg', 'allocation_admin', 'system_admin'}
    csg = next(r for r in listing['roles'] if r['name'] == 'csg')
    assert csg['extends'] == 'allocation_admin'
    assert 'edit_resources' in csg['direct'] and 'view_projects' in csg['effective']

    result = runner.invoke(cli, ['--format', 'json', 'rbac', '--effective', 'sureshm'])
    assert result.exit_code == 0, result.output
    eff = json.loads(result.output)
    assert eff['subject_type'] == 'user' and eff['scoped'].get('WNA')
    assert 'view_projects' in eff['scoped']['WNA'] and eff['unscoped'] == []

    result = runner.invoke(cli, ['rbac', '--effective', 'group:csg'])
    assert result.exit_code == 0, result.output
    assert 'edit_resources' in result.output

    result = runner.invoke(cli, ['rbac', '--effective', 'nobody-here'])
    assert result.exit_code == 1


def test_keys_and_seed_keys(runner, mock_db_session, monkeypatch):
    runner.invoke(cli, ['rbac', '--seed'])
    cred = make_api_credentials(mock_db_session, username='k-test')
    monkeypatch.setenv('API_KEYS_COLLECTOR', '$2b$04$x')
    result = runner.invoke(cli, ['--format', 'json', 'rbac', '--keys'])
    assert result.exit_code == 0, result.output
    keys = json.loads(result.output)
    by_name = {k['name']: k for k in keys['keys']}
    assert by_name['collector']['grants'] == 1        # the default grant
    assert by_name[cred.username]['grants'] == 0
    assert keys['ungranted'] == [cred.username]

    result = runner.invoke(cli, ['rbac', '--seed-keys'])
    assert result.exit_code == 0, result.output
    assert 'granted api_legacy' in result.output
    assert json.loads(runner.invoke(cli, ['--format', 'json', 'rbac', '--keys']).output)['ungranted'] == []
    # A second pass touches nothing.
    result = runner.invoke(cli, ['rbac', '--seed-keys'])
    assert 'kept as is' in result.output and 'granted api_legacy' not in result.output


def test_grant_and_revoke(runner, mock_db_session):
    runner.invoke(cli, ['rbac', '--seed'])
    result = runner.invoke(cli, ['rbac', '--grant', 'group:wna-staff', '--role', 'facility_manager',
                                 '--facility', 'WNA', '--note', 'test'])
    assert result.exit_code == 0, result.output
    row = SamuelRoleGrant.find_active(mock_db_session, subject_type='group', subject_name='wna-staff',
                                      role=SamuelRole.get_by_name(mock_db_session, 'facility_manager'),
                                      facility_name='WNA')
    assert row is not None and row.created_by.startswith('cli:')

    result = runner.invoke(cli, ['rbac', '--revoke', str(row.samuel_role_grant_id)])
    assert result.exit_code == 0, result.output
    assert row.revoked_at is not None

    assert runner.invoke(cli, ['rbac', '--grant', 'user:x', '--role', 'ghost']).exit_code == 1
    assert runner.invoke(cli, ['rbac', '--grant', 'user:x', '--permission', 'no_such']).exit_code == 1
    assert runner.invoke(cli, ['rbac', '--grant', 'user:x']).exit_code == 2
    assert runner.invoke(cli, ['rbac', '--revoke', '999999999']).exit_code == 1


def test_the_last_holder_cannot_be_revoked(runner, mock_db_session):
    runner.invoke(cli, ['rbac', '--seed'])
    ids = [g.samuel_role_grant_id for g in mock_db_session.query(SamuelRoleGrant)
           .filter(SamuelRoleGrant.subject_type == 'user',
                   SamuelRoleGrant.subject_name.in_(('benkirk', 'kyledavis'))).all()]
    assert runner.invoke(cli, ['rbac', '--revoke', str(ids[0])]).exit_code == 0
    result = runner.invoke(cli, ['rbac', '--revoke', str(ids[1])])
    assert result.exit_code == 2
    assert 'nobody' in result.output
