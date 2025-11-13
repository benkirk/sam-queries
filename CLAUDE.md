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

## Testing Strategy

### Test Execution
```bash
# Run all tests
python3 -m pytest tests/ -v

# Run specific test file
python3 -m pytest tests/test_schema_validation.py -v

# Run with coverage (if needed)
python3 -m pytest tests/ --cov=sam --cov-report=html
```

### Test Results (Current)
- **172 tests passed, 10 skipped**
- Execution: ~50 seconds
- Zero failures

### Test Files
1. **test_basic_read.py** (26 tests): Basic queries, relationships
2. **test_crud_operations.py** (17 tests): Create, update, delete, transactions
3. **test_new_models.py** (51 tests): Factor, Formula, ApiCredentials, RoleApiCredentials, ProjectCode, FosAoi, ResponsibleParty
4. **test_views.py** (24 tests): XRAS views, read-only enforcement
5. **test_schema_validation.py** (18 tests): Automated schema drift detection
6. **test_sam_search_cli.py** (44 tests): CLI integration tests

### Schema Validation Tests
**Purpose**: Prevent XrasResourceRepositoryKeyResource-style bugs where ORM models don't match database schema.

**Coverage**:
- ✅ All ORM tables exist in database
- ✅ All ORM columns exist in database
- ✅ Type compatibility (SQLAlchemy → MySQL)
- ✅ Primary key validation
- ✅ Foreign key checks
- ✅ Coverage metrics (94% of DB tables have ORM)

**Already caught**: DavActivity composite primary key mismatch!

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

- **User**: Ben Kirk (<benkirk@ucar.edu>)
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

## Quick Reference Commands

```bash
# Database access
mysql -u root -h 127.0.0.1 -proot sam

# Run tests
python3 -m pytest tests/ -v

# Schema validation
python3 -m pytest tests/test_schema_validation.py -v

# CLI tests
python3 -m pytest tests/test_sam_search_cli.py -v

# User lookup
./python/sam_search.py user benkirk --verbose

# Project lookup
./python/sam_search.py project SCSG0001 --list-users

# Git status
git status
git log --oneline -10

# Table inspection
mysql -u root -h 127.0.0.1 -proot sam -e "SHOW CREATE TABLE <table>\G"
mysql -u root -h 127.0.0.1 -proot sam -e "DESCRIBE <table>"
```

---

## Testing Priorities

### High Value Tests (Already Implemented)
✅ **Schema validation** - Prevents ORM/DB drift
✅ **CLI integration** - Tests user-facing interface
✅ **New model tests** - Validates recent additions
✅ **Basic CRUD** - Core functionality coverage

### Future Considerations (Optional)
⚠️ **Relationship tests** - Deep relationship validation (medium-high effort)
⚠️ **Performance tests** - Query optimization (if needed)
⚠️ **Load tests** - Concurrent operations (if needed)

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

## Success Metrics

- **Test Coverage**: 172 tests passing ✅
- **Schema Coverage**: 94% (91/97 tables) ✅
- **CLI Coverage**: 100% commands tested ✅
- **Test Speed**: ~50 seconds for full suite ✅
- **Zero Failures**: All tests passing ✅

---

*Last Updated: 2025-11-12*
*Current Branch: testing*
*Test Status: 172 passed, 10 skipped, 0 failed*
