"""Rich renderers for ``sam-admin xras``.

Stateless module-level functions taking ``(ctx, payload)`` where ``payload`` is the
plain dict a builder produced — never an ORM object. All formatting goes through
``sam.fmt`` / ``cli.core.display_utils``; no ``strftime``, no ``'{:,}'.format``.
"""

from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from cli.core.display_utils import BLANK, stamp, styled, text, truncate
from cli.core.output import print_table

#: Rich styles per action status. Deliberately the same semantics as the web
#: badges (dashboards/fragments/badges.html) so an operator reading the terminal
#: and an operator reading the dashboard learn one vocabulary, not two.
_STATUS_STYLE = {
    'received':  'cyan',
    'processed': 'green',
    'manual':    'yellow',
    'failed':    'red',
    'rechecked':  'dim',
    'unmapped':   'magenta',
    'duplicate':  'dim',
}

#: Short Source labels, same vocabulary as the Pending Users card badges.
_SOURCE_LABELS = {'action_log': 'push', 'reports': 'pending'}


def _sources(values) -> str:
    """A row's provenance, received-push first."""
    labels = [_SOURCE_LABELS.get(v, v) for v in values or ()]
    labels.sort(key=lambda s: s != 'push')
    return ', '.join(labels) or BLANK


def _status(value) -> str:
    return styled(value, _STATUS_STYLE)


_READINESS_STYLE = {'failed': '[red]would fail[/red]',
                    'manual': '[yellow]would park[/yellow]',
                    'incomplete': '[dim]incomplete[/dim]',
                    'rechecked': '[green]would succeed[/green]',
                    None: '[dim]—[/dim]'}


def display_readiness(ctx, payload) -> None:
    """Push-readiness board: the sweep's per-request preflight, worst first."""
    rows = payload['requests']
    if not rows:
        ctx.console.print('No swept requests carry a pre-flight verdict yet.',
                          style='yellow')
        return
    table = Table(title=f"XRAS push-readiness ({payload['total']} request(s))",
                  show_lines=False, header_style='bold')
    table.add_column('Request #', no_wrap=True)
    table.add_column('Readiness', no_wrap=True)
    table.add_column('Status', no_wrap=True)
    table.add_column('PI', no_wrap=True)
    table.add_column('Opportunity', overflow='fold')
    table.add_column('First reason', overflow='fold')
    for r in rows:
        table.add_row(
            text(r['request_number']),
            _READINESS_STYLE.get(r['rollup'], text(r['rollup'])),
            text(r['status']),
            text(r['pi']),
            text(r['opportunity_name']),
            f"[red]{truncate(r['messages'][0], 60)}[/red]" if r['messages'] else BLANK,
        )
    print_table(ctx.console, table)


def display_mnemonic_report(ctx, payload) -> None:
    """The orgs/institutions to link, ranked by how many failing pushes each unblocks."""
    targets = payload['targets']
    if not targets and not payload['unresolved'] and not payload.get('resolvable'):
        ctx.console.print('No failing push cites a missing organization mnemonic.',
                          style='yellow')
        return
    if targets:
        table = Table(
            title=f"Mnemonic links to create ({len(targets)}), "
                  f"ranked by pushes unblocked",
            show_lines=False, header_style='bold')
        table.add_column('Organization / Institution', overflow='fold')
        table.add_column('Type', no_wrap=True)
        table.add_column('Unblocks', justify='right', no_wrap=True)
        table.add_column('Sample requests', overflow='fold')
        for t in targets:
            table.add_row(text(t['name']), t['family'], str(t['unblock_count']),
                          ', '.join(t['sample']))
        print_table(ctx.console, table)
    if payload['unresolved']:
        # Not fixable by minting a code — these PIs have no current affiliation.
        pis = sorted({u['pi'] for u in payload['unresolved'] if u['pi']})
        ctx.console.print(
            f"[yellow]{len(payload['unresolved'])} action(s) blocked by "
            f"{len(pis)} PI(s) with no current affiliation[/yellow] "
            f"(need a user_organization row, not a mnemonic): "
            f"{', '.join(pis) or '—'}")
    if payload.get('resolvable'):
        # Best affiliation unmapped but another current one resolves — minting a
        # code for the unmapped one would stamp the wrong series; fix the ordering.
        pis = sorted({r['pi'] for r in payload['resolvable'] if r['pi']})
        ctx.console.print(
            f"[yellow]{len(payload['resolvable'])} action(s) blocked by "
            f"{len(pis)} PI(s) whose best affiliation is unmapped but another "
            f"resolves[/yellow] (affiliation-ordering fix, not a mnemonic): "
            f"{', '.join(pis) or '—'}")


