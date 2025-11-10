from datetime import datetime, timedelta
from flask import current_app
from flask_admin import AdminIndexView, expose

from sam.core.users import User
from sam.projects.projects import Project, Resource
from sam.queries import get_projects_by_allocation_end_date, get_projects_with_expired_allocations


class MyAdminIndexView(AdminIndexView):
    @expose('/')
    def index(self):
        Session = current_app.Session

        # Get some stats
        user_count = Session.query(User).filter(User.active == True).count()
        project_count = Session.query(Project).filter(Project.active == True).count()

        # Get active resource count
        resource_count = Session.query(Resource).filter(
            Resource.is_commissioned == True
        ).count()

        # Get expiration counts - use default facilities like the CLI
        default_facilities = ['UNIV', 'WNA']

        # Upcoming expirations (next 30 days)
        upcoming_expirations = get_projects_by_allocation_end_date(
            Session,
            start_date=datetime.now(),
            end_date=datetime.now() + timedelta(days=30),
            facility_names=default_facilities
        )
        upcoming_count = len(upcoming_expirations)

        # Recently expired (last 90 days)
        expired_projects = get_projects_with_expired_allocations(
            Session,
            max_days_expired=90,
            min_days_expired=0,
            facility_names=default_facilities
        )
        expired_count = len(expired_projects)

        return self.render('custom_admin_index.html',
                           user_count=f"{user_count:,}",
                           project_count=f"{project_count:,}",
                           resource_count=f"{resource_count:,}",
                           upcoming_count=f"{upcoming_count:,}",
                           expired_count=f"{expired_count:,}")
