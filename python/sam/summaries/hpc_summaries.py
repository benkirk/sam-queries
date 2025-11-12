#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class HPCChargeSummary(Base):
    """Daily summary of HPC charges."""
    __tablename__ = 'hpc_charge_summary'

    __table_args__ = (
        Index('ix_hpc_charge_summary_date', 'activity_date'),
        Index('ix_hpc_charge_summary_user', 'user_id'),
        Index('ix_hpc_charge_summary_account', 'account_id'),
        Index('ix_hpc_charge_summary_machine', 'machine'),
        Index('ix_hpc_charge_summary_queue', 'queue_name'),
    )

    hpc_charge_summary_id = Column(Integer, primary_key=True, autoincrement=True)
    activity_date = Column(Date, nullable=False)

    act_username = Column(String(35))
    unix_uid = Column(Integer)
    act_unix_uid = Column(Integer)
    projcode = Column(String(30))
    username = Column(String(35))
    act_projcode = Column(String(30))
    facility_name = Column(String(30))

    machine = Column(String(100), nullable=False)
    queue_name = Column(String(100), nullable=False)

    user_id = Column(Integer, ForeignKey('users.user_id'))
    account_id = Column(Integer, ForeignKey('account.account_id'))

    num_jobs = Column(Integer)
    core_hours = Column(Float)
    charges = Column(Float)

    user = relationship('User', back_populates='hpc_charge_summaries')
    account = relationship('Account', back_populates='hpc_charge_summaries')


#----------------------------------------------------------------------------
class HPCChargeSummaryStatus(Base):
    """Tracks which charge summaries are current."""
    __tablename__ = 'hpc_charge_summary_status'

    activity_date = Column(Date, primary_key=True)
    current = Column(Boolean)


# ============================================================================
# DAV (Data Analysis & Visualization) Activity and Charges
# ============================================================================


#-------------------------------------------------------------------------em-
