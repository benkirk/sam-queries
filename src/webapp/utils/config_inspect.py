"""Read-only runtime-configuration inspection helpers.

Used by the Admin -> Configuration tab to surface webapp state for
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
import time
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text


# Per-pid cache so a fresh worker doesn't re-parse /proc on every request.
# Module-level dict survives forks (correctly: each forked worker gets its
# own copy); keyed by os.getpid() so it's resilient to the master initializing
# this dict before forking.
_worker_started_by_pid: Dict[int, datetime] = {}


# ---------------------------------------------------------------------------
# Primitive helpers
# ---------------------------------------------------------------------------

def _safe_pkg_version(name: str) -> str:
    """Return the installed version of a package, or ``'unknown'`` if it
    can't be resolved. Replaces ``flask.__version__`` which Flask 3.2
    removes; works for any importable distribution.
    """
    try:
        return _pkg_version(name)
    except PackageNotFoundError:
        return 'unknown'


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


def classify_connection_error(error: Optional[str]) -> Optional[Dict[str, str]]:
    """Classify a connection-failure error string into a cause class.

    Distinguishes the two failure modes that look identical on the Admin
    health card but require opposite responses:

    - ``server-exhaustion``: the database server is out of connection
      slots. Raising the local pool would reserve *more* slots per pod
      and make this worse. Diagnose at the server (``pg_stat_activity``).
    - ``client-exhaustion``: the local SQLAlchemy pool is full and
      ``pool_timeout`` elapsed waiting for a slot. Raising the pool is
      the right response.
    - ``connection-refused``: the host is unreachable (DNS failure,
      port closed, server down). Neither pool tuning helps.

    Returns ``None`` when ``error`` is falsy. Returns ``{'class': 'unknown',
    'hint': None}`` when the string doesn't match a known pattern.
    """
    if not error:
        return None
    e = error.lower()
    if ('remaining connection slots are reserved' in e
            or 'too many connections' in e
            or 'sorry, too many clients already' in e):
        return {
            'class': 'server-exhaustion',
            'hint':  ('Database server is out of connection slots. '
                      'Diagnose with pg_stat_activity on the server — '
                      'raising the local pool would make this worse.'),
        }
    if ('queuepool limit' in e
            or 'timed out waiting for connection' in e
            or 'connection pool is full' in e):
        return {
            'class': 'client-exhaustion',
            'hint':  ('Local SQLAlchemy pool is full. Raise the '
                      'pool_size / max_overflow env vars for this engine.'),
        }
    if ('connection refused' in e
            or 'could not connect to server' in e
            or 'could not translate host name' in e
            or 'name or service not known' in e):
        return {
            'class': 'connection-refused',
            'hint':  ('Database host is unreachable (DNS, port, or '
                      'server down). Not a pool issue.'),
        }
    return {'class': 'unknown', 'hint': None}


# ORM <-> database schema drift.
#
# On 2026-08-10 a production DDL change dropped 8 columns the ORM still
# selected: every page 500'd with MySQL 1054 for ~20 minutes while
# /api/v1/health/ reported 200 the whole time, because a `SELECT 1` ping cannot
# see a column that stopped existing. One INFORMATION_SCHEMA query covers every
# mapped model, catching the bug class rather than the tables someone probes.

# (expires_at_monotonic, result). Module-level, so each gunicorn worker keeps
# its own memo; a warm entry inherited across a fork is at most _TTL stale.
_schema_drift_memo: Optional[Tuple[float, Dict[str, Any]]] = None

_SCHEMA_DRIFT_TTL_SECONDS = 60.0


def _mapped_tables() -> Dict[str, set]:
    """Return {table_name: {column_names}} for models on the default bind.

    Mirrors the filter in ``scripts/lib/schema_introspection.py``
    ``iter_table_mappers``: skips views (``info={'is_view': True}``, which are
    never DDL-managed by us) and cross-bind models (``__bind_key__``, i.e.
    system_status, which is Alembic-managed and covered by
    ``test_alembic_migrations.py``).
    """
    from sam.base import Base

    tables: Dict[str, set] = {}
    for mapper in Base.registry.mappers:
        selectable = mapper.persist_selectable
        if selectable.info.get('is_view', False):
            continue
        cls = mapper.class_
        if getattr(cls, '__bind_key__', None) is not None:
            continue
        tables.setdefault(selectable.name, set()).update(
            c.name for c in selectable.columns
        )
    return tables


def schema_drift(engine, ttl_seconds: float = _SCHEMA_DRIFT_TTL_SECONDS) -> Dict[str, Any]:
    """Report columns the ORM selects that the database does not have.

    Returns a health-check entry:

        {'status': 'healthy'}
        {'status': 'unhealthy', 'drift': ['users: ORM has pdb_modified_time']}
        {'status': 'unknown', 'error': '...'}   # introspection itself failed

    Only ORM-side extras flip the status. Two deliberate exclusions:

    - **DB columns absent from the ORM are ignored.** SQLAlchemy names its
      columns explicitly, so an unmapped column breaks nothing. This matches
      ``test_database_columns_in_orm``, which is informational for the same
      reason.
    - **Tables absent from the database are reported but do NOT flip the
      status.** A table that a deployment has not been given yet is an
      operational state with its own remedy, not schema drift; counting it here
      would pin an environment at 503 and train everyone to ignore this check.
      The bug class this guards is specifically "table exists, column does not".
      ``missing_tables`` is still reported, so the condition stays visible —
      that key going from populated to absent is how the 2026-08-10 XRAS /
      notification tables were confirmed live in production.

    Memoized for ``ttl_seconds``; ``/api/v1/health/`` is public and exempt from
    rate limiting, so this costs at most one INFORMATION_SCHEMA query per
    minute per pod.
    """
    global _schema_drift_memo

    now = time.monotonic()
    if _schema_drift_memo is not None and _schema_drift_memo[0] > now:
        return _schema_drift_memo[1]

    try:
        orm_tables = _mapped_tables()
        db_tables: Dict[str, set] = {}
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT TABLE_NAME, COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
            """))
            for table_name, column_name in rows:
                db_tables.setdefault(table_name, set()).add(column_name)

        drift: List[str] = []
        missing_tables: List[str] = []
        for table, orm_cols in sorted(orm_tables.items()):
            db_cols = db_tables.get(table)
            if db_cols is None:
                missing_tables.append(table)
                continue
            extra = orm_cols - db_cols
            if extra:
                drift.append(f"{table}: ORM has {', '.join(sorted(extra))}")

        result: Dict[str, Any] = {
            'status': 'unhealthy' if drift else 'healthy',
            'models_checked': len(orm_tables),
        }
        if drift:
            result['drift'] = drift
        if missing_tables:
            result['missing_tables'] = sorted(missing_tables)
    except Exception as exc:
        # Introspection failing is not itself schema drift — report it
        # separately so a permissions or connectivity problem doesn't get
        # misread as a dropped column.
        result = {'status': 'unknown', 'error': str(exc)}

    _schema_drift_memo = (now + ttl_seconds, result)
    return result


