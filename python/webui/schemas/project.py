"""
Project schemas for API serialization.

Provides three levels of project serialization:
- ProjectSchema: Full project details with nested relationships
- ProjectListSchema: Lightweight for list endpoints (no deep nesting)
- ProjectSummarySchema: Minimal fields for nested references

Usage:
    from webui.schemas import ProjectSchema, ProjectListSchema, ProjectSummarySchema

    # Full project details
    project_data = ProjectSchema().dump(project)

    # List of projects (lightweight)
    projects_data = ProjectListSchema(many=True).dump(projects)

    # Nested reference (minimal)
    summary_data = ProjectSummarySchema().dump(project)
"""

from marshmallow import fields
from . import BaseSchema
from .user import UserSummarySchema
from sam.projects.projects import Project


class ProjectSummarySchema(BaseSchema):
    """
    Minimal project schema for nested references.

    Used when a project is referenced from another object.
    Only includes essential identification fields.
    """
    class Meta(BaseSchema.Meta):
        model = Project
        fields = ('project_id', 'projcode', 'title', 'active')


class ProjectListSchema(BaseSchema):
    """
    Lightweight project schema for list endpoints.

    Includes key project fields with minimal nesting.
    Used for /api/v1/projects/ list endpoint.
    """
    class Meta(BaseSchema.Meta):
        model = Project
        fields = (
            'project_id',
            'projcode',
            'title',
            'lead_username',
            'admin_username',
            'active',
            'charging_exempt',
            'area_of_interest',
        )

    # Custom fields for lead/admin (just usernames, not full nested objects)
    lead_username = fields.Method('get_lead_username')
    admin_username = fields.Method('get_admin_username')
    area_of_interest = fields.Method('get_area_of_interest')

    def get_lead_username(self, obj):
        """Get lead username."""
        return obj.lead.username if obj.lead else None

    def get_admin_username(self, obj):
        """Get admin username."""
        return obj.admin.username if obj.admin else None

    def get_area_of_interest(self, obj):
        """Get area of interest name."""
        return obj.area_of_interest.area_of_interest if obj.area_of_interest else None


class ProjectSchema(BaseSchema):
    """
    Full project schema with nested relationships.

    Includes all project details plus related lead, admin, area of interest.
    Used for /api/v1/projects/<projcode> detail endpoint.
    """
    class Meta(BaseSchema.Meta):
        model = Project
        fields = (
            'project_id',
            'projcode',
            'title',
            'abstract',
            'lead',
            'admin',
            'active',
            'charging_exempt',
            'area_of_interest',
            'creation_time',
            'modified_time',
        )

    # Nested user objects
    lead = fields.Nested(UserSummarySchema)
    admin = fields.Nested(UserSummarySchema)
    area_of_interest = fields.Method('get_area_of_interest')

    def get_area_of_interest(self, obj):
        """Get area of interest name."""
        return obj.area_of_interest.area_of_interest if obj.area_of_interest else None
