"""hpc-usage-queries plugin loader and session factory for Flask.

The plugin (``job_history``) maintains its own SQLAlchemy engine per
machine — a *different* database from the SAM MySQL one Flask-SQLAlchemy
binds at startup. We can't fold it under ``SQLALCHEMY_BINDS`` because
``job_history`` models are not part of SAM's ``db.Model`` registry.

The load / warm / tag / stash / accessor machinery is
:class:`webapp.plugins.PluginExtension`, shared with the fs-scans loader.
What's specific here is one engine per configured machine, plus the
per-request :func:`job_history_session` context manager — unlike fs-scans,
whose facade owns its own sessions, ``job_history`` hands out a Session on
an engine we hold.

The plugin is optional. If it can't be imported (developer skipped the
``[hpc]`` install extra), we log a warning, mark the feature disabled,
and let the rest of the webapp boot — same posture as ``sam-admin``.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from flask import Flask, current_app

from sam.plugins import HPC_USAGE_QUERIES
from webapp.plugins import PluginExtension

# app.extensions key under which we stash plugin state.
_EXT_KEY = 'hpc_usage_queries'


class JobHistoryExtension(PluginExtension):
    """Warms one ``job_history`` engine per configured machine."""

    ext_key = _EXT_KEY
    plugin = HPC_USAGE_QUERIES
    log_label = 'job_history'

    def _init_state(self, state: Dict[str, Any]) -> None:
        state['engines'] = {}   # machine -> Engine

    def _should_load(self, app: Flask) -> bool:
        """Skip entirely when no machines are configured (TestingConfig does)."""
        if not app.config.get('JOB_HISTORY_MACHINES', ['derecho', 'casper']):
            self.logger.info(
                'hpc-usage-queries: no machines configured, plugin not loaded')
            return False
        return True

    def _warm(self, app: Flask, mod, state: Dict[str, Any]) -> None:
        """Open one Engine per machine.

        Reads:

        - ``JOB_HISTORY_MACHINES`` (list[str]): machines to pre-warm engines
          for. Default ``['derecho', 'casper']``.
        - ``JOB_HISTORY_POOL_KWARGS`` (dict): forwarded to
          ``job_history.get_engine(pool_kwargs=…)``. Only honored on the
          PostgreSQL backend; SQLite ignores most pool args.
        - ``JOB_HISTORY_STATEMENT_TIMEOUT_MS`` (int): 0 disables.

        A failure is logged per-machine; other machines still come up.
        """
        machines = app.config.get('JOB_HISTORY_MACHINES', ['derecho', 'casper'])
        pool_kwargs = app.config.get('JOB_HISTORY_POOL_KWARGS', {}) or {}
        statement_timeout_ms = int(
            app.config.get('JOB_HISTORY_STATEMENT_TIMEOUT_MS', 0) or 0
        )

        for machine in machines:
            try:
                engine = mod.get_engine(machine, pool_kwargs=pool_kwargs)
                state['engines'][machine] = engine
                # We can't inject application_name via `pool_kwargs` because
                # the plugin's ``get_engine`` sets ``connect_args`` itself when
                # calling ``create_engine``, so passing our own through
                # ``pool_kwargs`` would collide — hence the post-creation
                # ``connect`` listener.
                if engine.url.drivername.startswith('postgresql'):
                    self.apply_connection_settings(
                        engine,
                        self.connection_tag(machine),
                        statement_timeout_ms=statement_timeout_ms,
                    )
                self.logger.info(
                    'hpc-usage-queries engine ready: machine=%s url=%s',
                    machine, self.safe_url(engine),
                )
            except Exception as exc:
                self.logger.warning(
                    'hpc-usage-queries engine init failed for machine=%s: %s',
                    machine, exc,
                )

        state['enabled'] = bool(state['engines'])


#: Module-level singleton. The functions below are its bound methods under the
#: names every caller already imports.
extension = JobHistoryExtension()

init_job_history = extension.init_app
is_enabled = extension.is_enabled
get_module = extension.get_module
get_engines = extension.get_engines


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
