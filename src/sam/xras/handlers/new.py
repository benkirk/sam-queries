"""New -- 21% of traffic at 30% success, and the only handler that mints a projcode.

``AddProjectAssembler`` marks its own order ``// the order below is important!!``:
**AddProject -> AddContract -> AddAllocation x N -> AddUser x N ->
InactivateNewProject**. It cannot be rearranged, and both reasons are enforced
by exceptions: an allocation cannot be added to an inactive project, and
accounts exist only as a side effect of adding an allocation while user
assignment requires an account.

WARNING: the project is created ACTIVE and deactivated at the end. That is not
a quirk to tidy -- ``InactivateNewProject`` running last is the whole reason the
middle steps can run. The resulting ``active = 0`` is by design: the success
email is the human trigger to approve it, and 21 of 23 XRAS-created projects
have since been activated by hand.

The 70% failure rate is not from this code. The measured causes are data: an
unresolvable mnemonic (24%, a frozen ``user_organization`` table), unreconciled
ARC placeholder identities (55%), and resource keys with no mapping row. Each
now arrives as a reviewable 422 carrying the string legacy emitted, rather than
an opaque 500 -- which is the actual deliverable.

Verified against ``~/codes/sam`` at tag 2.0.3, but ported against
``projects_routes.py``'s create flow rather than the Java. See
``docs/xras/incoming/implemented/XRAS_SPRINT_C.md``, *New*.
"""

import logging
from typing import List

from sam.core.groups import GidAllocation, NoAvailableGidError
from sam.core.organizations import ProjectOrganization
from sam.core.users import User
from sam.manage import add_user_to_project
from sam.projects.contracts import ProjectContract
from sam.projects.projects import Project, ProjcodeExhaustedError, next_projcode

from ..dispatch import DispatchResult, register
from ..extractors import (
    resolve_allocation_type,
    resolve_area_of_interest,
    resolve_mnemonic_code,
)
from ..roster import resolve_roster
from ._allocations import auth_at_panel_meeting, create_window_from_action_dates
from ._plans import PlannedCreate
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
from .base import ActionHandler

logger = logging.getLogger(__name__)

__all__ = ['NewHandler', 'handle_new']


class XrasProjectCreationFailed(RuntimeError):
    """Exhausted projcode counter or GID pool — an operational failure, not a payload one.

    Deliberately **not** an :class:`~sam.xras.errors.XrasActionRejected`: nothing about
    the request is wrong, so a 422 telling XRAS to fix its payload would be a lie. It
    propagates and the route's error handling records it, which is the honest outcome —
    and both conditions need a human with database access, not a resubmission.

    WARNING: It lives **here** rather than in :mod:`sam.xras.errors`, where it would read as a
    sibling of :class:`~sam.xras.errors.XrasActionRejected`. That module is the *error
    string vocabulary*, and two tests enumerate its public callables to prove every
    builder is exported and declared — ``tests/unit/test_xras_errors.py`` and
    ``test_xras_error_coverage.py``, both excluding the two existing classes by name. A
    class is callable, so moving this one there would enrol it in the 34-builder matrix
    and fail both gates. Raised below by :func:`handle_new`; defined above it so the
    reference reads forward.
    """


