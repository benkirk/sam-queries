# CLI JSON Output Migration Plan

**Modules**: `src/cli/` (`sam-search` and `sam-admin` entry points)
**Status**: **Complete.** All five phases shipped; `--format json` is wired
end-to-end across `user`, `project`, `allocations`, and `accounting` for both
`sam-search` and `sam-admin`. Read-only commands emit a JSON envelope; write
commands (`--notify`, `--deactivate`) are rejected with a clear error.

---

## Overview

`sam-search` and `sam-admin` currently emit Rich-formatted output only.
Downstream consumers (cron jobs, dashboards, ad-hoc scripts) want clean,
parseable output and are reduced to scraping `rich`-rendered tables. This
plan adds a first-class `--format json` mode at the group level that emits
indented JSON to `stdout` with no Rich markup, no progress bars, and a
"complete" payload (every sub-builder runs regardless of `--verbose`).

The chosen design is **Option C with lazy sub-builders**:

1. **`builders.py`** per domain — pure data-extraction functions that turn
   ORM objects into plain `dict`/`list[dict]` payloads. No Rich, no I/O.
2. **`display.py`** per domain — Rich rendering, refactored to accept the
   same dicts. No ORM access.
3. **`commands.py`** per domain — orchestration: load ORM, call core
   builder, conditionally call sub-builders, then route to either
   `output_json(data)` or `display_*(ctx, data)`.

Sub-builders are gated `(ctx.output_format == 'json') OR
(verbosity_condition)` so Rich users only pay for what they ask for, while
JSON consumers always get the complete payload.

---

## Current State

| Domain | Display takes | Builder needed? | Routing only? |
|---|---|---|---|
| `allocations` | `List[Dict]` already | No | ✅ yes |
| `accounting` | `List[Dict]` already | No | ✅ yes |
| `user` | ORM `User` objects + relationships traversed inline in `display.py` | ✅ yes | no — refactor display |
| `project` | ORM `Project` objects + `get_detailed_allocation_usage()` + `get_project_rolling_usage()` + tree recursion inline | ✅ yes | no — refactor display |

Allocation queries (`sam.queries.allocations.get_allocation_summary*`)
already return dicts. Charge queries (`sam.queries.charges.query_comp_charge_summaries`)
already return dicts. So the cheapest two domains are pure plumbing.

---

## Target Architecture

### Before (user domain example)

```
Click cmd → UserSearchCommand.execute(username, list_projects)
              ├── self.get_user(username)                 # ORM
              └── display_user(ctx, user, list_projects)  # Rich + ORM traversal
                    ├── grid.add_row("Email(s)", ...)     # iterates user.email_addresses
                    ├── if ctx.verbose: iterate user.institutions, user.organizations
                    └── if list_projects: display_user_projects(ctx, user)
                                              └── iterates user.active_projects()
```

### After

```
Click cmd → UserSearchCommand.execute(username, list_projects)
              ├── user = self.get_user(username)                 # ORM
              ├── data = build_user_core(user)                   # always
              ├── if json or verbose:    data['detail']   = build_user_detail(user)
              ├── if json or list_projects: data['projects'] = build_user_projects(user, inactive)
              └── if ctx.output_format == 'json':
                      output_json(data)                          # → stdout
                  else:
                      display_user(ctx, data, list_projects)     # Rich, dict-only
```

The display function never sees an ORM object again. Tests of builders
need only an ORM fixture; tests of display can hand-craft dicts.

---

## Design Decisions

1. **`output_json` writes to `sys.stdout` directly**, bypassing
   `ctx.console`. Rich's `Console` injects soft-wrap, ANSI resets, and
   trailing newlines that corrupt JSON pipes. Using `print(...,
   file=sys.stdout)` keeps output `jq`-clean.

2. **Custom encoder**: `_SAMEncoder(json.JSONEncoder)` handles
   `datetime`/`date` → ISO 8601 string and `Decimal` → `float`. Set
   `set` → `sorted list` for determinism (relevant for
   `UserAbandonedCommand` and `UserWithProjectsCommand` which use sets).
   Falls through to `default()` raising `TypeError` for anything else,
   to surface bugs early.

