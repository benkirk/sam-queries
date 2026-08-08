"""Transfer — deliberately **not** built. Routed to the manual-fallback path.

This module exists so that the decision is a line of code with an argument attached,
rather than an absent handler that looks like an oversight. Legacy *does* service
Transfer actions; we deliberately do not, and an operator triaging a ``manual`` row
deserves to find out why from the audit trail rather than by reading the Java.

Why not build it
----------------
**Zero production traffic.** No Transfer appears in the 175 posts measured
2026-07-07 → 2026-08-05, and none is in the sampled corpus — so there is no payload to
port against, no outcome to diff against, and no way to tell a correct implementation
from a plausible one before it runs against live allocations.

**The one primitive that looks like a fit is not one.** ``exchange_allocations``
(``sam/manage/allocations.py``) is 1→1, same-resource, and raises rather than clamping.
Legacy's transfer is **one negative source to N positive destinations summing to zero**,
with the source clamped to what is actually available
(``TransferProjectAllocationActionCommandsFactory``). Those are different operations
that happen to share a name.

**It moves allocation between projects.** Of the six action types this is the only one
that takes from one project to give to another, so a wrong implementation is wrong in
two places at once and the error is a real balance rather than a date.

The vocabulary is complete anyway
---------------------------------
All five Transfer error strings are already implemented and pinned in
:mod:`sam.xras.errors` — including
``Transfer requires one source resource (negative amount)``, the third arity string
missing from § 3.4. A future implementation starts from verified strings rather than
re-reading the Java.

What happens instead
--------------------
The dispatcher selects ``transfer``, this handler returns ``manual``, and the audit row
records *why*. That is the same outcome an unserviceable action gets from legacy — a
human applies it — except that legacy leaves no trace, and this leaves a row saying the
action was recognised, deliberately not applied, and by whose decision.

⚠️ **If Transfer traffic ever appears, this is the thing to notice.** The
``xras_action_log`` query for it is ``status='manual' AND action_type='Transfer'``.
See ``docs/plans/XRAS_SPRINT_C.md`` § *Write primitives* item 5.
"""

import logging

from ..dispatch import DispatchResult, register

logger = logging.getLogger(__name__)

__all__ = ['handle_transfer', 'NOT_IMPLEMENTED_REASON']

#: Recorded on the audit row and logged. Phrased for whoever reads it at 3am with no
#: context: what happened, that it was intended, and what to do.
NOT_IMPLEMENTED_REASON = (
    'Transfer is deliberately not serviced by this integration — it has zero '
    'production traffic and no sampled payload, so it is applied by a human. '
    'Legacy SAM does service it; see sam/xras/handlers/transfer.py.'
)


def handle_transfer(session, action) -> DispatchResult:
    """Record the action for a human and return ``manual``.

    Takes ``session`` and ``action`` it does not use, because it is a handler and the
    registry's contract is uniform — a special case in the dispatcher would be a worse
    trade than an unused argument here.
    """
    projcode = (action.get('requestNumber') if isinstance(action, dict)
                else getattr(action, 'requestNumber', None)) or ''
    logger.warning(
        'XRAS Transfer action for %s parked for a human: %s',
        projcode.strip() or '<no projcode>', NOT_IMPLEMENTED_REASON)
    return DispatchResult(status='manual', service='transfer',
                          projcode=projcode.strip() or None,
                          reason=NOT_IMPLEMENTED_REASON)


register('transfer', handle_transfer)
