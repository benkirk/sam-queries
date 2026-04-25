"""Base types for quota file readers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class QuotaEntry:
    """A single fileset/project quota reported by the storage system."""
    fileset_name: str
    path: Optional[str]
    limit_bytes: int
    usage_bytes: int
    file_count: int

    @property
    def limit_tib(self) -> float:
        return self.limit_bytes / (1024 ** 4)

    @property
    def usage_tib(self) -> float:
        return self.usage_bytes / (1024 ** 4)

    @property
    def utilization(self) -> float:
        """Fraction of limit used, in [0, 1+]. Returns 0 if limit_bytes is 0."""
        if not self.limit_bytes:
            return 0.0
        return self.usage_bytes / self.limit_bytes


class QuotaReader(ABC):
    """Read per-project quota limits from a storage-system-specific file.

    Subclasses may also expose:
      * ``snapshot_date``: when the quota file was generated (populated by
        ``read()``).
      * ``mount_root``: where the storage is mounted on hosts that see it
        — used by the reconcile command to probe for local FS access.
      * ``mount_hosts``: ordered list of known hosts with the mount —
        used to auto-detect an SSH target when the local host lacks it.
    """

    snapshot_date: Optional[datetime] = None
    mount_root: Optional[str] = None
    mount_hosts: list[str] = []

    def __init__(self, path: str):
        self.path = path

    @abstractmethod
    def read(self) -> list[QuotaEntry]:
        """Return all project-level quota entries. User-level quotas are excluded."""
