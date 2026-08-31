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
    # e.g., API_KEYS_COLLECTOR=$2b$12$...  ->  {'collector': '$2b$12$...'}
    # Use scripts/gen_api_key.py to generate new key/hash pairs.
    API_KEYS: dict = {
        k[9:].lower(): v          # strip 'API_KEYS_' prefix (9 chars), lowercase username
        for k, v in os.environ.items()
        if k.startswith('API_KEYS_') and v
    }

    # Fall back to the legacy `api_credentials` SQL table for Basic-Auth keys
    # absent from API_KEYS above; config always wins. Enabled rows are read live
    # behind an in-process TTL cache, TTL=0 refreshing on every lookup.
    API_KEYS_DB_ENABLED = os.getenv('API_KEYS_DB_ENABLED', '1').lower() in ('1', 'true', 'yes')
    API_KEYS_DB_TTL     = int(os.getenv('API_KEYS_DB_TTL', 60))

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

    # Dev-only component gallery (/dev/gallery). When off, the blueprint is not
    # mounted. ProductionConfig flips the default OFF so the public deploy never
    # serves it — same idiom and posture as FLASK_ADMIN_ENABLED above.
    COMPONENT_GALLERY_ENABLED = os.getenv('COMPONENT_GALLERY_ENABLED', '1').lower() in ('1', 'true', 'yes')

    # Create Project workflow. When off, the modal still renders with all inputs
    # editable but its submit button is replaced with a disabled indicator, and
    # the create POST route 403s. Lets ops temporarily freeze project creation.
    CREATE_PROJECTS_ENABLED = os.getenv('CREATE_PROJECTS_ENABLED', '1').lower() in ('1', 'true', 'yes')

    # POST /api/xras/v1/actions capture mode. When ON (the default) the endpoint
    # authenticates, parses and writes its xras_action_log row, then returns 200
    # WITHOUT dispatching to a handler — legacy SAM is still the system of record
    # for these actions until cutover, and the audit rows are how we harvest real
    # payloads in the meantime. Flip OFF per handler as each one lands.
    XRAS_ACTIONS_CAPTURE_ONLY = os.getenv('XRAS_ACTIONS_CAPTURE_ONLY', '1').lower() in ('1', 'true', 'yes')

    # Per-type triage lever for POST /api/xras/v1/actions: 'all' (the default),
    # 'none', or a comma-separated list of action types ('Extension,Supplement').
    # NOT a rollout mechanism — XRAS repoints its base URL once, so all six
    # handlers arrive at the same moment. This parks a misbehaving payload class
    # by config instead of by revert; a disabled type takes the audited
    # manual-fallback path. An unknown token is logged and dropped, leaving that
    # type DISABLED rather than enabling something nobody meant to.
    XRAS_ACTIONS_ENABLED = os.getenv('XRAS_ACTIONS_ENABLED', 'all')

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
    # Mirror audit entries to STDOUT (on by default). The in-pod log file lives
    # on ephemeral storage and is lost on reboot/reschedule; CIRRUS/k8s captures
    # and retains container STDOUT for 45 days, so this is the durable sink.
    AUDIT_LOG_STDOUT = os.getenv('AUDIT_LOG_STDOUT', '1').lower() in ('1', 'true', 'yes')

    # Application logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE  = os.getenv('LOG_FILE', '')       # empty = console only

    # Google Calendar embed URL (public calendar shown on the Events tab; empty = hidden)
    GOOGLE_CALENDAR_EMBED_URL = os.getenv('GOOGLE_CALENDAR_EMBED_URL', '')

    # Status collectors tick every ~5 minutes; a snapshot older than this many
    # minutes (default: 3 missed ticks) raises the per-system stale-data banner
    # on the status dashboard.
    STATUS_STALE_MINUTES = int(os.getenv('STATUS_STALE_MINUTES', 15))

    # Content-Security-Policy mode: 'enforce' | 'report-only' | 'off'. The policy
    # is generated from webapp.vendor_assets (webapp/utils/csp.py) and, with
    # every asset vendored, is essentially all-'self'. 'report-only' is the
    # no-rebuild rollback knob: violations log to the console, nothing is
    # blocked. tests/unit/test_template_csp_lint.py keeps templates inline-free.
    CSP_MODE = os.getenv('CSP_MODE', 'enforce')

    # Flask-Cache default TTL (seconds) — used by @cache.cached / @cache.memoize
    # when no explicit timeout= is given.  Applies to all API and dashboard routes.
    # Distinct from the cachetools TTL below (which wraps a single query function).
    CACHE_DEFAULT_TIMEOUT = int(os.getenv('CACHE_DEFAULT_TIMEOUT', 300))

    # Rate limiting (Flask-Limiter; see docs/plans/implemented/RATE_LIMITING.md).
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

    # Award-source lookup cache (sam.integration.awards). Award records are
    # near-immutable, so this sits at the long end of the range like
    # FS_SCANS_CACHE_TTL rather than the volatile jobs TTL.
    AWARD_LOOKUP_CACHE_TTL  = int(os.getenv('AWARD_LOOKUP_CACHE_TTL', 691200))   # 8 days
    AWARD_LOOKUP_CACHE_SIZE = int(os.getenv('AWARD_LOOKUP_CACHE_SIZE', 256))     # max entries

    # Free-text award search gets its own bucket at a much shorter TTL: a
    # search is a view over a changing corpus, not a near-immutable record,
    # so a new award should surface the next day rather than the next week.
    AWARD_SEARCH_CACHE_TTL  = int(os.getenv('AWARD_SEARCH_CACHE_TTL', 86400))    # 1 day
    AWARD_SEARCH_CACHE_SIZE = int(os.getenv('AWARD_SEARCH_CACHE_SIZE', 256))     # max entries

    # Outbound XRAS Allocations API cache (sam.integration.xras_api). People
    # sit at 4 hours because `isReconciled` is the account-creation worklist's
    # closure signal and must not go stale; the 13-row resource catalog changes
    # about once a year.
    XRAS_PEOPLE_CACHE_TTL     = int(os.getenv('XRAS_PEOPLE_CACHE_TTL', 14400))   # 4 hours
    XRAS_PEOPLE_CACHE_SIZE    = int(os.getenv('XRAS_PEOPLE_CACHE_SIZE', 512))    # max entries
    XRAS_RESOURCES_CACHE_TTL  = int(os.getenv('XRAS_RESOURCES_CACHE_TTL', 86400))  # 1 day
    XRAS_RESOURCES_CACHE_SIZE = int(os.getenv('XRAS_RESOURCES_CACHE_SIZE', 8))     # max entries
    # The Feed-B mailbox: xras_sweep publishes, the dashboard tab reads. TTL
    # spans the overnight gap between business-hours sweeps (17:00 -> 08:00),
    # or the tab would be blank every morning until the first run.
    XRAS_PENDING_CACHE_TTL    = int(os.getenv('XRAS_PENDING_CACHE_TTL', 86400))   # 1 day
    XRAS_PENDING_CACHE_SIZE   = int(os.getenv('XRAS_PENDING_CACHE_SIZE', 4))      # max entries

    # hpc-usage-queries plugin (per-job rows on resource-usage detail pages).
    # The plugin owns its own per-machine PostgreSQL database (derecho_jobs,
    # casper_jobs) on the shared `csg-postgres` cluster.
    #
    # The pool is sized for the warm working set under burst (one page load fans
    # out into ~5 queries), not as a safety cap: server-side
    # `idle_session_timeout` on `csg-postgres` reaps idle connections at 10
    # minutes, and `pool_recycle=600` mirrors that window client-side.
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

    # Server-side Postgres statement_timeout (ms) on every job-history
    # connection, so a runaway aggregation fails cleanly instead of holding a PG
    # connection and a gthread thread until gunicorn's worker timeout kills it.
    # Warm month-window aggregations measure ~0.6 s, unbounded ~200 s; keep this
    # comfortably below gunicorn `timeout` (120 s). 0 disables.
    JOB_HISTORY_STATEMENT_TIMEOUT_MS = int(
        os.getenv('JOB_HISTORY_STATEMENT_TIMEOUT_MS', '60000')
    )

    # fs-scans plugin (filesystem-scan analytics over the CNPG backend).
    # Master switch only — the plugin reads its own connection settings
    # (FS_SCAN_DB_BACKEND, FS_SCAN_PG_*) from the environment. Collections
    # are discovered at startup, so there's no per-collection list here.
    # Disable (TestingConfig does) to skip plugin load entirely.
    FS_SCANS_ENABLED = os.getenv('FS_SCANS_ENABLED', '1').lower() not in (
        '0', 'false', 'no', '',
    )

    # Master switch for the /api/v1/fairshare endpoint (hpc-scheduling-tools
    # plugin). Off skips the plugin load entirely; TestingConfig forces it off.
    HPC_SCHEDULING_TOOLS_ENABLED = os.getenv(
        'HPC_SCHEDULING_TOOLS_ENABLED', '1').lower() not in ('0', 'false', 'no', '')

    # Server-side Postgres statement_timeout (ms) applied to every fs-scans
    # connection. A runaway scope can otherwise hold a CNPG connection (and a
    # gthread thread) until the gunicorn worker timeout kills it; this caps the
    # query server-side so it fails cleanly first. Set comfortably below
    # gunicorn `timeout` (120s) but above legit lab-parent scans (measured
    # ~70s). 0 disables.
    FS_SCAN_STATEMENT_TIMEOUT_MS = int(
        os.getenv('FS_SCAN_STATEMENT_TIMEOUT_MS', '100000')
    )

    # Disk resources with filesystem-scan collections, surfaced as subtabs on the
    # Status dashboard. An explicit list of resource NAMES (not IDs, and not
    # derived from the Resource table). A configured resource with no warmed
    # collections is filtered out at render time, so a subtab is never empty.
    FS_SCAN_RESOURCES = [
        s.strip()
        for s in os.getenv('FS_SCAN_RESOURCES', 'Campaign_Store,Destor').split(',')
        if s.strip()
    ]

    # Scan resource NAME -> the CNPG database holding its collections, parsed as
    # `Name:db,Name2:db2`. One database per disk resource on the shared cluster.
    # NOTE the Destor database is named `destor`; `desc1` is only the Lustre
    # MOUNT /lustre/desc1. `init_fs_scans` warms one engine set per DISTINCT
    # database; a resource whose database is unreachable warms nothing and drops
    # out of the UI. The default tracks the plugin's own `FS_SCAN_PG_DB` so a
    # single-database deployment keeps working unchanged.
    FS_SCAN_RESOURCE_DATABASES = {
        name.strip(): db.strip()
        for name, _, db in (
            pair.partition(':')
            for pair in os.getenv(
                'FS_SCAN_RESOURCE_DATABASES',
                f"Campaign_Store:{os.getenv('FS_SCAN_PG_DB', 'campaign')},Destor:destor",
            ).split(',')
        )
        if name.strip() and db.strip()
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

    # "Quick Login" buttons on the dev login page, as 'username[:LABEL]'. The
    # optional label badges the permission tier the account is expected to land
    # in; it is cosmetic, since permissions resolve through POSIX groups +
    # USER_PERMISSION_OVERRIDES, so a stale label grants nothing. Sync by hand.
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

    # Default OFF in production — the dev-only component gallery is never mounted
    # on the public deploy.
    COMPONENT_GALLERY_ENABLED = os.getenv('COMPONENT_GALLERY_ENABLED', '0').lower() in ('1', 'true', 'yes')

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
        # Notifications are fail-closed, so a dropped env var means "no mail"
        # rather than "mail the wrong people" — right, but silent. Warn so the
        # disabled state surfaces within a day rather than at the next
        # expiration round (docs/plans/implemented/NOTIFICATION_FRAMEWORK.md § 3).
        if os.getenv('NOTIFY_ENABLED', '0').lower() not in ('1', 'true', 'yes'):
            import warnings
            warnings.warn(
                "NOTIFY_ENABLED is not set. No notification will be sent — "
                "expiration notices and XRAS activation mail will be recorded "
                "as 'suppressed'. Set NOTIFY_ENABLED=1 to enable delivery.",
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

    # No caching of DB-sourced keys in tests: every lookup refreshes so a row
    # inserted mid-test is visible immediately and no cache state leaks across
    # tests (the module-level cache in api_auth is process-global).
    API_KEYS_DB_TTL = 0

    # Disable usage cache in tests to prevent cross-test pollution
    ALLOCATION_USAGE_CACHE_TTL  = 0
    ALLOCATION_USAGE_CACHE_SIZE = 0

    # Award lookups and searches are stubbed; a live cache leaks one test's stub
    # answer into the next. BOTH buckets must be listed — a bucket absent here
    # falls through to its hardcoded default and stays LIVE under test, which is
    # how the award-search cache leaked in CI (AWARD_SEARCH.md § 13.1).
    AWARD_LOOKUP_CACHE_TTL  = 0
    AWARD_LOOKUP_CACHE_SIZE = 0
    AWARD_SEARCH_CACHE_TTL  = 0
    AWARD_SEARCH_CACHE_SIZE = 0

    # Off in tests — xdist parallelism trips global limits across workers. The
    # one module that does exercise it (tests/integration/test_rate_limit_flow.py)
    # flips the facade on per-test and clears storage; pinning memory:// keeps
    # that clear a per-worker dict wipe rather than an attempt to wipe the shared
    # CI Redis that compose.yaml points the webapp container at.
    RATELIMIT_ENABLED     = False
    RATELIMIT_STORAGE_URI = 'memory://'

    # The hpc-usage-queries plugin talks to a separate (per-machine
    # PostgreSQL) database that the test container does not provide.
    # Empty machine list disables eager engine init at startup; route-
    # level tests stub the service layer instead.
    JOB_HISTORY_MACHINES = []

    # The fs-scans plugin talks to a separate CNPG/PostgreSQL backend the
    # test container does not provide. Disable eager load at startup;
    # route-level tests stub the service layer instead.
    FS_SCANS_ENABLED = False

    # The hpc-scheduling-tools plugin is not installed in the test image; route
    # tests mock HPC_SCHEDULING_TOOLS.load. Individual tests flip this on.
    HPC_SCHEDULING_TOOLS_ENABLED = False

    # OFF and pinned to the recording transport: here the shared state a test
    # tier can reach is the internet — ndir.ucar.edu relays for the whole UCAR
    # /16 and accepts arbitrary external recipients (NOTIFICATION_FRAMEWORK.md
    # § 9). Belt and braces with the autouse no-socket fixture in
    # tests/conftest.py, which holds even when a test builds its own config.
    NOTIFY_ENABLED   = False
    NOTIFY_TRANSPORT = 'null'

    # Pinned because `SAMWebappConfig.XRAS_ACTIONS_CAPTURE_ONLY` reads os.getenv
    # at class-body time: a developer with XRAS_ACTIONS_CAPTURE_ONLY=0 in `.env`
    # — what a local dispatch smoke needs — otherwise runs the whole API tier
    # against the dispatching arm and watches ten capture tests fail for reasons
    # unrelated to their change. Tests wanting the other arm override
    # `app.config` explicitly (tests/api/test_xras_access.py::TestDispatchArms).
    XRAS_ACTIONS_CAPTURE_ONLY = True

    # Same reasoning, one step further out: a developer with XRAS_WRITE_ENABLED=1
    # in `.env` must not have the suite inherit a live write capability against
    # production XRAS. Pinned False so `write_configured` is False unless a test
    # says otherwise.
    XRAS_WRITE_ENABLED = False


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
