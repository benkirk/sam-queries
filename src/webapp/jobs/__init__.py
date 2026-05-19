"""Flask integration for the hpc-usage-queries plugin.

Surfaces per-job rows from the ``job_history`` package (a separate
PostgreSQL/SQLite database, never SAM's MySQL) inside the webapp without
recreating its connection pool on every request.

Public entry point is :func:`init_job_history`, called from ``create_app``.
It loads the plugin once, eagerly opens an Engine per machine with the
pool kwargs supplied by Flask config, and stashes everything on
``app.extensions['hpc_usage_queries']``.

If the plugin is unavailable or no machines are configured the webapp
still boots — downstream features just degrade. Job-level UI rendering
checks :func:`is_enabled` before issuing any query.
"""

from webapp.jobs.routes import bp
from webapp.jobs.session import (
    init_job_history,
    is_enabled,
    job_history_session,
    get_engines,
    get_module,
    get_capabilities,
)

__all__ = [
    'bp',
    'init_job_history',
    'is_enabled',
    'job_history_session',
    'get_engines',
    'get_module',
    'get_capabilities',
]
