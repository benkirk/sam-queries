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
from sam.operational import *
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

from .default_model_views import SAMModelView


#: (Model, endpoint_slug) for every model that gets a default Flask-Admin
#: view under the "Everything" menu category. Endpoints are literal — they
#: are URLs; never derive them from a class name.
_DEFAULT_MODELS = [
    (AcademicStatus, 'academic_status'),
    (AccessBranch, 'access_branch'),
    (AccessBranchResource, 'access_branch_resource'),
    (Account, 'account'),
    (AccountUser, 'account_user'),
    (AdhocGroup, 'adhoc_group'),
    (AdhocGroupTag, 'adhoc_group_tag'),
    (AdhocSystemAccountEntry, 'adhoc_system_account_entry'),
    (Allocation, 'allocation'),
    (AllocationTransaction, 'allocation_transaction'),
    (AllocationType, 'allocation_type'),
    (ApiCredentials, 'api_credentials'),
    (ArchiveActivity, 'archive_activity'),
    (ArchiveCharge, 'archive_charge'),
    (ArchiveChargeSummary, 'archive_charge_summary'),
    (ArchiveChargeSummaryStatus, 'archive_charge_summary_status'),
    (ArchiveCos, 'archive_cos'),
    (AreaOfInterest, 'area_of_interest'),
    (AreaOfInterestGroup, 'area_of_interest_group'),
    (ChargeAdjustment, 'charge_adjustment'),
    (ChargeAdjustmentType, 'charge_adjustment_type'),
    (CompActivity, 'comp_activity'),
    (CompActivityChargeView, 'comp_activity_charge_view'),
    (CompChargeSummary, 'comp_charge_summary'),
    (CompChargeSummaryStatus, 'comp_charge_summary_status'),
    (CompJob, 'comp_job'),
    (Contract, 'contract'),
    (ContractSource, 'contract_source'),
    (DatasetActivity, 'dataset_activity'),
    (DavActivity, 'dav_activity'),
    (DavCharge, 'dav_charge'),
    (DavChargeSummary, 'dav_charge_summary'),
    (DavChargeSummaryStatus, 'dav_charge_summary_status'),
    (DavCos, 'dav_cos'),
    (DefaultProject, 'default_project'),
    (DiskActivity, 'disk_activity'),
    (DiskCharge, 'disk_charge'),
    (DiskChargeSummary, 'disk_charge_summary'),
    (DiskChargeSummaryStatus, 'disk_charge_summary_status'),
    (DiskCos, 'disk_cos'),
    (DiskResourceRootDirectory, 'disk_resource_root_directory'),
    (EmailAddress, 'email_address'),
    (Facility, 'facility'),
    (FacilityResource, 'facility_resource'),
    (Factor, 'factor'),
    (Formula, 'formula'),
    (FosAoi, 'fos_aoi'),
    (HPCActivity, 'hpc_activity'),
    (HPCCharge, 'hpc_charge'),
    (HPCChargeSummary, 'hpc_charge_summary'),
    (HPCChargeSummaryStatus, 'hpc_charge_summary_status'),
    (HPCCos, 'hpc_cos'),
    (Institution, 'institution'),
    (InstitutionType, 'institution_type'),
    (LoginType, 'login_type'),
    (Machine, 'machine'),
    (MachineFactor, 'machine_factor'),
    (MnemonicCode, 'mnemonic_code'),
    (NSFProgram, 'nsf_program'),
    (Organization, 'organization'),
    (Panel, 'panel'),
    (PanelSession, 'panel_session'),
    (Phone, 'phone'),
    (PhoneType, 'phone_type'),
    (Project, 'project'),
    (ProjectCode, 'project_code'),
    (ProjectContract, 'project_contract'),
    (ProjectDirectory, 'project_directory'),
    (ProjectNumber, 'project_number'),
    (ProjectOrganization, 'project_organization'),
    (Queue, 'queue'),
    (QueueFactor, 'queue_factor'),
    (Resource, 'resource'),
    (ResourceShell, 'resource_shell'),
    (ResourceType, 'resource_type'),
    (ResponsibleParty, 'responsible_party'),
    (Role, 'role'),
    (RoleApiCredentials, 'role_api_credentials'),
    (RoleUser, 'role_user'),
    (User, 'user'),
    (UserAlias, 'user_alias'),
    (UserInstitution, 'user_institution'),
    (UserOrganization, 'user_organization'),
    (UserResourceHome, 'user_resource_home'),
    (UserResourceShell, 'user_resource_shell'),
    (WallclockExemption, 'wallclock_exemption'),
    (XrasActionView, 'xras_action_view'),
    (XrasAllocationView, 'xras_allocation_view'),
    (XrasHpcAllocationAmountView, 'xras_hpc_allocation_amount_view'),
    (XrasRequestView, 'xras_request_view'),
    (XrasResourceRepositoryKeyResource, 'xras_resource_repository_key_resource'),
    (XrasRoleView, 'xras_role_view'),
    (XrasUserView, 'xras_user_view'),
]

#: Promotion path: map a Model here to a SAMModelView subclass when it
#: needs custom behavior. Everything else is served by SAMModelView
#: directly — Flask-Admin distinguishes views by endpoint, not by class.
_CUSTOM_VIEWS = {}


def add_default_views(app, admin):
    """Add to Flask-Admin default model views"""

    # Import db (Flask-SQLAlchemy instance) to pass to Flask-Admin views.
    from webapp.extensions import db

    for model, endpoint in _DEFAULT_MODELS:
        view_cls = _CUSTOM_VIEWS.get(model, SAMModelView)
        admin.add_view(view_cls(
            model, db,
            name=model.__name__,
            endpoint=f'default_views/{endpoint}',
            category='Everything',
        ))
