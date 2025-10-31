"""
SQLAlchemy ORM models for SAM (System for Allocation Management) database.

This module provides ORM classes for managing users, projects, allocations,
and related entities in an HPC allocation system.
"""

from datetime import datetime
from typing import List, Optional, Dict
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean, 
    ForeignKey, Text, BigInteger, TIMESTAMP, text
)
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


# ============================================================================
# User Management
# ============================================================================

class User(Base):
    """Represents a user in the system."""
    __tablename__ = 'users'
    
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
    
    # Timestamps
    creation_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)
    pdb_modified_time = Column(TIMESTAMP)
    access_status_change_time = Column(TIMESTAMP)
    
    # Tokens
    token_type = Column(String(30))
    idms_sync_token = Column(String(64))
    
    # Relationships
    academic_status = relationship('AcademicStatus', back_populates='users')
    email_addresses = relationship('EmailAddress', back_populates='user')
    institutions = relationship('UserInstitution', back_populates='user')
    organizations = relationship('UserOrganization', back_populates='user')
    accounts = relationship('AccountUser', back_populates='user')
    led_projects = relationship('Project', foreign_keys='Project.project_lead_user_id', back_populates='lead')
    admin_projects = relationship('Project', foreign_keys='Project.project_admin_user_id', back_populates='admin')
    primary_group = relationship('AdhocGroup', foreign_keys=[primary_gid])
    
    @property
    def full_name(self) -> str:
        """Return the user's full name."""
        parts = [self.first_name, self.middle_name, self.last_name]
        return ' '.join(p for p in parts if p)
    
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
            if email.active or email.active is None:  # NULL is considered active
                return email.email_address
        
        # Last resort: return any email
        if self.email_addresses:
            return self.email_addresses[0].email_address
        
        return None
    
    @property
    def all_emails(self) -> List[str]:
        """Return all email addresses for this user."""
        return [email.email_address for email in self.email_addresses]
    
    def get_emails_detailed(self) -> List[dict]:
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

    def __repr__(self):
        return f"<User(username='{self.username}', name='{self.full_name}')>"


class UserAlias(Base):
    """Stores external identifiers for users."""
    __tablename__ = 'user_alias'
    
    user_alias_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False, unique=True)
    username = Column(String(35), nullable=False, unique=True)
    orcid_id = Column(String(20), index=True)
    access_global_id = Column(String(31), index=True)
    modified_time = Column(TIMESTAMP(3), server_default=text('CURRENT_TIMESTAMP(3)'))
    creation_time = Column(DateTime, default=datetime.utcnow)
    
    user = relationship('User')


class EmailAddress(Base):
    """Email addresses for users."""
    __tablename__ = 'email_address'
    
    email_address_id = Column(Integer, primary_key=True, autoincrement=True)
    email_address = Column(String(255), nullable=False)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    is_primary = Column(Boolean, nullable=False)
    active = Column(Boolean)
    creation_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)
    
    user = relationship('User', back_populates='email_addresses')


