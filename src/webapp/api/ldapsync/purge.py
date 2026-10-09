"""``{user,group,institution,organization}PurgePermit`` and ``...Purge``.

The client's parameter names differ by route (``groupPurgePermit?unixGid=`` but
``groupPurge?posixGid=``); both group routes accept both. ``LDAPSYNC_PURGE_ENABLED``
off refuses every purge of an existing row (see ``sam.manage.purge``).
"""

from flask import current_app, request

from sam.manage import purge
from sam.manage.ldapsync import SyncValidationError
from sam.manage.transaction import management_transaction
from sam.schemas import ldapsync as s
from webapp.extensions import csrf, db

from . import bp, empty_response, json_response, ldapsync_api_required


def _enabled() -> bool:
    return bool(current_app.config.get('LDAPSYNC_PURGE_ENABLED'))


def _int_arg(*names):
    """The first of *names* present as an int; a non-integer value is a 400 (legacy: 400)."""
    for name in names:
        raw = request.args.get(name)
        if raw not in (None, ''):
            try:
                return int(raw)
            except ValueError:
                raise SyncValidationError(f'Invalid value for parameter {name}: {raw}.')
    return None


def _required_int(*names):
    value = _int_arg(*names)
    if value is None:
        raise SyncValidationError(f'Required parameter {names[0]} is missing.')
    return value


def _permit_route(name):
    return bp.route(f'/{name}PurgePermit', methods=['GET'], strict_slashes=False)


def _purge_route(name):
    return bp.route(f'/{name}Purge', methods=['DELETE'], strict_slashes=False)


@_permit_route('user')
@ldapsync_api_required()
def user_purge_permit():
    permit = purge.user_permit(db.session, unix_uid=_int_arg('unixUid'), upid=_int_arg('upid'),
                               username=request.args.get('username'), enabled=_enabled())
    return json_response(s.UserPurgePermitSchema().dump(permit))


@_permit_route('group')
@ldapsync_api_required()
def group_purge_permit():
    permit = purge.group_permit(db.session, _required_int('unixGid', 'posixGid'),
                                enabled=_enabled())
    return json_response(s.GroupPurgePermitSchema().dump(permit))


@_permit_route('institution')
@ldapsync_api_required()
def institution_purge_permit():
    permit = purge.institution_permit(db.session, _required_int('institutionId'),
                                      enabled=_enabled())
    return json_response(s.InstitutionPurgePermitSchema().dump(permit))


@_permit_route('organization')
@ldapsync_api_required()
def organization_purge_permit():
    permit = purge.organization_permit(db.session, _required_int('organizationId'),
                                       enabled=_enabled())
    return json_response(s.OrganizationPurgePermitSchema().dump(permit))


@_purge_route('user')
@csrf.exempt
@ldapsync_api_required()
def user_purge():
    with management_transaction(db.session):
        purge.purge_user(db.session, unix_uid=_int_arg('unixUid'), upid=_int_arg('upid'),
                         username=request.args.get('username'), enabled=_enabled())
    return empty_response()


@_purge_route('group')
@csrf.exempt
@ldapsync_api_required()
def group_purge():
    with management_transaction(db.session):
        purge.purge_group(db.session, gid=_int_arg('posixGid', 'unixGid'),
                          name=request.args.get('groupname'), enabled=_enabled())
    return empty_response()


@_purge_route('institution')
@csrf.exempt
@ldapsync_api_required()
def institution_purge():
    with management_transaction(db.session):
        purge.purge_institution(db.session, _required_int('institutionId'), enabled=_enabled())
    return empty_response()


@_purge_route('organization')
@csrf.exempt
@ldapsync_api_required()
def organization_purge():
    with management_transaction(db.session):
        purge.purge_organization(db.session, _required_int('organizationId'),
                                 enabled=_enabled())
    return empty_response()
