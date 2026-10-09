"""``/userlifecycle/*`` -- the collaborator keep-alive the daemon pushes to LDAP staging."""

from sam.queries.user_lifecycle import collab_expiry_updates
from sam.schemas.ldapsync import ActiveUserStatusSchema
from webapp.extensions import db

from . import bp, json_response, ldapsync_api_required


@bp.route('/userlifecycle/collabexpiryupdates', methods=['GET'], strict_slashes=False)
@ldapsync_api_required()
def get_collab_expiry_updates():
    """Collaborators whose directory end date is earlier than SAM's nominal expiry."""
    return json_response(
        ActiveUserStatusSchema(many=True).dump(collab_expiry_updates(db.session)))
