"""``/userlifecycle/*`` -- collaborator keep-alive, and finishing deactivations.

The finish pair is behind ``LDAPSYNC_LIFECYCLE_ENABLED`` (off): while off the
pending list is empty, so the daemon never asks to finish anyone.
"""

from flask import current_app, request

from sam.manage.ldapsync import SyncValidationError
from sam.manage.lifecycle import finish_user_deactivation, pending_deactivations
from sam.manage.transaction import management_transaction
from sam.queries.user_lifecycle import collab_expiry_updates
from sam.schemas.ldapsync import ActiveUserStatusSchema
from webapp.extensions import csrf, db

from . import bp, json_response, ldapsync_api_required

LIFECYCLE_DISABLED = 'Lifecycle updates are disabled in SAM.'


def _enabled() -> bool:
    return bool(current_app.config.get('LDAPSYNC_LIFECYCLE_ENABLED'))


@bp.route('/userlifecycle/collabexpiryupdates', methods=['GET'], strict_slashes=False)
@ldapsync_api_required()
def get_collab_expiry_updates():
    """Collaborators whose directory end date is earlier than SAM's nominal expiry."""
    return json_response(
        ActiveUserStatusSchema(many=True).dump(collab_expiry_updates(db.session)))


@bp.route('/userlifecycle/pendingdeactivations/', methods=['GET'], strict_slashes=False)
@bp.route('/userlifecycle/pendingdeactivations/<hours>', methods=['GET'])
@ldapsync_api_required()
def get_pending_deactivations(hours=None):
    """Usernames stamped more than *hours* ago. The client sends ``/?24``; both forms work."""
    raw = hours if hours is not None else request.query_string.decode()
    try:
        failsafe_hours = int(raw)
    except ValueError:
        raise SyncValidationError(f'Invalid failsafe hours {raw}.')
    if not _enabled():
        return json_response([])
    return json_response(pending_deactivations(db.session, failsafe_hours))


@bp.route('/userlifecycle/deactivate/<username>', methods=['PUT'], strict_slashes=False)
@csrf.exempt
@ldapsync_api_required()
def put_deactivate(username):
    """Finish a pending deactivation; the body (legacy: the JSON string ``""``) is ignored."""
    if not _enabled():
        raise SyncValidationError(LIFECYCLE_DISABLED)
    with management_transaction(db.session):
        finish_user_deactivation(db.session, username)
    return json_response('')
