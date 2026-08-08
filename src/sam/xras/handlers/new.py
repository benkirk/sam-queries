"""New — 21% of traffic at **30% success**, the hardest of the six, and the only one
that mints a projcode.

``AddProjectAssembler`` marks its own order ``// the order below is important!!``:

**AddProject → AddContract → AddAllocation×N → AddUser×N → InactivateNewProject**

and it cannot be rearranged, for two reasons that are both enforced by exceptions:

* ``Project.addAllocation`` throws ``Cannot add allocation to inactive project %s`` and
  ``Account.isAssignable()`` requires ``project.isActive()`` — so the project is created
  **active** and inactivated only at the very end.
* Accounts exist as a side effect of adding an allocation, and user assignment requires
  an account. Allocations must precede users; SAM's ``add_user_to_project`` raises for
  the same reason, so the constraint survives the port unchanged.

⚠️ **The project is created active and then deactivated.** That is not a quirk to tidy:
``InactivateNewProject`` running last is the whole reason the middle steps can run at
all, and the resulting ``active = 0`` is by design — the success email is the human
trigger to approve it. Production agrees: 21 of 23 XRAS-created projects have since
been activated by hand.

Where the 70% failure rate comes from
------------------------------------
Not from this code. The measured causes are data: an unresolvable mnemonic (24%, a
frozen ``user_organization`` table), unreconciled ARC placeholder identities (55%), and
resource keys with no mapping row. Every one of those now arrives as a reviewable 422
with the string legacy emitted, rather than as an opaque 500 — which is the actual
deliverable here.

Verified against ``~/codes/sam`` at tag 2.0.3 (``AddProjectAssembler``,
``AddProjectActionCommandFactory``, ``ProjectActionCommandFactoryBase``). Ported
against ``src/webapp/dashboards/admin/projects_routes.py``'s create flow rather than
against the Java, per the plan. See ``docs/plans/XRAS_SPRINT_C.md`` § *New*.
"""

import logging
from typing import List

from sam.core.groups import GidAllocation, NoAvailableGidError
from sam.core.organizations import ProjectOrganization
from sam.core.users import User
from sam.manage import add_user_to_project
from sam.manage.allocations import create_allocation
from sam.manage.transaction import management_transaction
from sam.projects.contracts import ProjectContract
from sam.projects.projects import Project, ProjcodeExhaustedError, next_projcode

