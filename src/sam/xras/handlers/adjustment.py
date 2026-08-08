"""Adjustment — the handler to review hardest, because legacy has never run one.

Two independent legacy defects have kept ``AdjustProjectActionService`` dark for its
entire existence:

1. **Defect 4, the spelling.** ``isServiceable`` tests
   ``actionType.equals("Adjust")``; XRAS sends ``"Adjustment"``. They never match, so
   every Adjustment falls through ``ProjectActionServiceSelector`` to the manual-email
   fallback. The corpus confirms the wire spelling.
2. **The copy-pasted ``> 0`` gate.** ``AdjustProjectAllocationActionCommandsFactory`` is
   a near-verbatim copy of the supplement factory, *including* the positive-amount
   guard — which silently drops the one thing an adjustment exists to do.

So this is the only handler in the sprint that will begin servicing traffic a human has
always handled, with **no production outcome to diff against**. Everything below is
reasoned from the source rather than confirmed against behaviour, and that is worth
knowing when reading it.

Three consequences:

* **Negatives are honoured.** Removing the ``> 0`` gate is the point of the handler.
  Nothing depends on it, because nothing has ever run.
* **A negative that would take the allocation below zero is rejected.** Legacy has no
  such guard — ``verifyValidateState`` checks only the end date — but legacy also never
  applies one. A below-zero ``amount`` makes every ``remaining = allocated − used``
  nonsense, and the guard can only reject, never corrupt. A rejected Adjustment goes to
  a human, which is where 100% of them go today.
* **Both spellings dispatch here**, via the existing ``canonical_action_type``.

Otherwise the shape is Supplement's, and the per-resource pieces are imported from it
rather than copied: same resource-key resolution, same amount parsing, same unfiltered
account lookup, same create branch. The differences are the transaction type, the
absence of ``auth_at_panel_mtg``, and the sign.

Verified against ``~/codes/sam`` at tag 2.0.3
(``AdjustProjectAllocationActionCommandsFactory``, ``Allocation.adjust``).
See ``docs/plans/XRAS_SPRINT_C.md`` § *Adjustment*.
"""

import logging
from datetime import datetime
from typing import List, Optional, Tuple

from sam.base import normalize_end_date
from sam.manage.allocations import adjust_allocation, create_allocation
from sam.manage.transaction import management_transaction
from sam.projects.projects import Project

from .. import errors as e
from ..dispatch import DispatchResult, register
from ..errors import ActionErrors
from .extension import _get, latest_allocation
from .supplement import (
    account_for_resource,
    auth_at_panel_meeting,
    new_allocation_end_date,
    resolve_resource,
    resource_comment,
    transaction_amount,
)

logger = logging.getLogger(__name__)

__all__ = ['handle_adjustment']


def _plan(session, action, errs: ActionErrors) -> Tuple[List[tuple], List[tuple]]:
    """Assemble, reporting everything. Returns ``(adjustments, creations)``.

    Structurally Supplement's ``_plan`` with the sign gate removed and the below-zero
    guard added. Kept as its own function rather than parameterising the supplement one:
    the two differ in three places and a shared function with three flags reads worse
    than two functions that each say what they do — and this one carries risk the other
    does not.
    """
    projcode = (_get(action, 'requestNumber') or '').strip()
    project = Project.get_by_projcode(session, projcode)
    if project is None:                              # pragma: no cover - dispatcher checked
        return [], []

    auth = auth_at_panel_meeting(session, action)
    adjustments: List[tuple] = []
    creations: List[tuple] = []

    for wire_resource in _get(action, 'resources') or ():
        resource = resolve_resource(session, wire_resource, errs)
        if resource is None:
            continue

        account = account_for_resource(project, resource)
        allocation = latest_allocation(account) if account is not None else None
        amount = transaction_amount(wire_resource, errs)

        if allocation is None:
            # Create branch, identical to Supplement's — including deriving the window
            # from today rather than from the action's own dates.
            start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            end = new_allocation_end_date(project, start)
            if end is None:
                errs.report(e.all_end_dates_null_or_past(projcode))
                continue
            if amount is None:
                continue
            if amount <= 0:
                # There is nothing to create. Legacy would build the add command with a
                # non-positive amount and fail downstream on `Allocation.create`'s
                # `amount > 0` validation; reporting is the legible version of that.
                errs.report(e.adjustment_would_go_negative(
                    resource.resource_name, 0.0, amount))
                continue
            creations.append((resource, amount, resource_comment(wire_resource),
                              start, normalize_end_date(end), auth))
            continue

        if amount is None:
            continue
        if amount == 0:
            logger.warning(
                'XRAS adjustment for %s on %s is zero; nothing to apply',
                projcode, resource.resource_name)
            continue

        current = float(allocation.amount or 0.0)
        if current + amount < 0:
            errs.report(e.adjustment_would_go_negative(
                resource.resource_name, current, amount))
            continue

        adjustments.append((allocation, amount, resource_comment(wire_resource)))

    return adjustments, creations


def handle_adjustment(session, action) -> DispatchResult:
    """Apply a signed correction to each requested resource's allocation.

    Raises:
        XrasActionRejected: an unmapped resource key, a missing or unparseable amount,
            a create branch with no usable end date, or an adjustment that would take
            an allocation below zero. Nothing is written.
    """
    projcode = (_get(action, 'requestNumber') or '').strip()
    errs = ActionErrors()
    adjustments, creations = _plan(session, action, errs)

    errs.raise_if_any()

    project = Project.get_by_projcode(session, projcode)
    with management_transaction(session):
        for allocation, amount, comment in adjustments:
            adjust_allocation(session, allocation, amount=amount, comment=comment)
        for resource, amount, comment, start, end, auth in creations:
            create_allocation(
                session,
                project_id=project.project_id,
                resource_id=resource.resource_id,
                amount=amount,
                start_date=start,
                end_date=end,
                user_id=None,
                comment=comment,
            )

    return DispatchResult(status='processed', service='adjust', projcode=projcode)


register('adjust', handle_adjustment)
