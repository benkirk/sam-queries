"""Rich renderers for ``sam-admin masks``: stateless, ``(ctx, payload)`` in."""

from rich.table import Table

from cli.core.display_utils import BLANK, date_cell, text, truncate
from cli.core.output import print_table

_STATE_STYLE = {'pending': 'yellow', 'confirmed': 'green', 'expired': 'dim', 'withdrawn': 'dim'}


def display_listing(ctx, payload: dict) -> None:
    title = f"{payload['count']} {'open ' if payload['open_only'] else ''}mask(s)"
    t = Table(title=title)
    for col in ('Id', 'Group', 'Kind', 'Member', 'Change', 'State', 'Flags', 'Ticket',
                'Requested', 'Until', 'Note'):
        t.add_column(col)
    for r in payload['masks']:
        style = _STATE_STYLE.get(r['state'], 'white')
        ticket = r['ticket']
        t.add_row(str(r['mask_id']), r['group_name'] or str(r['gid']), r['kind'], r['username'],
                  r['op'], f'[{style}]{r["state"]}[/{style}]',
                  ', '.join(r['flags']) if r['flags'] else BLANK,
                  f"{ticket['key']} ({ticket['status']})" if ticket else BLANK,
                  f"{r['requested_by']} {date_cell(r['creation_time'])}",
                  date_cell(r['expires_at']) if r['expires_at'] else 'open-ended',
                  truncate(text(r['note']), 40) if r['note'] else BLANK)
    print_table(ctx.console, t)


def display_opened(ctx, payload: dict) -> None:
    t = payload['target']
    head = f"{'Would open' if payload['dry_run'] else 'Opened'} {payload['op']} on {t['name']} (gid {t['gid']}, {t['kind']})"
    ctx.console.print(f'[bold]{head}[/bold]')
    if payload['dry_run']:
        for name in payload['checked']:
            ctx.console.print(f'  ✓ {name}: every guard passes')
        return
    for m in payload['masks']:
        until = f" until {date_cell(m['expires_at'])}" if m['expires_at'] else ''
        ctx.console.print(f"  #{m['mask_id']} {m['username']}{until}")
    ticket = payload['ticket']
    if ticket is None:
        ctx.console.print('  no ticket path is configured here; NUSD has not been told',
                          style='yellow')
    elif ticket['status'] == 'sent':
        ctx.console.print(f"  NUSD ticket {ticket['detail']}")
    else:
        ctx.console.print(f"  ticket {ticket['status']}: {ticket['detail'] or ''}")


def display_withdrawn(ctx, payload: dict) -> None:
    verb = 'Would withdraw' if payload['dry_run'] else 'Withdrew'
    line = f"{verb} #{payload['mask_id']}: {payload['op']} {payload['username']} on gid {payload['gid']}"
    if not payload['dry_run'] and payload['commented']:
        line += '; NUSD ticket commented'
    ctx.console.print(line)
