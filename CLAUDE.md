# SAM Queries - Project Memory

## Project Overview

**SAM (System for Allocation Management)** - Python ORM and query tools for NCAR's resource allocation and accounting database. Used by CISL to manage HPC allocations, user accounts, project tracking, and charging for Derecho, Casper, and other computational resources.

**Tech Stack**: SQLAlchemy 2.0, MySQL/MariaDB, Python 3.13, pytest

---

## Session Setup

To efficiently set up your environment (conda activation, .env loading):

```bash
# Recommended: Full environment setup (activates conda, loads .env)
source etc/config_env.sh

# Alternative: Load variables only (if python env is already active)
source ../.env
```

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
├── src/sam/              # ORM models (organized by domain)
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
├── src/cli/              # Modular CLI architecture ⭐ NEW
│   ├── core/                # Shared infrastructure (Context, base classes, utils)
│   ├── user/                # User commands and display functions
│   ├── project/             # Project commands and display functions
│   ├── allocations/         # Allocation commands and display functions
│   └── cmds/                # Entry points (search.py, admin.py)
├── src/webapp/           # Flask web application
├── compose.yaml          # Docker Compose config (preferred way to run webapp)
└── tests/                   # Comprehensive test suite
    ├── unit/
    │   ├── test_basic_read.py           # Basic ORM queries
    │   ├── test_crud_operations.py      # Create/update/delete
    │   ├── test_new_models.py           # 7 new models (51 tests)
    │   ├── test_query_functions.py      # Query functions (41 tests) ⭐
    │   └── test_sam_search_cli.py       # CLI integration tests (20 tests)
    ├── integration/
    │   ├── test_schema_validation.py    # Schema drift detection (18 tests)
    │   └── test_views.py                # Database views
    └── api/                 # API endpoint tests
```

---

## Key ORM Models

### Core Models
- **User** (`users`): System users with UPID, unix_uid
  - Properties: `primary_email`, `all_emails`, `full_name`, `display_name`, `all_projects`
  - Class methods: `get_by_username(session, username)`
  - Relationships: `email_addresses`, `projects`, `accounts`

- **Organization** (`organization`): NCAR labs/sections
- **Institution** (`institution`): Universities, research orgs

- **Project** (`project`): Research projects with projcode, unix_gid
  - Properties: `active`, `lead`, `admin`
  - Instance methods: `get_detailed_allocation_usage(resource_name=None, include_adjustments=True)` - Returns dict of usage by resource
  - Class methods: `get_by_projcode(session, projcode)`
  - Relationships: `accounts`, `allocations`, `users`

### Accounting
- **Account** (`account`): Billing accounts
  - Links projects to resources
  - Relationships: `project`, `resource`, `allocations`, `users`

- **Allocation** (`allocation`): Resource allocations (hierarchical tree)
  - Properties: `is_active` (hybrid property - works in Python and SQL)
  - Instance methods: `is_active_at(check_date)` - Check if active at specific date
  - Relationships: `account`, `parent`, `children`, `transactions`

- **AllocationType** (`allocation_type`): NSC, University, Staff, etc.

### Resources
- **Resource** (`resources`): HPC systems, storage
- **ResourceType** (`resource_type`): HPC, DAV, DISK, ARCHIVE, DATA ACCESS
- **Machine** (`machine`): Physical/logical machines (Derecho, Casper, Gust)
  - `is_active`: commissioned and not yet decommissioned (checks commission_date / decommission_date)
- **Queue** (`queue`): Job queues
  - `is_active`: within start_date / end_date window (null start = always started)
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
src/sam.schemas/
├── __init__.py           # Base schema + exports
├── user.py               # User schemas (3 tiers)
├── project.py            # Project schemas (3 tiers)
├── resource.py           # Resource schemas
├── allocation.py         # Allocation/Account schemas ⭐ KEY FILE
└── charges.py            # Charge summary schemas
```

### Three-Tier Schema Strategy
Schemas follow a consistent pattern for optimal performance:

1. **Full Schemas** (`UserSchema`, `ProjectSchema`):
   - All fields + nested relationships
   - Use for: Single object detail views (`GET /api/v1/users/<username>`)
   - Performance: Slower due to relationship loading
   - Example fields: All user data + email_addresses + projects + institutions

