"""Flask application configuration hierarchy.

Usage in run.py:
    from webapp.config import get_webapp_config
    cfg = get_webapp_config()   # selects class via FLASK_CONFIG env var
    cfg.validate()
    app.config.from_object(cfg)
"""
import os
from datetime import timedelta
from config import SAMConfig


class SAMWebappConfig(SAMConfig):
    """All webapp-layer config that extends the base DB + mail config."""
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    FLASK_ADMIN_SWATCH = 'lumen'
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB

    # API key authentication for machine-to-machine routes (status collectors, etc.)
    # Populated from API_KEYS_<USERNAME> environment variables at startup.
    # e.g., API_KEYS_COLLECTOR=$2b$12$...  →  {'collector': '$2b$12$...'}
    # Use scripts/gen_api_key.py to generate new key/hash pairs.
    API_KEYS: dict = {
        k[9:].lower(): v          # strip 'API_KEYS_' prefix (9 chars), lowercase username
        for k, v in os.environ.items()
        if k.startswith('API_KEYS_') and v
    }

    # Auth provider ('stub' | 'ldap' | 'oidc')
    AUTH_PROVIDER = os.getenv('AUTH_PROVIDER', 'stub')

    # Whether the DISABLE_AUTH=1 dev auto-login bypass may register at all.
    # Fail-closed default; only DevelopmentConfig opts in. Runtime activation
    # still requires the DISABLE_AUTH=1 env var (see webapp.utils.dev_auth).
    DEV_AUTO_LOGIN_ALLOWED = False

    # Flask-Admin DB browser (/database). When off, init_admin() never runs
    # and the blueprint is not mounted. ProductionConfig flips the default
    # OFF so the public deploy never serves it [PR295 P0-3]; helm sets the
    # env var explicitly either way.
    FLASK_ADMIN_ENABLED = os.getenv('FLASK_ADMIN_ENABLED', '1').lower() in ('1', 'true', 'yes')

    # Create Project workflow. When off, the modal still renders with all inputs
    # editable but its submit button is replaced with a disabled indicator, and
    # the create POST route 403s. Lets ops temporarily freeze project creation.
    CREATE_PROJECTS_ENABLED = os.getenv('CREATE_PROJECTS_ENABLED', '1').lower() in ('1', 'true', 'yes')

    # OIDC configuration (active when AUTH_PROVIDER='oidc')
    OIDC_CLIENT_ID = os.getenv('OIDC_CLIENT_ID', '')
    OIDC_CLIENT_SECRET = os.getenv('OIDC_CLIENT_SECRET', '')
    OIDC_ISSUER = os.getenv('OIDC_ISSUER', '')
    OIDC_SCOPES = os.getenv('OIDC_SCOPES', 'openid email profile')
    OIDC_USERNAME_CLAIM = os.getenv('OIDC_USERNAME_CLAIM', 'preferred_username')
    OIDC_REDIRECT_URI = os.getenv('OIDC_REDIRECT_URI', '')

    # Audit logging
    AUDIT_ENABLED  = os.getenv('AUDIT_ENABLED', '1').lower() in ('1', 'true', 'yes')
    AUDIT_LOG_PATH = os.getenv('AUDIT_LOG_PATH', '/var/log/sam/model_audit.log')

    # Application logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE  = os.getenv('LOG_FILE', '')       # empty = console only

    # Google Calendar embed URL (public calendar shown on reservations tab; empty = hidden)
    GOOGLE_CALENDAR_EMBED_URL = os.getenv('GOOGLE_CALENDAR_EMBED_URL', '')

    # Content-Security-Policy mode: 'enforce' | 'report-only' | 'off'.
    # The policy itself is generated from webapp.vendor_assets (see
    # webapp/utils/csp.py); with every asset vendored it is essentially
    # all-'self'. 'report-only' sends Content-Security-Policy-Report-Only
    # — violations show in the browser console, nothing is blocked — and
    # is the no-rebuild rollback/diagnostic knob (helm values change, no
    # image build). Templates are kept inline-script-free by
    # tests/unit/test_template_csp_lint.py.
    CSP_MODE = os.getenv('CSP_MODE', 'enforce')

    # Flask-Cache default TTL (seconds) — used by @cache.cached / @cache.memoize
    # when no explicit timeout= is given.  Applies to all API and dashboard routes.
    # Distinct from the cachetools TTL below (which wraps a single query function).
    CACHE_DEFAULT_TIMEOUT = int(os.getenv('CACHE_DEFAULT_TIMEOUT', 300))

    # Rate limiting (Flask-Limiter; see docs/plans/RATE_LIMITING.md).
    # RATELIMIT_STORAGE_URI is injected by compose/helm when Redis is available;
    # empty value falls back to per-worker memory:// with a startup warning.
    RATELIMIT_ENABLED      = os.getenv('RATELIMIT_ENABLED', '1').lower() in ('1', 'true', 'yes')
    RATELIMIT_STORAGE_URI  = os.getenv('RATELIMIT_STORAGE_URI', '')
    RATELIMIT_STRATEGY     = 'fixed-window'

    RATELIMIT_AUTH_LOGIN = os.getenv('RATELIMIT_AUTH_LOGIN', '5 per minute; 20 per hour')
    RATELIMIT_M2M        = os.getenv('RATELIMIT_M2M',        '120 per minute')
    RATELIMIT_AUTHED     = os.getenv('RATELIMIT_AUTHED',     '200 per minute')
    RATELIMIT_ANON       = os.getenv('RATELIMIT_ANON',       '30 per minute')

    RATELIMIT_EVENT_RETENTION_HOURS = int(os.getenv('RATELIMIT_EVENT_RETENTION_HOURS', 24))
    RATELIMIT_EVENT_MAX             = int(os.getenv('RATELIMIT_EVENT_MAX', 1000))

    # Usage calculation cache (TTLCache wrapping get_allocation_summary_with_usage)
    # TTL=0 disables caching; SIZE controls max LRU entries
    ALLOCATION_USAGE_CACHE_TTL  = int(os.getenv('ALLOCATION_USAGE_CACHE_TTL', 3600))   # seconds
    ALLOCATION_USAGE_CACHE_SIZE = int(os.getenv('ALLOCATION_USAGE_CACHE_SIZE', 200))    # max entries

    # hpc-usage-queries plugin (per-job rows on resource-usage detail pages).
    # The plugin owns its own database — typically a per-machine PostgreSQL
    # database (derecho_jobs, casper_jobs) on the shared `csg-postgres` cluster.
    #
    # Sizing rationale: per-job query traffic is bursty — a single
    # resource-detail page load fans out into ~5 queries against one
    # machine's database. Server-side `idle_session_timeout` on
    # `csg-postgres` (configured in the peer repo's helm chart) reaps
    # truly-idle connections at 10 minutes, so pool_size is sized for the
    # *warm working set under typical burst* rather than as a safety cap.
    # 5 base + 10 burst keeps a page's worth of queries from paying the
    # TLS handshake cost mid-render, while the server's reaper prevents
    # the per-worker pool from accumulating idle across the gunicorn
    # worker pool. `pool_recycle=600` is belt-and-suspenders: same window
    # as the server-side timeout, gives client-side cleanup symmetry.
    # All knobs remain env-overridable for deployments backed by a
    # different postgres or without server-side idle eviction.
    JOB_HISTORY_MACHINES = [
        m.strip() for m in os.getenv('JOB_HISTORY_MACHINES', 'derecho,casper').split(',')
        if m.strip()
    ]
    JOB_HISTORY_POOL_KWARGS = {
        'pool_size':      int(os.getenv('JOB_HISTORY_POOL_SIZE',     5)),
        'max_overflow':   int(os.getenv('JOB_HISTORY_POOL_MAX_OVERFLOW', 10)),
        'pool_pre_ping':  True,
        'pool_recycle':   int(os.getenv('JOB_HISTORY_POOL_RECYCLE',  600)),
    }

    # Session cookies (common defaults; subclasses tighten for prod)
    SESSION_COOKIE_HTTPONLY    = True
    SESSION_COOKIE_SAMESITE    = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)

    # CSRF (Flask-WTF / CSRFProtect, initialized in create_app).
    # TIME_LIMIT=None ties token validity to the session lifetime — the
    # 1-hour flask-wtf default would 400 long-lived dashboard tabs.
    WTF_CSRF_ENABLED    = True
    WTF_CSRF_TIME_LIMIT = None


