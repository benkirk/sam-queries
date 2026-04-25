"""
Admin dashboard — Resource management routes.

Covers: Resources, Resource Types, Machines, Queues.
"""

from flask import render_template, request
from flask_login import login_required
from datetime import datetime

from webapp.utils.htmx import (
    handle_htmx_form_post,
    handle_htmx_soft_delete,
    htmx_not_found,
    htmx_success_message,
)
from webapp.extensions import db
from webapp.utils.rbac import (
    require_permission, require_permission_any_facility, Permission,
)
from sam.manage import management_transaction
from sam.schemas.forms.resources import (
    EditResourceForm, CreateResourceForm,
    EditResourceTypeForm, CreateResourceTypeForm,
    EditMachineForm, CreateMachineForm, EditQueueForm,
    CreateDiskResourceRootDirectoryForm, EditDiskResourceRootDirectoryForm,
)

from .blueprint import bp


_RESOURCES_TRIGGERS = {'closeActiveModal': {}, 'reloadResourcesCard': {}}


def _active_resources():
    from sam.resources.resources import Resource
    return (
        db.session.query(Resource)
        .filter(Resource.is_active)
        .order_by(Resource.resource_name)
        .all()
    )


def _all_resource_types():
    from sam.resources.resources import ResourceType
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

    active_only = request.args.get('active_only') == '1'
    now = datetime.now()

    resource_q = db.session.query(Resource).order_by(Resource.resource_name)
    if active_only:
        resource_q = resource_q.filter(Resource.is_active)
    resources = resource_q.all()

    resource_types = db.session.query(ResourceType).order_by(ResourceType.resource_type).all()

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
        machines=machines,
        queues=queues,
        exemptions=exemptions,
        disk_resources_with_roots=disk_resources_with_roots,
        is_admin=True,
        now=now,
        active_only=active_only,
    )


# ── Resource Edit ──────────────────────────────────────────────────────────


@bp.route('/htmx/resource-edit-form/<int:resource_id>')
@login_required
@require_permission(Permission.EDIT_RESOURCES)
def htmx_resource_edit_form(resource_id):
    """Return the resource edit form fragment (loaded into modal)."""
    from sam.resources.resources import Resource

    resource = db.session.get(Resource, resource_id)
    if not resource:
        return '<div class="alert alert-warning">Resource not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_resource_form_htmx.html',
        resource=resource,
    )


