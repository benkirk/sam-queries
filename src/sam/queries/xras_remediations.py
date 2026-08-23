"""Read side of the XRAS remediation audit trail.

⚠️ **Not exported from ``sam/queries/__init__.py``**, and that is not an
oversight. That module imports its submodules eagerly, so listing this one
would pull ``sam.integration.xras`` — and through it the whole ORM — into every
``from sam.queries import ...``. The near-identical sibling
``sam/queries/xras_activation.py`` *is* exported, safely, which is exactly why
this needs saying out loud: the difference is what each module drags behind it,
not what it is named. Import this module by path.

One flat listing, no derived state. Unlike ``xras_activation_event``, whose
current state is a timestamp comparison, a remediation row means what it says:
somebody did this, and here is how it ended.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sam.integration.xras import XrasRemediationEvent

#: Never unbounded — this backs a card, and an operator scanning a page of
#: recent activity is the only read anyone has asked for.
DEFAULT_LIMIT = 50


def list_remediation_events(session, *, operation: Optional[str] = None,
                            request_number: Optional[str] = None,
                            username: Optional[str] = None,
                            status: Optional[str] = None,
                            since=None,
                            limit: int = DEFAULT_LIMIT
                            ) -> List[XrasRemediationEvent]:
    """Recent remediation events, newest first.

    Every filter is optional and they compose. *username* matches **either**
    side of a merge: asked "what happened to this person", an operator means
    the placeholder and the identity it was folded into, and answering with
    only one of them would hide half the story.
    """
    query = session.query(XrasRemediationEvent)

    if operation:
        query = query.filter(XrasRemediationEvent.operation == operation)
    if status:
        query = query.filter(XrasRemediationEvent.status == status)
    if request_number:
        query = query.filter(
            XrasRemediationEvent.request_number == str(request_number).strip())
    if username:
        wanted = str(username).strip()
        query = query.filter(
            (XrasRemediationEvent.username == wanted)
            | (XrasRemediationEvent.target_username == wanted))
    if since is not None:
        query = query.filter(XrasRemediationEvent.creation_time >= since)

    return (query.order_by(XrasRemediationEvent.creation_time.desc(),
                           XrasRemediationEvent.xras_remediation_event_id.desc())
            .limit(max(1, int(limit)))
            .all())


def remediation_summary(events) -> Dict[str, Any]:
    """Counts for the card header.

    ``needs_attention`` is the number the operator actually acts on: rows that
    never reached a verdict. A row stuck at ``attempted`` means a write went out
    and SAM never learned how it ended; an ``unverified`` one means XRAS said
    200 and the re-read did not agree. Both need a human to go and look, and
    neither is visible in a plain success count.
    """
    events = list(events or ())
    by_operation: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for event in events:
        by_operation[event.operation] = by_operation.get(event.operation, 0) + 1
        by_status[event.status] = by_status.get(event.status, 0) + 1
    return {
        'total': len(events),
        'by_operation': by_operation,
        'by_status': by_status,
        'needs_attention': by_status.get('attempted', 0)
                           + by_status.get('unverified', 0),
    }
