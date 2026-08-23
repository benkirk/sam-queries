"""Factories for project-domain entities: Facility, AreaOfInterest(Group),
Project, Account.

`Project` uses the NestedSetMixin, so `make_project` instantiates the row,
flushes to obtain a PK, then calls `.add(session, parent=None)` to set
`tree_left`/`tree_right`/`tree_root` for a single-node root tree. Without
the `.add()` call the subsequent NestedSetMixin queries (children,
descendants, etc.) misbehave.

`make_account` delegates to `Account.create()` which auto-propagates the
project lead onto the new account as an AccountUser. So a freshly built
`(project, account)` pair already has one member: the project lead.
"""
from datetime import datetime, timedelta
from typing import Optional

from sam.accounting.accounts import Account
from sam.accounting.adjustments import ChargeAdjustment, ChargeAdjustmentType
from sam.accounting.allocations import (
    Allocation, AllocationTransaction, AllocationType,
)
from sam.core.organizations import ProjectOrganization
from sam.core.users import User
from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup
from sam.projects.contracts import (
    Contract, ContractSource, NSFProgram, ProjectContract,
)
from sam.projects.projects import Project
from sam.resources.facilities import Facility, Panel
from sam.resources.resources import Resource

from ._seq import next_seq
from .core import make_organization, make_user
from .resources import make_resource


def make_facility(
    session,
    *,
    facility_name: Optional[str] = None,
    description: Optional[str] = None,
    fair_share_percentage: Optional[float] = None,
) -> Facility:
    """Build and flush a fresh Facility row.

    `code` is left `None` deliberately: it is a 1-character UNIQUE column
    and there are only ~26 viable values, so factory-generated codes would
    collide both with the snapshot and across xdist workers.
    """
    if facility_name is None:
        facility_name = next_seq("F")
    if description is None:
        description = f"Test facility {facility_name}"

    return Facility.create(
        session,
        facility_name=facility_name,
        description=description,
        code=None,
        fair_share_percentage=fair_share_percentage,
    )


def make_panel(
    session,
    *,
    facility: Optional[Facility] = None,
    facility_name: Optional[str] = None,
    panel_name: Optional[str] = None,
) -> Panel:
    """Build and flush a Panel, auto-building its Facility.

    `facility_name` is the ergonomic form: pass `'UNIV'` or `'WNA'` and the
    existing snapshot row is reused if present, because those two names are
    real production facilities that facility-scoped code filters on by name.
    Creating a second `Facility` called `UNIV` would make `.in_(['UNIV'])`
    ambiguous and the test's meaning with it.
    """
    if facility is None:
        if facility_name is not None:
            facility = (session.query(Facility)
                        .filter_by(facility_name=facility_name).first())
        if facility is None:
            facility = make_facility(session, facility_name=facility_name)
    if panel_name is None:
        panel_name = next_seq("PNL")

    return Panel.create(
        session,
        panel_name=panel_name,
        description=f"Test panel {panel_name}",
        facility_id=facility.facility_id,
    )


def make_allocation_type(
    session,
    *,
    panel: Optional[Panel] = None,
    facility_name: Optional[str] = None,
    allocation_type: Optional[str] = None,
) -> AllocationType:
    """Build and flush an AllocationType, auto-building its Panel and Facility.

    This is the chain a facility filter walks:
    ``Project -> AllocationType -> Panel -> Facility``. A project built by
    `make_project` has `allocation_type_id` NULL, so it is invisible to every
    facility-scoped query until one of these is attached — which is easy to
    mistake for "the query is broken".
    """
    if panel is None:
        panel = make_panel(session, facility_name=facility_name)
    if allocation_type is None:
        allocation_type = next_seq("AT")[:20]

    return AllocationType.create(
        session,
        allocation_type=allocation_type,
        panel_id=panel.panel_id,
    )


def make_aoi_group(
    session,
    *,
    name: Optional[str] = None,
) -> AreaOfInterestGroup:
    """Build and flush a fresh AreaOfInterestGroup."""
    if name is None:
        name = next_seq("AOIG")
    return AreaOfInterestGroup.create(session, name=name)


def make_aoi(
    session,
    *,
    group: Optional[AreaOfInterestGroup] = None,
    area_of_interest: Optional[str] = None,
) -> AreaOfInterest:
    """Build and flush a fresh AreaOfInterest, auto-building a group if needed."""
    if group is None:
        group = make_aoi_group(session)
    if area_of_interest is None:
        area_of_interest = next_seq("AOI")
    return AreaOfInterest.create(
        session,
        area_of_interest=area_of_interest,
        area_of_interest_group_id=group.area_of_interest_group_id,
    )


