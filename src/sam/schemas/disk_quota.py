"""Disk-quota output schema.

Reference for the "legacy shape via a declared schema" pattern: the legacy
camelCase contract is declared with marshmallow ``data_key`` rather than
hand-built in the query layer. Input is the plain record from
``sam.queries.disk_quota.get_disk_quotas``; output matches legacy
``GET /api/protected/admin/dasg/diskquota``. See CLAUDE.md section API.
"""

from marshmallow import Schema, fields


class DiskQuotaSchema(Schema):
    """One legacy diskquota record. snake_case attrs -> camelCase JSON keys."""

    projcode = fields.String()
    group_name = fields.String(data_key='groupName')
    data_manager = fields.String(data_key='dataManager', allow_none=True)
    resource_name = fields.String(data_key='resourceName')
    quota = fields.Float(allow_none=True)
    paths = fields.List(fields.String())
