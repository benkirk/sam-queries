"""`python -m logarchive run | cat | status`."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from logarchive import read, store

DEFAULT_ROOT = '/archive'


def _run(args) -> int:
    from logarchive.follow import Archiver
    from logarchive.kube import KubeClient

    apps = [a.strip() for a in os.environ.get('LOGARCHIVE_APPS', '').split(',') if a.strip()]
    if not apps:
        print('LOGARCHIVE_APPS is empty: name the app labels to archive', file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    days = float(os.environ.get('LOGARCHIVE_RETENTION_DAYS', '90'))
    archiver = Archiver(KubeClient.from_cluster(), args.root, apps, days,
                        self_pod=os.environ.get('HOSTNAME'))
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    logging.getLogger('logarchive').info(
        'archiving app=%s to %s, retention %s days', ','.join(apps), args.root, days)
    archiver.run(stop)
    return 0


def _cat(args) -> int:
    since = read.parse_when(args.since) if args.since else None
    until = read.parse_when(args.until) if args.until else None
    out = sys.stdout.buffer
    try:
        for stamp, line in read.iter_lines(args.root, args.app, since, until):
            text = f'{stamp} {line}\n' if args.timestamps else f'{line}\n'
            out.write(text.encode('utf-8', errors='replace'))
        out.flush()
    except BrokenPipeError:
        # The reader (grep -m, a pager) closed first; not an error.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return 0


def _status(args) -> int:
    stale = False
    if args.max_age is not None:
        age = store.heartbeat_age(args.root)
        stale = age is None or age > args.max_age
        if not args.json:
            # The liveness probe's path: one stat, no walk of the archive.
            print(f'heartbeat: {"never" if age is None else f"{age:.0f}s"}'
                  f' ({"STALE" if stale else "ok"}, max {args.max_age:.0f}s)')
            return 1 if stale else 0
    info = store.status(args.root)
    if args.json:
        print(json.dumps(info))
    else:
        largest = info['largest_live']
        print(f"files: {info['files']}  bytes: {info['bytes']}  full: {info['percent_full']}%"
              f"  newest write: {info['newest_write_age']}s  oldest: {info['oldest']}"
              f"  heartbeat: {info['heartbeat_age']}s"
              + (f"  largest live: {largest['name']} ({largest['bytes']})" if largest else ''))
    return 1 if stale else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='python -m logarchive', description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(os.environ.get('LOGARCHIVE_ROOT', DEFAULT_ROOT)),
                        help='archive directory (default: $LOGARCHIVE_ROOT or /archive)')
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('run', help='follow the pods named by LOGARCHIVE_APPS').set_defaults(func=_run)

    cat = sub.add_parser('cat', help='print archived lines in time order, as kubectl logs would')
    cat.add_argument('--app', action='append', help='app label; repeatable (default: every app)')
    cat.add_argument('--since', help='e.g. 48h, 7d, or an RFC3339 time')
    cat.add_argument('--until', help='as --since (default: now)')
    cat.add_argument('--timestamps', action='store_true', help='keep the leading timestamp')
    cat.set_defaults(func=_cat)

    status = sub.add_parser('status', help='size, age and fullness of the archive')
    status.add_argument('--json', action='store_true')
    status.add_argument('--max-age', type=float, metavar='SECONDS',
                        help='exit 1 when the heartbeat is older; alone, checks only that')
    status.set_defaults(func=_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
