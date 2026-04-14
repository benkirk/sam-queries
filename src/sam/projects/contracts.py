#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class Contract(Base, TimestampMixin, SessionMixin):
    """Funding contracts."""
    __tablename__ = 'contract'

    __table_args__ = (
        Index('ix_contract_source', 'contract_source_id'),
        Index('ix_contract_pi', 'principal_investigator_user_id'),
        Index('ix_contract_monitor', 'contract_monitor_user_id'),
        Index('ix_contract_nsf_program', 'nsf_program_id'),
    )

    contract_id = Column(Integer, primary_key=True, autoincrement=True)
    contract_source_id = Column(Integer, ForeignKey('contract_source.contract_source_id'),
                                nullable=False)
    contract_number = Column(String(50), nullable=False, unique=True)
    title = Column(String(255), nullable=False)
    url = Column(String(1000))

    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)

    @validates('end_date')
    def _validate_end_date(self, key, value):
        return normalize_end_date(value)

    principal_investigator_user_id = Column(Integer, ForeignKey('users.user_id'),
                                           nullable=False)
    contract_monitor_user_id = Column(Integer, ForeignKey('users.user_id'))
    nsf_program_id = Column(Integer, ForeignKey('nsf_program.nsf_program_id'))

    contract_monitor = relationship('User', foreign_keys=[contract_monitor_user_id], back_populates='monitored_contracts')
    contract_source = relationship('ContractSource', back_populates='contracts')
    nsf_program = relationship('NSFProgram', back_populates='contracts')
    principal_investigator = relationship('User', foreign_keys=[principal_investigator_user_id], back_populates='pi_contracts')
    projects = relationship('ProjectContract', back_populates='contract')

    def is_active_at(self, check_date: Optional[datetime] = None) -> bool:
        """Check if contract is active at a given date."""
        if check_date is None:
            check_date = datetime.now()

        if self.start_date > check_date:
            return False

        if self.end_date is not None and self.end_date < check_date:
            return False

        return True

    @hybrid_property
    def is_active(self) -> bool:
        """Check if contract is currently active (Python side)."""
        return self.is_active_at()

    @is_active.expression
    def is_active(cls):
        """Check if contract is currently active (SQL side)."""
        now = func.now()
        return and_(
            cls.start_date <= now,
            or_(cls.end_date.is_(None), cls.end_date >= now)
        )

    def update(
        self,
        *,
        title: Optional[str] = None,
        url: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> 'Contract':
        """
        Update this Contract record.

        Only title, url, start_date, and end_date may be changed.
        PI, contract monitor, source, and number are read-only via this method.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

        Args:
            title: New title (NOT NULL)
            url: New URL (nullable — pass empty string to clear)
            start_date: New start date (NOT NULL)
            end_date: New end date — must be after start_date if both known

        Returns:
            self

        Raises:
            ValueError: If validation fails
        """
        if title is not None:
            if not title.strip():
                raise ValueError("title is required")
            self.title = title.strip()

        if url is not None:
            self.url = url.strip() if url.strip() else None

        if start_date is not None:
            self.start_date = start_date

        if end_date is not None:
            effective_start = start_date or self.start_date
            if effective_start and end_date <= effective_start:
                raise ValueError("end_date must be after start_date")
            self.end_date = end_date

        self.session.flush()
        return self

    @classmethod
    def create(
        cls,
        session,
        *,
        contract_number: str,
        title: str,
        start_date: datetime,
        contract_source_id: int,
        principal_investigator_user_id: int,
        url: Optional[str] = None,
        end_date: Optional[datetime] = None,
    ) -> 'Contract':
        """
        Create a new Contract.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.
        """
        if not contract_number or not contract_number.strip():
            raise ValueError("contract_number is required")
        if not title or not title.strip():
            raise ValueError("title is required")
        if end_date is not None and end_date <= start_date:
            raise ValueError("end_date must be after start_date")

        obj = cls(
            contract_number=contract_number.strip(),
            title=title.strip(),
            start_date=start_date,
            end_date=end_date,
            url=url.strip() if url and url.strip() else None,
            contract_source_id=contract_source_id,
            principal_investigator_user_id=principal_investigator_user_id,
        )
        session.add(obj)
        session.flush()
        return obj

    def __str__(self):
        return f"{self.contract_number}: {self.title[:50]}..."

    def __repr__(self):
        return f"<Contract(number='{self.contract_number}', title='{self.title[:50]}...')>"

    def __eq__(self, other):
        """Two contracts are equal if they have the same contract_id."""
        if not isinstance(other, Contract):
            return False
        return self.contract_id is not None and self.contract_id == other.contract_id

    def __hash__(self):
        """Hash based on contract_id for set/dict operations."""
        return hash(self.contract_id) if self.contract_id is not None else hash(id(self))


#----------------------------------------------------------------------------
class ContractSource(Base, TimestampMixin, ActiveFlagMixin, SessionMixin):
    """Sources of funding contracts."""
    __tablename__ = 'contract_source'

    contract_source_id = Column(Integer, primary_key=True, autoincrement=True)
    contract_source = Column(String(50), nullable=False, unique=True)

    contracts = relationship('Contract', back_populates='contract_source')

    def update(
        self,
        *,
        contract_source: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> 'ContractSource':
        """
        Update this ContractSource record.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

        Args:
            contract_source: New source name (NOT NULL, unique)
            active: Whether the source is active

        Returns:
            self

        Raises:
            ValueError: If name is empty
        """
        if contract_source is not None:
            if not contract_source.strip():
                raise ValueError("contract_source name is required")
            self.contract_source = contract_source.strip()

        if active is not None:
            self.active = active

        self.session.flush()
        return self

    @classmethod
    def create(
        cls,
        session,
        *,
        contract_source: str,
    ) -> 'ContractSource':
        """
        Create a new ContractSource.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.
        """
        if not contract_source or not contract_source.strip():
            raise ValueError("contract_source name is required")

        obj = cls(contract_source=contract_source.strip())
        session.add(obj)
        session.flush()
        return obj

    def __str__(self):
        return f"{self.contract_source}"

    def __repr__(self):
        return f"<ContractSource(source='{self.contract_source}')>"


#----------------------------------------------------------------------------
class ProjectContract(Base):
    """Links projects to funding contracts."""
    __tablename__ = 'project_contract'

    __table_args__ = (
        Index('ix_project_contract_project', 'project_id'),
        Index('ix_project_contract_contract', 'contract_id'),
    )

    project_contract_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('project.project_id'), nullable=False)
    contract_id = Column(Integer, ForeignKey('contract.contract_id'), nullable=False)
    creation_time = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    project = relationship('Project', back_populates='contracts')
    contract = relationship('Contract', back_populates='projects')

    @classmethod
    def create(cls, session, *, project_id: int, contract_id: int) -> 'ProjectContract':
        """Link a project to a funding contract.

        Does NOT commit; caller must wrap in management_transaction().
        Note: removal requires session.delete(pc) since this model has no
        soft-delete column.  If the contract has no other project links,
        the caller should also call contract.update(end_date=...) to
        deactivate the Contract record.
        """
        obj = cls(project_id=project_id, contract_id=contract_id)
        session.add(obj)
        session.flush()
        return obj

    def __str__(self):
        projcode = self.project.projcode if self.project else self.project_id
        contract_num = self.contract.contract_number if self.contract else self.contract_id
        return f"{projcode} / {contract_num}"

    def __repr__(self):
        return f"<ProjectContract(id={self.project_contract_id}, project_id={self.project_id}, contract_id={self.contract_id})>"


# ============================================================================
# Role/Permission Management
# ============================================================================


#----------------------------------------------------------------------------
class NSFProgram(Base, TimestampMixin, ActiveFlagMixin, SessionMixin):
    """NSF program classifications."""
    __tablename__ = 'nsf_program'

    nsf_program_id = Column(Integer, primary_key=True, autoincrement=True)
    nsf_program_name = Column(String(255), nullable=False, unique=True)

    contracts = relationship('Contract', back_populates='nsf_program')

    def update(
        self,
        *,
        nsf_program_name: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> 'NSFProgram':
        """
        Update this NSFProgram record.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.

        Args:
            nsf_program_name: New program name (NOT NULL, unique)
            active: Whether the program is active

        Returns:
            self

        Raises:
            ValueError: If name is empty
        """
        if nsf_program_name is not None:
            if not nsf_program_name.strip():
                raise ValueError("nsf_program_name is required")
            self.nsf_program_name = nsf_program_name.strip()

        if active is not None:
            self.active = active

        self.session.flush()
        return self

    @classmethod
    def create(
        cls,
        session,
        *,
        nsf_program_name: str,
    ) -> 'NSFProgram':
        """
        Create a new NSFProgram.

        NOTE: Does NOT commit. Caller must use management_transaction or commit manually.
        """
        if not nsf_program_name or not nsf_program_name.strip():
            raise ValueError("nsf_program_name is required")

        obj = cls(nsf_program_name=nsf_program_name.strip())
        session.add(obj)
        session.flush()
        return obj

    def __str__(self):
        return f"{self.nsf_program_name}"

    def __repr__(self):
        return f"<NSFProgram(name='{self.nsf_program_name}')>"


#-------------------------------------------------------------------------em-
