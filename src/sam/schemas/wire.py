"""Marshmallow pieces the wire-format input schemas share (XRAS actions, the LDAP sync).

Both surfaces mirror Java POJOs: every field is optional and nullable, unknown keys are
ignored (``@JsonIgnoreProperties(ignoreUnknown = true)``), and Jackson coerced numbers
into ``String`` fields silently.
"""

from marshmallow import EXCLUDE, Schema, fields

from sam.dates import from_epoch_millis, to_epoch_millis


class CoercedStr(fields.String):
    """A String field that also accepts the ints and floats Jackson would coerce; a bool is rejected."""

    def _deserialize(self, value, attr, data, **kwargs):
        if isinstance(value, bool):
            raise self.make_error('invalid')
        if isinstance(value, (int, float)):
            value = repr(value) if isinstance(value, float) else str(value)
        return super()._deserialize(value, attr, data, **kwargs)


class EpochMillis(fields.Field):
    """A naive-Mountain datetime as epoch milliseconds (Jackson's ``java.util.Date``)."""

    default_error_messages = {'invalid': 'Not a valid epoch-millisecond timestamp.'}

    def _serialize(self, value, attr, obj, **kwargs):
        return to_epoch_millis(value)

    def _deserialize(self, value, attr, data, **kwargs):
        if isinstance(value, bool):
            raise self.make_error('invalid')
        try:
            return from_epoch_millis(value)
        except (TypeError, ValueError, OverflowError, OSError) as exc:
            raise self.make_error('invalid') from exc


class WireInput(Schema):
    """Base for a wire-format input schema: unknown keys are dropped, never an error."""

    class Meta:
        unknown = EXCLUDE


def opt_str(**kw):
    """An optional, nullable string, the shape almost every wire field has."""
    return fields.Str(load_default=None, allow_none=True, **kw)


def opt_coerced_str(**kw):
    """As :func:`opt_str`, tolerant of a JSON number."""
    return CoercedStr(load_default=None, allow_none=True, **kw)


def opt_int(**kw):
    return fields.Int(load_default=None, allow_none=True, **kw)


def opt_bool(**kw):
    return fields.Bool(load_default=None, allow_none=True, **kw)


__all__ = ['CoercedStr', 'EpochMillis', 'WireInput', 'opt_str', 'opt_coerced_str',
           'opt_int', 'opt_bool']
