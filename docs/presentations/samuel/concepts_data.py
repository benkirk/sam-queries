#!/usr/bin/env python3
"""Freeze Part 2's (Concepts) real-data fragments from the obfuscated test DB.

Writes _out_accounts.qmd, _out_users.qmd, _out_replay.qmd, _out_audit.qmd and the SAM-side
trees _tree_sam_award.qmd / _tree_sam_pool.qmd. Host port 3307 only, never 3306: only the
project SCSG0001 (Ben's own) and projcodes are read; obfuscated users render as user_xxxxxxxx.
Run by refresh_data.sh with the sam-queries Python; SAMUEL_REPO locates the models and CLIs.
"""
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

repo = Path(os.environ.get('SAMUEL_REPO', Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(repo / 'src'))

url = make_url(os.environ.get('SAM_TEST_DB_URL', 'mysql+pymysql://root:root@127.0.0.1:3307/sam'))
if url.host not in ('127.0.0.1', 'localhost') or url.port != 3307:
    sys.exit(f'concepts_data: refusing {url.host}:{url.port}; only the obfuscated test DB (3307)')

from sam import Allocation, Project  # noqa: E402
from sam import fmt  # noqa: E402
from sam.accounting.allocations import replay_amount  # noqa: E402

HERE = Path(__file__).resolve().parent
PROJCODE, RESOURCE, LEDGER_ALLOCATION = 'SCSG0001', 'Derecho', 21275
AWARD_ROOT, POOL_ROOT, POOL_RESOURCE = 'CESM0002', 'NMMM0003', 'Casper'  # Derecho's pool has no unequal detach

# The CLIs read SAM_DB_*; explicit values win over the .env they re-load.
CLI_ENV = {**os.environ, 'COLUMNS': '80', 'SAM_DB_DRIVER': 'mysql', 'SAM_DB_SERVER': url.host,
           'SAM_DB_PORT': str(url.port), 'SAM_DB_USERNAME': url.username,
           'SAM_DB_PASSWORD': url.password, 'SAM_DB_NAME': url.database}


def cli(name, *args, ok=(0,)):
    exe = repo / 'conda-env/bin' / name
    run = subprocess.run([str(exe), *args], env=CLI_ENV, capture_output=True, text=True)
    if run.returncode not in ok:
        sys.exit(f'concepts_data: {name} exited {run.returncode}: {run.stderr.strip()}')
    out = run.stdout
    return out.encode('ascii', 'ignore').decode()   # drop emoji; box-drawing survives below


def write(name, body):
    (HERE / name).write_text(body)
    print(f'wrote {name}', file=sys.stderr)


def fenced(text):
    return f'```text\n{text.rstrip()}\n```\n'


def accounts(session):
    project = Project.get_by_projcode(session, PROJCODE)
    usage = project.get_detailed_allocation_usage()
    rows = sorted(usage.items(), key=lambda kv: (kv[1]['resource_type'], kv[0]))
    lines = ['| Account (resource) | Type | Allocated | % used |', '|---|---|--:|--:|']
    for name, u in rows:
        lines.append(f"| {name} | {u['resource_type']} | {fmt.number(u['allocated'])} "
                     f"| {fmt.pct(u['percent_used'])} |")
    return '\n'.join(lines) + '\n'


def users():
    out = cli('sam-search', 'project', PROJCODE, '--list-users')
    lines = out.splitlines()
    start = next(i for i, ln in enumerate(lines) if 'Active users for' in ln)
    table = [ln.rstrip() for ln in lines[start:] if ln.strip()]
    keep = table[:3 + 7]   # title, header, rule, then 7 users
    if len(table) > len(keep):
        keep.append(f'  ...    ({len(table) - len(keep)} more)')
    return fenced('\n'.join(keep))


def nowrap(text):
    return f'[{text}]{{style="white-space: nowrap"}}'  # .fill must not break dates at hyphens


def replay(session):
    alloc = session.get(Allocation, LEDGER_ALLOCATION)
    txns = sorted(alloc.transactions, key=lambda t: (t.creation_time, t.allocation_transaction_id))
    lines = ['| Date | Type | Amount | Note | Replay |',
             '|--------------|------------|--------------:|----------------|-------------:|']
    for t in txns:
        amount = '—' if t.transaction_amount is None else f'{t.transaction_amount:+,.0f}'
        if t.transaction_type == 'NEW':
            amount = f'{t.transaction_amount:,.0f}'
        note = (t.transaction_comment or '').strip()
        if t.transaction_type == 'EXTENSION':
            amount, note = '—', nowrap(f'to {t.alloc_end_date:%Y-%m-%d}')
        if t.transaction_type == 'NEW':
            note = note or nowrap(f'to {t.alloc_end_date:%Y-%m-%d}')

        total = replay_amount(txns, until=t.creation_time)
        lines.append(f'| {nowrap(f"{t.creation_time:%Y-%m-%d}")} | {t.transaction_type} | {amount} '
                     f'| {note} | {total:,.0f} |')
    lines.append(f'| | **amount** | | | **{alloc.amount:,.0f}** |')
    assert abs(replay_amount(txns) - float(alloc.amount)) < 0.5, 'replay != amount'
    return '\n'.join(lines) + '\n'


def audit():
    out = cli('sam-admin', 'project', '--audit-trees', '--resource', RESOURCE, ok=(0, 2))  # 2: found
    keep = [ln.rstrip() for ln in out.splitlines()
            if ln.strip() and not ln.startswith('Auditing')]
    return fenced('\n'.join(keep))


def current_allocation(project, resource=RESOURCE):
    now = datetime.now()
    for account in project.accounts:
        if account.resource.resource_name != resource:
            continue
        for a in account.allocations:
            if not a.deleted and a.start_date <= now and (a.end_date is None or a.end_date >= now):
                return a
    return None


def tree(session, root_code, *, max_children, include=(), depth=2, resource=RESOURCE):
    """A mermaid graph of one allocation tree in SAM amounts, styled like sam_and_pbs."""
    root = Project.get_by_projcode(session, root_code)
    nodes, edges, classes = [], [], {'root': [], 'own': [], 'linked': [], 'detached': [],
                                     'elided': []}

    pool = any((a := current_allocation(k, resource)) is not None and a.parent_allocation_id
               for k in root.children)

    def walk(project, level):
        alloc = current_allocation(project, resource)
        node, amount = project.projcode, fmt.number(float(alloc.amount))
        if project is root:
            kind, note = 'root', ''
        elif alloc.parent_allocation_id:
            kind, note = 'linked', ', shared'
        elif pool:
            kind, note = 'detached', ', detached'
        else:
            kind, note = 'own', ''
        nodes.append(f'    {node}["{node}<br/>{amount}{note}"]')
        classes[kind].append(node)
        if level >= depth:
            return
        kids = [(k, current_allocation(k, resource)) for k in project.children]
        kids = [(k, a) for k, a in kids if a is not None]
        kids.sort(key=lambda ka: (-(ka[0].projcode in include), -float(ka[1].amount),
                                  ka[0].projcode))
        shown = kids[:max_children]
        for kid, _ in shown:
            edges.append(f'    {node} --> {kid.projcode}')
            walk(kid, level + 1)
        if len(kids) > len(shown):
            more = f'{node}_more'
            nodes.append(f'    {more}(["(+{len(kids) - len(shown)} more)"])')
            edges.append(f'    {node} --> {more}')
            classes['elided'].append(more)

    walk(root, 0)
    style = {
        'root': 'fill:#cfe2f3,stroke:#1f4e79,stroke-width:2px',
        'own': 'fill:#ffffff,stroke:#1f4e79',
        'linked': 'fill:#fff2cc,stroke:#bf9000',
        'detached': 'fill:#fde2e1,stroke:#c0392b,stroke-dasharray:5 3',
        'elided': 'fill:#f3f3f3,stroke:#999999,stroke-dasharray:4 3,color:#666666',
    }
    out = ['```{mermaid}', '%%| fig-width: 6', 'graph TD', *nodes, *edges]
    out += [f'    classDef {c} {s}' for c, s in style.items() if classes[c]]
    out += [f'    class {",".join(v)} {c}' for c, v in classes.items() if v]
    return '\n'.join(out) + '\n```\n'


with Session(create_engine(url)) as session:
    write('_out_accounts.qmd', accounts(session))
    write('_out_replay.qmd', replay(session))
    write('_tree_sam_award.qmd', tree(session, AWARD_ROOT, max_children=5, include=('CESM0028',)))
    write('_tree_sam_pool.qmd', tree(session, POOL_ROOT, max_children=4, include=('NMMM0080',),
                                     depth=1, resource=POOL_RESOURCE))
write('_out_users.qmd', users())
write('_out_audit.qmd', audit())
for name in ('_out_accounts.qmd', '_out_users.qmd', '_out_replay.qmd', '_out_audit.qmd'):
    if re.search(r'@(?!ucar\.edu)', (HERE / name).read_text()):
        sys.exit(f'concepts_data: {name} carries an email address; check before committing')
