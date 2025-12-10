#-------------------------------------------------------------------------bh-
# Common Imports:
from .base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class Synchronizer(Base):
    """Tracks last run times for synchronization jobs."""
    __tablename__ = 'synchronizer'

    synchronizer_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    last_run = Column(TIMESTAMP)

    def __str__(self):
        return f"{self.name}"

    def __repr__(self):
        return f"<Synchronizer(name='{self.name}', last_run={self.last_run})>"


#----------------------------------------------------------------------------
class ManualTask(Base):
    """Manual intervention tasks."""
    __tablename__ = 'manual_task'

    __table_args__ = (
        Index('ix_manual_task_client', 'client'),
    )

    manual_task_id = Column(Integer, primary_key=True, autoincrement=True)
    client = Column(String(32), nullable=False)
    transaction_context = Column(String(256))
    transaction_id = Column(String(64), nullable=False)
    job_key = Column(String(32), nullable=False)
    job_alias = Column(String(128))
    client_job_id = Column(String(32), nullable=False)
    name = Column(String(32), nullable=False)
    state = Column(String(16), nullable=False)
    mode = Column(String(64))
    assignee = Column(String(32))
    timestamp = Column(BigInteger, nullable=False)
    data = Column(Text, nullable=False)
    delete_on_clear = Column(Boolean, nullable=False, default=False)

    products = relationship('Product', back_populates='manual_task', cascade='all, delete-orphan')


#----------------------------------------------------------------------------
class Product(Base):
    """Products from manual tasks."""
    __tablename__ = 'product'

    __table_args__ = (
        Index('ix_product_manual_task', 'manual_task_id'),
    )

    product_id = Column(Integer, primary_key=True, autoincrement=True)
    manual_task_id = Column(Integer, ForeignKey('manual_task.manual_task_id'),
                           nullable=False)
    name = Column(String(31), nullable=False)
    value = Column(String(16384))
    timestamp = Column(BigInteger, nullable=False)

    manual_task = relationship('ManualTask', back_populates='products')


    # ============================================================================
# Wallclock Exemption
# ============================================================================


#----------------------------------------------------------------------------
class WallclockExemption(Base, TimestampMixin):
    """Exemptions from wallclock time limits for specific users on queues."""
    __tablename__ = 'wallclock_exemption'

    __table_args__ = (
        Index('ix_wallclock_exemption_user', 'user_id'),
        Index('ix_wallclock_exemption_queue', 'queue_id'),
        Index('ix_wallclock_exemption_dates', 'start_date', 'end_date'),
    )

    wallclock_exemption_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    queue_id = Column(Integer, ForeignKey('queue.queue_id'), nullable=False)
    time_limit_hours = Column(Float, nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    comment = Column(Text)

    user = relationship('User', back_populates='wallclock_exemptions')
    queue = relationship('Queue', back_populates='wallclock_exemptions')

    def is_active_at(self, check_date: Optional[datetime] = None) -> bool:
        """Check if exemption is active at a given date."""
        if check_date is None:
            check_date = datetime.now()
        return self.start_date <= check_date <= self.end_date

    @hybrid_property
    def is_currently_active(self) -> bool:
        """Check if exemption is currently active (Python side)."""
        return self.is_active_at()

    @is_currently_active.expression
    def is_currently_active(cls):
        """Check if exemption is currently active (SQL side)."""
        now = func.now()
        return and_(
            cls.start_date <= now,
            cls.end_date >= now
        )

    def __str__(self):
        return f"{self.user_id} / {self.queue_id} / {self.time_limit_hours}"

    def __repr__(self):
        return (f"<WallclockExemption(user_id={self.user_id}, queue_id={self.queue_id}, "
                f"hours={self.time_limit_hours})>")


# ============================================================================
# XRAS (XSEDE Resource Allocation System) Integration
# ============================================================================


#-------------------------------------------------------------------------em-
