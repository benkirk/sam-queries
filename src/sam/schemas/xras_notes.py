"""The reply to ``POST /api/v1/xras/notes``: what the run wrote."""

from marshmallow import Schema, fields


class RunNotesReceiptSchema(Schema):
    source = fields.String()
    run_id = fields.String()
    received = fields.Integer()
    request_numbers = fields.List(fields.String())
