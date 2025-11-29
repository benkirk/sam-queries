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
from webui.utils.rbac import require_permission, Permission
from webui.api.helpers import register_error_handlers
from webui.extensions import db
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


# ============================================================================
# POST Endpoints - Data Ingestion
# ============================================================================

@bp.route('/derecho', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_SYSTEM_STATUS)
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
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    try:
        timestamp = _validate_timestamp(data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    try:
        # Schema loads main status only (nested objects created manually below)
        data['timestamp'] = timestamp
        schema = DerechoStatusSchema()
        schema.context = {'session': db.session}

        # Extract nested data before loading
        login_nodes_data = data.pop('login_nodes', [])
        queues_data = data.pop('queues', [])
        filesystems_data = data.pop('filesystems', [])

        # Load main status record
        derecho_status = schema.load(data)

        # Manually create and link nested objects via relationships (FK set automatically)
        for node_dict in login_nodes_data:
            login_node = LoginNodeStatus(
                timestamp=timestamp,
                node_name=node_dict['node_name'],
                node_type=node_dict.get('node_type', 'cpu'),
                system_name='derecho',
                available=node_dict['available'],
                degraded=node_dict.get('degraded', False),
                user_count=node_dict.get('user_count'),
                load_1min=node_dict.get('load_1min'),
                load_5min=node_dict.get('load_5min'),
                load_15min=node_dict.get('load_15min'),
            )
            derecho_status.login_nodes.append(login_node)

        for queue_dict in queues_data:
            queue = QueueStatus(
                timestamp=timestamp,
                queue_name=queue_dict['queue_name'],
                system_name='derecho',
                running_jobs=queue_dict.get('running_jobs', 0),
                pending_jobs=queue_dict.get('pending_jobs', 0),
                held_jobs=queue_dict.get('held_jobs', 0),
                active_users=queue_dict.get('active_users', 0),
                cores_allocated=queue_dict.get('cores_allocated', 0),
                gpus_allocated=queue_dict.get('gpus_allocated', 0),
                nodes_allocated=queue_dict.get('nodes_allocated', 0),
            )
            derecho_status.queues.append(queue)

        for fs_dict in filesystems_data:
            filesystem = FilesystemStatus(
                timestamp=timestamp,
                filesystem_name=fs_dict['filesystem_name'],
                system_name='derecho',
                available=fs_dict['available'],
                degraded=fs_dict.get('degraded', False),
                capacity_tb=fs_dict.get('capacity_tb'),
                used_tb=fs_dict.get('used_tb'),
                utilization_percent=fs_dict.get('utilization_percent'),
            )
            derecho_status.filesystems.append(filesystem)

        # Add parent to session (cascades to all children automatically via relationships)
        db.session.add(derecho_status)
        db.session.flush()  # Get IDs for all objects

        # Collect IDs from relationships for response
        result = {
            'success': True,
            'message': 'Derecho status ingested successfully',
            'status_id': derecho_status.status_id,
            'timestamp': timestamp.isoformat(),
            'login_node_ids': [n.login_node_id for n in derecho_status.login_nodes],
            'queue_ids': [q.queue_status_id for q in derecho_status.queues],
            'filesystem_ids': [f.fs_status_id for f in derecho_status.filesystems],
        }

        # Handle reservation status if provided
        reservations = data.get('reservations', [])
        if reservations:
            from sqlalchemy import and_

            reservation_ids = []
            for resv_data in reservations:
                # Upsert logic: check if reservation exists
                existing = db.session.query(ResourceReservation).filter(
                    and_(
                        ResourceReservation.system_name == 'derecho',
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
                        system_name='derecho',
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

            result['reservation_ids'] = reservation_ids

        db.session.commit()
        return jsonify(result), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Database error: {str(e)}'}), 500


@bp.route('/casper', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_SYSTEM_STATUS)
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
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    try:
        timestamp = _validate_timestamp(data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    try:
        # Schema loads main status only (nested objects created manually below)
        data['timestamp'] = timestamp
        schema = CasperStatusSchema()
        schema.context = {'session': db.session}

        # Extract nested data before loading
        login_nodes_data = data.pop('login_nodes', [])
        node_types_data = data.pop('node_types', [])
        queues_data = data.pop('queues', [])
        filesystems_data = data.pop('filesystems', [])

        # Load main status record
        casper_status = schema.load(data)

        # Manually create and link nested objects via relationships (FK set automatically)
        for node_dict in login_nodes_data:
            login_node = LoginNodeStatus(
                timestamp=timestamp,
                node_name=node_dict['node_name'],
                node_type=node_dict.get('node_type', 'cpu'),
                system_name='casper',
                available=node_dict['available'],
                degraded=node_dict.get('degraded', False),
                user_count=node_dict.get('user_count'),
                load_1min=node_dict.get('load_1min'),
                load_5min=node_dict.get('load_5min'),
                load_15min=node_dict.get('load_15min'),
            )
            casper_status.login_nodes.append(login_node)

        for nt_dict in node_types_data:
            node_type = CasperNodeTypeStatus(
                timestamp=timestamp,
                node_type=nt_dict['node_type'],
                nodes_total=nt_dict['nodes_total'],
                nodes_available=nt_dict['nodes_available'],
                nodes_down=nt_dict.get('nodes_down', 0),
                nodes_allocated=nt_dict.get('nodes_allocated', 0),
                cores_per_node=nt_dict.get('cores_per_node'),
                memory_gb_per_node=nt_dict.get('memory_gb_per_node'),
                gpu_model=nt_dict.get('gpu_model'),
                gpus_per_node=nt_dict.get('gpus_per_node', 0),
                utilization_percent=nt_dict.get('utilization_percent'),
                memory_utilization_percent=nt_dict.get('memory_utilization_percent'),
            )
            casper_status.node_types.append(node_type)

        for queue_dict in queues_data:
            queue = QueueStatus(
                timestamp=timestamp,
                queue_name=queue_dict['queue_name'],
                system_name='casper',
                running_jobs=queue_dict.get('running_jobs', 0),
                pending_jobs=queue_dict.get('pending_jobs', 0),
                held_jobs=queue_dict.get('held_jobs', 0),
                active_users=queue_dict.get('active_users', 0),
                cores_allocated=queue_dict.get('cores_allocated', 0),
                nodes_allocated=queue_dict.get('nodes_allocated', 0),
            )
            casper_status.queues.append(queue)

        for fs_dict in filesystems_data:
            filesystem = FilesystemStatus(
                timestamp=timestamp,
                filesystem_name=fs_dict['filesystem_name'],
                system_name='casper',
                available=fs_dict['available'],
                degraded=fs_dict.get('degraded', False),
                capacity_tb=fs_dict.get('capacity_tb'),
                used_tb=fs_dict.get('used_tb'),
                utilization_percent=fs_dict.get('utilization_percent'),
            )
            casper_status.filesystems.append(filesystem)

        # Add parent to session (cascades to all children automatically via relationships)
        db.session.add(casper_status)
        db.session.flush()  # Get IDs for all objects

        # Collect IDs from relationships for response
        result = {
            'success': True,
            'message': 'Casper status ingested successfully',
            'status_id': casper_status.status_id,
            'timestamp': timestamp.isoformat(),
            'login_node_ids': [n.login_node_id for n in casper_status.login_nodes],
            'node_type_ids': [nt.node_type_status_id for nt in casper_status.node_types],
            'queue_ids': [q.queue_status_id for q in casper_status.queues],
            'filesystem_ids': [f.fs_status_id for f in casper_status.filesystems],
        }

        # Handle reservation status if provided
        reservations = data.get('reservations', [])
        if reservations:
            from sqlalchemy import and_

            reservation_ids = []
            for resv_data in reservations:
                # Upsert logic: check if reservation exists
                existing = db.session.query(ResourceReservation).filter(
                    and_(
                        ResourceReservation.system_name == 'casper',
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
                        system_name='casper',
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

            result['reservation_ids'] = reservation_ids

        db.session.commit()
        return jsonify(result), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Database error: {str(e)}'}), 500


@bp.route('/jupyterhub', methods=['POST'])
@login_required
@require_permission(Permission.MANAGE_SYSTEM_STATUS)
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
@login_required
@require_permission(Permission.MANAGE_SYSTEM_STATUS)
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

    # Order by start time
    reservations = query.order_by(ResourceReservation.start_time).all()

    # Serialize with marshmallow schema
    result = ResourceReservationSchema(many=True).dump(reservations)

    return jsonify(result), 200
