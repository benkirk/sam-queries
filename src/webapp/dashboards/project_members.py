"""
Project member management blueprint.

Cross-cutting feature: both the user dashboard and the admin Edit Project
page call these routes. Lives in its own blueprint so neither dashboard
has to reach into the other's namespace.

Routes:
  GET    /project-members/<projcode>                 — render members table
  GET    /project-members/<projcode>/add-form        — render add-member form
  POST   /project-members/<projcode>/add             — submit add-member form
  DELETE /project-members/<projcode>/<username>      — remove a member
  PUT    /project-members/<projcode>/admin           — change/clear project admin
"""

from datetime import date, datetime

from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from marshmallow import ValidationError

from sam.projects.projects import Project
from sam.queries.users import get_users_on_project
from sam.schemas.forms.user import AddMemberForm
from webapp.extensions import db
from webapp.utils.htmx import htmx_success
from webapp.utils.project_permissions import (
    can_change_admin,
    can_manage_project_members,
)


bp = Blueprint('project_members', __name__, url_prefix='/project-members')


@bp.route('/<projcode>')
@login_required
def members_fragment(projcode):
    """
    Lazy-loaded HTML fragment showing project members.

    Returns:
        HTML table of project members with management controls (if authorized)
    """
    project = Project.get_by_projcode(db.session, projcode)

    if not project:
        return '<p class="text-danger mb-0">Project not found</p>'

    members = get_users_on_project(db.session, projcode)

    if not members:
        return '<p class="text-muted mb-0">No members found or project not accessible</p>'

    return render_template(
        'project_members/fragments/members_table.html',
        members=sorted(members, key=lambda member: member["display_name"]),
        projcode=projcode,
        project=project,
        can_manage=can_manage_project_members(current_user, project),
        can_change_admin=can_change_admin(current_user, project)
    )


@bp.route('/<projcode>/add-form')
@login_required
def htmx_add_member_form(projcode):
    """
    Return the add member form as an HTML fragment.

    Called when the htmx Add Member button is clicked. Returns a fresh form
    pre-populated with today's start date, ready to be inserted into the modal.
    """
    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger m-3">Project not found</div>', 404

    if not can_manage_project_members(current_user, project):
        return '<div class="alert alert-danger m-3">Unauthorized</div>', 403

    return render_template(
        'project_members/fragments/add_member_form_htmx.html',
        projcode=projcode,
        start_date=date.today().strftime('%Y-%m-%d'),
        errors=[]
    )


@bp.route('/<projcode>/add', methods=['POST'])
@login_required
def htmx_add_member(projcode):
    """
    Handle add member form submission (htmx).

    On validation error: returns the form with error messages (htmx swaps
    it back into the modal, user sees inline errors).

    On success: returns a success message + OOB swap to update the members
    table, then auto-closes the modal.
    """
    from sam.manage import add_user_to_project, management_transaction
    from sam.core.users import User

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger m-3">Project not found</div>', 404

    if not can_manage_project_members(current_user, project):
        return '<div class="alert alert-danger m-3">Unauthorized</div>', 403

    try:
        form_data = AddMemberForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'project_members/fragments/add_member_form_htmx.html',
            projcode=projcode,
            start_date=request.form.get('start_date', ''),
            end_date=request.form.get('end_date', ''),
            errors=AddMemberForm.flatten_errors(e.messages)
        )

    username = form_data['username']
    start_date = datetime.combine(form_data['start_date'], datetime.min.time()) if form_data.get('start_date') else None
    end_date = form_data['end_date']

    # Look up the user
    user = db.session.query(User).filter_by(username=username).first()
    if not user:
        return render_template(
            'project_members/fragments/add_member_form_htmx.html',
            projcode=projcode,
            start_date=request.form.get('start_date', ''),
            end_date=request.form.get('end_date', ''),
            errors=[f'User "{username}" not found']
        )

    # Add the member
    try:
        with management_transaction(db.session):
            add_user_to_project(
                db.session, project.project_id, user.user_id,
                start_date, end_date
            )
    except (ValueError, Exception) as e:
        return render_template(
            'project_members/fragments/add_member_form_htmx.html',
            projcode=projcode,
            start_date=request.form.get('start_date', ''),
            end_date=request.form.get('end_date', ''),
            errors=[str(e)]
        )

    # Success — render updated members table for OOB swap
    members_html = _render_members_table(projcode, project)

    return htmx_success(
        'project_members/fragments/add_member_success_htmx.html',
        {'closeModal': 'addMemberModal'},
        message=f'Added {user.display_name} to project {projcode}',
        projcode=projcode,
        members_html=members_html
    )


@bp.route('/<projcode>/<username>', methods=['DELETE'])
@login_required
def htmx_remove_member(projcode, username):
    """
    Remove a member from a project (htmx).

    Returns the updated members table HTML on success, or an error alert.
    """
    from sam.manage import remove_user_from_project, management_transaction
    from sam.core.users import User

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found</div>', 404

    if not can_manage_project_members(current_user, project):
        return '<div class="alert alert-danger">Unauthorized</div>', 403

    user = db.session.query(User).filter_by(username=username).first()
    if not user:
        return f'<div class="alert alert-danger">User "{username}" not found</div>', 404

    try:
        with management_transaction(db.session):
            remove_user_from_project(db.session, project.project_id, user.user_id)
    except (ValueError, Exception) as e:
        return f'<div class="alert alert-danger">{e}</div>', 400

    return _render_members_table(projcode, project)


@bp.route('/<projcode>/admin', methods=['PUT'])
@login_required
def htmx_change_admin(projcode):
    """
    Change or remove the project admin (htmx).

    Form field: admin_username (empty string to remove admin role).
    Returns the updated members table HTML on success.
    """
    from sam.manage import change_project_admin, management_transaction
    from sam.core.users import User

    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return '<div class="alert alert-danger">Project not found</div>', 404

    if not can_change_admin(current_user, project):
        return '<div class="alert alert-danger">Unauthorized — only project lead can change admin</div>', 403

    admin_username = request.form.get('admin_username', '').strip()

    try:
        with management_transaction(db.session):
            if admin_username:
                new_admin = db.session.query(User).filter_by(username=admin_username).first()
                if not new_admin:
                    return f'<div class="alert alert-danger">User "{admin_username}" not found</div>', 404
                change_project_admin(db.session, project.project_id, new_admin.user_id)
            else:
                change_project_admin(db.session, project.project_id, None)
    except (ValueError, Exception) as e:
        return f'<div class="alert alert-danger">{e}</div>', 400

    return _render_members_table(projcode, project)


def _render_members_table(projcode, project):
    """Render the members table fragment for a project (shared by htmx routes)."""
    members = get_users_on_project(db.session, projcode)
    return render_template(
        'project_members/fragments/members_table.html',
        members=sorted(members, key=lambda m: m["display_name"]),
        projcode=projcode,
        project=project,
        can_manage=can_manage_project_members(current_user, project),
        can_change_admin=can_change_admin(current_user, project)
    )
