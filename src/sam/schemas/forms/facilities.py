"""
Marshmallow form validation schemas for Facility management routes.

Covers: Facilities, Panels, Panel Sessions, Allocation Types, their request limits.
"""

import marshmallow.fields as f
import marshmallow.validate as v
from marshmallow import ValidationError, post_load, validates_schema

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


class EditAllocationLimitForm(HtmxFormSchema):
    """An empty amount clears that bound; the order rule is the model's, checked here first."""
    min_amount = f.Float(load_default=None, validate=v.Range(min=0))
    default_amount = f.Float(load_default=None, validate=v.Range(min=0))
    max_amount = f.Float(load_default=None, validate=v.Range(min=0))
    active = f.Bool(load_default=False)

    @validates_schema
    def _bounds_in_order(self, data, **kwargs):
        from sam.accounting.allocation_type_limits import AllocationTypeResourceLimit
        try:
            AllocationTypeResourceLimit.check_bounds(
                data.get('min_amount'), data.get('default_amount'), data.get('max_amount'))
        except ValueError as exc:
            raise ValidationError(str(exc))


class CreateAllocationLimitForm(EditAllocationLimitForm):
    allocation_type_id = f.Int(required=True)
    resource_id = f.Int(required=True)


class EditAllocationTypeGuideForm(HtmxFormSchema):
    """A request page card; an empty text field clears it, the title stays required."""
    title = f.Str(required=True, validate=v.Length(min=1, max=64))
    allocation_type = f.Str(load_default=None, validate=v.Length(max=20))
    summary = f.Str(load_default=None, validate=v.Length(max=4000))
    funding = f.Str(load_default=None, validate=v.Length(max=128))
    cadence = f.Str(load_default=None, validate=v.Length(max=64))
    notes = f.Str(load_default=None, validate=v.Length(max=4000))
    audience = f.Str(load_default=None, validate=v.OneOf(('university', 'ncar')))
    display_order = f.Int(load_default=0, validate=v.Range(min=0))
    active = f.Bool(load_default=False)


class CreateAllocationTypeGuideForm(EditAllocationTypeGuideForm):
    xras_allocation_type_id = f.Int(required=True, validate=v.Range(min=1))
