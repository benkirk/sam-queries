"""
Admin-specific query functions.

Centralizes the heavy selectinload/subqueryload chains used by the admin
HTMX card endpoints, keeping the view layer thin.
"""

from sqlalchemy.orm import subqueryload, selectinload, lazyload, joinedload


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
    """Load all institution types, ordered by type name.

    Used by the admin Institutions tab to label/group institutions by type.
    The caller provides the Institution rows separately (from
    ``get_institutions_with_members``) and joins them to types in Python,
    so no nested eager loads are needed here.
    """
    from sam.core.organizations import InstitutionType

    return session.query(InstitutionType).order_by(InstitutionType.type).all()


def get_institutions_with_members(session, *, country_id=None, state_prov_id=None,
                                  active_only=False, include_projects=False):
    """Load institutions, optionally filtered by geography and membership status.

    Used by the admin organizations card (Institutions tab). Eagerly loads
    ``state_prov`` and ``state_prov.country`` so the Location column can render
    without per-row lazy queries.

    Args:
        country_id: filter to institutions whose state_prov belongs to this
            country. Ignored when ``state_prov_id`` is given.
        state_prov_id: filter to institutions with this state/province.
        active_only: if True, limit results to institutions that have at
            least one currently-active ``UserInstitution`` linked to an
            active ``User`` (EXISTS subquery).
        include_projects: if True, eager-load each member user plus their
            ``led_projects`` and ``admin_projects`` so the view can render
            user/project chips without N+1 queries. When False, the
            ``users`` relationship is left lazy — the default view doesn't
            touch it, so no extra queries fire.

    Returns:
        list of Institution ordered by name
    """
    from sam.core.organizations import Institution, UserInstitution
    from sam.core.users import User
    from sam.projects.projects import Project
    from sam.geography import StateProv

    q = session.query(Institution).options(
        joinedload(Institution.state_prov).joinedload(StateProv.country),
    )
    if include_projects:
        q = q.options(
            selectinload(Institution.users)
                .selectinload(UserInstitution.user)
                .lazyload(User.accounts),
            selectinload(Institution.users)
                .selectinload(UserInstitution.user)
                .lazyload(User.email_addresses),
            selectinload(Institution.users)
                .selectinload(UserInstitution.user)
                .selectinload(User.led_projects)
                .lazyload(Project.accounts),
            selectinload(Institution.users)
                .selectinload(UserInstitution.user)
                .selectinload(User.admin_projects)
                .lazyload(Project.accounts),
        )

    if state_prov_id:
        q = q.filter(Institution.state_prov_id == state_prov_id)
    elif country_id:
        q = q.join(StateProv, Institution.state_prov_id == StateProv.ext_state_prov_id) \
             .filter(StateProv.ext_country_id == country_id)

    if active_only:
        q = q.filter(
            session.query(UserInstitution)
                .join(User, User.user_id == UserInstitution.user_id)
                .filter(UserInstitution.institution_id == Institution.institution_id)
                .filter(UserInstitution.is_active)
                .filter(User.is_active)
                .exists()
        )

    return q.order_by(Institution.name).all()


def get_countries_with_institutions(session):
    """Return distinct Country rows that have at least one linked institution.

    Used to populate the Country filter dropdown — skips countries with no
    institutions so the dropdown stays short.

    Returns:
        list of Country ordered by name
    """
    from sam.core.organizations import Institution
    from sam.geography import Country, StateProv

    return (
        session.query(Country)
        .join(StateProv, StateProv.ext_country_id == Country.ext_country_id)
        .join(Institution, Institution.state_prov_id == StateProv.ext_state_prov_id)
        .distinct()
        .order_by(Country.name)
        .all()
    )


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


def get_contracts_with_pi(session, active_only=False, with_source=False):
    """Load all contracts with the people and program the card displays.

    The monitor and NSF program are eager-loaded for the same reason the PI
    is: the Organizations card renders ~2,200 contract rows, so a lazy load
    per row is 2,200 extra queries. The ``lazyload`` guards keep the user
    loads from dragging in accounts and email addresses the card never shows.

    Args:
        active_only: restrict to contracts inside their date window.
        with_source: also eager-load ``contract_source``. Off by default
            because the Organizations card does not show it; the contract
            audit (``sam.queries.contract_audit``) needs it, since several
            of its checks only apply to ``contract_source = 'NSF'``. One
            extra ``selectin`` query against a 21-row lookup table.

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
        selectinload(Contract.contract_monitor)
            .lazyload(User.accounts),
        selectinload(Contract.contract_monitor)
            .lazyload(User.email_addresses),
        selectinload(Contract.nsf_program),
    ).order_by(Contract.contract_number)
    if with_source:
        q = q.options(selectinload(Contract.contract_source))
    if active_only:
        q = q.filter(Contract.is_active)
    return q.all()


def get_contract_detail(session, contract_id):
    """Load one contract with everything its detail card renders.

    Keyed by id rather than number: contract numbers are free text and
    include values like ``USDA Prime Award No. 2013-67003-20652`` and
    ``OCE- 1419584``, so unlike ``username`` / ``projcode`` they cannot key
    a URL path.

    The linked-project chain is the reason this is a separate loader from
    ``get_contracts_with_pi`` — that one deliberately skips the source and
    the project graph because the list view shows neither, and pulling them
    for 2,200 rows would be expensive.

    Returns:
        Contract, or None if no such row.
    """
    from sam.projects.contracts import Contract, ProjectContract
    from sam.core.users import User

    return (
        session.query(Contract)
        .options(
            selectinload(Contract.contract_source),
            selectinload(Contract.nsf_program),
            selectinload(Contract.principal_investigator)
                .lazyload(User.accounts),
            selectinload(Contract.principal_investigator)
                .lazyload(User.email_addresses),
            selectinload(Contract.contract_monitor)
                .lazyload(User.accounts),
            selectinload(Contract.contract_monitor)
                .lazyload(User.email_addresses),
            selectinload(Contract.projects)
                .joinedload(ProjectContract.project),
        )
        .filter(Contract.contract_id == contract_id)
        .first()
    )


def get_nsf_program_contracts(session, nsf_program_id):
    """Load one NSF program and its contracts, for the drill-down modal.

    ``get_nsf_programs_with_contracts`` loads bare contracts, which is fine
    for a count but would N+1 here: the largest program carries 401
    contracts and the modal shows each one's source and PI.

    Returns:
        (NSFProgram, list of Contract ordered by number) — (None, []) when
        no such program.
    """
    from sam.projects.contracts import Contract, NSFProgram
    from sam.core.users import User

    program = session.get(NSFProgram, nsf_program_id)
    if program is None:
        return None, []

    contracts = (
        session.query(Contract)
        .options(
            selectinload(Contract.contract_source),
            selectinload(Contract.principal_investigator)
                .lazyload(User.accounts),
            selectinload(Contract.principal_investigator)
                .lazyload(User.email_addresses),
        )
        .filter(Contract.nsf_program_id == nsf_program_id)
        .order_by(Contract.contract_number)
        .all()
    )
    return program, contracts


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
