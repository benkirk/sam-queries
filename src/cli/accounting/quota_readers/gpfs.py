"""GPFS quota reader — parses cs_usage.json produced for Campaign Store."""

import json
from datetime import datetime

from .base import QuotaReader, QuotaEntry


def _parse_snapshot_date(s: str) -> "datetime | None":
    """Best-effort parse for cs_usage.json's `date` field.

    Observed format: ``"Fri Apr 24 07:05:10 MDT 2026"`` — not a standard
    ISO/RFC shape. Tries a couple of likely patterns and returns ``None``
    on failure rather than raising.
    """
    if not s:
        return None
    # Strip the timezone abbreviation before strptime (%Z is unreliable).
    parts = s.split()
    if len(parts) == 6:
        stripped = " ".join(parts[:4] + parts[5:])   # drop the TZ token
        try:
            return datetime.strptime(stripped, "%a %b %d %H:%M:%S %Y")
        except ValueError:
            pass
    # Fallback: try email.utils (handles RFC 2822 variants).
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


class GpfsQuotaReader(QuotaReader):
    """Read per-fileset quotas from a GPFS cs_usage.json file.

    Expected schema::

        {
          "paths":  {"csfs1": {"<fileset>": "<mount path>", ...}},
          "usage":  {"FILESET": {"<fileset>": {"limit": "<KiB>",
                                               "usage": "<KiB>",
                                               "files": "<count>"}, ...},
                     "USR":     {...}},   # user-level, ignored
          "date":   "..."
        }

    Limit/usage values in the source JSON are **KiB** (mmlsquota's default
    unit); this reader converts them to bytes so ``QuotaEntry`` is always
    byte-denominated across storage backends.

    Umbrella filesets with ``limit == 0`` (e.g. ``univ``) are skipped —
    they are mount-point placeholders, not real per-project quotas.
    """

    _KIB = 1024
    mount_root = '/gpfs/csfs1'
    mount_hosts = ['derecho', 'casper']

    def read(self) -> list[QuotaEntry]:
        with open(self.path) as fh:
            data = json.load(fh)

        self.snapshot_date = _parse_snapshot_date(data.get('date', ''))

        fs_usage = data.get('usage', {}).get('FILESET', {})
        paths = {}
        for _volume, volume_paths in data.get('paths', {}).items():
            paths.update(volume_paths)

        entries = []
        for fileset, raw in fs_usage.items():
            try:
                limit_kib = int(raw['limit'])
                usage_kib = int(raw['usage'])
                file_count = int(raw['files'])
            except (KeyError, TypeError, ValueError):
                continue

            if limit_kib == 0:
                continue

            entries.append(QuotaEntry(
                fileset_name=fileset,
                path=paths.get(fileset),
                limit_bytes=limit_kib * self._KIB,
                usage_bytes=usage_kib * self._KIB,
                file_count=file_count,
            ))
        return entries
