# WebUI Refactoring Plan

This document outlines prioritized opportunities for simplification and refactoring to improve code maintainability in the `python/webui` module.

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
from webui.api.helpers import register_error_handlers
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
from webui.extensions import db

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

## Phase 2: Schema Consolidation (4-5 hours)

These changes refactor repetitive schema patterns.

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

### 2.3 Charge Query Module Extraction

**Problem**: Same charge summary table queries duplicated in API and schema.

**Files Affected**:
- `api/v1/charges.py` (lines 108-173)
- `schemas/allocation.py` (lines 170-222)

**Proposed Solution**: Create `queries/charges.py`:
```python
"""Charge query utilities for the web UI."""

from datetime import datetime
from sqlalchemy import func
from sam.summaries.charge_summaries import (
    CompChargeSummary, DavChargeSummary,
    DiskChargeSummary, ArchiveChargeSummary
)

def get_charge_totals(session, account_ids, start_date, end_date, resource_type='HPC'):
    """
    Get total charges by type for given accounts and date range.

    Returns:
        dict: {'comp': float, 'dav': float, 'disk': float, 'archive': float}
    """
    totals = {'comp': 0.0, 'dav': 0.0, 'disk': 0.0, 'archive': 0.0}

    if resource_type in ('HPC', 'DAV'):
        # Query comp and dav summaries
        comp_total = session.query(func.sum(CompChargeSummary.charges)).filter(
            CompChargeSummary.account_id.in_(account_ids),
            CompChargeSummary.activity_date.between(start_date, end_date)
        ).scalar() or 0.0
        totals['comp'] = float(comp_total)

        dav_total = session.query(func.sum(DavChargeSummary.charges)).filter(
            DavChargeSummary.account_id.in_(account_ids),
            DavChargeSummary.activity_date.between(start_date, end_date)
        ).scalar() or 0.0
        totals['dav'] = float(dav_total)

    elif resource_type == 'DISK':
        disk_total = session.query(func.sum(DiskChargeSummary.charges)).filter(
            DiskChargeSummary.account_id.in_(account_ids),
            DiskChargeSummary.activity_date.between(start_date, end_date)
        ).scalar() or 0.0
        totals['disk'] = float(disk_total)

    elif resource_type == 'ARCHIVE':
        archive_total = session.query(func.sum(ArchiveChargeSummary.charges)).filter(
            ArchiveChargeSummary.account_id.in_(account_ids),
            ArchiveChargeSummary.activity_date.between(start_date, end_date)
        ).scalar() or 0.0
        totals['archive'] = float(archive_total)

    return totals


def get_daily_charges(session, account_ids, start_date, end_date):
    """
    Get daily charge breakdown for timeline visualization.

    Returns:
        list: [{'date': 'YYYY-MM-DD', 'comp': float, 'dav': float, ...}, ...]
    """
    # Implementation for daily aggregation
    pass
```

**Impact**: Reusable query logic, single source of truth

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
from webui.utils.rbac import has_permission, Permission
from webui.utils.project_permissions import can_manage_project_members


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
- `queries/charges.py` - Charge query utilities (optional, Phase 2)
- `api/access_control.py` - Access control decorators (optional, Phase 3)

### Files to Modify
| File | Phase | Changes |
|------|-------|---------|
| `api/v1/users.py` | 1, 3 | Use helpers, split endpoint |
| `api/v1/projects.py` | 1 | Use helpers |
| `api/v1/charges.py` | 1, 2 | Use helpers, extract queries |
| `schemas/charge_details.py` | 2 | Base class refactor |
| `schemas/charges.py` | 2 | Inheritance refactor |
| `schemas/allocation.py` | 2 | Use charge query module |

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
