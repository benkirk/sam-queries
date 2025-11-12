#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class AreaOfInterest(Base, TimestampMixin, ActiveFlagMixin):
    """Research areas for projects."""
    __tablename__ = 'area_of_interest'

    area_of_interest_id = Column(Integer, primary_key=True, autoincrement=True)
    area_of_interest = Column(String(255), nullable=False, unique=True)
    area_of_interest_group_id = Column(Integer, ForeignKey('area_of_interest_group.area_of_interest_group_id'), nullable=False)
    group = relationship('AreaOfInterestGroup', back_populates='areas')
    projects = relationship('Project', back_populates='area_of_interest')
    fos_mappings = relationship('FosAoi', back_populates='area_of_interest')

    def __str__(self):
        return f"{self.area_of_interest}"

    def __repr__(self):
        return f"<AreaOfInterest(name='{self.area_of_interest}')>"


# ============================================================================
# Account and Allocation Management
# ============================================================================


#----------------------------------------------------------------------------
class AreaOfInterestGroup(Base, TimestampMixin, ActiveFlagMixin):
    """Groupings for research areas."""
    __tablename__ = 'area_of_interest_group'

    area_of_interest_group_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)

    areas = relationship('AreaOfInterest', back_populates='group')

    def __str__(self):
        return f"{self.name}"

    def __repr__(self):
        return f"<AreaOfInterestGroup(name='{self.name}')>"


#----------------------------------------------------------------------------
class FosAoi(Base, TimestampMixin):
    """
    Maps NSF Field of Science (FOS) codes to Areas of Interest.

    FOS (Field of Science) is an external classification system used by NSF.
    This table maps FOS codes to internal SAM Areas of Interest.
    """
    __tablename__ = 'fos_aoi'

    __table_args__ = (
        Index('ix_fos_aoi_fos', 'fos_id', unique=True),
        Index('ix_fos_aoi_area', 'area_of_interest_id'),
    )

    fos_aoi_id = Column(Integer, primary_key=True, autoincrement=True)
    fos_id = Column(Integer, nullable=False, unique=True)
    area_of_interest_id = Column(Integer, ForeignKey('area_of_interest.area_of_interest_id'), nullable=False)
    fos = Column(String(255))  # FOS description/name

    # Relationships
    area_of_interest = relationship('AreaOfInterest', back_populates='fos_mappings')

    def __str__(self):
        return f"FOS {self.fos_id} -> {self.fos}"

    def __repr__(self):
        return f"<FosAoi(fos_id={self.fos_id}, area_of_interest_id={self.area_of_interest_id})>"


#-------------------------------------------------------------------------em-
