"""
Marshmallow form validation schemas for user-facing dashboard routes.

Covers: Add Member, Edit Allocation.
"""

import marshmallow.fields as f
import marshmallow.validate as v
from marshmallow import post_load

from . import HtmxFormSchema


class AddMemberForm(HtmxFormSchema):
    username = f.Str(required=True, validate=v.Length(min=1))
    start_date = f.Date('%Y-%m-%d', load_default=None)
    end_date = f.Str(load_default=None)   # 23:59:59 convention applied in post_load

    @post_load
    def coerce_and_validate_dates(self, data, **kwargs):
        data['end_date'] = self.normalize_end_date(data.get('end_date'))
        self.assert_date_range(data.get('start_date'), data.get('end_date'))
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
        data['end_date'] = self.normalize_end_date(data.get('end_date'))
        self.assert_date_range(data.get('start_date'), data.get('end_date'))
        return data


class RenewAllocationsForm(HtmxFormSchema):
    """Validate the admin 'Renew Allocations' form (Edit Project → Allocations tab).

    Renewal clones existing allocations (identified server-side by the
    ``source_active_at`` context) into a new time period. The client submits
    only the new date range and the subset of resources to renew.
    """
    source_active_at = f.Date('%Y-%m-%d', required=True)
    new_start_date = f.Date('%Y-%m-%d', required=True)
    new_end_date = f.Str(required=True)   # 23:59:59 convention applied in post_load
    resource_ids = f.List(f.Int(), required=True, validate=v.Length(min=1))

    @post_load
    def coerce_and_validate_dates(self, data, **kwargs):
        data['new_end_date'] = self.normalize_end_date(data['new_end_date'])
        self.assert_date_range(
            data['new_start_date'], data['new_end_date'],
            field='new_end_date',
        )
        return data


class ExtendAllocationsForm(HtmxFormSchema):
    """Validate the admin 'Extend Allocations' form (Edit Project → Allocations tab).

    Extend pushes ``end_date`` forward on existing allocations identified
    server-side by the ``source_active_at`` context. The client submits only
    the new end date and the subset of resources to extend.
    """
    source_active_at = f.Date('%Y-%m-%d', required=True)
    new_end_date = f.Str(required=True)   # 23:59:59 convention applied in post_load
    resource_ids = f.List(f.Int(), required=True, validate=v.Length(min=1))

    @post_load
    def coerce_and_validate_dates(self, data, **kwargs):
        data['new_end_date'] = self.normalize_end_date(data['new_end_date'])
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
        data['end_date'] = self.normalize_end_date(data.get('end_date'))
        self.assert_date_range(data.get('start_date'), data.get('end_date'))
        return data
