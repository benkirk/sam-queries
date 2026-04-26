"""pytest fixtures and safety guards for the SAM test suite.

The most important thing this file does: refuse to run against any database
other than the dedicated `mysql-test` container. Production safety depends
on the allowlist check in `pytest_configure` firing before any fixture or
test module touches a connection.

Any run of `pytest` MUST set `SAM_TEST_DB_URL` to a SQLAlchemy URL pointing
at an allowed host/port.
"""
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import sessionmaker


# ---- Safety allowlist -----------------------------------------------------
#
# (host, port) pairs that are permitted as a test database target. The
# mysql-test compose service binds host port 3307 on localhost; from inside
# a container on the sam-network it would be reachable as mysql-test:3306.
# Any other combination — in particular the main dev DB on 3306, or any
# remote production host — is rejected.

_ALLOWED_TEST_TARGETS = {
    ("127.0.0.1", 3307),
    ("localhost", 3307),
    ("mysql-test", 3306),
}


def _verify_test_target(url) -> None:
    """Raise pytest.exit if `url` does not point at an allowed test DB."""
    try:
        parsed = make_url(url)
    except Exception as exc:
        pytest.exit(
            f"SAM_TEST_DB_URL is not a valid SQLAlchemy URL: {exc}",
            returncode=2,
        )

    host = parsed.host or ""
    port = parsed.port or 0

    if (host, port) not in _ALLOWED_TEST_TARGETS:
        allowed = ", ".join(f"{h}:{p}" for h, p in sorted(_ALLOWED_TEST_TARGETS))
        pytest.exit(
            "\n"
            "=" * 70 + "\n"
            "REFUSING TO RUN tests against this database.\n"
            f"  SAM_TEST_DB_URL points at: {host}:{port}\n"
            f"  Allowed targets:           {allowed}\n"
            "\n"
            "Start the isolated test container with:\n"
            "  docker compose --profile test up -d mysql-test\n"
            "\n"
            "Then set:\n"
            "  export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'\n"
            + "=" * 70,
            returncode=2,
        )