def display_identity_report(ctx, payload) -> None:
    """Placeholders to merge, ranked by how many pushes each merge unblocks."""
    targets = payload['targets']
    others = payload['reactivations'], payload['needs_account']
    if not targets and not any(others):
        ctx.console.print('No failing push is blocked by a placeholder SAM already knows.',
                          style='yellow')
        return
    if targets:
        table = Table(
            title=f"Placeholders to merge ({len(targets)}), ranked by pushes unblocked",
            show_lines=False, header_style='bold')
        table.add_column('Placeholder', no_wrap=True)
        table.add_column('Merge into', no_wrap=True)
        table.add_column('Email', overflow='fold')
        table.add_column('Identified?', no_wrap=True)
        table.add_column('Unblocks', justify='right', no_wrap=True)
        table.add_column('Sample requests', overflow='fold')
        for t in targets:
            table.add_row(text(t['username']), text(t['target_username']),
                          text(t['email']),
                          'yes' if t['is_reconciled'] else 'no',
                          str(t['unblock_count']), ', '.join(t['sample']))
        print_table(ctx.console, table)
    for r in payload['reactivations']:
        ctx.console.print(
            f"[yellow]{r['username']}[/yellow] matches {r['target_username']}, "
            f"which is inactive — reactivate, then merge ({r['unblock_count']} push(es))")
    if payload['needs_account']:
        names = ', '.join(f"{n['username']} <{n['email'] or BLANK}>"
                          for n in payload['needs_account'])
        ctx.console.print(
            f"[dim]{len(payload['needs_account'])} need a SAM account before a merge "
            f"is possible: {names}[/dim]")


def display_contract_report(ctx, payload) -> None:
    """The contracts to create, ranked by how many failing pushes each unblocks."""
    targets, variants = payload['targets'], payload['variants']
    if not targets and not variants:
        ctx.console.print('No failing push cites a contract SAM does not hold.',
                          style='yellow')
        return
    if targets:
        table = Table(
            title=f"Contracts to create ({len(targets)}), ranked by pushes unblocked",
            show_lines=False, header_style='bold')
        table.add_column('Grant number', no_wrap=True)
        table.add_column('Agency', overflow='fold')
        table.add_column('Award?', no_wrap=True)
        table.add_column('Title', overflow='fold')
        table.add_column('Unblocks', justify='right', no_wrap=True)
        table.add_column('Sample requests', overflow='fold')
        for t in targets:
            table.add_row(text(t['number']), text(t['agency']),
                          'yes' if t['award_like'] else 'no',
                          truncate(t['title'] or '', 48), str(t['unblock_count']),
                          ', '.join(t['sample']))
        print_table(ctx.console, table)
    for v in variants:
        # A tie is a spelling problem, not a missing row — do not create a second one.
        ctx.console.print(
            f"[yellow]{v['number']}[/yellow] would tie against "
            f"{', '.join(v['candidates']) or '—'} — a possible spelling variant, "
            f"not a missing contract ({v['unblock_count']} push(es))")


#: Rich styles per award-audit issue: red is a discrepancy, yellow is context.
_AUDIT_STYLE = {
    'project_missing': 'red', 'resource_unmapped': 'red', 'allocation_missing': 'red',
    'amount_differs': 'red', 'begin_differs': 'red', 'end_differs': 'red',
    'renamed_pending': 'yellow', 'project_inactive': 'yellow', 'approximate': 'yellow',
    'sam_only': 'yellow',
}


