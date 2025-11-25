"""
System Status schemas for API serialization.

Provides marshmallow schemas for all system_status database models,
replacing manual JSON dictionary construction with type-safe serialization.

Note: These schemas use the system_status database, which is separate
from the main SAM database. We don't use the standard BaseSchema since
these models don't use db.session.

Usage:
    from webui.schemas.status import DerechoStatusSchema

    # Serialize status object
    status_data = DerechoStatusSchema().dump(status)

    # Serialize with login nodes
    from system_status import get_session
    session = get_session()
    login_nodes = session.query(DerechoLoginNodeStatus).filter_by(
        timestamp=status.timestamp
    ).all()
    status_data = DerechoStatusSchema().dump(status)
    status_data['login_nodes'] = DerechoLoginNodeSchema(many=True).dump(login_nodes)
"""

from marshmallow import Schema, fields


# ============================================================================
# Login Node Schemas
# ============================================================================

class DerechoLoginNodeSchema(Schema):
    """
    Schema for individual Derecho login nodes.

    Returns per-node status including availability, user count, and load metrics.
    """
    login_node_id = fields.Int()
    node_name = fields.Str()
    node_type = fields.Str()  # 'cpu' or 'gpu'
    available = fields.Bool()
    degraded = fields.Bool()
    user_count = fields.Int(allow_none=True)
    load_1min = fields.Float(allow_none=True)
    load_5min = fields.Float(allow_none=True)
    load_15min = fields.Float(allow_none=True)
    timestamp = fields.DateTime()


class CasperLoginNodeSchema(Schema):
    """
    Schema for individual Casper login nodes.

    Returns per-node status including availability, user count, and load metrics.
    """
    login_node_id = fields.Int()
    node_name = fields.Str()
    available = fields.Bool()
    degraded = fields.Bool()
    user_count = fields.Int(allow_none=True)
    load_1min = fields.Float(allow_none=True)
    load_5min = fields.Float(allow_none=True)
    load_15min = fields.Float(allow_none=True)
    timestamp = fields.DateTime()


# ============================================================================
# Derecho Schemas
# ============================================================================

class DerechoFilesystemSchema(Schema):
    """Schema for Derecho filesystem status."""
    fs_status_id = fields.Int()
    filesystem_name = fields.Str()
    available = fields.Bool()
    degraded = fields.Bool()
    capacity_tb = fields.Float(allow_none=True)
    used_tb = fields.Float(allow_none=True)
    utilization_percent = fields.Float(allow_none=True)
    timestamp = fields.DateTime()


class DerechoQueueSchema(Schema):
    """Schema for Derecho queue status."""
    queue_status_id = fields.Int()
    queue_name = fields.Str()
    running_jobs = fields.Int(allow_none=True)
    pending_jobs = fields.Int(allow_none=True)
    active_users = fields.Int(allow_none=True)
    cores_allocated = fields.Int(allow_none=True)
    gpus_allocated = fields.Int(allow_none=True)
    nodes_allocated = fields.Int(allow_none=True)
    timestamp = fields.DateTime()


class DerechoStatusSchema(Schema):
    """
    Schema for Derecho system-level status.

    Main status record for Derecho HPC system including compute nodes,
    job statistics, and utilization metrics.
    """
    status_id = fields.Int()
    timestamp = fields.DateTime()

    # CPU Compute Nodes
    cpu_nodes_total = fields.Int(allow_none=True)
    cpu_nodes_available = fields.Int(allow_none=True)
    cpu_nodes_down = fields.Int(allow_none=True)
    cpu_nodes_reserved = fields.Int(allow_none=True)

    # GPU Compute Nodes
    gpu_nodes_total = fields.Int(allow_none=True)
    gpu_nodes_available = fields.Int(allow_none=True)
    gpu_nodes_down = fields.Int(allow_none=True)
    gpu_nodes_reserved = fields.Int(allow_none=True)

    # CPU Utilization
    cpu_cores_total = fields.Int(allow_none=True)
    cpu_cores_allocated = fields.Int(allow_none=True)
    cpu_cores_idle = fields.Int(allow_none=True)
    cpu_utilization_percent = fields.Float(allow_none=True)

    # GPU Utilization
    gpu_count_total = fields.Int(allow_none=True)
    gpu_count_allocated = fields.Int(allow_none=True)
    gpu_count_idle = fields.Int(allow_none=True)
    gpu_utilization_percent = fields.Float(allow_none=True)

    # Memory
    memory_total_gb = fields.Float(allow_none=True)
    memory_allocated_gb = fields.Float(allow_none=True)
    memory_utilization_percent = fields.Float(allow_none=True)

    # Jobs
    running_jobs = fields.Int(allow_none=True)
    pending_jobs = fields.Int(allow_none=True)
    active_users = fields.Int(allow_none=True)


