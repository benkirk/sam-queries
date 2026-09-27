"""Drift gates for the ticket-provider levers in the chart.

Production ships armed (API create, mail fallback); the CronJob reads Jira but
can never write to it or select a filing path; samuel-dev ships mute. Flipping
any of these is a deliberate edit here as well as in the values file.
"""
import yaml

from _paths import REPO_ROOT

HELM = REPO_ROOT / 'helm'
WEBAPP_ONLY = ('JIRA_WRITE_ENABLED', 'TICKET_PROVIDER')
READ_KEYS = ('JIRA_ENABLED', 'JIRA_BASE_URL', 'JIRA_AUTH', 'JIRA_USER', 'JIRA_PROJECT_KEY',
             'JIRA_SERVICE_DESK_ID', 'JIRA_REQUEST_TYPE_ID')


def _values(name='values.yaml'):
    return yaml.safe_load((HELM / name).read_text())


def _code(path):
    return '\n'.join(line for line in (HELM / path).read_text().splitlines()
                     if not line.lstrip().startswith('#'))


class TestProduction:
    def test_the_levers_are_armed_on_purpose(self):
        env = _values()['webapp']['env']
        assert (env['TICKET_PROVIDER'], env['JIRA_ENABLED'], env['JIRA_WRITE_ENABLED']) == (
            'jira-servicedesk', '1', '1')

    def test_the_request_type_is_add_a_user_on_the_rc_desk(self):
        env = _values()['webapp']['env']
        assert (env['JIRA_BASE_URL'], env['JIRA_PROJECT_KEY'], env['JIRA_SERVICE_DESK_ID'],
                env['JIRA_REQUEST_TYPE_ID']) == ('https://ithelp.ucar.edu', 'RC', '3', '20')

    def test_the_token_comes_from_openbao(self):
        creds = _values()['webapp']['jiraCredentials']
        assert creds == {'enabled': True, 'useExternalSecret': True,
                         'secretStoreRefName': 'csg-ro',
                         'secretPath': 'csg/sam-jira-token', 'tokenKey': 'token'}

    def test_the_selector_names_a_registered_provider(self):
        from sam.integration.tickets.registry import MAIL_NAMES, PROVIDERS
        name = _values()['webapp']['env']['TICKET_PROVIDER']
        assert name in PROVIDERS or name in MAIL_NAMES


class TestTheCronJobReadsOnly:
    def test_the_tasks_env_never_carries_a_write_key(self):
        tasks_env = (_values().get('tasks') or {}).get('env') or {}
        for key in WEBAPP_ONLY:
            assert key not in tasks_env, key

    def test_the_manifest_never_carries_a_write_key(self):
        body = _code('templates/cronjob-tasks.yaml')
        for key in WEBAPP_ONLY:
            assert key not in body, f'the task pod must never carry {key}'

    def test_the_manifest_carries_every_read_key_and_the_token(self):
        body = _code('templates/cronjob-tasks.yaml')
        for key in READ_KEYS + ('JIRA_TOKEN',):
            assert f'name: {key}' in body, key
        assert '-jira-credentials' in body


class TestDev:
    def test_dev_is_mute_and_holds_no_token(self):
        dev = _values('values-dev.yaml')['webapp']
        env = dev['env']
        assert (env['TICKET_PROVIDER'], env['JIRA_ENABLED'], env['JIRA_WRITE_ENABLED']) == (
            '', '0', '0')
        assert dev['jiraCredentials']['enabled'] is False
