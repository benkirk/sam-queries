"""
SQLAlchemy ORM models for SAM - Refactored for maintainability.

Key improvements:
- Patterns for common patterns (timestamps, soft deletes, active flags)
- Explicit indexes for performance
- Consistent naming conventions
- Better type hints and documentation
- Separation of concerns
- Proper __eq__ and __hash__ methods for reliable set/dict operations
  - All entity classes implement __eq__ and __hash__ based on their primary key ID.
"""

from datetime import datetime
from typing import List, Optional, Dict, Set
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean,
    ForeignKey, Text, BigInteger, TIMESTAMP, text, and_, or_, Index
)
from sqlalchemy.orm import relationship, declarative_base, declared_attr
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.sql import func

Base = declarative_base()


# ============================================================================
# Patterns - Common patterns extracted
# ============================================================================

class TimestampPattern:
    """Provides creation and modification timestamps."""

    @declared_attr
    def creation_time(cls):
        return Column(DateTime, nullable=False, default=datetime.utcnow)

    @declared_attr
    def modified_time(cls):
        return Column(TIMESTAMP)


class SoftDeletePattern:
    """Provides soft delete capability."""

    @declared_attr
    def deleted(cls):
        return Column(Boolean, nullable=False, default=False)

    @declared_attr
    def deletion_time(cls):
        return Column(TIMESTAMP)

    @property
    def is_deleted(self) -> bool:
        """Check if this record is soft-deleted."""
        return bool(self.deleted)


class ActiveFlagPattern:
    """Provides active status flag."""

    @declared_attr
    def active(cls):
        return Column(Boolean, nullable=False, default=True)

    @property
    def is_active(self) -> bool:
        """Check if this record is active."""
        return bool(self.active)


class DateRangePattern:
    """Provides start_date and end_date for temporal relationships."""

    @declared_attr
    def start_date(cls):
        return Column(DateTime, nullable=False)

    @declared_attr
    def end_date(cls):
        return Column(DateTime)

    def is_active_at(self, check_date: Optional[datetime] = None) -> bool:
        """Check if this record is active at a given date."""
        if check_date is None:
            check_date = datetime.utcnow()

        if self.start_date > check_date:
            return False

        if self.end_date is not None and self.end_date < check_date:
            return False

        return True

    @hybrid_property
    def is_currently_active(self) -> bool:
        """Check if this record is currently active (Python side)."""
        return self.is_active_at()

    @is_currently_active.expression
    def is_currently_active(cls):
        """Check if this record is currently active (SQL side)."""
        now = func.now()
        return and_(
            cls.start_date <= now,
            or_(cls.end_date.is_(None), cls.end_date >= now)
        )


# ============================================================================
# User Management
# ============================================================================

