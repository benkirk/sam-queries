"""
System Status API endpoints (v1).

Provides RESTful API for system status data ingestion and retrieval.

POST endpoints (data ingestion, requires MANAGE_SYSTEM_STATUS permission):
    POST /api/v1/status/derecho
    POST /api/v1/status/casper
    POST /api/v1/status/jupyterhub
    POST /api/v1/status/outage

GET endpoints (status retrieval, public with login):
    GET /api/v1/status/derecho/latest
    GET /api/v1/status/casper/latest
    GET /api/v1/status/jupyterhub/latest
    GET /api/v1/status/outages
    GET /api/v1/status/reservations
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required
from webapp.utils.rbac import require_permission, Permission
from webapp.utils.api_auth import api_key_required
from webapp.api.helpers import register_error_handlers
from webapp.extensions import db
from datetime import datetime
import sys
from pathlib import Path

# Add system_status to path
python_dir = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(python_dir))

from system_status import (
    DerechoStatus,
    CasperStatus, CasperNodeTypeStatus,
    JupyterHubStatus,
    LoginNodeStatus,
    QueueStatus,
    FilesystemStatus,
    SystemOutage, ResourceReservation
)
from system_status.schemas.status import (
    DerechoStatusSchema,

    CasperStatusSchema, CasperNodeTypeSchema,
    JupyterHubStatusSchema,
    LoginNodeSchema,
    QueueSchema,
    FilesystemSchema,
    SystemOutageSchema, ResourceReservationSchema,
)

bp = Blueprint('api_status', __name__)
register_error_handlers(bp)


# ============================================================================
# Helper Functions
# ============================================================================

def _validate_timestamp(data):
    """
    Validate and parse timestamp from request data.

    Args:
        data: Request data dict

    Returns:
        datetime object or None if not provided (will use current time)

    Raises:
        ValueError if timestamp format is invalid
    """
    timestamp_str = data.get('timestamp')
    if not timestamp_str:
        return datetime.now()

    try:
        # Try ISO format first
        return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        try:
            # Try common datetime formats
            return datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            raise ValueError(f"Invalid timestamp format: {timestamp_str}. Use ISO format or 'YYYY-MM-DD HH:MM:SS'")


def _handle_reservations(reservations_data, system_name):
    """
    Upsert reservations for a given system.

    Args:
        reservations_data: List of reservation dicts from the request
        system_name: Name of the system ('derecho' or 'casper')

    Returns:
        A list of upserted reservation IDs.
    """
    from sqlalchemy import and_

    reservation_ids = []
    for resv_data in reservations_data:
        # Upsert logic: check if reservation exists
        existing = db.session.query(ResourceReservation).filter(
            and_(
                ResourceReservation.system_name == system_name,
                ResourceReservation.reservation_name == resv_data['reservation_name']
            )
        ).first()

        if existing:
            # Update existing reservation
            existing.description = resv_data.get('description')
            existing.start_time = datetime.fromisoformat(resv_data['start_time'])
            existing.end_time = datetime.fromisoformat(resv_data['end_time'])
            existing.node_count = resv_data.get('node_count')
            existing.partition = resv_data.get('partition')
            existing.updated_at = datetime.now()
            reservation_ids.append(existing.reservation_id)
        else:
            # Insert new reservation
            resv = ResourceReservation(
                system_name=system_name,
                reservation_name=resv_data['reservation_name'],
                description=resv_data.get('description'),
                start_time=datetime.fromisoformat(resv_data['start_time']),
                end_time=datetime.fromisoformat(resv_data['end_time']),
                node_count=resv_data.get('node_count'),
                partition=resv_data.get('partition'),
            )
            db.session.add(resv)
            db.session.flush()
            reservation_ids.append(resv.reservation_id)
    return reservation_ids


def _ingest_system_status(system_name, StatusSchema, id_mappers):
    """
    Generic helper to ingest system status for Derecho and Casper.

    Args:
        system_name (str): The name of the system (e.g., 'derecho').
        StatusSchema (marshmallow.Schema): The schema for the system status.
        id_mappers (dict): A mapping to extract IDs from nested objects.

    Returns:
        Flask Response: JSON response with success or error.
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    try:
        timestamp = _validate_timestamp(data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    try:
        # Extract reservations before loading (handled separately due to upsert logic)
        reservations = data.pop('reservations', [])

        # Schema loads EVERYTHING - main status + all nested objects
        data['timestamp'] = timestamp
        schema = StatusSchema()
        schema.context = {'session': db.session}
        status_object = schema.load(data)

        # Add to session - all nested objects are already linked
        db.session.add(status_object)
        db.session.flush()  # Get IDs for all objects

        # Collect IDs from relationships for response
        result = {
            'success': True,
            'message': f'{system_name.capitalize()} status ingested successfully',
            'status_id': status_object.status_id,
            'timestamp': timestamp.isoformat(),
        }
        for result_key, (object_list_attr, id_attr) in id_mappers.items():
            if hasattr(status_object, object_list_attr):
                result[result_key] = [getattr(obj, id_attr) for obj in getattr(status_object, object_list_attr)]

        # Handle reservation status if provided
        if reservations:
            result['reservation_ids'] = _handle_reservations(reservations, system_name)

        db.session.commit()
        return jsonify(result), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Database error: {str(e)}'}), 500


# ============================================================================
# POST Endpoints - Data Ingestion
# ============================================================================

@bp.route('/derecho', methods=['POST'])
@api_key_required
def ingest_derecho():
    """
    POST /api/v1/status/derecho - Ingest Derecho system metrics.

    Requires MANAGE_SYSTEM_STATUS permission.

    JSON body should contain:
        - timestamp (optional): ISO format or 'YYYY-MM-DD HH:MM:SS', defaults to now
        - System-level metrics (cpu_nodes_total, cpu_nodes_available, etc.)
        - login_nodes (optional): List of login node status dicts
        - queues (optional): List of queue status dicts
        - filesystems (optional): List of filesystem status dicts
        - reservations (optional): List of reservation dicts

    Returns:
        JSON with success status and created record IDs
    """
    id_mappers = {
        'login_node_ids': ('login_nodes', 'login_node_id'),
        'queue_ids': ('queues', 'queue_status_id'),
        'filesystem_ids': ('filesystems', 'fs_status_id'),
    }
    return _ingest_system_status(
        system_name='derecho',
        StatusSchema=DerechoStatusSchema,
        id_mappers=id_mappers
    )


@bp.route('/casper', methods=['POST'])
@api_key_required
def ingest_casper():
    """
    POST /api/v1/status/casper - Ingest Casper system metrics.

    Requires MANAGE_SYSTEM_STATUS permission.

    JSON body should contain:
        - timestamp (optional): ISO format or 'YYYY-MM-DD HH:MM:SS', defaults to now
        - Aggregate system metrics
        - login_nodes (optional): List of login node status dicts
        - node_types (optional): List of node type status dicts
        - queues (optional): List of queue status dicts
        - reservations (optional): List of reservation dicts

    Returns:
        JSON with success status and created record IDs
    """
    id_mappers = {
        'login_node_ids': ('login_nodes', 'login_node_id'),
        'node_type_ids': ('node_types', 'node_type_status_id'),
        'queue_ids': ('queues', 'queue_status_id'),
        'filesystem_ids': ('filesystems', 'fs_status_id'),
    }
    return _ingest_system_status(
        system_name='casper',
        StatusSchema=CasperStatusSchema,
        id_mappers=id_mappers
    )


@bp.route('/jupyterhub', methods=['POST'])
@api_key_required
def ingest_jupyterhub():
    """
    POST /api/v1/status/jupyterhub - Ingest JupyterHub metrics.

    Requires MANAGE_SYSTEM_STATUS permission.

    JSON body should contain:
        - timestamp (optional): ISO format or 'YYYY-MM-DD HH:MM:SS', defaults to now
        - Basic JupyterHub metrics

    Returns:
        JSON with success status and created record ID
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    try:
        timestamp = _validate_timestamp(data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    try:
        # Create main status record using schema
        data['timestamp'] = timestamp
        jupyterhub_status = JupyterHubStatusSchema().load(data, session=db.session)
        db.session.add(jupyterhub_status)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'JupyterHub status ingested successfully',
            'status_id': jupyterhub_status.status_id,
            'timestamp': timestamp.isoformat()
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Database error: {str(e)}'}), 500


@bp.route('/outage', methods=['POST'])
@api_key_required
def report_outage():
    """
    POST /api/v1/status/outage - Report a system outage or degradation.

    Requires MANAGE_SYSTEM_STATUS permission.

    JSON body should contain:
        - system_name (required): System identifier (e.g., 'derecho', 'casper')
        - title (required): Brief outage title
        - severity (required): 'critical', 'major', 'minor', 'maintenance'
        - description (optional): Detailed description
        - component (optional): Affected component
        - start_time (optional): ISO format, defaults to now
        - estimated_resolution (optional): ISO format

    Returns:
        JSON with success status and outage ID
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    # Validate required fields
    required = ['system_name', 'title', 'severity']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'error': f'Missing required fields: {", ".join(missing)}'}), 400

    # Validate severity
    valid_severities = ['critical', 'major', 'minor', 'maintenance']
    if data['severity'] not in valid_severities:
        return jsonify({'error': f'Invalid severity. Must be one of: {", ".join(valid_severities)}'}), 400

    try:
        # Parse timestamps
        start_time = datetime.now()
        if data.get('start_time'):
            try:
                start_time = datetime.fromisoformat(data['start_time'].replace('Z', '+00:00'))
            except ValueError:
                return jsonify({'error': 'Invalid start_time format'}), 400

        estimated_resolution = None
        if data.get('estimated_resolution'):
            try:
                estimated_resolution = datetime.fromisoformat(data['estimated_resolution'].replace('Z', '+00:00'))
            except ValueError:
                return jsonify({'error': 'Invalid estimated_resolution format'}), 400

        # Create outage record using schema
        data['start_time'] = start_time
        data['estimated_resolution'] = estimated_resolution
        outage = SystemOutageSchema().load(data, session=db.session)
        db.session.add(outage)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Outage reported successfully',
            'outage_id': outage.outage_id,
            'system_name': outage.system_name,
            'severity': outage.severity
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Database error: {str(e)}'}), 500


