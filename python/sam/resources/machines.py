#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class Machine(Base, TimestampMixin):
    """Computing machines/systems."""
    __tablename__ = 'machine'

    __table_args__ = (
        Index('ix_machine_resource', 'resource_id'),
    )

    machine_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False)
    description = Column(String(255))
    resource_id = Column(Integer, ForeignKey('resources.resource_id'), nullable=False)
    cpus_per_node = Column(Integer)

    commission_date = Column(DateTime)
    decommission_date = Column(DateTime)

    comp_charge_summaries = relationship('CompChargeSummary', foreign_keys='CompChargeSummary.machine_id', back_populates='machine_ref')
    machine_factors = relationship('MachineFactor', back_populates='machine')
    resource = relationship('Resource', back_populates='machines')

    def __repr__(self):
        return f"<Machine(name='{self.name}', cpus_per_node={self.cpus_per_node})>"

    def __eq__(self, other):
        """Two machines are equal if they have the same machine_id."""
        if not isinstance(other, Machine):
            return False
        return self.machine_id is not None and self.machine_id == other.machine_id

    def __hash__(self):
        """Hash based on machine_id for set/dict operations."""
        return hash(self.machine_id) if self.machine_id is not None else hash(id(self))


#----------------------------------------------------------------------------
class MachineFactor(Base, TimestampMixin):
    """Charging factors for machines over time."""
    __tablename__ = 'machine_factor'

    __table_args__ = (
        Index('ix_machine_factor_machine', 'machine_id'),
        Index('ix_machine_factor_dates', 'start_date', 'end_date'),
    )

    machine_factor_id = Column(Integer, primary_key=True, autoincrement=True)
    machine_id = Column(Integer, ForeignKey('machine.machine_id'), nullable=False)
    factor_value = Column(Numeric(15, 2), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)

    machine = relationship('Machine', back_populates='machine_factors')


#----------------------------------------------------------------------------
class Queue(Base, TimestampMixin):
    """Job queues on resources."""
    __tablename__ = 'queue'

    __table_args__ = (
        Index('ix_queue_resource', 'resource_id'),
        Index('ix_queue_name', 'queue_name'),
    )

    queue_id = Column(Integer, primary_key=True, autoincrement=True)
    resource_id = Column(Integer, ForeignKey('resources.resource_id'), nullable=False)
    queue_name = Column(String(50), nullable=False)
    description = Column(String(255), nullable=False)
    wall_clock_hours_limit = Column(Numeric(5, 2))
    cos_id = Column(Integer)

    start_date = Column(DateTime)
    end_date = Column(DateTime)

    resource = relationship('Resource', back_populates='queues')
    queue_factors = relationship('QueueFactor', back_populates='queue')
    wallclock_exemptions = relationship('WallclockExemption', back_populates='queue')
    comp_charge_summaries = relationship('CompChargeSummary', foreign_keys='CompChargeSummary.queue_id', back_populates='queue_ref')

    def __repr__(self):
        return f"<Queue(name='{self.queue_name}', resource='{self.resource.resource_name if self.resource else None}')>"

    def __eq__(self, other):
        """Two queues are equal if they have the same queue_id."""
        if not isinstance(other, Queue):
            return False
        return self.queue_id is not None and self.queue_id == other.queue_id

    def __hash__(self):
        """Hash based on queue_id for set/dict operations."""
        return hash(self.queue_id) if self.queue_id is not None else hash(id(self))


#----------------------------------------------------------------------------
class QueueFactor(Base, TimestampMixin):
    """Charging factors for queues over time."""
    __tablename__ = 'queue_factor'

    __table_args__ = (
        Index('ix_queue_factor_queue', 'queue_id'),
        Index('ix_queue_factor_dates', 'start_date', 'end_date'),
    )

    queue_factor_id = Column(Integer, primary_key=True, autoincrement=True)
    queue_id = Column(Integer, ForeignKey('queue.queue_id'), nullable=False)
    factor_value = Column(Float, nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)

    queue = relationship('Queue', back_populates='queue_factors')


# ============================================================================
# Facility Management
# ============================================================================


#-------------------------------------------------------------------------em-
