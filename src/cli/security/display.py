"""Rich renderers for ``sam-admin rbac``: stateless, ``(ctx, payload)`` in."""

from rich.table import Table

from cli.core.display_utils import BLANK, text


def _what(g: dict) -> str:
    return f'role [bold]{g["role"]}[/bold]' if g['role'] else f'perm [cyan]{g["permission"]}[/cyan]'


def display_listing(ctx, payload: dict) -> None:
    roles = Table(title='Roles', show_lines=False)
    for col in ('Name', 'Extends', 'Active', 'Direct', 'Effective', 'Description'):
        roles.add_column(col)
    for r in payload['roles']:
        roles.add_row(r['name'], text(r['extends']) if r['extends'] else BLANK,
                      'yes' if r['active'] else '[red]no[/red]',
                      str(len(r['direct'])), str(len(r['effective'])),
                      text(r['description']) if r['description'] else BLANK)
    ctx.console.print(roles)
    ctx.console.print(_grants_table(payload['grants'], title='Grants'))


def _grants_table(grants, *, title: str) -> Table:
    t = Table(title=title)
    for col in ('Id', 'Subject', 'What', 'Facility', 'Note', 'By', 'Revoked'):
        t.add_column(col)
    for g in grants:
        t.add_row(str(g['id']), f'{g["subject_type"]}:{g["subject_name"]}', _what(g),
                  g['facility'] or 'all', text(g['note']) if g['note'] else BLANK,
                  g['created_by'], g['revoked_by'] or BLANK)
    return t


def display_effective(ctx, payload: dict) -> None:
    head = f'{payload["subject_type"]}:{payload["subject_name"]} (source: {payload["source"]})'
    if payload['groups']:
        head += '  groups: ' + ', '.join(payload['groups'])
    ctx.console.print(f'[bold]{head}[/bold]')
    ctx.console.print(_grants_table(payload['grants'], title='Contributing grants'))
    t = Table(title='Effective permissions')
    t.add_column('Scope')
    t.add_column('Permissions')
    t.add_row('all facilities', ', '.join(payload['unscoped']) or BLANK)
    for facility, perms in payload['scoped'].items():
        t.add_row(facility, ', '.join(perms))
    ctx.console.print(t)


def display_keys(ctx, payload: dict) -> None:
    t = Table(title='API keys')
    for col in ('Key', 'Source', 'Active grants'):
        t.add_column(col)
    for k in payload['keys']:
        n = k['grants']
        t.add_row(k['name'], k['source'], f'[red]{n}[/red]' if not n else str(n))
    ctx.console.print(t)
    if payload['ungranted']:
        ctx.console.print(f'[yellow]{len(payload["ungranted"])} key(s) hold no grant and '
                          'are denied everywhere in db mode: '
                          f'{", ".join(payload["ungranted"])}[/yellow]')
    else:
        ctx.console.print('[green]Every key holds a grant.[/green]')


def display_diff(ctx, payload: dict) -> None:
    if not (payload['roles'] or payload['grants_only_in_db'] or payload['grants_only_in_defaults']):
        ctx.console.print('[green]The tables match the factory defaults.[/green]')
        return
    for name, sides in payload['roles'].items():
        ctx.console.print(f'[bold]{name}[/bold]')
        db, dflt = sides['db'], sides['defaults']
        if db is None:
            ctx.console.print('  only in defaults')
        elif dflt is None:
            ctx.console.print('  only in db')
        else:
            extra = sorted(set(db) - set(dflt))
            missing = sorted(set(dflt) - set(db))
            if extra:
                ctx.console.print(f'  db adds: {", ".join(extra)}')
            if missing:
                ctx.console.print(f'  db lacks: {", ".join(missing)}')
    for g in payload['grants_only_in_db']:
        ctx.console.print(f'grant only in db: {g}')
    for g in payload['grants_only_in_defaults']:
        ctx.console.print(f'grant only in defaults: {g}')
