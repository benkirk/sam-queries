#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-

from dataclasses import dataclass


@dataclass(frozen=True)
class CurrentDiskUsage:
    """Latest disk-snapshot occupancy for a single account.

    Returned by ``Account.current_disk_usage()`` /
    ``Project.current_disk_usage()``. Distinct from the cumulative
    billing roll-up — these values are read from the single
    ``disk_charge_summary`` row marked current, not summed.
    """
    activity_date: object        # datetime.date — kept loose to avoid import cycle
    bytes: int
    terabyte_years: float
    number_of_files: int

    @property
    def used_tib(self) -> float:
        """Occupancy in TiB (binary)."""
        return self.bytes / (1024 ** 4)


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class Account(Base, SoftDeleteMixin, SessionMixin):
    """Billing accounts linking projects to resources."""
    __tablename__ = 'account'

    __table_args__ = (
        Index('idx_account', 'project_id'),
        Index('idx_account_1', 'resource_id'),
        Index('project_resource_ux', 'project_id', 'resource_id', unique=True),
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

    allocations = relationship('Allocation', back_populates='account', cascade='all, delete-orphan')
    archive_charge_summaries = relationship('ArchiveChargeSummary', back_populates='account')
    archive_charges = relationship('ArchiveCharge', back_populates='account')
    charge_adjustments = relationship('ChargeAdjustment', back_populates='account', cascade='all, delete-orphan')
    comp_charge_summaries = relationship('CompChargeSummary', back_populates='account')
    dav_charge_summaries = relationship('DavChargeSummary', back_populates='account')
    dav_charges = relationship('DavCharge', back_populates='account')
    disk_charge_summaries = relationship('DiskChargeSummary', back_populates='account')
    disk_charges = relationship('DiskCharge', back_populates='account')
    hpc_charge_summaries = relationship('HPCChargeSummary', back_populates='account')
    hpc_charges = relationship('HPCCharge', back_populates='account')
    project = relationship('Project', back_populates='accounts', lazy='selectin')
    resource = relationship('Resource', back_populates='accounts')
    users = relationship('AccountUser', back_populates='account', lazy='select', cascade='all')
    responsible_parties = relationship('ResponsibleParty', back_populates='account', cascade='all, delete-orphan')

    @classmethod
    def create(cls, session, *, project_id: int, resource_id: int) -> 'Account':
        """Create a new Account for a project+resource pair, auto-populating members.

        Creates open-ended AccountUser rows (start_date=now, end_date=None) for:
          - The project lead
          - The project admin (if set)
          - Every user currently active (end_date IS NULL) on any sibling
            Account of the same project

        This enforces the invariant that a project's lead, admin, and existing
        members become members of every new Account added to the project. No
        user propagation happens outside this method — all Account creation
        paths (webapp, API, Allocation.create) should route through here.

        Does NOT commit; caller must wrap in management_transaction().
        """
        from sam.projects.projects import Project

        project = session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project {project_id} not found")

        account = cls(project_id=project_id, resource_id=resource_id)
        session.add(account)
        session.flush()

        cls._seed_members(session, account)
        return account

    @classmethod
    def get_or_create(cls, session, *, project_id: int, resource_id: int) -> 'Account':
        """Get the account for a project+resource pair, reviving or creating as needed.

        Idempotent companion to :meth:`create`. Handles the three states a
        ``(project_id, resource_id)`` slot can be in — note the table's
        ``project_resource_ux`` UNIQUE index is NOT scoped by ``deleted``, so a
        soft-deleted row still occupies the slot and a blind ``create()`` would
        raise an IntegrityError:

          1. A soft-deleted account exists → un-delete it, re-seed members, return.
          2. A live account exists → return it unchanged.
          3. No account exists → create one (delegates to :meth:`create`).

        All allocation-creation paths route account acquisition through here so
        re-allocating a resource whose account was previously removed just works.

        Does NOT commit; caller must wrap in management_transaction().
        """
        existing = cls.get_by_project_and_resource(
            session, project_id, resource_id, exclude_deleted=False
        )
        if existing is None:
            return cls.create(session, project_id=project_id, resource_id=resource_id)

        if existing.deleted:
            existing.deleted = False
            session.flush()
            cls._seed_members(session, existing)
        return existing

    @classmethod
    def _seed_members(cls, session, account: 'Account') -> None:
        """Populate open-ended AccountUser rows for a (new or revived) account.

        Adds start_date=now, end_date=None membership rows for:
          - The project lead
          - The project admin (if set)
          - Every user currently active (end_date IS NULL) on any sibling
            Account of the same project

        Enforces the invariant that a project's lead, admin, and existing members
        become members of every Account added to the project. Skips users who are
        already active members of this account (so reviving an account is safe to
        re-run). Does NOT commit.
        """
        from sam.projects.projects import Project

        project = session.get(Project, account.project_id)
        if project is None:
            raise ValueError(f"Project {account.project_id} not found")

        propagate_user_ids: Set[int] = set()
        if project.project_lead_user_id is not None:
            propagate_user_ids.add(project.project_lead_user_id)
        if project.project_admin_user_id is not None:
            propagate_user_ids.add(project.project_admin_user_id)

        sibling_members = session.query(AccountUser).join(
            Account, AccountUser.account_id == Account.account_id
        ).filter(
            Account.project_id == account.project_id,
            Account.account_id != account.account_id,
            Account.is_active,
            AccountUser.end_date.is_(None),
        ).all()
        for au in sibling_members:
            propagate_user_ids.add(au.user_id)

        # Don't duplicate members already active on this account (revive path).
        existing_member_ids = {
            au.user_id for au in session.query(AccountUser).filter(
                AccountUser.account_id == account.account_id,
                AccountUser.end_date.is_(None),
            ).all()
        }
        propagate_user_ids -= existing_member_ids

        # Floor to the second: MySQL DATETIME has no fractional part and rounds
        # half-up, which can push a microsecond-stamped "now" into the next
        # second and make a just-seeded member briefly is_active=False.
        now = datetime.now().replace(microsecond=0)
        for user_id in propagate_user_ids:
            session.add(AccountUser(
                account_id=account.account_id,
                user_id=user_id,
                start_date=now,
                end_date=None,
            ))
        session.flush()

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

    _SENTINEL = object()

    def update_thresholds(self, *, first_threshold=_SENTINEL, second_threshold=_SENTINEL):
        """
        Update rolling consumption rate thresholds.

        Pass None to clear a threshold; omit a keyword argument to leave it unchanged.

        Args:
            first_threshold: 30-day window threshold percentage (int > 100, or None to clear)
            second_threshold: 90-day window threshold percentage (int > 100, or None to clear)

        Returns:
            self
        """
        if first_threshold is not Account._SENTINEL:
            self.first_threshold = first_threshold
        if second_threshold is not Account._SENTINEL:
            self.second_threshold = second_threshold
        self.session.flush()
        return self

    def current_disk_usage(self, session=None) -> Optional['CurrentDiskUsage']:
        """Return the latest ``disk_charge_summary`` snapshot for this account.

        Reads the single row whose ``activity_date`` is marked
        ``current = True`` in ``disk_charge_summary_status``, falling
        back to the row with the maximum ``activity_date`` if the
        status table has no current row (defensive — should not happen
        post-cutover).

        Returns ``None`` for accounts whose resource is not a disk
        resource, or which have no disk_charge_summary row at all.
        Sums across all rows for the snapshot date — so when the
        ``<unidentified>`` reconciliation is on, the lead-attributed
        gap row is included in the total.

        Thin wrapper over :func:`sam.queries.disk_usage.bulk_current_disk_usage`
        so single- and multi-account paths share the same fixed-query
        kernel. Use the bulk helper directly whenever loading >1
        account at a time (``build_disk_subtree``,
        ``Project.current_disk_usage``).
        """
        s = session or self.session
        if s is None:
            return None

        # Disk resource gate: skip when the account's resource isn't disk.
        # Match the bulk helper's contract — caller filters by resource
        # type so the helper itself doesn't need a per-row check.
        if self.resource and self.resource.resource_type and \
                self.resource.resource_type.resource_type != 'DISK':
            return None

        # Lazy import: sam.queries.disk_usage already imports Account at
        # module level, so a top-level import here would be circular.
        from sam.queries.disk_usage import bulk_current_disk_usage
        return bulk_current_disk_usage(s, [self.account_id]).get(self.account_id)

    def __str__(self):
        return f"{self.project.projcode if self.project else None} - {self.resource.resource_name if self.resource else None}"

    def __repr__(self):
        return f"<Account(id={self.account_id}, project='{self.project.projcode if self.project else None}', resource='{self.resource.resource_name if self.resource else None}')>"


#----------------------------------------------------------------------------
class AccountUser(Base, TimestampMixin, DateRangeMixin):
    """Maps users to accounts with date ranges."""
    __tablename__ = 'account_user'

    __table_args__ = (
        Index('account_user_account_fk', 'account_id'),
        Index('account_user_user_fk', 'user_id'),
        Index('idx_account_id', 'account_id'),
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

    def __str__(self):
        username = self.user.username if self.user else (f"user_id={self.user_id}" if self.user_id else None)
        projcode = self.account.project.projcode if (self.account and self.account.project) else (f"account_id={self.account_id}" if self.account_id else None)
        return f"AccountUser: {username} on {projcode}"

    def __repr__(self):
        username = self.user.username if self.user else (f"user_id={self.user_id}" if self.user_id else None)
        projcode = self.account.project.projcode if (self.account and self.account.project) else (f"account_id={self.account_id}" if self.account_id else None)
        resource = self.account.resource.resource_name if (self.account and self.account.resource) else None
        return f"<AccountUser {self.account_user_id} user={username!r} project={projcode!r} resource={resource!r}>"

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
        Index('allocation_account_fk', 'account_id'),
        Index('responsible_party_user_fk', 'user_id'),
        Index('responsible_party_ux',
              'account_id', 'user_id', 'responsible_party_type',
              unique=True),
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