def display_award_audit(ctx, payload) -> None:
    """Awards whose SAM allocation differs, then the per-kind counts."""
    from sam import fmt

    shown = [r for r in payload['rows'] if r['issues']]
    scope = payload.get('projcode') or 'current awards'
    if not shown:
        ctx.console.print(f"All {fmt.plural(payload['checked'], 'award', 'awards')} "
                          f"({scope}) match SAM.", style='green')
        return
    table = Table(title=f"XRAS awarded vs SAM allocated ({scope}, as of {payload['as_of']})",
                  show_lines=False, header_style='bold')
    table.add_column('Project', no_wrap=True)
    table.add_column('Resource', overflow='fold')
    table.add_column('XRAS', justify='right', no_wrap=True)
    table.add_column('SAM', justify='right', no_wrap=True)
    table.add_column('XRAS dates', no_wrap=True)
    table.add_column('SAM dates', no_wrap=True)
    table.add_column('Issues', overflow='fold')
    for r in shown:
        project = r['projcode'] if r['sam_projcode'] in (None, r['projcode']) \
            else f"{r['projcode']} -> {r['sam_projcode']}"
        table.add_row(
            project, text(r['sam_resource'] or r['xras_resource']),
            # Exact: compact notation would print a 1.25M vs 1.2M mismatch as equal-looking.
            fmt.number(r['xras_amount'], raw=True), fmt.number(r['sam_amount'], raw=True),
            f"{text(r['xras_begin'])} .. {text(r['xras_end'])}",
            f"{text(r['sam_begin'])} .. {text(r['sam_end'])}",
            ', '.join(styled(k, _AUDIT_STYLE) for k in r['issues']))
    print_table(ctx.console, table)
    counts = ', '.join(f'{k} {n}' for k, n in payload['counts'].items() if n)
    ctx.console.print(f"{payload['mismatched']} of {fmt.plural(payload['checked'], 'row', 'rows')} "
                      f"mismatched; {counts}")
    if payload.get('retired_skipped'):
        ctx.console.print(f"Skipped {fmt.plural(payload['retired_skipped'], 'award', 'awards')} "
                          'on decommissioned resources.', style='dim')
    if not payload['complete']:
        ctx.console.print('Incomplete: the request page budget ran out.', style='yellow')


def _wait_cell(stats) -> str:
    """``median (n)`` in hours."""
    from sam import fmt
    return BLANK if not stats['n'] else f"{fmt.amount(stats['median_h'])} ({stats['n']})"


def _wait_table(ctx, title, groups) -> None:
    from sam import fmt
    table = Table(title=title, header_style='bold')
    table.add_column('', no_wrap=True)
    for name in ('Episodes', 'Applied', 'Waiting'):
        table.add_column(name, justify='right')
    table.add_column('a: refused -> cleared', justify='right')
    table.add_column('b: cleared -> applied', justify='right')
    table.add_column('c: total', justify='right')
    table.add_column('c p90', justify='right')
    table.add_column('c max', justify='right')
    for name, g in groups.items():
        table.add_row(name, fmt.number(g['episodes']), fmt.number(g['applied']),
                      fmt.number(g['waiting']), _wait_cell(g['a']), _wait_cell(g['b']),
                      _wait_cell(g['c']), fmt.amount(g['c']['p90_h']),
                      fmt.amount(g['c']['max_h']))
    print_table(ctx.console, table)


def display_repost_wait(ctx, payload) -> None:
    """The waits overall, by action type and by blocker, then the still-waiting list."""
    from sam import fmt

    ctx.console.print(f"XRAS re-post wait, first refusals {payload['since']} .. "
                      f"{payload['until']} (hours: median (n))", style='bold')
    if not payload['overall']['episodes']:
        ctx.console.print('No refused posts in the window.', style='green')
        return
    _wait_table(ctx, 'Overall', {'all': payload['overall']})
    _wait_table(ctx, 'By action type', payload['by_action_type'])
    _wait_table(ctx, 'By blocker (an episode counts under each kind it had)',
                payload['by_blocker'])

    blockers = Table(title='Each blocker on its own', header_style='bold')
    blockers.add_column('Kind', no_wrap=True)
    blockers.add_column('Blockers', justify='right')
    blockers.add_column('Cleared', justify='right')
    blockers.add_column('refused -> cleared', justify='right')
    blockers.add_column('Signals', overflow='fold')
    for kind, g in payload['by_blocker'].items():
        blockers.add_row(kind, fmt.number(g['blockers']), fmt.number(g['blockers_cleared']),
                         _wait_cell(g['blocker_a']),
                         ', '.join(f'{s} {n}' for s, n in g['sources'].items()) or BLANK)
    print_table(ctx.console, blockers)

    w, t = payload['waiting'], payload['ticket']
    ctx.console.print(
        f"Still waiting: {w['count']} (age median {fmt.amount(w['age']['median_h'])} h); "
        f"cleared in SAM but not re-posted: {w['cleared_not_reposted']} "
        f"(median {fmt.amount(w['since_cleared']['median_h'])} h since)")
    ctx.console.print(
        f"NUSD tickets: {t['opened']}; 'repost it' comments: {t['repost_asked']}; "
        f"comment -> applied median {fmt.amount(t['asked_to_applied']['median_h'])} h")
    linked = ', '.join(f'{k} {n}' for k, n in payload['linked_by'].items())
    ctx.console.print(f'Linked to the applied post by: {linked}', style='dim')
    _episode_table(ctx, 'Oldest waiting', w['oldest'])
    if payload.get('episodes'):
        _episode_table(ctx, 'Episodes', payload['episodes'])