3. **`Context.output_format: str = 'rich'`** — the only Context change.
   Both group callbacks (`cmds/search.py`, `cmds/admin.py`) get
   `--format` and set `ctx.output_format` before the subcommand runs.

4. **JSON is always "complete"**: `--format json` triggers all
   sub-builders unconditionally. Rich gates them on `--verbose`,
   `--list-projects`, etc. as today. Rationale: a dashboard consuming
   JSON should not need to pass `-vv` to get fields; verbosity is a
   human-display concern.

5. **Progress bars**: `rich.progress.track()` and `rich.progress.Progress`
   write to `ctx.console` (stderr-routable, but currently goes to stdout).
   Pass `disable=(self.ctx.output_format == 'json')` on every `track()`
   call. Same for the `Progress` context manager in `AccountingAdminCommand`.

6. **Errors stay on stderr** in both modes. Existing code already uses
   `ctx.stderr_console` for connection failures; keep that. JSON consumers
   should rely on the exit code (0/1/2/130) and ignore stderr.

7. **Empty results**: JSON mode emits a structured envelope even when
   nothing matched (`{"users": [], "count": 0}`), not an empty payload.
   Avoids consumer-side null checks.

8. **Top-level envelope**: every JSON payload is a single object with a
   stable `kind` field naming the response shape (e.g.
   `"kind": "user"`, `"kind": "user_search_results"`,
   `"kind": "allocation_summary"`). Lets consumers dispatch without
   parsing the command line.

---

## Phase 1 — Infrastructure

Lay the foundation: Context flag, output helper, group-level `--format`.
No domain code changes yet.

### `src/cli/core/context.py`

Add one line to `__init__`:

```python
def __init__(self):
    self.session: Optional[Session] = None
    self.verbose: bool = False
    self.very_verbose: bool = False
    self.inactive_projects: bool = False
    self.inactive_users: bool = False
    self.output_format: str = 'rich'        # NEW
    self.console = Console()
    ...
```

### `src/cli/core/output.py` (new file)

```python
"""JSON output helper for CLI commands.

Bypasses Rich entirely so piped consumers (jq, dashboards, cron jobs)
get clean, parseable JSON on stdout.
"""

import json
import sys
from datetime import date, datetime
from decimal import Decimal
from typing import Any


class _SAMEncoder(json.JSONEncoder):
    """Encode types the SAM ORM commonly returns."""

    def default(self, obj: Any):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, set):
            return sorted(obj)
        return super().default(obj)


def output_json(data: Any) -> None:
    """Write `data` as indented JSON to stdout.

    Always ends with a single trailing newline so shell pipelines
    behave (e.g. `sam-search ... --format json | jq .`).
    """
    json.dump(data, sys.stdout, cls=_SAMEncoder, indent=2, sort_keys=False)
    sys.stdout.write('\n')
```

### `src/cli/cmds/search.py`

Add `--format` to the group decorator and propagate to context:

```python
@click.group(context_settings=CONTEXT_SETTINGS)
@click.option('--verbose', '-v', is_flag=True, help='Show detailed information')
@click.option('--inactive-projects', is_flag=True, help='Consider inactive projects')
@click.option('--inactive-users', is_flag=True, help='Consider inactive users')
@click.option('--format', 'output_format',
              type=click.Choice(['rich', 'json']), default='rich',
              help='Output format (default: rich)')
@pass_context
def cli(ctx: Context, verbose: bool, inactive_projects: bool,
        inactive_users: bool, output_format: str):
    """Search and query the SAM database"""
    ...
    ctx.verbose = verbose
    ctx.inactive_projects = inactive_projects
    ctx.inactive_users = inactive_users
    ctx.output_format = output_format       # NEW
    ...
```

### `src/cli/cmds/admin.py`

Same `--format` option on its group decorator and set `ctx.output_format`.

### Verification

After this phase, no behaviour changes — `--format rich` is the default
and code paths still go through the existing display functions. Smoke
test: `sam-search --format rich user benkirk` works as before;
`sam-search --format json user benkirk` works as before too (the flag is
plumbed but no command honours it yet).

