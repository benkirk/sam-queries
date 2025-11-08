from flask import Flask, redirect, url_for
from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from flask_bootstrap import Bootstrap
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from wtforms.validators import ValidationError
from sam import *
from sam.session import create_sam_engine
from sam.core.users import *
from sam.core.organizations import *
from sam.summaries.comp_summaries import *
from sam.summaries.hpc_summaries import *
from sam.summaries.comp_summaries import *

# Better session management
engine, _ = create_sam_engine()
session_factory = sessionmaker(bind=engine)
Session = scoped_session(session_factory)


app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-change-this-in-production'
app.config['FLASK_ADMIN_SWATCH'] = 'cerulean'  # Bootstrap theme

# Initialize Flask-Bootstrap
bootstrap = Bootstrap(app)


# Custom index page
class MyAdminIndexView(AdminIndexView):
    @expose('/')
    def index(self):
        # Get some stats
        user_count = Session.query(User).filter(User.active == True).count()
        project_count = Session.query(Project).filter(Project.active == True).count()

        # Get active resource count
        resource_count = Session.query(Resource).filter(
            Resource.is_commissioned == True
        ).count()

        return self.render('admin/custom_index.html',
                         user_count=user_count,
                         project_count=project_count,
                         resource_count=resource_count)


# Initialize Admin
admin = Admin(
    app,
    name='SAM Admin',
    index_view=MyAdminIndexView(),
)


# User Management
class UserAdmin(ModelView):
    column_list = ('user_id', 'username', 'full_name', 'primary_email',
                   'active', 'locked')
    column_searchable_list = ('username', 'first_name', 'last_name')
    column_filters = ('active', 'locked', 'charging_exempt')
    form_excluded_columns = ('creation_time', 'modified_time',
                            'led_projects', 'admin_projects',
                            'accounts', 'email_addresses')

    # Display settings
    column_default_sort = 'username'
    page_size = 50

    column_formatters = {
        'full_name': lambda v, c, m, p: m.full_name,
        'primary_email': lambda v, c, m, p: m.primary_email or 'N/A'
    }

    # Details view
    column_details_list = ('user_id', 'username', 'full_name', 'primary_email',
                          'active', 'locked', 'charging_exempt', 'upid',
                          'unix_uid', 'creation_time')


# Project Management
class ProjectAdmin(ModelView):
    column_list = ('project_id', 'projcode', 'title', 'lead_username',
                   'active', 'charging_exempt')
    column_searchable_list = ('projcode', 'title')
    column_filters = ('active', 'charging_exempt', 'area_of_interest.area_of_interest')
    form_excluded_columns = ('creation_time', 'modified_time', 'accounts',
                            'children', 'contracts', 'directories')

    column_default_sort = 'projcode'
    page_size = 50

    # Inline editing for quick updates
    column_editable_list = ('active', 'charging_exempt')

    column_formatters = {
        'lead_username': lambda v, c, m, p: m.lead.username if m.lead else 'N/A'
    }


# Account Management
class AccountAdmin(ModelView):
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
class AllocationAdmin(ModelView):
    column_list = ('allocation_id', 'account_projcode',
                   'amount', 'start_date', 'end_date', 'deleted')
    column_filters = ('deleted', 'start_date', 'end_date')
    column_searchable_list = ('description',)
    form_excluded_columns = ('creation_time', 'transactions', 'children')

    column_default_sort = ('start_date', True)  # Newest first

    column_formatters = {
        'account_projcode': lambda v, c, m, p: m.account.project.projcode if m.account and m.account.project else 'N/A'
    }

    # Custom validation
    def on_model_change(self, form, model, is_created):
        if model.end_date and model.start_date > model.end_date:
            raise ValidationError('Start date must be before end date')


# Resource Management
class ResourceAdmin(ModelView):
    column_list = ('resource_id', 'resource_name', 'resource_type_name',
                   'is_commissioned', 'charging_exempt')
    column_searchable_list = ('resource_name', 'description')
    column_filters = ('charging_exempt', 'resource_type.resource_type')
    form_excluded_columns = ('creation_time', 'modified_time', 'accounts')

    column_formatters = {
        'resource_type_name': lambda v, c, m, p: m.resource_type.resource_type if m.resource_type else 'N/A'
    }


# Read-only reports
class ChargeSummaryAdmin(ModelView):
    can_create = False
    can_edit = False
    can_delete = False
    can_export = True

    column_default_sort = ('activity_date', True)  # Newest first
    column_filters = ('activity_date', 'projcode', 'facility_name')
    page_size = 100


# Add all views with proper endpoint names (no spaces!)
# Flask-Admin uses the 'name' parameter for display, and the endpoint is derived from the model class name

# Users category
admin.add_view(UserAdmin(User, Session(),
                         name='Users',
                         endpoint='users',  # Explicit endpoint
                         category='Users'))
admin.add_view(ModelView(EmailAddress, Session(),
                         name='Email Addresses',
                         endpoint='email_addresses',
                         category='Users'))
admin.add_view(ModelView(UserInstitution, Session(),
                         name='User Institutions',
                         endpoint='user_institutions',
                         category='Users'))

# Projects category
admin.add_view(ProjectAdmin(Project, Session(),
                            name='Projects',
                            endpoint='projects',
                            category='Projects'))
admin.add_view(AccountAdmin(Account, Session(),
                            name='Accounts',
                            endpoint='accounts',
                            category='Projects'))
admin.add_view(AllocationAdmin(Allocation, Session(),
                               name='Allocations',
                               endpoint='allocations',
                               category='Projects'))

# Resources category
admin.add_view(ResourceAdmin(Resource, Session(),
                             name='Resources',
                             endpoint='resources',
                             category='Resources'))
admin.add_view(ModelView(Machine, Session(),
                         name='Machines',
                         endpoint='machines',
                         category='Resources'))
admin.add_view(ModelView(Queue, Session(),
                         name='Queues',
                         endpoint='queues',
                         category='Resources'))

# Reports category
admin.add_view(ChargeSummaryAdmin(CompChargeSummary, Session(),
                                  name='Comp Charges',
                                  endpoint='comp_charges',  # Use underscore, not space!
                                  category='Reports'))
admin.add_view(ChargeSummaryAdmin(HPCChargeSummary, Session(),
                                  name='HPC Charges',
                                  endpoint='hpc_charges',  # Use underscore, not space!
                                  category='Reports'))


# Home page redirect
@app.route('/')
def index():
    return redirect(url_for('admin.index'))


if __name__ == '__main__':
    app.run(debug=True, port=5000)