class User(Base, TimestampPattern):
    """Represents a user in the system."""
    __tablename__ = 'users'

    # Explicit indexes for common queries
    __table_args__ = (
        Index('ix_users_username', 'username'),
        Index('ix_users_upid', 'upid'),
        Index('ix_users_active_locked', 'active', 'locked'),
    )

    def __eq__(self, other):
        """Two users are equal if they have the same user_id."""
        if not isinstance(other, User):
            return False
        # Both must have IDs and they must match
        return self.user_id is not None and self.user_id == other.user_id

    def __hash__(self):
        """Hash based on user_id for set/dict operations."""
        # Use id() for transient instances, user_id for persistent ones
        return hash(self.user_id) if self.user_id is not None else hash(id(self))

    user_id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(35), nullable=False, unique=True)
    locked = Column(Boolean, nullable=False, default=False)
    upid = Column(Integer, unique=True)
    unix_uid = Column(Integer, nullable=False)

    # Personal information
    title = Column(String(15))
    first_name = Column(String(40))
    middle_name = Column(String(40))
    last_name = Column(String(50))
    nickname = Column(String(50))
    name_suffix = Column(String(40))

    # Status flags
    active = Column(Boolean, nullable=False, default=True)
    charging_exempt = Column(Boolean, nullable=False, default=False)
    deleted = Column(Boolean)

    # Foreign keys
    academic_status_id = Column(Integer, ForeignKey('academic_status.academic_status_id'))
    login_type_id = Column(Integer, ForeignKey('login_type.login_type_id'))
    primary_gid = Column(Integer, ForeignKey('adhoc_group.group_id'))
    contact_person_upid = Column(Integer)

    # Additional timestamps
    pdb_modified_time = Column(TIMESTAMP)
    access_status_change_time = Column(TIMESTAMP)

    # Tokens
    token_type = Column(String(30))
    idms_sync_token = Column(String(64))

    # Relationships
    academic_status = relationship('AcademicStatus', back_populates='users')
    email_addresses = relationship('EmailAddress', back_populates='user',
                                   lazy='selectin', order_by='EmailAddress.is_primary.desc()')
    institutions = relationship('UserInstitution', back_populates='user')
    organizations = relationship('UserOrganization', back_populates='user')
    accounts = relationship('AccountUser', back_populates='user', lazy='selectin')
    led_projects = relationship('Project', foreign_keys='Project.project_lead_user_id',
                                back_populates='lead')
    admin_projects = relationship('Project', foreign_keys='Project.project_admin_user_id',
                                 back_populates='admin')
    primary_group = relationship('AdhocGroup', foreign_keys=[primary_gid])

    # Association proxy for projects
    _projects_w_dups = association_proxy('accounts', 'account.project')

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
    def full_name(self) -> str:
        """Return the user's full name."""
        parts = [self.first_name, self.middle_name, self.last_name]
        return ' '.join(p for p in parts if p)

    @property
    def display_name(self) -> str:
        """Return the user's display name (nickname or full name)."""
        return self.nickname or self.full_name

    @property
    def primary_email(self) -> Optional[str]:
        """
        Return the user's primary email address.
        Falls back to first active email if no primary is set.
        """
        # Try to find primary email
        for email in self.email_addresses:
            if email.is_primary:
                return email.email_address

        # Fallback: return first active email
        for email in self.email_addresses:
            if email.active or email.active is None:
                return email.email_address

        # Last resort: return any email
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

    def __repr__(self):
        return f"<User(id={self.user_id}, username='{self.username}', name='{self.full_name}')>"


class UserAlias(Base, TimestampPattern):
    """Stores external identifiers for users."""
    __tablename__ = 'user_alias'

    __table_args__ = (
        Index('ix_user_alias_orcid', 'orcid_id'),
        Index('ix_user_alias_access_global', 'access_global_id'),
    )

    user_alias_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False, unique=True)
    username = Column(String(35), nullable=False, unique=True)
    orcid_id = Column(String(20), index=True)
    access_global_id = Column(String(31), index=True)
    modified_time = Column(TIMESTAMP(3), server_default=text('CURRENT_TIMESTAMP(3)'))

    user = relationship('User')


class EmailAddress(Base, TimestampPattern):
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


class AcademicStatus(Base, TimestampPattern, SoftDeletePattern, ActiveFlagPattern):
    """Academic status types (Faculty, Student, etc.)."""
    __tablename__ = 'academic_status'

    academic_status_id = Column(Integer, primary_key=True, autoincrement=True)
    academic_status_code = Column(String(2), nullable=False)
    description = Column(String(100), nullable=False)

    users = relationship('User', back_populates='academic_status')


class UserInstitution(Base, TimestampPattern, DateRangePattern):
    """Maps users to institutions."""
    __tablename__ = 'user_institution'

    __table_args__ = (
        Index('ix_user_institution_user', 'user_id'),
        Index('ix_user_institution_dates', 'start_date', 'end_date'),
    )

    user_institution_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    institution_id = Column(Integer, ForeignKey('institution.institution_id'), nullable=False)

    user = relationship('User', back_populates='institutions')
    institution = relationship('Institution', back_populates='users')


class UserOrganization(Base, TimestampPattern, DateRangePattern):
    """Maps users to organizations."""
    __tablename__ = 'user_organization'

    __table_args__ = (
        Index('ix_user_organization_user', 'user_id'),
        Index('ix_user_organization_dates', 'start_date', 'end_date'),
    )

    user_organization_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    organization_id = Column(Integer, ForeignKey('organization.organization_id'), nullable=False)

    user = relationship('User', back_populates='organizations')
    organization = relationship('Organization', back_populates='users')


