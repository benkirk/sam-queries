"""Flask integration for the fs-scans plugin.

Surfaces filesystem-scan analytics (large directories, owner/group
rollups, access-time histograms) from the ``fs_scans`` package (a
separate CNPG/PostgreSQL database, never SAM's MySQL) inside the webapp,
restricted to a project's directories.

Public entry point is :func:`init_fs_scans`, called from ``create_app``.
It loads the plugin once, discovers the available collection schemas,
pre-warms one Engine per collection, and stashes everything on
``app.extensions['fs_scans']``.

If the plugin is unavailable, the backend is unconfigured, or no
collections are reachable, the webapp still boots — downstream features
just degrade. UI rendering checks :func:`is_enabled` first.

Unlike ``job_history`` the facade owns its own sessions, so there is no
per-request session context manager — the service layer constructs
``FsScanQueries`` directly over the plugin's memoized engine cache.
"""

from webapp.disk_scans.session import (
    get_collections,
    get_engines,
    get_module,
    init_fs_scans,
    is_enabled,
)

__all__ = [
    'init_fs_scans',
    'is_enabled',
    'get_module',
    'get_collections',
    'get_engines',
]
