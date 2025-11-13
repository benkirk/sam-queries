# SAM ORM Test Suite

This directory contains comprehensive tests for the SAM SQLAlchemy ORM models against the local MySQL database clone.

## Quick Start

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_basic_read.py -v
python -m pytest tests/test_crud_operations.py -v

# Run ORM inventory analysis
python tests/orm_inventory.py
```

## Files Overview

### Test Files

- **`test_basic_read.py`** - Read-only tests (26 tests)
  - Basic queries on all core models
  - Complex joins and relationships
  - Search method validation

- **`test_crud_operations.py`** - CRUD operations (18 tests)
  - Create, Update, Delete operations
  - Transaction behavior
  - Bulk operations

### Infrastructure Files

- **`conftest.py`** - pytest configuration and fixtures
- **`test_config.py`** - Database connection utilities
- **`orm_inventory.py`** - Schema analysis tool

### Documentation

- **`TEST_RESULTS_SUMMARY.md`** - Comprehensive test results and analysis
- **`README.md`** - This file

## Test Results

✅ **44/44 tests pass**

- 26 read operation tests
- 18 CRUD operation tests
- 91 ORM models validated
- All relationships verified

See `TEST_RESULTS_SUMMARY.md` for detailed results.

## Database Connection

Tests use the same `.env` file configuration as the main codebase for database credentials.

### Configuration

**Environment Variables (from `.env` in project root):**
- `SAM_DB_USERNAME` - Database username (default: root)
- `SAM_DB_PASSWORD` - Database password (default: root)
- `SAM_DB_SERVER` - Database server (default: 127.0.0.1)
- `SAM_DB_PORT` - Database port (default: 3306)
- Database: `sam`

**Default Connection (Local Docker):**
```bash
# In .env file:
SAM_DB_USERNAME=${LOCAL_SAM_DB_USERNAME}
SAM_DB_SERVER=${LOCAL_SAM_DB_SERVER}
SAM_DB_PASSWORD=${LOCAL_SAM_DB_PASSWORD}

# Which resolves to:
# mysql+pymysql://root:root@127.0.0.1:3306/sam
```

### Switching Test Environments

To test against different database instances, edit `.env` file:

```bash
# Local Docker (default - for most development)
SAM_DB_USERNAME=${LOCAL_SAM_DB_USERNAME}
SAM_DB_SERVER=${LOCAL_SAM_DB_SERVER}
SAM_DB_PASSWORD=${LOCAL_SAM_DB_PASSWORD}

# Test instance (for integration testing)
# SAM_DB_USERNAME=${TEST_SAM_DB_USERNAME}
# SAM_DB_SERVER=${TEST_SAM_DB_SERVER}
# SAM_DB_PASSWORD=${TEST_SAM_DB_PASSWORD}

# Production (read-only access for validation)
# SAM_DB_USERNAME=${PROD_SAM_DB_USERNAME}
# SAM_DB_SERVER=${PROD_SAM_DB_SERVER}
# SAM_DB_PASSWORD=${PROD_SAM_DB_PASSWORD}
```

### Docker Setup

Tests require a local MySQL database. To start the Docker container:

```bash
cd containers/sam-sql-dev/
./docker_start.sh

# Verify container is running
docker ps | grep local-sam-mysql

# Check database connectivity
./tests/check_environment.sh
```

## Usage Examples

### Running Tests

```bash
# All tests with verbose output
python -m pytest tests/ -v

# All tests with print statements
python -m pytest tests/ -v -s

# Specific test class
python -m pytest tests/test_basic_read.py::TestBasicRead -v

# Specific test method
python -m pytest tests/test_basic_read.py::TestBasicRead::test_user_count -v

# Stop on first failure
python -m pytest tests/ -x

# Run last failed tests
python -m pytest tests/ --lf

# Parallel execution (if pytest-xdist installed)
python -m pytest tests/ -n auto
```

### Using Test Sessions in Your Code

```python
from test_config import get_test_session
from sam import User, Project

# Read data (auto-rollback)
with get_test_session() as session:
    user = session.query(User).first()
    print(f"User: {user.username}")
    # Automatically commits on success

# Test data (always rollback)
from test_config import get_test_session_rollback

with get_test_session_rollback() as session:
    new_user = User(username='test')
    session.add(new_user)
    session.flush()  # Get ID
    print(f"ID: {new_user.user_id}")
    # Automatically rolls back on exit
```

### Running ORM Inventory

```python
# Generate full inventory report
python tests/orm_inventory.py

# Output shows:
# - All ORM models and their table mappings
# - Schema comparison (DB vs ORM)
# - Missing ORM models
# - Type mismatches
# - Relationship counts
```

## Test Categories

### 1. Basic Read Tests

Test fundamental query operations:

```python
# Examples of what's tested:
- session.query(User).count()
- session.query(Project).filter(Project.active == True).first()
- user.email_addresses  # Relationship navigation
- project.accounts  # One-to-many relationships
```

### 2. Complex Query Tests

Test joins, filters, and class methods:

```python
# Examples:
- User.get_by_username(session, 'jsmith')
- Project.get_by_projcode(session, 'UCSD0001')
- session.query(Project).join(Project.accounts).all()
```

### 3. Relationship Tests

Test bidirectional relationships and hierarchies:

```python
# Examples:
- account_user.user  # Forward relationship
- account_user.account  # Forward relationship
- user.accounts  # Reverse relationship (back_populates)
- project.parent  # Parent-child hierarchy
```

### 4. Create Operations

Test creating new records:

```python
# Examples:
new_email = EmailAddress(
    email_address='test@example.com',
    user_id=user.user_id,
    is_primary=False
)
session.add(new_email)
session.flush()  # Get ID
```

### 5. Update Operations

Test modifying existing records:

```python
# Examples:
email.active = False
session.flush()

