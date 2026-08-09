"""Rich renderers for ``sam-admin xras``.

Stateless module-level functions taking ``(ctx, payload)`` where ``payload`` is the
plain dict a builder produced — never an ORM object. All formatting goes through
``sam.fmt`` / ``cli.core.display_utils``; no ``strftime``, no ``'{:,}'.format``.
"""

from rich.panel import Panel
from rich.table import Table

from cli.core.display_utils import BLANK, text, truncate

#: Rich styles per action status. Deliberately the same semantics as the web
#: badges (dashboards/fragments/badges.html) so an operator reading the terminal
#: and an operator reading the dashboard learn one vocabulary, not two.
_STATUS_STYLE = {
    'received':  'cyan',
    'processed': 'green',
    'manual':    'yellow',
    'failed':    'red',
    'replayed':  'dim',
}


def _status(value) -> str:
    style = _STATUS_STYLE.get(value, 'white')
    return f'[{style}]{text(value)}[/{style}]'


def _timestamp(value) -> str:
    """Actions arrive in bursts, so the time of day is what separates rows."""
    from sam import fmt
    return BLANK if value is None else fmt.date_str(value, fmt='%Y-%m-%d %H:%M:%S')


def display_action_list(ctx, payload) -> None:
    """Table of recent actions."""
    actions = payload['actions']
    if not actions:
        ctx.console.print('No XRAS actions match the current filter.', style='yellow')
        return

    table = Table(title=f"XRAS actions ({payload['count']})",
                  show_lines=False, header_style='bold')
    table.add_column('ID', justify='right', no_wrap=True)
    # no_wrap on the identity columns and fold on Errors: in a narrow terminal
    # Rich will otherwise wrap the timestamp onto a second line, which turns a
    # scannable log into a wall. Errors is the column that should absorb the
    # squeeze — it is prose.
    table.add_column('Received', no_wrap=True)
    table.add_column('Type', no_wrap=True)
    table.add_column('Request #', no_wrap=True)
    table.add_column('Status', no_wrap=True)
    table.add_column('HTTP', justify='right', no_wrap=True)
    table.add_column('Result', no_wrap=True)
    table.add_column('Errors', overflow='fold')

    for a in actions:
        # The first error is the triage signal; the rest are in --show.
        errors = a['errors']
        if errors:
            summary = errors[0] if len(errors) == 1 else \
                f'{len(errors)} errors: {errors[0]}'
            error_cell = f'[red]{truncate(summary, 60)}[/red]'
        else:
            error_cell = BLANK

        replay_marker = f" [dim]↩{a['replay_of_id']}[/dim]" if a['replay_of_id'] else ''
        table.add_row(
            str(a['action_log_id']) + replay_marker,
            _timestamp(a['received_time']),
            text(a['action_type']),
            text(a['request_number']),
            _status(a['status']),
            text(a['http_status']),
            text(a['projcode_result']),
            error_cell,
        )

    ctx.console.print(table)


def display_action_detail(ctx, payload) -> None:
    """One action in full, with its replay lineage and optionally its payload."""
    a = payload['action']

    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column('field', style='bold')
    table.add_column('value')

    table.add_row('Action ID', str(a['action_log_id']))
    table.add_row('Received', _timestamp(a['received_time']))
    if a['processed_time']:
        table.add_row('Processed', _timestamp(a['processed_time']))
    table.add_row('Status', _status(a['status']))
    table.add_row('HTTP status', text(a['http_status']))
    table.add_row('Action type', text(a['action_type']))
    table.add_row('Request #', text(a['request_number']))
    table.add_row('Result projcode', text(a['projcode_result']))
    table.add_row('Posted by', text(a['remote_actor']))
    if a['processed_by']:
        table.add_row('Replayed by', text(a['processed_by']))
    if a['replay_of_id']:
        table.add_row('Replay of', f"#{a['replay_of_id']}")
    if payload['replays']:
        table.add_row('Replays', ', '.join(f"#{c['action_log_id']} ({c['status']})"
                                           for c in payload['replays']))

    ctx.console.print(Panel(table, title=f"XRAS action #{a['action_log_id']}",
                            border_style='blue'))

    if a['errors']:
        # Verbatim and in order — this is what XRAS was told in the 422 body.
        err = Table(show_header=False, box=None, padding=(0, 1, 0, 0))
        err.add_column('n', justify='right', style='dim')
        err.add_column('message', style='red', overflow='fold')
        for i, message in enumerate(a['errors'], start=1):
            err.add_row(str(i), message)
        ctx.console.print(Panel(err, title=f"Errors ({len(a['errors'])})",
                                border_style='red'))

    if payload['payload_included']:
        ctx.console.print(Panel(a.get('raw_payload', ''),
                                title='Raw payload (verbatim, contains PII)',
                                border_style='yellow'))


