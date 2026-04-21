"""
Marshmallow form validation schemas for user-facing dashboard routes.

Covers: Add Member, Edit Allocation.
"""

import marshmallow.fields as f
import marshmallow.validate as v
from marshmallow import post_load, validates_schema, ValidationError

from . import HtmxFormSchema


class SetShellForm(HtmxFormSchema):
    """Set a user's login shell.

    The route enforces that ``shell_name`` is in the allowable set
    (shells present on every active HPC+DAV resource) — that requires a
    DB hit and stays in the route per CLAUDE.md §9.
    """
    shell_name = f.Str(required=True, validate=v.Length(min=1, max=25))


class SetPrimaryGidForm(HtmxFormSchema):
    """Set a user's primary GID.

    Membership validation (``unix_gid`` must be in the user's allowable
    set from ``get_user_group_access(..., include_projects=True)``)
    happens inside ``User.set_primary_gid`` — requires a DB hit, so it
    stays on the model per CLAUDE.md §9.
    """
    unix_gid = f.Integer(required=True)


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
    scales = f.Dict(
        keys=f.Int(),
        values=f.Float(validate=v.Range(min=0, min_inclusive=False)),
        load_default=dict,
    )
    # Admin override: when True, soft-delete any non-deleted allocations
    # that already overlap the target period before creating the new ones.
    # Route injects explicit False when the checkbox is unchecked (absent
    # from request.form).
    replace_existing = f.Bool(load_default=False)

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


class ExchangeAllocationForm(HtmxFormSchema):
    """Move ``amount`` from one dedicated allocation to another.

    The route enforces (all require DB access, so they stay inline):
    - both allocation IDs exist, are not deleted, and are not inheriting;
    - both allocations are on the same resource;
    - both owning projects lie within the edit-page project's subtree;
    - amount does not push FROM below its currently-used balance.
    """
    from_allocation_id = f.Int(required=True)
    to_allocation_id = f.Int(required=True)
    amount = f.Float(required=True, validate=v.Range(min=0, min_inclusive=False))

    @validates_schema
    def _distinct(self, data, **kwargs):
        if data.get('from_allocation_id') == data.get('to_allocation_id'):
            raise ValidationError(
                {'to_allocation_id': ['FROM and TO allocations must differ.']}
            )


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
        data['end_date'] = self.normalize_end_date(data.get('end_date'))
        self.assert_date_range(data.get('start_date'), data.get('end_date'))
        return data
