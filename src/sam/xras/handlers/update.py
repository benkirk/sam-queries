"""Update — ``New`` or ``Renewal`` against a project that already exists.

Not an ``actionType``. "Update" is a *handler*, selected by the pair
``(actionType ∈ {New, Renewal}, the project exists)`` — so New and Update are one
dispatch decision resolved against the database, and ``new_uwis0071_existing_ok.json``
is the production proof.

Assembler order: **UpdateProject → AddContract → UpdateAllocation×N → AddUser×N.**
No mnemonic (the projcode already exists) and, critically, **no inactivation step** —
which is the source of the first bug below.

Per resource, and a single resource can emit **three** commands
-------------------------------------------------------------

==================================================  ==========================================
condition                                           result
==================================================  ==========================================
no allocation, **or** it does not overlap the       **ADD**, using the action's dates
action window
overlaps, existing EOD end **after** action end     **ERROR** ``Action end date before
                                                    existing allocation end date for %s``
overlaps, existing end **before** action end        **EXTEND**, then…
…unless ``comments == "AUTO_DEFAULT_ALLOCATION_     stop — a contingent resource gets its
TRANSACTION"``                                      date moved but not its amount
otherwise                                           **SUPPLEMENT** (``> 0``) or
                                                    **ADJUST** (``< 0``)
==================================================  ==========================================

⚠️ The error string here is **not** Extension's. ``Action end date before existing
allocation end date for %s`` interpolates a *resource name* and omits the word "is";
Extension's interpolates a *date* and includes it. Which one an operator sees is how
they tell which path rejected them, so the two builders are separate.

⚠️ Update-driven extends carry the **resource comment**, not
``XrasAction Extension Request``.

Three legacy bugs, and what this port does with each
----------------------------------------------------

1. **It silently re-activates an inactive project.** ``getActive()`` is hardcoded
   ``true`` and, unlike the Add path, nothing runs ``InactivateNewProject`` afterwards.
   An XRAS project is inactive because a human has not approved it yet, so re-approving
   it as a side effect of a Supplement is wrong. **Not ported** — we leave ``active``
   alone and warn.
2. **It never actually updates the lead or the admin.** The guard compares the fetched
   user's username against the lookup key, which is always equal, and ``setLeadUser``
   is missing braces so only its first statement is guarded. **Fixed** — plainly a bug.
3. **The ``UNDO AUTO/DEFAULT`` compensating adjustment.** ``ActionTag`` writers use
   ``.name()`` while the detector compares ``.getValue()``; they never match, and
   production holds **zero** ``UNDO`` rows of either spelling. **Not ported** — detected
   and warned. See ``docs/plans/XRAS_SPRINT_C.md`` § *Legacy defect 5*. The separate
   *contingent-resource* short-circuit compares ``.name()`` on both sides and does work,
   so that one **is** ported.

Verified against ``~/codes/sam`` at tag 2.0.3 (``UpdateProjectAssembler``,
``UpdateProjectActionCommandFactory``, ``UpdateProjectAllocationActionCommandsFactory``).
See ``docs/plans/XRAS_SPRINT_C.md`` § *Update*.
"""

import logging
from datetime import datetime
from typing import List, Optional

from sam.accounting.allocations import Allocation, AllocationTransactionType
from sam.core.users import User
from sam.manage import add_user_to_project
from sam.manage.allocations import (
    adjust_allocation,
    create_allocation,
    supplement_allocation,
)
from sam.manage.extend import extend_account_allocation
from sam.manage.transaction import management_transaction
from sam.projects.contracts import ProjectContract
from sam.projects.projects import Project

from .. import errors as e
from ..dispatch import DispatchResult, register
from ..errors import ActionErrors
from ..extractors import resolve_allocation_type, resolve_area_of_interest
from ..roster import resolve_roster
from .extension import _get, effective_end_date, latest_allocation, parse_action_end_date
from .new import _abstract, _title, clamp_start_to_commission, parse_action_begin_date
from .supplement import (
    account_for_resource,
    auth_at_panel_meeting,
    new_allocation_end_date,
    resolve_resource,
    resource_comment,
    transaction_amount,
)

logger = logging.getLogger(__name__)

__all__ = ['handle_update', 'is_allocation_overlapping', 'CONTINGENT_RESOURCE_COMMENT']

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

    ⚠️ It returns ``False`` when **either** action date is null, which routes the
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