def display_summary(ctx, payload) -> None:
    """Rollup by status, then by (status, action type)."""
    by_status = Table(title=f"XRAS actions by status (total {payload['total']})",
                      header_style='bold')
    by_status.add_column('Status')
    by_status.add_column('Count', justify='right')
    for status, count in payload['by_status'].items():
        # Zero rows are printed, not skipped: an absent bucket reads as
        # "not measured" rather than "none".
        by_status.add_row(_status(status), str(count))
    ctx.console.print(by_status)

    if not payload['by_type']:
        return

    by_type = Table(title='By status and action type', header_style='bold')
    by_type.add_column('Status')
    by_type.add_column('Action type')
    by_type.add_column('Count', justify='right')
    for row in payload['by_type']:
        by_type.add_row(_status(row['status']),
                        text(row['action_type']),
                        str(row['count']))
    ctx.console.print(by_type)


def display_replay_result(ctx, payload) -> None:
    """Confirmation after a replay."""
    ctx.console.print(
        f"Replayed action #{payload['replayed_id']} → "
        f"new action #{payload['new_action_id']} "
        f"({_status(payload['status'])})",
        style='green',
    )


def display_mapping_report(ctx, payload) -> None:
    """Render the resource-mapping gaps, worst group first."""
    ctx.console.rule('[bold]XRAS resource mapping')
    ctx.console.print(
        f"[bold]{payload['mapped']}[/bold] mapping row(s) in "
        f"xras_resource_repository_key_resource")

    unmapped = payload['unmapped_active']
    if unmapped:
        table = Table(title='Active resources not offered through XRAS',
                      title_style='bold')
        table.add_column('Resource', style='yellow')
        for name in unmapped:
            table.add_row(name)
        ctx.console.print(table)
        ctx.console.print(
            '[dim]Expected, not a gap: not every internal resource is offered for '
            'allocation through XRAS, so most of these have no mapping by design. '
            'This list is a diagnostic for the opposite case — if an award cites a '
            'resource that SHOULD be allocatable and it appears here, that is the '
            'data fix behind "No resource found in SAM corresponding to key %s".\n'
            'Adding a mapping changes GET response bytes (resourceRepositoryKey is '
            'omitted when unmapped), so do it before a parity run, not after.[/dim]')
    else:
        ctx.console.print('[green]Every active resource is mapped.[/green]')

    stale = payload['mapped_decommissioned']
    if stale:
        table = Table(title='Mappings pointing at decommissioned resources',
                      title_style='bold')
        table.add_column('Key', justify='right', style='cyan')
        table.add_column('Resource', style='dim')
        for entry in stale:
            table.add_row(str(entry['key']), entry['resource'])
        ctx.console.print(table)
        ctx.console.print('[dim]Harmless, but misleading in triage.[/dim]')

    if payload['dangling_keys']:
        ctx.console.print(
            f"[bold red]Dangling keys with no resource row:[/bold red] "
            f"{', '.join(str(k) for k in payload['dangling_keys'])}")
