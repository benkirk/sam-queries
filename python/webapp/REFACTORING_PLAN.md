# WebUI Refactoring Plan

This document outlines prioritized opportunities for simplification and refactoring to improve code maintainability in the `python/webapp` module.

**Estimated Total Impact**: ~350+ lines of duplicated code can be consolidated
**Last Updated**: 2025-11-21

---

## Overview

The current codebase has evolved organically with some common patterns repeated across multiple files. This plan identifies opportunities to:
- Extract common patterns into reusable utilities
- Reduce code duplication
- Improve consistency across API endpoints
- Make the codebase more maintainable

---

## Phase 1: Quick Wins (2-3 hours)

These are low-risk, high-impact changes that extract obvious duplications.

### 1.1 Centralize Error Handlers

**Problem**: Identical error handlers registered in 3 API blueprint files.

**Files Affected**:
- `api/v1/users.py` (lines 317-326)
- `api/v1/projects.py` (lines 659-668)
- `api/v1/charges.py` (lines 307-316)

**Current Pattern** (repeated 3 times):
```python
@bp.errorhandler(403)
def forbidden(e):
    return jsonify({'error': 'Forbidden - insufficient permissions'}), 403

@bp.errorhandler(401)
def unauthorized(e):
    return jsonify({'error': 'Unauthorized - authentication required'}), 401
```

**Proposed Solution**: Create `api/helpers.py`:
```python
from flask import jsonify

def register_error_handlers(blueprint):
    """Register standard API error handlers on a blueprint."""

    @blueprint.errorhandler(400)
    def bad_request(e):
        return jsonify({'error': str(e.description) or 'Bad request'}), 400

    @blueprint.errorhandler(401)
    def unauthorized(e):
        return jsonify({'error': 'Unauthorized - authentication required'}), 401

    @blueprint.errorhandler(403)
    def forbidden(e):
        return jsonify({'error': 'Forbidden - insufficient permissions'}), 403

    @blueprint.errorhandler(404)
    def not_found(e):
        return jsonify({'error': 'Resource not found'}), 404
```

**Usage**:
```python
# In each API blueprint file
from webapp.api.helpers import register_error_handlers
register_error_handlers(bp)
```

**Impact**: ~30 lines removed, consistent error responses

---

### 1.2 Date Range Parsing Helper

**Problem**: Identical date parsing try/except blocks in multiple endpoints.

**Files Affected**:
- `api/v1/charges.py` (lines 61-66)
- `api/v1/projects.py` (lines 631-636)

**Current Pattern** (repeated):
```python
try:
    end_date = datetime.strptime(request.args.get('end_date'), '%Y-%m-%d') if request.args.get('end_date') else datetime.now()
    start_date = datetime.strptime(request.args.get('start_date'), '%Y-%m-%d') if request.args.get('start_date') else end_date - timedelta(days=90)
except ValueError:
    return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
```

**Proposed Solution**: Add to `api/helpers.py`:
```python
from datetime import datetime, timedelta
from flask import request, jsonify

def parse_date_range(days_back=90):
    """
    Parse start_date and end_date from request query parameters.

    Args:
        days_back: Default number of days before end_date for start_date

    Returns:
        tuple: (start_date, end_date, error_response)
        If error_response is not None, return it immediately.

    Usage:
        start_date, end_date, error = parse_date_range()
        if error:
            return error
    """
    try:
        end_str = request.args.get('end_date')
        start_str = request.args.get('start_date')

        end_date = datetime.strptime(end_str, '%Y-%m-%d') if end_str else datetime.now()
        start_date = datetime.strptime(start_str, '%Y-%m-%d') if start_str else end_date - timedelta(days=days_back)

        return start_date, end_date, None
    except ValueError:
        return None, None, (jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400)
```

**Usage**:
```python
start_date, end_date, error = parse_date_range(days_back=90)
if error:
    return error
```

**Impact**: ~20 lines removed, centralized validation

---

### 1.3 Project Lookup Pattern

**Problem**: Same project lookup + 404 check repeated 7 times.

**Files Affected**:
- `api/v1/projects.py` (lines 199, 232, 286, 457, 518, 557, 622)

**Current Pattern** (repeated 7 times):
```python
project = find_project_by_code(db.session, projcode)
if not project:
    return jsonify({'error': 'Project not found'}), 404
```

