"""Reader for the ``acct.<host>.YYYY-MM-DD`` disk usage CSV format.

A daily/weekly per-(user, project) disk usage snapshot. Each row covers
the storage held by one user on one project's directory tree at the
snapshot moment. Three feeds share this format:

  - ``acct.glade.*``  — GPFS Campaign Store (per-user rows)
  - ``acct.quasar.*`` — GPFS Quasar (per-project rollup; username='total')
  - ``acct.desc1.*``  — Lustre Destor   (per-project rollup; username='total')

CSV columns (no header):

  1. activity_date    "2026-04-18"
  2. directory_path   "/gpfs/csfs1/cesm"
  3. projcode         "cesm"          (lowercase in source; SAM stores upper)
  4. username         "gdicker"       (also rejects numeric-only "uid" rows)
  5. number_of_files  "4986"
  6. file_size_total  "688092272"     (KiB; bytes = col6 * 1024)
  7. reporting_int    "7"   OPTIONAL  (legacy; charging now derives the
                                       interval from snapshot tick spacing,
                                       so this column is vestigial)
  8. cos_id           "0"   OPTIONAL  (class-of-service id; not currently
                                       written — disk_cos_id is hardcoded 0)

Columns 7-8 are optional: the GPFS feeds emit all 8 (always "7","0" across
every observed row), the Lustre/Destor feed ships only the first 6. When
absent they default to 7 and 0 respectively (matching DiskUsageEntry's
defaults); when present they are parsed but their values don't affect
charging.

Verified 2026-04-27 against an existing disk_charge_summary row: the
ratio (DB.bytes / col6) is exactly 1024, confirming KiB units. Bytes
is the on-disk physical occupancy (matches GPFS mmlsquota usage).
"""

import csv
import os
import re
from datetime import date, datetime
from typing import Optional

from .base import DiskUsageEntry, DiskUsageReader


# Glade snapshots historically include lines where the username column
# is the literal numeric uid (the OS resolved the file owner to a uid
# that no longer maps to a username). We skip these rather than try to
# resolve — the bytes are still attributed to the project, but on the
# `<unidentified>` reconciliation path if --reconcile-quota-gap is set.
_NUMERIC_USERNAME_RE = re.compile(r'^\d+$')

# Service / nobody accounts that are never real users in SAM.
_SKIP_USERNAMES = frozenset({
    'gpfsnobody',
    'nobody',
    'root',
})

# Filename pattern: acct.<host>.YYYY-MM-DD (e.g. acct.glade.2026-04-18).
_FILENAME_DATE_RE = re.compile(r'\.(\d{4}-\d{2}-\d{2})(?:\.|$)')


def _parse_filename_date(path: str) -> Optional[date]:
    """Best-effort: extract YYYY-MM-DD from the filename."""
    base = os.path.basename(path)
    m = _FILENAME_DATE_RE.search(base)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


class GladeCsvReader(DiskUsageReader):
    """Parse the ``acct.<host>.YYYY-MM-DD`` per-(user, project) usage CSV.

    Handles all three feeds (Campaign_Store, Quasar, Destor). The trailing
    ``reporting_interval`` and ``cos_id`` columns are optional: the GPFS
    feeds carry 8 columns, the Lustre/Destor feed carries 6. Rows with
    fewer than 6 columns are skipped.
    """

    # KiB → bytes
    _KIB = 1024

    # First 6 columns are mandatory; columns 7-8 (reporting_interval, cos)
    # are optional and default to '7'/'0' when absent.
    _MIN_COLS = 6

    def read(self) -> list[DiskUsageEntry]:
        entries: list[DiskUsageEntry] = []
        snapshot_dates: set[date] = set()

        with open(self.path, newline='') as fh:
            reader = csv.reader(fh)
            for row in reader:
                if not row or len(row) < self._MIN_COLS:
                    continue

                date_s, dir_path, projcode, username, nfiles, fsize_kib = row[:6]
                # Columns 7-8 are legacy/vestigial — default when missing.
                interval = row[6] if len(row) > 6 else '7'
                cos = row[7] if len(row) > 7 else '0'

                # Filter rows we cannot meaningfully attribute to a real user.
                if username in _SKIP_USERNAMES:
                    continue
                if _NUMERIC_USERNAME_RE.match(username):
                    continue

                try:
                    activity_date = date.fromisoformat(date_s)
                    n_files = int(nfiles)
                    b_kib = int(fsize_kib)
                    rep_int = int(interval)
                    cos_id = int(cos)
                except ValueError:
                    # Malformed row — skip silently; --skip-errors at the CLI
                    # layer governs strictness for resolution failures, not
                    # parse errors.
                    continue

                snapshot_dates.add(activity_date)

                entries.append(DiskUsageEntry(
                    activity_date=activity_date,
                    projcode=projcode.upper().strip(),
                    username=username.strip(),
                    number_of_files=n_files,
                    bytes=b_kib * self._KIB,
                    directory_path=dir_path.strip() or None,
                    reporting_interval=rep_int,
                    cos=cos_id,
                ))

        # Snapshot date: prefer the date in the rows; fall back to filename.
        if len(snapshot_dates) == 1:
            self.snapshot_date = next(iter(snapshot_dates))
        elif snapshot_dates:
            # File mixes multiple dates — pick the most recent and let the
            # importer's window check decide whether to allow that.
            self.snapshot_date = max(snapshot_dates)
        else:
            self.snapshot_date = _parse_filename_date(self.path)

        return entries
