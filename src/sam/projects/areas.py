#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class AreaOfInterest(Base, TimestampMixin, ActiveFlagMixin, SessionMixin):
    """Research areas for projects."""
    __tablename__ = 'area_of_interest'

    area_of_interest_id = Column(Integer, primary_key=True, autoincrement=True)
    area_of_interest = Column(String(255), nullable=False, unique=True)
    area_of_interest_group_id = Column(Integer, ForeignKey('area_of_interest_group.area_of_interest_group_id'), nullable=False)
    group = relationship('AreaOfInterestGroup', back_populates='areas')
    projects = relationship('Project', back_populates='area_of_interest')
    fos_mappings = relationship('FosAoi', back_populates='area_of_interest')

    def update(
        self,
        *,
        area_of_interest: Optional[str] = None,
        area_of_interest_group_id: Optional[int] = None,
        active: Optional[bool] = None,
    ) -> 'AreaOfInterest':
        """
        Update this AreaOfInterest record.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

        Args:
            area_of_interest: New name (NOT NULL, unique)
            area_of_interest_group_id: FK to AreaOfInterestGroup (NOT NULL)
            active: Whether the area is active

        Returns:
            self

        Raises:
            ValueError: If name is empty or group does not exist
        """
        if area_of_interest is not None:
            if not area_of_interest.strip():
                raise ValueError("area_of_interest name is required")
            self.area_of_interest = area_of_interest.strip()

        if area_of_interest_group_id is not None:
            group = self.session.get(AreaOfInterestGroup, area_of_interest_group_id)
            if not group:
                raise ValueError(f"AreaOfInterestGroup {area_of_interest_group_id} not found")
            self.area_of_interest_group_id = area_of_interest_group_id

        if active is not None:
            self.active = active

        self.session.flush()
        return self

    def __str__(self):
        return f"{self.area_of_interest}"

    def __repr__(self):
        return f"<AreaOfInterest(name='{self.area_of_interest}')>"


# ============================================================================
# Account and Allocation Management
# ============================================================================


#----------------------------------------------------------------------------
class AreaOfInterestGroup(Base, TimestampMixin, ActiveFlagMixin, SessionMixin):
    """Groupings for research areas."""
    __tablename__ = 'area_of_interest_group'

    area_of_interest_group_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)

    areas = relationship('AreaOfInterest', back_populates='group')

    def update(
        self,
        *,
        name: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> 'AreaOfInterestGroup':
        """
        Update this AreaOfInterestGroup record.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

        Args:
            name: New name (NOT NULL, unique)
            active: Whether the group is active

        Returns:
            self

        Raises:
            ValueError: If name is empty
        """
        if name is not None:
            if not name.strip():
                raise ValueError("name is required")
            self.name = name.strip()

        if active is not None:
            self.active = active

        self.session.flush()
        return self

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
