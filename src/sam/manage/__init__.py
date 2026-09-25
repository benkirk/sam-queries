"""
Project and user management functions.

Administrative operations for managing project membership, roles, etc.
These are write operations that modify the database, as opposed to
read-only query functions in sam.queries.
"""

from datetime import datetime, time, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import select, or_

from sam.accounting.accounts import Account, AccountUser
from sam.projects.projects import Project
from .transaction import management_transaction
from .allocations import (
    validate_allocation_dates,
    log_allocation_transaction,
    update_allocation,
    propagate_allocation_to_subprojects,
    detach_allocation,
    get_partitioned_descendant_sum,
)
from .summaries import (
    upsert_comp_charge_summary,
    upsert_disk_charge_summary,
    upsert_archive_charge_summary,
)
from .projects import (
    DeactivationResult,
    deactivate_projects,
)


__all__ = [
    'add_user_to_project',
    'remove_user_from_project',
    'change_project_admin',
    'grant_user_resource_access',
    'revoke_user_resource_access',
    'reconcile_project_access',
    'management_transaction',
    'validate_allocation_dates',
    'log_allocation_transaction',
    'update_allocation',
    'propagate_allocation_to_subprojects',
    'detach_allocation',
    'get_partitioned_descendant_sum',
    'upsert_comp_charge_summary',
    'upsert_disk_charge_summary',
    'upsert_archive_charge_summary',
    'DeactivationResult',
    'deactivate_projects',
]


