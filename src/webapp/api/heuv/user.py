"""``user/{username}/*`` -- a user's groups, login resources, assignments and exemptions."""

from flask import request

from sam.queries import heuv as q
from sam.schemas import heuv as h
from webapp.extensions import db

from . import bp, dump, found_or_400, heuv_api_required, json_response, parse_flag


def _user(username: str, method: str):
    return found_or_400(q.find_user(db.session, username), f'{method}.username',
                        f'Username {username} does not exist.')


@bp.route('/user/<username>/defaultproject', methods=['GET'], strict_slashes=False)
@heuv_api_required()
def get_user_default_projects(username):
    user = _user(username, 'getUserDefaultProjects')
    return dump(h.DefaultProjectSchema(many=True), q.default_projects(user))


@bp.route('/user/<username>/group', methods=['GET'], strict_slashes=False)
@heuv_api_required()
def get_user_groups(username):
    user = _user(username, 'getUserGroups')
    return dump(h.UserGroupSchema(many=True), q.user_groups(db.session, user))


@bp.route('/user/<username>/access', methods=['GET'], strict_slashes=False)
@heuv_api_required()
def get_user_accessible_resources(username):
    user = _user(username, 'getUserAccessibleResources')
    return dump(h.UserAccessSchema(many=True), q.user_access(db.session, user))


@bp.route('/user/<username>/assignedproject', methods=['GET'], strict_slashes=False)
@heuv_api_required()
def get_user_assignment_by_project(username):
    user = _user(username, 'getUserAssignmentByProject')
    name = (request.args.get('resourcename') or '').strip()
    resource = None
    if name:
        resource = found_or_400(q.find_resource(db.session, name), 'getUserAssignmentByProject.resourceName',
                                f'Resource {request.args["resourcename"]} does not exist.')
    rows = q.assigned_projects(db.session, user, parse_flag(request.args.get('thresholdlimited')), resource)
    return dump(h.AssignedProjectSchema(many=True), rows)


@bp.route('/user/<username>/assignedresource', methods=['GET'], strict_slashes=False)
@heuv_api_required()
def get_user_assignment_by_resource(username):
    user = _user(username, 'getUserAssignmentByResource')
    rows = q.assigned_resources(db.session, user, parse_flag(request.args.get('thresholdlimited')))
    return dump(h.AssignedResourceSchema(many=True), rows)


@bp.route('/user/<username>/userrolelogin', methods=['GET'], strict_slashes=False)
@heuv_api_required()
def get_user_role_logins(username):
    user = _user(username, 'getUserRoleLogins')
    return json_response(q.role_logins(db.session, user))


@bp.route('/user/<username>/wallclockexemption', methods=['GET'], strict_slashes=False)
@heuv_api_required()
def get_user_wallclock_exemptions(username):
    user = _user(username, 'getUserWallclockExemptions')
    active = request.args.get('active')
    flag = parse_flag(active) if active is not None else None
    return dump(h.WallclockExemptionsSchema(), q.wallclock_exemptions(user, username, flag))