from ..dispatch import DispatchResult, register
from ..errors import ActionErrors
from ..extractors import (
    resolve_allocation_type,
    resolve_area_of_interest,
    resolve_mnemonic_code,
)
from ..roster import resolve_roster
from ..wire import get_field
from ._allocations import (
    auth_at_panel_meeting,
    create_window_from_action_dates,
    mark_panel_authorised,
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

logger = logging.getLogger(__name__)

__all__ = ['handle_new']


class XrasProjectCreationFailed(RuntimeError):
    """Exhausted projcode counter or GID pool — an operational failure, not a payload one.

    Deliberately **not** an :class:`~sam.xras.errors.XrasActionRejected`: nothing about
    the request is wrong, so a 422 telling XRAS to fix its payload would be a lie. It
    propagates and the route's error handling records it, which is the honest outcome —
    and both conditions need a human with database access, not a resubmission.

    ⚠️ It lives **here** rather than in :mod:`sam.xras.errors`, where it would read as a
    sibling of :class:`~sam.xras.errors.XrasActionRejected`. That module is the *error
    string vocabulary*, and two tests enumerate its public callables to prove every
    builder is exported and declared — ``tests/unit/test_xras_errors.py`` and
    ``test_xras_error_coverage.py``, both excluding the two existing classes by name. A
    class is callable, so moving this one there would enrol it in the 34-builder matrix
    and fail both gates. Raised below by :func:`handle_new`; defined above it so the
    reference reads forward.
    """


def _plan_allocations(session, action, errs: ActionErrors) -> List[tuple]:
    """One allocation per ``resources[]`` entry, using the **action's own** dates.

    ⚠️ The contrast with Supplement matters: that handler derives its create-branch
    window from *today* and the project's history, while New uses ``actionBeginDate``
    and ``actionEndDate``. Same table, two different date policies, both legacy's, and
    both now named — ``create_window_from_action_dates`` is this one.

    ⚠️ Both dates are parsed **above** the loop, so date errors precede resource errors
    in the 422 body. That order is asserted across ten test modules.
    """
    begin = parse_action_begin_date(action, errs)
    end = parse_action_end_date(action, errs)

    planned: List[tuple] = []
    for wire_resource in get_field(action, 'resources') or ():
        resource = resolve_resource(session, wire_resource, errs)
        amount = transaction_amount(wire_resource, errs)
        if resource is None or amount is None or begin is None or end is None:
            continue
        window = create_window_from_action_dates(resource, begin, end, errs)
        if window is None:
            continue
        start, alloc_end = window
        planned.append((resource, amount, start, alloc_end,
                        resource_comment(wire_resource)))
    return planned


def handle_new(session, action) -> DispatchResult:
    """Create a project, its contracts, allocations and members — then inactivate it.

    Raises:
        XrasActionRejected: anything the assembly reported. Nothing is written; the
            projcode counter and the GID pool are untouched, because both are drawn
            **inside** the transaction.
    """
    errs = ActionErrors()

    # ---- assemble: everything is resolved and reported before anything is written.
    project_title = title(action, errs)
    roster = resolve_roster(session, action, errs)
    aoi = resolve_area_of_interest(session, action, errs)
    allocation_type = resolve_allocation_type(session, action, errs)
    mnemonic = resolve_mnemonic_code(session, action, errs,
                                     pi_username=roster.pi_username)
    contracts = plan_contracts(session, action, errs)
    allocations = _plan_allocations(session, action, errs)
    auth = auth_at_panel_meeting(session, action)

    lead = (User.get_by_username(session, roster.pi_username)
            if roster.pi_username else None)
    admin = (User.get_by_username(session, roster.admin_username)
             if roster.admin_username else None)
    members = [User.get_by_username(session, name)
               for name in roster.member_usernames]

    errs.raise_if_any()

    # ---- execute, in the order AddProjectAssembler marks "important!!".
    with management_transaction(session):
        facility_id = allocation_type.panel.facility_id
        try:
            projcode = next_projcode(
                session, facility_id=facility_id,
                mnemonic_code_id=mnemonic.mnemonic_code_id, allocate=True)
        except (ValueError, ProjcodeExhaustedError) as exc:
            raise XrasProjectCreationFailed(
                f'Could not generate a project code: {exc}') from exc

        try:
            unix_gid = GidAllocation.allocate_next_gid(session)
        except NoAvailableGidError as exc:
            raise XrasProjectCreationFailed(
                'GID pool is exhausted — no Unix GID could be allocated') from exc

        # 1. The project, created ACTIVE. Steps 2-4 depend on it.
        project = Project.create(
            session,
            projcode=projcode,
            title=project_title,
            abstract=abstract(action),
            project_lead_user_id=lead.user_id,
            project_admin_user_id=admin.user_id if admin else None,
            area_of_interest_id=aoi.area_of_interest_id,
            allocation_type_id=allocation_type.allocation_type_id,
            unix_gid=unix_gid,
            # ChargeType.NONEXEMPT, always — legacy's getChargeType() is a constant.
            charging_exempt=False,
        )

        # The lead's organization, mirroring the admin create-project flow.
        organization = _lead_organization(lead)
        if organization is not None:
            ProjectOrganization.create(
                session, project_id=project.project_id,
                organization_id=organization.organization_id)

        # 2. Contracts.
        for contract in contracts:
            ProjectContract.create(session, project_id=project.project_id,
                                   contract_id=contract.contract_id)

        # 3. Allocations — these create the accounts step 4 needs.
        for resource, amount, start, end, comment in allocations:
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

        # 4. Members. Skipped entirely when there are no accounts —
        # `add_user_to_project` raises rather than no-ops, and an Educational
        # allocation with `resources: []` is a real shape.
        if allocations:
            for member in members:
                if member is not None:
                    add_user_to_project(session, project.project_id, member.user_id)

        # 5. Inactivate, last. See the module docstring for why it cannot move.
        project.update(active=False)

    return DispatchResult(status='processed', service='add', projcode=projcode,
                          warnings=roster.warnings)


def _lead_organization(lead: User):
    """The lead's current organization, or ``None``.

    Legacy reads ``leadUser.getBestOrganization()`` for the project's organization
    acronym. Same predicate as the mnemonic extractor's and the admin lead-hint
    route's, so all three agree on which organization a person is in.
    """
    if lead is None:
        return None
    return next((uo.organization for uo in lead.organizations if uo.is_active), None)


register('add', handle_new)
