#-------------------------------------------------------------------------bh-
# Common Imports:
from dataclasses import dataclass
from ..base import *
#-------------------------------------------------------------------------eh-


# ---------------------------------------------------------------------------
# System-wide group conventions
# ---------------------------------------------------------------------------
# The "ncar" unix group (gid 1000) is a system-wide LDAP convention every
# HPC user belongs to. It is NOT materialized as an adhoc_group row, so it
# will never resolve through AdhocGroup.get_by_unix_gid(). Callers that
# want to render a name for a unix gid should consult these constants as a
# fallback.

DEFAULT_COMMON_GROUP = 'ncar'
DEFAULT_COMMON_GROUP_GID = 1000


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class AdhocGroup(Base, ActiveFlagMixin):
    """Unix groups for organizing users."""
    __tablename__ = 'adhoc_group'

    __table_args__ = (
        Index('adhoc_group_group_name_uk', 'group_name', unique=True),
        Index('adhoc_group_unix_gid_uk', 'unix_gid', unique=True),
    )

    def __eq__(self, other):
        """Two groups are equal if they have the same group_id."""
        if not isinstance(other, AdhocGroup):
            return False
        return self.group_id is not None and self.group_id == other.group_id

    def __hash__(self):
        """Hash based on group_id for set/dict operations."""
        return hash(self.group_id) if self.group_id is not None else hash(id(self))

    group_id = Column(Integer, primary_key=True, autoincrement=True)
    group_name = Column(String(30), nullable=False)
    unix_gid = Column(Integer, nullable=False)
    creation_time = Column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    pdb_modified_time = Column(TIMESTAMP)
    idms_sync_token = Column(String(64))

    tags = relationship('AdhocGroupTag', back_populates='group', cascade='all, delete-orphan')
    system_accounts = relationship('AdhocSystemAccountEntry', back_populates='group', cascade='all, delete-orphan')

    @classmethod
    def get_by_name(cls, session, group_name: str) -> Optional['AdhocGroup']:
        """
        Get a group by its name.

        Args:
            session: SQLAlchemy session
            group_name: Name of the group

        Returns:
            AdhocGroup object if found, None otherwise

        Example:
            >>> group = AdhocGroup.get_by_name(session, 'research_group')
        """
        return session.query(cls).filter(cls.group_name == group_name).first()

    @classmethod
    def get_by_unix_gid(cls, session, unix_gid: int) -> Optional['AdhocGroup']:
        """
        Get a group by its unix gid.

        Note: `users.primary_gid` stores unix gids, so this is the correct
        lookup for resolving a user's primary group name — despite the
        column's FK declaration to `adhoc_group.group_id`.
        """
        return session.query(cls).filter(cls.unix_gid == unix_gid).first()

    def __str__(self):
        return f"{self.group_name}"

    def __repr__(self):
        return f"<AdhocGroup(name='{self.group_name}', gid={self.unix_gid})>"


#----------------------------------------------------------------------------
class AdhocGroupTag(Base):
    """Tags for categorizing adhoc groups."""
    __tablename__ = 'adhoc_group_tag'

    __table_args__ = (
        Index('adhoc_group_tag_uk', 'group_id', 'tag', unique=True),
    )

    adhoc_group_tag_id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey('adhoc_group.group_id'), nullable=False)
    tag = Column(String(40), nullable=False)
    creation_time = Column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    group = relationship('AdhocGroup', back_populates='tags')

    def __str__(self):
        return f"'{self.tag}' (group {self.group_id})"

    def __repr__(self):
        return f"<AdhocGroupTag(id={self.adhoc_group_tag_id}, tag='{self.tag}', group_id={self.group_id})>"