def _plan_resource(session, project, wire_resource, action, errs, *,
                   start, end, auth):
    """The per-resource decision. Returns a list of ``(kind, …)`` tuples, in order."""
    resource = resolve_resource(session, wire_resource, errs)
    if resource is None:
        return []

    account = account_for_resource(project, resource)
    allocation = latest_allocation(account) if account is not None else None
    comment = resource_comment(wire_resource)
    planned = []

    # --- ADD: no allocation at all, or none overlapping the action window.
    if allocation is None or not is_allocation_overlapping(allocation, start, end):
        amount = transaction_amount(wire_resource, errs)
        if amount is None or start is None or end is None:
            return []
        clamped = clamp_start_to_commission(resource, start)
        if end <= clamped:
            errs.report(e.allocation_end_before_commission(
                end.strftime('%Y-%m-%d'), resource.resource_name))
            return []
        planned.append(('add', resource, amount, clamped, end, comment, auth))
        return planned

    existing_end = effective_end_date(allocation)

    # --- ERROR: the action would shrink it. Note the Update-specific string.
    if existing_end is not None and existing_end > end:
        errs.report(e.update_end_date_before_existing(resource.resource_name))
        return []

    # --- EXTEND, carrying the RESOURCE comment rather than the Extension one.
    if existing_end is not None and existing_end < end:
        planned.append(('extend', allocation, end, comment))

    # --- Contingent resource: the date moves, the amount does not. This short-circuit
    # compares `.name()` on both sides and genuinely works, unlike the undo below.
    if _get(wire_resource, 'comments') == CONTINGENT_RESOURCE_COMMENT:
        return planned

    # --- The undo that has never fired. Detected, warned, NOT performed.
    if _is_auto_default_allocation(allocation):
        logger.warning(
            'XRAS update touched an AUTO/DEFAULT allocation (%s on %s). Legacy would '
            'attempt a compensating UNDO adjustment here; that mechanism is broken and '
            'has never executed (zero UNDO rows in production), so none is written. '
            'See XRAS_SPRINT_C.md legacy defect 5.',
            allocation.allocation_id, resource.resource_name)

    amount = transaction_amount(wire_resource, errs)
    if amount is None:
        return planned
    if amount > 0:
        planned.append(('supplement', allocation, amount, comment, auth))
    elif amount < 0:
        planned.append(('adjust', allocation, amount, comment))
    return planned


def handle_update(session, action) -> DispatchResult:
    """Update an existing project, its contracts, allocations and membership.

    Raises:
        XrasActionRejected: anything assembly reported. Nothing is written.
    """
    projcode = (_get(action, 'requestNumber') or '').strip()
    project = Project.get_by_projcode(session, projcode)
    errs = ActionErrors()

    # ---- assemble
    title = _title(action, errs)
    roster = resolve_roster(session, action, errs)
    aoi = resolve_area_of_interest(session, action, errs)
    allocation_type = resolve_allocation_type(session, action, errs)
    start = parse_action_begin_date(action, errs)
    end = parse_action_end_date(action, errs)
    auth = auth_at_panel_meeting(session, action)

    from .new import _plan_contracts
    contracts = _plan_contracts(session, action, errs)

    planned: List[tuple] = []
    if project is not None:
        for wire_resource in _get(action, 'resources') or ():
            planned.extend(_plan_resource(
                session, project, wire_resource, action, errs,
                start=start, end=end, auth=auth))

    lead = (User.get_by_username(session, roster.pi_username)
            if roster.pi_username else None)
    admin = (User.get_by_username(session, roster.admin_username)
             if roster.admin_username else None)
    members = [User.get_by_username(session, name)
               for name in roster.member_usernames]

    errs.raise_if_any()

    # ---- execute
    with management_transaction(session):
        # 1. The project itself. `active` is deliberately absent — see bug 1.
        if not project.is_active:
            logger.warning(
                'XRAS update targets inactive project %s; legacy would silently '
                're-activate it. Leaving it inactive — a human has not approved it.',
                projcode)
        project.update(
            title=title,
            abstract=_abstract(action),
            area_of_interest_id=aoi.area_of_interest_id,
            allocation_type_id=allocation_type.allocation_type_id,
            # Bug 2: legacy's guard never fires, so these never move. They do here.
            project_lead_user_id=lead.user_id if lead else None,
            project_admin_user_id=admin.user_id if admin else None,
        )

        # 2. Contracts — additive; an existing link is left alone.
        existing = {pc.contract_id for pc in project.contracts}
        for contract in contracts:
            if contract.contract_id not in existing:
                ProjectContract.create(session, project_id=project.project_id,
                                       contract_id=contract.contract_id)

        # 3. Allocations, in the order the factory emitted them.
        for step in planned:
            kind = step[0]
            if kind == 'add':
                _, resource, amount, alloc_start, alloc_end, comment, panel = step
                created = create_allocation(
                    session, project_id=project.project_id,
                    resource_id=resource.resource_id, amount=amount,
                    start_date=alloc_start, end_date=alloc_end,
                    user_id=None, comment=comment)
                if panel:
                    _mark_panel_authorised(session, created)
            elif kind == 'extend':
                _, allocation, new_end, comment = step
                extend_account_allocation(session, allocation, new_end=new_end,
                                          comment=comment)
            elif kind == 'supplement':
                _, allocation, amount, comment, panel = step
                supplement_allocation(session, allocation, amount=amount,
                                      comment=comment, auth_at_panel_mtg=panel)
            elif kind == 'adjust':
                _, allocation, amount, comment = step
                adjust_allocation(session, allocation, amount=amount, comment=comment)

        # 4. Membership. As on the Add path, skipped when the project has no accounts.
        if project.accounts:
            for member in members:
                if member is not None:
                    add_user_to_project(session, project.project_id, member.user_id)

    return DispatchResult(status='processed', service='update', projcode=projcode,
                          warnings=roster.warnings)


def _mark_panel_authorised(session, allocation) -> None:
    latest = max(allocation.transactions,
                 key=lambda t: (t.creation_time, t.allocation_transaction_id or 0),
                 default=None)
    if latest is not None:
        latest.auth_at_panel_mtg = True
        session.flush()


register('update', handle_update)
