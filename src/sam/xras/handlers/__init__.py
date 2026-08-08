"""The six XRAS action handlers.

Importing this package registers every handler that exists with
:mod:`sam.xras.dispatch`. That import is the *only* wiring — the route calls
``dispatch_action`` and never names a handler, so a handler lands by adding a
module here and one line below.

Registration happens at import time, which makes import order part of the
contract: ``sam.xras.dispatch`` must be importable without this package (it is —
the dependency runs one way), and this package must be imported before the first
dispatch. ``webapp/api/xras/actions.py`` does that by importing it alongside the
dispatcher.

Each handler takes ``(session, action)`` and returns a
:class:`~sam.xras.dispatch.DispatchResult`, or raises
:class:`~sam.xras.errors.XrasActionRejected` with the accumulated 422 list.
None of them opens a transaction until validation has passed — see
``docs/plans/XRAS_SPRINT_C.md`` § *Assemble → check once → execute*.
"""

from . import extension  # noqa: F401  — imported for its registration side effect

__all__ = ['extension']