**Proposed Solution**: Add to `api/helpers.py`:
```python
from sam.queries import find_project_by_code
from webapp.extensions import db

def get_project_or_404(projcode):
    """
    Look up a project by projcode, returning 404 error if not found.

    Returns:
        tuple: (project, error_response)
        If error_response is not None, return it immediately.

    Usage:
        project, error = get_project_or_404(projcode)
        if error:
            return error
    """
    project = find_project_by_code(db.session, projcode)
    if not project:
        return None, (jsonify({'error': f'Project {projcode} not found'}), 404)
    return project, None
```

**Usage**:
```python
project, error = get_project_or_404(projcode)
if error:
    return error
```

**Impact**: ~30 lines removed, consistent error messages

---

### 1.4 Project Membership Serialization

**Problem**: Identical logic for grouping projects by user role (led/admin/member).

**Files Affected**:
- `api/v1/users.py` (lines 125-149)
- `api/v1/projects.py` (lines 269-314)

**Current Pattern** (repeated):
```python
led_projects = [{**schema.dump(p), 'role': 'lead'} for p in user.led_projects]
admin_projects = [{**schema.dump(p), 'role': 'admin'} for p in user.admin_projects if p not in user.led_projects]
member_projects = [{**schema.dump(p), 'role': 'member'} for p in user.active_projects
                   if p not in user.led_projects and p not in user.admin_projects]
```

**Proposed Solution**: Add to `api/helpers.py`:
```python
def serialize_projects_by_role(user, schema):
    """
    Serialize a user's projects grouped by their role.

    Args:
        user: User object with led_projects, admin_projects, active_projects
        schema: Marshmallow schema instance for project serialization

    Returns:
        dict with keys: led_projects, admin_projects, member_projects, total_projects
    """
    led_set = set(user.led_projects)
    admin_set = set(user.admin_projects) - led_set

    led_projects = [{**schema.dump(p), 'role': 'lead'} for p in user.led_projects]
    admin_projects = [{**schema.dump(p), 'role': 'admin'} for p in admin_set]
    member_projects = [
        {**schema.dump(p), 'role': 'member'}
        for p in user.active_projects
        if p not in led_set and p not in admin_set
    ]

    return {
        'led_projects': led_projects,
        'admin_projects': admin_projects,
        'member_projects': member_projects,
        'total_projects': len(led_projects) + len(admin_projects) + len(member_projects)
    }
```

**Impact**: ~40 lines removed, consistent serialization

---

## Phase 2: Schema Consolidation & Query Reuse (4-5 hours)

These changes refactor repetitive schema patterns and consolidate query logic.

### 2.1 Charge Detail Schema Base Class

**Problem**: Four nearly identical charge detail schemas with only constants differing.

**File Affected**:
- `schemas/charge_details.py` (lines 19-149)

**Current Pattern**: Four separate schema classes (HPCChargeDetailSchema, DAVChargeDetailSchema, DiskChargeDetailSchema, ArchiveChargeDetailSchema) each with:
- Same 5 fields (date, type, comment, user, amount)
- Same 5 getter methods
- Only the `type` constant differs

**Proposed Solution**:
```python
class ChargeDetailBaseSchema(Schema):
    """Base schema for charge detail serialization."""

    date = fields.Method('get_date')
    type = fields.Method('get_type')
    comment = fields.Method('get_comment')
    user = fields.Method('get_user')
    amount = fields.Method('get_amount')

    # Override in subclass
    charge_type = 'Unknown'

    def get_date(self, obj):
        _, activity, _ = obj
        return activity.activity_date.strftime('%Y-%m-%d') if activity.activity_date else 'N/A'

    def get_type(self, obj):
        return self.charge_type

    def get_comment(self, obj):
        _, activity, _ = obj
        return getattr(activity, 'comment', '') or ''

    def get_user(self, obj):
        _, activity, _ = obj
        return getattr(activity, 'username', 'N/A')

    def get_amount(self, obj):
        charge, _, _ = obj
        return float(charge.charges) if charge.charges else 0.0


class HPCChargeDetailSchema(ChargeDetailBaseSchema):
    charge_type = 'HPC Compute'


class DAVChargeDetailSchema(ChargeDetailBaseSchema):
    charge_type = 'DAV'


class DiskChargeDetailSchema(ChargeDetailBaseSchema):
    charge_type = 'Disk Storage'


class ArchiveChargeDetailSchema(ChargeDetailBaseSchema):
    charge_type = 'HPSS Archive'
```

**Impact**: ~80 lines removed

---

### 2.2 Charge Summary Schema Factory

**Problem**: Four similar charge summary schemas differing only by model.

