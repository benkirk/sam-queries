# SAM Queries - Project Memory

## Project Overview

**SAM (System for Allocation Management)** - Python ORM and query tools for NCAR's resource allocation and accounting database. Used by CISL to manage HPC allocations, user accounts, project tracking, and charging for Derecho, Casper, and other computational resources.

**Tech Stack**: SQLAlchemy 2.0, MySQL/MariaDB, Python 3.13, pytest

---

## Database Connection

```python
# Local MySQL connection
mysql -u root -h 127.0.0.1 -proot sam

# Session creation (in code)
from sam.session import create_sam_engine
engine, _ = create_sam_engine()
session = Session(engine)
```

**Database**: `sam` database with 97 tables, 91+ ORM models (94% coverage)

---

## Code Organization

```
sam-queries/
├── python/sam/              # ORM models (organized by domain)
│   ├── base.py              # Base classes, mixins
│   ├── core/                # Users, organizations, groups
│   ├── resources/           # Resources, machines, facilities, charging
│   ├── projects/            # Projects, contracts, areas of interest
│   ├── accounting/          # Accounts, allocations, adjustments
│   ├── activity/            # Job activity (HPC, DAV, disk, archive)
│   ├── summaries/           # Charge summaries
│   ├── integration/         # XRAS integration (tables + views)
│   ├── security/            # Roles, API credentials, access control
│   └── operational.py       # Synchronizers, tasks, products
├── python/sam_search.py     # CLI tool for user/project searches
└── tests/                   # Comprehensive test suite
    ├── test_basic_read.py           # Basic ORM queries
    ├── test_crud_operations.py      # Create/update/delete
    ├── test_new_models.py           # 7 new models (51 tests)
    ├── test_views.py                # Database views
    ├── test_schema_validation.py    # Schema drift detection (18 tests)
    └── test_sam_search_cli.py       # CLI integration tests (44 tests)
```

---

## Key ORM Models

### Core Models
- **User** (`users`): System users with UPID, unix_uid
- **Organization** (`organization`): NCAR labs/sections
- **Institution** (`institution`): Universities, research orgs
- **Project** (`project`): Research projects with projcode, unix_gid

### Accounting
- **Account** (`account`): Billing accounts
- **Allocation** (`allocation`): Resource allocations (hierarchical tree)
- **AllocationType** (`allocation_type`): NSC, University, Staff, etc.

### Resources
- **Resource** (`resources`): HPC systems, storage
- **ResourceType** (`resource_type`): HPC, DAV, DISK, ARCHIVE, DATA ACCESS
- **Machine** (`machine`): Physical/logical machines (Derecho, Casper, Gust)
- **Queue** (`queue`): Job queues
- **Facility** (`facility`): UNIV, WNA, NCAR facilities

### Charging Infrastructure
- **Factor** (`factor`): Charging factors (WCH, queue factors) with validity periods
- **Formula** (`formula`): Charging formulas using `@{variable}` template syntax

### Activity/Usage
- **CompJob** / **CompActivity** (`comp_job`, `comp_activity`): Computational jobs
- **HPCActivity** / **HPCCharge** (`hpc_activity`, `hpc_charge`): HPC usage
- **DavActivity** / **DavCharge** (`dav_activity`, `dav_charge`): DAV usage
- **DiskActivity** / **DiskCharge** (`disk_activity`, `disk_charge`): Storage usage
- **ArchiveActivity** / **ArchiveCharge** (`archive_activity`, `archive_charge`): HPSS usage

### Security
- **Role** (`role`): Security roles
- **ApiCredentials** (`api_credentials`): API auth (bcrypt hashed passwords)
- **RoleApiCredentials** (`role_api_credentials`): Role mappings

### Integration
- **XrasResourceRepositoryKeyResource** (`xras_resource_repository_key_resource`): XRAS resource mapping (2 columns ONLY - fixed!)
- **XrasUserView**, **XrasAllocationView**, etc.: Read-only database views

---

## Marshmallow-SQLAlchemy Schemas

### Overview
The API uses marshmallow-sqlalchemy for declarative serialization, replacing manual dictionary construction with type-safe schemas. Schemas automatically handle datetime serialization, nested relationships, and calculated fields.

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
Schemas follow a consistent pattern for optimal performance:

