"""
Marshmallow-SQLAlchemy schemas for API serialization.

This module provides the base schema infrastructure and exports all schema classes
for use in API endpoints. Following the "Base Schema I" pattern from marshmallow-sqlalchemy.

Usage:
    from webui.schemas import UserSchema, ProjectSchema

    # Serialize a single object
    user_data = UserSchema().dump(user)

    # Serialize multiple objects
    users_data = UserSchema(many=True).dump(users)
"""

from marshmallow_sqlalchemy import SQLAlchemyAutoSchema
from ..extensions import db


class BaseSchema(SQLAlchemyAutoSchema):
    """
    Base schema class for all SAM schemas.

    Provides shared configuration:
    - Uses Flask-SQLAlchemy's db.session for all queries
    - load_instance=True: Load SQLAlchemy model instances
    - include_fk=True: Include foreign key fields in serialization

    All model-specific schemas should inherit from this class.
    """
    class Meta:
        sqla_session = db.session
        load_instance = True
        include_fk = True


# Import and export all schemas
from .user import UserSchema, UserListSchema, UserSummarySchema
from .project import ProjectSchema, ProjectListSchema, ProjectSummarySchema
from .resource import ResourceSchema, ResourceSummarySchema, ResourceTypeSchema
from .allocation import (
    AllocationSchema,
    AllocationWithUsageSchema,
    AccountSchema,
    AccountSummarySchema
)
from .charges import (
    CompChargeSummarySchema,
    DavChargeSummarySchema,
    DiskChargeSummarySchema,
    ArchiveChargeSummarySchema
)

__all__ = [
    'BaseSchema',
    # User schemas
    'UserSchema',
    'UserListSchema',
    'UserSummarySchema',
    # Project schemas
    'ProjectSchema',
    'ProjectListSchema',
    'ProjectSummarySchema',
    # Resource schemas
    'ResourceSchema',
    'ResourceSummarySchema',
    'ResourceTypeSchema',
    # Allocation/Account schemas
    'AllocationSchema',
    'AllocationWithUsageSchema',
    'AccountSchema',
    'AccountSummarySchema',
    # Charge summary schemas
    'CompChargeSummarySchema',
    'DavChargeSummarySchema',
    'DiskChargeSummarySchema',
    'ArchiveChargeSummarySchema',
]
