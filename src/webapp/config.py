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

    # Auth provider ('stub' | 'ldap' | 'saml')
    AUTH_PROVIDER = os.getenv('AUTH_PROVIDER', 'stub')

    # Audit logging
    AUDIT_ENABLED  = os.getenv('AUDIT_ENABLED', '1').lower() in ('1', 'true', 'yes')
    AUDIT_LOG_PATH = os.getenv('AUDIT_LOG_PATH', '/var/log/sam/model_audit.log')

    # Application logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE  = os.getenv('LOG_FILE', '')       # empty = console only

    # Google Calendar embed URL (public calendar shown on reservations tab; empty = hidden)
    GOOGLE_CALENDAR_EMBED_URL = os.getenv('GOOGLE_CALENDAR_EMBED_URL', '')

    # Usage calculation cache (TTLCache wrapping get_allocation_summary_with_usage)
    # TTL=0 disables caching; SIZE controls max LRU entries
    ALLOCATION_USAGE_CACHE_TTL  = int(os.getenv('ALLOCATION_USAGE_CACHE_TTL', 3600))   # seconds
    ALLOCATION_USAGE_CACHE_SIZE = int(os.getenv('ALLOCATION_USAGE_CACHE_SIZE', 200))    # max entries

    # Session cookies (common defaults; subclasses tighten for prod)
    SESSION_COOKIE_HTTPONLY    = True
    SESSION_COOKIE_SAMESITE    = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)


class DevelopmentConfig(SAMWebappConfig):
    DEBUG = True
    SESSION_COOKIE_SECURE = False   # no HTTPS required in dev

    # Development API keys — rotate with: python scripts/gen_api_key.py
    # Actual key goes in collectors/.env as STATUS_API_KEY
    API_KEYS = {
        'collector': '$2b$12$X8NQvOUvyrj80Ud3N6Y.0uZs70ZC6lJYy/zfka/v7uQQFKJhds0b2',
    }

    # Development role mapping (bypasses role DB tables)
    DEV_ROLE_MAPPING = {
        'benkirk':  ['admin'],
        'mtrahan':  ['facility_manager'],
        'rory':     ['project_lead'],
        'andersnb': ['user'],
        'negins':   ['user'],
        'bdobbins': ['user'],
        'tcraig':   ['user'],
    }


class ProductionConfig(SAMWebappConfig):
    DEBUG = False
    SESSION_COOKIE_SECURE = True    # HTTPS only

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
