"""Webapp-side wrappers around SAM's optional query plugins.

Each plugin (``job_history``, ``fs_scans``) owns SQLAlchemy engines against a
database that is *not* the SAM MySQL one Flask-SQLAlchemy binds at startup —
their models aren't in SAM's ``db.Model`` registry, so they can't be folded
under ``SQLALCHEMY_BINDS``. :class:`PluginExtension` is the shared shape for
loading one at app startup, pre-warming its engines, and reaching them again
from a request.
"""

from webapp.plugins.base import PluginExtension

__all__ = ['PluginExtension']