@bp.route('/htmx/resource-edit/<int:resource_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_RESOURCES)
def htmx_resource_edit(resource_id):
    """Update a resource."""
    from sam.resources.resources import Resource

    resource = db.session.get(Resource, resource_id)
    if not resource:
        return htmx_not_found('Resource')

    return handle_htmx_form_post(
        schema_cls=EditResourceForm,
        template='dashboards/admin/fragments/edit_resource_form_htmx.html',
        success_triggers=_RESOURCES_TRIGGERS,
        error_prefix='Error updating resource',
        extra_context={'resource': resource},
        do_action=lambda data: resource.update(
            description=data['description'],
            commission_date=datetime.combine(data['commission_date'], datetime.min.time()),
            decommission_date=data['decommission_date'],
            charging_exempt=data['charging_exempt'],
        ),
    )


# ── Resource Create ────────────────────────────────────────────────────────


@bp.route('/htmx/resource-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_resource_create_form():
    """Return the resource create form fragment (loaded into modal)."""
    return render_template(
        'dashboards/admin/fragments/create_resource_form_htmx.html',
        resource_types=_all_resource_types(),
    )


@bp.route('/htmx/resource-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_resource_create():
    """Create a new resource."""
    from sam.resources.resources import Resource

    return handle_htmx_form_post(
        schema_cls=CreateResourceForm,
        template='dashboards/admin/fragments/create_resource_form_htmx.html',
        success_triggers=_RESOURCES_TRIGGERS,
        error_prefix='Error creating resource',
        context_fn=lambda: {'resource_types': _all_resource_types()},
        do_action=lambda data: Resource.create(
            db.session,
            resource_name=data['resource_name'],
            resource_type_id=data['resource_type_id'],
            description=data['description'],
            commission_date=datetime.combine(data['commission_date'], datetime.min.time()) if data.get('commission_date') else None,
            charging_exempt=data['charging_exempt'],
        ),
    )


# ── Resource Delete ────────────────────────────────────────────────────────


@bp.route('/htmx/resource-delete/<int:resource_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_resource_delete(resource_id):
    """Soft-delete (decommission) a resource."""
    from sam.resources.resources import Resource

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


# ── Resource Type Edit ─────────────────────────────────────────────────────


@bp.route('/htmx/resource-type-edit-form/<int:resource_type_id>')
@login_required
@require_permission(Permission.EDIT_RESOURCES)
def htmx_resource_type_edit_form(resource_type_id):
    """Return the resource type edit form fragment (loaded into modal)."""
    from sam.resources.resources import ResourceType

    resource_type = db.session.get(ResourceType, resource_type_id)
    if not resource_type:
        return '<div class="alert alert-warning">Resource type not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_resource_type_form_htmx.html',
        resource_type=resource_type,
    )


@bp.route('/htmx/resource-type-edit/<int:resource_type_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_RESOURCES)
def htmx_resource_type_edit(resource_type_id):
    """Update a resource type."""
    from sam.resources.resources import ResourceType

    resource_type = db.session.get(ResourceType, resource_type_id)
    if not resource_type:
        return htmx_not_found('Resource type')

    return handle_htmx_form_post(
        schema_cls=EditResourceTypeForm,
        template='dashboards/admin/fragments/edit_resource_type_form_htmx.html',
        success_triggers=_RESOURCES_TRIGGERS,
        error_prefix='Error updating resource type',
        extra_context={'resource_type': resource_type},
        do_action=lambda data: resource_type.update(
            grace_period_days=data['grace_period_days'],
        ),
    )


# ── Resource Type Create ───────────────────────────────────────────────────


@bp.route('/htmx/resource-type-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_resource_type_create_form():
    """Return the resource type create form fragment (loaded into modal)."""
    return render_template('dashboards/admin/fragments/create_resource_type_form_htmx.html')


@bp.route('/htmx/resource-type-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_resource_type_create():
    """Create a new resource type."""
    from sam.resources.resources import ResourceType

    return handle_htmx_form_post(
        schema_cls=CreateResourceTypeForm,
        template='dashboards/admin/fragments/create_resource_type_form_htmx.html',
        success_triggers=_RESOURCES_TRIGGERS,
        error_prefix='Error creating resource type',
        do_action=lambda data: ResourceType.create(
            db.session,
            resource_type=data['resource_type'],
            grace_period_days=data['grace_period_days'],
        ),
    )


# ── Resource Type Delete ───────────────────────────────────────────────────


@bp.route('/htmx/resource-type-delete/<int:resource_type_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_resource_type_delete(resource_type_id):
    """Soft-delete (deactivate) a resource type."""
    from sam.resources.resources import ResourceType

    resource_type = db.session.get(ResourceType, resource_type_id)
    if not resource_type:
        return htmx_not_found('Resource type')
    return handle_htmx_soft_delete(resource_type, name='Resource type')


# ── Machine Edit ───────────────────────────────────────────────────────────


@bp.route('/htmx/machine-edit-form/<int:machine_id>')
@login_required
@require_permission(Permission.EDIT_RESOURCES)
def htmx_machine_edit_form(machine_id):
    """Return the machine edit form fragment (loaded into modal)."""
    from sam.resources.machines import Machine

    machine = db.session.get(Machine, machine_id)
    if not machine:
        return '<div class="alert alert-warning">Machine not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_machine_form_htmx.html',
        machine=machine,
    )


@bp.route('/htmx/machine-edit/<int:machine_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_RESOURCES)
def htmx_machine_edit(machine_id):
    """Update a machine."""
    from sam.resources.machines import Machine

    machine = db.session.get(Machine, machine_id)
    if not machine:
        return htmx_not_found('Machine')

    return handle_htmx_form_post(
        schema_cls=EditMachineForm,
        template='dashboards/admin/fragments/edit_machine_form_htmx.html',
        success_triggers=_RESOURCES_TRIGGERS,
        error_prefix='Error updating machine',
        extra_context={'machine': machine},
        do_action=lambda data: machine.update(
            description=data['description'],
            cpus_per_node=data['cpus_per_node'],
            commission_date=datetime.combine(data['commission_date'], datetime.min.time()),
            decommission_date=data['decommission_date'],
        ),
    )


# ── Machine Create ─────────────────────────────────────────────────────────


@bp.route('/htmx/machine-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_machine_create_form():
    """Return the machine create form fragment (loaded into modal)."""
    return render_template(
        'dashboards/admin/fragments/create_machine_form_htmx.html',
        resources=_active_resources(),
    )


@bp.route('/htmx/machine-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_machine_create():
    """Create a new machine."""
    from sam.resources.machines import Machine

    return handle_htmx_form_post(
        schema_cls=CreateMachineForm,
        template='dashboards/admin/fragments/create_machine_form_htmx.html',
        success_triggers=_RESOURCES_TRIGGERS,
        error_prefix='Error creating machine',
        context_fn=lambda: {'resources': _active_resources()},
        do_action=lambda data: Machine.create(
            db.session,
            name=data['name'],
            resource_id=data['resource_id'],
            description=data['description'],
            cpus_per_node=data['cpus_per_node'],
            commission_date=datetime.combine(data['commission_date'], datetime.min.time()) if data.get('commission_date') else None,
        ),
    )


# ── Machine Delete ─────────────────────────────────────────────────────────


@bp.route('/htmx/machine-delete/<int:machine_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_machine_delete(machine_id):
    """Soft-delete (decommission) a machine."""
    from sam.resources.machines import Machine

    machine = db.session.get(Machine, machine_id)
    if not machine:
        return htmx_not_found('Machine')

    try:
        with management_transaction(db.session):
            machine.update(decommission_date=datetime.now())
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return ''


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


@bp.route('/htmx/queue-edit/<int:queue_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_RESOURCES)
def htmx_queue_edit(queue_id):
    """Update a queue."""
    from sam.resources.machines import Queue

    queue = db.session.get(Queue, queue_id)
    if not queue:
        return htmx_not_found('Queue')

    # Cross-field check requiring the ORM object's start_date — done after
    # validation so this route uses the schema directly rather than the helper.
    from marshmallow import ValidationError
    try:
        data = EditQueueForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/edit_queue_form_htmx.html',
            queue=queue,
            errors=EditQueueForm.flatten_errors(e.messages),
            form=request.form,
        )

    if data.get('end_date') and queue.start_date and data['end_date'] <= queue.start_date:
        return render_template(
            'dashboards/admin/fragments/edit_queue_form_htmx.html',
            queue=queue,
            errors=['End date must be after start date.'],
            form=request.form,
        )

    try:
        with management_transaction(db.session):
            queue.update(
                description=data['description'],
                wall_clock_hours_limit=data['wall_clock_hours_limit'],
                end_date=data['end_date'],
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_queue_form_htmx.html',
            queue=queue,
            errors=[f'Error updating queue: {e}'],
            form=request.form,
        )

    return htmx_success_message(_RESOURCES_TRIGGERS, 'Saved successfully.')


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

    return ''


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


@bp.route('/htmx/admin/disk-roots/create', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_RESOURCES)
def htmx_admin_disk_root_create():
    """Create a new DiskResourceRootDirectory row."""
    from marshmallow import ValidationError
    from sqlalchemy.exc import IntegrityError
    from sam.resources.resources import Resource, ResourceType, DiskResourceRootDirectory

    def _reload(errors, form=None):
        return render_template(
            'dashboards/admin/fragments/disk_root_new_form_htmx.html',
            disk_resources=_disk_resources(),
            errors=errors,
            form=form if form is not None else request.form,
        )

    try:
        data = CreateDiskResourceRootDirectoryForm().load(request.form)
    except ValidationError as e:
        return _reload(CreateDiskResourceRootDirectoryForm.flatten_errors(e.messages))

    target = db.session.get(Resource, data['resource_id'])
    if not target or not target.resource_type or target.resource_type.resource_type != 'DISK':
        return _reload(['Selected resource does not exist or is not a disk resource.'])

    try:
        with management_transaction(db.session):
            DiskResourceRootDirectory.create(
                db.session,
                resource_id=data['resource_id'],
                root_directory=data['root_directory'],
                charging_exempt=data['charging_exempt'],
            )
    except IntegrityError:
        db.session.rollback()
        return _reload([f'Root directory "{data["root_directory"]}" already exists.'])
    except Exception as e:
        return _reload([f'Error creating root directory: {e}'])

    return htmx_success_message(_RESOURCES_TRIGGERS, 'Root directory created.')


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


@bp.route('/htmx/admin/disk-roots/<int:dr_id>/edit', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_RESOURCES)
def htmx_admin_disk_root_edit(dr_id):
    """Update a DiskResourceRootDirectory row."""
    from marshmallow import ValidationError
    from sqlalchemy.exc import IntegrityError
    from sam.resources.resources import Resource, DiskResourceRootDirectory

    dr = db.session.get(DiskResourceRootDirectory, dr_id)
    if not dr:
        return '<div class="alert alert-danger">Root directory not found.</div>', 404

    def _reload(errors, form=None):
        return render_template(
            'dashboards/admin/fragments/disk_root_edit_form_htmx.html',
            dr=dr,
            disk_resources=_disk_resources(),
            errors=errors,
            form=form if form is not None else request.form,
        )

    try:
        data = EditDiskResourceRootDirectoryForm().load(request.form)
    except ValidationError as e:
        return _reload(EditDiskResourceRootDirectoryForm.flatten_errors(e.messages))

    target = db.session.get(Resource, data['resource_id'])
    if not target or not target.resource_type or target.resource_type.resource_type != 'DISK':
        return _reload(['Selected resource does not exist or is not a disk resource.'])

    try:
        with management_transaction(db.session):
            dr.update(
                resource_id=data['resource_id'],
                root_directory=data['root_directory'],
                charging_exempt=data['charging_exempt'],
            )
    except IntegrityError:
        db.session.rollback()
        return _reload([f'Root directory "{data["root_directory"]}" already exists.'])
    except Exception as e:
        return _reload([f'Error updating root directory: {e}'])

    return htmx_success_message(_RESOURCES_TRIGGERS, 'Root directory updated.')


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
