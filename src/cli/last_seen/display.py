"""Rich renderers for ``sam-admin last-seen``; payloads are plain dicts from the command."""

from rich.table import Table

from sam import fmt


def display_last_seen(ctx, payload):
    if not payload['sources']:
        ctx.console.print(f"No last-seen rows for {payload['username']}", style='yellow')
        return
    table = Table(title=f"Last seen: {payload['username']}")
    for col in ('Source', 'System', 'First seen (UTC)', 'Last seen (UTC)'):
        table.add_column(col)
    for row in payload['sources']:
        last = fmt.date_str(row['last_seen'], fmt='%Y-%m-%d %H:%M')
        table.add_row(row['kind'], row['system'],
                      fmt.date_str(row['first_seen'], fmt='%Y-%m-%d %H:%M'),
                      f"{last}  (now)" if row.get('current') else last)
    ctx.console.print(table)


def display_backfill(ctx, payload):
    tables = Table(title='Charge summaries read')
    for col in ('Table', 'User/machine groups', 'Seconds'):
        tables.add_column(col, justify='right' if col != 'Table' else 'left')
    for t in payload['tables']:
        tables.add_row(t['table'], fmt.number(t['groups']), str(t['seconds']))
    ctx.console.print(tables)

    machines = Table(title='SAM machine -> status system')
    for col in ('Machine', 'System', 'Users'):
        machines.add_column(col, justify='right' if col == 'Users' else 'left')
    for m in payload['machines']:
        machines.add_row(m['machine'], m['system'], fmt.number(m['users']))
    ctx.console.print(machines)

    systems = Table(title='Would write (dry run)' if payload['dry_run'] else 'Written')
    for col in ('System', 'Users', 'Applied'):
        systems.add_column(col, justify='left' if col == 'System' else 'right')
    for s in payload['systems']:
        systems.add_row(s['system'], fmt.number(s['users']), fmt.number(s['applied']))
    ctx.console.print(systems)

    skipped = payload['skipped']
    if any(skipped.values()):
        ctx.console.print(f"Skipped groups: {fmt.number(skipped['no_username'])} without a username, "
                          f"{fmt.number(skipped['username_too_long'])} with a username over 32 characters",
                          style='yellow')
