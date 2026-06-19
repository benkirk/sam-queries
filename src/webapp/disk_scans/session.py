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
        'module':      None,
        'collections': [],   # list[str] of warmed collection schemas
        'engines':     {},   # collection -> Engine (for health/config card)
        'enabled':     False,
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

    # Discover collection schemas (postgres backend). A failure here means
    # the backend is unreachable/unconfigured — degrade gracefully.
    try:
        collections = mod.list_pg_schemas()
    except Exception as exc:
        logger.warning(
            'fs-scans: could not list collections (backend unreachable?) — '
            'features disabled: %s',
            exc,
        )
        return

    # `application_name` tags each connection with pod + collection so
    # postgres `pg_stat_activity` can attribute load. Mirrors the
    # job_history loader: the plugin owns ``connect_args`` inside its
    # ``get_engine``, so we attach a post-creation ``connect`` listener
    # rather than threading our own connect_args through.
    pod_id = os.environ.get('HOSTNAME') or socket.gethostname()

    def _warm(collection: str):
        """Create + tag + health-check one collection's engine.

        Returns the Engine on success, or None on failure (logged). Safe to
        run concurrently: the plugin's ``get_engine`` cache is lock-guarded.
        """
        try:
            engine = mod.get_engine(collection)
            if engine.url.drivername.startswith('postgresql'):
                _attach_application_name(
                    engine, f'sam-webapp:{pod_id}:fs_scans:{collection}'
                )
            # Health check — also forces the pool to open one connection
            # so the application_name listener fires before first query.
            with engine.connect() as conn:
                conn.execute(text('SELECT 1'))
            logger.info(
                'fs-scans engine ready: collection=%s url=%s',
                collection, _safe_url(engine),
            )
            return engine
        except Exception as exc:
            logger.warning(
                'fs-scans engine init failed for collection=%s: %s',
                collection, exc,
            )
            return None

    # Warm collections concurrently — each opens a fresh TLS connection to the
    # remote CNPG (~1-1.5s), so serial warming of a dozen-plus collections
    # would add ~20s to webapp boot. Bounded pool keeps it to a few seconds.
    engines: Dict[str, Any] = {}
    if collections:
        max_workers = min(len(collections), 8)
        with ThreadPoolExecutor(max_workers=max_workers,
                                thread_name_prefix='fs-scans-warm') as pool:
            for collection, engine in zip(
                collections, pool.map(_warm, collections)
            ):
                if engine is not None:
                    engines[collection] = engine

    state['engines'] = engines
    state['collections'] = sorted(engines)
    state['enabled'] = bool(engines)


def _attach_application_name(engine, app_name: str) -> None:
    """Run ``SET application_name`` on every new DBAPI connection for this engine.

    The ``connect`` event fires once per fresh postgres connection (not on
    pool checkout), so this is the cheap, correct hook to tag connections
    when the engine's ``connect_args`` are owned by the plugin and can't
    be amended in-place. Mirrors ``webapp/jobs/session.py``.

    Toggles autocommit around the ``SET`` because postgres documents that
    ``application_name`` changes made via ``SET`` "will not appear in
    pg_stat_activity until after a commit or rollback" — and psycopg2's
    default is ``autocommit=False``.
    """
    @event.listens_for(engine, 'connect')
    def _set_app_name(dbapi_conn, _conn_record):
        saved = dbapi_conn.autocommit
        dbapi_conn.autocommit = True
        try:
            cur = dbapi_conn.cursor()
            try:
                cur.execute("SET application_name = %s", (app_name,))
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


def get_collections(app: Optional[Flask] = None) -> List[str]:
    """Return the list of warmed/reachable collection schemas (possibly empty)."""
    state = (app or current_app).extensions.get(_EXT_KEY) or {}
    return state.get('collections') or []


def collections_for_resource(
    resource_name: str, app: Optional[Flask] = None
) -> List[str]:
    """Warmed collection schemas that make up a disk *resource*, unscoped.

    The single decision point for resource→collections when a query is **not**
    project-scoped (resource mode). Today every warmed collection lives in the
    one ``campaign`` CNPG database (one DB per resource via the plugin's
    ``FS_SCAN_PG_DB``), so this returns :func:`get_collections` verbatim — i.e.
    the whole resource.

    This is the **seam** where a future resource→database map plugs in: once a
    second resource (e.g. Destor) ships its own collections, branch here on
    *resource_name* rather than scattering the mapping across callers. Returns
    ``[]`` when the plugin is off (so resource-mode callers degrade to "no
    results", same as project mode).
    """
    return get_collections(app)


def get_engines(app: Optional[Flask] = None) -> Dict[str, Any]:
    """Return ``{collection: Engine}`` for warmed collections (possibly empty).

    Used by the Admin → Configuration Database card to ping each fs-scans
    collection and report health / scan-date freshness.
    """
    state = (app or current_app).extensions.get(_EXT_KEY) or {}
    return state.get('engines') or {}


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