def _episode_table(ctx, title, episodes) -> None:
    from sam import fmt
    if not episodes:
        return
    table = Table(title=title, header_style='bold')
    table.add_column('Request', no_wrap=True)
    table.add_column('Type', no_wrap=True)
    table.add_column('First refused', no_wrap=True)
    table.add_column('Refusals', justify='right')
    table.add_column('Blockers', overflow='fold')
    table.add_column('a', justify='right')
    table.add_column('b', justify='right')
    table.add_column('c / age', justify='right')
    table.add_column('Ticket', no_wrap=True)
    for e in episodes:
        hrs = e['hours']
        total = fmt.amount(hrs['c']) if hrs['c'] is not None else \
            f"[yellow]{fmt.amount(hrs['age'])}[/yellow]"
        table.add_row(text(e['request_number']), text(e['action_type']),
                      text(e['first_refused']), fmt.number(e['refusals']),
                      ', '.join(b['kind'] for b in e['blockers']) or BLANK,
                      fmt.amount(hrs['a']), fmt.amount(hrs['b']), total,
                      text(e['ticket_key']))
    print_table(ctx.console, table)


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

        recheck_marker = f" [dim]↩{a['source_action_id']}[/dim]" if a['source_action_id'] else ''
        table.add_row(
            str(a['action_log_id']) + recheck_marker,
            stamp(a['received_time']),
            text(a['action_type']),
            text(a['request_number']),
            _status(a['status']),
            text(a['http_status']),
            text(a['projcode_result']),
            error_cell,
        )

    print_table(ctx.console, table)


def display_action_detail(ctx, payload) -> None:
    """One action in full, with its re-check lineage and optionally its payload."""
    a = payload['action']

    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column('field', style='bold')
    table.add_column('value')

    table.add_row('Action ID', str(a['action_log_id']))
    table.add_row('Received', stamp(a['received_time']))
    if a['processed_time']:
        table.add_row('Processed', stamp(a['processed_time']))
    table.add_row('Status', _status(a['status']))
    table.add_row('HTTP status', text(a['http_status']))
    table.add_row('Action type', text(a['action_type']))
    table.add_row('Request #', text(a['request_number']))
    table.add_row('Result projcode', text(a['projcode_result']))
    table.add_row('Posted by', text(a['remote_actor']))
    if a['processed_by']:
        table.add_row('Re-checked by', text(a['processed_by']))
    if a['source_action_id']:
        table.add_row('Re-check of', f"#{a['source_action_id']}")
    if payload['rechecks']:
        table.add_row('Re-checks', ', '.join(f"#{c['action_log_id']} ({c['status']})"
                                           for c in payload['rechecks']))

    ctx.console.print(Panel(table, title=f"XRAS action #{a['action_log_id']}",
                            border_style='blue'))

    if a['errors']:
        # The blockers, verbatim and in order; the 422 body also carried the ticket
        # line and the pointers (`_refusal_reply`).
        err = Table(show_header=False, box=None, padding=(0, 1, 0, 0))
        err.add_column('n', justify='right', style='dim')
        err.add_column('message', style='red', overflow='fold')
        for i, message in enumerate(a['errors'], start=1):
            err.add_row(str(i), message)
        ctx.console.print(Panel(err, title=f"Errors ({len(a['errors'])})",
                                border_style='red'))

    if a.get('warnings'):
        warn = Table(show_header=False, box=None, padding=(0, 1, 0, 0))
        warn.add_column('n', justify='right', style='dim')
        warn.add_column('message', style='yellow', overflow='fold')
        for i, message in enumerate(a['warnings'], start=1):
            warn.add_row(str(i), message)
        ctx.console.print(Panel(
            warn, title=f"Warnings ({len(a['warnings'])}) — non-fatal",
            border_style='yellow'))

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