class DevelopmentConfig(SAMWebappConfig):
    DEBUG = True
    SESSION_COOKIE_SECURE = False   # no HTTPS required in dev
    DEV_AUTO_LOGIN_ALLOWED = True   # DISABLE_AUTH=1 auto-login permitted in dev only

    # Development API keys — rotate with: python scripts/gen_api_key.py
    # Actual key goes in collectors/.env as STATUS_API_KEY
    API_KEYS = {
        'collector': '$2b$12$X8NQvOUvyrj80Ud3N6Y.0uZs70ZC6lJYy/zfka/v7uQQFKJhds0b2',
    }

    # Usernames rendered as "Quick Login" buttons on the dev login page.
    # Format: 'username[:LABEL]'. The optional ':LABEL' suffix is shown
    # as a badge on the button so reviewers can see at a glance what
    # permission tier a given test account is expected to land in.
    # Bare usernames (no colon) are rendered without a badge.
    #
    # The label is purely cosmetic — actual permissions still resolve
    # through POSIX groups + USER_PERMISSION_OVERRIDES (see
    # webapp.utils.rbac), so an out-of-date label here cannot grant or
    # revoke access; keep it in sync by hand.
    DEV_QUICK_LOGIN_USERS = [
        'benkirk:ADMIN',
        'mtrahan:CSG',
        'rory:CSG',
        'andersnb:HSG',
        'tfair:NUSD',
        'dlawren:PROJ_TREE_LEAD',
        'sureshm:WNA_SCOPED_ADMIN',
        'bdobbins',
    ]


