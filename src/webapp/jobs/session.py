"""hpc-usage-queries plugin loader and session factory for Flask.

The plugin (``job_history``) maintains its own SQLAlchemy engine per
machine — a *different* database from the SAM MySQL one Flask-SQLAlchemy
binds at startup. We can't fold it under ``SQLALCHEMY_BINDS`` because
``job_history`` models are not part of SAM's ``db.Model`` registry.

Instead, on app startup we:

1. Load the plugin once via ``sam.plugins.require_plugin``.
2. Call ``get_engine(machine, pool_kwargs=…)`` per configured machine so
   Engines are warmed before the first request. The plugin memoizes
   engines internally; we just hold a reference for introspection
   (e.g. the Admin → Configuration DB card).
3. Stash both the plugin module and the engine dict on ``app.extensions``
   so routes/services can reach them via :func:`get_module` and
   :func:`get_engines`.

The plugin is optional. If it can't be imported (developer skipped the
``[hpc]`` install extra), we log a warning, mark the feature disabled,
and let the rest of the webapp boot — same posture as ``sam-admin``.
"""

from __future__ import annotations

import logging
import os
import socket
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from flask import Flask, current_app
from sqlalchemy import event

logger = logging.getLogger(__name__)

# app.extensions key under which we stash plugin state.
_EXT_KEY = 'hpc_usage_queries'


def init_job_history(app: Flask) -> None:
    """Load the hpc-usage-queries plugin and open per-machine engines.

    Called once from ``create_app`` after Flask-SQLAlchemy is bound.
    Reads these config values:

    - ``JOB_HISTORY_MACHINES`` (list[str]): machines to pre-warm engines
      for. Default ``['derecho', 'casper']``. Set to ``[]`` to disable
      (tests do this via TestingConfig).
    - ``JOB_HISTORY_POOL_KWARGS`` (dict): forwarded to
      ``job_history.get_engine(pool_kwargs=…)``. Only honored on the
      PostgreSQL backend; SQLite ignores most pool args.

    On any failure (plugin import error, engine init error, missing env
    config for the postgres backend) the offending machine is skipped
    and a warning is logged. The webapp continues to boot.
    """
    machines = app.config.get('JOB_HISTORY_MACHINES', ['derecho', 'casper'])
    pool_kwargs = app.config.get('JOB_HISTORY_POOL_KWARGS', {}) or {}

    state: Dict[str, Any] = {
        'module':  None,
        'engines': {},  # machine -> Engine
        'enabled': False,
    }
    app.extensions[_EXT_KEY] = state

    if not machines:
        logger.info('hpc-usage-queries: no machines configured, plugin not loaded')
        return

    # Plugin loading mirrors the CLI pattern in src/cli/accounting/commands.py.
    try:
        from sam.plugins import HPC_USAGE_QUERIES
        mod = HPC_USAGE_QUERIES.load()
    except Exception as exc:
        logger.warning(
            'hpc-usage-queries plugin not available — per-job features disabled: %s',
            exc,
        )
        return

    state['module'] = mod

    # `application_name` tags each connection with pod + engine so postgres
    # `pg_stat_activity` can attribute load without IP archaeology.
    # We can't inject this via `pool_kwargs` because the plugin's
    # ``get_engine`` (peer repo: hpc-usage-queries) sets ``connect_args``
    # itself when calling ``create_engine``, so passing our own
    # ``connect_args`` through ``pool_kwargs`` would collide. Instead we
    # attach a ``connect`` event listener after engine creation that issues
    # ``SET application_name`` once per fresh DBAPI connection. libpq
    # truncates to 63 chars; the format below (~54 chars on typical k8s
    # pod names) stays comfortably under the limit.
    pod_id = os.environ.get('HOSTNAME') or socket.gethostname()

    # Eagerly create one Engine per machine. A failure here is logged
    # per-machine; other machines can still come up.
    for machine in machines:
        try:
            engine = mod.get_engine(machine, pool_kwargs=pool_kwargs)
            state['engines'][machine] = engine
            if engine.url.drivername.startswith('postgresql'):
                _attach_application_name(engine, f'sam-webapp:{pod_id}:job_history:{machine}')
            logger.info(
                'hpc-usage-queries engine ready: machine=%s url=%s',
                machine,
                _safe_url(engine),
            )
        except Exception as exc:
            logger.warning(
                'hpc-usage-queries engine init failed for machine=%s: %s',
                machine, exc,
            )

    state['enabled'] = bool(state['engines'])


def _attach_application_name(engine, app_name: str) -> None:
    """Run ``SET application_name`` on every new DBAPI connection for this engine.

    The ``connect`` event fires once per fresh postgres connection (not on
    pool checkout), so this is the cheap, correct hook to tag connections
    when the engine's ``connect_args`` are owned by the plugin and can't
    be amended in-place.
    """
    @event.listens_for(engine, 'connect')
    def _set_app_name(dbapi_conn, _conn_record):
        cur = dbapi_conn.cursor()
        try:
            cur.execute("SET application_name = %s", (app_name,))
        finally:
            cur.close()


def is_enabled(app: Optional[Flask] = None) -> bool:
    """True iff the plugin loaded and at least one engine is ready."""
    state = (app or current_app).extensions.get(_EXT_KEY) or {}
    return bool(state.get('enabled'))


def get_module(app: Optional[Flask] = None):
    """Return the loaded ``job_history`` module, or ``None`` if disabled."""
    state = (app or current_app).extensions.get(_EXT_KEY) or {}
    return state.get('module')


def get_engines(app: Optional[Flask] = None) -> Dict[str, Any]:
    """Return ``{machine: Engine}`` (possibly empty)."""
    state = (app or current_app).extensions.get(_EXT_KEY) or {}
    return state.get('engines') or {}


@contextmanager
def job_history_session(machine: str) -> Iterator[Any]:
    """Yield a fresh SQLAlchemy session bound to *machine*'s engine.

    Closes the session on exit. Engines are reused across calls; only the
    Session is per-call (the standard SQLAlchemy pattern).

    Raises:
        RuntimeError: if the plugin is disabled or *machine* has no engine.
    """
    state = current_app.extensions.get(_EXT_KEY) or {}
    if not state.get('enabled'):
        raise RuntimeError(
            'hpc-usage-queries plugin is not available; '
            'install with: pip install -e "<path>[postgres]"'
        )

    engine = state['engines'].get(machine)
    if engine is None:
        raise RuntimeError(
            f'hpc-usage-queries: no engine for machine={machine!r}. '
            f'Available: {sorted(state["engines"].keys())}'
        )

    get_session = state['module'].get_session
    session = get_session(machine, engine=engine)
    try:
        yield session
    finally:
        session.close()


def _safe_url(engine) -> str:
    """Best-effort password-stripped URL for log lines.

    Mirrors webapp.utils.config_inspect.format_db_url_safe but inlined
    to avoid an import cycle with the config-inspect module (Part C
    will pull engines back the other way).
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
