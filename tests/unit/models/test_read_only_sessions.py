"""SAM_DB_READ_ONLY / STATUS_DB_READ_ONLY against the real test database (HPC_LANE_ENV_LAYERING.md § 5)."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError

from sam.session import connect_args


def _engine(test_db_url, **kwargs):
    driver = 'postgresql' if test_db_url.startswith('postgresql') else 'mysql'
    return driver, create_engine(test_db_url, connect_args=connect_args(driver, False, **kwargs))


def test_a_read_only_engine_refuses_writes(test_db_url):
    driver, eng = _engine(test_db_url, read_only=True)
    try:
        with eng.connect() as conn:
            flag = 'SHOW transaction_read_only' if driver == 'postgresql' else 'SELECT @@transaction_read_only'
            assert str(conn.execute(text(flag)).scalar()).lower() in ('on', '1')
            assert conn.execute(text('SELECT count(*) FROM users')).scalar() > 0
            with pytest.raises(DBAPIError, match='(?i)read.only'):
                conn.execute(text('UPDATE users SET username = username WHERE 1 = 0'))
    finally:
        eng.dispose()


def test_a_writer_bind_is_unchanged(test_db_url):
    driver, eng = _engine(test_db_url)
    try:
        with eng.connect() as conn:
            flag = 'SHOW transaction_read_only' if driver == 'postgresql' else 'SELECT @@transaction_read_only'
            assert str(conn.execute(text(flag)).scalar()).lower() in ('off', '0')
    finally:
        eng.dispose()


def test_read_write_target_refuses_a_read_only_session(test_db_url):
    """Why read_only switches target_session_attrs: the HPC hpc_reader role failed exactly so."""
    if not test_db_url.startswith('postgresql'):
        pytest.skip('target_session_attrs is libpq-only')
    args = {**connect_args('postgresql', False), 'options': '-c default_transaction_read_only=on'}
    eng = create_engine(test_db_url, connect_args=args)
    try:
        with pytest.raises(OperationalError, match='read-only'):
            eng.connect().close()
    finally:
        eng.dispose()