#----------------------------------------------------------------------------
class AdhocSystemAccountEntry(Base):
    """System account entries for adhoc groups."""
    __tablename__ = 'adhoc_system_account_entry'

    __table_args__ = (
        Index('adhoc_system_account_entry_uk',
              'group_id', 'access_branch_name', 'username',
              unique=True),
    )

    entry_id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey('adhoc_group.group_id'), nullable=False)
    access_branch_name = Column(String(40), nullable=False)
    username = Column(String(12), nullable=False)
    creation_time = Column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    group = relationship('AdhocGroup', back_populates='system_accounts')

    def __str__(self):
        return f"{self.username} ({self.access_branch_name})"

    def __repr__(self):
        return f"<AdhocSystemAccountEntry(id={self.entry_id}, username='{self.username}', branch='{self.access_branch_name}')>"


# ---------------------------------------------------------------------------
# Group-name resolution
# ---------------------------------------------------------------------------

def resolve_group_name(session, unix_gid):
    """Return the adhoc group name for a unix gid, or the system default
    when the gid is the well-known DEFAULT_COMMON_GROUP_GID.

    Returns None for unresolved non-default gids and for None inputs.
    """
    if unix_gid is None:
        return None
    g = AdhocGroup.get_by_unix_gid(session, unix_gid)
    if g is not None:
        return g.group_name
    if unix_gid == DEFAULT_COMMON_GROUP_GID:
        return DEFAULT_COMMON_GROUP
    return None


# ============================================================================
# Resource Management
# ============================================================================


class NoAvailableGidError(RuntimeError):
    """Raised when every gid_allocation block is exhausted."""


@dataclass(frozen=True)
class GidPoolSummary:
    """Aggregate view of the GID allocation pool, for admin UIs."""
    available: int               # GIDs still drawable across all blocks
    total: int                   # sum of block sizes across all blocks
    block_count: int             # number of blocks in the table
    exhausted_block_count: int   # subset of blocks with no remaining GIDs


