from flask_admin.contrib.sqla import ModelView
from flask_login import current_user
from flask import redirect, url_for, request
from webui.utils.rbac import has_permission, Permission


class SAMModelView(ModelView):
    """
    Core base class to extend Flask Admin's ModelView with
    consistent defaults and authentication for all SAM ORM Models.

    All views require login by default. Override is_accessible()
    for role-based access control.
    """

    # Flask Admin exposes edit/delete by default, but suppresses view.
    # Let's turn that on.
    can_view_details = True
    #can_delete = False

    #can_set_page_size = True
    #page_size_options = (10, 25, 50, 100)
    #page_size = 50

    def is_accessible(self):
        """
        Determine if current user can access this view.

        Default: Requires authentication.
        Override this method in subclasses to add role/permission checks.

        Example:
            def is_accessible(self):
                if not current_user.is_authenticated:
                    return False
                from webui.utils.rbac import has_permission, Permission
                return has_permission(current_user, Permission.VIEW_USERS)
        """
        return current_user.is_authenticated

    def inaccessible_callback(self, name, **kwargs):
        """Redirect to login page if not accessible."""
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login', next=request.url))
        # If authenticated but not authorized, show 403
        return redirect(url_for('admin.index'))

    def _check_permission(self, permission_name):
        """Helper to check specific permission."""
        perm = getattr(Permission, permission_name, None)
        return perm and has_permission(current_user, perm)

    def _is_acccessible(self, PERMISSION_TYPE):
        """Check if user has PERMISSION_TYPE permission."""
        if not current_user.is_authenticated:
            return False
        return has_permission(current_user, PERMISSION_TYPE)


#---------------------------------------------------
# Full listing -
# Generated automatically from utils/wrap_classes.py
#---------------------------------------------------

# AcademicStatus Admin View
class AcademicStatusDefaultAdmin(SAMModelView):
    pass

# AccessBranch Admin View
class AccessBranchDefaultAdmin(SAMModelView):
    pass

# AccessBranchResource Admin View
class AccessBranchResourceDefaultAdmin(SAMModelView):
    pass

# Account Admin View
class AccountDefaultAdmin(SAMModelView):
    pass

# AccountUser Admin View
class AccountUserDefaultAdmin(SAMModelView):
    pass

# AdhocGroup Admin View
class AdhocGroupDefaultAdmin(SAMModelView):
    pass

# AdhocGroupTag Admin View
class AdhocGroupTagDefaultAdmin(SAMModelView):
    pass

# AdhocSystemAccountEntry Admin View
class AdhocSystemAccountEntryDefaultAdmin(SAMModelView):
    pass

# Allocation Admin View
class AllocationDefaultAdmin(SAMModelView):
    pass

# AllocationTransaction Admin View
class AllocationTransactionDefaultAdmin(SAMModelView):
    pass

# AllocationType Admin View
class AllocationTypeDefaultAdmin(SAMModelView):
    pass

# ApiCredentials Admin View
class ApiCredentialsDefaultAdmin(SAMModelView):
    pass

# ArchiveActivity Admin View
class ArchiveActivityDefaultAdmin(SAMModelView):
    pass

# ArchiveCharge Admin View
class ArchiveChargeDefaultAdmin(SAMModelView):
    pass

# ArchiveChargeSummary Admin View
class ArchiveChargeSummaryDefaultAdmin(SAMModelView):
    pass

# ArchiveChargeSummaryStatus Admin View
class ArchiveChargeSummaryStatusDefaultAdmin(SAMModelView):
    pass

# ArchiveCos Admin View
class ArchiveCosDefaultAdmin(SAMModelView):
    pass

# AreaOfInterest Admin View
class AreaOfInterestDefaultAdmin(SAMModelView):
    pass

# AreaOfInterestGroup Admin View
class AreaOfInterestGroupDefaultAdmin(SAMModelView):
    pass

# ChargeAdjustment Admin View
class ChargeAdjustmentDefaultAdmin(SAMModelView):
    pass

# ChargeAdjustmentType Admin View
class ChargeAdjustmentTypeDefaultAdmin(SAMModelView):
    pass

# CompActivity Admin View
class CompActivityDefaultAdmin(SAMModelView):
    pass

# CompActivityChargeView Admin View
class CompActivityChargeViewDefaultAdmin(SAMModelView):
    pass

# CompChargeSummary Admin View
class CompChargeSummaryDefaultAdmin(SAMModelView):
    pass

# CompChargeSummaryStatus Admin View
class CompChargeSummaryStatusDefaultAdmin(SAMModelView):
    pass

# CompJob Admin View
class CompJobDefaultAdmin(SAMModelView):
    pass

# Contract Admin View
class ContractDefaultAdmin(SAMModelView):
    pass

# ContractSource Admin View
class ContractSourceDefaultAdmin(SAMModelView):
    pass

# DatasetActivity Admin View
class DatasetActivityDefaultAdmin(SAMModelView):
    pass

# DavActivity Admin View
class DavActivityDefaultAdmin(SAMModelView):
    pass

# DavCharge Admin View
class DavChargeDefaultAdmin(SAMModelView):
    pass

# DavChargeSummary Admin View
class DavChargeSummaryDefaultAdmin(SAMModelView):
    pass

# DavChargeSummaryStatus Admin View
class DavChargeSummaryStatusDefaultAdmin(SAMModelView):
    pass

# DavCos Admin View
class DavCosDefaultAdmin(SAMModelView):
    pass

# DefaultProject Admin View
class DefaultProjectDefaultAdmin(SAMModelView):
    pass

# DiskActivity Admin View
class DiskActivityDefaultAdmin(SAMModelView):
    pass

