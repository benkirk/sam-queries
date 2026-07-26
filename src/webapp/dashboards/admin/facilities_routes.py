"""
Admin dashboard — Facility management routes.

Covers: Facilities, Panels, Panel Sessions, Allocation Types.

The CRUD quintets are generated from `_FACILITY_CRUD_SPECS` at the bottom
of this module via `register_crud`. Hand-written routes remaining: the
card fragment and the panel-session edit pair, whose cross-field check
(end date vs the stored start date) needs the loaded ORM object.
"""

from flask import render_template, request
from flask_login import login_required
from datetime import datetime
from functools import partial

from webapp.utils.htmx import (
    htmx_not_found,
    htmx_success_message,
    modal_triggers,
    read_active_only,
)
from webapp.extensions import db
from webapp.utils.rbac import (
    require_permission, require_permission_any_facility, Permission,
)
from sam.manage import management_transaction
from sam.accounting.allocations import AllocationType
from sam.resources.facilities import Facility, Panel, PanelSession
from sam.schemas.forms.facilities import (
    EditFacilityForm, CreateFacilityForm, CreatePanelForm, EditPanelForm,
    EditPanelSessionForm, EditAllocationTypeForm, CreateAllocationTypeForm,
)

from .blueprint import bp
from .crud import CrudSpec, register_crud


_FACILITY_TRIGGERS = modal_triggers('reloadFacilitiesCard')


def _active_facilities():
    return (
        db.session.query(Facility)
        .filter(Facility.is_active)
        .order_by(Facility.facility_name)
        .all()
    )


# ── Facility Card ──────────────────────────────────────────────────────────


@bp.route('/htmx/facilities')
@login_required
@require_permission_any_facility(Permission.VIEW_FACILITIES)
def htmx_facilities_card():
    """
    Return the Facility card body fragment with four tabs:
    Facilities, Panels, Panel Sessions, Allocation Types.
    Lazy-loaded when the Facility collapsible section is first expanded.
    """
    active_only = read_active_only(request.args)

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


# ── Panel Session Edit (bespoke: cross-field check needs the ORM object) ───
# (panel session create/delete intentionally omitted — PanelSession has
#  date-range semantics and no active flag; manage via edit only)


@bp.route('/htmx/panel-session-edit-form/<int:panel_session_id>')
@login_required
@require_permission(Permission.EDIT_FACILITIES)
def htmx_panel_session_edit_form(panel_session_id):
    """Return the panel session edit form fragment (loaded into modal)."""
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


# ── Allocation Type create-form context ────────────────────────────────────


def _alloc_type_create_context():
    """Re-render context for the allocation type create form: facilities +
    panels-for-the-currently-selected-facility (from request.form)."""
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


# ── CRUD quintets — generated from specs ───────────────────────────────────
#
# Endpoints, URL rules, templates, permissions, and not-found messages are
# identical to the hand-written routes these replace (pinned by
# tests/unit/test_admin_facilities_resources_crud.py and the route-map
# parity snapshot). Panel edit gained schema validation (EditPanelForm) —
# it previously coerced request.form inline.

_facility_spec = partial(
    CrudSpec,
    triggers=_FACILITY_TRIGGERS,
    edit_permission=Permission.EDIT_FACILITIES,
    create_permission=Permission.CREATE_FACILITIES,
    delete_permission=Permission.DELETE_FACILITIES,
)

_FACILITY_CRUD_SPECS = (
    _facility_spec(
        slug='facility', name='Facility',
        model=Facility, id_param='facility_id', context_key='facility',
        edit_schema=EditFacilityForm, create_schema=CreateFacilityForm,
        edit_fields=('description', 'fair_share_percentage', 'active'),
        create_fields=('facility_name', 'description', 'code',
                       'fair_share_percentage'),
    ),
    _facility_spec(
        slug='panel', name='Panel',
        model=Panel, id_param='panel_id', context_key='panel',
        edit_schema=EditPanelForm, create_schema=CreatePanelForm,
        edit_fields=('description', 'active'),
        create_fields=('panel_name', 'facility_id', 'description'),
        create_context=lambda: {'facilities': _active_facilities()},
    ),
    _facility_spec(
        slug='allocation-type', name='Allocation type',
        model=AllocationType, id_param='allocation_type_id',
        context_key='allocation_type',
        edit_schema=EditAllocationTypeForm, create_schema=CreateAllocationTypeForm,
        edit_fields=('default_allocation_amount', 'fair_share_percentage',
                     'active'),
        create_fields=('allocation_type', 'panel_id',
                       'default_allocation_amount', 'fair_share_percentage'),
        create_context=_alloc_type_create_context,
    ),
)

for _spec in _FACILITY_CRUD_SPECS:
    register_crud(bp, _spec)
