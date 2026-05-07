from sqlalchemy import create_engine, URL
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager


# ============================================================================
# System Status Database Connection Setup
# ============================================================================

connection_string = None

def init_status_db_defaults():
    """Build the module-level `connection_string` from STATUS_DB_* env vars.

    Tolerant of missing env at import time: if any of USERNAME/PASSWORD/
    SERVER is unset/empty, leave `connection_string = None` and return
    silently. Callers raise a clear error when they actually need it.
    See `sam.session.init_sam_db_defaults` for the full rationale.
    """
    from dotenv import load_dotenv, find_dotenv
    import os

    load_dotenv(find_dotenv())

    username = os.environ.get('STATUS_DB_USERNAME')
    password = os.environ.get('STATUS_DB_PASSWORD')
    server = os.environ.get('STATUS_DB_SERVER')

    if not (username and password and server):
        return

    database = os.getenv('STATUS_DB_NAME', 'system_status')

    print(f'{username}:$STATUS_DB_PASSWORD@{server}/{database}')

    # Build connection string based on configured driver
    driver = os.getenv('STATUS_DB_DRIVER', 'mysql').lower()
    if driver in ('postgresql', 'postgres'):
        dialect = 'postgresql+psycopg2'
    else:
        dialect = 'mysql+pymysql'

    # Use URL.create() to safely handle special characters in the password
    # (e.g. '@', '%') that would break f-string URL interpolation.
    global connection_string
    connection_string = URL.create(
        drivername=dialect,
        username=username,
        password=password,
        host=server,
        database=database,
    )

# run on import
init_status_db_defaults()

def create_status_engine(input_connection_string: str = None):
    """
    Create database engine and session factory for system_status database.

    If input_connection_string is not provided, will load credentials from environment variables:
        STATUS_DB_USERNAME
        STATUS_DB_PASSWORD
        STATUS_DB_SERVER
        STATUS_DB_REQUIRE_SSL (optional, default: false)

    Example connection strings:
        'mysql+pymysql://username:password@localhost/system_status'
        'postgresql+psycopg2://username:password@localhost/system_status'
    """
    import os

    if input_connection_string is None:
        input_connection_string = connection_string

    if input_connection_string is None:
        raise RuntimeError(
            "system_status database connection is not configured. Set the "
            "STATUS_DB_USERNAME, STATUS_DB_PASSWORD, and STATUS_DB_SERVER "
            "environment variables (see `.env.example` for a template) and "
            "call `system_status.session.init_status_db_defaults()` again, "
            "or pass an explicit `input_connection_string` to "
            "`create_status_engine()`."
        )

    # Check if SSL is required (for remote servers)
    require_ssl = os.getenv('STATUS_DB_REQUIRE_SSL', 'false').lower() in ('true', '1', 'yes')
    driver = os.getenv('STATUS_DB_DRIVER', 'mysql').lower()

    # Build connect_args based on SSL requirement (syntax differs by driver)
    connect_args = {}
    if require_ssl:
        if driver in ('postgresql', 'postgres'):
            connect_args['sslmode'] = 'require'
        else:
            connect_args['ssl'] = {'ssl_disabled': False}

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
