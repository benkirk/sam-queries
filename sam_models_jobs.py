from sam_models import *

# ============================================================================
# Computational Activity and Charges
# ============================================================================

class CompJob(Base):
    """
    Base computational job records.

    Stores metadata about batch jobs submitted to computational resources.
    Each job can have multiple activity records (comp_activity) representing
    different utilization calculations or processing stages.

    Note: Uses composite primary key for partitioning support.
    """
    __tablename__ = 'comp_job'

    __table_args__ = (
        Index('ix_comp_job_era_job', 'era_part_key', 'job_id', 'job_idx'),
        Index('ix_comp_job_activity_date', 'activity_date'),
        Index('ix_comp_job_machine', 'machine'),
        Index('ix_comp_job_projcode', 'projcode'),
        Index('ix_comp_job_username', 'username'),
    )

    def __eq__(self, other):
        """Two jobs are equal if they have the same composite key."""
        if not isinstance(other, CompJob):
            return False
        return (
            self.era_part_key == other.era_part_key and
            self.job_id == other.job_id and
            self.job_idx == other.job_idx and
            self.machine == other.machine and
            self.submit_time == other.submit_time
        )

    def __hash__(self):
        """Hash based on composite primary key for set/dict operations."""
        return hash((self.era_part_key, self.job_id, self.job_idx,
                    self.machine, self.submit_time))

    # Composite primary key
    era_part_key = Column(Integer, primary_key=True, default=99)
    job_id = Column(String(35), primary_key=True)
    job_idx = Column(Integer, primary_key=True)
    machine = Column(String(100), primary_key=True)
    submit_time = Column(Integer, primary_key=True)

    # Job identification
    queue = Column(String(100), nullable=False)
    projcode = Column(String(30), nullable=False)
    username = Column(String(35))
    resource = Column(String(40))
    unix_uid = Column(Integer)
    job_name = Column(String(255))

    # Job lifecycle
    start_time = Column(Integer, nullable=False)
    end_time = Column(Integer, nullable=False)

    # Job characteristics
    cos = Column(Integer)
    exit_status = Column(String(20))
    interactive = Column(Integer)
    log_data = Column(Text)  # JSON-formatted PBS/Slurm logs

    # Timestamps
    activity_date = Column(DateTime, nullable=False)
    load_date = Column(DateTime, nullable=False)

    # Relationships
    activities = relationship(
        'CompActivity',
        primaryjoin='and_('
                    'CompJob.era_part_key == CompActivity.era_part_key, '
                    'CompJob.job_id == CompActivity.job_id, '
                    'CompJob.job_idx == CompActivity.job_idx, '
                    'CompJob.machine == CompActivity.machine, '
                    'CompJob.submit_time == CompActivity.submit_time)',
        foreign_keys='[CompActivity.era_part_key, CompActivity.job_id, '
                    'CompActivity.job_idx, CompActivity.machine, CompActivity.submit_time]',
        back_populates='job',
        lazy='selectin',
        viewonly=True
    )

    @property
    def wall_time_seconds(self) -> int:
        """Calculate wall clock time in seconds."""
        return self.end_time - self.start_time if self.end_time and self.start_time else 0

    @property
    def queue_wait_time_seconds(self) -> int:
        """Calculate queue wait time in seconds."""
        return self.start_time - self.submit_time if self.start_time and self.submit_time else 0

    @property
    def wall_time_hours(self) -> float:
        """Calculate wall clock time in hours."""
        return self.wall_time_seconds / 3600.0

    @property
    def is_successful(self) -> bool:
        """Check if job completed successfully."""
        return self.exit_status == '0' if self.exit_status else False

    @property
    def is_interactive_job(self) -> bool:
        """Check if this was an interactive job."""
        return bool(self.interactive)

    def __repr__(self):
        return (f"<CompJob(job_id='{self.job_id}', idx={self.job_idx}, "
                f"machine='{self.machine}', projcode='{self.projcode}')>")


