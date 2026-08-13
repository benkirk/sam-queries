#!/usr/bin/env python3
"""
Clean up old system status data — a hand-run wrapper around the retention policy.

The policy itself lives in `src/system_status/retention.py` and is shared with
the `cleanup_status_snapshots` scheduled task, so a manual prune and the nightly
one cannot disagree. This file owns no defaults: `--retention-days` falls back to
`retention.DEFAULT_RETENTION_DAYS`.

Nothing here is scheduled. Routine pruning is the dispatcher's job
(`sam-admin tasks`, docs/plans/SCHEDULED_TASKS.md); this script exists because
running a prune by hand — against a specific cutoff, or with --dry-run to see
what a window would take — is legitimate and occasionally necessary.

Usage:
    python scripts/cleanup_status_data.py [--retention-days N] [--dry-run]
"""

import argparse
import logging
import sys
from pathlib import Path

# Add python directory to path
python_dir = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(python_dir))

from system_status.retention import (        # noqa: E402  (after sys.path)
    DEFAULT_RETENTION_DAYS,
    cleanup_old_data,
)


def build_parser() -> argparse.ArgumentParser:
    """Split out so a test can assert this consumer's default is the policy's."""
    parser = argparse.ArgumentParser(
        description='Clean up old system status snapshot data')
    parser.add_argument('--retention-days', type=int,
                        default=DEFAULT_RETENTION_DAYS,
                        help=f'Number of days to retain '
                             f'(default: {DEFAULT_RETENTION_DAYS})')
    parser.add_argument('--dry-run', action='store_true',
                        help='Count records without deleting')
    parser.add_argument('--chunk-size', type=int, default=None,
                        help='Rows per delete batch (default: policy default)')
    return parser


def main() -> int:
    args = build_parser().parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    kwargs = {'retention_days': args.retention_days, 'dry_run': args.dry_run}
    if args.chunk_size is not None:
        kwargs['chunk_size'] = args.chunk_size

    try:
        counts = cleanup_old_data(**kwargs)
    except Exception as e:
        print(f'\nERROR: Cleanup failed: {e}', file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

    total = sum(counts.values())
    verb = 'would be deleted' if args.dry_run else 'deleted'
    print(f'\n{total:,} rows {verb}')
    for table_name, count in counts.items():
        if count:
            print(f'  {table_name}: {count:,}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
