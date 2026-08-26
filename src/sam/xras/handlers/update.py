"""Update -- ``New`` or ``Renewal`` against a project that already exists.

Not an ``actionType``; "Update" is a handler, selected by the pair
``(actionType in {New, Renewal}, the project exists)``. Order:
UpdateProject -> AddContract -> UpdateAllocation x N -> AddUser x N. No
mnemonic, and no inactivation step -- the source of bug 1 below.

Per resource, and one resource can emit three commands:

* no allocation, or none overlapping the action window -> **ADD**
* overlaps, existing EOD end AFTER action end -> **ERROR**
* overlaps, existing end BEFORE action end -> **EXTEND**, then stop if
  ``comments == "AUTO_DEFAULT_ALLOCATION_TRANSACTION"`` (a contingent resource
  gets its date moved but not its amount), else **SUPPLEMENT** (> 0) or
  **ADJUST** (< 0)

WARNING: the error string here is NOT Extension's. This one interpolates a
*resource name* and omits the word "is"; Extension's interpolates a *date* and
includes it. Which one an operator sees is how they tell which path rejected
them, so the two builders stay separate. Update-driven extends also carry the
**resource comment**, not ``XrasAction Extension Request``.

Three legacy bugs:

1. **Silently re-activates an inactive project** -- ``getActive()`` is
   hardcoded true and nothing runs ``InactivateNewProject`` afterwards. An XRAS
   project is inactive because a human has not approved it. NOT ported: leave
   ``active`` alone and warn.
2. **Never updates the lead or admin** -- the guard compares the fetched user's
   username against the lookup key, always equal, and ``setLeadUser`` is missing
   braces. FIXED.
3. **The ``UNDO AUTO/DEFAULT`` compensating adjustment** -- writers use
   ``.name()``, the detector compares ``.getValue()``; production holds zero
   ``UNDO`` rows of either spelling. NOT ported: detected and warned. The
   separate contingent-resource short-circuit compares ``.name()`` on both
   sides and does work, so that one IS ported.

Verified against ``~/codes/sam`` at tag 2.0.3. See
``docs/xras/incoming/implemented/XRAS_SPRINT_C.md``, *Update* and *Legacy defect 5*.
"""

import logging
from datetime import datetime
from typing import List, Optional

from sam.accounting.allocations import Allocation
from sam.core.users import User
from sam.manage import add_user_to_project
from sam.projects.contracts import ProjectContract

from .. import errors as e
from ..dispatch import DispatchResult, register
from ..extractors import resolve_allocation_type, resolve_area_of_interest
from ..roster import resolve_roster
from ..wire import get_field
from ._allocations import (
    account_for_resource,
    auth_at_panel_meeting,
    create_window_from_action_dates,
    effective_end_date,
    latest_allocation,
)
from ._fields import (
    abstract,
    parse_action_begin_date,
    parse_action_end_date,
    plan_contracts,
    resolve_resource,
    resource_comment,
    title,
    transaction_amount,
)
from ._plans import (
    PlannedAdjust,
    PlannedCreate,
    PlannedExtend,
    PlannedSupplement,
)
from .base import ActionHandler

logger = logging.getLogger(__name__)

__all__ = ['UpdateHandler', 'handle_update', 'is_allocation_overlapping',
           'CONTINGENT_RESOURCE_COMMENT']

#: ``ActionTag.AUTO_DEFAULT_ALLOCATION_TRANSACTION.name()`` — the ``.name()`` spelling,
#: which is what makes the contingent-resource short-circuit work where the undo does
#: not. Both sides of *this* comparison use ``.name()``; the undo's do not.
CONTINGENT_RESOURCE_COMMENT = 'AUTO_DEFAULT_ALLOCATION_TRANSACTION'

#: ``AllocationTransaction.AUTO_DEFAULT_ALLOCATION_COMMENT`` — the ``.getValue()``
#: spelling, written by an older code path. Detected for the warning only.
_AUTO_DEFAULT_VALUE = 'AUTO/DEFAULT'