---

## Phase 2 — Allocations & Accounting (routing only)

These two domains already pass dicts to their display functions, so the
only change is a JSON branch in `execute()`. No builder file, no display
refactor.

### `src/cli/allocations/commands.py`

```python
# AllocationSearchCommand.execute(...)
if not results:
    if self.ctx.output_format == 'json':
        from cli.core.output import output_json
        output_json({'kind': 'allocation_summary', 'count': 0, 'rows': []})
        return EXIT_SUCCESS
    self.console.print("No allocations found matching criteria.", style="yellow")
    return EXIT_SUCCESS

if self.ctx.output_format == 'json':
    from cli.core.output import output_json
    output_json({
        'kind': 'allocation_summary',
        'count': len(results),
        'show_usage': show_usage,
        'rows': results,
    })
    return EXIT_SUCCESS

display_allocation_summary(self.ctx, results, show_usage=show_usage)
return EXIT_SUCCESS
```

### `src/cli/accounting/commands.py`

Mirror in `AccountingSearchCommand.execute()` (line 801):

```python
if not rows:
    if self.ctx.output_format == 'json':
        from cli.core.output import output_json
        output_json({'kind': 'comp_charge_summary', 'count': 0, 'rows': []})
        return 0
    self.console.print("[yellow]No charge records found for the given filters.[/yellow]")
    return 1

if self.ctx.output_format == 'json':
    from cli.core.output import output_json
    output_json({
        'kind': 'comp_charge_summary',
        'start_date': start_date,
        'end_date': end_date,
        'count': len(rows),
        'rows': rows,
    })
    return 0

display_charge_summary_table(self.ctx, rows, start_date, end_date)
return 0
```

`AccountingAdminCommand` (charge-posting and quota-reconcile) is **out of
scope for this plan** — those are write commands whose primary output is
side effects, not query results. Add JSON support there in a follow-up if
demand exists; for now, only the read path ships JSON.

### Verification

```bash
sam-search --format json allocations --resource Derecho | jq '.count, .rows[0]'
sam-search --format json accounting --last 7d --resource Derecho | jq '.rows | length'
```

Both should emit valid JSON; `jq` exits 0.

---

## Phase 3 — User Domain

Refactor `cli/user/display.py` to take dicts; introduce
`cli/user/builders.py`; wire JSON routing in `cli/user/commands.py`.

### `src/cli/user/builders.py` (new file)

