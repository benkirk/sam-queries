"""Quota reader for hdig's weekly ``acct.<fs>.YYYY-MM-DD`` usage reports (quota8 layout).

Each row repeats its fileset's quota, so a path's quota is read once and its usage and
file counts are summed over every row, system accounts included. The file lists only
filesets that hold data: an empty fileset with a quota is absent, so the reconcile
reports its allocation as having no quota (``--verify-paths`` shows the path exists).
"""
import csv
import posixpath
from datetime import datetime, time

from cli.accounting.disk_usage.glade_csv import QUOTA8, detect_layout, lenient_int
from .base import QuotaReader, QuotaEntry


class AcctQuotaReader(QuotaReader):
    """Read per-path quotas from an acct CSV; KiB in the file, bytes in ``QuotaEntry``."""

    _KIB = 1024
    mount_hosts = ['casper', 'derecho']

    def read(self) -> list[QuotaEntry]:
        with open(self.path, newline='') as fh:
            rows = [r for r in csv.reader(fh) if len(r) >= 6]
        if any(len(r) != 8 for r in rows) or (rows and detect_layout(rows, self.path) != QUOTA8):
            raise ValueError(f"{self.path} has no quota columns (needs the quota8 acct layout)")

        by_path: dict[str, dict] = {}
        dates = set()
        for date_s, path, label, _user, nfiles, _file_quota, kib, quota_kib in rows:
            try:
                dates.add(datetime.fromisoformat(date_s).date())
            except ValueError:
                continue
            fs = by_path.setdefault(path, {'label': label, 'limit': 0, 'usage': 0, 'files': 0})
            fs['limit'] = max(fs['limit'], lenient_int(quota_kib) or 0)
            fs['usage'] += lenient_int(kib) or 0
            fs['files'] += lenient_int(nfiles) or 0

        if dates:
            self.snapshot_date = datetime.combine(max(dates), time())
        if by_path:
            parts = posixpath.commonpath(list(by_path)).split('/')
            self.mount_root = '/'.join(parts[:3]) or None
        return [
            QuotaEntry(
                fileset_name=posixpath.basename(path.rstrip('/')) or path,
                path=path,
                limit_bytes=fs['limit'] * self._KIB,
                usage_bytes=fs['usage'] * self._KIB,
                file_count=fs['files'],
                label=fs['label'],
            )
            for path, fs in by_path.items() if fs['limit'] > 0
        ]