# ============================================================================
# Group Management
# ============================================================================

class AdhocGroup(Base, ActiveFlagPattern):
    """Unix groups for organizing users."""
    __tablename__ = 'adhoc_group'

    __table_args__ = (
        Index('ix_adhoc_group_gid', 'unix_gid'),
    )

    def __eq__(self, other):
        """Two groups are equal if they have the same group_id."""
        if not isinstance(other, AdhocGroup):
            return False
        return self.group_id is not None and self.group_id == other.group_id

    def __hash__(self):
        """Hash based on group_id for set/dict operations."""
        return hash(self.group_id) if self.group_id is not None else hash(id(self))

    group_id = Column(Integer, primary_key=True, autoincrement=True)
    group_name = Column(String(30), nullable=False, unique=True)
    unix_gid = Column(Integer, nullable=False, unique=True)
    creation_time = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)
    pdb_modified_time = Column(TIMESTAMP)
    idms_sync_token = Column(String(64))

    tags = relationship('AdhocGroupTag', back_populates='group')
    system_accounts = relationship('AdhocSystemAccountEntry', back_populates='group')


class AdhocGroupTag(Base):
    """Tags for categorizing adhoc groups."""
    __tablename__ = 'adhoc_group_tag'

    adhoc_group_tag_id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey('adhoc_group.group_id'), nullable=False)
    tag = Column(String(40), nullable=False)
    creation_time = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)

    group = relationship('AdhocGroup', back_populates='tags')


class AdhocSystemAccountEntry(Base):
    """System account entries for adhoc groups."""
    __tablename__ = 'adhoc_system_account_entry'

    entry_id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey('adhoc_group.group_id'), nullable=False)
    access_branch_name = Column(String(40), nullable=False)
    username = Column(String(12), nullable=False)
    creation_time = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)

    group = relationship('AdhocGroup', back_populates='system_accounts')


# ============================================================================
# Supporting Tables
# ============================================================================

class Institution(Base, TimestampPattern):
    """Educational and research institutions."""
    __tablename__ = 'institution'

    def __eq__(self, other):
        """Two institutions are equal if they have the same institution_id."""
        if not isinstance(other, Institution):
            return False
        return (self.institution_id is not None and
                self.institution_id == other.institution_id)

    def __hash__(self):
        """Hash based on institution_id for set/dict operations."""
        return (hash(self.institution_id) if self.institution_id is not None
                else hash(id(self)))

    institution_id = Column(Integer, primary_key=True)
    name = Column(String(80), nullable=False)
    acronym = Column(String(40), nullable=False)
    nsf_org_code = Column(String(200))
    address = Column(String(255))
    city = Column(String(30))
    zip = Column(String(15))
    code = Column(String(3))

    state_prov_id = Column(Integer, ForeignKey('state_prov.ext_state_prov_id'))
    institution_type_id = Column(Integer, ForeignKey('institution_type.institution_type_id'))

    idms_sync_token = Column(String(64))

    users = relationship('UserInstitution', back_populates='institution')


class Organization(Base, TimestampPattern, ActiveFlagPattern):
    """Organizational units (departments, labs, etc.)."""
    __tablename__ = 'organization'

    __table_args__ = (
        Index('ix_organization_tree', 'tree_left', 'tree_right'),
    )

    def __eq__(self, other):
        """Two organizations are equal if they have the same organization_id."""
        if not isinstance(other, Organization):
            return False
        return (self.organization_id is not None and
                self.organization_id == other.organization_id)

    def __hash__(self):
        """Hash based on organization_id for set/dict operations."""
        return (hash(self.organization_id) if self.organization_id is not None
                else hash(id(self)))

    organization_id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    acronym = Column(String(15), nullable=False, unique=True)
    description = Column(String(255))
    parent_org_id = Column(Integer, ForeignKey('organization.organization_id'))

    # Tree structure (nested set model)
    tree_left = Column(Integer)
    tree_right = Column(Integer)
    level = Column(String(80))
    level_code = Column(String(10))

    idms_sync_token = Column(String(64))

    users = relationship('UserOrganization', back_populates='organization')
    projects = relationship('ProjectOrganization', back_populates='organization')
    parent = relationship('Organization', remote_side=[organization_id])


