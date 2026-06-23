"""fs-scans plugin loader and collection discovery for Flask.

The plugin (``fs_scans``) maintains its own SQLAlchemy engines — a
*different* database (CNPG/PostgreSQL) from the SAM MySQL one
Flask-SQLAlchemy binds at startup. We can't fold it under
``SQLALCHEMY_BINDS`` because ``fs_scans`` models are not part of SAM's
``db.Model`` registry, and unlike ``job_history`` the facade
(:class:`fs_scans.FsScanQueries`) *owns its own sessions* — it opens and
closes one per query, per collection. So there is no per-request session
context manager here; callers construct ``FsScanQueries(filesystems=…)``
directly (via the service layer) and the plugin's internal, memoized
engine cache is reused.

On app startup we:

1. Load the plugin once via :data:`sam.plugins.FS_SCANS`.
2. Discover the available collection schemas via ``list_pg_schemas()``.
3. Pre-warm one Engine per collection with ``get_engine(collection)`` so
   the first query doesn't pay TLS + auth, attach a ``SET
   application_name`` listener for CNPG ``pg_stat_activity`` attribution,
   and ``SELECT 1`` as a health check. The facade reuses these memoized
   engines on later ``FsScanQueries`` calls (same engine-cache key).
4. Stash ``{module, collections, enabled}`` on ``app.extensions`` so the
   service layer and the Admin → Configuration DB card can reach them.

The plugin is optional. If it can't be imported (developer skipped the
``[hpc]`` install extra), if the backend isn't configured, or if no
collections are reachable, we log a warning, mark the feature disabled,
and let the rest of the webapp boot — same posture as ``job_history``.
"""

from __future__ import annotations

import logging
import os
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from flask import Flask, current_app
from sqlalchemy import event, text

logger = logging.getLogger(__name__)

# app.extensions key under which we stash plugin state.
_EXT_KEY = 'fs_scans'


