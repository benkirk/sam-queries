"""
User schemas for API serialization.

Provides three levels of user serialization:
- UserSchema: Full user details with nested relationships
- UserListSchema: Lightweight for list endpoints (no deep nesting)
- UserSummarySchema: Minimal fields for nested references

Usage:
    from webui.schemas import UserSchema, UserListSchema, UserSummarySchema

    # Full user details
    user_data = UserSchema().dump(user)

    # List of users (lightweight)
    users_data = UserListSchema(many=True).dump(users)

    # Nested reference (minimal)
    summary_data = UserSummarySchema().dump(user)
"""

from marshmallow import fields
from marshmallow_sqlalchemy import auto_field
from . import BaseSchema
from sam.core.users import User


class UserSummarySchema(BaseSchema):
    """
    Minimal user schema for nested references.

    Used when a user is referenced from another object (e.g., project lead).
    Only includes essential identification fields.
    """
    class Meta(BaseSchema.Meta):
        model = User
        fields = ('user_id', 'username', 'full_name', 'email')

    # Override to use @property method
    full_name = fields.Method('get_full_name')
    email = fields.Method('get_primary_email')

    def get_full_name(self, obj):
        """Get computed full_name from @property."""
        return obj.full_name

    def get_primary_email(self, obj):
        """Get primary email from @property."""
        return obj.primary_email


class UserListSchema(BaseSchema):
    """
    Lightweight user schema for list endpoints.

    Includes key user fields but avoids deep nesting.
    Used for /api/v1/users/ list endpoint.
    """
    class Meta(BaseSchema.Meta):
        model = User
        fields = (
            'user_id',
            'username',
            'full_name',
            'display_name',
            'email',
            'active',
            'locked',
            'charging_exempt',
        )

    # Override to use @property methods
    full_name = fields.Method('get_full_name')
    display_name = fields.Method('get_display_name')
    email = fields.Method('get_primary_email')

    def get_full_name(self, obj):
        """Get computed full_name from @property."""
        return obj.full_name

    def get_display_name(self, obj):
        """Get computed display_name from @property."""
        return obj.display_name

    def get_primary_email(self, obj):
        """Get primary email from @property."""
        return obj.primary_email


class UserSchema(BaseSchema):
    """
    Full user schema with nested relationships.

    Includes all user details plus related institutions, organizations, and roles.
    Used for /api/v1/users/<username> detail endpoint.
    """
    class Meta(BaseSchema.Meta):
        model = User
        fields = (
            'user_id',
            'username',
            'first_name',
            'middle_name',
            'last_name',
            'full_name',
            'display_name',
            'email',
            'active',
            'locked',
            'charging_exempt',
            'unix_uid',
            'institutions',
            'organizations',
            'roles',
            'creation_time',
            'modified_time',
        )

    # Override to use @property methods
    full_name = fields.Method('get_full_name')
    display_name = fields.Method('get_display_name')
    email = fields.Method('get_primary_email')

    # Nested relationships (serialized as method fields for custom formatting)
    institutions = fields.Method('get_institutions')
    organizations = fields.Method('get_organizations')
    roles = fields.Method('get_roles')

    def get_full_name(self, obj):
        """Get computed full_name from @property."""
        return obj.full_name

    def get_display_name(self, obj):
        """Get computed display_name from @property."""
        return obj.display_name

    def get_primary_email(self, obj):
        """Get primary email from @property."""
        return obj.primary_email

    def get_institutions(self, obj):
        """
        Serialize user institutions.

        Returns list of institutions with name and acronym.
        Handles cases where institution relationship may be None.
        """
        institutions = []
        for ui in obj.institutions:
            if ui.institution:
                institutions.append({
                    'institution_name': ui.institution.name,
                    'institution_acronym': ui.institution.acronym
                })
        return institutions

    def get_organizations(self, obj):
        """
        Serialize user organizations.

        Returns list of organizations with acronym and name.
        Handles cases where organization relationship may be None.
        """
        organizations = []
        for uo in obj.organizations:
            if uo.organization:
                organizations.append({
                    'organization_acronym': uo.organization.acronym,
                    'organization_name': uo.organization.name
                })
        return organizations

    def get_roles(self, obj):
        """
        Serialize user roles.

        Returns list of role names.
        Handles cases where role relationship may be None.
        """
        roles = []
        for ra in obj.role_assignments:
            if ra.role:
                roles.append(ra.role.name)
        return roles
