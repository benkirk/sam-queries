from typing import List, Dict, Optional, Any
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.orm import Session

from sam.enums import ChargeType
from sam.summaries.comp_summaries import CompChargeSummary
from sam.summaries.dav_summaries import DavChargeSummary
from sam.summaries.hpc_summaries import HPCChargeSummary
from sam.summaries.disk_summaries import DiskChargeSummary
from sam.summaries.archive_summaries import ArchiveChargeSummary

# Map generic charge keys to their SQLAlchemy models
CHARGE_MODELS_BY_KEY = {
    ChargeType.HPC: HPCChargeSummary,
    ChargeType.COMP: CompChargeSummary,
    ChargeType.DAV: DavChargeSummary,
    ChargeType.DISK: DiskChargeSummary,
    ChargeType.ARCHIVE: ArchiveChargeSummary,
}

# A resource's charges live in exactly ONE table, named by its activity_type —
# the system of record (legacy routes by activity_type, one table per resource).
# Summing comp+dav for a resource_type double-counts a resource whose history was
# written to both tables (e.g. Casper: comp+dav mirror 2019-2022) and misses
# hpc_charge_summary for legacy HPC machines. Route by activity_type instead.
ACTIVITY_TYPE_TO_CHARGE_KEY = {
    'HPC':     ChargeType.HPC,
    'COMP':    ChargeType.COMP,
    'DAV':     ChargeType.DAV,
    'DISK':    ChargeType.DISK,
    'ARCHIVE': ChargeType.ARCHIVE,
}

# activity_type values that accrue per-job compute charges (num_jobs/core_hours)
COMPUTE_ACTIVITY_TYPES = frozenset({'HPC', 'COMP', 'DAV'})


def get_charge_models_for_activity(activity_type: Optional[str]) -> Dict[str, Any]:
    """Return {charge_key: Model} for a resource's activity_type (all models if None)."""
    if activity_type is None:
        return CHARGE_MODELS_BY_KEY.copy()
    key = ACTIVITY_TYPE_TO_CHARGE_KEY.get(activity_type)
    return {key: CHARGE_MODELS_BY_KEY[key]} if key is not None else {}


def get_compute_charge_model_for_activity(activity_type: Optional[str]):
    """The single summary model carrying job stats for a compute activity_type, else None."""
    if activity_type in COMPUTE_ACTIVITY_TYPES:
        return CHARGE_MODELS_BY_KEY[ACTIVITY_TYPE_TO_CHARGE_KEY[activity_type]]
    return None


def calculate_charges(session: Session,
                      account_ids: List[int],
                      start_date: datetime,
                      end_date: datetime,
                      activity_type: Optional[str]) -> Dict[str, float]:
    """
    Sum charges from the resource's authoritative table (by activity_type).
    Returns breakdown by charge key, only keys with non-zero values.
    """
    totals = {}
    models = get_charge_models_for_activity(activity_type)

    for key, model in models.items():
        val = session.query(func.coalesce(func.sum(model.charges), 0))\
            .filter(
                model.account_id.in_(account_ids),
                model.activity_date >= start_date,
                model.activity_date <= end_date
            ).scalar()

        # replicate projects.py behavior: only add if truthy (non-zero)
        if val:
            totals[key] = float(val)

    return totals


def calculate_total_charges(session: Session,
                            account_ids: List[int],
                            start_date: datetime,
                            end_date: datetime,
                            activity_type: Optional[str]) -> float:
    """
    Sum charges from the resource's authoritative table (by activity_type).
    Returns a single float value.
    """
    charges = calculate_charges(session, account_ids, start_date, end_date, activity_type)
    return sum(charges.values())
