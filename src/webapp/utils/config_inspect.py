"""Read-only runtime-configuration inspection helpers.

Used by the Admin → Configuration tab to surface webapp state for
sysadmins.

Design notes:

- **Allowlist only.** ``gather_runtime_state`` is the *only* place that
  reads from ``app.config`` / ``os.environ`` / extensions. Adding a
  new field to the page means adding it here, which makes it trivial
  to review what is being rendered.
- **Secrets never render.** Any sensitive value goes through
  ``mask_secret``. DB connection passwords are dropped by
  ``format_db_url_safe``. API key bcrypt hashes are never read — only
  the dict keys (usernames).
- **Graceful degradation.** ``tail_audit_log`` returns ``None`` (not
  an exception) on missing/unreadable files so a misconfigured path
  cannot break the page.
"""
import os
import platform
import socket
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import flask


# ---------------------------------------------------------------------------
# Primitive helpers
# ---------------------------------------------------------------------------

def mask_secret(value: Any) -> str:
    """Return ``'set'`` if value is non-empty, else ``'unset'``.

    Never reveals length, prefix, or any portion of the value. The
    intent is that an admin can see whether a secret is configured,
    nothing more.
    """
    if value is None:
        return 'unset'
    if isinstance(value, str) and value.strip() == '':
        return 'unset'
    return 'set'


def format_db_url_safe(engine) -> str:
    """Format a SQLAlchemy engine's URL without the password.

    Builds the string from URL components rather than relying on
    ``.render_as_string(hide_password=True)`` (not available in all
    SQLAlchemy versions) so this works defensively.
    """
    u = engine.url
    user = f"{u.username}@" if u.username else ''
    host = u.host or ''
    port = f":{u.port}" if u.port else ''
    database = f"/{u.database}" if u.database else ''
    return f"{u.drivername}://{user}{host}{port}{database}"


def pool_stats(pool) -> Dict[str, Any]:
    """Connection-pool statistics — same shape as the JSON returned
    by ``GET /api/v1/health/db-pool``.

    Extracted to module level so the Admin Configuration page and the
    JSON health endpoint share one implementation.
    """
    size = pool.size()
    checked_out = pool.checkedout()
    utilization_pct = round(checked_out / size * 100, 1) if size else 0
    return {
        'pool_size':       size,
        'checked_in':      pool.checkedin(),
        'checked_out':     checked_out,
        'overflow':        pool.overflow(),
        'max_overflow':    pool._max_overflow,
        'utilization_pct': utilization_pct,
        'health': 'warning' if utilization_pct > 80 else 'healthy',
    }


def tail_audit_log(path: Optional[str], n: int = 25) -> Optional[List[str]]:
    """Return the last ``n`` lines of the audit log file at ``path``.

    Returns ``None`` if the path is empty/None, the file is missing,
    or it cannot be read for any reason. Reads only the trailing
    block of the file so this is safe even on a multi-megabyte log.
    """
    if not path:
        return None
    try:
        p = Path(path)
        if not p.is_file():
            return None
        size = p.stat().st_size
        # Read up to ~16 KiB from the tail to find n lines comfortably.
        read_bytes = min(size, 16 * 1024)
        with p.open('rb') as f:
            f.seek(size - read_bytes)
            chunk = f.read(read_bytes)
        # Drop the (possibly partial) first line if we didn't start
        # at byte 0 — it may be split mid-line.
        text = chunk.decode('utf-8', errors='replace')
        lines = text.splitlines()
        if size > read_bytes and lines:
            lines = lines[1:]
        return lines[-n:] if lines else []
    except (OSError, PermissionError):
        return None


# ---------------------------------------------------------------------------
# State gathering (allowlist)
# ---------------------------------------------------------------------------

