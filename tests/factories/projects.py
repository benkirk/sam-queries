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
from sam.accounting.allocations import Allocation
from sam.core.users import User
from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup
from sam.projects.projects import Project
from sam.resources.facilities import Facility
from sam.resources.resources import Resource

from ._seq import next_seq
from .core import make_user
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
) -> Project:
    """Build and flush a fresh Project row, auto-building a lead user and AOI.

    Calls `Project._ns_place_in_tree(session, parent=parent)` after the
    initial flush so the NestedSetMixin tree columns (`tree_left`,
    `tree_right`, `tree_root`) are populated.

    For a child project, pass `parent=` an existing Project — the mixin
    handles re-shifting siblings automatically.
    """
    if lead is None:
        lead = make_user(session)
    if aoi is None:
        aoi = make_aoi(session)
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
    )
    session.add(project)
    session.flush()
    project._ns_place_in_tree(session, parent=parent)
    return project


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
    creates the Account → Project → Resource graph as needed.
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
