from typing import List, Dict, Type, Optional, Any
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.orm import Session

from sam.summaries.comp_summaries import CompChargeSummary
from sam.summaries.dav_summaries import DavChargeSummary
from sam.summaries.disk_summaries import DiskChargeSummary
from sam.summaries.archive_summaries import ArchiveChargeSummary

# Map generic charge keys to their SQLAlchemy models
CHARGE_MODELS_BY_KEY = {
    'comp': CompChargeSummary,
    'dav': DavChargeSummary,
    'disk': DiskChargeSummary,
    'archive': ArchiveChargeSummary,
}

# Map resource types to the keys of charges they accumulate
# Note: Matching src/sam/projects/projects.py behavior where HPC and DAV check both comp and dav
RESOURCE_TYPE_TO_CHARGE_KEYS = {
    'HPC': ['comp', 'dav'],
    'DAV': ['comp', 'dav'],
    'DISK': ['disk'],
    'ARCHIVE': ['archive'],
}

def get_charge_models_for_resource(resource_type: Optional[str]) -> Dict[str, Any]:
    """
    Return the dict of {charge_key: Model} for a given resource type.
    If resource_type is None, returns all models.
    """
    if resource_type is None:
        return CHARGE_MODELS_BY_KEY.copy()
        
    keys = RESOURCE_TYPE_TO_CHARGE_KEYS.get(resource_type, [])
    return {k: CHARGE_MODELS_BY_KEY[k] for k in keys}

def calculate_charges(session: Session, 
                      account_ids: List[int], 
                      start_date: datetime, 
                      end_date: datetime, 
                      resource_type: str) -> Dict[str, float]:
    """
    Sum charges across all applicable tables for the given resource type.
    Returns breakdown by charge key.
    Only includes keys with non-zero values.
    """
    totals = {}
    models = get_charge_models_for_resource(resource_type)
    
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
                            resource_type: str) -> float:
    """
    Sum charges across all applicable tables for the given resource type.
    Returns a single float value.
    """
    charges = calculate_charges(session, account_ids, start_date, end_date, resource_type)
    return sum(charges.values())
