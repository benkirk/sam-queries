# Contributing to SAM Queries

## Getting Started

### Prerequisites
- Conda
- Docker (for local database)
- Access to NCAR network (for <ucar.edu> database connections, not required once local development environment established)

### Initial Setup

Starting from a clean checkout:

1. **Bootstrap `conda` environment**
   ```bash
   source etc/config_env.sh
   ```

2. **Configure credentials**

   Create a `.env` file in the project root with your database credentials:
   ```bash
   #-------------------------
   # local container instance
   LOCAL_SAM_DB_USERNAME=.....
   LOCAL_SAM_DB_SERVER=127.0.0.1
   LOCAL_SAM_DB_PASSWORD=.....
   SAM_DB_USERNAME=${LOCAL_SAM_DB_USERNAME}
   SAM_DB_SERVER=${LOCAL_SAM_DB_SERVER}
   SAM_DB_PASSWORD=${LOCAL_SAM_DB_PASSWORD}

   #-------------------------
   # test instance
   TEST_SAM_DB_USERNAME=.....
   TEST_SAM_DB_SERVER=test-sam-sql.ucar.edu
   TEST_SAM_DB_PASSWORD=.....
   #SAM_DB_USERNAME=${TEST_SAM_DB_USERNAME}
   #SAM_DB_SERVER=${TEST_SAM_DB_SERVER}
   #SAM_DB_PASSWORD=${TEST_SAM_DB_PASSWORD}

   #-------------------------
   # # prod instance
   PROD_SAM_DB_USERNAME=.....
   PROD_SAM_DB_SERVER=sam-sql.ucar.edu
   PROD_SAM_DB_PASSWORD=.....
   #SAM_DB_USERNAME=${PROD_SAM_DB_USERNAME}
   #SAM_DB_SERVER=${PROD_SAM_DB_SERVER}
   #SAM_DB_PASSWORD=${PROD_SAM_DB_PASSWORD}
   ```

   **Note**: The `.env` file is gitignored and should never be committed.

3. **Set up local development database** (one-time setup, for CRUD operations)

   For safe CRUD operations and testing, you can create a local Docker-based MySQL instance with subsetted data:
   ```bash
   cd containers/sam-sql-dev
   ./docker_start.sh
   ./bootstrap_clone.py
   ```

   ⚠️ **Important**: The local database is **not anonymized** and may contain PII. Never push this data to public repositories.

4. **Verify environment**
   ```bash
   ./tests/check_environment.sh
   ```

5. **Run tests**
   ```bash
   python3 -m pytest ./tests -v
   ```

## Database Access

### Production & Test (Read-Only)
- `sam-sql.ucar.edu` - Production database (read-only access)
- `test-sam-sql.ucar.edu` - Test database (read-only access)

### Local Development (Read-Write)
- Docker container created by `containers/sam-sql-dev` scripts
- Subsetted data for faster development
- Safe for CRUD operations without affecting production

## Development Workflow

### Running Tests
```bash
# Run all tests
python3 -m pytest ./tests -v

# Run specific test file
python3 -m pytest tests/test_schema_validation.py -v

# Run with coverage
python3 -m pytest tests/ --cov=sam --cov-report=html
```

### Using the CLI Tool

The `sam_search.py` CLI provides quick access to user and project information:

```bash
# Search for a project with full details
./python/sam_search.py project SCSG0001 --verbose

# Search for a user
./python/sam_search.py user benkirk

# List user's projects
./python/sam_search.py user benkirk --list-projects

# Pattern search (SQL wildcards)
./python/sam_search.py project --search "SCSG%"

# Check upcoming expirations (within 32 days)
./python/sam_search.py project --upcoming-expirations

# Check recently expired projects
./python/sam_search.py project --recent-expirations --list-users

# Find users without active projects
./python/sam_search.py user --abandoned

# Search inactive projects
./python/sam_search.py --inactive-projects user benkirk --list-projects
```

### Launching the Web UI

```bash
./python/webui/run.py
```

The web interface will be available at `http://127.0.0.1:5050`

### Using the REST API

The REST API provides programmatic access to SAM data. All API endpoints require authentication.

**Note**: You must be logged in to access API endpoints. Use a browser session or provide authentication headers.

Example endpoints (requires authentication):
```bash
# Get user details
GET /api/v1/users/<username>

# Get user's projects
GET /api/v1/users/<username>/projects

# Get project details
GET /api/v1/projects/<projcode>

# Get project members
GET /api/v1/projects/<projcode>/members

# Get project allocations with current usage
GET /api/v1/projects/<projcode>/allocations

# Get detailed charge breakdown
GET /api/v1/projects/<projcode>/charges?start_date=2024-10-01&end_date=2024-10-31

# Get account balance
GET /api/v1/accounts/<account_id>/balance?include_adjustments=true

# List projects expiring soon
GET /api/v1/projects/expiring

# List recently expired projects
GET /api/v1/projects/recently_expired
```

All API responses are in JSON format using Marshmallow-SQLAlchemy schemas for consistent serialization.

## Adding New Features

### Adding ORM Models
1. Create model in appropriate domain module (`sam/core/`, `sam/resources/`, etc.)
2. Add to `sam/__init__.py` imports
3. Create comprehensive tests in `tests/test_new_models.py`
4. Run schema validation: `pytest tests/test_schema_validation.py`
5. Verify all tests pass: `pytest tests/`

### Adding API Endpoints
1. Create Marshmallow schema in `python/webui/schemas/`
2. Add endpoint to appropriate blueprint in `python/webui/api/`
3. Test manually via browser/curl
4. Add integration tests if needed

### Adding CLI Features
1. Add functionality to `python/sam_search.py`
2. Create integration tests in `tests/test_sam_search_cli.py`
3. Test manually: `./python/sam_search.py <command>`
4. Run test suite: `pytest tests/test_sam_search_cli.py`

## Code Style

- Follow existing patterns in the codebase
- Use type hints where helpful
- Write clear, concise docstrings with examples
- Explain "why" in comments, not "what"
- Add tests for new features
- Run schema validation tests after ORM changes

## Testing Philosophy

- **Schema validation is critical** - Prevents ORM/database drift
- **Integration tests preferred** - Test real workflows, not just units
- **Keep tests fast** - Full suite should run in ~1 minute
- **Test CLI output** - Users depend on consistent formatting

## Submitting Changes

1. Create a feature branch from the current development branch
2. Make your changes with clear, descriptive commits
3. Ensure all tests pass: `python3 -m pytest tests/`
4. Run schema validation: `python3 -m pytest tests/test_schema_validation.py`
5. Submit a pull request with a detailed description

## Common Pitfalls

❌ **DON'T** use `datetime.now(UTC)` - database uses naive datetimes
❌ **DON'T** assume single-column primary keys - check database first
❌ **DON'T** modify database schema - ORM follows database
❌ **DON'T** skip schema validation tests after model changes

✅ **DO** use schema validation tests before committing model changes
✅ **DO** check actual database schema when in doubt
✅ **DO** use bidirectional relationships with `back_populates`
✅ **DO** write integration tests for CLI features
✅ **DO** use proper exit codes (0=success, 1=not found, 2=error, 130=interrupt)

## Questions?

See [CLAUDE.md](CLAUDE.md) for comprehensive project documentation, including:
- Detailed ORM model reference
- Database schema patterns
- Marshmallow-SQLAlchemy schema usage
- Allocation balance calculation logic
- Common queries and examples
- Known issues and gotchas
