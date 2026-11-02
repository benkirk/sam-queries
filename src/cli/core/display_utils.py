"""Rich display helpers shared across the CLI's display modules.

Cell coercions (`text`, `truncate`, `date_cell`, `stamp`, `styled`), the JSON-aware
progress bar and the provisioning issues table. Dates go through `sam.fmt`.
"""

from datetime import date, datetime

from rich import box
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

from cli.core.context import Context
from sam import fmt

#: What every formatter here renders for "nothing to show". Matches
#: `sam.fmt`'s own default null marker.
BLANK = '—'


def text(value) -> str:
    """A value for a table cell, with `None` and `''` both reading as blank."""
    return BLANK if value is None or value == '' else str(value)


def truncate(value, width: int = 48) -> str:
    """Keep titles from wrapping the table into unreadability."""
    if not value:
        return BLANK
    value = str(value)
    return value if len(value) <= width else value[:width - 1] + '…'


def date_cell(value) -> str:
    """Format a date that may have arrived as an ISO string.

    `build_award` keeps real `date` objects (`_SAMEncoder` serializes them),
    but anything embedding `ContractSummarySchema` output gets ISO text
    instead — correct for the JSON payload, wrong for `fmt.date_str`, which
    wants an object. Parse, then hand off.

    A string that does not parse is returned as-is rather than raising: it is
    a display path, and showing the raw value beats a traceback.
    """
    if not value:
        return BLANK
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, (date, datetime)):
        return fmt.date_str(value)
    return str(value)


def progress(ctx) -> Progress:
    """The CLI's progress bar; disabled in JSON mode so stdout stays one document."""
    return Progress(TextColumn("[progress.description]{task.description}"), BarColumn(),
                    MofNCompleteColumn(), TimeElapsedColumn(), console=ctx.console,
                    disable=ctx.output_format == 'json')


def styled(value, styles: dict, default: str = 'white') -> str:
    """``value`` as Rich markup in its style from ``styles``."""
    style = styles.get(value, default)
    return f'[{style}]{text(value)}[/{style}]'


def stamp(value, seconds: bool = True) -> str:
    """A datetime, or the ISO text a builder emitted, to the second or the minute."""
    if isinstance(value, str) and value:
        value = datetime.fromisoformat(value)
    return fmt.date_str(value or None, fmt='%Y-%m-%d %H:%M:%S' if seconds else '%Y-%m-%d %H:%M')


def issues_table(ctx, subject: str, issues: list, *,
                 heading: str = 'Host provisioning issues for') -> None:
    """Findings for ``subject``: (check, detail) rows under ``heading``."""
    from rich.table import Table
    ctx.console.print(f"\n[bold yellow]{heading} {subject}:[/]")
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Check", style="cyan")
    table.add_column("Detail", style="yellow")
    for label, detail in issues:
        table.add_row(label, detail)
    ctx.console.print(table)

def directory_unreadable(ctx: Context, view: dict, subject: str) -> bool:
    """The two states before a record: unavailable, or absent. True when nothing more to show."""
    if not view['available']:
        ctx.console.print(f"⚠️  Directory: could not read {subject} ({view['error'] or 'unavailable'}).",
                          style='yellow')
        return True
    if not view['found']:
        ctx.console.print(f"⚠️  Directory: {subject} is not in the directory.", style='yellow')
        return True
    return False


def directory_drift(ctx: Context, view: dict, subject: str, *, report_only: str = ''):
    """The drift rows as an issues table, then the lever line (or ``report_only``, the note)."""
    issues = [(f"{r['category']} {r['field'] or ''}".strip(),
               f"SAM {r['sam'] or '—'} · directory {r['directory'] or '—'} ({r['disposition']})")
              for r in view['rows']]
    if issues:
        issues_table(ctx, subject, issues, heading='Directory drift for')
    else:
        ctx.console.print('[green]✓[/] SAM matches the directory.', style='dim')
    if report_only:
        ctx.console.print(report_only, style='dim')
    elif view.get('sync'):
        ctx.console.print(f"--directory-apply writes {view['would_apply']}, withholds {view['withheld']}."
                          f" Automatic apply on sync: {', '.join(view['apply_kinds']) or 'none'}.",
                          style='dim')
    else:
        ctx.console.print(f"Levers on: {', '.join(view['apply_kinds']) or 'none'}."
                          f" {view['would_apply']} would apply, {view['withheld']} withheld.",
                          style='dim')
