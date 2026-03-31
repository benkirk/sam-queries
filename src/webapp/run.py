#!/usr/bin/env python3
import os
import uuid
import time
os.environ['FLASK_ACTIVE'] = '1'

from flask import Flask, redirect, url_for
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
from webapp.auth.models import AuthUser
from webapp.utils.rbac import rbac_context_processor
from sam.core.users import User
from webapp.api.v1.projects import bp as api_projects_bp
from webapp.api.v1.users import bp as api_users_bp
from webapp.api.v1.charges import bp as api_charges_bp
from webapp.api.v1.status import bp as api_status_bp
from webapp.api.v1.allocations import bp as api_allocations_bp
from webapp.api.v1.health import bp as api_health_bp
from webapp.config import get_webapp_config
from webapp.logging_config import configure_logging


def create_app():
    import os

    # Load and validate environment-based configuration
    cfg = get_webapp_config()
    cfg.validate()

    app = Flask(__name__)

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

    # Initialize Flask-Login
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'

    @login_manager.user_loader
    def load_user(user_id):
        """Load user by ID for Flask-Login."""
        sam_user = db.session.query(User).filter_by(user_id=int(user_id)).first()
        if sam_user:
            return AuthUser(sam_user, dev_role_mapping=app.config.get('DEV_ROLE_MAPPING'))
        return None

    # Register context processor for RBAC in templates
    app.context_processor(rbac_context_processor)

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
    # NOTE: admin_bp blueprint removed - Flask-Admin handles /database routing
    # app.register_blueprint(admin_bp, url_prefix='/database')

    # Register API blueprints
    app.register_blueprint(api_projects_bp, url_prefix='/api/v1/projects')
    app.register_blueprint(api_users_bp, url_prefix='/api/v1/users')
    app.register_blueprint(api_charges_bp, url_prefix='/api/v1')
    app.register_blueprint(api_status_bp, url_prefix='/api/v1/status')
    app.register_blueprint(api_allocations_bp, url_prefix='/api/v1/allocations')
    app.register_blueprint(api_health_bp, url_prefix='/api/v1/health')

    # Register centralized formatting filters (fmt_number, fmt_pct, fmt_date, fmt_size)
    import sam.fmt as fmt
    fmt.register_jinja_filters(app)

    # Initialize Flask-Admin
    init_admin(app)

    # Auto-login middleware for development (enabled via DISABLE_AUTH=1)
    from webapp.utils.dev_auth import auto_login_middleware
    auto_login_middleware(app, db)

    # Home page redirect
    @app.route('/')
    def index():
        if current_user.is_authenticated:
            # Redirect admin users to admin dashboard, regular users to user dashboard
            if 'admin' in current_user.roles:
                return redirect(url_for('admin_dashboard.index'))
            else:
                return redirect(url_for('user_dashboard.index'))
        return redirect(url_for('status_dashboard.index'))

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', debug=True, port=5050)
