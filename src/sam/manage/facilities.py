"""
Facility management functions.

Administrative operations for updating Facilities, Panels, PanelSessions,
and AllocationTypes. These are write operations that modify the database,
as opposed to read-only query functions in sam.queries.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from sam.resources.facilities import Facility, Panel, PanelSession
from sam.accounting.allocations import AllocationType


def update_facility(
    session: Session,
    facility_id: int,
    *,
    description: Optional[str] = None,
    fair_share_percentage: Optional[float] = None,
    active: Optional[bool] = None,
) -> Facility:
    """
    Update an existing Facility record.

    NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

    Args:
        session: SQLAlchemy session
        facility_id: ID of the facility to update
        description: New description (NOT NULL column — empty string stored as '')
        fair_share_percentage: Percentage 0–100 (optional, pass None to leave unchanged)
        active: Whether the facility is active

    Returns:
        The updated Facility object

    Raises:
        ValueError: If facility not found or validation fails
    """
    facility = session.get(Facility, facility_id)
    if not facility:
        raise ValueError(f"Facility {facility_id} not found")

    if description is not None:
        # description is NOT NULL in schema — store empty string rather than None
        facility.description = description.strip()

    if fair_share_percentage is not None:
        if not (0 <= fair_share_percentage <= 100):
            raise ValueError("fair_share_percentage must be between 0 and 100")
        facility.fair_share_percentage = fair_share_percentage

    if active is not None:
        facility.active = active

    session.flush()
    return facility


def update_panel(
    session: Session,
    panel_id: int,
    *,
    description: Optional[str] = None,
    active: Optional[bool] = None,
) -> Panel:
    """
    Update an existing Panel record.

    NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

    Args:
        session: SQLAlchemy session
        panel_id: ID of the panel to update
        description: New description (nullable — pass empty string to clear)
        active: Whether the panel is active

    Returns:
        The updated Panel object

    Raises:
        ValueError: If panel not found
    """
    panel = session.get(Panel, panel_id)
    if not panel:
        raise ValueError(f"Panel {panel_id} not found")

    if description is not None:
        panel.description = description if description.strip() else None

    if active is not None:
        panel.active = active

    session.flush()
    return panel


def update_panel_session(
    session: Session,
    panel_session_id: int,
    *,
    description: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    panel_meeting_date: Optional[datetime] = None,
) -> PanelSession:
    """
    Update an existing PanelSession record.

    NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

    Args:
        session: SQLAlchemy session
        panel_session_id: ID of the panel session to update
        description: New description (nullable)
        start_date: New start date (required on creation, updatable here)
        end_date: New end date — must be after start_date if both known
        panel_meeting_date: New panel meeting date (no constraint)

    Returns:
        The updated PanelSession object

    Raises:
        ValueError: If panel session not found or end_date <= start_date
    """
    panel_session = session.get(PanelSession, panel_session_id)
    if not panel_session:
        raise ValueError(f"PanelSession {panel_session_id} not found")

    if start_date is not None:
        panel_session.start_date = start_date

    if end_date is not None:
        effective_start = start_date or panel_session.start_date
        if effective_start and end_date <= effective_start:
            raise ValueError("end_date must be after start_date")
        panel_session.end_date = end_date

    if panel_meeting_date is not None:
        panel_session.panel_meeting_date = panel_meeting_date

    if description is not None:
        panel_session.description = description if description.strip() else None

    session.flush()
    return panel_session


def update_allocation_type(
    session: Session,
    allocation_type_id: int,
    *,
    default_allocation_amount: Optional[float] = None,
    fair_share_percentage: Optional[float] = None,
    active: Optional[bool] = None,
) -> AllocationType:
    """
    Update an existing AllocationType record.

    NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

    Args:
        session: SQLAlchemy session
        allocation_type_id: ID of the allocation type to update
        default_allocation_amount: New default amount (must be >= 0 if provided)
        fair_share_percentage: Percentage 0–100 (optional)
        active: Whether the allocation type is active

    Returns:
        The updated AllocationType object

    Raises:
        ValueError: If allocation type not found or validation fails
    """
    allocation_type = session.get(AllocationType, allocation_type_id)
    if not allocation_type:
        raise ValueError(f"AllocationType {allocation_type_id} not found")

    if default_allocation_amount is not None:
        if default_allocation_amount < 0:
            raise ValueError("default_allocation_amount must be >= 0")
        allocation_type.default_allocation_amount = default_allocation_amount

    if fair_share_percentage is not None:
        if not (0 <= fair_share_percentage <= 100):
            raise ValueError("fair_share_percentage must be between 0 and 100")
        allocation_type.fair_share_percentage = fair_share_percentage

    if active is not None:
        allocation_type.active = active

    session.flush()
    return allocation_type
