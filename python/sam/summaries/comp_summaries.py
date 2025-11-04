#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class CompChargeSummary(Base):
    """
    Daily summary of computational charges.

    Aggregates charges by date, user, project, machine, queue, and resource.
    Used for reporting, trend analysis, and dashboard displays.

    The summary provides:
    - Aggregated job counts
    - Total core hours consumed
    - Total charges incurred
    - Breakdown by facility, machine, queue
    """
    __tablename__ = 'comp_charge_summary'

    __table_args__ = (
        Index('ix_comp_charge_summary_date', 'activity_date'),
        Index('ix_comp_charge_summary_user', 'user_id'),
        Index('ix_comp_charge_summary_account', 'account_id'),
        Index('ix_comp_charge_summary_machine', 'machine_id'),
        Index('ix_comp_charge_summary_queue', 'queue_id'),
        Index('ix_comp_charge_summary_resource', 'resource'),
        Index('ix_comp_charge_summary_projcode', 'projcode'),
        Index('ix_comp_charge_summary_date_account', 'activity_date', 'account_id'),
    )

    def __eq__(self, other):
        """Two summaries are equal if they have the same ID."""
        if not isinstance(other, CompChargeSummary):
            return False
        return (self.charge_summary_id is not None and
                self.charge_summary_id == other.charge_summary_id)

    def __hash__(self):
        """Hash based on charge_summary_id for set/dict operations."""
        return (hash(self.charge_summary_id) if self.charge_summary_id is not None
                else hash(id(self)))

    charge_summary_id = Column(Integer, primary_key=True, autoincrement=True)
    activity_date = Column(DateTime, nullable=False)

    # User identification (actual and recorded)
    act_username = Column(String(35))
    unix_uid = Column(Integer)
    act_unix_uid = Column(Integer)
    username = Column(String(35))
    user_id = Column(Integer, ForeignKey('users.user_id'))

    # Project identification (actual and recorded)
    projcode = Column(String(30))
    act_projcode = Column(String(30))
    facility_name = Column(String(30))
    account_id = Column(Integer, ForeignKey('account.account_id'))

    # Resource identification
    machine = Column(String(100), nullable=False)
    machine_id = Column(Integer, ForeignKey('machine.machine_id'))
    queue = Column(String(100), nullable=False)
    queue_id = Column(Integer, ForeignKey('queue.queue_id'))
    resource = Column(String(40))
    cos = Column(Integer)

    # Aggregated metrics
    num_jobs = Column(Integer)
    core_hours = Column(Numeric(22, 8))
    charges = Column(Numeric(22, 8))

    # Processing
    error_comment = Column(Text)
    sweep = Column(Integer, nullable=True)

    user = relationship('User', back_populates='comp_charge_summaries')
    account = relationship('Account', back_populates='comp_charge_summaries')
    machine_ref = relationship('Machine', foreign_keys=[machine_id], back_populates='comp_charge_summaries')
    queue_ref = relationship('Queue', foreign_keys=[queue_id], back_populates='comp_charge_summaries')

    # Status tracking
    status_records = relationship(
        'CompChargeSummaryStatus',
        foreign_keys='CompChargeSummaryStatus.charge_summary_id',
        back_populates='charge_summary'
    )

    @property
    def average_charge_per_job(self) -> Optional[float]:
        """Calculate average charge per job."""
        if self.num_jobs and self.num_jobs > 0 and self.charges:
            return self.charges / self.num_jobs
        return None

    @property
    def average_core_hours_per_job(self) -> Optional[float]:
        """Calculate average core hours per job."""
        if self.num_jobs and self.num_jobs > 0 and self.core_hours:
            return self.core_hours / self.num_jobs
        return None

    @property
    def has_jobs(self) -> bool:
        """Check if this summary has any jobs."""
        return bool(self.num_jobs and self.num_jobs > 0)

    @property
    def has_charges(self) -> bool:
        """Check if this summary has any charges."""
        return bool(self.charges and self.charges > 0)

    def __repr__(self):
        return (f"<CompChargeSummary(id={self.charge_summary_id}, "
                f"date={self.activity_date.date() if self.activity_date else None}, "
                f"jobs={self.num_jobs}, charges={self.charges})>")


#----------------------------------------------------------------------------
class CompChargeSummaryStatus(Base):
    """
    Tracks processing status of charge summaries.

    Links charge summaries to their processing command/batch,
    tracking when they were created or last modified. This is used
    to track data lineage and processing history.

    The command_id typically contains:
    - Command name
    - Resource/machine
    - Timestamp
    - Batch identifier
    """
    __tablename__ = 'comp_charge_summary_status'

    __table_args__ = (
        Index('ix_comp_charge_summary_status_command', 'command_id'),
        Index('ix_comp_charge_summary_status_summary', 'charge_summary_id'),
        Index('ix_comp_charge_summary_status_modified', 'modified'),
    )

    def __eq__(self, other):
        """Two status records are equal if they have the same ID."""
        if not isinstance(other, CompChargeSummaryStatus):
            return False
        return (self.charge_summary_status_id is not None and
                self.charge_summary_status_id == other.charge_summary_status_id)

    def __hash__(self):
        """Hash based on charge_summary_status_id for set/dict operations."""
        return (hash(self.charge_summary_status_id)
                if self.charge_summary_status_id is not None
                else hash(id(self)))

    charge_summary_status_id = Column(Integer, primary_key=True, autoincrement=True)
    command_id = Column(String(100), nullable=False)
    charge_summary_id = Column(Integer,
                               ForeignKey('comp_charge_summary.charge_summary_id'),
                               nullable=False)
    modified = Column(DateTime,
                     server_default=text('CURRENT_TIMESTAMP'),
                     onupdate=datetime.utcnow)

    charge_summary = relationship('CompChargeSummary', back_populates='status_records')

    @property
    def age_days(self) -> Optional[float]:
        """Calculate age in days since last modification."""
        if self.modified:
            delta = datetime.utcnow() - self.modified
            return delta.total_seconds() / 86400
        return None

    def __repr__(self):
        return (f"<CompChargeSummaryStatus(summary_id={self.charge_summary_id}, "
                f"command='{self.command_id}', modified={self.modified})>")

# ============================================================================
# Activity and Charge Tables (HPC)
# ============================================================================


#-------------------------------------------------------------------------em-
