#!/usr/bin/env python3
"""Measure how long refused XRAS posts wait for the re-post that applies them.

Read-only: SELECTs on xras_action_log, xras_remediation_event, account_request, users,
contract, external_ticket and notification_log; the session is rolled back. Connects
with SAM_DB_* (as create_sam_engine does). Definitions: src/sam/queries/xras_repost_wait.py.

    python scripts/xras_repost_wait.py --days 180
    python scripts/xras_repost_wait.py --since 2026-09-01 --json --episodes
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

PHASES = (('a', 'refused -> cleared'), ('b', 'cleared -> applied'), ('c', 'total'))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--days', type=int, default=180,
                   help='Window: first refusals in the last N days (default 180).')
    p.add_argument('--since', type=datetime.fromisoformat,
                   help='Window start (ISO, naive Mountain); overrides --days.')
    p.add_argument('--until', type=datetime.fromisoformat,
                   help='Window end and the clock for ages (default now).')
    p.add_argument('--json', action='store_true', help='Print the report as JSON.')
    p.add_argument('--episodes', action='store_true',
                   help='Include every episode (JSON) or list them (table).')
    return p.parse_args(argv)


def _fmt(value):
    return '-' if value is None else f'{value:.1f}'


def _rows(title, groups):
    print(f'\n{title}  (hours; a = refused -> cleared, b = cleared -> applied, c = total)')
    head = ''.join(f"{k + ' n':>6}{'median':>8}{'p90':>8}{'max':>8}" for k, _ in PHASES)
    print(f"  {'':<12}{'eps':>5}{'applied':>8}{'waiting':>8}{head}")
    for name, g in groups.items():
        cells = ''.join(f"{g[k]['n']:>6}{_fmt(g[k]['median_h']):>8}{_fmt(g[k]['p90_h']):>8}"
                        f"{_fmt(g[k]['max_h']):>8}" for k, _ in PHASES)
        print(f"  {name:<12}{g['episodes']:>5}{g['applied']:>8}{g['waiting']:>8}{cells}")


def print_table(report, *, episodes=False):
    print(f"XRAS re-post wait, first refusals {report['since']} .. {report['until']}")
    _rows('Overall', {'all': report['overall']})
    _rows('By action type', report['by_action_type'])
    _rows('By blocker (an episode counts under each kind it had)', report['by_blocker'])
    w, t = report['waiting'], report['ticket']
    print(f"\nStill waiting: {w['count']} (age median {_fmt(w['age']['median_h'])} h, "
          f"max {_fmt(w['age']['max_h'])} h); cleared in SAM but not reposted: "
          f"{w['cleared_not_reposted']} (median {_fmt(w['since_cleared']['median_h'])} h since)")
    print(f"Linked to the applied post by: {report['linked_by']}")
    print(f"NUSD tickets opened: {t['opened']}; 'repost it' comments: {t['repost_asked']}; "
          f"comment -> applied median {_fmt(t['asked_to_applied']['median_h'])} h")
    if w['oldest']:
        print('\nOldest waiting:')
        for e in w['oldest']:
            print(f"  {e['request_number']:<14} {e['action_type']:<10} since "
                  f"{e['first_refused']}  {e['refusals']}x  "
                  f"{','.join(b['kind'] for b in e['blockers'])}  {e['ticket_key'] or ''}")
    if episodes:
        print('\nEpisodes:')
        for e in report.get('episodes', ()):
            hrs = e['hours']
            print(f"  {e['request_number']:<14} {e['action_type']:<10} {e['first_refused']}  "
                  f"a={_fmt(hrs['a'])} b={_fmt(hrs['b'])} c={_fmt(hrs['c'])} "
                  f"age={_fmt(hrs['age'])}  {e['linked_by'] or 'waiting'}")


def main(argv=None):
    args = parse_args(argv)
    from sqlalchemy.orm import Session

    from sam.queries.xras_repost_wait import repost_wait_report
    from sam.session import create_sam_engine

    until = args.until or datetime.now()
    since = args.since or until - timedelta(days=args.days)
    engine, _ = create_sam_engine()
    with Session(engine) as session:
        try:
            report = repost_wait_report(session, since=since, until=until,
                                        include_episodes=args.episodes)
        finally:
            session.rollback()
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_table(report, episodes=args.episodes)
    return 0


if __name__ == '__main__':
    sys.exit(main())