def pytest_configure(config):
    """Runs once at session start, before collection. Fail fast here."""
    # FLASK_ACTIVE=1 must be set BEFORE any test module imports `system_status.*`,
    # because `system_status.base.StatusBase` is resolved at import time. Without
    # this, `StatusBase = declarative_base()` (standalone) and the status models
    # bind to a private metadata that Flask-SQLAlchemy can't see — INSERT/UPDATE
    # ops then fall back to the default sam bind. The Phase 4f status_session
    # fixture relies on `StatusBase = db.Model` so the routing engages cleanly.
    os.environ.setdefault("FLASK_ACTIVE", "1")
    os.environ.setdefault("FLASK_CONFIG", "testing")
    os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key")

    url = os.environ.get("SAM_TEST_DB_URL")
    if not url:
        pytest.exit(
            "\n"
            "=" * 70 + "\n"
            "The test suite requires SAM_TEST_DB_URL to be set.\n"
            "\n"
            "Example:\n"
            "  docker compose --profile test up -d mysql-test\n"
            "  export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'\n"
            "  pytest\n"
            + "=" * 70,
            returncode=2,
        )

    _verify_test_target(url)

    # Put src/ on the import path so tests can `from sam...` without install,
    # and put tests/ on the path so tests can `from factories import ...`
    # without requiring tests/ itself to be a package.
    proj_root = Path(__file__).parent.parent
    src_path = str(proj_root / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    tests_path = str(Path(__file__).parent)
    if tests_path not in sys.path:
        sys.path.insert(0, tests_path)


# ---- Test-only RBAC bundle ------------------------------------------------
#
# Registers a synthetic group bundle ``admin-testing-only`` that grants
# every ``Permission``, used by stub-user tests that need a "system
# admin" without depending on the contents of any real production
# bundle (csg / nusd / hsg). Keeping this bundle defined inside the
# test session means production code never sees it — `GROUP_PERMISSIONS`
# in `webapp/utils/rbac.py` stays restricted to real POSIX groups.
#
# Tests opt in by writing `roles=['admin-testing-only']` on their stub
# user. The fixture is autouse + session-scoped so the registration
# happens once per xdist worker before any test runs and is removed at
# the end of the session.

ADMIN_TESTING_BUNDLE = 'admin-testing-only'


@pytest.fixture(scope="session", autouse=True)
def _register_admin_testing_bundle():
    from webapp.utils.rbac import GROUP_PERMISSIONS, Permission

    assert ADMIN_TESTING_BUNDLE not in GROUP_PERMISSIONS, (
        f"{ADMIN_TESTING_BUNDLE!r} collides with a real bundle — rename "
        "the test-only bundle in tests/conftest.py."
    )
    GROUP_PERMISSIONS[ADMIN_TESTING_BUNDLE] = set(Permission)
    try:
        yield
    finally:
        GROUP_PERMISSIONS.pop(ADMIN_TESTING_BUNDLE, None)


# ---- Engine / session fixtures --------------------------------------------


@pytest.fixture(scope="session")
def test_db_url() -> str:
    """Return the validated test DB URL."""
    return os.environ["SAM_TEST_DB_URL"]


@pytest.fixture(scope="session")
def engine(test_db_url):
    """SQLAlchemy engine bound to the isolated mysql-test container."""
    eng = create_engine(test_db_url, future=True, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def SessionFactory(engine):
    """Session factory for tests that want their own short-lived sessions."""
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@pytest.fixture
def session(engine):
    """Per-test SQLAlchemy session with SAVEPOINT-based rollback isolation.

    The pattern:
      1. Open a raw connection and begin an outer transaction on it.
      2. Bind the Session to that connection with
         `join_transaction_mode="create_savepoint"` — every call to
         `session.begin()`/`session.commit()`/`session.rollback()` from
         test code becomes a SAVEPOINT operation inside the outer
         transaction, NOT a real commit/rollback.
      3. At teardown, roll back the outer transaction. Nothing escapes.

    This is what lets 12 xdist workers share one `sam_test` database
    without stepping on each other — and it lets a read-only test call
    `session.rollback()` (as the XRAS view tests do) without tearing
    down the fixture's outer transaction.
    """
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        future=True,
        join_transaction_mode="create_savepoint",
    )
    sess = Session()
    try:
        yield sess
    finally:
        sess.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


# ---- Flask app / client fixtures (Phase 4) --------------------------------
#
# These exist because `src/webapp/run.py::create_app()` was extended with a
# `config_overrides=` kwarg that lets the caller replace SQLALCHEMY config
# after defaults land but before `db.init_app(app)` runs. Without that kwarg,
# Flask-SQLAlchemy's URL comes from a module-level global in `sam.session`
# that's set at import time — which is why the legacy suite has the
# elaborate env-var-before-import dance in `tests/conftest.py`.


@pytest.fixture(scope="session")
def status_db_path(tmp_path_factory):
    """Per-worker SQLite tempfile holding the `system_status` schema.

    Why SQLite (not the mysql-test container, like the legacy suite did
    with `system_status_test_<workerid>` databases): the `system_status`
    models use only portable column types (DateTime, Integer, Float,
    Boolean, String, ForeignKey), so SQLAlchemy's dialect-aware DDL
    materializes the schema cleanly via `db.create_all(bind_key=...)`.
    No per-worker DB creation, no env-var ordering, no `init_status_db_defaults()`
    re-call dance — just a temp file path that goes into SQLALCHEMY_BINDS.

    `tmp_path_factory.getbasetemp()` returns a worker-scoped directory
    under pytest's tmp dir, so concurrent xdist workers get isolated
    files automatically without touching PYTEST_XDIST_WORKER ourselves.
    """
    base = tmp_path_factory.getbasetemp()
    return str(base / "status_test.sqlite")


@pytest.fixture(scope="session")
def status_db_url(status_db_path):
    """SQLite URL used as the `system_status` bind in the test app config."""
    return f"sqlite:///{status_db_path}"


@pytest.fixture(scope="session")
def app(test_db_url, status_db_url):
    """Flask application bound to the mysql-test container + a SQLite status DB.

    Uses `create_app(config_overrides=...)` to point Flask-SQLAlchemy at
    the test DB without touching `sam.session.connection_string`. Everything
    else (API_KEYS, TESTING=True, WTF_CSRF_ENABLED=False,
    ALLOCATION_USAGE_CACHE_TTL=0) comes from `TestingConfig`, selected via
    `FLASK_CONFIG='testing'`.

    The `system_status` bind points at a per-worker SQLite tempfile (see
    `status_db_path` / `status_db_url`). Schema is materialized once via
    `db.create_all(bind_key='system_status')` after `create_app()` returns
    — `db.metadatas['system_status']` knows about the status models because
    importing `system_status.models` registers them on the bind-keyed
    metadata at import time.

    Session-scoped because `create_app()` is expensive (registers all
    blueprints, initializes extensions) — one app per xdist worker.
    """
    os.environ.setdefault("FLASK_CONFIG", "testing")
    os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key")
    # FLASK_ACTIVE=1 makes `system_status.base.StatusBase` resolve to
    # `webapp.extensions.db.Model`, so the status models bind to the
    # Flask-SQLAlchemy registry instead of a standalone declarative base.
    # Required for `db.session.query(DerechoStatus)` to route through
    # the system_status bind in test code and routes alike.
    os.environ.setdefault("FLASK_ACTIVE", "1")
    from webapp.run import create_app

    flask_app = create_app(config_overrides={
        "SQLALCHEMY_DATABASE_URI": test_db_url,
        "SQLALCHEMY_BINDS": {
            "system_status": status_db_url,
        },
    })

    # Regression guard — mirrors the legacy app fixture. Fails loudly if
    # TestingConfig didn't load (which would mean API_KEYS and cache
    # disables are all wrong).
    assert flask_app.config["ALLOCATION_USAGE_CACHE_TTL"] == 0, (
        "TestingConfig not loaded — check FLASK_CONFIG env var and "
        "webapp.config.get_webapp_config() class selection"
    )

    # Materialize the system_status schema in the SQLite tempfile.
    # Mirrors the canonical pattern from scripts/setup_status_db.py
    # (StatusBase.metadata.create_all). The model imports populate
    # `db.metadatas['system_status']` thanks to `__bind_key__`.
    import system_status.models  # noqa: F401  — populate metadata registry
    with flask_app.app_context():
        from webapp.extensions import db
        db.create_all(bind_key="system_status")

    return flask_app


@pytest.fixture
def client(app):
    """Unauthenticated Flask test client — one per test."""
    return app.test_client()


@pytest.fixture
def auth_client(client, session):
    """Test client logged in as `benkirk` — admin-equivalent via
    `USER_PERMISSION_OVERRIDES['benkirk']` in `webapp.utils.rbac`.

    Uses Flask-Login's session-cookie mechanism (`client.session_transaction()`
    → `sess['_user_id']`). This triggers the `load_user()` callback in the
    login_manager. `benkirk` resolves to the full Permission set via the
    user-override layer — no POSIX-group membership required, no
    `role_user` rows required.
    """
    from sam import User

    user = User.get_by_username(session, "benkirk")
    assert user is not None, (
        "benkirk must be preserved in the mysql-test snapshot — "
        "see project_test_db_fixtures.md"
    )
    with client:
        with client.session_transaction() as sess_data:
            sess_data["_user_id"] = str(user.user_id)
            sess_data["_fresh"] = True
        yield client


@pytest.fixture
def non_admin_client(client, session):
    """Test client logged in as a non-admin user from the snapshot.

    Picks any active user who isn't `benkirk` — they're not in
    `USER_PERMISSION_OVERRIDES`, and the obfuscated test snapshot
    typically gives them no POSIX-group membership in any
    `GROUP_PERMISSIONS` bundle, so they end up with no permissions.
    Used by tests that need to verify 403-on-admin-only-routes behavior.

    We can't use a factory-built user here: `load_user` goes through
    Flask-SQLAlchemy's `db.session` (its own connection, not the raw
    test session), so it only sees committed snapshot rows.
    """
    from sam import User

    user = (
        session.query(User)
        .filter(User.active == True, User.username != "benkirk")
        .order_by(User.user_id)
        .first()
    )
    assert user is not None, "snapshot has no active non-benkirk users"
    with client:
        with client.session_transaction() as sess_data:
            sess_data["_user_id"] = str(user.user_id)
            sess_data["_fresh"] = True
        yield client


# ---- system_status fixtures (Phase 4f) ------------------------------------
#
# The status fixtures use `db.session` directly inside an app context — both
# the test code and the Flask routes route system_status queries through the
# same Flask-SQLAlchemy session, which is bound to the per-worker SQLite
# tempfile via the `system_status` bind. Per-test isolation is via DELETE
# (not SAVEPOINT): SQLite's DELETE is fast, the routes commit normally, and
# everything lives in the worker-scoped tempfile that pytest cleans up.


def _truncate_status_tables(db):
    """Wipe all system_status tables in dependency order.

    Mirrors the iteration pattern from scripts/cleanup_status_data.py but
    uses SQLAlchemy's `sorted_tables` so the FK ordering is automatic.
    `reversed(sorted_tables)` deletes children before parents.
    """
    for tbl in reversed(db.metadatas["system_status"].sorted_tables):
        db.session.execute(tbl.delete())
    db.session.commit()


@pytest.fixture
def status_session(app):
    """Per-test session for `system_status` schema, pre-cleaned.

    Yields the Flask-SQLAlchemy `db.session`, wrapped in an app context.
    The session routes queries on system_status models (DerechoStatus,
    CasperStatus, …) through the per-worker SQLite engine. Tests can use
    it just like a normal session — `add()`, `flush()`, `commit()`,
    `query()` — and the same `db.session` inside Flask routes will see
    the committed data because both go through the same engine.

    Cleanup strategy: DELETE all status tables on entry. SQLite tempfile
    + per-test DELETE is faster than SAVEPOINT bridging and isolates
    tests cleanly without any session-mode mucking.
    """
    with app.app_context():
        from webapp.extensions import db

        _truncate_status_tables(db)
        try:
            yield db.session
        finally:
            db.session.remove()


@pytest.fixture
def api_key_client(client, session, app):
    """Test client authenticated via HTTP Basic Auth API key.

    Mirrors the legacy `api_key_client` from tests/api/conftest.py — wraps
    the Flask test client to inject `Authorization: Basic <collector:test-api-key>`
    on every request. Used by `test_status_endpoints.py` for POST routes
    decorated with `@api_key_required`.

    Also sets up a Flask-Login session as benkirk so GET routes decorated
    with `@login_required` work in the same test class.

    The bcrypt hash for `test-api-key` is precomputed in TestingConfig
    (rounds=4 for fast test execution), so we don't need to recompute it
    on every fixture call.
    """
    import base64
    from sam.core.users import User

    test_key = "test-api-key"
    credentials = base64.b64encode(f"collector:{test_key}".encode()).decode("ascii")
    auth_header = {"Authorization": f"Basic {credentials}"}

    with client:
        user = User.get_by_username(session, "benkirk")
        assert user is not None, "benkirk must exist in the snapshot"
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.user_id)
            sess["_fresh"] = True

        original_post = client.post
        original_get = client.get
        original_patch = client.patch
        original_delete = client.delete

        def post_with_auth(path, **kwargs):
            kwargs.setdefault("headers", {}).update(auth_header)
            return original_post(path, **kwargs)

        def get_with_auth(path, **kwargs):
            kwargs.setdefault("headers", {}).update(auth_header)
            return original_get(path, **kwargs)

        def patch_with_auth(path, **kwargs):
            kwargs.setdefault("headers", {}).update(auth_header)
            return original_patch(path, **kwargs)

        def delete_with_auth(path, **kwargs):
            kwargs.setdefault("headers", {}).update(auth_header)
            return original_delete(path, **kwargs)

        client.post = post_with_auth
        client.get = get_with_auth
        client.patch = patch_with_auth
        client.delete = delete_with_auth

        yield client