#----------------------------------------------------------------------------
class GidAllocation(Base):
    """Block of Unix GIDs available for assignment to new projects.

    Each row defines a half-open allocation block ``[start_gid, end_gid]``
    (both endpoints inclusive). ``next_gid`` is the next GID to hand out;
    it is ``NULL`` until the first allocation, after which it advances by
    one for each GID drawn. When ``next_gid > end_gid`` the block is
    exhausted. ``allocate_next_gid()`` picks the lowest-``start_gid`` block
    that still has capacity.
    """
    __tablename__ = 'gid_allocation'

    gid_allocation_id = Column(Integer, primary_key=True, autoincrement=True)
    # MySQL columns are camelCase; map to snake_case attributes.
    start_gid = Column('startGid', Integer, nullable=False)
    next_gid = Column('nextGid', Integer)
    end_gid = Column('endGid', Integer, nullable=False)
    creation_time = Column(DateTime, nullable=False,
                           default=datetime.now,
                           server_default=text('CURRENT_TIMESTAMP'))
    # No server_default — MySQL defines this column as NULL DEFAULT NULL
    # with ON UPDATE CURRENT_TIMESTAMP.
    modified_time = Column(TIMESTAMP, onupdate=text('CURRENT_TIMESTAMP'))
    idms_sync_token = Column(String(64))

    def __eq__(self, other):
        if not isinstance(other, GidAllocation):
            return False
        return (self.gid_allocation_id is not None
                and self.gid_allocation_id == other.gid_allocation_id)

    def __hash__(self):
        return (hash(self.gid_allocation_id)
                if self.gid_allocation_id is not None else hash(id(self)))

    def __str__(self):
        n = self.next_gid if self.next_gid is not None else self.start_gid
        return f"gid_allocation[{self.start_gid}-{self.end_gid}] next={n}"

    def __repr__(self):
        return (f"<GidAllocation(id={self.gid_allocation_id}, "
                f"start={self.start_gid}, next={self.next_gid}, "
                f"end={self.end_gid})>")

    # --- introspection ------------------------------------------------------
    @property
    def is_initialized(self) -> bool:
        """True once at least one GID has been drawn from this block."""
        return self.next_gid is not None

    @property
    def is_exhausted(self) -> bool:
        """True when no more GIDs remain in this block."""
        if self.next_gid is None:
            return False
        return self.next_gid > self.end_gid

    @property
    def has_capacity(self) -> bool:
        """True when at least one GID is still available in this block."""
        return not self.is_exhausted

    @property
    def available_count(self) -> int:
        """Number of GIDs still available in this block."""
        effective_next = (self.next_gid
                          if self.next_gid is not None else self.start_gid)
        return max(0, self.end_gid - effective_next + 1)

    # --- query helpers ------------------------------------------------------
    @classmethod
    def list_blocks(cls, session) -> List['GidAllocation']:
        """All blocks ordered by ``start_gid`` ascending."""
        return session.query(cls).order_by(cls.start_gid).all()

    @classmethod
    def pool_summary(cls, session) -> GidPoolSummary:
        """Aggregate view of every block.

        Walks all blocks once and tallies remaining capacity, total
        capacity (block size), block count, and how many blocks are
        exhausted. Read-only: no rows are locked or mutated.
        """
        blocks = cls.list_blocks(session)
        available = sum(b.available_count for b in blocks)
        total = sum(b.end_gid - b.start_gid + 1 for b in blocks)
        exhausted = sum(1 for b in blocks if b.is_exhausted)
        return GidPoolSummary(
            available=available,
            total=total,
            block_count=len(blocks),
            exhausted_block_count=exhausted,
        )

    @classmethod
    def _available_block_query(cls, session):
        """Base query: blocks with capacity, ordered by start_gid asc.

        A block has capacity when ``next_gid IS NULL`` (pristine, never
        allocated from) or ``next_gid <= end_gid`` (not yet exhausted).
        """
        return (session.query(cls)
                .filter(or_(cls.next_gid.is_(None),
                            cls.next_gid <= cls.end_gid))
                .order_by(cls.start_gid))

    @classmethod
    def next_available_block(cls, session, *,
                             lock: bool = False) -> Optional['GidAllocation']:
        """Lowest-``start_gid`` block with remaining capacity, or None."""
        q = cls._available_block_query(session)
        if lock:
            q = q.with_for_update()
        return q.first()

    # --- allocation ---------------------------------------------------------
    @classmethod
    def allocate_next_gid(cls, session) -> int:
        """Atomically draw the next available GID.

        Uses a two-step lock pattern to avoid the gap-lock deadlocks that
        MySQL's InnoDB would otherwise produce under REPEATABLE READ when
        two concurrent transactions each scan the table with
        ``WHERE next_gid IS NULL OR next_gid <= end_gid ORDER BY start_gid
        LIMIT 1 FOR UPDATE``:

          1. Pick the lowest-``start_gid`` block with capacity using an
             ordinary read (no row locks, no gap locks).
          2. Re-fetch the candidate ``FOR UPDATE`` by primary key. PK
             equality locks are pure row locks — InnoDB does not need a
             gap lock to enforce them — so two concurrent allocations
             serialize cleanly without ever deadlocking.
          3. Re-verify that the locked block still has capacity. A
             second allocator that locked the same block first could
             have drained it between (1) and (2). If so, recurse to pick
             the next candidate. Progress is guaranteed: every losing
             race advances some block's ``next_gid`` by one.

        Returns the chosen GID. Raises ``NoAvailableGidError`` when every
        block is exhausted.
        """
        candidate = cls._available_block_query(session).first()
        if candidate is None:
            raise NoAvailableGidError("No available GID blocks!")

        block = (session.query(cls)
                 .filter(cls.gid_allocation_id == candidate.gid_allocation_id)
                 .with_for_update()
                 .one())

        if not block.has_capacity:
            # Lost the race; another transaction drained this block while
            # we were acquiring the lock. Try again with a fresh scan.
            return cls.allocate_next_gid(session)

        chosen = (block.next_gid
                  if block.next_gid is not None else block.start_gid)
        block.next_gid = chosen + 1
        session.flush()
        return chosen


#-------------------------------------------------------------------------em-
