"""CliRunner tests for `sam-admin project` --validate / --reconcile / --reconcile-lead-admin / --all."""
import json
from datetime import datetime, timedelta

import pytest

from cli.cmds.admin import cli
from cli.project.commands import ProjectReconcileCommand
from sam.accounting.accounts import AccountUser

from factories import make_account, make_allocation, make_project, make_user


# `_project_with_rowless_lead` deletes account_user rows by user_id, a next-key
# lock at the top of that index that another worker's membership inserts wait
# on; the resulting deadlock rollback destroys the per-test SAVEPOINT. See
# `serial_file_lock` in tests/conftest.py.
@pytest.fixture(autouse=True)
def _one_worker_at_a_time(serial_file_lock):
    with serial_file_lock('project_membership_account_user_gap'):
        yield


def _project_with_rowless_lead(session):
    project = make_project(session, facility_name='UNIV')
    for _ in range(2):
        make_allocation(session, account=make_account(session, project=project))
    # The CESM0020 shape: the lead's rows are gone, the FK still points at them.
    session.query(AccountUser).filter_by(user_id=project.project_lead_user_id).delete()
    session.flush()
    session.expire_all()
    return project


def test_validate_flags_reconcile_fixes(runner, mock_db_session):
    project = _project_with_rowless_lead(mock_db_session)
    lead = project.lead.username

    result = runner.invoke(cli, ['project', project.projcode, '--validate'])
    assert result.exit_code == 2, result.output
    assert f'Project lead {lead} is not a member on:' in result.output

    result = runner.invoke(cli, ['project', project.projcode, '--reconcile'])
    assert result.exit_code == 0, result.output
    assert lead in result.output
    assert 'Added 2 memberships' in result.output

    result = runner.invoke(cli, ['project', project.projcode, '--validate'])
    assert result.exit_code == 0, result.output
    assert 'validated' in result.output

    result = runner.invoke(cli, ['project', project.projcode, '--reconcile'])
    assert result.exit_code == 0, result.output
    assert 'nothing to add' in result.output


def test_validate_flags_an_admin_with_only_expired_rows(runner, mock_db_session):
    session = mock_db_session
    project = make_project(session, facility_name='UNIV')
    make_allocation(session, account=make_account(session, project=project))
    admin = make_user(session)
    project.update(project_admin_user_id=admin.user_id)
    for au in session.query(AccountUser).filter_by(user_id=admin.user_id):
        au.end_date = datetime.now() - timedelta(days=1)
    session.flush()

    result = runner.invoke(cli, ['project', project.projcode, '--validate'])
    assert result.exit_code == 2, result.output
    assert f'Project admin {admin.username} is not a member on:' in result.output


def _live_lead_rows(session, project):
    now = datetime.now()
    return [au for au in session.query(AccountUser).filter_by(user_id=project.project_lead_user_id)
            if au.end_date is None or au.end_date > now]


@pytest.mark.parametrize('args, message', [
    (['--all'], '--all requires --reconcile'),
    (['PRJX', '--reconcile', '--all'], 'not both'),
    (['PRJX', '--reconcile', '--reconcile-lead-admin'], 'use one of'),
    (['PRJX', '--dry-run'], '--dry-run requires'),
])
def test_flag_guards(runner, mock_db_session, args, message):
    result = runner.invoke(cli, ['project', *args])
    assert result.exit_code == 1
    assert message in result.output


def test_dry_run_reports_and_writes_nothing(runner, mock_db_session):
    project = _project_with_rowless_lead(mock_db_session)
    result = runner.invoke(cli, ['project', project.projcode, '--reconcile-lead-admin', '--dry-run'])
    assert result.exit_code == 0, result.output
    assert 'Would add 2 memberships' in result.output
    assert 'never a member' in result.output
    assert _live_lead_rows(mock_db_session, project) == []


def test_all_reconciles_every_active_project(runner, mock_db_session, monkeypatch):
    project = _project_with_rowless_lead(mock_db_session)
    monkeypatch.setattr(ProjectReconcileCommand, '_active_projects', lambda self: [project])

    result = runner.invoke(cli, ['--format', 'json', 'project', '--reconcile-lead-admin',
                                 '--all', '--dry-run'])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert (data['kind'], data['mode'], data['dry_run']) == ('project_reconcile', 'lead_admin', True)
    [row] = data['added']
    assert (row['projcode'], row['role'], row['history']) == (project.projcode, 'lead', 'never a member')
    assert len(row['resources']) == 2

    result = runner.invoke(cli, ['project', '--reconcile-lead-admin', '--all'])
    assert result.exit_code == 0, result.output
    assert len(_live_lead_rows(mock_db_session, project)) == 2


def test_validate_ignores_an_inactive_lead(runner, mock_db_session):
    project = _project_with_rowless_lead(mock_db_session)
    project.lead.active = False
    mock_db_session.flush()
    result = runner.invoke(cli, ['project', project.projcode, '--validate'])
    assert result.exit_code == 0, result.output
    assert 'is not a member' not in result.output
