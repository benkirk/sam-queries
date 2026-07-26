"""
Admin dashboard — Resource management routes.

Covers: Resources, Resource Types, Machines, Queues.
"""

from flask import render_template, request
from flask_login import login_required
from datetime import datetime
from functools import partial

from webapp.utils.htmx import (
    handle_htmx_form_post,
    htmx_not_found,
    htmx_success_message,
    modal_triggers,
    read_active_only,
)
from webapp.extensions import db
from webapp.api.v1.queue import invalidate_queue_cache
from webapp.utils.fk_validation import validate_fk_existence
from webapp.utils.form_handler import FormError, HtmxFormHandler
from webapp.utils.rbac import (
    require_permission, require_permission_any_facility, Permission,
)
from sam.manage import management_transaction
from sam.resources.machines import Machine
from sam.resources.resources import Resource, ResourceType
from sam.schemas.forms.resources import (
    EditResourceForm, CreateResourceForm, EditFacilityResourceForm,
    EditResourceTypeForm, CreateResourceTypeForm,
    EditMachineForm, CreateMachineForm, EditQueueForm, CreateQueueForm,
    QueueCleanupForm, QueueCleanupCommitForm,
    CreateDiskResourceRootDirectoryForm, EditDiskResourceRootDirectoryForm,
)

from .blueprint import bp
from .crud import CrudSpec, register_crud


_RESOURCES_TRIGGERS = modal_triggers('reloadResourcesCard')


def _active_resources():
    return (
        db.session.query(Resource)
        .filter(Resource.is_active)
        .order_by(Resource.resource_name)
        .all()
    )


def _all_resource_types():
    return db.session.query(ResourceType).order_by(ResourceType.resource_type).all()


# ── Resource Management Card ───────────────────────────────────────────────


@bp.route('/htmx/resources')
@login_required
@require_permission_any_facility(Permission.VIEW_RESOURCES)
def htmx_resources_card():
    """
    Return the Resources card body fragment with four tabs:
    Resources, Resource Types, Machines, Queues.
    Lazy-loaded when the Resources collapsible section is first expanded.
    """
    from sam.resources.resources import Resource, ResourceType
    from sam.resources.machines import Machine, Queue
    from sam.resources.facilities import Facility

    active_only = read_active_only(request.args)
    now = datetime.now()

    resource_q = db.session.query(Resource).order_by(Resource.resource_name)
    if active_only:
        resource_q = resource_q.filter(Resource.is_active)
    resources = resource_q.all()

    resource_types = db.session.query(ResourceType).order_by(ResourceType.resource_type).all()

    # Active facilities drive the per-resource fair-share override table shown
    # under each HPC/DAV resource row (defaults are shown where no override row
    # exists — see fragments/resources_card.html).
    facilities = (
        db.session.query(Facility)
        .filter(Facility.is_active)
        .order_by(Facility.facility_name)
        .all()
    )

    machine_q = db.session.query(Machine).order_by(Machine.resource_id, Machine.name)
    if active_only:
        machine_q = machine_q.filter(Machine.is_active)
    machines = machine_q.all()

    queue_q = db.session.query(Queue).order_by(Queue.resource_id, Queue.queue_name)
    if active_only:
        queue_q = queue_q.filter(Queue.is_active)
    queues = queue_q.all()

    from sam.operational import WallclockExemption
    from sam.core.users import User
    from sqlalchemy.orm import joinedload
    exemption_q = (
        db.session.query(WallclockExemption)
        .join(WallclockExemption.queue)
        .join(WallclockExemption.user)
        .options(
            joinedload(WallclockExemption.queue).joinedload(Queue.resource),
            joinedload(WallclockExemption.user),
        )
        .order_by(Queue.resource_id, Queue.queue_name, User.username)
    )
    if active_only:
        exemption_q = exemption_q.filter(WallclockExemption.is_active)
    exemptions = exemption_q.all()

    # Disk resources (with their root_directories collection) for the
    # "Disk Resource Root Directories" section in the Resources sub-tab.
    disk_resources_with_roots = (
        db.session.query(Resource)
        .join(ResourceType)
        .filter(ResourceType.resource_type == 'DISK')
        .order_by(Resource.resource_name)
        .all()
    )

    return render_template(
        'dashboards/admin/fragments/resources_card.html',
        resources=resources,
        resource_types=resource_types,
        facilities=facilities,
        machines=machines,
        queues=queues,
        exemptions=exemptions,
        disk_resources_with_roots=disk_resources_with_roots,
        is_admin=True,
        now=now,
        active_only=active_only,
    )


