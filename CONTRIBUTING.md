# Contributing to SAM Queries

## Overview

**SAM (System for Allocation Management)** is NCAR's resource allocation and accounting system for managing HPC allocations, user accounts, and project tracking for Derecho, Casper, and other computational resources.

This repository provides:
- **Python ORM models** (SQLAlchemy 2.0) for the SAM database
- **CLI tool** (`sam_search.py`) for quick user/project lookups
- **REST API** (Flask) for programmatic access to SAM data
- **Comprehensive test suite** with schema validation

## Quick Start for Impatient Users

Already have conda and credentials? Fast path:

```bash
# 1. Create environment
make conda-env

# 2. Create .env with your credentials (see Configuration section below)

# 3. Try the CLI
./python/sam_search.py user <your_username>

# 4. Run tests
cd tests && pytest -v
```

For local development with write access, continue to the full setup guide below.

## Full Setup Guide

### Prerequisites

**Required:**
- **Conda** (miniconda or anaconda)
- **Git**

**For local development database:**
- **Docker** (Docker Desktop or similar)
- **VPN access** to NCAR network (to clone production data)

**Database credentials:**
- Contact your project lead or CISL staff for read-only credentials to:
  - `sam-sql.ucar.edu` (production) - recommended for most development
  - `test-sam-sql.ucar.edu` (test instance)

### Initial Setup

#### Step 1: Create Conda Environment

From the project root directory:

```bash
make conda-env
```

This creates a local conda environment in `./conda-env/` with Python 3.13 and all dependencies.

**What happens:**
- Reads `conda-env.yaml` for package definitions
- Creates isolated environment (not installed globally)
- Installs SQLAlchemy, Flask, pytest, and other dependencies

**Time:** 2-5 minutes

#### Step 2: Configure Database Credentials

Create a `.env` file in the project root:

```bash
touch .env
chmod 600 .env  # Protect your credentials
```

**For read-only access** (recommended starting point):

```bash
# Production instance (read-only)
PROD_SAM_DB_USERNAME=your_username
PROD_SAM_DB_SERVER=sam-sql.ucar.edu
PROD_SAM_DB_PASSWORD='your_password'

# Use production credentials by default
SAM_DB_USERNAME=${PROD_SAM_DB_USERNAME}
SAM_DB_SERVER=${PROD_SAM_DB_SERVER}
SAM_DB_PASSWORD=${PROD_SAM_DB_PASSWORD}
```