def display_recheck_result(ctx, payload) -> None:
    """The verdict, which is the only thing the operator asked for."""
    verdict = {
        'rechecked': ('Would succeed now.', 'green'),
        'failed':    ('Would STILL FAIL.', 'bold red'),
        'manual':    ('Nothing would run for this action.', 'yellow'),
    }.get(payload['status'], ('Re-check complete.', 'green'))
    ctx.console.print(
        f"{verdict[0]}  action #{payload['source_action_id']} → "
        f"recorded as #{payload['new_action_id']} "
        f"({_status(payload['status'])})",
        style=verdict[1],
    )
    if payload['status'] == 'failed':
        ctx.console.print(
            f"  Nothing was applied. "
            f"See `sam-admin xras --show {payload['new_action_id']}` for the reasons.",
            style='dim')


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
        print_table(ctx.console, table)
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
        print_table(ctx.console, table)
        ctx.console.print('[dim]Harmless, but misleading in triage.[/dim]')

    if payload['dangling_keys']:
        ctx.console.print(
            f"[bold red]Dangling keys with no resource row:[/bold red] "
            f"{', '.join(str(k) for k in payload['dangling_keys'])}")

    # the XRAS half
    if not payload.get('live_checked'):
        ctx.console.print(
            '[dim]Local half only — the XRAS API was not configured or not '
            'reachable, so keys XRAS sends that SAM cannot resolve are NOT '
            'checked. Set XRAS_OUTGOING_ENABLED=1 and XRAS_API_KEY for the '
            'two-sided report.[/dim]')
        return

    ctx.console.print(
        f"[bold]{payload['live_key_count']}[/bold] key(s) offered by XRAS")
    if payload['xras_only_keys']:
        ctx.console.print(
            f"[bold red]XRAS sends keys SAM cannot resolve:[/bold red] "
            f"{', '.join(str(k) for k in payload['xras_only_keys'])}")
        ctx.console.print(
            '[dim]This is the failure that breaks an award: the action fails '
            'at runtime with "No resource found in SAM corresponding to key %s". '
            'Add the mapping row before cutover.[/dim]')
    else:
        ctx.console.print(
            '[green]Every key XRAS offers resolves to a SAM resource.[/green]')


def _pair(pair) -> str:
    """A ``(panel, allocation_type)`` tuple as one cell, or the miss marker."""
    return f'{pair[0]} / {pair[1]}' if pair else BLANK


