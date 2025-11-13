# Marshmallow-SQLAlchemy API Integration Plan

**Complete coverage for Users, Projects, Allocations, Charges, and Balances**

Generated: 2025-11-12

---

## Overview

Refactor the Flask API in `python/webui/api/v1/` to use marshmallow-sqlalchemy for automatic serialization, replacing manual dictionary construction with declarative schemas. This plan includes support for allocation balances and charge tracking as displayed in the `sam_search.py` CLI tool.

---

## Phase 1: Foundation Setup

### 1.1 Create Base Schema Infrastructure
Following the "Base Schema I" pattern from [marshmallow-sqlalchemy docs](https://marshmallow-sqlalchemy.readthedocs.io/en/latest/recipes.html#base-schema-i):

- Create `python/webui/schemas/__init__.py` with `BaseSchema` class
- Configure shared `db.session` from Flask-SQLAlchemy
- Set common config: `load_instance=True`, `include_fk=True`, `sqla_session=db.session`
- Export all schemas from this module

---

## Phase 2: Core Model Schemas

### 2.1 User Schemas
**File:** `python/webui/schemas/user.py`

- `UserSchema` - Full user details with nested relationships
  - Fields: user_id, username, full_name, display_name, email, active, locked, charging_exempt, unix_uid
  - Nested: institutions, organizations, role_assignments
- `UserListSchema` - Lightweight for list endpoints (no nested objects)
- `UserSummarySchema` - Minimal for nested references (id, username, full_name, email)

### 2.2 Project Schemas
**File:** `python/webui/schemas/project.py`

- `ProjectSchema` - Full project details with nested relationships
  - Fields: project_id, projcode, title, start_date, end_date, active, unix_gid, charging_exempt
  - Nested: lead (UserSummarySchema), admin (UserSummarySchema), area_of_interest, organization
  - Method fields: `allocations_with_usage` (computed from get_detailed_allocation_usage())
- `ProjectListSchema` - Lightweight for list endpoints
- `ProjectSummarySchema` - Minimal for nested references

---

## Phase 3: Accounting & Allocation Schemas

### 3.1 Allocation Schemas
**File:** `python/webui/schemas/allocation.py`

- `AllocationSchema` - Full allocation details
  - Fields: allocation_id, amount, start_date, end_date, account_id, parent_allocation_id
  - Nested: account (AccountSummarySchema), parent_allocation (self-reference)
  - Method field: `is_active` (computed from is_active_at())

- `AllocationWithUsageSchema` - **KEY SCHEMA** for allocation balances
  - Extends AllocationSchema with usage data
  - Additional fields: used, remaining, percent_used (computed values)
  - Nested: resource (ResourceSummarySchema)
  - **This matches the output from sam_search.py --verbose**

### 3.2 Account Schemas
**File:** `python/webui/schemas/account.py`

- `AccountSchema` - Account details
  - Fields: account_id, project_id, resource_id, active
  - Nested: project (ProjectSummarySchema), resource (ResourceSchema), allocations
- `AccountSummarySchema` - Minimal for nested references

### 3.3 Charge Adjustment Schema
**File:** `python/webui/schemas/adjustment.py`

- `ChargeAdjustmentSchema` - Manual adjustments to account balances
  - Fields: charge_adjustment_id, account_id, amount, adjustment_date, description
  - Used in balance calculations when include_adjustments=True

---

## Phase 4: Summary & Charge Schemas

### 4.1 Charge Summary Schemas
**File:** `python/webui/schemas/summaries.py`

These schemas serialize the daily aggregated charge data:

- `CompChargeSummarySchema` - HPC computational charges
  - Fields: charge_summary_id, activity_date, account_id, num_jobs, core_hours, charges
  - Used for: HPC and DAV resources

- `DavChargeSummarySchema` - DAV charges
  - Fields: dav_charge_summary_id, activity_date, account_id, num_jobs, core_hours, charges
  - Used for: DAV resources

- `DiskChargeSummarySchema` - Storage charges
  - Fields: disk_charge_summary_id, activity_date, account_id, number_of_files, bytes, terabyte_years, charges
  - Used for: DISK resources (Stratus, Campaign Store)

- `ArchiveChargeSummarySchema` - Archive/HPSS charges
  - Fields: archive_charge_summary_id, activity_date, account_id, number_of_files, bytes, terabyte_years, charges
  - Used for: ARCHIVE resources

### 4.2 Aggregate Charge Schema
**File:** `python/webui/schemas/charges.py`

- `AggregateChargeSchema` - Combined view of charges by resource type
  - Fields: resource_name, resource_type, total_charges, date_range, breakdown_by_type
  - Method field: Aggregates from appropriate summary tables based on resource type

---

## Phase 5: Resource & Supporting Schemas

### 5.1 Resource Schemas
**File:** `python/webui/schemas/resource.py`

- `ResourceSchema` - Full resource details
  - Fields: resource_id, resource_name, resource_code, active
  - Nested: resource_type (ResourceTypeSchema), facility
- `ResourceSummarySchema` - Minimal for nested references
- `ResourceTypeSchema` - HPC, DAV, DISK, ARCHIVE, DATA_ACCESS types

### 5.2 Supporting Entity Schemas
**File:** `python/webui/schemas/entities.py`

- `InstitutionSchema` - User institutions
- `OrganizationSchema` - Labs/sections
- `AreaOfInterestSchema` - Project research areas
- `RoleSchema` - User roles
- `FacilitySchema` - UNIV, WNA, NCAR facilities

---

## Phase 6: API Endpoint Refactoring

### 6.1 User Endpoints
**File:** `python/webui/api/v1/users.py`

- `GET /api/v1/users/` - Use `UserListSchema(many=True).dump()`
- `GET /api/v1/users/<username>` - Use `UserSchema().dump()`
- `GET /api/v1/users/<username>/projects` - Use `ProjectListSchema(many=True).dump()`

### 6.2 Project Endpoints
**File:** `python/webui/api/v1/projects.py`

- `GET /api/v1/projects/` - Use `ProjectListSchema(many=True).dump()`
- `GET /api/v1/projects/<projcode>` - Use `ProjectSchema().dump()`
- `GET /api/v1/projects/<projcode>/members` - Use `UserListSchema(many=True).dump()`
- `GET /api/v1/projects/<projcode>/allocations` - **ENHANCED**: Use `AllocationWithUsageSchema(many=True).dump()`
  - Show allocation amounts, used, remaining, percent_used (like sam_search.py output)
  - Include resource details nested
- `GET /api/v1/projects/expiring` - Use `ProjectListSchema(many=True).dump()`
- `GET /api/v1/projects/recently_expired` - Use `ProjectListSchema(many=True).dump()`

### 6.3 New Charge Endpoints
**File:** `python/webui/api/v1/charges.py` - **NEW FILE**

- `GET /api/v1/projects/<projcode>/charges` - Get charge summaries by date range
  - Query params: start_date, end_date, resource_id (optional)
  - Use `CompChargeSummarySchema`, `DavChargeSummarySchema`, etc.

- `GET /api/v1/projects/<projcode>/charges/summary` - Aggregate charges
  - Use `AggregateChargeSchema`
  - Returns total charges by resource type

- `GET /api/v1/accounts/<account_id>/balance` - Get current account balance
  - Use `AllocationWithUsageSchema`
  - Shows allocation, used, remaining (real-time calculation)

---

## Phase 7: Testing

### 7.1 Schema Unit Tests
**File:** `tests/test_schemas.py`

- Test serialization of all schemas
- Verify nested relationships serialize correctly
- Test List vs Detail vs Summary schema differences
- Validate datetime field handling
- Test method fields (is_active, allocations_with_usage, etc.)
- Test self-referential relationships (parent_allocation)

### 7.2 Schema Integration Tests
**File:** `tests/test_schema_integration.py`

- Test Project.get_detailed_allocation_usage() serializes correctly
- Verify charge summary aggregation with schemas
- Test allocation balance calculations match sam_search.py output
- Test resource type routing to correct summary tables

### 7.3 API Endpoint Tests
**File:** `tests/test_api_with_schemas.py`

- Enpoint access requires login, username 'benkirk' with any password.
- Test each endpoint returns correct schema format
- Verify backward compatibility (same JSON structure as before)
- Test new allocation/charge endpoints
- Verify pagination still works with schemas
- Test error handling unchanged

### 7.4 Performance Tests
**File:** `tests/test_schema_performance.py`

- Verify summary table queries perform well (should be fast - already indexed)
- Test pagination with large result sets

---

## Phase 8: Documentation & Cleanup

### 8.1 Update Documentation

- Add schema documentation section to CLAUDE.md
- Document schema usage patterns and conventions
- Document allocation/charge calculation logic
- Add API endpoint examples with new charge endpoints
- Update project overview with marshmallow-sqlalchemy

### 8.2 Code Cleanup

- Remove all manual dictionary construction code (200+ lines eliminated)
- Consolidate datetime formatting (marshmallow handles automatically)
- Standardize error responses
- Add type hints to schema classes

---

## Key Design Decisions

### 1. Base Schema Pattern
- Use "Base Schema I" pattern from marshmallow-sqlalchemy docs
- Shared session management via `db.session`
- Consistent configuration across all schemas

### 2. Three-Tier Schema Strategy
- **Full Schemas** (`UserSchema`, `ProjectSchema`) - All fields + nested relationships
- **List Schemas** (`UserListSchema`, `ProjectListSchema`) - Lightweight, no deep nesting
- **Summary Schemas** (`UserSummarySchema`, `ProjectSummarySchema`) - Minimal fields for references

### 3. Computed Fields via Method Fields
- `AllocationWithUsageSchema.used` - Calculated from summary tables
- `AllocationWithUsageSchema.remaining` - allocation.amount - used
- `AllocationWithUsageSchema.percent_used` - (used / amount) * 100
- `AllocationSchema.is_active` - allocation.is_active_at(datetime.now())

### 4. Resource Type Routing
- Schemas aware of resource types (HPC, DAV, DISK, ARCHIVE)
- Automatic routing to correct summary tables based on resource.resource_type
- Follows existing pattern from Project.get_detailed_allocation_usage()

### 5. Read-Only API (For Now)
- All schemas configured for serialization (dump) only
- No validation fields needed yet (API is GET-only)
- Future: Add `load` support for POST/PUT/PATCH endpoints

### 6. Backward Compatibility Not Required
- OK to change current return value structures.

---

## Expected Benefits

### Code Quality
- ✅ Eliminate ~250 lines of manual dict construction
- ✅ Standardize relationship serialization across all endpoints
- ✅ Type-safe schema definitions
- ✅ Self-documenting API via schema introspection

### Performance
- ✅ Leverage existing summary table indexes (no performance regression)
- ✅ Lazy loading of nested relationships (only load what's needed)
- ✅ Efficient pagination with `many=True`

### Maintainability
- ✅ Single source of truth for serialization logic
- ✅ Easy to add new fields (update schema, not every endpoint)
- ✅ Automatic datetime/type conversion
- ✅ Easier to extend for write operations (POST/PUT/PATCH) later

### Feature Enablement
- ✅ New charge/balance endpoints match sam_search.py functionality
- ✅ Foundation for API documentation (OpenAPI/Swagger) generation
- ✅ Validation framework ready for future write endpoints
- ✅ Easy to add filtering/search via schema fields

---

## Implementation Order

1. **Phase 1** - Foundation (schemas/__init__.py, base setup)
2. **Phase 2** - Core schemas (User, Project)
3. **Phase 6.1-6.2** - Refactor existing endpoints to use new schemas
4. **Phase 7.2-7.3** - Test existing endpoints work with schemas
5. **Phase 3** - Accounting schemas (Allocation, Account, ChargeAdjustment)
6. **Phase 4** - Summary/charge schemas
7. **Phase 5** - Supporting schemas (Resource, etc.)
8. **Phase 6.3** - New charge endpoints
9. **Phase 7.1, 7.4** - Comprehensive testing
10. **Phase 8** - Documentation and cleanup

**Rationale for this order:**
- Prove the pattern with core models first
- Refactor existing endpoints early (get value quickly)
- Add charge/balance functionality incrementally
- Test thoroughly before moving to next phase

---

## Key ORM Models for Charges & Balances

### How Allocation Balances Are Calculated

From `Project.get_detailed_allocation_usage()` in `python/sam/projects/projects.py`:

```python
# Used amount calculation (based on resource type):
charges_by_type = _get_charges_by_resource_type(account_id, resource_type, start_date, end_date)

# For HPC/DAV:
comp_charges = SUM(CompChargeSummary.charges) WHERE account_id AND date range
dav_charges = SUM(DavChargeSummary.charges) WHERE account_id AND date range
total_charges = comp_charges + dav_charges

# For DISK:
disk_charges = SUM(DiskChargeSummary.charges) WHERE account_id AND date range

# For ARCHIVE:
archive_charges = SUM(ArchiveChargeSummary.charges) WHERE account_id AND date range

# Optional adjustments:
adjustments = SUM(ChargeAdjustment.amount) WHERE account_id AND date range

# Final used amount:
effective_used = total_charges + adjustments

# Remaining calculation:
allocated = Allocation.amount
remaining = allocated - effective_used
percent_used = (effective_used / allocated * 100) if allocated > 0 else 0
```

### Summary Tables (Pre-aggregated Daily)

The system uses **summary tables** (not raw activity records) for performance:

- **CompChargeSummary** (`comp_charge_summary`) - HPC computational charges
- **DavChargeSummary** (`dav_charge_summary`) - DAV charges
- **DiskChargeSummary** (`disk_charge_summary`) - Storage charges
- **ArchiveChargeSummary** (`archive_charge_summary`) - Archive/HPSS charges

All located in `python/sam/summaries/`

### Resource Type → Summary Table Mapping

- **HPC resources**: CompChargeSummary + DavChargeSummary
- **DAV resources**: CompChargeSummary + DavChargeSummary (DAV can have both!)
- **DISK resources**: DiskChargeSummary only
- **ARCHIVE resources**: ArchiveChargeSummary only

### Key Relationships for Schemas

1. **Project → Account** (project.accounts)
2. **Account → Allocation** (account.allocations)
3. **Account → Resource** (account.resource)
4. **Account → CompChargeSummary** (account.comp_charge_summaries)
5. **Account → DavChargeSummary** (account.dav_charge_summaries)
6. **Account → DiskChargeSummary** (account.disk_charge_summaries)
7. **Account → ArchiveChargeSummary** (account.archive_charge_summaries)
8. **Account → ChargeAdjustment** (account.charge_adjustments)
9. **Resource → ResourceType** (resource.resource_type)

---

## Current API Analysis

### Existing Endpoints (Manual Serialization)

**User Endpoints:**
- `GET /api/v1/users/` - List users with pagination, search, filtering
- `GET /api/v1/users/<username>` - Get user details
- `GET /api/v1/users/<username>/projects` - Get user's projects

**Project Endpoints:**
- `GET /api/v1/projects/` - List projects with pagination, search, filtering
- `GET /api/v1/projects/<projcode>` - Get project details
- `GET /api/v1/projects/<projcode>/members` - Get project members
- `GET /api/v1/projects/<projcode>/allocations` - Get project allocations
- `GET /api/v1/projects/expiring` - Get expiring projects
- `GET /api/v1/projects/recently_expired` - Get expired projects

### Current Serialization Issues

- Manual dictionary construction in every endpoint
- Inconsistent relationship handling
- Manual `.isoformat()` datetime conversion
- Repetitive code (~250 lines can be eliminated)
- No type safety or validation
- Error-prone (easy to forget fields)

### API Framework

- **Flask** with Flask-SQLAlchemy integration
- Application factory pattern in `python/webui/run.py`
- RBAC with `@login_required` and `@require_permission` decorators
- Consistent error responses: `{"error": "message"}`
- Blueprints per resource type

---

## References

- [Marshmallow-SQLAlchemy Docs](https://marshmallow-sqlalchemy.readthedocs.io/)
- [Base Schema I Pattern](https://marshmallow-sqlalchemy.readthedocs.io/en/latest/recipes.html#base-schema-i)
- [Flask-Marshmallow Docs](https://flask-marshmallow.readthedocs.io/)
- SAM ORM Models: `python/sam/` (91+ models, 94% DB coverage)
- Current API: `python/webui/api/v1/`
- CLI Reference: `python/sam_search.py` (shows allocation balance output format)

---

## Notes

- This plan was generated after exploring the current API implementation and the `sam_search.py` CLI tool
- The allocation balance display in `sam_search.py --verbose` serves as the reference for API output format
- All ORM models are already defined and tested (172 tests passing)
- Summary tables are pre-indexed and optimized for fast queries
- No database schema changes needed - this is purely a serialization layer

---

*Plan created: 2025-11-12*
*Status: Ready for implementation*
*Estimated effort: 2-3 days for full implementation*
