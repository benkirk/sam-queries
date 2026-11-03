"""CliRunner tests for `sam-admin masks`: list, open (dry-run and live), withdraw, the
refusals and the JSON conventions. The ticket routine is stubbed: it is the webapp's."""
import json
from datetime import datetime, timedelta

import pytest

from cli.cmds.admin import cli
from factories import make_adhoc_group, make_adhoc_group_tag, make_project, make_user, next_int
from sam.core.groups import UnixGroupMask


@pytest.fixture
def group(session):
    g = make_adhoc_group(session)
    make_adhoc_group_tag(session, g, 'hpc')
    make_adhoc_group_tag(session, g, 'hpc-data')
    session.expire(g, ['tags', 'system_accounts'])
    return g


@pytest.fixture(autouse=True)
def no_ticket_path(monkeypatch):
    calls = []
    monkeypatch.setattr('cli.group_masks.commands.MasksCommand._file_ticket',
                        lambda self, masks, target: calls.append([m.username for m in masks]) or None)
    monkeypatch.setattr('cli.group_masks.commands.MasksCommand._comment_withdrawn',
                        lambda self, mask: True)
    return calls


def _rows(group):
    return sorted((e.access_branch_name, e.username) for e in group.system_accounts)


def test_dry_run_checks_every_guard_and_writes_nothing(runner, mock_db_session, group, no_ticket_path):
    user = make_user(mock_db_session)
    result = runner.invoke(cli, ['--format', 'json', 'masks', 'open', group.group_name, user.username,
                                 'no-such-user-zz', '--dry-run'])
    assert result.exit_code == 2, result.output
    assert 'No user named' in result.output
    result = runner.invoke(cli, ['--format', 'json', 'masks', 'open', str(group.unix_gid), user.username,
                                 '--dry-run'])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload['kind'] == 'group_masks_open' and payload['dry_run'] is True
    assert payload['checked'] == [user.username] and payload['target']['kind'] == 'adhoc'
    assert _rows(group) == [] and no_ticket_path == []


def test_a_live_open_is_one_batch_and_files_once(runner, mock_db_session, group, no_ticket_path):
    users = [make_user(mock_db_session) for _ in range(2)]
    names = [u.username for u in users]
    result = runner.invoke(cli, ['masks', 'open', group.group_name, *names, '--note', 'RC-00000'])
    assert result.exit_code == 0, result.output
    assert 'Opened add on' in result.output and 'no ticket path' in result.output
    rows = (mock_db_session.query(UnixGroupMask)
            .filter(UnixGroupMask.unix_gid == group.unix_gid).order_by(UnixGroupMask.mask_id).all())
    assert [r.username for r in rows] == names
    assert {r.batch_id for r in rows} == {rows[0].mask_id}
    assert rows[0].requested_by.startswith('cli:') and rows[0].note == 'RC-00000'
    assert len(_rows(group)) == 4
    assert no_ticket_path == [names]


def test_json_is_refused_for_the_live_writes(runner, mock_db_session, group):
    result = runner.invoke(cli, ['--format', 'json', 'masks', 'open', group.group_name, 'x'])
    assert result.exit_code == 2
    assert json.loads(result.output)['error'] == 'json_unsupported_for_writes'
    result = runner.invoke(cli, ['--format', 'json', 'masks', 'withdraw', '1'])
    assert result.exit_code == 2


def test_an_unknown_target_or_mask_is_not_found(runner, mock_db_session):
    result = runner.invoke(cli, ['--format', 'json', 'masks', 'open', 'no-such-group-zz', 'x', '--dry-run'])
    assert result.exit_code == 1
    assert json.loads(result.output)['error'] == 'not_found'
    result = runner.invoke(cli, ['masks', 'withdraw', '999999999'])
    assert result.exit_code == 1


def test_a_project_grant_defaults_to_seven_days(runner, mock_db_session):
    project = make_project(mock_db_session)
    project.unix_gid = 96_000_000 + next_int('mask_gid')
    mock_db_session.flush()
    user = make_user(mock_db_session)
    result = runner.invoke(cli, ['masks', 'open', project.projcode.lower(), user.username])
    assert result.exit_code == 0, result.output
    row = mock_db_session.query(UnixGroupMask).filter(UnixGroupMask.unix_gid == project.unix_gid).one()
    assert row.kind == 'project'
    assert row.expires_at.date() == (datetime.now() + timedelta(days=7)).date()
    result = runner.invoke(cli, ['masks', 'open', project.projcode, user.username, '--until', '91d'])
    assert result.exit_code == 2 and 'Refused' in result.output


def test_list_and_withdraw(runner, mock_db_session, group):
    user = make_user(mock_db_session, upid=True)
    assert runner.invoke(cli, ['masks', 'open', group.group_name, user.username]).exit_code == 0
    result = runner.invoke(cli, ['--format', 'json', 'masks', 'list', '-q', group.group_name])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload['kind'] == 'group_masks' and payload['count'] == 1
    row = payload['masks'][0]
    assert (row['username'], row['state'], row['flags']) == (user.username, 'pending', ['no ticket'])
    assert row['batch_id'] == row['mask_id']
    result = runner.invoke(cli, ['masks', 'withdraw', str(row['mask_id']), '--dry-run'])
    assert result.exit_code == 0 and 'Would withdraw' in result.output
    result = runner.invoke(cli, ['masks', 'withdraw', str(row['mask_id'])])
    assert result.exit_code == 0, result.output
    assert 'Withdrew' in result.output and 'commented' in result.output
    assert _rows(group) == []
    result = runner.invoke(cli, ['masks', 'withdraw', str(row['mask_id'])])
    assert result.exit_code == 2 and 'already withdrawn' in result.output
    result = runner.invoke(cli, ['--format', 'json', 'masks', 'list', '--all', '-q', group.group_name])
    assert json.loads(result.output)['masks'][0]['state'] == 'withdrawn'