```python
"""Data extraction for user CLI output. No Rich, no I/O."""

from typing import Optional
from sam import User


def build_user_core(user: User) -> dict:
    """Always-cheap fields. No relationship traversal beyond
    `email_addresses` (already eager-loaded)."""
    return {
        'kind': 'user',
        'username': user.username,
        'display_name': user.display_name,
        'user_id': user.user_id,
        'upid': user.upid,
        'unix_uid': user.unix_uid,
        'active': user.active,
        'locked': user.locked,
        'is_accessible': user.is_accessible,
        'primary_email': user.primary_email,
        'emails': [
            {'address': e.email_address, 'is_primary': e.is_primary}
            for e in user.email_addresses
        ],
        'active_project_count': len(user.active_projects()),
    }


def build_user_detail(user: User) -> dict:
    """Verbose-only fields: institutions, organizations, academic status."""
    return {
        'academic_status': (
            user.academic_status.description if user.academic_status else None
        ),
        'institutions': [
            {'name': ui.institution.name, 'acronym': ui.institution.acronym}
            for ui in user.institutions if ui.is_currently_active
        ],
        'organizations': [
            {'name': uo.organization.name, 'acronym': uo.organization.acronym}
            for uo in user.organizations if uo.is_currently_active
        ],
    }


def build_user_projects(user: User, inactive: bool) -> list[dict]:
    """List of projects (active or all). Mirrors `display_user_projects`."""
    projects = user.all_projects if inactive else user.active_projects()
    out = []
    for p in projects:
        if p.lead == user:
            role = 'Lead'
        elif p.admin == user:
            role = 'Admin'
        else:
            role = 'Member'
        latest_end = None
        for account in p.accounts:
            for alloc in account.allocations:
                if alloc.end_date and (latest_end is None or alloc.end_date > latest_end):
                    latest_end = alloc.end_date
        out.append({
            'projcode': p.projcode,
            'title': p.title,
            'role': role,
            'active': p.active,
            'latest_allocation_end': latest_end,   # _SAMEncoder handles date
        })
    return out


def build_user_search_results(users: list, pattern: str) -> dict:
    return {
        'kind': 'user_search_results',
        'pattern': pattern,
        'count': len(users),
        'users': [
            {
                'user_id': u.user_id,
                'username': u.username,
                'display_name': u.display_name,
                'primary_email': u.primary_email,
                'is_accessible': u.is_accessible,
            }
            for u in users
        ],
    }


def build_abandoned_users(abandoned: set, total_active: int) -> dict:
    return {
        'kind': 'abandoned_users',
        'total_active_users': total_active,
        'count': len(abandoned),
        'users': [
            {
                'username': u.username,
                'display_name': u.display_name,
                'primary_email': u.primary_email,
            }
            for u in sorted(abandoned, key=lambda x: x.username)
        ],
    }


def build_users_with_projects(users: set, list_projects: bool) -> dict:
    out = {
        'kind': 'users_with_active_projects',
        'count': len(users),
        'users': [],
    }
    for u in sorted(users, key=lambda x: x.username):
        entry = {
            'username': u.username,
            'display_name': u.display_name,
            'primary_email': u.primary_email,
        }
        if list_projects:
            entry['projects'] = build_user_projects(u, inactive=False)
        out['users'].append(entry)
    return out
```

### `src/cli/user/display.py` — refactor to accept dicts

`display_user(ctx, user, list_projects)` becomes `display_user(ctx, data,
list_projects)`. Replace every `user.X` access with `data['X']`. Example:

```python
# Before
grid.add_row("Username", user.username)
grid.add_row("Name", user.display_name)
...
if user.email_addresses:
    emails = []
    for email in user.email_addresses:
        primary_marker = " (PRIMARY)" if email.is_primary else ""
        emails.append(f"<{email.email_address}>{primary_marker}")
    grid.add_row("Email(s)", "\n".join(emails))

# After
grid.add_row("Username", data['username'])
grid.add_row("Name", data['display_name'])
...
if data['emails']:
    emails = []
    for email in data['emails']:
        primary_marker = " (PRIMARY)" if email['is_primary'] else ""
        emails.append(f"<{email['address']}>{primary_marker}")
    grid.add_row("Email(s)", "\n".join(emails))
```

The verbose block reads from `data.get('detail')` (present iff
`build_user_detail` ran):

```python
if ctx.verbose and 'detail' in data:
    detail = data['detail']
    if detail['academic_status']:
        grid.add_row("Academic Status", detail['academic_status'])
    if detail['institutions']:
        grid.add_row(
            "Institution(s)",
            "\n".join(f"{i['name']} ({i['acronym']})" for i in detail['institutions'])
        )
    ...
```

`display_user_projects(ctx, user)` becomes `display_user_projects(ctx,
projects)` taking the list returned by `build_user_projects`. Iterate the
list, format each row from dict keys (`p['projcode']`, `p['title']`,
`p['role']`, etc.). The `latest_allocation_end` is already a `date` (or
`None`) — wrap with `fmt.date_str(...)` per the FORMAT_DISPLAY plan.

### `src/cli/user/commands.py` — wire builders + JSON routing

