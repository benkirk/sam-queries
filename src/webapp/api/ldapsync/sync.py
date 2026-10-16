"""``/ldapsync/*`` -- the collections the daemon loads and the upserts it pushes."""

from flask import current_app, request

from sam.manage import ldapsync as sync
from sam.manage.lifecycle import restore_on_reactivate
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

from . import bp, empty_response, json_response, ldapsync_api_required, parse_int, read_json_body


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
    uid = parse_int(unix_uid, f'Invalid unixUid {unix_uid}.')
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


_SYNC_KEYS = {'person': 'upid', 'organization': 'organization_id',
              'institution': 'institution_id', 'group': 'posix_gid'}


def _directory_check(kind, data, key):
    """After the commit, fail-open: read the record, log the drift, apply the kinds whose
    lever is on in a second transaction. A failure is one warning, never a failed PUT."""
    if kind is None or not current_app.config.get('DIRECTORY_CHECK_ON_SYNC'):
        return
    try:
        from sam.integration.directory.config import DirectoryConfig
        from sam.queries.directory import run_check
        config = DirectoryConfig.from_environment()
        result = run_check(db.session, kind, data, key, config=config)
        if result is None or result.record is None or not config.apply_kinds:
            return
        from sam.manage.directory import log_result, reconcile_record
        with management_transaction(db.session):
            applied = reconcile_record(db.session, kind, data.get(_SYNC_KEYS[kind]),
                                       result.record, config=config,
                                       on_reactivate=restore_hook())
        if applied is not None:
            log_result(applied, key)
    except Exception as exc:
        current_app.logger.warning('ldapsync: directory check %s key=%s failed: %s',
                                   kind, key, type(exc).__name__)


def _apply(schema_cls, sync_fn, kind=None):
    data = schema_cls().load(read_json_body())
    with management_transaction(db.session):
        key = sync_fn(db.session, data)
    _directory_check(kind, data, key)
    return json_response(key)


@_put('institution')
@csrf.exempt
@ldapsync_api_required()
def put_institution():
    return _apply(InstitutionSyncInput, sync.sync_institution, 'institution')


@_put('organization')
@csrf.exempt
@ldapsync_api_required()
def put_organization():
    return _apply(OrganizationSyncInput, sync.sync_organization, 'organization')


@_put('gidAllocation')
@csrf.exempt
@ldapsync_api_required()
def put_gid_allocation():
    return _apply(GidAllocationSyncInput, sync.sync_gid_allocation)


def restore_hook():
    """The undo of a finished deactivation, when ``LDAPSYNC_RESTORE_ON_REACTIVATE`` is on."""
    return restore_on_reactivate(
        db.session, enabled=bool(current_app.config.get('LDAPSYNC_RESTORE_ON_REACTIVATE')),
        within_days=current_app.config.get('LDAPSYNC_RESTORE_WINDOW_DAYS', 90))


@_put('user')
@csrf.exempt
@ldapsync_api_required()
def put_user():
    data = UserSyncInput().load(read_json_body())
    with management_transaction(db.session):
        key = sync.sync_user(db.session, data, on_reactivate=restore_hook())
    _directory_check('person', data, key)
    return json_response(key)


@_put('group')
@csrf.exempt
@ldapsync_api_required()
def put_group():
    return _apply(GroupSyncInput, sync.sync_group, 'group')
