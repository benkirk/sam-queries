"""
Marshmallow form validation schemas for status-dashboard (outage) routes.

Covers: Create Outage, Edit Outage.

Note on time zones: the outage modals use ``<input type="datetime-local">``,
which is TZ-blind — it submits a naive wall-clock string. The browser's IANA
zone is sent alongside in a hidden ``tz`` field; ``@post_load`` converts the
submitted naive-local datetimes into the naive-UTC storage convention via
``sam.fmt.naive_local_to_utc``. The ``tz`` field is transport-only and is popped
before the validated dict reaches the route.
"""

import marshmallow.fields as f
import marshmallow.validate as v
from marshmallow import post_load

from . import HtmxFormSchema

# Allowed values mirror the model enums (system_status.models.outages) and the
# system <select> options in the create modal.
_SYSTEMS = ['derecho', 'casper', 'jupyterhub', 'all']
_SEVERITIES = ['critical', 'major', 'minor', 'maintenance']
_STATUSES = ['investigating', 'identified', 'monitoring', 'resolved']

_DATETIME_LOCAL = '%Y-%m-%dT%H:%M'   # HTML datetime-local wire format


def _to_naive_utc(data):
    """Convert any present datetime-local fields from browser-local to naive-UTC."""
    from sam.fmt import naive_local_to_utc
    tz = data.pop('tz', None)
    for key in ('start_time', 'estimated_resolution'):
        if data.get(key) is not None:
            data[key] = naive_local_to_utc(data[key], tz)
    return data


class CreateOutageForm(HtmxFormSchema):
    """Report a new system outage.

    ``system_name`` is resolved to a ``system_id`` by the model's
    ``system_name`` setter (a DB hit) — that stays in the route per CLAUDE.md §9.
    """
    system_name = f.Str(required=True, validate=v.OneOf(_SYSTEMS))
    title = f.Str(required=True, validate=v.Length(min=1, max=255))
    severity = f.Str(required=True, validate=v.OneOf(_SEVERITIES))
    component = f.Str(load_default=None)
    description = f.Str(load_default=None)
    tz = f.Str(load_default=None)   # transport-only; popped in post_load
    start_time = f.DateTime(_DATETIME_LOCAL, load_default=None)
    estimated_resolution = f.DateTime(_DATETIME_LOCAL, load_default=None)

    @post_load
    def to_naive_utc(self, data, **kwargs):
        return _to_naive_utc(data)


class EditOutageForm(HtmxFormSchema):
    """Update an existing outage.

    ``title`` is optional on edit (the route applies it only when present, to
    preserve the existing pre-refactor behavior).
    """
    title = f.Str(load_default=None, validate=v.Length(max=255))
    status = f.Str(required=True, validate=v.OneOf(_STATUSES))
    severity = f.Str(required=True, validate=v.OneOf(_SEVERITIES))
    description = f.Str(load_default=None)
    tz = f.Str(load_default=None)   # transport-only; popped in post_load
    estimated_resolution = f.DateTime(_DATETIME_LOCAL, load_default=None)

    @post_load
    def to_naive_utc(self, data, **kwargs):
        return _to_naive_utc(data)