# ---- Representative fixtures ----------------------------------------------
#
# Layer 1 of the two-layer test data strategy (see docs/plans/REFACTOR_TESTING.md).
#
# These fixtures pick ANY row from the snapshot that matches a structural
# shape — "any active project with allocations", "any HPC resource", "any
# user with >=2 active projects". They are session-scoped (one query at
# suite startup) and return IDs, not ORM instances — each test fetches a
# fresh instance bound to its own session via session.get().
#
# This layer is for READ-PATH tests that want snapshot-shaped data but
# don't care about specific projcodes/usernames. Tests built on this layer
# survive snapshot refreshes as long as AT LEAST ONE row of each shape
# still exists.
#
# Layer 2 (factories) lives in tests/factories/ and is for WRITE-PATH
# tests that need to assert on exact counts/values.


def _session_for_setup(engine):
    """One-shot session for fixture setup queries, separate from test session."""
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    return Session()


@pytest.fixture(scope="session")
def _active_project_id(engine):
    """ID of any project with at least one account AND at least one active allocation."""
    from sqlalchemy import text as _text
    with _session_for_setup(engine) as s:
        row = s.execute(_text("""
            SELECT p.project_id
            FROM project p
            JOIN account a ON a.project_id = p.project_id
            JOIN allocation al ON al.account_id = a.account_id
            WHERE p.active = 1
              AND al.start_date <= NOW()
              AND (al.end_date IS NULL OR al.end_date >= NOW())
            GROUP BY p.project_id
            HAVING COUNT(DISTINCT al.allocation_id) >= 1
            ORDER BY p.project_id
            LIMIT 1
        """)).first()
    assert row is not None, "snapshot has no active projects with allocations"
    return row[0]


