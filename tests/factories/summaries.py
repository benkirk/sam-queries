"""Factories for charge-summary rows.

Only `comp_charge_summary` is covered — it is the sole summary table still
receiving rows (dav/hpc stopped in 2023/2024), and it is what queue-usage
queries read.
"""
from datetime import date, datetime
from typing import Optional, Union

from sam.resources.machines import Queue
from sam.summaries.comp_summaries import CompChargeSummary

from ._seq import next_seq
from .resources import make_queue


def make_comp_charge_summary(
    session,
    *,
    queue: Optional[Queue] = None,
    activity_date: Optional[Union[date, datetime]] = None,
    machine: Optional[str] = None,
    num_jobs: int = 1,
    charges: float = 1.0,
    core_hours: float = 1.0,
) -> CompChargeSummary:
    """Build and flush a CompChargeSummary row, auto-building a Queue if needed.

    Both `queue_id` (the FK the usage queries join on) and `queue` (the
    denormalized name column, NOT NULL) are populated from the same Queue.
    """
    if queue is None:
        queue = make_queue(session)
    if activity_date is None:
        activity_date = date.today()
    if isinstance(activity_date, datetime):
        activity_date = activity_date.date()
    if machine is None:
        machine = next_seq("mach")

    row = CompChargeSummary(
        activity_date=activity_date,
        machine=machine,
        queue=queue.queue_name,
        queue_id=queue.queue_id,
        num_jobs=num_jobs,
        charges=charges,
        core_hours=core_hours,
    )
    session.add(row)
    session.flush()
    return row