class CompActivity(Base):
    """
    Computational activity records with charging information.

    Represents the actual charged activity for a job. A single job (comp_job)
    can have multiple activity records with different util_idx values,
    representing different charging calculations or processing stages.

    Note: Uses composite primary key including partitioning keys for
    efficient data management in large-scale systems.
    """
    __tablename__ = 'comp_activity'

    __table_args__ = (
        Index('ix_comp_activity_era_acct_job', 'era_part_key', 'acct_part_key',
              'job_id', 'job_idx'),
        Index('ix_comp_activity_activity_date', 'activity_date'),
        Index('ix_comp_activity_charge_summary', 'charge_summary_id'),
        Index('ix_comp_activity_machine', 'machine'),
        # Index for the join with comp_job
        Index('ix_comp_activity_job_lookup', 'era_part_key', 'job_id', 'job_idx',
              'machine', 'submit_time'),
    )

    def __eq__(self, other):
        """Two activities are equal if they have the same composite key."""
        if not isinstance(other, CompActivity):
            return False
        return (
            self.era_part_key == other.era_part_key and
            self.acct_part_key == other.acct_part_key and
            self.job_id == other.job_id and
            self.job_idx == other.job_idx and
            self.util_idx == other.util_idx and
            self.machine == other.machine and
            self.submit_time == other.submit_time
        )

    def __hash__(self):
        """Hash based on composite primary key for set/dict operations."""
        return hash((self.era_part_key, self.acct_part_key, self.job_id,
                    self.job_idx, self.util_idx, self.machine, self.submit_time))

    # Composite primary key (includes partitioning keys)
    era_part_key = Column(Integer, primary_key=True, default=99)
    acct_part_key = Column(Integer, primary_key=True, default=0)
    job_id = Column(String(35), primary_key=True)
    job_idx = Column(Integer, primary_key=True)
    util_idx = Column(Integer, primary_key=True)  # Utilization calculation index
    machine = Column(String(100), primary_key=True)
    submit_time = Column(Integer, primary_key=True)

    # Job timing (denormalized from comp_job)
    start_time = Column(Integer, nullable=False)
    end_time = Column(Integer, nullable=False)

    # Activity metadata
    activity_date = Column(DateTime, nullable=False)
    load_date = Column(DateTime, nullable=False)
    charge_summary_id = Column(Integer, nullable=False)

    # Job details (denormalized for query performance)
    job_name = Column(String(255))
    processor_type = Column(String(50))

    # Resource utilization
    wall_time = Column(Float)
    unix_user_time = Column(Float)
    unix_system_time = Column(Float)
    num_nodes_used = Column(Integer)
    num_cores_used = Column(Integer)
    chargeable_processors = Column(Integer)

    # Charging
    core_hours = Column(Float(22, 8))
    charge = Column(Float(22, 8))
    external_charge = Column(Float(22, 8))  # For external/special charges
    charge_date = Column(DateTime)

    # Processing status
    processing_status = Column(Boolean)
    error_comment = Column(Text)

    # Relationships
    job = relationship(
        'CompJob',
        primaryjoin='and_('
                    'CompActivity.era_part_key == CompJob.era_part_key, '
                    'CompActivity.job_id == CompJob.job_id, '
                    'CompActivity.job_idx == CompJob.job_idx, '
                    'CompActivity.machine == CompJob.machine, '
                    'CompActivity.submit_time == CompJob.submit_time)',
        foreign_keys='[CompActivity.era_part_key, CompActivity.job_id, '
                    'CompActivity.job_idx, CompActivity.machine, CompActivity.submit_time]',
        back_populates='activities',
        lazy='joined',
        viewonly=True,
        uselist=False
    )

    @property
    def cpu_time_seconds(self) -> Optional[float]:
        """Calculate total CPU time in seconds."""
        if self.unix_user_time is not None and self.unix_system_time is not None:
            return self.unix_user_time + self.unix_system_time
        return None

    @property
    def cpu_efficiency(self) -> Optional[float]:
        """
        Calculate CPU efficiency as percentage.

        Returns:
            CPU time / (wall time * cores) * 100, or None if insufficient data
        """
        if not all([self.wall_time, self.num_cores_used]):
            return None

        cpu_time = self.cpu_time_seconds
        if cpu_time is None:
            return None

        theoretical_max = self.wall_time * self.num_cores_used

        if theoretical_max == 0:
            return None

        return (cpu_time / theoretical_max) * 100

    @property
    def is_charged(self) -> bool:
        """Check if this activity has been charged."""
        return self.charge is not None and self.charge_date is not None

    @property
    def effective_charge(self) -> float:
        """
        Get the effective charge amount.

        Returns external_charge if set, otherwise regular charge.
        Defaults to 0.0 if neither is set.
        """
        if self.external_charge is not None:
            return self.external_charge
        return self.charge if self.charge is not None else 0.0

    @hybrid_property
    def has_external_charge(self) -> bool:
        """Check if external charge is applied (Python side)."""
        return self.external_charge is not None and self.external_charge > 0

    @has_external_charge.expression
    def has_external_charge(cls):
        """Check if external charge is applied (SQL side)."""
        return and_(
            cls.external_charge.isnot(None),
            cls.external_charge > 0
        )

    def __repr__(self):
        return (f"<CompActivity(job_id='{self.job_id}', util_idx={self.util_idx}, "
                f"core_hours={self.core_hours}, charge={self.charge})>")


