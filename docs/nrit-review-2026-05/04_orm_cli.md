# Phase 4 — ORM + CLI (`src/sam/`, `src/cli/`, `src/sam_search_cli.py`)

> Domain core. SQLAlchemy 2.0 ORM, schemas, query helpers, CLI tooling (`sam-search`, `sam-admin`). The "library" most other subsystems depend on.

## Scope

- `src/sam/` — base, core, resources, projects, accounting, activity, summaries, integration, security, operational, schemas, queries, fmt, session, manage
- `src/cli/` — Click-based CLI architecture (core, user, project, allocations, cmds)
- `src/sam_search_cli.py` — legacy/entry shim
- `src/config.py`
- Tests: `tests/unit/test_basic_read.py`, `test_crud_operations.py`, `test_new_models.py`, `test_query_functions.py`, `test_sam_search_cli.py`, `tests/integration/test_schema_validation.py`, `test_views.py`
- `tests/factories/` (Layer-2 builders)

## Method

1. Walk the model package structure. Confirm domain boundaries match the documented split.
2. Check `is_active` discipline — universal hybrid property per CLAUDE.md §5. Grep for raw column comparisons that should use it.
3. Write-path: `update()` instance methods + `create()` classmethods vs. anything still in `sam.manage`.
4. Schemas: marshmallow tier discipline (Full/List/Summary), form schemas under `sam.schemas.forms`.
5. Queries: `sam.queries` API surface, do they belong on the model?
6. CLI: command-class inheritance pattern, error/exit-code consistency, JSON output envelope.
7. Schema-drift tests: how strict, what they actually catch.

## Lenses applied

- Architecture (primary)
- Security (input validation in CLI / form schemas)
- Testing
- Performance (query patterns)

## Findings

*TBD*

### Architecture

*TBD*

### Security

*TBD*

### Testing

*TBD*

### Performance

*TBD*

## Cross-cutting tags raised

*TBD*

## Open questions for Ben

*TBD*
