# Marshmallow-SQLAlchemy Phase 8: Documentation & Cleanup

**Status**: Not started (optional future work)
**Prerequisites**: Phases 1-7 complete (all core functionality implemented and tested)
**Estimated effort**: 2-4 hours

---

## Overview

Phase 8 focuses on documentation updates and code cleanup. All core functionality is complete and tested (200 tests passing). This phase is optional but recommended for long-term maintainability.

---

## Tasks

### 8.1 Update CLAUDE.md

Add comprehensive schema documentation section to `/Users/benkirk/codes/sam-queries/CLAUDE.md`:

#### Schema Usage Patterns Section
```markdown
## Marshmallow-SQLAlchemy Schemas

### Overview
The API uses marshmallow-sqlalchemy for declarative serialization, replacing manual dictionary construction with type-safe schemas.

### Schema Organization
```
python/webui/schemas/
├── __init__.py           # Base schema + exports
├── user.py               # User schemas (3 tiers)
├── project.py            # Project schemas (3 tiers)
├── resource.py           # Resource schemas
├── allocation.py         # Allocation/Account schemas ⭐ KEY FILE
└── charges.py            # Charge summary schemas
```

### Three-Tier Schema Strategy
1. **Full Schemas** (`UserSchema`, `ProjectSchema`): All fields + nested relationships
2. **List Schemas** (`UserListSchema`, `ProjectListSchema`): Lightweight for lists
3. **Summary Schemas** (`UserSummarySchema`, `ProjectSummarySchema`): Minimal for references

### Usage Examples
\`\`\`python
from webui.schemas import UserSchema, ProjectListSchema, AllocationWithUsageSchema

# Serialize single object
user_data = UserSchema().dump(user)

# Serialize multiple objects
projects_data = ProjectListSchema(many=True).dump(projects)

# Serialize with context (for usage calculations)
schema = AllocationWithUsageSchema()
schema.context = {
    'account': account,
    'session': db.session,
    'include_adjustments': True
}
allocation_data = schema.dump(allocation)
\`\`\`

### Key Schemas

#### AllocationWithUsageSchema ⭐
**Most important schema** - calculates allocation balances matching sam_search.py output.

**Calculated Fields**:
- `used`: Total charges from summary tables
- `remaining`: allocated - used
- `percent_used`: (used / allocated) * 100
- `charges_by_type`: Breakdown by comp/dav/disk/archive
- `adjustments`: Manual charge adjustments

**Context Parameters**:
- `account`: Account object (provides resource/project info)
- `session`: SQLAlchemy session (for charge queries)
- `include_adjustments`: Include manual adjustments (default: True)

**Resource Type Routing**:
- HPC/DAV → CompChargeSummary + DavChargeSummary
- DISK → DiskChargeSummary
- ARCHIVE → ArchiveChargeSummary

### Datetime Handling
- Schemas automatically convert datetime objects to ISO format strings
- No manual `.isoformat()` calls needed
- Database uses naive datetimes (no timezone)

### Method Fields
Use Method fields to serialize @property methods:
\`\`\`python
class UserSchema(BaseSchema):
    full_name = fields.Method('get_full_name')

    def get_full_name(self, obj):
        return obj.full_name  # Calls @property
\`\`\`
```

#### Allocation Balance Calculation Logic Section
```markdown
## Allocation Balance Calculations

### Overview
Allocation balances are calculated in real-time from pre-aggregated summary tables. The logic is implemented in `AllocationWithUsageSchema` and matches `Project.get_detailed_allocation_usage()`.

### Calculation Flow
1. Determine resource type (HPC, DAV, DISK, ARCHIVE)
2. Query appropriate summary table(s) for date range
3. Sum charges by type
4. Add manual adjustments (if enabled)
5. Calculate: remaining = allocated - (charges + adjustments)

### Summary Tables
- **CompChargeSummary** (`comp_charge_summary`): Daily HPC charges
- **DavChargeSummary** (`dav_charge_summary`): Daily DAV charges
- **DiskChargeSummary** (`disk_charge_summary`): Daily storage charges
- **ArchiveChargeSummary** (`archive_charge_summary`): Daily archive charges

All tables are pre-indexed for fast queries by account_id and activity_date.

### Example Calculation
\`\`\`python
# For HPC resource over allocation period:
comp_charges = SUM(CompChargeSummary.charges)
    WHERE account_id = X
    AND activity_date BETWEEN start_date AND end_date

dav_charges = SUM(DavChargeSummary.charges)
    WHERE account_id = X
    AND activity_date BETWEEN start_date AND end_date

adjustments = SUM(ChargeAdjustment.amount)
    WHERE account_id = X
    AND adjustment_date BETWEEN start_date AND end_date

total_used = comp_charges + dav_charges + adjustments
remaining = allocated - total_used
percent_used = (total_used / allocated) * 100
\`\`\`
```

