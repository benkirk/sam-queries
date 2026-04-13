#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
import enum
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class InheritingAllocationException(Exception):
    """Raised when a direct mutation is attempted on an inheriting (child) allocation."""
    pass


#----------------------------------------------------------------------------
class Allocation(Base, TimestampMixin, SoftDeleteMixin, SessionMixin):
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

    @validates('end_date')
    def _validate_end_date(self, key, value):
        return normalize_end_date(value)

    account = relationship('Account', back_populates='allocations')
    children = relationship('Allocation', back_populates='parent', cascade='all')
    parent = relationship('Allocation', remote_side=[allocation_id], back_populates='children')
    transactions = relationship('AllocationTransaction', back_populates='allocation', cascade='all, delete-orphan')
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

    @property
    def is_inheriting(self) -> bool:
        """True if this allocation is a child node in the shared-allocation tree."""
        return self.parent_allocation_id is not None

    def _walk_tree(self, action_func, *args, **kwargs) -> None:
        """
        Applies action_func to this node and recursively to all descendants.
        Equivalent to Java's TreeWalker.walk(). Handles trees of any depth.
        """
        action_func(self, *args, **kwargs)
        for child in self.children:
            child._walk_tree(action_func, *args, **kwargs)

    def extend_allocation(self, new_end_date: datetime, user_id: int) -> None:
        """
        Extend end_date across the entire allocation tree.
        Must be called on the master (root) allocation.

        Sets propagated=True on all child/grandchild transactions. Cascades
        to all descendants regardless of depth (handles grandchild trees).

        NOTE: Does NOT commit. Caller is responsible for committing.

        Args:
            new_end_date: New end date to apply to the entire tree.
            user_id: User performing the extension (for audit trail).

        Raises:
            InheritingAllocationException: If called on a child allocation.
            ValueError: If new_end_date is before start_date.
        """
        if self.is_inheriting:
            raise InheritingAllocationException(
                "extend_allocation() must be called on the master (root) allocation, "
                "not on an inheriting child."
            )
        if new_end_date < self.start_date:
            raise ValueError(
                f"new_end_date ({new_end_date}) cannot be before start_date ({self.start_date})"
            )

        def _do_extend(node: 'Allocation') -> None:
            node.end_date = new_end_date
            txn = AllocationTransaction(
                allocation_id=node.allocation_id,
                user_id=user_id,
                transaction_type=AllocationTransactionType.EXTENSION,
                alloc_start_date=node.start_date,
                alloc_end_date=new_end_date,
                transaction_amount=node.amount,
                propagated=(node.parent_allocation_id is not None),
                transaction_comment=f"End date extended to {new_end_date.strftime('%Y-%m-%d')}",
            )
            node.session.add(txn)

        self._walk_tree(_do_extend)
        self.session.flush()

    @classmethod
    def create(
        cls,
        session,
        *,
        project_id: int,
        resource_id: int,
        amount: float,
        start_date: 'datetime',
        end_date: 'Optional[datetime]' = None,
        description: 'Optional[str]' = None,
    ) -> 'Allocation':
        """Create a new allocation for a project + resource pair.

        Gets or creates the Account linking project ↔ resource, then
        instantiates and flushes the Allocation.

        Does NOT log an audit transaction — callers that need an audit trail
        (e.g. sam.manage.allocations.create_allocation) should call
        log_allocation_transaction() after this returns.

        Does NOT commit; caller must wrap in management_transaction().

        Args:
            session:     SQLAlchemy session.
            project_id:  FK to Project.
            resource_id: FK to Resource.
            amount:      Allocation amount (must be > 0).
            start_date:  Start of allocation period.
            end_date:    End of allocation period (None = open-ended).
            description: Optional human-readable note.

        Returns:
            Newly created and flushed Allocation instance.
        """
        from sam.accounting.accounts import Account

        if amount <= 0:
            raise ValueError(f"Amount must be > 0, got {amount}")

        account = Account.get_by_project_and_resource(
            session, project_id, resource_id, exclude_deleted=True
        )
        if account is None:
            account = Account(project_id=project_id, resource_id=resource_id)
            session.add(account)
            session.flush()

        allocation = cls(
            account_id=account.account_id,
            amount=amount,
            start_date=start_date,
            end_date=end_date,
            description=description,
        )
        session.add(allocation)
        session.flush()
        return allocation

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

    @validates('alloc_end_date')
    def _validate_alloc_end_date(self, key, value):
        return normalize_end_date(value)

    auth_at_panel_mtg = Column(Boolean)
    transaction_comment = Column(Text)
    propagated = Column(Boolean, nullable=False, default=False)

    creation_time = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    allocation = relationship('Allocation', back_populates='transactions')
    related_transaction = relationship('AllocationTransaction', remote_side=[allocation_transaction_id], back_populates='related_transactions')
    related_transactions = relationship('AllocationTransaction', back_populates='related_transaction')
    user = relationship('User', back_populates='allocation_transactions')

    def __str__(self):
        return f"{self.transaction_type}: {self.transaction_amount} (allocation {self.allocation_id})"

    def __repr__(self):
        return (
            f"<AllocationTransaction id={self.allocation_transaction_id} "
            f"type={self.transaction_type!r} amount={self.transaction_amount} "
            f"allocation_id={self.allocation_id}>"
        )


