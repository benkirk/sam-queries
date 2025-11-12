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
from sam.integration.xras_views import *

from .default_model_views import *


def add_default_views(app,admin):
    """Add to Flask-Admin default model views"""

    # Import db to access Flask-SQLAlchemy session
    from webui.extensions import db

    admin.add_view(AcademicStatusDefaultAdmin(AcademicStatus, db.session, name='AcademicStatus', endpoint='everything/academic_status', category='Everything'))
    admin.add_view(AccessBranchDefaultAdmin(AccessBranch, db.session, name='AccessBranch', endpoint='everything/access_branch', category='Everything'))
    admin.add_view(AccessBranchResourceDefaultAdmin(AccessBranchResource, db.session, name='AccessBranchResource', endpoint='everything/access_branch_resource', category='Everything'))
    admin.add_view(AccountDefaultAdmin(Account, db.session, name='Account', endpoint='everything/account', category='Everything'))
    admin.add_view(AccountUserDefaultAdmin(AccountUser, db.session, name='AccountUser', endpoint='everything/account_user', category='Everything'))
    admin.add_view(AdhocGroupDefaultAdmin(AdhocGroup, db.session, name='AdhocGroup', endpoint='everything/adhoc_group', category='Everything'))
    admin.add_view(AdhocGroupTagDefaultAdmin(AdhocGroupTag, db.session, name='AdhocGroupTag', endpoint='everything/adhoc_group_tag', category='Everything'))
    admin.add_view(AdhocSystemAccountEntryDefaultAdmin(AdhocSystemAccountEntry, db.session, name='AdhocSystemAccountEntry', endpoint='everything/adhoc_system_account_entry', category='Everything'))
    admin.add_view(AllocationDefaultAdmin(Allocation, db.session, name='Allocation', endpoint='everything/allocation', category='Everything'))
    admin.add_view(AllocationTransactionDefaultAdmin(AllocationTransaction, db.session, name='AllocationTransaction', endpoint='everything/allocation_transaction', category='Everything'))
    admin.add_view(AllocationTypeDefaultAdmin(AllocationType, db.session, name='AllocationType', endpoint='everything/allocation_type', category='Everything'))
    admin.add_view(ArchiveActivityDefaultAdmin(ArchiveActivity, db.session, name='ArchiveActivity', endpoint='everything/archive_activity', category='Everything'))
    admin.add_view(ArchiveChargeDefaultAdmin(ArchiveCharge, db.session, name='ArchiveCharge', endpoint='everything/archive_charge', category='Everything'))
    admin.add_view(ArchiveChargeSummaryDefaultAdmin(ArchiveChargeSummary, db.session, name='ArchiveChargeSummary', endpoint='everything/archive_charge_summary', category='Everything'))
    admin.add_view(ArchiveChargeSummaryStatusDefaultAdmin(ArchiveChargeSummaryStatus, db.session, name='ArchiveChargeSummaryStatus', endpoint='everything/archive_charge_summary_status', category='Everything'))
    admin.add_view(ArchiveCosDefaultAdmin(ArchiveCos, db.session, name='ArchiveCos', endpoint='everything/archive_cos', category='Everything'))
    admin.add_view(AreaOfInterestDefaultAdmin(AreaOfInterest, db.session, name='AreaOfInterest', endpoint='everything/area_of_interest', category='Everything'))
    admin.add_view(AreaOfInterestGroupDefaultAdmin(AreaOfInterestGroup, db.session, name='AreaOfInterestGroup', endpoint='everything/area_of_interest_group', category='Everything'))
    admin.add_view(ChargeAdjustmentDefaultAdmin(ChargeAdjustment, db.session, name='ChargeAdjustment', endpoint='everything/charge_adjustment', category='Everything'))
    admin.add_view(ChargeAdjustmentTypeDefaultAdmin(ChargeAdjustmentType, db.session, name='ChargeAdjustmentType', endpoint='everything/charge_adjustment_type', category='Everything'))
    admin.add_view(CompActivityDefaultAdmin(CompActivity, db.session, name='CompActivity', endpoint='everything/comp_activity', category='Everything'))
    admin.add_view(CompActivityChargeViewDefaultAdmin(CompActivityChargeView, db.session, name='CompActivityChargeView', endpoint='everything/comp_activity_charge', category='Everything'))
    admin.add_view(CompChargeSummaryDefaultAdmin(CompChargeSummary, db.session, name='CompChargeSummary', endpoint='everything/comp_charge_summary', category='Everything'))
    admin.add_view(CompChargeSummaryStatusDefaultAdmin(CompChargeSummaryStatus, db.session, name='CompChargeSummaryStatus', endpoint='everything/comp_charge_summary_status', category='Everything'))
    admin.add_view(CompJobDefaultAdmin(CompJob, db.session, name='CompJob', endpoint='everything/comp_job', category='Everything'))
    admin.add_view(ContractDefaultAdmin(Contract, db.session, name='Contract', endpoint='everything/contract', category='Everything'))
    admin.add_view(ContractSourceDefaultAdmin(ContractSource, db.session, name='ContractSource', endpoint='everything/contract_source', category='Everything'))
    admin.add_view(DatasetActivityDefaultAdmin(DatasetActivity, db.session, name='DatasetActivity', endpoint='everything/dataset_activity', category='Everything'))
    admin.add_view(DavActivityDefaultAdmin(DavActivity, db.session, name='DavActivity', endpoint='everything/dav_activity', category='Everything'))
    admin.add_view(DavChargeDefaultAdmin(DavCharge, db.session, name='DavCharge', endpoint='everything/dav_charge', category='Everything'))
    admin.add_view(DavChargeSummaryDefaultAdmin(DavChargeSummary, db.session, name='DavChargeSummary', endpoint='everything/dav_charge_summary', category='Everything'))
    admin.add_view(DavChargeSummaryStatusDefaultAdmin(DavChargeSummaryStatus, db.session, name='DavChargeSummaryStatus', endpoint='everything/dav_charge_summary_status', category='Everything'))
    admin.add_view(DavCosDefaultAdmin(DavCos, db.session, name='DavCos', endpoint='everything/dav_cos', category='Everything'))
    admin.add_view(DefaultProjectDefaultAdmin(DefaultProject, db.session, name='DefaultProject', endpoint='everything/default_project', category='Everything'))
    admin.add_view(DiskActivityDefaultAdmin(DiskActivity, db.session, name='DiskActivity', endpoint='everything/disk_activity', category='Everything'))
    admin.add_view(DiskChargeDefaultAdmin(DiskCharge, db.session, name='DiskCharge', endpoint='everything/disk_charge', category='Everything'))
    admin.add_view(DiskChargeSummaryDefaultAdmin(DiskChargeSummary, db.session, name='DiskChargeSummary', endpoint='everything/disk_charge_summary', category='Everything'))
    admin.add_view(DiskChargeSummaryStatusDefaultAdmin(DiskChargeSummaryStatus, db.session, name='DiskChargeSummaryStatus', endpoint='everything/disk_charge_summary_status', category='Everything'))
    admin.add_view(DiskCosDefaultAdmin(DiskCos, db.session, name='DiskCos', endpoint='everything/disk_cos', category='Everything'))
    admin.add_view(DiskResourceRootDirectoryDefaultAdmin(DiskResourceRootDirectory, db.session, name='DiskResourceRootDirectory', endpoint='everything/disk_resource_root_directory', category='Everything'))
    admin.add_view(EmailAddressDefaultAdmin(EmailAddress, db.session, name='EmailAddress', endpoint='everything/email_address', category='Everything'))
    admin.add_view(FacilityDefaultAdmin(Facility, db.session, name='Facility', endpoint='everything/facility', category='Everything'))
    admin.add_view(FacilityResourceDefaultAdmin(FacilityResource, db.session, name='FacilityResource', endpoint='everything/facility_resource', category='Everything'))
    admin.add_view(HPCActivityDefaultAdmin(HPCActivity, db.session, name='HPCActivity', endpoint='everything/hpc_activity', category='Everything'))
    admin.add_view(HPCChargeDefaultAdmin(HPCCharge, db.session, name='HPCCharge', endpoint='everything/hpc_charge', category='Everything'))
    admin.add_view(HPCChargeSummaryDefaultAdmin(HPCChargeSummary, db.session, name='HPCChargeSummary', endpoint='everything/hpc_charge_summary', category='Everything'))
    admin.add_view(HPCChargeSummaryStatusDefaultAdmin(HPCChargeSummaryStatus, db.session, name='HPCChargeSummaryStatus', endpoint='everything/hpc_charge_summary_status', category='Everything'))
    admin.add_view(HPCCosDefaultAdmin(HPCCos, db.session, name='HPCCos', endpoint='everything/hpc_cos', category='Everything'))
    admin.add_view(InstitutionDefaultAdmin(Institution, db.session, name='Institution', endpoint='everything/institution', category='Everything'))
    admin.add_view(InstitutionTypeDefaultAdmin(InstitutionType, db.session, name='InstitutionType', endpoint='everything/institution_type', category='Everything'))
    admin.add_view(LoginTypeDefaultAdmin(LoginType, db.session, name='LoginType', endpoint='everything/login_type', category='Everything'))
    admin.add_view(MachineDefaultAdmin(Machine, db.session, name='Machine', endpoint='everything/machine', category='Everything'))
    admin.add_view(MachineFactorDefaultAdmin(MachineFactor, db.session, name='MachineFactor', endpoint='everything/machine_factor', category='Everything'))
    admin.add_view(MnemonicCodeDefaultAdmin(MnemonicCode, db.session, name='MnemonicCode', endpoint='everything/mnemonic_code', category='Everything'))
    admin.add_view(NSFProgramDefaultAdmin(NSFProgram, db.session, name='NSFProgram', endpoint='everything/nsf_program', category='Everything'))
    admin.add_view(OrganizationDefaultAdmin(Organization, db.session, name='Organization', endpoint='everything/organization', category='Everything'))
    admin.add_view(PanelDefaultAdmin(Panel, db.session, name='Panel', endpoint='everything/panel', category='Everything'))
    admin.add_view(PanelSessionDefaultAdmin(PanelSession, db.session, name='PanelSession', endpoint='everything/panel_session', category='Everything'))
    admin.add_view(PhoneDefaultAdmin(Phone, db.session, name='Phone', endpoint='everything/phone', category='Everything'))
    admin.add_view(PhoneTypeDefaultAdmin(PhoneType, db.session, name='PhoneType', endpoint='everything/phone_type', category='Everything'))
    admin.add_view(ProjectDefaultAdmin(Project, db.session, name='Project', endpoint='everything/project', category='Everything'))
    admin.add_view(ProjectContractDefaultAdmin(ProjectContract, db.session, name='ProjectContract', endpoint='everything/project_contract', category='Everything'))
    admin.add_view(ProjectDirectoryDefaultAdmin(ProjectDirectory, db.session, name='ProjectDirectory', endpoint='everything/project_directory', category='Everything'))
    admin.add_view(ProjectNumberDefaultAdmin(ProjectNumber, db.session, name='ProjectNumber', endpoint='everything/project_number', category='Everything'))
    admin.add_view(ProjectOrganizationDefaultAdmin(ProjectOrganization, db.session, name='ProjectOrganization', endpoint='everything/project_organization', category='Everything'))
    admin.add_view(QueueDefaultAdmin(Queue, db.session, name='Queue', endpoint='everything/queue', category='Everything'))
    admin.add_view(QueueFactorDefaultAdmin(QueueFactor, db.session, name='QueueFactor', endpoint='everything/queue_factor', category='Everything'))
    admin.add_view(ResourceDefaultAdmin(Resource, db.session, name='Resource', endpoint='everything/resource', category='Everything'))
    admin.add_view(ResourceShellDefaultAdmin(ResourceShell, db.session, name='ResourceShell', endpoint='everything/resource_shell', category='Everything'))
    admin.add_view(ResourceTypeDefaultAdmin(ResourceType, db.session, name='ResourceType', endpoint='everything/resource_type', category='Everything'))
    admin.add_view(RoleDefaultAdmin(Role, db.session, name='Role', endpoint='everything/role', category='Everything'))
    admin.add_view(RoleUserDefaultAdmin(RoleUser, db.session, name='RoleUser', endpoint='everything/role_user', category='Everything'))
    admin.add_view(UserDefaultAdmin(User, db.session, name='User', endpoint='everything/user', category='Everything'))
    admin.add_view(UserAliasDefaultAdmin(UserAlias, db.session, name='UserAlias', endpoint='everything/user_alias', category='Everything'))
    admin.add_view(UserInstitutionDefaultAdmin(UserInstitution, db.session, name='UserInstitution', endpoint='everything/user_institution', category='Everything'))
    admin.add_view(UserOrganizationDefaultAdmin(UserOrganization, db.session, name='UserOrganization', endpoint='everything/user_organization', category='Everything'))
    admin.add_view(UserResourceHomeDefaultAdmin(UserResourceHome, db.session, name='UserResourceHome', endpoint='everything/user_resource_home', category='Everything'))
    admin.add_view(UserResourceShellDefaultAdmin(UserResourceShell, db.session, name='UserResourceShell', endpoint='everything/user_resource_shell', category='Everything'))
    #admin.add_view(XrasActionDefaultAdmin(XrasAction, Session(), name='XrasAction', endpoint='everything/xras_action', category='Everything'))
    #admin.add_view(XrasAllocationDefaultAdmin(XrasAllocation, Session(), name='XrasAllocation', endpoint='everything/xras_allocation', category='Everything'))
    #admin.add_view(XrasHpcAllocationAmountDefaultAdmin(XrasHpcAllocationAmount, Session(), name='XrasHpcAllocationAmount', endpoint='everything/xras_hpc_allocation_amount', category='Everything'))
    #admin.add_view(XrasRequestDefaultAdmin(XrasRequest, Session(), name='XrasRequest', endpoint='everything/xras_request', category='Everything'))
    #admin.add_view(XrasResourceRepositoryKeyResourceDefaultAdmin(XrasResourceRepositoryKeyResource, Session(), name='XrasResourceRepositoryKeyResource', endpoint='everything/xras_resource_repository_key_resource', category='Everything'))
    #admin.add_view(XrasRoleDefaultAdmin(XrasRole, Session(), name='XrasRole', endpoint='everything/xras_role', category='Everything'))
    #admin.add_view(XrasUserDefaultAdmin(XrasUser, Session(), name='XrasUser', endpoint='everything/xras_user', category='Everything'))