#### API Endpoints Section Update
```markdown
## API Endpoints (Marshmallow Schemas)

### User Endpoints
- `GET /api/v1/users/` → UserListSchema(many=True)
- `GET /api/v1/users/<username>` → UserSchema()
- `GET /api/v1/users/<username>/projects` → ProjectListSchema(many=True)

### Project Endpoints
- `GET /api/v1/projects/` → ProjectListSchema(many=True)
- `GET /api/v1/projects/<projcode>` → ProjectSchema()
- `GET /api/v1/projects/<projcode>/members` → UserListSchema(many=True)
- `GET /api/v1/projects/<projcode>/allocations` → AllocationWithUsageSchema(many=True) ⭐
- `GET /api/v1/projects/expiring` → ProjectListSchema(many=True)
- `GET /api/v1/projects/recently_expired` → ProjectListSchema(many=True)

### Charge/Balance Endpoints ⭐ NEW
- `GET /api/v1/projects/<projcode>/charges` - Detailed charge summaries
  - Query params: `start_date`, `end_date`, `resource_id`
  - Returns: All charge types grouped by resource

- `GET /api/v1/projects/<projcode>/charges/summary` - Aggregated totals
  - Returns: Summary of all active allocations with usage

- `GET /api/v1/accounts/<account_id>/balance` - Current balance
  - Query params: `include_adjustments` (default: true)
  - Returns: Real-time allocation balance
```

---

### 8.2 Add Schema Examples to README

Update `/Users/benkirk/codes/sam-queries/python/webui/README.md` with:

1. **Quick Schema Example** in "API Endpoints" section
2. **Link to CLAUDE.md** for detailed schema documentation
3. **Example API Responses** showing schema output format

---

### 8.3 Code Comments Cleanup (Optional)

Review and enhance docstrings in:
- `python/webui/schemas/allocation.py` - Already well-documented
- `python/webui/api/v1/charges.py` - Already well-documented
- Consider adding module-level docstrings with usage examples

---

### 8.4 Future Enhancements (Not Phase 8)

Document potential future work in MARSHMALLOW_PLAN.md:

#### OpenAPI/Swagger Documentation
- Use `apispec` with `marshmallow` for automatic API docs generation
- Schemas already provide structure for OpenAPI spec
- Estimated effort: 1-2 days

#### Write Operations (POST/PUT/PATCH)
- Add marshmallow validation for input
- Currently all schemas are read-only (dump only)
- Would enable API-based data modification
- Estimated effort: 3-5 days

#### Performance Optimization
- Add caching for frequently accessed allocations
- Consider materialized views for charge summaries
- Already using indexed summary tables (fast)
- Estimated effort: 2-3 days

---

## Success Criteria

Phase 8 complete when:
- ✅ CLAUDE.md has comprehensive schema documentation section
- ✅ Schema usage patterns documented with examples
- ✅ Allocation balance calculation logic explained
- ✅ API endpoint → schema mapping documented
- ✅ README.md updated with schema examples

---

## Notes

**Why Phase 8 is Optional**:
- All core functionality is complete and tested (200 tests passing)
- Code is well-commented and self-documenting
- Schemas have inline docstrings
- Commit messages provide detailed implementation notes

**When to Do Phase 8**:
- Before onboarding new developers to the project
- Before creating external API documentation
- When preparing for production deployment
- If documentation drift becomes an issue

**Time Investment vs. Value**:
- Low effort (2-4 hours)
- High value for long-term maintainability
- Can be done incrementally as needed

---

## Quick Start (When Ready)

To implement Phase 8:
1. Read this plan
2. Open CLAUDE.md
3. Add schema sections using templates above
4. Update README.md with schema examples
5. Commit with message: "Add Phase 8 documentation for marshmallow schemas"

---

*Plan created: 2025-11-13*
*Status: Ready for implementation (optional)*
*Current completion: Phases 1-7 complete (100% core functionality)*
