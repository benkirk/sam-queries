# Getting Started Guide - SAM Queries & Web Application

## Overview

Welcome to the **SAM (System for Allocation Management)** project! This guide will help you understand the technology stack, get your environment set up, and start contributing effectively.

### What is SAM?

- **Python ORM and query tools** for NCAR's resource allocation and accounting database
- **Web application** for managing HPC allocations, user accounts, and project tracking
- **CLI tools** for searching users, projects, and generating reports
- **Used by CISL** to manage Derecho, Casper, and other computational resources

### What You'll Learn

This guide covers:
- Core technologies with learning resources
- Project-specific patterns and conventions
- Quick start commands
- Common gotchas to avoid
- Learning paths for different experience levels

---

## Technology Stack Overview

SAM uses a modern Python web stack with focus on:

- **Type safety**: SQLAlchemy 2.0 with ORM models, Python type hints
- **Performance**: Parallel testing, connection pooling, three-tier API schemas
- **Developer experience**: Rich CLI output, hot reloading, comprehensive test suite
- **Production ready**: Containerized deployment, structured logging, audit trails

**Key Stats**:
- **Database**: 97 tables, 91+ ORM models (94% coverage)
- **Tests**: 380 passed, 77.47% code coverage
- **Test Speed**: 32 seconds (parallel, no coverage) / 97 seconds (with coverage)
- **API Endpoints**: RESTful under `/api/v1/` with Marshmallow serialization

---

## Core Technologies (Learn These First)

### 1. Python 3.13+

**What we use**: Python 3.13+ for all backend code (minimum: 3.10)

