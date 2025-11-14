"""
pytest configuration and fixtures for SAM ORM tests
"""

import pytest
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'python'))

from fixtures.test_config import (
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


# Common test data fixtures
@pytest.fixture
def test_user(session):
    """Get known test user (benkirk)."""
    from sam import User
    return User.get_by_username(session, 'benkirk')


@pytest.fixture
def test_project(session):
    """Get known test project (SCSG0001)."""
    from sam import Project
    return Project.get_by_projcode(session, 'SCSG0001')


@pytest.fixture
def test_allocation(test_project):
    """Get active allocation from test project."""
    for account in test_project.accounts:
        for allocation in account.allocations:
            if allocation.is_active:
                return allocation
    return None


@pytest.fixture
def test_resource(session):
    """Get known test resource (Derecho)."""
    from sam import Resource
    return session.query(Resource).filter_by(resource_name='Derecho').first()
