"""
Resource schemas for API serialization.

Provides schemas for Resource and ResourceType models.

Usage:
    from webui.schemas import ResourceSchema, ResourceSummarySchema, ResourceTypeSchema

    # Full resource details
    resource_data = ResourceSchema().dump(resource)

    # Nested reference (minimal)
    summary_data = ResourceSummarySchema().dump(resource)
"""

from marshmallow import fields
from . import BaseSchema
from sam.resources.resources import Resource, ResourceType


class ResourceTypeSchema(BaseSchema):
    """
    Schema for resource types (HPC, DAV, DISK, ARCHIVE, DATA_ACCESS).
    """
    class Meta(BaseSchema.Meta):
        model = ResourceType
        fields = ('resource_type_id', 'resource_type', 'description', 'active')


class ResourceSummarySchema(BaseSchema):
    """
    Minimal resource schema for nested references.

    Used when a resource is referenced from another object (e.g., allocation).
    """
    class Meta(BaseSchema.Meta):
        model = Resource
        fields = ('resource_id', 'resource_name', 'resource_type')

    # Nested resource type (just the type string, not full object)
    resource_type = fields.Method('get_resource_type')

    def get_resource_type(self, obj):
        """Get resource type as simple string."""
        return obj.resource_type.resource_type if obj.resource_type else None


class ResourceSchema(BaseSchema):
    """
    Full resource schema with details.

    Includes resource type, commission dates, and configuration.
    """
    class Meta(BaseSchema.Meta):
        model = Resource
        fields = (
            'resource_id',
            'resource_name',
            'resource_type',
            'description',
            'activity_type',
            'charging_exempt',
            'commission_date',
            'decommission_date',
            'creation_time',
            'modified_time',
        )

    # Nested full resource type object
    resource_type = fields.Nested(ResourceTypeSchema)
