"""
SAM (System Accounting Manager) ORM Models

Organized into logical subsystems:
- core: Users, organizations, groups
- resources: Computing resources and facilities
- projects: Project management and contracts
- accounting: Accounts, allocations, adjustments
- activity: Usage activity by resource type
- summaries: Daily charge summaries
- integration: External system integration (XRAS)
- security: Roles and access control
"""

from .base import (
    Base,
    TimestampMixin,
    SoftDeleteMixin,
    ActiveFlagMixin,
    DateRangeMixin,
    SessionMixin
)

# Core modules
from .core.users import User, UserAlias, EmailAddress, Phone, AcademicStatus
from .core.organizations import Organization, Institution
from .core.groups import AdhocGroup

# Resources
from .resources.resources import Resource, ResourceType
from .resources.machines import Machine, Queue
from .resources.facilities import Facility, Panel

# Projects
from .projects.projects import Project
from .projects.areas import AreaOfInterest
from .projects.contracts import Contract

# Accounting
from .accounting.accounts import Account, AccountUser
from .accounting.allocations import Allocation, AllocationType

# Activity (commonly used)
from .activity.computational import CompJob, CompActivity
from .activity.hpc import HPCActivity
from .activity.disk import DiskActivity

# Geography
from .geography import Country, StateProv

# Version
__version__ = '1.0.0'

# Expose commonly used classes at package level
__all__ = [
    # Base
    'Base',
    # Core
    'User',
    'Organization',
    'Institution',
    # Resources
    'Resource',
    'Machine',
    'Queue',
    'Facility',
    # Projects
    'Project',
    'Contract',
    # Accounting
    'Account',
    'Allocation',
    # Activity
    'CompJob',
    'CompActivity',
    # Geography
    'Country',
]
