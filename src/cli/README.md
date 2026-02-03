# SAM CLI Architecture

## Overview

The SAM CLI has been refactored into a modular, class-based architecture that supports both the existing `sam-search` command and a new `sam-admin` command for administrative functions.

## Directory Structure

```
cli/
├── core/                     # Shared infrastructure
│   ├── __init__.py
│   ├── context.py            # Context class (session, console, flags)
│   ├── base.py               # Base command classes
│   └── utils.py              # Exit codes, utilities
├── user/                     # User commands
│   ├── __init__.py
│   ├── commands.py           # UserSearchCommand, UserAdminCommand
│   └── display.py            # display_user(), display_user_projects()
├── project/                  # Project commands
│   ├── __init__.py
│   ├── commands.py           # ProjectSearchCommand, ProjectAdminCommand
│   └── display.py            # display_project(), display_project_users()
├── allocations/              # Allocation commands
│   ├── __init__.py
│   ├── commands.py           # AllocationSearchCommand
│   └── display.py            # display_allocation_summary()
└── cmds/                     # Entry points
    ├── __init__.py
    ├── search.py             # sam-search entry point
    └── admin.py              # sam-admin entry point
```

## Design Principles

1. **Command Classes**: Encapsulate business logic, reusable via inheritance
2. **Display Functions**: Module-level functions (no state needed)
3. **Entry Points**: Minimal CLI wiring, delegate to command classes
4. **Single Context**: Shared Context class for session and configuration
5. **Zero Breaking Changes**: `sam-search` behavior identical to original

## Class Hierarchy

```python
# Base classes (core/base.py)
BaseCommand(ABC)
├── BaseUserCommand
├── BaseProjectCommand
└── BaseAllocationCommand

# User commands (user/commands.py)
BaseUserCommand
├── UserSearchCommand
├── UserPatternSearchCommand
├── UserAbandonedCommand
├── UserWithProjectsCommand
└── UserAdminCommand (extends UserSearchCommand)

# Project commands (project/commands.py)
BaseProjectCommand
├── ProjectSearchCommand
├── ProjectPatternSearchCommand
├── ProjectExpirationCommand
└── ProjectAdminCommand (extends ProjectSearchCommand)

# Allocation commands (allocations/commands.py)
BaseAllocationCommand
└── AllocationSearchCommand
```

## Entry Points

### sam-search
User-facing search CLI - all original functionality preserved:
- User searches (exact, pattern, abandoned, with active projects)
- Project searches (exact, pattern, expirations)
- Allocation queries with flexible grouping

### sam-admin
Administrative commands extending search functionality:
- User validation (`sam-admin user <username> --validate`)
- Project validation (`sam-admin project <projcode> --validate`)
- Project reconciliation (`sam-admin project <projcode> --reconcile`)

## Adding New Commands

1. **Create command class** in appropriate domain module:
   ```python
   from cli.core.base import BaseUserCommand

   class NewUserCommand(BaseUserCommand):
       def execute(self, **kwargs) -> int:
           # Implementation
           return EXIT_SUCCESS
   ```

2. **Add display functions** if needed in domain's `display.py`:
   ```python
   def display_new_thing(ctx: Context, thing):
       # Display logic using Rich
       pass
   ```

3. **Wire up in entry point** (`cmds/search.py` or `cmds/admin.py`):
   ```python
   @cli.command()
   @click.option('--flag', is_flag=True)
   @pass_context
   def new_command(ctx: Context, flag):
       command = NewUserCommand(ctx)
       exit_code = command.execute(flag=flag)
       sys.exit(exit_code)
   ```

4. **Write tests** following existing patterns in `tests/unit/test_sam_search_cli.py`

## Key Files

- **core/context.py**: Shared state (session, console, verbose flags)
- **core/base.py**: Abstract base classes for commands
- **core/utils.py**: Exit codes and utilities
- **user/commands.py**: All user-related command classes
- **user/display.py**: User display functions
- **project/commands.py**: All project-related command classes
- **project/display.py**: Project display functions
- **allocations/commands.py**: Allocation query commands
- **allocations/display.py**: Allocation display functions
- **cmds/search.py**: sam-search entry point
- **cmds/admin.py**: sam-admin entry point

## Backward Compatibility

The original `sam_search_cli.py` has been preserved as `sam_search_cli_original.py` for reference. A compatibility shim at `sam_search_cli.py` re-exports the CLI from the new location, ensuring existing imports continue to work.

## Testing

All existing tests pass without modification (except import path update):
- 20 unit tests in `tests/unit/test_sam_search_cli.py`
- Full integration test suite (437 tests total)

Run tests:
```bash
# Fast iteration (no coverage)
source ../.env && pytest tests/unit/test_sam_search_cli.py --no-cov

# Full test suite
source ../.env && pytest tests/ --no-cov
```

## Future Extensions

Easy to add:
- New search commands (add command class, wire up in search.py)
- New admin features (extend admin command classes)
- New domains (add `cli/resources/` following same pattern)
- Output formats (add JSON mode to display functions)

Requires planning:
- Interactive mode (needs event loop, state management)
- Async operations (commands currently synchronous)
- GUI frontend (display layer tied to Rich console)
