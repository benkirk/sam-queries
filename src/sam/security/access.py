#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class AccessBranch(Base):
    """Access branches for resource access control."""
    __tablename__ = 'access_branch'

    access_branch_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(40), nullable=False, unique=True)

    resources = relationship('AccessBranchResource', back_populates='access_branch')

    def __str__(self):
        return f"{self.name}"

    def __repr__(self):
        return f"<AccessBranch(name='{self.name}')>"


#----------------------------------------------------------------------------
class AccessBranchResource(Base):
    """Maps access branches to resources."""
    __tablename__ = 'access_branch_resource'

    __table_args__ = (
        Index('ix_access_branch_resource_branch', 'access_branch_id'),
        Index('ix_access_branch_resource_resource', 'resource_id'),
    )

    access_branch_id = Column(Integer, ForeignKey('access_branch.access_branch_id'),
                              primary_key=True)
    resource_id = Column(Integer, ForeignKey('resources.resource_id'), primary_key=True)

    access_branch = relationship('AccessBranch', back_populates='resources')
    resource = relationship('Resource', back_populates='access_branch_resources')

# ============================================================================
# Utility/Operational Tables
# ============================================================================


#-------------------------------------------------------------------------em-
