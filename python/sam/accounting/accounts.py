#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class Account(Base, SoftDeleteMixin):
    """Billing accounts linking projects to resources."""
    __tablename__ = 'account'

    __table_args__ = (
        Index('ix_account_project', 'project_id'),
        Index('ix_account_resource', 'resource_id'),
        Index('ix_account_deleted', 'deleted'),
    )

    def __eq__(self, other):
        """Two accounts are equal if they have the same account_id."""
        if not isinstance(other, Account):
            return False
        return self.account_id is not None and self.account_id == other.account_id

    def __hash__(self):
        """Hash based on account_id for set/dict operations."""
        return hash(self.account_id) if self.account_id is not None else hash(id(self))

    account_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('project.project_id'))
    resource_id = Column(Integer, ForeignKey('resources.resource_id'))

    # Thresholds
    first_threshold = Column(Integer)
    second_threshold = Column(Integer)
    cutoff_threshold = Column(Integer, nullable=False, default=100)

    # Timestamps
    creation_time = Column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    modified_time = Column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'), onupdate=text('CURRENT_TIMESTAMP'))

    allocations = relationship('Allocation', back_populates='account')
    archive_charge_summaries = relationship('ArchiveChargeSummary', back_populates='account')
    archive_charges = relationship('ArchiveCharge', back_populates='account')
    charge_adjustments = relationship('ChargeAdjustment', back_populates='account')
    comp_charge_summaries = relationship('CompChargeSummary', back_populates='account')
    dav_charge_summaries = relationship('DavChargeSummary', back_populates='account')
    dav_charges = relationship('DavCharge', back_populates='account')
    disk_charge_summaries = relationship('DiskChargeSummary', back_populates='account')
    disk_charges = relationship('DiskCharge', back_populates='account')
    hpc_charge_summaries = relationship('HPCChargeSummary', back_populates='account')
    hpc_charges = relationship('HPCCharge', back_populates='account')
    project = relationship('Project', back_populates='accounts')
    resource = relationship('Resource', back_populates='accounts')
    users = relationship('AccountUser', back_populates='account', lazy='selectin')
    responsible_parties = relationship('ResponsibleParty', back_populates='account')

    @classmethod
    def get_by_project_and_resource(cls, session, project_id: int, resource_id: int,
                                     exclude_deleted: bool = True) -> Optional['Account']:
        """
        Get an account by project and resource IDs.

        Args:
            session: SQLAlchemy session
            project_id: Project ID
            resource_id: Resource ID
            exclude_deleted: If True, exclude deleted accounts (default True)

        Returns:
            Account object if found, None otherwise

        Example:
            >>> account = Account.get_by_project_and_resource(session, 123, 456)
        """
        query = session.query(cls).filter(
            cls.project_id == project_id,
            cls.resource_id == resource_id
        )

        if exclude_deleted:
            query = query.filter(cls.deleted == False)

        return query.first()

    def __str__(self):
        return f"{self.project.projcode if self.project else None} - {self.resource.resource_name if self.resource else None}"

    def __repr__(self):
        return f"<Account(id={self.account_id}, project='{self.project.projcode if self.project else None}', resource='{self.resource.resource_name if self.resource else None}')>"


#----------------------------------------------------------------------------
class AccountUser(Base, TimestampMixin, DateRangeMixin):
    """Maps users to accounts with date ranges."""
    __tablename__ = 'account_user'

    __table_args__ = (
        Index('ix_account_user_account', 'account_id'),
        Index('ix_account_user_user', 'user_id'),
        Index('ix_account_user_dates', 'start_date', 'end_date'),
    )

    def __eq__(self, other):
        """Two account_users are equal if they have the same account_user_id."""
        if not isinstance(other, AccountUser):
            return False
        return (self.account_user_id is not None and
                self.account_user_id == other.account_user_id)

    def __hash__(self):
        """Hash based on account_user_id for set/dict operations."""
        return (hash(self.account_user_id) if self.account_user_id is not None
                else hash(id(self)))

    account_user_id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)

    account = relationship('Account', back_populates='users', lazy='selectin')
    user = relationship('User', back_populates='accounts', lazy='selectin')

    @hybrid_property
    def is_active(self) -> bool:
        """Check if this account-user mapping is currently active (Python side)."""
        return self.is_currently_active

    @is_active.expression
    def is_active(cls):
        """Check if this account-user mapping is currently active (SQL side)."""
        return cls.is_currently_active


#----------------------------------------------------------------------------
class ResponsibleParty(Base, TimestampMixin):
    """
    Tracks responsible parties for accounts.

    Defines who is responsible for an account (e.g., PI, admin, billing contact).
    The responsible_party_type field indicates the type of responsibility.
    """
    __tablename__ = 'responsible_party'

    __table_args__ = (
        Index('ix_responsible_party_account', 'account_id'),
        Index('ix_responsible_party_user', 'user_id'),
    )

    responsible_party_id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    responsible_party_type = Column(String(20), nullable=False)

    # Relationships
    account = relationship('Account', back_populates='responsible_parties')
    user = relationship('User', back_populates='responsible_accounts')

    def __str__(self):
        return f"{self.responsible_party_type}: User {self.user_id} for Account {self.account_id}"

    def __repr__(self):
        return f"<ResponsibleParty(id={self.responsible_party_id}, type='{self.responsible_party_type}', user_id={self.user_id}, account_id={self.account_id})>"


#-------------------------------------------------------------------------em-
