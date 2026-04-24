"""Base types for quota file readers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
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


class QuotaReader(ABC):
    """Read per-project quota limits from a storage-system-specific file."""

    def __init__(self, path: str):
        self.path = path

    @abstractmethod
    def read(self) -> list[QuotaEntry]:
        """Return all project-level quota entries. User-level quotas are excluded."""
