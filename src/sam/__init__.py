"""
SAM Models Package

Import order matters! Follow dependency chain:
1. Base classes (no dependencies)
2. Geographic/lookup tables (minimal dependencies)
3. Core models (users, orgs)
4. Resources (depends on core)
5. Projects (depends on core and resources)
6. Accounting (depends on projects and resources)
7. Activity (depends on accounting)
8. Summaries (depends on activity)
"""

# 1. Base classes first
from .base import (
    Base,
    TimestampMixin,
    SoftDeleteMixin,
    ActiveFlagMixin,
    DateRangeMixin,
    SessionMixin
)

# 2. Simple lookup tables
from .geography import Country, StateProv

# 3. Core models
from .core.users import (
    User,
    UserAlias,
    EmailAddress,
    Phone,
    PhoneType,
    UserResourceHome,
    UserResourceShell,
    LoginType,
    AcademicStatus
)

from .core.organizations import (
    Organization,
    UserOrganization,
    Institution,
    InstitutionType,
    UserInstitution,
    MnemonicCode,
    ProjectOrganization
)

from .core.groups import (
    AdhocGroup,
    AdhocGroupTag,
    AdhocSystemAccountEntry
)

# 4. Resources
from .resources.resources import (
    Resource,
    ResourceType,
    ResourceShell,
    DiskResourceRootDirectory
)

from .resources.machines import (
    Machine,
    MachineFactor,
    Queue,
    QueueFactor
)

from .resources.facilities import (
    Facility,
    FacilityResource,
    Panel,
    PanelSession,
    ProjectCode
)

from .resources.charging import (
    Factor,
    Formula
)

# 5. Projects
from .projects.projects import (
    Project,
    ProjectNumber,
    ProjectDirectory,
    DefaultProject
)

from .projects.areas import (
    AreaOfInterest,
    AreaOfInterestGroup,
    FosAoi
)

from .projects.contracts import (
    Contract,
    ContractSource,
    ProjectContract,
    NSFProgram
)

# 6. Accounting
from .accounting.accounts import Account, AccountUser, ResponsibleParty
from .accounting.allocations import (
    Allocation,
    AllocationTransaction,
    AllocationTransactionType,
    AllocationType
)
from .accounting.adjustments import (
    ChargeAdjustment,
    ChargeAdjustmentType
)

# 7. Activity modules
from .activity.computational import (
    CompJob,
    CompActivity,
    CompActivityChargeView
)

from .activity.hpc import (
    HPCActivity,
    HPCCharge,
    HPCCos
)

from .activity.dav import (
    DavActivity,
    DavCharge,
    DavCos
)

from .activity.disk import (
    DiskActivity,
    DiskCharge,
    DiskCos
)

from .activity.archive import (
    ArchiveActivity,
    ArchiveCharge,
    ArchiveCos
)

from .activity.dataset import DatasetActivity

# 8. Summaries
from .summaries.comp_summaries import (
    CompChargeSummary,
    CompChargeSummaryStatus
)

from .summaries.hpc_summaries import (
    HPCChargeSummary,
    HPCChargeSummaryStatus
)

from .summaries.dav_summaries import (
    DavChargeSummary,
    DavChargeSummaryStatus
)

from .summaries.disk_summaries import (
    DiskChargeSummary,
    DiskChargeSummaryStatus
)

from .summaries.archive_summaries import (
    ArchiveChargeSummary,
    ArchiveChargeSummaryStatus
)

# 9. Integration and security
# XRAS table model (actual table, not a view)
from .integration.xras import XrasResourceRepositoryKeyResource

# XRAS view models (read-only database views)
from .integration.xras_views import (
    XrasUserView,
    XrasRoleView,
    XrasActionView,
    XrasAllocationView,
    XrasHpcAllocationAmountView,
    XrasRequestView
)

from .security.roles import Role, RoleUser, ApiCredentials, RoleApiCredentials
from .security.access import AccessBranch, AccessBranchResource

# 10. Operational
from .operational import (
    Synchronizer,
    ManualTask,
    Product,
    WallclockExemption
)

# Domain enums
from .enums import ResourceTypeName, FacilityName, ChargeType

# Expose all imported models at package level
__all__ = [
    # Base
    'Base', 'TimestampMixin', 'SoftDeleteMixin', 'ActiveFlagMixin',
    'DateRangeMixin', 'SessionMixin',
    # Enums
    'ResourceTypeName', 'FacilityName', 'ChargeType',
    # Geography
    'Country', 'StateProv',
    # Core - Users
    'User', 'UserAlias', 'EmailAddress', 'Phone', 'PhoneType',
    'UserResourceHome', 'UserResourceShell', 'LoginType', 'AcademicStatus',
    # Core - Organizations
    'Organization', 'UserOrganization', 'Institution', 'InstitutionType',
    'UserInstitution', 'MnemonicCode', 'ProjectOrganization',
    # Core - Groups
    'AdhocGroup', 'AdhocGroupTag', 'AdhocSystemAccountEntry',
    # Resources
    'Resource', 'ResourceType', 'ResourceShell', 'DiskResourceRootDirectory',
    'Machine', 'MachineFactor', 'Queue', 'QueueFactor',
    'Facility', 'FacilityResource', 'Panel', 'PanelSession', 'ProjectCode',
    'Factor', 'Formula',
    # Projects
    'Project', 'ProjectNumber', 'ProjectDirectory', 'DefaultProject',
    'AreaOfInterest', 'AreaOfInterestGroup', 'FosAoi',
    'Contract', 'ContractSource', 'ProjectContract', 'NSFProgram',
    # Accounting
    'Account', 'AccountUser', 'ResponsibleParty',
    'Allocation', 'AllocationTransaction', 'AllocationTransactionType', 'AllocationType',
    'ChargeAdjustment', 'ChargeAdjustmentType',
    # Activity
    'CompJob', 'CompActivity', 'CompActivityChargeView',
    'HPCActivity', 'HPCCharge', 'HPCCos',
    'DavActivity', 'DavCharge', 'DavCos',
    'DiskActivity', 'DiskCharge', 'DiskCos',
    'ArchiveActivity', 'ArchiveCharge', 'ArchiveCos',
    'DatasetActivity',
    # Summaries
    'CompChargeSummary', 'CompChargeSummaryStatus',
    'HPCChargeSummary', 'HPCChargeSummaryStatus',
    'DavChargeSummary', 'DavChargeSummaryStatus',
    'DiskChargeSummary', 'DiskChargeSummaryStatus',
    'ArchiveChargeSummary', 'ArchiveChargeSummaryStatus',
    # Integration
    'XrasResourceRepositoryKeyResource',
    'XrasUserView', 'XrasRoleView', 'XrasActionView',
    'XrasAllocationView', 'XrasHpcAllocationAmountView', 'XrasRequestView',
    # Security
    'Role', 'RoleUser', 'ApiCredentials', 'RoleApiCredentials',
    'AccessBranch', 'AccessBranchResource',
    # Operational
    'Synchronizer', 'ManualTask', 'Product', 'WallclockExemption',
]
