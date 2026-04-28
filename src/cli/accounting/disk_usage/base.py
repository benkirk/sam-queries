"""Base types for per-user-per-project disk usage readers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class DiskUsageEntry:
    """A single per-user-per-project disk usage row from a snapshot file.

    Pre-charging-math: ``terabyte_years`` and ``charges`` are populated
    after the row is constructed, by the caller, using
    ``sam.summaries.disk_summaries.tib_years()``.

    Synthetic gap rows (the ``<unidentified>`` reconciliation rows) carry
    ``act_username='<unidentified>'``, ``user_override`` set to the
    project lead, and ``account_override`` set to the resolved account.
    For normal rows, both overrides are ``None`` and the upsert resolves
    them via the standard path.
    """
    activity_date: date
    projcode: str
    username: str
    number_of_files: int
    bytes: int
    directory_path: Optional[str] = None
    reporting_interval: int = 7
    cos: int = 0

    # Audit label written to act_username column. For normal rows we
    # leave this as ``None`` so the column is NULL (matching the legacy
    # disk-accounting convention). For synthetic gap rows the import
    # path sets it to e.g. ``'<unidentified>'``.
    act_username: Optional[str] = None

    # Computed by the import after read() (TiB-years, post-epoch).
    terabyte_years: float = 0.0
    charges: float = 0.0

    # Pre-resolved entity overrides (used for gap rows; None otherwise).
    # The disk_usage layer doesn't import sam ORM types — these are typed
    # as Optional[object] so the package remains import-light.
    user_override: Optional[object] = field(default=None, repr=False)
    account_override: Optional[object] = field(default=None, repr=False)


class DiskUsageReader(ABC):
    """Read per-user-per-project disk usage rows from a backend-specific file.

    Subclasses populate ``snapshot_date`` during ``read()`` so the
    importer can validate the file matches the requested date window.
    """

    snapshot_date: Optional[date] = None

    def __init__(self, path: str):
        self.path = path

    @abstractmethod
    def read(self) -> list[DiskUsageEntry]:
        """Return all per-user-per-project usage rows in the file."""
