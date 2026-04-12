"""
Admin dashboard — Facility management routes.

Covers: Facilities, Panels, Panel Sessions, Allocation Types.
"""

from flask import render_template, request
from webapp.utils.htmx import htmx_success, htmx_success_message
from flask_login import login_required
from datetime import datetime
from marshmallow import ValidationError

from webapp.extensions import db
from webapp.utils.rbac import require_permission, Permission
from sam.manage import management_transaction
from sam.schemas.forms.facilities import (
    EditFacilityForm, CreateFacilityForm, CreatePanelForm,
    EditPanelSessionForm, EditAllocationTypeForm, CreateAllocationTypeForm,
)

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

    try:
        data = EditFacilityForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/edit_facility_form_htmx.html',
            facility=facility,
            errors=EditFacilityForm.flatten_errors(e.messages),
            form=request.form,
        )

    try:
        with management_transaction(db.session):
            facility.update(
                description=data['description'],
                fair_share_percentage=data['fair_share_percentage'],
                active=data['active'],
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_facility_form_htmx.html',
            facility=facility,
            errors=[f'Error updating facility: {e}'],
            form=request.form,
        )

    return htmx_success_message({'closeActiveModal': {}, 'reloadFacilitiesCard': {}}, 'Saved successfully.')


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

    try:
        data = CreateFacilityForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/create_facility_form_htmx.html',
            errors=CreateFacilityForm.flatten_errors(e.messages), form=request.form,
        )

    try:
        with management_transaction(db.session):
            Facility.create(
                db.session,
                facility_name=data['facility_name'],
                description=data['description'],
                code=data['code'],
                fair_share_percentage=data['fair_share_percentage'],
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/create_facility_form_htmx.html',
            errors=[f'Error creating facility: {e}'], form=request.form,
        )

    return htmx_success_message({'closeActiveModal': {}, 'reloadFacilitiesCard': {}}, 'Saved successfully.')


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

    return htmx_success_message({'closeActiveModal': {}, 'reloadFacilitiesCard': {}}, 'Saved successfully.')


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

    def _reload_form(extra_errors=None):
        facilities = db.session.query(Facility).filter(Facility.is_active).order_by(Facility.facility_name).all()
        return render_template(
            'dashboards/admin/fragments/create_panel_form_htmx.html',
            facilities=facilities,
            errors=extra_errors or [],
            form=request.form,
        )

    try:
        data = CreatePanelForm().load(request.form)
    except ValidationError as e:
        return _reload_form(CreatePanelForm.flatten_errors(e.messages))

    try:
        with management_transaction(db.session):
            Panel.create(
                db.session,
                panel_name=data['panel_name'],
                facility_id=data['facility_id'],
                description=data['description'],
            )
    except Exception as e:
        return _reload_form([f'Error creating panel: {e}'])

    return htmx_success_message({'closeActiveModal': {}, 'reloadFacilitiesCard': {}}, 'Saved successfully.')


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

    try:
        data = EditPanelSessionForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/edit_panel_session_form_htmx.html',
            panel_session=panel_session,
            errors=EditPanelSessionForm.flatten_errors(e.messages),
            form=request.form,
        )

    # Additional cross-field check: end_date vs existing start_date when form start not set
    if data.get('end_date') and data.get('start_date') is None and panel_session.start_date:
        if data['end_date'] <= panel_session.start_date:
            return render_template(
                'dashboards/admin/fragments/edit_panel_session_form_htmx.html',
                panel_session=panel_session,
                errors=['End date must be after start date.'],
                form=request.form,
            )

    try:
        with management_transaction(db.session):
            panel_session.update(
                description=data['description'],
                start_date=datetime.combine(data['start_date'], datetime.min.time()) if data.get('start_date') else None,
                end_date=data['end_date'],
                panel_meeting_date=datetime.combine(data['panel_meeting_date'], datetime.min.time()) if data.get('panel_meeting_date') else None,
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_panel_session_form_htmx.html',
            panel_session=panel_session,
            errors=[f'Error updating panel session: {e}'],
            form=request.form,
        )

    return htmx_success_message({'closeActiveModal': {}, 'reloadFacilitiesCard': {}}, 'Saved successfully.')


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

    try:
        data = EditAllocationTypeForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/edit_allocation_type_form_htmx.html',
            allocation_type=allocation_type,
            errors=EditAllocationTypeForm.flatten_errors(e.messages),
            form=request.form,
        )

    try:
        with management_transaction(db.session):
            allocation_type.update(
                default_allocation_amount=data['default_allocation_amount'],
                fair_share_percentage=data['fair_share_percentage'],
                active=data['active'],
            )
    except Exception as e:
        return render_template(
            'dashboards/admin/fragments/edit_allocation_type_form_htmx.html',
            allocation_type=allocation_type,
            errors=[f'Error updating allocation type: {e}'],
            form=request.form,
        )

    return htmx_success_message({'closeActiveModal': {}, 'reloadFacilitiesCard': {}}, 'Saved successfully.')


# ── Allocation Type Create ─────────────────────────────────────────────────


@bp.route('/htmx/allocation-type-create-form')
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_allocation_type_create_form():
    """Return the allocation type create form fragment (loaded into modal)."""
    from sam.resources.facilities import Facility

    facilities = (
        db.session.query(Facility)
        .filter(Facility.is_active)
        .order_by(Facility.facility_name)
        .all()
    )

    return render_template(
        'dashboards/admin/fragments/create_allocation_type_form_htmx.html',
        facilities=facilities,
        panels_for_facility=[],
    )


@bp.route('/htmx/allocation-type-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_RESOURCES)
def htmx_allocation_type_create():
    """Create a new allocation type."""
    from sam.accounting.allocations import AllocationType
    from sam.resources.facilities import Facility, Panel

    facility_id_str = request.form.get('facility_id', '').strip()

    def _reload_form(extra_errors=None):
        facilities = (
            db.session.query(Facility)
            .filter(Facility.is_active)
            .order_by(Facility.facility_name)
            .all()
        )
        panels_for_facility = []
        if facility_id_str:
            try:
                panels_for_facility = (
                    db.session.query(Panel)
                    .filter(Panel.facility_id == int(facility_id_str), Panel.is_active)
                    .order_by(Panel.panel_name)
                    .all()
                )
            except (ValueError, TypeError):
                pass
        return render_template(
            'dashboards/admin/fragments/create_allocation_type_form_htmx.html',
            facilities=facilities,
            panels_for_facility=panels_for_facility,
            errors=extra_errors or [],
            form=request.form,
        )

    try:
        data = CreateAllocationTypeForm().load(request.form)
    except ValidationError as e:
        return _reload_form(CreateAllocationTypeForm.flatten_errors(e.messages))

    try:
        with management_transaction(db.session):
            AllocationType.create(
                db.session,
                allocation_type=data['allocation_type'],
                panel_id=data['panel_id'],
                default_allocation_amount=data['default_allocation_amount'],
                fair_share_percentage=data['fair_share_percentage'],
            )
    except Exception as e:
        return _reload_form([f'Error creating allocation type: {e}'])

    return htmx_success_message({'closeActiveModal': {}, 'reloadFacilitiesCard': {}}, 'Saved successfully.')


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