def make_project(
    session,
    *,
    projcode: Optional[str] = None,
    title: Optional[str] = None,
    lead: Optional[User] = None,
    aoi: Optional[AreaOfInterest] = None,
    parent: Optional[Project] = None,
    active: bool = True,
    allocation_type: Optional[AllocationType] = None,
    facility_name: Optional[str] = None,
) -> Project:
    """Build and flush a fresh Project row, auto-building a lead user and AOI.

    Calls `Project._ns_place_in_tree(session, parent=parent)` after the
    initial flush so the NestedSetMixin tree columns (`tree_left`,
    `tree_right`, `tree_root`) are populated.

    For a child project, pass `parent=` an existing Project — the mixin
    handles re-shifting siblings automatically.

    `allocation_type` / `facility_name` are how a project becomes visible to
    facility-scoped queries, which walk
    `Project -> AllocationType -> Panel -> Facility`. Both default to None, so
    a plain `make_project()` has `allocation_type_id` NULL and is invisible to
    every such query — pass `facility_name='UNIV'` when that matters.
    """
    if lead is None:
        lead = make_user(session)
    if aoi is None:
        aoi = make_aoi(session)
    if allocation_type is None and facility_name is not None:
        allocation_type = make_allocation_type(session,
                                               facility_name=facility_name)
    if projcode is None:
        projcode = next_seq("PRJ")
    if title is None:
        title = f"Test project {projcode}"

    project = Project(
        projcode=projcode,
        title=title,
        project_lead_user_id=lead.user_id,
        area_of_interest_id=aoi.area_of_interest_id,
        parent_id=parent.project_id if parent is not None else None,
        active=active,
        allocation_type_id=(allocation_type.allocation_type_id
                            if allocation_type is not None else None),
    )
    session.add(project)
    session.flush()
    project._ns_place_in_tree(session, parent=parent)
    return project


def make_contract_source(session, *, name: str = "NSF") -> ContractSource:
    """Fetch (or create) a ContractSource by name.

    Resolved by name at runtime rather than by hardcoded ID — `contract_source`
    is a lookup table whose PKs differ between the snapshot and a fresh DB.
    """
    src = session.query(ContractSource).filter_by(contract_source=name).first()
    if src is None:
        src = ContractSource(contract_source=name, active=True)
        session.add(src)
        session.flush()
    return src


def make_nsf_program(session, *, name: Optional[str] = None) -> NSFProgram:
    """Fetch (or create) an NSFProgram by name.

    Resolved by name at runtime for the same reason as `make_contract_source`:
    `nsf_program` is a lookup table whose PKs differ between the snapshot and a
    fresh DB, and `nsf_program_name` is uniquely indexed.
    """
    if name is None:
        name = f"Test program {next_seq('PROG')}"
    program = session.query(NSFProgram).filter_by(nsf_program_name=name).first()
    if program is None:
        program = NSFProgram(nsf_program_name=name, active=True)
        session.add(program)
        session.flush()
    return program


def make_contract(
    session,
    *,
    contract_number: Optional[str] = None,
    title: Optional[str] = None,
    pi: Optional[User] = None,
    source: Optional[ContractSource] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    monitor: Optional[User] = None,
    nsf_program: Optional[NSFProgram] = None,
    url: Optional[str] = None,
) -> Contract:
    """Build and flush a fresh Contract row.

    Defaults to a currently-effective window (started a year ago, open-ended).
    Pass an `end_date` in the past for an expired contract, or a `start_date`
    in the future for one that has not begun.

    `monitor`, `nsf_program` and `url` all default to unset, matching the
    nullable columns — the contract audit's checks turn on exactly these, so
    pass them explicitly when a test needs a clean row.
    """
    if pi is None:
        pi = make_user(session)
    if source is None:
        source = make_contract_source(session)
    if contract_number is None:
        contract_number = next_seq("CTR")
    if title is None:
        title = f"Test contract {contract_number}"
    if start_date is None:
        start_date = datetime.now() - timedelta(days=365)

    contract = Contract(
        contract_source_id=source.contract_source_id,
        contract_number=contract_number,
        title=title,
        principal_investigator_user_id=pi.user_id,
        contract_monitor_user_id=monitor.user_id if monitor else None,
        nsf_program_id=nsf_program.nsf_program_id if nsf_program else None,
        url=url,
        start_date=start_date,
        end_date=end_date,
    )
    session.add(contract)
    session.flush()
    return contract


def make_project_contract(
    session,
    *,
    project: Optional[Project] = None,
    contract: Optional[Contract] = None,
    **contract_kwargs,
) -> ProjectContract:
    """Link a project to a contract. Extra kwargs go to `make_contract`."""
    if project is None:
        project = make_project(session)
    if contract is None:
        contract = make_contract(session, **contract_kwargs)

    pc = ProjectContract(project_id=project.project_id, contract_id=contract.contract_id)
    session.add(pc)
    session.flush()
    return pc