class NewHandler(ActionHandler):
    """Create a project, its contracts, allocations and members — then inactivate it."""

    service = 'add'

    def assemble(self) -> None:
        """Resolve everything and report everything, before a projcode is drawn.

        WARNING: ``self.project`` is always ``None`` here, by dispatch invariant: this
        handler is selected only when no project of that name exists. The project this
        action creates lives on :attr:`created_project`, deliberately under a different
        name — see ``ActionHandler.project``.
        """
        self.title = title(self.action, self.errors)
        self.roster = resolve_roster(self.session, self.action, self.errors)
        self.aoi = resolve_area_of_interest(self.session, self.action, self.errors)
        self.allocation_type = resolve_allocation_type(
            self.session, self.action, self.errors)
        self.mnemonic = resolve_mnemonic_code(
            self.session, self.action, self.errors,
            pi_username=self.roster.pi_username, pi=self.roster.pi)
        self.contracts = plan_contracts(self.session, self.action, self.errors)

        # WARNING: Before `_plan_allocations()`, and that ordering is now load-bearing: the
        # plan records capture the flag at construction, where the old execute-time
        # loop read it after assembly had finished. Computing it after planning would
        # stamp every CREATE row with the `False` from `__init__`.
        #
        # Safe to move up: `auth_at_panel_meeting` reports no errors, so it cannot
        # disturb the 422 ordering that ten test modules assert.
        self.panel_authorised = auth_at_panel_meeting(self.session, self.action)
        self.allocations = self._plan_allocations()

        # Taken from the roster, not re-looked-up: `resolve_roster` fetched every one
        # of these while validating them, and this block used to throw that away and
        # query again — twenty SELECTs for a ten-member roster where ten would do.
        self.lead = self.roster.pi
        self.admin = self.roster.admin
        self.members = list(self.roster.members)
        self.warnings = self.roster.warnings

    def _plan_allocations(self):
        """One allocation per ``resources[]`` entry, using the **action's own** dates.

        WARNING: The contrast with Supplement matters: that handler derives its create-branch
        window from *today* and the project's history, while New uses ``actionBeginDate``
        and ``actionEndDate``. Same table, two different date policies, both legacy's,
        and both now named — ``create_window_from_action_dates`` is this one.

        WARNING: Both dates are parsed **above** the loop, so date errors precede resource
        errors in the 422 body. That order is asserted across ten test modules.
        """
        begin = parse_action_begin_date(self.action, self.errors)
        end = parse_action_end_date(self.action, self.errors)

        planned: List[tuple] = []
        for wire_resource in self.get('resources') or ():
            resource = resolve_resource(self.session, wire_resource, self.errors)
            amount = transaction_amount(wire_resource, self.errors)
            if resource is None or amount is None or begin is None or end is None:
                continue
            window = create_window_from_action_dates(
                resource, begin, end, self.errors)
            if window is None:
                continue
            start, alloc_end = window
            planned.append(PlannedCreate(
                resource=resource, amount=amount,
                comment=resource_comment(wire_resource),
                start=start, end=alloc_end,
                panel_authorised=self.panel_authorised))
        return planned

    def execute(self) -> None:
        """In the order ``AddProjectAssembler`` marks "important!!".

        WARNING: ``self.lead`` is dereferenced without a guard. That is safe **only** because
        assembly reported ``Missing pi role`` / ``PI %s is not in database`` and
        ``raise_if_any()`` has already fired. The invariant now spans two methods; do
        not weaken the roster reporting without revisiting this line.
        """
        assert self.lead is not None, 'assemble() must reject a roster with no PI'

        facility_id = self.allocation_type.panel.facility_id
        try:
            projcode = next_projcode(
                self.session, facility_id=facility_id,
                mnemonic_code_id=self.mnemonic.mnemonic_code_id, allocate=True)
        except (ValueError, ProjcodeExhaustedError) as exc:
            raise XrasProjectCreationFailed(
                f'Could not generate a project code: {exc}') from exc
        self.projcode_result = projcode

        try:
            unix_gid = GidAllocation.allocate_next_gid(self.session)
        except NoAvailableGidError as exc:
            raise XrasProjectCreationFailed(
                'GID pool is exhausted — no Unix GID could be allocated') from exc

        # 1. The project, created ACTIVE. Steps 2-4 depend on it.
        project = Project.create(
            self.session,
            projcode=projcode,
            title=self.title,
            abstract=abstract(self.action),
            project_lead_user_id=self.lead.user_id,
            project_admin_user_id=self.admin.user_id if self.admin else None,
            area_of_interest_id=self.aoi.area_of_interest_id,
            allocation_type_id=self.allocation_type.allocation_type_id,
            unix_gid=unix_gid,
            # ChargeType.NONEXEMPT, always — legacy's getChargeType() is a constant.
            charging_exempt=False,
        )
        self.created_project = project

        # The lead's organization, mirroring the admin create-project flow.
        organization = _lead_organization(self.lead)
        if organization is not None:
            ProjectOrganization.create(
                self.session, project_id=project.project_id,
                organization_id=organization.organization_id)

        # 2. Contracts.
        for contract in self.contracts:
            ProjectContract.create(self.session, project_id=project.project_id,
                                   contract_id=contract.contract_id)

        # 3. Allocations — these create the accounts step 4 needs.
        #
        # `project` is the row created moments ago inside this transaction, NOT
        # `self.project`, which is None here by dispatch invariant.
        self.execute_plan(self.allocations, project=project)

        # 4. Members. Skipped entirely when there are no accounts —
        # `add_user_to_project` raises rather than no-ops, and an Educational
        # allocation with `resources: []` is a real shape.
        if self.allocations:
            for member in self.members:
                if member is not None:
                    add_user_to_project(self.session, project.project_id,
                                        member.user_id)

        # 5. Inactivate, last. See the module docstring for why it cannot move.
        project.update(active=False)


def handle_new(session, action, *, validate_only: bool = False) -> DispatchResult:
    """The registry's contract:
    ``(session, action, *, validate_only=False) -> DispatchResult``."""
    return NewHandler(session, action).run(validate_only=validate_only)


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
