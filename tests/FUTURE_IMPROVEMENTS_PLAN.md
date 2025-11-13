# Test Suite Improvement Plan (Phases 2-4)

**Status:** Phase 1 completed on 2025-11-13
**Current State:** Tests now use `.env` configuration, pymysql driver standardized across codebase

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

### 3.2 Add Docker Compose for Test Database

**Problem:** Tests assume Docker container already running, no automated setup

**Solution:** Add `tests/docker-compose.yml` for one-command database startup

**File:** `tests/docker-compose.yml`
```yaml
version: '3.8'

services:
  test-db:
    image: mysql:8.0
    container_name: sam-test-mysql
    environment:
      MYSQL_ROOT_PASSWORD: root
      MYSQL_DATABASE: sam
      MYSQL_USER: testuser
      MYSQL_PASSWORD: testpass
    ports:
      - "3306:3306"
    volumes:
      # Optional: mount bootstrap data
      - ./test-data:/docker-entrypoint-initdb.d
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-uroot", "-proot"]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 30s
    command:
      - --character-set-server=utf8mb4
      - --collation-server=utf8mb4_unicode_ci
```

**Usage:**
```bash
# Start test database
cd tests
docker-compose up -d

# Wait for healthcheck to pass
docker-compose ps

# Run tests
pytest -v

# View logs
docker-compose logs -f test-db

# Stop and remove
docker-compose down

# Stop and remove including volumes
docker-compose down -v
```

**Optional:** Add Make targets for convenience

**File:** `tests/Makefile` (new file)
```makefile
.PHONY: start-db stop-db test test-fast

start-db:
	docker-compose up -d
	@echo "Waiting for MySQL to be ready..."
	@until docker-compose exec -T test-db mysqladmin ping -h localhost -uroot -proot --silent; do \
		sleep 1; \
	done
	@echo "MySQL is ready!"

stop-db:
	docker-compose down

clean-db:
	docker-compose down -v

test: start-db
	pytest -v
	$(MAKE) stop-db

test-fast:
	pytest -v -n auto
```

**Benefits:**
- Self-documenting test environment
- One-command setup for new developers
- Reproducible test database state
- CI/CD friendly
- Version-controlled database configuration

**Files to Create:**
- `tests/docker-compose.yml`
- `tests/Makefile` (optional)

**Files to Modify:**
- `tests/README.md` - Add docker-compose instructions
- `CONTRIBUTING.md` - Update Docker setup section

**Estimated Time:** 1-2 hours

---

### 3.3 Add pytest Plugins for Enhanced Testing

**Problem:** Missing common pytest features (coverage, parallel execution, etc.)

**Solution:** Add pytest plugins to `conda-env.yaml`

**File:** `conda-env.yaml` (add to pip dependencies)
```yaml
dependencies:
  - python=3.13
  - pip
  - pip:
      # Existing dependencies...

      # Testing plugins
      - pytest-cov          # Coverage reporting
      - pytest-xdist        # Parallel test execution
      - pytest-timeout      # Prevent hanging tests
      - pytest-benchmark    # Performance benchmarks (optional)
      - pytest-sugar        # Better output formatting (optional)
```

**Update pytest.ini:**
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
- `conda-env.yaml` - Add plugins
- `pytest.ini` - Add plugin configuration
- `tests/README.md` - Document plugin usage

**Installation:**
```bash
# Recreate conda environment with new dependencies
make conda-env

# Or update existing environment
conda env update --file conda-env.yaml --prune
```

**Estimated Time:** 1 hour

---

### 3.4 Add GitHub Actions CI (Optional)

**Problem:** Tests only run locally, no automated CI

**Solution:** Add GitHub Actions workflow for automated testing

**File:** `.github/workflows/tests.yml` (new file)
```yaml
name: Test Suite

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main, develop ]

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      mysql:
        image: mysql:8.0
        env:
          MYSQL_ROOT_PASSWORD: root
          MYSQL_DATABASE: sam
        ports:
          - 3306:3306
        options: >-
          --health-cmd="mysqladmin ping -h localhost -uroot -proot"
          --health-interval=10s
          --health-timeout=5s
          --health-retries=5

    steps:
    - uses: actions/checkout@v3

    - name: Setup Miniconda
      uses: conda-incubator/setup-miniconda@v2
      with:
        auto-update-conda: true
        python-version: 3.13
        environment-file: conda-env.yaml
        activate-environment: sam-queries

    - name: Create .env file
      run: |
        cat > .env << EOF
        SAM_DB_USERNAME=root
        SAM_DB_PASSWORD=root
        SAM_DB_SERVER=127.0.0.1
        SAM_DB_PORT=3306
        EOF

    - name: Run tests
      shell: bash -l {0}
      run: |
        pytest tests/ -v --cov=sam --cov-report=xml

    - name: Upload coverage to Codecov
      uses: codecov/codecov-action@v3
      with:
        file: ./coverage.xml
        fail_ci_if_error: true
```

**Benefits:**
- Automated testing on every push/PR
- Catches regressions early
- Test results visible in PR
- Coverage tracking over time

**Files to Create:**
- `.github/workflows/tests.yml`

**Files to Modify:**
- Add `.coveragerc` for coverage configuration (optional)

**Estimated Time:** 1-2 hours

---

## PHASE 4: Advanced Enhancements
**Goal:** Production-grade testing
**Effort:** 8+ hours
**Risk:** Medium-High
**Priority:** Low (future consideration)

### 4.1 Add Performance/Benchmark Tests

**Problem:** No performance baselines, no way to detect regressions

**Solution:** Add pytest-benchmark tests for critical operations