```python
from cli.core.output import output_json
from cli.user.builders import (
    build_user_core, build_user_detail, build_user_projects,
    build_user_search_results, build_abandoned_users,
    build_users_with_projects,
)


class UserSearchCommand(BaseUserCommand):
    def execute(self, username: str, list_projects: bool = False) -> int:
        try:
            user = self.get_user(username)
            if not user:
                if self.ctx.output_format == 'json':
                    output_json({'kind': 'user', 'error': 'not_found',
                                 'username': username})
                else:
                    self.console.print(f"❌ User not found: {username}", style="bold red")
                return EXIT_NOT_FOUND

            data = build_user_core(user)

            want_detail   = self.ctx.output_format == 'json' or self.ctx.verbose
            want_projects = self.ctx.output_format == 'json' or list_projects

            if want_detail:
                data['detail'] = build_user_detail(user)
            if want_projects:
                data['projects'] = build_user_projects(
                    user, inactive=self.ctx.inactive_projects
                )

            if self.ctx.output_format == 'json':
                output_json(data)
            else:
                display_user(self.ctx, data, list_projects)
            return EXIT_SUCCESS
        except Exception as e:
            return self.handle_exception(e)


class UserAbandonedCommand(BaseUserCommand):
    def execute(self) -> int:
        try:
            active_users = User.get_active_users(self.session)
            abandoned = set()

            for user in track(
                active_users,
                description=" --> determining abandoned users...",
                disable=(self.ctx.output_format == 'json'),  # NEW
            ):
                if len(user.active_projects()) == 0:
                    abandoned.add(user)

            data = build_abandoned_users(abandoned, len(active_users))
            if self.ctx.output_format == 'json':
                output_json(data)
            else:
                display_abandoned_users(self.ctx, data)
            return EXIT_SUCCESS
        except Exception as e:
            return self.handle_exception(e)
```

`UserPatternSearchCommand` and `UserWithProjectsCommand` follow the same
pattern: build the dict, branch on `output_format`. `UserWithProjectsCommand`
also gets `disable=` on its `track()` call.

### Tests

`tests/unit/test_cli_json_builders.py` — for each builder function, fixture
an ORM object (use Layer 1 representative fixtures: `multi_project_user`,
`active_project`) and assert the dict shape:

```python
def test_build_user_core_keys(multi_project_user):
    data = build_user_core(multi_project_user)
    assert data['kind'] == 'user'
    assert set(data.keys()) >= {
        'username', 'display_name', 'user_id', 'unix_uid',
        'active', 'locked', 'primary_email', 'emails', 'active_project_count',
    }
    assert isinstance(data['emails'], list)


def test_build_user_projects_role_assignment(active_project):
    lead = active_project.lead
    projects = build_user_projects(lead, inactive=False)
    assert any(p['projcode'] == active_project.projcode and p['role'] == 'Lead'
               for p in projects)
```

---

## Phase 4 — Project Domain

Same shape as Phase 3, but more sub-builders. The expensive ones
(`build_project_rolling`, `build_project_tree`) stay gated behind
`(json or verbose)`.

### `src/cli/project/builders.py` (new file)

