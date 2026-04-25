**Context**: `sam-queries` is a Python CLI project (`src/cli/`) built on Click + SQLAlchemy + Rich. There are two entry point groups — `sam-search` (`src/cli/cmds/search.py`) and `sam-admin` (`src/cli/cmds/admin.py`) — each a `@click.group()` that creates a `Context` object and delegates to command classes.

**Current architecture** (three domains, consistent pattern):
- `cli/core/context.py` — `Context` dataclass: `session`, `console`, `stderr_console`, `verbose`, `very_verbose`, `inactive_projects`, `inactive_users`
- `cli/core/base.py` — `BaseCommand(ABC)`, `BaseUserCommand`, `BaseProjectCommand`, `BaseAllocationCommand`
- Per domain: `commands.py` (command classes with `execute()`) + `display.py` (Rich formatting functions)
- `cli/user/display.py` — `display_user(ctx, user: User, ...)` takes raw ORM objects, traverses relationships inline, builds Rich Tables/Panels
- `cli/project/display.py` — `display_project(ctx, project: Project, ...)` takes ORM Project; internally calls `project.get_detailed_allocation_usage()` (already returns a dict), `get_project_rolling_usage()` (expensive, verbose-only), and recursively builds a project hierarchy tree (verbose-only)
- `cli/allocations/display.py` — `display_allocation_summary(ctx, results: List[Dict], ...)` **already takes plain dicts** — this is the target pattern for everything else
- `cli/accounting/display.py` — also dict-based already

**The task**: Write `docs/plans/CLI_JSON.md` — a comprehensive implementation plan for adding first-class `--format json` output to `sam-search` and `sam-admin`. The chosen design is **Option C with lazy sub-builders**: a `builders.py` file per domain extracts data from ORM objects into plain dicts; display functions are refactored to take those dicts instead of ORM objects; `execute()` routes to either `output_json(data)` or `display_*(ctx, data)`.

**Design decisions already settled** — the plan must reflect all of these:

1. **Three-layer model per domain**: `builders.py` (data extraction, no Rich), `display.py` (Rich formatting, no ORM), `commands.py` (orchestration, calls builders then routes).

2. **New file `cli/core/output.py`**: Contains `output_json(data)` which writes to `sys.stdout` directly (not `ctx.console`) using a custom `json.JSONEncoder` that handles `datetime`/`date` → ISO string, `Decimal` → float. Output is indented (2 spaces). This bypasses Rich entirely so piped consumers get clean JSON.

3. **`Context` gains `output_format: str = 'rich'`**. Both CLI group callbacks get `@click.option('--format', 'output_format', type=click.Choice(['rich', 'json']), default='rich')` and set `ctx.output_format = output_format`.

4. **Lazy sub-builders**: builders are split into core (always fast, always called) and optional sub-builders (only called when needed). The trigger condition for each sub-builder is `(ctx.output_format == 'json') OR (verbosity_condition)`. Example:
   - `build_user_core(user) -> dict` — always called
   - `build_user_detail(user) -> dict` — called when `json OR ctx.verbose`
   - `build_user_projects(user, inactive) -> list[dict]` — called when `json OR list_projects`
   - `build_project_rolling(session, projcode) -> dict` — called when `json OR ctx.verbose`
   - `build_project_tree(project) -> dict` — called when `json OR ctx.verbose`

5. **JSON is always "complete"**: `--format json` always triggers all sub-builders so the JSON payload includes everything regardless of `--verbose`. Rich output continues to gate on verbosity flags as before.

6. **Progress bars**: Commands using `rich.progress.track()` (e.g. `UserAbandonedCommand`, `UserWithProjectsCommand`) must pass `disable=(ctx.output_format == 'json')` so progress bars don't corrupt stdout JSON.

7. **`display_allocation_summary` needs no refactoring** — it already takes `List[Dict]`. `AllocationSearchCommand.execute()` just needs the JSON routing branch added.

8. **Accounting is also already dict-based** — same treatment as allocations: add routing branch, no display refactor needed.

9. **Migration order** (least to most complex): allocations → accounting → user → project. Each domain should be fully working (builders + display refactor + command wiring + tests) before starting the next.

10. **Tests**: builder functions are independently testable with just an ORM object. The plan should call for unit tests of each `build_*` function (assert dict keys/values) and CLI integration tests using Click's `CliRunner` with `--format json` asserting valid JSON output with expected top-level keys.

**Exact files that change or are created**:
- `src/cli/core/context.py` — add `output_format`
- `src/cli/core/output.py` — new: `output_json()`, `_SAMEncoder`
- `src/cli/cmds/search.py` — add `--format` option to group
- `src/cli/cmds/admin.py` — add `--format` option to group
- `src/cli/allocations/commands.py` — add JSON routing branch (no builder/display changes)
- `src/cli/accounting/commands.py` — add JSON routing branch (no builder/display changes)
- `src/cli/user/builders.py` — new: `build_user_core`, `build_user_detail`, `build_user_projects`, `build_user_search_results`, `build_abandoned_users`
- `src/cli/user/display.py` — refactor all functions to accept dicts instead of ORM objects
- `src/cli/user/commands.py` — wire builders + JSON routing in each `execute()`
- `src/cli/project/builders.py` — new: `build_project_core`, `build_project_detail`, `build_project_allocations`, `build_project_rolling`, `build_project_tree`, `build_project_users`, `build_expiring_projects`
- `src/cli/project/display.py` — refactor all functions to accept dicts
- `src/cli/project/commands.py` — wire builders + JSON routing
- `tests/unit/test_cli_json_builders.py` — new unit tests for all builder functions
- `tests/integration/test_cli_json_output.py` — new CliRunner integration tests

**Format for the plan**: Follow the style of existing plans in `docs/plans/` (e.g. `FORMAT_DISPLAY.md`). Include: Overview, Current State (what's already dict-based), Target Architecture diagram (before/after data flow), Design Decisions section, then one section per phase (Infrastructure → Allocations/Accounting → User → Project), each phase listing exact file changes with concrete before/after code snippets. End with a status checklist of all deliverables.