# ── Resource Delete (bespoke: decommission by date, not active flag) ───────
# (resource edit/create are generated from _RESOURCE_CRUD_SPECS below)


@bp.route('/htmx/resource-delete/<int:resource_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_resource_delete(resource_id):
    """Soft-delete (decommission) a resource."""
    resource = db.session.get(Resource, resource_id)
    if not resource:
        return htmx_not_found('Resource')

    # Resource decommission sets a date rather than the active flag, so we
    # don't use handle_htmx_soft_delete here.
    try:
        with management_transaction(db.session):
            resource.update(decommission_date=datetime.now())
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return ''


# ── Per-resource Facility Fair-Share Override ──────────────────────────────
# A row in `facility_resource` overrides the facility default fair-share for a
# single resource (COALESCE(fr…, f…) in sam/queries/fstree_access.py). "Set"
# upserts the row; "Unset" deletes it so the facility default re-emerges.


@bp.route('/htmx/facility-resource-edit-form/<int:resource_id>/<int:facility_id>')
@login_required
@require_permission(Permission.EDIT_RESOURCES)
def htmx_facility_resource_edit_form(resource_id, facility_id):
    """Return the fair-share override edit form fragment (loaded into modal)."""
    from sam.resources.resources import Resource
    from sam.resources.facilities import Facility, FacilityResource

    resource = db.session.get(Resource, resource_id)
    facility = db.session.get(Facility, facility_id)
    if not resource or not facility:
        return '<div class="alert alert-warning">Resource or facility not found</div>'

    override = FacilityResource.get_override(db.session, facility_id, resource_id)

    return render_template(
        'dashboards/admin/fragments/edit_facility_resource_form_htmx.html',
        resource=resource,
        facility=facility,
        override=override,
    )


@bp.route('/htmx/facility-resource-edit/<int:resource_id>/<int:facility_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_RESOURCES)
def htmx_facility_resource_edit(resource_id, facility_id):
    """Set (upsert) a per-resource fair-share override for a facility."""
    from sam.resources.resources import Resource
    from sam.resources.facilities import Facility, FacilityResource

    resource = db.session.get(Resource, resource_id)
    facility = db.session.get(Facility, facility_id)
    if not resource or not facility:
        return htmx_not_found('Resource or facility')

    return handle_htmx_form_post(
        schema_cls=EditFacilityResourceForm,
        template='dashboards/admin/fragments/edit_facility_resource_form_htmx.html',
        success_triggers=_RESOURCES_TRIGGERS,
        error_prefix='Error saving fair-share override',
        extra_context={'resource': resource, 'facility': facility,
                       'override': FacilityResource.get_override(db.session, facility_id, resource_id)},
        do_action=lambda data: FacilityResource.set_override(
            db.session,
            facility_id=facility_id,
            resource_id=resource_id,
            fair_share_percentage=data['fair_share_percentage'],
        ),
    )


@bp.route('/htmx/facility-resource-unset/<int:resource_id>/<int:facility_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_facility_resource_unset(resource_id, facility_id):
    """Delete a per-resource fair-share override so the facility default re-emerges."""
    from sam.resources.facilities import FacilityResource

    try:
        with management_transaction(db.session):
            FacilityResource.clear_override(
                db.session, facility_id=facility_id, resource_id=resource_id,
            )
    except Exception as e:  # noqa: BLE001 — surface to the user
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return htmx_success_message(_RESOURCES_TRIGGERS, 'Override removed.')


# ── Machine Delete (bespoke: decommission by date, not active flag) ────────
# (resource-type / machine edit+create are generated from
#  _RESOURCE_CRUD_SPECS below)


@bp.route('/htmx/machine-delete/<int:machine_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_machine_delete(machine_id):
    """Soft-delete (decommission) a machine."""
    machine = db.session.get(Machine, machine_id)
    if not machine:
        return htmx_not_found('Machine')

    try:
        with management_transaction(db.session):
            machine.update(decommission_date=datetime.now())
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return ''


# ── Queue Create ───────────────────────────────────────────────────────────


@bp.route('/htmx/queue-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_queue_create_form():
    """Return the queue create form fragment (loaded into modal)."""
    return render_template(
        'dashboards/admin/fragments/create_queue_form_htmx.html',
        resources=_active_resources(),
    )


@bp.route('/htmx/queue-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_queue_create():
    """Create a new queue (plus its companion queue_factor row)."""
    from sam.resources.machines import Queue

    def _create(data):
        # FK existence check lives here — schemas don't touch the DB.
        validate_fk_existence(db.session, (Resource, data['resource_id'], 'resource'))
        return Queue.create(db.session, **data)

    return handle_htmx_form_post(
        schema_cls=CreateQueueForm,
        template='dashboards/admin/fragments/create_queue_form_htmx.html',
        success_triggers=_RESOURCES_TRIGGERS,
        error_prefix='Error creating queue',
        context_fn=lambda: {'resources': _active_resources()},
        do_action=_create,
        # Runs after the transaction has committed, never inside it — clearing
        # early would let a concurrent read re-cache the pre-insert payload.
        after_commit=lambda result: invalidate_queue_cache(),
    )


# ── Queue Edit ─────────────────────────────────────────────────────────────


@bp.route('/htmx/queue-edit-form/<int:queue_id>')
@login_required
@require_permission(Permission.EDIT_RESOURCES)
def htmx_queue_edit_form(queue_id):
    """Return the queue edit form fragment (loaded into modal)."""
    from sam.resources.machines import Queue

    queue = db.session.get(Queue, queue_id)
    if not queue:
        return '<div class="alert alert-warning">Queue not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_queue_form_htmx.html',
        queue=queue,
    )


class _EditQueueHandler(HtmxFormHandler):
    """Update a queue; the end-date cross-field check needs the ORM
    object's start_date, and success invalidates the queue API cache."""

    schema_cls = EditQueueForm
    template = 'dashboards/admin/fragments/edit_queue_form_htmx.html'
    error_prefix = 'Error updating queue'

    def clean(self, data):
        if (data.get('end_date') and self.queue.start_date
                and data['end_date'] <= self.queue.start_date):
            raise FormError('End date must be after start date.')
        return data

    def perform(self, data):
        self.queue.update(
            description=data['description'],
            wall_clock_hours_limit=data['wall_clock_hours_limit'],
            end_date=data['end_date'],
        )

    def after_commit(self, result):
        invalidate_queue_cache()

    def context(self):
        return {'queue': self.queue}

    def triggers(self, result):
        return _RESOURCES_TRIGGERS


@bp.route('/htmx/queue-edit/<int:queue_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_RESOURCES)
def htmx_queue_edit(queue_id):
    """Update a queue."""
    from sam.resources.machines import Queue

    queue = db.session.get(Queue, queue_id)
    if not queue:
        return htmx_not_found('Queue')

    return _EditQueueHandler(queue=queue).handle()


# ── Queue Delete ───────────────────────────────────────────────────────────


@bp.route('/htmx/queue-delete/<int:queue_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_queue_delete(queue_id):
    """Soft-delete (expire) a queue by setting end_date to now."""
    from sam.resources.machines import Queue

    queue = db.session.get(Queue, queue_id)
    if not queue:
        return htmx_not_found('Queue')

    try:
        with management_transaction(db.session):
            queue.update(end_date=datetime.now())
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    invalidate_queue_cache()
    return ''


# ── Queue Cleanup ──────────────────────────────────────────────────────────
#
# Three-step form → preview → commit, mirroring the project-directory bulk
# deactivation in projects_routes.py. One deliberate difference: the commit
# step acts on the admin's edited checkbox selection rather than re-running
# the query, so unticking a queue in the preview actually spares it.


def _cleanup_context(resource_id):
    """Resolve the resource for the cleanup modal, or None if it's gone."""
    from sam.resources.resources import Resource
    return db.session.get(Resource, resource_id)


def _annotate_pbs_activity(candidates, resource, days):
    """Cross-check SAM cleanup candidates against system_status PBS snapshots.

    SAM charging data is blind to routing queues and charging-exempt work,
    so a queue can look dead while PBS is actively serving it. Two status
    signals fill that gap, both matched by queue name against the system
    derived from the resource ('Derecho GPU' → 'derecho'):

    * ``queue_status`` — a queue appears there only while jobs sit in it,
      giving "last held jobs at tick X".
    * ``queues.last_defined_at`` — stamped from the collectors' qstat -Q
      roster; covers routing queues that drain instantly and idle-but-live
      execution queues.

    Annotate-only, never drops rows: a queue seen in PBS within the window
    is un-preselected (and badged in the template), but the admin can still
    tick it deliberately. Absence of status data changes nothing — old
    resources without snapshot coverage behave exactly as before.

    Returns True when any status data existed for the system (template
    hides the PBS column entirely otherwise).
    """
    from datetime import timedelta
    from system_status.queries import get_queue_last_seen, get_queue_definitions
    from system_status.timeutil import utcnow_naive

    system_name = resource.resource_name.split()[0].lower()
    try:
        last_seen = get_queue_last_seen(db.session, system_name)
        defined = get_queue_definitions(db.session, system_name)
    except Exception:
        last_seen, defined = {}, {}     # status DB unavailable → SAM-only view

    cutoff = utcnow_naive() - timedelta(days=days)
    for c in candidates:
        name = c['queue'].queue_name
        d = defined.get(name)
        c['last_seen_pbs'] = last_seen.get(name)
        c['pbs_queue_type'] = d['queue_type'] if d else None
        c['defined_in_pbs'] = d is not None and d['last_defined_at'] >= cutoff
        c['active_in_pbs'] = (c['last_seen_pbs'] is not None
                              and c['last_seen_pbs'] >= cutoff)
        if c['active_in_pbs'] or c['defined_in_pbs']:
            c['preselected'] = False    # never pre-check a queue PBS still knows

    return bool(last_seen) or bool(defined)


@bp.route('/htmx/queue-cleanup-form/<int:resource_id>')
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_queue_cleanup_form(resource_id):
    """Step 1: ask for the inactivity window."""
    resource = _cleanup_context(resource_id)
    if not resource:
        return htmx_not_found('Resource')

    return render_template(
        'dashboards/admin/fragments/queue_cleanup_form_htmx.html',
        resource=resource,
    )


@bp.route('/htmx/queue-cleanup-preview/<int:resource_id>', methods=['POST'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_queue_cleanup_preview(resource_id):
    """Step 2: list unused queues with editable checkboxes."""
    from marshmallow import ValidationError
    from sam.queries.queue_access import get_queue_cleanup_candidates

    resource = _cleanup_context(resource_id)
    if not resource:
        return htmx_not_found('Resource')

    try:
        form_data = QueueCleanupForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/queue_cleanup_form_htmx.html',
            resource=resource,
            errors=QueueCleanupForm.flatten_errors(e.messages),
            form=request.form,
        )

    candidates = get_queue_cleanup_candidates(
        db.session, resource_id, days=form_data['days']
    )
    pbs_data_available = _annotate_pbs_activity(candidates, resource, form_data['days'])
    return render_template(
        'dashboards/admin/fragments/queue_cleanup_preview_htmx.html',
        resource=resource,
        days=form_data['days'],
        candidates=candidates,
        pbs_data_available=pbs_data_available,
    )


@bp.route('/htmx/queue-cleanup/<int:resource_id>', methods=['POST'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_queue_cleanup(resource_id):
    """Step 3: expire the selected queues."""
    from marshmallow import ValidationError
    from sam.queries.queue_access import get_queue_cleanup_candidates

    resource = _cleanup_context(resource_id)
    if not resource:
        return htmx_not_found('Resource')

    # queue_ids arrives as repeated fields; request.form is a MultiDict, from
    # which the List field would otherwise read only the first value.
    data = {**request.form.to_dict(), 'queue_ids': request.form.getlist('queue_ids')}
    try:
        form_data = QueueCleanupCommitForm().load(data)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/queue_cleanup_form_htmx.html',
            resource=resource,
            errors=QueueCleanupCommitForm.flatten_errors(e.messages),
            form=request.form,
        )

    days = form_data['days']
    candidates = get_queue_cleanup_candidates(db.session, resource_id, days=days)
    # Annotate for the error-path re-renders below; PBS activity never
    # disqualifies a candidate, so the admin's selection remains valid.
    pbs_data_available = _annotate_pbs_activity(candidates, resource, days)

    # Honour the admin's selection, but only over queues that are still
    # candidates — a submitted id that no longer qualifies (or never did) is
    # dropped rather than trusted.
    selected_ids = set(form_data['queue_ids'])
    by_id = {c['queue'].queue_id: c['queue'] for c in candidates}
    to_expire = [by_id[qid] for qid in sorted(selected_ids) if qid in by_id]

    if not to_expire:
        return render_template(
            'dashboards/admin/fragments/queue_cleanup_preview_htmx.html',
            resource=resource,
            days=days,
            candidates=candidates,
            pbs_data_available=pbs_data_available,
            errors=['No queues selected — nothing to do.'],
        )

    try:
        with management_transaction(db.session):
            for queue in to_expire:
                queue.update(end_date=datetime.now())
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/queue_cleanup_preview_htmx.html',
            resource=resource,
            days=days,
            candidates=candidates,
            pbs_data_available=pbs_data_available,
            errors=[f'Error expiring queues: {e}'],
        )

    invalidate_queue_cache()
    n = len(to_expire)
    return htmx_success_message(
        _RESOURCES_TRIGGERS,
        f'Expired {n} queue{"s" if n != 1 else ""} on {resource.resource_name}.',
    )


# ── Search helpers ─────────────────────────────────────────────────────────
# Note: user search is handled by the unified admin_dashboard.htmx_search_users
# endpoint (admin/blueprint.py) with context='fk'.


@bp.route('/htmx/search-organizations')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_search_organizations():
    """
    Search organizations for FK fields (e.g. prim_responsible_org_id on Resource).
    """
    from sam.core.organizations import Organization

    query = request.args.get('q', '').strip()
    if len(query) < 2:
        return ''

    orgs = (
        db.session.query(Organization)
        .filter(
            Organization.is_active,
            Organization.name.ilike(f'%{query}%') | Organization.acronym.ilike(f'%{query}%')
        )
        .order_by(Organization.name)
        .limit(15)
        .all()
    )

    return render_template(
        'dashboards/admin/fragments/org_search_results_fk_htmx.html',
        orgs=orgs,
    )


# ---------------------------------------------------------------------------
# Admin: Disk Resource Root Directories CRUD
# ---------------------------------------------------------------------------

def _disk_resources():
    """Return all DISK-type resources, ordered by name (used for the
    resource_id select on add/edit forms)."""
    from sam.resources.resources import Resource, ResourceType
    return (
        db.session.query(Resource)
        .join(ResourceType)
        .filter(ResourceType.resource_type == 'DISK')
        .order_by(Resource.resource_name)
        .all()
    )


@bp.route('/htmx/admin/disk-roots/new-form')
@login_required
@require_permission(Permission.EDIT_RESOURCES)
def htmx_admin_disk_root_new_form():
    """Return the create-disk-root form fragment for the modal."""
    return render_template(
        'dashboards/admin/fragments/disk_root_new_form_htmx.html',
        disk_resources=_disk_resources(),
    )


class _DiskRootFormHandler(HtmxFormHandler):
    """Shared lifecycle for the disk-root create/edit modals: checkbox
    injection, DISK-resource validation, duplicate-key translation."""

    def form_input(self):
        # Inject explicit booleans for checkboxes (absent from request.form
        # when unchecked) per CLAUDE.md §9.
        raw = {k: v for k, v in request.form.items() if v != ''}
        raw['charging_exempt'] = 'charging_exempt' in request.form
        raw['active'] = 'active' in request.form
        return raw

    def clean(self, data):
        from sam.resources.resources import Resource
        target = db.session.get(Resource, data['resource_id'])
        if (not target or not target.resource_type
                or target.resource_type.resource_type != 'DISK'):
            raise FormError('Selected resource does not exist or is not a disk resource.')
        return data

    def _write(self, data):
        raise NotImplementedError

    def perform(self, data):
        from sqlalchemy.exc import IntegrityError
        try:
            self._write(data)
        except IntegrityError:
            raise FormError(
                f'Root directory "{data["root_directory"]}" already exists.')

    def triggers(self, result):
        return _RESOURCES_TRIGGERS


class _DiskRootCreateHandler(_DiskRootFormHandler):
    schema_cls = CreateDiskResourceRootDirectoryForm
    template = 'dashboards/admin/fragments/disk_root_new_form_htmx.html'
    error_prefix = 'Error creating root directory'
    success_message = 'Root directory created.'

    def _write(self, data):
        from sam.resources.resources import DiskResourceRootDirectory
        DiskResourceRootDirectory.create(
            db.session,
            resource_id=data['resource_id'],
            root_directory=data['root_directory'],
            charging_exempt=data['charging_exempt'],
            active=data['active'],
        )

    def context(self):
        return {'disk_resources': _disk_resources()}


@bp.route('/htmx/admin/disk-roots/create', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_RESOURCES)
def htmx_admin_disk_root_create():
    """Create a new DiskResourceRootDirectory row."""
    return _DiskRootCreateHandler().handle()


@bp.route('/htmx/admin/disk-roots/<int:dr_id>/edit-form')
@login_required
@require_permission(Permission.EDIT_RESOURCES)
def htmx_admin_disk_root_edit_form(dr_id):
    """Return the edit-disk-root form fragment for the modal."""
    from sam.resources.resources import DiskResourceRootDirectory

    dr = db.session.get(DiskResourceRootDirectory, dr_id)
    if not dr:
        return '<div class="alert alert-danger">Root directory not found.</div>', 404

    return render_template(
        'dashboards/admin/fragments/disk_root_edit_form_htmx.html',
        dr=dr,
        disk_resources=_disk_resources(),
    )


class _DiskRootEditHandler(_DiskRootFormHandler):
    schema_cls = EditDiskResourceRootDirectoryForm
    template = 'dashboards/admin/fragments/disk_root_edit_form_htmx.html'
    error_prefix = 'Error updating root directory'
    success_message = 'Root directory updated.'

    def _write(self, data):
        self.dr.update(
            resource_id=data['resource_id'],
            root_directory=data['root_directory'],
            charging_exempt=data['charging_exempt'],
            active=data['active'],
        )

    def context(self):
        return {'dr': self.dr, 'disk_resources': _disk_resources()}


@bp.route('/htmx/admin/disk-roots/<int:dr_id>/edit', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_RESOURCES)
def htmx_admin_disk_root_edit(dr_id):
    """Update a DiskResourceRootDirectory row."""
    from sam.resources.resources import DiskResourceRootDirectory

    dr = db.session.get(DiskResourceRootDirectory, dr_id)
    if not dr:
        return '<div class="alert alert-danger">Root directory not found.</div>', 404

    return _DiskRootEditHandler(dr=dr).handle()


@bp.route('/htmx/admin/disk-roots/<int:dr_id>/toggle-active', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_RESOURCES)
def htmx_admin_disk_root_toggle_active(dr_id):
    """Soft-delete toggle: flip the active flag on a DiskResourceRootDirectory."""
    from sam.resources.resources import DiskResourceRootDirectory

    dr = db.session.get(DiskResourceRootDirectory, dr_id)
    if not dr:
        return '<div class="alert alert-danger">Root directory not found.</div>', 404

    new_state = not dr.is_active
    try:
        with management_transaction(db.session):
            dr.update(active=new_state)
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    msg = 'Root directory activated.' if new_state else 'Root directory deactivated.'
    return htmx_success_message(_RESOURCES_TRIGGERS, msg)


@bp.route('/htmx/admin/disk-roots/<int:dr_id>/delete', methods=['POST'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_admin_disk_root_delete(dr_id):
    """Hard-delete a DiskResourceRootDirectory row."""
    from sam.resources.resources import DiskResourceRootDirectory

    dr = db.session.get(DiskResourceRootDirectory, dr_id)
    if not dr:
        return '<div class="alert alert-danger">Root directory not found.</div>', 404

    try:
        with management_transaction(db.session):
            dr.delete()
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return htmx_success_message({'reloadResourcesCard': {}}, 'Root directory deleted.')


# ── CRUD routes — generated from specs ─────────────────────────────────────
#
# Endpoints, URL rules, templates, permissions, and not-found messages are
# identical to the hand-written routes these replace (pinned by
# tests/unit/test_admin_facilities_resources_crud.py and the route-map
# parity snapshot). Deletes for resource/machine/queue stay bespoke above —
# they retire by date rather than the active flag.

_resource_spec = partial(
    CrudSpec,
    triggers=_RESOURCES_TRIGGERS,
    edit_permission=Permission.EDIT_RESOURCES,
    create_permission=Permission.CREATE_RESOURCES,
    delete_permission=Permission.DELETE_RESOURCES,
)

_RESOURCE_CRUD_SPECS = (
    _resource_spec(
        slug='resource', name='Resource',
        model=Resource, id_param='resource_id', context_key='resource',
        edit_schema=EditResourceForm, create_schema=CreateResourceForm,
        edit_kwargs=lambda data: dict(
            description=data['description'],
            commission_date=datetime.combine(data['commission_date'], datetime.min.time()),
            decommission_date=data['decommission_date'],
            charging_exempt=data['charging_exempt'],
        ),
        create_kwargs=lambda data: dict(
            resource_name=data['resource_name'],
            resource_type_id=data['resource_type_id'],
            description=data['description'],
            commission_date=datetime.combine(data['commission_date'], datetime.min.time()) if data.get('commission_date') else None,
            charging_exempt=data['charging_exempt'],
        ),
        create_context=lambda: {'resource_types': _all_resource_types()},
        actions=('edit', 'create'),   # delete is bespoke (htmx_resource_delete)
    ),
    _resource_spec(
        slug='resource-type', name='Resource type',
        model=ResourceType, id_param='resource_type_id',
        context_key='resource_type',
        edit_schema=EditResourceTypeForm, create_schema=CreateResourceTypeForm,
        edit_fields=('grace_period_days',),
        create_fields=('resource_type', 'grace_period_days'),
    ),
    _resource_spec(
        slug='machine', name='Machine',
        model=Machine, id_param='machine_id', context_key='machine',
        edit_schema=EditMachineForm, create_schema=CreateMachineForm,
        edit_kwargs=lambda data: dict(
            description=data['description'],
            cpus_per_node=data['cpus_per_node'],
            commission_date=datetime.combine(data['commission_date'], datetime.min.time()),
            decommission_date=data['decommission_date'],
        ),
        create_kwargs=lambda data: dict(
            name=data['name'],
            resource_id=data['resource_id'],
            description=data['description'],
            cpus_per_node=data['cpus_per_node'],
            commission_date=datetime.combine(data['commission_date'], datetime.min.time()) if data.get('commission_date') else None,
        ),
        create_context=lambda: {'resources': _active_resources()},
        actions=('edit', 'create'),   # delete is bespoke (htmx_machine_delete)
    ),
)

for _spec in _RESOURCE_CRUD_SPECS:
    register_crud(bp, _spec)