def display_opportunity_report(ctx, payload) -> None:
    """Render the opportunityId map, broken rows first, then what is undecided.

    Order is deliberate and is not "worst group first" in the usual sense: a
    dangling row is the only broken state, but the group an operator is actually
    here for is ``review`` — the opportunities two independent derivations
    disagree about, which is where the silent wrong-projcode failure would come
    from if anyone resolved one by guessing.
    """
    ctx.console.rule('[bold]XRAS opportunity mapping')
    ctx.console.print(
        f"[bold]{payload['mapped']}[/bold] mapping row(s) in "
        f"xras_opportunity_allocation_type")

    if payload['dangling_ids']:
        ctx.console.print(
            f"[bold red]Rows whose allocation type has vanished or has no "
            f"panel:[/bold red] "
            f"{', '.join(str(i) for i in payload['dangling_ids'])}")
        ctx.console.print(
            '[dim]The ingest-side lookup treats these as a miss and falls back to '
            'the free-text ladder, silently. This is the only state this command '
            'exits non-zero on.[/dim]')

    if not payload['live_checked']:
        ctx.console.print(
            '[dim]Local half only — the XRAS API was not configured or not '
            'reachable, so opportunities XRAS is currently offering are NOT '
            'checked. Set XRAS_OUTGOING_ENABLED=1 and XRAS_API_KEY for the '
            'two-sided report.[/dim]')
        return

    ctx.console.print(
        f"[bold]{payload['live_id_count']}[/bold] opportunity(ies) currently open "
        f"in XRAS")

    if not payload['unmapped_ids']:
        ctx.console.print(
            '[green]Every open opportunity resolves through the map.[/green]')
        return

    ctx.console.print(
        f"[bold]{len(payload['unmapped_ids'])}[/bold] of them have no mapping row")
    ctx.console.print(
        '[dim]Not a failure: an unmapped opportunity falls back to the free-text '
        'ladder, exactly as every opportunity did before the map existed. What '
        'follows is whether it could be mapped automatically.[/dim]')

    proposal = payload['proposal']

    if proposal['agree']:
        table = Table(title='Would be mapped automatically (both derivations agree)',
                      title_style='bold')
        table.add_column('Id', justify='right', style='cyan')
        table.add_column('Opportunity', style='dim')
        table.add_column('Panel / type', style='green')
        for entry in proposal['agree']:
            table.add_row(str(entry['opportunity_id']),
                          truncate(text(entry['opportunity_name']), 44),
                          _pair(entry['pair']))
        print_table(ctx.console, table)
        ctx.console.print(
            '[dim]xras_sweep writes these on its next run, newest first, capped by '
            'SAM_TASKS_XRAS_MAP_MAX. Nothing to do.[/dim]')

    if proposal['review']:
        table = Table(title='Withheld — the two derivations disagree',
                      title_style='bold')
        table.add_column('Id', justify='right', style='cyan')
        table.add_column('Opportunity', style='dim')
        table.add_column('XRAS says', style='yellow')
        table.add_column('Ladder says', style='yellow')
        for entry in proposal['review']:
            table.add_row(str(entry['opportunity_id']),
                          truncate(text(entry['opportunity_name']), 36),
                          _pair(entry.get('xras')),
                          _pair(entry.get('ladder')))
        print_table(ctx.console, table)
        ctx.console.print(
            '[dim]A human decides these, as a `source=manual` row. Disagreement is '
            'the rule working: XRAS is not authoritative about SAM, and each known '
            'case changes the FACILITY, which is what reaches next_projcode. A '
            'Wyoming opportunity lands here by construction.[/dim]')

    if proposal['unknown_pair']:
        table = Table(title='Unknown to the reference map — a new allocation product',
                      title_style='bold')
        table.add_column('Id', justify='right', style='cyan')
        table.add_column('Opportunity', style='dim')
        table.add_column('Ladder says', style='yellow')
        for entry in proposal['unknown_pair']:
            table.add_row(str(entry['opportunity_id']),
                          truncate(text(entry['opportunity_name']), 44),
                          _pair(entry.get('ladder')))
        print_table(ctx.console, table)
        ctx.console.print(
            '[dim]XRAS shipped an (allocationTypeId, panelId) pair that '
            'sam/xras/opportunity_types.py does not name. Adding it is a one-line '
            'edit to the constant — a code review, never a silent DB write.[/dim]')


def _stale_note(ctx, payload) -> None:
    """The rows the card header calls hidden: the terminal says so too, or a row just vanishes."""
    stale = payload.get('stale_dropped')
    if stale:
        ctx.console.print(
            f"[dim]{stale} stale push(es) hidden: only an old refused push names them, and "
            "XRAS's current rosters no longer do.[/dim]")