@pytest.fixture(scope="session")
def _multi_project_user_id(engine):
    """ID of any active user who belongs to >=2 active projects (for dashboard tests)."""
    from sqlalchemy import text as _text
    with _session_for_setup(engine) as s:
        row = s.execute(_text("""
            SELECT u.user_id
            FROM users u
            JOIN account_user au ON au.user_id = u.user_id
            JOIN account a ON a.account_id = au.account_id
            JOIN project p ON p.project_id = a.project_id
            WHERE u.active = 1 AND u.locked = 0
              AND p.active = 1
            GROUP BY u.user_id
            HAVING COUNT(DISTINCT p.project_id) >= 2
            ORDER BY u.user_id
            LIMIT 1
        """)).first()
    assert row is not None, "snapshot has no multi-project active users"
    return row[0]


@pytest.fixture(scope="session")
def _hpc_resource_id(engine):
    """ID of any currently-active HPC resource.

    "Active" means commissioned on or before today AND either still
    commissioned (NULL decommission_date) or decommissioned in the future.
    The low-ID HPC resources in the obfuscated snapshot are long-retired
    (Bluefire, Yellowstone, Jellystone, ...) so ORDER BY resource_id
    without this filter returns a dead resource with no fstree presence.
    """
    from sqlalchemy import text as _text
    with _session_for_setup(engine) as s:
        row = s.execute(_text("""
            SELECT r.resource_id
            FROM resources r
            JOIN resource_type rt ON rt.resource_type_id = r.resource_type_id
            WHERE rt.resource_type = 'HPC'
              AND (r.commission_date IS NULL OR r.commission_date <= NOW())
              AND (r.decommission_date IS NULL OR r.decommission_date >= NOW())
            ORDER BY r.resource_id
            LIMIT 1
        """)).first()
    assert row is not None, "snapshot has no currently-active HPC resources"
    return row[0]


