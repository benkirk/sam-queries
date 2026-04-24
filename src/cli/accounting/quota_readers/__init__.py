"""Pluggable quota-file readers, dispatched by resource name."""

from .base import QuotaReader, QuotaEntry
from .gpfs import GpfsQuotaReader


_READERS = {
    'Campaign_Store': GpfsQuotaReader,
}


def get_quota_reader(resource_name: str, path: str) -> QuotaReader:
    """Return a QuotaReader instance for the given resource.

    Raises NotImplementedError if no reader is registered for the resource.
    """
    cls = _READERS.get(resource_name)
    if cls is None:
        supported = ', '.join(sorted(_READERS)) or '(none)'
        raise NotImplementedError(
            f"No quota reader is registered for resource {resource_name!r}. "
            f"Supported: {supported}."
        )
    return cls(path)


__all__ = ['QuotaReader', 'QuotaEntry', 'GpfsQuotaReader', 'get_quota_reader']
