from flask import Blueprint, current_app
from flask_admin import Admin
from flask_admin.theme import Bootstrap4Theme
from flask import Flask, redirect, url_for
from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from flask_admin.theme import Bootstrap4Theme
from wtforms.validators import ValidationError

from .views import MyAdminIndexView
from .custom_model_views import *
from .expiration_views import ProjectExpirationView

from sam import *
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

    # Import db from extensions module to access Flask-SQLAlchemy session
    from webapp.extensions import db

    # Initialize Admin
    admin = Admin(
        app,
        name='SAM Database',
        theme=Bootstrap4Theme(swatch="lumen", fluid=True),
        index_view=MyAdminIndexView(),
        url='/admin'
    )

    # Use db.session for all ModelViews - Flask-Admin will handle per-request session management
    # Users category
    admin.add_view(UserAdmin(User, db.session,
                             name='Users',
                             endpoint='users',
                             category='Users'))
    admin.add_view(ModelView(EmailAddress, db.session,
                             name='Email Addresses',
                             endpoint='email_addresses',
                             category='Users'))
    admin.add_view(ModelView(UserInstitution, db.session,
                             name='User Institutions',
                             endpoint='user_institutions',
                             category='Users'))

    # Projects category
    admin.add_view(ProjectAdmin(Project, db.session,
                                name='Projects',
                                endpoint='projects',
                                category='Projects'))
    admin.add_view(AccountAdmin(Account, db.session,
                                name='Accounts',
                                endpoint='accounts',
                                category='Projects'))
    admin.add_view(AllocationAdmin(Allocation, db.session,
                                   name='Allocations',
                                   endpoint='allocations',
                                   category='Projects'))
    admin.add_view(ProjectDirectoryAdmin(ProjectDirectory, db.session,
                                         name='Directories',
                                         endpoint='project_directory',
                                         category='Projects'))

    # Resources category
    admin.add_view(ResourceAdmin(Resource, db.session,
                                 name='Resources',
                                 endpoint='resources',
                                 category='Resources'))
    admin.add_view(ModelView(Machine, db.session,
                             name='Machines',
                             endpoint='machines',
                             category='Resources'))
    admin.add_view(ModelView(Queue, db.session,
                             name='Queues',
                             endpoint='queues',
                             category='Resources'))

    # Reports category
    admin.add_view(ChargeSummaryAdmin(CompChargeSummary, db.session,
                                      name='Comp Charges',
                                      endpoint='comp_charges',
                                      category='Reports'))
    admin.add_view(ChargeSummaryAdmin(HPCChargeSummary, db.session,
                                      name='HPC Charges',
                                      endpoint='hpc_charges',
                                      category='Reports'))
    admin.add_view(ProjectExpirationView(
                                      name='Project Expirations',
                                      endpoint='expirations',
                                      category='Reports'))

    # Misc
    from .add_default_models import add_default_views
    add_default_views(app,admin)
    return admin
