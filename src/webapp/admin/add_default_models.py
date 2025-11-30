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
from sam.resources.charging import *
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
    from webapp.extensions import db

    admin.add_view(AcademicStatusDefaultAdmin(AcademicStatus, db.session, name='AcademicStatus', endpoint='default_views/academic_status', category='Everything'))
    admin.add_view(AccessBranchDefaultAdmin(AccessBranch, db.session, name='AccessBranch', endpoint='default_views/access_branch', category='Everything'))
    admin.add_view(AccessBranchResourceDefaultAdmin(AccessBranchResource, db.session, name='AccessBranchResource', endpoint='default_views/access_branch_resource', category='Everything'))
    admin.add_view(AccountDefaultAdmin(Account, db.session, name='Account', endpoint='default_views/account', category='Everything'))
    admin.add_view(AccountUserDefaultAdmin(AccountUser, db.session, name='AccountUser', endpoint='default_views/account_user', category='Everything'))
    admin.add_view(AdhocGroupDefaultAdmin(AdhocGroup, db.session, name='AdhocGroup', endpoint='default_views/adhoc_group', category='Everything'))
    admin.add_view(AdhocGroupTagDefaultAdmin(AdhocGroupTag, db.session, name='AdhocGroupTag', endpoint='default_views/adhoc_group_tag', category='Everything'))
    admin.add_view(AdhocSystemAccountEntryDefaultAdmin(AdhocSystemAccountEntry, db.session, name='AdhocSystemAccountEntry', endpoint='default_views/adhoc_system_account_entry', category='Everything'))
    admin.add_view(AllocationDefaultAdmin(Allocation, db.session, name='Allocation', endpoint='default_views/allocation', category='Everything'))
    admin.add_view(AllocationTransactionDefaultAdmin(AllocationTransaction, db.session, name='AllocationTransaction', endpoint='default_views/allocation_transaction', category='Everything'))
    admin.add_view(AllocationTypeDefaultAdmin(AllocationType, db.session, name='AllocationType', endpoint='default_views/allocation_type', category='Everything'))
    admin.add_view(ApiCredentialsDefaultAdmin(ApiCredentials, db.session, name='ApiCredentials', endpoint='default_views/api_credentials', category='Everything'))
    admin.add_view(ArchiveActivityDefaultAdmin(ArchiveActivity, db.session, name='ArchiveActivity', endpoint='default_views/archive_activity', category='Everything'))
    admin.add_view(ArchiveChargeDefaultAdmin(ArchiveCharge, db.session, name='ArchiveCharge', endpoint='default_views/archive_charge', category='Everything'))
    admin.add_view(ArchiveChargeSummaryDefaultAdmin(ArchiveChargeSummary, db.session, name='ArchiveChargeSummary', endpoint='default_views/archive_charge_summary', category='Everything'))
    admin.add_view(ArchiveChargeSummaryStatusDefaultAdmin(ArchiveChargeSummaryStatus, db.session, name='ArchiveChargeSummaryStatus', endpoint='default_views/archive_charge_summary_status', category='Everything'))
    admin.add_view(ArchiveCosDefaultAdmin(ArchiveCos, db.session, name='ArchiveCos', endpoint='default_views/archive_cos', category='Everything'))
    admin.add_view(AreaOfInterestDefaultAdmin(AreaOfInterest, db.session, name='AreaOfInterest', endpoint='default_views/area_of_interest', category='Everything'))
    admin.add_view(AreaOfInterestGroupDefaultAdmin(AreaOfInterestGroup, db.session, name='AreaOfInterestGroup', endpoint='default_views/area_of_interest_group', category='Everything'))
    admin.add_view(ChargeAdjustmentDefaultAdmin(ChargeAdjustment, db.session, name='ChargeAdjustment', endpoint='default_views/charge_adjustment', category='Everything'))
    admin.add_view(ChargeAdjustmentTypeDefaultAdmin(ChargeAdjustmentType, db.session, name='ChargeAdjustmentType', endpoint='default_views/charge_adjustment_type', category='Everything'))
    admin.add_view(CompActivityDefaultAdmin(CompActivity, db.session, name='CompActivity', endpoint='default_views/comp_activity', category='Everything'))
    admin.add_view(CompActivityChargeViewDefaultAdmin(CompActivityChargeView, db.session, name='CompActivityChargeView', endpoint='default_views/comp_activity_charge_view', category='Everything'))
    admin.add_view(CompChargeSummaryDefaultAdmin(CompChargeSummary, db.session, name='CompChargeSummary', endpoint='default_views/comp_charge_summary', category='Everything'))
    admin.add_view(CompChargeSummaryStatusDefaultAdmin(CompChargeSummaryStatus, db.session, name='CompChargeSummaryStatus', endpoint='default_views/comp_charge_summary_status', category='Everything'))
    admin.add_view(CompJobDefaultAdmin(CompJob, db.session, name='CompJob', endpoint='default_views/comp_job', category='Everything'))
    admin.add_view(ContractDefaultAdmin(Contract, db.session, name='Contract', endpoint='default_views/contract', category='Everything'))
    admin.add_view(ContractSourceDefaultAdmin(ContractSource, db.session, name='ContractSource', endpoint='default_views/contract_source', category='Everything'))
    admin.add_view(DatasetActivityDefaultAdmin(DatasetActivity, db.session, name='DatasetActivity', endpoint='default_views/dataset_activity', category='Everything'))
    admin.add_view(DavActivityDefaultAdmin(DavActivity, db.session, name='DavActivity', endpoint='default_views/dav_activity', category='Everything'))
    admin.add_view(DavChargeDefaultAdmin(DavCharge, db.session, name='DavCharge', endpoint='default_views/dav_charge', category='Everything'))
    admin.add_view(DavChargeSummaryDefaultAdmin(DavChargeSummary, db.session, name='DavChargeSummary', endpoint='default_views/dav_charge_summary', category='Everything'))
    admin.add_view(DavChargeSummaryStatusDefaultAdmin(DavChargeSummaryStatus, db.session, name='DavChargeSummaryStatus', endpoint='default_views/dav_charge_summary_status', category='Everything'))
    admin.add_view(DavCosDefaultAdmin(DavCos, db.session, name='DavCos', endpoint='default_views/dav_cos', category='Everything'))
    admin.add_view(DefaultProjectDefaultAdmin(DefaultProject, db.session, name='DefaultProject', endpoint='default_views/default_project', category='Everything'))
    admin.add_view(DiskActivityDefaultAdmin(DiskActivity, db.session, name='DiskActivity', endpoint='default_views/disk_activity', category='Everything'))
    admin.add_view(DiskChargeDefaultAdmin(DiskCharge, db.session, name='DiskCharge', endpoint='default_views/disk_charge', category='Everything'))
    admin.add_view(DiskChargeSummaryDefaultAdmin(DiskChargeSummary, db.session, name='DiskChargeSummary', endpoint='default_views/disk_charge_summary', category='Everything'))
    admin.add_view(DiskChargeSummaryStatusDefaultAdmin(DiskChargeSummaryStatus, db.session, name='DiskChargeSummaryStatus', endpoint='default_views/disk_charge_summary_status', category='Everything'))
    admin.add_view(DiskCosDefaultAdmin(DiskCos, db.session, name='DiskCos', endpoint='default_views/disk_cos', category='Everything'))
    admin.add_view(DiskResourceRootDirectoryDefaultAdmin(DiskResourceRootDirectory, db.session, name='DiskResourceRootDirectory', endpoint='default_views/disk_resource_root_directory', category='Everything'))
    admin.add_view(EmailAddressDefaultAdmin(EmailAddress, db.session, name='EmailAddress', endpoint='default_views/email_address', category='Everything'))
    admin.add_view(FacilityDefaultAdmin(Facility, db.session, name='Facility', endpoint='default_views/facility', category='Everything'))
    admin.add_view(FacilityResourceDefaultAdmin(FacilityResource, db.session, name='FacilityResource', endpoint='default_views/facility_resource', category='Everything'))
    admin.add_view(FactorDefaultAdmin(Factor, db.session, name='Factor', endpoint='default_views/factor', category='Everything'))
    admin.add_view(FormulaDefaultAdmin(Formula, db.session, name='Formula', endpoint='default_views/formula', category='Everything'))
    admin.add_view(FosAoiDefaultAdmin(FosAoi, db.session, name='FosAoi', endpoint='default_views/fos_aoi', category='Everything'))
    admin.add_view(HPCActivityDefaultAdmin(HPCActivity, db.session, name='HPCActivity', endpoint='default_views/hpc_activity', category='Everything'))
    admin.add_view(HPCChargeDefaultAdmin(HPCCharge, db.session, name='HPCCharge', endpoint='default_views/hpc_charge', category='Everything'))
    admin.add_view(HPCChargeSummaryDefaultAdmin(HPCChargeSummary, db.session, name='HPCChargeSummary', endpoint='default_views/hpc_charge_summary', category='Everything'))
    admin.add_view(HPCChargeSummaryStatusDefaultAdmin(HPCChargeSummaryStatus, db.session, name='HPCChargeSummaryStatus', endpoint='default_views/hpc_charge_summary_status', category='Everything'))
    admin.add_view(HPCCosDefaultAdmin(HPCCos, db.session, name='HPCCos', endpoint='default_views/hpc_cos', category='Everything'))
    admin.add_view(InstitutionDefaultAdmin(Institution, db.session, name='Institution', endpoint='default_views/institution', category='Everything'))
    admin.add_view(InstitutionTypeDefaultAdmin(InstitutionType, db.session, name='InstitutionType', endpoint='default_views/institution_type', category='Everything'))
    admin.add_view(LoginTypeDefaultAdmin(LoginType, db.session, name='LoginType', endpoint='default_views/login_type', category='Everything'))
    admin.add_view(MachineDefaultAdmin(Machine, db.session, name='Machine', endpoint='default_views/machine', category='Everything'))
    admin.add_view(MachineFactorDefaultAdmin(MachineFactor, db.session, name='MachineFactor', endpoint='default_views/machine_factor', category='Everything'))
    admin.add_view(MnemonicCodeDefaultAdmin(MnemonicCode, db.session, name='MnemonicCode', endpoint='default_views/mnemonic_code', category='Everything'))
    admin.add_view(NSFProgramDefaultAdmin(NSFProgram, db.session, name='NSFProgram', endpoint='default_views/nsf_program', category='Everything'))
    admin.add_view(OrganizationDefaultAdmin(Organization, db.session, name='Organization', endpoint='default_views/organization', category='Everything'))
    admin.add_view(PanelDefaultAdmin(Panel, db.session, name='Panel', endpoint='default_views/panel', category='Everything'))
    admin.add_view(PanelSessionDefaultAdmin(PanelSession, db.session, name='PanelSession', endpoint='default_views/panel_session', category='Everything'))
    admin.add_view(PhoneDefaultAdmin(Phone, db.session, name='Phone', endpoint='default_views/phone', category='Everything'))
    admin.add_view(PhoneTypeDefaultAdmin(PhoneType, db.session, name='PhoneType', endpoint='default_views/phone_type', category='Everything'))
    admin.add_view(ProjectDefaultAdmin(Project, db.session, name='Project', endpoint='default_views/project', category='Everything'))
    admin.add_view(ProjectCodeDefaultAdmin(ProjectCode, db.session, name='ProjectCode', endpoint='default_views/project_code', category='Everything'))
    admin.add_view(ProjectContractDefaultAdmin(ProjectContract, db.session, name='ProjectContract', endpoint='default_views/project_contract', category='Everything'))
    admin.add_view(ProjectDirectoryDefaultAdmin(ProjectDirectory, db.session, name='ProjectDirectory', endpoint='default_views/project_directory', category='Everything'))
    admin.add_view(ProjectNumberDefaultAdmin(ProjectNumber, db.session, name='ProjectNumber', endpoint='default_views/project_number', category='Everything'))
    admin.add_view(ProjectOrganizationDefaultAdmin(ProjectOrganization, db.session, name='ProjectOrganization', endpoint='default_views/project_organization', category='Everything'))
    admin.add_view(QueueDefaultAdmin(Queue, db.session, name='Queue', endpoint='default_views/queue', category='Everything'))
    admin.add_view(QueueFactorDefaultAdmin(QueueFactor, db.session, name='QueueFactor', endpoint='default_views/queue_factor', category='Everything'))
    admin.add_view(ResourceDefaultAdmin(Resource, db.session, name='Resource', endpoint='default_views/resource', category='Everything'))
    admin.add_view(ResourceShellDefaultAdmin(ResourceShell, db.session, name='ResourceShell', endpoint='default_views/resource_shell', category='Everything'))
    admin.add_view(ResourceTypeDefaultAdmin(ResourceType, db.session, name='ResourceType', endpoint='default_views/resource_type', category='Everything'))
    admin.add_view(ResponsiblePartyDefaultAdmin(ResponsibleParty, db.session, name='ResponsibleParty', endpoint='default_views/responsible_party', category='Everything'))
    admin.add_view(RoleDefaultAdmin(Role, db.session, name='Role', endpoint='default_views/role', category='Everything'))
    admin.add_view(RoleApiCredentialsDefaultAdmin(RoleApiCredentials, db.session, name='RoleApiCredentials', endpoint='default_views/role_api_credentials', category='Everything'))
    admin.add_view(RoleUserDefaultAdmin(RoleUser, db.session, name='RoleUser', endpoint='default_views/role_user', category='Everything'))
    admin.add_view(UserDefaultAdmin(User, db.session, name='User', endpoint='default_views/user', category='Everything'))
    admin.add_view(UserAliasDefaultAdmin(UserAlias, db.session, name='UserAlias', endpoint='default_views/user_alias', category='Everything'))
    admin.add_view(UserInstitutionDefaultAdmin(UserInstitution, db.session, name='UserInstitution', endpoint='default_views/user_institution', category='Everything'))
    admin.add_view(UserOrganizationDefaultAdmin(UserOrganization, db.session, name='UserOrganization', endpoint='default_views/user_organization', category='Everything'))
    admin.add_view(UserResourceHomeDefaultAdmin(UserResourceHome, db.session, name='UserResourceHome', endpoint='default_views/user_resource_home', category='Everything'))
    admin.add_view(UserResourceShellDefaultAdmin(UserResourceShell, db.session, name='UserResourceShell', endpoint='default_views/user_resource_shell', category='Everything'))
    admin.add_view(XrasActionViewDefaultAdmin(XrasActionView, db.session, name='XrasActionView', endpoint='default_views/xras_action_view', category='Everything'))
    admin.add_view(XrasAllocationViewDefaultAdmin(XrasAllocationView, db.session, name='XrasAllocationView', endpoint='default_views/xras_allocation_view', category='Everything'))
    admin.add_view(XrasHpcAllocationAmountViewDefaultAdmin(XrasHpcAllocationAmountView, db.session, name='XrasHpcAllocationAmountView', endpoint='default_views/xras_hpc_allocation_amount_view', category='Everything'))
    admin.add_view(XrasRequestViewDefaultAdmin(XrasRequestView, db.session, name='XrasRequestView', endpoint='default_views/xras_request_view', category='Everything'))
    admin.add_view(XrasResourceRepositoryKeyResourceDefaultAdmin(XrasResourceRepositoryKeyResource, db.session, name='XrasResourceRepositoryKeyResource', endpoint='default_views/xras_resource_repository_key_resource', category='Everything'))
    admin.add_view(XrasRoleViewDefaultAdmin(XrasRoleView, db.session, name='XrasRoleView', endpoint='default_views/xras_role_view', category='Everything'))
    admin.add_view(XrasUserViewDefaultAdmin(XrasUserView, db.session, name='XrasUserView', endpoint='default_views/xras_user_view', category='Everything'))
