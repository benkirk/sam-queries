"""CliRunner tests for the directory views and apply: `sam-search user|project --directory`,
`sam-admin user --directory-apply [--dry-run]`. A FakeDirectory stands in for the reader."""
import json
from datetime import datetime

import pytest

from cli.cmds.admin import cli as admin
from cli.cmds.search import cli as search
from factories import make_project, make_user
from factories.directory import FakeDirectory, make_group_record, make_person_record
from sam.integration.directory import DirectoryUnavailable, outage


@pytest.fixture
def configured(app, monkeypatch):
    """A configured directory (Flask config and env, whichever the CLI reads) served by a fake."""
    import sam.integration.directory.registry as registry
    for key, value in (('DIRECTORY_PROVIDER', 'fake'), ('DIRECTORY_URL', 'ldap://ldap.example.invalid')):
        monkeypatch.setitem(app.config, key, value)
        monkeypatch.setenv(key, value)
    fake = FakeDirectory()
    monkeypatch.setattr(registry, 'reader_from_environment', lambda: fake)
    return fake


@pytest.fixture
def user(mock_db_session):
    return make_user(mock_db_session, upid=True, first_name='Jane', last_name='Doe', token_type='DUO')


def _seed(fake, user, **overrides):
    values = dict(upid=user.upid, username=user.username, unix_uid=user.unix_uid,
                  first_name='Jane', last_name='Doe', emails=(), phones=(), positions=())
    values.update(overrides)
    return fake.add(make_person_record(**values))


class TestUserDirectory:
    def test_the_key_is_absent_when_unconfigured(self, runner, user):
        result = runner.invoke(search, ['--format', 'json', 'user', user.username, '--directory'])
        assert result.exit_code == 0, result.output
        assert 'directory' not in json.loads(result.stdout)
        rich = runner.invoke(search, ['user', user.username, '--directory'])
        assert rich.exit_code == 0 and 'No directory is configured' in rich.output

    def test_the_view_carries_the_record_and_the_drift(self, runner, configured, user):
        _seed(configured, user, first_name='Someone Else')
        result = runner.invoke(search, ['--format', 'json', 'user', user.username, '--directory'])
        assert result.exit_code == 0, result.output
        view = json.loads(result.stdout)['directory']
        assert (view['found'], view['record']['username'], view['would_apply']) == (True, user.username, 1)
        assert [(r['category'], r['field'], r['disposition']) for r in view['rows']] == [
            ('name', 'first_name', 'would apply')]
        assert configured.calls == [('person', user.upid)]
        rich = runner.invoke(search, ['user', user.username, '--directory'])
        assert rich.exit_code == 0, rich.output
        assert 'Directory drift for' in rich.output and 'Automatic apply on sync: none' in rich.output

    def test_unavailable_and_absent(self, runner, configured, user):
        configured.raise_with = DirectoryUnavailable('timeout')
        result = runner.invoke(search, ['--format', 'json', 'user', user.username, '--directory'])
        view = json.loads(result.stdout)['directory']
        assert (view['available'], view['error'], view['record']) == (False, 'DirectoryUnavailable', None)
        outage.reset()
        configured.raise_with = None
        rich = runner.invoke(search, ['user', user.username, '--directory'])
        assert 'is not in the directory' in rich.output

    def test_the_flag_needs_a_username(self, runner):
        result = runner.invoke(search, ['user', '--search', 'x%', '--directory'])
        assert result.exit_code == 2


class TestProjectDirectory:
    def test_the_group_view_marks_the_lead(self, runner, configured, mock_db_session):
        lead = make_user(mock_db_session, upid=True)
        project = make_project(mock_db_session, lead=lead)
        project.unix_gid = 77_777_701
        mock_db_session.flush()
        configured.add(make_group_record(gid=project.unix_gid, name=project.projcode, tags=(),
                                         member_upids=(lead.upid,), managers=(lead.upid,)))
        result = runner.invoke(search, ['--format', 'json', 'project', project.projcode, '--directory'])
        assert result.exit_code == 0, result.output
        view = json.loads(result.stdout)['directory']
        assert [(m['username'], m['role']) for m in view['managers']] == [(lead.username, 'lead')]
        assert [r['category'] for r in view['rows']] == ['tags']    # SAM's exclude-from-google is absent
        rich = runner.invoke(search, ['project', project.projcode, '--directory'])
        assert rich.exit_code == 0, rich.output
        assert f'managers: {lead.username} (lead)' in rich.output and 'Report only' in rich.output

    def test_absent_names_the_cause(self, runner, configured, mock_db_session):
        project = make_project(mock_db_session, lead=make_user(mock_db_session, upid=True))
        project.unix_gid = 77_777_702
        mock_db_session.flush()
        rich = runner.invoke(search, ['project', project.projcode, '--directory'])
        assert 'is not in the directory' in rich.output and '2026-07-10' in rich.output
        project.creation_time = datetime(2026, 1, 1)
        mock_db_session.flush()
        rich = runner.invoke(search, ['project', project.projcode, '--directory'])
        assert 'is not in the directory' in rich.output and '2026-07-10' not in rich.output


