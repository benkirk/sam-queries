"""GPFS quota reader — parses cs_usage.json produced for Campaign Store."""

import json

from .base import QuotaReader, QuotaEntry


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

    def read(self) -> list[QuotaEntry]:
        with open(self.path) as fh:
            data = json.load(fh)

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