**Learning Resources**:
- [Official Python Tutorial](https://docs.python.org/3/tutorial/) - Start here if new to Python
- [Real Python](https://realpython.com/) - Practical tutorials and in-depth guides
- [Python Type Hints](https://docs.python.org/3/library/typing.html) - Used throughout our codebase
- [Python Data Model](https://docs.python.org/3/reference/datamodel.html) - Understanding `__init__`, `__repr__`, etc.

**Key concepts for this project**:
- **Object-oriented programming**: Classes, inheritance, mixins
- **Decorators**: `@property`, `@classmethod`, `@hybrid_property`, `@app.route`
- **Context managers**: `with` statements for resource management
- **Type hints**: Better IDE support and code clarity
- **Generators**: Used in data processing

**Project-specific**:
- See `src/sam/base.py` for our mixin classes
- We use Python 3.13+ features (type hints, dataclasses patterns)
- Code style follows PEP 8

---

### 2. SQLAlchemy 2.0 (ORM)

**What we use**: SQLAlchemy 2.0.45 for database access and ORM models

**Learning Resources**:
- [SQLAlchemy 2.0 Tutorial](https://docs.sqlalchemy.org/en/20/tutorial/) - Official comprehensive tutorial (START HERE)
- [SQLAlchemy ORM Quickstart](https://docs.sqlalchemy.org/en/20/orm/quickstart.html) - Faster introduction
- [SQLAlchemy 2.0 Migration Guide](https://docs.sqlalchemy.org/en/20/changelog/migration_20.html) - If familiar with 1.x
- [Hybrid Attributes](https://docs.sqlalchemy.org/en/20/orm/extensions/hybrid.html) - We use these extensively

**What to focus on**:
- **Declarative base** and mapped classes
- **Relationships**: `relationship()`, `back_populates`
- **Querying**: Modern `select()` statements (not legacy `query()`)
- **Hybrid properties**: `@hybrid_property` - works in both Python and SQL
- **Session management**: Transactions, commit, rollback
- **Connection pooling**: Performance optimization

**Project-specific patterns**:
- **Base classes**: See `src/sam/base.py` for `Base`, mixins, and common patterns
- **Example model**: `src/sam/core/user.py` - well-documented reference
- **Hybrid properties**: `Allocation.is_active` works in Python expressions and SQL WHERE clauses
- **Class methods**: `User.get_by_username(session, username)` - preferred query pattern
- **SessionMixin**: Models can access session via `self.session`

**Important**:
- We use **SQLAlchemy 2.0 style** (not 1.x legacy patterns)
- Database is **source of truth** - ORM follows database schema
- Always use `back_populates` for bidirectional relationships

---

### 3. Flask 3.x (Web Framework)

**What we use**: Flask 3.1.2 for our web application

**Learning Resources**:
- [Flask Quickstart](https://flask.palletsprojects.com/en/stable/quickstart/) - 30-minute intro
- [Flask Tutorial](https://flask.palletsprojects.com/en/stable/tutorial/) - Build a complete application
- [Flask Patterns](https://flask.palletsprojects.com/en/stable/patterns/) - Best practices and design patterns
- [Flask Extensions](https://flask.palletsprojects.com/en/stable/extensions/) - Ecosystem overview

**What to focus on**:
- **Application factory pattern** - We use this (`create_app()`)
- **Blueprints** for organizing routes by feature
- **Request/Response cycle**: `request`, `g`, `session` objects
- **Templates with Jinja2**: Template inheritance, filters, context
- **Flask extensions**: Flask-Login, Flask-Admin, Flask-SQLAlchemy

**Project-specific patterns**:
- **Application factory**: `src/webapp/run.py` - how we create and configure the app
- **Blueprints**: `src/webapp/api/v1/` - organized by feature (users, projects, allocations, admin)
- **Authentication**: Flask-Login with custom user loader
- **Admin interface**: Flask-Admin with custom ModelViews

**Directory structure**:
```
src/webapp/
├── run.py              # Application factory, configuration
├── api/
│   └── v1/            # RESTful API endpoints
│       ├── users.py
│       ├── projects.py
│       ├── allocations.py
│       └── status.py
├── admin/             # Flask-Admin custom views
├── templates/         # Jinja2 templates
├── static/            # CSS, JavaScript, images
└── utils/             # Helper functions
```

---

### 4. Marshmallow & Marshmallow-SQLAlchemy (Serialization)

**What we use**: Marshmallow 4.1.1 + Marshmallow-SQLAlchemy 1.4.2 for API serialization

**Learning Resources**:
- [Marshmallow Quickstart](https://marshmallow.readthedocs.io/en/stable/quickstart.html) - Essential concepts
- [Marshmallow-SQLAlchemy Docs](https://marshmallow-sqlalchemy.readthedocs.io/) - ORM integration
- [Schema Design Patterns](https://marshmallow.readthedocs.io/en/stable/examples.html) - Real-world examples
- [Advanced Usage](https://marshmallow.readthedocs.io/en/stable/quickstart.html#validation) - Validation, nesting

**What to focus on**:
- **Schema definition** with fields
- **Nested schemas** for relationships
- **Method fields** for calculated values
- **Serialization vs deserialization**: `dump()` vs `load()`
- **Context passing**: Dynamic behavior based on request context

**Project-specific patterns - Three-Tier Schema Strategy** ⭐

We use a **three-tier schema architecture** for optimal API performance:

1. **Full Schemas** (`UserSchema`, `ProjectSchema`):
   - All fields + nested relationships
   - **Use for**: Single object detail views (`GET /api/v1/users/<username>`)
   - **Performance**: Slower due to relationship loading
   - **Example fields**: All user data + email_addresses + projects + institutions

2. **List Schemas** (`UserListSchema`, `ProjectListSchema`):
   - Core fields only, NO expensive nested queries
   - **Use for**: Collection endpoints (`GET /api/v1/users/`)
   - **Performance**: Fast, suitable for pagination
   - **Example fields**: Basic user data (id, name, email) but NO relationships

3. **Summary Schemas** (`UserSummarySchema`, `ProjectSummarySchema`):
   - Minimal fields for references
   - **Use for**: Nested within other schemas (e.g., `project.lead`)
   - **Performance**: Fastest, no additional queries
   - **Example fields**: Just id, name, code - essential identifiers only

**When to use which tier:**
- Fetching **1 object** → Use Full Schema (user profile page)
- Fetching **10-100 objects** → Use List Schema (user listing, search results)
- Showing **related object** → Use Summary Schema (project's lead within project detail)

**Concrete Example**:
```python
# UserSchema (Full) - Returns everything
{
    "user_id": 12345,
    "username": "benkirk",
    "first_name": "Benjamin",
    "last_name": "Kirk",
    "email_addresses": [{"email_address": "benkirk@ucar.edu", "is_primary": true}],
    "active_projects": [{"projcode": "SCSG0001", "title": "..."}],
    "institutions": [...],
    "organizations": [...]
}

# UserListSchema - Just core fields
{
    "user_id": 12345,
    "username": "benkirk",
    "first_name": "Benjamin",
    "last_name": "Kirk",
    "primary_email": "benkirk@ucar.edu"
}

# UserSummarySchema - Minimal identifier
{
    "user_id": 12345,
    "username": "benkirk",
    "full_name": "Benjamin Shelton Kirk"
}
```

**Most Important Schema**: `AllocationWithUsageSchema` in `src/sam/schemas/allocation.py`
- Calculates allocation balances in real-time
- Matches `sam-search` CLI output
- Aggregates charges from multiple summary tables
- Handles manual adjustments
- See CLAUDE.md "Marshmallow-SQLAlchemy Schemas" section for details

---

## Database Technologies

### 5. MySQL/MariaDB

**What we use**: MySQL 9.0 for data storage

**Learning Resources**:
- [MySQL Tutorial](https://dev.mysql.com/doc/mysql-tutorial-excerpt/8.0/en/) - Official guide
- [MySQL Workbench](https://dev.mysql.com/doc/workbench/en/) - GUI tool for visualization
- [SQL Teaching](https://www.sqlteaching.com/) - Interactive SQL tutorial
- [MySQL Performance](https://dev.mysql.com/doc/refman/8.0/en/optimization.html) - Query optimization

**What to focus on**:
- **Basic SQL queries**: SELECT, JOIN, WHERE, ORDER BY, LIMIT
- **Indexes and performance**: How indexes work, EXPLAIN plans
- **Transactions and ACID**: BEGIN, COMMIT, ROLLBACK
- **Foreign keys and constraints**: Referential integrity

**Project-specific info**:
- **Database**: `sam` with **97 tables** and comprehensive relationships
- **Access**: `mysql -u root -h 127.0.0.1 -proot sam`
- **Schema is source of truth**: ORM models follow database schema, not vice versa
- **Datetime**: Database uses **naive datetimes** (no timezone info)
- **Views**: Read-only database views for XRAS integration
- See `CLAUDE.md` "Database Connection" section for connection details

---

### 6. PyMySQL

**What we use**: PyMySQL 1.1.2 as SQLAlchemy database driver

**Learning Resources**:
- [PyMySQL Documentation](https://pymysql.readthedocs.io/) - Usage and API
- [SQLAlchemy MySQL Dialects](https://docs.sqlalchemy.org/en/20/dialects/mysql.html) - Integration details

**Why PyMySQL?**
- **Pure Python**: No C dependencies, easier installation
- **Compatible**: Drop-in replacement for MySQLdb
- **Good performance**: Sufficient for our use case
- **Easy SSL/TLS**: Simple configuration for encrypted connections

---

## Testing Technologies

### 7. pytest

**What we use**: pytest 9.0.2 + plugins (pytest-cov, pytest-xdist, pytest-timeout)

**Learning Resources**:
- [pytest Documentation](https://docs.pytest.org/) - Comprehensive guide
- [pytest Quickstart](https://docs.pytest.org/en/stable/getting-started.html) - Get running fast
- [Real Python pytest Guide](https://realpython.com/pytest-python-testing/) - Excellent tutorial
- [pytest Fixtures](https://docs.pytest.org/en/stable/fixture.html) - Reusable test setup

**What to focus on**:
- **Writing test functions**: `test_*` naming convention, assertions
- **Fixtures**: Setup/teardown, scope, dependencies
- **Markers**: Organizing tests (`@pytest.mark.slow`)
- **Parameterized tests**: Testing multiple inputs
- **Test coverage**: Understanding coverage reports

**Project-specific patterns**:

```bash
# Fast iteration (parallel, no coverage) - 32 seconds
source ../.env && pytest tests/ --no-cov

# Full validation (with coverage) - 97 seconds
source ../.env && pytest tests/

# Specific test file
source ../.env && pytest tests/unit/test_basic_read.py -v

# Run only marked tests
source ../.env && pytest -m "not slow"
```

**Test organization**:
```
tests/
├── unit/                      # Unit tests (fast)
│   ├── test_basic_read.py            # Basic ORM queries (26 tests)
│   ├── test_crud_operations.py       # Create/update/delete (tests)
│   ├── test_new_models.py            # New models coverage (51 tests)
│   └── test_query_functions.py       # Query functions (41 tests)
├── integration/               # Integration tests
│   ├── test_schema_validation.py     # Schema drift detection (18 tests)
│   └── test_views.py                 # Database views (24 tests)
└── api/                       # API endpoint tests (14 files)
    ├── test_users.py
    ├── test_projects.py
    └── ...
```

**Current status**: **380 tests passed, 16 skipped, 77.47% coverage**

**Key test files to review**:
- `test_basic_read.py` - Simple ORM query examples
- `test_schema_validation.py` - How we prevent schema drift
- `test_query_functions.py` - Testing query helper functions
- `tests/api/` - API endpoint testing patterns

**Parallel execution** (pytest-xdist):
- Uses all CPU cores by default (`-n auto`)
- Each worker gets unique database (isolation)
- 3x faster than serial execution
- Automatically enabled in project

---

## CLI & Terminal Technologies

### 8. Click

**What we use**: Click 8.3.1 for command-line interfaces

**Learning Resources**:
- [Click Documentation](https://click.palletsprojects.com/) - Official docs
- [Click Tutorial](https://click.palletsprojects.com/en/stable/quickstart/) - Quick intro
- [Complex Applications](https://click.palletsprojects.com/en/stable/complex/) - Advanced patterns

**What to focus on**:
- **Commands and groups**: Organizing CLI tools
- **Options and arguments**: `@click.option`, `@click.argument`
- **Context passing**: Sharing data between commands
- **Help text**: Making user-friendly CLIs

**Project-specific**:
- **Main CLI**: `src/sam_search.py` - comprehensive search tool
- **Usage**: `sam-search user benkirk --list-projects`
- **Entry points**: Defined in `pyproject.toml` (sam-search, sam-status)

```bash
# Find user
sam-search user benkirk --list-projects --verbose

# Pattern search
sam-search user --search "ben%"

# Find expiring projects
sam-search project --upcoming-expirations --list-users

# Project lookup
sam-search project SCSG0001 --list-users --verbose
```

---

### 9. Rich

**What we use**: Rich 14.2.0 for beautiful terminal output

**Learning Resources**:
- [Rich Documentation](https://rich.readthedocs.io/) - Examples and features
- [Rich GitHub](https://github.com/Textualize/rich) - Extensive examples and gallery
- [Rich Tables](https://rich.readthedocs.io/en/stable/tables.html) - Table formatting

**What to focus on**:
- **Tables**: Formatted data display
- **Progress bars**: Long-running operations
- **Panels**: Organized information display
- **Colors and styles**: Terminal formatting

**Project-specific**:
- We use Rich tables extensively for user/project data
- Progress bars for batch operations
- See `src/sam_search.py` for implementation examples

---

## Container & Deployment Technologies

### 10. Docker & Docker Compose

**What we use**: Docker 29.1.3 + Docker Compose v2.40.3

**Learning Resources**:
- [Docker Getting Started](https://docs.docker.com/get-started/) - Official tutorial
- [Docker Compose Docs](https://docs.docker.com/compose/) - Multi-container apps
- [Docker for Developers](https://docker-curriculum.com/) - Comprehensive guide
- [Best Practices](https://docs.docker.com/develop/dev-best-practices/) - Production-ready images

**What to focus on**:
- **Dockerfile syntax**: FROM, RUN, COPY, CMD
- **Image layers and caching**: Build optimization
- **Docker Compose services**: Multi-container applications
- **Volume mounts**: Development workflow
- **Networking**: Service communication

**Project-specific**:

```bash
# Start web application (preferred method)
docker compose up

# Access at: http://localhost:5050

# Start in background
docker compose up -d

# View logs
docker compose logs -f webapp

# Rebuild after changes
docker compose up --build

# Stop services
docker compose down
```

**Configuration**: See `compose.yaml`
- **webapp service**: Flask app on port 5050
- **mysql service**: MySQL 9.0 on port 3306
- **Watch mode**: Hot reloading on file changes
- **Health checks**: Automatic service monitoring

---

### 11. Gunicorn

**What we use**: Gunicorn 23.0.0 as production WSGI server

**Learning Resources**:
- [Gunicorn Documentation](https://docs.gunicorn.org/) - Configuration guide
- [Deploying Flask with Gunicorn](https://flask.palletsprojects.com/en/stable/deploying/gunicorn/) - Best practices
- [Gunicorn Design](https://docs.gunicorn.org/en/stable/design.html) - Architecture overview

**What to focus on**:
- **Worker processes and threads**: Concurrency models
- **Configuration options**: Workers, timeout, keepalive
- **Graceful restarts**: Zero-downtime deployment
- **Logging**: Access and error logs

**Project-specific**:
- Production config: `gunicorn_config.py` (to be created)
- Worker count: (2 × CPU cores) + 1
- Used in Dockerfile for production deployment

---

## Frontend Technologies (Light Touch)

### 12. Bootstrap 4

**What we use**: Bootstrap 4.6.2 via CDN

**Learning Resources**:
- [Bootstrap 4 Documentation](https://getbootstrap.com/docs/4.6/) - Component reference
- [Bootstrap Grid System](https://getbootstrap.com/docs/4.6/layout/grid/) - Layout basics
- [Bootstrap Components](https://getbootstrap.com/docs/4.6/components/alerts/) - UI elements

**What to focus on**:
- **Grid system**: Rows, columns, responsive breakpoints
- **Common components**: Cards, buttons, forms, modals, navigation
- **Responsive utilities**: Hiding/showing elements by screen size

**Project-specific**:
- Templates in: `src/webapp/templates/`
- Custom CSS in: `src/webapp/static/css/`
- Theme: Lumen (via Flask-Admin swatch)

---

### 13. Jinja2

**What we use**: Jinja2 3.1.6 for HTML templating

**Learning Resources**:
- [Jinja2 Documentation](https://jinja.palletsprojects.com/) - Template syntax
- [Flask Templating](https://flask.palletsprojects.com/en/stable/templating/) - Flask integration
- [Jinja2 Template Designer Docs](https://jinja.palletsprojects.com/en/stable/templates/) - Full syntax

**What to focus on**:
- **Template inheritance**: `{% extends %}`, `{% block %}`
- **Variables and expressions**: `{{ variable }}`
- **Control structures**: `{% for %}`, `{% if %}`
- **Filters**: `{{ value|filter }}`

**Project-specific**:
- Base template: `src/webapp/templates/base.html`
- Template inheritance used throughout
- Partials pattern for reusable components

---

## Development Tools

### 14. Conda

**What we use**: Conda for environment management

**Learning Resources**:
- [Conda User Guide](https://docs.conda.io/projects/conda/en/latest/user-guide/) - Complete reference
- [Conda Cheat Sheet](https://docs.conda.io/projects/conda/en/latest/user-guide/cheatsheet.html) - Quick commands
- [Managing Environments](https://docs.conda.io/projects/conda/en/latest/user-guide/tasks/manage-environments.html) - Environment workflows

**Setup**:
```bash
# Create environment (one time)
make conda-env

# Or manually
conda env create -f conda-env.yaml
conda activate sam-queries

# Install project in editable mode
pip install -e ".[test]"
```

**Recommended**: Use `source etc/config_env.sh` - activates conda AND loads `.env`

---

### 15. Git & GitHub

**What we use**: Git for version control, GitHub for hosting

**Learning Resources**:
- [Pro Git Book](https://git-scm.com/book/en/v2) - Free comprehensive guide (BEST RESOURCE)
- [GitHub Skills](https://skills.github.com/) - Interactive tutorials
- [Atlassian Git Tutorial](https://www.atlassian.com/git/tutorials) - Beginner-friendly
- [Git Flight Rules](https://github.com/k88hudson/git-flight-rules) - Problem solving

**Project workflow**:
- **Main branch**: `main` (default for PRs)
- **Feature branches**: Create from `main`, descriptive names
- **Commit messages**: Detailed, markdown-formatted (see git history for examples)

**Commit message format** (see examples in git history):
```
Brief summary of change (50 chars or less)

## Summary
- Bullet points of key changes
- What was added/modified/fixed

## Test Results
- Test command output
- Coverage changes

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
```

---

## Specialized Libraries

### 16. Flask-Admin

**What we use**: Flask-Admin 2.0.2 for database admin interface

**Learning Resources**:
- [Flask-Admin Documentation](https://flask-admin.readthedocs.io/) - Complete reference
- [Flask-Admin Quickstart](https://flask-admin.readthedocs.io/en/latest/introduction/#getting-started) - Getting started
- [Custom Views](https://flask-admin.readthedocs.io/en/latest/introduction/#customizing-built-in-views) - Customization

**Project-specific**:
- **Admin interface**: Available at `/admin` (requires authentication)
- **Custom ModelViews**: `src/webapp/admin/` - customized CRUD interfaces
- **Theme**: Bootstrap4 with Lumen swatch
- Includes expiration monitoring dashboards

---

### 17. Flask-Login

**What we use**: Flask-Login 0.6.3 for authentication

**Learning Resources**:
- [Flask-Login Documentation](https://flask-login.readthedocs.io/) - User session management
- [Flask-Login Quickstart](https://flask-login.readthedocs.io/en/latest/#quick-start) - Basic setup

**Project-specific**:
- User loader: `src/webapp/run.py`
- Current user: Access via `current_user` in templates and views
- Login required: `@login_required` decorator

---

## Learning Path Recommendations

### For Complete Beginners (No Python Experience)

**Goal**: Build foundation in Python and web development

**Week 1: Python Basics**
- [ ] Complete [Official Python Tutorial](https://docs.python.org/3/tutorial/) (8-10 hours)
- [ ] Practice with [Python Exercises](https://www.practicepython.org/)
- [ ] Learn basic Git commands

**Week 2: SQL and Databases**
- [ ] [SQL Teaching](https://www.sqlteaching.com/) - Interactive tutorial
- [ ] [MySQL Tutorial](https://dev.mysql.com/doc/mysql-tutorial-excerpt/8.0/en/)
- [ ] Practice writing SELECT queries on SAM database

**Week 3: Flask Web Development**
- [ ] [Flask Quickstart](https://flask.palletsprojects.com/en/stable/quickstart/)
- [ ] Build a simple Flask app (todo list)
- [ ] Learn Jinja2 templates

**Week 4: SQLAlchemy ORM**
- [ ] [SQLAlchemy ORM Tutorial](https://docs.sqlalchemy.org/en/20/tutorial/)
- [ ] Read SAM base classes (`src/sam/base.py`)
- [ ] Review User model (`src/sam/core/user.py`)

**Week 5: Project Exploration**
- [ ] Set up SAM development environment
- [ ] Run tests: `pytest tests/ --no-cov`
- [ ] Start webapp: `docker compose up`
- [ ] Try CLI: `sam-search user benkirk`

**Week 6: First Contribution**
- [ ] Read CLAUDE.md thoroughly
- [ ] Find a simple issue or improvement
- [ ] Make your first pull request

---

### For Experienced Python Developers (New to Project)

**Goal**: Understand SAM architecture and start contributing quickly

**Day 1: Project Overview**
- [ ] Read this guide completely
- [ ] Read [CLAUDE.md](../CLAUDE.md) - comprehensive project memory
- [ ] Review database schema (97 tables)
- [ ] Understand three-tier schema strategy

**Day 2: ORM Models Deep Dive**
- [ ] Review SQLAlchemy 2.0 changes (if coming from 1.x)
- [ ] Explore models: `src/sam/core/`, `src/sam/accounting/`, `src/sam/resources/`
- [ ] Understand key models: User, Project, Account, Allocation
- [ ] Study hybrid properties (`Allocation.is_active`)
- [ ] Review class methods (`User.get_by_username()`)

**Day 3: API and Schemas**
- [ ] Review API endpoints: `src/webapp/api/v1/`
- [ ] Understand Marshmallow schemas: `src/sam/schemas/`
- [ ] Study three-tier schema pattern (Full/List/Summary)
- [ ] Analyze `AllocationWithUsageSchema` (most complex)

**Day 4: Testing Infrastructure**
- [ ] Run full test suite: `pytest tests/`
- [ ] Review test organization and patterns
- [ ] Understand pytest-xdist parallel execution
- [ ] Study schema validation tests (schema drift prevention)

**Day 5: Hands-On Development**
- [ ] Set up development environment
- [ ] Make a small code change
- [ ] Write tests for your change
- [ ] Run: `pytest tests/ --no-cov` to verify
- [ ] Create pull request

---

### For Database/DevOps Focus

**Goal**: Understand infrastructure, deployment, and operations

**Day 1: Database Architecture**
- [ ] Explore database schema (97 tables)
- [ ] Understand relationships (foreign keys)
- [ ] Review key tables: users, project, account, allocation
- [ ] Study charge summary tables (performance optimization)
- [ ] Learn about XRAS integration views

**Day 2: Docker and Containerization**
- [ ] Review `compose.yaml` configuration
- [ ] Understand service dependencies (webapp ↔ mysql)
- [ ] Study Dockerfile (`containers/webapp/Dockerfile`)
- [ ] Learn about volume mounts and hot reloading
- [ ] Explore health checks and startup dependencies

**Day 3: Testing Infrastructure**
- [ ] Understand pytest-xdist parallel execution
- [ ] Learn about worker isolation (unique databases per worker)
- [ ] Review coverage configuration and goals
- [ ] Study CI/CD workflows (`.github/workflows/`)

**Day 4: Production Considerations**
- [ ] Read [docs/plans/PRODUCTION_IMPROVEMENTS.md](plans/PRODUCTION_IMPROVEMENTS.md)
- [ ] Understand Gunicorn configuration
- [ ] Review database connection pooling
- [ ] Study logging and monitoring requirements
- [ ] Learn about health check endpoints

**Day 5: Deployment Planning**
- [ ] Review environment variable management
- [ ] Understand configuration separation (dev/prod/test)
- [ ] Study SSL/TLS requirements
- [ ] Plan monitoring and alerting strategy

---

## Quick Reference: Development Workflow

### One-Time Setup
```bash
# Clone repository
git clone <repository-url>
cd sam-queries

# Set up environment (activates conda + loads .env)
source etc/config_env.sh

# Install dependencies
make conda-env
# Or manually:
# conda env create -f conda-env.yaml
# conda activate sam-queries
# pip install -e ".[test]"
```

### Daily Workflow
```bash
# 1. Activate environment and load variables
source etc/config_env.sh

# 2. Run web application
docker compose up
# Access at: http://localhost:5050
# Login with: benkirk (any password in dev mode)

# 3. Run tests (fast iteration)
source ../.env && pytest tests/ --no-cov  # 32 seconds

# 4. Run tests (with coverage)
source ../.env && pytest tests/  # 97 seconds

# 5. Use CLI tools
sam-search user benkirk --list-projects
sam-search project SCSG0001 --verbose
sam-search project --upcoming-expirations

# 6. Direct database access
mysql -u root -h 127.0.0.1 -proot sam
```

### Common Commands
```bash
# Run specific test file
pytest tests/unit/test_basic_read.py -v

# Run tests matching pattern
pytest -k "test_user" -v

# Run with specific markers
pytest -m "not slow"

# Check code coverage for specific module
pytest tests/ --cov=sam.schemas --cov-report=term-missing

# View dependency tree
pipdeptree
```

---

## Important Project Documentation

**READ THESE FIRST**:

1. **[CLAUDE.md](../CLAUDE.md)** ⭐ - Comprehensive project memory
   - Database schema details
   - ORM patterns and conventions
   - API endpoints and schemas
   - Common queries and patterns
   - Known issues and gotchas
   - Testing strategies

2. **[README.md](../README.md)** - Project overview and setup
   - Quick start instructions
   - Basic usage examples

3. **[src/webapp/README.md](../src/webapp/README.md)** - Web application docs
   - API endpoint documentation
   - Authentication and authorization
   - Development server setup

4. **[docs/plans/PRODUCTION_IMPROVEMENTS.md](plans/PRODUCTION_IMPROVEMENTS.md)** - Production readiness
   - Security improvements
   - Operational enhancements
   - Deployment checklist

5. **[pyproject.toml](../pyproject.toml)** - Project metadata
   - Dependencies and versions
   - Entry points (CLI tools)
   - Project configuration

6. **[compose.yaml](../compose.yaml)** - Docker services
   - Service definitions
   - Environment variables
   - Volume mounts

---

## Getting Help

### Documentation Resources
1. **Project docs**: Check CLAUDE.md first (most comprehensive)
2. **This guide**: Technology references and learning paths
3. **Official docs**: Technology-specific documentation (links above)
4. **Code examples**: Existing tests show patterns

### Debugging Tips
1. **Read error messages carefully** - Python tracebacks are helpful
2. **Check logs**: `docker compose logs -f webapp`
3. **Use debugger**: `import pdb; pdb.set_trace()`
4. **Run single test**: Isolate the problem
5. **Check CLAUDE.md**: Known issues and gotchas section

### When Stuck
1. Search project documentation (CLAUDE.md, READMEs)
2. Look at similar code in the project
3. Review test files for usage examples
4. Check official documentation for the technology
5. Ask experienced team members

---

## Contributing Guidelines

### Before You Start
1. **Read CLAUDE.md** - Project patterns and conventions
2. **Understand the architecture** - ORM models, API structure
3. **Review existing code** - Follow established patterns
4. **Check for similar implementations** - Don't reinvent the wheel

### Development Process
1. **Create feature branch** from `main`
2. **Write tests first** (TDD encouraged)
3. **Implement feature** following project patterns
4. **Run tests**: `pytest tests/ --no-cov` (fast iteration)
5. **Run full test suite**: `pytest tests/` (with coverage)
6. **Check schema validation** if ORM models changed
7. **Write detailed commit message** (see git history for format)
8. **Create pull request** to `main`

### Code Quality
- **Tests required**: For any new functionality
- **Coverage target**: 70% minimum (project currently at 77.47%)
- **Follow patterns**: Use existing code as examples
- **Type hints**: Add where helpful
- **Docstrings**: Clear, concise, with examples when useful
- **Comments**: Explain "why" not "what"

### Commit Message Format
See git history for examples. Format:
```markdown
Brief summary (50 chars or less)

## Summary
- Key changes in bullet points
- What was added/modified/fixed

## Test Results (if applicable)
- Tests passed: 380
- Coverage: 77.47%

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
```

---

## Common Gotchas (READ THIS!)

### Critical Don'ts ❌

**DON'T**:
- ❌ Use `datetime.now(UTC)` - database uses **naive datetimes** (no timezone)
- ❌ Assume single-column primary keys - **always check database first**
- ❌ Use `user.email` - use `user.primary_email` instead (no `email` attribute)
- ❌ Use `allocation.active` - use `allocation.is_active` (it's a hybrid property)
- ❌ Pass `session` to `project.get_detailed_allocation_usage()` - uses SessionMixin
- ❌ Modify database schema - **ORM follows database** (database is source of truth)
- ❌ Skip schema validation tests after model changes
- ❌ Use raw SQL strings without `text()` wrapper
- ❌ Batch todo completions - mark complete immediately
- ❌ Create files unnecessarily - prefer editing existing files
- ❌ Forget to unpack tuples from query functions (see below)

### Best Practices ✅

**DO**:
- ✅ Use `allocation.is_active` - hybrid property (works in Python AND SQL)
- ✅ Use three-tier schemas for API performance (Full/List/Summary)
- ✅ Run fast tests during development (`--no-cov`, 32s vs 97s)
- ✅ Use bidirectional relationships with `back_populates`
- ✅ Check actual database schema when in doubt
- ✅ Write tests for query functions (see `test_query_functions.py`)
- ✅ Use proper exit codes in CLI tools (0, 1, 2, 130)
- ✅ Use schema validation tests before committing model changes
- ✅ Unpack query result tuples properly:

```python
# CORRECT - Unpack tuple
for project, allocation, resource_name, days_remaining in expiring_projects:
    print(f"{project.projcode}: {resource_name} expires in {days_remaining} days")

# WRONG - Don't use tuple directly
for result in expiring_projects:
    print(result)  # This is a tuple, not a project!
```

### Database Specifics
- **Datetime**: Database uses naive datetimes (no timezone), use `datetime.now()` not `datetime.now(UTC)`
- **Primary Keys**: Check database - some tables have composite PKs (e.g., `dav_activity`)
- **Views**: Read-only - never attempt INSERT/UPDATE/DELETE on views
- **Encoding**: Database uses UTF-8
- **Password hashing**: Uses bcrypt (~60 chars), not SHA-256

### ORM Patterns
- **Hybrid properties**: `@hybrid_property` works in both Python and SQL expressions
- **Class methods**: Preferred for queries (`User.get_by_username(session, username)`)
- **Session**: Models use SessionMixin for `self.session` access
- **Relationships**: Always use `back_populates` for bidirectional relationships
- **Properties**: Use `@property` for derived values (`user.primary_email`, `project.active`)

### Testing Tips
- **Fast iteration**: Use `--no-cov` flag (32s vs 97s)
- **Parallel execution**: Automatic via pytest-xdist, workers get isolated databases
- **Schema validation**: Run after any ORM model changes
- **Coverage target**: 70% minimum (currently at 77.47%)

---

## Next Steps

### Immediate Actions (Day 1)
1. ✅ **Set up environment**: `source etc/config_env.sh`
2. ✅ **Read CLAUDE.md**: Comprehensive project memory (1-2 hours)
3. ✅ **Run tests**: `source ../.env && pytest tests/ --no-cov` (verify setup)
4. ✅ **Start webapp**: `docker compose up` (explore interface)
5. ✅ **Try CLI**: `sam-search user benkirk --list-projects` (see CLI in action)

### First Week
6. ✅ **Pick a learning path** from recommendations above based on experience level
7. ✅ **Explore ORM models**: Read core models (User, Project, Account, Allocation)
8. ✅ **Review API endpoints**: Understand RESTful structure
9. ✅ **Study schemas**: Learn three-tier pattern (Full/List/Summary)
10. ✅ **Make a small change**: Fix a typo, improve a docstring, add a test

### Continuous Learning
- **Read code daily**: Best way to learn project patterns
- **Run tests frequently**: Understand what's being tested
- **Ask questions**: No question is too basic
- **Document learnings**: Update docs when you find gaps
- **Pair program**: Learn from experienced team members

---

## Technology Stack Summary

| Category | Technology | Version | Purpose |
|----------|-----------|---------|---------|
| **Language** | Python | 3.13+ | Backend code |
| **ORM** | SQLAlchemy | 2.0.45 | Database access |
| **Web Framework** | Flask | 3.1.2 | Web application |
| **Serialization** | Marshmallow | 4.1.1 | API schemas |
| **Database** | MySQL | 9.0 | Data storage |
| **Driver** | PyMySQL | 1.1.2 | DB connection |
| **Testing** | pytest | 9.0.2 | Test framework |
| **CLI** | Click | 8.3.1 | Command-line tools |
| **Terminal UI** | Rich | 14.2.0 | Beautiful output |
| **Containers** | Docker | 29.1.3 | Deployment |
| **Orchestration** | Docker Compose | 2.40.3 | Multi-container |
| **WSGI Server** | Gunicorn | 23.0.0 | Production server |
| **Frontend CSS** | Bootstrap | 4.6.2 | UI framework |
| **Templates** | Jinja2 | 3.1.6 | HTML templating |
| **Environment** | Conda | latest | Env management |
| **Admin UI** | Flask-Admin | 2.0.2 | Database admin |
| **Auth** | Flask-Login | 0.6.3 | Authentication |

---

## Appendix: Useful Links

### Official Documentation
- [Python 3.13](https://docs.python.org/3/)
- [SQLAlchemy 2.0](https://docs.sqlalchemy.org/en/20/)
- [Flask 3.x](https://flask.palletsprojects.com/)
- [Marshmallow](https://marshmallow.readthedocs.io/)
- [MySQL 9.0](https://dev.mysql.com/doc/refman/9.0/en/)
- [pytest](https://docs.pytest.org/)
- [Docker](https://docs.docker.com/)
- [Bootstrap 4](https://getbootstrap.com/docs/4.6/)

### Learning Resources
- [Real Python](https://realpython.com/) - Python tutorials
- [SQLAlchemy Tutorial](https://docs.sqlalchemy.org/en/20/tutorial/)
- [Flask Mega-Tutorial](https://blog.miguelgrinberg.com/post/the-flask-mega-tutorial-part-i-hello-world)
- [Pro Git Book](https://git-scm.com/book/en/v2)

### Tools
- [MySQL Workbench](https://dev.mysql.com/doc/workbench/en/) - Database GUI
- [Postman](https://www.postman.com/) - API testing
- [DB Browser for SQLite](https://sqlitebrowser.org/) - SQLite viewer

---

**Welcome to the SAM team!** 🚀

This guide is a living document. If you find gaps or have suggestions, please contribute improvements!

---

**Document Version**: 1.0
**Last Updated**: 2025-12-23
**Maintained By**: Development Team