**⚠️ Critical**: If your password contains special characters (`$`, `!`, `\`, etc.), **wrap it in single quotes**:
```bash
PROD_SAM_DB_PASSWORD='my$ecure!pa$$word'  # ✅ Correct
PROD_SAM_DB_PASSWORD=my$ecure!pa$$word    # ❌ Will fail - shell will expand $
```

**For local development** (optional, see Step 3):

```bash
# Local Docker instance (for CRUD operations)
LOCAL_SAM_DB_USERNAME=root
LOCAL_SAM_DB_SERVER=127.0.0.1
LOCAL_SAM_DB_PASSWORD=root

# Uncomment to use local instance instead:
#SAM_DB_USERNAME=${LOCAL_SAM_DB_USERNAME}
#SAM_DB_SERVER=${LOCAL_SAM_DB_SERVER}
#SAM_DB_PASSWORD=${LOCAL_SAM_DB_PASSWORD}
```

**Security notes:**
- The `.env` file is gitignored and will never be committed
- Never share your credentials or commit them to the repository
- Use read-only credentials unless you need to test CRUD operations

#### Step 3: Verify Setup

At this point, you should be able to use the CLI and run read-only tests:

```bash
# Activate the environment (if not already active)
source etc/config_env.sh

# Test database connection
./python/sam_search.py user --search "a%" | head -20

# Run tests (most will pass with read-only access)
cd tests && pytest -v
```

**Expected results:**
- CLI should display user information
- Tests: ~200 passed, ~10 skipped (CRUD tests skipped without local DB)

#### Step 4: Local Development Database (Optional)

**When do you need this?**
- Testing create/update/delete operations (CRUD)
- Faster query performance (local vs. remote)
- Working offline without VPN

**Setup:**

```bash
# 1. Start Docker container
cd containers/sam-sql-dev
./docker_start.sh

# 2. Bootstrap database (clones subset of production data)
source ../../.env && ./bootstrap_clone.py
```

**What to expect:**
- **Time:** 10-20 minutes for initial clone
- **Size:** ~500MB-1GB (subsetted from multi-GB production database)
- **Success message:** `🎉 Done. Local clone is ready. Connect to local db as configured.`

The bootstrap script:
- Copies all 97 table schemas
- Samples recent data from large tables (maintains ~10k rows from multi-million row tables)
- Preserves foreign key relationships
- Creates 7 database views

**⚠️ Important:**
- Local database is **not anonymized** and contains real PII (names, emails)
- Never push Docker volumes or database dumps to public repositories
- Only use for local development and testing

**Switch to local database:**

Edit your `.env` file:
```bash
# Comment out production, uncomment local
#SAM_DB_USERNAME=${PROD_SAM_DB_USERNAME}
#SAM_DB_SERVER=${PROD_SAM_DB_SERVER}
#SAM_DB_PASSWORD=${PROD_SAM_DB_PASSWORD}

SAM_DB_USERNAME=${LOCAL_SAM_DB_USERNAME}
SAM_DB_SERVER=${LOCAL_SAM_DB_SERVER}
SAM_DB_PASSWORD=${LOCAL_SAM_DB_PASSWORD}
```

**Verify local setup:**
```bash
cd ../..  # Back to project root
./tests/check_environment.sh
```

Should show:
- `SAM_DB_SERVER=127.0.0.1`
- List of all 97 tables
- Sample user records

## Usage Examples

### CLI Tool

The `sam_search.py` CLI provides quick access to user and project information:

```bash
# Search for a user (replace with actual username in your database)
./python/sam_search.py user bruceb

# List user's projects
./python/sam_search.py user bruceb --list-projects

# Search for users matching a pattern (SQL wildcards)
./python/sam_search.py user --search "b%"

# Search for a project
./python/sam_search.py project UCSU0092 --verbose

# Pattern search for projects
./python/sam_search.py project --search "UCSU%"

# Find projects expiring soon (within 32 days)
./python/sam_search.py project --upcoming-expirations

# Find projects that recently expired
./python/sam_search.py project --recent-expirations --list-users

# Find users without active projects
./python/sam_search.py user --abandoned

# Search including inactive projects
./python/sam_search.py --inactive-projects user bruceb --list-projects
```

**Exit codes:**
- `0` - Success
- `1` - Not found
- `2` - Error
- `130` - Keyboard interrupt (Ctrl+C)

### Web UI

Launch the Flask web interface:

```bash
./python/webui/run.py
```

Access at `http://127.0.0.1:5050`

**Features:**
- Browse users, projects, allocations
- View real-time usage and balances
- Search and filter capabilities
- RESTful API endpoints

### REST API

All API endpoints require authentication. Example queries:

```bash
# Get user details
http://127.0.0.1:5050/api/v1/users/<username>

# Get user's projects
http://127.0.0.1:5050/api/v1/users/<username>/projects

# Get project details
http://127.0.0.1:5050/api/v1/projects/<projcode>

# Get project members
http://127.0.0.1:5050/api/v1/projects/<projcode>/members

# Get project allocations with current usage
http://127.0.0.1:5050/api/v1/projects/<projcode>/allocations

# Get account balance
http://127.0.0.1:5050/api/v1/accounts/<account_id>/balance

# List projects expiring soon
http://127.0.0.1:5050/api/v1/projects/expiring
```

All responses are JSON formatted using Marshmallow-SQLAlchemy schemas.

## Development Workflow

### Running Tests

```bash
# Run all tests
cd tests && pytest -v

# Run specific test file
cd tests && pytest integration/test_schema_validation.py -v

# Run tests matching a pattern
cd tests && pytest -k "user" -v

# Run with coverage
cd tests && pytest --cov=sam --cov-report=html
```

**Test categories:**
- **Schema validation** (`test_schema_validation.py`) - Ensures ORM matches database
- **Basic queries** (`test_basic_read.py`) - Tests core ORM functionality
- **CRUD operations** (`test_crud_operations.py`) - Create/update/delete (requires local DB)
- **CLI integration** (`test_sam_search_cli.py`) - End-to-end CLI testing
- **API schemas** (`test_schemas.py`) - Marshmallow serialization tests
- **Views** (`test_views.py`) - Database view queries

**Expected results with read-only access:**
- ~190 passed
- ~20 skipped (CRUD tests)
- 0 failed

**With local database:**
- ~200 passed
- ~10 skipped
- 0 failed

### Adding New Features

#### Adding ORM Models

1. **Create the model** in appropriate domain module:
   - Core entities: `sam/core/` (users, organizations)
   - Resources: `sam/resources/` (machines, allocations)
   - Accounting: `sam/accounting/` (accounts, charges)
   - Activity: `sam/activity/` (jobs, usage)

2. **Add to exports** in `sam/__init__.py`

3. **Create tests** in `tests/test_new_models.py`:
   ```python
   def test_new_model_count(session):
       count = session.query(NewModel).count()
       assert count >= 0
   ```

4. **Run schema validation**:
   ```bash
   cd tests && pytest integration/test_schema_validation.py -v
   ```

5. **Verify all tests pass**:
   ```bash
   cd tests && pytest -v
   ```

#### Adding API Endpoints

1. **Create Marshmallow schema** in `python/sam.schemas/`:
   ```python
   from sam.schemas import BaseSchema
   from marshmallow import fields

   class NewModelSchema(BaseSchema):
       class Meta:
           model = NewModel

       id = fields.Int()
       name = fields.Str()
   ```

2. **Add endpoint** in `python/webui/api/`:
   ```python
   @bp.route('/newmodels/<int:id>')
   def get_newmodel(id):
       obj = db.session.get(NewModel, id)
       return NewModelSchema().dump(obj)
   ```

3. **Test manually** via browser/curl

4. **Add integration tests** (optional but recommended)

#### Adding CLI Features

1. **Add functionality** to `python/sam_search.py`

2. **Create integration tests** in `tests/test_sam_search_cli.py`:
   ```python
   def test_new_cli_feature():
       result = subprocess.run(
           ['./python/sam_search.py', 'newfeature', '--arg'],
           capture_output=True, text=True
       )
       assert result.returncode == 0
       assert 'expected output' in result.stdout
   ```

3. **Test manually**:
   ```bash
   ./python/sam_search.py newfeature --help
   ```

4. **Run CLI test suite**:
   ```bash
   cd tests && pytest integration/test_sam_search_cli.py -v
   ```

## Code Style & Best Practices

### General Guidelines

- **Follow existing patterns** - Browse similar code before implementing
- **Use type hints** where helpful (especially function signatures)
- **Write docstrings** with examples:
  ```python
  def get_user_projects(username: str) -> list[Project]:
      """Get all active projects for a user.

      Args:
          username: User's login name (e.g., 'benkirk')

      Returns:
          List of Project objects

      Example:
          >>> projects = get_user_projects('benkirk')
          >>> len(projects)
          5
      """
  ```
- **Comments explain "why"** not "what"
- **Add tests** for new features
- **Run schema validation** after ORM changes

### SQLAlchemy Patterns

**DateTime handling:**
```python
from datetime import datetime

# ✅ DO - Database uses naive datetimes
now = datetime.now()

# ❌ DON'T - No timezone awareness
now = datetime.now(UTC)
```

**Primary keys:**
```python
# ✅ DO - Check database schema first
# Single column PK:
id = Column(Integer, primary_key=True, autoincrement=True)

# Composite PK:
__table_args__ = (
    PrimaryKeyConstraint('col1', 'col2', name='pk_tablename'),
)
```

**Relationships:**
```python
# ✅ DO - Always use back_populates for bidirectional relationships
class Project(Base):
    accounts = relationship('Account', back_populates='project')

class Account(Base):
    project = relationship('Project', back_populates='accounts')
```

### Common Pitfalls

❌ **DON'T**:
- Use `datetime.now(UTC)` - database uses naive datetimes
- Assume single-column primary keys - check database first
- Modify database schema - ORM follows database (database is source of truth)
- Skip schema validation tests after model changes
- Use `session.execute()` with plain strings - wrap with `text()`

✅ **DO**:
- Run schema validation tests before committing model changes
- Check actual database schema when in doubt (`SHOW CREATE TABLE`)
- Use bidirectional relationships with `back_populates`
- Write integration tests for CLI features
- Use proper exit codes (0=success, 1=not found, 2=error, 130=interrupt)
- Keep tests fast (full suite should run in ~1 minute)

## Testing Philosophy

- **Schema validation is critical** - Prevents ORM/database drift
- **Integration tests preferred** - Test real workflows, not just units
- **Keep tests fast** - Full suite should run in ~1 minute
- **Test CLI output** - Users depend on consistent formatting
- **Use realistic test data** - Query actual database, don't mock everything

## Submitting Changes

1. **Create a feature branch** from the current development branch:
   ```bash
   git checkout -b feature/my-new-feature
   ```

2. **Make your changes** with clear, descriptive commits:
   ```bash
   git commit -m "Add user search by email address

   - Add User.search_by_email() method
   - Add CLI flag --email for user search
   - Add integration tests
   - Update documentation"
   ```

3. **Ensure all tests pass**:
   ```bash
   cd tests && pytest -v
   ```

4. **Run schema validation** (if you modified ORM):
   ```bash
   cd tests && pytest integration/test_schema_validation.py -v
   ```

5. **Submit a pull request** with:
   - Clear description of what changed and why
   - Test results
   - Any breaking changes noted
   - Screenshots/examples if relevant

## Troubleshooting

### Environment Issues

**"conda: command not found"**
- Install miniconda: https://docs.conda.io/en/latest/miniconda.html
- Or use system package manager: `brew install miniconda` (macOS)

**"make: command not found"**
- macOS: Install Xcode command line tools: `xcode-select --install`
- Linux: Install build essentials: `apt-get install build-essential`

**"ModuleNotFoundError: No module named 'sam'"**
- Activate conda environment: `source etc/config_env.sh`
- Check PYTHONPATH is set: `echo $PYTHONPATH` (should include `/path/to/sam-queries/python`)

### Database Connection Issues

**"Access denied for user"**
- Check password quoting in `.env` (wrap in single quotes if special characters)
- Verify credentials with direct mysql: `mysql -u username -h hostname -p`
- Ensure VPN connection is active (for remote databases)

**"Can't connect to MySQL server"**
- For local DB: Check Docker is running: `docker ps | grep local-sam-mysql`
- For remote DB: Check VPN connection
- Check hostname: `ping sam-sql.ucar.edu` or `ping 127.0.0.1`

**Bootstrap script fails**
- Ensure `.env` file is sourced: `source ../../.env`
- Check disk space: `df -h` (need ~2GB free)
- Verify Docker has resources allocated (Settings → Resources → increase memory to 4GB+)

### Test Failures

**"Schema validation failed"**
- Database schema changed - update ORM models to match
- Check database: `mysql ... -e "SHOW CREATE TABLE tablename\G"`
- Update model definition to match database

**"CRUD tests skipped"**
- Normal with read-only database access
- Set up local development database (Step 4) to run CRUD tests

**Tests timeout**
- Increase timeout in pytest.ini
- Check database connection performance
- Consider using local database for faster queries

### CLI Issues

**CLI returns "User not found" for known users**
- Check which database you're connected to: `echo $SAM_DB_SERVER`
- Local database may not have all users (subsetted data)
- Try production database for complete data

**CLI output is truncated**
- This is normal - use `--verbose` flag for full details
- Or redirect to file: `./python/sam_search.py ... > output.txt`

## Getting Help

### Documentation Resources

- **[CLAUDE.md](CLAUDE.md)** - Comprehensive technical reference:
  - Detailed ORM model documentation
  - Database schema patterns
  - Marshmallow-SQLAlchemy schema usage
  - Allocation balance calculation logic
  - Common queries and examples
  - Known issues and gotchas

- **Inline documentation** - Most modules have detailed docstrings
  ```bash
  python3 -c "from sam.core import User; help(User)"
  ```

### Getting Support

- **Project team** - Contact your project lead or CISL staff
- **Issues** - File bug reports or feature requests in the issue tracker
- **Code review** - Pull requests are reviewed by maintainers

## Project Structure

```
sam-queries/
├── python/
│   ├── sam/                  # ORM models
│   │   ├── __init__.py      # Main exports
│   │   ├── base.py          # Base classes, mixins
│   │   ├── core/            # Users, organizations, groups
│   │   ├── resources/       # Resources, machines, facilities
│   │   ├── projects/        # Projects, contracts, areas
│   │   ├── accounting/      # Accounts, allocations, adjustments
│   │   ├── activity/        # Job activity (HPC, DAV, disk, archive)
│   │   ├── summaries/       # Charge summaries
│   │   ├── integration/     # XRAS integration
│   │   └── security/        # Roles, API credentials
│   ├── sam_search.py        # CLI tool
│   └── webui/               # Flask web application
│       ├── api/             # REST API blueprints
│       ├── schemas/         # Marshmallow schemas
│       └── run.py           # Application entry point
├── tests/                   # Test suite
│   ├── test_basic_read.py           # Basic ORM queries
│   ├── test_crud_operations.py      # Create/update/delete
│   ├── test_schema_validation.py    # Schema drift detection
│   ├── test_sam_search_cli.py       # CLI integration tests
│   └── test_views.py                # Database views
├── containers/
│   └── sam-sql-dev/         # Docker setup for local development
├── .env                     # Your credentials (gitignored, you create this)
├── conda-env.yaml          # Conda dependencies
├── Makefile                # Build automation
└── CONTRIBUTING.md         # This file
```

## What's Next?

Once you have the environment set up:

1. **Explore the database** - Use the CLI to browse users and projects
2. **Run the test suite** - Understand how the ORM works
3. **Read [CLAUDE.md](CLAUDE.md)** - Deep dive into the architecture
4. **Start small** - Fix a bug, add a test, improve documentation
5. **Ask questions** - Don't hesitate to reach out to the team

Welcome to SAM Queries! 🚀
