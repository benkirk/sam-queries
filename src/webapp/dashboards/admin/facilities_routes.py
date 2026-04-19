"""
Admin dashboard — Facility management routes.

Covers: Facilities, Panels, Panel Sessions, Allocation Types.
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
from webapp.utils.rbac import require_permission, Permission
from sam.manage import management_transaction
from sam.schemas.forms.facilities import (
    EditFacilityForm, CreateFacilityForm, CreatePanelForm,
    EditPanelSessionForm, EditAllocationTypeForm, CreateAllocationTypeForm,
)

from .blueprint import bp


_FACILITY_TRIGGERS = {'closeActiveModal': {}, 'reloadFacilitiesCard': {}}


# ── Facility Card ──────────────────────────────────────────────────────────


@bp.route('/htmx/facilities')
@login_required
@require_permission(Permission.VIEW_FACILITIES)
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
@require_permission(Permission.EDIT_FACILITIES)
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
@require_permission(Permission.EDIT_FACILITIES)
def htmx_facility_edit(facility_id):
    """Update a facility."""
    from sam.resources.facilities import Facility

    facility = db.session.get(Facility, facility_id)
    if not facility:
        return htmx_not_found('Facility')

    return handle_htmx_form_post(
        schema_cls=EditFacilityForm,
        template='dashboards/admin/fragments/edit_facility_form_htmx.html',
        success_triggers=_FACILITY_TRIGGERS,
        error_prefix='Error updating facility',
        extra_context={'facility': facility},
        do_action=lambda data: facility.update(
            description=data['description'],
            fair_share_percentage=data['fair_share_percentage'],
            active=data['active'],
        ),
    )


# ── Facility Create ────────────────────────────────────────────────────────


@bp.route('/htmx/facility-create-form')
@login_required
@require_permission(Permission.CREATE_FACILITIES)
def htmx_facility_create_form():
    """Return the facility create form fragment (loaded into modal)."""
    return render_template('dashboards/admin/fragments/create_facility_form_htmx.html')


@bp.route('/htmx/facility-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_FACILITIES)
def htmx_facility_create():
    """Create a new facility."""
    from sam.resources.facilities import Facility

    return handle_htmx_form_post(
        schema_cls=CreateFacilityForm,
        template='dashboards/admin/fragments/create_facility_form_htmx.html',
        success_triggers=_FACILITY_TRIGGERS,
        error_prefix='Error creating facility',
        do_action=lambda data: Facility.create(
            db.session,
            facility_name=data['facility_name'],
            description=data['description'],
            code=data['code'],
            fair_share_percentage=data['fair_share_percentage'],
        ),
    )


# ── Facility Delete ────────────────────────────────────────────────────────


@bp.route('/htmx/facility-delete/<int:facility_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_FACILITIES)
def htmx_facility_delete(facility_id):
    """Soft-delete (deactivate) a facility."""
    from sam.resources.facilities import Facility

    facility = db.session.get(Facility, facility_id)
    if not facility:
        return htmx_not_found('Facility')
    return handle_htmx_soft_delete(facility, name='Facility')


# ── Panel Edit ─────────────────────────────────────────────────────────────


@bp.route('/htmx/panel-edit-form/<int:panel_id>')
@login_required
@require_permission(Permission.EDIT_FACILITIES)
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
@require_permission(Permission.EDIT_FACILITIES)
def htmx_panel_edit(panel_id):
    """Update a panel."""
    from sam.resources.facilities import Panel

    panel = db.session.get(Panel, panel_id)
    if not panel:
        return htmx_not_found('Panel')

    # Note: panel edit doesn't validate via a schema (only description + active),
    # so it can't use handle_htmx_form_post directly. The body is small enough
    # that the inline pattern stays clear.
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

    return htmx_success_message(_FACILITY_TRIGGERS, 'Saved successfully.')


# ── Panel Create ───────────────────────────────────────────────────────────


@bp.route('/htmx/panel-create-form')
@login_required
@require_permission(Permission.CREATE_FACILITIES)
def htmx_panel_create_form():
    """Return the panel create form fragment (loaded into modal)."""
    from sam.resources.facilities import Facility

    facilities = db.session.query(Facility).filter(Facility.is_active).order_by(Facility.facility_name).all()

    return render_template(
        'dashboards/admin/fragments/create_panel_form_htmx.html',
        facilities=facilities,
    )


def _active_facilities():
    from sam.resources.facilities import Facility
    return (
        db.session.query(Facility)
        .filter(Facility.is_active)
        .order_by(Facility.facility_name)
        .all()
    )


@bp.route('/htmx/panel-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_FACILITIES)
def htmx_panel_create():
    """Create a new panel."""
    from sam.resources.facilities import Panel

    return handle_htmx_form_post(
        schema_cls=CreatePanelForm,
        template='dashboards/admin/fragments/create_panel_form_htmx.html',
        success_triggers=_FACILITY_TRIGGERS,
        error_prefix='Error creating panel',
        context_fn=lambda: {'facilities': _active_facilities()},
        do_action=lambda data: Panel.create(
            db.session,
            panel_name=data['panel_name'],
            facility_id=data['facility_id'],
            description=data['description'],
        ),
    )


# ── Panel Delete ───────────────────────────────────────────────────────────


@bp.route('/htmx/panel-delete/<int:panel_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_FACILITIES)
def htmx_panel_delete(panel_id):
    """Soft-delete (deactivate) a panel."""
    from sam.resources.facilities import Panel

    panel = db.session.get(Panel, panel_id)
    if not panel:
        return htmx_not_found('Panel')
    return handle_htmx_soft_delete(panel, name='Panel')


# ── Panel Session Edit ─────────────────────────────────────────────────────


@bp.route('/htmx/panel-session-edit-form/<int:panel_session_id>')
@login_required
@require_permission(Permission.EDIT_FACILITIES)
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
@require_permission(Permission.EDIT_FACILITIES)
def htmx_panel_session_edit(panel_session_id):
    """Update a panel session."""
    from sam.resources.facilities import PanelSession

    panel_session = db.session.get(PanelSession, panel_session_id)
    if not panel_session:
        return htmx_not_found('Panel session')

    # Cross-field check (end_date vs existing start_date) needs the loaded
    # object, so this route uses the schema directly rather than the helper.
    from marshmallow import ValidationError
    try:
        data = EditPanelSessionForm().load(request.form)
    except ValidationError as e:
        return render_template(
            'dashboards/admin/fragments/edit_panel_session_form_htmx.html',
            panel_session=panel_session,
            errors=EditPanelSessionForm.flatten_errors(e.messages),
            form=request.form,
        )

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

    return htmx_success_message(_FACILITY_TRIGGERS, 'Saved successfully.')


# ── Allocation Type Edit ───────────────────────────────────────────────────
# (panel session create/delete intentionally omitted — PanelSession has date-range
#  semantics and no active flag; manage via edit only)


@bp.route('/htmx/allocation-type-edit-form/<int:allocation_type_id>')
@login_required
@require_permission(Permission.EDIT_FACILITIES)
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
@require_permission(Permission.EDIT_FACILITIES)
def htmx_allocation_type_edit(allocation_type_id):
    """Update an allocation type."""
    from sam.accounting.allocations import AllocationType

    allocation_type = db.session.get(AllocationType, allocation_type_id)
    if not allocation_type:
        return htmx_not_found('Allocation type')

    return handle_htmx_form_post(
        schema_cls=EditAllocationTypeForm,
        template='dashboards/admin/fragments/edit_allocation_type_form_htmx.html',
        success_triggers=_FACILITY_TRIGGERS,
        error_prefix='Error updating allocation type',
        extra_context={'allocation_type': allocation_type},
        do_action=lambda data: allocation_type.update(
            default_allocation_amount=data['default_allocation_amount'],
            fair_share_percentage=data['fair_share_percentage'],
            active=data['active'],
        ),
    )


# ── Allocation Type Create ─────────────────────────────────────────────────


@bp.route('/htmx/allocation-type-create-form')
@login_required
@require_permission(Permission.CREATE_FACILITIES)
def htmx_allocation_type_create_form():
    """Return the allocation type create form fragment (loaded into modal)."""
    return render_template(
        'dashboards/admin/fragments/create_allocation_type_form_htmx.html',
        facilities=_active_facilities(),
        panels_for_facility=[],
    )


def _alloc_type_create_context():
    """Re-render context for the allocation type create form: facilities +
    panels-for-the-currently-selected-facility (from request.form)."""
    from sam.resources.facilities import Panel

    panels_for_facility = []
    facility_id_str = request.form.get('facility_id', '').strip()
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
    return {
        'facilities': _active_facilities(),
        'panels_for_facility': panels_for_facility,
    }


@bp.route('/htmx/allocation-type-create', methods=['POST'])
@login_required
@require_permission(Permission.CREATE_FACILITIES)
def htmx_allocation_type_create():
    """Create a new allocation type."""
    from sam.accounting.allocations import AllocationType

    return handle_htmx_form_post(
        schema_cls=CreateAllocationTypeForm,
        template='dashboards/admin/fragments/create_allocation_type_form_htmx.html',
        success_triggers=_FACILITY_TRIGGERS,
        error_prefix='Error creating allocation type',
        context_fn=_alloc_type_create_context,
        do_action=lambda data: AllocationType.create(
            db.session,
            allocation_type=data['allocation_type'],
            panel_id=data['panel_id'],
            default_allocation_amount=data['default_allocation_amount'],
            fair_share_percentage=data['fair_share_percentage'],
        ),
    )


# ── Allocation Type Delete ─────────────────────────────────────────────────


@bp.route('/htmx/allocation-type-delete/<int:allocation_type_id>', methods=['DELETE'])
@login_required
@require_permission(Permission.DELETE_FACILITIES)
def htmx_allocation_type_delete(allocation_type_id):
    """Soft-delete (deactivate) an allocation type."""
    from sam.accounting.allocations import AllocationType

    allocation_type = db.session.get(AllocationType, allocation_type_id)
    if not allocation_type:
        return htmx_not_found('Allocation type')
    return handle_htmx_soft_delete(allocation_type, name='Allocation type')
