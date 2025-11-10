#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class DavChargeSummary(Base):
    """Daily summary of DAV charges."""
    __tablename__ = 'dav_charge_summary'

    __table_args__ = (
        Index('ix_dav_charge_summary_date', 'activity_date'),
        Index('ix_dav_charge_summary_user', 'user_id'),
        Index('ix_dav_charge_summary_account', 'account_id'),
        Index('ix_dav_charge_summary_machine', 'machine'),
        Index('ix_dav_charge_summary_queue', 'queue_name'),
    )

    dav_charge_summary_id = Column(Integer, primary_key=True, autoincrement=True)
    activity_date = Column(DateTime, nullable=False)

    # User identification (actual and recorded)
    act_username = Column(String(35))
    unix_uid = Column(Integer)
    act_unix_uid = Column(Integer)
    projcode = Column(String(30))
    username = Column(String(35))
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)

    # Project identification
    act_projcode = Column(String(30))
    facility_name = Column(String(30))
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)

    # Resource identification
    machine = Column(String(100), nullable=False)
    queue_name = Column(String(100), nullable=False)

    # Aggregated metrics
    num_jobs = Column(Integer)
    core_hours = Column(Numeric(22, 8))
    charges = Column(Numeric(22, 8))

    user = relationship('User', back_populates='dav_charge_summaries')
    account = relationship('Account', back_populates='dav_charge_summaries')

    def __str__(self):
        return f"{self.activity_date if self.activity_date else None}"

    def __repr__(self):
        return (f"<DavChargeSummary(date={self.activity_date if self.activity_date else None}, "
                f"jobs={self.num_jobs}, charges={self.charges})>")


#----------------------------------------------------------------------------
class DavChargeSummaryStatus(Base):
    """Tracks which DAV charge summaries are current."""
    __tablename__ = 'dav_charge_summary_status'

    activity_date = Column(DateTime, primary_key=True)
    current = Column(Boolean)

    def __str__(self):
        return f"{self.activity_date}"

    def __repr__(self):
        return f"<DavChargeSummaryStatus(date={self.activity_date}, current={self.current})>"


# ============================================================================
# Disk Activity and Charges
# ============================================================================


#-------------------------------------------------------------------------em-