def is_allocation_overlapping(allocation: Allocation, start: Optional[datetime],
                              end: Optional[datetime]) -> bool:
    """``isAllocationOverlapping`` — standard interval overlap, both dates required.

    WARNING: It returns ``False`` when **either** action date is null, which routes the
    resource to the ADD branch — and legacy then dereferences the same null date on the
    commission clamp and throws. Unreachable here because the handler reports a missing
    or unparseable date during assembly and never reaches this, but the guard stays
    explicit rather than implied.
    """
    if start is None or end is None:
        return False
    if allocation.start_date is not None and allocation.start_date > end:
        return False
    alloc_end = effective_end_date(allocation)
    if alloc_end is not None and alloc_end < start:
        return False
    return True


def _is_auto_default_allocation(allocation: Allocation) -> bool:
    """``isAutoDefaultAllocation`` — the detector behind the dead undo.

    The most recent transaction carrying an amount must have the comment
    ``AUTO/DEFAULT`` **and** an amount equal to the allocation's. Reproduced only so
    the warning can fire; nothing acts on it. See legacy defect 5.
    """
    rows = [t for t in allocation.transactions if t.transaction_amount is not None]
    if not rows:
        return False
    latest = max(rows, key=lambda t: (t.creation_time,
                                      t.allocation_transaction_id or 0))
    return (latest.transaction_comment == _AUTO_DEFAULT_VALUE
            and latest.transaction_amount == allocation.amount)