# ============================================================================
# GET Endpoints - Status Retrieval
# ============================================================================

@bp.route('/derecho/latest', methods=['GET'])
@login_required
def get_derecho_latest():
    """
    GET /api/v1/status/derecho/latest - Get latest Derecho status.

    Returns:
        JSON with latest Derecho system status including login nodes, queues, and filesystems.

    Nested objects are automatically loaded via ORM relationships with eager loading.
    """
    # Get latest main status
    status = db.session.query(DerechoStatus).order_by(
        DerechoStatus.timestamp.desc()
    ).first()

    if not status:
        return jsonify({'message': 'No Derecho status data available'}), 404

    # Schema automatically includes nested objects via ORM relationships!
    # (login_nodes, queues, filesystems are eager-loaded via lazy='selectin')
    result = DerechoStatusSchema().dump(status)

    return jsonify(result), 200


@bp.route('/casper/latest', methods=['GET'])
@login_required
def get_casper_latest():
    """
    GET /api/v1/status/casper/latest - Get latest Casper status.

    Returns:
        JSON with latest Casper system status including login nodes, node types, and queues.

    Nested objects are automatically loaded via ORM relationships with eager loading.
    """
    # Get latest main status
    status = db.session.query(CasperStatus).order_by(
        CasperStatus.timestamp.desc()
    ).first()

    if not status:
        return jsonify({'message': 'No Casper status data available'}), 404

    # Schema automatically includes nested objects via ORM relationships!
    # (login_nodes, node_types, queues, filesystems are eager-loaded via lazy='selectin')
    result = CasperStatusSchema().dump(status)

    return jsonify(result), 200