class AcademicStatus(Base):
    """Academic status types (Faculty, Student, etc.)."""
    __tablename__ = 'academic_status'
    
    academic_status_id = Column(Integer, primary_key=True, autoincrement=True)
    academic_status_code = Column(String(2), nullable=False)
    description = Column(String(100), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    deleted = Column(Boolean, nullable=False, default=False)
    creation_time = Column(DateTime, nullable=False)
    modified_time = Column(TIMESTAMP)
    deletion_time = Column(TIMESTAMP)
    
    users = relationship('User', back_populates='academic_status')


class UserInstitution(Base):
    """Maps users to institutions."""
    __tablename__ = 'user_institution'
    
    user_institution_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    institution_id = Column(Integer, ForeignKey('institution.institution_id'), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)
    creation_time = Column(DateTime, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)
    
    user = relationship('User', back_populates='institutions')
    institution = relationship('Institution', back_populates='users')


class UserOrganization(Base):
    """Maps users to organizations."""
    __tablename__ = 'user_organization'
    
    user_organization_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    organization_id = Column(Integer, ForeignKey('organization.organization_id'), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)
    creation_time = Column(DateTime, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)
    
    user = relationship('User', back_populates='organizations')
    organization = relationship('Organization', back_populates='users')


# ============================================================================
# Group Management
# ============================================================================

class AdhocGroup(Base):
    """Unix groups for organizing users."""
    __tablename__ = 'adhoc_group'
    
    group_id = Column(Integer, primary_key=True, autoincrement=True)
    group_name = Column(String(30), nullable=False, unique=True)
    unix_gid = Column(Integer, nullable=False, unique=True)
    active = Column(Boolean, nullable=False)
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
# Project Management
# ============================================================================

class Project(Base):
    """Research projects."""
    __tablename__ = 'project'
    
    project_id = Column(Integer, primary_key=True, autoincrement=True)
    projcode = Column(String(30), nullable=False, unique=True)
    title = Column(String(255), nullable=False)
    abstract = Column(Text)
    
    # Leadership
    project_lead_user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    project_admin_user_id = Column(Integer, ForeignKey('users.user_id'))
    
    # Status flags
    active = Column(Boolean, nullable=False, default=True)
    charging_exempt = Column(Boolean, nullable=False, default=False)
    
    # Foreign keys
    area_of_interest_id = Column(Integer, ForeignKey('area_of_interest.area_of_interest_id'), nullable=False)
    allocation_type_id = Column(Integer, ForeignKey('allocation_type.allocation_type_id'))
    parent_id = Column(Integer, ForeignKey('project.project_id'))
    
    # Tree structure (nested set model)
    tree_left = Column(Integer)
    tree_right = Column(Integer)
    tree_root = Column(Integer, ForeignKey('project.project_id'))
    
    # Unix group
    unix_gid = Column(Integer)
    ext_alias = Column(String(64))
    
    # Timestamps
    creation_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)
    membership_change_time = Column(TIMESTAMP)
    inactivate_time = Column(DateTime)
    
    # Relationships
    lead = relationship('User', foreign_keys=[project_lead_user_id], back_populates='led_projects')
    admin = relationship('User', foreign_keys=[project_admin_user_id], back_populates='admin_projects')
    area_of_interest = relationship('AreaOfInterest', back_populates='projects')
    allocation_type = relationship('AllocationType', back_populates='projects')
    accounts = relationship('Account', back_populates='project')
    directories = relationship('ProjectDirectory', back_populates='project')
    organizations = relationship('ProjectOrganization', back_populates='project')
    contracts = relationship('ProjectContract', back_populates='project')
    parent = relationship('Project', remote_side=[project_id], foreign_keys=[parent_id])
    
    @property
    def current_allocation(self) -> Optional['Allocation']:
        """Get the most recent active allocation (across all resources)."""
        active_allocs = [
            alloc for account in self.accounts 
            for alloc in account.allocations 
            if not alloc.deleted and (alloc.end_date is None or alloc.end_date >= datetime.utcnow())
        ]
        return max(active_allocs, key=lambda a: a.allocation_id) if active_allocs else None
    
    def get_allocation_by_resource(self, resource_name: str) -> Optional['Allocation']:
        """
        Get the most recent active allocation for a specific resource.
        
        Args:
            resource_name: Name of the resource (e.g., 'Derecho', 'GLADE', 'Campaign')
        
        Returns:
            Most recent active allocation for that resource, or None
        """
        active_allocs = [
            alloc for account in self.accounts
            if account.resource and account.resource.resource_name == resource_name
            for alloc in account.allocations
            if not alloc.deleted and (alloc.end_date is None or alloc.end_date >= datetime.utcnow())
        ]
        return max(active_allocs, key=lambda a: a.allocation_id) if active_allocs else None
    
    def get_all_allocations_by_resource(self) -> Dict[str, Optional['Allocation']]:
        """
        Get the most recent active allocation for each resource.
        
        Returns:
            Dict mapping resource_name to Allocation object
        """
        allocations_by_resource = {}
        for account in self.accounts:
            if account.resource:
                resource_name = account.resource.resource_name
                active_allocs = [
                    alloc for alloc in account.allocations
                    if not alloc.deleted and (alloc.end_date is None or alloc.end_date >= datetime.utcnow())
                ]
                if active_allocs:
                    current = max(active_allocs, key=lambda a: a.allocation_id)
                    allocations_by_resource[resource_name] = current
        return allocations_by_resource
    
    def __repr__(self):
        return f"<Project(projcode='{self.projcode}', title='{self.title[:50]}...')>"


