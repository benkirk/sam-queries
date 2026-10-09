"""``/ldapsync/*`` -- the collections the daemon loads and the upserts it pushes."""

from flask import request

from sam.manage import ldapsync as sync
from sam.manage.ldapsync import SyncValidationError
from sam.manage.transaction import management_transaction
from sam.queries import ldapsync as q
from sam.schemas import ldapsync as s
from sam.schemas.forms.ldapsync import (
    GidAllocationSyncInput,
    GroupSyncInput,
    InstitutionSyncInput,
    OrganizationSyncInput,
    UserSyncInput,
)
from webapp.extensions import csrf, db

from . import bp, empty_response, json_response, ldapsync_api_required, read_json_body


def _get(rule):
    """A GET collection rule; legacy also served each with a trailing slash."""
    return bp.route(f'/ldapsync/{rule}', methods=['GET'], strict_slashes=False)


@_get('status')
@ldapsync_api_required()
def get_status():
    return json_response(s.SyncStatusSchema().dump(q.sync_status(db.session)))


@_get('institution')
@ldapsync_api_required()
def get_institutions():
    return json_response(s.InstitutionSyncSchema(many=True).dump(q.institutions(db.session)))


@_get('organization')
@ldapsync_api_required()
def get_organizations():
    return json_response(s.OrganizationSyncSchema(many=True).dump(q.organizations(db.session)))


@_get('user')
@ldapsync_api_required()
def get_users():
    return json_response(s.UserSyncSchema(many=True).dump(q.users(db.session)))


@_get('user/<unix_uid>')
@ldapsync_api_required()
def get_user(unix_uid):
    """One user by unix uid; an unknown uid is 200 with an empty body, never 404."""
    try:
        uid = int(unix_uid)
    except ValueError:
        raise SyncValidationError(f'Invalid unixUid {unix_uid}.')
    user = q.user_by_unix_uid(db.session, uid)
    return empty_response() if user is None else json_response(s.UserSyncSchema().dump(user))


@_get('group')
@ldapsync_api_required()
def get_groups():
    return json_response(s.GroupSyncSchema(many=True).dump(q.groups(db.session)))


@_get('gidAllocation')
@ldapsync_api_required()
def get_gid_allocations():
    return json_response(
        s.GidAllocationSyncSchema(many=True).dump(q.gid_allocations(db.session)))


@_get('projectGroup')
@ldapsync_api_required()
def get_project_groups():
    """Every project as a group; only the named ``since=`` filters (legacy ignores a bare ``?<ms>``)."""
    since = q.read_since(request.args.get('since'))
    return json_response(
        s.ProjectGroupSyncSchema(many=True).dump(q.project_groups(db.session, since)))


@_get('groupTag')
@ldapsync_api_required()
def get_group_tags():
    return json_response(s.GroupTagSyncSchema(many=True).dump(q.group_tags(db.session)))


# --- upserts: each echoes the record's key as a bare JSON integer ----------

def _put(rule):
    return bp.route(f'/ldapsync/{rule}', methods=['PUT'], strict_slashes=False)


def _apply(schema_cls, sync_fn):
    data = schema_cls().load(read_json_body())
    with management_transaction(db.session):
        key = sync_fn(db.session, data)
    return json_response(key)


@_put('institution')
@csrf.exempt
@ldapsync_api_required()
def put_institution():
    return _apply(InstitutionSyncInput, sync.sync_institution)


@_put('organization')
@csrf.exempt
@ldapsync_api_required()
def put_organization():
    return _apply(OrganizationSyncInput, sync.sync_organization)


@_put('gidAllocation')
@csrf.exempt
@ldapsync_api_required()
def put_gid_allocation():
    return _apply(GidAllocationSyncInput, sync.sync_gid_allocation)


@_put('user')
@csrf.exempt
@ldapsync_api_required()
def put_user():
    return _apply(UserSyncInput, sync.sync_user)


@_put('group')
@csrf.exempt
@ldapsync_api_required()
def put_group():
    return _apply(GroupSyncInput, sync.sync_group)
