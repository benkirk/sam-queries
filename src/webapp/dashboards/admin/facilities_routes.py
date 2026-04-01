"""
Admin dashboard — Facility management routes.

Covers: Facilities, Panels, Panel Sessions, Allocation Types.
"""

from flask import render_template, request
from flask_login import login_required
from datetime import datetime

from webapp.extensions import db
from webapp.utils.rbac import require_permission, Permission
from sam.manage import management_transaction

from .blueprint import bp


# ── Facility Card ──────────────────────────────────────────────────────────


@bp.route('/htmx/facilities')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_facilities_card():
    """
    Return the Facility card body fragment with four tabs:
    Facilities, Panels, Panel Sessions, Allocation Types.
    Lazy-loaded when the Facility collapsible section is first expanded.
    """
    from sam.resources.facilities import Facility

    active_only = request.args.get('active_only') == '1'

    facility_q = db.session.query(Facility).order_by(Facility.facility_name)
    if active_only:
        facility_q = facility_q.filter(Facility.is_active)
    facilities = facility_q.all()

    return render_template(
        'dashboards/admin/fragments/facility_card.html',
        facilities=facilities,
        is_admin=True,
        active_only=active_only,
    )


# ── Facility Edit ──────────────────────────────────────────────────────────


@bp.route('/htmx/facility-edit-form/<int:facility_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_facility_edit_form(facility_id):
    """Return the facility edit form fragment (loaded into modal)."""
    from sam.resources.facilities import Facility

    facility = db.session.get(Facility, facility_id)
    if not facility:
        return '<div class="alert alert-warning">Facility not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_facility_form_htmx.html',
        facility=facility,
    )


