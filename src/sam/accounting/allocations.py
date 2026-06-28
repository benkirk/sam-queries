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
        Index('allocation_account_fk', 'account_id'),
        Index('allocation_allocation_fk', 'parent_allocation_id'),
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

    @property
    def root(self) -> 'Allocation':
        """Walk parent links to the root allocation of the shared tree.

        Returns self when this allocation is non-inheriting (already a root).
        """
        node = self
        while node.parent is not None:
            node = node.parent
        return node

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
        parent_allocation_id: 'Optional[int]' = None,
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

        account = Account.get_or_create(
            session, project_id=project_id, resource_id=resource_id
        )

        allocation = cls(
            account_id=account.account_id,
            amount=amount,
            start_date=start_date,
            end_date=end_date,
            description=description,
            parent_allocation_id=parent_allocation_id,
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
        Index('allocation_trans_alloc_fk', 'allocation_id'),
        Index('allocation_trans_user_fk', 'user_id'),
        Index('allocation_trans_related_fk', 'related_transaction_id'),
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
    """Transaction types for allocation audit trail.

    Two vocabularies coexist:

    1. **Python-side intent** (CREATE/EDIT/RENEW/DELETE/DETACH/LINK/EXPIRE):
       what the application means by the operation. Use these at call sites
       — they read clearly and are greppable.
    2. **Legacy DB strings** (NEW/ADJUSTMENT/SUPPLEMENT/EXTENSION/TRANSFER):
       the only values legacy SAM's Java enum will accept in the
       ``transaction_type`` column. Anything else throws on the Java side.

    ``log_allocation_transaction`` translates intent → DB string via
    ``LEGACY_TYPE_MAP`` on write, and prepends a ``[TAG]`` to
    ``transaction_comment`` so the original intent is recoverable via
    ``parse_intent()``. See ``LEGACY_TRANSACTION_TYPES`` for the closed
    set of strings that may appear in the column.

    .. note:: **Transitional design — retire when legacy SAM is decommissioned.**

       This intent → legacy-string mapping (``LEGACY_TYPE_MAP``,
       ``intent_filter``, ``parse_intent``, the ``[TAG]`` comment
       convention) exists solely so the new Python implementation can
       coexist with legacy SAM's Java enum validator on shared MySQL
       storage. Once legacy SAM is retired, the entire translation layer
       can be deleted: drop ``LEGACY_TYPE_MAP`` / ``parse_intent`` /
       ``intent_filter``, store the Python intent strings directly in
       ``transaction_type``, and stop emitting ``[TAG]`` prefixes. A
       one-shot data migration can rewrite existing rows
       (``ADJUSTMENT`` + ``[DELETE]`` → ``DELETE``, etc.) using the same
       ``parse_intent`` logic before deleting the helper.
    """
    # Python-side intents (translated to legacy strings on write)
    CREATE = "CREATE"
    EDIT = "EDIT"
    EXPIRE = "EXPIRE"
    DELETE = "DELETE"
    DETACH = "DETACH"
    LINK = "LINK"
    RENEW = "RENEW"
    # Legacy strings (also valid as call-site values; identity-mapped)
    NEW = "NEW"
    ADJUSTMENT = "ADJUSTMENT"
    SUPPLEMENT = "SUPPLEMENT"
    EXTENSION = "EXTENSION"
    TRANSFER = "TRANSFER"


#: Closed set of values that may appear in
#: ``allocation_transaction.transaction_type``. Legacy SAM's Java enum
#: validator throws on anything outside this set.
LEGACY_TRANSACTION_TYPES = frozenset({
    "NEW", "ADJUSTMENT", "SUPPLEMENT", "EXTENSION", "TRANSFER",
})


#: Maps Python-side intent → ``(db_string, optional_comment_tag)``.
#:
#: The tag — when present — is prepended to ``transaction_comment`` as
#: ``[TAG] <comment>`` so we can recover the high-level intent without
#: needing a second column. Only intents that collapse onto an
#: ambiguous DB string (e.g. CREATE vs RENEW both map to NEW) get a
#: tag; identity mappings stay untagged.
#:
#: For DELETE/DETACH/LINK — operations that don't change ``amount`` —
#: the writer also forces ``transaction_amount = 0.0`` so the legacy
#: replay's ``addAmount`` is a no-op (these map to ADJUSTMENT, which
#: replay treats as additive).
#:
#: **DELETE THIS WHEN LEGACY SAM IS RETIRED.** See
#: ``AllocationTransactionType`` docstring for the retirement plan.
LEGACY_TYPE_MAP: dict = {
    AllocationTransactionType.CREATE:     ("NEW",        None),
    AllocationTransactionType.RENEW:      ("NEW",        "RENEW"),
    AllocationTransactionType.EDIT:       ("ADJUSTMENT", None),
    AllocationTransactionType.EXPIRE:     ("EXTENSION",  None),
    AllocationTransactionType.DELETE:     ("ADJUSTMENT", "DELETE"),
    AllocationTransactionType.DETACH:     ("ADJUSTMENT", "DETACH"),
    AllocationTransactionType.LINK:       ("ADJUSTMENT", "LINK"),
    AllocationTransactionType.NEW:        ("NEW",        None),
    AllocationTransactionType.ADJUSTMENT: ("ADJUSTMENT", None),
    AllocationTransactionType.SUPPLEMENT: ("SUPPLEMENT", None),
    AllocationTransactionType.EXTENSION:  ("EXTENSION",  None),
    AllocationTransactionType.TRANSFER:   ("TRANSFER",   None),
}

#: Tags that must NEVER change once written (Stream A backfill rows
#: also use a different ``[REMEDIATION YYYY-MM-DD]`` prefix; that one
#: is NOT a type-tag and is not parsed by parse_intent).
_RECOGNIZED_TAGS = frozenset({"RENEW", "DELETE", "DETACH", "LINK"})


def parse_intent(txn: 'AllocationTransaction') -> AllocationTransactionType:
    """Recover the original Python-side intent from a stored row.

    Reads ``transaction_type`` + the optional ``[TAG]`` prefix on
    ``transaction_comment``. Untagged rows return the intent that
    naturally corresponds to the DB string (e.g. ADJUSTMENT → EDIT,
    since EDIT is the canonical Python-side name; NEW → CREATE).

    Returns the original ``AllocationTransactionType`` even for rows
    written before B3 landed (those are still legacy strings, so the
    untagged-fallback path applies).
    """
    db_type = txn.transaction_type
    comment = txn.transaction_comment or ""

    # Look for a leading [TAG] prefix
    if comment.startswith("[") and "]" in comment:
        tag = comment[1:comment.index("]")]
        if tag in _RECOGNIZED_TAGS:
            try:
                return AllocationTransactionType[tag]
            except KeyError:
                pass

    # Untagged: map DB string → canonical Python intent
    _DB_TO_INTENT = {
        "NEW":        AllocationTransactionType.CREATE,
        "ADJUSTMENT": AllocationTransactionType.EDIT,
        "SUPPLEMENT": AllocationTransactionType.SUPPLEMENT,
        "EXTENSION":  AllocationTransactionType.EXTENSION,
        "TRANSFER":   AllocationTransactionType.TRANSFER,
    }
    return _DB_TO_INTENT.get(db_type, AllocationTransactionType.EDIT)


def intent_filter(intent: AllocationTransactionType):
    """Build a SQLAlchemy filter expression matching rows of a given intent.

    Translates Python-side intent → legacy DB string + optional ``[TAG]``
    comment prefix. For tagged intents (RENEW, DELETE, DETACH, LINK) the
    expression matches both the DB string AND the leading ``[TAG]`` in
    the comment. For untagged intents whose DB string also serves a
    different tagged intent (e.g. EDIT and DELETE both store as
    ``ADJUSTMENT``), the filter excludes any ``[TAG]``-prefixed comment
    so EDIT and DELETE remain distinguishable.

    Example::

        rows = session.query(AllocationTransaction).filter(
            AllocationTransaction.allocation_id == aid,
            intent_filter(AllocationTransactionType.RENEW),
        ).all()
    """
    db_type, tag = LEGACY_TYPE_MAP[intent]
    type_match = AllocationTransaction.transaction_type == db_type
    if tag is not None:
        return and_(
            type_match,
            AllocationTransaction.transaction_comment.like(f"[{tag}]%"),
        )

    # Untagged: exclude rows whose comment starts with a recognized [TAG],
    # so that e.g. EDIT (untagged ADJUSTMENT) doesn't match DELETE
    # (`[DELETE] ...` ADJUSTMENT). Backfill rows with [REMEDIATION ...]
    # comments stay matchable here because REMEDIATION is not a
    # recognized intent tag.
    tagged_clauses = [
        AllocationTransaction.transaction_comment.like(f"[{t}]%")
        for t in _RECOGNIZED_TAGS
    ]
    return and_(
        type_match,
        or_(
            AllocationTransaction.transaction_comment.is_(None),
            ~or_(*tagged_clauses),
        ),
    )


def replay_amount(transactions, *, until: Optional[datetime] = None) -> float:
    """Replay a list of ``AllocationTransaction`` rows and return the resulting amount.

    Mirrors legacy SAM's ``DateBoundedAllocationAmount`` /
    ``AllocationTransactionType`` semantics:

    - ``NEW``: ``setAmount(transaction_amount)`` (resets running total)
    - ``ADJUSTMENT`` / ``SUPPLEMENT`` / ``TRANSFER``:
      ``addAmount(transaction_amount)`` (delta)
    - ``EXTENSION``: no amount change

    Used by the Stream A backfill script to *prove* that a corrective
    row repairs the audit-trail sum before any DB write. Also used by
    tests as the post-fix invariant: replay(history) ≈ allocation.amount.

    Args:
        transactions: iterable of AllocationTransaction rows. Replayed
            in ``creation_time`` ascending order.
        until: optional cutoff — only rows with ``creation_time <= until``
            are applied (matches legacy "as of date" reporting).
    """
    rows = sorted(
        (t for t in transactions
         if until is None or (t.creation_time and t.creation_time <= until)),
        key=lambda t: (t.creation_time, t.allocation_transaction_id or 0),
    )
    amount = 0.0
    for t in rows:
        if t.transaction_type == "NEW":
            amount = float(t.transaction_amount or 0.0)
        elif t.transaction_type in ("ADJUSTMENT", "SUPPLEMENT", "TRANSFER"):
            amount += float(t.transaction_amount or 0.0)
        # EXTENSION: end_date only — no amount effect
    return amount


# ============================================================================
# Project Management
# ============================================================================


#----------------------------------------------------------------------------
class AllocationType(Base, TimestampMixin, ActiveFlagMixin, SessionMixin):
    """Types of allocations (CHAP, ASD-UNIV, etc.)."""
    __tablename__ = 'allocation_type'

    __table_args__ = (
        Index('idx_allocation_type', 'panel_id'),
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