class AreaOfInterestGroup(Base, TimestampPattern, ActiveFlagPattern):
    """Groupings for research areas."""
    __tablename__ = 'area_of_interest_group'

    area_of_interest_group_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)

    areas = relationship('AreaOfInterest', back_populates='group')


class AreaOfInterest(Base, TimestampPattern, ActiveFlagPattern):
    """Research areas for projects."""
    __tablename__ = 'area_of_interest'

    area_of_interest_id = Column(Integer, primary_key=True, autoincrement=True)
    area_of_interest = Column(String(255), nullable=False, unique=True)
    area_of_interest_group_id = Column(Integer,
                                       ForeignKey('area_of_interest_group.area_of_interest_group_id'),
                                       nullable=False)

    group = relationship('AreaOfInterestGroup', back_populates='areas')
    projects = relationship('Project', back_populates='area_of_interest')


class ResourceType(Base, TimestampPattern, ActiveFlagPattern):
    """Types of resources (HPC, DISK, ARCHIVE, etc.)."""
    __tablename__ = 'resource_type'

    resource_type_id = Column(Integer, primary_key=True, autoincrement=True)
    resource_type = Column(String(35), nullable=False, unique=True)
    description = Column(String(255))
    grace_period_days = Column(Integer)

    resources = relationship('Resource', back_populates='resource_type')


class Resource(Base, TimestampPattern):
    """Computing resources (HPC systems, storage, etc.)."""
    __tablename__ = 'resources'

    def __eq__(self, other):
        """Two resources are equal if they have the same resource_id."""
        if not isinstance(other, Resource):
            return False
        return self.resource_id is not None and self.resource_id == other.resource_id

    def __hash__(self):
        """Hash based on resource_id for set/dict operations."""
        return hash(self.resource_id) if self.resource_id is not None else hash(id(self))

    resource_id = Column(Integer, primary_key=True, autoincrement=True)
    resource_name = Column(String(40), nullable=False, unique=True)
    resource_type_id = Column(Integer, ForeignKey('resource_type.resource_type_id'),
                             nullable=False)
    description = Column(String(255))
    activity_type = Column(String(12), nullable=False, default='NONE')

    needs_default_project = Column(Boolean, nullable=False, default=False)
    configurable = Column(Boolean, nullable=False, default=True)
    charging_exempt = Column(Boolean, nullable=False, default=False)

    commission_date = Column(DateTime)
    decommission_date = Column(DateTime)

    prim_sys_admin_user_id = Column(Integer, ForeignKey('users.user_id'))
    prim_responsible_org_id = Column(Integer, ForeignKey('organization.organization_id'))

    default_first_threshold = Column(Integer)
    default_second_threshold = Column(Integer)
    default_home_dir_base = Column(String(255))
    default_resource_shell_id = Column(Integer)

    accounts = relationship('Account', back_populates='resource')
    resource_type = relationship('ResourceType', back_populates='resources')

    def is_commissioned_at(self, check_date: Optional[datetime] = None) -> bool:
        """Check if resource is commissioned at a given date."""
        if check_date is None:
            check_date = datetime.utcnow()

        if self.commission_date and self.commission_date > check_date:
            return False

        if self.decommission_date and self.decommission_date <= check_date:
            return False

        return True

    @hybrid_property
    def is_commissioned(self) -> bool:
        """Check if resource is currently commissioned (Python side)."""
        return self.is_commissioned_at()

    @is_commissioned.expression
    def is_commissioned(cls):
        """Check if resource is currently commissioned (SQL side)."""
        now = func.now()
        return and_(
            or_(cls.commission_date.is_(None), cls.commission_date <= now),
            or_(cls.decommission_date.is_(None), cls.decommission_date > now)
        )


