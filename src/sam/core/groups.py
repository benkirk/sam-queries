#-------------------------------------------------------------------------bh-
# Common Imports:
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
        Index('ix_adhoc_group_gid', 'unix_gid'),
        Index('ix_adhoc_group_name', 'group_name'),
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
    group_name = Column(String(30), nullable=False, unique=True)
    unix_gid = Column(Integer, nullable=False, unique=True)
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
        Index('ix_adhoc_group_tag_group', 'group_id'),
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
        Index('ix_adhoc_system_account_group', 'group_id'),
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


#-------------------------------------------------------------------------em-
