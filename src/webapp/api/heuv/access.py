"""``access`` and ``access/resource/{resourceName}`` -- login resources and their shells."""

from sam.queries import heuv as q
from sam.schemas import heuv as h
from webapp.extensions import db

from . import bp, dump, found_or_400, heuv_api_required, not_found


@bp.route('/access', methods=['GET'], strict_slashes=False)
@heuv_api_required()
def get_accessible_resources():
    return dump(h.AccessibleResourceSchema(many=True), q.accessible_resources(db.session))


@bp.route('/access/resource/<resource_name>', methods=['GET'], strict_slashes=False)
@heuv_api_required()
def get_accessible_resource(resource_name):
    found_or_400(q.find_resource(db.session, resource_name), 'getAccessibleResource.resourceName',
                 f'Resource {resource_name} does not exist.')
    rows = q.accessible_resources(db.session, resource_name)
    return dump(h.AccessibleResourceSchema(), rows[0]) if len(rows) == 1 else not_found()
