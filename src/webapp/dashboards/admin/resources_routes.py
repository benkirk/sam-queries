"""
Admin dashboard — Resource management routes.

Covers: Resources, Resource Types, Machines, Queues.
"""

from flask import render_template, request
from webapp.utils.htmx import htmx_success
from flask_login import login_required
from datetime import datetime
from webapp.api.helpers import parse_input_end_date

from webapp.extensions import db
from webapp.utils.rbac import require_permission, Permission
from sam.manage import management_transaction

from .blueprint import bp


# ── Resource Management Card ───────────────────────────────────────────────


@bp.route('/htmx/resources')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
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

    return render_template(
        'dashboards/admin/fragments/resources_card.html',
        resources=resources,
        resource_types=resource_types,
        machines=machines,
        queues=queues,
        is_admin=True,
        now=now,
        active_only=active_only,
    )


# ── Resource Edit ──────────────────────────────────────────────────────────


@bp.route('/htmx/resource-edit-form/<int:resource_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
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
@require_permission(Permission.EDIT_PROJECTS)
def htmx_resource_edit(resource_id):
    """Update a resource."""
    from sam.resources.resources import Resource

    resource = db.session.get(Resource, resource_id)
    if not resource:
        return '<div class="alert alert-danger">Resource not found</div>', 404

    errors = []

    commission_str = request.form.get('commission_date', '').strip()
    decommission_str = request.form.get('decommission_date', '').strip()
    description = request.form.get('description', '').strip()
    charging_exempt = bool(request.form.get('charging_exempt'))

    commission_date = None
    if commission_str:
        try:
            commission_date = datetime.strptime(commission_str, '%Y-%m-%d')
        except ValueError:
            errors.append('Invalid commission date format.')
    else:
        errors.append('Commission date is required.')

    decommission_date = None
    if decommission_str:
        try:
            decommission_date = parse_input_end_date(decommission_str)
            if commission_date and decommission_date <= commission_date:
                errors.append('Decommission date must be after commission date.')
        except ValueError:
            errors.append('Invalid decommission date format.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_resource_form_htmx.html',
            resource=resource,
            errors=errors,
            form=request.form,
        )

    try:
        with management_transaction(db.session):
            resource.update(
                description=description,
                commission_date=commission_date,
                decommission_date=decommission_date,
                charging_exempt=charging_exempt,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_resource_form_htmx.html',
            resource=resource,
            errors=[f'Error updating resource: {e}'],
            form=request.form,
        )

    return htmx_success('dashboards/admin/fragments/resource_edit_success_htmx.html', {'closeActiveModal': {}, 'reloadResourcesCard': {}})


# ── Resource Create ────────────────────────────────────────────────────────


@bp.route('/htmx/resource-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_resource_create_form():
    """Return the resource create form fragment (loaded into modal)."""
    from sam.resources.resources import ResourceType

    resource_types = db.session.query(ResourceType).order_by(ResourceType.resource_type).all()

    return render_template(
        'dashboards/admin/fragments/create_resource_form_htmx.html',
        resource_types=resource_types,
    )


@bp.route('/htmx/resource-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_resource_create():
    """Create a new resource."""
    from sam.resources.resources import Resource, ResourceType

    errors = []

    resource_name = request.form.get('resource_name', '').strip()
    resource_type_id_str = request.form.get('resource_type_id', '').strip()
    description = request.form.get('description', '').strip()
    charging_exempt = bool(request.form.get('charging_exempt'))
    commission_str = request.form.get('commission_date', '').strip()

    if not resource_name:
        errors.append('Resource name is required.')

    resource_type_id = None
    if resource_type_id_str:
        try:
            resource_type_id = int(resource_type_id_str)
        except ValueError:
            errors.append('Invalid resource type selection.')
    else:
        errors.append('Resource type is required.')

    commission_date = None
    if commission_str:
        try:
            commission_date = datetime.strptime(commission_str, '%Y-%m-%d')
        except ValueError:
            errors.append('Invalid commission date format.')

    def _reload_form(extra_errors=None):
        resource_types = db.session.query(ResourceType).order_by(ResourceType.resource_type).all()
        return render_template(
            'dashboards/admin/fragments/create_resource_form_htmx.html',
            resource_types=resource_types,
            errors=(extra_errors or []) + errors,
            form=request.form,
        )

    if errors:
        return _reload_form()

    try:
        with management_transaction(db.session):
            Resource.create(
                db.session,
                resource_name=resource_name,
                resource_type_id=resource_type_id,
                description=description or None,
                commission_date=commission_date,
                charging_exempt=charging_exempt,
            )
    except Exception as e:
        return _reload_form([f'Error creating resource: {e}'])

    return htmx_success('dashboards/admin/fragments/resource_edit_success_htmx.html', {'closeActiveModal': {}, 'reloadResourcesCard': {}})


# ── Resource Delete ────────────────────────────────────────────────────────


@bp.route('/htmx/resource-delete/<int:resource_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_resource_delete(resource_id):
    """Soft-delete (decommission) a resource."""
    from sam.resources.resources import Resource

    resource = db.session.get(Resource, resource_id)
    if not resource:
        return '<div class="alert alert-danger">Resource not found</div>', 404

    try:
        with management_transaction(db.session):
            resource.update(decommission_date=datetime.now())
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return ''  # hx-swap="delete" removes the row


# ── Resource Type Edit ─────────────────────────────────────────────────────


@bp.route('/htmx/resource-type-edit-form/<int:resource_type_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
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
@require_permission(Permission.EDIT_PROJECTS)
def htmx_resource_type_edit(resource_type_id):
    """Update a resource type."""
    from sam.resources.resources import ResourceType

    resource_type = db.session.get(ResourceType, resource_type_id)
    if not resource_type:
        return '<div class="alert alert-danger">Resource type not found</div>', 404

    errors = []

    grace_period_str = request.form.get('grace_period_days', '').strip()

    grace_period_days = None
    if grace_period_str:
        try:
            grace_period_days = int(grace_period_str)
            if grace_period_days < 0:
                errors.append('Grace period days must be >= 0.')
        except ValueError:
            errors.append('Grace period days must be a whole number.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_resource_type_form_htmx.html',
            resource_type=resource_type,
            errors=errors,
            form=request.form,
        )

    try:
        with management_transaction(db.session):
            resource_type.update(
                grace_period_days=grace_period_days,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_resource_type_form_htmx.html',
            resource_type=resource_type,
            errors=[f'Error updating resource type: {e}'],
            form=request.form,
        )

    return htmx_success('dashboards/admin/fragments/resource_edit_success_htmx.html', {'closeActiveModal': {}, 'reloadResourcesCard': {}})


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

    errors = []

    resource_type_name = request.form.get('resource_type', '').strip()
    grace_period_str = request.form.get('grace_period_days', '').strip()

    if not resource_type_name:
        errors.append('Type name is required.')

    grace_period_days = None
    if grace_period_str:
        try:
            grace_period_days = int(grace_period_str)
            if grace_period_days < 0:
                errors.append('Grace period days must be >= 0.')
        except ValueError:
            errors.append('Grace period days must be a whole number.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/create_resource_type_form_htmx.html',
            errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            ResourceType.create(
                db.session,
                resource_type=resource_type_name,
                grace_period_days=grace_period_days,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/create_resource_type_form_htmx.html',
            errors=[f'Error creating resource type: {e}'], form=request.form,
        )

    return htmx_success('dashboards/admin/fragments/resource_edit_success_htmx.html', {'closeActiveModal': {}, 'reloadResourcesCard': {}})


# ── Resource Type Delete ───────────────────────────────────────────────────


@bp.route('/htmx/resource-type-delete/<int:resource_type_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_resource_type_delete(resource_type_id):
    """Soft-delete (deactivate) a resource type."""
    from sam.resources.resources import ResourceType

    resource_type = db.session.get(ResourceType, resource_type_id)
    if not resource_type:
        return '<div class="alert alert-danger">Resource type not found</div>', 404

    try:
        with management_transaction(db.session):
            resource_type.update(active=False)
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return ''


# ── Machine Edit ───────────────────────────────────────────────────────────


@bp.route('/htmx/machine-edit-form/<int:machine_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
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
@require_permission(Permission.EDIT_PROJECTS)
def htmx_machine_edit(machine_id):
    """Update a machine."""
    from sam.resources.machines import Machine

    machine = db.session.get(Machine, machine_id)
    if not machine:
        return '<div class="alert alert-danger">Machine not found</div>', 404

    errors = []

    description = request.form.get('description', '').strip()
    cpus_str = request.form.get('cpus_per_node', '').strip()
    commission_str = request.form.get('commission_date', '').strip()
    decommission_str = request.form.get('decommission_date', '').strip()

    commission_date = None
    if commission_str:
        try:
            commission_date = datetime.strptime(commission_str, '%Y-%m-%d')
        except ValueError:
            errors.append('Invalid commission date format.')
    else:
        errors.append('Commission date is required.')

    decommission_date = None
    if decommission_str:
        try:
            decommission_date = parse_input_end_date(decommission_str)
            if commission_date and decommission_date <= commission_date:
                errors.append('Decommission date must be after commission date.')
        except ValueError:
            errors.append('Invalid decommission date format.')

    cpus_per_node = None
    if cpus_str:
        try:
            cpus_per_node = int(cpus_str)
            if cpus_per_node <= 0:
                errors.append('CPUs per node must be a positive integer.')
        except ValueError:
            errors.append('CPUs per node must be a whole number.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_machine_form_htmx.html',
            machine=machine,
            errors=errors,
            form=request.form,
        )

    try:
        with management_transaction(db.session):
            machine.update(
                description=description,
                cpus_per_node=cpus_per_node,
                commission_date=commission_date,
                decommission_date=decommission_date,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_machine_form_htmx.html',
            machine=machine,
            errors=[f'Error updating machine: {e}'],
            form=request.form,
        )

    return htmx_success('dashboards/admin/fragments/resource_edit_success_htmx.html', {'closeActiveModal': {}, 'reloadResourcesCard': {}})


# ── Machine Create ─────────────────────────────────────────────────────────


@bp.route('/htmx/machine-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_machine_create_form():
    """Return the machine create form fragment (loaded into modal)."""
    from sam.resources.resources import Resource

    resources = db.session.query(Resource).filter(Resource.is_active).order_by(Resource.resource_name).all()

    return render_template(
        'dashboards/admin/fragments/create_machine_form_htmx.html',
        resources=resources,
    )


@bp.route('/htmx/machine-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_machine_create():
    """Create a new machine."""
    from sam.resources.machines import Machine
    from sam.resources.resources import Resource

    errors = []

    name = request.form.get('name', '').strip()
    resource_id_str = request.form.get('resource_id', '').strip()
    description = request.form.get('description', '').strip()
    cpus_str = request.form.get('cpus_per_node', '').strip()
    commission_str = request.form.get('commission_date', '').strip()

    if not name:
        errors.append('Machine name is required.')

    resource_id = None
    if resource_id_str:
        try:
            resource_id = int(resource_id_str)
        except ValueError:
            errors.append('Invalid resource selection.')
    else:
        errors.append('Resource is required.')

    commission_date = None
    if commission_str:
        try:
            commission_date = datetime.strptime(commission_str, '%Y-%m-%d')
        except ValueError:
            errors.append('Invalid commission date format.')

    cpus_per_node = None
    if cpus_str:
        try:
            cpus_per_node = int(cpus_str)
            if cpus_per_node <= 0:
                errors.append('CPUs per node must be a positive integer.')
        except ValueError:
            errors.append('CPUs per node must be a whole number.')

    def _reload_form(extra_errors=None):
        resources = db.session.query(Resource).filter(Resource.is_active).order_by(Resource.resource_name).all()
        return render_template(
            'dashboards/admin/fragments/create_machine_form_htmx.html',
            resources=resources,
            errors=(extra_errors or []) + errors,
            form=request.form,
        )

    if errors:
        return _reload_form()

    try:
        with management_transaction(db.session):
            Machine.create(
                db.session,
                name=name,
                resource_id=resource_id,
                description=description or None,
                cpus_per_node=cpus_per_node,
                commission_date=commission_date,
            )
    except Exception as e:
        return _reload_form([f'Error creating machine: {e}'])

    return htmx_success('dashboards/admin/fragments/resource_edit_success_htmx.html', {'closeActiveModal': {}, 'reloadResourcesCard': {}})


# ── Machine Delete ─────────────────────────────────────────────────────────


@bp.route('/htmx/machine-delete/<int:machine_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_machine_delete(machine_id):
    """Soft-delete (decommission) a machine."""
    from sam.resources.machines import Machine

    machine = db.session.get(Machine, machine_id)
    if not machine:
        return '<div class="alert alert-danger">Machine not found</div>', 404

    try:
        with management_transaction(db.session):
            machine.update(decommission_date=datetime.now())
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return ''


# ── Queue Edit ─────────────────────────────────────────────────────────────


@bp.route('/htmx/queue-edit-form/<int:queue_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
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
@require_permission(Permission.EDIT_PROJECTS)
def htmx_queue_edit(queue_id):
    """Update a queue."""
    from sam.resources.machines import Queue

    queue = db.session.get(Queue, queue_id)
    if not queue:
        return '<div class="alert alert-danger">Queue not found</div>', 404

    errors = []

    description = request.form.get('description', '').strip()
    wallclock_str = request.form.get('wall_clock_hours_limit', '').strip()
    end_date_str = request.form.get('end_date', '').strip()

    wall_clock_hours_limit = None
    if wallclock_str:
        try:
            wall_clock_hours_limit = float(wallclock_str)
            if wall_clock_hours_limit <= 0:
                errors.append('Wallclock limit must be a positive number.')
        except ValueError:
            errors.append('Wallclock limit must be a number.')
    else:
        errors.append('Wallclock limit (hours) is required.')

    end_date = None
    if end_date_str:
        try:
            end_date = parse_input_end_date(end_date_str)
            if queue.start_date and end_date <= queue.start_date:
                errors.append('End date must be after start date.')
        except ValueError:
            errors.append('Invalid end date format.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_queue_form_htmx.html',
            queue=queue,
            errors=errors,
            form=request.form,
        )

    try:
        with management_transaction(db.session):
            queue.update(
                description=description,
                wall_clock_hours_limit=wall_clock_hours_limit,
                end_date=end_date,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_queue_form_htmx.html',
            queue=queue,
            errors=[f'Error updating queue: {e}'],
            form=request.form,
        )

    return htmx_success('dashboards/admin/fragments/resource_edit_success_htmx.html', {'closeActiveModal': {}, 'reloadResourcesCard': {}})


# ── Queue Delete ───────────────────────────────────────────────────────────


@bp.route('/htmx/queue-delete/<int:queue_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_queue_delete(queue_id):
    """Soft-delete (expire) a queue by setting end_date to now."""
    from sam.resources.machines import Queue

    queue = db.session.get(Queue, queue_id)
    if not queue:
        return '<div class="alert alert-danger">Queue not found</div>', 404

    try:
        with management_transaction(db.session):
            queue.update(end_date=datetime.now())
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return ''


# ── Search helpers ─────────────────────────────────────────────────────────


@bp.route('/htmx/search-users')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_search_users():
    """
    Search users for FK fields (e.g. prim_sys_admin_user_id on Resource).
    Returns JSON-friendly option list as HTML fragment.
    """
    from sam.queries.users import search_users_by_pattern

    query = request.args.get('q', '').strip()
    if len(query) < 2:
        return ''

    users = search_users_by_pattern(db.session, query, limit=15, active_only=True)

    return render_template(
        'dashboards/admin/fragments/user_search_results_fk_htmx.html',
        users=users,
    )


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
