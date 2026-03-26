"""
Wallclock exemption management functions.

Administrative operations for creating and updating per-user wallclock
time-limit exemptions on queues.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from sam.operational import WallclockExemption


def create_wallclock_exemption(
    session: Session,
    user_id: int,
    queue_id: int,
    start_date: datetime,
    end_date: datetime,
    time_limit_hours: float,
    comment: Optional[str] = None
) -> WallclockExemption:
    """
    Create a wallclock time-limit exemption for a user on a queue.

    NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

    Args:
        session: SQLAlchemy session
        user_id: User ID receiving the exemption
        queue_id: Queue ID the exemption applies to
        start_date: When the exemption becomes active
        end_date: When the exemption expires
        time_limit_hours: Maximum wallclock hours allowed
        comment: Optional notes

    Returns:
        The newly created WallclockExemption object

    Raises:
        ValueError: If dates are invalid or time_limit_hours <= 0
    """
    if end_date <= start_date:
        raise ValueError("end_date must be after start_date")
    if time_limit_hours <= 0:
        raise ValueError("time_limit_hours must be positive")

    exemption = WallclockExemption(
        user_id=user_id,
        queue_id=queue_id,
        start_date=start_date,
        end_date=end_date,
        time_limit_hours=time_limit_hours,
        comment=comment or None
    )
    session.add(exemption)
    session.flush()
    return exemption


def update_wallclock_exemption(
    session: Session,
    exemption_id: int,
    end_date: Optional[datetime] = None,
    time_limit_hours: Optional[float] = None,
    comment: Optional[str] = None
) -> WallclockExemption:
    """
    Update an existing wallclock exemption.

    Only end_date, time_limit_hours, and comment may be changed.
    NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

    Args:
        session: SQLAlchemy session
        exemption_id: ID of the exemption to update
        end_date: New end date (must be after start_date)
        time_limit_hours: New hour limit (must be positive)
        comment: New comment (pass empty string to clear)

    Returns:
        The updated WallclockExemption object

    Raises:
        ValueError: If exemption not found or validation fails
    """
    exemption = session.get(WallclockExemption, exemption_id)
    if not exemption:
        raise ValueError(f"WallclockExemption {exemption_id} not found")

    if end_date is not None:
        if end_date <= exemption.start_date:
            raise ValueError("end_date must be after start_date")
        exemption.end_date = end_date

    if time_limit_hours is not None:
        if time_limit_hours <= 0:
            raise ValueError("time_limit_hours must be positive")
        exemption.time_limit_hours = time_limit_hours

    if comment is not None:
        exemption.comment = comment if comment.strip() else None

    session.flush()
    return exemption
