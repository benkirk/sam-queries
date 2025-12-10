#-------------------------------------------------------------------------bh-
# Common Imports:
from ..base import *
#-------------------------------------------------------------------------eh-


#-------------------------------------------------------------------------bm-
#----------------------------------------------------------------------------
class User(Base, TimestampMixin):
    """Represents a user in the system."""
    __tablename__ = 'users'

    __table_args__ = (
        Index('ix_users_username', 'username'),
        Index('ix_users_upid', 'upid'),
        Index('ix_users_active_locked', 'active', 'locked'),
        Index('ix_users_primary_gid', 'primary_gid'),
    )

    def __eq__(self, other):
        """Two users are equal if they have the same user_id."""
        if not isinstance(other, User):
            return False
        return self.user_id is not None and self.user_id == other.user_id

    def __hash__(self):
        """Hash based on user_id for set/dict operations."""
        return hash(self.user_id) if self.user_id is not None else hash(id(self))

    # [All existing column definitions remain the same...]
    user_id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(35), nullable=False, unique=True)
    locked = Column(Boolean, nullable=False, default=False)
    upid = Column(Integer, unique=True, nullable=True)
    unix_uid = Column(Integer, nullable=False)

    # Personal information
    title = Column(String(15))
    first_name = Column(String(40))
    middle_name = Column(String(40))
    last_name = Column(String(50))
    nickname = Column(String(50))
    name_suffix = Column(String(40))

    active = Column(Boolean, nullable=False, default=True)
    charging_exempt = Column(Boolean, nullable=False, default=False)
    deleted = Column(Boolean)

    academic_status_id = Column(Integer, ForeignKey('academic_status.academic_status_id'))
    login_type_id = Column(Integer, ForeignKey('login_type.login_type_id'))
    primary_gid = Column(Integer, ForeignKey('adhoc_group.group_id'))
    contact_person_upid = Column(Integer)

    pdb_modified_time = Column(TIMESTAMP)
    access_status_change_time = Column(TIMESTAMP)

    token_type = Column(String(30))
    idms_sync_token = Column(String(64))

    # [All existing relationships remain the same...]
    academic_status = relationship('AcademicStatus', back_populates='users')
    accounts = relationship('AccountUser', back_populates='user', lazy='selectin')
    admin_projects = relationship('Project', foreign_keys='Project.project_admin_user_id', back_populates='admin')
    administered_resources = relationship('Resource', foreign_keys='Resource.prim_sys_admin_user_id', back_populates='prim_sys_admin')
    aliases = relationship('UserAlias', back_populates='user', uselist=False)
    allocation_transactions = relationship('AllocationTransaction', foreign_keys='AllocationTransaction.user_id', back_populates='user')
    archive_charge_summaries = relationship('ArchiveChargeSummary', back_populates='user')
    archive_charges = relationship('ArchiveCharge', back_populates='user')
    charge_adjustments_made = relationship('ChargeAdjustment', foreign_keys='ChargeAdjustment.adjusted_by_id', back_populates='adjusted_by')
    comp_charge_summaries = relationship('CompChargeSummary', back_populates='user')
    dav_charge_summaries = relationship('DavChargeSummary', back_populates='user')
    dav_charges = relationship('DavCharge', back_populates='user')
    default_projects = relationship('DefaultProject', back_populates='user', cascade='all, delete-orphan')
    disk_charge_summaries = relationship('DiskChargeSummary', back_populates='user')
    disk_charges = relationship('DiskCharge', back_populates='user')
    email_addresses = relationship('EmailAddress', back_populates='user', lazy='selectin', order_by='EmailAddress.is_primary.desc()', cascade='all, delete-orphan')
    hpc_charge_summaries = relationship('HPCChargeSummary', back_populates='user')
    hpc_charges = relationship('HPCCharge', back_populates='user')
    institutions = relationship('UserInstitution', back_populates='user', cascade='all, delete-orphan')
    led_projects = relationship('Project', foreign_keys='Project.project_lead_user_id', back_populates='lead')
    login_type = relationship('LoginType', back_populates='users')
    monitored_contracts = relationship('Contract', foreign_keys='Contract.contract_monitor_user_id', back_populates='contract_monitor')
    organizations = relationship('UserOrganization', back_populates='user', cascade='all, delete-orphan')
    phones = relationship('Phone', back_populates='user', cascade='all, delete-orphan')
    pi_contracts = relationship('Contract', foreign_keys='Contract.principal_investigator_user_id', back_populates='principal_investigator')
    primary_group = relationship('AdhocGroup', foreign_keys=[primary_gid])
    resource_homes = relationship('UserResourceHome', back_populates='user', cascade='all, delete-orphan')
    resource_shells = relationship('UserResourceShell', back_populates='user', cascade='all, delete-orphan')
    responsible_accounts = relationship('ResponsibleParty', back_populates='user')
    role_assignments = relationship('RoleUser', back_populates='user')
    wallclock_exemptions = relationship('WallclockExemption', back_populates='user', cascade='all')
    # xras_user = relationship('XrasUser', back_populates='local_user', uselist=False)  # DEPRECATED - XRAS views don't support relationships

    _projects_w_dups = association_proxy('accounts', 'account.project')

    # ============================================================================
    # Class Methods - User Lookup
    # ============================================================================

    @classmethod
    def get_by_username(cls, session, username: str) -> Optional['User']:
        """
        Get a user by exact username match.

        Args:
            session: SQLAlchemy session
            username: Exact username to search for

        Returns:
            User object if found, None otherwise

        Example:
            >>> user = User.get_by_username(session, 'jsmith')
            >>> if user:
            ...     print(f"Found: {user.full_name}")
        """
        return session.query(cls).filter(cls.username == username).first()

    @classmethod
    def get_by_upid(cls, session, upid: int) -> Optional['User']:
        """
        Get a user by their UPID (Universal Person ID).

        Args:
            session: SQLAlchemy session
            upid: Universal Person ID

        Returns:
            User object if found, None otherwise

        Example:
            >>> user = User.get_by_upid(session, 12345)
        """
        return session.query(cls).filter(cls.upid == upid).first()

    @classmethod
    def get_by_email(cls, session, email: str) -> Optional['User']:
        """
        Get a user by email address.

        Args:
            session: SQLAlchemy session
            email: Email address to search for

        Returns:
            User object if found, None otherwise

        Example:
            >>> user = User.get_by_email(session, 'john.smith@example.com')
        """
        from sqlalchemy.orm import joinedload

        user = session.query(cls).join(cls.email_addresses).filter(
            EmailAddress.email_address == email
        ).options(joinedload(cls.email_addresses)).first()

        return user

    @classmethod
    def search_by_username(cls, session, pattern: str,
                          active_only: bool = True,
                          limit: int = 50) -> List['User']:
        """
        Search for users by username pattern (case-insensitive).

        Args:
            session: SQLAlchemy session
            pattern: Search pattern (supports SQL LIKE wildcards % and _)
            active_only: If True, only return active, unlocked users
            limit: Maximum number of results to return

        Returns:
            List of matching User objects

        Examples:
            >>> # Find all users starting with 'smith'
            >>> users = User.search_by_username(session, 'smith%')

            >>> # Find all users containing 'john'
            >>> users = User.search_by_username(session, '%john%')

            >>> # Find users with 5-character usernames starting with 'a'
            >>> users = User.search_by_username(session, 'a____')

            >>> # Include inactive users
            >>> users = User.search_by_username(session, 'test%', active_only=False)
        """
        query = session.query(cls).filter(cls.username.ilike(pattern))

        if active_only:
            query = query.filter(cls.active == True, cls.locked == False)

        return query.order_by(cls.username).limit(limit).all()

    @classmethod
    def search_by_name(cls, session, pattern: str,
                      search_first: bool = True,
                      search_last: bool = True,
                      search_nickname: bool = False,
                      active_only: bool = True,
                      limit: int = 50) -> List['User']:
        """
        Search for users by name pattern (case-insensitive).

        Args:
            session: SQLAlchemy session
            pattern: Search pattern (supports SQL LIKE wildcards % and _)
            search_first: Include first_name in search
            search_last: Include last_name in search
            search_nickname: Include nickname in search
            active_only: If True, only return active, unlocked users
            limit: Maximum number of results to return

        Returns:
            List of matching User objects

        Examples:
            >>> # Find users with last name containing 'smith'
            >>> users = User.search_by_name(session, '%smith%',
            ...                            search_first=False, search_last=True)

            >>> # Find users with first name starting with 'john'
            >>> users = User.search_by_name(session, 'john%',
            ...                            search_first=True, search_last=False)

            >>> # Search across all name fields
            >>> users = User.search_by_name(session, '%alex%',
            ...                            search_nickname=True)
        """
        conditions = []

        if search_first:
            conditions.append(cls.first_name.ilike(pattern))
        if search_last:
            conditions.append(cls.last_name.ilike(pattern))
        if search_nickname:
            conditions.append(cls.nickname.ilike(pattern))

        if not conditions:
            return []

        query = session.query(cls).filter(or_(*conditions))

        if active_only:
            query = query.filter(cls.active == True, cls.locked == False)

        return query.order_by(cls.last_name, cls.first_name).limit(limit).all()

    @classmethod
    def search_by_email(cls, session, pattern: str,
                       active_only: bool = True,
                       limit: int = 50) -> List['User']:
        """
        Search for users by email pattern (case-insensitive).

        Args:
            session: SQLAlchemy session
            pattern: Email pattern (supports SQL LIKE wildcards % and _)
            active_only: If True, only return active, unlocked users
            limit: Maximum number of results to return

        Returns:
            List of matching User objects

        Examples:
            >>> # Find all users with @ucar.edu emails
            >>> users = User.search_by_email(session, '%@ucar.edu')

            >>> # Find users with gmail addresses
            >>> users = User.search_by_email(session, '%@gmail.com')
        """
        from sqlalchemy.orm import joinedload

        query = session.query(cls).join(cls.email_addresses).filter(
            EmailAddress.email_address.ilike(pattern)
        ).options(joinedload(cls.email_addresses))

        if active_only:
            query = query.filter(cls.active == True, cls.locked == False)

        # Distinct to avoid duplicates if user has multiple matching emails
        return query.distinct().order_by(cls.username).limit(limit).all()

    @classmethod
    def search_users(cls, session,
                    search_term: str,
                    search_username: bool = True,
                    search_name: bool = True,
                    search_email: bool = True,
                    active_only: bool = True,
                    limit: int = 50) -> List['User']:
        """
        Universal search across username, name, and email fields.

        This is a convenience method that searches across multiple fields
        simultaneously. Perfect for user search boxes or autocomplete.

        Args:
            session: SQLAlchemy session
            search_term: Search term (will be wrapped with % for partial match)
            search_username: Include username in search
            search_name: Include first_name and last_name in search
            search_email: Include email addresses in search
            active_only: If True, only return active, unlocked users
            limit: Maximum number of results to return

        Returns:
            List of matching User objects, ordered by relevance

        Examples:
            >>> # General search - finds users matching 'john' in any field
            >>> users = User.search_users(session, 'john')

            >>> # Search only usernames and emails
            >>> users = User.search_users(session, 'smith',
            ...                          search_name=False)

            >>> # Find all matching users including inactive
            >>> users = User.search_users(session, 'test',
            ...                          active_only=False)
        """
        from sqlalchemy.orm import joinedload

        # Wrap search term with wildcards for partial matching
        pattern = f'%{search_term}%'

        conditions = []

        # Username search
        if search_username:
            conditions.append(cls.username.ilike(pattern))

        # Name search
        if search_name:
            conditions.append(cls.first_name.ilike(pattern))
            conditions.append(cls.last_name.ilike(pattern))
            conditions.append(cls.nickname.ilike(pattern))

        # Build base query
        if conditions:
            query = session.query(cls).filter(or_(*conditions))
        else:
            query = session.query(cls).filter(False)  # Empty result

        # Email search (requires join)
        if search_email:
            email_query = session.query(cls).join(cls.email_addresses).filter(
                EmailAddress.email_address.ilike(pattern)
            )

            if active_only:
                email_query = email_query.filter(cls.active == True, cls.locked == False)

            # Union with main query
            if conditions:
                query = query.union(email_query)
            else:
                query = email_query

        # Apply active filter
        if active_only and conditions:
            query = query.filter(cls.active == True, cls.locked == False)

        # Load email addresses for display
        query = query.options(joinedload(cls.email_addresses))

        return query.distinct().order_by(cls.username).limit(limit).all()

    @classmethod
    def get_active_users(cls, session, limit: Optional[int] = None) -> List['User']:
        """
        Get all active, unlocked users.

        Args:
            session: SQLAlchemy session
            limit: Optional maximum number of results

        Returns:
            List of active User objects

        Example:
            >>> active_users = User.get_active_users(session, limit=100)
        """
        query = session.query(cls).filter(
            cls.active == True,
            cls.locked == False
        ).order_by(cls.username)

        if limit:
            query = query.limit(limit)

        return query.all()

    @property
    def all_projects(self) -> List['Project']:
        """Return all projects (including inactive), deduplicated."""
        return list({p for p in self._projects_w_dups if p is not None})

    @property
    def active_projects(self) -> List['Project']:
        """Return only active projects, deduplicated."""
        return [p for p in self.all_projects if p.active]

    @property
    def projects(self) -> List['Project']:
        """Return active projects (default)."""
        return self.active_projects

    @property
    def active_account_users(self) -> List['AccountUser']:
        """Get currently active account users."""
        now = datetime.now()
        return [
            au for account in self.accounts
            for au in account.users
            if au.end_date is None or au.end_date >= now
        ]

    @property
    def users(self) -> List['User']:
        """Return deduplicated list of active users."""
        return list({au.user for au in self.active_account_users if au.user})

    @property
    def full_name(self) -> str:
        """Return the user's full name."""
        parts = [self.first_name, self.middle_name, self.last_name]
        return ' '.join(p for p in parts if p)

    @property
    def display_name(self) -> str:
        """Return the user's display name (nickname or full name)."""
        parts = [self.nickname or self.first_name, self.last_name]
        return ' '.join(p for p in parts if p)

    @property
    def primary_email(self) -> Optional[str]:
        """
        Return the user's primary email address.
        Falls back to first active email if no primary is set.
        """
        for email in self.email_addresses:
            if email.is_primary:
                return email.email_address

        for email in self.email_addresses:
            if email.active or email.active is None:
                return email.email_address

        if self.email_addresses:
            return self.email_addresses[0].email_address

        return None

    @property
    def all_emails(self) -> List[str]:
        """Return all email addresses for this user."""
        return [email.email_address for email in self.email_addresses]

    def get_emails_detailed(self) -> List[Dict[str, any]]:
        """
        Return detailed information about all email addresses.

        Returns:
            List of dicts with keys: email, is_primary, active, created
        """
        return [
            {
                'email': email.email_address,
                'is_primary': bool(email.is_primary),
                'active': email.active if email.active is not None else True,
                'created': email.creation_time
            }
            for email in self.email_addresses
        ]

    @hybrid_property
    def is_accessible(self) -> bool:
        """Check if user can access the system (Python side)."""
        return self.active and not self.locked

    @is_accessible.expression
    def is_accessible(cls):
        """Check if user can access the system (SQL side)."""
        return and_(cls.active == True, cls.locked == False)

    def __str__(self):
        return f"{self.username} ({self.display_name})"

    def __repr__(self):
        return f"<User(id={self.user_id}, username='{self.username}', name='{self.full_name}')>"