def reset_schema_drift_cache() -> None:
    """Drop the memoized ``schema_drift`` result. For tests."""
    global _schema_drift_memo
    _schema_drift_memo = None


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


# Worker / pod runtime probes: the worker's own /proc and /sys/fs/cgroup files
# only -- no subprocess, no other-pod visibility, no privileged ops. Every probe
# returns None on macOS dev or any host without those entries, so the Flask dev
# server and a bare-metal host both render the card. cgroup v2 only; v1 hosts
# silently report None.

def _worker_start_time() -> datetime:
    """Best-effort worker process start time, cached per pid.

    Linux: parses ``/proc/self/stat`` field 22 (process start in clock
    ticks since boot) + ``/proc/stat`` btime. This is the kernel's truth
    even when ``preload_app=True`` makes module-level timestamps reflect
    master init time rather than worker fork time.

    macOS / non-procfs: falls back to the time of the first call from
    this pid (close enough as a lower bound for dev).
    """
    pid = os.getpid()
    cached = _worker_started_by_pid.get(pid)
    if cached:
        return cached
    try:
        with open(f'/proc/{pid}/stat', 'rb') as f:
            data = f.read()
        # comm field is wrapped in parens and may contain spaces or
        # parens itself — find the LAST ')' before splitting.
        rparen = data.rindex(b')')
        rest = data[rparen + 2:].split()
        # Field 22 (1-indexed) is starttime in clock ticks; index 19
        # in `rest` because rest starts at field 3 (state).
        starttime_ticks = int(rest[19])
        boot = None
        with open('/proc/stat') as f:
            for line in f:
                if line.startswith('btime '):
                    boot = int(line.split()[1])
                    break
        if boot is None:
            raise ValueError('btime not found')
        clk_tck = os.sysconf(os.sysconf_names['SC_CLK_TCK'])
        result = datetime.fromtimestamp(boot + starttime_ticks / clk_tck)
    except (OSError, ValueError, IndexError, KeyError):
        result = datetime.now()
    _worker_started_by_pid[pid] = result
    return result


