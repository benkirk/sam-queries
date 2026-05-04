"""
System Status schemas for API serialization.

Provides marshmallow schemas for all system_status database models,
replacing manual JSON dictionary construction with type-safe serialization.

Note: These schemas use the system_status database, which is separate
from the main SAM database.

Phase 2 (PR-A): the legacy text columns ``system_name``, ``queue_name``,
``filesystem_name``, ``node_name``, ``node_type`` are no longer ORM
columns — they live in the four lookup tables (``systems``, ``queues``,
``filesystems``, ``login_nodes``) and the snapshot models expose them
through ``@property`` accessors. The schemas below declare these as
explicit Marshmallow fields so the JSON contract is unchanged. On
load, the property setter stages the string as a ``_pending_*`` instance
attribute; the ``before_flush`` listener in
``system_status.queries.lookups`` resolves the staged strings into FK
ids using ``get_or_create_*`` helpers.
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

    Note: ``timestamp`` and ``system_name`` are injected by the parent schema's
    @post_load hook, so they're dump_only during deserialization. ``node_name``
    and ``node_type`` come from the child JSON object.
    """
    class Meta(BaseSchema.Meta):
        model = LoginNodeStatus
        load_instance = True
        include_relationships = True
        exclude = ('derecho_status', 'casper_status', 'system', 'login_node_def')

    # Injected by parent schema, not provided in JSON.
    timestamp = fields.DateTime(dump_only=True)
    system_name = fields.String(dump_only=True)

    # Per-row strings — loaded into property setter, dumped via @property reader.
    node_name = fields.String(required=True)
    node_type = fields.String(required=False, load_default='cpu')

    # FK columns are resolved from name strings by the before_flush listener;
    # collectors don't send them.
    system_id = fields.Integer(dump_only=True)
    login_node_def_id = fields.Integer(dump_only=True)


class FilesystemSchema(BaseSchema):
    """
    Schema for filesystem status (common to all systems).

    Note: ``timestamp`` and ``system_name`` are injected by the parent schema.
    ``filesystem_name`` comes from the child JSON object.
    """
    class Meta(BaseSchema.Meta):
        model = FilesystemStatus
        load_instance = True
        include_relationships = True
        exclude = ('derecho_status', 'casper_status', 'system', 'filesystem')

    timestamp = fields.DateTime(dump_only=True)
    system_name = fields.String(dump_only=True)
    filesystem_name = fields.String(required=True)

    system_id = fields.Integer(dump_only=True)
    filesystem_id = fields.Integer(dump_only=True)


class QueueSchema(BaseSchema):
    """
    Schema for queue status.

    Note: ``timestamp`` and ``system_name`` are injected by the parent schema.
    ``queue_name`` comes from the child JSON object.
    """
    class Meta(BaseSchema.Meta):
        model = QueueStatus
        load_instance = True
        include_relationships = True
        exclude = ('derecho_status', 'casper_status', 'system', 'queue')

    timestamp = fields.DateTime(dump_only=True)
    system_name = fields.String(dump_only=True)
    queue_name = fields.String(required=True)

    system_id = fields.Integer(dump_only=True)
    queue_id = fields.Integer(dump_only=True)


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
    timestamp and system_name on the children for consistency. Lookup
    resolution (system_name → system_id, queue_name → queue_id, etc.) is
    handled by the ``before_flush`` listener at session.commit() time.
    """
    class Meta(BaseSchema.Meta):
        model = DerechoStatus
        load_instance = True
        include_relationships = True

    login_nodes = fields.Nested(LoginNodeSchema, many=True, required=False, load_default=[])
    queues = fields.Nested(QueueSchema, many=True, required=False, load_default=[])
    filesystems = fields.Nested(FilesystemSchema, many=True, required=False, load_default=[])

    @post_load
    def link_nested_objects(self, data, **kwargs):
        """Stage timestamp and system_name on nested objects.

        Lookup-id resolution happens later in the ``before_flush`` listener.
        """
        timestamp = data['timestamp']

        for node in data.get('login_nodes', []):
            node.timestamp = timestamp
            node.system_name = 'derecho'

        for queue in data.get('queues', []):
            queue.timestamp = timestamp
            queue.system_name = 'derecho'

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

    timestamp = fields.DateTime(dump_only=True)
    casper_status_id = fields.Integer(dump_only=True)


class CasperStatusSchema(BaseSchema):
    """
    Schema for Casper system-level status with nested objects.

    See DerechoStatusSchema for the lookup-resolution flow.
    """
    class Meta(BaseSchema.Meta):
        model = CasperStatus
        load_instance = True
        include_relationships = True

    login_nodes = fields.Nested(LoginNodeSchema, many=True, required=False, load_default=[])
    node_types = fields.Nested(CasperNodeTypeSchema, many=True, required=False, load_default=[])
    queues = fields.Nested(QueueSchema, many=True, required=False, load_default=[])
    filesystems = fields.Nested(FilesystemSchema, many=True, required=False, load_default=[])

    @post_load
    def link_nested_objects(self, data, **kwargs):
        """Stage timestamp and system_name on nested objects."""
        timestamp = data['timestamp']

        for node in data.get('login_nodes', []):
            node.timestamp = timestamp
            node.system_name = 'casper'

        for node_type in data.get('node_types', []):
            node_type.timestamp = timestamp

        for queue in data.get('queues', []):
            queue.timestamp = timestamp
            queue.system_name = 'casper'

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
    """
    class Meta(BaseSchema.Meta):
        model = JupyterHubStatus
        load_instance = True
        include_relationships = True
        exclude = ()


# ============================================================================
# Support Schemas (Outages, Reservations)
# ============================================================================

class SystemOutageSchema(BaseSchema):
    """Schema for system outages.

    The ``system_name`` field is now backed by a property on the model
    (the column was replaced by a ``system_id`` FK in Phase 2). Marshmallow
    loads the string into the property setter, which stages it for
    resolution by ``before_flush``.
    """
    class Meta(BaseSchema.Meta):
        model = SystemOutage
        load_instance = True
        include_relationships = True
        exclude = ('system',)

    system_name = fields.String(required=True)
    system_id = fields.Integer(dump_only=True)


class ResourceReservationSchema(BaseSchema):
    """Schema for resource reservations.

    Reservations are written via the ingest path's ``_handle_reservations``
    helper, which sets ``system_id`` directly. The ``system_name`` field
    is dump-only here so GET endpoints can render it.
    """
    class Meta(BaseSchema.Meta):
        model = ResourceReservation
        load_instance = True
        include_relationships = True
        exclude = ('system',)

    system_name = fields.String(dump_only=True)
    system_id = fields.Integer(dump_only=True)
