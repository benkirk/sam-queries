"""``POST /api/v1/xras/notes``: a recommender run's notes on XRAS requests, key only.

Not the frozen ``/api/xras/v1`` blueprint, which is XRAS's own inbound contract.
Design: ``docs/plans/ARC_RECOMMENDER_NOTES.md`` § 3.
"""
import json

from flask import Blueprint, g, jsonify, request
from marshmallow import ValidationError

from sam.manage import management_transaction
from sam.manage.xras_notes import record_run_notes
from sam.schemas.forms import RecommenderNoteBatchSchema
from sam.schemas.xras_notes import RunNotesReceiptSchema
from webapp.api.helpers import register_error_handlers
from webapp.extensions import csrf, db
from webapp.utils.api_auth import api_key_required
from webapp.utils.rbac import Permission

bp = Blueprint('api_xras_notes', __name__)
register_error_handlers(bp)

#: A forty-card run is ~200 KB; the cap bounds what one key can make the app parse.
_MAX_BODY = 1024 * 1024


@bp.route('/notes', methods=['POST'])
@csrf.exempt          # Basic-auth M2M caller: no cookies, CSRF n/a
@api_key_required(permission=Permission.POST_XRAS_NOTES)
def post_notes():
    """Upsert the latest note per request for one run; 200 with what was written."""
    if (request.content_length or 0) > _MAX_BODY:
        return jsonify({'error': f'Body exceeds {_MAX_BODY} bytes'}), 413
    raw = request.get_data(cache=False)
    if len(raw) > _MAX_BODY:
        return jsonify({'error': f'Body exceeds {_MAX_BODY} bytes'}), 413
    try:
        body = json.loads(raw)
    except ValueError as exc:
        return jsonify({'error': 'Request body must be JSON', 'details': str(exc)}), 400
    try:
        batch = RecommenderNoteBatchSchema().load(body)
    except ValidationError as exc:
        return jsonify({'error': 'Validation failed', 'details': exc.messages}), 400

    with management_transaction(db.session):
        written = record_run_notes(db.session, created_by=g.api_key_user, **batch)
    return jsonify(RunNotesReceiptSchema().dump({
        'source': batch['source'], 'run_id': batch['run_id'],
        'received': len(written), 'request_numbers': written}))
