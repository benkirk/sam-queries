# Test Suite Improvement Plan (Phases 2-3)

**Status:** Phase 1 completed on 2025-11-13

This document outlines planned improvements to the test suite for future execution.

---

## PHASE 2: Short-term Improvements
**Goal:** Reduce test code duplication, improve maintainability
**Effort:** ~3-4 hours
**Risk:** Low (non-breaking additions)
**Priority:** High (should do soon)

### 2.1 Add Common Test Fixtures

**Problem:** Tests repeatedly query for the same test data (benkirk, SCSG0001, etc.)

**Solution:** Create reusable fixtures in `tests/conftest.py`

```python
@pytest.fixture
def test_user(session):
    """Get known test user (benkirk)."""
    return User.get_by_username(session, 'benkirk')

@pytest.fixture
def test_project(session):
    """Get known test project (SCSG0001)."""
    return Project.get_by_projcode(session, 'SCSG0001')

@pytest.fixture
def test_allocation(test_project):
    """Get active allocation from test project."""
    for account in test_project.accounts:
        for allocation in account.allocations:
            if allocation.is_active:
                return allocation
    return None

@pytest.fixture
def test_resource(session):
    """Get known test resource (Derecho)."""
    from sam import Resource
    return session.query(Resource).filter_by(resource_name='Derecho').first()
```

**Usage Example:**
```python
# Before:
def test_user_schema(self, session):
    user = User.get_by_username(session, 'benkirk')
    assert user is not None
    # ...

# After:
def test_user_schema(self, test_user):
    assert test_user.username == 'benkirk'
    # ...
```

**Benefits:**
- DRY - don't repeat user/project lookups
- Centralized test data management
- Easy to change test data source
- Clearer test intent

**Files to Modify:**
- `tests/conftest.py` - Add fixtures
- Various test files - Update to use fixtures

**Estimated Time:** 2 hours

---

### 2.2 Add Parameterized Tests

**Problem:** Similar tests repeated for different models (count tests, basic queries, etc.)

**Solution:** Use `@pytest.mark.parametrize` to reduce duplication

**Example - Count Tests:**
```python
# Before (test_basic_read.py):
def test_user_count(self, session):
    user_count = session.query(User).count()
    assert user_count > 0

def test_project_count(self, session):
    project_count = session.query(Project).count()
    assert project_count > 0

def test_account_count(self, session):
    account_count = session.query(Account).count()
    assert account_count > 0

# After:
@pytest.mark.parametrize('model,min_count', [
    (User, 1000),
    (Project, 100),
    (Account, 100),
    (Allocation, 100),
    (Resource, 10),
    (Organization, 50),
    (Institution, 100),
])
def test_model_count(session, model, min_count):
    """Test that core models have expected record counts."""
    count = session.query(model).count()
    assert count >= min_count, f"{model.__name__} has only {count} records (expected >= {min_count})"
```

**Additional Parameterized Tests:**

```python
# Primary key tests
@pytest.mark.parametrize('model,pk_column', [
    (User, 'user_id'),
    (Project, 'project_id'),
    (Account, 'account_id'),
])
def test_model_primary_key(session, model, pk_column):
    instance = session.query(model).first()
    assert hasattr(instance, pk_column)
    assert getattr(instance, pk_column) is not None

# Relationship tests
@pytest.mark.parametrize('model,relationship', [
    (User, 'email_addresses'),
    (Project, 'accounts'),
    (Account, 'allocations'),
])
def test_model_relationship(session, model, relationship):
    instance = session.query(model).first()
    assert hasattr(instance, relationship)
```

**Benefits:**
- Less code duplication
- Easier to add new models to tests
- Clearer test coverage
- Better failure reporting (shows which model failed)

**Files to Modify:**
- `tests/test_basic_read.py` - Convert count/basic tests
- `tests/test_schema_validation.py` - Convert schema tests

**Estimated Time:** 1-2 hours

---

### 2.3 Add pytest.ini Configuration

**Problem:** No standardized pytest configuration

**Solution:** Create `tests/pytest.ini` for consistent test execution

```ini
[pytest]
# Test discovery
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*

# Output and reporting
addopts =
    -v
    --strict-markers
    --tb=short
    --maxfail=5

# Markers for test categorization
markers =
    slow: marks tests as slow (deselect with '-m "not slow"')
    integration: marks tests requiring database
    unit: marks tests that don't require database
    crud: marks tests that modify database
    schema: marks schema validation tests
    cli: marks CLI integration tests
    api: marks API/schema tests

# Timeout (requires pytest-timeout plugin)
# timeout = 300

# Coverage (requires pytest-cov plugin)
# addopts = --cov=sam --cov-report=html
```

**Usage:**
```bash
# Run only unit tests
pytest -m unit

# Run everything except slow tests
pytest -m "not slow"

# Run only integration tests
pytest -m integration

# Combine markers
pytest -m "integration and not slow"
```

**Benefits:**
- Consistent test execution across developers
- Easy test categorization
- Better control over which tests run
- Foundation for CI/CD integration

**Files to Create:**
- `tests/pytest.ini` (new file)

**Files to Modify:**
- Add markers to existing test files (optional, can be done incrementally)

**Estimated Time:** 30 minutes

---

## PHASE 3: Long-term Enhancements
**Goal:** Professional-grade test infrastructure
**Effort:** ~6-8 hours
**Risk:** Medium (requires file reorganization)
**Priority:** Medium (do when team agrees)

