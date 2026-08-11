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
account lookup, same create branch. The differences are the transaction type and the
sign.

⚠️ **``auth_at_panel_mtg`` splits by command, not by handler.** The ADJUSTMENT row does
not carry it — ``buildAdjustAllocationCommand`` never calls ``.authAtPanelMeeting(...)``
— but the CREATE row this handler can also write does, because
``buildAddAllocationCommand`` is the copy taken verbatim from the supplement factory and
that one does. Getting this half-right is what the original port did: the flag was
computed, carried through the creations tuple, unpacked, and then never applied.

Verified against ``~/codes/sam`` at tag 2.0.3
(``AdjustProjectAllocationActionCommandsFactory``, ``Allocation.adjust``).
See ``docs/plans/XRAS_SPRINT_C.md`` § *Adjustment*.
"""

import logging
from typing import List


from .. import errors as e
from ..dispatch import DispatchResult, register
from ._allocations import (
    account_for_resource,
    auth_at_panel_meeting,
    create_window_from_project_history,
    latest_allocation,
)
from ._fields import resolve_resource, resource_comment, transaction_amount
from ._plans import PlannedAdjust, PlannedCreate
from .base import ActionHandler

logger = logging.getLogger(__name__)

__all__ = ['AdjustmentHandler', 'handle_adjustment']


class AdjustmentHandler(ActionHandler):
    """Apply a signed correction to each requested resource's allocation."""

    service = 'adjust'

    def assemble(self) -> None:
        """Supplement's assembly with the sign gate replaced and two guards added:
        the create branch's non-positive refusal and the below-zero one.

        ⚠️ This used to be a separate ``_plan`` arguing for its own existence — *"the
        two differ in three places and a shared function with three flags reads worse
        than two functions that each say what they do"*. The count was wrong (four,
        not three) and so was the conclusion: the duplicated thirty lines are where
        the panel-authorisation flag went missing for an entire sprint. What actually
        needed naming was the shared **create policy**, not the whole planner.
        """
        self.planned: List[object] = []
        if self.project is None:                     # pragma: no cover - dispatcher checked
            return

        self.panel_authorised = auth_at_panel_meeting(self.session, self.action)

        for wire_resource in self.get('resources') or ():
            resource = resolve_resource(self.session, wire_resource, self.errors)
            if resource is None:
                continue

            account = account_for_resource(self.project, resource)
            allocation = latest_allocation(account) if account is not None else None
            amount = transaction_amount(wire_resource, self.errors)

            if allocation is None:
                # Create branch, identical to Supplement's — same named policy, so the
                # two cannot drift the way they did when each carried its own copy.
                window = create_window_from_project_history(
                    self.project, self.projcode, self.errors)
                if window is None:
                    continue
                if amount is None:
                    continue
                if amount <= 0:
                    # There is nothing to create. Legacy would build the add command
                    # with a non-positive amount and fail downstream on
                    # `Allocation.create`'s `amount > 0` validation; reporting is the
                    # legible version of that.
                    #
                    # ⚠️ This guard is Adjustment's alone. Supplement has no equivalent
                    # and must not gain one here — that would turn a Supplement crash
                    # into a 422, which is a behaviour change nobody asked for.
                    self.errors.report(e.adjustment_would_go_negative(
                        resource.resource_name, 0.0, amount))
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
            if amount == 0:
                logger.warning(
                    'XRAS adjustment for %s on %s is zero; nothing to apply',
                    self.projcode, resource.resource_name)
                continue

            current = float(allocation.amount or 0.0)
            if current + amount < 0:
                self.errors.report(e.adjustment_would_go_negative(
                    resource.resource_name, current, amount))
                continue

            # PlannedAdjust carries no panel flag — see its docstring; that absence
            # is what keeps auth_at_panel_mtg NULL rather than 0.
            self.planned.append(PlannedAdjust(
                allocation=allocation, amount=amount,
                comment=resource_comment(wire_resource)))

    def execute(self) -> None:
        # ⚠️ ADJUSTMENT rows get no `auth_at_panel_mtg` while the CREATE rows do.
        # That split is `buildAdjustAllocationCommand`'s, not a slip, and it now
        # lives in the plan types rather than in this loop.
        self.execute_plan(self.planned)


def handle_adjustment(session, action, *, validate_only: bool = False) -> DispatchResult:
    """The registry's contract:
    ``(session, action, *, validate_only=False) -> DispatchResult``."""
    return AdjustmentHandler(session, action).run(validate_only=validate_only)


register('adjust', handle_adjustment)
