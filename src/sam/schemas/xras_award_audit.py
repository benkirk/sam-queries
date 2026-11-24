"""``GET /api/v1/xras/award-audit/<projcode>``: dumps ``AwardAudit.to_dict()`` plus ``projcode``.

The field list is the wire contract (``docs/apis/XRAS_API.md``); a gate test pins it
to ``to_dict()`` so a new envelope key fails until it is declared here.
"""

from marshmallow import Schema, fields


class AwardAuditRowSchema(Schema):
    projcode = fields.String()
    xras_resource = fields.String(allow_none=True)
    sam_resource = fields.String(allow_none=True)
    issues = fields.List(fields.String())
    xras_amount = fields.Float(allow_none=True)
    sam_amount = fields.Float(allow_none=True)
    xras_begin = fields.String(allow_none=True)
    xras_end = fields.String(allow_none=True)
    sam_begin = fields.String(allow_none=True)
    sam_end = fields.String(allow_none=True)
    allocation_id = fields.Integer(allow_none=True)
    sam_projcode = fields.String(allow_none=True)
    units = fields.String()
    action_ids = fields.List(fields.Integer(allow_none=True))


class AwardAuditSchema(Schema):
    kind = fields.String()
    projcode = fields.String()
    as_of = fields.String()
    include_expired = fields.Boolean()
    complete = fields.Boolean()
    checked = fields.Integer()
    mismatched = fields.Integer()
    retired_skipped = fields.Integer()
    counts = fields.Dict(keys=fields.String(), values=fields.Integer())
    rows = fields.List(fields.Nested(AwardAuditRowSchema))