class ProjectDirectory(Base):
    """File system directories associated with projects."""
    __tablename__ = 'project_directory'
    
    project_directory_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('project.project_id'), nullable=False)
    directory_name = Column(String(255), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)
    creation_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)
    
    project = relationship('Project', back_populates='directories')


class ProjectOrganization(Base):
    """Maps projects to organizations."""
    __tablename__ = 'project_organization'
    
    project_organization_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('project.project_id'), nullable=False)
    organization_id = Column(Integer, ForeignKey('organization.organization_id'), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)
    creation_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)
    
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
# Account and Allocation Management
# ============================================================================

class Account(Base):
    """Billing accounts linking projects to resources."""
    __tablename__ = 'account'
    
    account_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('project.project_id'))
    resource_id = Column(Integer, ForeignKey('resources.resource_id'))
    
    # Thresholds
    first_threshold = Column(Integer)
    second_threshold = Column(Integer)
    cutoff_threshold = Column(Integer, nullable=False, default=100)
    
    # Status
    deleted = Column(Boolean, nullable=False, default=False)
    
    # Timestamps
    creation_time = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)
    deletion_time = Column(TIMESTAMP)
    
    # Relationships
    project = relationship('Project', back_populates='accounts')
    resource = relationship('Resource', back_populates='accounts')
    allocations = relationship('Allocation', back_populates='account')
    users = relationship('AccountUser', back_populates='account')


class AccountUser(Base):
    """Maps users to accounts with date ranges."""
    __tablename__ = 'account_user'
    
    account_user_id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)
    creation_time = Column(DateTime, nullable=False)
    modified_time = Column(TIMESTAMP)
    
    account = relationship('Account', back_populates='users')
    user = relationship('User', back_populates='accounts')


class Allocation(Base):
    """Resource allocations for accounts."""
    __tablename__ = 'allocation'
    
    allocation_id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('account.account_id'), nullable=False)
    parent_allocation_id = Column(Integer, ForeignKey('allocation.allocation_id'))
    
    amount = Column(Float(15, 2), nullable=False)
    description = Column(String(255))
    
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)
    
    deleted = Column(Boolean, nullable=False, default=False)
    creation_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)
    deletion_time = Column(TIMESTAMP)
    
    # Relationships
    account = relationship('Account', back_populates='allocations')
    transactions = relationship('AllocationTransaction', back_populates='allocation')
    parent = relationship('Allocation', remote_side=[allocation_id])
    
    @property
    def is_active(self) -> bool:
        """Check if allocation is currently active."""
        now = datetime.utcnow()
        return (not self.deleted and 
                self.start_date <= now and 
                (self.end_date is None or self.end_date >= now))


class AllocationTransaction(Base):
    """Transaction history for allocations."""
    __tablename__ = 'allocation_transaction'
    
    allocation_transaction_id = Column(Integer, primary_key=True, autoincrement=True)
    allocation_id = Column(Integer, ForeignKey('allocation.allocation_id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.user_id'))
    related_transaction_id = Column(Integer, ForeignKey('allocation_transaction.allocation_transaction_id'))
    
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
    related_transaction = relationship('AllocationTransaction', remote_side=[allocation_transaction_id])


class AllocationType(Base):
    """Types of allocations (CHAP, ASD-UNIV, etc.)."""
    __tablename__ = 'allocation_type'
    
    allocation_type_id = Column(Integer, primary_key=True, autoincrement=True)
    allocation_type = Column(String(20), nullable=False)
    default_allocation_amount = Column(Float(15, 2))
    fair_share_percentage = Column(Float)
    panel_id = Column(Integer, ForeignKey('panel.panel_id'))
    active = Column(Boolean, nullable=False, default=True)
    creation_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)
    
    panel = relationship('Panel', back_populates='allocation_types')
    projects = relationship('Project', back_populates='allocation_type')


# ============================================================================
# Supporting Tables
# ============================================================================

class Institution(Base):
    """Educational and research institutions."""
    __tablename__ = 'institution'
    
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
    
    creation_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)
    idms_sync_token = Column(String(64))
    
    users = relationship('UserInstitution', back_populates='institution')