#----------------------------------------------------------------------------
class UserAlias(Base, TimestampMixin):
    """Stores external identifiers for users."""
    __tablename__ = 'user_alias'

    __table_args__ = (
        Index('ix_user_alias_username', 'username'),
        Index('ix_user_alias_orcid', 'orcid_id'),
        Index('ix_user_alias_access_global', 'access_global_id'),
    )

    user_alias_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False, unique=True)
    username = Column(String(35), nullable=False, unique=True)
    orcid_id = Column(String(20))
    access_global_id = Column(String(31))
    modified_time = Column(TIMESTAMP(3), server_default=text('CURRENT_TIMESTAMP(3)'))

    user = relationship('User', back_populates='aliases')

    def __str__(self):
        return f"{self.username} - {self.orcid_id}"

    def __repr__(self):
        return f"<UserAlias(username='{self.username}', orcid='{self.orcid_id}')>"


#----------------------------------------------------------------------------
class EmailAddress(Base, TimestampMixin):
    """Email addresses for users."""
    __tablename__ = 'email_address'

    __table_args__ = (
        Index('ix_email_address_user', 'user_id'),
        Index('ix_email_address_email', 'email_address'),
    )

    def __eq__(self, other):
        """Two email addresses are equal if they have the same email_address_id."""
        if not isinstance(other, EmailAddress):
            return False
        return (self.email_address_id is not None and
                self.email_address_id == other.email_address_id)

    def __hash__(self):
        """Hash based on email_address_id for set/dict operations."""
        return (hash(self.email_address_id) if self.email_address_id is not None
                else hash(id(self)))

    email_address_id = Column(Integer, primary_key=True, autoincrement=True)
    email_address = Column(String(255), nullable=False)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    is_primary = Column(Boolean, nullable=False)
    active = Column(Boolean)

    user = relationship('User', back_populates='email_addresses')

    def __str__(self):
        return f"{self.email_address}"

    def __repr__(self):
        return f"<EmailAddress(email='{self.email_address}', primary={self.is_primary})>"


