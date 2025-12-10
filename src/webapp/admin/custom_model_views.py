from flask_admin.contrib.sqla import ModelView
from flask_login import current_user
from wtforms.validators import ValidationError
from webapp.utils.rbac import Permission

from .default_model_views import SAMModelView

# ===== Phase 3: Refactored Custom Views =====
# Redundant configurations removed (now handled by SAMModelView defaults):
# - creation_time, modified_time auto-excluded from forms
# - active, deleted, timestamp filters auto-added
# - Smart column ordering (status early, timestamps late)
# - Status indicator formatters added

# Helper functions for status formatters
def format_active_status(view, context, model, name):
    """Format active status with visual indicator."""
    if hasattr(model, 'active'):
        return '✓ Active' if model.active else '✗ Inactive'
    return ''

def format_deleted_status(view, context, model, name):
    """Format deleted status with visual indicator."""
    if hasattr(model, 'deleted'):
        return '✗ Deleted' if model.deleted else ''
    return ''


# User Management
class UserAdmin(SAMModelView):
    """
    User administration view with RBAC.

    Permissions required:
    - VIEW_USERS: View and search users
    - EDIT_USERS: Edit user details
    - CREATE_USERS: Create new users
    - DELETE_USERS: Delete users

    Phase 3 Refactoring:
    - Removed redundant creation_time/modified_time exclusions (auto-handled)
    - Removed redundant 'active' filter (auto-added by ActiveFlagMixin)
    - Kept custom formatters and relationship exclusions
    """

    column_list = ('user_id', 'username', 'full_name', 'primary_email',
                   'active', 'locked')
    column_searchable_list = ('username', 'first_name', 'last_name')

    # Only specify filters NOT auto-added by mixins
    column_filters = ['active', 'locked', 'charging_exempt', 'deleted', 'creation_time', 'modified_time']

    # Only specify non-mixin exclusions (relationships, etc.)
    form_excluded_columns = ['led_projects', 'admin_projects', 'accounts', 'email_addresses']

    column_default_sort = 'username'
    page_size = 50

    column_formatters = {
        'full_name': lambda v, c, m, p: m.full_name,
        'primary_email': lambda v, c, m, p: m.primary_email or 'N/A',
        'active': format_active_status,
        'deleted': format_deleted_status,
    }

    column_details_list = ('user_id', 'username', 'full_name', 'primary_email',
                          'active', 'locked', 'charging_exempt', 'upid',
                          'unix_uid', 'creation_time')


# Project Management
class ProjectAdmin(SAMModelView):
    """
    Project administration view.

    Phase 3 Refactoring:
    - Removed redundant timestamp exclusions (auto-handled)
    - Removed redundant 'active' filter (auto-added)
    - Added status formatter
    """

    column_list = ('project_id', 'projcode', 'title', 'lead_username',
                   'active', 'charging_exempt')
    column_searchable_list = ('projcode', 'title')

    # Keep area_of_interest filter, others are auto-added
    column_filters = ['active', 'charging_exempt', 'area_of_interest.area_of_interest',
                     'creation_time', 'modified_time']

    # Only exclude relationships (timestamps auto-excluded)
    form_excluded_columns = ['accounts', 'children', 'contracts', 'directories']

    column_default_sort = 'projcode'
    page_size = 50
    column_editable_list = ('active', 'charging_exempt')

    column_formatters = {
        'lead_username': lambda v, c, m, p: m.lead.username if m.lead else 'N/A',
        'active': format_active_status,
    }


# Project Directories
class ProjectDirectoryAdmin(SAMModelView):
    """
    Project directory management.

    Phase 3 Refactoring:
    - Removed form_excluded_columns (all were timestamps, now auto-excluded)
    """

    column_list = ('directory_name', 'project', 'creation_time', 'end_date')
    column_searchable_list = ('directory_name', 'project.projcode')
    # form_excluded_columns removed - timestamps auto-excluded


# Account Management
class AccountAdmin(SAMModelView):
    """
    Account administration (links projects to resources).

    Phase 3 Refactoring:
    - Removed timestamp exclusions (auto-handled)
    - Removed 'deleted' filter (auto-added by SoftDeleteMixin)
    - Added deleted status formatter
    """

    column_list = ('account_id', 'project_projcode', 'resource_name',
                   'deleted', 'creation_time')
    column_searchable_list = ('project.projcode',)

    # resource filter is custom, deleted/timestamps are auto-added
    column_filters = ['deleted', 'resource.resource_name', 'creation_time', 'modified_time']

    # No form exclusions needed (timestamps auto-handled)

    column_formatters = {
        'project_projcode': lambda v, c, m, p: m.project.projcode if m.project else 'N/A',
        'resource_name': lambda v, c, m, p: m.resource.resource_name if m.resource else 'N/A',
        'deleted': format_deleted_status,
    }


# Allocation Management
class AllocationAdmin(SAMModelView):
    """
    Allocation management (resource allocations for accounts).

    Phase 3 Refactoring:
    - Removed timestamp exclusion (auto-handled)
    - Removed deleted/date filters (auto-added by mixins)
    - Added deleted status formatter
    """

    column_list = ('allocation_id', 'account_projcode',
                   'amount', 'start_date', 'end_date', 'deleted')

    # All these filters are now auto-added by mixins
    column_filters = ['deleted', 'start_date', 'end_date', 'creation_time', 'modified_time']

    column_searchable_list = ('description',)

    # Only exclude relationships (timestamps auto-handled)
    form_excluded_columns = ['transactions', 'children']

    column_default_sort = ('start_date', True)

    column_formatters = {
        'account_projcode': lambda v, c, m, p: m.account.project.projcode if m.account and m.account.project else 'N/A',
        'deleted': format_deleted_status,
    }

    def on_model_change(self, form, model, is_created):
        if model.end_date and model.start_date > model.end_date:
            raise ValidationError('Start date must be before end date')


# Resource Management
class ResourceAdmin(SAMModelView):
    """
    Resource administration (HPC systems, storage).

    Phase 3 Refactoring:
    - Removed timestamp exclusions (auto-handled)
    """

    column_list = ('resource_id', 'resource_name', 'resource_type_name',
                   'is_commissioned', 'charging_exempt')
    column_searchable_list = ('resource_name', 'description')

    column_filters = ['charging_exempt', 'resource_type.resource_type', 'creation_time', 'modified_time']

    # Only exclude relationships (timestamps auto-handled)
    form_excluded_columns = ['accounts']

    column_formatters = {
        'resource_type_name': lambda v, c, m, p: m.resource_type.resource_type if m.resource_type else 'N/A'
    }


# Read-only reports
class ChargeSummaryAdmin(SAMModelView):
    """
    Read-only charge summary reports.

    No Phase 3 changes (specialized view).
    """

    can_create = False
    can_edit = False
    can_delete = False
    can_export = True

    column_default_sort = ('activity_date', True)
    column_filters = ('activity_date', 'projcode', 'facility_name')
    column_searchable_list = ('username', 'projcode')

    page_size = 100
