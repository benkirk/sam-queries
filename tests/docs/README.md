# SAM Test Suite

**Status**: 209 tests passed, 10 skipped (~52 seconds)

Comprehensive test suite for SAM ORM models, CLI tools, and API schemas.

---

## Quick Start

```bash
# Run all tests
cd tests && pytest -v

# Run specific category
cd tests && pytest unit/ -v
cd tests && pytest integration/ -v
cd tests && pytest api/ -v

# Run specific file
cd tests && pytest integration/test_schema_validation.py -v

# Run tests matching pattern
cd tests && pytest -k "user" -v

# Run with coverage
cd tests && pytest --cov=sam --cov-report=html
```

---

## Test Organization

```
tests/
├── pytest.ini          # Configuration, markers, timeouts
├── conftest.py         # Shared fixtures (test_user, test_project, etc.)
├── unit/               # Unit tests (35 tests)
│   ├── test_basic_read.py
│   ├── test_crud_operations.py
│   └── test_new_models.py
├── integration/        # Integration tests (160 tests)
│   ├── test_schema_validation.py
│   ├── test_sam_search_cli.py
│   └── test_views.py
└── api/                # API/schema tests (28 tests)
    ├── test_schemas.py
    └── test_allocation_schemas.py
```

---

## Test Categories

### Unit Tests (`unit/`)
- **test_basic_read.py** - ORM queries, relationships, search methods
  - Parameterized count tests (9 models)
  - Parameterized primary key tests (9 models)
- **test_crud_operations.py** - Create, update, delete, transactions
- **test_new_models.py** - Factor, Formula, ApiCredentials, ProjectCode, etc.

### Integration Tests (`integration/`)
- **test_schema_validation.py** - Automated ORM/database drift detection
  - Validates all tables exist
  - Validates all columns match
  - Validates primary keys and foreign keys
- **test_sam_search_cli.py** - End-to-end CLI testing (44 tests)
  - User/project searches
  - Pattern matching
  - Exit codes and error handling
- **test_views.py** - Database views (XRAS integration, read-only enforcement)

### API Tests (`api/`)
- **test_schemas.py** - Marshmallow schema serialization
  - Three-tier strategy (Full, List, Summary)
  - DateTime handling
  - Nested relationships
- **test_allocation_schemas.py** - Allocation/usage calculations
  - Real-time balance calculations
  - Charge summaries
  - Schema integration

---

## Key Features

### 1. Shared Fixtures (`conftest.py`)
Reusable test data eliminates duplication:

```python
def test_example(test_user, test_project):
    # test_user = benkirk
    # test_project = SCSG0001
    assert test_user.username == 'benkirk'
```

Available fixtures:
- `session` - Auto-rollback session
- `session_commit` - Committing session (use sparingly)
- `test_user` - User 'benkirk'
- `test_project` - Project 'SCSG0001'
- `test_allocation` - Active allocation from test_project
- `test_resource` - Resource 'Derecho'

### 2. Parameterized Tests
Reduces code duplication:

```python
@pytest.mark.parametrize('model,min_count', [
    (User, 1000),
    (Project, 100),
    # ...
])
def test_model_count(session, model, min_count):
    count = session.query(model).count()
    assert count >= min_count
```

### 3. Test Markers (`pytest.ini`)
Run specific test categories:

```bash
# Run only integration tests
cd tests && pytest -m integration

# Skip slow tests
cd tests && pytest -m "not slow"

# Run only schema tests
cd tests && pytest -m schema
```

Available markers:
- `slow` - Slow-running tests
- `integration` - Requires database
- `unit` - No database required
- `crud` - Modifies database
- `schema` - Schema validation
- `cli` - CLI integration
- `api` - API/schema tests

### 4. Timeout Protection
All tests timeout after 300 seconds (configured in pytest.ini).

---

## Common Tasks

### Run Tests Before Committing
```bash
cd tests && pytest -v
```

### Check Schema Drift (After ORM Changes)
```bash
cd tests && pytest integration/test_schema_validation.py -v
```

### Test CLI Changes
```bash
cd tests && pytest integration/test_sam_search_cli.py -v
```

### Generate Coverage Report
```bash
cd tests && pytest --cov=sam --cov-report=html
open htmlcov/index.html
```

### Parallel Execution (Faster)
```bash
cd tests && pytest -n auto -v
```

---

## Test Results

**With Local Development Database:**
- 209 passed, 10 skipped
- Execution time: ~52 seconds

**With Read-Only Database:**
- ~190 passed, ~20 skipped (CRUD tests skip without write access)

**Skipped Tests:**
- View tests that require specific data
- Relationship tests that require foreign keys
- CRUD tests without write access

---

## Writing New Tests

### Unit Test Example
```python
# tests/unit/test_basic_read.py
class TestNewFeature:
    def test_new_query(self, session, test_user):
        """Test new query functionality."""
        result = test_user.some_new_method()
        assert result is not None
```

### Integration Test Example
```python
# tests/integration/test_sam_search_cli.py
def test_new_cli_feature():
    """Test new CLI command."""
    result = run_cli('newcommand', '--option')
    assert result.returncode == 0
    assert 'expected output' in result.stdout
```

### API Test Example
```python
# tests/api/test_schemas.py
def test_new_schema(test_user):
    """Test new schema serialization."""
    schema = NewSchema()
    result = schema.dump(test_user)
    assert 'field_name' in result
```

---

## Troubleshooting

**Tests fail with import errors:**
```bash
# Ensure you're in tests/ directory or use cd tests &&
cd tests && pytest -v
```

**Tests timeout:**
```bash
# Increase timeout in pytest.ini or skip slow tests
cd tests && pytest -m "not slow" -v
```

**Coverage not working:**
```bash
# Install pytest-cov if missing
conda install pytest-cov
```

**Parallel execution fails:**
```bash
# Install pytest-xdist if missing
conda install pytest-xdist
```

---

## Test Suite History

- **Phase 1** (2025-11-13): Initial test suite (172 tests)
- **Phase 2** (2025-11-14): Added fixtures, parameterization, pytest.ini
- **Phase 3** (2025-11-14): Directory reorganization, plugins configuration
- **Current** (2025-11-14): 209 tests, professional structure

For detailed improvement history, see [CURRENT_PLAN.md](CURRENT_PLAN.md).