2. **List Schemas** (`UserListSchema`, `ProjectListSchema`):
   - Core fields only, no expensive nested queries
   - Use for: Collection endpoints (`GET /api/v1/users/`)
   - Performance: Fast, suitable for pagination
   - Example fields: Basic user data (id, name, email) but NO relationships

3. **Summary Schemas** (`UserSummarySchema`, `ProjectSummarySchema`):
   - Minimal fields for references
   - Use for: Nested within other schemas (e.g., project.lead)
   - Performance: Fastest, no additional queries
   - Example fields: Just id, name, code - essential identifiers only

**When to use which tier:**
- Fetching 1 object → Use Full Schema (e.g., user profile page)
- Fetching 10-100 objects → Use List Schema (e.g., user listing, search results)
- Showing related object → Use Summary Schema (e.g., project's lead within project detail)

### Usage Examples
```python
from sam.schemas import UserSchema, ProjectListSchema, AllocationWithUsageSchema

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
**Most important schema** - calculates allocation balances matching sam-search CLI output.

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

### Using project.get_detailed_allocation_usage()

The `Project.get_detailed_allocation_usage()` method implements this calculation logic and returns usage for all active allocations:

```python
# Get usage for all resources
project = Project.get_by_projcode(session, 'SCSG0001')
usage = project.get_detailed_allocation_usage()

# Returns dict keyed by resource name:
{
    'Derecho': {
        'allocated': 1000000.0,
        'used': 456789.12,
        'remaining': 543210.88,
        'percent_used': 45.68,
        'charges_by_type': {
            'comp': 345678.90,
            'dav': 111110.22,
            'disk': 0.0,
            'archive': 0.0
        },
        'adjustments': []  # If include_adjustments=True
    },
    'Casper': { ... }
}

# Filter by specific resource
derecho_only = project.get_detailed_allocation_usage(resource_name='Derecho')

# Exclude manual adjustments
no_adjustments = project.get_detailed_allocation_usage(include_adjustments=False)
```

### Example SQL Calculation Logic
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
- `SoftDeleteMixin`: Adds deleted flag + `is_active` hybrid (not deleted)
- `ActiveFlagMixin`: Adds active flag + `is_active` hybrid (active == True)
- `DateRangeMixin`: Adds start_date, end_date + `is_active` / `is_currently_active` hybrids
- `SessionMixin`: Adds `self.session` property (`Session.object_session(self)`) — required for `update()` instance methods

### 5. Universal `is_active` Interface
Every ORM model exposes `Model.is_active` as a SQLAlchemy hybrid property.
Use it in both Python and SQL contexts — **never** use raw column comparisons:

```python
# ✅ DO — works in Python and SQL filter()
if project.is_active: ...
session.query(Project).filter(Project.is_active).all()

# ❌ DON'T — exposes column internals, can't invert cleanly
session.query(Project).filter(Project.active == True).all()
session.query(Machine).filter(Machine.decommission_date == None).all()
```

Inversion is `~Model.is_active`:
```python
session.query(User).filter(~User.is_active).all()  # inactive users
```

**Semantics by model type:**
| Mixin / Model | `is_active` meaning |
|---|---|
| `ActiveFlagMixin` (Project, Facility, Panel, …) | `active == True` |
| `SoftDeleteMixin` (Account, …) | `deleted == False` |
| `DateRangeMixin` (AccountUser, UserOrganization, …) | within start/end date range |
| Custom hybrids (Resource, Machine, Queue, PanelSession, …) | commissioned / within date range |
| `User.is_active` | `active == True AND locked == False` |

**Exception — `statistics.py`**: `User.active == True` is kept intentionally
so that `active_users` and `locked_users` remain separate counters.

### 6. Views
- Mark views with `__table_args__ = {'info': {'is_view': True}}`
- Never attempt INSERT/UPDATE/DELETE on views

### 7. Write Operations on ORM Models
Prefer co-locating write logic with the model definition:
- **`update()` → instance method**: validate fields + `self.session.flush()`, return `self`
- **`create()` → classmethod**: takes `session` explicitly, validates, does `session.add(obj)` + `session.flush()`, returns instance

```python
# Instance method pattern (most update ops)
def update(self, *, description=None, active=None):
    if description is not None:
        self.description = description if description.strip() else None
    if active is not None:
        self.active = active
    self.session.flush()
    return self

# Classmethod pattern (creation only)
@classmethod
def create(cls, session, *, required_field, optional_field=None):
    obj = cls(required_field=required_field, optional_field=optional_field)
    session.add(obj)
    session.flush()
    return obj
```

**Caller pattern**: load object first, then call method:
```python
resource = session.get(Resource, resource_id)
if not resource:
    raise ValueError(...)       # caller handles not-found
resource.update(description="new")
```

**What stays in `sam.manage`**: complex multi-entity ops (`add_user_to_project`), audit-trail-heavy ops (`update_allocation` + `log_allocation_transaction`), summary upserts (`summaries.py`), and the `management_transaction` context manager.

### 8. API Route Protection (webapp)

All project-scoped API routes **must** use a decorator from `webapp/api/access_control.py`
rather than a hand-rolled helper. Never write a local `_user_can_access_project`.

| Decorator | Access granted when |
|---|---|
| `@require_project_access` | `VIEW_PROJECTS` permission OR project member |
| `@require_project_member_access(Permission.X)` | permission X OR project member |

The decorators handle the `projcode` → `project` lookup and 403 on failure.
Function signatures receive `project` directly (not `projcode`):

```python
from webapp.api.access_control import require_project_access, require_project_member_access

@bp.route('/<projcode>/allocations', methods=['GET'])
@login_required
@require_project_member_access(Permission.VIEW_ALLOCATIONS)
def get_project_allocations(project):   # ← project object, not projcode
    ...
```

### 9. Form Validation in HTMX and API Routes

**The rule:** any validated POST/PUT route — HTMX (`webapp/dashboards/.../*_routes.py`)
**or** API (`webapp/api/v1/...`) — loads input via a schema from
`sam.schemas.forms`. If no schema fits, **add one first**, then wire the
route. Never write `datetime.strptime`, `parse_input_end_date`, `float()`,
or `int()` coercion ladders inline.

**Before writing any POST/PUT handler:**
1. Look in `src/sam/schemas/forms/` for an existing schema that matches.
   Exports live in `src/sam/schemas/forms/__init__.py`.
2. If none fits, add a new `HtmxFormSchema` subclass to the appropriate
   domain file (`projects.py`, `user.py`, `orgs.py`, ...) and export it
   from `__init__.py`.
3. Only then write the route.

FK existence checks (e.g. `db.session.get(User, lead_id)`) **stay in the
route** — schemas don't touch the DB. That's the one thing that belongs
inline, and it's explicit in each schema's docstring.

**Pattern — POST (all required fields enforced by the schema):**
```python
from sam.schemas.forms import AddMemberForm
from marshmallow import ValidationError

# HTMX: pre-process request.form — drop empty strings so Int/Float/Date
# fields fall back to load_default, and inject explicit False for unchecked
# checkboxes (absent from request.form when unchecked).
data = {k: v for k, v in request.form.items() if v != ''}
data['charging_exempt'] = 'charging_exempt' in request.form  # if applicable

# API: data = request.get_json()

try:
    form_data = AddMemberForm().load(data)
except ValidationError as e:
    errors = AddMemberForm.flatten_errors(e.messages)
    return _reload_form(errors)   # HTMX re-render, or JSON 400

# FK existence checks live here (require DB access)
if form_data.get('lead_id') and not db.session.get(User, form_data['lead_id']):
    errors.append('Selected lead does not exist.')
```

**Pattern — PUT (partial update):**
```python
form_data = EditAllocationForm().load(data, partial=True)

# Gate on original data dict, NOT form_data keys — load_default fills
# absent fields with None, which would silently clear them.
updates = {}
if 'amount' in data:
    updates['amount'] = form_data['amount']
if 'end_date' in data:
    updates['end_date'] = form_data.get('end_date')   # datetime or None
```

**HtmxFormSchema gives you for free:** `unknown=EXCLUDE` (silently drops
CSRF tokens, stray fields), `flatten_errors()` for template-friendly lists,
and a clear separation from ORM serialization (`sam.schemas/` proper).

---

## Common ORM Patterns

### Accessing User Emails
```python
# Get primary email (preferred method)
user = User.get_by_username(session, 'benkirk')
print(user.primary_email)  # Returns primary email or first active email

# Get all emails
all_emails = user.all_emails  # Returns list of email strings

# Access email relationship objects
for email in user.email_addresses:
    print(f"{email.email_address} (primary: {email.is_primary})")
```

### Checking Allocation Status
```python
from datetime import datetime, timedelta

# Check if allocation is currently active (hybrid property)
if allocation.is_active:
    print("Allocation is active")

# Check if allocation was/will be active at specific date
check_date = datetime.now() + timedelta(days=30)
if allocation.is_active_at(check_date):
    print(f"Will be active on {check_date}")

# Use in queries (SQL expression)
active_allocations = session.query(Allocation).filter(
    Allocation.is_active
).all()
```

### Project Queries
```python
# Get project by code
project = Project.get_by_projcode(session, 'SCSG0001')

# Get project allocation usage (returns dict keyed by resource name)
usage = project.get_detailed_allocation_usage()
for resource_name, details in usage.items():
    print(f"{resource_name}:")
    print(f"  Allocated: {details['allocated']}")
    print(f"  Used: {details['used']}")
    print(f"  Remaining: {details['remaining']}")
    print(f"  Percent Used: {details['percent_used']}%")
    print(f"  Charges by type: {details['charges_by_type']}")

# Filter by specific resource
derecho_usage = project.get_detailed_allocation_usage(resource_name='Derecho')

# Exclude manual adjustments
usage_no_adj = project.get_detailed_allocation_usage(include_adjustments=False)
```

### User Project Access
```python
# Get user's active projects
user = User.get_by_username(session, 'benkirk')
for project in user.active_projects():
    print(f"{project.projcode}: {project.title}")

# Get all projects (including inactive)
for project in user.all_projects:
    print(f"{project.projcode} (active: {project.active})")
```

---

## Testing

### Current Status
- **~1400 tests** passing in ~65 seconds under xdist `-n auto`
- **Parallel execution**: pytest-xdist, all cores by default (configured in root `pytest.ini`)
- **Isolation model**: per-test SAVEPOINT rollback (`join_transaction_mode="create_savepoint"`),
  so xdist workers share one `sam_test` database without stepping on each other
- **Safety guard**: `tests/conftest.py` refuses to run against any database
  other than the allowlisted `mysql-test` container (host port 3307)

### Test Data Strategy — Two Tiers
- **Layer 1 — Representative fixtures** (`tests/conftest.py`):
  `active_project`, `multi_project_user`, `hpc_resource`, `any_*` —
  pick ANY snapshot row matching a structural shape. Used by read-path
  tests that want snapshot data but don't care about specific
  projcodes/usernames. Survives obfuscated-snapshot refreshes as long
  as at least one row of each shape exists.
- **Layer 2 — Factories** (`tests/factories/`): plain builder
  functions (`make_user`, `make_project`, `make_allocation`, ...).
  `session` is the first positional arg. Each builder auto-builds the
  minimum FK graph it needs, calls `session.flush()`, returns the
  flushed instance. Used by write-path tests that need exact
  counts/values. Composes cleanly with Layer 1 in the same test.

**Never blend Layer 1 and Layer 2 inside a single helper.** But a test
may compose both: e.g. `active_project` for a Project+Account graph
and `make_user(session)` for a fresh user that's unambiguously not on
that project.

### Test Execution
```bash
# One-time setup: start the isolated test container + set URL
docker compose --profile test up -d mysql-test
export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'

# Fast iteration (parallel, no coverage)
source etc/config_env.sh && pytest  # ~65 seconds

# With coverage report
source etc/config_env.sh && pytest --cov=src --cov-report=html

# Specific test file
pytest tests/unit/test_query_functions.py -v

# Force serial execution
pytest -n 0
```

### Key Test Directories
- `tests/unit/` — ORM models, query functions, CLI (CliRunner), webapp unit
  (admin model views, OIDC), cache behavior, allocation performance
- `tests/integration/` — schema validation (ORM↔MySQL drift),
  database views, system_status flow/dashboard, CLI subprocess smoke
- `tests/api/` — all API endpoint tests + Marshmallow schema tests
- `tests/factories/` — Layer-2 builder module (core, resources,
  projects, operational)

### The `system_status` tier

The status tests use a per-worker SQLite tempfile bound at
`SQLALCHEMY_BINDS['system_status']`. Flask-SQLAlchemy routes
`DerechoStatus`/`CasperStatus`/etc queries to SQLite via
`__bind_key__`, so test fixtures and route handlers both see the same
engine/same data. The schema is materialized via
`db.create_all(bind_key='system_status')` in the `app` session-scoped
fixture. Per-test isolation is via `DELETE FROM` on all status tables
(faster than SAVEPOINT bridging for SQLite).

**Critical**: `FLASK_ACTIVE=1` is set in `pytest_configure` (not inside
the `app` fixture) because `system_status.base.StatusBase` is resolved
at module import time. If a test module does `from system_status import
DerechoStatus` at module level, it runs during collection before any
fixture — without `FLASK_ACTIVE`, `StatusBase` becomes a standalone
`declarative_base()` and the bind routing never engages.

---

## CLI Tools: sam-search & sam-admin

### Architecture

The CLI has been refactored into a modular, class-based architecture supporting both search and administrative commands:

```
src/cli/
├── core/           # Shared infrastructure
│   ├── context.py     # Context class (session, console, flags)
│   ├── base.py        # Base command classes (BaseCommand, BaseUserCommand, etc.)
│   └── utils.py       # Exit codes, utilities
├── user/           # User domain
│   ├── commands.py    # UserSearchCommand, UserAdminCommand, etc.
│   └── display.py     # display_user(), display_user_projects()
├── project/        # Project domain
│   ├── commands.py    # ProjectSearchCommand, ProjectAdminCommand, etc.
│   └── display.py     # display_project(), display_project_users()
├── allocations/    # Allocation domain
│   ├── commands.py    # AllocationSearchCommand
│   └── display.py     # display_allocation_summary()
└── cmds/           # Entry points
    ├── search.py      # sam-search CLI (user-facing)
    └── admin.py       # sam-admin CLI (administrative)
```

**Design Principles:**
- Command classes encapsulate business logic, enable inheritance
- Display functions are stateless, reusable
- Entry points delegate to command classes
- Admin commands extend search commands via inheritance

### sam-search Usage Examples

```bash
# Find user
sam-search user benkirk
sam-search user benkirk --list-projects --verbose

# Pattern search
sam-search user --search "ben%"
sam-search project --search "SCSG%"

# Special searches
sam-search user --abandoned
sam-search user --has-active-project

# Project lookup
sam-search project SCSG0001 --list-users --verbose

# Expirations
sam-search project --upcoming-expirations --list-users
sam-search project --recent-expirations --list-users

# Allocation queries with flexible grouping
sam-search allocations --resource Derecho --total-facilities --total-types
sam-search allocations --resource Derecho,Casper --allocation-type Small

# Global flags
sam-search --inactive-projects user benkirk --list-projects
```

### sam-admin Usage Examples ⭐ NEW

Administrative commands that extend search functionality:

```bash
# User validation
sam-admin user benkirk --validate

# Project validation
sam-admin project SCSG0001 --validate

# Project reconciliation
sam-admin project SCSG0001 --reconcile

# Admin commands include all search functionality
sam-admin user benkirk --list-projects  # Works like sam-search
sam-admin project SCSG0001 --list-users  # Works like sam-search
```

### Exit Codes
- `0`: Success
- `1`: Not found
- `2`: Error
- `130`: Keyboard interrupt

### Adding New Commands

See `src/cli/README.md` for detailed guide on extending the CLI with new commands.

---

## Common Queries (sam.queries module)

```python
from sam.queries import (
    get_projects_by_allocation_end_date,
    get_projects_with_expired_allocations
)
from datetime import datetime, timedelta

# Find expiring projects
# Returns: List[Tuple[Project, Allocation, str, Optional[int]]]
#          Tuple elements: (project, allocation, resource_name, days_remaining)
expiring = get_projects_by_allocation_end_date(
    session,
    start_date=datetime.now(),
    end_date=datetime.now() + timedelta(days=32),
    facility_names=['UNIV', 'WNA']
)

# Proper usage with tuple unpacking
for project, allocation, resource_name, days_remaining in expiring:
    print(f"{project.projcode}: {resource_name} expires in {days_remaining} days")

# Find expired projects
# Returns same structure: List[Tuple[Project, Allocation, str, Optional[int]]]
#                         (project, allocation, resource_name, days_since_expiration)
expired = get_projects_with_expired_allocations(
    session,
    max_days_expired=90,
    min_days_expired=365,
    facility_names=['UNIV', 'WNA']
)

for project, allocation, resource_name, days_since in expired:
    print(f"{project.projcode}: {resource_name} expired {days_since} days ago")
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

### Running the Web Application
```bash
# Preferred method: Docker Compose
docker compose up

# Access at http://localhost:5050
# Auth disabled by default (DEV_AUTO_LOGIN_USER=benkirk)
```

### Adding New ORM Models
1. Create model in appropriate domain module; add `SessionMixin` if write methods are needed
2. Add `update()` instance method and/or `create()` classmethod for any write operations (do NOT create standalone functions in `sam/manage/`)
3. Add to `sam/__init__.py` imports
4. Create comprehensive tests in `tests/unit/test_new_models.py`
5. Run schema validation: `pytest tests/integration/test_schema_validation.py`
6. Verify all tests pass: `pytest` (fast, ~65s)
7. Commit with detailed message

### Fixing Schema Mismatches
1. Check actual DB schema: `mysql ... -e "SHOW CREATE TABLE tablename\G"`
2. Compare with ORM model definition
3. Update ORM to match database (database is source of truth)
4. Run tests to verify fix
5. Schema validation tests will catch future drift

### Adding a New Validated POST/PUT Route (HTMX or API)
1. Check `src/sam/schemas/forms/__init__.py` for an existing form schema.
2. If none fits, add a new `HtmxFormSchema` subclass in the appropriate
   domain file and export it from `__init__.py` **before** writing the route.
3. Write the route: load input with the schema, keep FK existence checks
   inline, gate `updates` dicts on original `data` keys for PUT.
4. See §9 *Form Validation in HTMX and API Routes* for the full pattern.

### Skipping CI for trivial changes
Put `[skip ci]`, `[ci skip]`, or `[no ci]` in either the commit message
or the PR title to skip test/lint workflows (`sam-ci-docker`,
`sam-ci-conda_make`, `test-install`, `ci-staging`, `mega-linter`). Use
for comment-only edits, non-code config tweaks, or changes covered by
separate manual verification.

Does NOT skip: `build-images-cirrus-deploy` and `deploy-staging` — the
deploy-path TruffleHog scan runs unconditionally.

---

## Key Contacts & Context

- **User**: Ben Kirk (benkirk@ucar.edu)
- **Organization**: CISL USS (University Services Section)
- **Project**: SCSG0001 (CSG systems project)
- **Facilities**: UNIV (university), WNA (Wyoming-NCAR Alliance)
- **Resources**: Derecho, Casper, Gust (HPC); Stratus, Campaign Store (disk)

---

## Display Formatting — `sam.fmt`

All number, date, percentage, and size formatting goes through `src/sam/fmt.py`.
Jinja2 filters are registered in `create_app()` — use them in every template.

| Need | Jinja2 filter | Python (CLI) |
|---|---|---|
| Integer / compact number | `{{ x \| fmt_number }}` | `fmt.number(x)` |
| Percentage (0–100) | `{{ x \| fmt_pct }}` | `fmt.pct(x)` |
| Date / datetime | `{{ x \| fmt_date }}` | `fmt.date_str(x)` |
| Byte size | `{{ x \| fmt_size }}` | `fmt.size(x)` |

**Key behaviours**
- Numbers ≤ 100,000 → exact with commas (`34,283`); above → compact (`68.6M`)
- `None` → `'—'` by default for all filters (no manual `if x else '—'` needed)
- `fmt_pct(decimals=N)`, `fmt_date(fmt='%b %Y')`, `fmt_number(raw=True)` for overrides
- `SAM_RAW_OUTPUT=1` env-var forces exact integers everywhere (scripting/grepping)
- `fmt.mpl_number_formatter()` for matplotlib y-axis tick labels

**Do NOT** use raw `'{:,.0f}'.format(x)`, `'%.1f'|format(x)%`, or `.strftime(…)` in
templates or CLI display code — route through `sam.fmt` instead.

The migration plan is documented in `docs/plans/FORMAT_DISPLAY.md`.

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
# Web application
docker compose up                                  # Start webapp (http://localhost:5050)

# Testing
source etc/config_env.sh && pytest                 # Fast tests (~65s, parallel)
source etc/config_env.sh && pytest --cov=src       # With coverage report

# CLI - Search
sam-search user benkirk --list-projects           # User lookup
sam-search project SCSG0001 --list-users          # Project lookup
sam-search allocations --resource Derecho         # Allocation queries

# CLI - Admin
sam-admin user benkirk --validate                 # Validate user data
sam-admin project SCSG0001 --validate             # Validate project
sam-admin project SCSG0001 --reconcile            # Reconcile allocations

# CLI - JSON output (machine-readable)
sam-search --format json user benkirk | jq        # User envelope (kind=user)
sam-search --format json project SCSG0001 | jq    # Project envelope w/ allocations, tree, users
sam-search --format json allocations --total-resources --total-facilities --total-types
sam-search --format json accounting --last 7d
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
❌ **DON'T** use `user.email` - use `user.primary_email` instead (no `email` attribute exists)
❌ **DON'T** use raw column comparisons (`Model.active == True`, `Model.deleted == False`, raw date checks) — use `Model.is_active` instead (universal hybrid property across all models)
❌ **DON'T** pass `session` to `project.get_detailed_allocation_usage()` - it uses SessionMixin internally
❌ **DON'T** forget to unpack tuples from `get_projects_by_allocation_end_date()` - returns `(project, allocation, resource_name, days)`
❌ **DON'T** add standalone `update_*(session, id, ...)` functions to `sam/manage/` — put `update()` instance methods and `create()` classmethods directly on the ORM model instead
❌ **DON'T** write a local `_user_can_access_project` helper in a route file — use decorators from `webapp.api.access_control` (centralized, tested, consistent)
❌ **DON'T** use `datetime.strptime`, `parse_input_end_date`, `float()`, or `int()` coercion ladders directly in **any** POST/PUT route handler (HTMX or API) — load input via a schema from `sam.schemas.forms`. If no schema fits, add one to the appropriate domain file and export it from `sam/schemas/forms/__init__.py` **before** writing the route. See §9 and the "Adding a New Validated POST/PUT Route" workflow.

✅ **DO** use schema validation tests before committing model changes
✅ **DO** check actual database schema when in doubt
✅ **DO** use bidirectional relationships with back_populates
✅ **DO** write tests for query functions (see test_query_functions.py)
✅ **DO** use proper exit codes (0, 1, 2, 130)
✅ **DO** run `pytest` for fast iteration (~65s, parallel via xdist)
✅ **DO** use `Model.is_active` for any active check — it's a hybrid property on every model (works in Python and SQL, supports `~Model.is_active` inversion)
✅ **DO** unpack query result tuples properly when using `get_projects_by_allocation_end_date()`
✅ **DO** add `SessionMixin` to any ORM model that needs an `update()` method (provides `self.session`)
✅ **DO** use `@require_project_access` or `@require_project_member_access(Permission.X)` on all project-scoped GET routes — function receives `project`, not `projcode`
✅ **DO** use `FormSchema().load(data)` for POST mutations, `FormSchema().load(data, partial=True)` for PUT — gate the `updates` dict on keys present in the original `data` dict, not the form output

