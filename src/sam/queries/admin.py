"""
Admin-specific query functions.

Centralizes the heavy selectinload/subqueryload chains used by the admin
HTMX card endpoints, keeping the view layer thin.
"""

from sqlalchemy.orm import subqueryload, selectinload, lazyload


def get_organizations_with_members(session, active_only=False):
    """Load all organizations with their child orgs and users.

    Used by the admin organizations card.

    Returns:
        list of Organization (with .children and .users eagerly loaded)
    """
    from sam.core.organizations import Organization

    q = session.query(Organization).options(
        subqueryload(Organization.children),
        selectinload(Organization.users),
    )
    if active_only:
        q = q.filter(Organization.is_active)
    return q.all()


def get_institution_type_tree(session):
    """Load all institution types with their institutions and member users.

    Used by the admin organizations card (InstitutionTypes tab).

    Returns:
        list of InstitutionType (with deep .institutions → .users eager loads)
    """
    from sam.core.organizations import InstitutionType, Institution, UserInstitution
    from sam.core.users import User

    return session.query(InstitutionType).options(
        selectinload(InstitutionType.institutions)
            .selectinload(Institution.users)
            .selectinload(UserInstitution.user)
            .lazyload(User.accounts),
        selectinload(InstitutionType.institutions)
            .selectinload(Institution.users)
            .selectinload(UserInstitution.user)
            .lazyload(User.email_addresses),
    ).order_by(InstitutionType.type).all()


def get_institutions_with_members(session):
    """Load all institutions with their member users.

    Used by the admin organizations card (Institutions tab).

    Returns:
        list of Institution ordered by name
    """
    from sam.core.organizations import Institution, UserInstitution
    from sam.core.users import User

    return session.query(Institution).options(
        selectinload(Institution.users)
            .selectinload(UserInstitution.user)
            .lazyload(User.accounts),
        selectinload(Institution.users)
            .selectinload(UserInstitution.user)
            .lazyload(User.email_addresses),
    ).order_by(Institution.name).all()


def get_aoi_groups_with_areas(session, active_only=False):
    """Load all AOI groups with their areas of interest.

    Returns:
        list of AreaOfInterestGroup ordered by name
    """
    from sam.projects.areas import AreaOfInterestGroup

    q = session.query(AreaOfInterestGroup).options(
        selectinload(AreaOfInterestGroup.areas),
    ).order_by(AreaOfInterestGroup.name)
    if active_only:
        q = q.filter(AreaOfInterestGroup.is_active)
    return q.all()


def get_areas_of_interest_with_projects(session, active_only=False):
    """Load all areas of interest with their associated projects.

    Returns:
        list of AreaOfInterest ordered by name
    """
    from sam.projects.areas import AreaOfInterest
    from sam.projects.projects import Project

    q = session.query(AreaOfInterest).options(
        selectinload(AreaOfInterest.projects).lazyload(Project.accounts),
    ).order_by(AreaOfInterest.area_of_interest)
    if active_only:
        q = q.filter(AreaOfInterest.is_active)
    return q.all()


def get_contracts_with_pi(session, active_only=False):
    """Load all contracts with their principal investigator users.

    Returns:
        list of Contract ordered by contract_number
    """
    from sam.projects.contracts import Contract
    from sam.core.users import User

    q = session.query(Contract).options(
        selectinload(Contract.principal_investigator)
            .lazyload(User.accounts),
        selectinload(Contract.principal_investigator)
            .lazyload(User.email_addresses),
    ).order_by(Contract.contract_number)
    if active_only:
        q = q.filter(Contract.is_active)
    return q.all()


def get_nsf_programs_with_contracts(session, active_only=False):
    """Load all NSF programs with their associated contracts.

    Returns:
        list of NSFProgram ordered by name
    """
    from sam.projects.contracts import NSFProgram

    q = session.query(NSFProgram).options(
        selectinload(NSFProgram.contracts),
    ).order_by(NSFProgram.nsf_program_name)
    if active_only:
        q = q.filter(NSFProgram.is_active)
    return q.all()
