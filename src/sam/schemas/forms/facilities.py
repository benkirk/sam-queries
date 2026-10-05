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


class EditPanelForm(HtmxFormSchema):
    description = f.Str(load_default=None)
    active = f.Bool(load_default=False)

    @post_load
    def strip_description(self, data, **kwargs):
        # Parity with the pre-schema handler: whitespace-only -> None.
        if data.get('description') is not None:
            data['description'] = data['description'].strip() or None
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