class Facility(Base, TimestampPattern, ActiveFlagPattern):
    """Facility classifications (NCAR, UNIV, etc.)."""
    __tablename__ = 'facility'

    facility_id = Column(Integer, primary_key=True, autoincrement=True)
    facility_name = Column(String(30), nullable=False, unique=True)
    code = Column(String(1), unique=True)
    description = Column(String(255), nullable=False)
    fair_share_percentage = Column(Float)

    panels = relationship('Panel', back_populates='facility')


class Panel(Base, TimestampPattern, ActiveFlagPattern):
    """Allocation review panels."""
    __tablename__ = 'panel'

    panel_id = Column(Integer, primary_key=True, autoincrement=True)
    panel_name = Column(String(30), nullable=False, unique=True)
    description = Column(String(100))
    facility_id = Column(Integer, ForeignKey('facility.facility_id'), nullable=False)

    facility = relationship('Facility', back_populates='panels')
    allocation_types = relationship('AllocationType', back_populates='panel')


# ============================================================================
# Account and Allocation Management
# ============================================================================

class Account(Base, SoftDeletePattern):
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
    creation_time = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)

    # Relationships
    project = relationship('Project', back_populates='accounts')
    resource = relationship('Resource', back_populates='accounts')
    allocations = relationship('Allocation', back_populates='account')
    users = relationship('AccountUser', back_populates='account', lazy='selectin')


class AccountUser(Base, TimestampPattern, DateRangePattern):
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


class Allocation(Base, TimestampPattern, SoftDeletePattern):
    """Resource allocations for accounts."""
    __tablename__ = 'allocation'

    __table_args__ = (
        Index('ix_allocation_account', 'account_id'),
        Index('ix_allocation_dates', 'start_date', 'end_date'),
        Index('ix_allocation_active', 'deleted', 'start_date', 'end_date'),
    )

    def __eq__(self, other):
        """Two allocations are equal if they have the same allocation_id."""
        if not isinstance(other, Allocation):
            return False
        return (self.allocation_id is not None and
                self.allocation_id == other.allocation_id)

    def __hash__(self):
        """Hash based on allocation_id for set/dict operations."""
        return (hash(self.allocation_id) if self.allocation_id is not None
                else hash(id(self)))

    allocation_id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)
    parent_allocation_id = Column(Integer, ForeignKey('allocation.allocation_id'))

    amount = Column(Float(15, 2), nullable=False)
    description = Column(String(255))

    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)

    # Relationships
    account = relationship('Account', back_populates='allocations')
    transactions = relationship('AllocationTransaction', back_populates='allocation')
    parent = relationship('Allocation', remote_side=[allocation_id])

    def is_active_at(self, check_date: Optional[datetime] = None) -> bool:
        """Check if allocation is active at a given date."""
        if self.deleted:
            return False

        if check_date is None:
            check_date = datetime.utcnow()

        if self.start_date > check_date:
            return False

        if self.end_date is not None and self.end_date < check_date:
            return False

        return True

    @hybrid_property
    def is_active(self) -> bool:
        """Check if allocation is currently active (Python side)."""
        return self.is_active_at()

    @is_active.expression
    def is_active(cls):
        """Check if allocation is currently active (SQL side)."""
        now = func.now()
        return and_(
            cls.deleted == False,
            cls.start_date <= now,
            or_(cls.end_date.is_(None), cls.end_date >= now)
        )


class AllocationTransaction(Base):
    """Transaction history for allocations."""
    __tablename__ = 'allocation_transaction'

    __table_args__ = (
        Index('ix_allocation_transaction_allocation', 'allocation_id'),
        Index('ix_allocation_transaction_user', 'user_id'),
    )

    allocation_transaction_id = Column(Integer, primary_key=True, autoincrement=True)
    allocation_id = Column(Integer, ForeignKey('allocation.allocation_id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.user_id'))
    related_transaction_id = Column(Integer,
                                   ForeignKey('allocation_transaction.allocation_transaction_id'))

    transaction_type = Column(String(50), nullable=False)
    requested_amount = Column(Float(15, 2))
    transaction_amount = Column(Float(15, 2))

    alloc_start_date = Column(DateTime)
    alloc_end_date = Column(DateTime)

    auth_at_panel_mtg = Column(Boolean)
    transaction_comment = Column(Text)
    propagated = Column(Boolean, nullable=False, default=False)

    creation_time = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    allocation = relationship('Allocation', back_populates='transactions')
    user = relationship('User')
    related_transaction = relationship('AllocationTransaction',
                                      remote_side=[allocation_transaction_id])


