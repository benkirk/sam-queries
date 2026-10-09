"""CliRunner tests for `sam-admin user <u> --deactivation` / `--restore-deactivation`."""
import json
from datetime import datetime, timedelta

from cli.cmds.admin import cli
from sam.accounting.accounts import AccountUser

from factories import make_account, make_project, make_resource, make_user

CLOSED = datetime.now().replace(microsecond=0) - timedelta(days=3)


def _deactivated_user(session):
    user = make_user(session, active=False, deactivate=CLOSED)
    rows = []
    for _ in range(2):
        account = make_account(session, project=make_project(session),
                               resource=make_resource(session))
        rows.append(AccountUser(account_id=account.account_id, user_id=user.user_id,
                                start_date=CLOSED - timedelta(days=300), end_date=CLOSED))
    session.add_all(rows)
    session.flush()
    return user, rows


def test_preview_is_read_only(runner, mock_db_session):
    user, rows = _deactivated_user(mock_db_session)
    result = runner.invoke(cli, ['--format', 'json', 'user', user.username, '--deactivation'])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert (data['kind'], data['dry_run'], data['restored']) == ('deactivation_restore', True, 0)
    assert [r['outcome'] for r in data['rows']] == ['restored', 'restored']
    assert all(r.end_date == CLOSED for r in rows)


def test_restore_reopens_then_finds_nothing(runner, mock_db_session):
    user, rows = _deactivated_user(mock_db_session)
    result = runner.invoke(cli, ['user', user.username, '--restore-deactivation'])
    assert result.exit_code == 0, result.output
    assert all(r.end_date is None for r in rows)
    again = runner.invoke(cli, ['user', user.username, '--restore-deactivation'])
    assert again.exit_code == 1
    assert 'No deactivation closure found' in again.output


def test_json_refuses_the_write_and_reports_not_found(runner, mock_db_session):
    user, _ = _deactivated_user(mock_db_session)
    result = runner.invoke(cli, ['--format', 'json', 'user', user.username, '--restore-deactivation'])
    assert result.exit_code == 2
    assert json.loads(result.output)['error'] == 'json_unsupported_for_writes'
    result = runner.invoke(cli, ['--format', 'json', 'user', 'nosuchuser-ever', '--deactivation'])
    assert result.exit_code == 1
    assert json.loads(result.output) == {'kind': 'deactivation_restore', 'error': 'not_found',
                                         'username': 'nosuchuser-ever'}


def test_a_legacy_deactivation_has_no_closure(runner, mock_db_session):
    """Closed before the sync kept a stamp: the runbook's SQL, not this command."""
    user = make_user(mock_db_session, active=False)
    account = make_account(mock_db_session, project=make_project(mock_db_session),
                           resource=make_resource(mock_db_session))
    mock_db_session.add(AccountUser(account_id=account.account_id, user_id=user.user_id,
                                    start_date=CLOSED - timedelta(days=300), end_date=CLOSED))
    mock_db_session.flush()
    result = runner.invoke(cli, ['user', user.username, '--deactivation'])
    assert result.exit_code == 1 and 'No deactivation closure found' in result.output