#----------------------------------------------------------------------------
class Phone(Base, TimestampMixin):
    """Phone numbers for users."""
    __tablename__ = 'phone'

    __table_args__ = (
        Index('ix_phone_user', 'user_id'),
    )

    ext_phone_id = Column(Integer, primary_key=True, autoincrement=True)
    ext_phone_type_id = Column(Integer, ForeignKey('phone_type.ext_phone_type_id'),
                               nullable=False)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    phone_number = Column(String(50), nullable=False)

    phone_type = relationship('PhoneType', back_populates='phones')
    user = relationship('User', back_populates='phones')

    def __str__(self):
        return f"{self.phone_number}"

    def __repr__(self):
        return f"<Phone(number='{self.phone_number}', type='{self.phone_type.phone_type if self.phone_type else None}')>"


#----------------------------------------------------------------------------
class PhoneType(Base, TimestampMixin):
    """Types of phone numbers."""
    __tablename__ = 'phone_type'

    ext_phone_type_id = Column(Integer, primary_key=True, autoincrement=True)
    phone_type = Column(String(25), nullable=False)

    phones = relationship('Phone', back_populates='phone_type')

    def __str__(self):
        return f"{self.phone_type}"

    def __repr__(self):
        return f"<PhoneType(type='{self.phone_type}')>"


