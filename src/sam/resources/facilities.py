#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class Facility(Base, TimestampMixin, ActiveFlagMixin, SessionMixin):
    """Facility classifications (NCAR, UNIV, etc.)."""
    __tablename__ = 'facility'

    facility_id = Column(Integer, primary_key=True, autoincrement=True)
    facility_name = Column(String(30), nullable=False, unique=True)
    code = Column(String(1), unique=True)
    description = Column(String(255), nullable=False)
    fair_share_percentage = Column(Float)

    panels = relationship('Panel', back_populates='facility', cascade='save-update, merge')
    facility_resources = relationship('FacilityResource', back_populates='facility')
    project_codes = relationship('ProjectCode', back_populates='facility', cascade='all')

    def update(
        self,
        *,
        description: Optional[str] = None,
        fair_share_percentage: Optional[float] = None,
        active: Optional[bool] = None,
    ) -> 'Facility':
        """
        Update this Facility record.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

        Args:
            description: New description (NOT NULL column — empty string stored as '')
            fair_share_percentage: Percentage 0–100
            active: Whether the facility is active

        Returns:
            self

        Raises:
            ValueError: If validation fails
        """
        if description is not None:
            # description is NOT NULL in schema — store empty string rather than None
            self.description = description.strip()

        if fair_share_percentage is not None:
            if not (0 <= fair_share_percentage <= 100):
                raise ValueError("fair_share_percentage must be between 0 and 100")
            self.fair_share_percentage = fair_share_percentage

        if active is not None:
            self.active = active

        self.session.flush()
        return self

    @classmethod
    def create(
        cls,
        session,
        *,
        facility_name: str,
        description: str,
        code: Optional[str] = None,
        fair_share_percentage: Optional[float] = None,
    ) -> 'Facility':
        """
        Create a new Facility.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.
        """
        if not facility_name or not facility_name.strip():
            raise ValueError("facility_name is required")
        if not description or not description.strip():
            raise ValueError("description is required")
        if fair_share_percentage is not None and not (0 <= fair_share_percentage <= 100):
            raise ValueError("fair_share_percentage must be between 0 and 100")

        obj = cls(
            facility_name=facility_name.strip(),
            description=description.strip(),
            code=code.strip() if code and code.strip() else None,
            fair_share_percentage=fair_share_percentage,
        )
        session.add(obj)
        session.flush()
        return obj

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

    def __str__(self):
        return f"<{self.facility.facility_name}/{self.resource.resource_name}>"

    def __repr__(self):
        return f"<FacilityResource(id={self.facility_resource_id})>"

#----------------------------------------------------------------------------
class Panel(Base, TimestampMixin, ActiveFlagMixin, SessionMixin):
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
    allocation_types = relationship('AllocationType', back_populates='panel', cascade='save-update, merge')
    panel_sessions = relationship('PanelSession', back_populates='panel', cascade='save-update, merge')

    def update(
        self,
        *,
        description: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> 'Panel':
        """
        Update this Panel record.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

        Args:
            description: New description (nullable — pass empty string to clear)
            active: Whether the panel is active

        Returns:
            self
        """
        if description is not None:
            self.description = description if description.strip() else None

        if active is not None:
            self.active = active

        self.session.flush()
        return self

    @classmethod
    def create(
        cls,
        session,
        *,
        panel_name: str,
        facility_id: int,
        description: Optional[str] = None,
    ) -> 'Panel':
        """
        Create a new Panel.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.
        """
        if not panel_name or not panel_name.strip():
            raise ValueError("panel_name is required")

        obj = cls(
            panel_name=panel_name.strip(),
            facility_id=facility_id,
            description=description.strip() if description and description.strip() else None,
        )
        session.add(obj)
        session.flush()
        return obj

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
class PanelSession(Base, TimestampMixin, SessionMixin):
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

    @validates('end_date')
    def _validate_end_date(self, key, value):
        return normalize_end_date(value)
    description = Column(String(255))
    panel_id = Column(Integer, ForeignKey('panel.panel_id'), nullable=False)

    panel = relationship('Panel', back_populates='panel_sessions')

    def update(
        self,
        *,
        description: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        panel_meeting_date: Optional[datetime] = None,
    ) -> 'PanelSession':
        """
        Update this PanelSession record.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

        Args:
            description: New description (nullable)
            start_date: New start date
            end_date: New end date — must be after start_date if both known
            panel_meeting_date: New panel meeting date (no constraint)

        Returns:
            self

        Raises:
            ValueError: If end_date <= start_date
        """
        if start_date is not None:
            self.start_date = start_date

        if end_date is not None:
            effective_start = start_date or self.start_date
            if effective_start and end_date <= effective_start:
                raise ValueError("end_date must be after start_date")
            self.end_date = end_date

        if panel_meeting_date is not None:
            self.panel_meeting_date = panel_meeting_date

        if description is not None:
            self.description = description if description.strip() else None

        self.session.flush()
        return self

    def is_active_at(self, check_date=None) -> bool:
        """Check if panel session is active at a given date."""
        if check_date is None:
            check_date = datetime.now()
        if self.start_date > check_date:
            return False
        if self.end_date is not None and self.end_date < check_date:
            return False
        return True

    @hybrid_property
    def is_active(self) -> bool:
        """Check if panel session is currently active (Python side)."""
        return self.is_active_at()

    @is_active.expression
    def is_active(cls):
        """Check if panel session is currently active (SQL side)."""
        now = func.now()
        return and_(
            cls.start_date <= now,
            or_(cls.end_date.is_(None), cls.end_date >= now)
        )

    def __str__(self):
        return f"{self.name} ({self.start_date} - {self.end_date})"

    def __repr__(self):
        return f"<PanelSession(id={self.panel_session_id}, name='{self.name}', start={self.start_date})>"


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
        return f"<{self.facility.code}/{self.mnemonic_code.code}/{self.digits} digits>"

    def __repr__(self):
        return f"<ProjectCode(facility_id={self.facility_id}, mnemonic_code_id={self.mnemonic_code_id}, digits={self.digits})>"



#-------------------------------------------------------------------------em-
