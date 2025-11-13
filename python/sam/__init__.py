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

# 6. Accounting
from .accounting.accounts import Account, AccountUser, ResponsibleParty
from .accounting.adjustments import ChargeAdjustment, ChargeAdjustmentType
from .accounting.allocations import Allocation, AllocationTransaction, AllocationType
from .activity.archive import ArchiveActivity, ArchiveCharge, ArchiveCos

# 7. Activity modules
from .activity.computational import CompActivity, CompActivityChargeView, CompJob
from .activity.dataset import DatasetActivity
from .activity.dav import DavActivity, DavCharge, DavCos
from .activity.disk import DiskActivity, DiskCharge, DiskCos
from .activity.hpc import HPCActivity, HPCCharge, HPCCos

# 1. Base classes first
from .base import (
    ActiveFlagMixin,
    Base,
    DateRangeMixin,
    SessionMixin,
    SoftDeleteMixin,
    TimestampMixin,
)
from .core.groups import AdhocGroup, AdhocGroupTag, AdhocSystemAccountEntry
from .core.organizations import (
    Institution,
    InstitutionType,
    MnemonicCode,
    Organization,
    ProjectOrganization,
    UserInstitution,
    UserOrganization,
)

# 3. Core models
from .core.users import (
    AcademicStatus,
    EmailAddress,
    LoginType,
    Phone,
    PhoneType,
    User,
    UserAlias,
    UserResourceHome,
    UserResourceShell,
)

# 2. Simple lookup tables
from .geography import Country, StateProv

# 9. Integration and security
# XRAS table model (actual table, not a view)
from .integration.xras import XrasResourceRepositoryKeyResource

# XRAS view models (read-only database views)
from .integration.xras_views import (
    XrasActionView,
    XrasAllocationView,
    XrasHpcAllocationAmountView,
    XrasRequestView,
    XrasRoleView,
    XrasUserView,
)

# 10. Operational
from .operational import ManualTask, Product, Synchronizer, WallclockExemption
from .projects.areas import AreaOfInterest, AreaOfInterestGroup, FosAoi
from .projects.contracts import Contract, ContractSource, NSFProgram, ProjectContract

# 5. Projects
from .projects.projects import DefaultProject, Project, ProjectDirectory, ProjectNumber
from .resources.charging import Factor, Formula
from .resources.facilities import (
    Facility,
    FacilityResource,
    Panel,
    PanelSession,
    ProjectCode,
)
from .resources.machines import Machine, MachineFactor, Queue, QueueFactor

# 4. Resources
from .resources.resources import (
    DiskResourceRootDirectory,
    Resource,
    ResourceShell,
    ResourceType,
)
from .security.access import AccessBranch, AccessBranchResource
from .security.roles import ApiCredentials, Role, RoleApiCredentials, RoleUser
from .summaries.archive_summaries import (
    ArchiveChargeSummary,
    ArchiveChargeSummaryStatus,
)

# 8. Summaries
from .summaries.comp_summaries import CompChargeSummary, CompChargeSummaryStatus
from .summaries.dav_summaries import DavChargeSummary, DavChargeSummaryStatus
from .summaries.disk_summaries import DiskChargeSummary, DiskChargeSummaryStatus
from .summaries.hpc_summaries import HPCChargeSummary, HPCChargeSummaryStatus

# Expose commonly used at package level
__all__ = [
    # Base
    "Base",
    # Core
    "User",
    "Organization",
    "Institution",
    # Resources
    "Resource",
    "Machine",
    "Queue",
    "Facility",
    # Projects
    "Project",
    "Contract",
    # Accounting
    "Account",
    "Allocation",
    # Activity
    "CompJob",
    "CompActivity",
    "HPCActivity",
]