# DiskCharge Admin View
class DiskChargeDefaultAdmin(SAMModelView):
    pass

# DiskChargeSummary Admin View
class DiskChargeSummaryDefaultAdmin(SAMModelView):
    pass

# DiskChargeSummaryStatus Admin View
class DiskChargeSummaryStatusDefaultAdmin(SAMModelView):
    pass

# DiskCos Admin View
class DiskCosDefaultAdmin(SAMModelView):
    pass

# DiskResourceRootDirectory Admin View
class DiskResourceRootDirectoryDefaultAdmin(SAMModelView):
    pass

# EmailAddress Admin View
class EmailAddressDefaultAdmin(SAMModelView):
    pass

# Facility Admin View
class FacilityDefaultAdmin(SAMModelView):
    pass

# FacilityResource Admin View
class FacilityResourceDefaultAdmin(SAMModelView):
    pass

# Factor Admin View
class FactorDefaultAdmin(SAMModelView):
    pass

# Formula Admin View
class FormulaDefaultAdmin(SAMModelView):
    pass

# FosAoi Admin View
class FosAoiDefaultAdmin(SAMModelView):
    pass

# HPCActivity Admin View
class HPCActivityDefaultAdmin(SAMModelView):
    pass

# HPCCharge Admin View
class HPCChargeDefaultAdmin(SAMModelView):
    pass

# HPCChargeSummary Admin View
class HPCChargeSummaryDefaultAdmin(SAMModelView):
    pass

# HPCChargeSummaryStatus Admin View
class HPCChargeSummaryStatusDefaultAdmin(SAMModelView):
    pass

# HPCCos Admin View
class HPCCosDefaultAdmin(SAMModelView):
    pass

# Institution Admin View
class InstitutionDefaultAdmin(SAMModelView):
    pass

# InstitutionType Admin View
class InstitutionTypeDefaultAdmin(SAMModelView):
    pass

# LoginType Admin View
class LoginTypeDefaultAdmin(SAMModelView):
    pass

# Machine Admin View
class MachineDefaultAdmin(SAMModelView):
    pass

# MachineFactor Admin View
class MachineFactorDefaultAdmin(SAMModelView):
    pass

# MnemonicCode Admin View
class MnemonicCodeDefaultAdmin(SAMModelView):
    pass

# NSFProgram Admin View
class NSFProgramDefaultAdmin(SAMModelView):
    pass

# Organization Admin View
class OrganizationDefaultAdmin(SAMModelView):
    pass

# Panel Admin View
class PanelDefaultAdmin(SAMModelView):
    pass

# PanelSession Admin View
class PanelSessionDefaultAdmin(SAMModelView):
    pass

# Phone Admin View
class PhoneDefaultAdmin(SAMModelView):
    pass

# PhoneType Admin View
class PhoneTypeDefaultAdmin(SAMModelView):
    pass

# Project Admin View
class ProjectDefaultAdmin(SAMModelView):
    pass

# ProjectCode Admin View
class ProjectCodeDefaultAdmin(SAMModelView):
    pass

# ProjectContract Admin View
class ProjectContractDefaultAdmin(SAMModelView):
    pass

# ProjectDirectory Admin View
class ProjectDirectoryDefaultAdmin(SAMModelView):
    pass

# ProjectNumber Admin View
class ProjectNumberDefaultAdmin(SAMModelView):
    pass

# ProjectOrganization Admin View
class ProjectOrganizationDefaultAdmin(SAMModelView):
    pass

# Queue Admin View
class QueueDefaultAdmin(SAMModelView):
    pass

# QueueFactor Admin View
class QueueFactorDefaultAdmin(SAMModelView):
    pass

# Resource Admin View
class ResourceDefaultAdmin(SAMModelView):
    pass

# ResourceShell Admin View
class ResourceShellDefaultAdmin(SAMModelView):
    pass

# ResourceType Admin View
class ResourceTypeDefaultAdmin(SAMModelView):
    pass

# ResponsibleParty Admin View
class ResponsiblePartyDefaultAdmin(SAMModelView):
    pass

# Role Admin View
class RoleDefaultAdmin(SAMModelView):
    pass

# RoleApiCredentials Admin View
class RoleApiCredentialsDefaultAdmin(SAMModelView):
    pass

# RoleUser Admin View
class RoleUserDefaultAdmin(SAMModelView):
    pass

# User Admin View
class UserDefaultAdmin(SAMModelView):
    pass

# UserAlias Admin View
class UserAliasDefaultAdmin(SAMModelView):
    pass

# UserInstitution Admin View
class UserInstitutionDefaultAdmin(SAMModelView):
    pass

# UserOrganization Admin View
class UserOrganizationDefaultAdmin(SAMModelView):
    pass

# UserResourceHome Admin View
class UserResourceHomeDefaultAdmin(SAMModelView):
    pass

# UserResourceShell Admin View
class UserResourceShellDefaultAdmin(SAMModelView):
    pass

# XrasActionView Admin View
class XrasActionViewDefaultAdmin(SAMModelView):
    pass

# XrasAllocationView Admin View
class XrasAllocationViewDefaultAdmin(SAMModelView):
    pass

# XrasHpcAllocationAmountView Admin View
class XrasHpcAllocationAmountViewDefaultAdmin(SAMModelView):
    pass

# XrasRequestView Admin View
class XrasRequestViewDefaultAdmin(SAMModelView):
    pass

# XrasResourceRepositoryKeyResource Admin View
class XrasResourceRepositoryKeyResourceDefaultAdmin(SAMModelView):
    pass

# XrasRoleView Admin View
class XrasRoleViewDefaultAdmin(SAMModelView):
    pass

# XrasUserView Admin View
class XrasUserViewDefaultAdmin(SAMModelView):
    pass