def _uptime(start_time: Optional[datetime]) -> Optional[str]:
    if start_time is None:
        return None
    delta = datetime.now() - start_time
    total_seconds = int(delta.total_seconds())
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def gather_runtime_state(app, db) -> Dict[str, Any]:
    """Collect runtime state for the Admin Configuration page.

    This is the *only* function that reads from app.config / os.environ
    / extensions. Anything renderable on the page must come from here.
    Sensitive values are pre-masked so the template cannot accidentally
    leak them.
    """
    cfg = app.config

    # Resolve the loaded config class name (TestingConfig / DevelopmentConfig
    # / ProductionConfig) — `type(app.config).__name__` is just Flask's
    # internal Config dict wrapper, which is uninformative here.
    try:
        from webapp.config import get_webapp_config
        config_class_name = get_webapp_config().__name__
    except Exception:
        config_class_name = 'unknown'

    # Display timezone (sam.fmt converts naive-UTC db timestamps to this
    # zone for human-readable rendering; charts and history use it too).
    try:
        from sam import fmt as _fmt
        display_tz_name = _fmt._DISPLAY_TZ_NAME
        display_tz_abbr = _fmt.local_tz_label()
    except Exception:
        display_tz_name = None
        display_tz_abbr = None

    # --- Application
    application = {
        'config_class':    config_class_name,
        'flask_config':    os.getenv('FLASK_CONFIG', 'development'),
        'debug':           bool(cfg.get('DEBUG')),
        'testing':         bool(cfg.get('TESTING')),
        'python_version':  platform.python_version(),
        'flask_version':   getattr(flask, '__version__', 'unknown'),
        'hostname':        socket.gethostname(),
        'start_time':      getattr(app, 'start_time', None),
        'uptime':          _uptime(getattr(app, 'start_time', None)),
        'display_tz':      display_tz_name,
        'display_tz_abbr': display_tz_abbr,
        'git_sha':         os.getenv('GIT_SHA', '') or None,
        'build_date':      os.getenv('BUILD_DATE', '') or None,
    }

    # --- Database (per bind)
    databases = []
    engines = {'sam': db.engine}
    ss_engine = db.engines.get('system_status') if hasattr(db, 'engines') else None
    if ss_engine:
        engines['system_status'] = ss_engine

    # Lazy-import to avoid circulars
    from webapp.api.v1.health import _ping_engine

    for name, engine in engines.items():
        ok, latency_ms, err = _ping_engine(engine)
        try:
            stats = pool_stats(engine.pool)
        except Exception:
            stats = None
        databases.append({
            'name':       name,
            'url':        format_db_url_safe(engine),
            'status':     'healthy' if ok else 'unhealthy',
            'latency_ms': latency_ms,
            'error':      err,
            'pool':       stats,
        })

    # --- Authentication
    auth = {
        'auth_provider':       cfg.get('AUTH_PROVIDER', 'stub'),
        'session_httponly':    bool(cfg.get('SESSION_COOKIE_HTTPONLY', False)),
        'session_samesite':    cfg.get('SESSION_COOKIE_SAMESITE', ''),
        'session_secure':      bool(cfg.get('SESSION_COOKIE_SECURE', False)),
        'session_lifetime':    str(cfg.get('PERMANENT_SESSION_LIFETIME', '')),
        'flask_secret_key':    mask_secret(cfg.get('SECRET_KEY')),
        'api_key_usernames':   sorted(list((cfg.get('API_KEYS') or {}).keys())),
        'oidc_active':         cfg.get('AUTH_PROVIDER') == 'oidc',
        'oidc_issuer':         cfg.get('OIDC_ISSUER', '') or None,
        'oidc_client_id':      cfg.get('OIDC_CLIENT_ID', '') or None,
        'oidc_scopes':         cfg.get('OIDC_SCOPES', '') or None,
        'oidc_redirect_uri':   cfg.get('OIDC_REDIRECT_URI', '') or None,
        'oidc_client_secret':  mask_secret(cfg.get('OIDC_CLIENT_SECRET')),
    }

    # --- Caching (unified facade — see webapp.caching)
    try:
        from webapp.caching import caching as _caching_facade
        caching_block = _caching_facade.stats()
        # Legacy template keys kept alongside the new shape for back-compat.
        caching_block['flask_cache_backend']   = caching_block.get('backend')
        caching_block['cache_default_timeout'] = caching_block.get('default_timeout')
        caching_block['usage_cache']           = caching_block.get('usage')
    except Exception:
        caching_block = {
            'flask_cache_backend':   cfg.get('CACHE_TYPE', 'unknown'),
            'cache_default_timeout': cfg.get('CACHE_DEFAULT_TIMEOUT'),
            'usage_cache':           None,
            'flask':                 None,
            'chart':                 [],
            'usage':                 None,
        }

    # --- Audit & Logging
    audit_path = cfg.get('AUDIT_LOG_PATH', '')
    audit_logging = {
        'audit_enabled':  bool(cfg.get('AUDIT_ENABLED', False)),
        'audit_log_path': audit_path or None,
        'log_level':      cfg.get('LOG_LEVEL', 'INFO'),
        'log_file':       cfg.get('LOG_FILE') or None,
    }

    # --- Recent audit entries (None if missing/unreadable)
    audit_tail = tail_audit_log(audit_path, n=25)

    return {
        'application':   application,
        'databases':     databases,
        'auth':          auth,
        'caching':       caching_block,
        'audit_logging': audit_logging,
        'audit_tail':    audit_tail,
        'gathered_at':   datetime.now(),
    }
