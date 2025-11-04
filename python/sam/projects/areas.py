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

    def __repr__(self):
        return f"<AreaOfInterestGroup(name='{self.name}')>"


#-------------------------------------------------------------------------em-
