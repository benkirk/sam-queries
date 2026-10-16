"""Rich output for the directory commands; dict input only."""

from rich import box
from rich.table import Table

from cli.core.context import Context
from cli.core.display_utils import stamp


def display_report(ctx: Context, data: dict) -> None:
    """One table per kind: category, count, the first sample keys."""
    scope = f" (limit {data['limit']:,} per kind; SAM rows the sample lacks are not counted)" if data['limit'] else ''
    ctx.console.print(f"\n[bold]Directory report[/] generated {stamp(data['generated'], seconds=False)}{scope}")
    for kind, r in data['kinds'].items():
        title = (f"{kind}: {r['directory']:,} read · {r['sam']:,} in SAM · {r['compared']:,} compared"
                 f" · {r['elapsed_seconds']}s")
        table = Table(title=title, box=box.SIMPLE, title_justify='left')
        table.add_column('Category', style='cyan')
        table.add_column('Count', justify='right')
        table.add_column('Sample keys')
        for category, n in sorted(r['counts'].items(), key=lambda kv: (-kv[1], kv[0])):
            table.add_row(category, f'{n:,}', ', '.join(r['ids'].get(category, [])[:10]))
        if not r['counts']:
            table.add_row('—', '0', 'SAM matches the directory')
        ctx.console.print(table)
