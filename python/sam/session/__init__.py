from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager


# ============================================================================
# Database Connection Setup
# ============================================================================

def create_sam_engine(connection_string: str = None):
    """
    Create database engine and session factory.

    If connection_string is not provided, will load credentials from environment variables:
        SAM_DB_USERNAME
        SAM_DB_PASSWORD
        SAM_DB_SERVER

    Example connection_string:
        'mysql+pymysql://username:password@localhost/sam'
    """
    if connection_string is None:
        from dotenv import load_dotenv, find_dotenv
        import os

        load_dotenv(find_dotenv())

        username = os.environ['SAM_DB_USERNAME']
        password = os.environ['SAM_DB_PASSWORD']
        server = os.environ['SAM_DB_SERVER']
        database = 'sam'

        print(f'{username}:$SAM_DB_PASSWORD@{server}/{database}')

        # Create connection string
        connection_string = f'mysql+mysqlconnector://{username}:{password}@{server}/{database}'

    engine = create_engine(
        connection_string,
        echo=False,  # Set to True for SQL debugging
        pool_pre_ping=True,
        pool_recycle=3600
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