allocation.amount = 15000.00
session.flush()
```

### 6. Delete Operations

Test soft and hard deletes:

```python
# Soft delete:
allocation.deleted = True
allocation.deletion_time = datetime.utcnow()

# Hard delete:
session.delete(email)
session.flush()
```

### 7. Transaction Tests

Test rollback and commit behavior:

```python
# All test sessions auto-rollback to avoid pollution
session.add(new_record)
session.flush()  # Generates ID
session.rollback()  # Discards changes
```

## Database Schema

### Core Models Tested

- **User** (27,203 records)
  - EmailAddress, Phone, UserAlias
  - UserInstitution, UserOrganization
  - UserResourceHome, UserResourceShell

- **Project** (5,452 records)
  - ProjectDirectory, ProjectNumber
  - ProjectOrganization, ProjectContract

- **Account** (17,031 records)
  - AccountUser (membership)
  - Links Projects to Resources

- **Allocation** (21,166 records)
  - AllocationTransaction
  - Parent-child hierarchies

- **Resource** (31 records)
  - Machine, Queue
  - ResourceType, ResourceShell

- **Organization** (397 records)
- **Institution** (1,347 records)
- **ChargeAdjustment**
- **Summary Tables** (Comp, HPC, DAV, Disk, Archive)

## Known Issues

### Minor Type Mismatches (28 models)

These are cosmetic and don't affect functionality:

- `FLOAT` in DB vs `NUMERIC` in ORM (allocations, charges)
- `DATE` in DB vs `DATETIME` in ORM (summary tables)
- `CHAR(1)` vs `VARCHAR(1)` (some code fields)

### Missing Server Defaults

Some timestamp fields require explicit values:
```python
# Required when creating:
creation_time=datetime.utcnow()
```

### Tables Without ORM Models (11 tables)

Mostly system/temporary tables:
- `api_credentials`, `factor`, `formula`, `schema_version`, etc.

## Adding New Tests

### 1. Create Test File

```python
# tests/test_my_feature.py
import pytest
from sam import User

class TestMyFeature:
    def test_something(self, session):
        """Test description."""
        user = session.query(User).first()
        assert user is not None
```

### 2. Use Fixtures

```python
def test_with_auto_rollback(self, session):
    """Session fixture auto-rolls back."""
    # Make changes...
    # Automatically rolled back at end

def test_with_commit(self, session_commit):
    """Use only if you need to commit."""
    # Changes will be committed
    # Use sparingly!
```

### 3. Run Your Tests

```bash
python -m pytest tests/test_my_feature.py -v
```

## Best Practices

1. **Always use fixtures** - Don't create sessions manually
2. **Prefer rollback sessions** - Avoid polluting test database
3. **Test relationships** - Verify both directions
4. **Use flush() for IDs** - Get auto-generated IDs before rollback
5. **Test edge cases** - NULL values, empty lists, etc.
6. **Isolate tests** - Each test should be independent
7. **Clear test names** - Describe what's being tested

## Troubleshooting

### Connection Errors

```bash
# Check if MySQL container is running
docker ps | grep local-sam-mysql

# Start if needed
docker start local-sam-mysql

# Check connectivity
python -c "from test_config import create_test_engine; create_test_engine().connect()"
```

### Import Errors

```bash
# Ensure you're in the project root
cd /Users/benkirk/codes/sam-queries

# Check Python path
python -c "import sys; print(sys.path)"

# Run tests from project root
python -m pytest tests/
```

### Test Failures

```bash
# Run with full traceback
python -m pytest tests/ -v --tb=long

# Run with print statements
python -m pytest tests/ -v -s

# Run just the failing test
python -m pytest tests/test_file.py::TestClass::test_method -v
```

## Resources

- [SQLAlchemy ORM Tutorial](https://docs.sqlalchemy.org/en/20/tutorial/index.html)
- [pytest Documentation](https://docs.pytest.org/)
- [Project Wiki](#) - Internal documentation

## Contributing

When adding new ORM models:

1. Add model to appropriate module under `python/sam/`
2. Import in `python/sam/__init__.py`
3. Run `python tests/orm_inventory.py` to verify
4. Add tests to `test_basic_read.py`
5. Add CRUD tests if needed to `test_crud_operations.py`
6. Run full test suite: `python -m pytest tests/ -v`
7. Update this README if needed

## Support

For issues or questions:
- Check `TEST_RESULTS_SUMMARY.md` for known issues
- Run `python tests/orm_inventory.py` for schema analysis
- Review existing tests for examples

---

**Last Updated:** 2025-11-12
**Test Suite Version:** 1.0
**Total Tests:** 44
**Pass Rate:** 100%
