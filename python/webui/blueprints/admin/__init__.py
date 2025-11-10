from flask import Blueprint, current_app
from flask_admin import Admin
from flask_admin.theme import Bootstrap4Theme
from flask import Flask, redirect, url_for
from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from flask_admin.theme import Bootstrap4Theme
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from wtforms.validators import ValidationError

from .views import MyAdminIndexView
from .model_views import (
    UserAdmin, ProjectAdmin, ProjectDirectoryAdmin,
    AccountAdmin, AllocationAdmin, ResourceAdmin,
    ChargeSummaryAdmin
)

from sam import *
from sam.session import create_sam_engine
from sam.core.users import *
from sam.core.organizations import *
from sam.projects.projects import *
from sam.summaries.comp_summaries import *
from sam.summaries.hpc_summaries import *
from sam.summaries.comp_summaries import *

# Create blueprint
admin_bp = Blueprint('admin_bp', __name__, template_folder='templates')


def init_admin(app):
    """Initialize Flask-Admin with all model views"""

    # Initialize Admin
    admin = Admin(
        app,
        name='SAM Admin',
        theme=Bootstrap4Theme(swatch="flatly", fluid=True),
        index_view=MyAdminIndexView(),
        url='/admin'
    )

    Session = app.Session

    # Users category
    admin.add_view(UserAdmin(User, Session(),
                             name='Users',
                             endpoint='users',
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
    admin.add_view(ProjectDirectoryAdmin(ProjectDirectory, Session(),
                                         name='Directories',
                                         endpoint='project_directory',
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
                                      endpoint='comp_charges',
                                      category='Reports'))
    admin.add_view(ChargeSummaryAdmin(HPCChargeSummary, Session(),
                                      name='HPC Charges',
                                      endpoint='hpc_charges',
                                      category='Reports'))

    # Misc
    from .add_default_models import add_default_views
    add_default_views(app,admin)
    return admin
