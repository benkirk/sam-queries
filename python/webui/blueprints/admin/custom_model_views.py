from flask_admin.contrib.sqla import ModelView
from wtforms.validators import ValidationError
from .default_model_views import SAMModelView

# User Management
class UserAdmin(SAMModelView):
    column_list = ('user_id', 'username', 'full_name', 'primary_email',
                   'active', 'locked')
    column_searchable_list = ('username', 'first_name', 'last_name')
    column_filters = ('active', 'locked', 'charging_exempt')
    form_excluded_columns = ('creation_time', 'modified_time',
                            'led_projects', 'admin_projects',
                            'accounts', 'email_addresses')

    column_default_sort = 'username'
    page_size = 50

    column_formatters = {
        'full_name': lambda v, c, m, p: m.full_name,
        'primary_email': lambda v, c, m, p: m.primary_email or 'N/A'
    }

    column_details_list = ('user_id', 'username', 'full_name', 'primary_email',
                          'active', 'locked', 'charging_exempt', 'upid',
                          'unix_uid', 'creation_time')


# Project Management
class ProjectAdmin(SAMModelView):
    column_list = ('project_id', 'projcode', 'title', 'lead_username',
                   'active', 'charging_exempt')
    column_searchable_list = ('projcode', 'title')
    column_filters = ('active', 'charging_exempt', 'area_of_interest.area_of_interest')
    form_excluded_columns = ('creation_time', 'modified_time', 'accounts',
                            'children', 'contracts', 'directories')

    column_default_sort = 'projcode'
    page_size = 50
    column_editable_list = ('active', 'charging_exempt')

    column_formatters = {
        'lead_username': lambda v, c, m, p: m.lead.username if m.lead else 'N/A'
    }


# Project Directories
class ProjectDirectoryAdmin(SAMModelView):
    column_list = ('directory_name', 'project', 'creation_time', 'end_date')
    column_searchable_list = ('directory_name', 'project.projcode')
    form_excluded_columns = ('creation_time', 'modified_time')


# Account Management
class AccountAdmin(SAMModelView):
    column_list = ('account_id', 'project_projcode', 'resource_name',
                   'deleted', 'creation_time')
    column_searchable_list = ('project.projcode',)
    column_filters = ('deleted', 'resource.resource_name')
    form_excluded_columns = ('creation_time', 'modified_time')

    column_formatters = {
        'project_projcode': lambda v, c, m, p: m.project.projcode if m.project else 'N/A',
        'resource_name': lambda v, c, m, p: m.resource.resource_name if m.resource else 'N/A'
    }


# Allocation Management
class AllocationAdmin(SAMModelView):
    column_list = ('allocation_id', 'account_projcode',
                   'amount', 'start_date', 'end_date', 'deleted')
    column_filters = ('deleted', 'start_date', 'end_date')
    column_searchable_list = ('description',)
    form_excluded_columns = ('creation_time', 'transactions', 'children')

    column_default_sort = ('start_date', True)

    column_formatters = {
        'account_projcode': lambda v, c, m, p: m.account.project.projcode if m.account and m.account.project else 'N/A'
    }

    def on_model_change(self, form, model, is_created):
        if model.end_date and model.start_date > model.end_date:
            raise ValidationError('Start date must be before end date')


# Resource Management
class ResourceAdmin(SAMModelView):
    column_list = ('resource_id', 'resource_name', 'resource_type_name',
                   'is_commissioned', 'charging_exempt')
    column_searchable_list = ('resource_name', 'description')
    column_filters = ('charging_exempt', 'resource_type.resource_type')
    form_excluded_columns = ('creation_time', 'modified_time', 'accounts')

    column_formatters = {
        'resource_type_name': lambda v, c, m, p: m.resource_type.resource_type if m.resource_type else 'N/A'
    }


# Read-only reports
class ChargeSummaryAdmin(SAMModelView):
    can_create = False
    can_edit = False
    can_delete = False
    can_export = True

    column_default_sort = ('activity_date', True)
    column_filters = ('activity_date', 'projcode', 'facility_name')
    column_searchable_list = ('username', 'projcode')

    page_size = 100
