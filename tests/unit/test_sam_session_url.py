"""SAM_DB_DRIVER / SAM_DB_PORT select the dialect of the SAM engine (POSTGRES_MIGRATION.md Fix 0)."""
import pytest

from sam import session as sam_session

pytestmark = pytest.mark.unit

_CREDS = {'SAM_DB_USERNAME': 'u', 'SAM_DB_PASSWORD': 'p@ss', 'SAM_DB_SERVER': 'db.local'}


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
    assert url.password == 'p@ss'


def test_postgres_driver_port_and_name(env):
    env.setenv('SAM_DB_DRIVER', 'postgres')
    env.setenv('SAM_DB_PORT', '5433')
    env.setenv('SAM_DB_NAME', 'sam_dev')
    sam_session.init_sam_db_defaults()
    url = sam_session.connection_string
    assert (url.drivername, url.port, url.database) == ('postgresql+psycopg2', 5433, 'sam_dev')


def test_ssl_connect_args_follow_the_driver():
    assert sam_session.ssl_connect_args('mysql', True) == {'ssl': {'ssl_disabled': False}}
    assert sam_session.ssl_connect_args('postgresql', True) == {'sslmode': 'require'}
    assert sam_session.ssl_connect_args('PostgreSQL', False) == {}
    assert sam_session.sam_dialect('mariadb') == 'mysql+pymysql'


def test_config_reload_reads_the_driver(monkeypatch):
    from config import SAMConfig
    monkeypatch.setenv('SAM_DB_DRIVER', 'postgresql')
    monkeypatch.setenv('SAM_DB_PORT', '5433')
    SAMConfig.reload()
    try:
        assert (SAMConfig.SAM_DB_DRIVER, SAMConfig.SAM_DB_PORT) == ('postgresql', '5433')
    finally:
        monkeypatch.delenv('SAM_DB_DRIVER')
        monkeypatch.delenv('SAM_DB_PORT')
        SAMConfig.reload()
