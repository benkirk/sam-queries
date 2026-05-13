#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
from datetime import date as _stdlib_date
#-------------------------------------------------------------------------eh-


# ============================================================================
# Disk-charging unit / cutover constants
# ============================================================================

# Bytes per tebibyte (binary, 1024**4). The new disk-charging convention
# stores `disk_charge_summary.terabyte_years` in TiB-years so the column
# composes cleanly with `Allocation.amount` (TiB).
BYTES_PER_TIB = 1024 ** 4

# Days per year used in the storage-charging formula. Matches the legacy
# Java constant `DAYS_IN_YEAR = 365` (NOT 365.25 — the legacy
# `disk_charge_summary.md` doc was wrong about that).
DAYS_IN_YEAR = 365

# Cutover epoch: the first activity_date whose disk_charge_summary row is
# written under the new TiB-year convention. Rows with activity_date < EPOCH
# stay in the legacy decimal-TB-year convention and are not rewritten.
#
# This date is set to the first --disk run we ship and treated as immutable
# thereafter. Allocations whose window straddles this date will read mixed
# units (~9.95% drift on the pre-epoch portion); see docs/plans/DISK_CHARGING.md.
DISK_CHARGING_TIB_EPOCH = _stdlib_date(2026, 1, 3)


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class DiskChargeSummary(Base):
    """Daily summary of disk charges."""
    __tablename__ = 'disk_charge_summary'

    __table_args__ = (
        Index('idx_disk_charge_summary_date', 'activity_date'),
        Index('idx_disk_charge_summary_user_id', 'user_id'),
        Index('idx_disk_charge_summary_account_id', 'account_id'),
        Index('idx_disk_charge_summary_directory_id', 'directory_id'),
    )

    disk_charge_summary_id = Column(Integer, primary_key=True, autoincrement=True)
    activity_date = Column(Date, ForeignKey('disk_charge_summary_status.activity_date'),
                           nullable=False)

    act_username = Column(String(35))
    unix_uid = Column(Integer)
    act_unix_uid = Column(Integer)
    projcode = Column(String(30))
    username = Column(String(35))
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    act_projcode = Column(String(30))
    facility_name = Column(String(30))
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)
    directory_id = Column(Integer,
                          ForeignKey('project_directory.project_directory_id'),
                          nullable=True)

    number_of_files = Column(Integer)
    bytes = Column(BigInteger)
    terabyte_years = Column(Float)
    charges = Column(Float)

    user = relationship('User', back_populates='disk_charge_summaries')
    account = relationship('Account', back_populates='disk_charge_summaries')
    project_directory = relationship('ProjectDirectory',
                                     back_populates='disk_charge_summaries')

    def __str__(self):
        return f"DiskChargeSummary {self.disk_charge_summary_id}: {self.username}/{self.projcode} {self.activity_date}"

    def __repr__(self):
        return f"<DiskChargeSummary(id={self.disk_charge_summary_id}, user='{self.username}', proj='{self.projcode}', date={self.activity_date})>"


#----------------------------------------------------------------------------
class DiskChargeSummaryStatus(Base):
    """Per-date validity flag for the disk charge summary.

    Legacy SAM semantics (confirmed against prod ``sam-sql.ucar.edu`` on
    2026-05-13: 883 imported dates, all ``current=TRUE``):

    - One row per successfully imported ``activity_date``.
    - ``current = TRUE`` → that date's summary is valid / posted.
    - ``current = FALSE`` → that date has been invalidated and needs
      recalculation (legacy named query
      ``findInvalidDiskChargeSummaryDates``).
    - Multiple ``current = TRUE`` rows are normal — readers pick
      ``MAX(activity_date) WHERE current = TRUE`` for "right now"
      queries (``Account.current_disk_usage()`` /
      ``Project.current_disk_usage()`` /
      ``bulk_current_disk_usage()``).

    Maintained by ``mark_disk_snapshot_current()``.
    """
    __tablename__ = 'disk_charge_summary_status'

    activity_date = Column(Date, primary_key=True)
    current = Column(Boolean)

    def __str__(self):
        return f"DiskChargeSummaryStatus: {self.activity_date} (current={self.current})"

    def __repr__(self):
        return f"<DiskChargeSummaryStatus(date={self.activity_date}, current={self.current})>"


# ============================================================================
# Snapshot status helpers
# ============================================================================

def mark_disk_snapshot_current(session, activity_date) -> None:
    """Mark ``activity_date`` as a valid disk snapshot (``current=TRUE``).

    Upserts only the given date. Does NOT touch any other row's
    ``current`` flag — that flag is per-date in the legacy schema, not
    a single-row pointer. Flipping prior rows to ``current=FALSE``
    would tell legacy reports that every historical date needs
    recalculation
    (cf. ``findInvalidDiskChargeSummaryDates``); see
    ``DiskChargeSummaryStatus`` docstring for the full semantics.

    Does NOT commit — caller wraps in ``management_transaction``.
    """
    existing = session.get(DiskChargeSummaryStatus, activity_date)
    if existing is None:
        session.add(DiskChargeSummaryStatus(activity_date=activity_date, current=True))
    else:
        existing.current = True
    session.flush()


def tib_years(bytes_: int, reporting_interval_days: int) -> float:
    """Compute storage charge in TiB-years (binary, post-epoch convention).

    Single source of truth for the disk charging formula. Used by both
    the per-user import path and the ``<unidentified>`` gap-row path.

    Formula (deviates from legacy decimal-TB convention):

        TiB-years = (bytes * reporting_interval_days) / 365 / 1024**4
    """
    return (bytes_ * reporting_interval_days) / DAYS_IN_YEAR / BYTES_PER_TIB


# ============================================================================
# Dataset Activity
# ============================================================================


#-------------------------------------------------------------------------em-
