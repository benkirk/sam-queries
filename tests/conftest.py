"""
pytest configuration and fixtures for SAM ORM tests
"""

import pytest
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'python'))

from test_config import (
    create_test_engine,
    create_test_session_factory,
    get_test_session,
    get_test_session_rollback
)


@pytest.fixture(scope='session')
def engine():
    """Create a test database engine for the entire test session."""
    return create_test_engine()


@pytest.fixture(scope='session')
def SessionFactory(engine):
    """Create a session factory for the test session."""
    return create_test_session_factory(engine)


@pytest.fixture
def session(SessionFactory):
    """
    Provide a transactional test session.

    Creates a new session for each test and rolls back after the test completes.
    """
    session = SessionFactory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def session_commit(SessionFactory):
    """
    Provide a session that commits changes.

    Use this ONLY for tests that need to persist data.
    Most tests should use the regular 'session' fixture which auto-rolls back.
    """
    session = SessionFactory()
    try:
        yield session
        session.commit()
    finally:
        session.close()
