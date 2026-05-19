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

import inspect
import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from flask import Flask, current_app

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
        # Plugin-feature probes, set once at startup. Routes read these to
        # decide whether to surface pagination/sort UI or fall back to the
        # limit-only path. Mirrors the pool_kwargs= drift handling below.
        'supports_offset':  False,
        'supports_sort':    False,
        'supports_count':   False,
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

    get_engine = getattr(mod, 'get_engine', None)
    if get_engine is None:
        logger.error(
            'hpc-usage-queries loaded but exposes no get_engine(); refusing to '
            'use it. Upgrade the plugin (PR #60 or later).'
        )
        return

    # Detect plugin signature drift once at startup, not per-machine.
    # PR #60 added pool_kwargs= to get_engine(); older plugin builds
    # raise TypeError on the keyword. We fall back to the old signature
    # so the integration still works (just without webapp-side pool
    # tuning) and surface one clear warning instead of N silent ones.
    supports_pool_kwargs = _accepts_kwarg(get_engine, 'pool_kwargs')
    if not supports_pool_kwargs:
        logger.warning(
            'hpc-usage-queries plugin is out of date: get_engine() does not '
            'accept pool_kwargs=. Engines will open without webapp-side pool '
            'tuning. Upgrade the plugin to PR #60 or later to enable '
            'JOB_HISTORY_POOL_* config.'
        )

    # Eagerly create one Engine per machine. A failure here is logged
    # per-machine; other machines can still come up.
    for machine in machines:
        try:
            if supports_pool_kwargs:
                engine = get_engine(machine, pool_kwargs=pool_kwargs)
            else:
                engine = get_engine(machine)
            state['engines'][machine] = engine
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

    # Probe paginated-search/count support on the plugin. These reads are
    # cheap and let the route degrade gracefully — older plugin builds
    # render the limit-only table without a 500.
    jq = getattr(mod, 'JobQueries', None)
    if jq is not None:
        js = getattr(jq, 'jobs_search', None)
        if js is not None:
            state['supports_offset'] = _accepts_kwarg(js, 'offset')
            state['supports_sort']   = _accepts_kwarg(js, 'sort_by')
        state['supports_count'] = callable(getattr(jq, 'jobs_count', None))
    if state['enabled'] and not (state['supports_offset'] and state['supports_count']):
        logger.warning(
            'hpc-usage-queries plugin is out of date: missing offset=/jobs_count '
            'on JobQueries — per-job UI will render without pagination. '
            'Upgrade the plugin to pick up the offset/sort/count PR.'
        )


def _accepts_kwarg(fn, name: str) -> bool:
    """True if *fn* accepts a keyword argument named *name*.

    Conservative: if introspection itself fails (C-implemented callables,
    decorators that hide the signature, …) returns True so we attempt the
    call and let any real failure surface through the per-machine try.
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return True
    if name in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


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


def get_capabilities(app: Optional[Flask] = None) -> Dict[str, bool]:
    """Return ``{offset, sort, count}`` plugin-capability flags.

    Set once during :func:`init_job_history` by introspecting the loaded
    plugin. Routes consult these to decide whether to render pagination /
    sort controls or degrade to the limit-only table.
    """
    state = (app or current_app).extensions.get(_EXT_KEY) or {}
    return {
        'offset': bool(state.get('supports_offset')),
        'sort':   bool(state.get('supports_sort')),
        'count':  bool(state.get('supports_count')),
    }


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
