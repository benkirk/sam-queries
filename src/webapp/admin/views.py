from datetime import datetime
from flask_admin import AdminIndexView, expose
from flask_login import current_user
from flask import redirect, url_for, request

from sam.core.users import User
from sam.projects.projects import Project, Resource

class MyAdminIndexView(AdminIndexView):
    """
    Custom Admin Index View with authentication and role-based content.
    """

    def is_accessible(self):
        """Require authentication to access admin panel."""
        return current_user.is_authenticated

    def inaccessible_callback(self, name, **kwargs):
        """Redirect to login if not authenticated."""
        return redirect(url_for('auth.login', next=request.url))

    @expose('/')
    def index(self):
        # Import db from extensions module to access Flask-SQLAlchemy session
        from webapp.extensions import db
        session = db.session

        # Get some stats
        user_count = session.query(User).filter(User.active == True).count()
        project_count = session.query(Project).filter(Project.active == True).count()

        # Get active resource count
        resource_count = session.query(Resource).filter(
            Resource.is_commissioned == True
        ).count()

        return self.render('admin/index.html',
                           user_count=f"{user_count:,}",
                           project_count=f"{project_count:,}",
                           resource_count=f"{resource_count:,}")
