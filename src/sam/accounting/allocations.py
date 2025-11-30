#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class Allocation(Base, TimestampMixin, SoftDeleteMixin):
    """Resource allocations for accounts."""
    __tablename__ = 'allocation'

    __table_args__ = (
        Index('ix_allocation_account', 'account_id'),
        Index('ix_allocation_parent', 'parent_allocation_id'),
        Index('ix_allocation_dates', 'start_date', 'end_date'),
        Index('ix_allocation_active', 'deleted', 'start_date', 'end_date'),
    )

    def __eq__(self, other):
        """Two allocations are equal if they have the same allocation_id."""
        if not isinstance(other, Allocation):
            return False
        return (self.allocation_id is not None and
                self.allocation_id == other.allocation_id)

    def __hash__(self):
        """Hash based on allocation_id for set/dict operations."""
        return (hash(self.allocation_id) if self.allocation_id is not None
                else hash(id(self)))

    allocation_id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)
    parent_allocation_id = Column(Integer, ForeignKey('allocation.allocation_id'))

    amount = Column(Float, nullable=False)
    description = Column(String(255))

    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)

    account = relationship('Account', back_populates='allocations')
    children = relationship('Allocation', remote_side=[parent_allocation_id], back_populates='parent')
    parent = relationship('Allocation', remote_side=[allocation_id], back_populates='children')
    transactions = relationship('AllocationTransaction', back_populates='allocation')
    # xras_allocation = relationship('XrasAllocation', back_populates='local_allocation', uselist=False)  # DEPRECATED - XRAS views don't support relationships

    def is_active_at(self, check_date: Optional[datetime] = None) -> bool:
        """Check if allocation is active at a given date."""
        if self.deleted:
            return False

        if check_date is None:
            check_date = datetime.now()

        if self.start_date > check_date:
            return False

        if self.end_date is not None and self.end_date < check_date:
            return False

        return True

    @hybrid_property
    def is_active(self) -> bool:
        """Check if allocation is currently active (Python side)."""
        return self.is_active_at()

    @is_active.expression
    def is_active(cls):
        """Check if allocation is currently active (SQL side)."""
        now = func.now()
        return and_(
            cls.deleted == False,
            cls.start_date <= now,
            or_(cls.end_date.is_(None), cls.end_date >= now)
        )

    def __str__(self):
        return f"{self.allocation_id}"

    def __repr__(self):
        return f"<Allocation(id={self.allocation_id}, amount={self.amount}, active={self.is_active_at()})>"

#----------------------------------------------------------------------------
class AllocationTransaction(Base):
    """Transaction history for allocations."""
    __tablename__ = 'allocation_transaction'

    __table_args__ = (
        Index('ix_allocation_transaction_allocation', 'allocation_id'),
        Index('ix_allocation_transaction_user', 'user_id'),
        Index('ix_allocation_transaction_related', 'related_transaction_id'),
    )

    allocation_transaction_id = Column(Integer, primary_key=True, autoincrement=True)
    allocation_id = Column(Integer, ForeignKey('allocation.allocation_id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.user_id'))
    related_transaction_id = Column(Integer,
                                   ForeignKey('allocation_transaction.allocation_transaction_id'))

    transaction_type = Column(String(50), nullable=False)
    requested_amount = Column(Float)
    transaction_amount = Column(Float)

    alloc_start_date = Column(DateTime)
    alloc_end_date = Column(DateTime)

    auth_at_panel_mtg = Column(Boolean)
    transaction_comment = Column(Text)
    propagated = Column(Boolean, nullable=False, default=False)

    creation_time = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    allocation = relationship('Allocation', back_populates='transactions')
    related_transaction = relationship('AllocationTransaction', remote_side=[allocation_transaction_id], back_populates='related_transactions')
    related_transactions = relationship('AllocationTransaction', remote_side=[related_transaction_id], back_populates='related_transaction')
    user = relationship('User', back_populates='allocation_transactions')


# ============================================================================
# Project Management
# ============================================================================


#----------------------------------------------------------------------------
class AllocationType(Base, TimestampMixin, ActiveFlagMixin):
    """Types of allocations (CHAP, ASD-UNIV, etc.)."""
    __tablename__ = 'allocation_type'

    __table_args__ = (
        Index('ix_allocation_type_panel', 'panel_id'),
    )

    allocation_type_id = Column(Integer, primary_key=True, autoincrement=True)
    allocation_type = Column(String(20), nullable=False)
    default_allocation_amount = Column(Float)
    fair_share_percentage = Column(Float)
    panel_id = Column(Integer, ForeignKey('panel.panel_id'))

    panel = relationship('Panel', back_populates='allocation_types')
    projects = relationship('Project', back_populates='allocation_type')

    def __str__(self):
        return f"{self.allocation_type}"

    def __repr__(self):
        return f"<AllocationType(type='{self.allocation_type}')>"


#-------------------------------------------------------------------------em-
