"""Every registered task, imported for its side effects.

This module is the answer to "what tasks exist?". The ``@task`` decorator puts
the schedule next to the function it schedules — which is right — but that
leaves nothing central to read, so importing each module here restores it. The
same pattern as ``system_status/models/__init__.py``.

Import this package (not the modules individually) before reading
``scheduling.registry.TASKS``; ``cli.tasks`` and any future daemon both do.
"""

from scheduling.tasks import cleanup_status       # noqa: F401
from scheduling.tasks import deactivate_expired   # noqa: F401
from scheduling.tasks import expiration_notices   # noqa: F401
from scheduling.tasks import xras_notices         # noqa: F401

__all__ = ['cleanup_status', 'deactivate_expired', 'expiration_notices',
           'xras_notices']
