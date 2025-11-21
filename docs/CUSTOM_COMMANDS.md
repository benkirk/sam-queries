# Custom Claude Code Commands for SAM Queries

This document describes the custom slash commands configured for the SAM Queries project, organized by development area.

**Last Updated**: 2025-11-21

---

## Overview

Custom commands are defined in `.claude/commands/` as markdown files. Each command provides specialized functionality for common development workflows.

### Directory Structure

```
.claude/
├── settings.local.json          # Local permissions (not committed)
├── settings.json                # Project settings (committed)
└── commands/                    # Custom slash commands
    ├── test.md                  # /test - Run test suite
    ├── schema-check.md          # /schema-check - Validate ORM vs DB
    ├── webui-dev.md             # /webui-dev - Start development server
    ├── db-query.md              # /db-query - Execute database queries
    ├── container-up.md          # /container-up - Start dev containers
    └── container-logs.md        # /container-logs - View container logs
```

---

## Development Areas

### 1. Python Development (`python/`)

The Python codebase contains:
- `sam/` - ORM models organized by domain (core, resources, projects, accounting, activity)
- `webui/` - Flask web application with REST API
- `sam_search.py` - CLI tool for user/project searches

#### Commands

| Command | Description | Use Case |
|---------|-------------|----------|
| `/webui-dev` | Start Flask dev server | Local API development |
| `/db-query` | Execute ORM queries | Data exploration |
| `/schema-check` | Validate ORM models | After model changes |

---

### 2. Testing (`tests/`)

The test suite contains:
- `unit/` - ORM model tests, CRUD operations
- `integration/` - Schema validation, CLI tests
- `api/` - REST API endpoint tests

#### Commands

| Command | Description | Use Case |
|---------|-------------|----------|
| `/test` | Run full pytest suite | Before commits |
| `/test-unit` | Run only unit tests | Quick validation |
| `/test-api` | Run API tests | After endpoint changes |
| `/test-schema` | Run schema validation | After ORM changes |

---

### 3. Containers (`containers/`)

Development database container with anonymized SAM data:
- `containers/sam-sql-dev/` - Docker compose setup
- Pre-loaded with anonymized production data
- Used for local development and testing

#### Commands

| Command | Description | Use Case |
|---------|-------------|----------|
| `/container-up` | Start MySQL container | Begin dev session |
| `/container-down` | Stop containers | End dev session |
| `/container-logs` | View container logs | Debug issues |
| `/db-restore` | Restore fresh DB dump | Reset to clean state |

---

## Command Specifications

### `/test` - Run Test Suite

**Purpose**: Execute the full pytest suite with summary reporting.

**Usage**:
```
/test
/test unit           # Run only unit tests
/test api            # Run only API tests
/test -k "schema"    # Run tests matching pattern
```

**What it does**:
1. Changes to `tests/` directory
2. Runs `pytest -v --tb=short`
3. Reports pass/fail counts
4. Shows any failures with context

**Expected Output**:
```
Running pytest suite...
===== 302 passed, 12 skipped in 64s =====
```

---

### `/schema-check` - Validate ORM Models

**Purpose**: Run schema validation tests to detect ORM/database drift.

**Usage**:
```
/schema-check
```

**What it does**:
1. Runs `test_schema_validation.py` tests
2. Compares ORM models to actual database schema
3. Reports any mismatches (missing columns, wrong types, etc.)
4. Shows coverage percentage

**When to use**:
- After adding/modifying ORM models
- After database migrations
- Before committing model changes

---

### `/webui-dev` - Start Development Server

**Purpose**: Launch the Flask development server with auto-login.

**Usage**:
```
/webui-dev
```

**What it does**:
1. Sets environment variables (DISABLE_AUTH, DEV_AUTO_LOGIN_USER)
2. Starts Flask in debug mode
3. Server runs on http://localhost:5000

**Prerequisites**:
- MySQL container running (`/container-up`)
- Conda environment activated

---

### `/db-query` - Execute Database Queries

**Purpose**: Run ad-hoc ORM queries for data exploration.

**Usage**:
```
/db-query "find user benkirk"
/db-query "show project SCSG0001 allocations"
/db-query "count active projects"
```

**What it does**:
1. Parses natural language query
2. Translates to appropriate ORM calls
3. Executes against development database
4. Returns formatted results

**Examples**:
- User lookup with projects
- Project allocation details
- Resource usage summaries

---

### `/container-up` - Start Development Containers

**Purpose**: Start the MySQL development container with anonymized data.

**Usage**:
```
/container-up
```

**What it does**:
1. Changes to `containers/sam-sql-dev/`
2. Runs `docker-compose up -d`
3. Waits for MySQL to be ready
4. Reports connection details

**Connection Info**:
```
Host: 127.0.0.1
Port: 3306
User: root
Password: root
Database: sam
```

---

### `/container-logs` - View Container Logs

**Purpose**: Stream logs from the development containers.

**Usage**:
```
/container-logs
/container-logs --follow    # Stream continuously
/container-logs --tail 50   # Last 50 lines
```

**What it does**:
1. Runs `docker-compose logs` in sam-sql-dev directory
2. Shows MySQL startup, queries, errors
3. Useful for debugging connection issues

---

## Installation

### Step 1: Create Commands Directory

```bash
mkdir -p .claude/commands
```

### Step 2: Create Command Files

Each command is a markdown file with YAML frontmatter:

```markdown
---
description: Brief description shown in /help
---

Your detailed instructions for Claude...
```

### Step 3: Update Settings (Optional)

Add to `.claude/settings.json` for additional configuration:

```json
{
  "permissions": {
    "allow": [
      "Bash(pytest:*)",
      "Bash(docker-compose:*)"
    ]
  }
}
```

---

## Recommended Workflow

### Starting a Development Session

```
1. /container-up          # Start MySQL
2. /test-schema           # Verify ORM matches DB
3. /webui-dev             # Start Flask server
```

### Before Committing Changes

```
1. /test                  # Run full test suite
2. /schema-check          # Verify no ORM drift
3. git add && git commit  # Commit if tests pass
```

### Debugging Issues

```
1. /container-logs        # Check MySQL logs
2. /db-query "..."        # Verify data state
3. /test -k "failing"     # Run specific tests
```

---

## Extending Commands

### Adding a New Command

1. Create `.claude/commands/my-command.md`:

```markdown
---
description: What this command does
---

# My Command

Instructions for Claude on how to execute this command.

## Steps
1. First step
2. Second step

## Example Output
```

2. Test by typing `/my-command` in Claude Code

### Command Best Practices

- **Be specific**: Include exact paths and commands
- **Handle errors**: Describe what to do if something fails
- **Show examples**: Include expected output formats
- **Keep focused**: One command = one purpose

---

## Troubleshooting

### Command Not Found

- Ensure file is in `.claude/commands/` directory
- Check file has `.md` extension
- Verify YAML frontmatter is valid

### Permission Denied

- Add required bash patterns to `settings.local.json`:
  ```json
  {
    "permissions": {
      "allow": ["Bash(your-command:*)"]
    }
  }
  ```

### Container Connection Issues

- Run `/container-logs` to check MySQL status
- Verify port 3306 is not in use
- Try `/container-down` then `/container-up`

---

## Related Documentation

- [CLAUDE.md](/CLAUDE.md) - Project memory and patterns
- [CONTRIBUTING.md](/CONTRIBUTING.md) - Development guidelines
- [containers/sam-sql-dev/README.md](/containers/sam-sql-dev/README.md) - Container setup
