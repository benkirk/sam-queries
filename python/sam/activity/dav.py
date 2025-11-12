#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class DavActivity(Base):
    """DAV job activity records (Geyser, Caldera, etc.)."""
    __tablename__ = 'dav_activity'

    __table_args__ = (
        Index('ix_dav_activity_job', 'job_id'),
        Index('ix_dav_activity_cos', 'dav_cos_id'),
        Index('ix_dav_activity_queue', 'queue_name'),
    )

    dav_activity_id = Column(Integer, primary_key=True, autoincrement=True)

    # User and project
    unix_uid = Column(Integer, nullable=False)
    username = Column(String(35), nullable=False)
    projcode = Column(String(30), nullable=False)

    # Job identification
    job_id = Column(String(35), nullable=False)
    job_name = Column(String(255), nullable=False)
    job_idx = Column(Integer)
    queue_name = Column(String(100), nullable=False)
    machine = Column(String(100), nullable=False)

    # Timing
    start_time = Column(Integer, nullable=False)
    end_time = Column(Integer, nullable=False)
    submit_time = Column(Integer, nullable=False)

    # Resource utilization
    unix_user_time = Column(Float)
    unix_system_time = Column(Float)
    queue_wait_time = Column(Integer)
    num_nodes_used = Column(Integer)
    num_cores_used = Column(Integer)

    # Job characteristics
    dav_cos_id = Column(Integer, ForeignKey('dav_cos.dav_cos_id'))
    exit_status = Column(String(20))
    from_host = Column(String(256))
    interactive = Column(Integer)
    reservation_id = Column(String(255))

    # Processing
    processing_status = Column(Boolean)
    error_comment = Column(Text)

    # Dates and charging
    activity_date = Column(DateTime)
    load_date = Column(DateTime, nullable=False)
    external_charge = Column(Numeric(15, 8))

    dav_cos = relationship('DavCos', back_populates='activities')
    charges = relationship('DavCharge', back_populates='activity')

    @property
    def wall_time_seconds(self) -> int:
        """Calculate wall clock time in seconds."""
        return self.end_time - self.start_time if self.end_time and self.start_time else 0

    @property
    def wall_time_hours(self) -> float:
        """Calculate wall clock time in hours."""
        return self.wall_time_seconds / 3600.0

    def __str__(self):
        return f"{self.job_id}"

    def __repr__(self):
        return f"<DavActivity(job_id='{self.job_id}', machine='{self.machine}')>"

    def __eq__(self, other):
        """Two activities are equal if they have the same dav_activity_id."""
        if not isinstance(other, DavActivity):
            return False
        return (self.dav_activity_id is not None and
                self.dav_activity_id == other.dav_activity_id)

    def __hash__(self):
        """Hash based on dav_activity_id for set/dict operations."""
        return (hash(self.dav_activity_id) if self.dav_activity_id is not None
                else hash(id(self)))


#----------------------------------------------------------------------------
class DavCharge(Base):
    """DAV charges derived from activity."""
    __tablename__ = 'dav_charge'

    __table_args__ = (
        Index('ix_dav_charge_account', 'account_id'),
        Index('ix_dav_charge_user', 'user_id'),
        Index('ix_dav_charge_activity', 'dav_activity_id', unique=True),
        Index('ix_dav_charge_date', 'charge_date'),
        Index('ix_dav_charge_activity_date', 'activity_date'),
    )

    dav_charge_id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)
    dav_activity_id = Column(Integer, ForeignKey('dav_activity.dav_activity_id'),
                             nullable=False, unique=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    charge_date = Column(DateTime, nullable=False)
    activity_date = Column(DateTime)
    charge = Column(Float)
    core_hours = Column(Float)

    account = relationship('Account', back_populates='dav_charges')
    activity = relationship('DavActivity', back_populates='charges')
    user = relationship('User', back_populates='dav_charges')

    def __str__(self):
        return f"{self.dav_charge_id}"

    def __repr__(self):
        return f"<DavCharge(id={self.dav_charge_id}, charge={self.charge})>"


#----------------------------------------------------------------------------
class DavCos(Base, TimestampMixin):
    """DAV Class of Service definitions."""
    __tablename__ = 'dav_cos'

    dav_cos_id = Column(Integer, primary_key=True)
    description = Column(String(50))
    modified_time = Column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'), onupdate=text('CURRENT_TIMESTAMP'))

    activities = relationship('DavActivity', back_populates='dav_cos')

    def __str__(self):
        return f"{self.dav_cos_id} - {self.description}"

    def __repr__(self):
        return f"<DavCos(id={self.dav_cos_id}, desc='{self.description}')>"


#-------------------------------------------------------------------------em-
