"""
Marshmallow form validation schemas for user-facing dashboard routes.

Covers: Add Member, Edit Allocation.
"""

import marshmallow.fields as f
import marshmallow.validate as v
from marshmallow import ValidationError, post_load
from datetime import datetime

from webapp.api.helpers import parse_input_end_date
from . import HtmxFormSchema


class AddMemberForm(HtmxFormSchema):
    username = f.Str(required=True, validate=v.Length(min=1))
    start_date = f.Date('%Y-%m-%d', load_default=None)
    end_date = f.Str(load_default=None)   # 23:59:59 convention applied in post_load

    @post_load
    def coerce_and_validate_dates(self, data, **kwargs):
        end_str = data.get('end_date')
        if end_str:
            data['end_date'] = parse_input_end_date(end_str)
        else:
            data['end_date'] = None

        start = data.get('start_date')
        end = data.get('end_date')
        if start and end:
            start_dt = datetime.combine(start, datetime.min.time())
            if end <= start_dt:
                raise ValidationError({'end_date': ['End date must be after start date.']})
        return data


class EditAllocationForm(HtmxFormSchema):
    amount = f.Float(required=True, validate=v.Range(min=0, min_inclusive=False))
    start_date = f.Date('%Y-%m-%d', load_default=None)
    end_date = f.Str(load_default=None)   # 23:59:59 convention applied in post_load
    description = f.Str(load_default=None)

    @post_load
    def coerce_and_validate_dates(self, data, **kwargs):
        if data.get('description') == '':
            data['description'] = None
        end_str = data.get('end_date')
        if end_str:
            data['end_date'] = parse_input_end_date(end_str)
        else:
            data['end_date'] = None

        start = data.get('start_date')
        end = data.get('end_date')
        if start and end:
            start_dt = datetime.combine(start, datetime.min.time())
            if end <= start_dt:
                raise ValidationError({'end_date': ['End date must be after start date.']})
        return data


class AddAllocationForm(HtmxFormSchema):
    """Validate the admin 'Add Allocation' form (Edit Project → Allocations tab).

    All core fields are required. ``apply_to_subprojects`` is an admin checkbox
    that triggers a DFS propagation of the new allocation to the full descendant
    tree — unchecked boxes send nothing, so the route must inject an explicit
    ``False`` before calling ``.load()``.
    """
    resource_id = f.Int(required=True)
    amount = f.Float(required=True, validate=v.Range(min=0, min_inclusive=False))
    start_date = f.Date('%Y-%m-%d', required=True)
    end_date = f.Str(load_default=None)   # 23:59:59 convention applied in post_load
    description = f.Str(load_default=None)
    apply_to_subprojects = f.Bool(load_default=False)

    @post_load
    def coerce_and_validate_dates(self, data, **kwargs):
        if data.get('description') == '':
            data['description'] = None
        end_str = data.get('end_date')
        if end_str:
            data['end_date'] = parse_input_end_date(end_str)
        else:
            data['end_date'] = None

        start = data.get('start_date')
        end = data.get('end_date')
        if start and end:
            start_dt = datetime.combine(start, datetime.min.time())
            if end <= start_dt:
                raise ValidationError({'end_date': ['End date must be after start date.']})
        return data
