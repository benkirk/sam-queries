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
    """
    __tablename__ = 'xras_resource_repository_key_resource'

    xras_resource_key_id = Column(Integer, primary_key=True, autoincrement=True)
    xras_resource_key = Column(String(100), nullable=False, unique=True)
    resource_id = Column(Integer, ForeignKey('resources.resource_id'), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    creation_time = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    resource = relationship('Resource', back_populates='xras_resource_keys')

    def __str__(self):
        return f"{self.xras_resource_key}"

    def __repr__(self):
        return f"<XrasResourceRepositoryKeyResource(key='{self.xras_resource_key}')>"


# ============================================================================
# End of module
# ============================================================================


#-------------------------------------------------------------------------em-
