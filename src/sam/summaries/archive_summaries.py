#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class ArchiveChargeSummary(Base):
    """Daily summary of archive charges."""
    __tablename__ = 'archive_charge_summary'

    __table_args__ = (
        Index('ix_archive_charge_summary_date', 'activity_date'),
        Index('ix_archive_charge_summary_user', 'user_id'),
        Index('ix_archive_charge_summary_account', 'account_id'),
    )

    archive_charge_summary_id = Column(Integer, primary_key=True, autoincrement=True)
    activity_date = Column(Date, nullable=False)

    act_username = Column(String(35))
    unix_uid = Column(Integer)
    act_unix_uid = Column(Integer)
    projcode = Column(String(30))
    username = Column(String(35))
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    act_projcode = Column(String(30))
    facility_name = Column(String(30))
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)

    number_of_files = Column(Integer)
    bytes = Column(BigInteger)
    terabyte_years = Column(Float)
    charges = Column(Float)

    user = relationship('User', back_populates='archive_charge_summaries')
    account = relationship('Account', back_populates='archive_charge_summaries')

    def __str__(self):
        return f"ArchiveChargeSummary {self.archive_charge_summary_id}: {self.username}/{self.projcode} {self.activity_date}"

    def __repr__(self):
        return f"<ArchiveChargeSummary(id={self.archive_charge_summary_id}, user='{self.username}', proj='{self.projcode}', date={self.activity_date})>"


#----------------------------------------------------------------------------
class ArchiveChargeSummaryStatus(Base):
    """Tracks which archive charge summaries are current."""
    __tablename__ = 'archive_charge_summary_status'

    activity_date = Column(Date, primary_key=True)
    current = Column(Boolean)

    def __str__(self):
        return f"ArchiveChargeSummaryStatus: {self.activity_date} (current={self.current})"

    def __repr__(self):
        return f"<ArchiveChargeSummaryStatus(date={self.activity_date}, current={self.current})>"


# ============================================================================
# Charge Adjustments
# ============================================================================


#-------------------------------------------------------------------------em-
