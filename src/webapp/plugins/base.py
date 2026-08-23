"""PluginExtension — shared bootstrap for the optional query plugins.

``webapp/jobs/session.py`` and ``webapp/disk_scans/session.py`` grew the same
shape independently, each carrying a comment pointing at the other:

* load a :class:`sam.plugins.Plugin` once at ``create_app`` time, tolerating
  absence (the ``[hpc]`` install extra is optional — a missing plugin logs a
  warning and disables the feature, it never blocks boot);
* eagerly open engines so the first request doesn't pay TLS + auth;
* tag each fresh postgres connection with ``application_name`` (and, when
  configured, ``statement_timeout``);
* stash ``{module, …, enabled}`` on ``app.extensions`` and expose
  ``is_enabled`` / ``get_module`` / ``get_engines`` accessors over it.

Everything above is here. The one genuinely per-plugin part — *what* to warm
and how to shape the state dict — is :meth:`_warm`. job_history warms one
engine per machine; fs-scans discovers collection schemas per CNPG database
and warms them through a bounded thread pool.

Subclasses are module-level singletons; the ``session.py`` modules re-export
bound methods under their historical function names, which is what every
caller (``run.py``, ``utils/nav.py``, ``utils/config_inspect.py``, the
service layers) still imports.
"""

from __future__ import annotations

import logging
import os
import socket
from typing import Any, Dict, Optional

from flask import Flask, current_app
from sqlalchemy import event


class PluginExtension:
    """Base for a webapp extension wrapping one optional SAM plugin.

    Subclasses set the three class attributes and implement :meth:`_warm`.
    """

    #: ``app.extensions`` key holding this plugin's state dict.
    ext_key: str = ''
    #: The :class:`sam.plugins.Plugin` descriptor to load.
    plugin: Any = None
    #: Short name used in log lines and ``application_name`` tags.
    log_label: str = ''

    def __init__(self) -> None:
        self.logger = logging.getLogger(f'{__name__}.{self.log_label}')

    # Startup

    def init_app(self, app: Flask) -> None:
        """Load the plugin and warm its engines. Called once from ``create_app``.

        Always leaves a state dict on ``app.extensions`` — even on failure, so
        the accessors below never have to distinguish "not initialized" from
        "initialized but disabled". ``enabled`` is set by :meth:`_warm`.
        """
        state: Dict[str, Any] = {'module': None, 'enabled': False}
        self._init_state(state)
        app.extensions[self.ext_key] = state

        if not self._should_load(app):
            return

        try:
            mod = self.plugin.load()
        except Exception as exc:
            self.logger.warning(
                '%s plugin not available — its features are disabled: %s',
                self.log_label, exc,
            )
            return

        state['module'] = mod
        self._warm(app, mod, state)

    def _init_state(self, state: Dict[str, Any]) -> None:
        """Add subclass-specific keys to the fresh state dict.

        Called before any load attempt, so the shape is complete even when
        the plugin is missing and the accessors return empties rather than
        raising KeyError.
        """

    def _should_load(self, app: Flask) -> bool:
        """Whether to attempt the load at all (a config kill-switch).

        Default: always. Subclasses gate on their own config and log why
        they declined — TestingConfig uses this to skip plugin load entirely.
        """
        return True

    def _warm(self, app: Flask, mod, state: Dict[str, Any]) -> None:
        """Open engines and set ``state['enabled']``. The per-plugin part.

        A per-engine failure should be logged and skipped, not raised: one
        unreachable database must not take out the others (or the app).
        """
        raise NotImplementedError

    # Accessors over app.extensions

    def _state(self, app: Optional[Flask] = None) -> Dict[str, Any]:
        return (app or current_app).extensions.get(self.ext_key) or {}

    def is_enabled(self, app: Optional[Flask] = None) -> bool:
        """True iff the plugin loaded and at least one engine is ready."""
        return bool(self._state(app).get('enabled'))

    def get_module(self, app: Optional[Flask] = None):
        """Return the loaded plugin module, or ``None`` if disabled."""
        return self._state(app).get('module')

    def get_engines(self, app: Optional[Flask] = None) -> Dict[str, Any]:
        """Return this plugin's engines keyed by whatever it keys them by."""
        return self._state(app).get('engines') or {}

    # Connection tagging

    def connection_tag(self, *parts: str) -> str:
        """Build the ``application_name`` for a connection.

        ``sam-webapp:<pod>:<plugin>:<parts…>`` so postgres
        ``pg_stat_activity`` can attribute load without IP archaeology.
        libpq truncates at 63 chars; this runs ~54 on typical k8s pod names.
        """
        pod_id = os.environ.get('HOSTNAME') or socket.gethostname()
        return ':'.join(('sam-webapp', pod_id, self.log_label, *parts))

    @staticmethod
    def apply_connection_settings(engine, app_name: str, *,
                                  statement_timeout_ms: int = 0) -> None:
        """Apply per-connection postgres settings on every new DBAPI connection.

        Sets ``application_name`` (for ``pg_stat_activity`` attribution) and,
        when ``statement_timeout_ms`` > 0, a server-side ``statement_timeout``
        so a runaway query fails cleanly instead of holding a connection (and
        a gthread thread) until the gunicorn worker timeout.

        The ``connect`` event fires once per fresh postgres connection (not on
        pool checkout), so this is the cheap, correct hook when the engine's
        ``connect_args`` are owned by the plugin and can't be amended in place.

        Toggles autocommit around the ``SET``s because postgres documents that
        ``application_name`` changes made via ``SET`` "will not appear in
        pg_stat_activity until after a commit or rollback" — and psycopg2's
        default is ``autocommit=False``, so a bare ``SET`` inside the implicit
        transaction would never become visible.
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

    @staticmethod
    def safe_url(engine) -> str:
        """Best-effort password-stripped URL for log lines.

        Mirrors ``webapp.utils.config_inspect.format_db_url_safe`` but inlined
        to avoid an import cycle with the config-inspect module.
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