**Example:**
```python
import pytest
from sam import User, Project

class TestPerformance:
    """Performance benchmarks for critical operations."""

    def test_user_search_performance(self, session, benchmark):
        """Benchmark user search by username."""
        def search_user():
            return User.get_by_username(session, 'benkirk')

        result = benchmark(search_user)
        assert result is not None
        # Benchmark will track execution time

    def test_project_allocation_usage_performance(self, session, test_project, benchmark):
        """Benchmark allocation usage calculation."""
        def get_usage():
            return test_project.get_detailed_allocation_usage()

        result = benchmark(get_usage)
        assert len(result) > 0
        # Ensure calculation completes in reasonable time

    @pytest.mark.parametrize('count', [10, 100, 1000])
    def test_bulk_query_performance(self, session, benchmark, count):
        """Benchmark bulk user queries."""
        def bulk_query():
            return session.query(User).limit(count).all()

        result = benchmark(bulk_query)
        assert len(result) == count
```

**Usage:**
```bash
# Run benchmarks
pytest tests/performance/ --benchmark-only

# Generate report
pytest tests/performance/ --benchmark-only --benchmark-save=baseline

# Compare against baseline
pytest tests/performance/ --benchmark-only --benchmark-compare=baseline
```

**Benefits:**
- Performance regression detection
- Baseline metrics for optimization
- Identify N+1 query problems
- Track performance over time

**Estimated Time:** 3-4 hours

---

### 4.2 Add Contract Tests for API

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

---

### 4.3 Add Mock/Stub Support

**Problem:** All tests require live database

**Solution:** Add pytest-mock for testing error scenarios

**Example:**
```python
import pytest
from unittest.mock import Mock, patch

class TestErrorHandling:
    """Test error handling without live database."""

    def test_database_connection_failure(self, mocker):
        """Test handling of database connection failures."""
        # Mock create_engine to raise exception
        mock_engine = mocker.patch('sqlalchemy.create_engine')
        mock_engine.side_effect = Exception("Connection refused")

        with pytest.raises(Exception, match="Connection refused"):
            create_sam_engine()

    def test_query_timeout_handling(self, mocker, session):
        """Test handling of query timeouts."""
        # Mock query to raise timeout
        mock_query = mocker.patch.object(session, 'query')
        mock_query.side_effect = TimeoutError("Query timeout")

        with pytest.raises(TimeoutError):
            User.get_by_username(session, 'test')
```

**Benefits:**
- Test error scenarios
- Faster unit tests (no DB)
- Test external service failures
- Better coverage of edge cases

**Estimated Time:** 2-3 hours

---

### 4.4 Add Load/Concurrency Tests

**Problem:** No testing of concurrent operations

**Solution:** Add multi-threaded tests for race conditions

**Example:**
```python
import pytest
import threading
from sam import User, Project

class TestConcurrency:
    """Test concurrent database operations."""

    def test_concurrent_reads(self, SessionFactory):
        """Test multiple threads reading simultaneously."""
        results = []
        errors = []

        def read_user():
            try:
                session = SessionFactory()
                user = User.get_by_username(session, 'benkirk')
                results.append(user.username)
                session.close()
            except Exception as e:
                errors.append(e)

        # Spawn 10 concurrent threads
        threads = [threading.Thread(target=read_user) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 10
        assert all(r == 'benkirk' for r in results)

    def test_connection_pool_exhaustion(self, SessionFactory):
        """Test behavior when connection pool is exhausted."""
        # This test would verify pool_size and max_overflow settings
        pass
```

**Benefits:**
- Detect race conditions
- Validate connection pool configuration
- Test thread safety
- Production scenario testing

**Estimated Time:** 2-3 hours

---

## Summary & Recommendations

### Execution Priority

**Do Now (Already Complete):**
- ✅ Phase 1: Configuration unification

**Do Soon (Phase 2 - High Priority):**
- 2.1: Add common test fixtures (2 hrs)
- 2.2: Add parameterized tests (1-2 hrs)
- 2.3: Add pytest.ini (30 min)
- **Total:** 3.5-4.5 hours

**Do Later (Phase 3 - Medium Priority):**
- 3.1: Reorganize directory structure (2-3 hrs)
- 3.2: Add Docker Compose (1-2 hrs)
- 3.3: Add pytest plugins (1 hr)
- 3.4: Add GitHub Actions (1-2 hrs) - optional
- **Total:** 5-8 hours

**Do Eventually (Phase 4 - Low Priority):**
- 4.1: Performance tests (3-4 hrs)
- 4.2: Contract tests (2-3 hrs)
- 4.3: Mock support (2-3 hrs)
- 4.4: Concurrency tests (2-3 hrs)
- **Total:** 9-13 hours

### Risk Assessment

| Phase | Risk Level | Rollback Strategy |
|-------|-----------|-------------------|
| Phase 2 | **LOW** | Git revert, fixtures are additive |
| Phase 3.1 | **MEDIUM** | Git revert, requires import updates |
| Phase 3.2-3.4 | **LOW** | Optional tools, easy to remove |
| Phase 4 | **MEDIUM** | Advanced features, may need debugging |

### Expected Benefits

**After Phase 2:**
- 30%+ reduction in test code via fixtures and parameterization
- Better test organization with pytest.ini
- Foundation for advanced testing

**After Phase 3:**
- Can run test categories independently
- One-command Docker startup
- Faster test execution with parallel runs
- Coverage reporting
- CI/CD pipeline (if using GitHub Actions)

**After Phase 4:**
- Performance benchmarks and regression detection
- API contract validation
- Comprehensive error scenario coverage
- Production-readiness validation

---

## Notes

- Each phase can be executed independently
- Phases 2 and 3 are highly recommended
- Phase 4 is optional, implement based on project needs
- All changes should maintain 100% test pass rate
- Update this document as phases are completed

**Last Updated:** 2025-11-13
**Status:** Phase 1 complete, Phases 2-4 planned
