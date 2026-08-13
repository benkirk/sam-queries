# SAM CLI Architecture

## Overview

The SAM CLI is a modular, class-based Click application providing two
entry points (declared in `pyproject.toml [project.scripts]`):

- **`sam-search`** — user-facing search and query tool
- **`sam-admin`** — administrative superset (validation, reconciliation,
  charge ingest, cache refresh); admin commands extend the search
  command classes via inheritance

This architecture is deliberately mirrored by the `jobhist` CLI in the
peer **hpc-usage-queries** repo (same `Context`/`BaseCommand` shape, exit
codes, JSON envelope, and `ExporterRegistry` interface) — if you change
any of those contracts here, update both repos in lockstep. See
`hpc-usage-queries/devel/job_history/README.md` § *CLI Architecture* for
the canonical recipe.

## Directory Structure

```
cli/
├── core/                     # Shared infrastructure
│   ├── context.py            # Context class (session, console, flags, output_format)
│   ├── base.py               # Base command classes (+ optional-plugin gating)
│   ├── output.py             # output_json() + _SAMEncoder
│   └── utils.py              # Exit codes, utilities
├── user/                     # User commands
│   ├── builders.py           # ORM → dict extractors (no Rich)
│   ├── commands.py           # UserSearchCommand, UserAdminCommand, ...
│   └── display.py            # display_user(), ... — dict input only
├── project/                  # Project commands (same builders/commands/display split)
├── allocations/              # Allocation commands
├── accounting/               # Charge rollups, per-job queries, summary ingest
├── contracts/                # Contract search (sam-search) + data-hygiene audit (sam-admin)
├── awards/                   # Public award APIs (NSF, USAspending) — sam-search
├── templates/                # Expiration email templates
└── cmds/                     # Entry points
    ├── search.py             # sam-search
    └── admin.py              # sam-admin
```

## Design Principles

1. **Command Classes**: encapsulate business logic, reusable via inheritance
2. **Display Functions**: module-level, take **plain dicts only** — never ORM objects
3. **Builder Functions**: per-domain `builders.py` extracts ORM data into dicts;
   the same dict feeds both Rich `display_*()` and JSON `output_json()`
4. **Entry Points**: minimal CLI wiring, delegate to command classes
5. **Single Context**: shared Context class for session, configuration, and `output_format`

## Output Formats

Both `sam-search` and `sam-admin` accept `--format [rich|json]` (default
`rich`) at the group level:

```bash
sam-search user benkirk                       # Rich panels + tables
sam-search --format json user benkirk | jq    # Parseable JSON envelope
```

JSON payloads:
- Indented, written to `sys.stdout` only (errors stay on stderr)
- Always "complete" — sub-builders fire regardless of `-v`/`-vv` so a
  consumer doesn't need to ask for verbosity
- Top-level `kind` field names the envelope (e.g. `"user"`,
  `"project"`, `"allocation_summary"`, `"expiring_projects"`)
- `datetime`/`date` → ISO 8601 string, `Decimal` → float, `set` → sorted list
- Not-found path emits `{"kind": "...", "error": "not_found", "<id>": "..."}`,
  exit 1
- Combining `--format json` with side-effecting flags (`--notify`,
  `--deactivate`) is rejected with `{"error": "json_unsupported_for_writes"}`,
  exit 2
- **One carve-out: `sam-admin tasks --run-due` / `--run`.** The rule exists to
  stop someone accidentally writing while scripting a *report*; for the task
  dispatcher the side effect **is** the command, and JSON on stdout is exactly
  what a log-scraped CronJob should emit. The guard stays in force everywhere
  else, including `--notify`, where the original hazard is real.

Progress bars (`rich.progress.track`) are auto-disabled in JSON mode so
stdout stays parseable.

## Class Hierarchy

```python
# Base classes (core/base.py)
BaseCommand(ABC)
├── BaseUserCommand
├── BaseProjectCommand
├── BaseContractCommand
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

# Contract commands (contracts/commands.py, awards/commands.py)
BaseContractCommand
├── ContractSearchCommand          # SAM's own contract table
├── ContractPatternSearchCommand
├── AwardSearchCommand             # the funding agency's API
└── AwardPatternSearchCommand
# ContractsAuditCommand extends BaseCommand directly — it is scope-wide and
# has no single contract to resolve.

# Allocation commands follow the same pattern; the accounting commands
# (AccountingSearchCommand, AccountingJobsCommand, AccountingAdminCommand)
# extend BaseCommand directly.
```

Some accounting commands (per-job queries) require the optional
`hpc-usage-queries` plugin, gated via `require_plugin(HPC_USAGE_QUERIES)`
in `core/base.py`; daily-rollup queries work without it.

## Exit Codes

`EXIT_SUCCESS=0` / `EXIT_NOT_FOUND=1` / `EXIT_ERROR=2` /
`EXIT_KEYBOARD_INTERRUPT=130` — shared verbatim with the `jobhist` CLI.

Two conventions coexist deliberately, and `cli/contracts/` holds one of each:

- **Lookups** (every `sam-search` subcommand) use all three codes literally.
  `sam-search awards` is the sharpest case: 1 means "the agency has no such
  award", 2 means "the agency could not be reached". Conflating them would
  report an outage as a missing record.
- **Audits** (`sam-admin contracts --validate`, `ProjectTreeAuditCommand`)
  overload `EXIT_ERROR` to mean "findings exist", so CI can gate on them.

## Adding New Commands

1. **Create a command class** in the appropriate domain module:
   ```python
   from cli.core.base import BaseUserCommand

   class NewUserCommand(BaseUserCommand):
       def execute(self, **kwargs) -> int:
           # Implementation
           return EXIT_SUCCESS
   ```

2. **Add a builder + display function** if needed — the builder returns a
   plain dict (feeds JSON directly); the display function renders that
   dict with Rich:
   ```python
   def display_new_thing(ctx: Context, thing: dict):
       ...
   ```

3. **Wire up in the entry point** (`cmds/search.py` or `cmds/admin.py`):
   ```python
   @cli.command()
   @click.option('--flag', is_flag=True)
   @pass_context
   def new_command(ctx: Context, flag):
       sys.exit(NewUserCommand(ctx).execute(flag=flag))
   ```

4. **Write tests** following `tests/unit/test_sam_search_cli.py`
   (CliRunner-based) and the subprocess smoke tests in
   `tests/integration/`.

## Backward Compatibility

`src/sam_search_cli.py` is a compatibility shim that re-exports the CLI
from `cli.cmds.search`, so historical imports keep working.

## Testing

CLI coverage lives in `tests/unit/test_sam_search_cli.py`,
`tests/unit/test_cli_json_builders.py`, and the entry-point smoke tests
under `tests/integration/`. See `docs/TESTING.md` for how to run the
suite (isolated `mysql-test` container, xdist parallelism).