#----------------------------------------------------------------------------
class UserResourceHome(Base, TimestampMixin):
    """Home directories for users on resources."""
    __tablename__ = 'user_resource_home'

    __table_args__ = (
        Index('ix_user_resource_home_user', 'user_id'),
        Index('ix_user_resource_home_resource', 'resource_id'),
    )

    user_resource_home_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    resource_id = Column(Integer, ForeignKey('resources.resource_id'), nullable=False)
    home_directory = Column(String(1024), nullable=False)

    user = relationship('User', back_populates='resource_homes')
    resource = relationship('Resource', back_populates='user_homes')

    def __str__(self):
        return f"{self.user_id} - {self.home_directory}"

    def __repr__(self):
        return f"<UserResourceHome(user_id={self.user_id}, dir='{self.home_directory}')>"


#----------------------------------------------------------------------------
class UserResourceShell(Base, TimestampMixin):
    """Shell preferences for users on resources."""
    __tablename__ = 'user_resource_shell'

    __table_args__ = (
        Index('ix_user_resource_shell_user', 'user_id'),
        Index('ix_user_resource_shell_shell', 'resource_shell_id'),
    )

    user_resource_shell_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    resource_shell_id = Column(Integer, ForeignKey('resource_shell.resource_shell_id'),
                              nullable=False)

    user = relationship('User', back_populates='resource_shells')
    resource_shell = relationship('ResourceShell', back_populates='user_shells')


# ============================================================================
# Institution Management
# ============================================================================


#----------------------------------------------------------------------------
class LoginType(Base):
    """Types of login accounts."""
    __tablename__ = 'login_type'

    login_type_id = Column(Integer, primary_key=True)
    type = Column(String(30), nullable=False)

    users = relationship('User', back_populates='login_type')

    def __str__(self):
        return f"{self.type}"

    def __repr__(self):
        return f"<LoginType(type='{self.type}')>"


#----------------------------------------------------------------------------
class AcademicStatus(Base, TimestampMixin, SoftDeleteMixin, ActiveFlagMixin):
    """Academic status types (Faculty, Student, etc.)."""
    __tablename__ = 'academic_status'

    academic_status_id = Column(Integer, primary_key=True, autoincrement=True)
    academic_status_code = Column(String(2), nullable=False)
    description = Column(String(100), nullable=False)

    users = relationship('User', back_populates='academic_status')

    def __str__(self):
        return f"{self.academic_status_code}"

    def __repr__(self):
        return f"<AcademicStatus(code='{self.academic_status_code}', desc='{self.description}')>"


#-------------------------------------------------------------------------em-