```python
"""Data extraction for project CLI output."""

from sam import Project
from sam.queries.rolling_usage import get_project_rolling_usage


def build_project_core(project: Project) -> dict:
    """Always-cheap fields. No expensive relationship traversal."""
    return {
        'kind': 'project',
        'projcode': project.projcode,
        'title': project.title,
        'unix_gid': project.unix_gid,
        'active': project.active,
        'charging_exempt': project.charging_exempt,
        'allocation_type': project.allocation_type.allocation_type,
        'panel': (project.allocation_type.panel.panel_name
                  if project.allocation_type.panel else None),
        'facility': (
            project.allocation_type.panel.facility.facility_name
            if project.allocation_type.panel and project.allocation_type.panel.facility
            else None
        ),
        'lead': _user_brief(project.lead),
        'admin': _user_brief(project.admin) if project.admin else None,
        'area_of_interest': (project.area_of_interest.area_of_interest
                             if project.area_of_interest else None),
        'organizations': [
            {'name': po.organization.name, 'acronym': po.organization.acronym}
            for po in project.organizations
        ],
        'contracts': [
            {
                'source': pc.contract.contract_source.contract_source,
                'number': pc.contract.contract_number,
                'title': pc.contract.title,
            }
            for pc in project.contracts
        ],
        'active_user_count': project.get_user_count(),
        'active_directories': list(project.active_directories or []),
    }


def _user_brief(u) -> dict:
    if u is None:
        return None
    return {
        'username': u.username,
        'display_name': u.display_name,
        'primary_email': u.primary_email,
    }


def build_project_detail(project: Project) -> dict:
    """Very-verbose fields: IDs, timestamps, abstract."""
    latest_end = None
    for account in project.accounts:
        for alloc in account.allocations:
            if alloc.end_date and (latest_end is None or alloc.end_date > latest_end):
                latest_end = alloc.end_date
    return {
        'project_id': project.project_id,
        'ext_alias': project.ext_alias,
        'creation_time': project.creation_time,
        'modified_time': project.modified_time,
        'membership_change_time': project.membership_change_time,
        'inactivate_time': project.inactivate_time,
        'latest_allocation_end': latest_end,
        'abstract': project.abstract,
        'pi_institutions': [
            {'name': ui.institution.name, 'acronym': ui.institution.acronym}
            for ui in (project.lead.institutions if project.lead else [])
            if ui.is_currently_active
        ],
    }


def build_project_allocations(project: Project) -> dict:
    """Wrap project.get_detailed_allocation_usage(); already returns a dict."""
    return project.get_detailed_allocation_usage()


def build_project_rolling(session, projcode: str) -> dict:
    """30/90-day rolling usage. Expensive — verbose-only in Rich mode."""
    try:
        return get_project_rolling_usage(session, projcode)
    except Exception:
        return {}


def build_project_tree(project: Project) -> dict:
    """Recursive parent/children hierarchy."""
    root = project.get_root() if hasattr(project, 'get_root') else project
    current = project.projcode

    def node(p):
        return {
            'projcode': p.projcode,
            'title': p.title,
            'active': bool(getattr(p, 'active', True)),
            'is_current': p.projcode == current,
            'children': [
                node(c) for c in sorted(p.get_children(), key=lambda x: x.projcode)
            ],
        }
    return node(root)


def build_project_users(project: Project) -> list[dict]:
    out = []
    for u in sorted(project.users, key=lambda x: x.username):
        inaccessible = project.get_user_inaccessible_resources(u)
        out.append({
            'username': u.username,
            'display_name': u.display_name,
            'primary_email': u.primary_email,
            'unix_uid': u.unix_uid,
            'inaccessible_resources': sorted(inaccessible) if inaccessible else [],
        })
    return out


def build_expiring_projects(rows: list, upcoming: bool) -> dict:
    """Wrap (project, allocation, resource_name, days) tuples."""
    return {
        'kind': 'expiring_projects' if upcoming else 'recently_expired_projects',
        'count': len(rows),
        'rows': [
            {
                'projcode': p.projcode,
                'title': p.title,
                'resource': res_name,
                'allocation_end': alloc.end_date,
                'days': days,                      # remaining or since-expiry
            }
            for (p, alloc, res_name, days) in rows
        ],
    }
```

### `src/cli/project/display.py` — refactor

`display_project(ctx, project, ...)` becomes `display_project(ctx, data,
...)`. Replace every `project.X` access with `data['X']`. The current
file already pulls allocations through `project.get_detailed_allocation_usage()`
which returns a dict — that goes into `data['allocations']` via
`build_project_allocations` and the table-rendering loop is unchanged
beyond reading from `data['allocations']` instead of the local `usage`
variable. Same for the rolling-usage block (read from `data.get('rolling',
{})` instead of calling `get_project_rolling_usage()` directly) and the
tree block (walk `data.get('tree')` instead of recursing on ORM nodes).

`display_project_users(ctx, project)` → `display_project_users(ctx, users)`
taking the list from `build_project_users`.

`display_expiring_projects(ctx, expiring_data, ...)` keeps its tuple list
input for now — the verbose path inside it calls `display_project(ctx,
proj, ...)` which would force a full builder run per row. The cleanest
fix is for `ProjectExpirationCommand.execute()` to:

- always call `build_expiring_projects(...)` and route to JSON if needed
- in Rich mode, keep passing the tuple list to `display_expiring_projects`
  but have **that** function call `build_project_core(proj)` +
  `display_project(ctx, data, ...)` for each verbose row

