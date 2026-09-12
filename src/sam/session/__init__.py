from sqlalchemy import create_engine, URL
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager


# ============================================================================
# Database Connection Setup
# ============================================================================

connection_string = None

POSTGRES_DRIVERS = ('postgresql', 'postgres')


def sam_dialect(driver: str) -> str:
    """SQLAlchemy drivername for SAM_DB_DRIVER ('mysql' default, 'postgresql'/'postgres')."""
    return 'postgresql+psycopg2' if driver.lower() in POSTGRES_DRIVERS else 'mysql+pymysql'


def ssl_connect_args(driver: str, require_ssl: bool) -> dict:
    """The SSL connect_args each DBAPI understands; empty when SSL is not required."""
    if not require_ssl:
        return {}
    if driver.lower() in POSTGRES_DRIVERS:
        return {'sslmode': 'require'}
    return {'ssl': {'ssl_disabled': False}}


def init_sam_db_defaults():
    """Build the module-level `connection_string` from SAM_DB_* env vars.

    Tolerant of missing env at import time: if any of the three required
    vars (USERNAME, PASSWORD, SERVER) is unset/empty, leave
    `connection_string = None` and return silently. Callers that need a
    connection (`create_sam_engine()`, webapp boot) raise a clear error
    when they actually need it. This lets pytest import `sam.session`
    without `SAM_DB_*` set in shell — `conftest.py` overrides
    `SQLALCHEMY_DATABASE_URI` via `config_overrides=` from
    `SAM_TEST_DB_URL`, so it never reads `connection_string`.
    """
    from dotenv import load_dotenv, find_dotenv
    import os

    load_dotenv(find_dotenv())

    username = os.environ.get('SAM_DB_USERNAME')
    password = os.environ.get('SAM_DB_PASSWORD')
    server = os.environ.get('SAM_DB_SERVER')

    if not (username and password and server):
        return

    database = os.getenv('SAM_DB_NAME', 'sam')
    driver = os.getenv('SAM_DB_DRIVER', 'mysql')
    port = os.getenv('SAM_DB_PORT') or None

    import logging as _logging
    _logging.getLogger(__name__).debug(
        'SAM DB: %s %s:$SAM_DB_PASSWORD@%s/%s', driver, username, server, database
    )

    # Create connection URL using URL.create() to safely handle special characters
    # in the password (e.g. '@', '%', etc.) that would break f-string URL interpolation.
    global connection_string
    connection_string = URL.create(
        drivername=sam_dialect(driver),
        username=username,
        password=password,
        host=server,
        port=int(port) if port else None,
        database=database,
    )

# run on import
init_sam_db_defaults()

def create_sam_engine(input_connection_string: str = None):
    """
    Create database engine and session factory.

    If input_connection_string is not provided, will load credentials from environment variables:
        SAM_DB_USERNAME
        SAM_DB_PASSWORD
        SAM_DB_SERVER
        SAM_DB_DRIVER (optional, default: mysql; postgresql for the dev copy)
        SAM_DB_PORT (optional)
        SAM_DB_REQUIRE_SSL (optional, default: false)

    Example connection_string:
        'mysql+pymysql://username:password@localhost/sam'
    """
    import os

    if input_connection_string is None:
        input_connection_string = connection_string

    if input_connection_string is None:
        raise RuntimeError(
            "SAM database connection is not configured. Set the SAM_DB_USERNAME, "
            "SAM_DB_PASSWORD, and SAM_DB_SERVER environment variables (see "
            "`.env.example` for a template) and call `sam.session.init_sam_db_defaults()` "
            "again, or pass an explicit `input_connection_string` to "
            "`create_sam_engine()`."
        )

    # Check if SSL is required (for remote servers)
    require_ssl = os.getenv('SAM_DB_REQUIRE_SSL', 'false').lower() in ('true', '1', 'yes')
    connect_args = ssl_connect_args(os.getenv('SAM_DB_DRIVER', 'mysql'), require_ssl)

    engine = create_engine(
        input_connection_string,
        echo=False,  # Set to True for SQL debugging
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args=connect_args
    )
    SessionLocal = sessionmaker(bind=engine)
    return engine, SessionLocal


@contextmanager
def get_session(SessionLocal):
    """Context manager for database sessions."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