def make_project_organization(
    session,
    *,
    project: Optional[Project] = None,
    organization=None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> ProjectOrganization:
    """Link a project to an organization over a date window.

    `end_date` of None means the link is still in effect.
    """
    if project is None:
        project = make_project(session)
    if organization is None:
        organization = make_organization(session)
    if start_date is None:
        start_date = datetime.now() - timedelta(days=365)

    po = ProjectOrganization(
        project_id=project.project_id,
        organization_id=organization.organization_id,
        start_date=start_date,
        end_date=end_date,
    )
    session.add(po)
    session.flush()
    return po


def make_account(
    session,
    *,
    project: Optional[Project] = None,
    resource: Optional[Resource] = None,
) -> Account:
    """Build and flush a fresh Account, auto-building project + resource.

    Delegates to `Account.create()`, which auto-propagates the project
    lead and any existing sibling members onto the new account as
    AccountUser rows.
    """
    if project is None:
        project = make_project(session)
    if resource is None:
        resource = make_resource(session)

    return Account.create(
        session,
        project_id=project.project_id,
        resource_id=resource.resource_id,
    )


def make_allocation(
    session,
    *,
    account: Optional[Account] = None,
    amount: float = 10_000.0,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    description: Optional[str] = None,
    parent: Optional[Allocation] = None,
) -> Allocation:
    """Build and flush a fresh Allocation, auto-building an Account if needed.

    Delegates to `Allocation.create()`, which validates `amount > 0` and
    creates the Account -> Project -> Resource graph as needed.
    """
    if account is None:
        account = make_account(session)
    if start_date is None:
        start_date = datetime.now() - timedelta(days=1)
    if end_date is None:
        end_date = datetime.now() + timedelta(days=365)

    return Allocation.create(
        session,
        project_id=account.project_id,
        resource_id=account.resource_id,
        amount=amount,
        start_date=start_date,
        end_date=end_date,
        description=description,
        parent_allocation_id=parent.allocation_id if parent is not None else None,
    )


def make_allocation_transaction(
    session,
    *,
    allocation: Optional[Allocation] = None,
    user: Optional[User] = None,
    transaction_type: str = "EDIT",
    transaction_amount: Optional[float] = None,
    requested_amount: Optional[float] = None,
    creation_time: Optional[datetime] = None,
    alloc_start_date: Optional[datetime] = None,
    alloc_end_date: Optional[datetime] = None,
    transaction_comment: Optional[str] = None,
    propagated: bool = False,
    auth_at_panel_mtg: Optional[bool] = None,
) -> AllocationTransaction:
    """Build and flush a fresh AllocationTransaction, auto-building an Allocation if needed.

    Bypasses the production ``log_allocation_transaction()`` helper so tests can
    set ``creation_time`` explicitly (for date-range filter tests) and mix
    arbitrary ``transaction_type`` / ``propagated`` / ``user`` combinations.
    """
    if allocation is None:
        allocation = make_allocation(session)

    txn = AllocationTransaction(
        allocation_id=allocation.allocation_id,
        user_id=user.user_id if user is not None else None,
        transaction_type=transaction_type,
        transaction_amount=(
            transaction_amount if transaction_amount is not None else allocation.amount
        ),
        requested_amount=(
            requested_amount if requested_amount is not None else allocation.amount
        ),
        alloc_start_date=(
            alloc_start_date if alloc_start_date is not None else allocation.start_date
        ),
        alloc_end_date=(
            alloc_end_date if alloc_end_date is not None else allocation.end_date
        ),
        transaction_comment=transaction_comment,
        propagated=propagated,
        auth_at_panel_mtg=auth_at_panel_mtg,
    )
    if creation_time is not None:
        txn.creation_time = creation_time
    session.add(txn)
    session.flush()
    return txn


def make_charge_adjustment(
    session,
    *,
    account: Optional[Account] = None,
    adjusted_by: Optional[User] = None,
    adjustment_type: Optional[ChargeAdjustmentType] = None,
    amount: float = -100.0,
    adjustment_date: Optional[datetime] = None,
    comment: Optional[str] = None,
) -> ChargeAdjustment:
    """Build and flush a fresh ChargeAdjustment, auto-building an Account if needed.

    ``ChargeAdjustmentType`` is snapshot-seeded reference data; when none is
    passed, pick any row. Tests that need a specific type name should fetch
    the row themselves and pass it in.
    """
    if account is None:
        account = make_account(session)
    if adjustment_date is None:
        adjustment_date = datetime.now()
    if adjustment_type is None:
        adjustment_type = session.query(ChargeAdjustmentType).first()
        if adjustment_type is None:
            import pytest
            pytest.skip("No ChargeAdjustmentType reference rows in test database")

    adj = ChargeAdjustment(
        account_id=account.account_id,
        adjusted_by_id=adjusted_by.user_id if adjusted_by is not None else None,
        charge_adjustment_type_id=adjustment_type.charge_adjustment_type_id,
        amount=amount,
        adjustment_date=adjustment_date,
        comment=comment,
    )
    session.add(adj)
    session.flush()
    return adj
