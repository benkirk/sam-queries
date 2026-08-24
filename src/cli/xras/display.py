"""Rich renderers for ``sam-admin xras``.

Stateless module-level functions taking ``(ctx, payload)`` where ``payload`` is the
plain dict a builder produced — never an ORM object. All formatting goes through
``sam.fmt`` / ``cli.core.display_utils``; no ``strftime``, no ``'{:,}'.format``.
"""

from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from cli.core.display_utils import BLANK, text, truncate

#: Rich styles per action status. Deliberately the same semantics as the web
#: badges (dashboards/fragments/badges.html) so an operator reading the terminal
#: and an operator reading the dashboard learn one vocabulary, not two.
_STATUS_STYLE = {
    'received':  'cyan',
    'processed': 'green',
    'manual':    'yellow',
    'failed':    'red',
    'rechecked':  'dim',
}

#: Short Source labels, same vocabulary as the Pending Users card badges.
_SOURCE_LABELS = {'action_log': 'push', 'reports': 'pending'}


def _sources(values) -> str:
    """A row's provenance, received-push first."""
    labels = [_SOURCE_LABELS.get(v, v) for v in values or ()]
    labels.sort(key=lambda s: s != 'push')
    return ', '.join(labels) or BLANK


def _status(value) -> str:
    style = _STATUS_STYLE.get(value, 'white')
    return f'[{style}]{text(value)}[/{style}]'


def _timestamp(value) -> str:
    """Actions arrive in bursts, so the time of day is what separates rows."""
    from sam import fmt
    return BLANK if value is None else fmt.date_str(value, fmt='%Y-%m-%d %H:%M:%S')


_READINESS_STYLE = {'failed': '[red]would fail[/red]',
                    'manual': '[yellow]would park[/yellow]',
                    'incomplete': '[dim]incomplete[/dim]',
                    'rechecked': '[green]would land[/green]',
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
    ctx.console.print(table)


def display_mnemonic_report(ctx, payload) -> None:
    """The orgs/institutions to link, ranked by how many failing pushes each unblocks."""
    targets = payload['targets']
    if not targets and not payload['unresolved']:
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
        ctx.console.print(table)
    if payload['unresolved']:
        # Not fixable by minting a code — these PIs have no current affiliation.
        pis = sorted({u['pi'] for u in payload['unresolved'] if u['pi']})
        ctx.console.print(
            f"[yellow]{len(payload['unresolved'])} action(s) blocked by "
            f"{len(pis)} PI(s) with no current affiliation[/yellow] "
            f"(need a user_organization row, not a mnemonic): "
            f"{', '.join(pis) or '—'}")


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
    """One action in full, with its re-check lineage and optionally its payload."""
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
    if a['source_action_id']:
        table.add_row('Replay of', f"#{a['source_action_id']}")
    if payload['rechecks']:
        table.add_row('Replays', ', '.join(f"#{c['action_log_id']} ({c['status']})"
                                           for c in payload['rechecks']))

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
        ctx.console.print(table)
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
        ctx.console.print(table)
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
        ctx.console.print(table)
        ctx.console.print(
            '[dim]XRAS shipped an (allocationTypeId, panelId) pair that '
            'sam/xras/opportunity_types.py does not name. Adding it is a one-line '
            'edit to the constant — a code review, never a silent DB write.[/dim]')


def display_account_worklist(ctx, payload) -> None:
    """Render the account-creation worklist, absent before inactive."""
    counts = payload['counts']
    ctx.console.rule('[bold]XRAS accounts needed')

    if not payload['accounts']:
        ctx.console.print(
            '[green]No accounts are waiting on creation or reactivation.[/green]')
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
        needs = ('[red]new account[/red]' if row['classification'] == 'absent'
                 else '[yellow]reactivation[/yellow]')
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

    ctx.console.print(table)
    ctx.console.print(
        # "placeholder", NOT "unreconciled" — a placeholder is a username
        # SHAPE, reconciliation is whether XRAS has linked it to a confirmed
        # identity. The smoke found all three placeholders reconciled, so
        # conflating them made this line contradict the table above it.
        f"[dim]{counts['absent']} new account(s), {counts['inactive']} "
        f"reactivation(s), {counts['placeholder']} ARC placeholder "
        f"identities.[/dim]")
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
    ctx.console.print(table)
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