1. **Full Schemas** (`UserSchema`, `ProjectSchema`): All fields + nested relationships - Use for single object detail views
2. **List Schemas** (`UserListSchema`, `ProjectListSchema`): Lightweight for collection endpoints - Excludes expensive nested queries
3. **Summary Schemas** (`UserSummarySchema`, `ProjectSummarySchema`): Minimal fields for references - Used inside other schemas

### Usage Examples
```python
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
```

### Key Schemas

#### AllocationWithUsageSchema ⭐
**Most important schema** - calculates allocation balances matching sam_search.py output.

**Calculated Fields**:
- `used`: Total charges from summary tables
- `remaining`: allocated - used
- `percent_used`: (used / allocated) * 100
- `charges_by_type`: Breakdown by comp/dav/disk/archive
- `adjustments`: Manual charge adjustments (if enabled)

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
Use Method fields to serialize `@property` methods:
```python
class UserSchema(BaseSchema):
    full_name = fields.Method('get_full_name')

    def get_full_name(self, obj):
        return obj.full_name  # Calls @property
```

---

## Allocation Balance Calculations

### Overview
Allocation balances are calculated in real-time from pre-aggregated summary tables. The logic is implemented in `AllocationWithUsageSchema` and matches `Project.get_detailed_allocation_usage()`.

### Calculation Flow
1. Determine resource type (HPC, DAV, DISK, ARCHIVE)
2. Query appropriate summary table(s) for date range
3. Sum charges by type
4. Add manual adjustments (if enabled)
5. Calculate: `remaining = allocated - (charges + adjustments)`

### Summary Tables
All tables are pre-indexed for fast queries by `account_id` and `activity_date`:

- **CompChargeSummary** (`comp_charge_summary`): Daily HPC charges
- **DavChargeSummary** (`dav_charge_summary`): Daily DAV charges
- **DiskChargeSummary** (`disk_charge_summary`): Daily storage charges
- **ArchiveChargeSummary** (`archive_charge_summary`): Daily HPSS archive charges

### Example Calculation
```python
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
```

---

## API Endpoints

### User Endpoints
- `GET /api/v1/users/` → `UserListSchema(many=True)`
- `GET /api/v1/users/<username>` → `UserSchema()`
- `GET /api/v1/users/<username>/projects` → `ProjectListSchema(many=True)`

### Project Endpoints
- `GET /api/v1/projects/` → `ProjectListSchema(many=True)`
- `GET /api/v1/projects/<projcode>` → `ProjectSchema()`
- `GET /api/v1/projects/<projcode>/members` → `UserListSchema(many=True)`
- `GET /api/v1/projects/<projcode>/allocations` → `AllocationWithUsageSchema(many=True)` ⭐
- `GET /api/v1/projects/expiring` → `ProjectListSchema(many=True)`
- `GET /api/v1/projects/recently_expired` → `ProjectListSchema(many=True)`

### Charge/Balance Endpoints ⭐
- `GET /api/v1/projects/<projcode>/charges` - Detailed charge summaries
  - Query params: `start_date`, `end_date`, `resource_id`
  - Returns: All charge types grouped by resource
  - Schema: Custom charge breakdown

- `GET /api/v1/projects/<projcode>/charges/summary` - Aggregated totals
  - Returns: Summary of all active allocations with usage
  - Schema: Allocation summaries with totals

- `GET /api/v1/accounts/<account_id>/balance` - Current balance
  - Query params: `include_adjustments` (default: true)
  - Returns: Real-time allocation balance
  - Schema: `AllocationWithUsageSchema()`

---

## Important Patterns & Conventions

### 1. DateTime Handling
```python
# Database uses NAIVE datetimes (no timezone)
from datetime import datetime
now = datetime.now()  # NOT datetime.now(UTC)

# TIMESTAMP columns auto-update
modified_time = Column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'),
                       onupdate=text('CURRENT_TIMESTAMP'))
```

### 2. Primary Keys
- Single PK: `primary_key=True, autoincrement=True`
- Composite PK: Use `PrimaryKeyConstraint` in `__table_args__`
```python
__table_args__ = (
    PrimaryKeyConstraint('col1', 'col2', name='pk_tablename'),
)
```

### 3. Relationships
- Always use `back_populates` for bidirectional relationships
- Parent side: `relationship('Child', back_populates='parent')`
- Child side: `relationship('Parent', back_populates='children')`