class CompActivityCharge(Base):
    """
    Computational activity charge view.

    This is a database VIEW that joins comp_job and comp_activity tables
    to provide a denormalized view of job information with charging details.
    This is read-only and typically used for reporting and analysis.

    Important: This represents a database view, not a table. It cannot be
    directly inserted/updated. Use CompJob and CompActivity for modifications.

    The view combines:
    - Job metadata from comp_job
    - Charging details from comp_activity
    - Calculated fields like queue_wait_time
    """
    __tablename__ = 'comp_activity_charge'

    # Mark as a view (read-only)
    __table_args__ = (
        {'info': {'is_view': True}},
    )

    def __eq__(self, other):
        """Two view records are equal if they reference the same activity."""
        if not isinstance(other, CompActivityCharge):
            return False
        return (
            self.job_id == other.job_id and
            self.job_idx == other.job_idx and
            self.util_idx == other.util_idx and
            self.submit_time == other.submit_time and
            self.projcode == other.projcode
        )

    def __hash__(self):
        """Hash based on identifying fields for set/dict operations."""
        return hash((self.job_id, self.job_idx, self.util_idx,
                    self.submit_time, self.projcode))

    # User and project information
    unix_uid = Column(Integer)
    username = Column(String(35))
    projcode = Column(String(30), nullable=False, primary_key=True)

    # Job identification
    job_id = Column(String(35), nullable=False, primary_key=True)
    job_name = Column(String(255))
    job_idx = Column(Integer, nullable=False, primary_key=True)
    util_idx = Column(Integer, nullable=False, primary_key=True)

    # Resource information
    queue_name = Column(String(100), nullable=False)
    machine = Column(String(100), nullable=False)
    num_nodes_used = Column(Integer)
    num_cores_used = Column(Integer)
    cos = Column(Integer)

    # Timing information
    submit_time = Column(Integer, nullable=False, primary_key=True)
    start_time = Column(Integer, nullable=False)
    end_time = Column(Integer, nullable=False)
    wall_time = Column(Float)
    unix_user_time = Column(Float)
    unix_system_time = Column(Float)
    queue_wait_time = Column(BigInteger, nullable=False, default=0)

    # Job status
    exit_status = Column(String(20))
    interactive = Column(Integer)

    # Processing
    processing_status = Column(Boolean)
    error_comment = Column(Text)

    # Dates
    activity_date = Column(DateTime, nullable=False)
    load_date = Column(DateTime, nullable=False)

    # Charging information
    core_hours = Column(Float(22, 8))
    charge = Column(Float(22, 8))
    external_charge = Column(Float(22, 8))
    charge_date = Column(DateTime)

    # Calculated properties
    @property
    def actual_wall_time(self) -> int:
        """Calculate actual wall time from timestamps."""
        return self.end_time - self.start_time if self.end_time and self.start_time else 0

    @property
    def actual_queue_wait(self) -> int:
        """Calculate actual queue wait time from timestamps."""
        return self.start_time - self.submit_time if self.start_time and self.submit_time else 0

    @property
    def wall_time_hours(self) -> float:
        """Calculate wall time in hours."""
        return (self.wall_time / 3600) if self.wall_time else 0.0

    @property
    def charge_efficiency(self) -> Optional[float]:
        """
        Calculate what percentage of requested resources were actually charged.

        Returns:
            (core_hours / (wall_time * cores)) * 100 if data available
        """
        if not all([self.wall_time, self.num_cores_used, self.core_hours]):
            return None

        theoretical_max = (self.wall_time / 3600) * self.num_cores_used  # Convert to hours
        if theoretical_max == 0:
            return None

        return (self.core_hours / theoretical_max) * 100

    @property
    def effective_charge(self) -> float:
        """
        Get the effective charge amount.

        Returns external_charge if set, otherwise regular charge.
        """
        if self.external_charge is not None:
            return self.external_charge
        return self.charge if self.charge is not None else 0.0

    @property
    def is_successful(self) -> bool:
        """Check if job completed successfully."""
        return self.exit_status == '0' if self.exit_status else False

    @property
    def is_interactive_job(self) -> bool:
        """Check if this was an interactive job."""
        return bool(self.interactive)

    def __repr__(self):
        return (f"<CompActivityCharge(job_id='{self.job_id}', projcode='{self.projcode}', "
                f"core_hours={self.core_hours}, charge={self.charge})>")


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
    core_hours = Column(Float(22, 8))
    charges = Column(Float(22, 8))

    # Processing
    error_comment = Column(Text)
    sweep = Column(Integer)  # Sweep/batch number

    # Relationships
    user = relationship('User')
    account = relationship('Account')
    machine_ref = relationship('Machine', foreign_keys=[machine_id])
    queue_ref = relationship('Queue', foreign_keys=[queue_id])

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

    # Relationships
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
