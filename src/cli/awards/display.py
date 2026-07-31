"""Display functions for award commands. Operate on plain dicts produced by
`cli.awards.builders`; never touch ORM objects or AwardRecords directly."""

from rich import box
from rich.table import Table

from cli.core.context import Context
from cli.core.display_utils import date_cell, text, truncate
from sam import fmt

#: How a cross-referenced status reads, and in what style.
_STATUS_NOTE = {
    'ok': ('SAM agrees with the source', 'green'),
    'no_record': ('the source has no such award', 'yellow'),
    'unavailable': ('the source could not be reached', 'yellow'),
    'suspect_match': (
        'the provider probably found a DIFFERENT award — verify the '
        'contract number, do not copy these values', 'bold red'),
}


def display_award(ctx: Context, data: dict):
    """Render one award lookup plus its SAM cross-reference."""
    award = data['award']

    # A fetched USAspending record deliberately carries no contract_number
    # (it would rewrite the operator's spelling), so fall back to what was
    # asked for rather than heading the panel with a dash.
    number = award['contract_number'] or data.get('contract_number')

    ctx.console.print(
        f"\n[bold]{text(number)}[/bold] — {text(award['title'])}")
    ctx.console.print(f"[dim]via {award['provenance']}[/dim]")

    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Field", style="dim", no_wrap=True)
    table.add_column("Value", overflow="fold")
    table.add_row("Period", f"{date_cell(award['start_date'])} → "
                            f"{date_cell(award['end_date'])}")
    table.add_row("Program", text(award['program_name']))
    table.add_row("PI", _person(award['pi']))
    table.add_row("Monitor", _person(award['monitor']))
    table.add_row("URL", text(award['url']))
    ctx.console.print(table)

    _display_unavailable(ctx, award)
    _display_in_sam(ctx, data.get('in_sam'))


def _display_unavailable(ctx: Context, award: dict):
    """State a structural gap positively rather than rendering a blank."""
    if not award.get('unavailable'):
        return
    fields = ' and '.join(award['unavailable'])
    ctx.console.print(
        f"[dim]ℹ {award['provenance']} cannot supply {fields} — "
        f"enter manually.[/dim]")


def _display_in_sam(ctx: Context, in_sam):
    if in_sam is None:
        ctx.console.print("[dim]Not in SAM — no contract with this number.[/dim]")
        return

    contract = in_sam['contract']
    ctx.console.print(
        f"\n[bold]In SAM:[/bold] contract {contract['contract_id']} — "
        f"{text(contract['title'])}")

    # `status='ok'` only means the comparison ran — `compare_contract`'s
    # docstring is explicit that agreement is *ok plus no divergences*.
    # Printing the 'ok' note unconditionally put "SAM agrees with the source"
    # directly above a table of the ways it does not.
    if in_sam['status'] == 'ok' and in_sam['divergences']:
        ctx.console.print(
            f"  SAM differs from {text(in_sam.get('provenance'))} in "
            f"{len(in_sam['divergences'])} field(s)", style='yellow')
    else:
        note, style = _STATUS_NOTE.get(in_sam['status'],
                                       (in_sam['status'], 'dim'))
        ctx.console.print(f"  {note}", style=style)

    if in_sam['status'] == 'suspect_match':
        summary = in_sam.get('source_summary') or {}
        stable = Table(box=box.SIMPLE, title="What the source returned")
        stable.add_column("Field", style="dim", no_wrap=True)
        stable.add_column("Value", no_wrap=True, overflow="ellipsis")
        for label, key in (("Number", 'contract_number'), ("Title", 'title'),
                           ("Start", 'start_date'), ("End", 'end_date')):
            value = summary.get(key)
            stable.add_row(label,
                           date_cell(value) if 'date' in key else text(value))
        ctx.console.print(stable)
        return

    if in_sam['divergences']:
        dtable = Table(box=box.SIMPLE, title="Divergences")
        dtable.add_column("Field", no_wrap=True)
        dtable.add_column("SAM", no_wrap=True, overflow="ellipsis")
        dtable.add_column("Source", no_wrap=True, overflow="ellipsis")
        for divergence in in_sam['divergences']:
            dtable.add_row(divergence['field'], text(divergence['sam']),
                           text(divergence['source']))
        ctx.console.print(dtable)

    for hint in in_sam['hints']:
        ctx.console.print(
            f"  [dim]hint: {hint['field']} — {text(hint['source'])} "
            f"({hint['note']})[/dim]")


def display_award_search(ctx: Context, data: dict):
    """Render composite free-text search results."""
    # Errors first: a partial result that looks complete is the failure mode
    # the (records, errors) return shape exists to prevent.
    for error in data.get('errors') or []:
        ctx.console.print(
            f"⚠️  {error['provenance']} unavailable — results are partial. "
            f"({error['reason']})", style="yellow")

    if not data['count']:
        ctx.console.print(f"No awards found for {data['query']!r}",
                          style="yellow")
        return

    title = f"{data['count']} award(s) for {data['query']!r}"
    if data['already_in_sam']:
        title += f" — {data['already_in_sam']} already in SAM"

    table = Table(box=box.SIMPLE, title=title)
    table.add_column("Source", no_wrap=True)
    table.add_column("Number", no_wrap=True)
    table.add_column("Title", no_wrap=True, overflow="ellipsis")
    table.add_column("Start", no_wrap=True)
    table.add_column("End", no_wrap=True)
    table.add_column("In SAM", no_wrap=True)

    for row in data['results']:
        table.add_row(
            _provenance(row['provenance']),
            text(row['contract_number']),
            truncate(row['title'], 44),
            date_cell(row['start_date']),
            date_cell(row['end_date']),
            f"✓ {row['in_sam']['contract_number']}" if row['in_sam'] else '',
        )
    ctx.console.print(table)

    # One note per provider that structurally cannot supply people, not one
    # per row — the limitation is the source's, not the award's.
    for provenance in sorted({r['provenance'] for r in data['results']
                              if r['unavailable']}):
        fields = ' and '.join(
            next(r['unavailable'] for r in data['results']
                 if r['provenance'] == provenance))
        ctx.console.print(
            f"[dim]ℹ {provenance} cannot supply {fields} — "
            f"enter manually.[/dim]")

    ctx.console.print(
        "[dim]Pick a number and run `sam-search awards <number>` for the "
        "full record.[/dim]")


def _provenance(value: str) -> str:
    """'NSF Awards API' is too wide for a table column."""
    return 'NSF' if str(value).startswith('NSF') else str(value)


def _person(person) -> str:
    return person['label'] if person and person.get('label') else '—'