def init_fs_scans(app: Flask) -> None:
    """Load the fs-scans plugin and pre-warm one engine per collection.

    Called once from ``create_app`` after Flask-SQLAlchemy is bound.
    Reads these config values:

    - ``FS_SCANS_ENABLED`` (bool): master switch. Default True; set False
      (TestingConfig does) to skip plugin load entirely.

    Connection settings (backend, host, credentials) are read by the
    plugin itself from the ``FS_SCAN_*`` environment at engine-creation
    time — SAM does not pass them through.

    On any failure (plugin import error, backend unconfigured, no
    reachable collections) the feature is marked disabled and a warning
    is logged. The webapp continues to boot.
    """
    state: Dict[str, Any] = {
        'module':    None,
        # database -> {'collections': [str], 'engines': {collection: Engine}}.
        # One disk resource maps to one database (see FS_SCAN_RESOURCE_DATABASES);
        # collection schemas can repeat across databases, so we key by database
        # rather than flattening to a single {collection: engine} dict.
        'databases': {},
        'enabled':   False,
    }
    app.extensions[_EXT_KEY] = state

    if not app.config.get('FS_SCANS_ENABLED', False):
        logger.info('fs-scans: disabled by config, plugin not loaded')
        return

    # Plugin loading mirrors the CLI pattern and webapp/jobs/session.py.
    try:
        from sam.plugins import FS_SCANS
        mod = FS_SCANS.load()
    except Exception as exc:
        logger.warning(
            'fs-scans plugin not available — filesystem-scan features disabled: %s',
            exc,
        )
        return

    state['module'] = mod

    # `application_name` tags each connection with pod + db + collection so
    # postgres `pg_stat_activity` can attribute load. Mirrors the
    # job_history loader: the plugin owns ``connect_args`` inside its
    # ``get_engine``, so we attach a post-creation ``connect`` listener
    # rather than threading our own connect_args through.
    pod_id = os.environ.get('HOSTNAME') or socket.gethostname()

    # Read once here (main thread) and close over it — the warm pool runs in
    # worker threads where ``current_app`` isn't bound, so we can't read config
    # from inside the connect listener.
    stmt_timeout_ms = int(app.config.get('FS_SCAN_STATEMENT_TIMEOUT_MS', 0) or 0)

    def _warm(collection: str, database: Optional[str]):
        """Create + tag + health-check one collection's engine in *database*.

        Returns the Engine on success, or None on failure (logged). Safe to
        run concurrently: the plugin's ``get_engine`` cache is lock-guarded.
        ``database`` selects the CNPG database (the plugin's ``database=``
        selector); ``None`` uses the plugin's default ``FS_SCAN_PG_DB``.
        """
        try:
            engine = mod.get_engine(collection, database=database)
            if engine.url.drivername.startswith('postgresql'):
                tag = f'sam-webapp:{pod_id}:fs_scans:{database or "default"}:{collection}'
                _apply_connection_settings(
                    engine, tag, statement_timeout_ms=stmt_timeout_ms,
                )
            # Health check — also forces the pool to open one connection
            # so the application_name listener fires before first query.
            with engine.connect() as conn:
                conn.execute(text('SELECT 1'))
            logger.info(
                'fs-scans engine ready: db=%s collection=%s url=%s',
                database or 'default', collection, _safe_url(engine),
            )
            return engine
        except Exception as exc:
            logger.warning(
                'fs-scans engine init failed for db=%s collection=%s: %s',
                database or 'default', collection, exc,
            )
            return None

    # The set of DISTINCT databases to warm comes from the resource→database
    # map (Campaign_Store → campaign, Destor → desc1). An empty map falls back
    # to the plugin's single default database (None → FS_SCAN_PG_DB), preserving
    # the original single-database behavior.
    resource_dbs: Dict[str, str] = app.config.get('FS_SCAN_RESOURCE_DATABASES') or {}
    db_names = sorted(set(resource_dbs.values())) or [None]

    databases: Dict[str, Any] = {}
    for database in db_names:
        # Discover collection schemas for this database. A failure here means
        # that database is unreachable/unconfigured — skip it, but keep going so
        # one bad database (e.g. desc1 not yet provisioned) can't disable the
        # rest.
        try:
            collections = mod.list_pg_schemas(database=database)
        except Exception as exc:
            logger.warning(
                'fs-scans: could not list collections for db=%s (unreachable?) — '
                'skipping: %s', database or 'default', exc,
            )
            continue

        # Warm collections concurrently — each opens a fresh TLS connection to
        # the remote CNPG (~1-1.5s), so serial warming of a dozen-plus
        # collections would add ~20s to webapp boot. Bounded pool keeps it to a
        # few seconds.
        engines: Dict[str, Any] = {}
        if collections:
            max_workers = min(len(collections), 8)
            with ThreadPoolExecutor(max_workers=max_workers,
                                    thread_name_prefix='fs-scans-warm') as pool:
                for collection, engine in zip(
                    collections,
                    pool.map(lambda c: _warm(c, database), collections),
                ):
                    if engine is not None:
                        engines[collection] = engine
        if engines:
            databases[database] = {
                'collections': sorted(engines),
                'engines':     engines,
            }

    state['databases'] = databases
    state['enabled'] = any(d['collections'] for d in databases.values())


def _apply_connection_settings(
    engine, app_name: str, *, statement_timeout_ms: int = 0
) -> None:
    """Apply per-connection postgres settings on every new DBAPI connection.

    Sets ``application_name`` (for ``pg_stat_activity`` attribution) and, when
    ``statement_timeout_ms`` > 0, a server-side ``statement_timeout`` so a
    runaway fs-scans query fails cleanly instead of holding a CNPG connection
    (and a gthread thread) until the gunicorn worker timeout.

    The ``connect`` event fires once per fresh postgres connection (not on
    pool checkout), so this is the cheap, correct hook when the engine's
    ``connect_args`` are owned by the plugin and can't be amended in-place.
    Mirrors ``webapp/jobs/session.py``.

    Toggles autocommit around the ``SET``s because postgres documents that
    ``application_name`` changes made via ``SET`` "will not appear in
    pg_stat_activity until after a commit or rollback" — and psycopg2's
    default is ``autocommit=False``.
    """
    @event.listens_for(engine, 'connect')
    def _on_connect(dbapi_conn, _conn_record):
        saved = dbapi_conn.autocommit
        dbapi_conn.autocommit = True
        try:
            cur = dbapi_conn.cursor()
            try:
                cur.execute("SET application_name = %s", (app_name,))
                if statement_timeout_ms and statement_timeout_ms > 0:
                    # statement_timeout accepts an integer number of ms.
                    cur.execute(
                        "SET statement_timeout = %s",
                        (str(int(statement_timeout_ms)),),
                    )
            finally:
                cur.close()
        finally:
            dbapi_conn.autocommit = saved