class TestReport:
    def test_the_group_lists_its_verbs(self, runner):
        result = runner.invoke(admin, ['directory'])
        assert 'report' in result.output

    def test_unconfigured_is_an_error(self, runner, mock_db_session):
        result = runner.invoke(admin, ['directory', 'report', '--kind', 'group'])
        assert result.exit_code == 2 and 'No directory is configured' in result.output

    def test_report(self, runner, configured, mock_db_session):
        lead = make_user(mock_db_session, upid=True)
        project = make_project(mock_db_session, lead=lead)
        project.unix_gid = 77_777_703
        mock_db_session.flush()
        configured.add(make_group_record(gid=project.unix_gid, name=project.projcode, tags=(),
                                         member_upids=(lead.upid,), managers=(lead.upid,)))
        result = runner.invoke(admin, ['--format', 'json', 'directory', 'report', '--kind', 'group',
                                       '--limit', '5'])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data['kind'] == 'directory_report' and list(data['kinds']) == ['group']
        assert data['kinds']['group']['counts'] == {'tags': 1} and data['limit'] == 5
        rich = runner.invoke(admin, ['directory', 'report', '--kind', 'group', '--limit', '5'])
        assert rich.exit_code == 0, rich.output
        assert 'Directory report' in rich.output and 'group: 1 read' in rich.output

    def test_unavailable_is_an_error(self, runner, configured, mock_db_session):
        configured.raise_with = DirectoryUnavailable('timeout')
        result = runner.invoke(admin, ['directory', 'report', '--kind', 'group', '--limit', '1'])
        assert result.exit_code == 2 and 'could not be read' in result.output


class TestDirectoryApply:
    def test_json_refuses_the_write(self, runner, configured, user):
        result = runner.invoke(admin, ['--format', 'json', 'user', user.username, '--directory-apply'])
        assert result.exit_code == 2
        assert json.loads(result.stdout)['error'] == 'json_unsupported_for_writes'

    def test_unconfigured_is_an_error(self, runner, user):
        result = runner.invoke(admin, ['user', user.username, '--directory-apply', '--dry-run'])
        assert result.exit_code == 2 and 'No directory is configured' in result.output

    def test_dry_run_decides_and_writes_nothing(self, runner, configured, user, monkeypatch):
        monkeypatch.setenv('DIRECTORY_APPLY_PERSON', '1')
        _seed(configured, user, first_name='Someone Else')
        result = runner.invoke(admin, ['--format', 'json', 'user', user.username,
                                       '--directory-apply', '--dry-run'])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert (data['kind'], data['dry_run'], data['found']) == ('directory_apply', True, True)
        assert [(c['category'], c['field']) for c in data['applied']] == [('name', 'first_name')]
        assert data['fragment'].startswith('dry-run applied=1[name:1]')
        assert user.first_name == 'Jane'

    def test_live_apply_lands_the_name(self, runner, configured, user, monkeypatch):
        monkeypatch.setenv('DIRECTORY_APPLY_PERSON', '1')
        _seed(configured, user, first_name='Someone Else')
        result = runner.invoke(admin, ['user', user.username, '--directory-apply'])
        assert result.exit_code == 0, result.output
        assert 'applied=1[name:1]' in result.output and 'Directory apply for' in result.output
        assert user.first_name == 'Someone Else'

    def test_the_operator_needs_no_lever(self, runner, configured, user):
        _seed(configured, user, first_name='Someone Else', unix_uid=user.unix_uid + 1)
        result = runner.invoke(admin, ['--format', 'json', 'user', user.username,
                                       '--directory-apply', '--dry-run'])
        data = json.loads(result.stdout)
        assert [d['category'] for d in data['applied']] == ['name']
        assert [d['category'] for d in data['withheld']] == ['unix_uid']

    def test_unavailable_is_an_error_and_not_found_is_not_found(self, runner, configured, user):
        configured.raise_with = DirectoryUnavailable('timeout')
        result = runner.invoke(admin, ['user', user.username, '--directory-apply', '--dry-run'])
        assert result.exit_code == 2 and 'could not be read' in result.output
        outage.reset()
        result = runner.invoke(admin, ['--format', 'json', 'user', 'nosuchuser-ever',
                                       '--directory-apply', '--dry-run'])
        assert result.exit_code == 1 and json.loads(result.stdout)['error'] == 'not_found'

    def test_dry_run_needs_a_write_mode(self, runner, user):
        assert runner.invoke(admin, ['user', user.username, '--dry-run']).exit_code == 2
        assert runner.invoke(admin, ['user', user.username, '--directory-apply',
                                     '--restore-deactivation']).exit_code == 2
