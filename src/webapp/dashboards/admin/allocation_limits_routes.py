"""Admin -> Configuration: what a request may ask for, and what the request page says.

Read with ``VIEW_SYSTEM_CONFIG``, edited with ``SYSTEM_ADMIN``. The limits bound the
amounts a user may request (a pair with no active row has no limit); the guide rows are
the request page's card per XRAS allocation type. Models:
``sam/accounting/allocation_type_limits.py``, ``sam/accounting/allocation_type_guide.py``.
"""

from flask import render_template, request
from flask_login import login_required
from sqlalchemy.orm import joinedload

from sam.accounting.allocation_type_guide import AllocationTypeGuide
from sam.accounting.allocation_type_limits import AllocationTypeResourceLimit
from sam.accounting.allocations import AllocationType
from sam.resources.resources import Resource
from sam.schemas.forms import (CreateAllocationLimitForm, CreateAllocationTypeGuideForm,
                               EditAllocationLimitForm, EditAllocationTypeGuideForm)
from webapp.extensions import db
from webapp.utils.htmx import modal_triggers, read_active_only
from webapp.utils.rbac import Permission, require_permission

from .blueprint import bp
from .crud import CrudSpec, register_crud


def _limits(active_only: bool):
    """Every limit, sorted by type name, panel, resource; the joins load in one query."""
    q = (db.session.query(AllocationTypeResourceLimit)
         .options(joinedload(AllocationTypeResourceLimit.allocation_type)
                  .joinedload(AllocationType.panel),
                  joinedload(AllocationTypeResourceLimit.resource)
                  .joinedload(Resource.resource_type)))
    if active_only:
        q = q.filter(AllocationTypeResourceLimit.is_active)
    return sorted(q, key=lambda r: (
        r.allocation_type.allocation_type if r.allocation_type else '',
        r.allocation_type.panel.panel_name if r.allocation_type and r.allocation_type.panel
        else '', r.resource.resource_name if r.resource else ''))


def _create_context():
    types = (db.session.query(AllocationType).filter(AllocationType.is_active)
             .order_by(AllocationType.allocation_type).all())
    resources = (db.session.query(Resource).filter(Resource.is_active)
                 .order_by(Resource.resource_name).all())
    return {'allocation_types': types, 'resources': resources}


@bp.route('/htmx/allocation-limits', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_SYSTEM_CONFIG)
def htmx_allocation_limits_card():
    """The limits card body."""
    active_only = read_active_only(request.args)
    return render_template('dashboards/admin/fragments/allocation_limits_card.html',
                           limits=_limits(active_only), active_only=active_only)


register_crud(bp, CrudSpec(
    slug='allocation-limit', name='Request limit',
    model=AllocationTypeResourceLimit, id_param='allocation_type_resource_limit_id',
    context_key='limit',
    edit_schema=EditAllocationLimitForm, create_schema=CreateAllocationLimitForm,
    edit_fields=('min_amount', 'default_amount', 'max_amount', 'active'),
    create_kwargs=lambda data: {
        **{k: data[k] for k in ('allocation_type_id', 'resource_id', 'min_amount',
                                'default_amount', 'max_amount')},
        'created_by': _actor()},
    create_context=_create_context,
    triggers=modal_triggers('reloadAllocationLimitsCard'),
    edit_permission=Permission.SYSTEM_ADMIN,
    create_permission=Permission.SYSTEM_ADMIN,
    delete_permission=Permission.SYSTEM_ADMIN,
))


def _guide_context():
    """The select options: the active SAM type names, and the audience labels."""
    from sam.accounting.allocation_type_guide import AUDIENCE_LABELS
    names = (db.session.query(AllocationType.allocation_type).filter(AllocationType.is_active)
             .order_by(AllocationType.allocation_type).all())
    return {'allocation_type_options': [(n, n) for (n,) in names],
            'audience_options': list(AUDIENCE_LABELS.items())}


_GUIDE_FIELDS = ('title', 'allocation_type', 'summary', 'funding', 'cadence', 'notes',
                 'audience', 'display_order')


@bp.route('/htmx/allocation-guides', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_SYSTEM_CONFIG)
def htmx_allocation_guides_card():
    """The request page's cards, in display order."""
    active_only = read_active_only(request.args)
    q = db.session.query(AllocationTypeGuide)
    if active_only:
        q = q.filter(AllocationTypeGuide.is_active)
    guides = q.order_by(AllocationTypeGuide.display_order, AllocationTypeGuide.title).all()
    return render_template('dashboards/admin/fragments/allocation_guides_card.html',
                           guides=guides, active_only=active_only)


register_crud(bp, CrudSpec(
    slug='allocation-guide', name='Allocation type guide',
    model=AllocationTypeGuide, id_param='allocation_type_guide_id',
    context_key='guide',
    edit_schema=EditAllocationTypeGuideForm, create_schema=CreateAllocationTypeGuideForm,
    edit_fields=_GUIDE_FIELDS + ('active',),
    create_kwargs=lambda data: {
        **{k: data[k] for k in ('xras_allocation_type_id',) + _GUIDE_FIELDS},
        'created_by': _actor()},
    edit_context=_guide_context, create_context=_guide_context,
    triggers=modal_triggers('reloadAllocationGuidesCard'),
    edit_permission=Permission.SYSTEM_ADMIN,
    create_permission=Permission.SYSTEM_ADMIN,
    delete_permission=Permission.SYSTEM_ADMIN,
))


def _actor() -> str:
    from flask_login import current_user
    return getattr(current_user, 'username', None) or 'unknown'
