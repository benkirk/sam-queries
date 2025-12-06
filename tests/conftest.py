#-------------------------------------------------------------------------bh-
# pytest configuration and fixtures for SAM ORM tests
#-------------------------------------------------------------------------eh-

import pytest
import sys
import os
from pathlib import Path


def pytest_configure(config):
    """
    Set up worker-specific environment variables for pytest-xdist.

    This hook runs before test collection, ensuring each worker gets
    unique database names before any modules import configuration.
    """
    worker_id = getattr(config, 'workerinput', {}).get('workerid', 'master')

    if worker_id == 'master':
        db_name = 'system_status_test'
    else:
        db_name = f'system_status_test_{worker_id}'

    os.environ['STATUS_DB_NAME'] = db_name
    os.environ['FLASK_ACTIVE'] = '1'

# Add project root and src to path for imports
PROJ_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJ_ROOT / 'src'))
sys.path.insert(0, str(PROJ_ROOT / 'tests'))

from fixtures.test_config import (
    create_test_engine,
    create_test_session_factory,
    get_test_session,
    get_test_session_rollback
)


@pytest.fixture(scope='session')
def worker_id(request):
    """
    Get the pytest-xdist worker ID.

    Returns 'master' when running without xdist, or 'gw0', 'gw1', etc.
    when running with parallel workers via pytest-xdist.
    """
    if hasattr(request.config, 'workerinput'):
        return request.config.workerinput['workerid']
    return 'master'


@pytest.fixture(scope='session')
def worker_db_name(worker_id):
    """
    Generate unique database name for this worker.

    For pytest-xdist compatibility, each worker gets its own database:
    - master -> system_status_test
    - gw0 -> system_status_test_gw0
    - gw1 -> system_status_test_gw1
    etc.
    """
    if worker_id == 'master':
        return 'system_status_test'
    return f'system_status_test_{worker_id}'


@pytest.fixture(scope='session')
def engine():
    """Create SAM database engine for entire test session (uses production backup data)."""
    return create_test_engine()


@pytest.fixture(scope='session')
def SessionFactory(engine):
    """Create a session factory for the test session."""
    return create_test_session_factory(engine)


@pytest.fixture(scope='session', autouse=True)
def test_databases(worker_db_name):
    """
    Create temporary test database for system_status.

    SAM database uses production backup data and doesn't need a separate test database.
    System status database is created fresh to avoid clearing production data.

    IMPORTANT: This fixture is autouse=True and session-scoped so it runs BEFORE
    any imports and sets STATUS_DB_NAME environment variable early.

    pytest-xdist compatible: Each worker gets a unique database name via worker_db_name.
    """
    from sqlalchemy import create_engine, text
    import os
    from webapp.run import create_app
    from webapp.extensions import db
    import system_status.session

    # Set worker-specific database name in environment
    os.environ['STATUS_DB_NAME'] = worker_db_name

    # Get connection parameters from already set environment variables
    db_server = os.getenv('STATUS_DB_SERVER', os.getenv('SAM_DB_SERVER', '127.0.0.1'))
    db_user = os.getenv('SAM_DB_USERNAME', 'root')
    db_password = os.getenv('SAM_DB_PASSWORD', 'root')

    # Connect to MySQL server (no database specified)
    server_url = f"mysql+pymysql://{db_user}:{db_password}@{db_server}"
    engine = create_engine(server_url, isolation_level="AUTOCOMMIT")

    with engine.connect() as conn:
        # Drop and recreate worker-specific test database
        conn.execute(text(f"DROP DATABASE IF EXISTS {worker_db_name}"))
        conn.execute(text(f"CREATE DATABASE {worker_db_name} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))

    # Now that the environment variable is set and session is imported,
    # system_status.session.connection_string will be correct.
    # Create a minimal Flask app to use its db object for schema creation
    # This is necessary because system_status models use Flask-SQLAlchemy's db.Model
    # and its __bind_key__ functionality.
    temp_app = create_app()

    with temp_app.app_context():
        # db.create_all() will now correctly use the updated connection_string
        # for 'system_status' bind, as create_app sets it from system_status.session
        db.create_all()

    yield {'system_status': worker_db_name}

    # Cleanup: Drop worker-specific test database
    with engine.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {worker_db_name}"))

    # Restore original environment
    os.environ.pop('STATUS_DB_NAME', None)
    os.environ.pop('FLASK_ACTIVE', None) # Clean up env var
    engine.dispose()



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


@pytest.fixture(scope='function')
def status_session(test_databases, worker_db_name):
    """
    Provide system_status test session with transaction rollback.

    Each test gets a fresh session that automatically rolls back changes.
    pytest-xdist compatible: Uses worker-specific database name.
    """
    import os
    from system_status.session import create_status_engine

    # Override database name for this session
    original_db = os.getenv('STATUS_DB_NAME', 'system_status')
    os.environ['STATUS_DB_NAME'] = worker_db_name

    try:
        engine, SessionLocal = create_status_engine()
        connection = engine.connect()
        transaction = connection.begin()
        session = SessionLocal(bind=connection)

        yield session

        session.close()
        transaction.rollback()
        connection.close()
    finally:
        os.environ['STATUS_DB_NAME'] = original_db
        engine.dispose()


@pytest.fixture(scope='session')
def app(test_databases, worker_db_name):
    """
    Create Flask app for testing.

    Uses session scope so the app is created once per test session.
    Configures app for testing mode with CSRF disabled and test database for system_status.
    Rebuilds system_status connection string to use test database.
    pytest-xdist compatible: Uses worker-specific database name.
    """
    import os
    from webapp.run import create_app
    import system_status.session

    # Configure for testing
    os.environ['FLASK_ENV'] = 'testing'

    # CRITICAL: Reinitialize system_status connection string with test database
    # The module may have been imported before STATUS_DB_NAME was set
    system_status.session.init_status_db_defaults()

    app = create_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False

    # Verify the app is using worker-specific test database
    assert worker_db_name in app.config['SQLALCHEMY_BINDS']['system_status'], \
        f"Flask app not using test database '{worker_db_name}': {app.config['SQLALCHEMY_BINDS']['system_status']}"

    return app


@pytest.fixture
def client(app):
    """
    Create Flask test client.

    Returns an unauthenticated test client. Use this for testing
    endpoints that don't require authentication.
    """
    return app.test_client()


@pytest.fixture
def auth_client(client, session, app):
    """
    Create authenticated test client (logged in as benkirk).

    Simulates a logged-in user by setting Flask-Login session data.
    The user 'benkirk' is used as it exists in the test database.
    """
    from sam.core.users import User

    with client:
        # Get benkirk user
        user = User.get_by_username(session, 'benkirk')
        assert user is not None, "Test user 'benkirk' not found in database"

        # Simulate login by setting session
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user.user_id)
            sess['_fresh'] = True

        yield client
