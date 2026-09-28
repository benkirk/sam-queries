"""``sam-search user``'s last-seen read side: the panel row and ``--not-seen-since``."""

import json
from datetime import timedelta
from unittest.mock import patch

import click
import pytest

from cli.cmds.search import cli
from cli.core.utils import parse_duration_days
from factories.core import make_user
from factories.projects import make_project
from system_status.queries.last_seen import record_seen_at
from system_status.timeutil import utcnow_naive


@pytest.mark.parametrize('spec, days', [
    ('30', 30), ('30d', 30), ('2w', 14), ('6m', 180), ('3y', 1095), (' 1Y ', 365),
])
def test_parse_duration_days(spec, days):
    assert parse_duration_days(spec, '--x') == days


@pytest.mark.parametrize('spec', ['x', '0', '-1', '', 'y', '1.5y'])
def test_parse_duration_days_rejects(spec):
    with pytest.raises(click.BadParameter):
        parse_duration_days(spec, '--x')


@pytest.fixture
def ledger(status_session):
    """Point the CLI at the SQLite status bind; yields a seeding helper."""
    from webapp.extensions import db

    def seen(kind, system, usernames, days_ago):
        record_seen_at(status_session, kind, system, usernames,
                       utcnow_naive() - timedelta(days=days_ago))
        status_session.commit()

    with patch('system_status.session.create_status_engine',
               return_value=(db.engines['system_status'], None)):
        yield seen


def _json(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


class TestUserLastSeenRow:

    def test_row_and_verbose_table(self, runner, mock_db_session, ledger):
        ledger('pbs', 'cheyenne', ['benkirk'], 400)
        ledger('webapp', 'samuel', ['benkirk'], 3)
        out = runner.invoke(cli, ['user', 'benkirk']).output
        assert 'Last seen' in out and 'webapp · samuel' in out and '3 days ago' in out
        verbose = runner.invoke(cli, ['user', 'benkirk', '-v']).output
        assert 'Last seen: benkirk' in verbose and 'cheyenne' in verbose

    def test_json_lists_every_source(self, runner, mock_db_session, ledger):
        ledger('pbs', 'cheyenne', ['benkirk'], 400)
        data = _json(runner.invoke(cli, ['--format', 'json', 'user', 'benkirk']))
        assert [(s['kind'], s['system']) for s in data['last_seen']] == [('pbs', 'cheyenne')]

    def test_never_seen(self, runner, mock_db_session, ledger):
        out = runner.invoke(cli, ['user', 'benkirk']).output
        assert 'never' in out

    def test_not_configured_omits_row_and_key(self, runner, mock_db_session):
        assert 'Last seen' not in runner.invoke(cli, ['user', 'benkirk']).output
        data = _json(runner.invoke(cli, ['--format', 'json', 'user', 'benkirk']))
        assert 'last_seen' not in data

    def test_unreadable_ledger_is_unavailable(self, runner, mock_db_session, ledger):
        with patch('system_status.queries.last_seen.get_last_seen',
                   side_effect=RuntimeError('down')):
            out = runner.invoke(cli, ['user', 'benkirk']).output
            data = _json(runner.invoke(cli, ['--format', 'json', 'user', 'benkirk']))
        assert 'unavailable' in out
        assert data['last_seen'] is None


class TestNotSeenSince:

    @pytest.fixture
    def people(self, session, ledger):
        fresh, old, ghost, lead = (make_user(session) for _ in range(4))
        make_project(session, lead=lead)
        ledger('webapp', 'samuel', [fresh.username], 1)
        ledger('pbs', 'derecho', [old.username, lead.username], 400)
        return {'fresh': fresh.username, 'old': old.username,
                'ghost': ghost.username, 'lead': lead.username}

    def _users(self, runner, *args):
        data = _json(runner.invoke(cli, ['--format', 'json', 'user', *args]))
        return data, {u['username']: u for u in data['users']}

    def test_old_and_never_seen_are_listed(self, runner, mock_db_session, people):
        data, users = self._users(runner, '--not-seen-since', '1y')
        assert people['fresh'] not in users
        assert users[people['old']]['system'] == 'derecho'
        assert users[people['ghost']]['last_seen'] is None
        assert users[people['lead']]['active_project_count'] == 1
        assert set(data) == {'kind', 'since', 'cutoff', 'source', 'abandoned', 'active_only',
                             'total_considered', 'count', 'users'}
        assert set(users[people['old']]) == {
            'username', 'display_name', 'status', 'last_seen', 'source', 'system',
            'active_project_count', 'primary_email'}

    def test_source_narrows_the_sightings(self, runner, mock_db_session, people):
        _, users = self._users(runner, '--not-seen-since', '1y', '--source', 'pbs')
        assert users[people['fresh']]['last_seen'] is None

    def test_abandoned_intersects(self, runner, mock_db_session, people):
        _, users = self._users(runner, '--abandoned', '--not-seen-since', '1y')
        assert people['lead'] not in users and people['old'] in users
        assert all(u['active_project_count'] == 0 for u in users.values())

    def test_email_column_only_when_verbose(self, runner, mock_db_session, people):
        args = ['user', '--not-seen-since', '1y']
        assert 'Email' not in runner.invoke(cli, args).output
        assert 'Email' in runner.invoke(cli, [*args, '-v']).output

    def test_without_a_status_db_it_is_an_error(self, runner, mock_db_session):
        result = runner.invoke(cli, ['user', '--not-seen-since', '1y'])
        assert result.exit_code == 2

    @pytest.mark.parametrize('args', [
        ['user', '--not-seen-since', 'soon'],
        ['user', '--source', 'pbs', '--search', 'ben'],
        ['user', 'benkirk', '--not-seen-since', '1y'],
    ])
    def test_bad_invocations_are_rejected(self, runner, mock_db_session, args):
        assert runner.invoke(cli, args).exit_code != 0
