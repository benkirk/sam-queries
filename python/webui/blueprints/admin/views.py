from datetime import datetime, timedelta
from flask_admin import AdminIndexView, expose
from flask_login import current_user
from flask import redirect, url_for, request

from sam.core.users import User
from sam.projects.projects import Project, Resource
from sam.queries import get_projects_by_allocation_end_date, get_projects_with_expired_allocations


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
        from webui.extensions import db
        session = db.session

        # Get some stats
        user_count = session.query(User).filter(User.active == True).count()
        project_count = session.query(Project).filter(Project.active == True).count()

        # Get active resource count
        resource_count = session.query(Resource).filter(
            Resource.is_commissioned == True
        ).count()

        # Get expiration counts - use default facilities like the CLI
        default_facilities = ['UNIV', 'WNA']

        # Upcoming expirations (next 30 days)
        upcoming_expirations = get_projects_by_allocation_end_date(
            session,
            start_date=datetime.now(),
            end_date=datetime.now() + timedelta(days=30),
            facility_names=default_facilities
        )
        upcoming_count = len(upcoming_expirations)

        # Recently expired (last 90 days)
        expired_projects = get_projects_with_expired_allocations(
            session,
            max_days_expired=90,
            min_days_expired=365,
            facility_names=default_facilities
        )
        expired_count = len(expired_projects)

        return self.render('custom_admin_index.html',
                           user_count=f"{user_count:,}",
                           project_count=f"{project_count:,}",
                           resource_count=f"{resource_count:,}",
                           upcoming_count=f"{upcoming_count:,}",
                           expired_count=f"{expired_count:,}")
