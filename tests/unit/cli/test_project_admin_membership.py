"""CliRunner tests for `sam-admin project <code> --validate / --reconcile` (lead/admin membership)."""
from datetime import datetime, timedelta

from cli.cmds.admin import cli
from sam.accounting.accounts import AccountUser

from factories import make_account, make_allocation, make_project, make_user


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
    assert 'added 2 memberships' in result.output

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
