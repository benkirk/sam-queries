"""``GET /api/v1/xras/award-audit/<projcode>``: XRAS awarded vs SAM allocated for one project.

The ``sam-admin xras --award-audit --request`` read over HTTP, for the ARC recommender's
``sam_crosscheck``. One XRAS read per call (the projcode's request family); the resource
catalog comes from the day-long ``cached_resources``. Contract: ``docs/apis/XRAS_API.md``.
"""
import logging

from flask import Blueprint, jsonify

from sam.schemas.xras_award_audit import AwardAuditSchema
from webapp.api.access_control import require_project_access
from webapp.api.helpers import register_error_handlers
from webapp.extensions import db
from webapp.utils.api_auth import login_or_token_required
from webapp.utils.rbac import Permission
from webapp.utils.xras import read_client

logger = logging.getLogger(__name__)

bp = Blueprint('api_xras_award_audit', __name__)
register_error_handlers(bp)

_schema = AwardAuditSchema()


def _unavailable(reason: str, message: str):
    return jsonify({'error': message, 'reason': reason}), 503


@bp.route('/award-audit/<projcode>', methods=['GET'])
@login_or_token_required(Permission.VIEW_XRAS_AWARD_AUDIT)
@require_project_access
def get_award_audit(project):
    """The award audit for one SAM project, expired awards included; 503 when XRAS cannot be asked."""
    from sam.integration.xras_api import XrasApiNotConfigured, XrasSourceUnavailable
    from sam.integration.xras_api.cache import cached_resources
    from sam.integration.xras_api.people import resource_key_map
    from sam.queries.xras_award_audit import audit_awards, fetch_award_lines

    try:
        client = read_client()
        lines, complete = fetch_award_lines(client, projcode=project.projcode,
                                            include_expired=True)
        catalog = cached_resources(client.get_resources)
    except XrasApiNotConfigured:
        return _unavailable('not_configured', 'The XRAS API is not configured on this server')
    except XrasSourceUnavailable as exc:
        logger.warning('award audit %s: XRAS not answering: %s', project.projcode, exc)
        return _unavailable('unreachable', 'XRAS did not answer')
    # An empty catalog would mark every award resource_unmapped: not an answer.
    if not catalog:
        return _unavailable('no_resource_catalog', 'XRAS returned no resource catalog')

    audit = audit_awards(db.session, lines, resource_key_map(catalog),
                         include_expired=True, complete=complete)
    return jsonify(_schema.dump(dict(audit.to_dict(), projcode=project.projcode)))
