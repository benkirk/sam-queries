"""
Marshmallow form validation schemas for Resource management routes.

Covers: Resources, Resource Types, Machines, Queues.
"""

import marshmallow.fields as f
import marshmallow.validate as v
from marshmallow import ValidationError, post_load
from datetime import datetime

from webapp.api.helpers import parse_input_end_date
from . import HtmxFormSchema


class EditResourceForm(HtmxFormSchema):
    commission_date = f.Date('%Y-%m-%d', required=True)
    decommission_date = f.Str(load_default=None)   # 23:59:59 convention applied in post_load
    description = f.Str(load_default=None)
    charging_exempt = f.Bool(load_default=False)

    @post_load
    def coerce_and_validate_dates(self, data, **kwargs):
        if data.get('description') == '':
            data['description'] = None
        end_str = data.get('decommission_date')
        if end_str:
            data['decommission_date'] = parse_input_end_date(end_str)
        else:
            data['decommission_date'] = None

        if data.get('decommission_date') and data.get('commission_date'):
            start = datetime.combine(data['commission_date'], datetime.min.time())
            if data['decommission_date'] <= start:
                raise ValidationError(
                    {'decommission_date': ['Decommission date must be after commission date.']}
                )
        return data


class CreateResourceForm(HtmxFormSchema):
    resource_name = f.Str(required=True, validate=v.Length(min=1))
    resource_type_id = f.Int(required=True)
    description = f.Str(load_default=None)
    charging_exempt = f.Bool(load_default=False)
    commission_date = f.Date('%Y-%m-%d', load_default=None)

    @post_load
    def coerce_empty(self, data, **kwargs):
        if data.get('description') == '':
            data['description'] = None
        return data


class EditResourceTypeForm(HtmxFormSchema):
    grace_period_days = f.Int(load_default=None, validate=v.Range(min=0))


class CreateResourceTypeForm(HtmxFormSchema):
    resource_type = f.Str(required=True, validate=v.Length(min=1))
    grace_period_days = f.Int(load_default=None, validate=v.Range(min=0))


class EditMachineForm(HtmxFormSchema):
    commission_date = f.Date('%Y-%m-%d', required=True)
    decommission_date = f.Str(load_default=None)   # 23:59:59 convention applied in post_load
    description = f.Str(load_default=None)
    cpus_per_node = f.Int(load_default=None, validate=v.Range(min=1))

    @post_load
    def coerce_and_validate_dates(self, data, **kwargs):
        if data.get('description') == '':
            data['description'] = None
        end_str = data.get('decommission_date')
        if end_str:
            data['decommission_date'] = parse_input_end_date(end_str)
        else:
            data['decommission_date'] = None

        if data.get('decommission_date') and data.get('commission_date'):
            start = datetime.combine(data['commission_date'], datetime.min.time())
            if data['decommission_date'] <= start:
                raise ValidationError(
                    {'decommission_date': ['Decommission date must be after commission date.']}
                )
        return data


class CreateMachineForm(HtmxFormSchema):
    name = f.Str(required=True, validate=v.Length(min=1))
    resource_id = f.Int(required=True)
    description = f.Str(load_default=None)
    cpus_per_node = f.Int(load_default=None, validate=v.Range(min=1))
    commission_date = f.Date('%Y-%m-%d', load_default=None)

    @post_load
    def coerce_empty(self, data, **kwargs):
        if data.get('description') == '':
            data['description'] = None
        return data


class EditQueueForm(HtmxFormSchema):
    wall_clock_hours_limit = f.Float(required=True, validate=v.Range(min=0, min_inclusive=False))
    end_date = f.Str(load_default=None)   # 23:59:59 convention applied in post_load
    description = f.Str(load_default=None)

    @post_load
    def coerce_dates(self, data, **kwargs):
        if data.get('description') == '':
            data['description'] = None
        end_str = data.get('end_date')
        if end_str:
            data['end_date'] = parse_input_end_date(end_str)
        else:
            data['end_date'] = None
        return data
    # Note: queue start_date is on the ORM object, not in the form. The route
    # checks end_date > queue.start_date inline after schema.load() since it
    # requires the existing DB value.
