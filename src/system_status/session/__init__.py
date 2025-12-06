from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager


# ============================================================================
# System Status Database Connection Setup
# ============================================================================

connection_string = None

def init_status_db_defaults():
    from dotenv import load_dotenv, find_dotenv
    import os

    load_dotenv(find_dotenv())

    username = os.environ['STATUS_DB_USERNAME']
    password = os.environ['STATUS_DB_PASSWORD']
    server = os.environ['STATUS_DB_SERVER']
    database = os.getenv('STATUS_DB_NAME', 'system_status')

    print(f'{username}:$STATUS_DB_PASSWORD@{server}/{database}')

    # Create connection string
    # Using pymysql driver for consistency with SAM
    global connection_string
    connection_string = f'mysql+pymysql://{username}:{password}@{server}/{database}'

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

    Example connection_string:
        'mysql+pymysql://username:password@localhost/system_status'
    """
    import os

    if input_connection_string is None:
        input_connection_string = connection_string

    # Check if SSL is required (for remote servers)
    require_ssl = os.getenv('STATUS_DB_REQUIRE_SSL', 'false').lower() in ('true', '1', 'yes')

    # Build connect_args based on SSL requirement
    connect_args = {}
    if require_ssl:
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