def display_account_worklist(ctx, payload) -> None:
    """Render the account-creation worklist, absent before inactive."""
    counts = payload['counts']
    ctx.console.rule('[bold]XRAS accounts needed')

    if not payload['accounts']:
        ctx.console.print(
            '[green]No accounts are waiting on creation or reactivation.[/green]')
        _stale_note(ctx, payload)
        return

    oldest = counts.get('oldest_days')
    title = f"{counts['total']} account(s) blocking XRAS handoffs"
    if oldest:
        title += f' — oldest waiting {oldest}d'
    table = Table(title=title, title_style='bold')
    table.add_column('Username', style='cyan')
    table.add_column('Needs')
    table.add_column('Role', style='dim')
    table.add_column('Source', style='dim')
    table.add_column('Requests', style='dim')
    table.add_column('XRAS identity')
    table.add_column('Waiting', justify='right')

    for row in payload['accounts']:
        # WARNING: The artifact, not an action — SAM cannot create or reactivate an
        # account. Same words the card uses, because the terminal and the
        # dashboard have to teach one vocabulary; the footer says who does.
        target = row.get('merge_target') or {}
        if row.get('remedy') == 'merge':
            needs = f"[green]merge into {target.get('username')}[/green]"
        else:
            needs = ('[red]new account[/red]' if row['classification'] == 'absent'
                     else '[yellow]reactivation[/yellow]')
            if target.get('username'):
                needs += f" [dim]then merge into {target['username']}[/dim]"
        if row['placeholder']:
            needs += ' [dim](placeholder)[/dim]'
        numbers = [a['request_number'] for a in row['actions'] if a['request_number']]
        # XRAS-side identity state, NOT progress: 9 of 9 rows measured on
        # the local smoke were reconciled and still needed a SAM account.
        # `unidentified` is the harder case — no detail sheet to create from.
        reconciled = {None: BLANK,
                      True: 'identified',
                      False: '[yellow]unidentified[/yellow]'}[row['is_reconciled']]
        waited = row.get('waiting_days')
        table.add_row(row['username'], needs, ', '.join(row['roles']),
                      _sources(row.get('sources')),
                      truncate(', '.join(dict.fromkeys(numbers)), 40), reconciled,
                      BLANK if waited is None else f'{waited}d')

    print_table(ctx.console, table)
    ctx.console.print(
        # "placeholder", NOT "unreconciled" — a placeholder is a username
        # SHAPE, reconciliation is whether XRAS has linked it to a confirmed
        # identity. The smoke found all three placeholders reconciled, so
        # conflating them made this line contradict the table above it.
        f"[dim]{counts['absent']} new account(s), {counts['inactive']} "
        f"reactivation(s), {counts['placeholder']} ARC placeholder "
        f"identities, {counts.get('merge_ready', 0)} ready to merge.[/dim]")
    ctx.console.print(
        # The invariant, said once. There is no INSERT into `users` anywhere in
        # this repo and nothing writes `active`/`locked` — both remedies are
        # somebody else's work, and a list that implies otherwise sends an
        # operator looking for a button that cannot exist.
        '[dim]Accounts are mirrored into SAM from the enterprise directory; '
        'SAM cannot create or reactivate one. Rows clear on the next '
        'sync.[/dim]')

    # WARNING: A subset must never be printed as if it were the whole queue. This is
    # the CLI half of the gap that had `--accounts` reporting 0 while the
    # dashboard showed a real worklist: the card reads the sweep's published
    # snapshot and this only ever read the action log.
    if not payload.get('pending_checked'):
        ctx.console.print(
            '[yellow]Posted actions only — the pending-request worklist could '
            'not be read, so accounts XRAS has approved but not yet sent are '
            'NOT counted here. Set XRAS_OUTGOING_ENABLED=1 with a reachable '
            'cache for the full queue.[/yellow]')
    _stale_note(ctx, payload)

    enrichment = payload.get('enrichment')
    if enrichment and enrichment['unavailable']:
        ctx.console.print(
            '[yellow]Person detail unavailable — the XRAS API could not be '
            'reached. The worklist itself is complete.[/yellow]')
    elif enrichment and enrichment['budget_exhausted']:
        ctx.console.print(
            f"[dim]Person detail fetched for the first "
            f"{enrichment['looked_up']} row(s).[/dim]")
    elif not payload['enriched']:
        ctx.console.print(
            '[dim]Pass --enrich for names, emails and XRAS identity state.[/dim]')


def display_person(ctx, payload) -> None:
    """Render one XRAS person record."""
    ctx.console.rule(f"[bold]XRAS person: {payload['username']}")
    if not payload['found']:
        ctx.console.print(
            f"[yellow]XRAS has no user {payload['username']}.[/yellow]")
        return

    person = payload['person']
    table = Table(show_header=False)
    table.add_column('Field', style='dim')
    table.add_column('Value')
    for label, key in (('Name', None), ('Email', 'email'),
                       ('Phone', 'phone'), ('Organization', 'organization'),
                       ('Academic status', 'academicStatus'),
                       ('Residence country', 'residenceCountry'),
                       ('ORCID', 'orcid')):
        if key is None:
            value = ' '.join(str(person.get(k) or '') for k in
                             ('firstName', 'middleName', 'lastName')).strip()
        else:
            value = person.get(key)
        table.add_row(label, text(value))
    reconciled = person.get('isReconciled')
    table.add_row('Reconciled', 'yes' if reconciled else '[yellow]no[/yellow]')
    print_table(ctx.console, table)
    if not reconciled:
        ctx.console.print(
            '[dim]XRAS has not linked this username to a confirmed identity, so '
            'the detail above may be self-reported and incomplete.[/dim]')