@bp.route('/jupyterhub/latest', methods=['GET'])
@login_required
def get_jupyterhub_latest():
    """
    GET /api/v1/status/jupyterhub/latest - Get latest JupyterHub status.

    Returns:
        JSON with latest JupyterHub status
    """
    status = db.session.query(JupyterHubStatus).order_by(
        JupyterHubStatus.timestamp.desc()
    ).first()

    if not status:
        return jsonify({'message': 'No JupyterHub status data available'}), 404

    # Serialize with marshmallow schema
    result = JupyterHubStatusSchema().dump(status)

    return jsonify(result), 200


@bp.route('/outages', methods=['GET'])
@login_required
def get_outages():
    """
    GET /api/v1/status/outages - Get active system outages.

    Query params:
        - system_name (optional): Filter by system
        - status (optional): Filter by status (investigating, identified, monitoring, resolved)
        - include_resolved (optional): Include resolved outages (default: false)

    Returns:
        JSON list of outages
    """
    query = db.session.query(SystemOutage)

    # Filter by system
    system_name = request.args.get('system_name')
    if system_name:
        query = query.filter(SystemOutage.system_name == system_name)

    # Filter by status
    status_filter = request.args.get('status')
    if status_filter:
        query = query.filter(SystemOutage.status == status_filter)
    elif not request.args.get('include_resolved', '').lower() in ('true', '1', 'yes'):
        # Exclude resolved unless explicitly requested
        query = query.filter(SystemOutage.status != 'resolved')

    # Order by most recent first
    outages = query.order_by(SystemOutage.start_time.desc()).all()

    # Serialize with marshmallow schema
    result = SystemOutageSchema(many=True).dump(outages)

    return jsonify(result), 200


