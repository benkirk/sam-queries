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


class SetThresholdForm(HtmxFormSchema):
    """Set a project+resource consumption-rate limit (% of prorated allocation).

    Blank input removes the limit: the base ``@pre_load`` strips empty strings,
    so an empty ``threshold_pct`` falls through to ``load_default=None``. A value
    must be an integer strictly greater than 100 (a rate limit below the prorated
    rate would be permanently tripped). The route persists via
    ``Account.update_thresholds`` per-window — a DB hit that stays inline.
    """
    threshold_pct = f.Integer(
        load_default=None,
        validate=v.Range(
            min=101,
            error='Must be an integer greater than 100 (or blank to remove the limit).',
        ),
    )


class AddMemberForm(HtmxFormSchema):
    username = f.Str(required=True, validate=v.Length(min=1))
    start_date = f.Date('%Y-%m-%d', load_default=None)
    end_date = f.Str(load_default=None)   # 23:59:59 convention applied in post_load

    @post_load
    def coerce_and_validate_dates(self, data, **kwargs):
        data['end_date'] = self.normalize_end_date(data.get('end_date'))
        self.assert_date_range(data.get('start_date'), data.get('end_date'))
        return data


class ChangeProjectAdminForm(HtmxFormSchema):
    """Change or clear a project's admin from the member list.

    ``admin_username`` empty/absent means "remove the admin role" — the
    base ``@pre_load`` strips empty strings, so both spellings arrive as
    None. User existence / membership checks require DB access and stay in
    the route per CLAUDE.md §9.
    """
    admin_username = f.Str(load_default=None)

    @post_load
    def strip_username(self, data, **kwargs):
        if data.get('admin_username') is not None:
            data['admin_username'] = data['admin_username'].strip() or None
        return data


class LinkAllocationParentForm(HtmxFormSchema):
    """Re-link a standalone child allocation to a parent allocation.

    Subtree/compatibility validation happens in
    ``link_allocation_to_parent`` (requires DB access).
    """
    parent_allocation_id = f.Int(
        required=True,
        validate=v.Range(min=1, error='Missing parent allocation id.'),
        error_messages={'required': 'Missing parent allocation id.',
                        'invalid': 'Invalid parent allocation id.'})


class GrantMemberAccessForm(HtmxFormSchema):
    """Grant an existing project member access to all resources they are
    currently missing (the member-list "Grant access" fix).

    Only the username is submitted; the set of missing resources is derived
    server-side from ``Project.get_members_access_status``. User existence /
    membership checks require DB access and stay in the route per CLAUDE.md §9.
    """
    username = f.Str(required=True, validate=v.Length(min=1))


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
    """Validate the admin 'Renew Allocations' form (Edit Project -> Allocations tab).

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
    # Optional email to each project's lead/admin. Default ON in the UI, but
    # an unchecked box sends no key, so load_default is False and the route
    # injects presence explicitly (see form_input).
    notify_leads = f.Bool(load_default=False)
    # Optional operator note rendered in the lead/admin email.
    operator_comment = f.Str(load_default=None, validate=v.Length(max=1000))

    @post_load
    def coerce_and_validate_dates(self, data, **kwargs):
        data['new_end_date'] = self.normalize_end_date(data['new_end_date'])
        self.assert_date_range(
            data['new_start_date'], data['new_end_date'],
            field='new_end_date',
        )
        return data


class ExtendAllocationsForm(HtmxFormSchema):
    """Validate the admin 'Extend Allocations' form (Edit Project -> Allocations tab).

    Extend pushes ``end_date`` forward on existing allocations identified
    server-side by the ``source_active_at`` context. The client submits only
    the new end date and the subset of resources to extend.
    """
    source_active_at = f.Date('%Y-%m-%d', required=True)
    new_end_date = f.Str(required=True)   # 23:59:59 convention applied in post_load
    resource_ids = f.List(f.Int(), required=True, validate=v.Length(min=1))
    # See RenewAllocationsForm.notify_leads — default ON in the UI, absent
    # when unchecked, so load_default False + explicit presence in the route.
    notify_leads = f.Bool(load_default=False)
    # Optional operator note rendered in the lead/admin email.
    operator_comment = f.Str(load_default=None, validate=v.Length(max=1000))

    @post_load
    def coerce_and_validate_dates(self, data, **kwargs):
        data['new_end_date'] = self.normalize_end_date(data['new_end_date'])
        return data


class AlignAllocationsForm(HtmxFormSchema):
    """Validate the admin 'Align Allocations' form (Edit Project -> Allocations tab).

    Align sets every dated resource to one [min(start), max(end)] window. The
    target is computed server-side from ``source_active_at``; the client submits
    only that date.
    """
    source_active_at = f.Date('%Y-%m-%d', required=True)


class NotifyProjectForm(HtmxFormSchema):
    """The manual Notify modal's note; the per-row ``action_<id>`` choices
    are dynamic and read by the route."""
    operator_comment = f.Str(load_default=None, validate=v.Length(max=1000))


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


class AllocateResidualForm(HtmxFormSchema):
    """Allocate part of a parent allocation's carve-out residual to a sub-project.

    ``target`` is a composite value from a single ``<select>``:

    - ``alloc:<id>`` — bump an existing frontier carve-out allocation;
    - ``proj:<id>`` — create a new standalone allocation on an uncovered
      direct child branch.

    The manage layer re-validates the target against the server-computed
    frontier (``get_carveout_frontier``) and the amount against the
    unallocated residual — DB-dependent checks stay out of the schema per
    CLAUDE.md §9.
    """
    target = f.Str(
        required=True,
        validate=v.Regexp(r'^(alloc|proj):\d+$', error='Invalid target selection.'),
    )
    amount = f.Float(required=True, validate=v.Range(min=0, min_inclusive=False))
    comment = f.Str(load_default=None)

    @post_load
    def split_target(self, data, **kwargs):
        kind, _, ident = data['target'].partition(':')
        data['target_allocation_id'] = int(ident) if kind == 'alloc' else None
        data['target_project_id'] = int(ident) if kind == 'proj' else None
        return data


class AddAllocationsForm(HtmxFormSchema):
    """Validate the admin 'Add Allocations' grid (Edit Project -> Allocations tab).

    One date range and description apply to every resource given an amount;
    the route flattens the per-row ``amount_<rid>`` inputs into ``amounts``.
    Unchecked boxes send nothing, so ``apply_to_subprojects`` defaults False.
    """
    amounts = f.Dict(
        keys=f.Int(),
        values=f.Float(validate=v.Range(min=0, min_inclusive=False)),
        required=True,
        validate=v.Length(min=1, error='Enter an amount for at least one resource.'),
        error_messages={'required': 'Enter an amount for at least one resource.'},
    )
    start_date = f.Date('%Y-%m-%d', required=True)
    end_date = f.Str(load_default=None)   # 23:59:59 convention applied in post_load
    description = f.Str(load_default=None)
    apply_to_subprojects = f.Bool(load_default=False)

    @post_load
    def coerce_and_validate_dates(self, data, **kwargs):
        data['end_date'] = self.normalize_end_date(data.get('end_date'))
        self.assert_date_range(data.get('start_date'), data.get('end_date'))
        return data