def display_family(ctx, payload) -> None:
    """Render a projcode's request lifecycle as a tree: lines, then their actions."""
    from sam import fmt

    projcode = payload['projcode']
    ctx.console.rule(f"[bold]XRAS request family: {projcode}")
    family = payload.get('family')
    if not family:
        ctx.console.print(f"[yellow]XRAS has no request under {projcode}.[/yellow]")
        return

    def _d(value):
        return fmt.date_str(value, fmt='%Y-%m-%d') if value else BLANK

    pi = family.get('pi') or {}
    ctx.console.print(Panel(
        f"PI: {text(pi.get('name') or pi.get('username'))}   "
        f"span {_d(family['begin_date'])} → {_d(family['end_date'])}   "
        f"last activity {_d(family['activity_date'])}   "
        f"{len(family['requests'])} request line(s)", expand=False))

    tree = Tree(f"[bold]{projcode}")
    for line in family['requests']:
        label = (f"[bold]{line.get('request_type') or '?'}[/bold] "
                 f"· request {line['request_id']} "
                 f"· begin {_d(line.get('begin_date'))}")
        if line.get('status'):
            label += f"  [dim]{line['status']}[/dim]"
        node = tree.add(label)
        for action in line['actions']:
            when = action.get('entry_date') or action.get('submit_date')
            row = f"{action.get('action_type') or '?'} · {_d(when)}"
            if action.get('action_status'):
                row += f"  [dim]{action['action_status']}[/dim]"
            node.add(row)
    ctx.console.print(tree)


def display_vocabulary_report(ctx, payload) -> None:
    """Render the three vocabulary checks — drift first, then the inventories."""
    ctx.console.rule('[bold]XRAS vocabulary')

    for problem in payload['drift']:
        ctx.console.print(f'[bold red]DRIFT[/bold red] {problem}')
    for name in payload['unresolved']:
        ctx.console.print(
            f'[bold red]UNRESOLVED[/bold red] panel-authorized type {name!r} '
            f'has no allocation_type row')
    if not payload['drift'] and not payload['unresolved']:
        ctx.console.print('[green]Every declared value checks out.[/green]')

    roles = payload['role_types']
    table = Table(title='Role types (vocabulary.ROLE_TYPES)', title_style='bold')
    table.add_column('Id', justify='right', style='cyan')
    table.add_column('Name')
    table.add_column('Display', style='dim')
    for row in roles['declared']:
        table.add_row(str(row['type_id']), row['name'], row['display'])
    print_table(ctx.console, table)
    if roles['extra_live']:
        ctx.console.print(
            f"[yellow]Live but undeclared role type(s):[/yellow] "
            f"{', '.join(roles['extra_live'])}")

    panels = payload['panels']
    table = Table(title='XRAS panels (opportunity_types.XRAS_PANEL_NAMES)',
                  title_style='bold')
    table.add_column('Id', justify='right', style='cyan')
    table.add_column('Name')
    for panel_id, name in sorted(panels['declared'].items()):
        table.add_row(str(panel_id), name)
    print_table(ctx.console, table)
    if panels['extra_live']:
        ctx.console.print(
            f"[yellow]Live but undeclared panel(s):[/yellow] "
            f"{', '.join(panels['extra_live'])}")

    authorized = payload['panel_authorized']
    table = Table(title='Panel-authorized allocation types '
                        '(PANEL_AUTHORISED_TYPES)', title_style='bold')
    table.add_column('Type')
    table.add_column('allocation_type_id', justify='right', style='cyan')
    table.add_column('SAM panel', style='dim')
    for row in authorized['types']:
        table.add_row(row['name'], str(row['allocation_type_id']),
                      text(row['panel']))
    print_table(ctx.console, table)
    ctx.console.print(
        f"[dim]Wire panels[] abbr(s) treated as panel-authorized: "
        f"{', '.join(authorized['abbrs'])}[/dim]")

    if not (roles['live_checked'] and panels['live_checked']):
        ctx.console.print(
            '[dim]Local half only — the XRAS API was not configured or not '
            'reachable, so the role-type and panel constants were NOT compared '
            'against the live process. Set XRAS_OUTGOING_ENABLED=1 and '
            'XRAS_API_KEY for the two-sided report.[/dim]')