#----------------------------------------------------------------------------
class AllocationTransactionType(enum.StrEnum):
    """Transaction types for allocation audit trail."""
    # Python-side types (new operations)
    CREATE = "CREATE"
    EDIT = "EDIT"
    TRANSFER = "TRANSFER"
    ADJUSTMENT = "ADJUSTMENT"
    EXPIRE = "EXPIRE"
    DELETE = "DELETE"
    # Legacy Java-side types (present in existing DB data)
    NEW = "NEW"
    EXTENSION = "EXTENSION"
    SUPPLEMENT = "SUPPLEMENT"


# ============================================================================
# Project Management
# ============================================================================


#----------------------------------------------------------------------------
class AllocationType(Base, TimestampMixin, ActiveFlagMixin, SessionMixin):
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

    def update(
        self,
        *,
        default_allocation_amount: Optional[float] = None,
        fair_share_percentage: Optional[float] = None,
        active: Optional[bool] = None,
    ) -> 'AllocationType':
        """
        Update this AllocationType record.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

        Args:
            default_allocation_amount: New default amount (must be >= 0 if provided)
            fair_share_percentage: Percentage 0–100
            active: Whether the allocation type is active

        Returns:
            self

        Raises:
            ValueError: If validation fails
        """
        if default_allocation_amount is not None:
            if default_allocation_amount < 0:
                raise ValueError("default_allocation_amount must be >= 0")
            self.default_allocation_amount = default_allocation_amount

        if fair_share_percentage is not None:
            if not (0 <= fair_share_percentage <= 100):
                raise ValueError("fair_share_percentage must be between 0 and 100")
            self.fair_share_percentage = fair_share_percentage

        if active is not None:
            self.active = active

        self.session.flush()
        return self

    @classmethod
    def create(
        cls,
        session,
        *,
        allocation_type: str,
        panel_id: Optional[int] = None,
        default_allocation_amount: Optional[float] = None,
        fair_share_percentage: Optional[float] = None,
    ) -> 'AllocationType':
        """
        Create a new AllocationType.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.
        """
        if not allocation_type or not allocation_type.strip():
            raise ValueError("allocation_type is required")
        if default_allocation_amount is not None and default_allocation_amount < 0:
            raise ValueError("default_allocation_amount must be >= 0")
        if fair_share_percentage is not None and not (0 <= fair_share_percentage <= 100):
            raise ValueError("fair_share_percentage must be between 0 and 100")

        obj = cls(
            allocation_type=allocation_type.strip(),
            panel_id=panel_id,
            default_allocation_amount=default_allocation_amount,
            fair_share_percentage=fair_share_percentage,
        )
        session.add(obj)
        session.flush()
        return obj

    def __str__(self):
        return f"{self.allocation_type}"

    def __repr__(self):
        return f"<AllocationType(type='{self.allocation_type}')>"


#-------------------------------------------------------------------------em-
