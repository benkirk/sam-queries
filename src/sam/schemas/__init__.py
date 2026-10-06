"""
Marshmallow-SQLAlchemy schemas for API serialization.

This module provides the base schema infrastructure and exports all schema classes
for use in API endpoints. Following the "Base Schema I" pattern from marshmallow-sqlalchemy.

Usage:
    from sam.schemas import UserSchema, ProjectSchema

    # Serialize a single object
    user_data = UserSchema().dump(user)

    # Serialize multiple objects
    users_data = UserSchema(many=True).dump(users)
"""

from marshmallow_sqlalchemy import SQLAlchemyAutoSchema


class _FlaskSession:
    """Forwards to ``webapp.extensions.db.session`` on first use, so importing
    a schema (CLI, scheduling) needs no Flask; only ``load_instance`` loads do."""

    def __getattr__(self, name):
        from webapp.extensions import db
        return getattr(db.session, name)


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
        sqla_session = _FlaskSession()
        load_instance = True
        include_fk = True


# Import and export all schemas
from .user import UserSchema, UserListSchema, UserSummarySchema
from .contract import ContractSummarySchema
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
from .jobs import CompJobSchema
from .disk_quota import DiskQuotaSchema
from .charge_details import (
    HPCChargeDetailSchema,
    DavChargeDetailSchema,
    DiskChargeDetailSchema,
    ArchiveChargeDetailSchema
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
    # Contract schemas
    'ContractSummarySchema',
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
    # Job schemas
    'CompJobSchema',
    # Disk-quota schema (legacy shape via data_key)
    'DiskQuotaSchema',
    # Charge detail schemas
    'HPCChargeDetailSchema',
    'DavChargeDetailSchema',
    'DiskChargeDetailSchema',
    'ArchiveChargeDetailSchema',
]
