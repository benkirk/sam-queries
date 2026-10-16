"""The CLI shape of a Directory view (``sam.manage.directory_panel``): JSON-safe dicts.

The envelope key is absent when no directory is configured, ``available: false`` when it
could not be read; the record is a plain dict. Record: docs/plans/DIRECTORY_CLIENT_NEXT.md.
"""

import dataclasses
from typing import Optional


def directory_view(panel: dict) -> Optional[dict]:
    """``None`` when unconfigured, else the panel with its record flattened for JSON."""
    if not panel['configured']:
        return None
    out = {k: v for k, v in panel.items() if k not in ('record', 'result', 'configured')}
    out['record'] = dataclasses.asdict(panel['record']) if panel['record'] is not None else None
    return out


def reconcile_view(result, dry_run: bool) -> dict:
    """A ``ReconcileResult`` as applied and withheld rows plus the log fragment.

    Column values are left as the compare recorded them (ids, dates, flags, usernames);
    names, email addresses and phone numbers are counted, never valued, as in the log.
    """
    return {
        'found': result.report.found_in_directory,
        'dry_run': dry_run,
        'applied': [dataclasses.asdict(c) for c in result.applied],
        'withheld': [dataclasses.asdict(d) for d in result.withheld],
        'fragment': result.fragment(),
    }
