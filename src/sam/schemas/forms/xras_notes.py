"""Load schemas for ``POST /api/v1/xras/notes``: one recommender run, a batch of notes.

A JSON body, so the plain-marshmallow ``WireInput`` base, not ``HtmxFormSchema``
(see :mod:`sam.schemas.forms.xras`). Every link is followed by an operator's click,
so a URL that is not ``http(s)`` is refused here, at ingest.
Design: ``docs/plans/ARC_RECOMMENDER_NOTES.md`` § 3.
"""
from urllib.parse import urlsplit

from marshmallow import ValidationError, fields, validate, validates_schema

from sam.dates import SERVER_TZ
from sam.schemas.wire import WireInput, opt_int, opt_str

__all__ = ['RecommenderNoteBatchSchema', 'RecommenderNoteSchema',
           'NOTES_PER_BATCH_MAX', 'FINDINGS_PER_NOTE_MAX']

NOTES_PER_BATCH_MAX = 200
FINDINGS_PER_NOTE_MAX = 40
_SOURCES_PER_FINDING_MAX = 20
_PROSE_MAX = 4000
_MESSAGE_MAX = 1000
_URL_MAX = 1000
_LABEL_MAX = 200


def _http_url(value):
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.netloc or any(c.isspace() for c in value):
        raise ValidationError('Must be an http(s) URL.')


def _url_field(**kw):
    return fields.Str(validate=[validate.Length(max=_URL_MAX), _http_url], **kw)


def _hours_field():
    return fields.Dict(keys=fields.Str(validate=validate.Length(min=1, max=64)),
                       values=fields.Float(allow_nan=False), load_default=dict, allow_none=False)


class MountainDateTime(fields.DateTime):
    """ISO datetime; an offset is converted to naive Mountain, a naive value is taken as Mountain."""

    def _deserialize(self, value, attr, data, **kwargs):
        stamp = super()._deserialize(value, attr, data, **kwargs)
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(SERVER_TZ).replace(tzinfo=None)
        return stamp


class NoteSourceSchema(WireInput):
    label = fields.Str(required=True, validate=validate.Length(min=1, max=_LABEL_MAX))
    url = _url_field(required=True)
    kind = opt_str(validate=validate.Length(max=32))


class NoteFindingSchema(WireInput):
    id = opt_str(validate=validate.Length(max=64))
    severity = fields.Str(required=True, validate=validate.OneOf(('block', 'warn', 'info', 'ok')))
    message = fields.Str(required=True, validate=validate.Length(min=1, max=_MESSAGE_MAX))
    sources = fields.List(fields.Nested(NoteSourceSchema), load_default=list,
                          validate=validate.Length(max=_SOURCES_PER_FINDING_MAX))


class RecommenderNoteSchema(WireInput):
    request_number = fields.Str(required=True, validate=validate.Length(min=1, max=128))
    request_id = opt_int()
    action_id = opt_int()
    decision = fields.Str(required=True,
                          validate=validate.OneOf(('approve', 'reject', 'route_to_human')))
    button = opt_str(validate=validate.Length(max=32))
    end_date = fields.Date(load_default=None, allow_none=True)
    hours_by_resource = _hours_field()
    requested_hours = _hours_field()
    admin_comment = opt_str(validate=validate.Length(max=_PROSE_MAX))
    rationale = opt_str(validate=validate.Length(max=_PROSE_MAX))
    pi_history = opt_str(validate=validate.Length(max=500))
    findings = fields.List(fields.Nested(NoteFindingSchema), load_default=list,
                           validate=validate.Length(max=FINDINGS_PER_NOTE_MAX))
    links = fields.Dict(keys=fields.Str(validate=validate.Length(min=1, max=32)),
                        values=_url_field(), load_default=dict)


class RecommenderNoteBatchSchema(WireInput):
    source = fields.Str(required=True, validate=validate.Length(min=1, max=32))
    run_id = fields.Str(required=True, validate=validate.Length(min=1, max=64))
    generated_at = MountainDateTime(required=True)
    ruleset_version = opt_str(validate=validate.Length(max=32))
    notes = fields.List(fields.Nested(RecommenderNoteSchema), required=True,
                        validate=validate.Length(max=NOTES_PER_BATCH_MAX))

    @validates_schema
    def _one_note_per_request(self, data, **kwargs):
        seen, twice = set(), set()
        for note in data.get('notes') or ():
            number = note['request_number']
            (twice if number in seen else seen).add(number)
        if twice:
            raise ValidationError(f"Each request once per batch: {', '.join(sorted(twice))}.",
                                  'notes')
