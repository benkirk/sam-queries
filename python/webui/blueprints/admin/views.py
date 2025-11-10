from flask import current_app
from flask_admin import AdminIndexView, expose

from sam.core.users import User
from sam.projects.projects import Project, Resource


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

        return self.render('custom_admin_index.html',
                           user_count=f"{user_count:,}",
                           project_count=f"{project_count:,}",
                           resource_count=f"{resource_count:,}")
