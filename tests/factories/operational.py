"""Factories for operational-domain entities: WallclockExemption."""
from datetime import datetime, timedelta
from typing import Optional

from sam import WallclockExemption
from sam.core.users import User
from sam.resources.machines import Queue

from .core import make_user
from .resources import make_queue


def make_wallclock_exemption(
    session,
    *,
    user: Optional[User] = None,
    queue: Optional[Queue] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    time_limit_hours: float = 24.0,
    comment: Optional[str] = None,
) -> WallclockExemption:
    """Build and flush a fresh WallclockExemption.

    Auto-builds a User and Queue if not supplied. Delegates to the model's
    own `WallclockExemption.create()` classmethod so its validation runs.
    """
    if user is None:
        user = make_user(session)
    if queue is None:
        queue = make_queue(session)
    if start_date is None:
        start_date = datetime.now() - timedelta(days=1)
    if end_date is None:
        end_date = datetime.now() + timedelta(days=30)

    return WallclockExemption.create(
        session,
        user.user_id,
        queue.queue_id,
        start_date,
        end_date,
        time_limit_hours,
        comment=comment,
    )