class AllocationType(Base, TimestampPattern, ActiveFlagPattern):
    """Types of allocations (CHAP, ASD-UNIV, etc.)."""
    __tablename__ = 'allocation_type'

    allocation_type_id = Column(Integer, primary_key=True, autoincrement=True)
    allocation_type = Column(String(20), nullable=False)
    default_allocation_amount = Column(Float(15, 2))
    fair_share_percentage = Column(Float)
    panel_id = Column(Integer, ForeignKey('panel.panel_id'))

    panel = relationship('Panel', back_populates='allocation_types')
    projects = relationship('Project', back_populates='allocation_type')


# ============================================================================
# Project Management
# ============================================================================

class Project(Base, TimestampPattern, ActiveFlagPattern):
    """Research projects."""
    __tablename__ = 'project'

    __table_args__ = (
        Index('ix_project_projcode', 'projcode'),
        Index('ix_project_lead', 'project_lead_user_id'),
        Index('ix_project_active', 'active'),
        Index('ix_project_tree', 'tree_left', 'tree_right'),
    )

    def __eq__(self, other):
        """Two projects are equal if they have the same project_id."""
        if not isinstance(other, Project):
            return False
        return self.project_id is not None and self.project_id == other.project_id

    def __hash__(self):
        """Hash based on project_id for set/dict operations."""
        return hash(self.project_id) if self.project_id is not None else hash(id(self))

    project_id = Column(Integer, primary_key=True, autoincrement=True)
    projcode = Column(String(30), nullable=False, unique=True)
    title = Column(String(255), nullable=False)
    abstract = Column(Text)

    # Leadership
    project_lead_user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    project_admin_user_id = Column(Integer, ForeignKey('users.user_id'))

    # Status flags
    charging_exempt = Column(Boolean, nullable=False, default=False)

    # Foreign keys
    area_of_interest_id = Column(Integer, ForeignKey('area_of_interest.area_of_interest_id'),
                                 nullable=False)
    allocation_type_id = Column(Integer, ForeignKey('allocation_type.allocation_type_id'))
    parent_id = Column(Integer, ForeignKey('project.project_id'))

    # Tree structure (nested set model)
    tree_left = Column(Integer)
    tree_right = Column(Integer)
    tree_root = Column(Integer, ForeignKey('project.project_id'))

    # Unix group
    unix_gid = Column(Integer)
    ext_alias = Column(String(64))

    # Additional timestamps
    membership_change_time = Column(TIMESTAMP)
    inactivate_time = Column(DateTime)

    # Relationships
    lead = relationship('User', foreign_keys=[project_lead_user_id],
                       back_populates='led_projects')
    admin = relationship('User', foreign_keys=[project_admin_user_id],
                        back_populates='admin_projects')
    area_of_interest = relationship('AreaOfInterest', back_populates='projects')
    allocation_type = relationship('AllocationType', back_populates='projects')
    accounts = relationship('Account', back_populates='project', lazy='selectin')
    directories = relationship('ProjectDirectory', back_populates='project')
    organizations = relationship('ProjectOrganization', back_populates='project')
    contracts = relationship('ProjectContract', back_populates='project')
    parent = relationship('Project', remote_side=[project_id], foreign_keys=[parent_id])

    # Active account users (filtered join)
    account_users = relationship(
        'AccountUser',
        secondary='account',
        primaryjoin=(project_id == Account.project_id),
        secondaryjoin=and_(
            Account.account_id == AccountUser.account_id,
            or_(AccountUser.end_date.is_(None), AccountUser.end_date >= func.now())
        ),
        viewonly=True,
        lazy='selectin',
        collection_class=set,
    )

    @property
    def users(self) -> List['User']:
        """Return a deduplicated list of active users on this project."""
        return list({au.user for au in self.account_users if au.user is not None})

    def get_all_allocations_by_resource(self) -> Dict[str, Optional['Allocation']]:
        """
        Get the most recent active allocation for each resource.

        Returns:
            Dict mapping resource_name to Allocation object
        """
        allocations_by_resource = {}
        now = datetime.utcnow()

        for account in self.accounts:
            if account.resource:
                resource_name = account.resource.resource_name
                active_allocs = [
                    alloc for alloc in account.allocations
                    if alloc.is_active_at(now)
                ]
                if active_allocs:
                    # Get most recent allocation
                    current = max(active_allocs, key=lambda a: a.allocation_id)
                    allocations_by_resource[resource_name] = current

        return allocations_by_resource

    def get_allocation_by_resource(self, resource_name: str) -> Optional['Allocation']:
        """
        Get the most recent active allocation for a specific resource.

        Args:
            resource_name: Name of the resource (e.g., 'Derecho', 'GLADE', 'Campaign')

        Returns:
            Most recent active allocation for that resource, or None
        """
        allocations_by_resource = self.get_all_allocations_by_resource()
        return allocations_by_resource.get(resource_name)

    def get_user_count(self) -> int:
        """Return the number of active users on this project."""
        return len(self.users)

    def has_user(self, user: 'User') -> bool:
        """Check if a user is active on this project."""
        return user in self.users

    @hybrid_property
    def has_active_allocations(self) -> bool:
        """Check if project has any active allocations (Python side)."""
        now = datetime.utcnow()
        for account in self.accounts:
            for alloc in account.allocations:
                if alloc.is_active_at(now):
                    return True
        return False

    @has_active_allocations.expression
    def has_active_allocations(cls):
        """Check if project has any active allocations (SQL side)."""
        from sqlalchemy import exists, select
        now = func.now()
        return exists(
            select(1)
            .select_from(Account)
            .join(Allocation)
            .where(
                Account.project_id == cls.project_id,
                Allocation.deleted == False,
                Allocation.start_date <= now,
                or_(Allocation.end_date.is_(None), Allocation.end_date >= now)
            )
        )

    def __repr__(self):
        return f"<Project(id={self.project_id}, projcode='{self.projcode}', title='{self.title[:50]}...')>"