@bp.route('/outages/<int:outage_id>', methods=['PATCH'])
@login_required
@require_permission(Permission.EDIT_SYSTEM_STATUS)
def update_outage(outage_id):
    """
    PATCH /api/v1/status/outages/<outage_id> - Update an existing outage.

    Requires EDIT_SYSTEM_STATUS permission.

    JSON body may include any of:
        - status: 'investigating', 'identified', 'monitoring', 'resolved'
        - severity: 'critical', 'major', 'minor', 'maintenance'
        - title: string
        - description: string or null
        - estimated_resolution: ISO datetime string or null/empty

    Returns:
        JSON with success status and updated outage_id/status
    """
    outage = db.session.query(SystemOutage).get(outage_id)
    if not outage:
        return jsonify({'error': 'Not found'}), 404

    data = request.get_json() or {}
    valid_statuses = ['investigating', 'identified', 'monitoring', 'resolved']
    valid_severities = ['critical', 'major', 'minor', 'maintenance']

    if 'status' in data and data['status'] in valid_statuses:
        outage.status = data['status']
    if 'severity' in data and data['severity'] in valid_severities:
        outage.severity = data['severity']
    if 'title' in data:
        outage.title = data['title']
    if 'description' in data:
        outage.description = data['description'] or None
    if 'estimated_resolution' in data:
        val = data['estimated_resolution']
        if val:
            try:
                outage.estimated_resolution = datetime.fromisoformat(val.replace('Z', '+00:00'))
            except ValueError:
                return jsonify({'error': 'Invalid estimated_resolution format'}), 400
        else:
            outage.estimated_resolution = None

    outage.updated_at = datetime.now()
    db.session.commit()
    return jsonify({'success': True, 'outage_id': outage_id, 'status': outage.status}), 200


@bp.route('/outages/<int:outage_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.EDIT_SYSTEM_STATUS)
def delete_outage(outage_id):
    """
    DELETE /api/v1/status/outages/<outage_id> - Delete an outage record.

    Requires EDIT_SYSTEM_STATUS permission.

    Returns:
        JSON with success status
    """
    outage = db.session.query(SystemOutage).get(outage_id)
    if not outage:
        return jsonify({'error': 'Not found'}), 404

    db.session.delete(outage)
    db.session.commit()
    return jsonify({'success': True}), 200


@bp.route('/reservations', methods=['GET'])
@login_required
def get_reservations():
    """
    GET /api/v1/status/reservations - Get upcoming resource reservations.

    Query params:
        - system_name (optional): Filter by system
        - upcoming_only (optional): Only future reservations (default: true)

    Returns:
        JSON list of reservations
    """
    query = db.session.query(ResourceReservation)

    # Filter by system
    system_name = request.args.get('system_name')
    if system_name:
        query = query.filter(ResourceReservation.system_name == system_name)

    # Filter by upcoming
    if request.args.get('upcoming_only', 'true').lower() in ('true', '1', 'yes'):
        query = query.filter(ResourceReservation.end_time >= datetime.now())

    # Exclude stale reservations (not reported by collector in last 30 minutes).
    # Uses COALESCE(updated_at, created_at) so newly-inserted records (updated_at=NULL)
    # are not incorrectly filtered out.
    from sqlalchemy import func
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(minutes=30)
    last_seen = func.coalesce(ResourceReservation.updated_at, ResourceReservation.created_at)
    query = query.filter(last_seen >= cutoff)

    # Order by start time
    reservations = query.order_by(ResourceReservation.start_time).all()

    # Serialize with marshmallow schema
    result = ResourceReservationSchema(many=True).dump(reservations)

    return jsonify(result), 200
