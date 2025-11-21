"""
Project and user management functions.

Administrative operations for managing project membership, roles, etc.
These are write operations that modify the database, as opposed to
read-only query functions in sam.queries.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import select

from sam.accounting.accounts import Account, AccountUser
from sam.projects.projects import Project


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

    Args:
        session: SQLAlchemy session
        project_id: Project ID
        user_id: User ID to add
        start_date: Start date for membership (defaults to now if not provided)
        end_date: End date for membership (optional, defaults to None/no end date)

    Raises:
        ValueError: If user is already a member of any account
    """
    # Default start_date to now if not provided
    if start_date is None:
        start_date = datetime.now()

    accounts = session.query(Account).filter(
        Account.project_id == project_id,
        Account.deleted == False
    ).all()

    if not accounts:
        raise ValueError(f"No accounts found for project {project_id}")

    for account in accounts:
        # Check if already exists
        existing = session.query(AccountUser).filter(
            AccountUser.account_id == account.account_id,
            AccountUser.user_id == user_id
        ).first()

        if not existing:
            account_user = AccountUser(
                account_id=account.account_id,
                user_id=user_id,
                start_date=start_date,
                end_date=end_date  # Can be None
            )
            session.add(account_user)

    session.commit()


def remove_user_from_project(session: Session, project_id: int, user_id: int) -> None:
    """
    Remove a user from all accounts in a project.

    Also clears the admin role if the user being removed is the project admin.
    Cannot remove the project lead.

    Args:
        session: SQLAlchemy session
        project_id: Project ID
        user_id: User ID to remove

    Raises:
        ValueError: If trying to remove the project lead
    """
    # Get project to check lead/admin
    project = session.query(Project).get(project_id)

    if not project:
        raise ValueError(f"Project {project_id} not found")

    # Cannot remove the lead
    if project.project_lead_user_id == user_id:
        raise ValueError("Cannot remove the project lead")

    # Get all account IDs for this project
    account_ids = session.query(Account.account_id).filter(
        Account.project_id == project_id
    ).subquery()

    # Remove from all accounts
    session.query(AccountUser).filter(
        AccountUser.account_id.in_(select(account_ids)),
        AccountUser.user_id == user_id
    ).delete(synchronize_session=False)

    # Clear admin role if they had it
    if project.project_admin_user_id == user_id:
        project.project_admin_user_id = None

    session.commit()


def change_project_admin(
    session: Session,
    project_id: int,
    new_admin_user_id: Optional[int]
) -> None:
    """
    Change the project admin to a different user.

    The new admin must already be a member of the project (unless clearing admin).

    Args:
        session: SQLAlchemy session
        project_id: Project ID
        new_admin_user_id: User ID for new admin, or None to clear admin

    Raises:
        ValueError: If new admin is not a project member
    """
    project = session.query(Project).get(project_id)

    if not project:
        raise ValueError(f"Project {project_id} not found")

    if new_admin_user_id:
        # Ensure new admin is a member of the project (on ANY account)
        member = session.query(AccountUser).join(Account).filter(
            Account.project_id == project_id,
            AccountUser.user_id == new_admin_user_id
        ).first()

        # Also allow if they are the lead
        if not member and project.project_lead_user_id != new_admin_user_id:
            raise ValueError("User must be a project member before becoming admin")

    project.project_admin_user_id = new_admin_user_id
    session.commit()
