#!/usr/bin/env python3
import os
import uuid
import time
os.environ['FLASK_ACTIVE'] = '1'

from flask import Flask, redirect, request, make_response, url_for
from flask_login import LoginManager, current_user
import sam.session
import system_status.session

from webapp.extensions import db, cache
from webapp.admin import admin_bp, init_admin
from webapp.auth import bp as auth_bp
from webapp.dashboards.user import bp as user_dashboard_bp
from webapp.dashboards.admin import bp as admin_dashboard_bp
from webapp.dashboards.status import bp as status_dashboard_bp
from webapp.dashboards.allocations import bp as allocations_dashboard_bp
from webapp.dashboards.project_members import bp as project_members_bp
from webapp.auth.models import AuthUser
from webapp.utils.rbac import rbac_context_processor
from sam.core.users import User
from webapp.api.v1.projects import bp as api_projects_bp
from webapp.api.v1.users import bp as api_users_bp
from webapp.api.v1.charges import bp as api_charges_bp
from webapp.api.v1.status import bp as api_status_bp
from webapp.api.v1.allocations import bp as api_allocations_bp
from webapp.api.v1.health import bp as api_health_bp
from webapp.api.v1.directory_access import bp as api_directory_access_bp
from webapp.api.v1.project_access import bp as api_project_access_bp
from webapp.api.v1.fstree_access import bp as api_fstree_access_bp
from webapp.config import get_webapp_config
from webapp.logging_config import configure_logging