### 3.1 Reorganize Test Directory Structure

**Problem:** All tests flat in `/tests/` directory, tools mixed with tests

**Current Structure:**
```
tests/
├── test_basic_read.py
├── test_crud_operations.py
├── test_new_models.py
├── test_views.py
├── test_schema_validation.py
├── test_sam_search_cli.py
├── test_schemas.py
├── test_allocation_schemas.py
├── conftest.py
├── test_config.py
├── orm_inventory.py          # Tool, not a test
├── fix_schema_types.py       # Tool, not a test
├── README.md
└── TEST_RESULTS_SUMMARY.md
```

**Proposed Structure:**
```
tests/
├── unit/                      # Unit tests (models, methods)
│   ├── __init__.py
│   ├── test_basic_read.py
│   ├── test_crud_operations.py
│   └── test_new_models.py
├── integration/               # Integration tests (DB, CLI)
│   ├── __init__.py
│   ├── test_views.py
│   ├── test_schema_validation.py
│   └── test_sam_search_cli.py
├── api/                       # API/schema tests
│   ├── __init__.py
│   ├── test_schemas.py
│   └── test_allocation_schemas.py
├── tools/                     # Utility scripts (not tests)
│   ├── __init__.py
│   ├── orm_inventory.py
│   └── fix_schema_types.py
├── fixtures/                  # Shared fixtures and config
│   ├── __init__.py
│   ├── conftest.py
│   └── test_config.py
├── docs/                      # Test documentation
│   ├── README.md
│   ├── TEST_RESULTS_SUMMARY.md
│   └── FUTURE_IMPROVEMENTS_PLAN.md  # This file
├── pytest.ini                 # pytest configuration
├── check_environment.sh       # Setup script
└── __init__.py
```

**Benefits:**
- Can run specific test categories: `pytest tests/unit/`, `pytest tests/integration/`
- Clearer separation of concerns
- Follows pytest best practices
- Easier to maintain as codebase grows

**Migration Steps:**
1. Create new directory structure
2. Move files to appropriate directories
3. Update imports in all test files (change `from conftest import` to `from tests.fixtures.conftest import`)
4. Update pytest.ini testpaths
5. Update CONTRIBUTING.md and README.md
6. Run full test suite to verify
7. Commit changes

**Files to Move:**
- Unit tests → `tests/unit/`
- Integration tests → `tests/integration/`
- API tests → `tests/api/`
- Tools → `tests/tools/`
- Fixtures → `tests/fixtures/`
- Docs → `tests/docs/`

**Files to Modify:**
- All test files (update imports)
- `pytest.ini` (update testpaths)
- `README.md` (update file paths)
- `CONTRIBUTING.md` (update test instructions)

**Estimated Time:** 2-3 hours

---

### 3.2 Use pytest Plugins for Enhanced Testing

**Problem:** Missing common pytest features (coverage, parallel execution, etc.)

**Solution:** Add pytest plugins to `pytest.ini`
```ini
[pytest]
# ... existing config ...

# Coverage options
addopts =
    --cov=sam
    --cov-report=html
    --cov-report=term-missing

# Timeout (prevent hanging tests)
timeout = 300

# Parallel execution
# Use with: pytest -n auto
```

**Usage:**
```bash
# Run with coverage
pytest --cov=sam --cov-report=html tests/

# View coverage report
open htmlcov/index.html

# Parallel execution (much faster!)
pytest -n auto tests/

# With timeout protection
pytest --timeout=300 tests/

# Combine all
pytest -n auto --cov=sam --cov-report=html --timeout=300 tests/
```

**Benefits:**
- Coverage reporting identifies untested code
- Parallel execution speeds up test suite (2-4x faster)
- Timeouts prevent CI/CD from hanging
- Better visibility into test health

**Files to Modify:**
- `pytest.ini` - Add plugin configuration
- `tests/README.md` - Document plugin usage

**Estimated Time:** 1 hour

---

### 3.3 Add Contract Tests for API

**Problem:** No automated validation of API response structure

**Solution:** Add contract tests using JSON schema

**Example:**
```python
import pytest
from jsonschema import validate
from webui.schemas import UserSchema, ProjectSchema

class TestAPIContracts:
    """Test API response contracts."""

    USER_SCHEMA = {
        "type": "object",
        "required": ["user_id", "username", "first_name", "last_name"],
        "properties": {
            "user_id": {"type": "integer"},
            "username": {"type": "string"},
            "first_name": {"type": "string"},
            "last_name": {"type": "string"},
            "primary_email": {"type": ["string", "null"]},
        }
    }

    def test_user_schema_contract(self, test_user):
        """Verify UserSchema produces valid contract."""
        schema = UserSchema()
        data = schema.dump(test_user)

        # Validate against JSON schema
        validate(instance=data, schema=self.USER_SCHEMA)

        # Verify required fields present
        assert 'user_id' in data
        assert 'username' in data

    def test_user_schema_backward_compatibility(self, test_user):
        """Ensure schema changes don't break existing clients."""
        schema = UserSchema()
        data = schema.dump(test_user)

        # Define fields that must never be removed
        required_fields = ['user_id', 'username', 'first_name', 'last_name']
        for field in required_fields:
            assert field in data, f"Breaking change: {field} removed from UserSchema"
```

**Benefits:**
- Prevent breaking API changes
- Document expected response structure
- Catch schema regressions
- Support API versioning

**Estimated Time:** 2-3 hours
