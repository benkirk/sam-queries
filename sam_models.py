"""
SQLAlchemy ORM models for SAM - Complete Corrected Version.

Key improvements:
- All models match SQL schema exactly
- Complete bidirectional relationships
- Proper indexes for performance
- Consistent naming conventions
- Better type hints and documentation
- Proper __eq__ and __hash__ methods for reliable set/dict operations
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
# Mixins - Common patterns extracted
# ============================================================================

class TimestampMixin:
    """Provides creation and modification timestamps."""

    @declared_attr
    def creation_time(cls):
        return Column(DateTime, nullable=False, default=datetime.utcnow)

    @declared_attr
    def modified_time(cls):
        return Column(TIMESTAMP)


class SoftDeleteMixin:
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


class ActiveFlagMixin:
    """Provides active status flag."""

    @declared_attr
    def active(cls):
        return Column(Boolean, nullable=False, default=True)

    @property
    def is_active(self) -> bool:
        """Check if this record is active."""
        return bool(self.active)


class DateRangeMixin:
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
# Geographic/Location
# ============================================================================

class Country(Base, TimestampMixin, SoftDeleteMixin):
    """Countries for address information."""
    __tablename__ = 'country'

    ext_country_id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(2), nullable=False)
    name = Column(String(50), nullable=False)

    state_provs = relationship('StateProv', back_populates='country')

    def __repr__(self):
        return f"<Country(code='{self.code}', name='{self.name}')>"


class StateProv(Base, TimestampMixin, SoftDeleteMixin):
    """U.S. states and international provinces."""
    __tablename__ = 'state_prov'

    ext_state_prov_id = Column(Integer, primary_key=True, autoincrement=True)
    ext_country_id = Column(Integer, ForeignKey('country.ext_country_id'), nullable=False)
    name = Column(String(100), nullable=False)
    code = Column(String(15))

    country = relationship('Country', back_populates='state_provs')
    institutions = relationship('Institution', back_populates='state_prov')

    def __repr__(self):
        return f"<StateProv(code='{self.code}', name='{self.name}')>"


# ============================================================================
# User Management
# ============================================================================

class LoginType(Base):
    """Types of login accounts."""
    __tablename__ = 'login_type'

    login_type_id = Column(Integer, primary_key=True)
    type = Column(String(30), nullable=False)

    users = relationship('User', back_populates='login_type')

    def __repr__(self):
        return f"<LoginType(type='{self.type}')>"


class AcademicStatus(Base, TimestampMixin, SoftDeleteMixin, ActiveFlagMixin):
    """Academic status types (Faculty, Student, etc.)."""
    __tablename__ = 'academic_status'

    academic_status_id = Column(Integer, primary_key=True, autoincrement=True)
    academic_status_code = Column(String(2), nullable=False)
    description = Column(String(100), nullable=False)

    users = relationship('User', back_populates='academic_status')

    def __repr__(self):
        return f"<AcademicStatus(code='{self.academic_status_code}', desc='{self.description}')>"


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
    login_type = relationship('LoginType', back_populates='users')
    primary_group = relationship('AdhocGroup', foreign_keys=[primary_gid])

    email_addresses = relationship('EmailAddress', back_populates='user',
                                   lazy='selectin', order_by='EmailAddress.is_primary.desc()')
    phones = relationship('Phone', back_populates='user')
    aliases = relationship('UserAlias', back_populates='user', uselist=False)

    institutions = relationship('UserInstitution', back_populates='user')
    organizations = relationship('UserOrganization', back_populates='user')
    accounts = relationship('AccountUser', back_populates='user', lazy='selectin')

    led_projects = relationship('Project', foreign_keys='Project.project_lead_user_id',
                                back_populates='lead')
    admin_projects = relationship('Project', foreign_keys='Project.project_admin_user_id',
                                 back_populates='admin')

    default_projects = relationship('DefaultProject', back_populates='user')
    resource_homes = relationship('UserResourceHome', back_populates='user')
    resource_shells = relationship('UserResourceShell', back_populates='user')
    role_assignments = relationship('RoleUser', back_populates='user')

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

    def __repr__(self):
        return f"<User(id={self.user_id}, username='{self.username}', name='{self.full_name}')>"


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

    def __repr__(self):
        return f"<UserAlias(username='{self.username}', orcid='{self.orcid_id}')>"


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

    def __repr__(self):
        return f"<EmailAddress(email='{self.email_address}', primary={self.is_primary})>"


class PhoneType(Base, TimestampMixin):
    """Types of phone numbers."""
    __tablename__ = 'phone_type'

    ext_phone_type_id = Column(Integer, primary_key=True, autoincrement=True)
    phone_type = Column(String(25), nullable=False)

    phones = relationship('Phone', back_populates='phone_type')

    def __repr__(self):
        return f"<PhoneType(type='{self.phone_type}')>"


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

    def __repr__(self):
        return f"<Phone(number='{self.phone_number}', type='{self.phone_type.phone_type if self.phone_type else None}')>"


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

    def __repr__(self):
        return f"<UserResourceHome(user_id={self.user_id}, dir='{self.home_directory}')>"


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

class InstitutionType(Base, TimestampMixin, ActiveFlagMixin):
    """Types of institutions (University, Government, etc.)."""
    __tablename__ = 'institution_type'

    institution_type_id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(45), nullable=False)

    institutions = relationship('Institution', back_populates='institution_type')

    def __repr__(self):
        return f"<InstitutionType(type='{self.type}')>"


class Institution(Base, TimestampMixin):
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
    idms_sync_token = Column(String(64))

    state_prov_id = Column(Integer, ForeignKey('state_prov.ext_state_prov_id'))
    institution_type_id = Column(Integer, ForeignKey('institution_type.institution_type_id'))

    state_prov = relationship('StateProv', back_populates='institutions')
    institution_type = relationship('InstitutionType', back_populates='institutions')
    users = relationship('UserInstitution', back_populates='institution')

    def __repr__(self):
        return f"<Institution(name='{self.name}', acronym='{self.acronym}')>"


class UserInstitution(Base, TimestampMixin, DateRangeMixin):
    """Maps users to institutions."""
    __tablename__ = 'user_institution'

    __table_args__ = (
        Index('ix_user_institution_user', 'user_id'),
        Index('ix_user_institution_institution', 'institution_id'),
        Index('ix_user_institution_dates', 'start_date', 'end_date'),
    )

    user_institution_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    institution_id = Column(Integer, ForeignKey('institution.institution_id'), nullable=False)

    user = relationship('User', back_populates='institutions')
    institution = relationship('Institution', back_populates='users')


# ============================================================================
# Organization Management
# ============================================================================

class Organization(Base, TimestampMixin, ActiveFlagMixin):
    """Organizational units (departments, labs, etc.)."""
    __tablename__ = 'organization'

    __table_args__ = (
        Index('ix_organization_tree', 'tree_left', 'tree_right'),
        Index('ix_organization_parent', 'parent_org_id'),
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

    def __repr__(self):
        return f"<Organization(name='{self.name}', acronym='{self.acronym}')>"


class UserOrganization(Base, TimestampMixin, DateRangeMixin):
    """Maps users to organizations."""
    __tablename__ = 'user_organization'

    __table_args__ = (
        Index('ix_user_organization_user', 'user_id'),
        Index('ix_user_organization_org', 'organization_id'),
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

class AdhocGroup(Base, ActiveFlagMixin):
    """Unix groups for organizing users."""
    __tablename__ = 'adhoc_group'

    __table_args__ = (
        Index('ix_adhoc_group_gid', 'unix_gid'),
        Index('ix_adhoc_group_name', 'group_name'),
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

    def __repr__(self):
        return f"<AdhocGroup(name='{self.group_name}', gid={self.unix_gid})>"


class AdhocGroupTag(Base):
    """Tags for categorizing adhoc groups."""
    __tablename__ = 'adhoc_group_tag'

    __table_args__ = (
        Index('ix_adhoc_group_tag_group', 'group_id'),
    )

    adhoc_group_tag_id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey('adhoc_group.group_id'), nullable=False)
    tag = Column(String(40), nullable=False)
    creation_time = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)

    group = relationship('AdhocGroup', back_populates='tags')


class AdhocSystemAccountEntry(Base):
    """System account entries for adhoc groups."""
    __tablename__ = 'adhoc_system_account_entry'

    __table_args__ = (
        Index('ix_adhoc_system_account_group', 'group_id'),
    )

    entry_id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey('adhoc_group.group_id'), nullable=False)
    access_branch_name = Column(String(40), nullable=False)
    username = Column(String(12), nullable=False)
    creation_time = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)

    group = relationship('AdhocGroup', back_populates='system_accounts')


# ============================================================================
# Resource Management
# ============================================================================

class ResourceType(Base, TimestampMixin, ActiveFlagMixin):
    """Types of resources (HPC, DISK, ARCHIVE, etc.)."""
    __tablename__ = 'resource_type'

    resource_type_id = Column(Integer, primary_key=True, autoincrement=True)
    resource_type = Column(String(35), nullable=False, unique=True)
    description = Column(String(255))
    grace_period_days = Column(Integer)

    resources = relationship('Resource', back_populates='resource_type')

    def __repr__(self):
        return f"<ResourceType(type='{self.resource_type}')>"


class Resource(Base, TimestampMixin):
    """Computing resources (HPC systems, storage, etc.)."""
    __tablename__ = 'resources'

    __table_args__ = (
        Index('ix_resources_type', 'resource_type_id'),
        Index('ix_resources_name', 'resource_name'),
    )

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
    default_resource_shell_id = Column(Integer, ForeignKey('resource_shell.resource_shell_id'))

    accounts = relationship('Account', back_populates='resource')
    resource_type = relationship('ResourceType', back_populates='resources')
    machines = relationship('Machine', back_populates='resource')
    queues = relationship('Queue', back_populates='resource')
    shells = relationship('ResourceShell', back_populates='resource',
                         foreign_keys='ResourceShell.resource_id')
    default_shell = relationship('ResourceShell', foreign_keys=[default_resource_shell_id])
    user_homes = relationship('UserResourceHome', back_populates='resource')
    default_projects = relationship('DefaultProject', back_populates='resource')
    facility_resources = relationship('FacilityResource', back_populates='resource')

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

    def __repr__(self):
        return f"<Resource(name='{self.resource_name}', type='{self.resource_type.resource_type if self.resource_type else None}')>"


class ResourceShell(Base, TimestampMixin):
    """Available shells on resources."""
    __tablename__ = 'resource_shell'

    __table_args__ = (
        Index('ix_resource_shell_resource', 'resource_id'),
    )

    resource_shell_id = Column(Integer, primary_key=True, autoincrement=True)
    resource_id = Column(Integer, ForeignKey('resources.resource_id'), nullable=False)
    shell_name = Column(String(25), nullable=False)
    path = Column(String(1024), nullable=False)

    resource = relationship('Resource', back_populates='shells',
                           foreign_keys=[resource_id])
    user_shells = relationship('UserResourceShell', back_populates='resource_shell')

    def __repr__(self):
        return f"<ResourceShell(name='{self.shell_name}', path='{self.path}')>"


class Machine(Base, TimestampMixin):
    """Computing machines/systems."""
    __tablename__ = 'machine'

    __table_args__ = (
        Index('ix_machine_resource', 'resource_id'),
    )

    machine_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False)
    description = Column(String(255))
    resource_id = Column(Integer, ForeignKey('resources.resource_id'), nullable=False)
    cpus_per_node = Column(Integer)

    commission_date = Column(DateTime)
    decommission_date = Column(DateTime)

    resource = relationship('Resource', back_populates='machines')
    machine_factors = relationship('MachineFactor', back_populates='machine')

    def __repr__(self):
        return f"<Machine(name='{self.name}', cpus_per_node={self.cpus_per_node})>"


class MachineFactor(Base, TimestampMixin):
    """Charging factors for machines over time."""
    __tablename__ = 'machine_factor'

    __table_args__ = (
        Index('ix_machine_factor_machine', 'machine_id'),
        Index('ix_machine_factor_dates', 'start_date', 'end_date'),
    )

    machine_factor_id = Column(Integer, primary_key=True, autoincrement=True)
    machine_id = Column(Integer, ForeignKey('machine.machine_id'), nullable=False)
    factor_value = Column(Float(15, 2), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)

    machine = relationship('Machine', back_populates='machine_factors')


class Queue(Base, TimestampMixin):
    """Job queues on resources."""
    __tablename__ = 'queue'

    __table_args__ = (
        Index('ix_queue_resource', 'resource_id'),
        Index('ix_queue_name', 'queue_name'),
    )

    queue_id = Column(Integer, primary_key=True, autoincrement=True)
    resource_id = Column(Integer, ForeignKey('resources.resource_id'), nullable=False)
    queue_name = Column(String(50), nullable=False)
    description = Column(String(255), nullable=False)
    wall_clock_hours_limit = Column(Float(5, 2))
    cos_id = Column(Integer)

    start_date = Column(DateTime)
    end_date = Column(DateTime)

    resource = relationship('Resource', back_populates='queues')
    queue_factors = relationship('QueueFactor', back_populates='queue')

    def __repr__(self):
        return f"<Queue(name='{self.queue_name}', resource='{self.resource.resource_name if self.resource else None}')>"


class QueueFactor(Base, TimestampMixin):
    """Charging factors for queues over time."""
    __tablename__ = 'queue_factor'

    __table_args__ = (
        Index('ix_queue_factor_queue', 'queue_id'),
        Index('ix_queue_factor_dates', 'start_date', 'end_date'),
    )

    queue_factor_id = Column(Integer, primary_key=True, autoincrement=True)
    queue_id = Column(Integer, ForeignKey('queue.queue_id'), nullable=False)
    factor_value = Column(Float, nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)

    queue = relationship('Queue', back_populates='queue_factors')


# ============================================================================
# Facility Management
# ============================================================================

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

    def __repr__(self):
        return f"<Facility(name='{self.facility_name}', code='{self.code}')>"


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
    creation_time = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)

    facility = relationship('Facility', back_populates='facility_resources')
    resource = relationship('Resource', back_populates='facility_resources')


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

    def __repr__(self):
        return f"<Panel(name='{self.panel_name}')>"


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


# ============================================================================
# Project Area of Interest
# ============================================================================

class AreaOfInterestGroup(Base, TimestampMixin, ActiveFlagMixin):
    """Groupings for research areas."""
    __tablename__ = 'area_of_interest_group'

    area_of_interest_group_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)

    areas = relationship('AreaOfInterest', back_populates='group')

    def __repr__(self):
        return f"<AreaOfInterestGroup(name='{self.name}')>"


class AreaOfInterest(Base, TimestampMixin, ActiveFlagMixin):
    """Research areas for projects."""
    __tablename__ = 'area_of_interest'

    area_of_interest_id = Column(Integer, primary_key=True, autoincrement=True)
    area_of_interest = Column(String(255), nullable=False, unique=True)
    area_of_interest_group_id = Column(Integer,
                                       ForeignKey('area_of_interest_group.area_of_interest_group_id'),
                                       nullable=False)

    group = relationship('AreaOfInterestGroup', back_populates='areas')
    projects = relationship('Project', back_populates='area_of_interest')

    def __repr__(self):
        return f"<AreaOfInterest(name='{self.area_of_interest}')>"


# ============================================================================
# Account and Allocation Management
# ============================================================================

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
    creation_time = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)

    # Relationships
    project = relationship('Project', back_populates='accounts')
    resource = relationship('Resource', back_populates='accounts')
    allocations = relationship('Allocation', back_populates='account')
    users = relationship('AccountUser', back_populates='account', lazy='selectin')

    def __repr__(self):
        return f"<Account(id={self.account_id}, project='{self.project.projcode if self.project else None}', resource='{self.resource.resource_name if self.resource else None}')>"


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


class AllocationType(Base, TimestampMixin, ActiveFlagMixin):
    """Types of allocations (CHAP, ASD-UNIV, etc.)."""
    __tablename__ = 'allocation_type'

    __table_args__ = (
        Index('ix_allocation_type_panel', 'panel_id'),
    )

    allocation_type_id = Column(Integer, primary_key=True, autoincrement=True)
    allocation_type = Column(String(20), nullable=False)
    default_allocation_amount = Column(Float(15, 2))
    fair_share_percentage = Column(Float)
    panel_id = Column(Integer, ForeignKey('panel.panel_id'))

    panel = relationship('Panel', back_populates='allocation_types')
    projects = relationship('Project', back_populates='allocation_type')

    def __repr__(self):
        return f"<AllocationType(type='{self.allocation_type}')>"


class Allocation(Base, TimestampMixin, SoftDeleteMixin):
    """Resource allocations for accounts."""
    __tablename__ = 'allocation'

    __table_args__ = (
        Index('ix_allocation_account', 'account_id'),
        Index('ix_allocation_parent', 'parent_allocation_id'),
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

    def __repr__(self):
        return f"<Allocation(id={self.allocation_id}, amount={self.amount}, active={self.is_active_at()})>"


class AllocationTransaction(Base):
    """Transaction history for allocations."""
    __tablename__ = 'allocation_transaction'

    __table_args__ = (
        Index('ix_allocation_transaction_allocation', 'allocation_id'),
        Index('ix_allocation_transaction_user', 'user_id'),
        Index('ix_allocation_transaction_related', 'related_transaction_id'),
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


# ============================================================================
# Project Management
# ============================================================================

class MnemonicCode(Base, TimestampMixin, ActiveFlagMixin):
    """Mnemonic codes for project naming."""
    __tablename__ = 'mnemonic_code'

    mnemonic_code_id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(3), nullable=False, unique=True)
    description = Column(String(200), nullable=False, unique=True)

    def __repr__(self):
        return f"<MnemonicCode(code='{self.code}', desc='{self.description}')>"


class ProjectNumber(Base):
    """Sequential project numbers."""
    __tablename__ = 'project_number'

    project_number_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('project.project_id'),
                       nullable=False, unique=True)

    project = relationship('Project', back_populates='project_number')


class Project(Base, TimestampMixin, ActiveFlagMixin):
    """Research projects."""
    __tablename__ = 'project'

    __table_args__ = (
        Index('ix_project_projcode', 'projcode'),
        Index('ix_project_lead', 'project_lead_user_id'),
        Index('ix_project_admin', 'project_admin_user_id'),
        Index('ix_project_active', 'active'),
        Index('ix_project_tree', 'tree_left', 'tree_right'),
        Index('ix_project_parent', 'parent_id'),
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
    projcode = Column(String(30), nullable=False, unique=True, default='')
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
    project_number = relationship('ProjectNumber', back_populates='project', uselist=False)
    default_projects = relationship('DefaultProject', back_populates='project')

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


class ProjectDirectory(Base, TimestampMixin, DateRangeMixin):
    """File system directories associated with projects."""
    __tablename__ = 'project_directory'

    __table_args__ = (
        Index('ix_project_directory_project', 'project_id'),
    )

    project_directory_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('project.project_id'), nullable=False)
    directory_name = Column(String(255), nullable=False)

    project = relationship('Project', back_populates='directories')


class ProjectOrganization(Base, TimestampMixin, DateRangeMixin):
    """Maps projects to organizations."""
    __tablename__ = 'project_organization'

    __table_args__ = (
        Index('ix_project_organization_project', 'project_id'),
        Index('ix_project_organization_org', 'organization_id'),
        Index('ix_project_organization_dates', 'start_date', 'end_date'),
    )

    project_organization_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('project.project_id'), nullable=False)
    organization_id = Column(Integer, ForeignKey('organization.organization_id'),
                            nullable=False)

    project = relationship('Project', back_populates='organizations')
    organization = relationship('Organization', back_populates='projects')


class DefaultProject(Base, TimestampMixin):
    """Default projects for users on resources."""
    __tablename__ = 'default_project'

    __table_args__ = (
        Index('ix_default_project_user', 'user_id'),
        Index('ix_default_project_resource', 'resource_id'),
        Index('ix_default_project_project', 'project_id'),
    )

    default_project_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    project_id = Column(Integer, ForeignKey('project.project_id'), nullable=False)
    resource_id = Column(Integer, ForeignKey('resources.resource_id'), nullable=False)
    modified_time = Column(TIMESTAMP, server_default=text('CURRENT_TIMESTAMP'))

    user = relationship('User', back_populates='default_projects')
    project = relationship('Project', back_populates='default_projects')
    resource = relationship('Resource', back_populates='default_projects')


# ============================================================================
# Contract Management
# ============================================================================

class ContractSource(Base, TimestampMixin, ActiveFlagMixin):
    """Sources of funding contracts."""
    __tablename__ = 'contract_source'

    contract_source_id = Column(Integer, primary_key=True, autoincrement=True)
    contract_source = Column(String(50), nullable=False, unique=True)

    contracts = relationship('Contract', back_populates='contract_source')

    def __repr__(self):
        return f"<ContractSource(source='{self.contract_source}')>"


class NSFProgram(Base, TimestampMixin, ActiveFlagMixin):
    """NSF program classifications."""
    __tablename__ = 'nsf_program'

    nsf_program_id = Column(Integer, primary_key=True, autoincrement=True)
    nsf_program_name = Column(String(255), nullable=False, unique=True)

    contracts = relationship('Contract', back_populates='nsf_program')

    def __repr__(self):
        return f"<NSFProgram(name='{self.nsf_program_name}')>"


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

    contract_source = relationship('ContractSource', back_populates='contracts')
    nsf_program = relationship('NSFProgram', back_populates='contracts')
    principal_investigator = relationship('User',
                                         foreign_keys=[principal_investigator_user_id])
    contract_monitor = relationship('User',
                                   foreign_keys=[contract_monitor_user_id])
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
    creation_time = Column(DateTime, nullable=False, default=datetime.utcnow)

    project = relationship('Project', back_populates='contracts')
    contract = relationship('Contract', back_populates='projects')


# ============================================================================
# Role/Permission Management
# ============================================================================

class Role(Base):
    """Security roles in the system."""
    __tablename__ = 'role'

    role_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    description = Column(String(255))

    users = relationship('RoleUser', back_populates='role')

    def __repr__(self):
        return f"<Role(name='{self.name}')>"


class RoleUser(Base):
    """Maps users to roles."""
    __tablename__ = 'role_user'

    __table_args__ = (
        Index('ix_role_user_role', 'role_id'),
        Index('ix_role_user_user', 'user_id'),
    )

    role_user_id = Column(Integer, primary_key=True, autoincrement=True)
    role_id = Column(Integer, ForeignKey('role.role_id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)

    role = relationship('Role', back_populates='users')
    user = relationship('User', back_populates='role_assignments')


# ============================================================================
# Activity and Charge Tables (HPC)
# ============================================================================

class HPCCos(Base, TimestampMixin):
    """HPC Class of Service definitions."""
    __tablename__ = 'hpc_cos'

    hpc_cos_id = Column(Integer, primary_key=True)
    description = Column(String(50))
    modified_time = Column(TIMESTAMP, nullable=False,
                          server_default=text('CURRENT_TIMESTAMP'),
                          onupdate=datetime.utcnow)

    activities = relationship('HPCActivity', back_populates='hpc_cos')

    def __repr__(self):
        return f"<HPCCos(id={self.hpc_cos_id}, desc='{self.description}')>"


class HPCActivity(Base):
    """HPC job activity records."""
    __tablename__ = 'hpc_activity'

    __table_args__ = (
        Index('ix_hpc_activity_job', 'job_id'),
        Index('ix_hpc_activity_date', 'activity_date'),
        Index('ix_hpc_activity_cos', 'hpc_cos_id'),
    )

    hpc_activity_id = Column(Integer, primary_key=True, autoincrement=True)
    unix_uid = Column(Integer)
    username = Column(String(35), nullable=False)
    projcode = Column(String(30), nullable=False)
    job_id = Column(String(35), nullable=False)
    job_name = Column(String(255))
    queue_name = Column(String(100), nullable=False)
    machine = Column(String(100), nullable=False)

    start_time = Column(Integer, nullable=False)
    end_time = Column(Integer, nullable=False)
    submit_time = Column(Integer, nullable=False)

    unix_user_time = Column(Float)
    unix_system_time = Column(Float)
    queue_wait_time = Column(Integer)

    num_nodes_used = Column(Integer)
    num_cores_used = Column(Integer)
    hpc_cos_id = Column(Integer, ForeignKey('hpc_cos.hpc_cos_id'))

    exit_status = Column(String(20))
    from_host = Column(String(256))
    interactive = Column(Integer)
    reservation_id = Column(String(255))

    processing_status = Column(Boolean)
    error_comment = Column(Text)

    activity_date = Column(DateTime)
    load_date = Column(DateTime, nullable=False)
    modified_time = Column(TIMESTAMP)

    external_charge = Column(Float(15, 8))
    job_idx = Column(Integer, nullable=False)

    hpc_cos = relationship('HPCCos')


class HPCCharge(Base):
    """HPC charges derived from activity."""
    __tablename__ = 'hpc_charge'

    __table_args__ = (
        Index('ix_hpc_charge_account', 'account_id'),
        Index('ix_hpc_charge_user', 'user_id'),
        Index('ix_hpc_charge_activity', 'hpc_activity_id', unique=True),
        Index('ix_hpc_charge_date', 'charge_date'),
        Index('ix_hpc_charge_activity_date', 'activity_date'),
    )

    hpc_charge_id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)
    hpc_activity_id = Column(Integer, ForeignKey('hpc_activity.hpc_activity_id'),
                             nullable=False, unique=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    charge_date = Column(DateTime, nullable=False)
    activity_date = Column(DateTime)
    charge = Column(Float(22, 8))
    core_hours = Column(Float(22, 8))

    account = relationship('Account')
    activity = relationship('HPCActivity')
    user = relationship('User')


class HPCChargeSummary(Base):
    """Daily summary of HPC charges."""
    __tablename__ = 'hpc_charge_summary'

    __table_args__ = (
        Index('ix_hpc_charge_summary_date', 'activity_date'),
        Index('ix_hpc_charge_summary_user', 'user_id'),
        Index('ix_hpc_charge_summary_account', 'account_id'),
        Index('ix_hpc_charge_summary_machine', 'machine'),
        Index('ix_hpc_charge_summary_queue', 'queue_name'),
    )

    hpc_charge_summary_id = Column(Integer, primary_key=True, autoincrement=True)
    activity_date = Column(DateTime, nullable=False)

    act_username = Column(String(35))
    unix_uid = Column(Integer)
    act_unix_uid = Column(Integer)
    projcode = Column(String(30))
    username = Column(String(35))
    act_projcode = Column(String(30))
    facility_name = Column(String(30))

    machine = Column(String(100), nullable=False)
    queue_name = Column(String(100), nullable=False)

    user_id = Column(Integer, ForeignKey('users.user_id'))
    account_id = Column(Integer, ForeignKey('account.account_id'))

    num_jobs = Column(Integer)
    core_hours = Column(Float(22, 8))
    charges = Column(Float(22, 8))

    user = relationship('User')
    account = relationship('Account')


class HPCChargeSummaryStatus(Base):
    """Tracks which charge summaries are current."""
    __tablename__ = 'hpc_charge_summary_status'

    activity_date = Column(DateTime, primary_key=True)
    current = Column(Boolean)


# ============================================================================
# Disk Activity and Charges
# ============================================================================

class DiskCos(Base, TimestampMixin):
    """Disk Class of Service definitions."""
    __tablename__ = 'disk_cos'

    disk_cos_id = Column(Integer, primary_key=True)
    description = Column(String(255), nullable=False)

    activities = relationship('DiskActivity', back_populates='disk_cos')

class DiskActivity(Base, TimestampMixin):
    """Disk usage activity records."""
    __tablename__ = 'disk_activity'

    __table_args__ = (
        Index('ix_disk_activity_directory', 'directory_name'),
        Index('ix_disk_activity_cos', 'disk_cos_id'),
    )

    disk_activity_id = Column(Integer, primary_key=True, autoincrement=True)
    directory_name = Column(String(255), nullable=False)
    username = Column(String(35), nullable=False)
    projcode = Column(String(30))
    activity_date = Column(DateTime, nullable=False)
    reporting_interval = Column(Integer, nullable=False)

    file_size_total = Column(BigInteger, nullable=False)
    bytes = Column(BigInteger, nullable=False)
    number_of_files = Column(Integer)

    load_date = Column(DateTime, nullable=False)
    disk_cos_id = Column(Integer, ForeignKey('disk_cos.disk_cos_id'), nullable=False)

    error_comment = Column(Text)
    processing_status = Column(Boolean)
    resource_name = Column(String(40))

    disk_cos = relationship('DiskCos')


class DiskCharge(Base):
    """Disk charges derived from activity."""
    __tablename__ = 'disk_charge'

    __table_args__ = (
        Index('ix_disk_charge_account', 'account_id'),
        Index('ix_disk_charge_user', 'user_id'),
        Index('ix_disk_charge_activity', 'disk_activity_id', unique=True),
        Index('ix_disk_charge_date', 'charge_date'),
    )

    disk_charge_id = Column(Integer, primary_key=True, autoincrement=True)
    disk_activity_id = Column(Integer, ForeignKey('disk_activity.disk_activity_id'),
                              nullable=False, unique=True)
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)
    charge_date = Column(DateTime, nullable=False)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    charge = Column(Float(22, 8))
    terabyte_year = Column(Float(22, 8))
    activity_date = Column(DateTime)

    account = relationship('Account')
    activity = relationship('DiskActivity')
    user = relationship('User')


class DiskChargeSummary(Base):
    """Daily summary of disk charges."""
    __tablename__ = 'disk_charge_summary'

    __table_args__ = (
        Index('ix_disk_charge_summary_date', 'activity_date'),
        Index('ix_disk_charge_summary_user', 'user_id'),
        Index('ix_disk_charge_summary_account', 'account_id'),
    )

    disk_charge_summary_id = Column(Integer, primary_key=True, autoincrement=True)
    activity_date = Column(DateTime, nullable=False)

    act_username = Column(String(35))
    unix_uid = Column(Integer)
    act_unix_uid = Column(Integer)
    projcode = Column(String(30))
    username = Column(String(35))
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    act_projcode = Column(String(30))
    facility_name = Column(String(30))
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)

    number_of_files = Column(Integer)
    bytes = Column(BigInteger)
    terabyte_years = Column(Float(22, 8))
    charges = Column(Float(22, 8))

    user = relationship('User')
    account = relationship('Account')


class DiskChargeSummaryStatus(Base):
    """Tracks which disk charge summaries are current."""
    __tablename__ = 'disk_charge_summary_status'

    activity_date = Column(DateTime, primary_key=True)
    current = Column(Boolean)


# ============================================================================
# Archive Activity and Charges
# ============================================================================

class ArchiveCos(Base, TimestampMixin):
    """Archive Class of Service definitions."""
    __tablename__ = 'archive_cos'

    archive_cos_id = Column(Integer, primary_key=True)
    number_of_copies = Column(Integer, nullable=False)
    description = Column(String(255), nullable=False)

    activities = relationship('ArchiveActivity', back_populates='archive_cos')

class ArchiveActivity(Base, TimestampMixin):
    """Archive (HPSS) activity records."""
    __tablename__ = 'archive_activity'

    __table_args__ = (
        Index('ix_archive_activity_type', 'type_act'),
        Index('ix_archive_activity_cos', 'archive_cos_id'),
    )

    archive_activity_id = Column(Integer, primary_key=True, autoincrement=True)
    archive_resource = Column(String(5), nullable=False)
    type_act = Column(String(1), nullable=False)
    reporting_interval = Column(Integer)
    activity_date = Column(DateTime, nullable=False)

    number_of_files = Column(Integer, nullable=False)
    bytes = Column(BigInteger, nullable=False)

    dns = Column(String(100))
    unix_uid = Column(Integer, nullable=False)
    username = Column(String(30))
    projcode = Column(String(30), nullable=False)

    load_date = Column(DateTime, nullable=False)
    processing_status = Column(Boolean)
    error_comment = Column(Text)

    archive_cos_id = Column(Integer, ForeignKey('archive_cos.archive_cos_id'))

    archive_cos = relationship('ArchiveCos')


class ArchiveCharge(Base):
    """Archive charges derived from activity."""
    __tablename__ = 'archive_charge'

    __table_args__ = (
        Index('ix_archive_charge_account', 'account_id'),
        Index('ix_archive_charge_user', 'user_id'),
        Index('ix_archive_charge_activity', 'archive_activity_id', unique=True),
        Index('ix_archive_charge_date', 'charge_date'),
    )

    archive_charge_id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)
    charge_date = Column(DateTime, nullable=False)
    archive_activity_id = Column(Integer, ForeignKey('archive_activity.archive_activity_id'),
                                 nullable=False, unique=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    charge = Column(Float(22, 8))
    terabyte_year = Column(Float(22, 8))
    activity_date = Column(DateTime)

    account = relationship('Account')
    activity = relationship('ArchiveActivity')
    user = relationship('User')


class ArchiveChargeSummary(Base):
    """Daily summary of archive charges."""
    __tablename__ = 'archive_charge_summary'

    __table_args__ = (
        Index('ix_archive_charge_summary_date', 'activity_date'),
        Index('ix_archive_charge_summary_user', 'user_id'),
        Index('ix_archive_charge_summary_account', 'account_id'),
    )

    archive_charge_summary_id = Column(Integer, primary_key=True, autoincrement=True)
    activity_date = Column(DateTime, nullable=False)

    act_username = Column(String(35))
    unix_uid = Column(Integer)
    act_unix_uid = Column(Integer)
    projcode = Column(String(30))
    username = Column(String(35))
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    act_projcode = Column(String(30))
    facility_name = Column(String(30))
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)

    number_of_files = Column(Integer)
    bytes = Column(BigInteger)
    terabyte_years = Column(Float(22, 8))
    charges = Column(Float(22, 8))

    user = relationship('User')
    account = relationship('Account')


class ArchiveChargeSummaryStatus(Base):
    """Tracks which archive charge summaries are current."""
    __tablename__ = 'archive_charge_summary_status'

    activity_date = Column(DateTime, primary_key=True)
    current = Column(Boolean)


# ============================================================================
# Charge Adjustments
# ============================================================================

class ChargeAdjustmentType(Base, TimestampMixin):
    """Types of charge adjustments (Credit, Debit, Refund)."""
    __tablename__ = 'charge_adjustment_type'

    charge_adjustment_type_id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(20), nullable=False)

    adjustments = relationship('ChargeAdjustment', back_populates='adjustment_type')

    def __repr__(self):
        return f"<ChargeAdjustmentType(type='{self.type}')>"


class ChargeAdjustment(Base):
    """Manual adjustments to account charges."""
    __tablename__ = 'charge_adjustment'

    __table_args__ = (
        Index('ix_charge_adjustment_account', 'account_id'),
        Index('ix_charge_adjustment_type', 'charge_adjustment_type_id'),
        Index('ix_charge_adjustment_adjusted_by', 'adjusted_by_id'),
    )

    charge_adjustment_id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)
    charge_adjustment_type_id = Column(Integer,
                                       ForeignKey('charge_adjustment_type.charge_adjustment_type_id'),
                                       nullable=False)
    amount = Column(Float, nullable=False)
    comment = Column(Text)
    adjustment_date = Column(DateTime, nullable=False)
    adjusted_by_id = Column(Integer, ForeignKey('users.user_id'))

    account = relationship('Account')
    adjustment_type = relationship('ChargeAdjustmentType', back_populates='adjustments')
    adjusted_by = relationship('User')


# ============================================================================
# Access Control
# ============================================================================

class AccessBranch(Base):
    """Access branches for resource access control."""
    __tablename__ = 'access_branch'

    access_branch_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(40), nullable=False, unique=True)

    resources = relationship('AccessBranchResource', back_populates='access_branch')

    def __repr__(self):
        return f"<AccessBranch(name='{self.name}')>"


class AccessBranchResource(Base):
    """Maps access branches to resources."""
    __tablename__ = 'access_branch_resource'

    __table_args__ = (
        Index('ix_access_branch_resource_branch', 'access_branch_id'),
        Index('ix_access_branch_resource_resource', 'resource_id'),
    )

    access_branch_id = Column(Integer, ForeignKey('access_branch.access_branch_id'),
                              primary_key=True)
    resource_id = Column(Integer, ForeignKey('resources.resource_id'), primary_key=True)

    access_branch = relationship('AccessBranch', back_populates='resources')
    resource = relationship('Resource')


# ============================================================================
# Utility/Operational Tables
# ============================================================================

class Synchronizer(Base):
    """Tracks last run times for synchronization jobs."""
    __tablename__ = 'synchronizer'

    synchronizer_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    last_run = Column(TIMESTAMP)

    def __repr__(self):
        return f"<Synchronizer(name='{self.name}', last_run={self.last_run})>"


class ManualTask(Base):
    """Manual intervention tasks."""
    __tablename__ = 'manual_task'

    __table_args__ = (
        Index('ix_manual_task_client', 'client'),
    )

    manual_task_id = Column(Integer, primary_key=True, autoincrement=True)
    client = Column(String(32), nullable=False)
    transaction_context = Column(String(256))
    transaction_id = Column(String(64), nullable=False)
    job_key = Column(String(32), nullable=False)
    job_alias = Column(String(128))
    client_job_id = Column(String(32), nullable=False)
    name = Column(String(32), nullable=False)
    state = Column(String(16), nullable=False)
    mode = Column(String(64))
    assignee = Column(String(32))
    timestamp = Column(BigInteger, nullable=False)
    data = Column(Text, nullable=False)
    delete_on_clear = Column(Boolean, nullable=False, default=False)

    products = relationship('Product', back_populates='manual_task')


class Product(Base):
    """Products from manual tasks."""
    __tablename__ = 'product'

    __table_args__ = (
        Index('ix_product_manual_task', 'manual_task_id'),
    )

    product_id = Column(Integer, primary_key=True, autoincrement=True)
    manual_task_id = Column(Integer, ForeignKey('manual_task.manual_task_id'),
                           nullable=False)
    name = Column(String(31), nullable=False)
    value = Column(String(16384))
    timestamp = Column(BigInteger, nullable=False)

    manual_task = relationship('ManualTask', back_populates='products')


# ============================================================================
# End of module
# ============================================================================