class ProductionConfig(SAMWebappConfig):
    DEBUG = False
    SESSION_COOKIE_SECURE = True    # HTTPS only

    # Default OFF in production — the public deploy doesn't mount /database;
    # full-CRUD admin stays available locally (webdev/webapp compose).
    FLASK_ADMIN_ENABLED = os.getenv('FLASK_ADMIN_ENABLED', '0').lower() in ('1', 'true', 'yes')

    @classmethod
    def validate(cls):
        super().validate()
        key = os.getenv('FLASK_SECRET_KEY', '')
        if not key:
            raise EnvironmentError(
                "FLASK_SECRET_KEY must be set in production.\n"
                "Generate: python -c 'import secrets; print(secrets.token_hex(32))'"
            )
        if len(key) < 32:
            raise EnvironmentError("FLASK_SECRET_KEY must be at least 32 characters.")
        if not cls.API_KEYS:
            import warnings
            warnings.warn(
                "No API_KEYS_* environment variables are set. "
                "Status collector routes will reject all requests. "
                "Generate keys with: python scripts/gen_api_key.py",
                stacklevel=2,
            )
        # Fail CLOSED: production must run OIDC, never stub/ldap [PR295 P0-2].
        # StubAuthProvider accepts any non-empty password, so a single dropped
        # env var must never silently downgrade a public deployment to it.
        if cls.AUTH_PROVIDER != 'oidc':
            raise EnvironmentError(
                f"ProductionConfig requires AUTH_PROVIDER=oidc "
                f"(got {cls.AUTH_PROVIDER!r}). StubAuthProvider accepts any "
                "password and must never serve a production deployment."
            )
        # Dev auto-login bypass must never be active in production [PR295 P0-1]
        if os.getenv('DISABLE_AUTH', '0') == '1':
            raise EnvironmentError(
                "DISABLE_AUTH=1 (dev auto-login bypass) must not be set when "
                "FLASK_CONFIG=production."
            )
        missing = [v for v in ('OIDC_CLIENT_ID', 'OIDC_CLIENT_SECRET', 'OIDC_ISSUER')
                   if not os.getenv(v)]
        if missing:
            raise EnvironmentError(
                f"AUTH_PROVIDER=oidc but missing required env vars: {', '.join(missing)}"
            )


class TestingConfig(SAMWebappConfig):
    TESTING = True
    DEBUG = False
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = False

    # Low-cost bcrypt hash for fast test execution (rounds=4)
    # Key value: 'test-api-key'
    API_KEYS = {
        'collector': '$2b$04$lEZO8EBAKbpGIUYMenFeOui8tvzj44hXlgWnbkkznBVe8oX1uQyE6',
    }

    # Disable usage cache in tests to prevent cross-test pollution
    ALLOCATION_USAGE_CACHE_TTL  = 0
    ALLOCATION_USAGE_CACHE_SIZE = 0

    # Rate limiting off in tests — xdist parallelism would otherwise trip
    # global limits across worker processes. The one test module that
    # *does* exercise rate limiting (tests/integration/test_rate_limit_flow.py)
    # flips the singleton facade on per-test and clears storage between
    # tests; pinning to memory:// here keeps that clear behaving as a
    # per-worker dict wipe instead of trying to wipe a shared CI Redis
    # (compose.yaml sets RATELIMIT_STORAGE_URI=redis://cache:6379/1 on
    # the webapp container, which pytest inherits in CI runs).
    RATELIMIT_ENABLED     = False
    RATELIMIT_STORAGE_URI = 'memory://'

    # The hpc-usage-queries plugin talks to a separate (per-machine
    # PostgreSQL) database that the test container does not provide.
    # Empty machine list disables eager engine init at startup; route-
    # level tests stub the service layer instead.
    JOB_HISTORY_MACHINES = []


_configs = {
    'development': DevelopmentConfig,
    'production':  ProductionConfig,
    'testing':     TestingConfig,
}


def get_webapp_config():
    """Return config class selected by FLASK_CONFIG env var (default: development)."""
    name = os.getenv('FLASK_CONFIG', 'development')
    if name not in _configs:
        raise ValueError(f"Unknown FLASK_CONFIG={name!r}. Choose: {list(_configs)}")
    return _configs[name]
