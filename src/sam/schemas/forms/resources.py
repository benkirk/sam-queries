"""
Marshmallow form validation schemas for Resource management routes.

Covers: Resources, Resource Types, Machines, Queues.
"""

import marshmallow.fields as f
import marshmallow.validate as v
from marshmallow import post_load, ValidationError

from . import HtmxFormSchema


class EditResourceForm(HtmxFormSchema):
    commission_date = f.Date('%Y-%m-%d', required=True)
    decommission_date = f.Str(load_default=None)   # 23:59:59 convention applied in post_load
    description = f.Str(load_default=None)
    charging_exempt = f.Bool(load_default=False)

    @post_load
    def coerce_and_validate_dates(self, data, **kwargs):
        data['decommission_date'] = self.normalize_end_date(data.get('decommission_date'))
        self.assert_date_range(
            data.get('commission_date'), data.get('decommission_date'),
            field='decommission_date',
            message='Decommission date must be after commission date.',
        )
        return data


class CreateResourceForm(HtmxFormSchema):
    resource_name = f.Str(required=True, validate=v.Length(min=1))
    resource_type_id = f.Int(required=True)
    description = f.Str(load_default=None)
    charging_exempt = f.Bool(load_default=False)
    commission_date = f.Date('%Y-%m-%d', load_default=None)


class EditFacilityResourceForm(HtmxFormSchema):
    """Per-(facility, resource) fair-share override.

    fair_share_percentage is required for a Set/Edit; the Unset action
    deletes the row via a separate DELETE route (no schema needed).
    """
    fair_share_percentage = f.Float(required=True, validate=v.Range(min=0, max=100))


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
        data['decommission_date'] = self.normalize_end_date(data.get('decommission_date'))
        self.assert_date_range(
            data.get('commission_date'), data.get('decommission_date'),
            field='decommission_date',
            message='Decommission date must be after commission date.',
        )
        return data


class CreateMachineForm(HtmxFormSchema):
    name = f.Str(required=True, validate=v.Length(min=1))
    resource_id = f.Int(required=True)
    description = f.Str(load_default=None)
    cpus_per_node = f.Int(load_default=None, validate=v.Range(min=1))
    commission_date = f.Date('%Y-%m-%d', load_default=None)


class EditQueueForm(HtmxFormSchema):
    wall_clock_hours_limit = f.Float(required=True, validate=v.Range(min=0, min_inclusive=False))
    end_date = f.Str(load_default=None)   # 23:59:59 convention applied in post_load
    description = f.Str(load_default=None)

    @post_load
    def coerce_dates(self, data, **kwargs):
        data['end_date'] = self.normalize_end_date(data.get('end_date'))
        return data
    # Note: queue start_date is on the ORM object, not in the form. The route
    # checks end_date > queue.start_date inline after schema.load() since it
    # requires the existing DB value.


class CreateQueueForm(HtmxFormSchema):
    """Validate creation of a Queue.

    FK existence check (resource_id -> Resource) stays in the route.
    Name uniqueness is checked in Queue.create(), which has the session.

    cos_id and start_date are deliberately absent: cos_id fed the superseded
    charging algorithm and is read nowhere, and start_date defaults to today
    in Queue.create().
    """
    queue_name = f.Str(required=True, validate=v.Length(min=1, max=50))
    resource_id = f.Int(required=True)
    description = f.Str(load_default=None, validate=v.Length(max=255))
    # Optional: a few real rows (e.g. 'systdd') carry no limit.
    wall_clock_hours_limit = f.Float(
        load_default=None, validate=v.Range(min=0, min_inclusive=False)
    )


class QueueCleanupForm(HtmxFormSchema):
    """Step 1 of the per-resource queue cleanup workflow: the inactivity window."""
    days = f.Int(load_default=90, validate=v.Range(min=1, max=3650))


class QueueCleanupCommitForm(QueueCleanupForm):
    """Step 3: the admin-edited checkbox selection.

    NOTE: `queue_ids` arrives as repeated form fields. `request.form` is a
    MultiDict, from which a List field would read only the first value — the
    route must pass `request.form.getlist('queue_ids')` explicitly.
    """
    queue_ids = f.List(f.Int(), load_default=list)


class CreateDiskResourceRootDirectoryForm(HtmxFormSchema):
    """Validate creation of a DiskResourceRootDirectory.

    FK existence check (resource_id -> Resource of DISK type) stays in the
    route since schemas do not touch the DB. Uniqueness on root_directory is
    enforced by the DB and surfaced as a route-level error.
    """
    resource_id = f.Int(required=True)
    root_directory = f.Str(required=True, validate=v.Length(min=1, max=64))
    charging_exempt = f.Bool(load_default=False)
    active = f.Bool(load_default=True)

    @post_load
    def normalize(self, data, **kwargs):
        data["root_directory"] = data["root_directory"].strip()
        if not data["root_directory"]:
            raise ValidationError({"root_directory": ["Root directory cannot be blank."]})
        return data


class EditDiskResourceRootDirectoryForm(CreateDiskResourceRootDirectoryForm):
    """Same shape as create. root_directory is editable; uniqueness collisions
    surface as a route-level error."""

