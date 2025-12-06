# SAM Test Suite

**Status**: 380 tests passed, 16 skipped (~32 seconds without coverage, ~97 seconds with coverage)

Comprehensive test suite for SAM ORM models, CLI tools, API schemas, and query functions.

**Code Coverage**: 77.47% overall (charges 90%, dashboard 79%, allocations 76%)

---

## Quick Start

```bash
# Run all tests (fast, parallel without coverage) - recommended for development
pytest tests/ --no-cov

# Run with coverage report
pytest tests/

# Run specific category
pytest tests/unit/ -v
pytest tests/integration/ -v
pytest tests/api/ -v

# Run specific file
pytest tests/integration/test_schema_validation.py -v

# Run tests matching pattern
pytest tests/ -k "user" -v

# Generate HTML coverage report
pytest tests/ --cov-report=html
open htmlcov/index.html
```

---

## Test Organization

```
tests/
├── conftest.py         # Shared fixtures with pytest-xdist support
├── unit/               # Unit tests (130+ tests)
│   ├── test_basic_read.py
│   ├── test_crud_operations.py
│   ├── test_new_models.py
│   ├── test_query_functions.py     # Query function coverage (41 tests)
│   └── test_sam_search_cli.py      # CLI integration (44 tests)
├── integration/        # Integration tests (42 tests)
│   ├── test_schema_validation.py   # Schema drift detection (18 tests)
│   └── test_views.py                # Database views (24 tests)
└── api/                # API/schema tests (208+ tests)
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
- **test_new_models.py** - Factor, Formula, ApiCredentials, ProjectCode, etc. (51 tests)
- **test_query_functions.py** - Targeted query function testing (41 tests)
  - Charge aggregations (charges.py: 38% → 90% coverage)
  - Dashboard queries (dashboard.py: 28% → 79% coverage)
  - Allocation lookups (allocations.py: 41% → 76% coverage)
  - Statistics & project searches
- **test_sam_search_cli.py** - End-to-end CLI testing (44 tests)
  - User/project searches with `sam-search` command
  - Pattern matching
  - Exit codes and error handling

### Integration Tests (`integration/`)
- **test_schema_validation.py** - Automated ORM/database drift detection (18 tests)
  - Validates all tables exist
  - Validates all columns match
  - Validates primary keys and foreign keys
- **test_views.py** - Database views (24 tests)
  - XRAS integration views
  - Read-only enforcement

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
Reusable test data eliminates duplication, with pytest-xdist support for parallel execution:

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
- `worker_id` - pytest-xdist worker ID ('master', 'gw0', 'gw1', etc.)
- `worker_db_name` - Worker-specific database name for isolation

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
# Fast check (32 seconds, parallel without coverage)
pytest tests/ --no-cov

# Full validation with coverage (97 seconds)
pytest tests/
```

### Check Schema Drift (After ORM Changes)
```bash
pytest tests/integration/test_schema_validation.py -v
```

### Test CLI Changes
```bash
pytest tests/unit/test_sam_search_cli.py -v
```

### Test Query Functions
```bash
pytest tests/unit/test_query_functions.py -v
```

### Generate Coverage Report
```bash
pytest tests/ --cov-report=html
open htmlcov/index.html
```

### Parallel Execution
Parallel execution is enabled by default via `-n auto` in pyproject.toml.

```bash
# Disable parallel (force serial)
pytest tests/ -n 1

# Explicitly set worker count
pytest tests/ -n 4
```

**Note**: Parallel execution speeds up tests without coverage (~32s vs ~98s serial), but coverage runs take the same time with or without parallelization (~97s).

---

## Test Results

**With Local Development Database:**
- 380+ passed, ~16 skipped
- Execution time: ~32 seconds (parallel without coverage), ~97 seconds (with coverage)
- Code coverage: 77.47% overall

**With Read-Only Database:**
- ~360+ passed, ~20 skipped (CRUD tests skip without write access)
- Execution time: Similar to local database

**Skipped Tests:**
- View tests that require specific data
- Relationship tests that require foreign keys
- CRUD tests without write access

**Key Coverage Improvements:**
- sam/queries/charges.py: 38% → 90%
- sam/queries/dashboard.py: 28% → 79%
- sam/queries/allocations.py: 41% → 76%

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
- **Phase 4** (2025-12-06): Added targeted query function tests (380 tests, 77.47% coverage)
- **Phase 5** (2025-12-06): Enabled parallel execution with pytest-xdist (3x speedup)
- **Current** (2025-12-06): 380+ tests, comprehensive coverage, parallel execution
