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

    # Auth provider ('stub' | 'ldap' | 'saml')
    AUTH_PROVIDER = os.getenv('AUTH_PROVIDER', 'stub')

    # Audit logging
    AUDIT_ENABLED  = os.getenv('AUDIT_ENABLED', '1').lower() in ('1', 'true', 'yes')
    AUDIT_LOG_PATH = os.getenv('AUDIT_LOG_PATH', '/var/log/sam/model_audit.log')

    # Session cookies (common defaults; subclasses tighten for prod)
    SESSION_COOKIE_HTTPONLY    = True
    SESSION_COOKIE_SAMESITE    = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)


class DevelopmentConfig(SAMWebappConfig):
    DEBUG = True
    SESSION_COOKIE_SECURE = False   # no HTTPS required in dev

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


class TestingConfig(SAMWebappConfig):
    TESTING = True
    DEBUG = False
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = False


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
