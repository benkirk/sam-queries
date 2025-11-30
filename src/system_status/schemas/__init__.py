"""
Marshmallow-SQLAlchemy schemas for API serialization.

This module provides the base schema infrastructure and exports all schema classes
for use in API endpoints. Following the "Base Schema I" pattern from marshmallow-sqlalchemy.

Usage:
    from system_status.schemas import QueuesSchema

    # Serialize a single object
    queue_data = QueueSchema().dump(queue)

    # Serialize multiple objects
    queue_data = QueueSchema(many=True).dump(queues)
"""

from marshmallow import EXCLUDE
from marshmallow_sqlalchemy import SQLAlchemyAutoSchema
from webapp.extensions import db


class BaseSchema(SQLAlchemyAutoSchema):
    """
    Base schema class for all System Status schemas.

    Provides shared configuration:
    - Uses Flask-SQLAlchemy's db.session for all queries
    - load_instance=True: Load SQLAlchemy model instances
    - include_fk=True: Include foreign key fields in serialization
    - unknown=EXCLUDE: Ignore unknown fields during deserialization

    All model-specific schemas should inherit from this class.
    """
    class Meta:
        sqla_session = db.session
        load_instance = True
        include_fk = True
        unknown = EXCLUDE


# Import and export all schemas
from .status import (
    DerechoStatusSchema,
    LoginNodeSchema,
    FilesystemSchema,
    QueueSchema,
    CasperStatusSchema,
    CasperNodeTypeSchema,
    JupyterHubStatusSchema,
    SystemOutageSchema,
    ResourceReservationSchema,
)

__all__ = [
    'BaseSchema',
    # System Status schemas
    'DerechoStatusSchema',
    'FilesystemSchema',
    'LoginNodeSchema',
    'QueueSchema',
    'FilesystemSchema',
    'CasperStatusSchema',
    'CasperNodeTypeSchema',
    'JupyterHubStatusSchema',
    'SystemOutageSchema',
    'ResourceReservationSchema',
]
