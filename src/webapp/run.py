#!/usr/bin/env python3
import os
import re
import socket
import sys
import uuid
import time
from datetime import datetime

# Disarm a module-name shadow, BEFORE any project import.
#
# The dev container runs `python3 ./src/webapp/run.py`, making src/webapp
# `sys.path[0]`. The top-level module name `config` (src/config.py, the
# SAMConfig this package and sam.fmt both import) is then shadowed by THIS
# directory's config.py, and webapp/config.py's own `from config import
# SAMConfig` resolves to itself with an ImportError from a partially
# initialized module. Whether it fires depends on import ORDER, which makes it
# a landmine an unrelated change can re-arm -- adding `sam.notify` to
# sam/__init__.py hoisted sam.fmt to the front of the chain and did exactly
# that. Dropping the script directory is safe: every package is reachable via
# src/, and this file's imports are all package-qualified. Under gunicorn
# sys.path[0] is the cwd, so this is a no-op.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path
               if os.path.abspath(p or os.getcwd()) != _HERE]

_HEALTH_PATH_RE = re.compile(r'^/api/v\d+/health(/|$)')
os.environ['FLASK_ACTIVE'] = '1'

from flask import Flask, redirect, request, make_response, url_for
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, current_user
import sam.session
import system_status.session

from webapp.extensions import db, csrf
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
from webapp.api.v1.queue import bp as api_queue_bp
from webapp.api.v1.wallclock_exemption import bp as api_wallclock_exemption_bp
from webapp.api.v1.admin import bp as api_admin_bp
from webapp.api.xras import bp as api_xras_bp
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

    # Validation is skipped when the caller passes `config_overrides=`, which
    # the test harness does to point Flask-SQLAlchemy at the mysql-test
    # container. Production callers pass none and fail fast on a missing var.
    cfg = get_webapp_config()
    if config_overrides is None:
        cfg.validate()

    app = Flask(__name__)

    # Trust N proxy hops in X-Forwarded-* so request.remote_addr reflects the
    # real client, which is what makes Flask-Limiter's per-IP limiting honest.
    # WARNING: N must be the EXACT number of trusted proxies appending to
    # X-Forwarded-For. Too high and ProxyFix trusts an attacker-supplied entry,
    # letting a client spoof its IP -- hence the default of 1, tuned via
    # PROXYFIX_X_FOR once the real chain is confirmed from the access log's xff.
    from werkzeug.middleware.proxy_fix import ProxyFix
    _pf_for = int(os.environ.get('PROXYFIX_X_FOR', '1'))
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_pf_for, x_proto=1, x_host=1, x_prefix=1)

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

    # Check if SSL is required (read from config class, which already parsed the env var)
    require_ssl = cfg.SAM_DB_REQUIRE_SSL

    # Production-ready connection pool configuration for the main SAM engine.
    engine_options = {
        'pool_size': 10,           # Number of connections to maintain
        'max_overflow': 20,        # Additional connections when pool is exhausted
        'pool_pre_ping': True,     # Verify connections before using
        'pool_recycle': 3600,      # Recycle connections after 1 hour
        'echo': False,             # Set to True for SQL debugging
    }

    # Add SSL configuration if required (MySQL/pymysql syntax — the SAM engine
    # is MySQL; the system_status engine handles its own SSL below since it
    # uses a different driver).
    if require_ssl:
        engine_options['connect_args'] = {'ssl': {'ssl_disabled': False}}

    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_options

    # system_status has its own pool config because it talks to postgres on the
    # shared `csg-postgres` cluster, not the main SAM MySQL. A generous
    # per-worker pool is safe: server-side `idle_session_timeout` reaps idle
    # connections and `pool_recycle=600` mirrors that window client-side. The
    # per-bind dict exists mainly to carry the postgres `application_name` and
    # driver-correct SSL handling below.
    status_pool = {
        'pool_size':     int(os.getenv('STATUS_DB_POOL_SIZE', 10)),
        'max_overflow':  int(os.getenv('STATUS_DB_POOL_MAX_OVERFLOW', 20)),
        'pool_pre_ping': True,
        'pool_recycle':  int(os.getenv('STATUS_DB_POOL_RECYCLE', 600)),
    }
    # Driver-correct connect_args: postgres takes sslmode plus an
    # application_name so pg_stat_activity can attribute a connection to a pod;
    # MySQL takes an ssl dict and has no application_name equivalent. Always set
    # explicitly, or the system_status engine inherits the MySQL-style ssl dict
    # from the main SAM engine under SAM_DB_REQUIRE_SSL=true.
    status_require_ssl = os.getenv('STATUS_DB_REQUIRE_SSL', 'false').lower() in ('true', '1', 'yes')
    status_driver = os.getenv('STATUS_DB_DRIVER', 'mysql').lower()
    pod_id = os.environ.get('HOSTNAME') or socket.gethostname()
    status_connect_args: dict = {}
    if status_driver in ('postgresql', 'postgres'):
        status_connect_args['application_name'] = f'sam-webapp:{pod_id}:system_status'
        if status_require_ssl:
            status_connect_args['sslmode'] = 'require'
    elif status_require_ssl:
        status_connect_args['ssl'] = {'ssl_disabled': False}
    status_pool['connect_args'] = status_connect_args

    app.config['SQLALCHEMY_BINDS'] = {
        # Dict form lets us override engine options per bind (Flask-SQLAlchemy
        # 3.x). Keys other than ``url`` are passed to ``create_engine()``.
        'system_status': {
            'url': system_status.session.connection_string,
            **status_pool,
        },
    }

    # Apply caller-supplied overrides AFTER defaults, BEFORE extensions bind.
    # The test suite uses this to point Flask-SQLAlchemy at the mysql-test
    # container and/or to supply a pre-existing SAVEPOINT-scoped connection.
    if config_overrides:
        app.config.update(config_overrides)

    # Initialize db with app
    db.init_app(app)

    # CSRF protection (Flask-WTF). HTMX requests carry the token via the
    # hx-headers attribute on <body> in dashboards/base.html; plain forms
    # embed a hidden csrf_token input. Basic-auth M2M routes are exempted
    # at the view with @csrf.exempt.
    csrf.init_app(app)

    from flask_wtf.csrf import CSRFError

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        from flask import jsonify
        app.logger.warning('CSRF failure: %s %s (%s)',
                           request.method, request.path, e.description)
        if request.path.startswith('/api/'):
            return jsonify({'error': f'CSRF validation failed: {e.description}'}), 400
        if request.headers.get('HX-Request'):
            return ('<div class="alert alert-danger">Your session expired. '
                    'Please reload the page and try again.</div>', 400)
        return f'CSRF validation failed: {e.description}', 400

    # HTMX-aware 403: access-control decorators abort(403); HTMX swaps need
    # a fragment, not the default HTML error page. API blueprints register
    # their own JSON 403 handlers (webapp.api.helpers), which take precedence.
    @app.errorhandler(403)
    def handle_403(e):
        if request.headers.get('HX-Request'):
            return '<div class="alert alert-danger m-3">Unauthorized</div>', 403
        return e

    # Initialize caching. Backend selection priority:
    #   testing             -> NullCache (no shared state across tests)
    #   CACHE_REDIS_URL set -> RedisCache (shared across all gunicorn workers + pods)
    #   default             -> SimpleCache (per-worker in-process)
    app.config.setdefault('CACHE_DEFAULT_TIMEOUT', 300)
    if app.config.get('TESTING') or os.environ.get('FLASK_ENV') == 'testing':
        app.config['CACHE_TYPE'] = 'NullCache'
    elif os.environ.get('CACHE_REDIS_URL'):
        app.config.setdefault('CACHE_TYPE', 'RedisCache')
        app.config.setdefault('CACHE_REDIS_URL', os.environ['CACHE_REDIS_URL'])
    else:
        app.config.setdefault('CACHE_TYPE', 'SimpleCache')
    from webapp.caching import caching
    caching.init_app(app)

    # Flask-Limiter, keyed per-API-key/per-user/per-IP. Redis when
    # RATELIMIT_STORAGE_URI is set and reachable, else per-worker memory:// with
    # a startup warning. init_app also registers the 429 errorhandler.
    from webapp.limiter import limiter
    limiter.init_app(app)

    # Track INSERT/UPDATE/DELETE on SAM models. Excludes the system_status
    # database and ApiCredentials -- see audit/events.py.
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
        # An earlier before_request hook (e.g. CSRF rejection) can abort the
        # request before _set_request_id runs — fall back gracefully.
        start = g.get('request_start')
        elapsed_ms = round((time.monotonic() - start) * 1000, 1) if start else 0.0
        request_id = g.get('request_id', request.headers.get('X-Request-ID', '-'))
        response.headers['X-Request-ID'] = request_id
        # Healthcheck probes fire every 10s — log only when they fail.
        is_health_probe = (
            _HEALTH_PATH_RE.match(request.path)
            and response.status_code < 400
        )
        if not is_health_probe:
            app.logger.info(
                '%s %s → %s  (%.1f ms)  rid=%s',
                request.method, request.path, response.status_code,
                elapsed_ms, request_id,
            )
        if elapsed_ms > 5000:
            app.logger.warning(
                'Slow request: %.1f ms  %s %s',
                elapsed_ms, request.method, request.path,
            )
        return response
    # =========================================================================

    # Baseline security response headers (HSTS prod-gated, nosniff, XFO, ...)
    from webapp.utils.security_headers import init_security_headers
    init_security_headers(app)

    # /static cache busting (?v=<content hash>) + long cache headers. Flask's
    # default is `no-cache`, which made 87.5% of this pod's requests be static
    # revalidations answering 304 — see the module docstring for the numbers.
    from webapp.utils.static_assets import init_static_assets
    init_static_assets(app)

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
            return AuthUser(sam_user)
        return None

    # Register context processor for RBAC in templates
    app.context_processor(rbac_context_processor)

    # Navigation registry (navbar dropdowns, mobile menu, breadcrumbs).
    # nav_locate is also a jinja global so the breadcrumbs macro can call it
    # without a `with context` import.
    from webapp.utils.nav import nav_context_processor, nav_locate
    app.context_processor(nav_context_processor)
    app.jinja_env.globals['nav_locate'] = nav_locate

    # Period pills on the job-history card, shared with the ?days= whitelist
    # the fragment routes validate against.
    from webapp.jobs.service import JOBS_WINDOW_PILLS
    app.jinja_env.globals['jobs_window_pills'] = JOBS_WINDOW_PILLS

    # Central CDN-asset registry (pinned versions + SRI) for templates
    from webapp.vendor_assets import vendor_assets_context_processor
    app.context_processor(vendor_assets_context_processor)

    # Dark mode, rendered straight onto <html data-bs-theme="..."> in both page
    # shells so the theme is correct in the FIRST BYTE of HTML: no flash to
    # prevent, no <head> script for the nonce-free CSP to forbid, no
    # localStorage read racing the paint. See docs/plans/implemented/DARK_MODE.md.
    @app.context_processor
    def theme_context_processor():
        from webapp.utils.htmx import read_theme
        return {'theme': read_theme()}

    # Expose runtime feature flags to all templates
    @app.context_processor
    def feature_flags_context_processor():
        return {
            'feature_flags': {
                'create_projects_enabled': app.config.get('CREATE_PROJECTS_ENABLED', True),
            }
        }

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

    # Initialize the hpc-usage-queries plugin (optional). Loads the
    # job_history package and opens one Engine per configured machine so
    # connection pools are warm before the first per-job query. Disabled
    # in tests (TestingConfig.JOB_HISTORY_MACHINES = []).
    from webapp.jobs import init_job_history, bp as jobs_bp
    init_job_history(app)

    # Initialize the fs-scans plugin (optional). Loads the fs_scans
    # package, discovers the available collection schemas, and pre-warms
    # one Engine per collection on the CNPG backend. Disabled in tests
    # (TestingConfig.FS_SCANS_ENABLED = False).
    from webapp.disk_scans import init_fs_scans
    from webapp.disk_scans.routes import bp as disk_scans_bp
    init_fs_scans(app)

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(user_dashboard_bp)
    app.register_blueprint(admin_dashboard_bp)
    app.register_blueprint(status_dashboard_bp)
    app.register_blueprint(allocations_dashboard_bp)
    app.register_blueprint(project_members_bp)
    app.register_blueprint(jobs_bp, url_prefix='/dashboards/user/jobs')
    app.register_blueprint(disk_scans_bp, url_prefix='/dashboards/user/disk-scans')
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
    app.register_blueprint(api_queue_bp, url_prefix='/api/v1/queue')
    app.register_blueprint(api_wallclock_exemption_bp, url_prefix='/api/v1/wallclock_exemption')
    app.register_blueprint(api_admin_bp, url_prefix='/api/v1/admin')
    # XRAS is the one API surface not under /api/v1 — the prefix is legacy's,
    # mapped by web.xml to a dedicated DispatcherServlet at /api/xras/*.
    app.register_blueprint(api_xras_bp, url_prefix='/api/xras/v1')

    # Register centralized formatting filters (fmt_number, fmt_pct, fmt_date, fmt_size)
    import sam.fmt as fmt
    fmt.register_jinja_filters(app)

    # Jinja's mtime-based auto-reload does not reliably see template changes
    # through Docker's bind-mount/watch-sync: the container mtime updates but
    # the running env still serves the cached compile. Disable the template
    # cache in debug so every render re-reads from disk.
    if app.config.get('DEBUG'):
        app.jinja_env.cache = None

    # Initialize Flask-Admin (kill-switch: OFF by default in production —
    # the /database browser is never mounted on the public deploy)
    if app.config.get('FLASK_ADMIN_ENABLED', False):
        init_admin(app)

    # Auto-login middleware for development (enabled via DISABLE_AUTH=1)
    from webapp.utils.dev_auth import auto_login_middleware
    auto_login_middleware(app, db)

    # Home page redirect
    @app.route('/')
    @limiter.limiter.limit(
        lambda: app.config['RATELIMIT_ANON'],
        key_func=lambda: f'ip:{get_remote_address()}',
    )
    def index():
        if current_user.is_authenticated:
            # Gate on the same permission that gates the Admin nav tab, so the
            # redirect target is always something the user can reach --
            # including one granted admin via USER_PERMISSION_OVERRIDES rather
            # than a group bundle.
            from webapp.utils.rbac import has_permission_any_facility, Permission
            if has_permission_any_facility(current_user, Permission.ACCESS_ADMIN_DASHBOARD):
                return redirect(url_for('admin_dashboard.index'))
            else:
                return redirect(url_for('user_dashboard.index'))
        return redirect(url_for('status_dashboard.index'))

    # Browsers and tools that don't parse HTML (curl, monitoring probes,
    # bookmark fetchers) request /favicon.ico unconditionally — serve the
    # same file the <link rel="icon"> tags point at, with static caching.
    @app.route('/favicon.ico')
    def favicon():
        return app.send_static_file('img/favicon.ico')

    # Process startup timestamp — surfaced on the Admin > Configuration tab
    # as both an absolute time and a derived uptime.
    app.start_time = datetime.now()

    return app


if __name__ == '__main__':
    app = create_app()
    # Port is configurable via WEBAPP_PORT so that an interactive debug
    # launch (utils/run-webui-dbg.sh) can coexist with `docker compose up
    # webdev` (which binds host port 5050). Default stays 5050 for backwards
    # compatibility with existing tooling that expects it.
    app.run(host='0.0.0.0', debug=True, port=int(os.environ.get('WEBAPP_PORT', 5050)))