**File Affected**:
- `schemas/charges.py` (lines 26-101)

**Current Pattern**: CompChargeSummarySchema, DavChargeSummarySchema, DiskChargeSummarySchema, ArchiveChargeSummarySchema all with same structure.

**Proposed Solution**: Use SQLAlchemyAutoSchema with inheritance:
```python
class BaseChargeSummarySchema(SQLAlchemyAutoSchema):
    """Base schema for charge summaries."""

    class Meta:
        load_instance = True
        include_fk = True

    # Common fields
    activity_date = auto_field()
    charges = auto_field()


class CompChargeSummarySchema(BaseChargeSummarySchema):
    class Meta(BaseChargeSummarySchema.Meta):
        model = CompChargeSummary
        fields = ('account_id', 'activity_date', 'charges', 'core_hours', 'gpu_hours')


class DavChargeSummarySchema(BaseChargeSummarySchema):
    class Meta(BaseChargeSummarySchema.Meta):
        model = DavChargeSummary
        fields = ('account_id', 'activity_date', 'charges')

# ... etc
```

**Impact**: ~50 lines removed

---

### 2.3 Leverage `sam.queries` Module for Charge Queries

**Problem**: Same charge summary table queries duplicated in API and schema.

**Files Affected**:
- `api/v1/charges.py` (lines 108-173)
- `schemas/allocation.py` (lines 170-222)

**Existing Solution in `sam.queries`**: The `sam/queries/__init__.py` module already provides comprehensive charge query utilities that should be reused:

```python
# Already available in sam.queries:
from sam.queries import (
    get_user_charge_summary,      # User charges by date range
    get_project_usage_summary,    # Aggregated project usage totals
    get_daily_usage_trend,        # Daily breakdown for charts
    get_resource_detail_data,     # Full resource detail with daily charges
    get_user_dashboard_data,      # Optimized dashboard queries
    get_user_breakdown_for_project,  # Per-user usage on a project
    get_queue_usage_breakdown,    # Usage by queue
)
```

**Refactoring Approach**:

1. **In `api/v1/charges.py`**: Replace inline charge queries with `sam.queries` functions:
   ```python
   from sam.queries import get_resource_detail_data, get_daily_usage_trend

   @bp.route('/projects/<projcode>/charges')
   def get_project_charges(projcode):
       # Use existing sam.queries function instead of inline queries
       data = get_resource_detail_data(
           db.session, projcode, resource_name, start_date, end_date
       )
       return jsonify(data)
   ```

2. **In `schemas/allocation.py`**: Use `Project.get_detailed_allocation_usage()` which already encapsulates the charge aggregation logic (see `sam/projects/projects.py`).

3. **Add any missing utilities to `sam.queries`**: If specific query patterns aren't already covered, add them to `sam/queries/__init__.py` rather than creating a webapp-specific module. This makes them available to:
   - The webapp API endpoints
   - The `sam_search.py` CLI tool
   - Any future interfaces

**Key `sam.queries` Functions for Charges**:

| Function | Purpose | Use Case |
|----------|---------|----------|
| `get_resource_detail_data()` | Resource summary + daily charges | Resource detail pages |
| `get_daily_usage_trend()` | Daily charge breakdown | Timeline charts |
| `get_project_usage_summary()` | Aggregated totals | Dashboard summaries |
| `get_user_breakdown_for_project()` | Per-user usage | User contribution views |
| `get_queue_usage_breakdown()` | Usage by queue | Queue analysis |

**Impact**:
- Webui endpoints become thin wrappers around `sam.queries`
- Single source of truth for charge calculations
- CLI and API share consistent query logic
- ~60 lines of duplicate queries removed from webapp

**Architecture Principle**:

```
┌─────────────────────────────────────────────────────────────────┐
│                        sam.queries                              │
│  (Database queries, business logic, data aggregation)           │
│  • Reusable by CLI, API, scripts                                │
│  • Returns Python objects/dicts                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
   │ sam_search  │    │   webapp/    │    │  (future)   │
   │    CLI      │    │    API      │    │   tools     │
   │             │    │   schemas   │    │             │
   └─────────────┘    └─────────────┘    └─────────────┘
```

- **`sam.queries`**: Database operations, business logic, calculations
- **`webapp/schemas`**: HTTP serialization (Marshmallow), field selection, nested relationships
- **`webapp/api`**: Request handling, authentication, thin wrappers around queries

---

## Phase 3: Structural Improvements (2-3 hours)

