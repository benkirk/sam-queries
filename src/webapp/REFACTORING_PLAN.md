# WebUI Refactoring Plan

This document outlines prioritized opportunities for simplification and refactoring to improve code maintainability in the `src/webapp` module.

**Last Updated**: 2025-12-04

---

## Overview

The current codebase has evolved to include a robust set of helpers and architectural patterns. This plan identifies the next set of priorities for refactoring, focusing on areas where further simplification and consolidation can be achieved.

Many items from the previous plan have been successfully implemented, including:
- Centralized API error handlers, response helpers, and lookup patterns in `api/helpers.py`.
- Consolidated access control using decorators in `api/access_control.py`.

This updated plan focuses on the remaining high-impact opportunities.

---

## Priority 1: Centralize Data Access & Business Logic

This is the most critical area for refactoring. The goal is to ensure a clean separation of concerns, where API endpoints and dashboards handle presentation, while a dedicated query layer handles all database interaction and business logic.

### 1.1. Leverage `sam.queries` for all SAM Database Interactions

**Problem**: The charges API endpoint (`api/v1/charges.py`) contains over 100 lines of complex, inline SQLAlchemy queries for fetching and aggregating charge data. This logic is tightly coupled to the API and is difficult to reuse or maintain.

**Files Affected**:
- `api/v1/charges.py`

**Example of Current Inline Query**:
```python
# in api/v1/charges.py -> get_project_charges()
comp_data = db.session.query(
    CompChargeSummary.activity_date,
    func.sum(CompChargeSummary.charges).label('total_charges')
).filter(
    CompChargeSummary.account_id.in_(account_ids),
    CompChargeSummary.activity_date >= start_date,
    CompChargeSummary.activity_date <= end_date
).group_by(CompChargeSummary.activity_date).all()
```

**Proposed Solution**: Move all data aggregation logic into the `sam.queries` module, which already serves as the central data access layer for other parts of the application. The API endpoint should become a thin wrapper.

**Refactoring Approach**:
1.  Create or enhance functions in `sam.queries` (e.g., `get_daily_charge_trends_for_project`).
2.  Refactor the API endpoint to call these functions.

**Example "After" Refactoring**:
```python
# in api/v1/charges.py
from sam.queries import get_daily_charge_trends_for_project

@bp.route('/projects/<projcode>/charges', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_ALLOCATIONS)
def get_project_charges(projcode):
    # ... (parsing and setup) ...

    if group_by == 'date':
        # Delegate all query logic to the central query module
        sorted_data = get_daily_charge_trends_for_project(
            db.session,
            project_id=project.project_id,
            start_date=start_date,
            end_date=end_date,
            resource_name=resource_name
        )
        return jsonify({ ... 'data': sorted_data })
    
    # ... (handle other cases) ...
```

**Impact**:
-   **Single Source of Truth**: Ensures consistent business logic across the API, CLI, and dashboards.
-   **Improved Maintainability**: Decouples the API from the database schema.
-   **Simplified Code**: Makes the API endpoint cleaner and more focused.

### 1.2. Abstract System Status Queries

**Problem**: The System Status Dashboard (`dashboards/status/blueprint.py`) contains inline SQLAlchemy queries for fetching status data, similar to the issue in the API. It also manages its own database session.

**Files Affected**:
- `dashboards/status/blueprint.py`

**Proposed Solution**: Create a dedicated query module for the system status database, for example at `src/system_status/queries.py`. Move all data-fetching logic from the blueprint into this new module.

**Refactoring Approach**:
1.  Create `src/system_status/queries.py`.
2.  Add functions like `get_latest_derecho_status`, `get_active_outages`, etc., that encapsulate the queries.
3.  Refactor the blueprint to call these new functions.

**Example "After" Refactoring**:
```python
# in dashboards/status/blueprint.py
from system_status import queries as status_queries

@bp.route('/')
@login_required
def index():
    session = get_session() # Use a centralized session getter
    try:
        derecho_status = status_queries.get_latest_derecho_status(session)
        casper_status = status_queries.get_latest_casper_status(session)
        outages = status_queries.get_active_outages(session)
        # ... etc ...
        return render_template(...)
    finally:
        session.close()
```

**Impact**:
-   **Architectural Consistency**: Aligns the status dashboard with the application's "thin view, fat query layer" pattern.
-   **Reusability**: Makes status queries available for other potential uses (e.g., future API endpoints).
-   **Improved Readability**: Simplifies the dashboard blueprint significantly.

---

## Priority 2: Consolidate Duplicate Code

This phase focuses on reducing boilerplate code in our Marshmallow schemas.

### 2.1. Consolidate Duplicate Schema Definitions

**Problem**: The codebase contains multiple, nearly identical Marshmallow schemas for different charge types. For example, `HPCChargeDetailSchema`, `DAVChargeDetailSchema`, etc., are structurally the same, as are the various `...ChargeSummarySchema` classes.

**Files Affected**:
- `schemas/charge_details.py` (if it exists, or where these are defined)
- `schemas/charges.py`

**Proposed Solution**: Use object-oriented principles to create base classes and reduce duplication.

**For Charge Detail Schemas**:
Create a `ChargeDetailBaseSchema` that contains all the common fields and methods. Subclasses will only need to specify the `charge_type`.

```python
class ChargeDetailBaseSchema(Schema):
    """Base schema for charge detail serialization."""
    date = fields.Method('get_date')
    type = fields.Method('get_type')
    # ... other common fields ...

    # Override in subclass
    charge_type = 'Unknown'

    # ... common getter methods ...

class HPCChargeDetailSchema(ChargeDetailBaseSchema):
    charge_type = 'HPC Compute'

class DAVChargeDetailSchema(ChargeDetailBaseSchema):
    charge_type = 'DAV'
```

**For Charge Summary Schemas**:
Use `SQLAlchemyAutoSchema` with inheritance to define a base schema.

```python
class BaseChargeSummarySchema(SQLAlchemyAutoSchema):
    """Base schema for charge summaries."""
    class Meta:
        load_instance = True
        include_fk = True
    activity_date = auto_field()
    charges = auto_field()

class CompChargeSummarySchema(BaseChargeSummarySchema):
    class Meta(BaseChargeSummarySchema.Meta):
        model = CompChargeSummary
        fields = ('account_id', 'activity_date', 'charges', 'core_hours', 'gpu_hours')
```

**Impact**:
-   **Reduces Boilerplate**: Could remove over 100 lines of duplicated schema code.
-   **Easier Maintenance**: Changes to the base schema will automatically propagate to all child schemas.
-   **Improved Readability**: Makes the schema definitions much more concise.