def create_app(*, config_overrides: dict | None = None):
    """Build and return a configured Flask application.

    Args:
        config_overrides: Optional dict merged into `app.config` AFTER the
            default values are populated but BEFORE `db.init_app(app)` runs.
            Used by the test suite to point Flask-SQLAlchemy at an isolated
            test database and/or to supply a SAVEPOINT-scoped connection via
            `SQLALCHEMY_ENGINE_OPTIONS['creator']` + `StaticPool`. Production
            callers pass no argument and see identical behavior.
    """
    import os

    # Load and validate environment-based configuration
    cfg = get_webapp_config()
    cfg.validate()

    app = Flask(__name__)

    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    # Apply all UPPERCASE class attributes as Flask config
    app.config.from_object(cfg)

    # SECRET_KEY must always be set explicitly (not in config class to avoid accidental exposure)
    app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY')
    if not app.config['SECRET_KEY']:
        raise ValueError(
            "FLASK_SECRET_KEY environment variable must be set. "
            "Generate one with: python -c 'import secrets; print(secrets.token_hex(32))'"
        )

    # Flask-SQLAlchemy configuration (connection strings come from session modules)
    app.config['SQLALCHEMY_DATABASE_URI'] = sam.session.connection_string
    app.config['SQLALCHEMY_BINDS'] = {
        'system_status': system_status.session.connection_string,
    }

    # Check if SSL is required (read from config class, which already parsed the env var)
    require_ssl = cfg.SAM_DB_REQUIRE_SSL

    # Production-ready connection pool configuration
    engine_options = {
        'pool_size': 10,           # Number of connections to maintain
        'max_overflow': 20,        # Additional connections when pool is exhausted
        'pool_pre_ping': True,     # Verify connections before using
        'pool_recycle': 3600,      # Recycle connections after 1 hour
        'echo': False,             # Set to True for SQL debugging
    }

    # Add SSL configuration if required
    if require_ssl:
        engine_options['connect_args'] = {'ssl': {'ssl_disabled': False}}

    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_options

    # Apply caller-supplied overrides AFTER defaults, BEFORE extensions bind.
    # The test suite uses this to point Flask-SQLAlchemy at the mysql-test
    # container and/or to supply a pre-existing SAVEPOINT-scoped connection.
    if config_overrides:
        app.config.update(config_overrides)

    # Initialize db with app
    db.init_app(app)

    # Initialize caching (NullCache when testing to avoid stale state between tests)
    app.config.setdefault('CACHE_TYPE', 'SimpleCache')
    app.config.setdefault('CACHE_DEFAULT_TIMEOUT', 300)
    if app.config.get('TESTING') or os.environ.get('FLASK_ENV') == 'testing':
        app.config['CACHE_TYPE'] = 'NullCache'
    cache.init_app(app)

    # =========================================================================
    # AUDIT LOGGING INITIALIZATION
    # =========================================================================
    # Track INSERT/UPDATE/DELETE operations on SAM database models
    # Excludes: system_status database, ApiCredentials model - see audit/events.py
    if app.config.get('AUDIT_ENABLED', True):
        from webapp.audit import init_audit
        init_audit(
            app=app,
            db=db,
            logfile_path=app.config.get('AUDIT_LOG_PATH', '/var/log/sam/model_audit.log')
        )
    # =========================================================================

    # =========================================================================
    # APPLICATION LOGGING + REQUEST ID TRACKING
    # =========================================================================
    configure_logging(app)

    @app.before_request
    def _set_request_id():
        from flask import g, request
        g.request_id    = request.headers.get('X-Request-ID', str(uuid.uuid4()))
        g.request_start = time.monotonic()

    @app.after_request
    def _log_request(response):
        from flask import g, request
        elapsed_ms = round((time.monotonic() - g.request_start) * 1000, 1)
        response.headers['X-Request-ID'] = g.request_id
        app.logger.info(
            '%s %s → %s  (%.1f ms)  rid=%s',
            request.method, request.path, response.status_code,
            elapsed_ms, g.request_id,
        )
        if elapsed_ms > 5000:
            app.logger.warning(
                'Slow request: %.1f ms  %s %s',
                elapsed_ms, request.method, request.path,
            )
        return response
    # =========================================================================

    # Initialize OIDC (Authlib) when configured
    if app.config.get('AUTH_PROVIDER') == 'oidc':
        from authlib.integrations.flask_client import OAuth
        oauth = OAuth(app)
        oauth.register(
            'entra',
            client_id=app.config['OIDC_CLIENT_ID'],
            client_secret=app.config['OIDC_CLIENT_SECRET'],
            server_metadata_url=app.config['OIDC_ISSUER'] + '/.well-known/openid-configuration',
            client_kwargs={
                'scope': app.config.get('OIDC_SCOPES', 'openid email profile'),
                'code_challenge_method': 'S256',
            },
        )
        app.extensions['oauth'] = oauth

    # Initialize Flask-Login
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'

    @login_manager.unauthorized_handler
    def unauthorized():
        """Handle unauthorized access — return HX-Redirect for HTMX requests."""
        if request.headers.get('HX-Request'):
            response = make_response('', 401)
            response.headers['HX-Redirect'] = url_for('auth.login')
            return response
        return redirect(url_for('auth.login'))

    @login_manager.user_loader
    def load_user(user_id):
        """Load user by ID for Flask-Login."""
        sam_user = db.session.query(User).filter_by(user_id=int(user_id)).first()
        if sam_user:
            return AuthUser(sam_user, dev_group_mapping=app.config.get('DEV_GROUP_MAPPING'))
        return None

    # Register context processor for RBAC in templates
    app.context_processor(rbac_context_processor)

    # Expose optional build provenance (set by CI via Docker build args) to all templates
    @app.context_processor
    def build_info_context_processor():
        sha = os.getenv('GIT_SHA', '').strip()
        if sha:
            sha = sha[:7]
        return {
            'build_info': {
                'sha': sha or None,
                'date': os.getenv('BUILD_DATE', '').strip() or None,
            }
        }

    # Register teardown handler for automatic session cleanup
    @app.teardown_appcontext
    def shutdown_session(exception=None):
        db.session.remove()

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(user_dashboard_bp)
    app.register_blueprint(admin_dashboard_bp)
    app.register_blueprint(status_dashboard_bp)
    app.register_blueprint(allocations_dashboard_bp)
    app.register_blueprint(project_members_bp)
    # NOTE: admin_bp blueprint removed - Flask-Admin handles /database routing
    # app.register_blueprint(admin_bp, url_prefix='/database')

    # Register API blueprints
    app.register_blueprint(api_projects_bp, url_prefix='/api/v1/projects')
    app.register_blueprint(api_users_bp, url_prefix='/api/v1/users')
    app.register_blueprint(api_charges_bp, url_prefix='/api/v1')
    app.register_blueprint(api_status_bp, url_prefix='/api/v1/status')
    app.register_blueprint(api_allocations_bp, url_prefix='/api/v1/allocations')
    app.register_blueprint(api_health_bp, url_prefix='/api/v1/health')
    app.register_blueprint(api_directory_access_bp, url_prefix='/api/v1/directory_access')
    app.register_blueprint(api_project_access_bp, url_prefix='/api/v1/project_access')
    app.register_blueprint(api_fstree_access_bp, url_prefix='/api/v1/fstree_access')

    # Register centralized formatting filters (fmt_number, fmt_pct, fmt_date, fmt_size)
    import sam.fmt as fmt
    fmt.register_jinja_filters(app)

    # In dev, Jinja's mtime-based auto-reload doesn't reliably detect template
    # changes through Docker's bind-mount/watch-sync — the file mtime in the
    # container updates correctly but the running Jinja env still serves the
    # cached compile. Disable the env's template cache entirely in debug mode
    # so every render re-reads from disk. Negligible cost in dev, no effect
    # on production.
    if app.config.get('DEBUG'):
        app.jinja_env.cache = None

    # Initialize Flask-Admin
    init_admin(app)

    # Auto-login middleware for development (enabled via DISABLE_AUTH=1)
    from webapp.utils.dev_auth import auto_login_middleware
    auto_login_middleware(app, db)

    # Home page redirect
    @app.route('/')
    def index():
        if current_user.is_authenticated:
            # Redirect admin-capable users to admin dashboard, others to
            # user dashboard. Gate on the same permission that gates the
            # Admin nav tab (see templates/dashboards/base.html), so the
            # redirect target is always something the user can actually
            # access — including users granted admin via
            # USER_PERMISSION_OVERRIDES rather than a group bundle.
            from webapp.utils.rbac import has_permission, Permission
            if has_permission(current_user, Permission.ACCESS_ADMIN_DASHBOARD):
                return redirect(url_for('admin_dashboard.index'))
            else:
                return redirect(url_for('user_dashboard.index'))
        return redirect(url_for('status_dashboard.index'))

    return app


if __name__ == '__main__':
    app = create_app()
    # Port is configurable via WEBAPP_PORT so that an interactive debug
    # launch (utils/run-webui-dbg.sh) can coexist with `docker compose up
    # webdev` (which binds host port 5050). Default stays 5050 for backwards
    # compatibility with existing tooling that expects it.
    app.run(host='0.0.0.0', debug=True, port=int(os.environ.get('WEBAPP_PORT', 5050)))