### 4. Mixins Available
- `TimestampMixin`: Adds creation_time, modified_time
- `SoftDeleteMixin`: Adds deleted flag
- `ActiveFlagMixin`: Adds active flag
- `DateRangeMixin`: Adds start_date, end_date

### 5. Views
- Mark views with `__table_args__ = {'info': {'is_view': True}}`
- Never attempt INSERT/UPDATE/DELETE on views

---

## Testing

### Current Status
- **172 tests passed, 10 skipped, 0 failed**
- **Execution time**: ~50 seconds
- **Schema coverage**: 94% (91/97 tables have ORM models)

### Test Execution
```bash
# Run all tests
python3 -m pytest tests/ -v

# Run specific test file
python3 -m pytest tests/test_schema_validation.py -v

# Run with coverage (if needed)
python3 -m pytest tests/ --cov=sam --cov-report=html
```

### Test Files
1. **test_basic_read.py** (26 tests): Basic queries, relationships
2. **test_crud_operations.py** (17 tests): Create, update, delete, transactions
3. **test_new_models.py** (51 tests): Factor, Formula, ApiCredentials, RoleApiCredentials, ProjectCode, FosAoi, ResponsibleParty
4. **test_views.py** (24 tests): XRAS views, read-only enforcement
5. **test_schema_validation.py** (18 tests): Automated schema drift detection ⭐ KEY
6. **test_sam_search_cli.py** (44 tests): CLI integration tests

### Schema Validation Tests ⭐
**Purpose**: Prevent XrasResourceRepositoryKeyResource-style bugs where ORM models don't match database schema.

**What's validated**:
- ✅ All ORM tables exist in database
- ✅ All ORM columns exist in database
- ✅ Type compatibility (SQLAlchemy → MySQL)
- ✅ Primary key validation
- ✅ Foreign key checks
- ✅ Coverage metrics (94% of DB tables have ORM)

**Already caught**: DavActivity composite primary key mismatch!

### Test Priorities
**High Value (Implemented)**:
- ✅ Schema validation - Prevents ORM/DB drift
- ✅ CLI integration - Tests user-facing interface
- ✅ New model tests - Validates recent additions
- ✅ Basic CRUD - Core functionality coverage

**Future Considerations (Optional)**:
- ⚠️ Relationship tests - Deep relationship validation (medium-high effort)
- ⚠️ Performance tests - Query optimization (if needed)
- ⚠️ Load tests - Concurrent operations (if needed)

---

## CLI Tool: sam_search.py

### Usage Examples
```bash
# Find user
./python/sam_search.py user benkirk
./python/sam_search.py user benkirk --list-projects --verbose

# Pattern search
./python/sam_search.py user --search "ben%"
./python/sam_search.py project --search "SCSG%"

# Special searches
./python/sam_search.py user --abandoned
./python/sam_search.py user --has-active-project

# Project lookup
./python/sam_search.py project SCSG0001 --list-users --verbose

# Expirations
./python/sam_search.py project --upcoming-expirations --list-users
./python/sam_search.py project --recent-expirations --list-users

# Global flags
./python/sam_search.py --inactive-projects user benkirk --list-projects
```

### Exit Codes
- `0`: Success
- `1`: Not found
- `2`: Error
- `130`: Keyboard interrupt

---

## Common Queries (sam.queries module)

```python
from sam.queries import (
    get_projects_by_allocation_end_date,
    get_projects_with_expired_allocations
)

# Find expiring projects
expiring = get_projects_by_allocation_end_date(
    session,
    start_date=datetime.now(),
    end_date=datetime.now() + timedelta(days=32),
    facility_names=['UNIV', 'WNA']
)

# Find expired projects
expired = get_projects_with_expired_allocations(
    session,
    max_days_expired=90,
    min_days_expired=365,
    facility_names=['UNIV', 'WNA']
)
```

---

## Known Issues & Gotchas

### 1. DavActivity Composite Primary Key
**Fixed in commit 2fc2595**: `dav_activity` has composite PK `(dav_activity_id, queue_name)`, not just `dav_activity_id`

### 2. XrasResourceRepositoryKeyResource
**Fixed in previous commit**: Model had 5 wrong columns. Correct schema:
- `resource_repository_key` (Integer, PK)
- `resource_id` (Integer, FK to resources, unique)

