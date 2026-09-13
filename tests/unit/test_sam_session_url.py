"""SAM_DB_DRIVER / SAM_DB_PORT select the dialect of the SAM engine (POSTGRES_MIGRATION.md Fix 0)."""
import pytest

from sam import session as sam_session

pytestmark = pytest.mark.unit

_CREDS = {'SAM_DB_USERNAME': 'u', 'SAM_DB_PASSWORD': 'test-placeholder-pass', 'SAM_DB_SERVER': 'db.local'}


@pytest.fixture
def env(monkeypatch):
    """Env for one URL build; the module-level URL is restored afterwards."""
    saved = sam_session.connection_string
    for k, v in _CREDS.items():
        monkeypatch.setenv(k, v)
    for k in ('SAM_DB_DRIVER', 'SAM_DB_PORT', 'SAM_DB_NAME'):
        monkeypatch.delenv(k, raising=False)
    yield monkeypatch
    sam_session.connection_string = saved


def test_the_default_is_mysql_without_a_port(env):
    sam_session.init_sam_db_defaults()
    url = sam_session.connection_string
    assert (url.drivername, url.host, url.port, url.database) == ('mysql+pymysql', 'db.local', None, 'sam')
    assert url.password == 'test-placeholder-pass'


def test_postgres_driver_port_and_name(env):
    env.setenv('SAM_DB_DRIVER', 'postgres')
    env.setenv('SAM_DB_PORT', '5433')
    env.setenv('SAM_DB_NAME', 'sam_dev')
    sam_session.init_sam_db_defaults()
    url = sam_session.connection_string
    assert (url.drivername, url.port, url.database) == ('postgresql+psycopg2', 5433, 'sam_dev')


_PG_HARDENING = {'connect_timeout': 10, 'keepalives': 1, 'keepalives_idle': 30,
                 'keepalives_interval': 10, 'keepalives_count': 3,
                 'target_session_attrs': 'read-write'}


def test_connect_args_follow_the_driver(monkeypatch):
    monkeypatch.delenv('SAM_DB_CONNECT_TIMEOUT', raising=False)
    assert sam_session.connect_args('mysql', True) == {'connect_timeout': 10, 'ssl': {'ssl_disabled': False}}
    assert sam_session.connect_args('mysql', False, application_name='x') == {'connect_timeout': 10}
    assert sam_session.connect_args('postgresql', True) == {**_PG_HARDENING, 'sslmode': 'require'}
    assert sam_session.connect_args('PostgreSQL', False) == _PG_HARDENING
    assert sam_session.connect_args('postgres', True, application_name='sam-webapp:pod:sam') == {
        **_PG_HARDENING, 'application_name': 'sam-webapp:pod:sam', 'sslmode': 'require'}
    assert sam_session.sam_dialect('mariadb') == 'mysql+pymysql'


def test_connect_timeout_is_tunable_and_bounded_on_both_drivers(monkeypatch):
    """A probe or a request must never hang in connect() for the OS SYN timeout."""
    monkeypatch.setenv('SAM_DB_CONNECT_TIMEOUT', '3')
    assert sam_session.connect_args('postgres', False)['connect_timeout'] == 3
    assert sam_session.connect_args('mysql', False)['connect_timeout'] == 3


def test_status_engine_shares_the_hardened_connect_args(monkeypatch):
    """The CLI/CronJob status engine funnels through sam.session.connect_args too."""
    from unittest.mock import patch
    from system_status import session as status_session
    monkeypatch.setenv('STATUS_DB_DRIVER', 'postgresql')
    monkeypatch.setenv('STATUS_DB_REQUIRE_SSL', 'true')
    with patch('sam.session.connect_args', wraps=sam_session.connect_args) as spy:
        engine, _ = status_session.create_status_engine('postgresql+psycopg2://u:p@db.local/system_status')
    engine.dispose()
    spy.assert_called_once_with('postgresql', True)


def test_config_reload_reads_the_driver(monkeypatch):
    from config import SAMConfig
    monkeypatch.setenv('SAM_DB_DRIVER', 'postgresql')
    SAMConfig.reload()
    try:
        assert SAMConfig.SAM_DB_DRIVER == 'postgresql'
    finally:
        monkeypatch.delenv('SAM_DB_DRIVER')
        SAMConfig.reload()