class ProjectDirectory(Base, TimestampPattern, DateRangePattern):
    """File system directories associated with projects."""
    __tablename__ = 'project_directory'

    project_directory_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('project.project_id'), nullable=False)
    directory_name = Column(String(255), nullable=False)

    project = relationship('Project', back_populates='directories')


class ProjectOrganization(Base, TimestampPattern, DateRangePattern):
    """Maps projects to organizations."""
    __tablename__ = 'project_organization'

    __table_args__ = (
        Index('ix_project_organization_project', 'project_id'),
        Index('ix_project_organization_org', 'organization_id'),
    )

    project_organization_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('project.project_id'), nullable=False)
    organization_id = Column(Integer, ForeignKey('organization.organization_id'),
                            nullable=False)

    project = relationship('Project', back_populates='organizations')
    organization = relationship('Organization', back_populates='projects')


class ProjectContract(Base):
    """Links projects to funding contracts."""
    __tablename__ = 'project_contract'

    project_contract_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('project.project_id'), nullable=False)
    contract_id = Column(Integer, ForeignKey('contract.contract_id'), nullable=False)
    creation_time = Column(DateTime, nullable=False, default=datetime.utcnow)

    project = relationship('Project', back_populates='contracts')
    contract = relationship('Contract', back_populates='projects')


# ============================================================================
# Contract / LoginType / Misc
# ============================================================================

class Contract(Base, TimestampPattern):
    """Funding contracts (NSF awards, etc.)."""
    __tablename__ = 'contract'

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


