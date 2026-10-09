# SAM CLI Architecture

## Overview

The SAM CLI is a modular, class-based Click application providing two
entry points (declared in `pyproject.toml [project.scripts]`):

- **`sam-search`** — user-facing search and query tool: `user`, `project`,
  `allocations`, `accounting`, `contracts`, `awards`
- **`sam-admin`** — administrative superset: `user`, `project`, `accounting`
  (charge ingest, quota reconcile), `contracts`, `cache`, `xras`, `tasks`,
  `last-seen`, `rbac`; admin commands extend the search command classes via
  inheritance

This architecture is deliberately mirrored by the `jobhist` CLI in the
peer **hpc-usage-queries** repo (same `Context`/`BaseCommand` shape, exit
codes and JSON envelope) — if you change any of those contracts here, update
both repos in lockstep. jobhist also has an `ExporterRegistry` for file
exports; SAM has only `rich` and `json` on stdout (`core/output.py`). See
`hpc-usage-queries/devel/job_history/README.md` § *CLI Architecture* for
the canonical recipe.

## Directory Structure

```
cli/
├── core/                     # Shared infrastructure
│   ├── context.py            # Context class (session, consoles, flags, output_format)
│   ├── base.py               # BaseCommand: emit(), not_found(), require_plugin()
│   ├── options.py            # verbose_option, provisioning_option, usage_error
│   ├── display_utils.py      # Shared cell formatters, stamp, styled, progress, issues_table
│   ├── output.py             # output_json() + _SAMEncoder
│   └── utils.py              # Exit codes, configure_logging, parse_duration_days
├── user/                     # User commands
│   ├── builders.py           # ORM → dict extractors (no Rich)
│   ├── commands.py           # UserSearchCommand, UserAdminCommand, ...
│   └── display.py            # display_user(), ... — dict input only
├── project/                  # Project commands (same builders/commands/display split)
├── allocations/              # Allocation commands
├── accounting/               # Charge rollups, per-job queries, summary ingest
│   ├── commands.py           # AccountingAdminCommand dispatcher, Search, Jobs
│   ├── comp_ingest.py        # --comp            (CompIngestMixin)
│   ├── disk_ingest.py        # --disk            (DiskIngestMixin)
│   ├── quota_reconcile.py    # --reconcile-quotas (QuotaReconcileMixin)
│   └── dates.py, quota_readers/, disk_usage/, path_verifier.py
├── contracts/                # Contract search (sam-search) + data-hygiene audit (sam-admin)
├── awards/                   # Public award APIs (NSF, USAspending) — sam-search
├── xras/                     # sam-admin xras: action log, reports, recheck
├── tasks/                    # sam-admin tasks: the scheduled-task dispatcher
├── security/                 # sam-admin rbac: the samuel_role_* catalog
├── last_seen/                # sam-admin last-seen: the system_status sightings ledger
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

A command writes through `BaseCommand.emit(payload, display_fn)`, which picks
the format, and reports a missing entity through `not_found(kind, message, **ids)`.

JSON payloads:
- Indented, written to `sys.stdout` only: messages, usage errors, progress
  and tracebacks go to stderr (`ctx.message_console` is stderr in JSON mode),
  so stdout is always exactly one JSON document
- Always "complete" — sub-builders fire regardless of `-v`/`-vv` so a
  consumer doesn't need to ask for verbosity
- Top-level `kind` field names the envelope (e.g. `"user"`,
  `"project"`, `"allocation_summary"`, `"expiring_projects"`)
- `datetime`/`date` → ISO 8601 string, `Decimal` → float, `set` → sorted list
- Not-found path emits `{"kind": "...", "error": "not_found", "<id>": "..."}`,
  exit 1
- The `user` envelope's `last_seen` is a list from the `system_status` ledger, `null`
  when that database is unreachable, and absent on a host with no `STATUS_DB_*`
- Combining `--format json` with side-effecting flags (`--notify`,
  `--deactivate`) is rejected with `{"error": "json_unsupported_for_writes"}`,
  exit 2
- **Carve-out: `sam-admin tasks --run-due` / `--run`.** The rule exists to
  stop someone accidentally writing while scripting a *report*; for the task
  dispatcher the side effect **is** the command, and JSON on stdout is exactly
  what a log-scraped CronJob should emit. The guard stays in force everywhere
  else, including `--notify`, where the original hazard is real.
- **Same carve-out: `sam-admin accounting --disk`.** The import is the command;
  its `disk_import` envelope carries the counts and per-category row lists,
  on live and `--dry-run` runs alike. Rich output lists the informational
  categories (known unowned, unlinked directory) only under `--verbose`.

Progress bars (`display_utils.progress(ctx)`, `rich.progress.track`) are
disabled in JSON mode so stdout stays parseable.

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
│   └── UserAdminCommand
├── UserPatternSearchCommand
├── UserAbandonedCommand
├── UserNotSeenCommand
└── UserWithProjectsCommand

# Project commands (project/commands.py)
BaseProjectCommand
├── ProjectSearchCommand
│   └── ProjectAdminCommand
├── ProjectPatternSearchCommand
├── ProjectExpirationCommand
├── ProjectReconcileCommand
└── ProjectTreeAuditCommand

# Contract commands (contracts/commands.py, awards/commands.py)
BaseContractCommand
├── ContractSearchCommand          # SAM's own contract table
├── ContractPatternSearchCommand
├── AwardSearchCommand             # the funding agency's API
└── AwardPatternSearchCommand

BaseAllocationCommand
└── AllocationSearchCommand

# Directly on BaseCommand: ContractsAuditCommand (scope-wide, no single contract),
# AccountingSearchCommand, AccountingJobsCommand,
# AccountingAdminCommand(CompIngestMixin, DiskIngestMixin, QuotaReconcileMixin),
# XrasCommand, TasksCommand, RbacCommand, LastSeenCommand.
```

