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
from typing import List

from ..dispatch import DispatchResult, register
from ._allocations import (
    account_for_resource,
    auth_at_panel_meeting,
    create_window_from_project_history,
    latest_allocation,
)
from ._fields import resolve_resource, resource_comment, transaction_amount
from ._plans import PlannedCreate, PlannedSupplement
from .base import ActionHandler

logger = logging.getLogger(__name__)

__all__ = ['SupplementHandler', 'handle_supplement']


class SupplementHandler(ActionHandler):
    """Add to each requested resource's allocation, creating it where there is none."""

    service = 'supplement'

    def assemble(self) -> None:
        """Examine the whole ``resources[]`` array and write nothing, so one bad
        resource still lets the rest report their own problems before the single
        ``raise_if_any()``."""
        self.planned: List[object] = []
        if self.project is None:                     # pragma: no cover - dispatcher checked
            return

        # ⚠️ During assembly, deliberately — see ActionHandler's docstring.
        self.panel_authorised = auth_at_panel_meeting(self.session, self.action)

        for wire_resource in self.get('resources') or ():
            resource = resolve_resource(self.session, wire_resource, self.errors)
            if resource is None:
                continue

            account = account_for_resource(self.project, resource)
            allocation = latest_allocation(account) if account is not None else None
            amount = transaction_amount(wire_resource, self.errors)

            if allocation is None:
                # Create branch. Note the amount is still required — legacy passes it
                # straight into the add command, where a null would fail validation.
                window = create_window_from_project_history(
                    self.project, self.projcode, self.errors)
                if window is None:
                    continue
                if amount is None:
                    continue
                start, end = window
                self.planned.append(PlannedCreate(
                    resource=resource, amount=amount,
                    comment=resource_comment(wire_resource),
                    start=start, end=end,
                    panel_authorised=self.panel_authorised))
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
                    self.projcode, resource.resource_name, amount)
                continue

            self.planned.append(PlannedSupplement(
                allocation=allocation, amount=amount,
                comment=resource_comment(wire_resource),
                panel_authorised=self.panel_authorised))

    def execute(self) -> None:
        self.execute_plan(self.planned)


def handle_supplement(session, action, *, validate_only: bool = False) -> DispatchResult:
    """The registry's contract:
    ``(session, action, *, validate_only=False) -> DispatchResult``."""
    return SupplementHandler(session, action).run(validate_only=validate_only)


register('supplement', handle_supplement)
