"""
Organization management functions.

Administrative operations for updating Organizations, Institutions,
AreaOfInterestGroups, AreasOfInterest, ContractSources, Contracts,
and NSFPrograms. These are write operations that modify the database,
as opposed to read-only query functions in sam.queries.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from sam.core.organizations import Organization, Institution
from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup
from sam.projects.contracts import Contract, ContractSource, NSFProgram


def update_organization(
    session: Session,
    org_id: int,
    *,
    name: Optional[str] = None,
    acronym: Optional[str] = None,
    description: Optional[str] = None,
    active: Optional[bool] = None,
) -> Organization:
    """
    Update an existing Organization record.

    NOTE: Does NOT commit. Caller must use management_transaction or commit manually.
    NOTE: Never touches tree columns (tree_left, tree_right, level, level_code,
          parent_org_id) — those are managed by the NestedSetMixin and must not
          be modified directly.

    Args:
        session: SQLAlchemy session
        org_id: ID of the organization to update
        name: New name (NOT NULL)
        acronym: New acronym (NOT NULL, unique)
        description: New description (nullable — pass empty string to clear)
        active: Whether the organization is active

    Returns:
        The updated Organization object

    Raises:
        ValueError: If organization not found or required fields are empty
    """
    org = session.get(Organization, org_id)
    if not org:
        raise ValueError(f"Organization {org_id} not found")

    if name is not None:
        if not name.strip():
            raise ValueError("name is required")
        org.name = name.strip()

    if acronym is not None:
        if not acronym.strip():
            raise ValueError("acronym is required")
        org.acronym = acronym.strip()

    if description is not None:
        org.description = description.strip() if description.strip() else None

    if active is not None:
        org.active = active

    session.flush()
    return org


def update_institution(
    session: Session,
    inst_id: int,
    *,
    name: Optional[str] = None,
    acronym: Optional[str] = None,
    nsf_org_code: Optional[str] = None,
    address: Optional[str] = None,
    city: Optional[str] = None,
    zip: Optional[str] = None,
    code: Optional[str] = None,
    institution_type_id: Optional[int] = None,
) -> Institution:
    """
    Update an existing Institution record.

    NOTE: Does NOT commit. Caller must use management_transaction or commit manually.
    NOTE: Institution has no active flag.

    Args:
        session: SQLAlchemy session
        inst_id: ID of the institution to update
        name: New name (NOT NULL)
        acronym: New acronym (NOT NULL)
        nsf_org_code: NSF organization code (nullable)
        address: Street address (nullable)
        city: City (nullable)
        zip: ZIP/postal code (nullable)
        code: Short code (nullable, max 3 chars)
        institution_type_id: FK to institution_type (nullable)

    Returns:
        The updated Institution object

    Raises:
        ValueError: If institution not found or required fields are empty
    """
    inst = session.get(Institution, inst_id)
    if not inst:
        raise ValueError(f"Institution {inst_id} not found")

    if name is not None:
        if not name.strip():
            raise ValueError("name is required")
        inst.name = name.strip()

    if acronym is not None:
        if not acronym.strip():
            raise ValueError("acronym is required")
        inst.acronym = acronym.strip()

    if nsf_org_code is not None:
        inst.nsf_org_code = nsf_org_code.strip() if nsf_org_code.strip() else None

    if address is not None:
        inst.address = address.strip() if address.strip() else None

    if city is not None:
        inst.city = city.strip() if city.strip() else None

    if zip is not None:
        inst.zip = zip.strip() if zip.strip() else None

    if code is not None:
        inst.code = code.strip() if code.strip() else None

    if institution_type_id is not None:
        inst.institution_type_id = institution_type_id

    session.flush()
    return inst


def update_area_of_interest_group(
    session: Session,
    group_id: int,
    *,
    name: Optional[str] = None,
    active: Optional[bool] = None,
) -> AreaOfInterestGroup:
    """
    Update an existing AreaOfInterestGroup record.

    NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

    Args:
        session: SQLAlchemy session
        group_id: ID of the AOI group to update
        name: New name (NOT NULL, unique)
        active: Whether the group is active

    Returns:
        The updated AreaOfInterestGroup object

    Raises:
        ValueError: If group not found or name is empty
    """
    group = session.get(AreaOfInterestGroup, group_id)
    if not group:
        raise ValueError(f"AreaOfInterestGroup {group_id} not found")

    if name is not None:
        if not name.strip():
            raise ValueError("name is required")
        group.name = name.strip()

    if active is not None:
        group.active = active

    session.flush()
    return group


def update_area_of_interest(
    session: Session,
    aoi_id: int,
    *,
    area_of_interest: Optional[str] = None,
    area_of_interest_group_id: Optional[int] = None,
    active: Optional[bool] = None,
) -> AreaOfInterest:
    """
    Update an existing AreaOfInterest record.

    NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

    Args:
        session: SQLAlchemy session
        aoi_id: ID of the area of interest to update
        area_of_interest: New name (NOT NULL, unique)
        area_of_interest_group_id: FK to AreaOfInterestGroup (NOT NULL)
        active: Whether the area is active

    Returns:
        The updated AreaOfInterest object

    Raises:
        ValueError: If AOI not found, name is empty, or group does not exist
    """
    aoi = session.get(AreaOfInterest, aoi_id)
    if not aoi:
        raise ValueError(f"AreaOfInterest {aoi_id} not found")

    if area_of_interest is not None:
        if not area_of_interest.strip():
            raise ValueError("area_of_interest name is required")
        aoi.area_of_interest = area_of_interest.strip()

    if area_of_interest_group_id is not None:
        group = session.get(AreaOfInterestGroup, area_of_interest_group_id)
        if not group:
            raise ValueError(f"AreaOfInterestGroup {area_of_interest_group_id} not found")
        aoi.area_of_interest_group_id = area_of_interest_group_id

    if active is not None:
        aoi.active = active

    session.flush()
    return aoi


def update_contract_source(
    session: Session,
    source_id: int,
    *,
    contract_source: Optional[str] = None,
    active: Optional[bool] = None,
) -> ContractSource:
    """
    Update an existing ContractSource record.

    NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

    Args:
        session: SQLAlchemy session
        source_id: ID of the contract source to update
        contract_source: New source name (NOT NULL, unique)
        active: Whether the source is active

    Returns:
        The updated ContractSource object

    Raises:
        ValueError: If source not found or name is empty
    """
    source = session.get(ContractSource, source_id)
    if not source:
        raise ValueError(f"ContractSource {source_id} not found")

    if contract_source is not None:
        if not contract_source.strip():
            raise ValueError("contract_source name is required")
        source.contract_source = contract_source.strip()

    if active is not None:
        source.active = active

    session.flush()
    return source


def update_contract(
    session: Session,
    contract_id: int,
    *,
    title: Optional[str] = None,
    url: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> Contract:
    """
    Update an existing Contract record.

    Only title, url, start_date, and end_date may be changed.
    PI, contract monitor, source, and number are read-only via this function.

    NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

    Args:
        session: SQLAlchemy session
        contract_id: ID of the contract to update
        title: New title (NOT NULL)
        url: New URL (nullable — pass empty string to clear)
        start_date: New start date (NOT NULL)
        end_date: New end date — must be after start_date if both known

    Returns:
        The updated Contract object

    Raises:
        ValueError: If contract not found or validation fails
    """
    contract = session.get(Contract, contract_id)
    if not contract:
        raise ValueError(f"Contract {contract_id} not found")

    if title is not None:
        if not title.strip():
            raise ValueError("title is required")
        contract.title = title.strip()

    if url is not None:
        contract.url = url.strip() if url.strip() else None

    if start_date is not None:
        contract.start_date = start_date

    if end_date is not None:
        effective_start = start_date or contract.start_date
        if effective_start and end_date <= effective_start:
            raise ValueError("end_date must be after start_date")
        contract.end_date = end_date

    session.flush()
    return contract


def update_nsf_program(
    session: Session,
    nsf_program_id: int,
    *,
    nsf_program_name: Optional[str] = None,
    active: Optional[bool] = None,
) -> NSFProgram:
    """
    Update an existing NSFProgram record.

    NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

    Args:
        session: SQLAlchemy session
        nsf_program_id: ID of the NSF program to update
        nsf_program_name: New program name (NOT NULL, unique)
        active: Whether the program is active

    Returns:
        The updated NSFProgram object

    Raises:
        ValueError: If NSF program not found or name is empty
    """
    program = session.get(NSFProgram, nsf_program_id)
    if not program:
        raise ValueError(f"NSFProgram {nsf_program_id} not found")

    if nsf_program_name is not None:
        if not nsf_program_name.strip():
            raise ValueError("nsf_program_name is required")
        program.nsf_program_name = nsf_program_name.strip()

    if active is not None:
        program.active = active

    session.flush()
    return program