class LoginType(Base):
    """Types of login accounts."""
    __tablename__ = 'login_type'

    login_type_id = Column(Integer, primary_key=True)
    type = Column(String(30), nullable=False)


# ============================================================================
# Query Helper Functions
# ============================================================================

class QueryHelpers:
    """
    Helper methods for common queries across the SAM database.
    These can be used as class methods or imported as utility functions.
    """

    @staticmethod
    def get_active_users_for_project(session, project_id: int) -> List[User]:
        """
        Get all active users for a project using an efficient query.

        Args:
            session: SQLAlchemy session
            project_id: ID of the project

        Returns:
            List of active User objects
        """
        from sqlalchemy.orm import joinedload

        return session.query(User).join(
            AccountUser, User.user_id == AccountUser.user_id
        ).join(
            Account, AccountUser.account_id == Account.account_id
        ).filter(
            Account.project_id == project_id,
            or_(AccountUser.end_date.is_(None),
                AccountUser.end_date >= func.now())
        ).options(
            joinedload(User.email_addresses)
        ).distinct().all()

    @staticmethod
    def get_projects_for_user(session, user_id: int,
                             active_only: bool = True) -> List[Project]:
        """
        Get all projects for a user using an efficient query.

        Args:
            session: SQLAlchemy session
            user_id: ID of the user
            active_only: Only return active projects

        Returns:
            List of Project objects
        """
        from sqlalchemy.orm import joinedload

        query = session.query(Project).join(
            Account, Project.project_id == Account.project_id
        ).join(
            AccountUser, Account.account_id == AccountUser.account_id
        ).filter(
            AccountUser.user_id == user_id,
            or_(AccountUser.end_date.is_(None),
                AccountUser.end_date >= func.now())
        ).options(
            joinedload(Project.lead),
            joinedload(Project.area_of_interest)
        )

        if active_only:
            query = query.filter(Project.active == True)

        return query.distinct().all()

    @staticmethod
    def get_allocation_usage_summary(session, project_id: int) -> Dict[str, Dict]:
        """
        Get allocation summary for all resources on a project.

        Args:
            session: SQLAlchemy session
            project_id: ID of the project

        Returns:
            Dict mapping resource names to allocation info
        """
        allocations = session.query(
            Resource.resource_name,
            Allocation.amount,
            Allocation.start_date,
            Allocation.end_date,
            Allocation.allocation_id
        ).join(
            Account, Allocation.account_id == Account.account_id
        ).join(
            Resource, Account.resource_id == Resource.resource_id
        ).filter(
            Account.project_id == project_id,
            Allocation.deleted == False,
            or_(Allocation.end_date.is_(None),
                Allocation.end_date >= func.now())
        ).all()

        result = {}
        for row in allocations:
            resource_name = row.resource_name
            if resource_name not in result or row.allocation_id > result[resource_name]['allocation_id']:
                result[resource_name] = {
                    'allocation_id': row.allocation_id,
                    'amount': row.amount,
                    'start_date': row.start_date,
                    'end_date': row.end_date
                }

        return result


# ============================================================================
# Validation and Business Logic Helpers
# ============================================================================

class ValidationHelpers:
    """Helper methods for common validation logic."""

    @staticmethod
    def validate_user_can_access_project(user: User, project: Project) -> tuple[bool, str]:
        """
        Validate if a user has access to a project.

        Returns:
            (is_valid, error_message) tuple
        """
        if not user.is_accessible:
            return False, f"User {user.username} is locked or inactive"

        if not project.active:
            return False, f"Project {project.projcode} is inactive"

        if not project.has_user(user):
            return False, f"User {user.username} is not a member of project {project.projcode}"

        return True, ""

    @staticmethod
    def validate_allocation_dates(start_date: datetime,
                                  end_date: Optional[datetime]) -> tuple[bool, str]:
        """
        Validate allocation date ranges.

        Returns:
            (is_valid, error_message) tuple
        """
        if end_date and start_date >= end_date:
            return False, "Start date must be before end date"

        if start_date > datetime.utcnow():
            return False, "Start date cannot be in the future"

        return True, ""


# ============================================================================
# End of module
# ============================================================================
