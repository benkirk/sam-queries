"""
Marshmallow form validation schemas for Facility management routes.

Covers: Facilities, Panels, Panel Sessions, Allocation Types.
"""

import marshmallow.fields as f
import marshmallow.validate as v
from marshmallow import post_load

from . import HtmxFormSchema


class EditFacilityForm(HtmxFormSchema):
    description = f.Str(required=True, validate=v.Length(min=1, max=255))
    fair_share_percentage = f.Float(load_default=None,
                                    validate=v.Range(min=0, max=100))
    active = f.Bool(load_default=False)


class CreateFacilityForm(HtmxFormSchema):
    facility_name = f.Str(required=True, validate=v.Length(min=1, max=30))
    description = f.Str(required=True, validate=v.Length(min=1, max=255))
    code = f.Str(load_default=None, validate=v.Length(max=1))
    fair_share_percentage = f.Float(load_default=None,
                                    validate=v.Range(min=0, max=100))


class CreatePanelForm(HtmxFormSchema):
    panel_name = f.Str(required=True, validate=v.Length(min=1))
    facility_id = f.Int(required=True)
    description = f.Str(load_default=None)


class EditPanelSessionForm(HtmxFormSchema):
    start_date = f.Date('%Y-%m-%d', required=True)
    end_date = f.Str(load_default=None)    # handled in post_load for 23:59:59
    panel_meeting_date = f.Date('%Y-%m-%d', load_default=None)
    description = f.Str(load_default=None)

    @post_load
    def coerce_and_validate_dates(self, data, **kwargs):
        data['end_date'] = self.normalize_end_date(data.get('end_date'))
        self.assert_date_range(data.get('start_date'), data.get('end_date'))
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
