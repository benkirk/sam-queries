# SAM Queries

**Python ORM, CLI tools, and REST API for NCAR's System for Allocation Management**

SAM Queries provides programmatic access to NCAR's resource allocation and accounting system, enabling automated queries, reporting, and management of HPC allocations for Derecho, Casper, and other computational resources.

---

## What is SAM?

**SAM (System for Allocation Management)** is NCAR's centralized database system for managing:
- HPC resource allocations - Core-hours, storage quotas, archive usage
- User accounts - Authentication, access control, project membership
- Project tracking - Research projects, PIs, collaborators, contracts
- Usage accounting - Charge tracking across compute, storage, and data services

This repository provides tools to interact with SAM data programmatically, replacing manual database queries with a type-safe Python ORM and user-friendly interfaces.

---

## Features

### Python ORM (SQLAlchemy 2.0)
- 91+ ORM models covering 94% of SAM database tables
- Type-safe queries with full relationship navigation
- Automated schema validation to prevent drift
- Test coverage for all major models

### CLI Tool (`sam_search.py`)
- User and project lookups by username or project code
- Pattern matching with SQL wildcards
- Track upcoming and expired allocations
- Proper exit codes for automation

### Web UI (Flask-Admin)
- Admin dashboard with CRUD operations for SAM tables
- Role-based access control
- Expiration monitoring dashboards
- Bootstrap 4 interface

### REST API
- RESTful endpoints for users, projects, allocations, charges
- JSON responses using Marshmallow-SQLAlchemy schemas
- Real-time allocation balances with charge breakdowns
- Session-based authentication with RBAC

---

## Quick Start

If you already have conda and database credentials:

```bash
# 1. Create conda environment
make conda-env

# 2. Create .env with your credentials
cat > .env << 'EOF'
SAM_DB_USERNAME=your_username
SAM_DB_SERVER=sam-sql.ucar.edu
SAM_DB_PASSWORD='your_password'
EOF
chmod 600 .env

# 3. Try the CLI tool
./python/sam_search.py user <your_username>

# 4. Run tests to verify setup
cd tests && pytest -v
```

For detailed setup instructions, see **[CONTRIBUTING.md](CONTRIBUTING.md)**.

---

## Installation

### Prerequisites

- **Conda** (miniconda or anaconda)
- **Git**
- **Database credentials** (contact CISL staff)
- **Docker** (optional, for local development database)

### Setup Steps

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd sam-queries
   ```

2. **Create conda environment**
   ```bash
   make conda-env
   source etc/config_env.sh
   ```

3. **Configure database credentials**

   Create `.env` file in project root:
   ```bash
   SAM_DB_USERNAME=your_username
   SAM_DB_SERVER=sam-sql.ucar.edu
   SAM_DB_PASSWORD='your_password'
   ```

   **Important:** Wrap passwords with special characters in single quotes.

4. **Verify installation**
   ```bash
   # Test CLI
   ./python/sam_search.py user --search "a%" | head -10

   # Run test suite
   cd tests && pytest -v
   ```

For full setup guide including local development database, see **[CONTRIBUTING.md](CONTRIBUTING.md)**.

---

## Usage Examples

### CLI Tool

The `sam_search.py` CLI provides access to SAM data:

```bash
# Find a user
./python/sam_search.py user benkirk

# List user's projects with allocations
./python/sam_search.py user benkirk --list-projects --verbose

# Search for users (SQL wildcards)
./python/sam_search.py user --search "ben%"

# Find a project
./python/sam_search.py project SCSG0001 --list-users

# Find projects expiring soon (next 32 days)
./python/sam_search.py project --upcoming-expirations

# Find recently expired projects (last 90 days)
./python/sam_search.py project --recent-expirations --list-users

# Find users with no active projects
./python/sam_search.py user --abandoned
```

**Exit codes:** `0` = success, `1` = not found, `2` = error, `130` = interrupted

### Python ORM

Use the SQLAlchemy ORM for complex queries and data analysis:

```python
from sam.session import create_sam_engine
from sam import User, Project, Allocation, Account
from sqlalchemy.orm import Session

# Create database session
engine, _ = create_sam_engine()
session = Session(engine)

# Find a user
user = User.get_by_username(session, 'benkirk')
print(f"{user.full_name} ({user.primary_email})")

