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
from datetime import datetime
import sys
from pathlib import Path

# Add system_status to path
python_dir = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(python_dir))

from system_status import (
    create_status_engine, get_session,
    DerechoStatus, DerechoQueueStatus,
    DerechoLoginNodeStatus,
    CasperStatus, CasperNodeTypeStatus, CasperQueueStatus,
    CasperLoginNodeStatus,
    JupyterHubStatus,
    LoginNodeStatus,
    QueueStatus,
    FilesystemStatus,
    SystemOutage, ResourceReservation
)
from system_status.schemas.status import (
    DerechoStatusSchema, DerechoQueueSchema, FilesystemSchema,
    DerechoLoginNodeSchema,
    CasperStatusSchema, CasperNodeTypeSchema, CasperQueueSchema,
    CasperLoginNodeSchema,
    JupyterHubStatusSchema,
    LoginNodeSchema,
    QueueSchema,
    SystemOutageSchema, ResourceReservationSchema,
)

bp = Blueprint('api_status', __name__)
register_error_handlers(bp)


# ============================================================================
# Helper Functions
# ============================================================================

def _get_status_session():
    """Get a system_status database session."""
    engine, SessionLocal = create_status_engine()
    return SessionLocal()


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

    session = _get_status_session()
    try:
        # Create main status record
        derecho_status = DerechoStatus(
            timestamp=timestamp,
            # Compute nodes - CPU
            cpu_nodes_total=data.get('cpu_nodes_total', 0),
            cpu_nodes_available=data.get('cpu_nodes_available', 0),
            cpu_nodes_down=data.get('cpu_nodes_down', 0),
            cpu_nodes_reserved=data.get('cpu_nodes_reserved', 0),
            # Compute nodes - GPU
            gpu_nodes_total=data.get('gpu_nodes_total', 0),
            gpu_nodes_available=data.get('gpu_nodes_available', 0),
            gpu_nodes_down=data.get('gpu_nodes_down', 0),
            gpu_nodes_reserved=data.get('gpu_nodes_reserved', 0),
            # CPU utilization
            cpu_cores_total=data.get('cpu_cores_total', 0),
            cpu_cores_allocated=data.get('cpu_cores_allocated', 0),
            cpu_cores_idle=data.get('cpu_cores_idle', 0),
            cpu_utilization_percent=data.get('cpu_utilization_percent'),
            # GPU utilization
            gpu_count_total=data.get('gpu_count_total', 0),
            gpu_count_allocated=data.get('gpu_count_allocated', 0),
            gpu_count_idle=data.get('gpu_count_idle', 0),
            gpu_utilization_percent=data.get('gpu_utilization_percent'),
            # Memory
            memory_total_gb=data.get('memory_total_gb', 0.0),
            memory_allocated_gb=data.get('memory_allocated_gb', 0.0),
            memory_utilization_percent=data.get('memory_utilization_percent'),
            # Jobs
            running_jobs=data.get('running_jobs', 0),
            pending_jobs=data.get('pending_jobs', 0),
            held_jobs=data.get('held_jobs', 0),
            active_users=data.get('active_users', 0),
        )
        session.add(derecho_status)
        session.flush()  # Get the ID

        result = {
            'success': True,
            'message': 'Derecho status ingested successfully',
            'status_id': derecho_status.status_id,
            'timestamp': timestamp.isoformat()
        }

        # Handle login nodes if provided
        login_nodes = data.get('login_nodes', [])
        if login_nodes:
            login_node_ids = []
            for node_data in login_nodes:
                login_node = LoginNodeStatus(
                    timestamp=timestamp,
                    node_name=node_data['node_name'],
                    node_type=node_data.get('node_type', 'cpu'),
                    system_name='derecho',
                    available=node_data.get('available', True),
                    degraded=node_data.get('degraded', False),
                    user_count=node_data.get('user_count'),
                    load_1min=node_data.get('load_1min'),
                    load_5min=node_data.get('load_5min'),
                    load_15min=node_data.get('load_15min'),
                )
                session.add(login_node)
                session.flush()
                login_node_ids.append(login_node.login_node_id)
            result['login_node_ids'] = login_node_ids

        # Handle queue status if provided
        queues = data.get('queues', [])
        if queues:
            queue_ids = []
            for queue_data in queues:
                queue_status = QueueStatus(
                    timestamp=timestamp,
                    queue_name=queue_data['queue_name'],
                    system_name='derecho',
                    running_jobs=queue_data.get('running_jobs', 0),
                    pending_jobs=queue_data.get('pending_jobs', 0),
                    held_jobs=queue_data.get('held_jobs', 0),
                    active_users=queue_data.get('active_users', 0),
                    cores_allocated=queue_data.get('cores_allocated', 0),
                    gpus_allocated=queue_data.get('gpus_allocated', 0),
                    nodes_allocated=queue_data.get('nodes_allocated', 0),
                )
                session.add(queue_status)
                session.flush()
                queue_ids.append(queue_status.queue_status_id)
            result['queue_ids'] = queue_ids

        # Handle filesystem status if provided
        filesystems = data.get('filesystems', [])
        if filesystems:
            fs_ids = []
            for fs_data in filesystems:
                fs_status = FilesystemStatus(
                    timestamp=timestamp,
                    filesystem_name=fs_data['filesystem_name'],
                    system_name='derecho',  # Tag with system name
                    available=fs_data.get('available', True),
                    degraded=fs_data.get('degraded', False),
                    capacity_tb=fs_data.get('capacity_tb'),
                    used_tb=fs_data.get('used_tb'),
                    utilization_percent=fs_data.get('utilization_percent'),
                )
                session.add(fs_status)
                session.flush()
                fs_ids.append(fs_status.fs_status_id)
            result['filesystem_ids'] = fs_ids

        # Handle reservation status if provided
        reservations = data.get('reservations', [])
        if reservations:
            from sqlalchemy import and_

            reservation_ids = []
            for resv_data in reservations:
                # Upsert logic: check if reservation exists
                existing = session.query(ResourceReservation).filter(
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
                    session.add(resv)
                    session.flush()
                    reservation_ids.append(resv.reservation_id)

            result['reservation_ids'] = reservation_ids

        session.commit()
        return jsonify(result), 201

    except Exception as e:
        session.rollback()
        return jsonify({'error': f'Database error: {str(e)}'}), 500
    finally:
        session.close()


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

    session = _get_status_session()
    try:
        # Create main status record
        casper_status = CasperStatus(
            timestamp=timestamp,
            # Compute nodes - CPU
            cpu_nodes_total=data.get('cpu_nodes_total', 0),
            cpu_nodes_available=data.get('cpu_nodes_available', 0),
            cpu_nodes_down=data.get('cpu_nodes_down', 0),
            cpu_nodes_reserved=data.get('cpu_nodes_reserved', 0),
            # Compute nodes - GPU
            gpu_nodes_total=data.get('gpu_nodes_total', 0),
            gpu_nodes_available=data.get('gpu_nodes_available', 0),
            gpu_nodes_down=data.get('gpu_nodes_down', 0),
            gpu_nodes_reserved=data.get('gpu_nodes_reserved', 0),
            # Compute nodes - VIZ
            viz_nodes_total=data.get('viz_nodes_total', 0),
            viz_nodes_available=data.get('viz_nodes_available', 0),
            viz_nodes_down=data.get('viz_nodes_down', 0),
            viz_nodes_reserved=data.get('viz_nodes_reserved', 0),
            # CPU utilization
            cpu_cores_total=data.get('cpu_cores_total', 0),
            cpu_cores_allocated=data.get('cpu_cores_allocated', 0),
            cpu_cores_idle=data.get('cpu_cores_idle', 0),
            cpu_utilization_percent=data.get('cpu_utilization_percent'),
            # GPU utilization
            gpu_count_total=data.get('gpu_count_total', 0),
            gpu_count_allocated=data.get('gpu_count_allocated', 0),
            gpu_count_idle=data.get('gpu_count_idle', 0),
            gpu_utilization_percent=data.get('gpu_utilization_percent'),
            # VIZ utilization
            viz_count_total=data.get('viz_count_total', 0),
            viz_count_allocated=data.get('viz_count_allocated', 0),
            viz_count_idle=data.get('viz_count_idle', 0),
            viz_utilization_percent=data.get('viz_utilization_percent'),
            # Memory
            memory_total_gb=data.get('memory_total_gb', 0.0),
            memory_allocated_gb=data.get('memory_allocated_gb', 0.0),
            memory_utilization_percent=data.get('memory_utilization_percent'),
            # Jobs
            running_jobs=data.get('running_jobs', 0),
            pending_jobs=data.get('pending_jobs', 0),
            held_jobs=data.get('held_jobs', 0),
            active_users=data.get('active_users', 0),
        )
        session.add(casper_status)
        session.flush()

        result = {
            'success': True,
            'message': 'Casper status ingested successfully',
            'status_id': casper_status.status_id,
            'timestamp': timestamp.isoformat()
        }

        # Handle login nodes if provided
        login_nodes = data.get('login_nodes', [])
        if login_nodes:
            login_node_ids = []
            for node_data in login_nodes:
                login_node = LoginNodeStatus(
                    timestamp=timestamp,
                    node_name=node_data['node_name'],
                    node_type='cpu',
                    system_name='casper',
                    available=node_data.get('available', True),
                    degraded=node_data.get('degraded', False),
                    user_count=node_data.get('user_count'),
                    load_1min=node_data.get('load_1min'),
                    load_5min=node_data.get('load_5min'),
                    load_15min=node_data.get('load_15min'),
                )
                session.add(login_node)
                session.flush()
                login_node_ids.append(login_node.login_node_id)
            result['login_node_ids'] = login_node_ids

        # Handle node type status if provided
        node_types = data.get('node_types', [])
        if node_types:
            nodetype_ids = []
            for nt_data in node_types:
                nt_status = CasperNodeTypeStatus(
                    timestamp=timestamp,
                    node_type=nt_data['node_type'],
                    nodes_total=nt_data.get('nodes_total', 0),
                    nodes_available=nt_data.get('nodes_available', 0),
                    nodes_down=nt_data.get('nodes_down', 0),
                    nodes_allocated=nt_data.get('nodes_allocated', 0),
                    cores_per_node=nt_data.get('cores_per_node'),
                    memory_gb_per_node=nt_data.get('memory_gb_per_node'),
                    gpu_model=nt_data.get('gpu_model'),
                    gpus_per_node=nt_data.get('gpus_per_node'),
                    utilization_percent=nt_data.get('utilization_percent'),
                    memory_utilization_percent=nt_data.get('memory_utilization_percent'),
                )
                session.add(nt_status)
                session.flush()
                nodetype_ids.append(nt_status.node_type_status_id)
            result['node_type_ids'] = nodetype_ids

        # Handle queue status if provided
        queues = data.get('queues', [])
        if queues:
            queue_ids = []
            for queue_data in queues:
                queue_status = QueueStatus(
                    timestamp=timestamp,
                    queue_name=queue_data['queue_name'],
                    system_name='casper',
                    running_jobs=queue_data.get('running_jobs', 0),
                    pending_jobs=queue_data.get('pending_jobs', 0),
                    held_jobs=queue_data.get('held_jobs', 0),
                    active_users=queue_data.get('active_users', 0),
                    cores_allocated=queue_data.get('cores_allocated', 0),
                    gpus_allocated=queue_data.get('gpus_allocated', 0),
                    nodes_allocated=queue_data.get('nodes_allocated', 0),
                )
                session.add(queue_status)
                session.flush()
                queue_ids.append(queue_status.queue_status_id)
            result['queue_ids'] = queue_ids

        # Handle filesystem status if provided
        filesystems = data.get('filesystems', [])
        if filesystems:
            fs_ids = []
            for fs_data in filesystems:
                fs_status = FilesystemStatus(
                    timestamp=timestamp,
                    filesystem_name=fs_data['filesystem_name'],
                    system_name='casper',  # Tag with system name
                    available=fs_data.get('available', True),
                    degraded=fs_data.get('degraded', False),
                    capacity_tb=fs_data.get('capacity_tb'),
                    used_tb=fs_data.get('used_tb'),
                    utilization_percent=fs_data.get('utilization_percent'),
                )
                session.add(fs_status)
                session.flush()
                fs_ids.append(fs_status.fs_status_id)
            result['filesystem_ids'] = fs_ids

        # Handle reservation status if provided
        reservations = data.get('reservations', [])
        if reservations:
            from sqlalchemy import and_

            reservation_ids = []
            for resv_data in reservations:
                # Upsert logic: check if reservation exists
                existing = session.query(ResourceReservation).filter(
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
                    session.add(resv)
                    session.flush()
                    reservation_ids.append(resv.reservation_id)

            result['reservation_ids'] = reservation_ids

        session.commit()
        return jsonify(result), 201

    except Exception as e:
        session.rollback()
        return jsonify({'error': f'Database error: {str(e)}'}), 500
    finally:
        session.close()


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

    session = _get_status_session()
    try:
        jupyterhub_status = JupyterHubStatus(
            timestamp=timestamp,
            available=data.get('available', True),
            active_users=data.get('active_users', 0),
            active_sessions=data.get('active_sessions', 0),
            cpu_utilization_percent=data.get('cpu_utilization_percent'),
            memory_utilization_percent=data.get('memory_utilization_percent'),
        )
        session.add(jupyterhub_status)
        session.commit()

        return jsonify({
            'success': True,
            'message': 'JupyterHub status ingested successfully',
            'status_id': jupyterhub_status.status_id,
            'timestamp': timestamp.isoformat()
        }), 201

    except Exception as e:
        session.rollback()
        return jsonify({'error': f'Database error: {str(e)}'}), 500
    finally:
        session.close()


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

    session = _get_status_session()
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

        outage = SystemOutage(
            system_name=data['system_name'],
            title=data['title'],
            severity=data['severity'],
            status=data.get('status', 'investigating'),
            description=data.get('description'),
            component=data.get('component'),
            start_time=start_time,
            estimated_resolution=estimated_resolution,
        )
        session.add(outage)
        session.commit()

        return jsonify({
            'success': True,
            'message': 'Outage reported successfully',
            'outage_id': outage.outage_id,
            'system_name': outage.system_name,
            'severity': outage.severity
        }), 201

    except Exception as e:
        session.rollback()
        return jsonify({'error': f'Database error: {str(e)}'}), 500
    finally:
        session.close()


# ============================================================================
# GET Endpoints - Status Retrieval
# ============================================================================

@bp.route('/derecho/latest', methods=['GET'])
@login_required
def get_derecho_latest():
    """
    GET /api/v1/status/derecho/latest - Get latest Derecho status.

    Returns:
        JSON with latest Derecho system status including login nodes, queues, and filesystems
    """
    session = _get_status_session()
    try:
        # Get latest main status
        status = session.query(DerechoStatus).order_by(
            DerechoStatus.timestamp.desc()
        ).first()

        if not status:
            return jsonify({'message': 'No Derecho status data available'}), 404

        # Get login nodes for same timestamp
        login_nodes = session.query(LoginNodeStatus).filter_by(
            timestamp=status.timestamp,
            system_name='derecho'
        ).all()

        # Get queues for same timestamp
        queues = session.query(QueueStatus).filter_by(
            timestamp=status.timestamp,
            system_name='derecho'
        ).all()

        # Get filesystems for same timestamp (filter by system_name='derecho')
        filesystems = session.query(FilesystemStatus).filter_by(
            timestamp=status.timestamp,
            system_name='derecho'
        ).all()

        # Serialize with marshmallow schemas
        result = DerechoStatusSchema().dump(status)
        result['login_nodes'] = LoginNodeSchema(many=True).dump(login_nodes)
        result['queues'] = QueueSchema(many=True).dump(queues)
        result['filesystems'] = FilesystemSchema(many=True).dump(filesystems)

        return jsonify(result), 200

    finally:
        session.close()


@bp.route('/casper/latest', methods=['GET'])
@login_required
def get_casper_latest():
    """
    GET /api/v1/status/casper/latest - Get latest Casper status.

    Returns:
        JSON with latest Casper system status including login nodes, node types, and queues
    """
    session = _get_status_session()
    try:
        # Get latest main status
        status = session.query(CasperStatus).order_by(
            CasperStatus.timestamp.desc()
        ).first()

        if not status:
            return jsonify({'message': 'No Casper status data available'}), 404

        # Get login nodes for same timestamp
        login_nodes = session.query(LoginNodeStatus).filter_by(
            timestamp=status.timestamp,
            system_name='casper'
        ).all()

        # Get node types for same timestamp
        node_types = session.query(CasperNodeTypeStatus).filter_by(
            timestamp=status.timestamp
        ).all()

        # Get queues for same timestamp
        queues = session.query(QueueStatus).filter_by(
            timestamp=status.timestamp,
            system_name='casper'
        ).all()

        # Get filesystems for same timestamp (filter by system_name='casper')
        filesystems = session.query(FilesystemStatus).filter_by(
            timestamp=status.timestamp,
            system_name='casper'
        ).all()

        # Serialize with marshmallow schemas
        result = CasperStatusSchema().dump(status)
        result['login_nodes'] = LoginNodeSchema(many=True).dump(login_nodes)
        result['node_types'] = CasperNodeTypeSchema(many=True).dump(node_types)
        result['queues'] = QueueSchema(many=True).dump(queues)
        result['filesystems'] = FilesystemSchema(many=True).dump(filesystems)

        return jsonify(result), 200

    finally:
        session.close()


@bp.route('/jupyterhub/latest', methods=['GET'])
@login_required
def get_jupyterhub_latest():
    """
    GET /api/v1/status/jupyterhub/latest - Get latest JupyterHub status.

    Returns:
        JSON with latest JupyterHub status
    """
    session = _get_status_session()
    try:
        status = session.query(JupyterHubStatus).order_by(
            JupyterHubStatus.timestamp.desc()
        ).first()

        if not status:
            return jsonify({'message': 'No JupyterHub status data available'}), 404

        # Serialize with marshmallow schema
        result = JupyterHubStatusSchema().dump(status)

        return jsonify(result), 200

    finally:
        session.close()


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
    session = _get_status_session()
    try:
        query = session.query(SystemOutage)

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

    finally:
        session.close()


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
    session = _get_status_session()
    try:
        query = session.query(ResourceReservation)

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

    finally:
        session.close()