These are optional enhancements for better code organization.

### 3.1 Split Large Endpoints

**Problem**: `get_current_user_projects()` handles two formats in 108 lines.

**File Affected**:
- `api/v1/users.py` (lines 42-150)

**Proposed Solution**: Extract format-specific logic:
```python
def _serialize_grouped_format(user, schema):
    """Serialize projects in grouped format (led/admin/member)."""
    return serialize_projects_by_role(user, schema)


def _serialize_dashboard_format(user, schema):
    """Serialize projects in dashboard format with usage details."""
    # Current lines 67-118 extracted here
    pass


@bp.route('/me/projects', methods=['GET'])
@login_required
def get_current_user_projects():
    """Get projects for currently authenticated user."""
    format_type = request.args.get('format', 'grouped')
    schema = ProjectListSchema()

    if format_type == 'dashboard':
        return jsonify(_serialize_dashboard_format(current_user, schema))
    else:
        return jsonify(_serialize_grouped_format(current_user, schema))
```

**Impact**: Better testability, clearer separation

---

### 3.2 Standardize Response Helpers

**Problem**: Inconsistent error response structure across endpoints.

**Current Variations**:
- `{'error': 'message'}`
- `{'error': 'message', 'details': {...}}`
- No error codes for programmatic handling

**Proposed Solution**: Add to `api/helpers.py`:
```python
def success_response(data, message=None):
    """Standard success response wrapper."""
    response = {'success': True, 'data': data}
    if message:
        response['message'] = message
    return jsonify(response)


def error_response(message, status_code=400, code=None, details=None):
    """
    Standard error response wrapper.

    Args:
        message: Human-readable error message
        status_code: HTTP status code
        code: Machine-readable error code (e.g., 'NOT_FOUND', 'INVALID_DATE')
        details: Additional error details dict
    """
    response = {'error': message}
    if code:
        response['code'] = code
    if details:
        response['details'] = details
    return jsonify(response), status_code
```

**Impact**: Consistent API responses, better client error handling

---

### 3.3 Consolidate Access Control

**Problem**: Permission checks scattered across endpoints.

**File Affected**:
- `api/v1/projects.py` (multiple locations)

**Proposed Solution**: Create `api/access_control.py`:
```python
"""Centralized access control helpers for API endpoints."""

from flask_login import current_user
from webapp.utils.rbac import has_permission, Permission
from webapp.utils.project_permissions import can_manage_project_members


def require_permission(permission):
    """Decorator to require a system-level permission."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not has_permission(current_user, permission):
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_project_access(f):
    """Decorator to require access to project specified by projcode."""
    @wraps(f)
    def decorated_function(projcode, *args, **kwargs):
        project, error = get_project_or_404(projcode)
        if error:
            return error
        if not _user_can_access_project(current_user, project):
            abort(403)
        return f(project, *args, **kwargs)
    return decorated_function
```

**Impact**: Declarative access control, reduced boilerplate

---

## Files Summary

### New Files to Create
- `api/helpers.py` - Response helpers, date parsing, project lookup
- `api/access_control.py` - Access control decorators (optional, Phase 3)

### Files to Modify

#### WebUI Module (`python/webapp/`)
| File | Phase | Changes |
|------|-------|---------|
| `api/v1/users.py` | 1, 3 | Use helpers, split endpoint |
| `api/v1/projects.py` | 1 | Use helpers |
| `api/v1/charges.py` | 1, 2 | Use helpers, use `sam.queries` functions |
| `schemas/charge_details.py` | 2 | Base class refactor |
| `schemas/charges.py` | 2 | Inheritance refactor |
| `schemas/allocation.py` | 2 | Use `Project.get_detailed_allocation_usage()` |

#### SAM Core Module (`python/sam/`)
| File | Phase | Changes |
|------|-------|---------|
| `queries/__init__.py` | 2 | Add any missing charge query utilities (if needed) |

---

## Implementation Notes

1. **Test Coverage**: Run existing tests after each change to ensure no regressions
2. **Incremental**: Each item can be implemented independently
3. **Backwards Compatible**: All changes maintain existing API contracts
4. **Documentation**: Update docstrings as refactoring progresses

---

## Not Recommended for Change

The following patterns are working well and should be preserved:

- **Three-tier schema pattern** (Summary/List/Full) - Well designed
- **RBAC separation** (`rbac.py` + `project_permissions.py`) - Clean separation
- **Blueprint organization** - Clear concerns separated
- **Session fixture patterns** in tests - Working correctly