class UpdateHandler(ActionHandler):
    """Update an existing project, its contracts, allocations and membership."""

    service = 'update'

    def assemble(self) -> None:
        self.title = title(self.action, self.errors)
        self.roster = resolve_roster(self.session, self.action, self.errors)
        fos_warnings: list = []
        self.aoi = resolve_area_of_interest(self.session, self.action,
                                            self.errors, warnings=fos_warnings)
        self.allocation_type = resolve_allocation_type(
            self.session, self.action, self.errors)
        self.start = parse_action_begin_date(self.action, self.errors)
        self.end = parse_action_end_date(self.action, self.errors)

        # WARNING: Here, and not one line later. `execute()` writes `allocation_type_id`
        # through `project.update()`, which flushes — so a lazily-evaluated version of
        # this would read back the type this very action installed rather than the one
        # the project had when it arrived. Nothing in the suite would catch that.
        self.panel_authorised = auth_at_panel_meeting(self.session, self.action)

        self.contracts, contract_warnings, self.unresolved_grants = plan_contracts(
            self.session, self.action, self.errors)

        self.planned: List[tuple] = []
        if self.project is not None:
            for wire_resource in self.get('resources') or ():
                self.planned.extend(self._plan_resource(wire_resource))

        # Taken from the roster, not re-looked-up — see the same block in `new.py`.
        self.lead = self.roster.pi
        self.admin = self.roster.admin
        self.members = list(self.roster.members)
        self.warnings = (self.roster.warnings + contract_warnings
                         + tuple(fos_warnings))

    def _plan_resource(self, wire_resource):
        """The per-resource decision. Returns a list of ``(kind, …)`` tuples, in order.

        A single resource can emit **three** commands — see the module docstring's
        table. Panel authorization is decided per step, not per action: only the ADD
        and SUPPLEMENT arms carry it.
        """
        start, end = self.start, self.end
        resource = resolve_resource(self.session, wire_resource, self.errors)
        if resource is None:
            return []

        account = account_for_resource(self.project, resource)
        allocation = latest_allocation(account) if account is not None else None
        comment = resource_comment(wire_resource)
        planned = []

        # --- ADD: no allocation at all, or none overlapping the action window.
        if allocation is None or not is_allocation_overlapping(allocation, start, end):
            amount = transaction_amount(wire_resource, self.errors)
            if amount is None or start is None or end is None:
                return []
            window = create_window_from_action_dates(
                resource, start, end, self.errors)
            if window is None:
                return []
            clamped, alloc_end = window
            planned.append(PlannedCreate(
                resource=resource, amount=amount, comment=comment,
                start=clamped, end=alloc_end,
                panel_authorised=self.panel_authorised))
            return planned

        existing_end = effective_end_date(allocation)

        # --- ERROR: the action would shrink it. Note the Update-specific string.
        if existing_end is not None and existing_end > end:
            self.errors.report(e.update_end_date_before_existing(
                resource.resource_name))
            return []

        # --- EXTEND, carrying the RESOURCE comment rather than the Extension one.
        if existing_end is not None and existing_end < end:
            planned.append(PlannedExtend(
                allocation=allocation, new_end=end, comment=comment))

        # --- Contingent resource: the date moves, the amount does not. This
        # short-circuit compares `.name()` on both sides and genuinely works, unlike
        # the undo below.
        if self.get_resource_comment_raw(wire_resource) == CONTINGENT_RESOURCE_COMMENT:
            return planned

        # --- The undo that has never fired. Detected, warned, NOT performed.
        if _is_auto_default_allocation(allocation):
            logger.warning(
                'XRAS update touched an AUTO/DEFAULT allocation (%s on %s). Legacy '
                'would attempt a compensating UNDO adjustment here; that mechanism is '
                'broken and has never executed (zero UNDO rows in production), so none '
                'is written. See XRAS_SPRINT_C.md legacy defect 5.',
                allocation.allocation_id, resource.resource_name)

        amount = transaction_amount(wire_resource, self.errors)
        if amount is None:
            return planned
        if amount > 0:
            planned.append(PlannedSupplement(
                allocation=allocation, amount=amount, comment=comment,
                panel_authorised=self.panel_authorised))
        elif amount < 0:
            # PlannedAdjust carries no panel flag by design — see its docstring.
            planned.append(PlannedAdjust(
                allocation=allocation, amount=amount, comment=comment))
        return planned

    @staticmethod
    def get_resource_comment_raw(wire_resource):
        """The **unnormalised** ``comments`` field.

        WARNING: Not :func:`resource_comment`. The contingent-resource sentinel is compared
        byte-for-byte against ``ActionTag.AUTO_DEFAULT_ALLOCATION_TRANSACTION.name()``,
        so it must not pass through ``StringUtil.normalize`` first.
        """
        return get_field(wire_resource, 'comments')

    def execute(self) -> None:
        project = self.project

        # 1. The project itself. `active` is deliberately absent — see bug 1.
        if not project.is_active:
            logger.warning(
                'XRAS update targets inactive project %s; legacy would silently '
                're-activate it. Leaving it inactive — a human has not approved it.',
                self.projcode)
        project.update(
            title=self.title,
            abstract=abstract(self.action),
            area_of_interest_id=self.aoi.area_of_interest_id,
            allocation_type_id=self.allocation_type.allocation_type_id,
            # Bug 2: legacy's guard never fires, so these never move. They do here.
            project_lead_user_id=self.lead.user_id if self.lead else None,
            project_admin_user_id=self.admin.user_id if self.admin else None,
        )

        # 2. Contracts — additive; an existing link is left alone.
        existing = {pc.contract_id for pc in project.contracts}
        for contract in self.contracts:
            if contract.contract_id not in existing:
                existing.add(contract.contract_id)
                ProjectContract.create(self.session, project_id=project.project_id,
                                       contract_id=contract.contract_id)

        # 3. Allocations, in the order the factory emitted them. One resource can
        # emit three steps and that order is legacy's, so this iterates a flat list
        # rather than grouping by kind.
        self.execute_plan(self.planned, project=project)

        # 4. Membership. As on the Add path, skipped when the project has no accounts.
        if project.accounts:
            for member in self.members:
                if member is not None:
                    add_user_to_project(self.session, project.project_id,
                                        member.user_id)


def handle_update(session, action, *, validate_only: bool = False) -> DispatchResult:
    """The registry's contract:
    ``(session, action, *, validate_only=False) -> DispatchResult``."""
    return UpdateHandler(session, action).run(validate_only=validate_only)


register('update', handle_update)
