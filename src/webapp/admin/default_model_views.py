from flask_admin.contrib.sqla import ModelView
from flask_login import current_user
from flask import redirect, url_for, request
from webapp.utils.rbac import has_permission, Permission
from sqlalchemy import String


class SAMModelView(ModelView):
    """
    Core base class to extend Flask Admin's ModelView with
    consistent defaults and authentication for all SAM ORM Models.

    All views require login by default. Override is_accessible()
    for role-based access control.

    Phase 1 Enhancements (Mixin-based defaults):
    - Auto-hide soft-deleted records (SoftDeleteMixin)
    - Auto-exclude system-managed columns from forms (TimestampMixin, SoftDeleteMixin)
    - Auto-add filters for mixin columns (active, deleted, timestamps)

    Feature Flags:
    - auto_hide_deleted: Hide deleted records by default (default: True)
    - auto_hide_inactive: Hide inactive records by default (default: False)
    - auto_exclude_system_columns: Exclude system columns from forms (default: True)
    - auto_filter_mixins: Add filters based on mixins (default: True)
    - auto_searchable_strings: Auto-detect searchable string columns (default: False, Phase 2)
    """

    # Flask Admin exposes edit/delete by default, but suppresses view.
    # Let's turn that on.
    can_view_details = True

    # ===== Phase 1: Feature Flags =====
    auto_hide_deleted = True           # 🔴 Critical - hide soft-deleted records
    auto_hide_inactive = False         # Explicitly opt-in only
    auto_exclude_system_columns = True # 🔴 Critical - prevent editing system fields
    auto_filter_mixins = True          # 🟡 High - add mixin-based filters
    auto_searchable_strings = False    # 🟢 Medium - Phase 2 feature

    # Standard exclusions for system-managed columns
    form_excluded_columns = ['creation_time', 'modified_time', 'deletion_time']

    # Control edit/create/delete via properties
    @property
    def can_edit(self):
        return self._check_permission('EDIT_USERS')

    @property
    def can_create(self):
        return self._check_permission('CREATE_USERS')

    @property
    def can_delete(self):
        # almost never would we want to delete a record through the UI, even as a site admin.
        # SAM records are generally deactivated but retained.  If absolutely necessary itels
        # can be deleted via raw DB access.
        return False
        #return self._check_permission('DELETE_USERS')

    #can_set_page_size = True
    #page_size_options = (10, 25, 50, 100)
    page_size = 100

    def is_accessible(self):
        """
        Determine if current user can access this view.

        Default: Requires authentication.
        Override this method in subclasses to add role/permission checks.

        Example:
            def is_accessible(self):
                if not current_user.is_authenticated:
                    return False
                from webapp.utils.rbac import has_permission, Permission
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

    # ===== Phase 1: Core Query Filtering =====

    def get_query(self):
        """
        Override to exclude soft-deleted records by default.

        Phase 1 Enhancement: Auto-hide deleted records for models with SoftDeleteMixin.
        Can be disabled per-view by setting auto_hide_deleted = False.

        Note: Handles NULL values in deleted column (treats NULL as "not deleted").
        """
        query = super().get_query()

        # Auto-hide soft-deleted records
        # Filter for deleted != True (includes both False and NULL)
        if self.auto_hide_deleted and hasattr(self.model, 'deleted'):
            query = query.filter(
                (self.model.deleted == False) | (self.model.deleted.is_(None))
            )

        # Auto-hide inactive records (opt-in)
        if self.auto_hide_inactive and hasattr(self.model, 'active'):
            query = query.filter_by(active=True)

        return query

    def get_count_query(self):
        """
        Override count query to match filtered query.

        Phase 1 Enhancement: Ensure pagination counts match visible records.
        """
        query = super().get_count_query()

        # Match the same filtering as get_query()
        if self.auto_hide_deleted and hasattr(self.model, 'deleted'):
            query = query.filter(
                (self.model.deleted == False) | (self.model.deleted.is_(None))
            )

        if self.auto_hide_inactive and hasattr(self.model, 'active'):
            query = query.filter_by(active=True)

        return query

    # ===== Phase 1: Auto-Exclude System Columns =====

    def scaffold_form(self):
        """
        Override to auto-exclude system-managed columns from forms.

        Phase 1 Enhancement: Automatically exclude columns that shouldn't be
        manually edited (timestamps, deletion_time, etc.).
        """
        form_class = super().scaffold_form()

        if self.auto_exclude_system_columns:
            # Build set of columns to auto-exclude based on model mixins
            auto_exclude = set(self.form_excluded_columns or [])

            # TimestampMixin columns
            if hasattr(self.model, 'creation_time'):
                auto_exclude.update(['creation_time', 'modified_time'])

            # SoftDeleteMixin columns
            if hasattr(self.model, 'deleted'):
                auto_exclude.update(['deleted', 'deletion_time'])

            # Update form_excluded_columns
            self.form_excluded_columns = list(auto_exclude)

        return form_class

    # ===== Phase 1: Auto-Add Mixin-Based Filters =====

    def __init__(self, model, session, name=None, category=None, endpoint=None, url=None, **kwargs):
        """
        Initialize view and auto-add mixin-based filters.

        Phase 1 Enhancement: Automatically prepend filters for model mixins
        to the column_filters list.
        """
        # Auto-add mixin-based filters if enabled
        if self.auto_filter_mixins:
            # Build list of mixin filters to prepend
            mixin_filters = []

            # ActiveFlagMixin - add 'active' filter as first/prominent option
            if hasattr(model, 'active'):
                mixin_filters.append('active')

            # SoftDeleteMixin - add 'deleted' filter to allow viewing deleted records
            if hasattr(model, 'deleted'):
                mixin_filters.append('deleted')

            # TimestampMixin - add date range filters (check each individually)
            if hasattr(model, 'creation_time'):
                mixin_filters.append('creation_time')
            if hasattr(model, 'modified_time'):
                mixin_filters.append('modified_time')

            # DateRangeMixin - add date range filters (check each individually)
            if hasattr(model, 'start_date'):
                mixin_filters.append('start_date')
            if hasattr(model, 'end_date'):
                mixin_filters.append('end_date')

            # Prepend mixin filters to existing column_filters
            existing_filters = list(self.column_filters) if self.column_filters else []

            # Remove duplicates while preserving order (mixin filters first)
            all_filters = []
            seen = set()
            for f in mixin_filters + existing_filters:
                if f not in seen:
                    all_filters.append(f)
                    seen.add(f)

            self.column_filters = all_filters if all_filters else None

        # Call parent __init__
        super().__init__(model, session, name, category, endpoint, url, **kwargs)

    # ===== Phase 2: Auto-Detection Features =====

    def scaffold_list_columns(self):
        """
        Override to auto-order columns with status flags early, timestamps late.

        Phase 2 Enhancement: Smart column ordering for better UX:
        1. Primary ID and identifier columns first (auto-detected)
        2. Status flags (active, deleted) early for quick scanning
        3. Domain-specific columns in the middle
        4. Timestamps (creation_time, modified_time) always last
        """
        columns = list(super().scaffold_list_columns())

        # Identify column types
        status_cols = []
        timestamp_cols = []
        other_cols = []

        for col_name in columns:
            if col_name in ['active', 'deleted', 'locked']:
                status_cols.append(col_name)
            elif col_name in ['creation_time', 'modified_time', 'deletion_time']:
                timestamp_cols.append(col_name)
            else:
                other_cols.append(col_name)

        # Re-order: other columns first, then status, then timestamps
        # (ID columns naturally come first in other_cols)
        return other_cols + status_cols + timestamp_cols

    def scaffold_searchable_columns(self):
        """
        Auto-populate searchable list with string columns.

        Phase 2 Enhancement: Make all VARCHAR/String columns searchable by default,
        excluding sensitive fields (password, hash, token, secret, key).

        Can be disabled by setting _manual_searchable = True on the view.
        """
        # Respect manual override
        if hasattr(self, '_manual_searchable') and self._manual_searchable:
            return self.column_searchable_list

        # Only auto-detect if feature is enabled and no manual list provided
        if not self.auto_searchable_strings or self.column_searchable_list:
            return super().scaffold_searchable_columns()

        searchable = []
        excluded_patterns = ['password', 'hash', 'token', 'secret', 'key']

        # Iterate over model columns
        for column_name, column in self.model.__mapper__.columns.items():
            # Add string columns, exclude sensitive fields
            if isinstance(column.type, String):
                if not any(pattern in column_name.lower() for pattern in excluded_patterns):
                    searchable.append(column_name)

        return searchable or super().scaffold_searchable_columns()

    def get_column_names(self, *args, **kwargs):
        """
        Override to set default sort order.

        Phase 2 Enhancement: Auto-sort by creation_time DESC if no other
        sort specified and model has TimestampMixin.
        """
        # Set default sort if not already specified
        if not hasattr(self, 'column_default_sort') or self.column_default_sort is None:
            if hasattr(self.model, 'creation_time'):
                self.column_default_sort = ('creation_time', True)  # True = descending

        return super().get_column_names(*args, **kwargs)


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

# WallclockExemption Admin View
class WallclockExemptionDefaultAdmin(SAMModelView):
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
