"""
Test Configuration for Local MySQL Database

Provides connection strings and session factories for testing against
the local MySQL clone.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager


# Local MySQL connection (Docker container)
LOCAL_MYSQL_CONNECTION = 'mysql+pymysql://root:root@127.0.0.1:3306/sam'


def create_test_engine(echo=False):
    """
    Create SQLAlchemy engine for local test database.

    Args:
        echo: If True, log all SQL statements (useful for debugging)

    Returns:
        SQLAlchemy Engine instance
    """
    engine = create_engine(
        LOCAL_MYSQL_CONNECTION,
        echo=echo,
        pool_pre_ping=True,  # Verify connections before using
        pool_recycle=3600,   # Recycle connections after 1 hour
        pool_size=5,
        max_overflow=10
    )
    return engine


def create_test_session_factory(engine=None):
    """
    Create a session factory for tests.

    Args:
        engine: Optional engine to use. If None, creates a new one.

    Returns:
        sessionmaker instance
    """
    if engine is None:
        engine = create_test_engine()

    SessionLocal = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False
    )
    return SessionLocal


@contextmanager
def get_test_session():
    """
    Context manager for test database sessions.

    Automatically handles commit/rollback and cleanup.

    Usage:
        with get_test_session() as session:
            user = session.query(User).first()
            print(user.username)
    """
    SessionLocal = create_test_session_factory()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def get_test_session_rollback():
    """
    Context manager for test sessions that ALWAYS rollback.

    Use this for tests that modify data but should leave the database unchanged.

    Usage:
        with get_test_session_rollback() as session:
            new_user = User(username='test_user')
            session.add(new_user)
            session.flush()  # Get ID without committing
            assert new_user.user_id is not None
            # Automatically rolls back on exit
    """
    SessionLocal = create_test_session_factory()
    session = SessionLocal()
    try:
        yield session
        session.rollback()  # Always rollback
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
