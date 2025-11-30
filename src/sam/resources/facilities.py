#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class Facility(Base, TimestampMixin, ActiveFlagMixin):
    """Facility classifications (NCAR, UNIV, etc.)."""
    __tablename__ = 'facility'

    facility_id = Column(Integer, primary_key=True, autoincrement=True)
    facility_name = Column(String(30), nullable=False, unique=True)
    code = Column(String(1), unique=True)
    description = Column(String(255), nullable=False)
    fair_share_percentage = Column(Float)

    panels = relationship('Panel', back_populates='facility')
    facility_resources = relationship('FacilityResource', back_populates='facility')
    project_codes = relationship('ProjectCode', back_populates='facility')

    def __str__(self):
        return f"{self.facility_name} - {self.code}"

    def __repr__(self):
        return f"<Facility(name='{self.facility_name}', code='{self.code}')>"

    def __eq__(self, other):
        """Two facilities are equal if they have the same facility_id."""
        if not isinstance(other, Facility):
            return False
        return self.facility_id is not None and self.facility_id == other.facility_id

    def __hash__(self):
        """Hash based on facility_id for set/dict operations."""
        return hash(self.facility_id) if self.facility_id is not None else hash(id(self))


#----------------------------------------------------------------------------
class FacilityResource(Base):
    """Maps facilities to resources with fair share percentages."""
    __tablename__ = 'facility_resource'

    __table_args__ = (
        Index('ix_facility_resource_facility', 'facility_id'),
        Index('ix_facility_resource_resource', 'resource_id'),
    )

    facility_resource_id = Column(Integer, primary_key=True, autoincrement=True)
    facility_id = Column(Integer, ForeignKey('facility.facility_id'), nullable=False)
    resource_id = Column(Integer, ForeignKey('resources.resource_id'), nullable=False)
    fair_share_percentage = Column(Float)
    creation_time = Column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    modified_time = Column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'), onupdate=text('CURRENT_TIMESTAMP'))

    facility = relationship('Facility', back_populates='facility_resources')
    resource = relationship('Resource', back_populates='facility_resources')


#----------------------------------------------------------------------------
class Panel(Base, TimestampMixin, ActiveFlagMixin):
    """Allocation review panels."""
    __tablename__ = 'panel'

    __table_args__ = (
        Index('ix_panel_facility', 'facility_id'),
    )

    panel_id = Column(Integer, primary_key=True, autoincrement=True)
    panel_name = Column(String(30), nullable=False, unique=True)
    description = Column(String(100))
    facility_id = Column(Integer, ForeignKey('facility.facility_id'), nullable=False)

    facility = relationship('Facility', back_populates='panels')
    allocation_types = relationship('AllocationType', back_populates='panel')
    panel_sessions = relationship('PanelSession', back_populates='panel')

    def __str__(self):
        return f"{self.panel_name}"

    def __repr__(self):
        return f"<Panel(name='{self.panel_name}')>"

    def __eq__(self, other):
        """Two panels are equal if they have the same panel_id."""
        if not isinstance(other, Panel):
            return False
        return self.panel_id is not None and self.panel_id == other.panel_id

    def __hash__(self):
        """Hash based on panel_id for set/dict operations."""
        return hash(self.panel_id) if self.panel_id is not None else hash(id(self))


#----------------------------------------------------------------------------
class PanelSession(Base, TimestampMixin):
    """Panel meeting sessions."""
    __tablename__ = 'panel_session'

    __table_args__ = (
        Index('ix_panel_session_panel', 'panel_id'),
    )

    panel_session_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False, unique=True)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)
    panel_meeting_date = Column(DateTime)
    description = Column(String(255))
    panel_id = Column(Integer, ForeignKey('panel.panel_id'), nullable=False)

    panel = relationship('Panel', back_populates='panel_sessions')


#----------------------------------------------------------------------------
class ProjectCode(Base):
    """
    Project code generation rules.

    Defines how project codes are generated for each facility and mnemonic code.
    The 'digits' field specifies how many digits the numeric portion should have.

    Example: Facility NCAR + Mnemonic Code UCAS + digits 4 -> UCAS0001, UCAS0002, etc.
    """
    __tablename__ = 'project_code'

    __table_args__ = (
        PrimaryKeyConstraint('facility_id', 'mnemonic_code_id', name='pk_project_code'),
    )

    facility_id = Column(Integer, ForeignKey('facility.facility_id'), nullable=False)
    mnemonic_code_id = Column(Integer, ForeignKey('mnemonic_code.mnemonic_code_id'), nullable=False)
    digits = Column(Integer, nullable=False)

    # Relationships
    facility = relationship('Facility', back_populates='project_codes')
    mnemonic_code = relationship('MnemonicCode', back_populates='project_codes')

    def __str__(self):
        return f"{self.facility_id}/{self.mnemonic_code_id} ({self.digits} digits)"

    def __repr__(self):
        return f"<ProjectCode(facility_id={self.facility_id}, mnemonic_code_id={self.mnemonic_code_id}, digits={self.digits})>"


# ============================================================================
# Project Area of Interest
# ============================================================================


#-------------------------------------------------------------------------em-
