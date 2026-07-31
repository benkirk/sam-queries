"""Display functions for contract commands. Operate on plain dicts produced
by `cli.contracts.builders`; never touch ORM objects directly."""

from cli.core.context import Context
from cli.core.display_utils import date_cell, text, truncate
from sam import fmt
from rich.table import Table
from rich import box

#: Rich style per severity, so the eye lands on the 15%-of-open bug first.
_SEVERITY_STYLE = {'high': 'bold yellow', 'medium': 'yellow', 'low': 'dim'}

#: Extra column per check: (heading, key into the finding's ``detail``).
_DETAIL_COLUMN = {
    'funding_account_program': ('Program', 'nsf_program'),
    'monitor_is_pi':           ('PI/Monitor', 'username'),
    'missing_program':         ('Program', 'nsf_program'),
}


def display_contract(ctx: Context, data: dict, list_projects: bool = False):
    """Render one contract's detail.

    ``data`` is a ``build_contract()`` envelope.
    """
    ctx.console.print(
        f"\n[bold]{data['contract_number']}[/bold] — {data['title']}")

    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Field", style="dim", no_wrap=True)
    table.add_column("Value", overflow="fold")

    rows = [
        ("Source",   text(data.get('contract_source'))),
        ("Status",   "active" if data.get('is_active') else "expired"),
        ("Period",   f"{date_cell(data.get('start_date'))} → "
                     f"{date_cell(data.get('end_date'))}"),
        ("PI",       text(data.get('pi_username'))),
        ("Monitor",  text(data.get('monitor_username'))),
        ("Program",  text(data.get('nsf_program'))),
        ("URL",      text(data.get('url'))),
    ]
    for label, value in rows:
        table.add_row(label, value)
    ctx.console.print(table)

    projects = data.get('projects') or []
    if not list_projects:
        if projects:
            ctx.console.print(
                f"[dim]{len(projects)} linked project(s) — "
                f"use --list-projects to show them[/dim]")
        return

    if not projects:
        ctx.console.print("[dim]No linked projects[/dim]")
        return

    ptable = Table(box=box.SIMPLE, title=f"Linked projects ({len(projects)})")
    ptable.add_column("Project", no_wrap=True)
    ptable.add_column("Title", no_wrap=True, overflow="ellipsis")
    ptable.add_column("Active", no_wrap=True)
    for project in projects:
        ptable.add_row(project['projcode'], truncate(project['title'], 54),
                       "yes" if project['is_active'] else "no")
    ctx.console.print(ptable)


def display_contract_search(ctx: Context, data: dict):
    """Render contract search results.

    ``data`` is a ``build_contract_search()`` envelope.
    """
    if not data['count']:
        ctx.console.print("No contracts found", style="yellow")
        return

    table = Table(box=box.SIMPLE,
                  title=f"{data['count']} contract(s) — {data['scope']}")
    table.add_column("Number", no_wrap=True)
    table.add_column("Source", no_wrap=True)
    table.add_column("Title", no_wrap=True, overflow="ellipsis")
    table.add_column("PI", no_wrap=True)
    table.add_column("Start", no_wrap=True)
    table.add_column("End", no_wrap=True)

    for contract in data['contracts']:
        table.add_row(
            contract['contract_number'],
            text(contract.get('contract_source')),
            truncate(contract.get('title'), 46),
            text(contract.get('pi_username')),
            date_cell(contract.get('start_date')),
            date_cell(contract.get('end_date')),
        )
    ctx.console.print(table)


def display_contract_audit(ctx: Context, data: dict):
    """Render the contract data-hygiene audit.

    ``data`` is a ``build_contract_audit()`` envelope. Every check is shown,
    including clean ones — an omitted section reads as "not run".
    """
    for check in data['checks']:
        _display_check(ctx, check, data['contracts_audited'])

    _display_program_findings(ctx, data['program_findings'])

    if data['source_check'] is not None:
        _display_source_check(ctx, data['source_check'])


def _display_check(ctx: Context, check: dict, audited: int):
    """One check: a warning table, or a green all-clear line."""
    if not check['count']:
        ctx.console.print(
            f"✅ 0 of {fmt.number(audited)} — {check['label']}",
            style="green",
        )
        return

    style = _SEVERITY_STYLE.get(check['severity'], 'yellow')
    ctx.console.print(
        f"\n⚠️  {check['count']} of {fmt.number(audited)} — {check['label']}:",
        style=style,
    )

    # Every column is no-wrap: a 57-row table whose titles wrap over three
    # lines each is unreadable, and the full values are in --format json.
    detail_column = _DETAIL_COLUMN.get(check['key'])
    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("Contract", no_wrap=True)
    table.add_column("Source", no_wrap=True)
    table.add_column("Title", no_wrap=True, overflow="ellipsis")
    table.add_column("Ends", justify="right", no_wrap=True, min_width=10)
    if detail_column:
        table.add_column(detail_column[0], no_wrap=True, overflow="ellipsis")

    for finding in check['findings']:
        contract = finding['contract']
        row = [
            contract['contract_number'],
            truncate(contract['contract_source'], 16),
            truncate(contract['title'], 44),
            date_cell(contract['end_date']),
        ]
        if detail_column:
            row.append(truncate(finding['detail'].get(detail_column[1]), 28))
        table.add_row(*row)
    ctx.console.print(table)

    if ctx.verbose:
        for finding in check['findings']:
            contract = finding['contract']
            ctx.console.print(
                f"    [dim]#{contract['contract_id']:<6} "
                f"PI {contract['pi_username'] or '—':12} "
                f"Monitor {contract['monitor_username'] or '—':12} "
                f"Program {contract['nsf_program'] or '—'}[/dim]"
            )


