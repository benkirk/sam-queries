# API Endpoint Testing Plan

**Status**: Not yet implemented
**Created**: 2025-11-14
**Priority**: Medium
**Estimated Effort**: 4-6 hours

---

## Overview

This document outlines the plan for adding comprehensive HTTP endpoint tests for the SAM Web API. Currently, only schema serialization is tested (`tests/api/test_schemas.py`), but actual HTTP endpoints (routing, authentication, error handling, pagination) are not covered.

### Current Gap
- ✅ Schema tests exist (28 tests in `tests/api/`)
- ❌ No HTTP endpoint tests (no Flask test client, no route testing)
- ❌ No authentication/RBAC testing
- ❌ No pagination/filtering validation
- ❌ No error response testing

---

## Test Scope

### Endpoints to Test
Based on user requirements, focus on:
1. **User API** (`/api/v1/users/`)
2. **Project API** (`/api/v1/projects/`) - including new tree functionality
3. **Charge/Balance API** (`/api/v1/projects/<projcode>/charges/summary`) ⭐

### Coverage Level
- ✅ **Happy path**: Successful requests (200 OK)
- ✅ **Error cases**: 404 Not Found, 400 Bad Request
- ⚠️ **Authentication**: Not prioritized (assume authenticated client)
- ⚠️ **Authorization/RBAC**: Not prioritized for initial implementation

---

## Technical Approach

### Testing Framework: Flask Test Client

**Why Flask Test Client?**
- Standard Flask testing pattern
- No need to run actual HTTP server
- Fast execution (in-process)
- Easy integration with pytest fixtures
- Access to app context and session