### 3. Password Hashing
**System uses bcrypt** (~60 chars), not SHA-256 (64 chars)

### 4. Project Code Digits
**Range**: 1-1000 (not 1-10) - actual data has values up to 188

### 5. Type Mappings (SQLAlchemy → MySQL)
- Boolean → BIT(1) or TINYINT(1)
- Float → DOUBLE
- Integer → INT/TINYINT/SMALLINT/MEDIUMINT/BIGINT
- String → VARCHAR/CHAR
- DateTime → DATETIME or TIMESTAMP

### 6. Missing Fields Added
- `archive_activity.modified_time` (TIMESTAMP)
- `dav_activity.modified_time` (TIMESTAMP)

---

## Git Workflow

### Recent Commits
1. **df4d317**: Admin functionality (#13)
2. **2fc2595**: Schema validation tests + DavActivity PK fix
3. Previous: New models (Factor, Formula, ApiCredentials, etc.) + 51 tests

### Branches
- **Current**: `testing`
- **Main branch**: (not set - check before PRs)

### Commit Guidelines
- Use detailed commit messages with markdown formatting
- Include "## Summary" section
- List "### Test Results" when relevant
- End with Claude Code attribution:
```
🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
```

---

## Development Workflow

### Adding New ORM Models
1. Create model in appropriate domain module
2. Add to `sam/__init__.py` imports
3. Create comprehensive tests in `tests/test_new_models.py`
4. Run schema validation: `pytest tests/test_schema_validation.py`
5. Verify all tests pass: `pytest tests/`
6. Commit with detailed message

### Fixing Schema Mismatches
1. Check actual DB schema: `mysql ... -e "SHOW CREATE TABLE tablename\G"`
2. Compare with ORM model definition
3. Update ORM to match database (database is source of truth)
4. Run tests to verify fix
5. Schema validation tests will catch future drift

### Adding CLI Features
1. Add functionality to `python/sam_search.py`
2. Create integration tests in `tests/test_sam_search_cli.py`
3. Test manually: `./python/sam_search.py <command>`
4. Run test suite: `pytest tests/test_sam_search_cli.py`

---

## Key Contacts & Context

- **User**: Ben Kirk (benkirk@ucar.edu)
- **Organization**: CISL USS (University Services Section)
- **Project**: SCSG0001 (CSG systems project)
- **Facilities**: UNIV (university), WNA (Wyoming-NCAR Alliance)
- **Resources**: Derecho, Casper, Gust (HPC); Stratus, Campaign Store (disk)

---

## Code Style & Preferences

1. **Imports**: Use `from ..base import *` for common ORM imports
2. **Type hints**: Use where helpful, especially in method signatures
3. **Docstrings**: Clear, concise, with examples when useful
4. **Comments**: Explain "why" not "what"
5. **Testing**: Integration tests preferred over unit tests for CLI
6. **Error handling**: Proper exit codes, informative error messages
7. **Formatting**: Follow existing patterns in codebase

---

## Quick Reference

```bash
# Most common commands (see full details in respective sections above)
python3 -m pytest tests/ -v                          # Run all tests
./python/sam_search.py user benkirk --list-projects  # User lookup
./python/sam_search.py project SCSG0001 --list-users # Project lookup
git log --oneline -10                                 # Recent commits
```

---

## Common Pitfalls to Avoid

❌ **DON'T** use `datetime.now(UTC)` - database uses naive datetimes
❌ **DON'T** use raw SQL strings in session.execute() - wrap with `text()`
❌ **DON'T** assume single-column primary keys - check database first
❌ **DON'T** modify database schema - ORM follows database
❌ **DON'T** skip schema validation tests after model changes
❌ **DON'T** create files unnecessarily - prefer editing existing files
❌ **DON'T** batch todo completions - mark complete immediately

✅ **DO** use schema validation tests before committing model changes
✅ **DO** check actual database schema when in doubt
✅ **DO** use bidirectional relationships with back_populates
✅ **DO** write integration tests for CLI features
✅ **DO** use proper exit codes (0, 1, 2, 130)
✅ **DO** keep tests fast (<1 minute for full suite)
✅ **DO** update CLAUDE.md when learning new patterns

---

*Last Updated: 2025-11-13*
*Current Branch: api_refactor*
*Test Status: 200 passed, 0 skipped, 0 failed (Phases 1-7 complete)*
