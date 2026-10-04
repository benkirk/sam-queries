"""
Admin dashboard — Facility management routes.

Covers: Facilities, Panels, Allocation Types.

The CRUD quintets are generated from `_FACILITY_CRUD_SPECS` at the bottom
of this module via `register_crud`; the card fragment is hand-written.
"""

from flask import render_template, request
from flask_login import login_required
from functools import partial

from webapp.utils.htmx import (
    modal_triggers,
    read_active_only,
    read_layout,
    read_theme,
)
from webapp.extensions import db
from webapp.dashboards.charts import generate_fair_share_sunburst
from webapp.dashboards.charts.theme import facility_slots
from webapp.utils.rbac import (
    require_permission, require_permission_any_facility, Permission,
)
from sam.accounting.allocations import AllocationType
from sam.resources.facilities import Facility, Panel
from sam.schemas.forms.facilities import (
    EditFacilityForm, CreateFacilityForm, CreatePanelForm, EditPanelForm,
    EditAllocationTypeForm, CreateAllocationTypeForm,
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


# Facility Card


@bp.route('/htmx/facilities')
@login_required
@require_permission_any_facility(Permission.VIEW_FACILITIES)
def htmx_facilities_card():
    """The Facilities card: one tree of facility -> panel -> allocation type, with fair shares."""
    active_only = read_active_only(request.args)

    facility_q = db.session.query(Facility).order_by(Facility.facility_name)
    if active_only:
        facility_q = facility_q.filter(Facility.is_active)
    facilities = facility_q.all()
    active = _active_facilities()
    slots = facility_slots(f.facility_id for f in active)
    sunburst = [
        {'id': f.facility_id, 'facility': f.facility_name, 'slot': slots.get(f.facility_id),
         'share': f.fair_share_percentage or 0,
         'types': [{'name': at.allocation_type, 'share': at.fair_share_percentage or 0}
                   for p in f.panels for at in p.allocation_types if at.active]}
        for f in active
    ]

    return render_template(
        'dashboards/admin/fragments/facility_card.html',
        facilities=facilities,
        active_only=active_only,
        fs_slots=slots,
        fair_share_chart=generate_fair_share_sunburst(
            sunburst, layout=read_layout(), theme=read_theme()),
    )


# Allocation Type create-form context


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


# CRUD quintets — generated from specs
#
# Endpoints, URL rules, templates, permissions, and not-found messages are
# identical to the hand-written routes these replace (pinned by
# tests/unit/webapp/test_admin_facilities_resources_crud.py and the route-map
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