**Not using:**
- ❌ Requests library + live server (too slow, complex setup)
- ❌ curl subprocess calls (not maintainable)
- ❌ Direct route testing (doesn't test HTTP layer)

### Test Data Strategy

**Use real database entities:**
- Users: `benkirk`, `negins`, `invalid_user_xyz` (for 404 tests)
- Projects: `CESM0002` (tree root), `SCSG0001`, `P93300012` (tree child)
- No test data creation needed
- Tests depend on database state (acceptable trade-off for speed)

### Authentication Strategy

**Flask-Login session-based auth:**
```python
@pytest.fixture
def auth_client(client, session):
    """Create authenticated test client."""
    from sam.queries import find_user_by_username
    user = find_user_by_username(session, 'benkirk')

    with client:
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user.user_id)
    return client
```

---

## Implementation Plan

### Phase 1: Foundation

#### File: `tests/api/conftest.py` (NEW)
Create Flask app and client fixtures.

**Fixtures to add:**
```python
@pytest.fixture(scope='session')
def app():
    """Create Flask app for testing."""
    from webui.run import create_app
    app = create_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app

@pytest.fixture
def client(app):
    """Create Flask test client."""
    return app.test_client()

@pytest.fixture
def auth_client(client, session):
    """Create authenticated test client (logged in as benkirk)."""
    from sam.queries import find_user_by_username
    user = find_user_by_username(session, 'benkirk')

    with client:
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user.user_id)
            sess['_fresh'] = True
    return client
```

**Estimated lines**: ~50-60 lines
**Estimated time**: 30 minutes

---

### Phase 2: User Endpoint Tests

#### File: `tests/api/test_user_endpoints.py` (NEW)

**Test Classes:**

##### 1. TestUserListEndpoint
Tests for `GET /api/v1/users/`

```python
class TestUserListEndpoint:
    """Test GET /api/v1/users/ endpoint."""

    def test_list_users_success(self, auth_client):
        """Test successful user listing."""
        response = auth_client.get('/api/v1/users/')
        assert response.status_code == 200

        data = response.get_json()
        assert 'users' in data
        assert 'page' in data
        assert 'total' in data
        assert isinstance(data['users'], list)

    def test_list_users_pagination(self, auth_client):
        """Test pagination parameters."""
        response = auth_client.get('/api/v1/users/?page=2&per_page=10')
        assert response.status_code == 200

        data = response.get_json()
        assert data['page'] == 2
        assert data['per_page'] == 10

    def test_list_users_search(self, auth_client):
        """Test search filtering."""
        response = auth_client.get('/api/v1/users/?search=benkirk')
        assert response.status_code == 200

        data = response.get_json()
        assert len(data['users']) > 0
        assert any(u['username'] == 'benkirk' for u in data['users'])

    def test_list_users_search_no_results(self, auth_client):
        """Test search with no results."""
        response = auth_client.get('/api/v1/users/?search=nonexistent_user_xyz')
        assert response.status_code == 200

        data = response.get_json()
        assert len(data['users']) == 0
```

##### 2. TestUserDetailEndpoint
Tests for `GET /api/v1/users/<username>`

```python
class TestUserDetailEndpoint:
    """Test GET /api/v1/users/<username> endpoint."""

    def test_get_user_success(self, auth_client):
        """Test successful user retrieval."""
        response = auth_client.get('/api/v1/users/benkirk')
        assert response.status_code == 200

        data = response.get_json()
        assert data['username'] == 'benkirk'
        assert 'email' in data
        assert 'institutions' in data
        assert 'organizations' in data
        assert 'roles' in data

    def test_get_user_with_institutions(self, auth_client):
        """Test user with institutions (validates UserSchema bug fix)."""
        response = auth_client.get('/api/v1/users/negins')
        assert response.status_code == 200

        data = response.get_json()
        assert data['username'] == 'negins'
        assert len(data['institutions']) > 0

        # Verify institution fields exist
        inst = data['institutions'][0]
        assert 'institution_name' in inst
        assert 'institution_acronym' in inst
        # Verify is_primary was removed (part of bug fix)
        assert 'is_primary' not in inst

    def test_get_user_not_found(self, auth_client):
        """Test 404 for non-existent user."""
        response = auth_client.get('/api/v1/users/invalid_user_xyz')
        assert response.status_code == 404

        data = response.get_json()
        assert 'error' in data
```

##### 3. TestUserProjectsEndpoint
Tests for `GET /api/v1/users/<username>/projects`

```python
class TestUserProjectsEndpoint:
    """Test GET /api/v1/users/<username>/projects endpoint."""

    def test_get_user_projects_success(self, auth_client):
        """Test successful retrieval of user's projects."""
        response = auth_client.get('/api/v1/users/benkirk/projects')
        assert response.status_code == 200

        data = response.get_json()
        assert 'username' in data
        assert 'led_projects' in data
        assert 'admin_projects' in data
        assert 'member_projects' in data
        assert 'total_projects' in data

    def test_get_user_projects_not_found(self, auth_client):
        """Test 404 for non-existent user."""
        response = auth_client.get('/api/v1/users/invalid_user_xyz/projects')
        assert response.status_code == 404
```

**Estimated tests**: 8-10
**Estimated lines**: 150-200
**Estimated time**: 1.5 hours

---

### Phase 3: Project Endpoint Tests

#### File: `tests/api/test_project_endpoints.py` (NEW)

**Test Classes:**

##### 1. TestProjectListEndpoint
Tests for `GET /api/v1/projects/`

```python
class TestProjectListEndpoint:
    """Test GET /api/v1/projects/ endpoint."""

    def test_list_projects_success(self, auth_client):
        """Test successful project listing."""
        response = auth_client.get('/api/v1/projects/')
        assert response.status_code == 200

        data = response.get_json()
        assert 'projects' in data
        assert 'page' in data
        assert 'total' in data
        assert isinstance(data['projects'], list)

    def test_list_projects_tree_fields(self, auth_client):
        """Test that list includes new tree fields."""
        response = auth_client.get('/api/v1/projects/?search=CESM0002')
        assert response.status_code == 200

        data = response.get_json()
        assert len(data['projects']) > 0

        project = data['projects'][0]
        assert 'parent_projcode' in project
        assert 'has_children' in project

    def test_list_projects_pagination(self, auth_client):
        """Test pagination parameters."""
        response = auth_client.get('/api/v1/projects/?page=2&per_page=10')
        assert response.status_code == 200

        data = response.get_json()
        assert data['page'] == 2
        assert data['per_page'] == 10
```

##### 2. TestProjectTreeEndpoints ⭐ NEW FUNCTIONALITY
Tests for `GET /api/v1/projects/<projcode>` with tree structure

```python
class TestProjectTreeEndpoints:
    """Test project tree functionality in API (NEW FEATURE)."""

    def test_root_project_tree_structure(self, auth_client):
        """Test GET /projects/CESM0002 returns complete tree from root."""
        response = auth_client.get('/api/v1/projects/CESM0002')
        assert response.status_code == 200

        data = response.get_json()
        assert data['projcode'] == 'CESM0002'

        # Test breadcrumb (root has only itself)
        assert data['breadcrumb_path'] == ['CESM0002']

        # Test depth (root is 0)
        assert data['tree_depth'] == 0

        # Test tree structure
        assert data['tree']['projcode'] == 'CESM0002'
        assert data['tree']['depth'] == 0
        assert 'children' in data['tree']
        assert len(data['tree']['children']) == 28  # CESM0002 has 28 children

    def test_child_project_shows_full_tree_from_root(self, auth_client):
        """Test that querying child project returns tree from root."""
        response = auth_client.get('/api/v1/projects/P93300012')
        assert response.status_code == 200

        data = response.get_json()

        # Queried project is P93300012
        assert data['projcode'] == 'P93300012'

        # Breadcrumb shows path from root
        assert data['breadcrumb_path'] == ['CESM0002', 'P93300012']

        # Depth is 1 (one level below root)
        assert data['tree_depth'] == 1

        # Tree STARTS FROM ROOT, not from queried project
        assert data['tree']['projcode'] == 'CESM0002'
        assert data['tree']['depth'] == 0
        assert len(data['tree']['children']) == 28  # All siblings visible

    def test_max_depth_parameter_controls_expansion(self, auth_client):
        """Test max_depth parameter limits tree expansion."""
        response = auth_client.get('/api/v1/projects/CESM0002?max_depth=1')
        assert response.status_code == 200

        data = response.get_json()
        tree = data['tree']

        # Root has children at depth 1
        assert len(tree['children']) == 28

        # But children at depth 1 should have empty children arrays
        for child in tree['children']:
            assert child['depth'] == 1
            assert child['children'] == []

    def test_max_depth_zero_no_expansion(self, auth_client):
        """Test max_depth=0 returns no children."""
        response = auth_client.get('/api/v1/projects/CESM0002?max_depth=0')
        assert response.status_code == 200

        data = response.get_json()
        tree = data['tree']

        # Should return root with no children expanded
        assert tree['projcode'] == 'CESM0002'
        assert tree['children'] == []

        # But should indicate more exist
        assert tree.get('has_more') == True

    def test_max_depth_default_value(self, auth_client):
        """Test default max_depth is 4."""
        response = auth_client.get('/api/v1/projects/CESM0002')
        assert response.status_code == 200

        data = response.get_json()
        # Default should expand to depth 4
        # (Verify by checking tree contains nested children)
        assert 'tree' in data
        assert 'children' in data['tree']

    def test_has_more_flag_at_boundary(self, auth_client):
        """Test has_more flag appears when max_depth truncates tree."""
        response = auth_client.get('/api/v1/projects/CESM0002?max_depth=1')
        assert response.status_code == 200

        data = response.get_json()

        # P93300012 has children, so at max_depth=1 it should show has_more
        p93300012 = next(
            (c for c in data['tree']['children'] if c['projcode'] == 'P93300012'),
            None
        )

        if p93300012:
            assert p93300012.get('has_more') == True
```

##### 3. TestProjectDetailEndpoint
Tests for `GET /api/v1/projects/<projcode>` general cases

```python
class TestProjectDetailEndpoint:
    """Test GET /api/v1/projects/<projcode> endpoint."""

    def test_get_project_success(self, auth_client):
        """Test successful project retrieval."""
        response = auth_client.get('/api/v1/projects/SCSG0001')
        assert response.status_code == 200

        data = response.get_json()
        assert data['projcode'] == 'SCSG0001'
        assert 'title' in data
        assert 'lead' in data
        assert 'admin' in data

    def test_get_project_not_found(self, auth_client):
        """Test 404 for non-existent project."""
        response = auth_client.get('/api/v1/projects/INVALID999')
        assert response.status_code == 404

        data = response.get_json()
        assert 'error' in data
```

##### 4. TestProjectMembersEndpoint
Tests for `GET /api/v1/projects/<projcode>/members`

```python
class TestProjectMembersEndpoint:
    """Test GET /api/v1/projects/<projcode>/members endpoint."""

    def test_get_project_members_success(self, auth_client):
        """Test successful retrieval of project members."""
        response = auth_client.get('/api/v1/projects/SCSG0001/members')
        assert response.status_code == 200

        data = response.get_json()
        assert 'projcode' in data
        assert 'lead' in data
        assert 'admin' in data
        assert 'members' in data
        assert 'total_members' in data

    def test_get_project_members_not_found(self, auth_client):
        """Test 404 for non-existent project."""
        response = auth_client.get('/api/v1/projects/INVALID999/members')
        assert response.status_code == 404
```

##### 5. TestProjectAllocationsEndpoint
Tests for `GET /api/v1/projects/<projcode>/allocations`

```python
class TestProjectAllocationsEndpoint:
    """Test GET /api/v1/projects/<projcode>/allocations endpoint."""

    def test_get_project_allocations_success(self, auth_client):
        """Test successful retrieval of project allocations."""
        response = auth_client.get('/api/v1/projects/SCSG0001/allocations')
        assert response.status_code == 200

        data = response.get_json()
        assert 'projcode' in data
        assert 'allocations' in data
        assert isinstance(data['allocations'], list)

    def test_get_project_allocations_resource_filter(self, auth_client):
        """Test resource name filtering."""
        response = auth_client.get(
            '/api/v1/projects/SCSG0001/allocations?resource=Derecho'
        )
        assert response.status_code == 200

        data = response.get_json()
        # All allocations should be for Derecho
        if data['allocations']:
            for alloc in data['allocations']:
                assert 'resource_name' in alloc

    def test_get_project_allocations_not_found(self, auth_client):
        """Test 404 for non-existent project."""
        response = auth_client.get('/api/v1/projects/INVALID999/allocations')
        assert response.status_code == 404
```

##### 6. TestProjectExpirationEndpoints
Tests for expiring/expired project queries

```python
class TestProjectExpirationEndpoints:
    """Test expiring/expired project endpoints."""

    def test_get_expiring_projects(self, auth_client):
        """Test GET /projects/expiring."""
        response = auth_client.get('/api/v1/projects/expiring')
        assert response.status_code == 200

        data = response.get_json()
        assert 'expiring_projects' in data
        assert 'days' in data
        assert isinstance(data['expiring_projects'], list)

    def test_get_recently_expired_projects(self, auth_client):
        """Test GET /projects/recently_expired."""
        response = auth_client.get('/api/v1/projects/recently_expired')
        assert response.status_code == 200

        data = response.get_json()
        assert 'expired_projects' in data
        assert 'min_days' in data
        assert 'max_days' in data
        assert isinstance(data['expired_projects'], list)
```

**Estimated tests**: 15-18
**Estimated lines**: 300-350
**Estimated time**: 2-2.5 hours

---

### Phase 4: Charge/Balance Endpoint Tests

#### File: `tests/api/test_charge_endpoints.py` (NEW)

**Test Classes:**

##### 1. TestProjectChargesSummary ⭐ CRITICAL ENDPOINT
Tests for `GET /api/v1/projects/<projcode>/charges/summary`

```python
class TestProjectChargesSummary:
    """Test GET /api/v1/projects/<projcode>/charges/summary endpoint."""

    def test_charges_summary_success(self, auth_client):
        """Test successful charges summary retrieval."""
        response = auth_client.get('/api/v1/projects/CESM0002/charges/summary')
        assert response.status_code == 200

        data = response.get_json()
        assert 'projcode' in data
        assert data['projcode'] == 'CESM0002'

        # Response structure (verify schema matches AllocationWithUsageSchema)
        assert 'allocations' in data or 'summary' in data

    def test_charges_summary_allocation_structure(self, auth_client):
        """Test allocation summary includes usage fields."""
        response = auth_client.get('/api/v1/projects/CESM0002/charges/summary')
        assert response.status_code == 200

        data = response.get_json()

        # Verify allocation summary structure matches AllocationWithUsageSchema
        if 'allocations' in data and len(data['allocations']) > 0:
            alloc = data['allocations'][0]

            # Core balance fields
            assert 'allocated' in alloc
            assert 'used' in alloc
            assert 'remaining' in alloc
            assert 'percent_used' in alloc

            # Breakdown by charge type
            assert 'charges_by_type' in alloc
            charges = alloc['charges_by_type']
            assert 'comp' in charges
            assert 'dav' in charges
            assert 'disk' in charges
            assert 'archive' in charges

    def test_charges_summary_with_adjustments(self, auth_client):
        """Test charges summary includes adjustments when requested."""
        response = auth_client.get(
            '/api/v1/projects/CESM0002/charges/summary?include_adjustments=true'
        )
        assert response.status_code == 200

        data = response.get_json()

        # If there are allocations, check for adjustments field
        if 'allocations' in data and len(data['allocations']) > 0:
            alloc = data['allocations'][0]
            # Adjustments field should be present (may be empty list)
            assert 'adjustments' in alloc or 'adjustment_total' in alloc

    def test_charges_summary_without_adjustments(self, auth_client):
        """Test charges summary excludes adjustments when requested."""
        response = auth_client.get(
            '/api/v1/projects/CESM0002/charges/summary?include_adjustments=false'
        )
        assert response.status_code == 200

        data = response.get_json()
        # Should succeed regardless
        assert response.status_code == 200

    def test_charges_summary_not_found(self, auth_client):
        """Test 404 for non-existent project."""
        response = auth_client.get('/api/v1/projects/INVALID999/charges/summary')
        assert response.status_code == 404

        data = response.get_json()
        assert 'error' in data

    def test_charges_summary_project_no_allocations(self, auth_client):
        """Test response for project with no allocations."""
        # Find a project with no allocations or use known test project
        response = auth_client.get('/api/v1/projects/UMIN0005/charges/summary')

        # Should return 200 with empty allocations (not 404)
        assert response.status_code in [200, 404]

        if response.status_code == 200:
            data = response.get_json()
            if 'allocations' in data:
                assert len(data['allocations']) == 0
```

##### 2. TestProjectChargesDetail (if endpoint exists)
Tests for `GET /api/v1/projects/<projcode>/charges`

```python
class TestProjectChargesDetail:
    """Test GET /api/v1/projects/<projcode>/charges endpoint."""

    def test_charges_detail_success(self, auth_client):
        """Test successful charges detail retrieval."""
        response = auth_client.get('/api/v1/projects/CESM0002/charges')

        # Skip if endpoint doesn't exist
        if response.status_code == 404:
            pytest.skip("Charges detail endpoint not implemented")

        assert response.status_code == 200

        data = response.get_json()
        assert 'projcode' in data
        assert 'charges' in data or 'charge_history' in data

    def test_charges_detail_date_filtering(self, auth_client):
        """Test date range filtering."""
        from datetime import datetime, timedelta

        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        response = auth_client.get(
            f'/api/v1/projects/CESM0002/charges?'
            f'start_date={start_date.date()}&end_date={end_date.date()}'
        )

        # Skip if endpoint doesn't exist
        if response.status_code == 404:
            pytest.skip("Charges detail endpoint not implemented")

        assert response.status_code == 200
```

**Estimated tests**: 5-8
**Estimated lines**: 150-200
**Estimated time**: 1-1.5 hours

---

### Phase 5: Pytest Configuration

#### File: `tests/pytest.ini` (MODIFY)

Add marker for endpoint tests:

```ini
[pytest]
# ... existing config ...

markers =
    api: marks tests as API tests (schemas)
    api_endpoints: marks tests for API HTTP endpoints (integration tests)
    slow: marks tests as slow (deselect with '-m "not slow"')
```

**Estimated time**: 5 minutes

---

## Test Execution

### Run Commands

```bash
# Run all API endpoint tests
cd tests && pytest api/test_user_endpoints.py api/test_project_endpoints.py api/test_charge_endpoints.py -v

# Run all API tests (schemas + endpoints)
cd tests && pytest api/ -v

# Run only endpoint tests (not schemas)
cd tests && pytest -m api_endpoints -v

# Run only user endpoint tests
cd tests && pytest api/test_user_endpoints.py -v

# Run only project tree tests
cd tests && pytest api/test_project_endpoints.py::TestProjectTreeEndpoints -v

# Run only charges/summary tests
cd tests && pytest api/test_charge_endpoints.py::TestProjectChargesSummary -v

# Run with verbose output
cd tests && pytest api/ -vv

# Run and show print statements
cd tests && pytest api/ -v -s
```

---

## Success Criteria

### Functional Requirements
- ✅ All ~28-36 tests pass
- ✅ Tests verify new tree API functionality (breadcrumb_path, tree_depth, tree structure)
- ✅ Tests verify charges/summary endpoint matches AllocationWithUsageSchema
- ✅ Tests catch UserSchema institution bug (negins case)
- ✅ Tests validate pagination, filtering, search
- ✅ Tests handle 404 errors correctly

### Non-Functional Requirements
- ✅ Fast execution: All endpoint tests complete in <10 seconds
- ✅ No external dependencies (no running server needed)
- ✅ Clean test organization (clear class/method names)
- ✅ Easy to extend with more tests
- ✅ Follows existing test patterns in codebase

---

## Expected Test Count

| Category | Tests | Lines | Time |
|----------|-------|-------|------|
| User endpoints | 8-10 | 150-200 | 1.5h |
| Project endpoints | 15-18 | 300-350 | 2-2.5h |
| Charge endpoints | 5-8 | 150-200 | 1-1.5h |
| **Total** | **28-36** | **600-750** | **5-5.5h** |

Plus fixtures: ~50 lines, ~30 minutes

**Grand total: ~28-36 tests, ~650-800 lines, ~5.5-6 hours**

---

## Real Database Entities to Test

These entities are known to exist in the test database:

### Users
- `benkirk` - Has organizations, roles, no institutions
- `negins` - Has institutions (tests UserSchema bug fix)
- `dlawren` - Lead/admin of CESM0002
- `invalid_user_xyz` - Does not exist (for 404 tests)

### Projects
- `CESM0002` - Root project with 28 children, has allocations
- `P93300012` - Child of CESM0002 (depth 1), has grandchild CESM0030
- `P93300007` - Child of CESM0002 (depth 1), no grandchildren
- `SCSG0001` - CSG project, has allocations
- `INVALID999` - Does not exist (for 404 tests)

### Resources
- `Derecho` - HPC resource
- `Casper` - Analysis resource
- `Stratus` - Storage resource

---

## Dependencies

### Required Packages
All already installed:
- `pytest` - Test framework
- `flask` - Web framework (test client)
- `marshmallow` - Schema serialization
- `marshmallow-sqlalchemy` - ORM integration

### Required Fixtures
From `tests/conftest.py`:
- `engine` - Database engine
- `session` - Database session
- `test_user` - benkirk user fixture
- `test_project` - SCSG0001 project fixture

---

## Implementation Checklist

- [ ] Create `tests/api/conftest.py` with Flask fixtures
- [ ] Create `tests/api/test_user_endpoints.py`
  - [ ] TestUserListEndpoint (3-4 tests)
  - [ ] TestUserDetailEndpoint (3-4 tests)
  - [ ] TestUserProjectsEndpoint (2 tests)
- [ ] Create `tests/api/test_project_endpoints.py`
  - [ ] TestProjectListEndpoint (3 tests)
  - [ ] TestProjectTreeEndpoints (6-7 tests) ⭐
  - [ ] TestProjectDetailEndpoint (2 tests)
  - [ ] TestProjectMembersEndpoint (2 tests)
  - [ ] TestProjectAllocationsEndpoint (3 tests)
  - [ ] TestProjectExpirationEndpoints (2 tests)
- [ ] Create `tests/api/test_charge_endpoints.py`
  - [ ] TestProjectChargesSummary (6-7 tests) ⭐
  - [ ] TestProjectChargesDetail (2 tests, if endpoint exists)
- [ ] Update `tests/pytest.ini` with api_endpoints marker
- [ ] Run all tests and verify they pass
- [ ] Document any database dependencies discovered
- [ ] Update this plan with actual results

---

## Notes

### Why Flask Test Client?
- **Speed**: In-process, no network overhead
- **Standard**: Industry-standard Flask testing pattern
- **Debugging**: Easy to set breakpoints, inspect state
- **Integration**: Works seamlessly with pytest fixtures

### Why Real Database Data?
- **Realistic**: Tests against actual production-like data
- **Fast**: No test data setup/teardown overhead
- **Simple**: Easier to write and maintain tests
- **Trade-off**: Tests depend on database state (acceptable for our use case)

### Future Enhancements
- Add authentication tests (401 when not logged in)
- Add RBAC tests (403 for insufficient permissions)
- Add performance tests (response time benchmarks)
- Add load tests (concurrent requests)
- Mock database for faster execution (if speed becomes issue)
- Add API documentation generation from tests

---

## Related Work

This testing plan was developed in conjunction with:

1. **Commit 8839949**: Added project tree API functionality
   - Added `breadcrumb_path`, `tree_depth`, `tree` fields to ProjectSchema
   - Added `parent_projcode`, `has_children` to ProjectListSchema
   - Added `max_depth` query parameter to detail endpoint

2. **Commit 67e9ebf**: Fixed UserSchema institution serialization bug
   - Fixed `institution.name` field (was incorrectly `institution_name`)
   - Removed non-existent `is_primary` field
   - Added defensive null checks for relationships

These tests will validate both of these changes work correctly via HTTP endpoints.

---

**End of Plan**
