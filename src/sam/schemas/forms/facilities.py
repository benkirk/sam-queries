"""
Marshmallow form validation schemas for Facility management routes.

Covers: Facilities, Panels, Panel Sessions, Allocation Types.
"""

import marshmallow.fields as f
import marshmallow.validate as v
from marshmallow import ValidationError, post_load

from webapp.api.helpers import parse_input_end_date
from . import HtmxFormSchema


class EditFacilityForm(HtmxFormSchema):
    description = f.Str(required=True, validate=v.Length(min=1, max=255))
    fair_share_percentage = f.Float(load_default=None,
                                    validate=v.Range(min=0, max=100))
    active = f.Bool(load_default=False)

    @post_load
    def coerce_empty(self, data, **kwargs):
        # Empty string for fair_share_percentage means None
        if data.get('fair_share_percentage') is None:
            data['fair_share_percentage'] = None
        return data


class CreateFacilityForm(HtmxFormSchema):
    facility_name = f.Str(required=True, validate=v.Length(min=1, max=30))
    description = f.Str(required=True, validate=v.Length(min=1, max=255))
    code = f.Str(load_default=None, validate=v.Length(max=1))
    fair_share_percentage = f.Float(load_default=None,
                                    validate=v.Range(min=0, max=100))

    @post_load
    def coerce_empty(self, data, **kwargs):
        if data.get('code') == '':
            data['code'] = None
        return data


class CreatePanelForm(HtmxFormSchema):
    panel_name = f.Str(required=True, validate=v.Length(min=1))
    facility_id = f.Int(required=True)
    description = f.Str(load_default=None)

    @post_load
    def coerce_empty(self, data, **kwargs):
        if data.get('description') == '':
            data['description'] = None
        return data


class EditPanelSessionForm(HtmxFormSchema):
    start_date = f.Date('%Y-%m-%d', required=True)
    end_date = f.Str(load_default=None)    # handled in post_load for 23:59:59
    panel_meeting_date = f.Date('%Y-%m-%d', load_default=None)
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

        from datetime import datetime
        if data.get('end_date') and data.get('start_date'):
            start = datetime.combine(data['start_date'], datetime.min.time())
            if data['end_date'] <= start:
                raise ValidationError({'end_date': ['End date must be after start date.']})
        return data


class EditAllocationTypeForm(HtmxFormSchema):
    default_allocation_amount = f.Float(load_default=None,
                                         validate=v.Range(min=0))
    fair_share_percentage = f.Float(load_default=None,
                                    validate=v.Range(min=0, max=100))
    active = f.Bool(load_default=False)


class CreateAllocationTypeForm(HtmxFormSchema):
    allocation_type = f.Str(required=True, validate=v.Length(min=1))
    panel_id = f.Int(required=True)
    default_allocation_amount = f.Float(load_default=None,
                                         validate=v.Range(min=0))
    fair_share_percentage = f.Float(load_default=None,
                                    validate=v.Range(min=0, max=100))
