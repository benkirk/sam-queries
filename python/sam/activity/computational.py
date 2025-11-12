#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
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
        PrimaryKeyConstraint('era_part_key', 'job_id', 'job_idx',
                             'machine', 'submit_time',
                             name='pk_comp_job'),
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
    era_part_key = Column(Integer, nullable=False, default=99)
    job_id = Column(String(35), nullable=False)
    job_idx = Column(Integer, nullable=False)
    machine = Column(String(100), nullable=False)
    submit_time = Column(Integer, nullable=False)

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
    log_data = Column(Text)

    # Timestamps
    activity_date = Column(DateTime, nullable=False)
    load_date = Column(DateTime, nullable=False)

    activities = relationship('CompActivity', back_populates='job')

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

    def __str__(self):
        return f"{self.job_id} / {self.machine} / {self.projcode}"

    def __repr__(self):
        return (f"<CompJob(job_id='{self.job_id}', idx={self.job_idx}, "
                f"machine='{self.machine}', projcode='{self.projcode}')>")


#----------------------------------------------------------------------------
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
        PrimaryKeyConstraint('era_part_key', 'acct_part_key', 'job_id',
                             'job_idx', 'util_idx', 'machine', 'submit_time',
                             name='pk_comp_activity'),
        ForeignKeyConstraint(
            ['era_part_key', 'job_id', 'job_idx', 'machine', 'submit_time'],
            ['comp_job.era_part_key', 'comp_job.job_id', 'comp_job.job_idx',
             'comp_job.machine', 'comp_job.submit_time'],
            name='fk_comp_activity_job'
        ),
        Index('ix_comp_activity_era_acct_job', 'era_part_key', 'acct_part_key',
              'job_id', 'job_idx'),
        Index('ix_comp_activity_activity_date', 'activity_date'),
        Index('ix_comp_activity_charge_date', 'charge_date'),
        Index('ix_comp_activity_processing', 'processing_status'),
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
    era_part_key = Column(Integer, nullable=False, default=99)
    acct_part_key = Column(Integer, nullable=False, default=0)
    job_id = Column(String(35), nullable=False)
    job_idx = Column(Integer, nullable=False)
    util_idx = Column(Integer, nullable=False)
    machine = Column(String(100), nullable=False)
    submit_time = Column(Integer, nullable=False)

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
    core_hours = Column(Float)
    charge = Column(Float)
    external_charge = Column(Float)  # For external/special charges
    charge_date = Column(DateTime)

    # Processing status
    processing_status = Column(Boolean)
    error_comment = Column(Text)

    # Relationship using composite foreign key
    job = relationship(
        'CompJob',
        foreign_keys=[era_part_key, job_id, job_idx, machine, submit_time],
        back_populates='activities'
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


#----------------------------------------------------------------------------
# ============================================================================
# Computational Activity Charge View
# ============================================================================

class CompActivityChargeView(Base):
    """
    Computational Activity Charge View - Read-only

    Denormalized view combining computational activity and charge information
    for reporting purposes. This is a database VIEW, not a table.

    Note: This view may have duplicate job_ids if charges were adjusted,
    so it's not suitable for using job_id as a unique key.
    """
    __tablename__ = 'comp_activity_charge'
    __table_args__ = {'info': dict(is_view=True)}

    # Composite key: job_idx + util_idx should be unique
    job_idx = Column(Integer, primary_key=True)
    util_idx = Column(Integer, primary_key=True)

    # User and project info
    unix_uid = Column(Integer)
    username = Column(String(35))
    projcode = Column(String(30), nullable=False)

    # Job identification
    job_id = Column(String(35), nullable=False)
    job_name = Column(String(255))

    # Resource info
    queue_name = Column(String(100), nullable=False)
    machine = Column(String(100), nullable=False)

    # Timing (epoch timestamps)
    start_time = Column(Integer, nullable=False)
    end_time = Column(Integer, nullable=False)
    submit_time = Column(Integer, nullable=False)

    # Resource usage
    unix_user_time = Column(Float)  # double in DB
    unix_system_time = Column(Float)  # double in DB
    queue_wait_time = Column(Integer, nullable=False)
    num_nodes_used = Column(Integer)
    num_cores_used = Column(Integer)
    wall_time = Column(Float)  # double in DB

    # Job metadata
    cos = Column(Integer)  # Class of service
    exit_status = Column(String(20))
    interactive = Column(Integer)  # boolean as int
    processing_status = Column(Integer)  # bit(1) as int
    error_comment = Column(Text)

    # Dates
    activity_date = Column(DateTime, nullable=False)
    load_date = Column(DateTime, nullable=False)
    charge_date = Column(DateTime)

    # Charges
    external_charge = Column(Float(22))  # float(22,8) in DB
    core_hours = Column(Float(22))  # float(22,8) in DB
    charge = Column(Float(22))  # float(22,8) in DB

    def __repr__(self):
        return f"<CompActivityChargeView(job='{self.job_id}', user='{self.username}', project='{self.projcode}', charge={self.charge})>"
