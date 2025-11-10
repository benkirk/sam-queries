#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class HPCActivity(Base):
    """HPC job activity records."""
    __tablename__ = 'hpc_activity'

    __table_args__ = (
        Index('ix_hpc_activity_job', 'job_id'),
        Index('ix_hpc_activity_date', 'activity_date'),
        Index('ix_hpc_activity_cos', 'hpc_cos_id'),
    )

    hpc_activity_id = Column(Integer, primary_key=True, autoincrement=True)
    unix_uid = Column(Integer)
    username = Column(String(35), nullable=False)
    projcode = Column(String(30), nullable=False)
    job_id = Column(String(35), nullable=False)
    job_name = Column(String(255))
    queue_name = Column(String(100), nullable=False)
    machine = Column(String(100), nullable=False)

    start_time = Column(Integer, nullable=False)
    end_time = Column(Integer, nullable=False)
    submit_time = Column(Integer, nullable=False)

    unix_user_time = Column(Float)
    unix_system_time = Column(Float)
    queue_wait_time = Column(Integer)

    num_nodes_used = Column(Integer)
    num_cores_used = Column(Integer)
    hpc_cos_id = Column(Integer, ForeignKey('hpc_cos.hpc_cos_id'))

    exit_status = Column(String(20))
    from_host = Column(String(256))
    interactive = Column(Integer)
    reservation_id = Column(String(255))

    processing_status = Column(Boolean)
    error_comment = Column(Text)

    activity_date = Column(DateTime)
    load_date = Column(DateTime, nullable=False)
    modified_time = Column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'), onupdate=text('CURRENT_TIMESTAMP'))

    external_charge = Column(Numeric(15, 8))
    job_idx = Column(Integer, nullable=False)

    hpc_cos = relationship('HPCCos', back_populates='activities')
    charges = relationship('HPCCharge', back_populates='activity')

    def __eq__(self, other):
        """Two activities are equal if they have the same hpc_activity_id."""
        if not isinstance(other, HPCActivity):
            return False
        return (self.hpc_activity_id is not None and
                self.hpc_activity_id == other.hpc_activity_id)

    def __hash__(self):
        """Hash based on hpc_activity_id for set/dict operations."""
        return (hash(self.hpc_activity_id) if self.hpc_activity_id is not None
                else hash(id(self)))


#----------------------------------------------------------------------------
class HPCCharge(Base):
    """HPC charges derived from activity."""
    __tablename__ = 'hpc_charge'

    __table_args__ = (
        Index('ix_hpc_charge_account', 'account_id'),
        Index('ix_hpc_charge_user', 'user_id'),
        Index('ix_hpc_charge_activity', 'hpc_activity_id', unique=True),
        Index('ix_hpc_charge_date', 'charge_date'),
        Index('ix_hpc_charge_activity_date', 'activity_date'),
    )

    hpc_charge_id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)
    hpc_activity_id = Column(Integer, ForeignKey('hpc_activity.hpc_activity_id'),
                             nullable=False, unique=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    charge_date = Column(DateTime, nullable=False)
    activity_date = Column(DateTime)
    charge = Column(Numeric(22, 8))
    core_hours = Column(Numeric(22, 8))

    account = relationship('Account', back_populates='hpc_charges')
    activity = relationship('HPCActivity', back_populates='charges')
    user = relationship('User', back_populates='hpc_charges')


#----------------------------------------------------------------------------
class HPCCos(Base, TimestampMixin):
    """HPC Class of Service definitions."""
    __tablename__ = 'hpc_cos'

    hpc_cos_id = Column(Integer, primary_key=True)
    description = Column(String(50))
    modified_time = Column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'), onupdate=text('CURRENT_TIMESTAMP'))

    activities = relationship('HPCActivity', back_populates='hpc_cos')

    def __str__(self):
        return f"{self.hpc_cos_id} - {self.description}"

    def __repr__(self):
        return f"<HPCCos(id={self.hpc_cos_id}, desc='{self.description}')>"


#-------------------------------------------------------------------------em-
