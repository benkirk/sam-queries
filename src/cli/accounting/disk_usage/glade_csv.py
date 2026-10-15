"""Reader for the ``acct.<host>.YYYY-MM-DD`` disk usage CSV format.

Feeds: ``acct.glade.*`` (per-user rows), ``acct.quasar.*``, ``acct.desc1.*`` (per-project,
username='total'). No header. Columns 1-5 are always activity_date, directory_path,
projcode (lowercase label), username, number_of_files; the rest depends on the layout.
WARNING: legacy8 and quota8 are both 8 columns; only the ``"7","0"`` tail tells them apart.

Usage and quota sizes are **KiB**, not bytes (verified 2026-04-27 against
disk_charge_summary). Quotas are per fileset, repeated on each user row: never sum them.
"""

import csv
import os
import re
from datetime import date
from typing import Optional

from .base import DiskUsageEntry, DiskUsageReader


# Glade snapshots historically include lines where the username column
# is the literal numeric uid (the OS resolved the file owner to a uid
# that no longer maps to a username). We skip these rather than try to
# resolve — the bytes are still attributed to the project, but on the
# `<unidentified>` reconciliation path if --reconcile-quota-gap is set.
_NUMERIC_USERNAME_RE = re.compile(r'^\d+$')

# OS service accounts that own files but are never SAM users; their rows are
# dropped before charging. Seeded from the prod acct.* logs, 2026-09-30.
SYSTEM_USERNAMES = frozenset({
    'bin', 'chrony', 'daemon', 'gpfsnobody', 'lldpd', 'lp', 'mail',
    'nfsnobody', 'nobody', 'ntp', 'polkitd', 'postfix', 'root', 'rpc',
    'rpcuser', 'shutdown', 'sshd', 'sssd', 'tcpdump', 'telegraf', 'tss',
})
_SYSTEM_USERNAME_PREFIXES = ('systemd-',)


def is_system_account(username: str) -> bool:
    return username in SYSTEM_USERNAMES or username.startswith(_SYSTEM_USERNAME_PREFIXES)

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


LEGACY6 = 'legacy6'  # ..., KiB (desc1 before 2026-10)
LEGACY8 = 'legacy8'  # ..., KiB, reporting_interval "7", cos "0" (glade, quasar before 2026-10)
QUOTA8 = 'quota8'    # ..., file quota, KiB, KiB quota (all feeds from 2026-10)

_LEGACY8_TAIL = ['7', '0']


def _detect_layout(rows: list[list[str]], path: str) -> str:
    """One layout for the file's 8-column rows; raises on any width other than 6 or 8."""
    widths = {len(r) for r in rows}
    unknown = widths - {6, 8}
    if unknown:
        raise ValueError(
            f"unrecognized column count(s) {sorted(unknown)} in {path}; expected 6 or 8"
        )
    eight = [r for r in rows if len(r) == 8]
    if eight and all(r[6:8] == _LEGACY8_TAIL for r in eight):
        return LEGACY8
    return QUOTA8


def _lenient_int(value: Optional[str]) -> Optional[int]:
    """Quota columns only: ``-`` or blank means no quota, and must never drop the usage row."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class GladeCsvReader(DiskUsageReader):
    """Parse the ``acct.<host>.YYYY-MM-DD`` per-(user, project) usage CSV for all three feeds.

    Rows with fewer than 6 columns are skipped. ``layout`` records what ``read()`` found.
    """

    # KiB -> bytes
    _KIB = 1024

    _MIN_COLS = 6

    def read(self) -> list[DiskUsageEntry]:
        entries: list[DiskUsageEntry] = []
        snapshot_dates: set[date] = set()
        self.skipped_system = {}

        with open(self.path, newline='') as fh:
            rows = [r for r in csv.reader(fh) if len(r) >= self._MIN_COLS]

        # Decided per file, not per row: a quota8 row with 7 KiB on a zero-quota
        # fileset carries the legacy "7","0" tail.
        eight_layout = _detect_layout(rows, self.path)
        layouts = {LEGACY6 if len(r) == 6 else eight_layout for r in rows}
        self.layout = '+'.join(sorted(layouts)) or None

        for row in rows:
            date_s, dir_path, projcode, username, nfiles = row[:5]
            interval, cos, file_quota, quota_kib = '7', '0', None, None
            if len(row) == 6:
                fsize_kib = row[5]
            elif eight_layout == LEGACY8:
                fsize_kib, interval, cos = row[5:8]
            else:
                file_quota, fsize_kib, quota_kib = row[5:8]

            # Filter rows we cannot meaningfully attribute to a real user.
            if is_system_account(username):
                self.skipped_system[username] = self.skipped_system.get(username, 0) + 1
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
                # Malformed row: skip silently. --skip-errors governs resolution
                # failures, not parse errors.
                continue

            snapshot_dates.add(activity_date)
            q_kib = _lenient_int(quota_kib)

            entries.append(DiskUsageEntry(
                activity_date=activity_date,
                projcode=projcode.upper().strip(),
                username=username.strip(),
                number_of_files=n_files,
                bytes=b_kib * self._KIB,
                directory_path=dir_path.strip() or None,
                reporting_interval=rep_int,
                cos=cos_id,
                file_quota=_lenient_int(file_quota),
                quota_bytes=None if q_kib is None else q_kib * self._KIB,
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
