# Refactoring Plan: Centralized Charge Registry

## Problem Statement
The logic for calculating resource charges is currently duplicated across the codebase, specifically in:
- `src/sam/projects/projects.py` (Project model methods)
- `src/sam/queries/charges.py` (Dashboard and reporting queries)
- `src/sam/queries/allocations.py` (CLI usage reporting)

This duplication leads to:
1.  **Maintenance Overhead**: Adding a new resource type requires changes in multiple files.
2.  **Inconsistency Risk**: Different parts of the application might calculate "Total Usage" differently if one file is updated and another is missed.
3.  **Model Bloat**: The `Project` class is handling complex accounting logic, violating the Single Responsibility Principle.

## Proposed Solution

### 1. Create `src/sam/accounting/calculator.py`
This new module will serve as the single source of truth for mapping Resource Types to Charge Models.

```python
# Prototype for src/sam/accounting/calculator.py

from sam.summaries.comp_summaries import CompChargeSummary
from sam.summaries.dav_summaries import DavChargeSummary
from sam.summaries.disk_summaries import DiskChargeSummary
from sam.summaries.archive_summaries import ArchiveChargeSummary

# Central Registry
CHARGE_MODELS = {
    'HPC': [CompChargeSummary],         # HPC uses Comp summary
    'DAV': [CompChargeSummary, DavChargeSummary], # DAV uses both Comp and its own summary
    'DISK': [DiskChargeSummary],
    'ARCHIVE': [ArchiveChargeSummary]
}

def get_charge_models_for_resource(resource_type: str) -> list:
    """Return the list of SQLAlchemy models that contribute charges to this resource type."""
    return CHARGE_MODELS.get(resource_type, [])
```

### 2. Implement Generic Aggregation Functions
Instead of writing custom queries for each resource type, implement generic aggregators that accept a list of models.

```python
# Prototype for src/sam/accounting/calculator.py

def calculate_total_charges(session, account_ids: list, start_date, end_date, resource_type: str) -> float:
    """
    Sum charges across all applicable tables for the given resource type.
    """
    total = 0.0
    models = get_charge_models_for_resource(resource_type)
    
    for model in models:
        val = session.query(func.coalesce(func.sum(model.charges), 0))\
            .filter(
                model.account_id.in_(account_ids),
                model.activity_date >= start_date,
                model.activity_date <= end_date
            ).scalar()
        total += float(val)
    
    return total
```

### 3. Refactor `Project` Model
Remove the low-level query logic from `src/sam/projects/projects.py`. The `Project` methods should delegate to the calculator.

**Before:**
```python
# src/sam/projects/projects.py
def get_charges_by_resource_type(self, ...):
    if resource_type == 'HPC':
        # ... manual query ...
    elif resource_type == 'DISK':
        # ... manual query ...
```

**After:**
```python
# src/sam/projects/projects.py
from sam.accounting.calculator import calculate_total_charges

def get_charges_by_resource_type(self, account_id, resource_type, start, end):
    return calculate_total_charges(self.session, [account_id], start, end, resource_type)
```

### 4. Refactor Queries
Update `src/sam/queries/charges.py` and `src/sam/queries/allocations.py` to import and use the registry and calculator functions.

## Benefits
- **DRY (Don't Repeat Yourself)**: Resource-to-Table mapping is defined once.
- **Extensibility**: Adding a new resource type (e.g., 'CLOUD') only requires updating the `CHARGE_MODELS` registry.
- **Simplicity**: The `Project` model becomes lighter and focused on entity relationships.

```
