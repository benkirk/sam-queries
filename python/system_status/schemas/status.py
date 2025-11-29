"""
System Status schemas for API serialization.

Provides marshmallow schemas for all system_status database models,
replacing manual JSON dictionary construction with type-safe serialization.

Note: These schemas use the system_status database, which is separate
from the main SAM database.
"""

from marshmallow import Schema, fields
from . import BaseSchema
from system_status import *


# ============================================================================
# Common Schemas
# ============================================================================

class LoginNodeSchema(BaseSchema):
    """
    Schema for individual login nodes.

    Returns per-node status including availability, user count, and load metrics.
    """
    class Meta(BaseSchema.Meta):
        model = LoginNodeStatus
        load_instance = True
        include_relationships = True


class FilesystemSchema(BaseSchema):
    """Schema for filesystem status (common to all systems)."""
    class Meta(BaseSchema.Meta):
        model = FilesystemStatus
        load_instance = True
        include_relationships = True


class QueueSchema(BaseSchema):
    """Schema for Derecho queue status."""
    class Meta(BaseSchema.Meta):
        model = QueueStatus
        load_instance = True
        include_relationships = True


# ============================================================================
# Derecho Schemas
# ============================================================================

class DerechoStatusSchema(BaseSchema):
    """
    Schema for Derecho system-level status with nested objects.

    Main status record for Derecho HPC system including compute nodes,
    job statistics, and utilization metrics.

    Nested objects (login_nodes, queues, filesystems) are automatically
    dumped via ORM relationships, but must be manually linked during load.
    """
    class Meta(BaseSchema.Meta):
        model = DerechoStatus
        load_instance = True
        include_relationships = True

    # Nested fields for dumping only (created manually in endpoint)
    login_nodes = fields.Nested(LoginNodeSchema, many=True, dump_only=True)
    queues = fields.Nested(QueueSchema, many=True, dump_only=True)
    filesystems = fields.Nested(FilesystemSchema, many=True, dump_only=True)


# ============================================================================
# Casper Schemas
# ============================================================================

class CasperNodeTypeSchema(BaseSchema):
    """Schema for Casper node type breakdown."""
    class Meta(BaseSchema.Meta):
        model = CasperNodeTypeStatus
        load_instance = True
        include_relationships = True


class CasperStatusSchema(BaseSchema):
    """
    Schema for Casper system-level status with nested objects.

    Main status record for Casper DAV system including nodes,
    jobs, and utilization.

    Nested objects (login_nodes, node_types, queues, filesystems) are
    automatically dumped via ORM relationships, but must be manually linked during load.
    """
    class Meta(BaseSchema.Meta):
        model = CasperStatus
        load_instance = True
        include_relationships = True

    # Nested fields for dumping only (created manually in endpoint)
    login_nodes = fields.Nested(LoginNodeSchema, many=True, dump_only=True)
    node_types = fields.Nested(CasperNodeTypeSchema, many=True, dump_only=True)
    queues = fields.Nested(QueueSchema, many=True, dump_only=True)
    filesystems = fields.Nested(FilesystemSchema, many=True, dump_only=True)


# ============================================================================
# JupyterHub Schema
# ============================================================================

class JupyterHubStatusSchema(BaseSchema):
    """
    Schema for JupyterHub status.

    Status for the JupyterHub/Casper JupyterHub service.
    """
    class Meta(BaseSchema.Meta):
        model = JupyterHubStatus
        load_instance = True
        include_relationships = True


# ============================================================================
# Support Schemas (Outages, Reservations)
# ============================================================================

class SystemOutageSchema(BaseSchema):
    """Schema for system outages."""
    class Meta(BaseSchema.Meta):
        model = SystemOutage
        load_instance = True
        include_relationships = True


class ResourceReservationSchema(BaseSchema):
    """Schema for resource reservations."""
    class Meta(BaseSchema.Meta):
        model = ResourceReservation
        load_instance = True
        include_relationships = True
