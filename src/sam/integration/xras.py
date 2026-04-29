#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class XrasResourceRepositoryKeyResource(Base):
    """
    Maps XRAS resource repository keys to local resources.

    This is an actual database TABLE (not a view).
    For XRAS views, see xras_views.py

    Note: This is a simple mapping table with just two columns:
    - resource_repository_key: The XRAS repository key (primary key)
    - resource_id: The local SAM resource ID (unique)
    """
    __tablename__ = 'xras_resource_repository_key_resource'

    __table_args__ = (
        Index('xras_resource_repo_key_resource_resource_rid_uniq',
              'resource_id', unique=True),
        Index('xras_resource_repo_key_resource_resource_repo_key_uniq',
              'resource_repository_key', unique=True),
    )

    resource_repository_key = Column(Integer, primary_key=True)
    resource_id = Column(Integer, ForeignKey('resources.resource_id'), nullable=False)

    resource = relationship('Resource', back_populates='xras_resource_keys')

    def __str__(self):
        return f"XRAS Key {self.resource_repository_key} -> Resource {self.resource_id}"

    def __repr__(self):
        return f"<XrasResourceRepositoryKeyResource(key={self.resource_repository_key}, resource_id={self.resource_id})>"


# ============================================================================
# End of module
# ============================================================================


#-------------------------------------------------------------------------em-
