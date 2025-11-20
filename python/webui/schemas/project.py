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
        )

    # Custom fields for lead/admin (just usernames, not full nested objects)
    lead_username = fields.Method('get_lead_username')
    admin_username = fields.Method('get_admin_username')

    def get_lead_username(self, obj):
        """Get lead username."""
        return obj.lead.username if obj.lead else None

    def get_admin_username(self, obj):
        """Get admin username."""

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
            'directories',
            'charging_exempt',
            'area_of_interest',
            'project_type',
            'panel',
            'contracts',
            'creation_time',
            'modified_time',
            'breadcrumb_path',
            'tree_depth',
            'tree',
        )

    # Nested user objects
    lead = fields.Nested(UserSummarySchema)
    admin = fields.Nested(UserSummarySchema)
    directories = fields.Method('get_active_directories')
    area_of_interest = fields.Method('get_area_of_interest')
    project_type = fields.Method('get_type')
    panel = fields.Method('get_panel')
    contracts = fields.Method('get_contracts')
    breadcrumb_path = fields.Method('get_breadcrumb_path')
    tree_depth = fields.Method('get_tree_depth')
    tree = fields.Method('get_tree')

    def get_area_of_interest(self, obj):
        """Get area of interest name."""
        return obj.area_of_interest.area_of_interest if obj.area_of_interest else None

    def get_type(self,obj):
        """Get project type"""
        return obj.allocation_type.allocation_type if obj.allocation_type else None

    def get_panel(self,obj):
        """Get associated panel"""
        return obj.allocation_type.panel.panel_name if obj.allocation_type.panel else None

    def get_contracts(self,obj):
        """Get associated contracts"""
        contracts = []
        if obj.contracts:
            for pc in obj.contracts:
                contracts.append(f"{pc.contract.contract_source.contract_source} {str(pc.contract.contract_number):<20} {pc.contract.title}")
        return contracts

    def get_breadcrumb_path(self, obj):
        """
        Get list of projcodes from root to current project.

        Returns:
            List of projcodes showing path from root to this project.
            Example: ['CESM0002', 'P93300012'] for a child project
        """
        breadcrumbs = obj.get_breadcrumb_path()
        # Extract just the projcodes from breadcrumb dicts
        return [b['projcode'] for b in breadcrumbs]

    def get_tree_depth(self, obj):
        """Get depth of project in tree (0 = root)."""
        return obj.get_depth()

    def _build_tree_recursive(self, project, current_depth, max_depth):
        """
        Recursively build tree structure from a project node.

        Args:
            project: Project object to build tree from
            current_depth: Current depth in tree (0 = root)
            max_depth: Maximum depth to traverse

        Returns:
            Dict with projcode, depth, and children list
        """
        tree_node = {
            'projcode': project.projcode,
            'depth': current_depth,
        }

        # Only add children if we haven't reached max depth
        if current_depth < max_depth:
            children = project.get_children()
            if children:
                # Build nested structure for children
                tree_node['children'] = [
                    self._build_tree_recursive(child, current_depth + 1, max_depth)
                    for child in children
                ]
            else:
                tree_node['children'] = []
        else:
            # At max depth, just indicate if there are more children
            tree_node['children'] = []
            if project.has_children:
                tree_node['has_more'] = True

        return tree_node

    def get_tree(self, obj):
        """
        Get full project tree from root with nested children.

        Uses max_depth from schema context (default: 4).
        Always returns tree from root, even if querying a child project.

        Returns:
            Dict representing tree structure from root:
            {
                'projcode': 'CESM0002',
                'depth': 0,
                'children': [
                    {'projcode': 'P93300007', 'depth': 1, 'children': [...]},
                    {'projcode': 'P93300012', 'depth': 1, 'children': [...]},
                    ...
                ]
            }
        """
        # Get max_depth from context, default to 4
        max_depth = getattr(self, 'context', {}).get('max_depth', 4)

        # Find the root of the tree
        root = obj.get_root()

        # Build tree from root
        return self._build_tree_recursive(root, 0, max_depth)

    def get_active_directories(self, obj):
        """Return a list of active project directories"""
        return obj.active_directories
