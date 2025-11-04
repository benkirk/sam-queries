#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


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

    tags = relationship('AdhocGroupTag', back_populates='group')
    system_accounts = relationship('AdhocSystemAccountEntry', back_populates='group')

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


# ============================================================================
# Resource Management
# ============================================================================


#-------------------------------------------------------------------------em-
