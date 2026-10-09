"""Cell formatters shared by the contract and award display modules.

Both packages render the same payloads — `ContractSummarySchema` output and
`compare_contract` results — so they need the same three coercions. They grew
a private copy each (#403 then #404) and the copies drifted: one `_date` was
missing the `date`/`datetime` guard, and an empty string rendered as `—` in
award output but as `''` in contract output. These are the more-correct
versions of each.

All date formatting still goes through `sam.fmt` rather than a local
`strftime` or a string slice, per the house rule.
"""

from datetime import date, datetime

from rich import box
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

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


def issues_table(ctx, subject: str, issues: list) -> None:
    """The host-provisioning findings for ``subject``: (check, detail) rows under a heading."""
    from rich.table import Table
    ctx.console.print(f"\n[bold yellow]Host provisioning issues for {subject}:[/]")
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Check", style="cyan")
    table.add_column("Detail", style="yellow")
    for label, detail in issues:
        table.add_row(label, detail)
    ctx.console.print(table)
