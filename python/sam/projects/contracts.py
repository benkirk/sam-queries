#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class Contract(Base, TimestampMixin):
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
            check_date = datetime.utcnow()

        if self.start_date > check_date:
            return False

        if self.end_date is not None and self.end_date < check_date:
            return False

        return True

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
class ContractSource(Base, TimestampMixin, ActiveFlagMixin):
    """Sources of funding contracts."""
    __tablename__ = 'contract_source'

    contract_source_id = Column(Integer, primary_key=True, autoincrement=True)
    contract_source = Column(String(50), nullable=False, unique=True)

    contracts = relationship('Contract', back_populates='contract_source')

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


# ============================================================================
# Role/Permission Management
# ============================================================================


#----------------------------------------------------------------------------
class NSFProgram(Base, TimestampMixin, ActiveFlagMixin):
    """NSF program classifications."""
    __tablename__ = 'nsf_program'

    nsf_program_id = Column(Integer, primary_key=True, autoincrement=True)
    nsf_program_name = Column(String(255), nullable=False, unique=True)

    contracts = relationship('Contract', back_populates='nsf_program')

    def __repr__(self):
        return f"<NSFProgram(name='{self.nsf_program_name}')>"


#-------------------------------------------------------------------------em-