# ============================================================================
# Casper Schemas
# ============================================================================

class CasperNodeTypeSchema(Schema):
    """Schema for Casper node type breakdown."""
    node_type_status_id = fields.Int()
    node_type = fields.Str()
    nodes_total = fields.Int(allow_none=True)
    nodes_available = fields.Int(allow_none=True)
    nodes_down = fields.Int(allow_none=True)
    nodes_allocated = fields.Int(allow_none=True)
    cores_per_node = fields.Int(allow_none=True)
    memory_gb_per_node = fields.Int(allow_none=True)
    gpu_model = fields.Str(allow_none=True)
    gpus_per_node = fields.Int(allow_none=True)
    utilization_percent = fields.Float(allow_none=True)
    timestamp = fields.DateTime()


class CasperQueueSchema(Schema):
    """Schema for Casper queue status."""
    queue_status_id = fields.Int()
    queue_name = fields.Str()
    running_jobs = fields.Int(allow_none=True)
    pending_jobs = fields.Int(allow_none=True)
    active_users = fields.Int(allow_none=True)
    cores_allocated = fields.Int(allow_none=True)
    nodes_allocated = fields.Int(allow_none=True)
    timestamp = fields.DateTime()


class CasperStatusSchema(Schema):
    """
    Schema for Casper system-level status.

    Main status record for Casper DAV system including nodes,
    jobs, and utilization.
    """
    status_id = fields.Int()
    timestamp = fields.DateTime()

    # Aggregate Node Counts
    compute_nodes_total = fields.Int(allow_none=True)
    compute_nodes_available = fields.Int(allow_none=True)
    compute_nodes_down = fields.Int(allow_none=True)

    # Utilization
    cpu_utilization_percent = fields.Float(allow_none=True)
    gpu_utilization_percent = fields.Float(allow_none=True)
    memory_utilization_percent = fields.Float(allow_none=True)

    # Jobs
    running_jobs = fields.Int(allow_none=True)
    pending_jobs = fields.Int(allow_none=True)
    active_users = fields.Int(allow_none=True)


# ============================================================================
# JupyterHub Schema
# ============================================================================

class JupyterHubStatusSchema(Schema):
    """
    Schema for JupyterHub status.

    Status for the JupyterHub/Casper JupyterHub service.
    """
    jupyterhub_id = fields.Int()
    timestamp = fields.DateTime()
    available = fields.Bool()
    degraded = fields.Bool()

    # User/Server Metrics
    active_users = fields.Int(allow_none=True)
    active_servers = fields.Int(allow_none=True)
    pending_spawns = fields.Int(allow_none=True)


# ============================================================================
# Support Schemas (Outages, Reservations)
# ============================================================================

class SystemOutageSchema(Schema):
    """Schema for system outages."""
    outage_id = fields.Int()
    system_name = fields.Str()
    outage_type = fields.Str()  # 'full', 'partial', 'degraded'
    start_time = fields.DateTime()
    end_time = fields.DateTime(allow_none=True)
    description = fields.Str(allow_none=True)
    impact = fields.Str(allow_none=True)
    status = fields.Str()  # 'active', 'scheduled', 'resolved'
    created_at = fields.DateTime()
    updated_at = fields.DateTime()


class ResourceReservationSchema(Schema):
    """Schema for resource reservations."""
    reservation_id = fields.Int()
    resource_name = fields.Str()
    reservation_name = fields.Str()
    start_time = fields.DateTime()
    end_time = fields.DateTime()
    nodes_reserved = fields.Int(allow_none=True)
    description = fields.Str(allow_none=True)
    created_at = fields.DateTime()