# Get user's active projects
for project in user.active_projects:
    print(f"  {project.projcode}: {project.title}")

# Find a project
project = Project.get_by_projcode(session, 'SCSG0001')
print(f"PI: {project.lead.full_name}")

# Get project allocations with usage
usage = project.get_detailed_allocation_usage()
for resource_name, details in usage.items():
    print(f"  {resource_name}: {details['used']:.2f}/{details['allocated']:.2f} ({details['percent_used']:.1f}% used)")

# Find projects expiring soon
from sam.queries import get_projects_by_allocation_end_date
from datetime import datetime, timedelta

expiring = get_projects_by_allocation_end_date(
    session,
    start_date=datetime.now(),
    end_date=datetime.now() + timedelta(days=32),
    facility_names=['UNIV', 'WNA']
)

# Returns tuples of (project, allocation, resource_name, days_remaining)
for project, allocation, resource_name, days_remaining in expiring:
    print(f"{project.projcode}: {resource_name} expires in {days_remaining} days")
```

For detailed ORM documentation, see **[CLAUDE.md](CLAUDE.md)**.

### Web UI

Launch the Flask web interface:

```bash
cd python/webapp
python run.py
```

Access at `http://localhost:5050`

Features:
- Dashboard with statistics and expiration monitoring
- CRUD operations for users, projects, allocations
- Role-based access control (admin, facility_manager, project_lead, user, analyst)
- Bootstrap 4 interface

For detailed Web UI documentation, see **[python/webapp/README.md](python/webapp/README.md)**.

### REST API

Programmatic access via JSON REST API:

```bash
# Authenticate and save session cookie
curl -c cookies.txt -X POST http://localhost:5050/auth/login \
  -d "username=your_username&password=your_password"

# Get user details
curl -b cookies.txt http://localhost:5050/api/v1/users/benkirk

# Get user's projects
curl -b cookies.txt http://localhost:5050/api/v1/users/benkirk/projects

# Get project details
curl -b cookies.txt http://localhost:5050/api/v1/projects/SCSG0001

# Get project allocations with real-time usage
curl -b cookies.txt http://localhost:5050/api/v1/projects/SCSG0001/allocations

# Get projects expiring in next 90 days
curl -b cookies.txt "http://localhost:5050/api/v1/projects/expiring?days=90&facility_names=UNIV"

# Get recently expired projects (90-365 days ago)
curl -b cookies.txt "http://localhost:5050/api/v1/projects/recently_expired?min_days=90&max_days=365"

# Get account balance
curl -b cookies.txt http://localhost:5050/api/v1/accounts/12345/balance
```

**Example Response:**

```json
{
  "allocation_id": 12345,
  "allocated": 1000000.0,
  "used": 456789.12,
  "remaining": 543210.88,
  "percent_used": 45.68,
  "start_date": "2024-01-01T00:00:00",
  "end_date": "2024-12-31T23:59:59",
  "charges_by_type": {
    "comp": 345678.90,
    "dav": 111110.22,
    "disk": 0.0,
    "archive": 0.0
  },
  "resource": {
    "resource_id": 42,
    "name": "Derecho"
  }
}
```