@pytest.fixture(scope="session")
def _subtree_project_id(engine):
    """ID of any active project with >=3 active child projects.

    Used by subtree-rollup tests that need a non-leaf project for MPTT
    aggregation. The threshold of 3 children is arbitrary — just large
    enough to produce a meaningful rollup and ensure we don't pick a
    project that happens to have 1 descendant.
    """
    from sqlalchemy import text as _text
    with _session_for_setup(engine) as s:
        row = s.execute(_text("""
            SELECT p.project_id
            FROM project p
            JOIN project c ON c.parent_id = p.project_id
            WHERE p.active = 1 AND c.active = 1
            GROUP BY p.project_id
            HAVING COUNT(c.project_id) >= 3
            ORDER BY p.project_id
            LIMIT 1
        """)).first()
    assert row is not None, "snapshot has no active projects with >=3 active children"
    return row[0]


@pytest.fixture
def active_project(session, _active_project_id):
    """A Project row bound to the test session. Has at least one active allocation."""
    from sam import Project
    return session.get(Project, _active_project_id)


@pytest.fixture
def multi_project_user(session, _multi_project_user_id):
    """A User bound to the test session with >=2 active projects."""
    from sam import User
    return session.get(User, _multi_project_user_id)


@pytest.fixture
def hpc_resource(session, _hpc_resource_id):
    """An HPC Resource bound to the test session."""
    from sam import Resource
    return session.get(Resource, _hpc_resource_id)


@pytest.fixture
def subtree_project(session, _subtree_project_id):
    """A Project bound to the test session that has >=3 active child projects."""
    from sam import Project
    return session.get(Project, _subtree_project_id)


