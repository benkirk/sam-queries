from sam.accounting.accounts import *
from sam.accounting.adjustments import *
from sam.accounting.allocations import *
from sam.activity.archive import *
from sam.activity.computational import *
from sam.activity.dataset import *
from sam.activity.dav import *
from sam.activity.disk import *
from sam.activity.hpc import *
from sam.core.groups import *
from sam.core.organizations import *
from sam.core.users import *
from sam.integration.xras import *
from sam.projects.areas import *
from sam.projects.contracts import *
from sam.projects.projects import *
from sam.resources.facilities import *
from sam.resources.machines import *
from sam.resources.resources import *
from sam.security.access import *
from sam.security.roles import *
from sam.summaries.archive_summaries import *
from sam.summaries.comp_summaries import *
from sam.summaries.dav_summaries import *
from sam.summaries.disk_summaries import *
from sam.summaries.hpc_summaries import *

from .default_model_views import *


def add_default_views(app,admin):
    """Add to Flask-Admin default model views"""

    Session = app.Session

    admin.add_view(AcademicStatusDefaultAdmin(AcademicStatus, Session(), name='AcademicStatus', endpoint='everything/academic_status', category='Everything'))
    admin.add_view(AccessBranchDefaultAdmin(AccessBranch, Session(), name='AccessBranch', endpoint='everything/access_branch', category='Everything'))
    admin.add_view(AccessBranchResourceDefaultAdmin(AccessBranchResource, Session(), name='AccessBranchResource', endpoint='everything/access_branch_resource', category='Everything'))
    admin.add_view(AccountDefaultAdmin(Account, Session(), name='Account', endpoint='everything/account', category='Everything'))
    admin.add_view(AccountUserDefaultAdmin(AccountUser, Session(), name='AccountUser', endpoint='everything/account_user', category='Everything'))
    admin.add_view(AdhocGroupDefaultAdmin(AdhocGroup, Session(), name='AdhocGroup', endpoint='everything/adhoc_group', category='Everything'))
    admin.add_view(AdhocGroupTagDefaultAdmin(AdhocGroupTag, Session(), name='AdhocGroupTag', endpoint='everything/adhoc_group_tag', category='Everything'))
    admin.add_view(AdhocSystemAccountEntryDefaultAdmin(AdhocSystemAccountEntry, Session(), name='AdhocSystemAccountEntry', endpoint='everything/adhoc_system_account_entry', category='Everything'))
    admin.add_view(AllocationDefaultAdmin(Allocation, Session(), name='Allocation', endpoint='everything/allocation', category='Everything'))
    admin.add_view(AllocationTransactionDefaultAdmin(AllocationTransaction, Session(), name='AllocationTransaction', endpoint='everything/allocation_transaction', category='Everything'))
    admin.add_view(AllocationTypeDefaultAdmin(AllocationType, Session(), name='AllocationType', endpoint='everything/allocation_type', category='Everything'))
    admin.add_view(ArchiveActivityDefaultAdmin(ArchiveActivity, Session(), name='ArchiveActivity', endpoint='everything/archive_activity', category='Everything'))
    admin.add_view(ArchiveChargeDefaultAdmin(ArchiveCharge, Session(), name='ArchiveCharge', endpoint='everything/archive_charge', category='Everything'))
    admin.add_view(ArchiveChargeSummaryDefaultAdmin(ArchiveChargeSummary, Session(), name='ArchiveChargeSummary', endpoint='everything/archive_charge_summary', category='Everything'))
    admin.add_view(ArchiveChargeSummaryStatusDefaultAdmin(ArchiveChargeSummaryStatus, Session(), name='ArchiveChargeSummaryStatus', endpoint='everything/archive_charge_summary_status', category='Everything'))
    admin.add_view(ArchiveCosDefaultAdmin(ArchiveCos, Session(), name='ArchiveCos', endpoint='everything/archive_cos', category='Everything'))
    admin.add_view(AreaOfInterestDefaultAdmin(AreaOfInterest, Session(), name='AreaOfInterest', endpoint='everything/area_of_interest', category='Everything'))
    admin.add_view(AreaOfInterestGroupDefaultAdmin(AreaOfInterestGroup, Session(), name='AreaOfInterestGroup', endpoint='everything/area_of_interest_group', category='Everything'))
    admin.add_view(ChargeAdjustmentDefaultAdmin(ChargeAdjustment, Session(), name='ChargeAdjustment', endpoint='everything/charge_adjustment', category='Everything'))
    admin.add_view(ChargeAdjustmentTypeDefaultAdmin(ChargeAdjustmentType, Session(), name='ChargeAdjustmentType', endpoint='everything/charge_adjustment_type', category='Everything'))
    admin.add_view(CompActivityDefaultAdmin(CompActivity, Session(), name='CompActivity', endpoint='everything/comp_activity', category='Everything'))
    admin.add_view(CompActivityChargeDefaultAdmin(CompActivityCharge, Session(), name='CompActivityCharge', endpoint='everything/comp_activity_charge', category='Everything'))
    admin.add_view(CompChargeSummaryDefaultAdmin(CompChargeSummary, Session(), name='CompChargeSummary', endpoint='everything/comp_charge_summary', category='Everything'))
    admin.add_view(CompChargeSummaryStatusDefaultAdmin(CompChargeSummaryStatus, Session(), name='CompChargeSummaryStatus', endpoint='everything/comp_charge_summary_status', category='Everything'))
    admin.add_view(CompJobDefaultAdmin(CompJob, Session(), name='CompJob', endpoint='everything/comp_job', category='Everything'))
    admin.add_view(ContractDefaultAdmin(Contract, Session(), name='Contract', endpoint='everything/contract', category='Everything'))
    admin.add_view(ContractSourceDefaultAdmin(ContractSource, Session(), name='ContractSource', endpoint='everything/contract_source', category='Everything'))
    admin.add_view(DatasetActivityDefaultAdmin(DatasetActivity, Session(), name='DatasetActivity', endpoint='everything/dataset_activity', category='Everything'))
    admin.add_view(DavActivityDefaultAdmin(DavActivity, Session(), name='DavActivity', endpoint='everything/dav_activity', category='Everything'))
    admin.add_view(DavChargeDefaultAdmin(DavCharge, Session(), name='DavCharge', endpoint='everything/dav_charge', category='Everything'))
    admin.add_view(DavChargeSummaryDefaultAdmin(DavChargeSummary, Session(), name='DavChargeSummary', endpoint='everything/dav_charge_summary', category='Everything'))
    admin.add_view(DavChargeSummaryStatusDefaultAdmin(DavChargeSummaryStatus, Session(), name='DavChargeSummaryStatus', endpoint='everything/dav_charge_summary_status', category='Everything'))
    admin.add_view(DavCosDefaultAdmin(DavCos, Session(), name='DavCos', endpoint='everything/dav_cos', category='Everything'))
    admin.add_view(DefaultProjectDefaultAdmin(DefaultProject, Session(), name='DefaultProject', endpoint='everything/default_project', category='Everything'))
    admin.add_view(DiskActivityDefaultAdmin(DiskActivity, Session(), name='DiskActivity', endpoint='everything/disk_activity', category='Everything'))
    admin.add_view(DiskChargeDefaultAdmin(DiskCharge, Session(), name='DiskCharge', endpoint='everything/disk_charge', category='Everything'))
    admin.add_view(DiskChargeSummaryDefaultAdmin(DiskChargeSummary, Session(), name='DiskChargeSummary', endpoint='everything/disk_charge_summary', category='Everything'))
    admin.add_view(DiskChargeSummaryStatusDefaultAdmin(DiskChargeSummaryStatus, Session(), name='DiskChargeSummaryStatus', endpoint='everything/disk_charge_summary_status', category='Everything'))
    admin.add_view(DiskCosDefaultAdmin(DiskCos, Session(), name='DiskCos', endpoint='everything/disk_cos', category='Everything'))
    admin.add_view(DiskResourceRootDirectoryDefaultAdmin(DiskResourceRootDirectory, Session(), name='DiskResourceRootDirectory', endpoint='everything/disk_resource_root_directory', category='Everything'))
    admin.add_view(EmailAddressDefaultAdmin(EmailAddress, Session(), name='EmailAddress', endpoint='everything/email_address', category='Everything'))
    admin.add_view(FacilityDefaultAdmin(Facility, Session(), name='Facility', endpoint='everything/facility', category='Everything'))
    admin.add_view(FacilityResourceDefaultAdmin(FacilityResource, Session(), name='FacilityResource', endpoint='everything/facility_resource', category='Everything'))
    admin.add_view(HPCActivityDefaultAdmin(HPCActivity, Session(), name='HPCActivity', endpoint='everything/hpc_activity', category='Everything'))
    admin.add_view(HPCChargeDefaultAdmin(HPCCharge, Session(), name='HPCCharge', endpoint='everything/hpc_charge', category='Everything'))
    admin.add_view(HPCChargeSummaryDefaultAdmin(HPCChargeSummary, Session(), name='HPCChargeSummary', endpoint='everything/hpc_charge_summary', category='Everything'))
    admin.add_view(HPCChargeSummaryStatusDefaultAdmin(HPCChargeSummaryStatus, Session(), name='HPCChargeSummaryStatus', endpoint='everything/hpc_charge_summary_status', category='Everything'))
    admin.add_view(HPCCosDefaultAdmin(HPCCos, Session(), name='HPCCos', endpoint='everything/hpc_cos', category='Everything'))
    admin.add_view(InstitutionDefaultAdmin(Institution, Session(), name='Institution', endpoint='everything/institution', category='Everything'))
    admin.add_view(InstitutionTypeDefaultAdmin(InstitutionType, Session(), name='InstitutionType', endpoint='everything/institution_type', category='Everything'))
    admin.add_view(LoginTypeDefaultAdmin(LoginType, Session(), name='LoginType', endpoint='everything/login_type', category='Everything'))
    admin.add_view(MachineDefaultAdmin(Machine, Session(), name='Machine', endpoint='everything/machine', category='Everything'))
    admin.add_view(MachineFactorDefaultAdmin(MachineFactor, Session(), name='MachineFactor', endpoint='everything/machine_factor', category='Everything'))
    admin.add_view(MnemonicCodeDefaultAdmin(MnemonicCode, Session(), name='MnemonicCode', endpoint='everything/mnemonic_code', category='Everything'))
    admin.add_view(NSFProgramDefaultAdmin(NSFProgram, Session(), name='NSFProgram', endpoint='everything/nsf_program', category='Everything'))
    admin.add_view(OrganizationDefaultAdmin(Organization, Session(), name='Organization', endpoint='everything/organization', category='Everything'))
    admin.add_view(PanelDefaultAdmin(Panel, Session(), name='Panel', endpoint='everything/panel', category='Everything'))
    admin.add_view(PanelSessionDefaultAdmin(PanelSession, Session(), name='PanelSession', endpoint='everything/panel_session', category='Everything'))
    admin.add_view(PhoneDefaultAdmin(Phone, Session(), name='Phone', endpoint='everything/phone', category='Everything'))
    admin.add_view(PhoneTypeDefaultAdmin(PhoneType, Session(), name='PhoneType', endpoint='everything/phone_type', category='Everything'))
    admin.add_view(ProjectDefaultAdmin(Project, Session(), name='Project', endpoint='everything/project', category='Everything'))
    admin.add_view(ProjectContractDefaultAdmin(ProjectContract, Session(), name='ProjectContract', endpoint='everything/project_contract', category='Everything'))
    admin.add_view(ProjectDirectoryDefaultAdmin(ProjectDirectory, Session(), name='ProjectDirectory', endpoint='everything/project_directory', category='Everything'))
    admin.add_view(ProjectNumberDefaultAdmin(ProjectNumber, Session(), name='ProjectNumber', endpoint='everything/project_number', category='Everything'))
    admin.add_view(ProjectOrganizationDefaultAdmin(ProjectOrganization, Session(), name='ProjectOrganization', endpoint='everything/project_organization', category='Everything'))
    admin.add_view(QueueDefaultAdmin(Queue, Session(), name='Queue', endpoint='everything/queue', category='Everything'))
    admin.add_view(QueueFactorDefaultAdmin(QueueFactor, Session(), name='QueueFactor', endpoint='everything/queue_factor', category='Everything'))
    admin.add_view(ResourceDefaultAdmin(Resource, Session(), name='Resource', endpoint='everything/resource', category='Everything'))
    admin.add_view(ResourceShellDefaultAdmin(ResourceShell, Session(), name='ResourceShell', endpoint='everything/resource_shell', category='Everything'))
    admin.add_view(ResourceTypeDefaultAdmin(ResourceType, Session(), name='ResourceType', endpoint='everything/resource_type', category='Everything'))
    admin.add_view(RoleDefaultAdmin(Role, Session(), name='Role', endpoint='everything/role', category='Everything'))
    admin.add_view(RoleUserDefaultAdmin(RoleUser, Session(), name='RoleUser', endpoint='everything/role_user', category='Everything'))
    admin.add_view(UserDefaultAdmin(User, Session(), name='User', endpoint='everything/user', category='Everything'))
    admin.add_view(UserAliasDefaultAdmin(UserAlias, Session(), name='UserAlias', endpoint='everything/user_alias', category='Everything'))
    admin.add_view(UserInstitutionDefaultAdmin(UserInstitution, Session(), name='UserInstitution', endpoint='everything/user_institution', category='Everything'))
    admin.add_view(UserOrganizationDefaultAdmin(UserOrganization, Session(), name='UserOrganization', endpoint='everything/user_organization', category='Everything'))
    admin.add_view(UserResourceHomeDefaultAdmin(UserResourceHome, Session(), name='UserResourceHome', endpoint='everything/user_resource_home', category='Everything'))
    admin.add_view(UserResourceShellDefaultAdmin(UserResourceShell, Session(), name='UserResourceShell', endpoint='everything/user_resource_shell', category='Everything'))
    #admin.add_view(XrasActionDefaultAdmin(XrasAction, Session(), name='XrasAction', endpoint='everything/xras_action', category='Everything'))
    #admin.add_view(XrasAllocationDefaultAdmin(XrasAllocation, Session(), name='XrasAllocation', endpoint='everything/xras_allocation', category='Everything'))
    #admin.add_view(XrasHpcAllocationAmountDefaultAdmin(XrasHpcAllocationAmount, Session(), name='XrasHpcAllocationAmount', endpoint='everything/xras_hpc_allocation_amount', category='Everything'))
    #admin.add_view(XrasRequestDefaultAdmin(XrasRequest, Session(), name='XrasRequest', endpoint='everything/xras_request', category='Everything'))
    #admin.add_view(XrasResourceRepositoryKeyResourceDefaultAdmin(XrasResourceRepositoryKeyResource, Session(), name='XrasResourceRepositoryKeyResource', endpoint='everything/xras_resource_repository_key_resource', category='Everything'))
    #admin.add_view(XrasRoleDefaultAdmin(XrasRole, Session(), name='XrasRole', endpoint='everything/xras_role', category='Everything'))
    #admin.add_view(XrasUserDefaultAdmin(XrasUser, Session(), name='XrasUser', endpoint='everything/xras_user', category='Everything'))