For complete API documentation, see **[python/webapp/README.md](python/webapp/README.md#rest-api)**.

---

## Project Structure

```
sam-queries/
├── README.md                    # This file
├── CONTRIBUTING.md              # Detailed setup and contribution guide
├── CLAUDE.md                    # Comprehensive technical reference
├── Makefile                     # Build automation (conda-env, etc.)
├── conda-env.yaml              # Conda environment specification
├── .env                         # Database credentials (you create this)
│
├── docs/                        # Project documentation
│   ├── SYSTEM_DASHBOARD_PLAN.md # System status dashboard implementation plan
│   └── HPC_DATA_COLLECTORS_GUIDE.md # Guide for implementing HPC data collectors
│
├── python/
│   ├── sam/                     # SQLAlchemy ORM models (SAM database)
│   │   ├── __init__.py          # Main exports
│   │   ├── base.py              # Base classes, mixins
│   │   ├── session.py           # Database session factory
│   │   ├── queries.py           # Common query functions
│   │   ├── core/                # Users, organizations, institutions
│   │   ├── projects/            # Projects, contracts, areas
│   │   ├── resources/           # Resources, machines, facilities
│   │   ├── accounting/          # Accounts, allocations, adjustments
│   │   ├── activity/            # Job activity (HPC, DAV, disk, archive)
│   │   ├── summaries/           # Charge summaries
│   │   ├── integration/         # XRAS integration
│   │   └── security/            # Roles, API credentials
│   │
│   ├── system_status/           # System status ORM models (system_status database)
│   │   ├── __init__.py          # Main exports
│   │   ├── base.py              # Base classes, mixins for status models
│   │   ├── session/             # Database session management
│   │   │   └── __init__.py      # Session factory for system_status DB
│   │   ├── models/              # Status tracking models
│   │   │   ├── __init__.py      # Model exports
│   │   │   ├── derecho.py       # Derecho HPC status (3 tables)
│   │   │   ├── casper.py        # Casper HPC status (3 tables)
│   │   │   ├── jupyterhub.py    # JupyterHub status
│   │   │   └── outages.py       # System outages and reservations
│   │   └── queries/             # Status query functions
│   │       └── __init__.py      # Common status queries
│   │
│   ├── sam_search.py            # CLI tool for user/project searches
│   │
│   └── webapp/                   # Flask web application
│       ├── README.md            # Web UI documentation
│       ├── run.py               # Development server
│       ├── auth/                # Authentication (providers, models, blueprint)
│       ├── admin/               # Flask-Admin interface
│       ├── dashboards/          # Dashboard blueprints
│       │   ├── user/            # User dashboard
│       │   └── status/          # System status dashboard
│       │       └── blueprint.py # Status dashboard routes
│       ├── api/                 # REST API v1 endpoints
│       │   └── v1/
│       │       ├── status.py    # System status API endpoints
│       │       └── ...          # Other API endpoints
│       ├── schemas/             # Marshmallow-SQLAlchemy schemas
│       ├── utils/               # RBAC, utilities
│       └── templates/           # Jinja2 templates
│           └── dashboards/
│               ├── user/        # User dashboard templates
│               └── status/      # Status dashboard templates
│                   └── dashboard.html # Main status dashboard
│
├── scripts/                     # Utility scripts
│   ├── setup_status_db.py       # Create system_status database tables
│   ├── test_status_db.py        # Test system_status database connection
│   ├── cleanup_status_data.py   # Clean up old status snapshots (7-day retention)
│   ├── ingest_mock_status.py    # Ingest mock status data for testing
│   └── create_status_db.sql     # SQL script for database creation
│
├── tests/                       # Comprehensive test suite (209 tests)
│   ├── pytest.ini               # Pytest configuration
│   ├── conftest.py              # Shared fixtures
│   ├── unit/                    # Unit tests
│   │   ├── test_basic_read.py   # Basic ORM queries
│   │   ├── test_crud_operations.py # Create/update/delete
│   │   └── test_new_models.py   # New model tests
│   ├── integration/             # Integration tests
│   │   ├── test_schema_validation.py # Schema drift detection
│   │   ├── test_sam_search_cli.py # CLI integration tests
│   │   └── test_views.py        # Database views
│   ├── api/                     # API/schema tests
│   │   ├── test_schemas.py      # Marshmallow schema tests
│   │   └── test_allocation_schemas.py # Allocation schemas
│   ├── mock_data/               # Test data
│   │   └── status_mock_data.json # Mock system status data
│   ├── tools/                   # Utility scripts
│   ├── fixtures/                # Shared test configuration
│   └── docs/                    # Test documentation
│       ├── README.md            # Testing guide
│       ├── TEST_RESULTS_SUMMARY.md # Test results
│       └── CURRENT_PLAN.md      # Improvement history
│
├── containers/
│   └── sam-sql-dev/             # Docker container for local MySQL
│       ├── README.md            # Container documentation
│       ├── docker_start.sh      # Start local database
│       └── bootstrap_clone.py   # Clone production data subset
│
├── bin/
│   └── sam_search               # Bash wrapper for sam_search.py
│
├── etc/
│   └── config_env.sh            # Environment configuration script
│
└── sql/                         # SQL utilities and queries
    └── queries/                 # Example SQL queries
```

---

## Documentation

### User Documentation
- **[README.md](README.md)** - This file (overview and quick start)
- **[CONTRIBUTING.md](CONTRIBUTING.md)** - Comprehensive setup and development guide
- **[python/webapp/README.md](python/webapp/README.md)** - Web UI and REST API documentation
- **[tests/docs/README.md](tests/docs/README.md)** - Testing guide and best practices

### Technical Reference
- **[CLAUDE.md](CLAUDE.md)** - Detailed technical documentation:
  - Complete ORM model reference
  - Database schema patterns
  - Marshmallow-SQLAlchemy schema usage
  - Allocation balance calculation logic
  - Common query patterns and examples
  - Known issues and gotchas
  - Development workflow

### API Documentation
- **REST API endpoints** - See [python/webapp/README.md](python/webapp/README.md#rest-api)
- **Marshmallow schemas** - See [CLAUDE.md](CLAUDE.md#marshmallow-sqlalchemy-schemas)
- **ORM models** - See [CLAUDE.md](CLAUDE.md#key-orm-models)

---

## Testing

The project includes a test suite with 200+ tests covering:

- Schema validation - Prevents ORM/database drift (18 tests)
- Basic queries - Core ORM functionality (26 tests)
- CRUD operations - Create/update/delete (17 tests)
- CLI integration - End-to-end CLI testing (44 tests)
- API schemas - Marshmallow serialization (multiple tests)
- Database views - XRAS integration views (24 tests)
- New models - Recent model additions (51 tests)

**Run all tests:**
```bash
cd tests && pytest -v
```

**Test specific areas:**
```bash
# Schema validation
cd tests && pytest integration/test_schema_validation.py -v

# CLI integration
cd tests && pytest integration/test_sam_search_cli.py -v

# Marshmallow schemas
cd tests && pytest api/test_schemas.py -v

# Run by category
cd tests && pytest unit/ -v          # Unit tests
cd tests && pytest integration/ -v   # Integration tests
cd tests && pytest api/ -v           # API tests
```

**Expected results:**
- With read-only database: ~190 passed, ~20 skipped (CRUD tests)
- With local development database: 209 passed, 10 skipped
- Execution time: ~52 seconds

For detailed testing documentation, see **[tests/docs/README.md](tests/docs/README.md)**.

---

## Development

### Contributing

Before submitting changes:

1. **Read the guides:**
   - [CONTRIBUTING.md](CONTRIBUTING.md) - Setup, workflow, code style
   - [CLAUDE.md](CLAUDE.md) - Technical patterns and conventions

2. **Set up your environment:**
   ```bash
   make conda-env
   source etc/config_env.sh
   ```

3. **Make changes following project patterns:**
   - Use existing code as examples
   - Follow SQLAlchemy 2.0 patterns
   - Add tests for new features
   - Run schema validation after ORM changes

4. **Run tests before committing:**
   ```bash
   cd tests && pytest -v
   ```

5. **Submit pull request** with:
   - Clear description of changes
   - Test results
   - Any breaking changes noted

### Key Development Guidelines

**Database is source of truth:**
- Never modify database schema
- ORM models follow database structure
- Use schema validation tests to prevent drift

**Always test:**
- Run full test suite before committing
- Add tests for new features
- Use integration tests for CLI features
- Keep tests fast (<1 minute for full suite)

**Code style:**
- Follow existing patterns in codebase
- Use type hints for function signatures
- Write clear docstrings with examples
- Comments explain "why" not "what"
- Use proper exit codes (0=success, 1=not found, 2=error)

For complete development guide, see **[CONTRIBUTING.md](CONTRIBUTING.md)**.

---

## Common Use Cases

### Finding User Information
```bash
# CLI
./python/sam_search.py user benkirk --list-projects

# Python
from sam import User
user = User.get_by_username(session, 'benkirk')
print(user.email_addresses)

# API
curl -b cookies.txt http://localhost:5050/api/v1/users/benkirk
```

### Monitoring Allocation Expirations
```bash
# CLI
./python/sam_search.py project --upcoming-expirations

# Python
from sam.queries import get_projects_by_allocation_end_date
expiring = get_projects_by_allocation_end_date(session, ...)

# API
curl -b cookies.txt "http://localhost:5050/api/v1/projects/expiring?days=30"
```

### Checking Allocation Balances
```bash
# CLI
./python/sam_search.py project SCSG0001 --verbose

# Python
usage = project.get_detailed_allocation_usage(session, allocation)
print(f"Used: {usage['used']}, Remaining: {usage['remaining']}")

# API
curl -b cookies.txt http://localhost:5050/api/v1/accounts/12345/balance
```

### Automated Reporting
```python
# Generate weekly expiration report
from sam.queries import get_projects_by_allocation_end_date
from datetime import datetime, timedelta
import csv

expiring = get_projects_by_allocation_end_date(
    session,
    start_date=datetime.now(),
    end_date=datetime.now() + timedelta(days=7),
    facility_names=['UNIV', 'WNA']
)

with open('expiring_report.csv', 'w') as f:
    writer = csv.writer(f)
    writer.writerow(['Project', 'PI', 'Resource', 'Expires', 'Days Remaining'])
    # get_projects_by_allocation_end_date returns tuples of
    # (project, allocation, resource_name, days_remaining)
    for project, allocation, resource_name, days_remaining in expiring:
        writer.writerow([
            project.projcode,
            project.lead.full_name,
            resource_name,
            allocation.end_date,
            days_remaining
        ])
```

---

## Troubleshooting

### Database Connection Issues

**"Access denied for user"**
- Check password quoting in `.env` (use single quotes for special characters)
- Verify credentials with direct mysql connection
- Ensure VPN is active (for remote databases)

**"Can't connect to MySQL server"**
- For local DB: Check Docker is running (`docker ps`)
- For remote DB: Check VPN connection
- Test connectivity: `ping sam-sql.ucar.edu`

### CLI Issues

**"User not found" for known users**
- Check which database you're connected to: `echo $SAM_DB_SERVER`
- Local database has subsetted data (may not have all users)
- Try production database for complete data

**ModuleNotFoundError**
- Activate conda environment: `source etc/config_env.sh`
- Check PYTHONPATH: `echo $PYTHONPATH`

### Test Failures

**Schema validation failed**
- Database schema changed - update ORM models to match
- Check database: `mysql ... -e "SHOW CREATE TABLE tablename\G"`

**CRUD tests skipped**
- Normal with read-only database access
- Set up local development database for CRUD testing (see CONTRIBUTING.md)

For additional troubleshooting, see **[CONTRIBUTING.md](CONTRIBUTING.md#troubleshooting)**.

---

## Technology Stack

- **Python 3.13** - Modern Python with type hints
- **SQLAlchemy 2.0** - Declarative ORM with relationship navigation
- **MySQL/MariaDB** - Production database (97 tables, 91+ ORM models)
- **Flask** - Web framework for admin UI and REST API
- **Flask-Admin** - Admin interface with CRUD operations
- **Flask-Login** - Session-based authentication
- **Marshmallow-SQLAlchemy** - JSON serialization schemas
- **pytest** - Comprehensive test framework
- **Conda** - Isolated environment management

---

## Key Contacts & Context

- **Organization:** NCAR CISL (Computational & Information Systems Laboratory)
- **Section:** USS (University Services Section)
- **Primary Resources:** Derecho (HPC), Casper (HPC), Gust (analysis), Stratus (storage), Campaign Store (storage)
- **Facilities:** UNIV (University), WNA (Wyoming-NCAR Alliance)

For access to SAM database credentials or production systems, contact CISL staff.

---

## License

Copyright (c) 2025 NCAR CISL

---

## Getting Help

1. **Documentation:**
   - Start with this README for overview
   - See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and development
   - See [CLAUDE.md](CLAUDE.md) for technical deep dive
   - Check subdirectory READMEs for specific components

2. **Common Questions:**
   - Setup issues → [CONTRIBUTING.md](CONTRIBUTING.md#troubleshooting)
   - ORM patterns → [CLAUDE.md](CLAUDE.md#key-orm-models)
   - API usage → [python/webapp/README.md](python/webapp/README.md#rest-api)
   - Testing → [tests/docs/README.md](tests/docs/README.md)

3. **Support:**
   - Check existing documentation first
   - Review code examples in test files
   - Contact CISL USS team for access/credentials
   - File issues for bugs or feature requests

---

See the [Quick Start](#quick-start) above for initial setup, or [CONTRIBUTING.md](CONTRIBUTING.md) for the full development guide.
