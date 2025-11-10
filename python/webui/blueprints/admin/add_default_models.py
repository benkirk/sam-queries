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

    admin.add_view(AcademicStatusAdmin(AcademicStatus, Session(), name='AcademicStatus', endpoint='everything/academic_status', category='Everything'))
    admin.add_view(AccessBranchAdmin(AccessBranch, Session(), name='AccessBranch', endpoint='everything/access_branch', category='Everything'))
    admin.add_view(AccessBranchResourceAdmin(AccessBranchResource, Session(), name='AccessBranchResource', endpoint='everything/access_branch_resource', category='Everything'))
    admin.add_view(AccountAdmin(Account, Session(), name='Account', endpoint='everything/account', category='Everything'))
    admin.add_view(AccountUserAdmin(AccountUser, Session(), name='AccountUser', endpoint='everything/account_user', category='Everything'))
    admin.add_view(AdhocGroupAdmin(AdhocGroup, Session(), name='AdhocGroup', endpoint='everything/adhoc_group', category='Everything'))
    admin.add_view(AdhocGroupTagAdmin(AdhocGroupTag, Session(), name='AdhocGroupTag', endpoint='everything/adhoc_group_tag', category='Everything'))
    admin.add_view(AdhocSystemAccountEntryAdmin(AdhocSystemAccountEntry, Session(), name='AdhocSystemAccountEntry', endpoint='everything/adhoc_system_account_entry', category='Everything'))
    admin.add_view(AllocationAdmin(Allocation, Session(), name='Allocation', endpoint='everything/allocation', category='Everything'))
    admin.add_view(AllocationTransactionAdmin(AllocationTransaction, Session(), name='AllocationTransaction', endpoint='everything/allocation_transaction', category='Everything'))
    admin.add_view(AllocationTypeAdmin(AllocationType, Session(), name='AllocationType', endpoint='everything/allocation_type', category='Everything'))
    admin.add_view(ArchiveActivityAdmin(ArchiveActivity, Session(), name='ArchiveActivity', endpoint='everything/archive_activity', category='Everything'))
    admin.add_view(ArchiveChargeAdmin(ArchiveCharge, Session(), name='ArchiveCharge', endpoint='everything/archive_charge', category='Everything'))
    admin.add_view(ArchiveChargeSummaryAdmin(ArchiveChargeSummary, Session(), name='ArchiveChargeSummary', endpoint='everything/archive_charge_summary', category='Everything'))
    admin.add_view(ArchiveChargeSummaryStatusAdmin(ArchiveChargeSummaryStatus, Session(), name='ArchiveChargeSummaryStatus', endpoint='everything/archive_charge_summary_status', category='Everything'))
    admin.add_view(ArchiveCosAdmin(ArchiveCos, Session(), name='ArchiveCos', endpoint='everything/archive_cos', category='Everything'))
    admin.add_view(AreaOfInterestAdmin(AreaOfInterest, Session(), name='AreaOfInterest', endpoint='everything/area_of_interest', category='Everything'))
    admin.add_view(AreaOfInterestGroupAdmin(AreaOfInterestGroup, Session(), name='AreaOfInterestGroup', endpoint='everything/area_of_interest_group', category='Everything'))
    admin.add_view(ChargeAdjustmentAdmin(ChargeAdjustment, Session(), name='ChargeAdjustment', endpoint='everything/charge_adjustment', category='Everything'))
    admin.add_view(ChargeAdjustmentTypeAdmin(ChargeAdjustmentType, Session(), name='ChargeAdjustmentType', endpoint='everything/charge_adjustment_type', category='Everything'))
    admin.add_view(CompActivityAdmin(CompActivity, Session(), name='CompActivity', endpoint='everything/comp_activity', category='Everything'))
    admin.add_view(CompActivityChargeAdmin(CompActivityCharge, Session(), name='CompActivityCharge', endpoint='everything/comp_activity_charge', category='Everything'))
    admin.add_view(CompChargeSummaryAdmin(CompChargeSummary, Session(), name='CompChargeSummary', endpoint='everything/comp_charge_summary', category='Everything'))
    admin.add_view(CompChargeSummaryStatusAdmin(CompChargeSummaryStatus, Session(), name='CompChargeSummaryStatus', endpoint='everything/comp_charge_summary_status', category='Everything'))
    admin.add_view(CompJobAdmin(CompJob, Session(), name='CompJob', endpoint='everything/comp_job', category='Everything'))
    admin.add_view(ContractAdmin(Contract, Session(), name='Contract', endpoint='everything/contract', category='Everything'))
    admin.add_view(ContractSourceAdmin(ContractSource, Session(), name='ContractSource', endpoint='everything/contract_source', category='Everything'))
    admin.add_view(DatasetActivityAdmin(DatasetActivity, Session(), name='DatasetActivity', endpoint='everything/dataset_activity', category='Everything'))
    admin.add_view(DavActivityAdmin(DavActivity, Session(), name='DavActivity', endpoint='everything/dav_activity', category='Everything'))
    admin.add_view(DavChargeAdmin(DavCharge, Session(), name='DavCharge', endpoint='everything/dav_charge', category='Everything'))
    admin.add_view(DavChargeSummaryAdmin(DavChargeSummary, Session(), name='DavChargeSummary', endpoint='everything/dav_charge_summary', category='Everything'))
    admin.add_view(DavChargeSummaryStatusAdmin(DavChargeSummaryStatus, Session(), name='DavChargeSummaryStatus', endpoint='everything/dav_charge_summary_status', category='Everything'))
    admin.add_view(DavCosAdmin(DavCos, Session(), name='DavCos', endpoint='everything/dav_cos', category='Everything'))
    admin.add_view(DefaultProjectAdmin(DefaultProject, Session(), name='DefaultProject', endpoint='everything/default_project', category='Everything'))
    admin.add_view(DiskActivityAdmin(DiskActivity, Session(), name='DiskActivity', endpoint='everything/disk_activity', category='Everything'))
    admin.add_view(DiskChargeAdmin(DiskCharge, Session(), name='DiskCharge', endpoint='everything/disk_charge', category='Everything'))
    admin.add_view(DiskChargeSummaryAdmin(DiskChargeSummary, Session(), name='DiskChargeSummary', endpoint='everything/disk_charge_summary', category='Everything'))
    admin.add_view(DiskChargeSummaryStatusAdmin(DiskChargeSummaryStatus, Session(), name='DiskChargeSummaryStatus', endpoint='everything/disk_charge_summary_status', category='Everything'))
    admin.add_view(DiskCosAdmin(DiskCos, Session(), name='DiskCos', endpoint='everything/disk_cos', category='Everything'))
    admin.add_view(DiskResourceRootDirectoryAdmin(DiskResourceRootDirectory, Session(), name='DiskResourceRootDirectory', endpoint='everything/disk_resource_root_directory', category='Everything'))
    admin.add_view(EmailAddressAdmin(EmailAddress, Session(), name='EmailAddress', endpoint='everything/email_address', category='Everything'))
    admin.add_view(FacilityAdmin(Facility, Session(), name='Facility', endpoint='everything/facility', category='Everything'))
    admin.add_view(FacilityResourceAdmin(FacilityResource, Session(), name='FacilityResource', endpoint='everything/facility_resource', category='Everything'))
    admin.add_view(HPCActivityAdmin(HPCActivity, Session(), name='HPCActivity', endpoint='everything/hpc_activity', category='Everything'))
    admin.add_view(HPCChargeAdmin(HPCCharge, Session(), name='HPCCharge', endpoint='everything/hpc_charge', category='Everything'))
    admin.add_view(HPCChargeSummaryAdmin(HPCChargeSummary, Session(), name='HPCChargeSummary', endpoint='everything/hpc_charge_summary', category='Everything'))
    admin.add_view(HPCChargeSummaryStatusAdmin(HPCChargeSummaryStatus, Session(), name='HPCChargeSummaryStatus', endpoint='everything/hpc_charge_summary_status', category='Everything'))
    admin.add_view(HPCCosAdmin(HPCCos, Session(), name='HPCCos', endpoint='everything/hpc_cos', category='Everything'))
    admin.add_view(InstitutionAdmin(Institution, Session(), name='Institution', endpoint='everything/institution', category='Everything'))
    admin.add_view(InstitutionTypeAdmin(InstitutionType, Session(), name='InstitutionType', endpoint='everything/institution_type', category='Everything'))
    admin.add_view(LoginTypeAdmin(LoginType, Session(), name='LoginType', endpoint='everything/login_type', category='Everything'))
    admin.add_view(MachineAdmin(Machine, Session(), name='Machine', endpoint='everything/machine', category='Everything'))
    admin.add_view(MachineFactorAdmin(MachineFactor, Session(), name='MachineFactor', endpoint='everything/machine_factor', category='Everything'))
    admin.add_view(MnemonicCodeAdmin(MnemonicCode, Session(), name='MnemonicCode', endpoint='everything/mnemonic_code', category='Everything'))
    admin.add_view(NSFProgramAdmin(NSFProgram, Session(), name='NSFProgram', endpoint='everything/nsf_program', category='Everything'))
    admin.add_view(OrganizationAdmin(Organization, Session(), name='Organization', endpoint='everything/organization', category='Everything'))
    admin.add_view(PanelAdmin(Panel, Session(), name='Panel', endpoint='everything/panel', category='Everything'))
    admin.add_view(PanelSessionAdmin(PanelSession, Session(), name='PanelSession', endpoint='everything/panel_session', category='Everything'))
    admin.add_view(PhoneAdmin(Phone, Session(), name='Phone', endpoint='everything/phone', category='Everything'))
    admin.add_view(PhoneTypeAdmin(PhoneType, Session(), name='PhoneType', endpoint='everything/phone_type', category='Everything'))
    admin.add_view(ProjectAdmin(Project, Session(), name='Project', endpoint='everything/project', category='Everything'))
    admin.add_view(ProjectContractAdmin(ProjectContract, Session(), name='ProjectContract', endpoint='everything/project_contract', category='Everything'))
    admin.add_view(ProjectDirectoryAdmin(ProjectDirectory, Session(), name='ProjectDirectory', endpoint='everything/project_directory', category='Everything'))
    admin.add_view(ProjectNumberAdmin(ProjectNumber, Session(), name='ProjectNumber', endpoint='everything/project_number', category='Everything'))
    admin.add_view(ProjectOrganizationAdmin(ProjectOrganization, Session(), name='ProjectOrganization', endpoint='everything/project_organization', category='Everything'))
    admin.add_view(QueueAdmin(Queue, Session(), name='Queue', endpoint='everything/queue', category='Everything'))
    admin.add_view(QueueFactorAdmin(QueueFactor, Session(), name='QueueFactor', endpoint='everything/queue_factor', category='Everything'))
    admin.add_view(ResourceAdmin(Resource, Session(), name='Resource', endpoint='everything/resource', category='Everything'))
    admin.add_view(ResourceShellAdmin(ResourceShell, Session(), name='ResourceShell', endpoint='everything/resource_shell', category='Everything'))
    admin.add_view(ResourceTypeAdmin(ResourceType, Session(), name='ResourceType', endpoint='everything/resource_type', category='Everything'))
    admin.add_view(RoleAdmin(Role, Session(), name='Role', endpoint='everything/role', category='Everything'))
    admin.add_view(RoleUserAdmin(RoleUser, Session(), name='RoleUser', endpoint='everything/role_user', category='Everything'))
    admin.add_view(UserAdmin(User, Session(), name='User', endpoint='everything/user', category='Everything'))
    admin.add_view(UserAliasAdmin(UserAlias, Session(), name='UserAlias', endpoint='everything/user_alias', category='Everything'))
    admin.add_view(UserInstitutionAdmin(UserInstitution, Session(), name='UserInstitution', endpoint='everything/user_institution', category='Everything'))
    admin.add_view(UserOrganizationAdmin(UserOrganization, Session(), name='UserOrganization', endpoint='everything/user_organization', category='Everything'))
    admin.add_view(UserResourceHomeAdmin(UserResourceHome, Session(), name='UserResourceHome', endpoint='everything/user_resource_home', category='Everything'))
    admin.add_view(UserResourceShellAdmin(UserResourceShell, Session(), name='UserResourceShell', endpoint='everything/user_resource_shell', category='Everything'))
    #admin.add_view(XrasActionAdmin(XrasAction, Session(), name='XrasAction', endpoint='everything/xras_action', category='Everything'))
    #admin.add_view(XrasAllocationAdmin(XrasAllocation, Session(), name='XrasAllocation', endpoint='everything/xras_allocation', category='Everything'))
    #admin.add_view(XrasHpcAllocationAmountAdmin(XrasHpcAllocationAmount, Session(), name='XrasHpcAllocationAmount', endpoint='everything/xras_hpc_allocation_amount', category='Everything'))
    #admin.add_view(XrasRequestAdmin(XrasRequest, Session(), name='XrasRequest', endpoint='everything/xras_request', category='Everything'))
    #admin.add_view(XrasResourceRepositoryKeyResourceAdmin(XrasResourceRepositoryKeyResource, Session(), name='XrasResourceRepositoryKeyResource', endpoint='everything/xras_resource_repository_key_resource', category='Everything'))
    #admin.add_view(XrasRoleAdmin(XrasRole, Session(), name='XrasRole', endpoint='everything/xras_role', category='Everything'))
    #admin.add_view(XrasUserAdmin(XrasUser, Session(), name='XrasUser', endpoint='everything/xras_user', category='Everything'))
