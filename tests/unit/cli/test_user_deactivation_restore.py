"""CliRunner tests for `sam-admin user <u> --deactivation` / `--restore-deactivation`."""
import json
from datetime import datetime, timedelta

from cli.cmds.admin import cli
from sam.accounting.accounts import AccountUser

from factories import make_account, make_project, make_resource, make_user

CLOSED = datetime.now().replace(microsecond=0) - timedelta(days=3)


def _deactivated_user(session):
    user = make_user(session, active=False)
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