This keeps display dict-only and confines the per-row builder call to one
spot.

### `src/cli/project/commands.py` — wire builders + JSON routing

```python
from cli.core.output import output_json
from cli.project.builders import (
    build_project_core, build_project_detail, build_project_allocations,
    build_project_rolling, build_project_tree, build_project_users,
    build_expiring_projects,
)


class ProjectSearchCommand(BaseProjectCommand):
    def execute(self, projcode: str, list_users: bool = False) -> int:
        try:
            project = self.get_project(projcode)
            if not project:
                if self.ctx.output_format == 'json':
                    output_json({'kind': 'project', 'error': 'not_found',
                                 'projcode': projcode})
                else:
                    self.console.print(f"❌ Project not found: {projcode}", style="bold red")
                return EXIT_NOT_FOUND

            json_mode = self.ctx.output_format == 'json'
            verbose   = self.ctx.verbose
            vv        = self.ctx.very_verbose

            data = build_project_core(project)
            data['allocations'] = build_project_allocations(project)

            if json_mode or verbose or vv:
                data['detail']  = build_project_detail(project)
                data['rolling'] = build_project_rolling(self.session, project.projcode)
                data['tree']    = build_project_tree(project)

            if json_mode or list_users:
                data['users'] = build_project_users(project)

            if json_mode:
                output_json(data)
            else:
                display_project(self.ctx, data, list_users=list_users)
            return EXIT_SUCCESS
        except Exception as e:
            return self.handle_exception(e)


class ProjectExpirationCommand(BaseProjectCommand):
    def execute(self, upcoming: bool = True, ...) -> int:
        try:
            ...
            expiring = get_projects_by_allocation_end_date(...)  # tuple list

            if self.ctx.output_format == 'json':
                output_json(build_expiring_projects(expiring, upcoming=upcoming))
                # Skip the Rich display + notify path entirely in JSON mode;
                # --notify and --deactivate are interactive admin features
                # and remain Rich-only by design.
                return EXIT_SUCCESS

            display_expiring_projects(self.ctx, expiring,
                                      list_users=list_users, upcoming=True)
            ...
```

Note: `--notify` (sends emails) and `--deactivate` (mutates state) are
side-effecting and should not be combinable with `--format json` in this
plan. Either reject the combo at the group callback, or simply ignore
JSON for those subpaths and document the limitation. **Recommended**:
emit `{"kind": "...", "error": "json_unsupported_for_writes"}` and
return `EXIT_ERROR` if the user combines `--format json` with `--notify`
or `--deactivate`.

### Tests

`tests/unit/test_cli_json_builders.py` — extend with:

```python
def test_build_project_core(active_project):
    data = build_project_core(active_project)
    assert data['kind'] == 'project'
    assert data['projcode'] == active_project.projcode
    assert data['lead']['username'] == active_project.lead.username


def test_build_project_tree_marks_current(active_project):
    tree = build_project_tree(active_project)
    # walk tree, assert exactly one node has is_current=True
    found = []
    def walk(n):
        if n['is_current']:
            found.append(n['projcode'])
        for c in n['children']:
            walk(c)
    walk(tree)
    assert found == [active_project.projcode]


def test_build_project_allocations_shape(active_project):
    data = build_project_allocations(active_project)
    assert isinstance(data, dict)
    # Each resource entry has the documented keys
    for resource_name, entry in data.items():
        assert {'allocated', 'used', 'remaining', 'percent_used'} <= entry.keys()
```

---

## Phase 5 — Integration Tests

`tests/integration/test_cli_json_output.py` (new file) — drive both CLIs
via Click's `CliRunner` and assert valid JSON envelopes. Use the
representative fixtures from `tests/conftest.py` (`benkirk` is preserved
in obfuscated snapshots per memory).