def _proc_rss_bytes() -> Optional[int]:
    """RSS of the current process in bytes, or None if /proc unavailable."""
    try:
        with open('/proc/self/status') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) * 1024  # kB -> bytes
    except OSError:
        pass
    return None


def _cgroup_memory() -> Tuple[Optional[int], Optional[int]]:
    """(used_bytes, limit_bytes) from cgroup v2. (None, None) outside cgroups
    or when memory is unrestricted."""
    used: Optional[int] = None
    limit: Optional[int] = None
    try:
        used = int(Path('/sys/fs/cgroup/memory.current').read_text().strip())
    except (OSError, ValueError):
        pass
    try:
        raw = Path('/sys/fs/cgroup/memory.max').read_text().strip()
        if raw != 'max':
            limit = int(raw)
    except (OSError, ValueError):
        pass
    return used, limit


def _cgroup_cpu_limit() -> Optional[float]:
    """Effective CPU limit as a float number of cores. None if unrestricted
    or cgroup v2 unavailable. The value comes from the same file the
    gunicorn-worker fix reads (``/sys/fs/cgroup/cpu.max``)."""
    try:
        raw = Path('/sys/fs/cgroup/cpu.max').read_text().strip()
        quota_str, period_str = raw.split()
        if quota_str == 'max':
            return None
        return int(quota_str) / int(period_str)
    except (OSError, ValueError):
        return None