@pytest.fixture(scope="session")
def _inheriting_project_lookup(engine):
    """(project_id, resource_name) for any project whose active HPC/DAV allocation
    on an active resource is inheriting (parent_allocation_id IS NOT NULL).

    Used by tests that need to verify pool-aware rolling charges. Returns
    None if the snapshot has no inheriting allocations.
    """
    from sqlalchemy import text as _text
    with _session_for_setup(engine) as s:
        row = s.execute(_text("""
            SELECT p.project_id, r.resource_name
            FROM project p
            JOIN account a ON a.project_id = p.project_id
            JOIN allocation al ON al.account_id = a.account_id
            JOIN resources r ON r.resource_id = a.resource_id
            JOIN resource_type rt ON rt.resource_type_id = r.resource_type_id
            WHERE p.active = 1
              AND a.deleted = 0
              AND al.parent_allocation_id IS NOT NULL
              AND al.start_date <= NOW()
              AND (al.end_date IS NULL OR al.end_date >= NOW())
              AND rt.resource_type IN ('HPC', 'DAV')
              AND (r.commission_date IS NULL OR r.commission_date <= NOW())
              AND (r.decommission_date IS NULL OR r.decommission_date >= NOW())
            ORDER BY p.project_id
            LIMIT 1
        """)).first()
    if row is None:
        return None
    return (row[0], row[1])


@pytest.fixture
def inheriting_project(session, _inheriting_project_lookup):
    """A (Project, resource_name) pair where the project has an inheriting
    allocation on the named resource. Skips the test if the snapshot has
    no inheriting allocations.
    """
    if _inheriting_project_lookup is None:
        pytest.skip("snapshot has no inheriting allocations")
    project_id, resource_name = _inheriting_project_lookup
    from sam import Project
    return session.get(Project, project_id), resource_name


# ---- "any row of X" fixtures ----------------------------------------------
#
# Simple function-scoped lookups used by the `.update()` contract tests in
# test_manage_*.py and by read-only property tests. Each runs one ~1ms
# SELECT with a skip fallback when the row doesn't exist. Session-cached
# ID lookups would be marginally faster but are not worth the complexity
# at this call volume.
#
# Naming: `any_X` → "pick any row of type X, don't care which". Contrast
# with the shape-constrained fixtures above (`active_project` requires
# allocations, `hpc_resource` requires active HPC, etc.).


def _any_or_skip(session, model, label):
    row = session.query(model).first()
    if row is None:
        pytest.skip(f"No {label} in database")
    return row


@pytest.fixture
def any_facility(session):
    from sam.resources.facilities import Facility
    return _any_or_skip(session, Facility, "facilities")


@pytest.fixture
def any_panel(session):
    from sam.resources.facilities import Panel
    return _any_or_skip(session, Panel, "panels")


@pytest.fixture
def any_panel_session(session):
    from sam.resources.facilities import PanelSession
    return _any_or_skip(session, PanelSession, "panel sessions")


@pytest.fixture
def any_allocation_type(session):
    from sam.accounting.allocations import AllocationType
    return _any_or_skip(session, AllocationType, "allocation types")


@pytest.fixture
def any_organization(session):
    from sam.core.organizations import Organization
    return _any_or_skip(session, Organization, "organizations")


@pytest.fixture
def any_institution(session):
    from sam.core.organizations import Institution
    return _any_or_skip(session, Institution, "institutions")


@pytest.fixture
def any_aoi(session):
    from sam.projects.areas import AreaOfInterest
    return _any_or_skip(session, AreaOfInterest, "areas of interest")


@pytest.fixture
def any_aoi_group(session):
    from sam.projects.areas import AreaOfInterestGroup
    return _any_or_skip(session, AreaOfInterestGroup, "AOI groups")


@pytest.fixture
def any_contract(session):
    from sam.projects.contracts import Contract
    return _any_or_skip(session, Contract, "contracts")


@pytest.fixture
def any_contract_source(session):
    from sam.projects.contracts import ContractSource
    return _any_or_skip(session, ContractSource, "contract sources")


@pytest.fixture
def any_nsf_program(session):
    from sam.projects.contracts import NSFProgram
    return _any_or_skip(session, NSFProgram, "NSF programs")


@pytest.fixture
def any_resource(session):
    from sam.resources.resources import Resource
    return _any_or_skip(session, Resource, "resources")


@pytest.fixture
def any_resource_type(session):
    from sam.resources.resources import ResourceType
    return _any_or_skip(session, ResourceType, "resource types")


@pytest.fixture
def any_machine(session):
    from sam.resources.machines import Machine
    return _any_or_skip(session, Machine, "machines")


@pytest.fixture
def any_queue(session):
    from sam.resources.machines import Queue
    return _any_or_skip(session, Queue, "queues")
