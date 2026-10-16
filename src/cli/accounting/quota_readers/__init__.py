"""Pluggable quota-file readers, dispatched by file kind and resource name."""

from .acct import AcctQuotaReader
from .base import QuotaReader, QuotaEntry
from .gpfs import GpfsQuotaReader


# GPFS cs_usage.json.
_READERS = {
    'Campaign_Store': GpfsQuotaReader,
}
# hdig acct.<fs>.YYYY-MM-DD CSV. Not Quasar: its quota column is tape-pool capacity.
_ACCT_READERS = {
    'Campaign_Store': AcctQuotaReader,
    'Destor': AcctQuotaReader,
}


def _is_json(path: str) -> bool:
    try:
        with open(path) as fh:
            return fh.read(64).lstrip().startswith('{')
    except OSError:
        return path.endswith('.json')


def get_quota_reader(resource_name: str, path: str) -> QuotaReader:
    """Return the reader for ``path``'s kind on ``resource_name``; NotImplementedError if none."""
    kind, registry = ('JSON', _READERS) if _is_json(path) else ('acct CSV', _ACCT_READERS)
    cls = registry.get(resource_name)
    if cls is None:
        supported = ', '.join(sorted(registry)) or '(none)'
        raise NotImplementedError(
            f"No {kind} quota reader is registered for resource {resource_name!r}. "
            f"Supported: {supported}."
        )
    return cls(path)


__all__ = ['QuotaReader', 'QuotaEntry', 'AcctQuotaReader', 'GpfsQuotaReader', 'get_quota_reader']