class Organization(Base):
    """Organizational units (departments, labs, etc.)."""
    __tablename__ = 'organization'
    
    organization_id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    acronym = Column(String(15), nullable=False, unique=True)
    description = Column(String(255))
    parent_org_id = Column(Integer, ForeignKey('organization.organization_id'))
    
    # Tree structure
    tree_left = Column(Integer)
    tree_right = Column(Integer)
    level = Column(String(80))
    level_code = Column(String(10))
    
    active = Column(Boolean, nullable=False)
    creation_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)
    idms_sync_token = Column(String(64))
    
    users = relationship('UserOrganization', back_populates='organization')
    projects = relationship('ProjectOrganization', back_populates='organization')
    parent = relationship('Organization', remote_side=[organization_id])


class AreaOfInterest(Base):
    """Research areas for projects."""
    __tablename__ = 'area_of_interest'
    
    area_of_interest_id = Column(Integer, primary_key=True, autoincrement=True)
    area_of_interest = Column(String(255), nullable=False, unique=True)
    area_of_interest_group_id = Column(Integer, ForeignKey('area_of_interest_group.area_of_interest_group_id'), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    creation_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)
    
    group = relationship('AreaOfInterestGroup', back_populates='areas')
    projects = relationship('Project', back_populates='area_of_interest')


class AreaOfInterestGroup(Base):
    """Groupings for research areas."""
    __tablename__ = 'area_of_interest_group'
    
    area_of_interest_group_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    active = Column(Boolean, nullable=False, default=True)
    creation_time = Column(DateTime, nullable=False)
    modified_time = Column(TIMESTAMP)
    
    areas = relationship('AreaOfInterest', back_populates='group')


class Resource(Base):
    """Computing resources (HPC systems, storage, etc.)."""
    __tablename__ = 'resources'
    
    resource_id = Column(Integer, primary_key=True, autoincrement=True)
    resource_name = Column(String(40), nullable=False, unique=True)
    resource_type_id = Column(Integer, ForeignKey('resource_type.resource_type_id'), nullable=False)
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
    
    creation_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)
    
    accounts = relationship('Account', back_populates='resource')
    resource_type = relationship('ResourceType', back_populates='resources')


class ResourceType(Base):
    """Types of resources (HPC, DISK, ARCHIVE, etc.)."""
    __tablename__ = 'resource_type'
    
    resource_type_id = Column(Integer, primary_key=True, autoincrement=True)
    resource_type = Column(String(35), nullable=False, unique=True)
    description = Column(String(255))
    active = Column(Boolean, nullable=False, default=True)
    grace_period_days = Column(Integer)
    creation_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)
    
    resources = relationship('Resource', back_populates='resource_type')


class Facility(Base):
    """Facility classifications (NCAR, UNIV, etc.)."""
    __tablename__ = 'facility'
    
    facility_id = Column(Integer, primary_key=True, autoincrement=True)
    facility_name = Column(String(30), nullable=False, unique=True)
    code = Column(String(1), unique=True)
    description = Column(String(255), nullable=False)
    fair_share_percentage = Column(Float)
    active = Column(Boolean, nullable=False, default=True)
    creation_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)
    
    panels = relationship('Panel', back_populates='facility')


class Panel(Base):
    """Allocation review panels."""
    __tablename__ = 'panel'
    
    panel_id = Column(Integer, primary_key=True, autoincrement=True)
    panel_name = Column(String(30), nullable=False, unique=True)
    description = Column(String(100))
    facility_id = Column(Integer, ForeignKey('facility.facility_id'), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    creation_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)
    
    facility = relationship('Facility', back_populates='panels')
    allocation_types = relationship('AllocationType', back_populates='panel')


class Contract(Base):
    """Funding contracts (NSF awards, etc.)."""
    __tablename__ = 'contract'
    
    contract_id = Column(Integer, primary_key=True, autoincrement=True)
    contract_source_id = Column(Integer, ForeignKey('contract_source.contract_source_id'), nullable=False)
    contract_number = Column(String(50), nullable=False, unique=True)
    title = Column(String(255), nullable=False)
    url = Column(String(1000))
    
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)
    
    principal_investigator_user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    contract_monitor_user_id = Column(Integer, ForeignKey('users.user_id'))
    nsf_program_id = Column(Integer, ForeignKey('nsf_program.nsf_program_id'))
    
    creation_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    modified_time = Column(TIMESTAMP)
    
    projects = relationship('ProjectContract', back_populates='contract')


class LoginType(Base):
    """Types of login accounts."""
    __tablename__ = 'login_type'
    
    login_type_id = Column(Integer, primary_key=True)
    type = Column(String(30), nullable=False)