def is_enabled(app: Optional[Flask] = None) -> bool:
    """True iff the plugin loaded and at least one collection is reachable."""
    state = (app or current_app).extensions.get(_EXT_KEY) or {}
    return bool(state.get('enabled'))


def get_module(app: Optional[Flask] = None):
    """Return the loaded ``fs_scans`` module, or ``None`` if disabled."""
    state = (app or current_app).extensions.get(_EXT_KEY) or {}
    return state.get('module')


def get_databases(app: Optional[Flask] = None) -> Dict[str, Any]:
    """Return ``{database: {'collections': [...], 'engines': {...}}}`` (warmed).

    The per-database warmed state. Used by the Admin → Configuration card to
    render one health row per CNPG database, and by the resource→database
    helpers below. Empty when the plugin is disabled/unreachable.
    """
    state = (app or current_app).extensions.get(_EXT_KEY) or {}
    return state.get('databases') or {}


def get_collections(app: Optional[Flask] = None) -> List[str]:
    """Union of warmed/reachable collection schemas across all databases.

    A flat, deduplicated view for callers that don't care which database a
    collection lives in. Resource-scoped reachability should use
    :func:`collections_for_resource` instead, which is database-aware.
    """
    out: set = set()
    for db in get_databases(app).values():
        out.update(db.get('collections') or [])
    return sorted(out)


def database_for_resource(
    resource_name: str, app: Optional[Flask] = None
) -> Optional[str]:
    """The CNPG database that backs a disk *resource* (or ``None``).

    Reads the ``FS_SCAN_RESOURCE_DATABASES`` map (resource NAME → database).
    Threaded into ``FsScanQueries(database=...)`` by the service layer so each
    resource's queries hit its own database. Safe outside an app context
    (returns ``None``) so service helpers can resolve it unconditionally.
    """
    try:
        cfg = (app or current_app).config
    except RuntimeError:
        return None
    return (cfg.get('FS_SCAN_RESOURCE_DATABASES') or {}).get(resource_name)


def collections_for_resource(
    resource_name: str, app: Optional[Flask] = None
) -> List[str]:
    """Warmed collection schemas that make up a disk *resource*, unscoped.

    The single decision point for resource→collections when a query is **not**
    project-scoped (resource mode). Resolves the resource's database via
    :func:`database_for_resource`, then returns that database's warmed
    collections. Returns ``[]`` when the plugin is off, the resource is
    unmapped, or its database warmed nothing (so callers degrade to "no
    results", same as project mode).
    """
    database = database_for_resource(resource_name, app)
    if database is None:
        return []
    return list(get_databases(app).get(database, {}).get('collections') or [])


def get_engines(app: Optional[Flask] = None) -> Dict[str, Any]:
    """Return a flat ``{collection: Engine}`` merged across databases.

    Legacy/convenience view. Collection names that repeat across databases
    collide (last wins) — the Admin card uses :func:`get_databases` instead so
    it can render each database separately.
    """
    out: Dict[str, Any] = {}
    for db in get_databases(app).values():
        out.update(db.get('engines') or {})
    return out


def _safe_url(engine) -> str:
    """Best-effort password-stripped URL for log lines.

    Mirrors ``webapp/jobs/session.py:_safe_url`` — inlined to avoid an
    import cycle with the config-inspect module.
    """
    try:
        u = engine.url
        user = f"{u.username}@" if u.username else ''
        host = u.host or ''
        port = f":{u.port}" if u.port else ''
        database = f"/{u.database}" if u.database else ''
        return f"{u.drivername}://{user}{host}{port}{database}"
    except Exception:
        return '<unknown>'
