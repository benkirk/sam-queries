"""
System Status schemas for API serialization.

Provides marshmallow schemas for all system_status database models,
replacing manual JSON dictionary construction with type-safe serialization.

Note: These schemas use the system_status database, which is separate
from the main SAM database.
"""

from marshmallow import Schema, fields, post_load
from . import BaseSchema
from system_status import *


# ============================================================================
# Common Schemas
# ============================================================================

class LoginNodeSchema(BaseSchema):
    """
    Schema for individual login nodes.

    Returns per-node status including availability, user count, and load metrics.

    Note: timestamp, system_name, and node_type are injected by parent schema's
    @post_load hook, so they're dump_only during deserialization.
    """
    class Meta(BaseSchema.Meta):
        model = LoginNodeStatus
        load_instance = True
        include_relationships = True
        exclude = ('derecho_status', 'casper_status')  # Exclude back-references

    # These fields are injected by parent schema, not provided in JSON
    timestamp = fields.DateTime(dump_only=True)
    system_name = fields.String(dump_only=True)
    node_type = fields.String(required=False, load_default='cpu')  # Optional with default


class FilesystemSchema(BaseSchema):
    """
    Schema for filesystem status (common to all systems).

    Note: timestamp and system_name are injected by parent schema's @post_load hook.
    """
    class Meta(BaseSchema.Meta):
        model = FilesystemStatus
        load_instance = True
        include_relationships = True
        exclude = ('derecho_status', 'casper_status')  # Exclude back-references

    # These fields are injected by parent schema, not provided in JSON
    timestamp = fields.DateTime(dump_only=True)
    system_name = fields.String(dump_only=True)


class QueueSchema(BaseSchema):
    """
    Schema for queue status.

    Note: timestamp and system_name are injected by parent schema's @post_load hook.
    """
    class Meta(BaseSchema.Meta):
        model = QueueStatus
        load_instance = True
        include_relationships = True
        exclude = ('derecho_status', 'casper_status')  # Exclude back-references

    # These fields are injected by parent schema, not provided in JSON
    timestamp = fields.DateTime(dump_only=True)
    system_name = fields.String(dump_only=True)


# ============================================================================
# Derecho Schemas
# ============================================================================

class DerechoStatusSchema(BaseSchema):
    """
    Schema for Derecho system-level status with nested objects.

    Main status record for Derecho HPC system including compute nodes,
    job statistics, and utilization metrics.

    Nested objects (login_nodes, queues, filesystems) are automatically
    loaded and linked via ORM relationships. The post_load hook just sets
    timestamp and system_name for consistency.
    """
    class Meta(BaseSchema.Meta):
        model = DerechoStatus
        load_instance = True
        include_relationships = True

    # Nested fields for both loading and dumping
    login_nodes = fields.Nested(LoginNodeSchema, many=True, required=False, load_default=[])
    queues = fields.Nested(QueueSchema, many=True, required=False, load_default=[])
    filesystems = fields.Nested(FilesystemSchema, many=True, required=False, load_default=[])

    @post_load
    def link_nested_objects(self, data, **kwargs):
        """Set timestamp and system_name on nested objects for consistency."""
        # data is a dict at this point (before instance creation)
        # but nested items are already ORM instances
        timestamp = data['timestamp']

        # Set metadata on login nodes (FK will be set automatically by relationship)
        for node in data.get('login_nodes', []):
            node.timestamp = timestamp
            node.system_name = 'derecho'

        # Set metadata on queues (FK will be set automatically by relationship)
        for queue in data.get('queues', []):
            queue.timestamp = timestamp
            queue.system_name = 'derecho'

        # Set metadata on filesystems (FK will be set automatically by relationship)
        for fs in data.get('filesystems', []):
            fs.timestamp = timestamp
            fs.system_name = 'derecho'

        return data


# ============================================================================
# Casper Schemas
# ============================================================================

class CasperNodeTypeSchema(BaseSchema):
    """
    Schema for Casper node type breakdown.

    Note: timestamp is injected by parent schema's @post_load hook.
    Foreign key relationship (casper_status_id) is set automatically by ORM.
    """
    class Meta(BaseSchema.Meta):
        model = CasperNodeTypeStatus
        load_instance = True
        include_relationships = True
        exclude = ('casper_status',)  # Exclude back-reference to prevent circular serialization

    # These fields are injected/set automatically, not provided in JSON
    timestamp = fields.DateTime(dump_only=True)
    casper_status_id = fields.Integer(dump_only=True)


class CasperStatusSchema(BaseSchema):
    """
    Schema for Casper system-level status with nested objects.

    Main status record for Casper DAV system including nodes,
    jobs, and utilization.

    Nested objects (login_nodes, node_types, queues, filesystems) are
    automatically loaded and linked via ORM relationships. The post_load hook
    just sets timestamp and system_name for consistency.
    """
    class Meta(BaseSchema.Meta):
        model = CasperStatus
        load_instance = True
        include_relationships = True

    # Nested fields for both loading and dumping
    login_nodes = fields.Nested(LoginNodeSchema, many=True, required=False, load_default=[])
    node_types = fields.Nested(CasperNodeTypeSchema, many=True, required=False, load_default=[])
    queues = fields.Nested(QueueSchema, many=True, required=False, load_default=[])
    filesystems = fields.Nested(FilesystemSchema, many=True, required=False, load_default=[])

    @post_load
    def link_nested_objects(self, data, **kwargs):
        """Set timestamp and system_name on nested objects for consistency."""
        # data is a dict at this point (before instance creation)
        # but nested items are already ORM instances
        timestamp = data['timestamp']

        # Set metadata on login nodes (FK will be set automatically by relationship)
        for node in data.get('login_nodes', []):
            node.timestamp = timestamp
            node.system_name = 'casper'

        # Set metadata on node types (FK will be set automatically by relationship)
        for node_type in data.get('node_types', []):
            node_type.timestamp = timestamp

        # Set metadata on queues (FK will be set automatically by relationship)
        for queue in data.get('queues', []):
            queue.timestamp = timestamp
            queue.system_name = 'casper'

        # Set metadata on filesystems (FK will be set automatically by relationship)
        for fs in data.get('filesystems', []):
            fs.timestamp = timestamp
            fs.system_name = 'casper'

        return data


# ============================================================================
# JupyterHub Schema
# ============================================================================

class JupyterHubStatusSchema(BaseSchema):
    """
    Schema for JupyterHub status.

    Status for the JupyterHub/Casper JupyterHub service.

    Uses auto-generated fields from the model - no explicit field declarations needed.
    SQLAlchemyAutoSchema will generate all fields from the JupyterHubStatus model.
    """
    class Meta(BaseSchema.Meta):
        model = JupyterHubStatus
        load_instance = True
        include_relationships = False  # No relationships in JupyterHubStatus
        exclude = ()


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