Some accounting commands (per-job queries) require the optional
`hpc-usage-queries` plugin, gated via `require_plugin(HPC_USAGE_QUERIES)`
in `core/base.py`; daily-rollup queries work without it.

## Exit Codes

`EXIT_SUCCESS=0` / `EXIT_NOT_FOUND=1` / `EXIT_ERROR=2` /
`EXIT_KEYBOARD_INTERRUPT=130` — shared verbatim with the `jobhist` CLI.

- **Usage errors** (a bad flag combination, a missing argument) exit 2 with the
  message on stderr, through `core/options.py`'s `usage_error`.
- **Lookups** use the codes literally: 1 is not found, which includes a search
  or summary with no rows (`allocations`, `accounting`), and 2 is an error.
  `sam-search awards` is the sharpest case: 1 means "the agency has no such
  award", 2 means "the agency could not be reached". Conflating them would
  report an outage as a missing record.
- **Audits** exit non-zero when findings exist, so CI can gate on them:
  `sam-admin contracts --validate` and `project --audit-trees` use 2;
  the `sam-admin xras --validate-mapping/-opportunities/-vocabulary` checks
  use 1.

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

3. **Wire up in the entry point** (`cmds/search.py` or `cmds/admin.py`);
   reuse `verbose_option` / `provisioning_option`, and reject bad flag
   combinations with `usage_error`:
   ```python
   @cli.command()
   @click.option('--flag', is_flag=True)
   @verbose_option()
   @pass_context
   def new_command(ctx: Context, flag, verbose):
       sys.exit(NewUserCommand(ctx).execute(flag=flag))
   ```

4. **Write tests** following `tests/unit/cli/test_sam_search_cli.py`
   (CliRunner-based) and the subprocess smoke tests in
   `tests/integration/`.

## Testing

CLI coverage lives in `tests/unit/cli/test_sam_search_cli.py`,
`tests/unit/cli/test_cli_json_builders.py`, and the entry-point smoke tests
under `tests/integration/`. See `docs/TESTING.md` for how to run the
suite (isolated `mysql-test` container, xdist parallelism).