```python
import json
from click.testing import CliRunner
from cli.cmds.search import cli as search_cli


def test_user_json_envelope():
    runner = CliRunner()
    result = runner.invoke(search_cli, ['--format', 'json', 'user', 'benkirk'])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data['kind'] == 'user'
    assert data['username'] == 'benkirk'
    assert 'detail' in data           # JSON always emits sub-builders
    assert 'projects' in data


def test_user_not_found_json():
    runner = CliRunner()
    result = runner.invoke(search_cli, ['--format', 'json', 'user', 'no_such_user_xyz'])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data == {'kind': 'user', 'error': 'not_found',
                    'username': 'no_such_user_xyz'}


def test_allocations_json():
    runner = CliRunner()
    result = runner.invoke(search_cli,
                           ['--format', 'json', 'allocations', '--resource', 'Derecho'])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data['kind'] == 'allocation_summary'
    assert isinstance(data['rows'], list)


def test_progress_bar_disabled_in_json(monkeypatch):
    """UserAbandonedCommand uses track(); JSON output must be parseable."""
    runner = CliRunner()
    result = runner.invoke(search_cli, ['--format', 'json', 'user', '--abandoned'])
    assert result.exit_code == 0
    json.loads(result.output)        # would raise if progress bar leaked to stdout
```

End-to-end smoke pipe: `sam-search --format json user benkirk | jq .username`
returns `"benkirk"`.

---

## Migration Order & Status Checklist

Each phase must end with the suite green (`pytest -n auto`) and a working
end-to-end smoke for that domain.

### Phase 1 — Infrastructure
- [x] `src/cli/core/context.py`: add `output_format` field
- [x] `src/cli/core/output.py`: new file with `_SAMEncoder` + `output_json`
- [x] `src/cli/cmds/search.py`: add `--format` option, set on context
- [x] `src/cli/cmds/admin.py`: add `--format` option, set on context
- [x] Smoke: `sam-search --format rich user benkirk` unchanged

### Phase 2 — Allocations & Accounting (routing only)
- [x] `src/cli/allocations/commands.py`: JSON branch in `execute()`
- [x] `src/cli/accounting/commands.py`: JSON branch in `AccountingSearchCommand.execute()`
- [x] Smoke: `sam-search --format json allocations ... | jq` parses cleanly

### Phase 3 — User Domain
- [x] `src/cli/user/builders.py`: 6 builder functions
- [x] `src/cli/user/display.py`: refactored to dict input
- [x] `src/cli/user/commands.py`: builders + JSON routing in 4 commands
- [x] `progress.track(disable=json_mode)` on UserAbandonedCommand, UserWithProjectsCommand
- [x] Smoke: `sam-search --format json user benkirk` returns full envelope

### Phase 4 — Project Domain
- [x] `src/cli/project/builders.py`: 8 builder functions
- [x] `src/cli/project/display.py`: refactored to dict input
- [x] `src/cli/project/commands.py`: builders + JSON routing in 3 commands
- [x] `--format json` + `--notify`/`--deactivate` rejected with clear error
- [x] Smoke: `sam-search --format json project SCSG0001 | jq .allocations`
- [x] Smoke: `sam-search --format json project --upcoming-expirations | jq .count`

### Phase 5 — Tests + Documentation
- [x] `tests/unit/test_cli_json_builders.py`: 30+ unit tests for builders + encoder
- [x] `tests/integration/test_cli_json_output.py`: CliRunner end-to-end tests
- [x] `src/cli/README.md`: documents `--format json` + envelope shapes
- [x] `CLAUDE.md` Quick Reference: lists JSON-mode example commands

---

## Out of Scope (follow-ups)

- **JSON for write commands** (`AccountingAdminCommand` charge posting,
  quota reconcile; `--notify`, `--deactivate`). These are side-effect
  commands; structured output of the *plan* or *result* is valuable but
  needs a separate design (e.g. JSON-Lines progress events vs. a single
  envelope).
- **JSON Schema publishing**: once envelopes stabilise, publish formal
  schemas under `docs/schemas/` so consumers can validate.
- **`--format yaml` / `--format csv`**: easy to add once builders exist
  (drop-in replacement for `output_json`); defer until requested.
- **Streaming output** for very large result sets (e.g. all expirations
  over 5 years): current design buffers the full payload. Acceptable for
  current scale; revisit if a query exceeds ~10MB.