def gather_server_info() -> Dict[str, Any]:
    """Worker-scoped runtime snapshot for the Admin -> Server Information card.

    Cheap to call (a handful of file reads). Safe to expose without auth
    headers other than the existing VIEW_SYSTEM_CONFIG gate — there are no
    secrets here, and every probe is scoped to this worker / this cgroup.

    Returned values reflect *this worker process*. With gunicorn's 33+
    workers per pod, the same admin user reloading the Configuration tab
    can land on a different worker each time and see different numbers.
    The card UI calls this out explicitly.
    """
    cg_used, cg_limit = _cgroup_memory()
    started = _worker_start_time()
    return {
        'pod_hostname':      socket.gethostname(),
        'worker_pid':        os.getpid(),
        'worker_started':    started,
        'worker_uptime':     _uptime(started),
        'process_rss':       _proc_rss_bytes(),
        'cgroup_mem_used':   cg_used,
        'cgroup_mem_limit':  cg_limit,
        'cgroup_cpu_limit':  _cgroup_cpu_limit(),
        'gunicorn_workers':  os.environ.get('GUNICORN_WORKERS') or None,
        'git_sha':           os.getenv('GIT_SHA', '') or None,
        'build_date':        os.getenv('BUILD_DATE', '') or None,
        'gathered_at':       datetime.now(),
    }


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

    # --- Application (config-shaped facts; runtime instance facts moved
    # to the Server Information card below)
    application = {
        'config_class':    config_class_name,
        'flask_config':    os.getenv('FLASK_CONFIG', 'development'),
        'debug':           bool(cfg.get('DEBUG')),
        'testing':         bool(cfg.get('TESTING')),
        'python_version':  platform.python_version(),
        'flask_version':   _safe_pkg_version('flask'),
        'display_tz':      display_tz_name,
        'display_tz_abbr': display_tz_abbr,
    }

    # --- Server Information (worker-scoped runtime facts)
    server = gather_server_info()

    # --- Database (per bind)
    databases = []
    engines = {'sam': db.engine}
    ss_engine = db.engines.get('system_status') if hasattr(db, 'engines') else None
    if ss_engine:
        engines['system_status'] = ss_engine

    # Lazy-import to avoid circulars
    from webapp.api.v1.health import _ping_engine

    def _health_row(name: str, engine, **extra) -> dict:
        """One Database-card row: ping + pool stats + safe URL.

        The single row shape for SAM's own binds and for every plugin engine,
        so a new plugin surfaces on the card by calling this rather than
        re-deriving 'healthy' if ok else 'unhealthy' for the fourth time.
        """
        ok, latency_ms, err = _ping_engine(engine)
        try:
            stats = pool_stats(engine.pool)
        except Exception:
            stats = None
        return {
            'name':         name,
            'url':          format_db_url_safe(engine),
            'status':       'healthy' if ok else 'unhealthy',
            'latency_ms':   latency_ms,
            'error':        err,
            'error_detail': classify_connection_error(err),
            'pool':         stats,
            **extra,
        }

    for name, engine in engines.items():
        databases.append(_health_row(name, engine))

    # hpc-usage-queries plugin (one engine per configured machine).
    # Registered on app.extensions by webapp.jobs.init_job_history at
    # startup; absent / empty when the plugin is disabled or not
    # installed, in which case no rows are added.
    jh_state = app.extensions.get('hpc_usage_queries') or {}
    for machine, engine in (jh_state.get('engines') or {}).items():
        databases.append(_health_row(f'job_history ({machine})', engine))

    # fs-scans collections are *schemas* within one CNPG database per disk
    # resource, so this renders ONE health row per database with per-collection
    # scan-date freshness hung off it. Registered on app.extensions by
    # webapp.disk_scans.init_fs_scans; empty when the plugin is disabled.
    fs_state = app.extensions.get('fs_scans') or {}
    fs_databases = fs_state.get('databases') or {}
    if fs_databases:
        fs_mod = fs_state.get('module')
        # One health row per backing CNPG database (campaign -> Campaign_Store,
        # desc1 -> Destor); the warmed state is already grouped by database.
        for dbname, db_state in sorted(fs_databases.items()):
            engines = db_state.get('engines') or {}
            if not engines:
                continue
            items = sorted(engines.items())
            display_db = dbname or 'fs_scans'
            # Health from one representative engine — all share host + db.
            rep_engine = items[0][1]
            # Per-collection scan-date freshness (best-effort; one tiny
            # scan_metadata read each, pinned to THIS database). A failing
            # collection reports None rather than sinking the whole card.
            collections = []
            for collection, _engine in items:
                scan_date = None
                try:
                    dates = fs_mod.FsScanQueries(
                        filesystems=[collection], database=dbname,
                    ).scan_dates()
                    if dates:
                        scan_date = max(dates).date().isoformat()
                except Exception:
                    scan_date = None
                collections.append({'name': collection, 'scan_date': scan_date})
            present = [c['scan_date'] for c in collections if c['scan_date']]
            databases.append(_health_row(
                f'fs_scans ({display_db})', rep_engine,
                scans={
                    'collection_count': len(collections),
                    'collections':      collections,
                    'oldest_scan':      min(present) if present else None,
                    'newest_scan':      max(present) if present else None,
                },
            ))

    # --- Authentication
    auth = {
        'auth_provider':       cfg.get('AUTH_PROVIDER', 'stub'),
        'session_httponly':    bool(cfg.get('SESSION_COOKIE_HTTPONLY', False)),
        'session_samesite':    cfg.get('SESSION_COOKIE_SAMESITE', ''),
        'session_secure':      bool(cfg.get('SESSION_COOKIE_SECURE', False)),
        'session_lifetime':    str(cfg.get('PERMANENT_SESSION_LIFETIME', '')),
        'flask_secret_key':    mask_secret(cfg.get('SECRET_KEY')),
        'api_key_usernames':   sorted(list((cfg.get('API_KEYS') or {}).keys())),
        'api_keys_db_enabled': bool(cfg.get('API_KEYS_DB_ENABLED', False)),
        'api_keys_db_ttl':     int(cfg.get('API_KEYS_DB_TTL', 0)),
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

    # --- Rate limiting (unified facade — see webapp.limiter)
    try:
        from webapp.limiter import limiter as _limiter_facade
        rate_limits_block = _limiter_facade.stats()
    except Exception:
        rate_limits_block = {
            'enabled':             False,
            'storage':             None,
            'tiers':               {},
            'events_24h':          0,
            'top_offenders_24h':   [],
            'active_blocks_count': 0,
        }

    # Notifications. Config comes from NotifyConfig.summary(), which never
    # returns MAIL_PASSWORD, so this module's masking rule holds by construction
    # rather than by the template remembering.
    #
    # WARNING: NO ADDRESSES. This card is VIEW_SYSTEM_CONFIG while every
    # activity-log row names a real person's email and is SYSTEM_ADMIN. The one
    # exception is `redirect_to`, an operator-configured sink rather than a
    # subject's address -- hiding it would defeat the line's purpose, since a
    # staging box quietly swallowing mail is what it exists to surface.
    try:
        from sam.notify import NotifyConfig
        from sam.queries.notifications import summarize_notifications

        notify_config = NotifyConfig.from_environment()
        notifications_block = dict(notify_config.summary())
        notifications_block.update(summarize_notifications(
            db.session,
            queued_stale_seconds=notify_config.queued_stale_seconds))
    except Exception:
        # The table may not exist yet (it awaits a DBA in production), and a
        # config card that 500s is worse than one saying "unavailable".
        #
        # Roll back FIRST: the failure came from a statement, which marks the
        # transaction as needing rollback, so any later `db.session` use in the
        # request would raise PendingRollbackError instead of its own error. No
        # caller does DB work after this block today -- which is why the trap is
        # worth closing before someone adds one.
        try:
            db.session.rollback()
        except Exception:                    # pragma: no cover - defensive
            pass
        notifications_block = {
            'enabled':      False,
            'transport':    None,
            'relay':        None,
            'redirect_to':  None,
            'bcc':          None,
            'addressing':   {},
            'unavailable':  True,
            'sent': 0, 'redirected': 0, 'failed': 0, 'suppressed': 0,
            'queued_stuck': 0, 'total': 0, 'by_status': {},
            # The template reads this outside the `unavailable` short-circuit, so it
            # has to be present even though nothing renders it in this state.
            'window_hours': None,
        }

    # Scheduled tasks: registry + SAM_TASKS_DISABLED + task_run counts. No
    # addresses and no PII. `runner_id` is a pod name and `detail` can hold a
    # traceback naming hosts and paths, which is why the per-row detail modal
    # sits a tier above this card -- but neither reaches here.
    try:
        from scheduling.registry import TASKS
        from scheduling.runner import disabled_tasks
        from system_status.queries.task_runs import summarize_task_runs

        # Import for the registration side effects — TASKS is populated by
        # the @task decorator at import time.
        import scheduling.tasks  # noqa: F401

        disabled = sorted(disabled_tasks())
        scheduled_tasks_block = {
            'tasks': [
                {
                    'name': name,
                    'schedule': task.schedule.describe(),
                    'description': task.description,
                    'disabled': name in disabled,
                }
                for name, task in sorted(TASKS.items())
            ],
            'disabled': disabled,
        }
        scheduled_tasks_block.update(summarize_task_runs(db.session))
    except Exception:
        # `task_run` does not exist until Alembic 0006 is applied, which staging
        # and production have not had, so this card renders before its table
        # exists. Roll back first, for the reason spelled out in the
        # notifications block above.
        try:
            db.session.rollback()
        except Exception:                    # pragma: no cover - defensive
            pass
        scheduled_tasks_block = {
            'tasks': [],
            'disabled': [],
            'unavailable': True,
            'succeeded': 0, 'partial': 0, 'failed': 0, 'skipped': 0,
            'stale_running': 0, 'total': 0, 'by_state': {},
            'last_dispatch': None, 'last_dispatch_age': None,
            # Read by the template outside the `unavailable` short-circuit, so
            # it must be present even though nothing renders it in this state.
            'window_hours': None,
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
    audit_tail = tail_audit_log(audit_path, n=500)

    return {
        'gathered_at':     datetime.now(),
        'application':     application,
        'audit_logging':   audit_logging,
        'audit_tail':      audit_tail,
        'auth':            auth,
        'caching':         caching_block,
        'databases':       databases,
        'notifications':   notifications_block,
        'rate_limits':     rate_limits_block,
        'scheduled_tasks': scheduled_tasks_block,
        'server':          server,
    }
