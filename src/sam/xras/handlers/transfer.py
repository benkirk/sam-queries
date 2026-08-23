"""Transfer -- deliberately NOT built. Routed to the manual-fallback path.

This module exists so the decision is a line of code with an argument attached
rather than an absent handler that looks like an oversight. Legacy services
Transfer; we do not, and an operator triaging a ``manual`` row should find out
why from the audit trail.

Why not build it:

* **Zero production traffic.** No Transfer in the 175 posts measured
  2026-07-07 -> 2026-08-05, and none in the corpus -- so there is no payload to
  port against and no way to tell a correct implementation from a plausible one
  before it runs against live allocations.
* **The one primitive that looks like a fit is not.** ``exchange_allocations``
  is 1->1, same-resource, and raises rather than clamping. Legacy's transfer is
  one negative source to N positive destinations summing to zero, with the
  source clamped to what is available. Different operations, same name.
* **It moves allocation between projects** -- the only action type that does,
  so a wrong implementation is wrong in two places and the error is a real
  balance rather than a date.

All five Transfer error strings are already implemented and pinned in
:mod:`sam.xras.errors`, so a future implementation starts from verified strings
rather than re-reading the Java.

The dispatcher selects ``transfer``, this returns ``manual``, and the audit row
records why -- the same outcome legacy gives an unserviceable action, except
legacy leaves no trace.

WARNING: if Transfer traffic ever appears, this is the thing to notice. The
query is ``status='manual' AND action_type='Transfer'``. See
``docs/xras/incoming/implemented/XRAS_SPRINT_C.md``, *Write primitives* item 5.
"""

import logging

from ..dispatch import DispatchResult, register
from ..wire import get_field

logger = logging.getLogger(__name__)

__all__ = ['handle_transfer', 'NOT_IMPLEMENTED_REASON']

#: Recorded on the audit row and logged. Phrased for whoever reads it at 3am with no
#: context: what happened, that it was intended, and what to do.
NOT_IMPLEMENTED_REASON = (
    'Transfer is deliberately not serviced by this integration — it has zero '
    'production traffic and no sampled payload, so it is applied by a human. '
    'Legacy SAM does service it; see sam/xras/handlers/transfer.py.'
)


def handle_transfer(session, action, *, validate_only: bool = False) -> DispatchResult:
    """Record the action for a human and return ``manual``.

    Takes ``session``, ``action`` and ``validate_only`` it does not use, because it is
    a handler and the registry's contract is uniform — a special case in the dispatcher
    would be a worse trade than unused arguments here.

    ``validate_only`` changes nothing on purpose: a re-check of a Transfer should
    answer *"nothing would run"*, which is the same answer as a live post. Returning
    ``rechecked`` here would claim the action was validated when no validation exists.
    """
    projcode = (get_field(action, 'requestNumber') or '').strip()
    logger.warning(
        'XRAS Transfer action for %s parked for a human: %s',
        projcode or '<no projcode>', NOT_IMPLEMENTED_REASON)
    return DispatchResult(status='manual', service='transfer',
                          projcode=projcode or None,
                          reason=NOT_IMPLEMENTED_REASON)


register('transfer', handle_transfer)
