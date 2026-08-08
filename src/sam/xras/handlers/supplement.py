"""Supplement — 15% of traffic, 100% successful, and Extension's mirror image.

Where Extension ignores ``resources[]`` and walks accounts, this walks
``resources[]`` and ignores everything else. Per requested resource:

.. code-block:: java

    if (allocation == null)                       return buildAddAllocationCommand(resource);
    else if (getTransactionAmount(resource) > 0)  return buildSupplementAllocationCommand(...);
    return null;                                   // <= 0 silently dropped

⚠️ **``awardedAmount`` is the INCREMENT, not the new total.** ``SUPPLEMENT`` replays
as ``addAmount(transaction_amount)``. This is the single most consequential porting
semantic in the sprint: reading it as an absolute would overwrite a multi-million-hour
allocation with a quarter-million-hour supplement, silently, and the resulting number
would look entirely plausible.

The lookup is ``Project.getAccount(name)`` — a plain scan over **all** accounts,
active or not, matching on resource name case-insensitively. Note the asymmetry with
Extension, which filters accounts hard. A supplement therefore lands on an account
whose resource is decommissioned, where an extension would skip it.

Like Extension, this assembler composes only its own factory: no title, PI or roster
validation. Unlike Extension, it can also *create* an allocation, and the create branch
uses **today** plus a derived end date rather than the action's own dates.

Verified against ``~/codes/sam`` at tag 2.0.3
(``SupplementProjectAllocationActionCommandsFactory``, ``Allocation.supplement``).
See ``docs/plans/XRAS_SPRINT_C.md`` § *Supplement*.
"""

import logging
from typing import List, Tuple

from sam.manage.allocations import create_allocation, supplement_allocation
from sam.manage.transaction import management_transaction
from sam.projects.projects import Project

from ..dispatch import DispatchResult, register
from ..errors import ActionErrors
from ..wire import get_field
from ._allocations import (           # noqa: F401  — re-exported, see the shim note
    account_for_resource,
    auth_at_panel_meeting,
    create_window_from_project_history,
    latest_allocation,
    mark_panel_authorised,
    new_allocation_end_date,
)
from ._fields import (               # noqa: F401  — re-exported
    resolve_resource,
    resource_comment,
    transaction_amount,
)

logger = logging.getLogger(__name__)

__all__ = ['handle_supplement']


def _plan(session, action, errs: ActionErrors) -> Tuple[List[tuple], List[tuple]]:
    """Assemble, reporting everything. Returns ``(supplements, creations)``.

    Pure: examines the whole ``resources[]`` array and writes nothing, so one bad
    resource still lets the rest report their own problems before the single
    ``raise_if_any()``.
    """
    projcode = (get_field(action, 'requestNumber') or '').strip()
    project = Project.get_by_projcode(session, projcode)
    if project is None:                              # pragma: no cover - dispatcher checked
        return [], []

    auth = auth_at_panel_meeting(session, action)
    supplements: List[tuple] = []
    creations: List[tuple] = []

    for wire_resource in get_field(action, 'resources') or ():
        resource = resolve_resource(session, wire_resource, errs)
        if resource is None:
            continue

        account = account_for_resource(project, resource)
        allocation = latest_allocation(account) if account is not None else None
        amount = transaction_amount(wire_resource, errs)

        if allocation is None:
            # Create branch. Note the amount is still required — legacy passes it
            # straight into the add command, where a null would fail validation.
            window = create_window_from_project_history(project, projcode, errs)
            if window is None:
                continue
            if amount is None:
                continue
            start, end = window
            creations.append((resource, amount, resource_comment(wire_resource),
                              start, end, auth))
            continue

        if amount is None:
            continue
        if amount <= 0:
            # Legacy drops these silently — `return null` with no report. Logged
            # rather than reported, so the action still succeeds as it does today,
            # but the drop is visible to whoever is triaging.
            logger.warning(
                'XRAS supplement for %s on %s has a non-positive amount (%s); '
                'legacy drops it silently and so do we',
                projcode, resource.resource_name, amount)
            continue

        supplements.append((allocation, amount, resource_comment(wire_resource),
                            auth))

    return supplements, creations


def handle_supplement(session, action) -> DispatchResult:
    """Add to each requested resource's allocation, creating it where there is none.

    Raises:
        XrasActionRejected: an unmapped resource key, a missing or unparseable amount,
            or a create branch with no usable end date. Nothing is written.
    """
    projcode = (get_field(action, 'requestNumber') or '').strip()
    errs = ActionErrors()
    supplements, creations = _plan(session, action, errs)

    errs.raise_if_any()

    project = Project.get_by_projcode(session, projcode)
    with management_transaction(session):
        for allocation, amount, comment, auth in supplements:
            supplement_allocation(session, allocation, amount=amount,
                                  comment=comment, auth_at_panel_mtg=auth)
        for resource, amount, comment, start, end, auth in creations:
            created = create_allocation(
                session,
                project_id=project.project_id,
                resource_id=resource.resource_id,
                amount=amount,
                start_date=start,
                end_date=end,
                user_id=None,
                comment=comment,
            )
            if auth:
                mark_panel_authorised(session, created)

    return DispatchResult(status='processed', service='supplement', projcode=projcode)


register('supplement', handle_supplement)