@bp.route('/htmx/facility-edit/<int:facility_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_facility_edit(facility_id):
    """Update a facility."""
    from sam.resources.facilities import Facility

    facility = db.session.get(Facility, facility_id)
    if not facility:
        return '<div class="alert alert-danger">Facility not found</div>', 404

    errors = []

    description = request.form.get('description', '').strip()
    fair_share_str = request.form.get('fair_share_percentage', '').strip()
    active = bool(request.form.get('active'))

    if not description:
        errors.append('Description is required.')

    fair_share_percentage = None
    if fair_share_str:
        try:
            fair_share_percentage = float(fair_share_str)
            if not (0 <= fair_share_percentage <= 100):
                errors.append('Fair share percentage must be between 0 and 100.')
        except ValueError:
            errors.append('Fair share percentage must be a number.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_facility_form_htmx.html',
            facility=facility,
            errors=errors,
            form=request.form,
        )

    try:
        with management_transaction(db.session):
            facility.update(
                description=description,
                fair_share_percentage=fair_share_percentage,
                active=active,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_facility_form_htmx.html',
            facility=facility,
            errors=[f'Error updating facility: {e}'],
            form=request.form,
        )

    return render_template('dashboards/admin/fragments/facility_edit_success_htmx.html')


# ── Facility Create ────────────────────────────────────────────────────────


@bp.route('/htmx/facility-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_facility_create_form():
    """Return the facility create form fragment (loaded into modal)."""
    return render_template('dashboards/admin/fragments/create_facility_form_htmx.html')


@bp.route('/htmx/facility-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_facility_create():
    """Create a new facility."""
    from sam.resources.facilities import Facility

    errors = []

    facility_name = request.form.get('facility_name', '').strip()
    description = request.form.get('description', '').strip()
    code = request.form.get('code', '').strip()
    fair_share_str = request.form.get('fair_share_percentage', '').strip()

    if not facility_name:
        errors.append('Facility name is required.')
    if not description:
        errors.append('Description is required.')

    fair_share_percentage = None
    if fair_share_str:
        try:
            fair_share_percentage = float(fair_share_str)
            if not (0 <= fair_share_percentage <= 100):
                errors.append('Fair share percentage must be between 0 and 100.')
        except ValueError:
            errors.append('Fair share percentage must be a number.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/create_facility_form_htmx.html',
            errors=errors, form=request.form,
        )

    try:
        with management_transaction(db.session):
            Facility.create(
                db.session,
                facility_name=facility_name,
                description=description,
                code=code or None,
                fair_share_percentage=fair_share_percentage,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/create_facility_form_htmx.html',
            errors=[f'Error creating facility: {e}'], form=request.form,
        )

    return render_template('dashboards/admin/fragments/facility_edit_success_htmx.html')


# ── Facility Delete ────────────────────────────────────────────────────────


@bp.route('/htmx/facility-delete/<int:facility_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_facility_delete(facility_id):
    """Soft-delete (deactivate) a facility."""
    from sam.resources.facilities import Facility

    facility = db.session.get(Facility, facility_id)
    if not facility:
        return '<div class="alert alert-danger">Facility not found</div>', 404

    try:
        with management_transaction(db.session):
            facility.update(active=False)
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return ''


# ── Panel Edit ─────────────────────────────────────────────────────────────


@bp.route('/htmx/panel-edit-form/<int:panel_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_panel_edit_form(panel_id):
    """Return the panel edit form fragment (loaded into modal)."""
    from sam.resources.facilities import Panel

    panel = db.session.get(Panel, panel_id)
    if not panel:
        return '<div class="alert alert-warning">Panel not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_panel_form_htmx.html',
        panel=panel,
    )


@bp.route('/htmx/panel-edit/<int:panel_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_panel_edit(panel_id):
    """Update a panel."""
    from sam.resources.facilities import Panel

    panel = db.session.get(Panel, panel_id)
    if not panel:
        return '<div class="alert alert-danger">Panel not found</div>', 404

    description = request.form.get('description', '').strip()
    active = bool(request.form.get('active'))

    try:
        with management_transaction(db.session):
            panel.update(
                description=description or None,
                active=active,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_panel_form_htmx.html',
            panel=panel,
            errors=[f'Error updating panel: {e}'],
            form=request.form,
        )

    return render_template('dashboards/admin/fragments/facility_edit_success_htmx.html')


# ── Panel Create ───────────────────────────────────────────────────────────


@bp.route('/htmx/panel-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_panel_create_form():
    """Return the panel create form fragment (loaded into modal)."""
    from sam.resources.facilities import Facility

    facilities = db.session.query(Facility).filter(Facility.is_active).order_by(Facility.facility_name).all()

    return render_template(
        'dashboards/admin/fragments/create_panel_form_htmx.html',
        facilities=facilities,
    )


@bp.route('/htmx/panel-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_panel_create():
    """Create a new panel."""
    from sam.resources.facilities import Facility, Panel

    errors = []

    panel_name = request.form.get('panel_name', '').strip()
    description = request.form.get('description', '').strip()
    facility_id_str = request.form.get('facility_id', '').strip()

    if not panel_name:
        errors.append('Panel name is required.')

    facility_id = None
    if facility_id_str:
        try:
            facility_id = int(facility_id_str)
        except ValueError:
            errors.append('Invalid facility selection.')
    else:
        errors.append('Facility is required.')

    def _reload_form(extra_errors=None):
        facilities = db.session.query(Facility).filter(Facility.is_active).order_by(Facility.facility_name).all()
        return render_template(
            'dashboards/admin/fragments/create_panel_form_htmx.html',
            facilities=facilities,
            errors=(extra_errors or []) + errors,
            form=request.form,
        )

    if errors:
        return _reload_form()

    try:
        with management_transaction(db.session):
            Panel.create(
                db.session,
                panel_name=panel_name,
                facility_id=facility_id,
                description=description or None,
            )
    except Exception as e:
        return _reload_form([f'Error creating panel: {e}'])

    return render_template('dashboards/admin/fragments/facility_edit_success_htmx.html')


# ── Panel Delete ───────────────────────────────────────────────────────────


@bp.route('/htmx/panel-delete/<int:panel_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_panel_delete(panel_id):
    """Soft-delete (deactivate) a panel."""
    from sam.resources.facilities import Panel

    panel = db.session.get(Panel, panel_id)
    if not panel:
        return '<div class="alert alert-danger">Panel not found</div>', 404

    try:
        with management_transaction(db.session):
            panel.update(active=False)
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return ''


# ── Panel Session Edit ─────────────────────────────────────────────────────


@bp.route('/htmx/panel-session-edit-form/<int:panel_session_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_panel_session_edit_form(panel_session_id):
    """Return the panel session edit form fragment (loaded into modal)."""
    from sam.resources.facilities import PanelSession

    panel_session = db.session.get(PanelSession, panel_session_id)
    if not panel_session:
        return '<div class="alert alert-warning">Panel session not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_panel_session_form_htmx.html',
        panel_session=panel_session,
    )


@bp.route('/htmx/panel-session-edit/<int:panel_session_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_panel_session_edit(panel_session_id):
    """Update a panel session."""
    from sam.resources.facilities import PanelSession

    panel_session = db.session.get(PanelSession, panel_session_id)
    if not panel_session:
        return '<div class="alert alert-danger">Panel session not found</div>', 404

    errors = []

    start_str = request.form.get('start_date', '').strip()
    end_str = request.form.get('end_date', '').strip()
    meeting_str = request.form.get('panel_meeting_date', '').strip()
    description = request.form.get('description', '').strip()

    start_date = None
    if start_str:
        try:
            start_date = datetime.strptime(start_str, '%Y-%m-%d')
        except ValueError:
            errors.append('Invalid start date format.')
    else:
        errors.append('Start date is required.')

    end_date = None
    if end_str:
        try:
            end_date = datetime.strptime(end_str, '%Y-%m-%d')
            effective_start = start_date or panel_session.start_date
            if effective_start and end_date <= effective_start:
                errors.append('End date must be after start date.')
        except ValueError:
            errors.append('Invalid end date format.')

    panel_meeting_date = None
    if meeting_str:
        try:
            panel_meeting_date = datetime.strptime(meeting_str, '%Y-%m-%d')
        except ValueError:
            errors.append('Invalid panel meeting date format.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_panel_session_form_htmx.html',
            panel_session=panel_session,
            errors=errors,
            form=request.form,
        )

    try:
        with management_transaction(db.session):
            panel_session.update(
                description=description or None,
                start_date=start_date,
                end_date=end_date,
                panel_meeting_date=panel_meeting_date,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_panel_session_form_htmx.html',
            panel_session=panel_session,
            errors=[f'Error updating panel session: {e}'],
            form=request.form,
        )

    return render_template('dashboards/admin/fragments/facility_edit_success_htmx.html')


# ── Allocation Type Edit ───────────────────────────────────────────────────
# (panel session create/delete intentionally omitted — PanelSession has date-range
#  semantics and no active flag; manage via edit only)


@bp.route('/htmx/allocation-type-edit-form/<int:allocation_type_id>')
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_allocation_type_edit_form(allocation_type_id):
    """Return the allocation type edit form fragment (loaded into modal)."""
    from sam.accounting.allocations import AllocationType

    allocation_type = db.session.get(AllocationType, allocation_type_id)
    if not allocation_type:
        return '<div class="alert alert-warning">Allocation type not found</div>'

    return render_template(
        'dashboards/admin/fragments/edit_allocation_type_form_htmx.html',
        allocation_type=allocation_type,
    )


@bp.route('/htmx/allocation-type-edit/<int:allocation_type_id>', methods=['POST'])
@login_required
@require_permission(Permission.EDIT_PROJECTS)
def htmx_allocation_type_edit(allocation_type_id):
    """Update an allocation type."""
    from sam.accounting.allocations import AllocationType

    allocation_type = db.session.get(AllocationType, allocation_type_id)
    if not allocation_type:
        return '<div class="alert alert-danger">Allocation type not found</div>', 404

    errors = []

    amount_str = request.form.get('default_allocation_amount', '').strip()
    fair_share_str = request.form.get('fair_share_percentage', '').strip()
    active = bool(request.form.get('active'))

    default_allocation_amount = None
    if amount_str:
        try:
            default_allocation_amount = float(amount_str)
            if default_allocation_amount < 0:
                errors.append('Default allocation amount must be >= 0.')
        except ValueError:
            errors.append('Default allocation amount must be a number.')

    fair_share_percentage = None
    if fair_share_str:
        try:
            fair_share_percentage = float(fair_share_str)
            if not (0 <= fair_share_percentage <= 100):
                errors.append('Fair share percentage must be between 0 and 100.')
        except ValueError:
            errors.append('Fair share percentage must be a number.')

    if errors:
        return render_template(
            'dashboards/admin/fragments/edit_allocation_type_form_htmx.html',
            allocation_type=allocation_type,
            errors=errors,
            form=request.form,
        )

    try:
        with management_transaction(db.session):
            allocation_type.update(
                default_allocation_amount=default_allocation_amount,
                fair_share_percentage=fair_share_percentage,
                active=active,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_allocation_type_form_htmx.html',
            allocation_type=allocation_type,
            errors=[f'Error updating allocation type: {e}'],
            form=request.form,
        )

    return render_template('dashboards/admin/fragments/facility_edit_success_htmx.html')


# ── Allocation Type Create ─────────────────────────────────────────────────


@bp.route('/htmx/allocation-type-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_allocation_type_create_form():
    """Return the allocation type create form fragment (loaded into modal)."""
    from sam.resources.facilities import Panel

    panels = db.session.query(Panel).filter(Panel.is_active).order_by(Panel.panel_name).all()

    return render_template(
        'dashboards/admin/fragments/create_allocation_type_form_htmx.html',
        panels=panels,
    )


@bp.route('/htmx/allocation-type-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_allocation_type_create():
    """Create a new allocation type."""
    from sam.accounting.allocations import AllocationType
    from sam.resources.facilities import Panel

    errors = []

    allocation_type_name = request.form.get('allocation_type', '').strip()
    panel_id_str = request.form.get('panel_id', '').strip()
    amount_str = request.form.get('default_allocation_amount', '').strip()
    fair_share_str = request.form.get('fair_share_percentage', '').strip()

    if not allocation_type_name:
        errors.append('Allocation type name is required.')

    panel_id = None
    if panel_id_str:
        try:
            panel_id = int(panel_id_str)
        except ValueError:
            errors.append('Invalid panel selection.')

    default_allocation_amount = None
    if amount_str:
        try:
            default_allocation_amount = float(amount_str)
            if default_allocation_amount < 0:
                errors.append('Default allocation amount must be >= 0.')
        except ValueError:
            errors.append('Default allocation amount must be a number.')

    fair_share_percentage = None
    if fair_share_str:
        try:
            fair_share_percentage = float(fair_share_str)
            if not (0 <= fair_share_percentage <= 100):
                errors.append('Fair share percentage must be between 0 and 100.')
        except ValueError:
            errors.append('Fair share percentage must be a number.')

    def _reload_form(extra_errors=None):
        panels = db.session.query(Panel).filter(Panel.is_active).order_by(Panel.panel_name).all()
        return render_template(
            'dashboards/admin/fragments/create_allocation_type_form_htmx.html',
            panels=panels,
            errors=(extra_errors or []) + errors,
            form=request.form,
        )

    if errors:
        return _reload_form()

    try:
        with management_transaction(db.session):
            AllocationType.create(
                db.session,
                allocation_type=allocation_type_name,
                panel_id=panel_id,
                default_allocation_amount=default_allocation_amount,
                fair_share_percentage=fair_share_percentage,
            )
    except Exception as e:
        return _reload_form([f'Error creating allocation type: {e}'])

    return render_template('dashboards/admin/fragments/facility_edit_success_htmx.html')


# ── Allocation Type Delete ─────────────────────────────────────────────────


@bp.route('/htmx/allocation-type-delete/<int:allocation_type_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_RESOURCES)
def htmx_allocation_type_delete(allocation_type_id):
    """Soft-delete (deactivate) an allocation type."""
    from sam.accounting.allocations import AllocationType

    allocation_type = db.session.get(AllocationType, allocation_type_id)
    if not allocation_type:
        return '<div class="alert alert-danger">Allocation type not found</div>', 404

    try:
        with management_transaction(db.session):
            allocation_type.update(active=False)
    except Exception as e:
        return f'<div class="alert alert-danger">Error: {e}</div>', 500

    return ''
