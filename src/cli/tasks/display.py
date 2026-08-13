"""Rich renderers for ``sam-admin tasks``.

Stateless module-level functions taking ``(ctx, payload)`` where ``payload`` is
the plain dict a builder produced — never an ORM object. All formatting goes
through ``sam.fmt`` / ``cli.core.display_utils``; no ``strftime``, no
``'{:,}'.format``.
"""

from rich.panel import Panel
from rich.table import Table

from cli.core.display_utils import BLANK, text

#: Rich styles per run state. Same semantics an operator would read on a
#: dashboard badge, so the terminal and the web teach one vocabulary.
_STATE_STYLE = {
    'running':   'cyan',
    'succeeded': 'green',
    'partial':   'yellow',
    'failed':    'red',
    'skipped':   'dim',
}

#: Outcomes `run_due` reports that are not ledger states.
_OUTCOME_STYLE = {
    'already_claimed': 'dim',
    'would_claim':     'cyan',
    'nothing_due':     'dim',
}


def _state(value) -> str:
    style = _STATE_STYLE.get(value, _OUTCOME_STYLE.get(value, 'white'))
    return f'[{style}]{text(value)}[/{style}]'


def _dt(value):
    """Builders emit ISO strings; `sam.fmt` wants datetimes."""
    from datetime import datetime
    if not value:
        return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def _stamp(value) -> str:
    """A run is identified by its time of day, not just its date."""
    from sam import fmt
    return fmt.date_str(_dt(value), fmt='%Y-%m-%d %H:%M:%S')


def _age(value) -> str:
    """How long ago, against a naive-**UTC** now.

    `task_run` timestamps are naive UTC like the rest of the `system_status`
    bind; `datetime.now()` here is naive Mountain, and mixing them would
    report every run as six or seven hours older than it is.
    """
    from sam import fmt
    from system_status.timeutil import utcnow_naive
    when = _dt(value)
    return BLANK if when is None else fmt.ago(utcnow_naive() - when)


def _duration(ms) -> str:
    from sam import fmt
    if ms is None:
        return BLANK
    if ms < 1000:
        return f'{ms}ms'
    return f'{fmt.number(round(ms / 1000))}s'


def display_task_list(ctx, payload) -> None:
    """Registry plus the latest run of each task."""
    tasks = payload['tasks']
    if not tasks:
        ctx.console.print('No tasks are registered.', style='yellow')
        return

    table = Table(title='Scheduled tasks', header_style='bold')
    table.add_column('Task')
    table.add_column('Schedule')
    table.add_column('Last occurrence')
    table.add_column('State')
    table.add_column('Age')
    table.add_column('Next due')

    for row in tasks:
        last = row['last_run']
        name = row['name'] if row['enabled'] else f"[dim]{row['name']} (disabled)[/dim]"
        table.add_row(
            name,
            text(row['schedule']),
            _stamp(last['claimed_at']) if last else BLANK,
            _state(last['state']) if last else BLANK,
            _age(last['finished_at'] or last['claimed_at']) if last else BLANK,
            _stamp(row['next_occurrence']),
        )

    ctx.console.print(table)

    if payload['disabled']:
        ctx.console.print(
            Panel(f"Disabled via SAM_TASKS_DISABLED: "
                  f"{', '.join(payload['disabled'])}",
                  style='yellow', title='Kill switch'))


def display_task_history(ctx, payload) -> None:
    """Recent runs, newest first."""
    runs = payload['runs']
    if not runs:
        scope = f" for {payload['task']}" if payload['task'] else ''
        ctx.console.print(f'No task runs recorded{scope}.', style='yellow')
        return

    table = Table(title=f"Task history (last {payload['count']})",
                  header_style='bold')
    table.add_column('Task')
    table.add_column('Occurrence')
    table.add_column('State')
    table.add_column('Trigger')
    table.add_column('Try', justify='right')
    table.add_column('Claimed')
    table.add_column('Took', justify='right')
    table.add_column('Runner')

    for run in runs:
        table.add_row(
            text(run['task']),
            text(run['occurrence']),
            _state(run['state']),
            text(run['trigger']),
            str(run['attempt']),
            _stamp(run['claimed_at']),
            _duration(run['duration_ms']),
            text(run['runner_id']),
        )

    ctx.console.print(table)


def display_task_dispatch(ctx, payload) -> None:
    """What one dispatch did."""
    results = payload['results']
    if not results:
        ctx.console.print('Nothing due.', style='dim')
        return

    title = 'Dispatch (dry run)' if payload['dry_run'] else 'Dispatch'
    table = Table(title=title, header_style='bold')
    table.add_column('Task')
    table.add_column('Occurrence')
    table.add_column('Outcome')
    table.add_column('Took', justify='right')
    table.add_column('Detail')

    for row in results:
        detail = row.get('detail') or {}
        note = row.get('error') or detail.get('message') or ''
        table.add_row(
            text(row['task']),
            _stamp(row.get('occurrence')),
            _state(row['outcome']),
            _duration(row.get('duration_ms')),
            text(note) if note else BLANK,
        )

    ctx.console.print(table)

    counts = ', '.join(f'{k}={v}' for k, v in sorted(payload['counts'].items()))
    ctx.console.print(f'[dim]{counts}[/dim]')