def _display_program_findings(ctx: Context, findings: list):
    """The lookup-table section: nsf_program rows that are funding accounts."""
    if not findings:
        ctx.console.print(
            "✅ No nsf_program rows are funding-account strings", style="green")
        return

    total_open = sum(f['open_contract_count'] for f in findings)
    ctx.console.print(
        f"\n⚠️  {len(findings)} nsf_program row(s) are funding accounts, not "
        f"research programs — {total_open} open contract(s) point at them:",
        style="bold yellow",
    )
    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("ID", justify="right")
    table.add_column("Program name", no_wrap=True, overflow="ellipsis")
    table.add_column("Active")
    table.add_column("Contracts", justify="right")
    table.add_column("Open", justify="right", style="red")
    for finding in findings:
        table.add_row(
            str(finding['nsf_program_id']),
            finding['nsf_program_name'],
            "yes" if finding['active'] else "no",
            fmt.number(finding['contract_count']),
            fmt.number(finding['open_contract_count']),
        )
    ctx.console.print(table)


def _display_source_check(ctx: Context, source_check: dict):
    """The --check-sources section: divergence against the funding agency."""
    ctx.console.print(
        f"\n[bold]Funding-source check[/bold] — "
        f"{fmt.number(source_check['checked'])} checked, "
        f"{fmt.number(source_check['agreed'])} agreed, "
        f"{fmt.number(source_check['divergent'])} divergent, "
        f"{fmt.number(source_check['suspect_match'])} suspect match, "
        f"{fmt.number(source_check['no_record'])} not found, "
        f"{fmt.number(source_check['unchecked'])} unchecked"
    )

    rows = [c for c in source_check['contracts'] if c['divergences']]
    if rows:
        ctx.console.print(
            f"\n⚠️  {len(rows)} contract(s) diverge from the funding source:",
            style="bold yellow",
        )
        table = Table(box=box.SIMPLE, show_header=True)
        table.add_column("Contract")
        table.add_column("Field")
        table.add_column("SAM")
        table.add_column("Source")
        for entry in rows:
            for i, divergence in enumerate(entry['divergences']):
                table.add_row(
                    entry['contract']['contract_number'] if i == 0 else "",
                    divergence['field'],
                    truncate(text(divergence['sam']), 40),
                    truncate(text(divergence['source']), 40),
                )
        ctx.console.print(table)
    else:
        ctx.console.print("✅ No divergence from the funding source", style="green")

    _display_suspect_matches(
        ctx, [c for c in source_check['contracts']
              if c['status'] == 'suspect_match'])

    if not ctx.verbose:
        return

    # Hints are suggestions, not findings — only shown with -v.
    hinted = [c for c in source_check['contracts'] if c['hints']]
    for entry in hinted:
        ctx.console.print(
            f"    [dim]{entry['contract']['contract_number']}: " + "; ".join(
                f"{h['field']} — {h['note']} ({h['source']})"
                for h in entry['hints']
            ) + "[/dim]"
        )


def _display_suspect_matches(ctx: Context, entries: list):
    """Contracts where the provider returned some *other* award.

    Shown apart from divergences on purpose: the finding here is "this number
    does not resolve", not "this field is stale". Acting on it means checking
    the number, not copying the source's values over SAM's.
    """
    if not entries:
        return

    ctx.console.print(
        f"\n[dim]{len(entries)} contract(s) resolved to what looks like a "
        f"different award — verify the contract number, do not copy these "
        f"values:[/dim]"
    )
    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("Contract", no_wrap=True)
    table.add_column("SAM title", no_wrap=True, overflow="ellipsis")
    table.add_column("Source", no_wrap=True)
    table.add_column("Source title", no_wrap=True, overflow="ellipsis")
    for entry in entries:
        summary = entry['source_summary'] or {}
        table.add_row(
            entry['contract']['contract_number'],
            truncate(entry['contract']['title'], 34),
            truncate(entry['provenance'], 14),
            truncate(summary.get('title'), 34),
        )
    ctx.console.print(table)


