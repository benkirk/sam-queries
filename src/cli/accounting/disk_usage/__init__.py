"""Pluggable per-user-per-project disk usage readers, dispatched by resource."""

from .base import DiskUsageEntry, DiskUsageReader
from .glade_csv import GladeCsvReader


_READERS = {
    'Campaign_Store': GladeCsvReader,
}


def get_disk_usage_reader(resource_name: str, path: str) -> DiskUsageReader:
    """Return a DiskUsageReader instance for the given resource.

    Raises NotImplementedError if no reader is registered for the resource.
    """
    cls = _READERS.get(resource_name)
    if cls is None:
        supported = ', '.join(sorted(_READERS)) or '(none)'
        raise NotImplementedError(
            f"No disk usage reader is registered for resource {resource_name!r}. "
            f"Supported: {supported}."
        )
    return cls(path)


__all__ = [
    'DiskUsageEntry', 'DiskUsageReader',
    'GladeCsvReader',
    'get_disk_usage_reader',
]