def add_user_to_project(
    session: Session,
    project_id: int,
    user_id: int,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> None:
    """
    Add a user to all accounts for a project.

    This adds the user to every account (resource) associated with the project,
    enabling them to use all resources the project has access to.

    NOTE: This function does NOT commit the session. The caller is responsible
    for calling session.commit() or session.flush() as appropriate.

    A returning member is re-added: accounts where the user has only an
    expired (end-dated) membership get a fresh open row. Only a currently
    live membership causes the account to be skipped, so the operation is
    idempotent for active members but never a silent no-op for someone
    whose prior rows have all lapsed.

    Args:
        session: SQLAlchemy session
        project_id: Project ID
        user_id: User ID to add
        start_date: Start date for membership (defaults to now if not provided)
        end_date: End date for membership (optional, defaults to None/no end date)

    Raises:
        ValueError: If the project has no accounts
    """
    now = datetime.now()
    if start_date is None:
        # Floor to the second: MySQL DATETIME rounds half-up, and a start that
        # lands in the next second reads as "not started yet" to a removal.
        start_date = now.replace(microsecond=0)

    accounts = session.query(Account).filter(
        Account.project_id == project_id,
        Account.deleted == False
    ).all()

    if not accounts:
        raise ValueError(f"No accounts found for project {project_id}")

    for account in accounts:
        # Skip only if the user already has a LIVE membership on this account —
        # one that is open-ended or not yet expired (a future start_date still
        # counts). A stale end-dated row must NOT block the add, otherwise
        # re-adding a returning member is a silent no-op on every account they
        # previously held. We test end_date directly rather than
        # AccountUser.is_active because is_active also excludes not-yet-started
        # rows, which we still want to treat as an existing membership.
        existing_open = session.query(AccountUser).filter(
            AccountUser.account_id == account.account_id,
            AccountUser.user_id == user_id,
            or_(AccountUser.end_date.is_(None), AccountUser.end_date >= now),
        ).first()

        if not existing_open:
            account_user = AccountUser(
                account_id=account.account_id,
                user_id=user_id,
                start_date=start_date,
                end_date=end_date  # Can be None
            )
            session.add(account_user)

    # Flush to assign IDs but do not commit
    # Caller is responsible for committing
    session.flush()


def _unended(now: datetime):
    """Rows still open at *now*: no end, or an end at/after it (same test as add_user_to_project)."""
    return or_(AccountUser.end_date.is_(None), AccountUser.end_date >= now)


def _end_membership(session: Session, row: AccountUser, now: datetime) -> None:
    """End an AccountUser row as of *now*; a row that has not started yet is deleted instead."""
    if row.start_date > now:
        # Never effective, so there is no history to keep, and end < start is nonsense.
        session.delete(row)
        return
    # Floor to the second: MySQL DATETIME rounds half-up, so a microsecond "now"
    # can land in the next second and leave the row live for a moment.
    # Minus 1 s: the same-request re-render tests end_date >= now, inclusive.
    cutoff = now.replace(microsecond=0) - timedelta(seconds=1)
    # Exactly-midnight ends become 23:59:59 the SAME day (normalize_end_date,
    # sam/base.py), which would keep the member live all day. Step back once more.
    if cutoff.time() == time(0, 0, 0):
        cutoff -= timedelta(seconds=1)
    row.end_date = cutoff


def remove_user_from_project(session: Session, project_id: int, user_id: int) -> None:
    """
    Remove a user from all accounts in a project.

    Every unended row the user holds on the project is end-dated as of now
    (rows are history, mirroring legacy SAM); a row that has not started yet
    is deleted. Also clears the admin role if the user being removed is the
    project admin. Cannot remove the project lead.

    NOTE: This function does NOT commit the session. The caller is responsible
    for calling session.commit() or session.flush() as appropriate.

    Args:
        session: SQLAlchemy session
        project_id: Project ID
        user_id: User ID to remove

    Raises:
        ValueError: If trying to remove the project lead
    """
    # Get project to check lead/admin
    project = session.get(Project, project_id)

    if not project:
        raise ValueError(f"Project {project_id} not found")

    # Cannot remove the lead
    if project.project_lead_user_id == user_id:
        raise ValueError("Cannot remove the project lead")

    # Get all account IDs for this project
    account_ids = session.query(Account.account_id).filter(
        Account.project_id == project_id
    ).subquery()

    now = datetime.now()
    # Load the rows so the update/delete goes through the session (audit events)
    account_users = session.query(AccountUser).filter(
        AccountUser.account_id.in_(select(account_ids)),
        AccountUser.user_id == user_id,
        _unended(now),
    ).all()

    for account_user in account_users:
        _end_membership(session, account_user, now)

    # Clear admin role if they had it
    if project.project_admin_user_id == user_id:
        project.project_admin_user_id = None

    # Flush changes but do not commit
    # Caller is responsible for committing
    session.flush()


def change_project_admin(
    session: Session,
    project_id: int,
    new_admin_user_id: Optional[int]
) -> None:
    """
    Change the project admin to a different user.

    The new admin must hold an unended row on the project, or be the lead;
    ``Project.update`` then gives them a live row on every account. An
    ex-member whose rows have all ended must be re-added first.

    NOTE: This function does NOT commit the session. The caller is responsible
    for calling session.commit() or session.flush() as appropriate.

    Args:
        session: SQLAlchemy session
        project_id: Project ID
        new_admin_user_id: User ID for new admin, or None to clear admin

    Raises:
        ValueError: If new admin is not a project member
    """
    project = session.get(Project, project_id)

    if not project:
        raise ValueError(f"Project {project_id} not found")

    if new_admin_user_id:
        # Ensure new admin holds an unended row on the project (on ANY account)
        member = session.query(AccountUser).join(Account).filter(
            Account.project_id == project_id,
            AccountUser.user_id == new_admin_user_id,
            _unended(datetime.now()),
        ).first()

        # Also allow if they are the lead
        if not member and project.project_lead_user_id != new_admin_user_id:
            raise ValueError("User must be a project member before becoming admin")

    if new_admin_user_id:
        project.update(project_admin_user_id=new_admin_user_id)
    else:
        # update() reads None as "no change", so clearing stays a direct write.
        project.project_admin_user_id = None

    # Flush changes but do not commit
    # Caller is responsible for committing
    session.flush()


def grant_user_resource_access(
    session: Session,
    project_id: int,
    user_id: int,
    resource_id: int,
) -> None:
    """
    Grant a single user access to a single project resource.

    Operator-level repair for partial-access errors: adds one open-ended
    AccountUser row on the (project, resource) account if the user does not
    already have currently-active access there. Idempotent.

    NOTE: This function does NOT commit the session. The caller is responsible
    for wrapping it in management_transaction().

    Args:
        session: SQLAlchemy session
        project_id: Project ID
        user_id: User ID to grant access
        resource_id: Resource ID whose project account the user should join

    Raises:
        ValueError: If the project has no (non-deleted) account for the resource
    """
    account = Account.get_by_project_and_resource(session, project_id, resource_id)
    if account is None:
        raise ValueError(
            f"Project {project_id} has no account for resource {resource_id}"
        )

    existing = session.query(AccountUser).filter(
        AccountUser.account_id == account.account_id,
        AccountUser.user_id == user_id,
        AccountUser.is_active,
    ).first()

    if existing is None:
        # Floor to the second: MySQL DATETIME has no fractional part and
        # rounds half-up, which can push a microsecond-stamped "now" into the
        # next second and make the just-granted row briefly is_active=False.
        session.add(AccountUser(
            account_id=account.account_id,
            user_id=user_id,
            start_date=datetime.now().replace(microsecond=0),
            end_date=None,
        ))

    session.flush()


def revoke_user_resource_access(
    session: Session,
    project_id: int,
    user_id: int,
    resource_id: int,
) -> None:
    """
    Revoke a single user's access to a single project resource.

    Ends the user's unended AccountUser row(s) on the (project, resource)
    account as of now (a row that has not started yet is deleted), mirroring
    remove_user_from_project. The project lead and admin cannot be revoked.

    NOTE: This function does NOT commit the session. The caller is responsible
    for wrapping it in management_transaction().

    Args:
        session: SQLAlchemy session
        project_id: Project ID
        user_id: User ID to revoke
        resource_id: Resource ID whose project account the user should leave

    Raises:
        ValueError: If the user is the project lead or admin, or no account exists
    """
    project = session.get(Project, project_id)
    if not project:
        raise ValueError(f"Project {project_id} not found")

    if project.project_lead_user_id == user_id:
        raise ValueError("Cannot revoke the project lead's access")
    if project.project_admin_user_id == user_id:
        raise ValueError("Cannot revoke the project admin's access")

    account = Account.get_by_project_and_resource(session, project_id, resource_id)
    if account is None:
        raise ValueError(
            f"Project {project_id} has no account for resource {resource_id}"
        )

    now = datetime.now()
    account_users = session.query(AccountUser).filter(
        AccountUser.account_id == account.account_id,
        AccountUser.user_id == user_id,
        _unended(now),
    ).all()

    for account_user in account_users:
        _end_membership(session, account_user, now)

    session.flush()


def reconcile_project_access(session: Session, project_id: int, *,
                             lead_admin_only: bool = False) -> list[AccountUser]:
    """
    Give active project members access to every project resource that is still active.

    Covers the non-deleted accounts on an active resource (``Project.live_accounts``).
    By default it re-runs the membership-seeding invariant (``Account._seed_members``)
    on each, so the lead, admin and every open-ended member hold a row everywhere.
    ``lead_admin_only`` seeds just the lead and admin (``Project.ensure_members``).
    Users who are not ``User.is_active`` (inactive or locked) are skipped. Only adds
    access; never revokes. Returns the AccountUser rows it added.

    NOTE: This function does NOT commit the session. The caller is responsible
    for wrapping it in management_transaction().

    Raises:
        ValueError: If the project does not exist
    """
    project = session.get(Project, project_id)
    if not project:
        raise ValueError(f"Project {project_id} not found")

    if lead_admin_only:
        ids = [uid for uid in (project.project_lead_user_id, project.project_admin_user_id)
               if uid is not None]
        return project.ensure_members(*ids, active_users_only=True)

    added = []
    for account in project.live_accounts:
        added.extend(Account._seed_members(session, account, active_users_only=True))

    session.flush()
    return added
